from django.urls import path

from .views.photo import ProfilePhotoView
from .views import ProfileView, RecipientListCreateView, RecipientUpdateView

from .views.password import PasswordChangeRequestView, PasswordChangeResendView, PasswordChangeConfirmView

urlpatterns = [
    path("change-password/request/", PasswordChangeRequestView.as_view(), name="password-change-request"),
    path("change-password/resend/", PasswordChangeResendView.as_view(), name="password-change-resend"),
    path("change-password/confirm/", PasswordChangeConfirmView.as_view(), name="password-change-confirm"),
    path("profile/photo/", ProfilePhotoView.as_view(), name="profile-photo"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("recipients/<int:pk>/", RecipientUpdateView.as_view(), name="recipient-update"),
    path("recipients/", RecipientListCreateView.as_view(), name="recipient-list-create"),
]
