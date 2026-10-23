from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase

from core.common.currencies import default_currency_for_user


class DefaultCurrencyTests(SimpleTestCase):
    def test_guest_and_missing_personal_address_use_usd(self):
        for user in (None, AnonymousUser(), SimpleNamespace(address=None)):
            with self.subTest(user=user):
                self.assertEqual(default_currency_for_user(user), "USD")

    def test_personal_address_controls_default_without_using_phone_country(self):
        user = SimpleNamespace(address=SimpleNamespace(country_code="ph"), phone_country="US")
        self.assertEqual(default_currency_for_user(user), "PHP")

    def test_unresolved_country_uses_usd(self):
        for country in ("XX", "", None):
            with self.subTest(country=country):
                user = SimpleNamespace(address=SimpleNamespace(country_code=country))
                self.assertEqual(default_currency_for_user(user), "USD")

    @patch("core.common.currencies.get_territory_currencies", return_value=["USD", "XXX"])
    def test_ambiguous_country_uses_usd(self, currencies):
        user = SimpleNamespace(address=SimpleNamespace(country_code="ZZ"))
        self.assertEqual(default_currency_for_user(user), "USD")
