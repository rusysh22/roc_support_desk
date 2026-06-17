from django.urls import path
from . import views_public

app_name = "projects_public"

urlpatterns = [
    path("project/<uuid:pk>/", views_public.public_project_timeline, name="public_timeline"),
    path("project/<uuid:pk>/phase/<uuid:phase_id>/comment/", views_public.add_comment_view, name="add_comment"),
]
