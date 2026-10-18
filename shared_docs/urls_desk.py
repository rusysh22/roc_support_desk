from django.urls import path
from . import views_desk

app_name = "shared_docs_desk"

urlpatterns = [
    path("", views_desk.shared_doc_list, name="list"),
    path("new/", views_desk.shared_doc_create, name="create"),
    path("<uuid:pk>/edit/", views_desk.shared_doc_edit, name="edit"),
    path("<uuid:pk>/delete/", views_desk.shared_doc_delete, name="delete"),
    path("<uuid:pk>/toggle/", views_desk.shared_doc_toggle_status, name="toggle_status"),
    path("bulk/archive/", views_desk.shared_doc_bulk_archive, name="bulk_archive"),
    path("bulk/unarchive/", views_desk.shared_doc_bulk_unarchive, name="bulk_unarchive"),
    path("bulk/delete/", views_desk.shared_doc_bulk_delete, name="bulk_delete"),
    path("bulk/duplicate/", views_desk.shared_doc_bulk_duplicate, name="bulk_duplicate"),
    path("bulk/export/", views_desk.shared_doc_bulk_export, name="bulk_export"),
]
