from django.urls import path

from .views import CheckoutConfirmationView, CheckoutPlacementView, CheckoutPreviewView

from .stripe_views import StripeCheckoutView, StripePaymentView, StripeCancelView, StripeWebhookView

urlpatterns = [
    path("checkouts/stripe/", StripeCheckoutView.as_view(), name="stripe-checkout"),
    path("checkouts/<uuid:reference>/payment/", StripePaymentView.as_view(), name="stripe-payment"),
    path("checkouts/<uuid:reference>/payment/cancel/", StripeCancelView.as_view(), name="stripe-cancel"),
    path("stripe/webhook/", StripeWebhookView.as_view(), name="stripe-webhook"),
    path("checkout/preview/", CheckoutPreviewView.as_view(), name="checkout-preview"),
    path("checkouts/", CheckoutPlacementView.as_view(), name="checkout-place"),
    path("checkouts/<uuid:reference>/", CheckoutConfirmationView.as_view(), name="checkout-confirmation"),
]
