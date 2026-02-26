"""
Gateways — Celery Tasks
=========================
Asynchronous webhook processing for Evolution API (WhatsApp).

The ``evolution_webhook`` view dispatches raw JSON payloads here
and returns HTTP 200 immediately.  All heavy lifting — Employee lookup,
CaseRecord creation/threading, media downloads — happens in the worker.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from celery import shared_task
from django.db import IntegrityError
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="gateways.process_evolution_webhook_task",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def process_evolution_webhook_task(self, payload: dict[str, Any]) -> str:
    """
    Process an Evolution API webhook payload asynchronously.

    Workflow:
    1. Parse sender phone number and message body.
    2. Deduplicate by ``external_id`` (message ID).
    3. Look up the Employee by E.164 phone number.
    4. **Session threading**: if the Employee has an active CaseRecord
       (Open / Investigating / Pending Info), append as a new Message.
       Otherwise, create a new CaseRecord.
    5. Download and save any attached media.

    Args:
        payload: Raw JSON dict from Evolution API webhook.

    Returns:
        A status string for logging/monitoring.
    """
    # Lazy imports to avoid circular references and ensure Django is ready
    from cases.models import Attachment, CaseCategory, CaseRecord, Message
    from core.models import Employee
    from gateways.services import EvolutionAPIService

    svc = EvolutionAPIService()

    try:
        # ---------------------------------------------------------
        # 1. Parse the payload
        # ---------------------------------------------------------
        sender_phone: Optional[str] = svc.extract_sender_phone(payload)
        message_body: str = svc.extract_message_body(payload)
        external_id: str = svc.extract_message_id(payload)
        media_info: Optional[dict] = svc.extract_media_info(payload)

        if not sender_phone:
            logger.info("Webhook ignored — no valid sender phone (group msg or status).")
            return "ignored:no_sender"

        if not message_body and not media_info:
            logger.info("Webhook ignored — empty message from %s.", sender_phone)
            return "ignored:empty_message"

        # ---------------------------------------------------------
        # 2. Deduplicate by external_id
        # ---------------------------------------------------------
        if external_id and Message.objects.filter(external_id=external_id).exists():
            logger.info(
                "Duplicate webhook skipped — external_id=%s already exists.",
                external_id,
            )
            return f"skipped:duplicate:{external_id}"

        # ---------------------------------------------------------
        # 3. Employee lookup
        # ---------------------------------------------------------
        try:
            employee: Employee = Employee.objects.get(phone_number=sender_phone)
        except Employee.DoesNotExist:
            logger.warning(
                "No registered Employee for phone %s. "
                "Message discarded.  Consider auto-registration.",
                sender_phone,
            )
            return f"discarded:unknown_employee:{sender_phone}"

        # ---------------------------------------------------------
        # 4. Session threading — only thread into a RECENT WhatsApp
        #    case from the same employee (within 30 min window).
        #    Each new WA message outside the window creates a new case.
        # ---------------------------------------------------------
        from datetime import timedelta

        session_window = timezone.now() - timedelta(minutes=30)
        active_case: Optional[CaseRecord] = (
            CaseRecord.objects.filter(
                requester=employee,
                source=CaseRecord.Source.EVOLUTION_WA,
                status__in=[
                    CaseRecord.Status.OPEN,
                    CaseRecord.Status.INVESTIGATING,
                    CaseRecord.Status.PENDING_INFO,
                ],
                created_at__gte=session_window,
            )
            .order_by("-created_at")
            .first()
        )

        is_new_case = False
        if active_case:
            case = active_case
            logger.info(
                "Threading WA message into recent case %s for %s.",
                case.case_number,
                employee.full_name,
            )
        else:
            # Auto-assign a default category — use first available or create one
            default_category = _get_or_create_default_category()

            case = CaseRecord.objects.create(
                requester=employee,
                category=default_category,
                subject=f"WhatsApp: {message_body[:80]}" if message_body else "WhatsApp media message",
                problem_description=message_body or "[Media attachment received]",
                status=CaseRecord.Status.OPEN,
                source=CaseRecord.Source.EVOLUTION_WA,
                requester_name=employee.full_name,
                requester_email=employee.email or "",
                requester_unit_name=employee.unit.name if employee.unit else "",
                requester_job_role=employee.job_role or "",
            )
            is_new_case = True
            logger.info(
                "Created new case %s from WhatsApp for %s.",
                case.case_number,
                employee.full_name,
            )

        # ---------------------------------------------------------
        # 5. Create Message record
        # ---------------------------------------------------------
        try:
            msg = Message.objects.create(
                case=case,
                sender_employee=employee,
                body=message_body or "[Media attachment]",
                direction=Message.Direction.INBOUND,
                channel=Message.Channel.WHATSAPP,
                external_id=external_id,
            )
        except IntegrityError:
            logger.warning(
                "IntegrityError creating message (possible race dedup) "
                "external_id=%s.",
                external_id,
            )
            return f"skipped:integrity_error:{external_id}"

        # ---------------------------------------------------------
        # 6. Download and save media attachment
        # ---------------------------------------------------------
        if media_info:
            _download_and_save_attachment(svc, msg, media_info)

        # ---------------------------------------------------------
        # 7. Auto-reply via WhatsApp when a NEW case is created
        # ---------------------------------------------------------
        if is_new_case and sender_phone:
            try:
                ack_text = (
                    f"✅ *Tiket Anda Telah Diterima*\n\n"
                    f"Halo *{employee.full_name}*,\n"
                    f"Terima kasih telah menghubungi *RoC Support Desk*.\n\n"
                    f"📋 *Nomor Tiket:* `{case.case_number}`\n"
                    f"📝 *Subjek:* {case.subject[:80]}\n\n"
                    f"Tim kami akan segera meninjau permintaan Anda.\n"
                    f"Anda dapat membalas pesan ini untuk menambahkan informasi terkait tiket.\n\n"
                    f"_Pesan otomatis — RoC Support Desk_"
                )
                svc.send_whatsapp_message(sender_phone, ack_text)
                logger.info(
                    "Sent WA acknowledgment for case %s to %s",
                    case.case_number, sender_phone,
                )
            except Exception as ack_exc:
                logger.warning(
                    "Failed to send WA acknowledgment for case %s: %s",
                    case.case_number, ack_exc,
                )

        return f"processed:case={case.case_number}:msg={msg.id}"

    except Exception as exc:
        logger.exception(
            "Unhandled error processing Evolution webhook: %s", exc,
        )
        # Retry with exponential backoff
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error(
                "Max retries exceeded for webhook payload. Giving up. "
                "Payload external_id: %s",
                payload.get("data", {}).get("key", {}).get("id", "unknown"),
            )
            return "error:max_retries_exceeded"
        return "error:retrying"


# =====================================================================
# Helper Functions
# =====================================================================

def _get_or_create_default_category():
    """
    Get or create a default CaseCategory for auto-generated WhatsApp cases.

    Returns:
        A ``CaseCategory`` instance.
    """
    from cases.models import CaseCategory

    category, created = CaseCategory.objects.get_or_create(
        slug="whatsapp-general",
        defaults={
            "name": "WhatsApp General Inquiry",
            "icon": "💬",
            "description": "Auto-created category for incoming WhatsApp messages.",
        },
    )
    if created:
        logger.info("Created default WhatsApp CaseCategory: %s", category.name)
    return category


def _download_and_save_attachment(
    svc,
    msg,
    media_info: dict,
) -> None:
    """
    Download media from Evolution API and save as an Attachment.

    Args:
        svc: EvolutionAPIService instance.
        msg: The parent Message object.
        media_info: Dict with keys ``media_url``, ``base64``,
                    ``mime_type``, ``filename``.
    """
    from cases.models import Attachment

    try:
        content_file = svc.download_media(
            media_url=media_info.get("media_url"),
            base64_data=media_info.get("base64"),
            mime_type=media_info.get("mime_type", "application/octet-stream"),
            filename=media_info.get("filename", "attachment"),
        )

        if content_file:
            Attachment.objects.create(
                message=msg,
                file=content_file,
                original_filename=media_info.get("filename", "attachment"),
                mime_type=media_info.get("mime_type", ""),
                file_size=content_file.size,
            )
            logger.info(
                "Saved attachment '%s' (%s, %d bytes) for message %s.",
                media_info.get("filename"),
                media_info.get("mime_type"),
                content_file.size,
                msg.id,
            )
        else:
            logger.warning(
                "Media download returned None for message %s. Skipping attachment.",
                msg.id,
            )
    except Exception as exc:
        logger.error(
            "Failed to save attachment for message %s: %s",
            msg.id,
            exc,
        )


@shared_task(
    name="gateways.poll_imap_emails_task",
    max_retries=1,
)
def poll_imap_emails_task() -> str:
    """
    Periodically poll the configured IMAP server for unread emails.
    Processes each email into a CaseRecord/Message.
    """
    from cases.models import Attachment, CaseRecord, Message
    from core.models import Employee
    from gateways.services import ImapEmailService
    from django.core.files.base import ContentFile
    import re

    svc = ImapEmailService()
    processed_count = 0

    try:
        # fetch_unread_emails yields dicts: {from, subject, text, html, attachments}
        for email_data in svc.fetch_unread_emails():
            sender_email = email_data.get("from", "")
            
            # Extract basic email if wrapped in Name <email>
            match = re.search(r"<(.+?)>", sender_email)
            if match:
                sender_email = match.group(1)
            sender_email = sender_email.strip().lower()

            subject = email_data.get("subject", "").strip()
            body_text = email_data.get("text", "").strip()
            if not body_text:
                body_text = email_data.get("html", "").strip() or "[Empty Email]"

            # 1. Lookup Employee
            try:
                employee = Employee.objects.get(email__iexact=sender_email)
            except Employee.DoesNotExist:
                logger.warning("Email from unknown sender %s discarded. Subject: %s", sender_email, subject)
                continue

            # 2. Threading — look for "CASE-XXXXXXXX" or "[CASE-XXXXXXXX]" in subject
            case = None
            is_new_case = False
            case_match = re.search(r"CS-([A-Fa-f0-9]{8})", subject)
            if case_match:
                case_prefix = case_match.group(1).lower()
                # Find case whose UUID starts with this prefix
                candidates = CaseRecord.objects.filter(requester=employee)
                for c in candidates:
                    if str(c.id).replace("-", "")[:8].lower() == case_prefix:
                        case = c
                        break

            if case:
                logger.info("Email threaded into existing case %s.", case.id)
            else:
                default_category = _get_or_create_default_email_category()
                case = CaseRecord.objects.create(
                    requester=employee,
                    category=default_category,
                    subject=subject[:500] or "Email Inquiry",
                    problem_description=body_text,
                    status=CaseRecord.Status.OPEN,
                    source=CaseRecord.Source.EMAIL,
                    requester_email=employee.email,
                    requester_name=employee.full_name,
                    requester_unit_name=employee.unit.name if employee.unit else "",
                    requester_job_role=employee.job_role,
                )
                is_new_case = True
                logger.info("Created new case %s from Email for %s.", case.id, employee.full_name)

            # 3. Create Message (store email Message-ID for threading)
            email_message_id = email_data.get("message_id", "")
            msg = Message.objects.create(
                case=case,
                sender_employee=employee,
                body=body_text,
                direction=Message.Direction.INBOUND,
                channel=Message.Channel.EMAIL,
                external_id=email_message_id or "",
            )

            # 4. Process Attachments
            for att in email_data.get("attachments", []):
                filename = att.get("filename", "attachment")
                content = att.get("content")
                mime_type = att.get("mime_type", "application/octet-stream")

                if content:
                    content_file = ContentFile(content, name=filename)
                    Attachment.objects.create(
                        message=msg,
                        file=content_file,
                        original_filename=filename,
                        mime_type=mime_type,
                        file_size=len(content),
                    )

            processed_count += 1

            # 5. Send auto-acknowledgment for new cases
            if is_new_case:
                try:
                    send_case_acknowledgment_task.delay(str(case.id))
                    logger.info("Dispatched acknowledgment email for case %s", case.id)
                except Exception as ack_exc:
                    logger.warning("Failed to dispatch ack email for case %s: %s", case.id, ack_exc)

        return f"processed_emails:{processed_count}"

    except Exception as exc:
        logger.exception("Error polling IMAP emails: %s", exc)
        return "error"


def _get_or_create_default_email_category():
    from cases.models import CaseCategory
    category, created = CaseCategory.objects.get_or_create(
        slug="email-general",
        defaults={
            "name": "Email General Inquiry",
            "icon": "📧",
            "description": "Auto-created category for incoming Email messages.",
        },
    )
    if created:
        logger.info("Created default Email CaseCategory: %s", category.name)
    return category

@shared_task(
    bind=True,
    name="gateways.send_outbound_email_task",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def send_outbound_email_task(self, message_id: str) -> str:
    """
    Sends an outbound email reply to the case requester based on a Message record.
    """
    from cases.models import Message
    from django.core.mail import EmailMultiAlternatives
    from django.conf import settings
    import html as html_mod

    try:
        msg = Message.objects.select_related("case", "case__requester").get(id=message_id)
        if msg.direction != Message.Direction.OUTBOUND or msg.channel != Message.Channel.EMAIL:
            return "ignored:not_outbound_email"

        case = msg.case
        requester = case.requester
        if not requester or not requester.email:
            logger.warning("Cannot send email for case %s: requester has no email", case.id)
            return "error:no_requester_email"

        case_number = case.case_number  # e.g. CASE-2A8E62EA
        subject = f"Re: [{case_number}] {case.subject}"

        # Plain text fallback
        plain_body = (
            f"Halo {requester.full_name},\n\n"
            f"{msg.body}\n\n"
            f"—\n"
            f"RoC Support Desk · {case_number}\n"
            f"Balas email ini untuk melanjutkan percakapan."
        )

        # HTML email
        safe_body = html_mod.escape(msg.body).replace("\n", "<br>")
        safe_name = html_mod.escape(requester.full_name)
        html_body = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">
    <tr><td align="center">
      <table width="580" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">

        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#1e293b,#334155);padding:20px 28px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <span style="color:#ffffff;font-size:18px;font-weight:700;letter-spacing:-0.3px;">🛠️ RoC Support Desk</span>
                </td>
                <td align="right">
                  <span style="background:rgba(255,255,255,0.15);color:#e2e8f0;font-size:12px;font-weight:600;padding:4px 10px;border-radius:6px;">{case_number}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:28px 28px 12px;">
            <p style="margin:0 0 16px;color:#475569;font-size:14px;line-height:1.5;">Halo <strong style="color:#1e293b;">{safe_name}</strong>,</p>
            <div style="background:#f8fafc;border-left:4px solid #6366f1;border-radius:0 8px 8px 0;padding:16px 20px;margin:0 0 20px;color:#334155;font-size:14px;line-height:1.65;">
              {safe_body}
            </div>
          </td>
        </tr>

        <!-- Divider -->
        <tr>
          <td style="padding:0 28px;">
            <hr style="border:none;border-top:1px solid #e2e8f0;margin:0;">
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:16px 28px 20px;">
            <p style="margin:0;color:#94a3b8;font-size:12px;line-height:1.5;">
              Balas email ini untuk melanjutkan percakapan terkait tiket <strong>{case_number}</strong>.<br>
              Pesan Anda akan otomatis masuk ke dalam sistem kami.
            </p>
          </td>
        </tr>

      </table>

      <!-- Sub-footer -->
      <p style="margin:16px 0 0;color:#94a3b8;font-size:11px;text-align:center;">
        © RoC Support Desk · Powered by JokoUI
      </p>
    </td></tr>
  </table>
</body>
</html>"""

        # Build a deterministic Message-ID for this case so all
        # emails about the same case form a thread.
        from_domain = settings.DEFAULT_FROM_EMAIL.split('@')[-1] if '@' in settings.DEFAULT_FROM_EMAIL else 'rocdesk.local'
        case_thread_id = f'<case-{case.id}@{from_domain}>'

        # Find the original inbound Message-ID (if stored)
        inbound_msg = (
            Message.objects.filter(
                case=case,
                channel=Message.Channel.EMAIL,
                direction=Message.Direction.INBOUND,
            )
            .exclude(external_id='')
            .order_by('sent_at')
            .first()
        )
        # Build References chain: original inbound + case thread ID
        ref_ids = []
        if inbound_msg and inbound_msg.external_id:
            ref_ids.append(inbound_msg.external_id)
        ref_ids.append(case_thread_id)
        references_str = ' '.join(ref_ids)
        reply_to_id = ref_ids[0]  # Reply to the first (original) message

        email = EmailMultiAlternatives(
            subject=subject,
            body=plain_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[requester.email],
            headers={
                'In-Reply-To': reply_to_id,
                'References': references_str,
                'Message-ID': f'<case-{case.id}-reply-{msg.id}@{from_domain}>',
            },
        )
        email.attach_alternative(html_body, 'text/html')
        email.send(fail_silently=False)

        logger.info("Sent outbound email for message %s to %s", msg.id, requester.email)
        return "success"

    except Message.DoesNotExist:
        logger.error("Message %s not found for outbound email", message_id)
        return "error:message_not_found"
    except Exception as exc:
        logger.exception("Failed to send outbound email for message %s: %s", message_id, exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"


@shared_task(
    bind=True,
    name="gateways.send_case_acknowledgment_task",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def send_case_acknowledgment_task(self, case_id: str) -> str:
    """
    Sends an auto-acknowledgment email when a new case is created from an
    inbound email. Includes the case number so the user can reference it
    and future replies are threaded automatically.
    """
    from cases.models import CaseRecord
    from django.core.mail import EmailMultiAlternatives
    from django.conf import settings
    import html as html_mod

    try:
        case = CaseRecord.objects.select_related("requester").get(id=case_id)
        requester = case.requester
        if not requester or not requester.email:
            return "skipped:no_requester_email"

        case_number = case.case_number  # e.g. CASE-E38BBD1F
        subject = f"[{case_number}] Tiket Anda Telah Diterima — {case.subject}"
        safe_name = html_mod.escape(requester.full_name)
        safe_subject = html_mod.escape(case.subject)

        plain_body = (
            f"Halo {requester.full_name},\n\n"
            f"Terima kasih telah menghubungi kami.\n\n"
            f"Permintaan Anda telah kami terima dan dicatat dengan nomor tiket:\n"
            f"📋 {case_number}\n\n"
            f"Subjek: {case.subject}\n\n"
            f"Tim kami akan segera meninjau dan menanggapi permintaan Anda.\n"
            f"Anda dapat membalas email ini untuk menambahkan informasi terkait tiket ini.\n\n"
            f"—\n"
            f"RoC Support Desk\n"
            f"Ini adalah email otomatis, mohon tidak menghapus nomor tiket pada subjek email."
        )

        html_body = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">
    <tr><td align="center">
      <table width="580" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">

        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#1e293b,#334155);padding:20px 28px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <span style="color:#ffffff;font-size:18px;font-weight:700;">🛠️ RoC Support Desk</span>
                </td>
                <td align="right">
                  <span style="background:rgba(255,255,255,0.15);color:#e2e8f0;font-size:12px;font-weight:600;padding:4px 10px;border-radius:6px;">{case_number}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:28px 28px 12px;">
            <p style="margin:0 0 16px;color:#475569;font-size:14px;line-height:1.5;">Halo <strong style="color:#1e293b;">{safe_name}</strong>,</p>
            <p style="margin:0 0 16px;color:#475569;font-size:14px;line-height:1.5;">Terima kasih telah menghubungi kami. Permintaan Anda telah kami terima dan dicatat dengan nomor tiket:</p>

            <!-- Ticket Number Badge -->
            <table cellpadding="0" cellspacing="0" style="margin:0 0 20px;">
              <tr>
                <td style="background:#f0fdf4;border:2px solid #86efac;border-radius:10px;padding:14px 24px;text-align:center;">
                  <span style="font-size:11px;color:#15803d;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Nomor Tiket Anda</span><br>
                  <span style="font-size:24px;font-weight:800;color:#166534;letter-spacing:1px;">{case_number}</span>
                </td>
              </tr>
            </table>

            <p style="margin:0 0 8px;color:#64748b;font-size:13px;"><strong>Subjek:</strong> {safe_subject}</p>
            <p style="margin:0 0 20px;color:#64748b;font-size:13px;">Tim kami akan segera meninjau dan menanggapi permintaan Anda.</p>
          </td>
        </tr>

        <!-- Divider -->
        <tr>
          <td style="padding:0 28px;">
            <hr style="border:none;border-top:1px solid #e2e8f0;margin:0;">
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:16px 28px 20px;">
            <p style="margin:0;color:#94a3b8;font-size:12px;line-height:1.5;">
              Anda dapat <strong>membalas email ini</strong> untuk menambahkan informasi terkait tiket.<br>
              Mohon <strong>tidak menghapus nomor tiket</strong> pada subjek email.
            </p>
          </td>
        </tr>

      </table>

      <p style="margin:16px 0 0;color:#94a3b8;font-size:11px;text-align:center;">
        © RoC Support Desk · Email otomatis, mohon tidak dibalas jika tidak diperlukan.
      </p>
    </td></tr>
  </table>
</body>
</html>"""

        # Deterministic Message-ID for threading
        from_domain = settings.DEFAULT_FROM_EMAIL.split('@')[-1] if '@' in settings.DEFAULT_FROM_EMAIL else 'rocdesk.local'
        case_thread_id = f'<case-{case.id}@{from_domain}>'

        # Find the original inbound Message-ID (if stored)
        inbound_msg = (
            Message.objects.filter(
                case=case,
                channel=Message.Channel.EMAIL,
                direction=Message.Direction.INBOUND,
            )
            .exclude(external_id='')
            .order_by('sent_at')
            .first()
        )
        ref_ids = []
        if inbound_msg and inbound_msg.external_id:
            ref_ids.append(inbound_msg.external_id)
        ref_ids.append(case_thread_id)
        references_str = ' '.join(ref_ids)
        reply_to_id = ref_ids[0]

        email = EmailMultiAlternatives(
            subject=subject,
            body=plain_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[requester.email],
            headers={
                'In-Reply-To': reply_to_id,
                'References': references_str,
                'Message-ID': f'<case-{case.id}-ack@{from_domain}>',
            },
        )
        email.attach_alternative(html_body, 'text/html')
        email.send(fail_silently=False)

        logger.info("Sent acknowledgment email for case %s to %s", case.id, requester.email)
        return "success"

    except CaseRecord.DoesNotExist:
        logger.error("Case %s not found for acknowledgment email", case_id)
        return "error:case_not_found"
    except Exception as exc:
        logger.exception("Failed to send acknowledgment email for case %s: %s", case_id, exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"
