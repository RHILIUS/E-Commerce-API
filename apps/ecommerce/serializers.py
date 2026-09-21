from rest_framework import serializers

from .models import Product, ProductImage


class CatalogImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ("image", "alt_text", "crop")


class ProductCatalogSerializer(serializers.ModelSerializer):
    default_image = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ("id", "name", "short_description", "price", "sold_count", "default_image")

    def get_default_image(self, obj):
        """Serialize the first prefetched default image, or null if none exists."""
        if not obj.catalog_images:
            return None
        return CatalogImageSerializer(obj.catalog_images[0], context=self.context).data
