"""
Cases — Chat Portal URL Configuration (public + login-required).
"""
from django.urls import path

from . import views_chat
from .views import notification_bell, notification_read_all

app_name = "chat"

urlpatterns = [
    # Shared authenticated polling must stay outside the staff-only desk namespace.
    path("notifications/", notification_bell, name="notifications"),
    path("notifications/read-all/", notification_read_all, name="notifications_read_all"),

    # JSON API for the widget category and unit pickers
    path("categories/", views_chat.chat_categories, name="categories"),
    path("units/", views_chat.chat_units, name="units"),

    # JSON API — auto-detect an existing Employee by email or phone
    path("lookup-employee/", views_chat.chat_lookup_employee, name="lookup_employee"),

    # Start a new chat session (POST — creates CaseRecord)
    path("start/", views_chat.chat_start, name="start"),

    # Per-ticket chat room (full page)
    path("<uuid:case_uuid>/", views_chat.chat_room, name="room"),

    # Per-ticket actions
    path("<uuid:case_uuid>/send/", views_chat.chat_send, name="send"),
    path("<uuid:case_uuid>/delete-message/", views_chat.chat_delete_message, name="delete_message"),
    path("<uuid:case_uuid>/upload/", views_chat.chat_upload, name="upload"),
    path("<uuid:case_uuid>/poll/", views_chat.chat_poll, name="poll"),
    path("<uuid:case_uuid>/resolve/", views_chat.chat_resolve, name="resolve"),
    path("<uuid:case_uuid>/reopen/", views_chat.chat_reopen, name="reopen"),
    path("<uuid:case_uuid>/follow-up/", views_chat.chat_follow_up, name="follow_up"),

    # My Tickets (login required)
    path("my-tickets/", views_chat.my_tickets, name="my_tickets"),
    path("my-tickets/status/", views_chat.my_tickets_status, name="my_tickets_status"),
    path("tickets/search/", views_chat.ticket_search, name="ticket_search"),

    # My Approvals — outstanding approval requests assigned to the logged-in user
    path("my-approvals/", views_chat.my_approvals, name="my_approvals"),

    # Followers
    path("<uuid:case_uuid>/followers/search/", views_chat.chat_search_users, name="search_users"),
    path("<uuid:case_uuid>/followers/add/", views_chat.chat_add_follower, name="add_follower"),
    path("<uuid:case_uuid>/followers/remove/", views_chat.chat_remove_follower, name="remove_follower"),

    # OG link preview (public JSON API, SSRF-protected)
    path("link-preview/", views_chat.link_preview, name="link_preview"),
]
