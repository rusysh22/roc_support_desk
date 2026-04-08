"""
User Management URL Configuration
=================================
"""
from django.urls import path

from . import views_user

app_name = "users_desk"

urlpatterns = [
    path("", views_user.user_list, name="user_list"),
    path("create/", views_user.user_create, name="user_create"),
    path("<int:pk>/edit/", views_user.user_edit, name="user_edit"),
    path("<int:pk>/delete/", views_user.user_delete, name="user_delete"),
    path("export/", views_user.user_export, name="user_export"),
    path("import/template/", views_user.user_import_template, name="user_import_template"),
    path("import/", views_user.user_import, name="user_import"),
    path("bulk-delete/", views_user.user_bulk_delete, name="user_bulk_delete"),
    path("bulk-archive/", views_user.user_bulk_archive, name="user_bulk_archive"),
    path("bulk-unarchive/", views_user.user_bulk_unarchive, name="user_bulk_unarchive"),
    path("<int:pk>/archive/", views_user.user_archive, name="user_archive"),
    path("<int:pk>/unarchive/", views_user.user_unarchive, name="user_unarchive"),
]
