"""Contact validation shared by registration and recipient API serializers."""

import re

import phonenumbers
from rest_framework import serializers


def validate_phone_number(value):
    """Validate and normalize an international E.164 number without requiring uniqueness."""
    if not re.fullmatch(r"\+[1-9][0-9]{1,14}", value):
        raise serializers.ValidationError(
            "Phone number must use E.164 format, for example +639171234567."
        )
    try:
        number = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException as error:
        raise serializers.ValidationError("Enter a valid phone number.") from error
    if not phonenumbers.is_valid_number(number):
        raise serializers.ValidationError("Enter a valid phone number.")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def validate_country_code(value):
    """Normalize the address country using the same supported regions as registration."""
    code = value.upper()
    if code not in phonenumbers.SUPPORTED_REGIONS:
        raise serializers.ValidationError("Enter a valid two-letter country code.")
    return code
