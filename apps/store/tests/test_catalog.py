"""Store visibility, sales qualification, filtering and pagination contracts."""
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.account.models import RecipientInfo
from apps.checkout.models import Checkout, OrderRefund, PaymentAttempt
from apps.ecommerce.models import Category, Order, Product, ProductImage
from apps.store.services.catalog import store_products
from apps.store.models import Store, StoreAddress, StoreMember
from core.test_support import create_purchase


class StoreCatalogTests(APITestCase):
    def setUp(self):
        rates = patch("apps.store.views.get_display_rates", return_value={"PHP": ("USD", Decimal(56))})
        rates.start()
        self.addCleanup(rates.stop)
        self.store = Store.objects.create(name="Public shop", country_code="PH", public_email="shop@example.com", public_phone="+63 917 123 4567")
        self.other = Store.objects.create(name="Other shop", country_code="PH")
        self.user = get_user_model().objects.create_user(username="buyer", email="private@example.com", full_name="Private member", country_code="PH", phone_number="+639171234567")
        self.recipient = RecipientInfo.objects.create(user=self.user, name="Buyer", phone_number="+639171234567", country_code="PH", address_line1="Private home", city="Manila")
        self.category = Category.objects.create(name="Shoes", taxonomy_id="shoes")
        self.url = f"/api/shop/stores/{self.store.pk}/"

    def product(self, name="Running Shoes", **kwargs):
        """Create active listings by default while permitting visibility/stock fixtures."""
        defaults = dict(store=self.store, name=name, status="active", price="560.00", stock=5, category=self.category)
        defaults.update(kwargs)
        return Product.objects.create(**defaults)

    def purchase(self, product, quantity=1, method="cod", status="delivered", paid=False, age=1):
        """Create valid purchase history with an explicitly controlled placement timestamp."""
        checkout = Checkout.objects.create(user=self.user, idempotency_key=uuid.uuid4(), payment_method=method, currency="PHP", total_amount=Decimal("560") * quantity)
        order, _ = create_purchase(user=self.user, recipient=self.recipient, product=product, quantity=quantity, status=status, checkout=checkout)
        Order.objects.filter(pk=order.pk).update(created_at=self.now - timedelta(days=age), payment_status="paid" if paid else "unpaid")
        return order

    def test_profile_exposes_only_public_fields_and_exact_category_counts(self):
        self.now = timezone.now()
        product = self.product()
        self.product("Draft", status="draft")
        self.product("Archived", status="archived")
        self.product("Other", store=self.other)
        self.purchase(product, quantity=2)
        StoreMember.objects.create(store=self.store, user=self.user, role="admin")
        StoreAddress.objects.create(store=self.store, country_code="PH", address_line1="Public business address", city="Manila")
        with self.assertNumQueries(4):
            response = self.client.get(self.url)
        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["listed_product_count"], 1)
        self.assertEqual(data["items_sold"], 2)
        self.assertEqual(data["public_email"], "shop@example.com")
        self.assertEqual(data["address"]["address_line1"], "Public business address")
        self.assertEqual(data["categories"], [{"id": self.category.pk, "name": "Shoes", "product_count": 1}])
        self.assertEqual(set(data), {"id", "name", "description", "profile_image", "banner_image", "created_at", "public_email", "public_phone", "address", "listed_product_count", "items_sold", "categories"})
        self.assertNotIn("private@example.com", response.content.decode())
        self.assertNotIn("Private home", response.content.decode())

    def test_empty_store_null_images_address_and_blank_contact(self):
        data = self.client.get(f"/api/shop/stores/{self.other.pk}/").json()
        self.assertIsNone(data["profile_image"])
        self.assertIsNone(data["banner_image"])
        self.assertIsNone(data["address"])
        self.assertEqual(data["public_email"], "")
        self.assertEqual(data["public_phone"], "")
        self.assertEqual(data["items_sold"], 0)
        self.assertEqual(data["categories"], [])
        self.assertEqual(self.client.get(self.url + "home/").json(), {"new_products": [], "trending_products": [], "best_sellers": []})

    def test_search_sort_pagination_images_prices_and_visibility(self):
        products = [self.product(f"Running Shoes {i}") for i in range(12)]
        self.product("Private shoes", status="draft")
        self.product("Archived shoes", status="archived")
        self.product("Other shoes", store=self.other)
        self.product("Unrelated")
        Product.objects.filter(pk=products[0].pk).update(stock=0)
        image = ProductImage.objects.create(product=products[0], image="products/photo.jpg", is_default=True)
        with self.assertNumQueries(4):
            response = self.client.get(self.url + "products/", {"q": "  SHOES  ", "sort": "oldest"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 12)
        self.assertEqual([row["id"] for row in data["results"]], [p.pk for p in products[:10]])
        self.assertEqual(data["results"][0]["stock"], 0)
        self.assertEqual(data["results"][0]["display_price"], {"amount": "10.00", "currency": "USD"})
        self.assertTrue(data["results"][0]["default_image"]["image"].endswith(image.image.url))
        second = self.client.get(self.url + "products/", {"q": "shoes", "page": 2, "sort": "oldest"}).json()
        self.assertEqual([row["id"] for row in second["results"]], [p.pk for p in products[10:]])
        self.assertIsNone(second["next"])
        self.assertEqual(self.client.get(self.url + "products/", {"q": "missing"}).json()["count"], 0)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(self.url + "products/", {"q": "shoes"}).json()["count"], 12)

    def test_categories_filter_exact_assignment_and_empty_category(self):
        parent = Category.objects.create(name="Clothing", taxonomy_id="clothing")
        self.category.parent = parent
        self.category.save()
        child = self.product()
        self.product(category=None)
        self.product(store=self.other, category=parent)
        data = self.client.get(self.url + "products/", {"category": self.category.pk}).json()
        self.assertEqual([row["id"] for row in data["results"]], [child.pk])
        self.assertEqual(self.client.get(self.url + "products/", {"category": parent.pk}).json()["count"], 0)
        self.category.is_active = False
        self.category.save()
        self.assertEqual(self.client.get(self.url).json()["categories"], [])
        self.assertEqual(self.client.get(self.url + "products/", {"category": self.category.pk}).status_code, 404)

    def test_sales_qualification_refunds_and_rolling_window(self):
        self.now = timezone.now()
        product = self.product()
        self.purchase(product, 3, method="stripe", paid=True, status="pending")
        self.purchase(product, 4, status="delivered")
        self.purchase(product, 5, status="completed")
        self.purchase(product, 100, method="stripe", paid=False)
        self.purchase(product, 100, status="shipped")
        self.purchase(product, 100, method="stripe", paid=True, status="cancelled")
        self.purchase(product, 100, status="cancelled")
        refunded = self.purchase(product, 100, method="stripe", paid=True)
        attempt = PaymentAttempt.objects.create(checkout=refunded.checkout, amount=refunded.checkout.total_amount, currency="PHP", status="succeeded")
        OrderRefund.objects.create(order=refunded, payment_attempt=attempt, amount_cents=5600000, status="succeeded")
        self.purchase(product, 6, age=30)
        self.purchase(product, 7, age=31)
        self.purchase(product, 100, age=-1)
        scored = store_products(self.store, self.now).get(pk=product.pk)
        self.assertEqual(scored.sales_recent, 18)
        self.assertEqual(scored.sales_total, 25)
        with patch("apps.store.services.catalog.timezone.now", return_value=self.now):
            home = self.client.get(self.url + "home/").json()
        self.assertEqual(home["trending_products"][0]["sold_count"], 25)
        self.assertEqual(self.client.get(self.url).json()["items_sold"], 25)

    def test_home_limits_ranking_ties_zero_sales_and_historical_totals(self):
        self.now = timezone.now()
        products = [self.product(str(i)) for i in range(12)]
        Product.objects.filter(store=self.store).update(created_at=self.now)
        for product in products:
            self.purchase(product, 1)
        self.purchase(products[0], 20, age=40)
        self.purchase(products[1], 5)
        no_sales = self.product("No sales")
        old = self.product("Archived", status="archived")
        self.purchase(old, 50)
        deleted = self.product("Deleted")
        self.purchase(deleted, 60)
        deleted.delete()
        data = self.client.get(self.url + "home/").json()
        self.assertTrue(all(len(section) == 10 for section in data.values()))
        self.assertEqual(data["new_products"][0]["id"], no_sales.pk)
        self.assertEqual(data["trending_products"][0]["id"], products[1].pk)
        self.assertEqual(data["trending_products"][1]["id"], products[-1].pk)
        self.assertEqual(data["best_sellers"][0]["id"], products[0].pk)
        self.assertEqual(self.client.get(self.url).json()["items_sold"], 147)
        for sort, first in [("popular", products[1]), ("best_sellers", products[0]), ("latest", no_sales)]:
            self.assertEqual(self.client.get(self.url + "products/", {"sort": sort}).json()["results"][0]["id"], first.pk)
        self.assertNotIn(no_sales.pk, [p["id"] for p in data["trending_products"]])

    def test_invalid_filters_pages_missing_store_and_no_write_api(self):
        for suffix in ("", "home/", "products/"):
            self.assertEqual(self.client.get(f"/api/shop/stores/999999/{suffix}").status_code, 404)
            self.assertEqual(self.client.get(self.url + suffix, HTTP_AUTHORIZATION="Bearer invalid").status_code, 401)
            self.assertEqual(self.client.post(self.url + suffix, {}).status_code, 405)
        for params in ({"sort": "invalid"}, {"category": "bad"}, {"category": -1}, {"q": "x" * 201}):
            self.assertEqual(self.client.get(self.url + "products/", params).status_code, 400)
        for page in (0, -1, 2, "invalid"):
            self.assertEqual(self.client.get(self.url + "products/", {"page": page}).status_code, 404)
