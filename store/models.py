from django.conf import settings
from django.db import models

from core.common.models import TimeStampedModel


class Store(TimeStampedModel):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    country_code = models.CharField(max_length=2)

    profile_image = models.ImageField(
        upload_to="stores/profile/",
        blank=True,
    )
    profile_image_crop = models.JSONField(default=dict, blank=True)

    banner_image = models.ImageField(
        upload_to="stores/banner/",
        blank=True,
    )
    banner_image_crop = models.JSONField(default=dict, blank=True)

    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="StoreMember",
        related_name="member_stores",
    )

    def __str__(self):
        return self.name


class StoreMemberRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    STAFF = "staff", "Staff"


class StoreMember(TimeStampedModel):
    Role = StoreMemberRole

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="store_memberships",
    )

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="memberships",
    )

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.STAFF,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "store"],
                name="unique_user_store_member",
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=StoreMemberRole.values),
                name="store_member_valid_role",
            ),
        ]

    def __str__(self):
        return f"User {self.user_id} - Store {self.store_id} ({self.role})"
