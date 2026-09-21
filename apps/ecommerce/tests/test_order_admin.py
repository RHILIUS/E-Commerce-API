from decimal import Decimal

from django.contrib.auth import get_user_model
from django.forms.models import inlineformset_factory
from django.test import TestCase

from apps.account.models import RecipientInfo
from apps.ecommerce.admin import OrderItemAdminFormSet, OrderItemInline
from apps.ecommerce.models import Order, OrderItem, Product
from apps.store.models import Store


class OrderItemAdminTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="buyer", email="buyer@example.com", full_name="Buyer",
            phone_number="+639111111111", country_code="PH",
        )
        recipient = RecipientInfo.objects.create(
            user=user, name="Buyer", phone_number=user.phone_number,
            is_default=True, country_code="PH", address_line1="Street", city="Manila",
        )
        self.order = Order.objects.create(user=user, recipient_info=recipient)
        store = Store.objects.create(name="Store", country_code="PH")
        self.product = Product.objects.create(
            store=store, name="Laptop", price=Decimal("100.00"),
        )
        self.other_product = Product.objects.create(
            store=store, name="Phone", price=Decimal("250.00"),
        )
        self.formset_class = inlineformset_factory(
            Order, OrderItem, formset=OrderItemAdminFormSet,
            fields=("product", "quantity"), extra=1,
        )

    def save_inline(self, product, quantity, item=None):
        """Submit the same item fields the admin inline accepts."""
        existing = item is not None
        data = {
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "1" if existing else "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-product": str(product.pk),
            "items-0-quantity": str(quantity),
        }
        if existing:
            data["items-0-id"] = str(item.pk)
        formset = self.formset_class(data=data, instance=self.order, prefix="items")
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()
        return self.order.items.get()

    def test_inline_captures_current_price_and_preserves_it_on_quantity_edit(self):
        self.assertIn("price_at_purchase", OrderItemInline.readonly_fields)
        item = self.save_inline(self.product, 2)
        self.assertEqual(item.price_at_purchase, Decimal("100.00"))

        self.product.price = Decimal("150.00")
        self.product.save(update_fields=["price"])
        item = self.save_inline(self.product, 3, item)
        self.assertEqual(item.price_at_purchase, Decimal("100.00"))
        self.assertEqual(item.get_subtotal(), Decimal("300.00"))

    def test_changing_product_uses_replacement_products_current_price(self):
        item = self.save_inline(self.product, 1)
        item = self.save_inline(self.other_product, 1, item)
        self.assertEqual(item.product_id, self.other_product.pk)
        self.assertEqual(item.price_at_purchase, Decimal("250.00"))
