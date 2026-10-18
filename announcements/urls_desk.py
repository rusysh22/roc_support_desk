from django.urls import path

from . import views_desk

app_name = "announcements_desk"

urlpatterns = [
    path("", views_desk.manage_list, name="list"),
    path("new/", views_desk.manage_create, name="create"),
    path("bulk/archive/", views_desk.bulk_archive, name="bulk_archive"),
    path("bulk/export/", views_desk.bulk_export, name="bulk_export"),
    path("bulk/delete/", views_desk.bulk_delete, name="bulk_delete"),
    path("<uuid:pk>/edit/", views_desk.manage_edit, name="edit"),
    path("<uuid:pk>/delete/", views_desk.manage_delete, name="delete"),
]
