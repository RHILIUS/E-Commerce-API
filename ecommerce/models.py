from django.db import models
from django.contrib.auth.models import User

# --------------------------------
# Add products to cart feature
# --------------------------------
class Cart(models.Model):
  user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="carts")