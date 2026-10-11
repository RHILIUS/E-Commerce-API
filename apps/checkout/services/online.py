"""Recoverable online payments: network outside locks, all local effects in one commit."""
import logging
from datetime import timedelta
from urllib.parse import urlsplit

import stripe
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException

from apps.checkout.models import Checkout, PaymentAttempt
from apps.ecommerce.models import Cart, CartItem, Order, OrderItem, Product
from apps.inventory.models import StockReservation
from apps.store.models import Store
from apps.wallet.models import LedgerAccount
from apps.wallet.services import Posting, create_wallet, get_treasury_account, post_transaction
from .placement import reserve_online
from . import stripe_provider as provider


logger = logging.getLogger(__name__)


class PaymentRejected(APIException):
    status_code = 400
    default_detail = 'Stripe rejected this checkout before payment started. Review your order and try again.'
    default_code = 'payment_rejected'


class PaymentMismatch(Exception):
    """Provider facts disagree with the accepted purchase; keep funds/holds for investigation."""


def payment_status(user, reference):
    """Read only an owned Stripe checkout, without provider calls or creating purchases."""
    checkout = get_object_or_404(Checkout, reference=reference, user=user, payment_method='stripe')
    attempt = checkout.payment_attempts.get()
    return {'reference': checkout.reference, 'status': checkout.status,
            'checkout_url': attempt.checkout_url if (checkout.status == 'pending' and not attempt.cancel_requested
                                                      and attempt.expires_at > timezone.now()) else None,
            'expires_at': attempt.expires_at, 'cancel_requested': attempt.cancel_requested}


def start_payment(user, data):
    """Reserve all stores once, then create/recover the provider session outside the transaction."""
    checkout, _ = reserve_online(user, data)
    attempt = checkout.payment_attempts.get()
    if attempt.status == 'pending':
        reconcile_attempt(attempt.pk)
    attempt.refresh_from_db()
    if attempt.status == 'failed' and attempt.failure_code == 'session_creation_rejected':
        raise PaymentRejected()
    return payment_status(user, checkout.reference)


@transaction.atomic
def reject_session_creation(attempt_id):
    """Close a verified pre-creation rejection under the payment/inventory lock order.

    Never release an attempt with assigned provider or ledger references: another
    reconciler may have recovered a session while this request was in flight.
    """
    initial = PaymentAttempt.objects.select_related('checkout').get(pk=attempt_id)
    get_user_model().objects.select_for_update().get(pk=initial.checkout.user_id)
    checkout = Checkout.objects.select_for_update().get(pk=initial.checkout_id)
    attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
    if attempt.status != 'pending':
        return
    if attempt.stripe_checkout_session_id or attempt.stripe_payment_intent_id or attempt.receipt_id:
        raise PaymentMismatch('rejection_after_session_assignment')
    items = list(OrderItem.objects.filter(order__checkout=checkout).order_by('product_id'))
    list(Product.objects.select_for_update().filter(pk__in=[i.product_id for i in items]).order_by('pk'))
    list(Store.objects.select_for_update().filter(pk__in=checkout.orders.values('store_id')).order_by('pk'))
    reservations = list(StockReservation.objects.select_for_update().filter(order_item__in=items).order_by('pk'))
    if len(reservations) != len(items) or any(r.status != 'held' for r in reservations):
        raise PaymentMismatch('reservation_mismatch')
    now = timezone.now()
    StockReservation.objects.filter(pk__in=[r.pk for r in reservations]).update(
        status='released', resolved_at=now, updated_at=now)
    Order.objects.filter(checkout=checkout).update(status='cancelled', payment_status='cancelled', concluded_at=now, updated_at=now)
    attempt.status = 'failed'
    attempt.failure_code = 'session_creation_rejected'
    attempt.checkout_url = ''
    attempt.save(update_fields=['status', 'failure_code', 'checkout_url', 'updated_at'])
    checkout.status = 'cancelled'
    checkout.save(update_fields=['status', 'updated_at'])


def _validate_session(attempt, session):
    """Match provider-controlled state to immutable payment snapshots before any effects."""
    metadata = session.get('metadata') or {}
    if (session.get('livemode') is not False or session.get('mode') != 'payment'
            or session.get('currency') != 'usd' or session.get('amount_total') != int(attempt.amount * 100)
            or metadata.get('attempt_reference') != str(attempt.reference)
            or metadata.get('checkout_reference') != str(attempt.checkout.reference)
            or session.get('client_reference_id') != str(attempt.checkout.reference)
            or not str(session.get('id', '')).startswith('cs_test_')
            or (attempt.stripe_checkout_session_id and attempt.stripe_checkout_session_id != session['id'])):
        raise PaymentMismatch('session_mismatch')


@transaction.atomic
def apply_session(attempt_id, session):
    """Atomically finalize payment or terminal expiry under the shared inventory lock order."""
    initial = PaymentAttempt.objects.select_related('checkout').get(pk=attempt_id)
    get_user_model().objects.select_for_update().get(pk=initial.checkout.user_id)
    checkout = Checkout.objects.select_for_update().get(pk=initial.checkout_id)
    attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
    attempt.checkout = checkout
    _validate_session(attempt, session)
    if attempt.status == 'succeeded':
        return
    if attempt.status != 'pending':
        if session.get('payment_status') == 'paid':
            raise PaymentMismatch('paid_after_release')
        return
    attempt.stripe_checkout_session_id = session['id']
    url = session.get('url')
    if url:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != 'checkout.stripe.com' or parsed.username or parsed.password:
            raise PaymentMismatch('checkout_url_mismatch')
        attempt.checkout_url = url
    intent = session.get('payment_intent')
    intent_id = intent.get('id') if isinstance(intent, dict) else intent
    if attempt.stripe_payment_intent_id and intent_id != attempt.stripe_payment_intent_id:
        raise PaymentMismatch('intent_mismatch')
    attempt.stripe_payment_intent_id = intent_id
    paid = session.get('status') == 'complete' and session.get('payment_status') == 'paid'
    expired = (session.get('status') == 'expired' and session.get('payment_status') == 'unpaid'
               and (intent is None or isinstance(intent, dict) and intent.get('status') in ('canceled', 'requires_payment_method')))
    if not paid and not expired:
        attempt.save()
        return
    if paid and (not isinstance(intent, dict) or intent.get('status') != 'succeeded'
                 or intent.get('currency') != 'usd' or intent.get('amount_received') != int(attempt.amount * 100)
                 or intent.get('livemode') is not False):
        raise PaymentMismatch('intent_payment_mismatch')
    items = list(OrderItem.objects.filter(order__checkout=checkout).order_by('product_id'))
    products = {p.pk: p for p in Product.objects.select_for_update().filter(
        pk__in=[item.product_id for item in items]).order_by('pk')}
    # Wallet provisioning locks stores before treasury; match purchase lock order.
    orders = list(checkout.orders.order_by('store_id'))
    list(Store.objects.select_for_update().filter(pk__in=[o.store_id for o in orders]).order_by('pk'))
    reservations = list(StockReservation.objects.select_for_update().filter(
        order_item__in=items).order_by('pk'))
    if len(reservations) != len(items) or any(r.status != 'held' for r in reservations):
        raise PaymentMismatch('reservation_mismatch')
    now = timezone.now()
    if paid:
        if any(item.product_id not in products or products[item.product_id].stock < item.quantity for item in items):
            raise PaymentMismatch('reserved_stock_missing')
        credits = []
        allocated = 0
        for order in orders:
            amount = int(sum(item.price_at_purchase * item.quantity for item in items if item.order_id == order.pk) * 100)
            allocated += amount
            if amount:
                wallet = create_wallet(store_id=order.store_id)
                account = wallet.accounts.get(bucket=LedgerAccount.Bucket.PENDING)
                credits.append(Posting(account.pk, 'credit', amount))
        if allocated != int(attempt.amount * 100):
            raise PaymentMismatch('allocation_mismatch')
        receipt = post_transaction(operation_key=f'payment:{attempt.reference}:receipt', operation_type='payment_receipt',
                                   postings=[Posting(get_treasury_account().pk, 'debit', allocated), *credits])
        for item in items:
            Product.objects.filter(pk=item.product_id).update(stock=products[item.product_id].stock - item.quantity, updated_at=now)
        StockReservation.objects.filter(pk__in=[r.pk for r in reservations]).update(status='consumed', resolved_at=now, updated_at=now)
        Order.objects.filter(checkout=checkout).update(payment_status='paid', paid_at=now, updated_at=now)
        # Selected lines are frozen by all public cart writers while holds are active.
        CartItem.objects.filter(pk__in=[item.source_cart_item_id for item in items if item.source_cart_item_id]).delete()
        Cart.objects.filter(user_id=checkout.user_id, items__isnull=True).delete()
        attempt.status, attempt.succeeded_at, attempt.receipt = 'succeeded', now, receipt
        checkout.status, checkout.completed_at = 'completed', now
    else:
        StockReservation.objects.filter(pk__in=[r.pk for r in reservations]).update(status='released', resolved_at=now, updated_at=now)
        checkout.status = 'cancelled' if attempt.cancel_requested else 'expired'
        attempt.status = checkout.status
        Order.objects.filter(checkout=checkout).update(status='cancelled', payment_status='cancelled', concluded_at=now, updated_at=now)
    attempt.checkout_url = ''
    attempt.failure_code = ''
    attempt.save()
    checkout.save(update_fields=['status', 'completed_at', 'updated_at'])



@transaction.atomic
def _remember_session(attempt_id, session):
    """Persist the recovered provider ID before any subsequent network request can fail."""
    attempt = PaymentAttempt.objects.select_for_update(of=('self',)).select_related('checkout').get(pk=attempt_id)
    _validate_session(attempt, session)
    if not attempt.stripe_checkout_session_id:
        attempt.stripe_checkout_session_id = session['id']
        attempt.save(update_fields=['stripe_checkout_session_id', 'updated_at'])


def reconcile_attempt(attempt_id):
    """Recover one attempt, preserving holds whenever the provider outcome is uncertain."""
    attempt = PaymentAttempt.objects.select_related('checkout').get(pk=attempt_id)
    if attempt.status != 'pending':
        return
    try:
        if attempt.stripe_checkout_session_id:
            session = provider.retrieve_session(attempt.stripe_checkout_session_id)
        else:
            # Stripe may prune idempotency keys after 24 hours. Never recreate an old unknown session.
            if timezone.now() >= attempt.created_at + timedelta(hours=23):
                raise PaymentMismatch('manual_session_recovery_required')
            try:
                session = provider.create_session(attempt)
            except stripe.InvalidRequestError as exc:
                # Only this demonstrated validation rejection proves no session was
                # created. All other errors keep the original request and holds.
                if (exc.http_status == 400 and exc.param == 'payment_method_types'
                        and 'payment_method_types' in attempt.session_parameters
                        and 'no longer supported' in str(exc)):
                    reject_session_creation(attempt.pk)
                    return
                raise
            _remember_session(attempt.pk, session)
            # Retrieve expanded intent even when a replay returns an already completed session.
            session = provider.retrieve_session(session['id'])
        _validate_session(attempt, session)
        attempt.refresh_from_db()
        if session.get('status') == 'open' and (attempt.cancel_requested or attempt.expires_at <= timezone.now()):
            try:
                provider.expire_session(session['id'])
            except stripe.StripeError:
                # Payment may have won the race; only current provider state decides release.
                pass
            session = provider.retrieve_session(session['id'])
        apply_session(attempt_id, session)
    except (stripe.StripeError, PaymentMismatch) as exc:
        logger.warning('Stripe reconciliation failed: attempt=%s error_type=%s', attempt.pk, type(exc).__name__)
        code = str(exc) if isinstance(exc, PaymentMismatch) else 'provider_unavailable'
        PaymentAttempt.objects.filter(pk=attempt_id, status='pending').update(failure_code=code, updated_at=timezone.now())
        raise provider.PaymentUnavailable() from exc


def refresh_payment(user, reference, *, cancel=False):
    """Authorize the owner, persist cancellation intent, then reconcile without open DB locks."""
    checkout = get_object_or_404(Checkout, reference=reference, user=user, payment_method='stripe')
    attempt = checkout.payment_attempts.get()
    if cancel:
        PaymentAttempt.objects.filter(pk=attempt.pk, status='pending').update(cancel_requested=True)
    reconcile_attempt(attempt.pk)
    return payment_status(user, reference)
