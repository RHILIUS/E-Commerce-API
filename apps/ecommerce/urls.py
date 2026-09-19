from django.urls import path

from apps.ecommerce import views

urlpatterns = [
    path('test/', views.Test.as_view()),
    path('products/', views.ProductCatalogView.as_view())
]
