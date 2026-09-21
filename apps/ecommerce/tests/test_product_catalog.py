from django.utils import timezone
from rest_framework.test import APITestCase

from apps.ecommerce.models import Product, ProductImage
from apps.store.models import Store


class ProductCatalogTests(APITestCase):
    def setUp(self):
        self.store = Store.objects.create(
            name="Catalog store", country_code="PH")

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
        Product.objects.filter(pk__in=[first.pk, second.pk]).update(
            created_at=timezone.now())
        with self.assertNumQueries(3):
            response = self.client.get("/api/shop/products/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)
        self.assertEqual(response.json()["results"], [
            {
                "id": product.pk, "name": product.name,
                "short_description": "Card description", "price": "123.45",
                "sold_count": 7, "default_image": None,
            }
            for product in (second, first)
        ])

    def test_default_image_is_selected_and_serialized_with_storage_url(self):
        product = self.product("With image")
        ProductImage.objects.create(
            product=product, image="products/other.jpg", order=0)
        chosen = ProductImage.objects.create(
            product=product, image="products/default.jpg", is_default=True,
            order=1, alt_text="Product photo", crop={"x": 0.5},
        )
        ProductImage.objects.create(
            product=product, image="products/later.jpg", is_default=True, order=2,
        )
        response = self.client.get("/api/shop/products/")
        expected_url = response.wsgi_request.build_absolute_uri(
            chosen.image.url)
        self.assertEqual(response.json()["results"][0]["default_image"], {
            "image": expected_url, "alt_text": "Product photo", "crop": {"x": 0.5},
        })

    def test_empty_catalog_returns_empty_array(self):
        response = self.client.get("/api/shop/products/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "count": 0, "next": None, "previous": None, "results": [],
        })

    def test_pages_are_bounded_and_search_filters_before_pagination(self):
        products = [self.product(f"Laptop {index}") for index in range(10)]
        self.product("Unrelated")
        url = "/api/shop/products/"
        first = self.client.get(url, {"q": "  LAPTOP  "}).json()
        self.assertEqual(first["count"], 10)
        self.assertEqual([p["id"] for p in first["results"]], [
            p.pk for p in reversed(products[2:])
        ])
        self.assertIsNone(first["previous"])
        self.assertIn("page=2", first["next"])
        self.assertIn("q=", first["next"])
        second = self.client.get(url, {"q": "LAPTOP", "page": 2}).json()
        self.assertEqual([p["id"] for p in second["results"]], [
            products[1].pk, products[0].pk,
        ])
        self.assertIsNone(second["next"])
        self.assertIsNotNone(second["previous"])
        self.assertEqual(self.client.get(
            url, {"q": "card description"}).json()["count"], 11)

    def test_invalid_pages_return_not_found(self):
        self.product("Laptop")
        for page in ("invalid", "0", "-1", "2"):
            with self.subTest(page=page):
                response = self.client.get(
                    "/api/shop/products/", {"page": page})
                self.assertEqual(response.status_code, 404)
                self.assertIn("detail", response.json())

    def test_invalid_bearer_token_is_rejected(self):
        response = self.client.get(
            "/api/shop/products/", HTTP_AUTHORIZATION="Bearer invalid",
        )
        self.assertEqual(response.status_code, 401)
