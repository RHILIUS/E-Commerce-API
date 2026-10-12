import re
import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.account.models import RecipientInfo
from apps.checkout.models import Checkout
from apps.checkout.services.snapshots import recipient_snapshot
from apps.ecommerce.models import (
    AttributeValue,
    Category,
    CategoryAttribute,
    Product,
    ProductAttributeValue,
    ProductImage,
    Order,
    OrderItem,
)
from apps.store.models import Store, StoreAddress, StoreMember
from core.seed_data import CUSTOMERS_DATA, STORES_DATA


def product_data():
    """Flatten store catalogs for shared image and taxonomy validation."""
    return [product for store in STORES_DATA for product in store["products"]]

PRODUCT_IMAGE_ROOT = (
    Path(settings.BASE_DIR) / "static" / "seed_images" / "products" / "image"
)
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
NUMBERED_IMAGE_PATTERN = re.compile(r"_(\d+)$")


def source_path(value):
    """Resolve a seed image path and ensure that the source file exists."""
    path = Path(value)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    if not path.is_file():
        raise CommandError(f"Seed image does not exist: {path}")
    return path


def discover_product_images():
    """Build image records from product folders and their filename conventions."""
    if not PRODUCT_IMAGE_ROOT.is_dir():
        raise CommandError(
            f"Product image directory does not exist: {PRODUCT_IMAGE_ROOT}"
        )

    product_names = [product["name"] for product in product_data()]
    folders = {
        folder.name: folder
        for folder in PRODUCT_IMAGE_ROOT.iterdir()
        if folder.is_dir()
    }
    missing = set(product_names) - folders.keys()
    unexpected = folders.keys() - set(product_names)
    if missing or unexpected:
        raise CommandError(
            "Product image folders do not match the seed products. "
            f"Missing: {sorted(missing)}; unexpected: {sorted(unexpected)}"
        )

    image_records = []
    for product_name in product_names:
        files = sorted(
            path
            for path in folders[product_name].iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
        )
        default_files = [
            path for path in files if path.stem.endswith("_default")]
        if len(default_files) != 1:
            raise CommandError(
                f"{product_name} must contain exactly one *_default image."
            )

        numbered_files = {}
        for path in files:
            if path in default_files:
                continue
            match = NUMBERED_IMAGE_PATTERN.search(path.stem)
            if match is None:
                raise CommandError(
                    f"Image must end in _default or _<order>: {path}"
                )
            order = int(match.group(1))
            if order in numbered_files:
                raise CommandError(
                    f"{product_name} contains duplicate image order {order}."
                )
            numbered_files[order] = path

        expected_orders = list(range(1, len(numbered_files) + 1))
        if sorted(numbered_files) != expected_orders:
            raise CommandError(
                f"{product_name} numbered images must start at 1 without gaps."
            )

        image_records.append(
            {
                "product_name": product_name,
                "is_default": True,
                "order": 0,
                "alt_text": f"{product_name} default product image",
                "source": default_files[0],
            }
        )
        image_records.extend(
            {
                "product_name": product_name,
                "is_default": False,
                "order": order,
                "alt_text": f"{product_name} product image {order}",
                "source": numbered_files[order],
            }
            for order in expected_orders
        )

    return image_records


def validate_seed_data():
    """Validate model limits, choices, source assets, and image conventions."""
    names = [product["name"] for product in product_data()]
    if len(names) != len(set(names)):
        raise CommandError("Product seed names must be unique.")

    name_limit = Product._meta.get_field("name").max_length
    short_description_limit = Product._meta.get_field(
        "short_description"
    ).max_length
    for product in product_data():
        if len(product["name"]) > name_limit:
            raise CommandError(f"Product name is too long: {product['name']}")
        if len(product["short_description"]) > short_description_limit:
            raise CommandError(
                f"Product short description is too long: {product['name']}"
            )
        if product["status"] not in Product.Status.values:
            raise CommandError(f"Invalid product status: {product['status']}")
        try:
            int(product["stock"])
            int(product["sold_count"])
            Decimal(product["price"])
        except (ValueError, TypeError) as error:
            raise CommandError(
                f"Invalid numeric product value: {product['name']}"
            ) from error

    users = list(CUSTOMERS_DATA)
    for store in STORES_DATA:
        source_path(store["profile_image"])
        source_path(store["banner_image"])
        users.extend(member["user"] for member in store["members"])
    product_stores = {product["name"]: store["name"] for store in STORES_DATA for product in store["products"]}
    order_keys = set()
    for user in users:
        source_path(user["profile_image"])
        if sum(recipient["is_default"] for recipient in user["recipient_infos"]) != 1:
            raise CommandError("Each seed user must have exactly one default recipient.")
    for customer in CUSTOMERS_DATA:
        for order in customer["orders"]:
            key = order.get("key")
            if not isinstance(key, str) or not key.strip() or key in order_keys:
                raise CommandError("Seed order keys must be unique non-empty strings.")
            order_keys.add(key)
            if order["status"] not in Order.Status.values:
                raise CommandError("Invalid seed order status.")
            if order.get("payment_status") not in (Order.PaymentStatus.UNPAID, Order.PaymentStatus.PAID):
                raise CommandError("Seed COD orders require an explicit unpaid or paid payment status.")
            if not order["order_items"]:
                raise CommandError("Seed orders must contain items.")
            item_names = set()
            for item in order["order_items"]:
                if item["product_name"] not in names or item["product_name"] in item_names:
                    raise CommandError("Order products must exist and occur only once per order.")
                item_names.add(item["product_name"])
                if type(item["quantity"]) is not int or item["quantity"] <= 0:
                    raise CommandError("Order quantities must be positive integers.")
            if len({product_stores[item["product_name"]] for item in order["order_items"]}) != 1:
                raise CommandError("Each seed order must contain products from exactly one store.")
    return discover_product_images()


def resolve_product_taxonomy():
    """Resolve and lock seed taxonomy references before any writes or uploads.

    Run inside the seed transaction. Missing or disallowed choices require an
    explicit taxonomy import, never locally invented taxonomy records.
    """
    resolved = {}
    categories, values = {}, {}
    for product in product_data():
        category_id = product["category_taxonomy_id"]
        if category_id not in categories:
            category = Category.objects.select_for_update().filter(
                taxonomy_id=category_id, is_active=True,
            ).first()
            if category is None:
                raise CommandError(
                    f"{product['name']}: missing or inactive category {category_id}. "
                    "Import taxonomy first and ensure the category is active."
                )
            categories[category_id] = category
        category = categories[category_id]
        specifications = []
        attribute_ids = set()
        for specification in product["attribute_values"]:
            key = (specification["attribute_taxonomy_id"], specification["attribute_handle"],
                   specification["value_taxonomy_id"])
            if key not in values:
                value = AttributeValue.objects.select_for_update().select_related("attribute").filter(
                    attribute__taxonomy_id=key[0], attribute__handle=key[1], taxonomy_id=key[2],
                ).first()
                if value is None:
                    raise CommandError(
                        f"{product['name']}: missing value {key[2]} for {key[1]}. "
                        "Run import_taxonomy with both JSON files first."
                    )
                values[key] = value
            value = values[key]
            if value.attribute_id in attribute_ids:
                raise CommandError(
                    f"{product['name']}: duplicate specification for {key[1]}.")
            attribute_ids.add(value.attribute_id)
            association = CategoryAttribute.objects.select_for_update().filter(
                category=category, attribute_id=value.attribute_id,
            ).first()
            if association is None:
                raise CommandError(
                    f"{product['name']}: {key[1]} is not allowed for the selected category.")
            specifications.append(value)
        if not specifications:
            raise CommandError(
                f"{product['name']}: at least one specification is required.")
        resolved[product["name"]] = (category, specifications)
    return resolved


def attach_image_if_missing(instance, field_name, path, uploaded_files):
    """Upload a source image only when the model's image field is empty."""
    if getattr(instance, field_name).name:
        return
    with path.open("rb") as image_file:
        setattr(instance, field_name, File(image_file, name=path.name))
        if instance.pk:
            instance.save(update_fields=[field_name])
        else:
            instance.save()
    saved_image = getattr(instance, field_name)
    uploaded_files.append((saved_image.storage, saved_image.name))


class Command(BaseCommand):
    help = "Create or update the initial development data."

    def handle(self, *args, **options):
        self.stdout.write("Validating seed data and image folders...")
        product_images = validate_seed_data()
        self.stdout.write(
            self.style.SUCCESS(
                f"Validation complete: {len(product_data())} products and "
                f"{len(product_images)} product images."
            )
        )
        uploaded_files = []

        try:
            with transaction.atomic():
                taxonomy = resolve_product_taxonomy()
                self._seed(product_images, uploaded_files, taxonomy)
        except Exception:
            for storage, name in reversed(uploaded_files):
                storage.delete(name)
            raise

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete: {len(CUSTOMERS_DATA)} customers, {len(STORES_DATA)} stores, "
                f"{sum(len(customer['orders']) for customer in CUSTOMERS_DATA)} orders, "
                f"{len(product_data())} products, and "
                f"{len(product_images)} product images. "
                f"New files uploaded: {len(uploaded_files)}."
            )
        )

    def _seed(self, product_images, uploaded_files, taxonomy):
        """Upsert the validated seed records in dependency order."""
        products = {}
        for data in STORES_DATA:
            store = self._seed_store(data, uploaded_files)
            self._seed_store_address(store, data["address"])
            for member in data["members"]:
                user = self._seed_user(member["user"], uploaded_files)
                for recipient in member["user"]["recipient_infos"]:
                    self._seed_recipient(user, recipient)
                self._seed_store_member(store, user, member["role"])
            products.update(self._seed_products(store, taxonomy, data["products"]))
        for data in CUSTOMERS_DATA:
            user = self._seed_user(data, uploaded_files)
            for recipient in data["recipient_infos"]:
                self._seed_recipient(user, recipient)
            self._seed_orders(user, data["orders"], products)
        self._seed_product_images(product_images, products, uploaded_files)

    def _seed_orders(self, user, orders, products):
        """Upsert explicit COD fixture purchases without payments, holds or inventory writes.

        Each fixture order gets one stable checkout in its store currency. This
        bypasses live checkout and FX intentionally; sample history is not a sale.
        """
        recipient = user.recipient_infos.get(is_default=True)
        for data in orders:
            lines = [(products[item["product_name"]], item["quantity"]) for item in data["order_items"]]
            stores = {product.store_id for product, _ in lines}
            if len(stores) != 1:
                raise CommandError("Each seed order must contain products from exactly one store.")
            store = lines[0][0].store
            total = sum((product.price * quantity for product, quantity in lines), Decimal("0"))
            key = uuid.uuid5(uuid.NAMESPACE_URL, f"ecommerce:seed:order:{data['key']}")
            checkout = Checkout.objects.select_for_update().filter(reference=key).first()
            if checkout is not None and checkout.user_id != user.pk:
                raise CommandError("Seed checkout already belongs to another user.")
            if checkout is None:
                checkout = Checkout(reference=key, user=user, idempotency_key=key)
            checkout.payment_method = Checkout.PaymentMethod.COD
            checkout.currency = store.currency
            checkout.total_amount = total
            checkout.status = Checkout.Status.COMPLETED
            checkout.completed_at = checkout.completed_at or timezone.now()
            checkout.save()
            order = Order.objects.select_for_update().filter(checkout=checkout, store=store).first()
            if order is not None and order.user_id != user.pk:
                raise CommandError("Seed order already belongs to another user.")
            if order is None:
                order = Order(user=user)
            order.checkout = checkout
            order.store = store
            order.store_name_at_purchase = store.name
            order.recipient_info = recipient
            order.recipient_snapshot = recipient_snapshot(recipient)
            order.source_currency = store.currency
            order.exchange_rate = Decimal("1")
            order.exchange_rate_date = None
            order.status = data["status"]
            order.payment_status = data["payment_status"]
            order.paid_at = (order.paid_at or checkout.completed_at) if order.payment_status == Order.PaymentStatus.PAID else None
            if order.status in (Order.Status.DELIVERED, Order.Status.CANCELLED):
                order.concluded_at = order.concluded_at or timezone.now()
            else:
                order.concluded_at = None
            order.save()
            if order.items.exclude(product_id__in=[product.pk for product, _ in lines]).exists():
                raise CommandError("Seed order contains unexpected items; resolve them before reseeding.")
            for product, quantity in lines:
                OrderItem.objects.update_or_create(
                    order=order, product=product,
                    defaults={
                        "quantity": quantity,
                        "product_name_at_purchase": product.name,
                        "original_unit_price": product.price,
                        "price_at_purchase": product.price,
                    },
                )

    def _seed_user(self, user_data, uploaded_files):
        """Create a seed user with a hashed password or refresh its profile."""
        User = get_user_model()
        user = User.objects.filter(email__iexact=user_data["email"]).first()
        if user is None:
            user = User.objects.create_user(
                username=user_data["username"],
                email=user_data["email"],
                full_name=user_data["full_name"],
                password=user_data["password"],
                phone_number=user_data["phone_number"],
                country_code=user_data["country_code"],
            )
        else:
            for field_name in (
                "username",
                "full_name",
                "phone_number",
                "country_code",
            ):
                setattr(user, field_name, user_data[field_name])
            user.save(
                update_fields=[
                    "username",
                    "full_name",
                    "phone_number",
                    "country_code",
                ]
            )
        attach_image_if_missing(
            user,
            "profile_image",
            source_path(user_data["profile_image"]),
            uploaded_files,
        )
        return user

    def _seed_recipient(self, user, recipient_data):
        """Update the default recipient; match additional addresses by their contents."""
        lookup = {} if recipient_data["is_default"] else {
            key: value for key, value in recipient_data.items() if key != "is_default"
        }
        return RecipientInfo.objects.update_or_create(
            user=user,
            is_default=recipient_data["is_default"],
            **lookup,
            defaults={
                key: value
                for key, value in recipient_data.items()
                if key != "is_default"
            },
        )

    def _seed_store(self, store_data, uploaded_files):
        """Upsert the store and upload its images when they are missing."""
        store, _ = Store.objects.update_or_create(
            name=store_data["name"],
            defaults={
                "description": store_data["description"],
                "public_email": store_data["public_email"],
                "public_phone": store_data["public_phone"],
                "country_code": store_data["country_code"],
            },
        )
        for field_name in ("profile_image", "banner_image"):
            attach_image_if_missing(
                store,
                field_name,
                source_path(store_data[field_name]),
                uploaded_files,
            )
        return store

    def _seed_store_address(self, store, address_data):
        """Upsert the store's one-to-one address."""
        StoreAddress.objects.update_or_create(
            store=store,
            defaults=address_data,
        )

    def _seed_store_member(self, store, user, role):
        """Ensure that the seed owner has the configured store role."""
        StoreMember.objects.update_or_create(
            store=store,
            user=user,
            defaults={"role": role},
        )

    def _seed_products(self, store, taxonomy, catalog):
        """Upsert product categories and seeded specifications, retaining other specs."""
        products = {}
        for data in catalog:
            category, specifications = taxonomy[data["name"]]
            product, _ = Product.objects.update_or_create(
                store=store,
                name=data["name"],
                defaults={
                    "category": category,
                    "short_description": data["short_description"],
                    "description": data["description"],
                    "stock": int(data["stock"]),
                    "price": Decimal(data["price"]),
                    "sold_count": int(data["sold_count"]),
                    "status": data["status"],
                },
            )
            for value in specifications:
                ProductAttributeValue.objects.update_or_create(
                    product=product, attribute_id=value.attribute_id,
                    defaults={"value": value},
                )
            products[product.name] = product
        return products

    def _seed_product_images(self, rows, products, uploaded_files):
        """Upsert image metadata by product and order without replacing uploads."""
        rows_by_product = {
            product_name: [
                row for row in rows if row["product_name"] == product_name
            ]
            for product_name in products
        }
        product_count = len(rows_by_product)

        for product_number, (product_name, image_rows) in enumerate(
            rows_by_product.items(),
            start=1,
        ):
            self.stdout.write(
                f"  [{product_number}/{product_count}] {product_name}: "
                f"{len(image_rows)} images"
            )
            product = products[product_name]
            for data in image_rows:
                order = data["order"]
                matches = ProductImage.objects.filter(
                    product=product, order=order)
                if matches.count() > 1:
                    raise CommandError(
                        f"Multiple images already use order {order} for "
                        f"{product.name}."
                    )
                image = matches.first()
                if image is None:
                    image = ProductImage(product=product, order=order)

                image.is_default = data["is_default"]
                image.alt_text = data["alt_text"]
                if image.pk:
                    image.save(update_fields=["is_default", "alt_text"])
                attach_image_if_missing(
                    image,
                    "image",
                    data["source"],
                    uploaded_files,
                )
