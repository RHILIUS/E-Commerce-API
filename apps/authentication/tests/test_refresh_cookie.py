from datetime import timedelta
from email.utils import parsedate_to_datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from apps.authentication.services.refresh_cookie import set_refresh_token_cookie


class RefreshCookieTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="persistent", email="persistent@example.com",
            full_name="Persistent Customer", phone_number="+639171234567",
            password="StrongPass1",
        )

    def setUp(self):
        self.client = APIClient()

    def login(self):
        """Obtain real login credentials without exposing them in test output."""
        response = self.client.post("/api/auth/login/", {
            "email": self.user.email, "password": "StrongPass1",
        }, format="json")
        self.assertEqual(response.status_code, 200)
        return response

    def assert_persistent_cookie(self, response, *, secure):
        """Check retention against token claims as well as cookie security flags."""
        cookie = response.cookies["refresh_token"]
        token = RefreshToken(cookie.value)
        remaining = int(token["exp"]) - int(timezone.now().timestamp())
        self.assertGreater(int(cookie["max-age"]), 0)
        self.assertLessEqual(abs(int(cookie["max-age"]) - remaining), 2)
        self.assertLessEqual(
            abs(parsedate_to_datetime(cookie["expires"]).timestamp() - int(token["exp"])),
            2,
        )
        self.assertTrue(cookie["httponly"])
        self.assertEqual(bool(cookie["secure"]), secure)
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], "/")
        self.assertNotIn("refresh", response.data)
        self.assertNotIn("refresh_token", response.data)
        return token

    def test_login_cookie_follows_configured_token_lifetime(self):
        response = self.login()
        token = self.assert_persistent_cookie(response, secure=True)
        self.assertEqual(
            token["exp"] - token["iat"],
            int(api_settings.REFRESH_TOKEN_LIFETIME.total_seconds()),
        )

    def test_rotation_replaces_cookie_and_rejects_previous_token(self):
        original = self.login().cookies["refresh_token"].value
        # An alternative issuance lifetime proves retention is not hard-coded to seven days.
        with patch.object(RefreshToken, "lifetime", timedelta(minutes=23)):
            response = self.client.post("/api/auth/refresh/")
        self.assertEqual(response.status_code, 200)
        token = self.assert_persistent_cookie(response, secure=True)
        self.assertEqual(token["exp"] - token["iat"], 23 * 60)
        self.client.cookies["refresh_token"] = original
        rejected = self.client.post("/api/auth/refresh/")
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(rejected.cookies["refresh_token"]["max-age"], 0)

    def test_cookie_uses_remaining_validity_and_preserves_development_security_flag(self):
        token = RefreshToken.for_user(self.user)
        response = Response()
        with patch("apps.authentication.services.refresh_cookie.time", return_value=token["exp"] - 123):
            set_refresh_token_cookie(response, token, secure=False)
        self.assertEqual(response.cookies["refresh_token"]["max-age"], 123)
        self.assertFalse(response.cookies["refresh_token"]["secure"])

    def test_logout_clears_persistent_cookie_and_revokes_token(self):
        token_value = self.login().cookies["refresh_token"].value
        response = self.client.post("/api/auth/logout/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.cookies["refresh_token"]["max-age"], 0)
        self.client.cookies["refresh_token"] = token_value
        self.assertEqual(self.client.post("/api/auth/refresh/").status_code, 401)
