"""Knowledge Management: tell the approvers (Manager / SuperAdmin) that an article waits for review,
by e-mail and by a personal Teams card, both linking straight to the article's approval bar."""
import logging

from celery import shared_task
from django.conf import settings
from django.urls import reverse

logger = logging.getLogger(__name__)


def approvers():
    """Active Manager / SuperAdmin accounts with an e-mail and access to Knowledge Management."""
    from core.models import User

    people = User.objects.filter(
        is_active=True, role_access__in=[User.RoleAccess.MANAGER, User.RoleAccess.SUPERADMIN],
    ).exclude(email="").exclude(email__isnull=True)
    return [u for u in people if "knowledge_management" in u.get_accessible_modules()]


def review_url(article):
    return settings.SITE_URL.rstrip("/") + reverse("kb_desk:article_edit", args=[article.pk])


def notify_review_requested(article, submitted_by):
    """Queue one e-mail and one Teams card per approver (never the submitter)."""
    for user in approvers():
        if user.pk == submitted_by.pk:
            continue
        send_review_email_task.delay(str(article.pk), str(user.pk), submitted_by.get_full_name() or submitted_by.username)
        _queue_teams_card(article, user, submitted_by)


@shared_task(bind=True, max_retries=2, default_retry_delay=60, name="knowledge_base.send_review_email_task")
def send_review_email_task(self, article_id, user_id, submitted_by_name):
    from django.core.mail import EmailMultiAlternatives
    from django.utils.html import strip_tags

    from core.models import User
    from core.notifications import _get_from_email, _get_site_name
    from core.utils.email_branding import attach_logo, render_branded_from_template

    from .models import Article

    article = Article.objects.filter(pk=article_id).select_related("category").first()
    user = User.objects.filter(pk=user_id).first()
    if article is None or user is None or not user.email:
        return "Skipped"
    site = _get_site_name()
    preview = f"{submitted_by_name} submitted \"{article.title}\" for review."
    if article.category_id:
        preview += f"\nCategory: {article.category.name}"
    html_body = render_branded_from_template(
        "emails/bodies/notification.html",
        {"description": "", "message_preview": preview},
        site_name=site,
        title="Knowledge article waiting for your approval",
        eyebrow=f"{site} · Knowledge Management",
        cta_url=review_url(article),
        cta_label="Review & approve",
        footer_note="You get this because you can approve Knowledge Management articles.",
    )
    email = EmailMultiAlternatives(
        subject=f"[{site}] Approval needed: {article.title}", body=strip_tags(html_body),
        from_email=_get_from_email(), to=[user.email],
    )
    email.attach_alternative(html_body, "text/html")
    attach_logo(email)
    try:
        email.send(fail_silently=False)
    except Exception as exc:
        logger.error("KB review e-mail to %s failed: %s", user.email, exc)
        raise self.retry(exc=exc)
    return f"Sent to {user.email}"


def _queue_teams_card(article, user, submitted_by):
    from core.models import UserNotificationPreference
    from gateways.teams_notify import allowed_teams_email, personal_enabled, post_teams_payload_task

    email = (user.email or "").strip().lower()
    if not (email and personal_enabled() and allowed_teams_email(email)):
        return False
    pref = UserNotificationPreference.objects.filter(user=user).first()
    if pref and (not pref.teams_enabled or pref.is_in_quiet_hours()):
        return False
    facts = [{"title": "Article", "value": article.title},
             {"title": "Submitted by", "value": submitted_by.get_full_name() or submitted_by.username}]
    if article.category_id:
        facts.append({"title": "Category", "value": article.category.name})
    payload = {
        "recipient": email,
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "contentUrl": None,
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "msteams": {"width": "Full"},
                "body": [
                    {"type": "TextBlock", "text": "📑 Knowledge article waiting for your approval", "weight": "Bolder", "size": "Medium", "wrap": True},
                    {"type": "FactSet", "facts": facts},
                ],
                "actions": [{"type": "Action.OpenUrl", "title": "Review & approve", "url": review_url(article)}],
            },
        }],
    }
    post_teams_payload_task.delay(payload, label=f"kb-review:{article.pk}", target="personal")
    return True
