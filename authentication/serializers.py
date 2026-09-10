import re

import phonenumbers
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from authentication.exceptions import (
    EmailAlreadyTakenError,
    ExpiredOTPError,
    InvalidRegistrationTokenError,
    MaxAttemptError,
)
from authentication.models import OTPVerificationRecord
from authentication.services.registration_token_service import (
    REGISTRATION_TOKEN_COOKIE_NAME,
    InvalidRegistrationToken,
    validate_registration_token,
)

User = get_user_model()


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(min_length=3, max_length=30)
    full_name = serializers.CharField(max_length=100)
    phone_number = serializers.CharField(max_length=20)
    country_code = serializers.CharField(min_length=2, max_length=2)
    address_line1 = serializers.CharField(max_length=150)
    address_line2 = serializers.CharField(
        required=False, allow_blank=True, max_length=150
    )
    city = serializers.CharField(max_length=100)
    region = serializers.CharField(
        required=False, allow_blank=True, max_length=100
    )
    postal_code = serializers.CharField(
        required=False, allow_blank=True, max_length=20
    )
    password = serializers.CharField(
        min_length=8,
        max_length=64,
        trim_whitespace=False,
        write_only=True,
    )

    def to_internal_value(self, data):
        """Validate the registration token before validating account details."""
        token = self.context["request"].COOKIES.get(
            REGISTRATION_TOKEN_COOKIE_NAME)
        if not token:
            raise InvalidRegistrationTokenError()

        try:
            email = validate_registration_token(token)
        except InvalidRegistrationToken as error:
            raise InvalidRegistrationTokenError() from error

        attrs = super().to_internal_value(data)
        attrs["email"] = email
        return attrs

    def validate_username(self, value):
        if re.search(r"\s", value):
            raise serializers.ValidationError(
                "Username must not contain spaces."
            )
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise serializers.ValidationError(
                "Username may contain only letters, numbers, underscores, "
                "hyphens, and periods."
            )
        if not re.match(r"[A-Za-z0-9]", value):
            raise serializers.ValidationError(
                "Username must start with a letter or number."
            )
        if re.search(r"[_.-]{2}", value):
            raise serializers.ValidationError(
                "Username must not contain consecutive special characters."
            )

        # Check the uniqueness of the username
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Username is already taken")

        return value

    def validate_phone_number(self, value):
        if not re.fullmatch(r"\+[1-9][0-9]{1,14}", value):
            raise serializers.ValidationError(
                "Phone number must use E.164 format, for example +639171234567."
            )

        try:
            phone_number = phonenumbers.parse(value, None)
        except phonenumbers.NumberParseException as error:
            raise serializers.ValidationError(
                "Enter a valid phone number."
            ) from error

        if not phonenumbers.is_valid_number(phone_number):
            raise serializers.ValidationError("Enter a valid phone number.")

        return phonenumbers.format_number(
            phone_number, phonenumbers.PhoneNumberFormat.E164
        )

    def validate_country_code(self, value):
        country_code = value.upper()
        if country_code not in phonenumbers.SUPPORTED_REGIONS:
            raise serializers.ValidationError(
                "Enter a valid two-letter country code."
            )

        return country_code

    def validate_password(self, value):
        if not re.search(r"[A-Z]", value):
            raise serializers.ValidationError(
                "Password must contain at least one uppercase letter."
            )
        if not re.search(r"[a-z]", value):
            raise serializers.ValidationError(
                "Password must contain at least one lowercase letter."
            )
        if not re.search(r"[0-9]", value):
            raise serializers.ValidationError(
                "Password must contain at least one number."
            )

        return value

    def validate(self, attrs):
        """Recheck the verified email's availability after field validation."""
        # to_internal_value already validated the cookie and supplied this email.
        if User.objects.filter(email__iexact=attrs["email"]).exists():
            raise EmailAlreadyTakenError()
        return attrs


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
