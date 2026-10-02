"""Transactional recipient creation and default selection."""

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import RecipientInfo


@transaction.atomic
def create_recipient(user, validated_data):
    """Serialize creation per customer, including when they have no recipient rows.

    Lock the customer before inspecting/changing defaults so concurrent first
    recipients and replacement defaults respect the partial unique constraint.
    Caller supplies serializer-validated recipient fields, never ownership.
    """
    get_user_model().objects.select_for_update().only("pk").get(pk=user.pk)
    recipients = RecipientInfo.objects.filter(user_id=user.pk)
    data = dict(validated_data)
    data["is_default"] = not recipients.exists() or data.get("is_default", False)
    if data["is_default"]:
        recipients.filter(is_default=True).update(is_default=False)
    return RecipientInfo.objects.create(user_id=user.pk, **data)
