"""Saved-recipient API shapes; ownership is never accepted from request data."""

from rest_framework import serializers

from core.common.contact import validate_country_code, validate_phone_number
from .models import RecipientInfo


class RecipientSerializer(serializers.ModelSerializer):
    is_default = serializers.BooleanField(required=False, default=False)

    class Meta:
        model = RecipientInfo
        fields = (
            "id", "name", "phone_number", "country_code", "address_line1",
            "address_line2", "city", "region", "postal_code", "is_default",
        )
        read_only_fields = ("id",)
        extra_kwargs = {
            "address_line2": {"required": False, "allow_blank": True, "default": ""},
            "region": {"required": False, "allow_blank": True, "default": ""},
            "postal_code": {"required": False, "allow_blank": True, "default": ""},
        }

    def validate_phone_number(self, value):
        return validate_phone_number(value)

    def validate_country_code(self, value):
        return validate_country_code(value)


class RecipientListSerializer(serializers.Serializer):
    recipients = RecipientSerializer(many=True)


class RecipientDeleteSerializer(serializers.Serializer):
    replacement_default_id = serializers.IntegerField(required=False, min_value=1)
