from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.account.models import RecipientInfo
from apps.checkout.models import Checkout, PaymentAttempt
from apps.ecommerce.models import Order, Product
from apps.inventory.models import StockReservation
from apps.store.models import Store
from core.test_support import create_purchase


class OrderAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-only",
            phone_number="+639111111111", full_name="Admin", country_code="PH",
        )
        recipient = RecipientInfo.objects.create(
            user=self.user, name="Buyer", phone_number=self.user.phone_number,
            country_code="PH", address_line1="Street", city="Manila",
        )
        store = Store.objects.create(name="Store", country_code="PH")
        product = Product.objects.create(store=store, name="Laptop", price="100")
        self.order, self.item = create_purchase(user=self.user, recipient=recipient, product=product)
        self.client.force_login(self.user)

    def test_purchase_is_visible_but_post_cannot_change_recorded_amounts(self):
        url = reverse("admin:ecommerce_order_change", args=[self.order.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Laptop")
        self.assertContains(response, "recipient_snapshot")
        self.assertEqual(self.client.post(url, {"payment_status": "paid"}).status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.UNPAID)

    def test_transaction_admin_cannot_add_change_or_delete_even_as_superuser(self):
        request = self.client.get(reverse("admin:index")).wsgi_request
        for model in (Order, Checkout, PaymentAttempt, StockReservation):
            with self.subTest(model=model.__name__):
                model_admin = admin.site._registry[model]
                self.assertTrue(model_admin.has_view_permission(request))
                self.assertFalse(model_admin.has_add_permission(request))
                self.assertFalse(model_admin.has_change_permission(request))
                self.assertFalse(model_admin.has_delete_permission(request))
        inline = admin.site._registry[Order].get_inline_instances(request, self.order)[0]
        self.assertFalse(inline.has_add_permission(request, self.order))
        self.assertFalse(inline.has_change_permission(request, self.order))
        self.assertFalse(inline.can_delete)
