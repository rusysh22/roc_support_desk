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
    from core.utils.email_branding import attach_logo, render_branded_from_template
    from django.core.mail import EmailMultiAlternatives
    from django.utils.html import strip_tags

    try:
        msg = Message.objects.select_related("case", "sender_staff").get(id=message_id)
    except Message.DoesNotExist:
        return

    case = msg.case

    if msg.direction != "OUT" or not case.has_unread_messages:
        return

    recipient_email = case.requester_email
    if not recipient_email:
        return

    try:
        site_config = SiteConfig.get_solo()
        site_name = getattr(site_config, "site_name", "Support Desk")
        base_url = (site_config.site_url or getattr(settings, 'SITE_URL', '')).rstrip('/')
    except Exception:
        site_name = "Support Desk"
        base_url = getattr(settings, 'SITE_URL', '').rstrip('/')

    sender_name = (
        msg.sender_staff.get_full_name() if msg.sender_staff else "Support Team"
    )
    preview_plain = strip_tags(msg.body or "")[:200]
    case_url = f"{base_url}/portal/chat/{case.id}/"

    subject = f"[{site_name}] New reply on your ticket: {case.subject}"
    html_content = render_branded_from_template(
        "emails/bodies/offline_chat.html",
        {
            "requester_name": case.requester_name or "User",
            "sender_name": sender_name,
            "case_number": case.case_number,
            "preview": preview_plain,
        },
        site_name=site_name,
        title="New reply on your ticket",
        cta_url=case_url,
        cta_label="View ticket",
    )

    try:
        mail = EmailMultiAlternatives(
            subject=subject,
            body=strip_tags(html_content),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient_email],
        )
        mail.attach_alternative(html_content, "text/html")
        attach_logo(mail)
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
    Close tickets that have stayed Resolved for CHAT_AUTO_CLOSE_IDLE_DAYS
    (default 5). Closed tickets cannot be reopened.
    """
    from cases.models import CaseRecord
    from django.utils import timezone
    from datetime import timedelta

    idle_days = getattr(settings, "CHAT_AUTO_CLOSE_IDLE_DAYS", 5)
    cutoff = timezone.now() - timedelta(days=idle_days)

    idle_qs = CaseRecord.objects.filter(
        status=CaseRecord.Status.RESOLVED,
        updated_at__lte=cutoff,
    )
    idle_ids = list(idle_qs.values_list("id", flat=True))
    count = CaseRecord.objects.filter(id__in=idle_ids).update(status=CaseRecord.Status.CLOSED)
    if idle_ids:
        from gateways.tasks import queue_requester_status_email
        for case_id in idle_ids:
            queue_requester_status_email(case_id, CaseRecord.Status.CLOSED)
    logger.info("auto_close_idle_tickets: closed %d resolved tickets (idle_days=%s)", count, idle_days)
    return count
