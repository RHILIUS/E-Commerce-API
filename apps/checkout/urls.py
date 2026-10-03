from django.urls import path

from .views import CheckoutConfirmationView, CheckoutPlacementView, CheckoutPreviewView

urlpatterns = [
    path("checkout/preview/", CheckoutPreviewView.as_view(), name="checkout-preview"),
    path("checkouts/", CheckoutPlacementView.as_view(), name="checkout-place"),
    path("checkouts/<uuid:reference>/", CheckoutConfirmationView.as_view(), name="checkout-confirmation"),
]
