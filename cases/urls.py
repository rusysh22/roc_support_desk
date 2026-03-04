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

    # Create a new case (optional category pre-selection via slug)
    path("submit/", views.create_case, name="create_case"),
    path("submit/<slug:slug>/", views.create_case, name="create_case_category"),

    # Confirmation page
    path("submitted/<uuid:case_id>/", views.case_submitted, name="case_submitted"),
    path("send-email/<uuid:case_id>/", views.send_case_email, name="send_case_email"),

    # Dynamic Form Public Renderer
    path("f/<slug:slug>/", views.public_form_view, name="public_form"),
]
