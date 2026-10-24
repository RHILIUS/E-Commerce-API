from django.urls import path

from .views.catalog import ProductCatalogView, ProductDetailView
from .views.currencies import CurrencyOptionsView
from .views.carts import CartListView, CartItemsView, CartItemView
from .views.development import Test
from .views.orders import UserOrderListView, UserOrderCancelView, UserOrderHistoryView

urlpatterns = [
    path('currencies/', CurrencyOptionsView.as_view(), name='currency-options'),
    path('orders/<int:pk>/history/', UserOrderHistoryView.as_view(), name='user-order-history'),
    path('orders/', UserOrderListView.as_view(), name='user-orders'),
    path('orders/<int:pk>/cancel/', UserOrderCancelView.as_view(), name='user-order-cancel'),
    path('carts/', CartListView.as_view(), name='cart-list'),
    path('carts/items/', CartItemsView.as_view(), name='cart-items'),
    path('carts/items/<int:pk>/', CartItemView.as_view(), name='cart-item'),
    path('test/', Test.as_view()),
    path('products/', ProductCatalogView.as_view()),
    path('products/<int:pk>/', ProductDetailView.as_view(),
         name='product-detail'),
]
