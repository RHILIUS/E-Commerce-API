import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.test_support import create_customer
from apps.account.models import RecipientInfo
from apps.checkout.models import Checkout, PaymentAttempt
from apps.ecommerce.models import Cart, CartItem, Order, OrderItem, Product
from apps.store.models import Store
from core.test_support import create_purchase


class CheckoutModelTests(TestCase):
    def setUp(self):
        self.user = create_customer(
            username="buyer", email="buyer@example.com", full_name="Buyer",
            phone_number="+639111111111", country_code="PH",
        )
        self.other = create_customer(
            username="other", email="other@example.com", full_name="Other",
            phone_number="+639222222222", country_code="PH",
        )
        self.recipient = RecipientInfo.objects.create(
            user=self.user, name="Buyer", phone_number=self.user.phone_number,
            country_code="PH", address_line1="Street", city="Manila",
        )
        self.store = Store.objects.create(name="Store", country_code="PH")
        self.product = Product.objects.create(store=self.store, name="Phone", price="100", stock=5)
        self.order, self.item = create_purchase(user=self.user, recipient=self.recipient, product=self.product)

    def test_checkout_ownership_and_recipient_snapshot_validation(self):
        foreign = Checkout.objects.create(user=self.other, idempotency_key=uuid.uuid4(), currency="PHP", total_amount=100)
        self.order.checkout = foreign
        with self.assertRaises(ValidationError) as error:
            self.order.save()
        self.assertIn("checkout", error.exception.message_dict)
        self.order.refresh_from_db()
        self.order.recipient_snapshot = {"name": "Buyer"}
        with self.assertRaises(ValidationError) as error:
            self.order.save()
        self.assertIn("recipient_snapshot", error.exception.message_dict)

    def test_snapshot_is_preserved_when_live_recipient_and_product_change(self):
        self.recipient.name = "New name"
        self.recipient.save()
        self.product.name = "New product name"
        self.product.price = Decimal("200")
        self.product.save()
        self.order.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.order.recipient_snapshot["name"], "Buyer")
        self.assertEqual(self.item.product_name_at_purchase, "Phone")
        self.assertEqual(self.item.price_at_purchase, Decimal("100"))

    def test_cross_store_product_and_foreign_cart_item_are_rejected(self):
        store = Store.objects.create(name="Other store", country_code="PH")
        product = Product.objects.create(store=store, name="Other product", price="100")
        self.item.product = product
        with self.assertRaises(ValidationError) as error:
            self.item.save()
        self.assertIn("product", error.exception.message_dict)
        self.item.refresh_from_db()
        cart = Cart.objects.create(user=self.other, store=self.store)
        self.item.source_cart_item = CartItem.objects.create(cart=cart, product=self.product)
        with self.assertRaises(ValidationError) as error:
            self.item.save()
        self.assertIn("source_cart_item", error.exception.message_dict)

    def test_cart_source_must_match_product_and_valid_source_is_saved(self):
        cart = Cart.objects.create(user=self.user, store=self.store)
        other = Product.objects.create(store=self.store, name="Second", price="100")
        self.item.source_cart_item = CartItem.objects.create(cart=cart, product=other)
        with self.assertRaises(ValidationError):
            self.item.save()
        self.item.source_cart_item = CartItem.objects.create(cart=cart, product=self.product)
        self.item.save()

    def test_purchased_product_cannot_move_stores(self):
        self.product.store = Store.objects.create(name="Other", country_code="PH")
        with self.assertRaises(ValidationError):
            self.product.save()

    def test_duplicate_store_order_and_idempotency_are_database_constraints(self):
        self.order.pk = None
        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.bulk_create([self.order])
        checkout = self.item.order.checkout
        duplicate = Checkout(user=self.user, idempotency_key=checkout.idempotency_key, currency="PHP", total_amount=100)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Checkout.objects.bulk_create([duplicate])

    def test_one_checkout_can_group_distinct_store_orders(self):
        store = Store.objects.create(name="Second", country_code="PH")
        product = Product.objects.create(store=store, name="Second product", price="50")
        checkout = Checkout.objects.create(
            user=self.user, idempotency_key=uuid.uuid4(), currency="PHP", total_amount=150,
        )
        first, _ = create_purchase(user=self.user, recipient=self.recipient, product=self.product, checkout=checkout)
        second, _ = create_purchase(user=self.user, recipient=self.recipient, product=product, checkout=checkout)
        self.assertEqual(checkout.orders.count(), 2)
        self.assertEqual(first.get_total() + second.get_total(), checkout.total_amount)

    def test_deleted_product_keeps_a_valid_saved_order_line(self):
        self.product.delete()
        self.item.refresh_from_db()
        self.item.save()
        self.assertIsNone(self.item.product_id)
        self.assertEqual(self.item.product_name_at_purchase, "Phone")
        self.assertEqual(self.item.get_subtotal(), Decimal("100"))

    def test_missing_related_rows_raise_validation_errors(self):
        self.item.product_id = 999999
        with self.assertRaises(ValidationError):
            self.item.save()
        self.order.checkout_id = 999999
        with self.assertRaises(ValidationError):
            self.order.save()

    def test_payment_requires_stripe_and_matching_terms(self):
        attempt = PaymentAttempt(checkout=self.order.checkout, amount=100, currency="PHP")
        with self.assertRaises(ValidationError) as error:
            attempt.save()
        self.assertIn("checkout", error.exception.message_dict)
        checkout = Checkout.objects.create(
            user=self.user, idempotency_key=uuid.uuid4(), currency="PHP", total_amount=100,
            payment_method=Checkout.PaymentMethod.STRIPE,
        )
        attempt.checkout = checkout
        attempt.amount = 99
        attempt.currency = "USD"
        with self.assertRaises(ValidationError) as error:
            attempt.save()
        self.assertIn("amount", error.exception.message_dict)
        self.assertIn("currency", error.exception.message_dict)
        attempt.amount = 100
        attempt.currency = "PHP"
        attempt.save()
        duplicate = PaymentAttempt(checkout=checkout, amount=100, currency="PHP")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentAttempt.objects.bulk_create([duplicate])
        attempt.status = PaymentAttempt.Status.EXPIRED
        attempt.save()
        duplicate.save()
        self.assertEqual(checkout.payment_attempts.count(), 2)
        self.assertEqual(self.product.stock, 5)

    def test_checkout_payment_terms_are_locked_after_attempt_creation(self):
        checkout = Checkout.objects.create(
            user=self.user, idempotency_key=uuid.uuid4(), currency="PHP", total_amount=100,
            payment_method=Checkout.PaymentMethod.STRIPE,
        )
        PaymentAttempt.objects.create(checkout=checkout, amount=100, currency="PHP")
        checkout.total_amount = 200
        with self.assertRaises(ValidationError):
            checkout.save()
