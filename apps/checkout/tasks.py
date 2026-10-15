from celery import shared_task
from django.core.management import call_command


@shared_task
def reconcile_stripe_payments():
    """Run the existing bounded Stripe recovery command."""
    call_command("reconcile_stripe", limit=100)