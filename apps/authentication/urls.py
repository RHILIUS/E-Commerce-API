from django.urls import path

from apps.authentication import views

urlpatterns = [
    path('login/',      views.LoginView.as_view(),      name='login'),
    path('refresh/',    views.RefreshView.as_view(),    name='token_refresh'),
    path('verify/',     views.VerifyAccessTokenView.as_view(), name='token_verify'),
    path('logout/',     views.LogoutView.as_view(),     name='logout'),
    path('send-otp/',   views.SendOTPView.as_view(),    name='send_otp'),
    path('verify-otp/', views.VerifyOTPView.as_view(),  name='verify_otp'),
    path('register/',   views.RegisterView.as_view(),   name='register'),
]
