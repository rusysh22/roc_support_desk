"""
Cases — Desk/Admin URL Configuration (authentication required).
All views enforce staff role_access checks.
"""
from django.urls import path

from . import views

app_name = "desk"

urlpatterns = [
    # Dashboard — Analytics
    path("", views.dashboard, name="dashboard"),

    # Ticket list — Table view
    path("cases/", views.case_list, name="case_list"),
    path("cases/bulk-action/", views.case_bulk_action, name="case_bulk_action"),

    # Ticket Kanban board
    path("cases/kanban/", views.case_kanban, name="case_kanban"),

    # Ticket Calendar view
    path("cases/calendar/", views.case_calendar, name="case_calendar"),

    # Auto-Refresh Partial Endpoints (returns HTML fragments only)
    path("cases/partials/table/", views.case_list_partial, name="case_list_partial"),
    path("cases/partials/kanban/", views.case_kanban_partial, name="case_kanban_partial"),

    # Export to Excel
    path("cases/export/", views.case_export_excel, name="case_export"),

    # Ticket detail — split-panel (chat + RCA)
    path("cases/<uuid:case_id>/", views.case_detail, name="case_detail"),

    # HTMX partials
    path("cases/<uuid:case_id>/quick-view/", views.case_quick_view, name="case_quick_view"),
    path("cases/<uuid:case_id>/rca/", views.case_update_rca, name="case_update_rca"),
    path("cases/<uuid:case_id>/reply/", views.case_send_reply, name="case_send_reply"),
    path("cases/<uuid:case_id>/comment/", views.case_add_comment, name="case_add_comment"),
    path("cases/<uuid:case_id>/thread/", views.chat_thread, name="chat_thread"),
    path("cases/<uuid:case_id>/status/", views.case_update_status, name="case_update_status"),
    path("cases/<uuid:case_id>/close-notify/", views.case_close_and_notify, name="case_close_and_notify"),
    path("cases/<uuid:case_id>/update-requester/", views.case_update_requester, name="case_update_requester"),
    path("cases/<uuid:case_id>/update-subject/", views.case_update_subject, name="case_update_subject"),
    path("cases/<uuid:case_id>/update-category/", views.case_update_category, name="case_update_category"),
    path("cases/<uuid:case_id>/change-requester/", views.case_change_requester, name="case_change_requester"),
    path("cases/<uuid:case_id>/forward/", views.case_forward_escalation, name="case_forward_escalation"),
    path("cases/<uuid:case_id>/request-edit/", views.case_request_edit, name="case_request_edit"),
    path("cases/<uuid:case_id>/approve-edit/", views.case_approve_edit, name="case_approve_edit"),
    path("cases/<uuid:case_id>/reject-edit/", views.case_reject_edit, name="case_reject_edit"),
    path("cases/<uuid:case_id>/unmerge/", views.case_unmerge, name="case_unmerge"),
    
    # Message Actions (Delete, Edit, React)
    path("cases/<uuid:case_id>/msg/<uuid:message_id>/delete/", views.message_delete, name="message_delete"),
    path("cases/<uuid:case_id>/msg/<uuid:message_id>/edit/", views.message_edit, name="message_edit"),
    path("cases/<uuid:case_id>/msg/<uuid:message_id>/react/", views.message_react, name="message_react"),

    # Toggle WA Session Hold
    path("cases/<uuid:case_id>/toggle-wa-session/", views.toggle_wa_session, name="toggle_wa_session"),

    # API
    path("api/users/", views.api_users_list, name="api_users_list"),

    # WhatsApp Integration Status
    path("whatsapp/status/", views.whatsapp_status_view, name="whatsapp_status"),
    path("whatsapp/disconnect/", views.whatsapp_disconnect_view, name="whatsapp_disconnect"),

    # Email Settings Dashboard
    path("email-settings/", views.email_settings_view, name="email_settings"),

    # Apps Connection Hub (Master Data — SuperAdmin only)
    path("apps-connection/", views.apps_connection_view, name="apps_connection"),

    # Dynamic Form Builder
    path("forms/", views.form_list_view, name="form_list"),
    path("forms/create/", views.form_create_view, name="form_create"),
    path("forms/<uuid:pk>/edit/", views.form_edit_view, name="form_edit"),
    path("forms/<uuid:pk>/delete/", views.form_delete_view, name="form_delete"),
    path("forms/<uuid:pk>/duplicate/", views.form_duplicate_view, name="form_duplicate"),
    path("forms/<uuid:pk>/responses/", views.form_responses_view, name="form_responses"),
    path("forms/<uuid:pk>/responses/export/", views.form_responses_export, name="form_responses_export"),

    # RCA Templates
    path("rca-templates/create/", views.rca_template_create, name="rca_template_create"),
    path("rca-templates/<uuid:template_id>/delete/", views.rca_template_delete, name="rca_template_delete"),

    # Notifications Bell
    path("notifications/", views.notification_bell, name="notifications"),
    path("notifications/<str:notif_type>/<str:notif_id>/read/", views.mark_notification_read, name="mark_notification_read"),
]

# Import the CompanyUnit CRUD from core.views
from core.views import (
    CompanyUnitListView, CompanyUnitCreateView, CompanyUnitUpdateView, CompanyUnitDeleteView
)

urlpatterns += [
    # Company Units Master Data
    path("company-units/", CompanyUnitListView.as_view(), name="company_unit_list"),
    path("company-units/create/", CompanyUnitCreateView.as_view(), name="company_unit_create"),
    path("company-units/<uuid:pk>/edit/", CompanyUnitUpdateView.as_view(), name="company_unit_edit"),
    path("company-units/<uuid:pk>/delete/", CompanyUnitDeleteView.as_view(), name="company_unit_delete"),
]
