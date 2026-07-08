from django.urls import path

from ecommerce import views

urlpatterns = [
    path('test/', views.Test.as_view())
]
