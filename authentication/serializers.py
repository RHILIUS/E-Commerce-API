import re
from csv import Error

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import APIException
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from authentication.models import OTPVerificationRecord

User = get_user_model()


class SignupSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate_username(self, value):

        # Field validation
        if len(value) < 3:
            raise serializers.ValidationError(
                "The length of the username must be atleast 3 characters")
        if len(value) > 30:
            raise serializers.ValidationError(
                "The maximum length of username is 30 characters only")
        if not re.match(r"^[a-zA-Z0-9]", value):
            raise serializers.ValidationError(
                "Username must start with a letter or a number")
        if not re.match(r"^[a-zA-Z0-9_.\-]+$", value):
            raise serializers.ValidationError(
                "Username can only contain letters, numbers, _ - and .")
        if re.search(r"[_.\-]{2}", value):
            raise serializers.ValidationError(
                "Username cannot have consecutive special characters (e.g. __ or .-)")

        # Uniqueness validation
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("The username is already taken")

        return value

    def validate_password(self, value):
        if len(value) < 8:
            raise serializers.ValidationError(
                "Password should not be less than 8 characters")
        if len(value) > 64:
            raise serializers.ValidationError(
                "Password should not be more than 64 characters")

        return value

    def create(self, validated_data):
        created_user = User.objects.create_user(
            username=validated_data['username'], password=validated_data['password'])

        return created_user


class UserPublicSerializer(serializers.ModelSerializer):
    fullName = serializers.CharField(source="full_name")

    class Meta:
        model = User
        fields = ["id", "fullName", "email"]


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        data["user"] = UserPublicSerializer(self.user).data
        return data


class SendOTPSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)

    def validate_email(self, email):

        # normalize email
        email = email.strip().lower()

        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                "This email is already registered. Please log in or use a different email")

        return email


class ExpiredOTPError(APIException):
    status_code = status.HTTP_410_GONE
    default_detail = {"email": ["Expired or invalid OTP"]}
    default_code = "otp_expired"


class VerifyOTPSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    otp = serializers.CharField(required=True, max_length=6, min_length=6)

    def validate_otp(self, value: str):
        if not value.isdigit():
            raise serializers.ValidationError("OTP must contain only digits.")

        return value

    def validate_email(self, email: str):

        # normalize email
        email = email.strip().lower()

        otp_record = OTPVerificationRecord.objects.filter(email=email).first()

        if otp_record is None:
            raise serializers.ValidationError(
                "Invalid OTP or email.")

        # Check OTP Expiration
        now = timezone.now()

        # Invalidate the record and raise error if the record has already expired
        if now >= otp_record.expires_at:
            otp_record.delete()
            raise ExpiredOTPError()

        return email
