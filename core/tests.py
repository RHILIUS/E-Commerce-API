from django.test import SimpleTestCase

from core.management.commands.seed import validate_seed_data
from core.seed_data import PRODUCT_DATA


class SeedDataTests(SimpleTestCase):
    def test_seed_data_has_valid_products_and_images(self):
        product_images = validate_seed_data()

        self.assertEqual(len(PRODUCT_DATA), 24)
        self.assertEqual(len(product_images), 72)
        self.assertEqual(
            sum(image["is_default"] for image in product_images),
            24,
        )
