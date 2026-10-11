from django.core.management.base import BaseCommand
from apps.checkout.models import OrderRefund
from apps.checkout.services.refunds import reconcile_refund


class Command(BaseCommand):
    help = 'Reconcile unresolved Stripe test order refunds. Safe to run periodically.'

    def handle(self, *args, **options):
        for pk in OrderRefund.objects.filter(status__in=['pending', 'requires_action']).values_list('pk', flat=True).iterator():
            reconcile_refund(pk)
        self.stdout.write('Order refund reconciliation finished.')
