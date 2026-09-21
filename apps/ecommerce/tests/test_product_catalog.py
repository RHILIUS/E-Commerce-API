from django.utils import timezone
from rest_framework.test import APITestCase

from apps.ecommerce.models import Product, ProductImage
from apps.store.models import Store


class ProductCatalogTests(APITestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Catalog store", country_code="PH")

    def product(self, name, status=Product.Status.ACTIVE):
        """Create a catalog candidate without uploading files."""
        return Product.objects.create(
            store=self.store, name=name, short_description="Card description",
            price="123.45", sold_count=7, status=status,
        )

    def test_public_catalog_filters_and_orders_products_with_bounded_queries(self):
        first = self.product("First")
        second = self.product("Second")
        self.product("Draft", Product.Status.DRAFT)
        self.product("Archived", Product.Status.ARCHIVED)
        Product.objects.filter(pk__in=[first.pk, second.pk]).update(created_at=timezone.now())
        with self.assertNumQueries(2):
            response = self.client.get("/api/shop/products/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [
            {
                "id": product.pk, "name": product.name,
                "short_description": "Card description", "price": "123.45",
                "sold_count": 7, "default_image": None,
            }
            for product in (second, first)
        ])

    def test_default_image_is_selected_and_serialized_with_storage_url(self):
        product = self.product("With image")
        ProductImage.objects.create(product=product, image="products/other.jpg", order=0)
        chosen = ProductImage.objects.create(
            product=product, image="products/default.jpg", is_default=True,
            order=1, alt_text="Product photo", crop={"x": 0.5},
        )
        ProductImage.objects.create(
            product=product, image="products/later.jpg", is_default=True, order=2,
        )
        response = self.client.get("/api/shop/products/")
        expected_url = response.wsgi_request.build_absolute_uri(chosen.image.url)
        self.assertEqual(response.json()[0]["default_image"], {
            "image": expected_url, "alt_text": "Product photo", "crop": {"x": 0.5},
        })

    def test_empty_catalog_returns_empty_array(self):
        response = self.client.get("/api/shop/products/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_invalid_bearer_token_is_rejected(self):
        response = self.client.get(
            "/api/shop/products/", HTTP_AUTHORIZATION="Bearer invalid",
        )
        self.assertEqual(response.status_code, 401)
