import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def send_announcement_email_task(self, announcement_id, user_id):
    from core.models import User
    from core.notifications import _get_from_email, _get_site_name
    from core.utils.email_branding import attach_logo, render_branded_from_template

    from .models import Announcement

    announcement = Announcement.objects.filter(pk=announcement_id).first()
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if announcement is None or user is None or not user.email:
        return "Skipped"

    site = _get_site_name()
    html_body = render_branded_from_template(
        "emails/bodies/notification.html",
        {"description": "", "message_preview": announcement.summary or strip_tags(announcement.body)[:300]},
        site_name=site,
        title=announcement.title,
        eyebrow=f"{site} · {announcement.get_source_type_display()} announcement",
        cta_url=f"{settings.SITE_URL.rstrip('/')}/announcements/{announcement.pk}/",
        cta_label="Read announcement",
        footer_note="You are receiving this because announcements are sent to your entity.",
    )
    email = EmailMultiAlternatives(
        subject=f"[{site}] {announcement.title}",
        body=strip_tags(html_body),
        from_email=_get_from_email(),
        to=[user.email],
    )
    email.attach_alternative(html_body, "text/html")
    attach_logo(email)
    try:
        email.send(fail_silently=False)
    except Exception as exc:
        logger.error("Announcement e-mail failed for %s: %s", user.email, exc)
        raise self.retry(exc=exc)
    return f"Sent to {user.email}"
