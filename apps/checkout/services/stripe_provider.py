"""Bounded Stripe SDK calls; this adapter intentionally rejects live-mode configuration."""
from ipaddress import ip_address
from urllib.parse import urlsplit

import stripe
from django.conf import settings
from django.db.models import Prefetch

from apps.ecommerce.models import OrderItem, ProductImage
from rest_framework.exceptions import APIException


class PaymentUnavailable(APIException):
    status_code = 503
    default_detail = 'Stripe test checkout is temporarily unavailable. Retry the same request.'
    default_code = 'payment_unavailable'


def ensure_configured():
    """Fail closed before reservation when test credentials or the return origin are invalid."""
    origin = urlsplit(settings.STRIPE_FRONTEND_ORIGIN)
    local = origin.hostname in ('localhost', '127.0.0.1', '[::1]', '::1')
    if (not settings.STRIPE_SECRET_KEY.startswith('sk_test_')
            or not settings.STRIPE_WEBHOOK_SECRET.startswith('whsec_')
            or origin.scheme not in ('http', 'https') or not origin.netloc
            or (origin.scheme == 'http' and not local)
            or origin.username or origin.password or origin.query or origin.fragment
            or origin.path not in ('', '/')):
        raise PaymentUnavailable()


def client():
    """Use bounded network requests without leaking provider errors or secret keys."""
    ensure_configured()
    return stripe.StripeClient(settings.STRIPE_SECRET_KEY, max_network_retries=1,
                               http_client=stripe.RequestsClient(timeout=10))


def _image_url(image):
    """Omit local or non-HTTPS storage URLs that hosted Checkout cannot display.

    URL validation does not establish public access; storage must permit Stripe
    to fetch the image. Save this URL once with the immutable session parameters.
    """
    if not image or not image.image:
        return None
    url = image.image.url
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (parsed.scheme != 'https' or not host or parsed.username or parsed.password
                or host == 'localhost' or host.endswith(('.localhost', '.local'))):
            return None
        try:
            if not ip_address(host).is_global:
                return None
        except ValueError:
            pass  # A storage hostname; availability is the storage provider's responsibility.
    except ValueError:
        return None
    return url


def _line_items(attempt):
    """Use purchase snapshots, retaining an exact total within Stripe's 100-line limit."""
    items = list(OrderItem.objects.filter(order__checkout_id=attempt.checkout_id)
                 .select_related('order', 'product').prefetch_related(Prefetch(
                     'product__images', queryset=ProductImage.objects.order_by('-is_default', 'order', 'pk')))
                 .order_by('order__store_id', 'pk'))
    amounts = [int(item.price_at_purchase * 100) for item in items]
    if not items or sum(amount * item.quantity for amount, item in zip(amounts, items)) != int(attempt.amount * 100):
        raise PaymentUnavailable()
    visible_count = len(items) if len(items) <= 100 else 99
    lines = []
    for item, amount in zip(items[:visible_count], amounts[:visible_count]):
        product = {'name': item.product_name_at_purchase,
                   'description': f'Sold by {item.order.store_name_at_purchase}'}
        image = next(iter(item.product.images.all()), None) if item.product else None
        url = _image_url(image)
        if url:
            product['images'] = [url]
        lines.append({'price_data': {'currency': 'usd', 'unit_amount': amount,
                                      'product_data': product}, 'quantity': item.quantity})
    if visible_count < len(items):
        remaining = items[visible_count:]
        lines.append({'price_data': {
            'currency': 'usd',
            'unit_amount': sum(amount * item.quantity for amount, item in zip(amounts[visible_count:], remaining)),
            'product_data': {
                'name': f'{len(remaining)} additional products',
                'description': 'Combined remaining items. See your checkout review for the full product list.',
            },
        }, 'quantity': 1})
    return lines


def session_parameters(attempt):
    """Snapshot stable request parameters so a timeout retry cannot alter Stripe's key payload."""
    ensure_configured()
    reference = str(attempt.checkout.reference)
    return_url = f"{settings.STRIPE_FRONTEND_ORIGIN.rstrip('/')}/checkout/payment/{reference}"
    return {
        'mode': 'payment',
        'adaptive_pricing': {'enabled': False},
        'success_url': return_url, 'cancel_url': return_url,
        'client_reference_id': reference,
        'metadata': {'attempt_reference': str(attempt.reference), 'checkout_reference': reference},
        'expires_at': int(attempt.expires_at.timestamp()),
        'line_items': _line_items(attempt),
    }


def create_session(attempt):
    """Reuse one deterministic key for every recovery of this exact session creation."""
    return client().v1.checkout.sessions.create(
        attempt.session_parameters, options={
            'idempotency_key': f'checkout:{attempt.reference}'},
    ).to_dict()


def retrieve_session(session_id):
    """Read current state and intent so stale webhook ordering cannot regress payment."""
    return client().v1.checkout.sessions.retrieve(session_id, {'expand': ['payment_intent']}).to_dict()


def expire_session(session_id):
    """Ask Stripe to close a session; callers must retrieve after a race or failure."""
    return client().v1.checkout.sessions.expire(session_id).to_dict()


def verify_webhook(body, signature):
    """Verify raw bytes before saving the minimal webhook inbox record."""
    ensure_configured()
    return stripe.Webhook.construct_event(body, signature, settings.STRIPE_WEBHOOK_SECRET).to_dict()
