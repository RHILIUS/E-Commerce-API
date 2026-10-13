"""Profile ownership, contact validation, atomic saves and address independence."""
from copy import deepcopy
from unittest.mock import patch

from django.db import IntegrityError
from rest_framework.test import APITestCase

from apps.account.models import RecipientInfo
from apps.account.services import update_profile
from apps.authentication.models import UserAddress
from apps.authentication.serializers import UserPublicSerializer
from apps.checkout.services.pricing import checkout_rates
from apps.ecommerce.services.display_prices import get_display_rates
from core.test_support import create_customer

URL = "/api/account/profile/"


class ProfileTests(APITestCase):
    def setUp(self):
        self.user = create_customer(username="buyer", email="buyer@example.com",
            full_name="Buyer", phone_number="+639171234567", country_code="PH")
        self.other = create_customer(username="other", email="other@example.com",
            full_name="Other", phone_number="+16502532222", country_code="US")
        self.recipient = RecipientInfo.objects.create(user=self.user, name="Delivery person",
            phone_number=self.user.phone_number, country_code="PH", address_line1="Delivery street", city="Manila")
        self.data = {"full_name": "Updated Buyer", "phone_number": "+12025550123", "address": {
            "country_code": "US", "address_line1": "10 Main Street", "address_line2": "Suite 2",
            "city": "Boston", "region": "MA", "postal_code": "02108"}}
        self.client.force_authenticate(self.user)

    def test_authentication_is_required_for_read_and_save(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(URL).status_code, 401)
        self.assertEqual(self.client.put(URL, self.data, format="json").status_code, 401)

    def test_read_returns_only_own_profile_without_writes(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"id", "username", "email", "full_name", "phone_number",
                                            "address", "profile_image", "profile_image_crop"})
        self.assertEqual(response.data["id"], self.user.pk)
        self.assertEqual(response.data["username"], "buyer")
        self.assertEqual(response.data["email"], "buyer@example.com")
        self.assertEqual(response.data["address"]["country_code"], "PH")
        self.assertIsNone(response.data["profile_image"])
        self.assertEqual(UserAddress.objects.count(), 2)

    def test_save_updates_personal_details_and_country_without_changing_recipients(self):
        response = self.client.put(URL, self.data, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["address"], self.data["address"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, self.data["full_name"])
        self.assertEqual(self.user.phone_number, self.data["phone_number"])
        self.assertEqual(UserPublicSerializer(self.user).data["address_country_code"], "US")
        self.assertEqual(get_display_rates(self.user, {"USD"})["USD"][0], "USD")
        self.assertEqual(checkout_rates(self.user, {"USD"})[0], "USD")
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.country_code, "PH")
        self.assertEqual(self.recipient.address_line1, "Delivery street")
        self.assertEqual(self.recipient.phone_number, "+639171234567")
        self.other.refresh_from_db()
        self.assertEqual(self.other.full_name, "Other")

    def test_read_missing_address_is_null_and_first_save_creates_it(self):
        self.user.address.delete()
        self.assertIsNone(self.client.get(URL).data["address"])
        self.assertEqual(UserAddress.objects.count(), 1)
        response = self.client.put(URL, self.data, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["address"], self.data["address"])
        self.assertEqual(UserAddress.objects.count(), 2)
        self.assertEqual(self.client.put(URL, self.data, format="json").status_code, 200)
        self.assertEqual(UserAddress.objects.count(), 2)

    def test_own_phone_can_be_retained_and_optional_address_fields_clear(self):
        self.data["phone_number"] = self.user.phone_number
        for field in ("address_line2", "region", "postal_code"):
            del self.data["address"][field]
        self.data["address"]["country_code"] = "us"
        self.data["full_name"] = "  Updated Buyer  "
        response = self.client.put(URL, self.data, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["full_name"], "Updated Buyer")
        self.assertEqual(response.data["address"]["country_code"], "US")
        for field in ("address_line2", "region", "postal_code"):
            self.assertEqual(response.data["address"][field], "")

    def test_required_fields_and_null_address_are_rejected(self):
        for field in ("full_name", "phone_number", "address"):
            data = deepcopy(self.data)
            del data[field]
            with self.subTest(field=field):
                response = self.client.put(URL, data, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)
        self.data["address"] = None
        self.assertEqual(self.client.put(URL, self.data, format="json").status_code, 400)

    def test_address_field_errors_are_nested_and_do_not_save_name(self):
        for field, value in (("country_code", "ZZ"), ("address_line1", ""), ("city", ""),
                             ("address_line1", "x" * 151), ("postal_code", "x" * 21)):
            data = deepcopy(self.data)
            data["address"][field] = value
            with self.subTest(field=field, value=value):
                response = self.client.put(URL, data, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data["address"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "Buyer")
        for field in ("country_code", "address_line1", "city"):
            data = deepcopy(self.data)
            del data["address"][field]
            with self.subTest(missing=field):
                response = self.client.put(URL, data, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data["address"])

    def test_invalid_or_duplicate_phone_is_rejected(self):
        for phone in ("09171234567", "+123", self.other.phone_number):
            with self.subTest(phone=phone):
                self.data["phone_number"] = phone
                response = self.client.put(URL, self.data, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("phone_number", response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone_number, "+639171234567")

    def test_phone_country_and_address_country_are_independent(self):
        self.data["phone_number"] = self.user.phone_number
        response = self.client.put(URL, self.data, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["address"]["country_code"], "US")
        self.assertEqual(response.data["phone_number"], "+639171234567")

    def test_identity_ownership_permissions_and_photo_fields_cannot_be_written(self):
        self.data.update(id=self.other.pk, user=self.other.pk, username="renamed", email="new@example.com",
                         is_staff=True, password="changed", profile_image="other.jpg", profile_image_crop={"x": 42})
        response = self.client.put(URL, self.data, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["id"], self.user.pk)
        self.assertEqual(response.data["username"], "buyer")
        self.assertEqual(response.data["email"], "buyer@example.com")
        self.assertIsNone(response.data["profile_image"])
        self.assertEqual(response.data["profile_image_crop"], {})
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.other.refresh_from_db()
        self.assertEqual(self.other.full_name, "Other")

    def test_address_failure_rolls_back_user_and_address_updates(self):
        with patch("apps.account.services.account.UserAddress.objects.update_or_create", side_effect=IntegrityError("address failed")):
            with self.assertRaises(IntegrityError):
                self.client.put(URL, self.data, format="json")
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "Buyer")
        self.assertEqual(self.user.phone_number, "+639171234567")
        self.assertEqual(self.user.address.country_code, "PH")

    def test_phone_claimed_after_validation_returns_field_error_and_rolls_back(self):
        def claim_then_save(user, data):
            self.other.phone_number = data["phone_number"]
            self.other.save(update_fields=["phone_number"])
            return update_profile(user, data)
        with patch("apps.account.views.account.update_profile", side_effect=claim_then_save):
            response = self.client.put(URL, self.data, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"phone_number": ["Phone number is already taken."]})
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "Buyer")
        self.assertEqual(self.user.address.country_code, "PH")

    def test_unsupported_methods_do_not_modify_profile(self):
        self.assertEqual(self.client.patch(URL, self.data, format="json").status_code, 405)
        self.assertEqual(self.client.delete(URL).status_code, 405)
