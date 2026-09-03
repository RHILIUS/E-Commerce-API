import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from authentication.exceptions import ExpiredOTPError, MaxAttemptError
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


class VerifyOTPSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    otp = serializers.CharField(required=True, max_length=6, min_length=6)

    def validate_otp(self, value: str):
        if not value.isdigit():
            raise serializers.ValidationError("OTP must contain only digits.")

        return value

    def validate_email(self, email: str):
        return email.strip().lower()

    def validate(self, attrs):
        email = attrs["email"]
        otp = attrs["otp"]
        validation_error = None

        # Lock the record so concurrent failures cannot overwrite each other's
        # failed-attempt increments and bypass the attempt limit.
        with transaction.atomic():
            otp_record = (
                OTPVerificationRecord.objects
                .select_for_update()
                .filter(email=email)
                .first()
            )

            # Does OTP verification record exist?
            if otp_record is None:
                validation_error = serializers.ValidationError(
                    {"email": "Invalid OTP or email."}
                )
            # OTP Expired?
            elif timezone.now() >= otp_record.expires_at:
                otp_record.delete()
                validation_error = ExpiredOTPError()
            # OTP Match?
            elif not check_password(otp, otp_record.otp_hash):
                otp_record.failed_attempts += 1

                # Too many attempts?
                if (
                    otp_record.failed_attempts
                    >= settings.OTP_MAX_VERIFICATION_ATTEMPTS
                ):
                    otp_record.delete()
                    validation_error = MaxAttemptError()

                else:
                    otp_record.save(update_fields=["failed_attempts"])
                    validation_error = serializers.ValidationError(
                        {"otp": "Invalid OTP"}
                    )

        # Raise after leaving the atomic block so intentional updates/deletions
        # are committed rather than rolled back with the exception.
        if validation_error is not None:
            raise validation_error

        return attrs
