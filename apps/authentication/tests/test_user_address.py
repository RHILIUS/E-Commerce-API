"""Personal-address ownership, optional account setup and auth response contract."""
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from apps.account.models import RecipientInfo
from apps.checkout.services.pricing import checkout_rates
from apps.authentication.models import UserAddress
from apps.authentication.serializers import UserPublicSerializer
from apps.ecommerce.services.display_prices import get_display_rates


class UserAddressTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="address.user", email="address@example.com",
            full_name="Address User", phone_number="+639171234567", password="StrongPass1")

    def test_missing_address_serializes_null_and_uses_usd(self):
        data = UserPublicSerializer(self.user).data
        self.assertIsNone(data["address_country_code"])
        self.assertNotIn("country_code", data)
        self.assertEqual(get_display_rates(self.user, {"USD"})["USD"][0], "USD")
        self.assertEqual(checkout_rates(self.user, {"USD"})[0], "USD")

    def test_login_and_refresh_expose_personal_country(self):
        UserAddress.objects.create(user=self.user, country_code="PH",
                                   address_line1="Personal street", city="Manila")
        client = APIClient()
        login = client.post("/api/auth/login/", {"email": self.user.email, "password": "StrongPass1"})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.data["user"]["address_country_code"], "PH")
        self.assertNotIn("country_code", login.data["user"])
        refresh = client.post("/api/auth/refresh/")
        self.assertEqual(refresh.status_code, 200)
        self.assertEqual(refresh.data["user"]["address_country_code"], "PH")

    def test_one_address_per_user_and_independent_recipient(self):
        address = UserAddress.objects.create(user=self.user, country_code="PH",
                                            address_line1="Personal street", city="Manila")
        recipient = RecipientInfo.objects.create(user=self.user, name="Other person",
            phone_number="+16502532222", country_code="US", address_line1="Delivery street", city="Seattle")
        address.city = "Marikina"
        address.save()
        recipient.refresh_from_db()
        self.assertEqual(recipient.city, "Seattle")
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserAddress.objects.create(user=self.user, country_code="US", address_line1="Duplicate", city="Seattle")
        self.user.delete()
        self.assertFalse(UserAddress.objects.filter(pk=address.pk).exists())

    def test_superuser_can_be_created_without_address(self):
        user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="StrongPass1",
            full_name="Admin", phone_number="+16502532222")
        self.assertTrue(user.is_superuser)
        self.assertIsNone(UserPublicSerializer(user).data["address_country_code"])
