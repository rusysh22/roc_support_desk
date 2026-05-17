"""
Core — Notification Dispatcher
================================
Central notification system for RoC Desk.

Usage:
    from core.notifications import notify_user
    notify_user(user, "new_message", {
        "ticket_id": str(case.id),
        "ticket_subject": case.subject,
        "sender_name": "Doni",
        "message_preview": "Hello...",
    })
"""
import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

# Event type → human-readable label
EVENT_LABELS = {
    "new_message": "New Chat Message",
    "mention": "@Mention",
    "status_change": "Ticket Status Changed",
    "follower_added": "Added as Follower",
}


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def notify_user(user, event_type: str, context: dict):
    """
    Central notification dispatcher.

    Checks user preferences, respects quiet hours, then dispatches
    to all enabled channels via Celery async tasks.

    Args:
        user: User model instance (the recipient)
        event_type: One of "new_message", "mention", "status_change", "follower_added"
        context: Dict with event-specific data (ticket_id, sender_name, message_preview, etc.)
    """
    pref = _get_pref(user)
    if not pref:
        return

    # Check if event type is enabled
    event_field = f"on_{event_type}"
    if not getattr(pref, event_field, False):
        return

    # Check quiet hours
    if pref.is_in_quiet_hours():
        logger.debug("Notification suppressed (quiet hours) for user=%s event=%s", user, event_type)
        return

    # Enrich context with common data
    context.setdefault("event_type", event_type)
    context.setdefault("event_label", EVENT_LABELS.get(event_type, event_type))
    context.setdefault("recipient_name", user.username or user.get_full_name() or "User")
    context.setdefault("site_name", _get_site_name())

    # Dispatch to enabled channels
    if pref.email_enabled and user.email:
        send_notification_email_task.delay(str(user.id), event_type, context)

    if pref.whatsapp_enabled:
        wa_number = pref.get_whatsapp_destination()
        if wa_number:
            send_notification_whatsapp_task.delay(str(user.id), event_type, context, wa_number)

    if pref.teams_enabled and pref.teams_webhook_url:
        send_notification_teams_task.delay(str(user.id), event_type, context, pref.teams_webhook_url)


def notify_users_bulk(users, event_type: str, context: dict, exclude_user=None):
    """
    Notify multiple users for the same event (e.g. all followers of a ticket).

    Args:
        users: Iterable of User instances
        event_type: Event type string
        context: Shared context dict
        exclude_user: Optional user to skip (e.g. the sender themselves)
    """
    for user in users:
        if exclude_user and user.id == exclude_user.id:
            continue
        notify_user(user, event_type, context)


# ──────────────────────────────────────────────────────────────────────
# Celery Tasks
# ──────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def send_notification_email_task(self, user_id: str, event_type: str, context: dict):
    """Send notification email to a user."""
    from core.models import User
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return "User not found"

    subject = _build_email_subject(event_type, context)
    html_body = _build_email_html(event_type, context)
    plain_body = strip_tags(html_body)

    try:
        send_mail(
            subject=subject,
            message=plain_body,
            from_email=_get_from_email(),
            recipient_list=[user.email],
            html_message=html_body,
            fail_silently=False,
        )
        logger.info("Email notification sent to %s for %s", user.email, event_type)
        return f"Sent to {user.email}"
    except Exception as exc:
        logger.error("Email notification failed for %s: %s", user.email, exc)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def send_notification_whatsapp_task(self, user_id: str, event_type: str, context: dict, phone_number: str):
    """Send notification via WhatsApp (Evolution API)."""
    try:
        from gateways.services import EvolutionAPIClient
        client = EvolutionAPIClient()

        text = _build_whatsapp_text(event_type, context)
        # Strip + and format for Evolution API
        wa_id = phone_number.lstrip("+").replace("-", "").replace(" ", "")
        client.send_whatsapp_message(wa_id, text)
        logger.info("WhatsApp notification sent to %s for %s", phone_number, event_type)
        return f"Sent to {phone_number}"
    except Exception as exc:
        logger.error("WhatsApp notification failed for %s: %s", phone_number, exc)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def send_notification_teams_task(self, user_id: str, event_type: str, context: dict, webhook_url: str):
    """Send notification via Teams Incoming Webhook."""
    import requests

    payload = _build_teams_card(event_type, context)
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Teams notification sent for %s", event_type)
        return "Sent to Teams"
    except Exception as exc:
        logger.error("Teams notification failed: %s", exc)
        raise self.retry(exc=exc)


# ──────────────────────────────────────────────────────────────────────
# Message Builders
# ──────────────────────────────────────────────────────────────────────

def _build_email_subject(event_type: str, context: dict) -> str:
    site = context.get("site_name", "Support Desk")
    ticket_subject = context.get("ticket_subject", "")
    label = EVENT_LABELS.get(event_type, "Notification")

    if ticket_subject:
        return f"[{site}] {label}: {ticket_subject}"
    return f"[{site}] {label}"


def _build_email_html(event_type: str, context: dict) -> str:
    """Build a clean HTML email for notifications."""
    site = context.get("site_name", "Support Desk")
    label = context.get("event_label", "Notification")
    ticket_subject = context.get("ticket_subject", "")
    sender_name = context.get("sender_name", "Someone")
    message_preview = context.get("message_preview", "")
    ticket_url = context.get("ticket_url", "")
    recipient = context.get("recipient_name", "User")

    # Build description based on event type
    descriptions = {
        "new_message": f"<strong>{sender_name}</strong> sent a new message in your ticket.",
        "mention": f"<strong>{sender_name}</strong> mentioned you (@{recipient}) in a chat.",
        "status_change": f"Ticket status has been updated to <strong>{context.get('new_status', 'Unknown')}</strong>.",
        "follower_added": f"You have been added as a follower to a ticket by <strong>{sender_name}</strong>.",
    }
    description = descriptions.get(event_type, "You have a new notification.")

    return f"""
    <div style="max-width:520px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;">
        <div style="background:linear-gradient(135deg,#4f46e5,#6366f1);padding:20px 24px;">
            <h1 style="margin:0;font-size:16px;font-weight:800;color:#fff;letter-spacing:-0.02em;">{site}</h1>
        </div>
        <div style="padding:24px;">
            <p style="margin:0 0 4px;font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#94a3b8;font-weight:700;">{label}</p>
            <h2 style="margin:0 0 16px;font-size:18px;font-weight:700;color:#1e293b;">{ticket_subject or 'Notification'}</h2>
            <p style="margin:0 0 16px;font-size:14px;color:#475569;line-height:1.6;">{description}</p>
            {"<div style='background:#f8fafc;border-left:3px solid #6366f1;padding:12px 16px;border-radius:0 8px 8px 0;margin:0 0 16px;'><p style=\"margin:0;font-size:13px;color:#64748b;\">" + (message_preview[:200]) + "</p></div>" if message_preview else ""}
            {"<a href='" + ticket_url + "' style='display:inline-block;background:#4f46e5;color:#fff;padding:10px 24px;border-radius:8px;font-size:13px;font-weight:700;text-decoration:none;'>View Ticket →</a>" if ticket_url else ""}
        </div>
        <div style="padding:12px 24px;background:#f8fafc;border-top:1px solid #f1f5f9;">
            <p style="margin:0;font-size:11px;color:#94a3b8;">You are receiving this because you enabled notifications in your profile settings.</p>
        </div>
    </div>
    """


def _build_whatsapp_text(event_type: str, context: dict) -> str:
    """Build plain text WhatsApp message."""
    site = context.get("site_name", "Support Desk")
    label = context.get("event_label", "Notification")
    ticket_subject = context.get("ticket_subject", "")
    sender_name = context.get("sender_name", "Someone")
    message_preview = context.get("message_preview", "")
    ticket_url = context.get("ticket_url", "")

    parts = [
        f"🔔 *{site} — {label}*",
        "",
    ]
    if ticket_subject:
        parts.append(f"📋 *{ticket_subject}*")

    descs = {
        "new_message": f"💬 {sender_name} mengirim pesan baru.",
        "mention": f"📣 {sender_name} menyebut Anda di chat.",
        "status_change": f"🔄 Status tiket berubah: *{context.get('new_status', '')}*",
        "follower_added": f"👥 {sender_name} menambahkan Anda sebagai follower.",
    }
    parts.append(descs.get(event_type, "Anda punya notifikasi baru."))

    if message_preview:
        parts.append(f"\n> _{message_preview[:150]}_")

    if ticket_url:
        parts.append(f"\n🔗 {ticket_url}")

    return "\n".join(parts)


def _build_teams_card(event_type: str, context: dict) -> dict:
    """Build Microsoft Teams Adaptive Card payload."""
    site = context.get("site_name", "Support Desk")
    label = context.get("event_label", "Notification")
    ticket_subject = context.get("ticket_subject", "")
    sender_name = context.get("sender_name", "Someone")
    message_preview = context.get("message_preview", "")
    ticket_url = context.get("ticket_url", "")

    card = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {
                        "type": "TextBlock",
                        "text": f"🔔 {site}",
                        "weight": "Bolder",
                        "size": "Medium",
                        "color": "Accent",
                    },
                    {
                        "type": "TextBlock",
                        "text": label,
                        "size": "Small",
                        "isSubtle": True,
                        "spacing": "None",
                    },
                    {
                        "type": "TextBlock",
                        "text": ticket_subject or "Notification",
                        "weight": "Bolder",
                        "size": "Large",
                        "wrap": True,
                    },
                    {
                        "type": "TextBlock",
                        "text": f"From: **{sender_name}**",
                        "wrap": True,
                    },
                ],
            },
        }],
    }

    body = card["attachments"][0]["content"]["body"]

    if message_preview:
        body.append({
            "type": "TextBlock",
            "text": message_preview[:200],
            "wrap": True,
            "isSubtle": True,
        })

    if ticket_url:
        card["attachments"][0]["content"]["actions"] = [{
            "type": "Action.OpenUrl",
            "title": "View Ticket →",
            "url": ticket_url,
        }]

    return card


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────

def _get_pref(user):
    """Get or return None for user's notification preference."""
    try:
        return user.notification_pref
    except Exception:
        return None


def _get_site_name():
    """Get site name from SiteConfig."""
    try:
        from core.models import SiteConfig
        return SiteConfig.get_solo().site_name or "Support Desk"
    except Exception:
        return "Support Desk"


def _get_from_email():
    """Get the from-email address."""
    try:
        from core.models import EmailConfig
        cfg = EmailConfig.get_solo()
        return cfg.default_from_email or cfg.smtp_user or settings.DEFAULT_FROM_EMAIL
    except Exception:
        return getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@example.com")
