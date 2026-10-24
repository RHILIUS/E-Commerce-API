import hashlib
import hmac
import json
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from core.common.country_hint import verified_country

SECRET = "country-hint-test-only-secret-000000000000"
NOW = 1780000000


@override_settings(COUNTRY_HINT_SECRET=SECRET, ALLOWED_HOSTS=["api.example.test", "other.example.test", "testserver"])
class CountryHintTests(SimpleTestCase):
    def setUp(self):
        clock = patch("core.common.country_hint.time", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def request(self, *, country="PH", timestamp=NOW):
        """Build the same request tuple signed by the Pages test fixture."""
        request = RequestFactory().get("/api/shop/products/?q=caf%C3%A9+camera", secure=True, HTTP_HOST="api.example.test")
        value = str(timestamp)
        message = json.dumps(["country-hint-v1", value, country, "GET", "https://api.example.test", request.get_full_path()], separators=(",", ":"))
        signature = hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
        request.META.update(HTTP_X_SHOP_COUNTRY=country, HTTP_X_SHOP_COUNTRY_TIME=value, HTTP_X_SHOP_COUNTRY_SIGNATURE=signature)
        return request

    def test_valid_hint_matches_the_pages_fixture(self):
        request = self.request()
        self.assertEqual(request.META["HTTP_X_SHOP_COUNTRY_SIGNATURE"], "0fc0202ba0f767217e8f9f113f6fc9e24446b1f495314cea08d6412aaf3ea96a")
        self.assertEqual(verified_country(request), "PH")

    def test_modified_country_method_origin_path_query_or_signature_is_ignored(self):
        for key, value in [("HTTP_X_SHOP_COUNTRY", "US"), ("REQUEST_METHOD", "POST"),
                           ("HTTP_HOST", "other.example.test"), ("PATH_INFO", "/api/shop/currencies/"),
                           ("QUERY_STRING", "q=changed"), ("HTTP_X_SHOP_COUNTRY_SIGNATURE", "0" * 64)]:
            with self.subTest(key=key):
                request = self.request()
                if key == "REQUEST_METHOD":
                    request.method = value
                elif key == "PATH_INFO":
                    request.path = value
                else:
                    request.META[key] = value
                self.assertIsNone(verified_country(request))

    def test_expired_future_or_malformed_hints_are_ignored(self):
        for timestamp in (NOW - 301, NOW + 31):
            self.assertIsNone(verified_country(self.request(timestamp=timestamp)))
        for key, value in [("HTTP_X_SHOP_COUNTRY_TIME", "invalid"), ("HTTP_X_SHOP_COUNTRY_TIME", "9" * 1000),
                           ("HTTP_X_SHOP_COUNTRY_SIGNATURE", "é" * 64), ("HTTP_X_SHOP_COUNTRY", "XX")]:
            request = self.request()
            request.META[key] = value
            self.assertIsNone(verified_country(request))

    def test_unsigned_country_and_disabled_or_weak_configuration_are_ignored(self):
        unsigned = RequestFactory().get("/", HTTP_CF_IPCOUNTRY="PH", HTTP_X_SHOP_COUNTRY="PH")
        self.assertIsNone(verified_country(unsigned))
        for secret in ("", "short"):
            with override_settings(COUNTRY_HINT_SECRET=secret):
                self.assertIsNone(verified_country(self.request()))
