from django.urls import path

from .views import RecipientListCreateView, RecipientUpdateView

urlpatterns = [
    path("recipients/<int:pk>/", RecipientUpdateView.as_view(), name="recipient-update"),
    path("recipients/", RecipientListCreateView.as_view(), name="recipient-list-create"),
]
