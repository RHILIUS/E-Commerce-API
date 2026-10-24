import json
from time import time
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from apps.ecommerce.services.currency_options import (
    CACHE_KEY, CurrencyOptionsUnavailable, display_currency_codes,
)


class CurrencyOptionsTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.fetcher = patch("apps.ecommerce.services.currency_options.urlopen")
        self.fetch = self.fetcher.start()
        self.addCleanup(self.fetcher.stop)
        self.respond(json.dumps([{"iso_code": code} for code in ("USD", "BRL", "INR", "PHP")]).encode())

    def respond(self, body):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = body
        self.fetch.return_value = response

    def test_discovery_includes_all_provider_codes_and_reuses_fresh_cache(self):
        expected = ("BRL", "INR", "PHP", "USD")
        self.assertEqual(display_currency_codes(), expected)
        self.assertEqual(display_currency_codes(), expected)
        self.fetch.assert_called_once()
        self.assertEqual(self.fetch.call_args.args[0].full_url, "https://api.frankfurter.dev/v2/currencies")
        self.assertEqual(self.fetch.return_value.__enter__.return_value.read.call_args.args, (131073,))

    def test_validated_stale_list_survives_refresh_failure_and_backs_off(self):
        cache.set(CACHE_KEY, {"codes": ("PHP", "USD"), "fetched_at": time() - 90000}, timeout=600)
        self.fetch.side_effect = OSError("offline")
        self.assertEqual(display_currency_codes(), ("PHP", "USD"))
        self.assertEqual(display_currency_codes(), ("PHP", "USD"))
        self.fetch.assert_called_once()

    def test_cold_outage_is_unavailable_and_does_not_hammer_provider(self):
        self.fetch.side_effect = OSError("offline")
        for _ in range(2):
            with self.assertRaises(CurrencyOptionsUnavailable):
                display_currency_codes()
        self.fetch.assert_called_once()

    def test_expired_metadata_is_not_used(self):
        cache.set(CACHE_KEY, {"codes": ("USD",), "fetched_at": time() - 8 * 86400}, timeout=600)
        self.fetch.side_effect = OSError("offline")
        with self.assertRaises(CurrencyOptionsUnavailable):
            display_currency_codes()

    def test_refresh_success_replaces_stale_choices(self):
        cache.set(CACHE_KEY, {"codes": ("USD",), "fetched_at": time() - 90000}, timeout=600)
        self.assertIn("INR", display_currency_codes())
        self.fetch.assert_called_once()

    def test_concurrent_refresh_uses_existing_list_or_reports_cold_unavailability(self):
        cache.set(CACHE_KEY + ":refresh", True, timeout=300)
        with self.assertRaises(CurrencyOptionsUnavailable):
            display_currency_codes()
        cache.set(CACHE_KEY, {"codes": ("USD",), "fetched_at": time() - 90000}, timeout=600)
        self.assertEqual(display_currency_codes(), ("USD",))
        self.fetch.assert_not_called()

    def test_malformed_or_incomplete_provider_payloads_never_replace_cache(self):
        for body in (b"{}", b"null", b"[]", b"invalid", b"x" * 131073,
                     b'[{"iso_code":"usd"}]', b'[{"iso_code":"USD"},{"iso_code":"USD"}]',
                     b'[{"iso_code":"USD"},{}]', b'[{"iso_code":"USD"},null]', b'[{"iso_code":"PHP"}]'):
            with self.subTest(body=body[:60]):
                cache.clear()
                self.respond(body)
                with self.assertRaises(CurrencyOptionsUnavailable):
                    display_currency_codes()
                self.assertIsNone(cache.get(CACHE_KEY))

    @override_settings(DISPLAY_CURRENCY_ALLOWLIST=("USD", "EUR", "XYZ"))
    def test_optional_restriction_intersects_discovery_without_enabling_unknown_codes(self):
        self.assertEqual(display_currency_codes(), ("USD",))
        with override_settings(DISPLAY_CURRENCY_ALLOWLIST=None):
            self.assertIn("BRL", display_currency_codes())
        self.fetch.assert_called_once()
