from django.contrib import admin

from core.common.admin import ReadOnlyTransactionAdmin
from .models import Checkout, PaymentAttempt, ProviderEvent, OrderRefund


@admin.register(Checkout)
class CheckoutAdmin(ReadOnlyTransactionAdmin):
    list_display = ("reference", "user", "payment_method", "status", "currency", "total_amount", "created_at")
    list_filter = ("payment_method", "status", "currency")
    search_fields = ("reference", "user__email")
    list_select_related = ("user",)


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(ReadOnlyTransactionAdmin):
    list_display = ("reference", "checkout", "status", "amount", "currency", "cancel_requested", "failure_code", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("reference", "stripe_checkout_session_id", "stripe_payment_intent_id")
    list_select_related = ("checkout",)


@admin.register(ProviderEvent)
class ProviderEventAdmin(ReadOnlyTransactionAdmin):
    list_display = ("event_id", "event_type", "processing_status", "last_error", "received_at", "last_attempt_at")
    list_filter = ("processing_status", "environment", "event_type")
    search_fields = ("event_id", "session_id", "attempt_reference")


@admin.register(OrderRefund)
class OrderRefundAdmin(ReadOnlyTransactionAdmin):
    list_display = ("reference", "order", "amount_cents", "status", "failure_code", "created_at")
    list_filter = ("status",)
    search_fields = ("reference", "stripe_refund_id")
