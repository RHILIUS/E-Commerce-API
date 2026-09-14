from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):

    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=20, unique=True)
    country_code = models.CharField(max_length=2)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username", "full_name", "phone_number", "country_code"]

    def __str__(self):
        return self.full_name or self.email


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
