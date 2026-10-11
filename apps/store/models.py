from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.common.currencies import currency_for_country
from core.common.images import compress_uploaded_image
from core.common.models import TimeStampedModel


class Store(TimeStampedModel):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    country_code = models.CharField(max_length=2)
    currency = models.CharField(
        max_length=3, editable=False,
        help_text="Product price currency, assigned from the store country on creation.",
    )

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

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(currency__regex=r"^[A-Z]{3}$"),
            name="store_currency_code_valid",
        )]

    def clean(self):
        """Assign source currency and reject edits that would relabel store prices."""
        super().clean()
        expected = currency_for_country(self.country_code)
        if expected is None:
            raise ValidationError(
                {"country_code": "This country has no unambiguous supported currency."})
        if not self._state.adding:
            original = type(self).objects.using(self._state.db).values_list(
                "currency", flat=True).get(pk=self.pk)
            if self.currency != original or expected != original:
                raise ValidationError(
                    {"country_code": "Changing the store's pricing currency requires an explicit price migration."})
        elif self.currency and self.currency != expected:
            raise ValidationError(
                {"country_code": "Store currency must match its country."})
        self.currency = expected

    def save(self, *args, **kwargs):
        self.clean()
        for field_name in ("profile_image", "banner_image"):
            image = getattr(self, field_name)
            if image and not image._committed:
                setattr(self, field_name, compress_uploaded_image(image))
        return super().save(*args, **kwargs)


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


class StoreAddress(models.Model):

    store = models.OneToOneField(
        Store, on_delete=models.CASCADE, related_name="address")

    country_code = models.CharField(max_length=2)
    address_line1 = models.CharField(max_length=150)
    address_line2 = models.CharField(max_length=150, blank=True)

    city = models.CharField(max_length=100)
    region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return f"{self.address_line1}, {self.city}"
