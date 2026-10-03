from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.account.models import RecipientInfo
from apps.checkout.services.snapshots import validate_recipient_snapshot
from apps.store.models import Store
from core.common.images import compress_uploaded_image
from core.common.models import TimeStampedModel

User = get_user_model()


# The taxonomy category, like Laptops -  Uses Shopify Product Taxonomy
class Category(models.Model):
    taxonomy_id = models.CharField(
        max_length=100,
        unique=True,
    )

    name = models.CharField(max_length=150)

    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


# A specification like Color, Storage capacity, Screen size
class Attribute(models.Model):
    taxonomy_id = models.CharField(
        max_length=100,
    )
    # Shopify contexts can share an ID, but have distinct handles.
    # Empty handles preserve existing local records until explicitly mapped.
    handle = models.CharField(max_length=100, blank=True, default="")
    name = models.CharField(max_length=150)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["taxonomy_id", "handle"],
                name="unique_taxonomy_attribute_context",
            )
        ]

    def __str__(self):
        return self.name


# Predefined values of an attribute (e.g. Black, 120 gb)
class AttributeValue(models.Model):
    taxonomy_id = models.CharField(
        max_length=100,
    )

    attribute = models.ForeignKey(
        Attribute,
        on_delete=models.CASCADE,
        related_name="values",
    )

    name = models.CharField(max_length=150)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["attribute", "taxonomy_id"],
                name="unique_attribute_taxonomy_value",
            )
        ]

    def __str__(self):
        return self.name

    def clean(self):
        """Keep values used by product specifications under their original attribute."""
        super().clean()
        if self.pk and self.products.exclude(attribute_id=self.attribute_id).exists():
            raise ValidationError({
                "attribute": "This value is used by specifications with another attribute."
            })

    def save(self, *args, **kwargs):
        """Validate value ownership on ordinary writes, including objects.create()."""
        self.full_clean()
        return super().save(*args, **kwargs)


# Defines which attributes are available for a category.
class CategoryAttribute(models.Model):
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="category_attributes",
    )

    attribute = models.ForeignKey(
        Attribute,
        on_delete=models.CASCADE,
        related_name="category_attributes",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["category", "attribute"],
                name="unique_category_attribute",
            )
        ]

    def clean(self):
        """Prevent changing an association that existing specifications require."""
        super().clean()
        previous = type(self).objects.filter(
            pk=self.pk).first() if self.pk else None
        if previous and (
            previous.category_id != self.category_id
            or previous.attribute_id != self.attribute_id
        ):
            previous.validate_removal()

    def validate_removal(self):
        """Reject removal while products in this category use the attribute."""
        if ProductAttributeValue.objects.filter(
            product__category_id=self.category_id, attribute_id=self.attribute_id
        ).exists():
            raise ValidationError(
                "This category attribute is used by existing product specifications."
            )

    def save(self, *args, **kwargs):
        """Validate association changes on ordinary model writes."""
        self.full_clean()
        return super().save(*args, **kwargs)


class Product(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=100)
    short_description = models.CharField(max_length=50, blank=True, default="")
    description = models.TextField(null=True, blank=True)
    stock = models.PositiveIntegerField(default=0)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    # Used only for UI and rankings, don't use for accounting and financial calculations
    sold_count = models.PositiveIntegerField(default=0)
    store = models.ForeignKey(
        Store, related_name="products", on_delete=models.PROTECT)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="products", null=True, blank=True)

    def __str__(self):
        return self.name

    def clean(self):
        """Ensure existing specifications remain valid for the selected category."""
        super().clean()
        if not self.pk:
            return
        if self.cartitem_set.exclude(cart__store_id=self.store_id).exists():
            raise ValidationError(
                {"store": "A product saved in carts cannot move to another store."})
        if self.orderitem_set.exclude(order__store_id=self.store_id).exists():
            raise ValidationError({"store": "A purchased product cannot move to another store."})
        allowed_attributes = CategoryAttribute.objects.filter(
            category_id=self.category_id
        ).values("attribute_id")
        if self.attribute_values.exclude(attribute_id__in=allowed_attributes).exists():
            raise ValidationError({
                "category": "This category does not support all existing specifications."
            })

    def save(self, *args, **kwargs):
        """Check category compatibility when saving a product."""
        self.clean()
        return super().save(*args, **kwargs)


class ProductImage(TimeStampedModel):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to="products/")
    is_default = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    alt_text = models.CharField(max_length=255, blank=True)
    crop = models.JSONField(default=dict, blank=True)

    def save(self, *args, **kwargs):
        if self.image and not self.image._committed:
            self.image = compress_uploaded_image(self.image)
        return super().save(*args, **kwargs)


# Defines the attribute/s of a product
class ProductAttributeValue(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="attribute_values",
    )

    attribute = models.ForeignKey(
        Attribute,
        on_delete=models.PROTECT,
        related_name="product_values",
    )

    value = models.ForeignKey(
        AttributeValue,
        on_delete=models.PROTECT,
        related_name="products",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "attribute"],
                name="unique_product_attribute",
            )
        ]

    def clean(self):
        """Validate the value's attribute and its availability for the category."""
        super().clean()
        errors = {}

        if self.attribute_id and self.value_id:
            value_attribute_id = AttributeValue.objects.filter(
                pk=self.value_id
            ).values_list("attribute_id", flat=True).first()
            if value_attribute_id is not None and value_attribute_id != self.attribute_id:
                errors["value"] = (
                    "This value does not belong to the selected attribute."
                )

        if self.product_id and self.attribute_id:
            product = Product.objects.filter(
                pk=self.product_id).only("category_id").first()
            if product is not None and not CategoryAttribute.objects.filter(
                category_id=product.category_id, attribute_id=self.attribute_id
            ).exists():
                errors["attribute"] = (
                    "This attribute is not available for the product's category."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Validate specifications before ordinary saves and objects.create()."""
        self.full_clean()
        return super().save(*args, **kwargs)


# --------------------------------
# Add to cart
# --------------------------------


class Cart(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='carts')
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='carts')

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=('user', 'store'), name='unique_user_store_cart')]

    def clean(self):
        """Prevent moving populated carts to a different store."""
        super().clean()
        if self.pk and self.items.exclude(product__store_id=self.store_id).exists():
            raise ValidationError(
                {"store": "Cart items must belong to the cart's store."})

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class CartItem(TimeStampedModel):
    cart = models.ForeignKey(
        Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(
        default=1, validators=[MinValueValidator(1)])

    class Meta:
        unique_together = ('cart', 'product')
        constraints = [models.CheckConstraint(condition=models.Q(
            quantity__gte=1), name='cart_item_quantity_positive')]

    def clean(self):
        """Keep each item inside its product's store cart."""
        super().clean()
        if self.cart_id and self.product_id and self.cart.store_id != self.product.store_id:
            raise ValidationError(
                {"product": "This product belongs to a different store."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

# --------------------------------
# Order a Product
# --------------------------------


class Order(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        PACKED = "packed", "Packed"
        SHIPMENT_ARRANGED = "shipment_arranged", "Shipment Arranged"
        SHIPPED = "shipped", "Shipped"
        CANCELLED = "cancelled", "Cancelled"
        DELIVERED = "delivered", "Delivered"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    recipient_info = models.ForeignKey(
        RecipientInfo,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    concluded_at = models.DateTimeField(null=True, blank=True)
    checkout = models.ForeignKey(
        "checkout.Checkout",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    store_name_at_purchase = models.CharField(max_length=100)

    payment_status = models.CharField(
        max_length=10,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
    )
    paid_at = models.DateTimeField(null=True, blank=True)

    recipient_snapshot = models.JSONField(validators=[validate_recipient_snapshot])

    source_currency = models.CharField(max_length=3)
    exchange_rate = models.DecimalField(
        max_digits=28,
        decimal_places=12,
    )
    exchange_rate_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["checkout", "store"],
                name="unique_checkout_store_order",
            ),
            models.CheckConstraint(
                condition=models.Q(exchange_rate__gt=0),
                name="order_exchange_rate_positive",
            ),
        ]

    def clean(self):
        """Keep the checkout, order and recipient owned by the same customer."""
        super().clean()
        errors = {}
        recipient_user = RecipientInfo.objects.filter(pk=self.recipient_info_id).values_list(
            "user_id", flat=True).first()
        checkout_model = self._meta.get_field("checkout").remote_field.model
        checkout_user = checkout_model.objects.filter(pk=self.checkout_id).values_list(
            "user_id", flat=True).first()
        if self.user_id and recipient_user is not None and recipient_user != self.user_id:
            errors["recipient_info"] = "The recipient must belong to the order user."
        if self.user_id and checkout_user is not None and checkout_user != self.user_id:
            errors["checkout"] = "The checkout must belong to the order user."
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values("user_id", "store_id", "checkout_id").first()
            if previous and any(previous[field] != getattr(self, field) for field in previous):
                errors["checkout"] = "An existing order cannot move to another user, store or checkout."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Validate order ownership and purchase snapshots on ordinary writes."""
        self.full_clean()
        return super().save(*args, **kwargs)

    def get_total(self):
        return sum(item.get_subtotal() for item in self.items.all())


class OrderItem(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name='items')
    price_at_purchase = models.DecimalField(max_digits=18, decimal_places=3)
    quantity = models.PositiveIntegerField(default=1)
    product_name_at_purchase = models.CharField(max_length=255)
    original_unit_price = models.DecimalField(
        max_digits=18,
        decimal_places=3,
    )
    source_cart_item = models.ForeignKey(
        CartItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checkout_order_items",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="order_item_quantity_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(original_unit_price__gte=0),
                name="order_item_original_price_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(price_at_purchase__gte=0),
                name="order_item_purchase_price_nonnegative",
            ),
        ]

    def clean(self):
        """Check store membership, cart ownership and existing reservation consistency."""
        super().clean()
        errors = {}
        order = Order.objects.filter(pk=self.order_id).values("store_id", "user_id").first()
        product_store = Product.objects.filter(pk=self.product_id).values_list("store_id", flat=True).first()
        if order and product_store is not None and product_store != order["store_id"]:
            errors["product"] = "The product must belong to the order store."
        if self.source_cart_item_id:
            source = CartItem.objects.filter(pk=self.source_cart_item_id).values(
                "product_id", "cart__user_id").first()
            if source and (source["product_id"] != self.product_id or
                           (order and source["cart__user_id"] != order["user_id"])):
                errors["source_cart_item"] = "The cart item must match the product and customer."
        if self.pk:
            reservation = getattr(self, "stock_reservation", None)
            if reservation and (reservation.product_id != self.product_id or
                                reservation.quantity != self.quantity):
                errors["quantity"] = "An item with a reservation cannot change product or quantity."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Validate purchase relationships and required snapshots on ordinary writes."""
        self.full_clean()
        return super().save(*args, **kwargs)

    def get_subtotal(self):
        return self.price_at_purchase * self.quantity
