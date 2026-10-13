"""Account API shapes; ownership is never accepted from request data."""

from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.authentication.models import UserAddress
from core.common.contact import validate_country_code, validate_phone_number
from ..models import RecipientInfo


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


class ProfileAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserAddress
        fields = ("country_code", "address_line1", "address_line2", "city", "region", "postal_code")
        extra_kwargs = {
            field: {"required": False, "allow_blank": True, "default": ""}
            for field in ("address_line2", "region", "postal_code")
        }

    def validate_country_code(self, value):
        return validate_country_code(value)


class ProfileSerializer(serializers.ModelSerializer):
    address = ProfileAddressSerializer(read_only=True, allow_null=True)

    class Meta:
        model = get_user_model()
        fields = ("id", "username", "email", "full_name", "phone_number",
                  "address", "profile_image", "profile_image_crop")
        read_only_fields = fields


class ProfileUpdateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=100)
    phone_number = serializers.CharField(max_length=20)
    address = ProfileAddressSerializer()

    def validate_phone_number(self, value):
        value = validate_phone_number(value)
        if get_user_model().objects.filter(phone_number=value).exclude(
                pk=self.context["request"].user.pk).exists():
            raise serializers.ValidationError("Phone number is already taken.")
        return value
