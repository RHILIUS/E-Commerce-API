"""Explicit cart request and response contracts; prices stay decimal strings."""
from rest_framework import serializers

from .serializers import CatalogImageSerializer, DisplayPriceSerializer


class CartQuantitySerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1, max_value=2147483647)


class CartAddSerializer(CartQuantitySerializer):
    product_id = serializers.IntegerField(min_value=1)


class CartRemoveSerializer(serializers.Serializer):
    item_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False, max_length=500)


class CartProductSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    status = serializers.ChoiceField(choices=['active', 'archived', 'unavailable'])
    stock = serializers.IntegerField()
    can_purchase = serializers.BooleanField()
    image = CatalogImageSerializer(allow_null=True)
    display_price = DisplayPriceSerializer(allow_null=True)


class CartItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    quantity = serializers.IntegerField()
    product = CartProductSerializer()
    subtotal = DisplayPriceSerializer(allow_null=True)


class CartStoreSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    profile_image = serializers.URLField(allow_null=True)


class StoreCartSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    store = CartStoreSerializer()
    items = CartItemSerializer(many=True)


class CartResponseSerializer(serializers.Serializer):
    carts = StoreCartSerializer(many=True)
