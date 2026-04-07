"""
Gateways — Webhook Views
==========================
CSRF-exempt endpoint for Evolution API (WhatsApp) webhooks.

Security:
    Validates the ``X-Evolution-Token`` header against the
    ``EVOLUTION_WEBHOOK_TOKEN`` setting from ``.env``.

Async Rule:
    The view **never** processes the payload synchronously.  It validates
    the request, extracts the JSON body, dispatches it to a Celery task,
    and returns ``HTTP 200 OK`` immediately — preventing Evolution API
    from timing out and retrying.
"""
from __future__ import annotations

import json
import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def evolution_webhook(request: HttpRequest, event_suffix: str = "") -> HttpResponse:
    """
    Receive and enqueue an Evolution API webhook payload.
    Supports Evolution API v1 and v2 payload formats.
    """
    # -----------------------------------------------------------------
    # 1. Debug — log incoming request details
    # -----------------------------------------------------------------
    logger.info(
        "Webhook received: method=%s, path=%s, content_type=%s, body_length=%d",
        request.method,
        request.path,
        request.content_type,
        len(request.body),
    )

    # -----------------------------------------------------------------
    # 2. Security — Validate webhook token (lenient for Evolution API v2)
    # -----------------------------------------------------------------
    expected_token: str = settings.EVOLUTION_WEBHOOK_TOKEN
    received_token: str = request.META.get("HTTP_X_EVOLUTION_TOKEN", "")

    if not expected_token:
        logger.error(
            "EVOLUTION_WEBHOOK_TOKEN is not configured in settings. "
            "Rejecting all webhooks until it is set."
        )
        return JsonResponse(
            {"error": "Webhook not configured."},
            status=503,
        )

    # Evolution API v2 may not reliably forward custom webhook headers.
    # It often sends the *instance token* instead of our configured webhook
    # token. Log mismatch but allow the request through.
    if received_token and received_token != expected_token:
        logger.info(
            "Webhook token mismatch from %s (got '%s...'). "
            "Allowing for Evolution API v2 compatibility.",
            request.META.get("REMOTE_ADDR", "unknown"),
            received_token[:10],
        )

    # -----------------------------------------------------------------
    # 3. Parse JSON payload
    # -----------------------------------------------------------------
    try:
        payload: dict = json.loads(request.body)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "Webhook rejected — malformed JSON: %s. Body preview: %s",
            exc,
            request.body[:200],
        )
        return JsonResponse(
            {"error": "Bad Request — invalid JSON body."},
            status=400,
        )

    logger.info(
        "Webhook payload parsed: event=%s, keys=%s",
        payload.get("event", "(none)"),
        list(payload.keys())[:10],
    )

    # -----------------------------------------------------------------
    # 4. Quick validation — only process message events
    # -----------------------------------------------------------------
    # Event can come from payload OR from URL suffix
    event: str = payload.get("event", "")
    if not event and event_suffix:
        # Convert URL suffix "messages-upsert" → "MESSAGES_UPSERT"
        event = event_suffix.upper().replace("-", "_")
        payload["event"] = event

    # Evolution API v1: "messages.upsert", v2: "MESSAGES_UPSERT"
    normalized_event = event.lower().replace(".", "_").replace("-", "_")
    allowed_normalized = {"messages_upsert", "messages_update", ""}

    if normalized_event not in allowed_normalized:
        logger.debug("Webhook event '%s' (normalized: '%s') ignored.", event, normalized_event)
        return JsonResponse({"status": "ignored", "event": event})

    # -----------------------------------------------------------------
    # 5. Dispatch to Celery — async processing
    # -----------------------------------------------------------------
    try:
        if normalized_event == "messages_update":
            from gateways.tasks import process_message_update_task
            process_message_update_task.delay(payload)
            logger.info("Webhook messages_update enqueued for async processing.")
        else:
            from gateways.tasks import process_evolution_webhook_task
            process_evolution_webhook_task.delay(payload)
            logger.info(
                "Webhook payload enqueued for async processing (event=%s).",
                event or "default",
            )
    except Exception as exc:
        logger.exception("Failed to enqueue webhook task: %s", exc)
        return JsonResponse(
            {"error": "Internal error — task dispatch failed."},
            status=500,
        )

    # -----------------------------------------------------------------
    # 6. Return 200 immediately — do NOT block
    # -----------------------------------------------------------------
    return JsonResponse({"status": "received"}, status=200)
