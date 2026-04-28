from django.db import models
from django.contrib.auth.models import User
from common.models import TimeStampedModel

# --------------------------------
# Add products to cart feature
# --------------------------------
class Cart(TimeStampedModel):
  user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='cart')

class Product(TimeStampedModel):
  name = models.CharField(max_length=100)
  description = models.TextField(null=True, blank=True)
  stock = models.PositiveIntegerField(default=0)
  price = models.DecimalField(max_digits=12, decimal_places=2)

  def __str__(self):
    return self.name

class CartItem(TimeStampedModel):
  cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
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

  user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orders')
  status = models.CharField(
    max_length=20,
    choices = STATUS_CHOICES,
    default =  'pending'
  )

  def get_total(self):
        return sum(item.get_subtotal() for item in self.items.all())


class OrderItem(TimeStampedModel):
  product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
  order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
  price_at_purchase = models.DecimalField(max_digits=12, decimal_places=2)
  quantity = models.PositiveIntegerField(default=1)

  def get_subtotal(self):
        return self.price_at_purchase * self.quantity