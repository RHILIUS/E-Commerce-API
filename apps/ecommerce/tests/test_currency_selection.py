from unittest.mock import patch
import hashlib
import hmac
import json
from time import time

from django.test import override_settings
from django.core.cache import cache
from rest_framework.test import APITestCase

from core.test_support import create_customer
from apps.ecommerce.models import CartItem, Product
from apps.store.models import Store


class CurrencySelectionTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Currency store", country_code="PH")
        cls.product = Product.objects.create(store=cls.store, name="Camera", price="100.00", stock=5, status="active")
        cls.user = create_customer(username="currency", email="currency@example.com", phone_number="+639111111111", full_name="Buyer", country_code="PH")

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        discovery = patch("apps.ecommerce.services.currency_options._fetch_codes", return_value=("EUR", "INR", "PHP", "USD"))
        self.discovery = discovery.start()
        self.addCleanup(discovery.stop)

    def browsing_paths(self):
        return ["/api/shop/products/", f"/api/shop/products/{self.product.pk}/",
                f"/api/shop/stores/{self.store.pk}/home/", f"/api/shop/stores/{self.store.pk}/products/"]

    @patch("apps.ecommerce.services.display_prices.urlopen")
    def test_public_options_discover_currencies_without_fetching_rates(self, fetch):
        options = self.client.get("/api/shop/currencies/")
        self.assertEqual(options.status_code, 200)
        self.assertEqual(options.data["default_currency"], "USD")
        self.assertEqual(options.data["default_source"], "fallback")
        self.assertEqual([row["code"] for row in options.data["currencies"] if row["stripe_supported"]], ["USD"])
        for path in self.browsing_paths():
            with self.subTest(path=path):
                response = self.client.get(path, {"display_currency": "PHP"})
                self.assertEqual(response.status_code, 200)
                rows = response.data.get("results", response.data.get("new_products", [response.data]))
                self.assertEqual(rows[0]["display_price"], {"amount": "100.00", "currency": "PHP"})
        self.assertIn("INR", [row["code"] for row in options.data["currencies"]])
        self.discovery.assert_called_once()
        fetch.assert_not_called()

    def test_defaults_and_options_follow_account_address(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get("/api/shop/currencies/").data["default_source"], "address")
        self.assertEqual(self.client.get("/api/shop/currencies/").data["default_currency"], "PHP")

    @override_settings(DISPLAY_CURRENCY_ALLOWLIST=("USD",))
    def test_configured_choices_and_invalid_queries(self):
        self.assertEqual(len(self.client.get("/api/shop/currencies/").data["currencies"]), 1)
        for path in self.browsing_paths():
            for code in ("PHP", "usd", "ZZZ", "", " USD"):
                with self.subTest(path=path, code=code):
                    response = self.client.get(path, {"display_currency": code})
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("display_currency", response.data)

    def test_cart_mutations_keep_selection_and_validate_before_writing(self):
        self.client.force_authenticate(self.user)
        response = self.client.post("/api/shop/carts/items/?display_currency=PHP", {"product_id": self.product.pk, "quantity": 1})
        self.assertEqual(response.status_code, 200)
        item = CartItem.objects.get()
        self.assertEqual(response.data["carts"][0]["items"][0]["subtotal"]["currency"], "PHP")
        self.assertEqual(self.client.patch(f"/api/shop/carts/items/{item.pk}/?display_currency=ZZZ", {"quantity": 2}).status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 1)
        self.assertEqual(self.client.delete("/api/shop/carts/items/?display_currency=ZZZ", {"item_ids": [item.pk]}, format="json").status_code, 400)
        self.assertTrue(CartItem.objects.filter(pk=item.pk).exists())
        response = self.client.patch(f"/api/shop/carts/items/{item.pk}/?display_currency=PHP", {"quantity": 2})
        self.assertEqual(response.data["carts"][0]["items"][0]["subtotal"], {"amount": "200.00", "currency": "PHP"})
        self.assertEqual(self.client.get("/api/shop/carts/?display_currency=PHP").data["carts"][0]["items"][0]["subtotal"]["currency"], "PHP")

    @patch("apps.ecommerce.services.display_prices.urlopen", side_effect=OSError("offline"))
    def test_explicit_choice_never_relabels_failed_conversion(self, fetch):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        response = self.client.get(f"/api/shop/products/{self.product.pk}/?display_currency=EUR")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["display_price"], {"amount": "100.00", "currency": "PHP"})

    def test_invalid_bearer_remains_rejected_on_public_metadata(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
        self.assertEqual(self.client.get("/api/shop/currencies/").status_code, 401)

    def test_metadata_outage_does_not_block_automatic_browsing_or_commit_cart_edits(self):
        self.discovery.side_effect = OSError("offline")
        self.assertEqual(self.client.get("/api/shop/currencies/").status_code, 503)
        with patch("apps.ecommerce.views.catalog.get_display_rates", return_value={}):
            self.assertEqual(self.client.get("/api/shop/products/").status_code, 200)
        self.client.force_authenticate(self.user)
        response = self.client.post("/api/shop/carts/items/?display_currency=PHP", {"product_id": self.product.pk, "quantity": 1})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(CartItem.objects.exists())
        self.discovery.assert_called_once()

    def country_headers(self, path, *, country="PH", method="GET", age=0):
        """Sign a country hint for this test-server request, including query parameters."""
        stamp = str(int(time()) - age)
        message = json.dumps(["country-hint-v1", stamp, country, method, "http://testserver", path], separators=(",", ":"))
        signature = hmac.new(b"country-hint-test-only-secret-000000000000", message.encode(), hashlib.sha256).hexdigest()
        return {"HTTP_X_SHOP_COUNTRY": country, "HTTP_X_SHOP_COUNTRY_TIME": stamp, "HTTP_X_SHOP_COUNTRY_SIGNATURE": signature}

    @override_settings(COUNTRY_HINT_SECRET="country-hint-test-only-secret-000000000000")
    def test_verified_country_defaults_apply_to_metadata_and_every_browsing_surface(self):
        path = "/api/shop/currencies/"
        response = self.client.get(path, **self.country_headers(path))
        self.assertEqual(response.data["default_currency"], "PHP")
        self.assertEqual(response.data["default_source"], "country")
        for path in self.browsing_paths():
            with self.subTest(path=path):
                response = self.client.get(path, **self.country_headers(path))
                rows = response.data.get("results", response.data.get("new_products", [response.data]))
                self.assertEqual(rows[0]["display_price"]["currency"], "PHP")
        # A signed-in user without a personal address also gets a country default.
        self.user.address.delete()
        self.user.refresh_from_db()
        self.client.force_authenticate(self.user)
        path = "/api/shop/currencies/"
        response = self.client.get(path, **self.country_headers(path))
        self.assertEqual(response.data["default_source"], "country")
        path = "/api/shop/carts/items/"
        response = self.client.post(path, {"product_id": self.product.pk, "quantity": 1}, **self.country_headers(path, method="POST"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["carts"][0]["items"][0]["subtotal"]["currency"], "PHP")

    @override_settings(COUNTRY_HINT_SECRET="country-hint-test-only-secret-000000000000")
    def test_address_and_explicit_choices_take_priority(self):
        self.client.force_authenticate(self.user)
        path = "/api/shop/currencies/"
        response = self.client.get(path, **self.country_headers(path, country="US"))
        self.assertEqual(response.data["default_currency"], "PHP")
        self.assertEqual(response.data["default_source"], "address")
        self.client.force_authenticate(None)
        path = f"/api/shop/products/{self.product.pk}/?display_currency=USD"
        with patch("apps.ecommerce.views.catalog.get_display_rates", return_value={} ) as rates:
            self.assertEqual(self.client.get(path, **self.country_headers(path)).status_code, 200)
        self.assertEqual(rates.call_args.kwargs["target_currency"], "USD")

    @override_settings(COUNTRY_HINT_SECRET="country-hint-test-only-secret-000000000000")
    def test_unknown_unsupported_forged_and_expired_countries_fall_back(self):
        path = "/api/shop/currencies/"
        for headers in (self.country_headers(path, country="ZZ"), self.country_headers(path, country="JP"),
                        self.country_headers(path, age=301), {"HTTP_CF_IPCOUNTRY": "PH", "HTTP_X_SHOP_COUNTRY": "PH"}):
            with self.subTest(headers=headers):
                response = self.client.get(path, **headers)
                self.assertEqual(response.data["default_currency"], "USD")
                self.assertEqual(response.data["default_source"], "fallback")

    @override_settings(COUNTRY_HINT_SECRET="country-hint-test-only-secret-000000000000")
    def test_discovery_outage_does_not_block_country_default_browsing(self):
        path = "/api/shop/products/"
        self.discovery.side_effect = OSError("offline")
        with patch("apps.ecommerce.views.catalog.get_display_rates", return_value={}) as rates:
            self.assertEqual(self.client.get(path, **self.country_headers(path)).status_code, 200)
        self.assertEqual(rates.call_args.kwargs["target_currency"], "USD")
