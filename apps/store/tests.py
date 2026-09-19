from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.forms import Select
from django.test import SimpleTestCase, TestCase

from apps.store.admin import StoreAdminForm
from apps.store.models import Store, StoreMember


class StoreAdminFormTests(SimpleTestCase):
    def test_country_code_uses_phonenumbers_region_choices(self):
        field = StoreAdminForm().fields["country_code"]

        self.assertIsInstance(field.widget, Select)
        self.assertIn(("PH", "Philippines (PH)"), list(field.choices))


class StoreMemberTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="store-member",
            email="member@example.com",
            full_name="Store Member",
            phone_number="+639171234567",
            country_code="PH",
        )
        cls.store = Store.objects.create(name="Test Store", country_code="PH")

    def test_membership_defaults_to_staff(self):
        membership = StoreMember.objects.create(
            user=self.user, store=self.store)

        self.assertEqual(membership.role, StoreMember.Role.STAFF)
        self.assertEqual(list(self.store.members.all()), [self.user])

    def test_admin_role_is_allowed(self):
        membership = StoreMember.objects.create(
            user=self.user, store=self.store, role=StoreMember.Role.ADMIN,
        )
        membership.refresh_from_db()
        self.assertEqual(membership.role, StoreMember.Role.ADMIN)

    def test_duplicate_membership_is_rejected(self):
        StoreMember.objects.create(user=self.user, store=self.store)

        with self.assertRaises(IntegrityError), transaction.atomic():
            StoreMember.objects.create(user=self.user, store=self.store)

    def test_invalid_role_is_rejected_by_database(self):
        membership = StoreMember.objects.create(
            user=self.user, store=self.store)

        with self.assertRaises(IntegrityError), transaction.atomic():
            StoreMember.objects.filter(pk=membership.pk).update(role="owner")

    def test_string_representation_does_not_query_related_objects(self):
        membership = StoreMember.objects.create(
            user=self.user, store=self.store)
        membership = StoreMember.objects.get(pk=membership.pk)

        with self.assertNumQueries(0):
            self.assertEqual(
                str(membership),
                f"User {self.user.pk} - Store {self.store.pk} (staff)",
            )
