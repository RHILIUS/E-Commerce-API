from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import (
    Attribute, AttributeValue, Category, CategoryAttribute,
    Product, ProductAttributeValue, ProductImage,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "taxonomy_id", "parent", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "taxonomy_id")
    autocomplete_fields = ("parent",)


@admin.register(Attribute)
class AttributeAdmin(admin.ModelAdmin):
    search_fields = ("name", "taxonomy_id")
    list_display = ("name", "taxonomy_id")


@admin.register(AttributeValue)
class AttributeValueAdmin(admin.ModelAdmin):
    list_display = ("name", "attribute", "taxonomy_id")
    search_fields = ("name", "taxonomy_id", "attribute__name")
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
    list_display = ("name", "store", "category", "status", "price", "stock", "created_at")
    list_filter = ("status", "store", "category")
    search_fields = ("name", "short_description", "store__name")
    autocomplete_fields = ("store", "category")
    readonly_fields = ("created_at", "updated_at")
    inlines = (ProductImageInline,)


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ("product", "image", "is_default", "order", "created_at")
    list_filter = ("is_default",)
    search_fields = ("product__name", "alt_text")
    autocomplete_fields = ("product",)
    readonly_fields = ("created_at", "updated_at")
