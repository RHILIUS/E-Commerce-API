"""Persist signed Stripe notifications before attempting recoverable local processing."""
import uuid

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.checkout.models import PaymentAttempt, ProviderEvent
from .online import PaymentMismatch, apply_session
from . import stripe_provider as provider

EVENT_TYPES = {'checkout.session.completed', 'checkout.session.expired',
               'checkout.session.async_payment_succeeded', 'checkout.session.async_payment_failed'}


def receive_event(event):
    """Save only routing identifiers, never card/customer payloads; deduplicate signed deliveries."""
    if event.get('livemode') is not False:
        raise ValidationError('Only Stripe test events are accepted.')
    if event.get('type') not in EVENT_TYPES:
        return None
    session = event.get('data', {}).get('object', {})
    reference = (session.get('metadata') or {}).get('attempt_reference', '')
    try:
        reference = str(uuid.UUID(reference))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError('Event is missing a valid payment attempt reference.') from exc
    if (not isinstance(event.get('id'), str) or len(event['id']) > 255
            or not str(session.get('id', '')).startswith('cs_test_') or len(session['id']) > 255):
        raise ValidationError('Invalid event identifiers.')
    saved, _ = ProviderEvent.objects.get_or_create(
        environment='test', event_id=event['id'],
        defaults={'event_type': event['type'], 'session_id': session['id'], 'attempt_reference': reference},
    )
    return saved


def process_event(event_id):
    """Reconcile from current provider facts; preserve failed inbox rows for scheduled retry."""
    event = ProviderEvent.objects.get(pk=event_id)
    if event.processing_status == 'processed':
        return
    ProviderEvent.objects.filter(pk=event.pk).update(last_attempt_at=timezone.now())
    try:
        attempt = PaymentAttempt.objects.get(reference=event.attempt_reference)
        ProviderEvent.objects.filter(pk=event.pk).update(payment_attempt=attempt)
        session = provider.retrieve_session(event.session_id)
        apply_session(attempt.pk, session)
    except Exception as exc:
        # Persist no exception text: provider errors may include sensitive request details.
        code = 'payment_mismatch' if isinstance(exc, PaymentMismatch) else 'processing_failed'
        ProviderEvent.objects.filter(pk=event.pk).exclude(processing_status='processed').update(
            processing_status='failed', last_error=code)
        raise
    ProviderEvent.objects.filter(pk=event.pk).update(processing_status='processed', processed_at=timezone.now(), last_error='')
