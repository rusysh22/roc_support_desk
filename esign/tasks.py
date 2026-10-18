"""
E-Signature App — Celery Tasks
================================
All email notifications for the e-sign workflow are dispatched from here.
Pattern: EmailMultiAlternatives with an explicit SMTP connection pulled from
EmailConfig, matching the convention in core/tasks.py.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


def _get_email_connection():
    """Return an SMTP connection using EmailConfig (same pattern as core/tasks.py)."""
    from core.models import EmailConfig
    cfg = EmailConfig.get_solo()
    return (
        get_connection(
            host=cfg.smtp_host,
            port=cfg.smtp_port,
            username=cfg.smtp_user,
            password=cfg.smtp_password,
            use_tls=cfg.smtp_use_tls,
            use_ssl=cfg.smtp_use_ssl,
            timeout=30,
        ),
        cfg.default_from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@rocdesk.local"),
    )


def _send(subject, html, to_email, attachment=None):
    """Send a single HTML email. Raises on failure (caller handles retry)."""
    from core.utils.email_branding import attach_logo

    connection, from_email = _get_email_connection()
    text = strip_tags(html)
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text,
        from_email=from_email,
        to=[to_email],
        connection=connection,
    )
    msg.attach_alternative(html, "text/html")
    attach_logo(msg)
    if attachment:
        filename, content, mime = attachment
        msg.attach(filename, content, mime)
    msg.send(fail_silently=False)


def _email_wrapper(site_name, body_html, *, title="E-signature", cta_url="", cta_label="Open document"):
    from core.utils.email_branding import render_branded_email

    return render_branded_email(
        site_name=site_name,
        title=title,
        body_html=body_html,
        eyebrow=f"{site_name} · E-Signature",
        cta_url=cta_url,
        cta_label=cta_label,
        footer_note="Automated e-signature notification. Do not reply to this email.",
    )


# ---------------------------------------------------------------------------
# send_signature_request_task
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="esign.send_signature_request_task",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def send_signature_request_task(self, signer_id: str, reminder: bool = False) -> str:
    from .models import Signer
    from core.models import SiteConfig

    try:
        signer   = Signer.objects.select_related("document", "user").get(pk=signer_id)
        document = signer.document
        site_cfg = SiteConfig.get_solo()
        site_name = site_cfg.site_name or "Support Desk"

        sign_url = _build_absolute_url(reverse("esign_public:sign", args=[signer.token]))

        due_note = ""
        if document.due_at:
            from cases.utils_pdf import _format_dt_with_gmt
            due_note = f"<p><strong>Due date:</strong> {_format_dt_with_gmt(document.due_at)}</p>"

        msg_note = f"<blockquote style='border-left:3px solid #6366f1;margin:0 0 14px;padding:10px 14px;color:#475569;background:#f4f8fa'>{document.message}</blockquote>" if document.message else ""

        verify_section = ""
        if not signer.user_id and signer.verify_code:
            verify_section = f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fff7ed;border:1px solid #fed7aa;margin:0 0 14px;">
  <tr>
    <td style="padding:16px;text-align:center;">
      <p style="margin:0 0 6px;font-size:12px;font-weight:600;color:#c2410c;">Verification code</p>
      <p style="margin:0;font-size:28px;font-weight:700;letter-spacing:0.16em;color:#312e81;font-family:ui-monospace,monospace;">{signer.verify_code}</p>
      <p style="margin:10px 0 0;font-size:12px;color:#7c2d12;">Enter your email and this code when you open the signing link.</p>
    </td>
  </tr>
</table>
"""

        body = f"""
<p style="margin:0 0 14px;font-size:15px;color:#475569;line-height:1.6;">Hello <span style="color:#0f172a;font-weight:500;">{signer.display_name}</span>,</p>
<p style="margin:0 0 14px;font-size:15px;color:#475569;line-height:1.6;">{"This is a friendly reminder: the following document is still waiting for your signature:" if reminder else "You have been requested to sign the following document:"}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f8fa;border:1px solid #dbe7ec;margin:0 0 14px;">
  <tr><td style="padding:14px 16px;font-size:15px;color:#0f172a;font-weight:600;">{document.title}</td></tr>
</table>
{msg_note}
{due_note}
{verify_section}
<p style="margin:0;font-size:12px;color:#64748b;line-height:1.5;">Or copy this link:<br><code>{sign_url}</code></p>
<p style="margin:14px 0 0;font-size:13px;color:#64748b;">If you are not the intended recipient, please disregard this email.</p>
"""
        html = _email_wrapper(
            site_name,
            body,
            title="Signature reminder" if reminder else "Signature request",
            cta_url=sign_url,
            cta_label="Sign document",
        )
        _send(
            subject=f"[{site_name}] {'Reminder: signature needed' if reminder else 'Signature Request'} — {document.title}",
            html=html,
            to_email=signer.email,
        )
        logger.info("Signature request sent to %s for document %s", signer.email, document.pk)
        return f"sent:{signer.email}"

    except Exception as exc:
        logger.exception("Failed to send signature request for signer %s: %s", signer_id, exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# send_signature_rejected_task
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="esign.send_signature_rejected_task",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def send_signature_rejected_task(self, document_id: str, signer_id: str) -> str:
    from .models import SignatureDocument, Signer
    from core.models import SiteConfig

    try:
        document = SignatureDocument.objects.select_related("created_by").get(pk=document_id)
        signer   = Signer.objects.get(pk=signer_id)
        owner    = document.created_by
        if not owner or not owner.email:
            return "no_owner_email"

        site_cfg  = SiteConfig.get_solo()
        site_name = site_cfg.site_name or "Support Desk"
        detail_url = _build_absolute_url(reverse("esign:document_detail", args=[document.pk]))

        body = f"""
<p style="margin:0 0 14px;font-size:15px;color:#475569;line-height:1.6;">Hello <span style="color:#0f172a;font-weight:500;">{owner.get_full_name() or owner.username}</span>,</p>
<p style="margin:0 0 14px;font-size:15px;color:#475569;line-height:1.6;">Document <span style="color:#0f172a;font-weight:600;">{document.title}</span> has been <span style="color:#b91c1c;font-weight:600">rejected</span> by <span style="color:#0f172a;font-weight:500;">{signer.display_name}</span>.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fff7ed;border:1px solid #fed7aa;margin:0 0 14px;">
  <tr>
    <td style="padding:14px 16px;">
      <p style="margin:0 0 6px;font-size:12px;color:#c2410c;font-weight:600;">Rejection reason</p>
      <p style="margin:0;font-size:14px;color:#7c2d12;line-height:1.5;">{signer.notes or '—'}</p>
    </td>
  </tr>
</table>
<p style="margin:0;font-size:14px;color:#475569;line-height:1.55;">You can reopen the document and resend it after making the necessary changes.</p>
"""
        html = _email_wrapper(
            site_name,
            body,
            title="Document rejected",
            cta_url=detail_url,
            cta_label="View document",
        )
        _send(
            subject=f"[{site_name}] Document Rejected — {document.title}",
            html=html,
            to_email=owner.email,
        )
        return f"sent:{owner.email}"

    except Exception as exc:
        logger.exception("Failed to send rejection notification for document %s: %s", document_id, exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# send_signature_completed_task
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="esign.send_signature_completed_task",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def send_signature_completed_task(self, document_id: str) -> str:
    from .models import SignatureDocument
    from core.models import SiteConfig

    try:
        document  = SignatureDocument.objects.select_related("created_by").prefetch_related("signers").get(pk=document_id)
        site_cfg  = SiteConfig.get_solo()
        site_name = site_cfg.site_name or "Support Desk"

        # Read signed PDF bytes for attachment
        pdf_attachment = None
        if document.signed_pdf:
            try:
                document.signed_pdf.seek(0)
                pdf_bytes = document.signed_pdf.read()
                safe_title = document.title.replace(" ", "_")[:60]
                pdf_attachment = (f"{safe_title}_signed.pdf", pdf_bytes, "application/pdf")
            except Exception:
                pass

        recipients = set()
        if document.created_by and document.created_by.email:
            recipients.add(document.created_by.email)
        for signer in document.signers.all():
            if signer.email:
                recipients.add(signer.email)

        body = f"""
<p style="margin:0 0 14px;font-size:15px;color:#475569;line-height:1.6;">Document <span style="color:#0f172a;font-weight:600;">{document.title}</span> has been fully signed by all parties.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f0fdf4;border:1px solid #bbf7d0;margin:0 0 14px;">
  <tr>
    <td style="padding:14px 16px;font-size:14px;color:#166534;line-height:1.5;font-weight:500;">All signatures collected — this document is now valid. The signed PDF is attached.</td>
  </tr>
</table>
"""
        html = _email_wrapper(site_name, body, title="Signing completed")
        subject = f"[{site_name}] Document Signing Completed — {document.title}"

        for email_addr in recipients:
            try:
                _send(subject=subject, html=html, to_email=email_addr, attachment=pdf_attachment)
            except Exception:
                logger.warning("Could not send completion email to %s", email_addr)

        return f"sent_to:{len(recipients)}_recipients"

    except Exception as exc:
        logger.exception("Failed to send completion notification for document %s: %s", document_id, exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# send_contributor_update_task
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="esign.send_contributor_update_task",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def send_contributor_update_task(self, document_id: str, user_ids: list, event_label: str, actor: str, detail: str = "") -> str:
    """Tell the document's contributors what just happened to it."""
    from django.utils.html import escape

    from core.models import SiteConfig, User
    from .models import SignatureDocument

    try:
        document = SignatureDocument.objects.get(pk=document_id)
        site_name = SiteConfig.get_solo().site_name or "Support Desk"
        detail_url = _build_absolute_url(reverse("esign:document_detail", args=[document.pk]))
        detail_html = (
            f'<p style="margin:0 0 14px;font-size:14px;color:#475569;line-height:1.5;">{escape(detail)}</p>' if detail else ""
        )
        body = f"""
<p style="margin:0 0 14px;font-size:15px;color:#475569;line-height:1.6;">An update on a document you follow as a contributor:</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f8fa;border:1px solid #dbe7ec;margin:0 0 14px;">
  <tr><td style="padding:14px 16px;">
    <p style="margin:0 0 4px;font-size:15px;color:#0f172a;font-weight:600;">{escape(document.title)}</p>
    <p style="margin:0;font-size:13px;color:#475569;"><strong>{escape(event_label)}</strong> &middot; by {escape(actor)}
       &middot; {document.signed_count}/{document.total_count} signed</p>
  </td></tr>
</table>
{detail_html}
"""
        html = _email_wrapper(site_name, body, title=f"Document update: {event_label}", cta_url=detail_url, cta_label="View document")
        subject = f"[{site_name}] {event_label} — {document.title}"
        sent = 0
        for user in User.objects.filter(pk__in=user_ids, is_active=True).exclude(email=""):
            try:
                _send(subject=subject, html=html, to_email=user.email)
                sent += 1
            except Exception:
                logger.warning("Could not send contributor update to %s", user.email)
        return f"sent_to:{sent}"
    except Exception as exc:
        logger.exception("Failed to send contributor update for document %s: %s", document_id, exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Automatic reminders (every ESIGN_AUTO_REMIND_HOURS, default 24 h)
# ---------------------------------------------------------------------------

def queue_signature_reminder_teams(signer, pref=None) -> bool:
    """Queue a personal Teams card (chat with the Workflows bot) for an internal signer. True if queued."""
    from gateways.teams_notify import allowed_teams_email, personal_enabled, post_teams_payload_task

    email = (signer.user.email or "").strip().lower()
    if not (email and personal_enabled() and allowed_teams_email(email)):
        return False
    if pref and (not pref.teams_enabled or pref.is_in_quiet_hours()):
        return False
    document = signer.document
    sign_url = _build_absolute_url(reverse("esign_public:sign", args=[signer.token]))
    facts = [{"title": "Document", "value": document.title}]
    if document.due_at:
        from cases.utils_pdf import _format_dt_with_gmt
        facts.append({"title": "Due", "value": _format_dt_with_gmt(document.due_at)})
    if signer.last_requested_at:
        hours = int((timezone.now() - signer.last_requested_at).total_seconds() // 3600)
        facts.append({"title": "Waiting", "value": f"{hours // 24} day(s)" if hours >= 24 else f"{hours} h"})
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
                    {"type": "TextBlock", "text": "✒️ Signature reminder", "weight": "Bolder", "size": "Medium", "wrap": True},
                    {"type": "TextBlock", "text": "A document is still waiting for your signature.", "wrap": True, "spacing": "Small"},
                    {"type": "FactSet", "facts": facts},
                ],
                "actions": [{"type": "Action.OpenUrl", "title": "Sign document", "url": sign_url}],
            },
        }],
    }
    post_teams_payload_task.delay(payload, label=f"esign-reminder:{signer.pk}", target="personal")
    return True


@shared_task(name="esign.send_auto_reminders_task")
def send_auto_reminders_task() -> str:
    """Hourly: remind every internal signer who has been pending for ESIGN_AUTO_REMIND_HOURS."""
    from .services import auto_remind_signer, signers_due_for_auto_reminder

    sent = 0
    for signer in signers_due_for_auto_reminder():
        try:
            if auto_remind_signer(signer):
                sent += 1
        except Exception:
            logger.exception("Automatic e-sign reminder failed for signer %s", signer.pk)
    logger.info("E-sign automatic reminders sent: %s", sent)
    return f"reminded:{sent}"


def _build_absolute_url(path: str) -> str:
    """Build a full absolute URL using SiteConfig, falling back to SITE_URL from settings."""
    from core.models import SiteConfig
    cfg = SiteConfig.get_solo()
    base = (cfg.site_url or getattr(settings, "SITE_URL", "")).rstrip("/")
    if not base:
        base = "http://localhost:8000"
    return f"{base}{path}"
