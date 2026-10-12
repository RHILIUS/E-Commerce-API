from django.urls import path

from .views import StoreDetailView, StoreHomeView, StoreProductsView

urlpatterns = [
    path('stores/<int:pk>/', StoreDetailView.as_view(), name='store-detail'),
    path('stores/<int:pk>/home/', StoreHomeView.as_view(), name='store-home'),
    path('stores/<int:pk>/products/', StoreProductsView.as_view(), name='store-products'),
]
