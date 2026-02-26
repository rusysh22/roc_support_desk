"""
Gateways — URL Configuration
===============================
Webhook endpoint for Evolution API (WhatsApp gateway).
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
]
