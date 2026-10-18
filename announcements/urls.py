from django.urls import path

from . import views

app_name = "announcements"

urlpatterns = [
    path("", views.announcement_list, name="list"),
    path("<uuid:pk>/", views.announcement_detail, name="detail"),
    path("<uuid:pk>/acknowledge/", views.announcement_acknowledge, name="acknowledge"),
]
