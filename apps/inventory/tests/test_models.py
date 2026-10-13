from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.test_support import create_customer
from apps.account.models import RecipientInfo
from apps.ecommerce.models import Product
from apps.inventory.models import StockReservation
from apps.store.models import Store
from core.test_support import create_purchase


class StockReservationTests(TestCase):
    def setUp(self):
        user = create_customer(
            username="buyer", email="buyer@example.com", full_name="Buyer",
            phone_number="+639111111111", country_code="PH",
        )
        recipient = RecipientInfo.objects.create(
            user=user, name="Buyer", phone_number=user.phone_number,
            country_code="PH", address_line1="Street", city="Manila",
        )
        store = Store.objects.create(name="Store", country_code="PH")
        self.product = Product.objects.create(store=store, name="Phone", price="100", stock=5)
        _, self.item = create_purchase(user=user, recipient=recipient, product=self.product, quantity=2)

    def test_reservation_must_match_product_and_quantity(self):
        other = Product.objects.create(store=self.product.store, name="Other", price="100")
        reservation = StockReservation(
            order_item=self.item, product=other, quantity=1,
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        with self.assertRaises(ValidationError) as error:
            reservation.save()
        self.assertEqual(set(error.exception.message_dict), {"product", "quantity"})
        reservation.product = self.product
        reservation.quantity = 2
        reservation.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.item.quantity = 3
        with self.assertRaises(ValidationError):
            self.item.save()

    def test_missing_item_and_zero_quantity_are_rejected(self):
        reservation = StockReservation(order_item_id=999999, product=self.product, quantity=0,
                                       expires_at=timezone.now() + timedelta(minutes=30))
        with self.assertRaises(ValidationError):
            reservation.save()
