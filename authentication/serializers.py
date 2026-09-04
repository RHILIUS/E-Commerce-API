import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from authentication.exceptions import ExpiredOTPError, MaxAttemptError
from authentication.models import OTPVerificationRecord

User = get_user_model()


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
            # A successfully verified OTP is single-use.
            elif otp_record.is_verified:
                validation_error = serializers.ValidationError(
                    {"otp": "Invalid OTP"}
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
            else:
                otp_record.is_verified = True
                otp_record.verified_at = timezone.now()
                # Invalidate/consume the OTP record
                otp_record.otp_hash = make_password(None)
                otp_record.save(
                    update_fields=["is_verified", "verified_at", "otp_hash"]
                )

        # Raise after leaving the atomic block so intentional updates/deletions
        # are committed rather than rolled back with the exception.
        if validation_error is not None:
            raise validation_error

        return attrs
