from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from .models import Product, ProductImage
from .services.display_prices import convert_display_price


class DisplayPriceSerializer(serializers.Serializer):
    amount = serializers.CharField(help_text="Converted decimal amount, rounded to the currency's minor unit. Display only.")
    currency = serializers.CharField(help_text="ISO 4217 display currency; original store currency when conversion is unavailable.")


class CatalogImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ("image", "alt_text", "crop")


class ProductCatalogSerializer(serializers.ModelSerializer):
    currency = serializers.CharField(source="store.currency", read_only=True)
    default_image = serializers.SerializerMethodField()
    display_price = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ("id", "name", "short_description", "price", "currency", "display_price", "sold_count", "default_image")
        extra_kwargs = {"price": {"help_text": "Original price in the store's currency; unchanged by display conversion."}}

    @extend_schema_field(DisplayPriceSerializer)
    def get_display_price(self, obj):
        """Reuse the page's conversion context without per-product rate lookups."""
        source = obj.store.currency
        currency, rate = self.context.get("display_rates", {}).get(source, (source, 1))
        return convert_display_price(obj.price, currency, rate)

    @extend_schema_field(CatalogImageSerializer(allow_null=True))
    def get_default_image(self, obj):
        """Serialize the first prefetched default image, or null if none exists."""
        if not obj.catalog_images:
            return None
        return CatalogImageSerializer(obj.catalog_images[0], context=self.context).data


class PaginatedProductsSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = ProductCatalogSerializer(many=True)
