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
    
    # My E-Forms (List of standalone submissions for the user)
    path("my-eforms/", views.my_eforms_list, name="my_eforms"),
    path("eforms/new/", views.eform_template_list, name="eform_template_list"),
    path("eforms/new/<uuid:template_id>/", views.eform_initiate, name="eform_initiate"),
    path("eforms/<uuid:submission_id>/", views.eform_detail, name="eform_detail"),

    # Category management (SuperAdmin only, AJAX)
    path("category/create/", views.create_category, name="create_category"),
    path("category/<uuid:category_id>/update/", views.update_category, name="update_category"),
    path("category/<uuid:category_id>/delete/", views.delete_category, name="delete_category"),

    # Sub-category selection
    path("category/<slug:slug>/", views.category_children, name="category_children"),

    # Create a new case (optional category pre-selection via slug)
    path("submit/", views.create_case, name="create_case"),
    path("submit/<slug:slug>/", views.create_case, name="create_case_category"),

    # Confirmation page
    path("submitted/<uuid:case_id>/", views.case_submitted, name="case_submitted"),
    path("send-email/<uuid:case_id>/", views.send_case_email, name="send_case_email"),

    # Enterprise E-Form Public/Vendor View
    path("eforms/guest/<str:token>/", views.eform_public_detail, name="eform_public_detail"),

    # Dynamic Form Public Renderer (Legacy)
    path("f/<slug:slug>/", views.public_form_view, name="public_form"),

    # Document template preview (HTMX, admin staff)
    path("document-preview/<uuid:template_id>/", views.document_template_preview_html, name="document_template_preview_html"),

    # Change Request Document (Surat Kronologi)
    path("change-request/<uuid:case_id>/new/", views.portal_change_request_new, name="portal_change_request_new"),
    path("change-request/<uuid:doc_id>/", views.portal_change_request_detail, name="portal_change_request_detail"),
    path("change-request/<uuid:doc_id>/revise/", views.portal_change_request_revise, name="portal_change_request_revise"),
    path("change-request/approve/<uuid:token>/", views.change_request_approve, name="change_request_approve"),
]
