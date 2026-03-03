from django.urls import path
from .views import (
    LinkListView,
    LinkCreateView,
    LinkUpdateView,
    LinkDeleteView,
    CheckSlugView
)

app_name = "links_desk"

urlpatterns = [
    path("", LinkListView.as_view(), name="list"),
    path("create/", LinkCreateView.as_view(), name="create"),
    path("<uuid:pk>/edit/", LinkUpdateView.as_view(), name="edit"),
    path("<uuid:pk>/delete/", LinkDeleteView.as_view(), name="delete"),
    path("api/check-slug/", CheckSlugView.as_view(), name="check_slug"),
]
