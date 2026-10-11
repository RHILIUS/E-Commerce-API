"""Recipient snapshots shared by order validation and record creation."""

from django.core.exceptions import ValidationError

RECIPIENT_FIELDS = (
    "name", "phone_number", "country_code", "address_line1", "address_line2",
    "city", "region", "postal_code",
)


def recipient_snapshot(recipient):
    """Copy contact and address values so later recipient edits cannot change an order."""
    return {field: getattr(recipient, field) for field in RECIPIENT_FIELDS}


def validate_recipient_snapshot(value):
    """Require the saved recipient shape, allowing optional address parts to be empty."""
    if not isinstance(value, dict) or set(value) != set(RECIPIENT_FIELDS):
        raise ValidationError("Provide the complete recipient snapshot.")
    if any(not isinstance(value[field], str) for field in RECIPIENT_FIELDS):
        raise ValidationError("Recipient snapshot values must be strings.")
    required = ("name", "phone_number", "country_code", "address_line1", "city")
    if any(not value[field].strip() for field in required):
        raise ValidationError("Recipient name, phone, country, address and city are required.")
