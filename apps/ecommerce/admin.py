from django.contrib import admin

from .models import Product, ProductImage


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ("image", "is_default", "order", "alt_text", "crop")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "status", "price", "stock", "created_at")
    list_filter = ("status", "store")
    search_fields = ("name", "short_description", "store__name")
    autocomplete_fields = ("store",)
    readonly_fields = ("created_at", "updated_at")
    inlines = (ProductImageInline,)


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ("product", "image", "is_default", "order", "created_at")
    list_filter = ("is_default",)
    search_fields = ("product__name", "alt_text")
    autocomplete_fields = ("product",)
    readonly_fields = ("created_at", "updated_at")
