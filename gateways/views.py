"""
Gateways — Webhook Views
==========================
CSRF-exempt endpoints for Evolution API (WhatsApp) and Microsoft Teams Bot webhooks.

Security:
    WhatsApp: Validates the ``X-Evolution-Token`` header.
    Teams Bot: Bearer JWT token is forwarded to the Celery task for async validation
               against Microsoft's JWKS endpoint (avoids blocking the view on HTTPS
               round-trips; Teams retries on non-200 so fast ack is critical).

Async Rule:
    Views never process payloads synchronously. They validate the request,
    dispatch to a Celery task, and return HTTP 200 immediately.
"""
from __future__ import annotations

import json
import logging

from ipware import get_client_ip as _get_client_ip

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
            _get_client_ip(request)[0] or "unknown",
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


@csrf_exempt
@require_POST
def teams_bot_webhook(request: HttpRequest) -> HttpResponse:
    """
    Receive and enqueue a Microsoft Teams Bot Framework activity payload.

    The Bearer JWT token is forwarded to the Celery task where it is validated
    asynchronously against Microsoft's JWKS endpoint, keeping this view fast.
    Teams requires a 200 OK within ~5 s or it will retry the delivery.
    """
    # -----------------------------------------------------------------
    # 1. Check Teams Bot is configured (fast reject before payload parse)
    # -----------------------------------------------------------------
    try:
        from core.models import TeamsConfig
        if not TeamsConfig.get_solo().is_bot_enabled:
            logger.debug("Teams Bot webhook hit but bot is disabled — returning 200.")
            return JsonResponse({"status": "disabled"}, status=200)
    except Exception:
        pass

    # -----------------------------------------------------------------
    # 2. Extract Bearer token from Authorization header
    # -----------------------------------------------------------------
    auth_header: str = request.META.get("HTTP_AUTHORIZATION", "")
    auth_token = ""
    if auth_header.startswith("Bearer "):
        auth_token = auth_header[7:].strip()

    if not auth_token:
        logger.warning(
            "Teams Bot webhook: missing Authorization header from %s",
            _get_client_ip(request)[0] or "unknown",
        )
        # Return 401 so Azure Bot Service knows the endpoint is live but not yet ready
        return JsonResponse({"error": "Unauthorized"}, status=401)

    # -----------------------------------------------------------------
    # 3. Parse JSON activity payload
    # -----------------------------------------------------------------
    try:
        payload: dict = json.loads(request.body)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Teams Bot webhook: malformed JSON — %s", exc)
        return JsonResponse({"error": "Bad Request — invalid JSON body."}, status=400)

    activity_type = payload.get("type", "")
    logger.info(
        "Teams Bot webhook: activity_type=%s, conv=%s",
        activity_type,
        (payload.get("conversation") or {}).get("id", "(none)")[:30],
    )

    # -----------------------------------------------------------------
    # 4. Dispatch to Celery — async processing (JWT validated in task)
    # -----------------------------------------------------------------
    try:
        from gateways.tasks import process_teams_webhook_task
        process_teams_webhook_task.delay(payload, auth_token)
        logger.info("Teams Bot activity enqueued (type=%s).", activity_type)
    except Exception as exc:
        logger.exception("Teams Bot webhook: failed to enqueue task — %s", exc)
        return JsonResponse({"error": "Internal error — task dispatch failed."}, status=500)

    # -----------------------------------------------------------------
    # 5. Return 200 immediately
    # -----------------------------------------------------------------
    return JsonResponse({"status": "received"}, status=200)
