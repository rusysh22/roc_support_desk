from django.urls import path

from . import views

app_name = "app_portal"

urlpatterns = [
    path("", views.portal_view, name="portal"),
    path("create/", views.entry_create, name="entry_create"),
    path("<int:pk>/update/", views.entry_update, name="entry_update"),
    path("<int:pk>/delete/", views.entry_delete, name="entry_delete"),
]
