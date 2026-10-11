from django.contrib import admin
from django.core.exceptions import ValidationError

from core.common.admin import ReadOnlyTransactionAdmin

from .models import (
    Attribute, AttributeValue, Category, CategoryAttribute,
    Order, OrderEvent, OrderItem, Cart, CartItem,
    Product, ProductAttributeValue, ProductImage,
)


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    fields = ('product', 'quantity')
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Cart)
class CartAdmin(ReadOnlyTransactionAdmin):
    list_display = ('id', 'user', 'store', 'updated_at')
    list_select_related = ('user', 'store')
    search_fields = ('user__email', 'store__name')
    readonly_fields = ('user', 'store', 'created_at', 'updated_at')
    inlines = (CartItemInline,)

    def has_add_permission(self, request):
        return False


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "taxonomy_id", "parent", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "taxonomy_id")
    autocomplete_fields = ("parent",)


@admin.register(Attribute)
class AttributeAdmin(admin.ModelAdmin):
    search_fields = ("name", "taxonomy_id", "handle")
    list_display = ("name", "handle", "taxonomy_id")


@admin.register(AttributeValue)
class AttributeValueAdmin(admin.ModelAdmin):
    list_display = ("name", "attribute", "taxonomy_id")
    search_fields = ("name", "taxonomy_id", "attribute__name", "attribute__handle")
    autocomplete_fields = ("attribute",)


@admin.register(CategoryAttribute)
class CategoryAttributeAdmin(admin.ModelAdmin):
    list_display = ("category", "attribute")
    search_fields = ("category__name", "attribute__name")
    autocomplete_fields = ("category", "attribute")

    def get_deleted_objects(self, objs, request):
        """Show used associations as protected on the admin deletion screen."""
        deleted, counts, permissions, protected = super().get_deleted_objects(objs, request)
        for association in objs:
            try:
                association.validate_removal()
            except ValidationError:
                protected.append(f"{association.category}: {association.attribute}")
        return deleted, counts, permissions, protected


@admin.register(ProductAttributeValue)
class ProductAttributeValueAdmin(admin.ModelAdmin):
    list_display = ("product", "attribute", "value")
    search_fields = ("product__name", "attribute__name", "value__name")
    autocomplete_fields = ("product", "attribute", "value")


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ("image", "is_default", "order", "alt_text", "crop")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "category", "status", "price", "store_currency", "stock", "created_at")
    list_select_related = ("store", "category")
    list_filter = ("status", "store", "category")
    search_fields = ("name", "short_description", "store__name")
    autocomplete_fields = ("store", "category")
    readonly_fields = ("store_currency", "created_at", "updated_at")
    inlines = (ProductImageInline,)

    @admin.display(description="Price currency")
    def store_currency(self, obj):
        """Identify the currency of the product's stored price in the admin."""
        return obj.store.currency if obj and obj.store_id else "Set automatically from the selected store"


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ("product", "image", "is_default", "order", "created_at")
    list_filter = ("is_default",)
    search_fields = ("product__name", "alt_text")
    autocomplete_fields = ("product",)
    readonly_fields = ("created_at", "updated_at")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ("product", "product_name_at_purchase", "quantity", "original_unit_price",
              "price_at_purchase", "source_cart_item", "subtotal")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Subtotal")
    def subtotal(self, obj):
        """Display the item's total using its recorded purchase price."""
        if obj.price_at_purchase is None or obj.quantity is None:
            return None
        return obj.get_subtotal()


class OrderEventInline(admin.TabularInline):
    model = OrderEvent
    extra = 0
    fields = ('event_type', 'occurred_at', 'actor_role', 'actor', 'is_backfilled')
    readonly_fields = fields
    can_delete = False
    ordering = ('occurred_at', 'pk')

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(ReadOnlyTransactionAdmin):
    list_display = ("id", "checkout", "store", "user", "status", "payment_status", "created_at")
    list_filter = ("status", "payment_status", "created_at")
    search_fields = ("=id", "user__email", "user__full_name", "recipient_info__name")
    autocomplete_fields = ("user",)
    list_select_related = ("user", "recipient_info", "checkout", "store")
    ordering = ("-created_at", "-pk")
    inlines = (OrderItemInline, OrderEventInline)

    def get_readonly_fields(self, request, obj=None):
        """Show the computed total alongside protected purchase fields."""
        return (*super().get_readonly_fields(request, obj), "total")

    @admin.display(description="Order total")
    def total(self, obj):
        """Show the sum of recorded item subtotals on an existing order."""
        return obj.get_total() if obj.pk else None
