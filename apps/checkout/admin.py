from django.contrib import admin

from core.common.admin import ReadOnlyTransactionAdmin
from .models import Checkout, PaymentAttempt


@admin.register(Checkout)
class CheckoutAdmin(ReadOnlyTransactionAdmin):
    list_display = ("reference", "user", "payment_method", "status", "currency", "total_amount", "created_at")
    list_filter = ("payment_method", "status", "currency")
    search_fields = ("reference", "user__email")
    list_select_related = ("user",)


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(ReadOnlyTransactionAdmin):
    list_display = ("reference", "checkout", "status", "amount", "currency", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("reference", "stripe_checkout_session_id", "stripe_payment_intent_id")
    list_select_related = ("checkout",)
