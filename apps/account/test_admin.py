"""Recipient admin behavior through Django's actual page and POST request flow."""

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from apps.ecommerce.models import Product
from apps.store.models import Store
from core.test_support import create_purchase

from .models import RecipientInfo
from .tests import customer, recipient_data


class RecipientAdminTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-only",
            phone_number="+639111111111", full_name="Admin", country_code="PH",
        )
        self.buyer = customer("buyer", "+639171234567")
        self.other = customer("other", "+16502532222")
        self.client.force_login(self.staff)
        self.add_url = reverse("admin:account_recipientinfo_add")
        self.list_url = reverse("admin:account_recipientinfo_changelist")

    def payload(self, **overrides):
        """Return the complete fields submitted by the admin form."""
        return {**recipient_data(), "user": self.buyer.pk, "address_line2": "",
                "region": "", "postal_code": "", **overrides}

    def change_url(self, recipient):
        """Resolve a saved recipient's admin edit page."""
        return reverse("admin:account_recipientinfo_change", args=[recipient.pk])

    def test_creation_normalizes_fields_sets_first_default_and_logs_once(self):
        response = self.client.get(self.add_url)
        self.assertContains(response, 'class="admin-autocomplete"')
        response = self.client.post(self.add_url, self.payload(name="  Juan  ", country_code="ph"))
        self.assertEqual(response.status_code, 302)
        recipient = RecipientInfo.objects.get()
        self.assertEqual(recipient.user, self.buyer)
        self.assertEqual(recipient.name, "Juan")
        self.assertEqual(recipient.country_code, "PH")
        self.assertTrue(recipient.is_default)
        self.assertEqual(LogEntry.objects.filter(object_id=str(recipient.pk), action_flag=ADDITION).count(), 1)

    def test_creation_and_edit_replace_default_preserving_owner_and_order_snapshot(self):
        first = RecipientInfo.objects.create(user=self.buyer, is_default=True, **recipient_data())
        self.assertEqual(self.client.post(self.add_url, self.payload(name="Second", is_default="on")).status_code, 302)
        second = RecipientInfo.objects.exclude(pk=first.pk).get()
        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)
        store = Store.objects.create(name="Store", country_code="PH")
        product = Product.objects.create(store=store, name="Camera", price="100")
        order, _ = create_purchase(user=self.buyer, recipient=first, product=product)
        snapshot = dict(order.recipient_snapshot)
        response = self.client.get(self.change_url(first))
        self.assertNotIn("user", response.context["adminform"].form.fields)
        response = self.client.post(self.change_url(first), self.payload(name="Updated", is_default="on", user=self.other.pk))
        self.assertEqual(response.status_code, 302)
        first.refresh_from_db()
        second.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(first.name, "Updated")
        self.assertEqual(first.user_id, self.buyer.pk)
        self.assertTrue(first.is_default)
        self.assertFalse(second.is_default)
        self.assertEqual(order.recipient_snapshot, snapshot)
        self.assertEqual(LogEntry.objects.filter(object_id=str(first.pk), action_flag=CHANGE).count(), 1)

    def test_invalid_fields_and_clearing_default_show_errors_without_writes(self):
        recipient = RecipientInfo.objects.create(user=self.buyer, is_default=True, **recipient_data())
        for field, value in (("phone_number", "09171234567"), ("country_code", "ZZ"), ("is_default", "")):
            with self.subTest(field=field):
                payload = self.payload(is_default="on")
                payload[field] = value
                response = self.client.post(self.change_url(recipient), payload)
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["adminform"].form.errors)
                recipient.refresh_from_db()
                self.assertTrue(recipient.is_default)
                self.assertEqual(recipient.phone_number, "+639171234567")
                self.assertEqual(recipient.country_code, "PH")
        response = self.client.post(self.add_url, self.payload(country_code="ZZ", is_default="on"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RecipientInfo.objects.count(), 1)
        self.assertFalse(LogEntry.objects.exists())

    def test_search_and_filters_find_recipients(self):
        RecipientInfo.objects.create(user=self.buyer, is_default=True, **recipient_data())
        other = RecipientInfo.objects.create(user=self.other, **recipient_data(name="Other", country_code="US", phone_number=self.other.phone_number, address_line1="456 Other Road"))
        for query in (self.buyer.email, "Juan", "123 Example Street", self.buyer.phone_number):
            response = self.client.get(self.list_url, {"q": query})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["cl"].result_count, 1)
        response = self.client.get(self.list_url, {"country_code__exact": "US", "is_default__exact": "0"})
        self.assertEqual(list(response.context["cl"].result_list), [other])

    def test_staff_with_add_and_change_permissions_can_manage_recipients(self):
        manager = customer("manager", "+639191234567")
        manager.is_staff = True
        manager.save()
        manager.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="account", codename__in=(
                "view_recipientinfo", "add_recipientinfo", "change_recipientinfo",
            ),
        ))
        self.client.force_login(manager)
        self.assertEqual(self.client.post(self.add_url, self.payload()).status_code, 302)
        first = RecipientInfo.objects.get()
        self.assertEqual(self.client.post(self.add_url, self.payload(name="Second")).status_code, 302)
        second = RecipientInfo.objects.get(name="Second")
        self.assertFalse(second.is_default)
        self.assertEqual(self.client.post(self.change_url(second), self.payload(name="Edited", is_default="on")).status_code, 302)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)
        self.assertEqual(second.name, "Edited")

    def test_permissions_and_deletion_are_enforced(self):
        recipient = RecipientInfo.objects.create(user=self.buyer, is_default=True, **recipient_data())
        delete_url = reverse("admin:account_recipientinfo_delete", args=[recipient.pk])
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(delete_url).status_code, 403)
        response = self.client.get(self.list_url)
        self.assertNotIn("delete_selected", response.context["cl"].model_admin.get_actions(response.wsgi_request))
        viewer = customer("viewer", "+639181234567")
        viewer.is_staff = True
        viewer.save()
        viewer.user_permissions.add(Permission.objects.get(codename="view_recipientinfo"))
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(self.list_url).status_code, 200)
        self.assertEqual(self.client.get(self.change_url(recipient)).status_code, 200)
        self.assertEqual(self.client.get(self.add_url).status_code, 403)
        self.assertEqual(self.client.post(self.change_url(recipient), self.payload(is_default="on")).status_code, 403)
        self.client.force_login(self.buyer)
        self.assertEqual(self.client.get(self.list_url).status_code, 302)
