from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.account.models import RecipientInfo
from apps.ecommerce.models import Order, OrderItem, Product, ProductImage
from apps.store.models import Store, StoreMember


class ProductDetailTests(APITestCase):
    def setUp(self):
        rates = patch("apps.ecommerce.views.get_display_rates",
                      return_value={"PHP": ("USD", Decimal(56))})
        self.rates = rates.start()
        self.addCleanup(rates.stop)
        self.store = Store.objects.create(
            name="Detail store", country_code="PH")
        self.product = Product.objects.create(
            store=self.store, name="Phone", short_description="Short summary",
            description="Full product description", stock=3, price="5600.00",
            sold_count=12, status=Product.Status.ACTIVE,
        )
        self.url = f"/api/shop/products/{self.product.pk}/"
        self.user = get_user_model().objects.create_user(
            username="viewer", email="viewer@example.com", phone_number="+639111111111",
            full_name="Viewer", country_code="PH",
        )

    def test_public_detail_contract_and_bounded_queries(self):
        Product.objects.create(
            store=self.store, name="Draft", price="1", status="draft")
        Product.objects.create(
            store=self.store, name="Archived", price="1", status="archived")
        with self.assertNumQueries(3):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), {
            "id", "name", "short_description", "description", "price", "currency",
            "display_price", "sold_count", "default_image", "images", "stock",
            "status", "archived_at", "store", "can_purchase",
        })
        self.assertEqual(data["description"], "Full product description")
        self.assertEqual(data["short_description"], "Short summary")
        self.assertEqual(data["price"], "5600.00")
        self.assertEqual(data["currency"], "PHP")
        self.assertEqual(data["display_price"], {
                         "amount": "100.00", "currency": "USD"})
        self.assertEqual(data["stock"], 3)
        self.assertEqual(data["sold_count"], 12)
        self.assertTrue(data["can_purchase"])
        self.assertEqual(data["images"], [])
        self.assertIsNone(data["default_image"])
        self.assertIsNone(data["archived_at"])
        self.assertEqual(set(data["store"]), {
            "id", "name", "profile_image", "profile_image_crop", "listed_product_count", "created_at",
        })
        self.assertEqual(data["store"]["listed_product_count"], 1)
        self.assertEqual(data["store"]["name"], "Detail store")
        self.assertIsNone(data["store"]["profile_image"])
        self.assertTrue(data["store"]["created_at"])

    def test_gallery_order_default_and_storage_urls(self):
        first = ProductImage.objects.create(
            product=self.product, image="products/first.jpg", order=0)
        chosen = ProductImage.objects.create(
            product=self.product, image="products/default.jpg", is_default=True,
            order=1, alt_text="Camera", crop={"x": 0.5},
        )
        last = ProductImage.objects.create(
            product=self.product, image="products/last.jpg", is_default=True, order=1)
        self.store.profile_image = "stores/profile/logo.jpg"
        self.store.profile_image_crop = {"x": 0.2}
        self.store.save()
        with self.assertNumQueries(3):
            response = self.client.get(self.url)
        data = response.json()
        self.assertEqual([image["id"] for image in data["images"]], [
                         first.pk, chosen.pk, last.pk])
        expected = response.wsgi_request.build_absolute_uri(chosen.image.url)
        self.assertEqual(data["default_image"], {
                         "image": expected, "alt_text": "Camera", "crop": {"x": 0.5}})
        self.assertEqual(data["store"]["profile_image"], response.wsgi_request.build_absolute_uri(
            self.store.profile_image.url))
        self.assertEqual(data["store"]["profile_image_crop"], {"x": 0.2})
        ProductImage.objects.filter(
            product=self.product).update(is_default=False)
        data = self.client.get(self.url).json()
        self.assertIsNone(data["default_image"])
        self.assertEqual(len(data["images"]), 3)

    def test_archived_is_public_and_cannot_be_purchased(self):
        self.product.status = Product.Status.ARCHIVED
        self.product.archived_at = timezone.now()
        self.product.save()
        for user in (None, self.user):
            with self.subTest(user=user):
                self.client.force_authenticate(user=user)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "archived")
                self.assertFalse(response.json()["can_purchase"])
                self.assertTrue(response.json()["archived_at"])

    def test_zero_stock_remains_visible_but_unavailable(self):
        self.product.stock = 0
        self.product.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["can_purchase"])
        self.assertEqual(response.json()["store"]["listed_product_count"], 1)

    def test_drafts_are_hidden_from_guests_outsiders_and_other_store_members(self):
        self.product.status = Product.Status.DRAFT
        self.product.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_authenticate(user=self.user)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        other_store = Store.objects.create(
            name="Other store", country_code="PH")
        StoreMember.objects.create(
            store=other_store, user=self.user, role="admin")
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.rates.assert_not_called()

    def test_both_store_roles_can_preview_drafts_and_revocation_takes_effect(self):
        self.product.status = Product.Status.DRAFT
        self.product.save()
        member = StoreMember.objects.create(store=self.store, user=self.user)
        self.client.force_authenticate(user=self.user)
        for role in ("admin", "staff"):
            member.role = role
            member.save()
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["can_purchase"])
        member.delete()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_missing_product_and_invalid_token(self):
        self.assertEqual(self.client.get(
            "/api/shop/products/999999/").status_code, 404)
        self.assertEqual(self.client.get(
            self.url, HTTP_AUTHORIZATION="Bearer invalid").status_code, 401)

    def test_unavailable_conversion_retains_store_currency(self):
        self.rates.return_value = {}
        self.assertEqual(self.client.get(self.url).json()["display_price"], {
                         "amount": "5600.00", "currency": "PHP"})

    def test_endpoint_is_read_only(self):
        self.assertEqual(self.client.post(
            self.url, {"stock": 99}).status_code, 405)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)

    def test_archiving_preserves_pending_orders_and_recorded_prices(self):
        recipient = RecipientInfo.objects.create(
            user=self.user, name="Buyer", phone_number=self.user.phone_number,
            country_code="PH", address_line1="Test address", city="Manila",
        )
        order = Order.objects.create(user=self.user, recipient_info=recipient)
        item = OrderItem.objects.create(
            order=order, product=self.product, quantity=2, price_at_purchase="5000.00",
        )
        self.product.status = Product.Status.ARCHIVED
        self.product.archived_at = timezone.now()
        self.product.save()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        item.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(item.product_id, self.product.pk)
        self.assertEqual(item.price_at_purchase, Decimal("5000.00"))
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.get_total(), Decimal("10000.00"))
