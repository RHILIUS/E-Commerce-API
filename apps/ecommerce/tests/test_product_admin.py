from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.account.models import RecipientInfo
from apps.ecommerce.models import Order, Product
from apps.store.models import Store
from core.test_support import create_purchase


class ProductAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="product-admin", email="product-admin@example.com", password="test-only",
            phone_number="+639111111111", full_name="Product admin",
        )
        store = Store.objects.create(name="Admin store", country_code="PH")
        cls.product = Product.objects.create(store=store, name="Sold product", price="100", status="active")
        cls.unsold = Product.objects.create(store=store, name="Unsold product", price="200", status="active")
        recipient = RecipientInfo.objects.create(
            user=cls.user, name="Buyer", phone_number=cls.user.phone_number,
            country_code="PH", address_line1="Street", city="Manila",
        )
        create_purchase(user=cls.user, recipient=recipient, product=cls.product,
                        quantity=3, status=Order.Status.DELIVERED)
        create_purchase(user=cls.user, recipient=recipient, product=cls.product, quantity=100)

    def setUp(self):
        self.client.force_login(self.user)
        self.model_admin = admin.site._registry[Product]

    def test_product_list_and_form_show_readonly_qualifying_sales(self):
        url = reverse("admin:ecommerce_product_changelist")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Units ordered")
        results = response.context["cl"].result_list
        self.assertEqual({p.pk: p.ordered_total for p in results}, {self.product.pk: 103, self.unsold.pk: 0})
        column = response.context["cl"].list_display.index("ordered_units")
        ordered = self.client.get(url, {"o": str(column)})
        self.assertEqual([p.pk for p in ordered.context["cl"].result_list], [self.unsold.pk, self.product.pk])

        response = self.client.get(reverse("admin:ecommerce_product_change", args=[self.product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<div class="readonly">103</div>', html=True)
        self.assertNotIn("ordered_units", response.context["adminform"].form.fields)
        self.assertNotIn("ordered_count", response.context["adminform"].form.fields)
        self.assertContains(response, "Units ordered")
        response = self.client.get(reverse("admin:ecommerce_product_add"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("ordered_units", response.context["adminform"].form.fields)

    def test_sales_display_uses_one_query_for_multiple_products(self):
        request = self.client.get(reverse("admin:index")).wsgi_request
        with self.assertNumQueries(1):
            products = list(self.model_admin.get_queryset(request))
            totals = {p.pk: self.model_admin.ordered_units(p) for p in products}
        self.assertEqual(totals, {self.product.pk: 103, self.unsold.pk: 0})
        self.assertIsNone(self.model_admin.ordered_units(Product()))
