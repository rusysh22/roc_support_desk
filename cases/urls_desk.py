"""
Cases — Desk/Admin URL Configuration (authentication required).
All views enforce staff role_access checks.
"""
from django.urls import path

from . import views

app_name = "desk"

urlpatterns = [
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
    path("cases/<uuid:case_id>/forward/", views.case_forward_escalation, name="case_forward_escalation"),
    path("cases/<uuid:case_id>/request-edit/", views.case_request_edit, name="case_request_edit"),
    path("cases/<uuid:case_id>/approve-edit/", views.case_approve_edit, name="case_approve_edit"),
    path("cases/<uuid:case_id>/reject-edit/", views.case_reject_edit, name="case_reject_edit"),
    path("cases/<uuid:case_id>/unmerge/", views.case_unmerge, name="case_unmerge"),

    # API
    path("api/users/", views.api_users_list, name="api_users_list"),

    # WhatsApp Integration Status
    path("whatsapp/status/", views.whatsapp_status_view, name="whatsapp_status"),

    # Email Settings Dashboard
    path("email-settings/", views.email_settings_view, name="email_settings"),

    # Dynamic Form Builder
    path("forms/", views.form_list_view, name="form_list"),
    path("forms/create/", views.form_create_view, name="form_create"),
    path("forms/<uuid:pk>/edit/", views.form_edit_view, name="form_edit"),
    path("forms/<uuid:pk>/duplicate/", views.form_duplicate_view, name="form_duplicate"),
    path("forms/<uuid:pk>/responses/", views.form_responses_view, name="form_responses"),
    path("forms/<uuid:pk>/responses/export/", views.form_responses_export, name="form_responses_export"),

    # Notifications Bell
    path("notifications/", views.notification_bell, name="notifications"),
    path("notifications/<str:notif_type>/<str:notif_id>/read/", views.mark_notification_read, name="mark_notification_read"),
]
