from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):

    authentication_version = models.PositiveIntegerField(default=0)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=20, unique=True)
    profile_image = models.ImageField(upload_to="users/profile/", blank=True)
    profile_image_crop = models.JSONField(default=dict, blank=True)
    profile_image_original = models.ImageField(upload_to="users/profile/originals/", blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username", "full_name", "phone_number"]

    def __str__(self):
        return self.full_name or self.email


class UserAddress(models.Model):
    """Personal address owned by the account, independent of delivery recipients."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="address")
    country_code = models.CharField(max_length=2)
    address_line1 = models.CharField(max_length=150)
    address_line2 = models.CharField(max_length=150, blank=True)
    city = models.CharField(max_length=100)
    region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return f"{self.address_line1}, {self.city}"


class OTPVerificationRecord(models.Model):
    email = models.EmailField(unique=True)
    otp_hash = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField()

    # Represents the time when OTP validity expires
    expires_at = models.DateTimeField()

    failed_attempts = models.PositiveSmallIntegerField(default=0)
    send_count = models.PositiveSmallIntegerField(default=1)
    send_window_started_at = models.DateTimeField()

    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)


class PasswordChangeState(models.Model):
    """One challenge slot per user; account counters survive replacing its UUID."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    challenge_id = models.UUIDField(null=True, unique=True)
    email = models.EmailField(blank=True)
    credential_state = models.CharField(max_length=64, blank=True)
    otp_hash = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField(null=True)
    last_sent_at = models.DateTimeField(null=True)
    consumed = models.BooleanField(default=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    send_count = models.PositiveIntegerField(default=0)
    send_window_started_at = models.DateTimeField()
    request_count = models.PositiveIntegerField(default=0)
    request_window_started_at = models.DateTimeField()
    verification_count = models.PositiveIntegerField(default=0)
    verification_window_started_at = models.DateTimeField()
