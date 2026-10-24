from datetime import timedelta
from decimal import Decimal
import uuid
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from core.test_support import create_customer
from apps.account.models import RecipientInfo
from apps.ecommerce.models import Category, Order, Product
from apps.checkout.models import Checkout, OrderRefund, PaymentAttempt
from apps.ecommerce.services.order_quantities import with_ordered_totals
from apps.ecommerce.services.product_recommendations import rank_products_for_user
from apps.store.models import Store
from core.test_support import create_purchase


class ProductRecommendationTests(APITestCase):
    def setUp(self):
        rate_lookup = patch("apps.ecommerce.views.catalog.get_display_rates", return_value={})
        rate_lookup.start()
        self.addCleanup(rate_lookup.stop)
        self.user = create_customer(
            username="buyer", email="buyer@example.com", phone_number="+639111111111",
            full_name="Buyer", country_code="PH",
        )
        self.other_user = create_customer(
            username="other", email="other@example.com", phone_number="+639222222222",
            full_name="Other", country_code="PH",
        )
        self.store = Store.objects.create(name="Store", country_code="PH")
        self.laptops = Category.objects.create(
            name="Laptops", taxonomy_id="laptops")
        self.phones = Category.objects.create(
            name="Phones", taxonomy_id="phones")
        self.laptop = self.product("Laptop", self.laptops)
        self.new_laptop = self.product("New laptop", self.laptops)
        self.phone = self.product("Phone", self.phones)
        self.uncategorized = self.product("Uncategorized", None)

    def product(self, name, category):
        """Create an active product whose creation order can break ranking ties."""
        return Product.objects.create(
            name=name, category=category, store=self.store,
            price="100.00", status=Product.Status.ACTIVE,
        )

    def purchase(self, product, quantity, status, user=None, *, method="cod", paid=False):
        """Create history with explicit payment eligibility, without provider effects."""
        user = user or self.user
        recipient, _ = RecipientInfo.objects.get_or_create(
            user=user, is_default=True,
            defaults={
                "name": "Buyer", "phone_number": user.phone_number,
                "country_code": "PH", "address_line1": "Test address", "city": "Manila",
            },
        )
        checkout = Checkout.objects.create(
            user=user, idempotency_key=uuid.uuid4(), payment_method=method,
            currency="PHP", total_amount=Decimal("100") * quantity,
        )
        order, _ = create_purchase(
            user=user, recipient=recipient, product=product,
            quantity=quantity, price="100.00", status=status, checkout=checkout,
        )
        if paid:
            order.payment_status = Order.PaymentStatus.PAID
            order.save(update_fields=["payment_status"])
        return order

    def test_payment_and_fulfillment_qualification_matches_ordered_units(self):
        products = Product.objects.filter(status=Product.Status.ACTIVE)
        for method, paid in (("cod", False), ("stripe", False), ("stripe", True)):
            for status in Order.Status.values:
                with self.subTest(method=method, paid=paid, status=status):
                    order = self.purchase(self.laptop, 3, status, method=method, paid=paid)
                    qualifies = status != Order.Status.CANCELLED
                    expected = 3 if qualifies else 0
                    ranked = rank_products_for_user(with_ordered_totals(products), self.user)
                    laptop = ranked.get(pk=self.laptop.pk)
                    self.assertEqual(laptop.preference_score, expected)
                    self.assertEqual(laptop.recent_popularity, expected)
                    self.assertEqual(laptop.ordered_total, expected)
                    self.assertEqual(ranked.get(pk=self.new_laptop.pk).preference_score, expected)
                    guest = rank_products_for_user(products, AnonymousUser()).get(pk=self.laptop.pk)
                    self.assertEqual(guest.preference_score, 0)
                    self.assertEqual(guest.recent_popularity, expected)
                    order.status = Order.Status.CANCELLED
                    order.save(update_fields=["status"])
                    cancelled = rank_products_for_user(products, self.user).get(pk=self.laptop.pk)
                    self.assertEqual(cancelled.preference_score, 0)
                    self.assertEqual(cancelled.recent_popularity, 0)

    def test_refunds_retain_both_scores_until_order_is_cancelled(self):
        order = self.purchase(self.laptop, 3, Order.Status.PENDING, method="stripe", paid=True)
        attempt = PaymentAttempt.objects.create(
            checkout=order.checkout, amount=order.checkout.total_amount, currency="PHP", status="succeeded",
        )
        refund = OrderRefund.objects.create(order=order, payment_attempt=attempt, amount_cents=30000)
        products = Product.objects.all()
        for status in ("pending", "requires_action", "failed", "canceled", "succeeded"):
            with self.subTest(refund_status=status):
                refund.status = status
                refund.save()
                ranked = rank_products_for_user(with_ordered_totals(products), self.user)
                laptop = ranked.get(pk=self.laptop.pk)
                expected = 3
                self.assertEqual(laptop.preference_score, expected)
                self.assertEqual(laptop.recent_popularity, expected)
                self.assertEqual(laptop.ordered_total, expected)
                self.client.force_authenticate(self.user)
                response = self.client.get("/api/shop/products/").json()
                self.assertEqual(response["results"][0]["id"], self.laptop.pk)

        order.status = Order.Status.CANCELLED
        order.save(update_fields=["status"])
        cancelled = rank_products_for_user(with_ordered_totals(products), self.user).get(pk=self.laptop.pk)
        self.assertEqual((cancelled.preference_score, cancelled.recent_popularity, cancelled.ordered_total), (0, 0, 0))

    def test_endpoint_ranks_by_quantity_isolates_users_and_keeps_response_shape(self):
        self.purchase(self.laptop, 2, Order.Status.DELIVERED)
        self.purchase(self.laptop, 2, Order.Status.COMPLETED)
        self.purchase(self.phone, 3, Order.Status.DELIVERED)
        self.purchase(self.phone, 100, Order.Status.CANCELLED)
        self.purchase(self.phone, 100, Order.Status.DELIVERED, self.other_user)
        draft = self.product("Draft laptop", self.laptops)
        draft.status = Product.Status.DRAFT
        draft.save()
        self.client.force_authenticate(self.user)
        with self.assertNumQueries(3):
            response = self.client.get("/api/shop/products/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["id"] for row in response.json()["results"]],
            [self.laptop.pk, self.new_laptop.pk,
                self.phone.pk, self.uncategorized.pk],
        )
        self.assertEqual(set(response.json()["results"][0]), {
            "id", "name", "short_description", "price", "currency", "display_price", "ordered_count", "default_image",
        })

    def test_guests_no_history_and_equal_scores_keep_newest_first_order(self):
        products = Product.objects.all()
        Product.objects.update(created_at=timezone.now())
        expected = [self.uncategorized.pk, self.phone.pk,
                    self.new_laptop.pk, self.laptop.pk]
        for user in (AnonymousUser(), self.user):
            with self.assertNumQueries(0):
                ranked = rank_products_for_user(products, user)
            self.assertEqual(
                list(ranked.values_list("pk", flat=True)), expected)
        self.purchase(self.laptop, 1, Order.Status.COMPLETED)
        self.purchase(self.phone, 1, Order.Status.DELIVERED)
        self.assertEqual(
            list(rank_products_for_user(
                products, self.user).values_list("pk", flat=True)),
            [self.phone.pk, self.laptop.pk, self.new_laptop.pk, self.uncategorized.pk],
        )

    def test_recent_popularity_for_guests_and_users_without_history(self):
        self.purchase(self.laptop, 2, Order.Status.DELIVERED, self.other_user)
        self.purchase(self.laptop, 3, Order.Status.COMPLETED, self.other_user)
        self.purchase(self.phone, 4, Order.Status.DELIVERED, self.other_user)
        self.purchase(self.phone, 100, Order.Status.CANCELLED, self.other_user)
        for user in (AnonymousUser(), self.user):
            self.client.force_authenticate(
                user if user.is_authenticated else None)
            with self.assertNumQueries(3):
                response = self.client.get("/api/shop/products/")
            self.assertEqual(
                [row["id"] for row in response.json()["results"]],
                [self.laptop.pk, self.phone.pk,
                    self.uncategorized.pk, self.new_laptop.pk],
            )

    @override_settings(PRODUCT_POPULARITY_DAYS=7)
    def test_window_uses_order_creation_time_including_cutoff_and_excludes_future(self):
        now = timezone.now()
        for age, quantity in (
            (timedelta(days=7), 2),
            (timedelta(days=7, microseconds=1), 100),
            (timedelta(days=-1), 100),
            (timedelta(0), 3),
        ):
            order = self.purchase(self.laptop, quantity, Order.Status.COMPLETED)
            Order.objects.filter(pk=order.pk).update(created_at=now - age)
        with patch("apps.ecommerce.services.product_recommendations.timezone.now", return_value=now):
            ranked = rank_products_for_user(Product.objects.all(), self.user)
            self.assertEqual(ranked.get(
                pk=self.laptop.pk).recent_popularity, 5)
            self.assertEqual(ranked.get(pk=self.laptop.pk).preference_score, 105)
            with override_settings(PRODUCT_POPULARITY_DAYS=30):
                ranked = rank_products_for_user(
                    Product.objects.all(), self.user)
                self.assertEqual(ranked.get(
                    pk=self.laptop.pk).recent_popularity, 105)

    def test_old_history_still_sets_personal_category_priority(self):
        old_order = self.purchase(self.laptop, 1, Order.Status.DELIVERED)
        Order.objects.filter(pk=old_order.pk).update(
            created_at=timezone.now() - timedelta(days=60),
        )
        self.purchase(self.phone, 100, Order.Status.DELIVERED, self.other_user)
        ranked = rank_products_for_user(Product.objects.all(), self.user)
        self.assertEqual(list(ranked.values_list("pk", flat=True)), [
            self.new_laptop.pk, self.laptop.pk, self.phone.pk, self.uncategorized.pk,
        ])
        self.assertEqual(ranked.get(pk=self.laptop.pk).recent_popularity, 0)

    def test_preferred_products_are_ranked_before_page_is_selected(self):
        for index in range(9):
            self.product(f"New unrelated {index}", None)
        self.purchase(self.laptop, 1, Order.Status.DELIVERED)
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/shop/products/").json()
        self.assertEqual(response["count"], 13)
        self.assertEqual(len(response["results"]), 12)
        self.assertEqual(
            [item["id"] for item in response["results"][:2]],
            [self.laptop.pk, self.new_laptop.pk],
        )

    def test_missing_products_and_categories_do_not_contribute(self):
        deleted_product = self.product("Deleted", self.laptops)
        self.purchase(deleted_product, 10, Order.Status.DELIVERED)
        deleted_product.delete()
        self.purchase(self.uncategorized, 10, Order.Status.DELIVERED)
        scores = rank_products_for_user(Product.objects.all(), self.user).values_list(
            "preference_score", flat=True,
        )
        self.assertEqual(list(scores), [0, 0, 0, 0])
