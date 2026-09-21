from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models

from apps.account.models import RecipientInfo
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
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='cart')


class CartItem(TimeStampedModel):
    cart = models.ForeignKey(
        Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ('cart', 'product')

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

    def clean(self):
        super().clean()
        if (
            self.user_id
            and self.recipient_info_id
            and self.recipient_info.user_id != self.user_id
        ):
            raise ValidationError({
                "recipient_info": "The recipient must belong to the order user."
            })

    def get_total(self):
        return sum(item.get_subtotal() for item in self.items.all())


class OrderItem(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name='items')
    price_at_purchase = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)

    def get_subtotal(self):
        return self.price_at_purchase * self.quantity
