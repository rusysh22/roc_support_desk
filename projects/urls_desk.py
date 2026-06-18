from django.urls import path
from . import views_desk

app_name = "projects_desk"

urlpatterns = [
    # Projects
    path("", views_desk.project_list, name="project_list"),
    path("new/", views_desk.project_create, name="project_create"),
    path("<uuid:pk>/edit/", views_desk.project_edit, name="project_edit"),
    path("<uuid:pk>/delete/", views_desk.project_delete, name="project_delete"),
    path("<uuid:pk>/toggle-public/", views_desk.project_toggle_public, name="project_toggle_public"),
    
    # Phases & Updates
    path("<uuid:project_id>/phase/new/", views_desk.phase_create, name="phase_create"),
    path("phase/<uuid:phase_id>/edit/", views_desk.phase_edit, name="phase_edit"),
    path("phase/<uuid:phase_id>/delete/", views_desk.phase_delete, name="phase_delete"),
    path("phase/<uuid:phase_id>/update/new/", views_desk.update_create, name="update_create"),
    path("update/<uuid:update_id>/edit/", views_desk.update_edit, name="update_edit"),
    path("update/<uuid:update_id>/delete/", views_desk.update_delete, name="update_delete"),

    # Checklist
    path("phase/<uuid:phase_id>/checklist/new/", views_desk.checklist_create, name="checklist_create"),
    path("checklist/<uuid:checklist_id>/toggle/", views_desk.checklist_toggle, name="checklist_toggle"),
    path("checklist/<uuid:checklist_id>/delete/", views_desk.checklist_delete, name="checklist_delete"),
]
