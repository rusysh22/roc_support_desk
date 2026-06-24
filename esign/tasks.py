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
    if attachment:
        filename, content, mime = attachment
        msg.attach(filename, content, mime)
    msg.send(fail_silently=False)


def _email_wrapper(site_name, body_html):
    """Wrap body in a standard branded container."""
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;line-height:1.6;color:#333;background:#f1f5f9;padding:20px}}
  .wrap{{max-width:600px;margin:0 auto}}
  .hdr{{background:#1e3a5f;padding:20px;border-radius:8px 8px 0 0;text-align:center;color:#fff}}
  .hdr h2{{margin:0;font-size:18px}}
  .bdy{{background:#fff;padding:28px 24px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 8px 8px}}
  .btn{{display:inline-block;background:#4f46e5;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:600;margin:16px 0}}
  .ftr{{margin-top:24px;font-size:11px;color:#94a3b8;text-align:center}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hdr"><h2>{site_name}</h2></div>
  <div class="bdy">{body_html}</div>
  <div class="ftr">This is an automated message — please do not reply directly.</div>
</div>
</body>
</html>"""


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
def send_signature_request_task(self, signer_id: str) -> str:
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

        msg_note = f"<blockquote style='border-left:3px solid #e2e8f0;margin:12px 0;padding:8px 16px;color:#555'>{document.message}</blockquote>" if document.message else ""

        # External signers see a verification code block; system users do not.
        verify_section = ""
        if not signer.user_id and signer.verify_code:
            verify_section = f"""
<div style="background:#fffbeb;border:2px solid #f59e0b;border-radius:10px;padding:18px 20px;margin:20px 0;text-align:center">
  <p style="margin:0 0 6px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#92400e">Verification Code</p>
  <p style="margin:0;font-size:30px;font-weight:800;letter-spacing:0.2em;color:#1e3a5f;font-family:ui-monospace,monospace">{signer.verify_code}</p>
  <p style="margin:10px 0 0;font-size:11px;color:#78350f">You will be asked to enter your email address and this code when you open the signing link.</p>
</div>
"""

        body = f"""
<p>Hello <strong>{signer.display_name}</strong>,</p>
<p>You have been requested to sign the following document:</p>
<p style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:12px">
  <strong>{document.title}</strong>
</p>
{msg_note}
{due_note}
{verify_section}
<p>Click the button below to open the signing page:</p>
<a href="{sign_url}" class="btn">Sign Document Now</a>
<p style="font-size:12px;color:#64748b">Or copy this link: <br><code>{sign_url}</code></p>
<p>If you are not the intended recipient, please disregard this email.</p>
"""
        html = _email_wrapper(site_name, body)
        _send(
            subject=f"[{site_name}] Signature Request — {document.title}",
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
<p>Hello <strong>{owner.get_full_name() or owner.username}</strong>,</p>
<p>Document <strong>{document.title}</strong> has been <span style="color:#dc2626;font-weight:600">rejected</span>
by <strong>{signer.display_name}</strong>.</p>
<p><strong>Rejection reason:</strong><br>
<span style="background:#fef2f2;border-left:3px solid #dc2626;display:block;padding:8px 14px;margin:8px 0;border-radius:4px">
{signer.notes or '—'}
</span></p>
<p>You can reopen the document and resend it after making the necessary changes.</p>
<a href="{detail_url}" class="btn">View Document</a>
"""
        html = _email_wrapper(site_name, body)
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
<p>Document <strong>{document.title}</strong> has been fully signed by all parties.</p>
<p style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:12px;color:#166534">
  ✅ All signatures collected — this document is now officially valid.
</p>
<p>The signed document is attached to this email.</p>
"""
        html = _email_wrapper(site_name, body)
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
# Utility
# ---------------------------------------------------------------------------

def _build_absolute_url(path: str) -> str:
    """Build a full absolute URL using SiteConfig, falling back to SITE_URL from settings."""
    from core.models import SiteConfig
    cfg = SiteConfig.get_solo()
    base = (cfg.site_url or getattr(settings, "SITE_URL", "")).rstrip("/")
    if not base:
        base = "http://localhost:8000"
    return f"{base}{path}"
