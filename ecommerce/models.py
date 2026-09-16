from django.contrib.auth import get_user_model
from django.db import models

from core.common.models import TimeStampedModel
from store.models import Store

User = get_user_model()


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

    def __str__(self):
        return self.name


class ProductImage(TimeStampedModel):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='images')
    image_url = models.URLField()
    is_default = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    alt_text = models.CharField(max_length=255, blank=True)
    crop = models.JSONField(default=dict, blank=True)


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
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('shipped', 'Shipped'),
        ('cancelled', 'Cancelled')
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='orders')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )

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
