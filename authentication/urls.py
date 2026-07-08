from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from authentication import views

urlpatterns = [
    path('login/',      views.LoginView.as_view(),      name='login'),
    path('refresh/',    TokenRefreshView.as_view(),     name='token_refresh'),
    path('verify/',     TokenVerifyView.as_view(),      name='token_verify'),
    path('logout/',     views.LogoutView.as_view(),     name='logout'),
    path('register/',   views.RegisterView.as_view(),   name='register')
]
