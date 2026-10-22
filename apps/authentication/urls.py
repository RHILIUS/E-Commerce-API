from django.urls import path

from apps.authentication import views
from apps.authentication.views import password_reset

urlpatterns = [

    # FORGOT PASSWORD
    path("forgot-password/confirm/", password_reset.ResetConfirmView.as_view(), name="password_reset_confirm"),
    path("forgot-password/verify/", password_reset.ResetVerifyView.as_view(), name="password_reset_verify"),
    path("forgot-password/resend/", password_reset.ResetResendView.as_view(), name="password_reset_resend"),
    path("forgot-password/request/", password_reset.ResetRequestView.as_view(), name="password_reset_request"),
    path("forgot-password/", password_reset.ResetBootstrapView.as_view(), name="password_reset_bootstrap"),
    
    path('login/',      views.LoginView.as_view(),      name='login'),
    path('refresh/',    views.RefreshView.as_view(),    name='token_refresh'),
    path('verify/',     views.VerifyAccessTokenView.as_view(), name='token_verify'),
    path('logout/',     views.LogoutView.as_view(),     name='logout'),
    path('send-otp/',   views.SendOTPView.as_view(),    name='send_otp'),
    path('verify-otp/', views.VerifyOTPView.as_view(),  name='verify_otp'),
    path('register/',   views.RegisterView.as_view(),   name='register'),
]
