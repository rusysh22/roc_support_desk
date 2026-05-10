"""
Cases App — Celery Tasks
=========================
All tasks route to the dedicated ``chat_tasks`` queue to prevent
blocking by heavy system jobs (email polling, report generation, etc.).
"""
import logging

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)

QUEUE = "chat_tasks"


@shared_task(
    bind=True,
    name="cases.send_chat_offline_notification",
    queue=QUEUE,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def send_chat_offline_notification(self, case_id: str, message_id: str) -> None:
    """
    Email the case requester when a staff message arrives while they are offline.

    Called with a 15-second countdown so real-time WebSocket delivery is
    attempted first. has_unread_messages acts as a proxy: if the user saw
    the message the flag would have been cleared.
    """
    from cases.models import CaseRecord, Message
    from core.models import SiteConfig
    from django.core.mail import EmailMultiAlternatives
    from django.utils.html import strip_tags

    try:
        msg = Message.objects.select_related("case", "sender_staff").get(id=message_id)
    except Message.DoesNotExist:
        return

    case = msg.case

    # Only notify for outbound staff messages when the ticket still shows unread.
    if msg.direction != "OUT" or not case.has_unread_messages:
        return

    recipient_email = case.requester_email
    if not recipient_email:
        return

    try:
        site_config = SiteConfig.get_solo()
        site_name = getattr(site_config, "site_name", "Support Desk")
    except Exception:
        site_name = "Support Desk"

    sender_name = (
        msg.sender_staff.get_full_name() if msg.sender_staff else "Support Team"
    )
    preview = (msg.body or "")[:200]
    case_url = f"{getattr(settings, 'SITE_URL', '')}/portal/chat/{case.id}/"

    subject = f"[{site_name}] Ada balasan baru untuk tiket Anda: {case.subject}"
    html_content = f"""
    <!DOCTYPE html><html><body style="font-family:sans-serif;color:#374151;">
    <p>Halo <strong>{case.requester_name or 'User'}</strong>,</p>
    <p><strong>{sender_name}</strong> membalas tiket Anda
       <strong>{case.case_number}</strong>:</p>
    <blockquote style="border-left:3px solid #4f46e5;margin:12px 0;
                        padding:8px 12px;color:#6b7280;background:#f5f3ff;">
      {preview}
    </blockquote>
    <p><a href="{case_url}"
          style="background:#4f46e5;color:#fff;padding:10px 20px;
                 border-radius:6px;text-decoration:none;display:inline-block;">
      Lihat Tiket
    </a></p>
    <p style="font-size:12px;color:#9ca3af;margin-top:24px;">
      Email ini dikirim otomatis oleh {site_name}.
    </p>
    </body></html>
    """

    try:
        mail = EmailMultiAlternatives(
            subject=subject,
            body=strip_tags(html_content),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient_email],
        )
        mail.attach_alternative(html_content, "text/html")
        mail.send()
        logger.info("Offline notification sent to %s for case %s", recipient_email, case_id)
    except Exception as exc:
        logger.warning("Offline notification failed: %s", exc)
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    name="cases.batch_mark_messages_read",
    queue=QUEUE,
    acks_late=True,
)
def batch_mark_messages_read(self, case_id: str, latest_message_id: str) -> None:
    """
    Batch-mark all inbound messages up to (and including) latest_message_id as read.

    Uses sent_at of the target message as the upper bound since PKs are UUIDs
    and have no chronological ordering guarantee.
    """
    from cases.models import Message, CaseRecord

    try:
        pivot = Message.objects.get(id=latest_message_id, case_id=case_id)
    except Message.DoesNotExist:
        return

    Message.objects.filter(
        case_id=case_id,
        direction=Message.Direction.INBOUND,
        is_read=False,
        sent_at__lte=pivot.sent_at,
    ).update(is_read=True)

    has_remaining = Message.objects.filter(
        case_id=case_id,
        direction=Message.Direction.INBOUND,
        is_read=False,
    ).exists()

    if not has_remaining:
        CaseRecord.objects.filter(id=case_id).update(has_unread_messages=False)


@shared_task(
    name="cases.auto_close_idle_tickets",
    queue=QUEUE,
    acks_late=True,
)
def auto_close_idle_tickets() -> int:
    """
    Close Resolved tickets that have received no inbound message for
    CHAT_AUTO_CLOSE_IDLE_DAYS (default 7) days.

    Register in Celery Beat:
        "auto-close-idle-tickets": {
            "task": "cases.auto_close_idle_tickets",
            "schedule": crontab(hour=2, minute=0),
        }
    """
    from cases.models import CaseRecord, Message
    from django.utils import timezone
    from datetime import timedelta

    idle_days = getattr(settings, "CHAT_AUTO_CLOSE_IDLE_DAYS", 7)
    cutoff = timezone.now() - timedelta(days=idle_days)

    # Resolved tickets where the most recent inbound message predates the cutoff.
    resolved_ids = CaseRecord.objects.filter(
        status=CaseRecord.Status.RESOLVED
    ).values_list("id", flat=True)

    # Exclude tickets that have a recent inbound message.
    recently_active_ids = Message.objects.filter(
        case_id__in=resolved_ids,
        direction=Message.Direction.INBOUND,
        sent_at__gte=cutoff,
    ).values_list("case_id", flat=True).distinct()

    idle_qs = CaseRecord.objects.filter(
        id__in=resolved_ids,
    ).exclude(id__in=recently_active_ids)

    count = idle_qs.update(status=CaseRecord.Status.CLOSED)
    logger.info("auto_close_idle_tickets: closed %d idle tickets", count)
    return count
