"""
core/views_ai.py
================
API endpoint for the AI Assistant widget.

POST /api/ai/ask/
    Request:  {"question": "..."}
    Response: {"success": true, "answer": "...", "sources": [...]}
              {"success": false, "error": "..."}

Accessible by both authenticated and anonymous users.
Rate-limited per session key (authenticated) or IP address (anonymous).
"""
import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)


def _get_identifier(request) -> str:
    """
    Build a rate-limit identifier.
    - Authenticated users: session key (stable per session).
    - Anonymous users: client IP address.
    """
    if request.user.is_authenticated:
        return f"user:{request.session.session_key or request.user.pk}"
    # Fall back to IP
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if x_forwarded:
        ip = x_forwarded.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR", "unknown")
    return f"ip:{ip}"


@csrf_protect
@require_POST
def ai_ask_view(request):
    """
    Handle a single AI question from the floating widget.

    Validates input, delegates to ai_service.ask_ai(), returns JSON.
    Session is used for rate limiting (best-practice for single-tab Q&A).
    """
    # Ensure session exists so session_key is available for rate limiting
    if not request.session.session_key:
        request.session.create()

    # ── Parse request body ─────────────────────────────────────────────
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"success": False, "error": "Invalid request."}, status=400)

    question = (body.get("question") or "").strip()
    if not question:
        return JsonResponse({"success": False, "error": "Question cannot be empty."}, status=400)

    if len(question) > 1000:
        return JsonResponse(
            {"success": False, "error": "Question is too long (max 1000 characters)."},
            status=400,
        )

    # ── Call AI service ────────────────────────────────────────────────
    identifier = _get_identifier(request)

    from core.ai_service import ask_ai
    result = ask_ai(question=question, identifier=identifier)

    status_code = 200 if result.get("success") else 503
    return JsonResponse(result, status=status_code)
