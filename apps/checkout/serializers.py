"""Checkout preview inputs and the single-currency review response."""
from rest_framework import serializers

from apps.ecommerce.serializers import CatalogImageSerializer


class PreviewRequestSerializer(serializers.Serializer):
    source = serializers.ChoiceField(choices=['product', 'cart'])
    product_id = serializers.IntegerField(min_value=1, required=False)
    quantity = serializers.IntegerField(min_value=1, max_value=2147483647, required=False)
    item_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), allow_empty=False,
        max_length=500, required=False,
    )

    def validate(self, attrs):
        """Require exactly one source shape, rejecting client-owned pricing fields."""
        expected = {'source', 'product_id', 'quantity'} if attrs['source'] == 'product' else {'source', 'item_ids'}
        errors = {key: ['This field is required.'] for key in expected - attrs.keys()}
        errors.update({key: ['Unexpected field for this checkout source.'] for key in self.initial_data.keys() - expected})
        if errors:
            raise serializers.ValidationError(errors)
        if attrs['source'] == 'cart' and len(set(attrs['item_ids'])) != len(attrs['item_ids']):
            raise serializers.ValidationError({'item_ids': ['Select each cart item only once.']})
        return attrs


class PreviewMoneySerializer(serializers.Serializer):
    amount = serializers.CharField(help_text='Decimal string rounded to the currency minor unit.')
    currency = serializers.CharField()


class PreviewProductSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    image = CatalogImageSerializer(allow_null=True)


class PreviewItemSerializer(serializers.Serializer):
    cart_item_id = serializers.IntegerField(allow_null=True)
    product = PreviewProductSerializer()
    quantity = serializers.IntegerField()
    available_stock = serializers.IntegerField()
    unit_price = PreviewMoneySerializer(allow_null=True)
    subtotal = PreviewMoneySerializer(allow_null=True)


class PreviewStoreSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    profile_image = serializers.URLField(allow_null=True)


class PreviewGroupSerializer(serializers.Serializer):
    store = PreviewStoreSerializer()
    items = PreviewItemSerializer(many=True)
    subtotal = PreviewMoneySerializer(allow_null=True)


class PreviewIssueSerializer(serializers.Serializer):
    code = serializers.ChoiceField(choices=['product_unavailable', 'insufficient_stock', 'invalid_price', 'amount_too_large'])
    product_id = serializers.IntegerField(allow_null=True)
    cart_item_id = serializers.IntegerField(allow_null=True)
    message = serializers.CharField()


class PreviewResponseSerializer(serializers.Serializer):
    source = serializers.ChoiceField(choices=['product', 'cart'])
    currency = serializers.CharField()
    stores = PreviewGroupSerializer(many=True)
    total = PreviewMoneySerializer(allow_null=True)
    can_place_order = serializers.BooleanField()
    issues = PreviewIssueSerializer(many=True)
    preview_token = serializers.CharField(allow_null=True)
    expires_at = serializers.DateTimeField(allow_null=True)
