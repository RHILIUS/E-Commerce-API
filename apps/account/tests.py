from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from .models import RecipientInfo
from .services import create_recipient


def recipient_data(**overrides):
    """Return a valid recipient payload with optional contract variations."""
    return {
        "name": "Juan Dela Cruz", "phone_number": "+639171234567",
        "country_code": "PH", "address_line1": "123 Example Street", "city": "Marikina",
        **overrides,
    }


def customer(username, phone):
    """Create an account without automatic registration recipients for API tests."""
    return get_user_model().objects.create_user(
        username=username, email=f"{username}@example.com", full_name=username,
        phone_number=phone, country_code="PH",
    )


class RecipientAPITests(APITestCase):
    url = "/api/account/recipients/"

    def setUp(self):
        self.user = customer("buyer", "+639171234567")
        self.other = customer("other", "+16502532222")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")

    def test_auth_required_and_invalid_token(self):
        for token in (None, "Bearer invalid"):
            self.client.credentials(**({"HTTP_AUTHORIZATION": token} if token else {}))
            for method in ("get", "post"):
                with self.subTest(token_present=bool(token), method=method):
                    self.assertEqual(getattr(self.client, method)(self.url, recipient_data(), format="json").status_code, 401)
        self.assertFalse(RecipientInfo.objects.exists())

    def test_empty_list_is_read_only(self):
        self.assertEqual(self.client.get(self.url).json(), {"recipients": []})
        self.assertFalse(RecipientInfo.objects.exists())

    def test_first_recipient_is_default_and_response_is_normalized(self):
        response = self.client.post(self.url, recipient_data(country_code="ph", name="  Juan  ", is_default=False), format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {
            "id": RecipientInfo.objects.get().pk, "name": "Juan", "phone_number": "+639171234567",
            "country_code": "PH", "address_line1": "123 Example Street", "city": "Marikina",
            "address_line2": "", "region": "", "postal_code": "", "is_default": True,
        })
        self.assertEqual(RecipientInfo.objects.get().user_id, self.user.pk)

    def test_defaults_replace_atomically_and_list_is_owner_scoped_default_first(self):
        foreign = RecipientInfo.objects.create(user=self.other, is_default=True, **recipient_data())
        first = self.client.post(self.url, recipient_data(), format="json").json()
        second = self.client.post(self.url, recipient_data(name="Second"), format="json").json()
        self.assertFalse(second["is_default"])
        third = self.client.post(self.url, recipient_data(name="Third", is_default=True), format="json").json()
        response = self.client.get(self.url)
        self.assertEqual([r["id"] for r in response.json()["recipients"]], [third["id"], first["id"], second["id"]])
        self.assertEqual(RecipientInfo.objects.filter(user=self.user, is_default=True).count(), 1)
        foreign.refresh_from_db()
        self.assertTrue(foreign.is_default)
        self.assertEqual(RecipientInfo.objects.filter(phone_number=self.user.phone_number).count(), 4)

    def test_submitted_ownership_and_id_cannot_reassign_a_recipient(self):
        foreign = RecipientInfo.objects.create(user=self.other, **recipient_data())
        response = self.client.post(self.url, recipient_data(user=self.other.pk, user_id=self.other.pk, id=foreign.pk), format="json")
        self.assertEqual(response.status_code, 201)
        created = RecipientInfo.objects.get(pk=response.json()["id"])
        self.assertEqual(created.user_id, self.user.pk)
        self.assertNotEqual(created.pk, foreign.pk)
        self.assertNotIn("user", response.json())

    def test_required_fields_and_model_lengths(self):
        for field in ("name", "phone_number", "country_code", "address_line1", "city"):
            for missing in (False, True):
                payload = recipient_data()
                if missing:
                    del payload[field]
                else:
                    payload[field] = "   "
                with self.subTest(field=field, missing=missing):
                    response = self.client.post(self.url, payload, format="json")
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(field, response.json())
        for field, limit in (("name", 100), ("address_line1", 150), ("address_line2", 150),
                             ("city", 100), ("region", 100), ("postal_code", 20)):
            response = self.client.post(self.url, recipient_data(**{field: "x" * (limit + 1)}), format="json")
            self.assertEqual(response.status_code, 400)
            self.assertIn(field, response.json())
        self.assertFalse(RecipientInfo.objects.exists())

    def test_invalid_contact_fields_do_not_replace_existing_default(self):
        existing = RecipientInfo.objects.create(user=self.user, is_default=True, **recipient_data())
        for field, value in (("phone_number", "09171234567"), ("phone_number", "+123"),
                             ("country_code", "ZZ"), ("is_default", "invalid")):
            response = self.client.post(self.url, recipient_data(**{"is_default": True, field: value}), format="json")
            self.assertEqual(response.status_code, 400)
            self.assertIn(field, response.json())
        existing.refresh_from_db()
        self.assertTrue(existing.is_default)
        self.assertEqual(RecipientInfo.objects.count(), 1)

    def test_phone_country_can_differ_from_delivery_country(self):
        response = self.client.post(self.url, recipient_data(phone_number="+16502532222", country_code="PH"), format="json")
        self.assertEqual(response.status_code, 201)

    def test_creation_failure_rolls_back_default_replacement(self):
        existing = RecipientInfo.objects.create(user=self.user, is_default=True, **recipient_data())
        with patch("apps.account.services.RecipientInfo.objects.create", side_effect=IntegrityError("simulated failure")):
            with self.assertRaises(IntegrityError):
                create_recipient(self.user, recipient_data(is_default=True))
        existing.refresh_from_db()
        self.assertTrue(existing.is_default)
        self.assertEqual(RecipientInfo.objects.count(), 1)

    def test_unsupported_methods(self):
        for method in ("patch", "put", "delete"):
            self.assertEqual(getattr(self.client, method)(self.url, {}, format="json").status_code, 405)

    def test_schema_documents_shapes_and_authentication(self):
        schema = self.client.get("/api/schema/?format=json").json()
        operations = schema["paths"][self.url]
        self.assertEqual(set(operations), {"get", "post"})
        self.assertEqual(set(operations["post"]["responses"]), {"201", "400", "401"})
        self.assertNotIn({}, operations["get"]["security"])
        self.assertEqual(operations["get"]["responses"]["200"]["content"]["application/json"]["schema"],
                         {"$ref": "#/components/schemas/RecipientList"})
        self.assertTrue(schema["components"]["schemas"]["Recipient"]["properties"]["id"]["readOnly"])


class ConcurrentRecipientTests(TransactionTestCase):
    def setUp(self):
        self.user = customer("concurrent", "+639171234567")

    def create_concurrently(self, is_default):
        """Start separate database connections together to exercise the customer lock."""
        barrier = Barrier(2)
        def create(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return create_recipient(self.user, recipient_data(name=f"Recipient {index}", is_default=is_default)).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(create, range(2)))

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_first_recipients_have_one_default(self):
        ids = self.create_concurrently(False)
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(RecipientInfo.objects.filter(user=self.user, is_default=True).count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_replacements_have_one_default(self):
        old = RecipientInfo.objects.create(user=self.user, is_default=True, **recipient_data())
        ids = self.create_concurrently(True)
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(RecipientInfo.objects.filter(user=self.user, is_default=True).count(), 1)
        old.refresh_from_db()
        self.assertFalse(old.is_default)
