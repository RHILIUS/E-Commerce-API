"""Periodic database-driven Stripe recovery; safe to run concurrently or after downtime."""
from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from apps.checkout.models import PaymentAttempt, ProviderEvent
from apps.checkout.services.events import process_event
from apps.checkout.services.online import reconcile_attempt


class Command(BaseCommand):
    help = 'Reconcile pending Stripe test payments and unfinished webhook events. Schedule every minute.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        """Bound each batch, rotate failed rows, and report safe IDs without provider secrets."""
        limit = max(1, min(options['limit'], 1000))
        failures = 0
        events = list(ProviderEvent.objects.exclude(processing_status='processed').order_by(
            F('last_attempt_at').asc(nulls_first=True), 'pk').values_list('pk', flat=True)[:limit])
        for pk in events:
            try:
                process_event(pk)
            except Exception:
                failures += 1
                self.stderr.write(f'Event {pk} remains unresolved; inspect its admin record.')
        attempts = list(PaymentAttempt.objects.filter(status='pending').order_by('updated_at', 'pk').values_list('pk', flat=True)[:limit])
        for pk in attempts:
            try:
                reconcile_attempt(pk)
            except Exception:
                failures += 1
                self.stderr.write(f'Attempt {pk} remains unresolved; inspect its admin record.')
            finally:
                PaymentAttempt.objects.filter(pk=pk, status='pending').update(updated_at=timezone.now())
        self.stdout.write(f'Checked {len(events)} events and {len(attempts)} attempts; {failures} unresolved operations.')
