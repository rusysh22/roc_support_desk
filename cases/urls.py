"""
Cases — Client-facing URL Configuration (public).
No authentication required.
"""
from django.urls import path

from . import views

app_name = "cases"

urlpatterns = [
    # Home / Client Dashboard
    path("", views.client_dashboard, name="dashboard"),

    # Category management (SuperAdmin only, AJAX)
    path("category/create/", views.create_category, name="create_category"),
    path("category/<uuid:category_id>/update/", views.update_category, name="update_category"),
    path("category/<uuid:category_id>/delete/", views.delete_category, name="delete_category"),

    # Sub-category selection (for main categories with children)
    path("category/<slug:slug>/", views.category_children, name="category_children"),

    # Create a new case (optional category pre-selection via slug)
    path("submit/", views.create_case, name="create_case"),
    path("submit/<slug:slug>/", views.create_case, name="create_case_category"),

    # Confirmation page
    path("submitted/<uuid:case_id>/", views.case_submitted, name="case_submitted"),
    path("send-email/<uuid:case_id>/", views.send_case_email, name="send_case_email"),

    # Dynamic Form Public Renderer
    path("f/<slug:slug>/", views.public_form_view, name="public_form"),
]
