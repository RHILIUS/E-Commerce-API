from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.store.models import Store

from .models import Product, ProductImage
from .services.display_prices import convert_display_price


class DisplayPriceSerializer(serializers.Serializer):
    amount = serializers.CharField(
        help_text="Converted decimal amount, rounded to the currency's minor unit. Display only.")
    currency = serializers.CharField(
        help_text="ISO 4217 display currency; original store currency when conversion is unavailable.")


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
        fields = ("id", "name", "short_description", "price",
                  "currency", "display_price", "sold_count", "default_image")
        extra_kwargs = {"price": {
            "help_text": "Original price in the store's currency; unchanged by display conversion."}}

    @extend_schema_field(DisplayPriceSerializer)
    def get_display_price(self, obj):
        """Reuse the page's conversion context without per-product rate lookups."""
        source = obj.store.currency
        currency, rate = self.context.get(
            "display_rates", {}).get(source, (source, 1))
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


class ProductDetailImageSerializer(CatalogImageSerializer):
    class Meta(CatalogImageSerializer.Meta):
        fields = ("id", "image", "alt_text", "crop", "is_default", "order")


class ProductStoreSerializer(serializers.ModelSerializer):
    listed_product_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Store
        fields = (
            "id", "name", "profile_image", "profile_image_crop",
            "listed_product_count", "created_at",
        )


class ProductDetailSerializer(ProductCatalogSerializer):
    images = ProductDetailImageSerializer(many=True, read_only=True)
    store = serializers.SerializerMethodField()
    can_purchase = serializers.SerializerMethodField()

    class Meta(ProductCatalogSerializer.Meta):
        fields = ProductCatalogSerializer.Meta.fields + (
            "description", "stock", "status", "archived_at", "images",
            "store", "can_purchase",
        )

    @extend_schema_field(CatalogImageSerializer(allow_null=True))
    def get_default_image(self, obj):
        """Select the first marked default from the ordered, prefetched gallery."""
        image = next((image for image in obj.images.all()
                     if image.is_default), None)
        return CatalogImageSerializer(image, context=self.context).data if image else None

    @extend_schema_field(ProductStoreSerializer)
    def get_store(self, obj):
        """Expose public store metadata and its active listing count."""
        obj.store.listed_product_count = self.context["listed_product_count"]
        return ProductStoreSerializer(obj.store, context=self.context).data

    @extend_schema_field(serializers.BooleanField)
    def get_can_purchase(self, obj):
        """Report product eligibility; checkout must recheck stock and status."""
        return obj.status == Product.Status.ACTIVE and obj.stock > 0
