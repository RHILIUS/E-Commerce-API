from django.urls import path

from .views import RecipientListCreateView

urlpatterns = [
    path("recipients/", RecipientListCreateView.as_view(), name="recipient-list-create"),
]
