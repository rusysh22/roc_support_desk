"""
Gateways — URL Configuration
===============================
Webhook endpoints for Evolution API (WhatsApp) and Microsoft Teams Bot.
"""
from django.urls import path

from . import views

app_name = "gateways"

urlpatterns = [
    # Evolution API webhook — POST only, CSRF exempt, token-secured
    path(
        "evolution/webhook/",
        views.evolution_webhook,
        name="evolution_webhook",
    ),
    # Evolution API v2 appends /<event-name> to the webhook URL (e.g. /messages-upsert)
    path(
        "evolution/webhook/<str:event_suffix>",
        views.evolution_webhook,
        name="evolution_webhook_event",
    ),
    # Microsoft Teams Bot Framework — POST only, CSRF exempt, JWT-secured
    path(
        "teams/webhook/",
        views.teams_bot_webhook,
        name="teams_bot_webhook",
    ),
]
