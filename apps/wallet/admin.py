"""Inspection-only financial records, including journal-derived USD balances."""

from django.contrib import admin

from core.common.admin import ReadOnlyTransactionAdmin
from .models import LedgerAccount, LedgerEntry, LedgerTransaction, Wallet
from .services import account_balance


@admin.register(Wallet)
class WalletAdmin(ReadOnlyTransactionAdmin):
    list_display = ("id", "user", "store", "currency", "created_at")
    list_select_related = ("user", "store")
    search_fields = ("user__email", "store__name")


@admin.register(LedgerAccount)
class LedgerAccountAdmin(ReadOnlyTransactionAdmin):
    list_display = ("code", "wallet", "kind", "bucket", "currency", "balance_cents")
    list_filter = ("kind", "bucket")
    list_select_related = ("wallet",)
    search_fields = ("code", "wallet__user__email", "wallet__store__name")
    readonly_fields = ("balance_cents",)

    def get_readonly_fields(self, request, obj=None):
        return (*super().get_readonly_fields(request, obj), "balance_cents")

    @admin.display(description="Balance (USD cents)")
    def balance_cents(self, obj):
        """Display the journal balance without introducing a mutable cache."""
        return account_balance(obj)


class LedgerEntryInline(admin.TabularInline):
    model = LedgerEntry
    fields = ("account", "side", "amount_cents")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LedgerTransaction)
class LedgerTransactionAdmin(ReadOnlyTransactionAdmin):
    list_display = ("operation_key", "operation_type", "posted_at")
    list_filter = ("operation_type",)
    search_fields = ("operation_key",)
    inlines = (LedgerEntryInline,)


@admin.register(LedgerEntry)
class LedgerEntryAdmin(ReadOnlyTransactionAdmin):
    list_display = ("transaction", "account", "side", "amount_cents")
    list_filter = ("side",)
    list_select_related = ("transaction", "account")
    search_fields = ("transaction__operation_key", "account__code")
