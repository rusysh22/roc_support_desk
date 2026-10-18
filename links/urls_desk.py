from django.urls import path
from .views import (
    LinkListView,
    LinkCreateView,
    LinkUpdateView,
    LinkDeleteView,
    CheckSlugView,
    link_bulk_archive,
    link_bulk_unarchive,
    link_bulk_delete,
    link_bulk_duplicate,
    link_bulk_export,
)

app_name = "links_desk"

urlpatterns = [
    path("", LinkListView.as_view(), name="list"),
    path("create/", LinkCreateView.as_view(), name="create"),
    path("<uuid:pk>/edit/", LinkUpdateView.as_view(), name="edit"),
    path("<uuid:pk>/delete/", LinkDeleteView.as_view(), name="delete"),
    path("api/check-slug/", CheckSlugView.as_view(), name="check_slug"),
    path("bulk/archive/", link_bulk_archive, name="bulk_archive"),
    path("bulk/unarchive/", link_bulk_unarchive, name="bulk_unarchive"),
    path("bulk/delete/", link_bulk_delete, name="bulk_delete"),
    path("bulk/duplicate/", link_bulk_duplicate, name="bulk_duplicate"),
    path("bulk/export/", link_bulk_export, name="bulk_export"),
]
