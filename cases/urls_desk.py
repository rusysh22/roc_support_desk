"""
Cases — Desk/Admin URL Configuration (authentication required).
All views enforce staff role_access checks.
"""
from django.urls import path

from . import views

app_name = "desk"

urlpatterns = [
    # Case list — Table view
    path("cases/", views.case_list, name="case_list"),

    # Case Kanban board
    path("cases/kanban/", views.case_kanban, name="case_kanban"),

    # Case Calendar view
    path("cases/calendar/", views.case_calendar, name="case_calendar"),

    # Export to Excel
    path("cases/export/", views.case_export_excel, name="case_export"),

    # Case detail — split-panel (chat + RCA)
    path("cases/<uuid:case_id>/", views.case_detail, name="case_detail"),

    # HTMX partials
    path("cases/<uuid:case_id>/rca/", views.case_update_rca, name="case_update_rca"),
    path("cases/<uuid:case_id>/reply/", views.case_send_reply, name="case_send_reply"),
    path("cases/<uuid:case_id>/thread/", views.chat_thread, name="chat_thread"),
    path("cases/<uuid:case_id>/status/", views.case_update_status, name="case_update_status"),
]
