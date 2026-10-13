from django.urls import path

from .views import ProfileView, RecipientListCreateView, RecipientUpdateView

urlpatterns = [
    path("profile/", ProfileView.as_view(), name="profile"),
    path("recipients/<int:pk>/", RecipientUpdateView.as_view(), name="recipient-update"),
    path("recipients/", RecipientListCreateView.as_view(), name="recipient-list-create"),
]
