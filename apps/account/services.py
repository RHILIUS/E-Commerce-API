"""Transactional recipient creation, editing and default selection."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from .models import RecipientInfo


@transaction.atomic
def create_recipient(user, validated_data):
    """Serialize creation per customer, including when they have no recipient rows.

    Lock the customer before inspecting/changing defaults so concurrent first
    recipients and replacement defaults respect the partial unique constraint.
    Caller supplies serializer-validated recipient fields, never ownership.
    """
    get_user_model().objects.select_for_update().only("pk").get(pk=user.pk)
    recipients = RecipientInfo.objects.filter(user_id=user.pk, is_deleted=False)
    data = dict(validated_data)
    data["is_default"] = not recipients.exists() or data.get("is_default", False)
    if data["is_default"]:
        recipients.filter(is_default=True).update(is_default=False)
    return RecipientInfo.objects.create(user_id=user.pk, **data)


@transaction.atomic
def update_recipient(user, recipient_id, validated_data):
    """Update an owned address under the shared customer lock, preserving a default.

    A current default cannot be cleared directly; selecting another default
    replaces it atomically. Missing and foreign recipients both return 404.
    """
    get_user_model().objects.select_for_update().only("pk").get(pk=user.pk)
    recipient = get_object_or_404(RecipientInfo.objects.select_for_update(), pk=recipient_id, user_id=user.pk, is_deleted=False)
    if recipient.is_default and validated_data.get("is_default") is False:
        raise ValidationError({"is_default": ["Choose another default recipient before replacing this default."]})
    if validated_data.get("is_default"):
        RecipientInfo.objects.filter(user_id=user.pk, is_default=True).exclude(pk=recipient.pk).update(is_default=False)
    for field, value in validated_data.items():
        setattr(recipient, field, value)
    recipient.save()
    return recipient


@transaction.atomic
def delete_recipient(user, recipient_id, replacement_default_id=None):
    """Remove an address from future use while preserving historical order links.

    Use the same customer lock as creation, editing and placement. Repeated
    removal is safe; an already removed address cannot change today's default.
    """
    get_user_model().objects.select_for_update().only("pk").get(pk=user.pk)
    recipient = get_object_or_404(RecipientInfo.objects.select_for_update(), pk=recipient_id, user_id=user.pk)
    active = RecipientInfo.objects.filter(user_id=user.pk, is_deleted=False)
    if not recipient.is_deleted:
        others = active.exclude(pk=recipient.pk)
        replacement = None
        if recipient.is_default and others.exists():
            replacement = others.filter(pk=replacement_default_id).first()
            if replacement is None:
                raise ValidationError({"replacement_default_id": ["Choose another saved recipient as your default."]})
        elif replacement_default_id is not None:
            raise ValidationError({"replacement_default_id": ["A replacement is only needed when deleting the default recipient with other saved recipients."]})
        recipient.is_default = False
        recipient.is_deleted = True
        recipient.save(update_fields=["is_default", "is_deleted"])
        if replacement is not None:
            replacement.is_default = True
            replacement.save(update_fields=["is_default"])
    return list(active.order_by("-is_default", "pk"))
