"""Customer order history and refund response contract."""
from rest_framework import serializers
from apps.checkout.serializers import ConfirmationOrderSerializer, PreviewMoneySerializer


class OrderFilterSerializer(serializers.Serializer):
    tab = serializers.ChoiceField(choices=['all', 'to_ship', 'to_receive', 'cancelled', 'delivered', 'completed'], default='all')


class OrderRefundSerializer(serializers.Serializer):
    status = serializers.CharField()
    amount = PreviewMoneySerializer()
    needs_review = serializers.BooleanField()


class UserOrderSerializer(ConfirmationOrderSerializer):
    created_at = serializers.DateTimeField()
    concluded_at = serializers.DateTimeField(allow_null=True)
    checkout_reference = serializers.UUIDField()
    payment_pending = serializers.BooleanField()
    can_cancel = serializers.BooleanField()
    refund = OrderRefundSerializer(allow_null=True)


class UserOrderListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = UserOrderSerializer(many=True)


class OrderEventSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    event_type = serializers.CharField()
    label = serializers.CharField(source='get_event_type_display')
    occurred_at = serializers.DateTimeField()
    actor_role = serializers.CharField()
    is_backfilled = serializers.BooleanField()


class OrderHistorySerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    current_status = serializers.CharField()
    is_partial = serializers.BooleanField()
    events = OrderEventSerializer(many=True)
