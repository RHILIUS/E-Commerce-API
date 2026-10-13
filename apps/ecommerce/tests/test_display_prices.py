import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from core.test_support import create_customer
from apps.ecommerce.models import Product
from apps.ecommerce.services.display_prices import convert_display_price, get_display_rates
from apps.store.models import Store


class DisplayPriceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = SimpleNamespace(is_authenticated=True, address=SimpleNamespace(country_code="PH"))

    def response(self, **updates):
        """Mock a daily table quoted against the viewer currency."""
        data = {"base": "USD", "quote": "PHP", "rate": 56,
                "date": datetime.now(timezone.utc).date().isoformat()}
        data.update(updates)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps([data]).encode()
        return response

    @patch("apps.ecommerce.services.display_prices.urlopen")
    def test_same_currency_and_empty_pages_do_not_contact_provider(self, fetch):
        self.assertEqual(get_display_rates(self.user, {"PHP"}), {"PHP": ("PHP", Decimal(1))})
        self.assertEqual(get_display_rates(AnonymousUser(), {"USD"}), {"USD": ("USD", Decimal(1))})
        self.assertEqual(get_display_rates(self.user, set()), {})
        fetch.assert_not_called()

    @patch("apps.ecommerce.services.display_prices.urlopen")
    def test_guests_convert_store_php_to_usd_and_cache_the_table(self, fetch):
        fetch.return_value = self.response()
        expected = {"PHP": ("USD", Decimal(56)), "USD": ("USD", Decimal(1))}
        self.assertEqual(get_display_rates(AnonymousUser(), {"PHP", "USD"}), expected)
        self.assertEqual(get_display_rates(AnonymousUser(), {"PHP", "USD"}), expected)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0].full_url, "https://api.frankfurter.dev/v2/rates?base=usd")
        self.assertEqual(convert_display_price(Decimal(5600), *expected["PHP"]),
                         {"amount": "100.00", "currency": "USD"})

    @patch("apps.ecommerce.services.display_prices.urlopen")
    def test_php_viewer_converts_us_store_prices_in_the_opposite_direction(self, fetch):
        fetch.return_value = self.response(base="PHP", quote="USD", rate="0.02")
        rates = get_display_rates(self.user, {"PHP", "USD"})
        self.assertEqual(convert_display_price(Decimal(100), *rates["USD"]),
                         {"amount": "5000.00", "currency": "PHP"})
        self.assertEqual(rates["PHP"], ("PHP", Decimal(1)))

    @patch("apps.ecommerce.services.display_prices.urlopen")
    def test_unknown_viewer_country_uses_usd(self, fetch):
        fetch.return_value = self.response()
        viewer = SimpleNamespace(is_authenticated=True, address=SimpleNamespace(country_code="XX"))
        self.assertEqual(get_display_rates(viewer, {"PHP"}), {"PHP": ("USD", Decimal(56))})

    @patch("apps.ecommerce.services.display_prices.urlopen", side_effect=URLError("offline"))
    def test_failure_retains_store_currencies_and_backs_off(self, fetch):
        expected = {"PHP": ("PHP", Decimal(1)), "EUR": ("EUR", Decimal(1))}
        with self.assertLogs("apps.ecommerce.services.display_prices", level="WARNING") as logs:
            self.assertEqual(get_display_rates(AnonymousUser(), {"PHP", "EUR"}), expected)
            self.assertEqual(get_display_rates(AnonymousUser(), {"PHP", "EUR"}), expected)
        self.assertEqual([record.getMessage() for record in logs.records], [
            "Catalog rates unavailable for USD; using cached rates or store currencies"])
        fetch.assert_called_once()

    @patch("apps.ecommerce.services.display_prices.urlopen", side_effect=TimeoutError)
    def test_failure_uses_unexpired_stale_rates_only(self, fetch):
        now = datetime.now(timezone.utc).timestamp()
        cache.set("catalog-fx:v2:USD", {
            "fetched_at": now - 90000,
            "rates": {"PHP": {"rate": Decimal(55), "expires_at": now + 3600},
                      "EUR": {"rate": Decimal("0.9"), "expires_at": now - 1}},
        }, timeout=600)
        with self.assertLogs("apps.ecommerce.services.display_prices", level="WARNING") as logs:
            self.assertEqual(get_display_rates(AnonymousUser(), {"PHP", "EUR"}),
                             {"PHP": ("USD", Decimal(55)), "EUR": ("EUR", Decimal(1))})
        self.assertEqual([record.getMessage() for record in logs.records], [
            "Catalog rates unavailable for USD; using cached rates or store currencies"])

    @patch("apps.ecommerce.services.display_prices.urlopen")
    def test_bad_or_old_provider_data_is_not_used(self, fetch):
        for update in [{"rate": 0}, {"rate": -1}, {"rate": "NaN"}, {"rate": "Infinity"},
                       {"base": "EUR"}, {"date": "2000-01-01"},
                       {"date": "2999-01-01"}, {"date": "invalid"}]:
            with self.subTest(update=update):
                cache.clear()
                fetch.return_value = self.response(**update)
                with self.assertLogs("apps.ecommerce.services.display_prices", level="WARNING") as logs:
                    self.assertEqual(get_display_rates(AnonymousUser(), {"PHP"}), {"PHP": ("PHP", Decimal(1))})
                self.assertEqual([record.getMessage() for record in logs.records], [
                    "Catalog rates unavailable for USD; using cached rates or store currencies"])

    def test_decimal_rounding_uses_currency_precision(self):
        for price, currency, divisor, amount in [
            ("5600.00", "USD", "56", "100.00"),
            ("1.13", "PHP", "2", "0.57"),
            ("301", "JPY", "2", "151"),
            ("0.615", "KWD", "2", "0.308"),
        ]:
            self.assertEqual(convert_display_price(Decimal(price), currency, Decimal(divisor)),
                             {"amount": amount, "currency": currency})


class CatalogDisplayPriceTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        ph = Store.objects.create(name="PH Store", country_code="PH")
        us = Store.objects.create(name="US Store", country_code="US")
        self.products = [Product.objects.create(store=store, name=name,
            price=price, status=Product.Status.ACTIVE) for store, name, price in [
                (ph, "PH product", "5600.00"), (us, "US product", "100.00"),
                (ph, "Another PH product", "2800.00"),
            ]]
        self.user = create_customer(username="buyer", email="buyer@example.com",
            phone_number="+639111111111", full_name="Buyer", country_code="PH")

    @patch("apps.ecommerce.services.display_prices.urlopen")
    def test_mixed_store_page_uses_one_request_and_preserves_source_prices(self, fetch):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps([{
            "base": "USD", "quote": "PHP", "rate": 56,
            "date": datetime.now(timezone.utc).date().isoformat(),
        }]).encode()
        fetch.return_value = response
        with self.assertNumQueries(3):
            result = self.client.get("/api/shop/products/")
        self.assertEqual(result.status_code, 200)
        fetch.assert_called_once()
        items = {item["name"]: item for item in result.json()["results"]}
        self.assertEqual(items["PH product"]["currency"], "PHP")
        self.assertEqual(items["PH product"]["price"], "5600.00")
        self.assertEqual(items["PH product"]["display_price"], {"amount": "100.00", "currency": "USD"})
        self.assertEqual(items["US product"]["display_price"], {"amount": "100.00", "currency": "USD"})
        for product in self.products:
            original_price = product.price
            product.refresh_from_db()
            self.assertEqual(product.price, Decimal(original_price))

    @patch("apps.ecommerce.services.display_prices.urlopen")
    def test_signed_in_country_is_used_not_client_query(self, fetch):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps([{
            "base": "PHP", "quote": "USD", "rate": "0.02",
            "date": datetime.now(timezone.utc).date().isoformat(),
        }]).encode()
        fetch.return_value = response
        self.client.force_authenticate(self.user)
        result = self.client.get("/api/shop/products/?currency=EUR")
        items = {item["name"]: item for item in result.json()["results"]}
        self.assertEqual(items["PH product"]["display_price"], {"amount": "5600.00", "currency": "PHP"})
        self.assertEqual(items["US product"]["display_price"], {"amount": "5000.00", "currency": "PHP"})
