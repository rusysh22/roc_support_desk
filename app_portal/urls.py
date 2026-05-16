from django.urls import path

from . import views

app_name = "app_portal"

urlpatterns = [
    path("", views.portal_view, name="portal"),
]
