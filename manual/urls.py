"""Manual app URL configuration."""
from django.urls import path
from . import views

app_name = "manual"

urlpatterns = [
    # Public landing
    path("", views.landing, name="landing"),
    path("search/", views.search, name="search"),

    # Editor
    path("manage/", views.manage_list, name="manage_list"),
    path("manage/new/", views.manual_create, name="manual_create"),
    path("manage/<slug:manual_slug>/", views.manual_edit, name="manual_edit"),
    path("manage/<slug:manual_slug>/pages/new/", views.page_new, name="page_new"),
    path("manage/<slug:manual_slug>/pages/<int:page_id>/", views.page_edit, name="page_edit"),
    path("manage/<slug:manual_slug>/pages/reorder/", views.page_reorder, name="page_reorder"),

    # Public / viewer (must come after manage/ routes so "manage" isn't caught as a slug)
    path("<slug:manual_slug>/", views.viewer, name="viewer"),
    path("<slug:manual_slug>/page/<int:page_id>/", views.viewer, name="viewer_page"),
]
