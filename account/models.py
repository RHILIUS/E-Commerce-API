from django.conf import settings
from django.db import models
from django.db.models import Q


class RecipientInfo(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recipient_infos")

    name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=20)

    is_default = models.BooleanField(default=False)

    country_code = models.CharField(max_length=2)
    address_line1 = models.CharField(max_length=150)
    address_line2 = models.CharField(max_length=150, blank=True)

    city = models.CharField(max_length=100)
    region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                # Ensures that a user must have at most 1 default RecipientInfo
                fields=["user"],
                condition=Q(is_default=True),
                name="unique_default_recipient_per_user",
            )
        ]

    def __str__(self):
        return self.name
