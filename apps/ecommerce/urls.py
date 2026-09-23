from django.urls import path

from apps.ecommerce import views
from apps.ecommerce.cart_views import CartListView, CartItemsView, CartItemView

urlpatterns = [
    path('carts/', CartListView.as_view(), name='cart-list'),
    path('carts/items/', CartItemsView.as_view(), name='cart-items'),
    path('carts/items/<int:pk>/', CartItemView.as_view(), name='cart-item'),
    path('test/', views.Test.as_view()),
    path('products/', views.ProductCatalogView.as_view()),
    path('products/<int:pk>/', views.ProductDetailView.as_view(),
         name='product-detail'),
]
