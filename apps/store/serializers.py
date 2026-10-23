from rest_framework import serializers

from apps.ecommerce.serializers import ProductCatalogSerializer
from .models import Store, StoreAddress


class StoreProductSerializer(ProductCatalogSerializer):
    stock = serializers.IntegerField(read_only=True)

    class Meta(ProductCatalogSerializer.Meta):
        fields = ProductCatalogSerializer.Meta.fields + ("stock",)


class StoreProductsSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = StoreProductSerializer(many=True)


class StoreCategorySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    product_count = serializers.IntegerField()


class StoreAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreAddress
        fields = ("country_code", "address_line1", "address_line2", "city", "region", "postal_code")


class PublicStoreSerializer(serializers.ModelSerializer):
    address = StoreAddressSerializer(read_only=True, allow_null=True)
    listed_product_count = serializers.IntegerField(read_only=True)
    items_ordered = serializers.IntegerField(read_only=True)
    categories = StoreCategorySerializer(many=True, read_only=True)

    class Meta:
        model = Store
        fields = (
            "id", "name", "description", "profile_image", "banner_image", "created_at",
            "public_email", "public_phone", "address", "listed_product_count", "items_ordered", "categories",
        )


class StoreHomeSerializer(serializers.Serializer):
    new_products = StoreProductSerializer(many=True)
    trending_products = StoreProductSerializer(many=True)
    most_ordered = StoreProductSerializer(many=True)


class StoreProductQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    sort = serializers.ChoiceField(choices=["latest", "oldest", "popular", "most_ordered"], default="latest")
    category = serializers.IntegerField(required=False, min_value=1)
