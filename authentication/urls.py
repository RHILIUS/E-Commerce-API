from django.urls import path
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from authentication.views import LogoutView, RegisterView

urlpatterns = [
    path('login/',   TokenObtainPairView.as_view(),  name='token_obtain_pair'),
    path('refresh/', TokenRefreshView.as_view(),     name='token_refresh'),
    path('verify/',  TokenVerifyView.as_view(),      name='token_verify'),
    path('logout/',  LogoutView.as_view(),           name='logout'),
    path('register/', RegisterView.as_view(), name='register')
]
