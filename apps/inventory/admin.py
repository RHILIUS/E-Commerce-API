from django.contrib import admin

from core.common.admin import ReadOnlyTransactionAdmin
from .models import StockReservation


@admin.register(StockReservation)
class StockReservationAdmin(ReadOnlyTransactionAdmin):
    list_display = ("id", "order_item", "product", "quantity", "status", "expires_at")
    list_filter = ("status",)
    search_fields = ("product__name",)
    list_select_related = ("order_item", "product")
