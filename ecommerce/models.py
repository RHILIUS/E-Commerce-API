from django.db import models
from django.contrib.auth.models import User

# --------------------------------
# Add products to cart feature
# --------------------------------
class Cart(models.Model):
  user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="carts")

class Product(models.Model):
  name = models.CharField(max_length=100)
  description = models.TextField(null=True, blank=True)
  stock = models.IntegerField(default=0)
  price = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

  def __str__(self):
    return self.name

class CartItem(models.Model):
  cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items"
  )
  product = models.ForeignKey(Product, on_delete=models.CASCADE)
  quantity = models.IntegerField(default=1)
  purchase_price = models.DecimalField(max_digits=12, decimal_places=2)