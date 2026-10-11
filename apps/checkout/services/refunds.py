"""Recoverable Stripe test refunds; network calls never hold database locks."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.checkout.models import OrderRefund
from apps.wallet.models import LedgerAccount
from apps.wallet.services import Posting, get_treasury_account, post_transaction
from . import stripe_provider as provider


@transaction.atomic
def apply_refund(pk, result):
    """Verify provider facts, reverse this store's receipt once, and preserve terminal state."""
    initial = OrderRefund.objects.select_related('order').get(pk=pk)
    get_user_model().objects.select_for_update().get(pk=initial.order.user_id)
    refund = OrderRefund.objects.select_for_update(of=('self',)).select_related('order', 'payment_attempt').get(pk=pk)
    if refund.status == 'succeeded':
        return
    attempt = refund.payment_attempt
    if (not str(result.get('id', '')).startswith('re_')
            or result.get('amount') != refund.amount_cents or result.get('currency') != 'usd'
            or result.get('payment_intent') != attempt.stripe_payment_intent_id
            or (result.get('metadata') or {}).get('order_refund_reference') != str(refund.reference)
            or result.get('status') not in ('pending', 'requires_action', 'succeeded', 'failed', 'canceled')
            or refund.stripe_refund_id and refund.stripe_refund_id != result['id']):
        raise ValueError('Refund facts do not match the persisted request.')
    # A stale concurrent response must not regress a final provider result.
    if refund.status in ('failed', 'canceled'):
        return
    refund.stripe_refund_id = result['id']
    refund.status = result['status']
    refund.failure_code = ''
    if refund.status == 'succeeded':
        pending = LedgerAccount.objects.get(wallet__store_id=refund.order.store_id, bucket='pending')
        refund.reversal = post_transaction(
            operation_key=f'order-refund:{refund.reference}', operation_type='stripe_order_refund',
            postings=[Posting(pending.pk, 'debit', refund.amount_cents),
                      Posting(get_treasury_account().pk, 'credit', refund.amount_cents)],
        )
    refund.save()


def reconcile_refund(pk):
    """Retry the same refund or retrieve it; old unknown requests never create a second refund.

    Unknown provider outcomes retain a durable pending record. The customer retry
    endpoint and reconciliation command can recover it without repeating inventory work.
    """
    refund = OrderRefund.objects.select_related('payment_attempt').get(pk=pk)
    if refund.status in ('succeeded', 'failed', 'canceled'):
        return
    try:
        if refund.stripe_refund_id:
            result = provider.retrieve_refund(refund.stripe_refund_id)
        else:
            # After Stripe's idempotency retention window, only search; never recreate.
            if timezone.now() >= refund.created_at + timedelta(hours=23):
                result = provider.find_refund(refund)
                if result is None:
                    OrderRefund.objects.filter(pk=pk, stripe_refund_id__isnull=True, status='pending').update(
                        failure_code='manual_review_required', updated_at=timezone.now())
                    return
            else:
                result = provider.create_refund(refund)
        apply_refund(pk, result)
    except Exception:
        # No raw provider exception text or payment details are exposed or stored.
        OrderRefund.objects.filter(pk=pk, status__in=['pending', 'requires_action']).update(
            failure_code='reconciliation_required', updated_at=timezone.now())
