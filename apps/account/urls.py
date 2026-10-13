from django.urls import path

from .photo_views import ProfilePhotoView
from .views import ProfileView, RecipientListCreateView, RecipientUpdateView

urlpatterns = [
    path("profile/photo/", ProfilePhotoView.as_view(), name="profile-photo"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("recipients/<int:pk>/", RecipientUpdateView.as_view(), name="recipient-update"),
    path("recipients/", RecipientListCreateView.as_view(), name="recipient-list-create"),
]
