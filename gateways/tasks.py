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
        from gateways.parsers import parse_evolution_webhook
        
        parsed_data = parse_evolution_webhook(payload)
        if not parsed_data:
            # The parser already logs the specific ignore reason
            return "ignored:parsed_none"

        sender_phone: str = parsed_data["sender_number"]
        sender_name: Optional[str] = parsed_data["sender_name"]
        message_body: str = parsed_data["message_text"] or ""
        external_id: str = parsed_data["message_id"]
        media_info: Optional[dict] = parsed_data["media"]
        quoted_id: Optional[str] = parsed_data.get("quoted_id")

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
            default_unit = _get_or_create_external_unit()
            # If we know their WhatsApp PushName, use it. Otherwise "WA User +62..."
            display_name = sender_name if sender_name else f"WA User {sender_phone}"
            employee = Employee.objects.create(
                phone_number=sender_phone,
                full_name=display_name,
                unit=default_unit,
                job_role="WhatsApp User"
            )
            logger.info("Auto-registered new Employee from WA: %s (%s)", sender_phone, display_name)

        # ---------------------------------------------------------
        # 4. Session threading
        #    a) Check if the user is quoting a previous message we sent
        #    b) Otherwise, thread into a RECENT WhatsApp case
        # ---------------------------------------------------------
        from datetime import timedelta
        
        active_case: Optional[CaseRecord] = None
        # quoted_id is already extracted via the parser
        
        if quoted_id:
            orig_msg = Message.objects.filter(external_id=quoted_id).first()
            if orig_msg:
                active_case = orig_msg.case
                logger.info("Threaded WA reply via quoted message %s to case %s.", quoted_id, active_case.case_number)
        
        if not active_case:
            from django.db.models import Q
            session_window = timezone.now() - timedelta(minutes=30)
            
            # Fallback A: Did we recently send an outbound message to this user's phone?
            # Escalations include the phone number in the body, e.g., "*** ESKALASI TIKET VIA WHATSAPP KE: 628... ***"
            clean_phone = sender_phone.lstrip('+') if sender_phone else ""
            if clean_phone:
                last_outbound = Message.objects.filter(
                    direction=Message.Direction.OUTBOUND,
                    channel=Message.Channel.WHATSAPP,
                    body__contains=clean_phone,
                    sent_at__gte=session_window
                ).order_by("-sent_at").first()
                if last_outbound:
                    active_case = last_outbound.case
                    logger.info("Threaded WA reply via recent outbound msg match to case %s.", active_case.case_number)

        if not active_case:
            # Fallback B: If they are the requester on a recent WA case
            session_window = timezone.now() - timedelta(minutes=60) # 30 mins for session
            active_case = (
                CaseRecord.objects.filter(
                    requester=employee,
                    source=CaseRecord.Source.EVOLUTION_WA,
                    status__in=[
                        CaseRecord.Status.OPEN,
                        CaseRecord.Status.INVESTIGATING,
                        CaseRecord.Status.PENDING_INFO,
                    ],
                    updated_at__gte=session_window,
                )
                .order_by("-updated_at")
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
            # Check for Spam (Rate Limiting)
            # e.g., > 3 new cases in the last 30 minutes from this employee
            recent_cases_count = CaseRecord.objects.filter(
                requester=employee,
                source=CaseRecord.Source.EVOLUTION_WA,
                created_at__gte=timezone.now() - timedelta(minutes=30)
            ).count()
            is_spam = recent_cases_count >= 3

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
                is_spam=is_spam,
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
            
        case.has_unread_messages = True
        case.save(update_fields=["has_unread_messages"])

        # ---------------------------------------------------------
        # 6. Download and save media attachment
        # ---------------------------------------------------------
        if media_info:
            _download_and_save_attachment(svc, msg, media_info)

        # ---------------------------------------------------------
        # 7. Auto-reply via WhatsApp when a NEW case is created (Skip if SPAM)
        # ---------------------------------------------------------
        if is_new_case and sender_phone and not case.is_spam:
            from core.models import SiteConfig
            import random
            import time
            
            site_config = SiteConfig.get_solo()
            site_name = getattr(site_config, 'site_name', 'Support Desk')
            
            try:
                # Spintax for greetings to avoid exact identical messages that trigger spam filters
                greetings = ["Hello", "Hi", "Greetings", "Welcome", "Dear"]
                random_greeting = random.choice(greetings)
                
                # Spintax for closings to replace "Automated Message"
                closings = [
                    f"_{site_name} Support Team_",
                    f"_{site_name} Site_",
                    f"_- {site_name}_",
                    f"_Best regards, {site_name}_",
                    f"_Thanks, {site_name}_"
                ]
                random_closing = random.choice(closings)
                
                ack_text = (
                    f"✅ *Request Received*\n\n"
                    f"{random_greeting} *{employee.full_name}*,\n"
                    f"Thank you for contacting *{site_name}*.\n\n"
                    f"📋 *Ticket Number:* `{case.case_number}`\n"
                    f"📝 *Subject:* {case.subject[:80]}\n\n"
                    f"Our team will review your request shortly.\n"
                    f"You may reply to this message to add any additional information regarding your ticket.\n\n"
                    f"{random_closing}"
                )
                
                # Human-like delay: Random pause between 3 - 8 seconds before sending
                sleep_duration = random.randint(3, 8)
                logger.info("Applying human-like auto-reply delay of %s seconds for %s", sleep_duration, sender_phone)
                
                # Send 'composing' presence to simulate human typing
                svc.send_presence(sender_phone, presence="composing", delay=sleep_duration * 1000)
                
                time.sleep(sleep_duration)
                
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

def _get_or_create_external_unit():
    from core.models import CompanyUnit
    unit, created = CompanyUnit.objects.get_or_create(
        code="NON-ID",
        defaults={
            "name": "Not Identified Unit Company",
        },
    )
    if created:
        logger.info("Created default CompanyUnit: %s", unit.name)
    return unit


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
        media_info: Dict with keys ``message_id``,
                    ``mime_type``, ``filename``.
    """
    from cases.models import Attachment

    try:
        content_file = svc.download_media(
            message_id=media_info.get("message_id"),
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
    from django.utils import timezone
    from datetime import timedelta
    import re

    svc = ImapEmailService()
    processed_count = 0

    try:
        # fetch_unread_emails yields dicts: {from, subject, text, html, attachments}
        for email_data in svc.fetch_unread_emails():
            sender_email = email_data.get("from", "")
            
            # Extract basic email if wrapped in Name <email>
            display_name = sender_email
            match = re.search(r"<(.+?)>", sender_email)
            if match:
                display_name = sender_email.split("<")[0].strip().strip('"') or match.group(1)
                sender_email = match.group(1)
            sender_email = sender_email.strip().lower()

            subject = email_data.get("subject", "").strip()
            body_text = email_data.get("text", "").strip()
            if not body_text:
                body_text = email_data.get("html", "").strip() or "[Empty Email]"

            # -----------------------------------------------------------------
            # 0. Anti-Spam (Loop Prevention)
            # Check for Auto-Submitted headers to prevent auto-responder loops
            # -----------------------------------------------------------------
            auto_submitted = email_data.get("auto_submitted", "").lower()
            x_auto_response = email_data.get("x_auto_response_suppress", "").lower()
            
            # If it's an auto-reply or bounce message from another system, skip entirely
            if ("auto-generated" in auto_submitted or "auto-replied" in auto_submitted or 
                x_auto_response == "all" or x_auto_response == "rn"):
                logger.info("Skipped email from %s due to auto-responder header.", sender_email)
                continue

            # 1. Lookup Employee
            try:
                employee = Employee.objects.get(email__iexact=sender_email)
            except Employee.DoesNotExist:
                default_unit = _get_or_create_external_unit()
                employee = Employee.objects.create(
                    email=sender_email,
                    full_name=display_name,
                    unit=default_unit,
                    job_role="Email User"
                )
                logger.info("Auto-registered new Employee from email: %s", sender_email)

            # 2. Threading — match existing case from reply email
            # Strategy A: Match [XX-XXXXXXXX] ticket number in subject line
            #   Outbound emails include the case_number in the subject, e.g.:
            #   "[AV-DF938403] Minta Testing" → Re: [AV-DF938403] Minta Testing
            case = None
            is_new_case = False

            case_match = re.search(
                r"\[([A-Z]{2}-[A-Fa-f0-9]{8})\]",  # e.g. [AV-DF938403]
                subject,
                re.IGNORECASE
            )
            if case_match:
                # Extract the 8-hex UUID prefix that follows the dash
                uuid_prefix = case_match.group(1).split("-")[1].lower()
                case = CaseRecord.objects.filter(id__istartswith=uuid_prefix).first()
                if case:
                    logger.info(
                        "Email threaded into case %s via subject ticket ID '%s'.",
                        case.id, case_match.group(1)
                    )

            # Strategy B: Match via In-Reply-To / References email header
            # If the original outbound email's Message-ID was stored in Message.external_id,
            # we can find the case via that ID.
            if not case:
                in_reply_to = email_data.get("in_reply_to", "").strip()
                references = email_data.get("references", "").strip()
                reply_ids = [mid.strip("<>") for mid in (in_reply_to + " " + references).split() if mid]
                if reply_ids:
                    from cases.models import Message as CaseMessage
                    matched_msg = CaseMessage.objects.filter(
                        external_id__in=reply_ids
                    ).select_related("case").first()
                    if matched_msg:
                        case = matched_msg.case
                        logger.info(
                            "Email threaded into case %s via In-Reply-To header.",
                            case.id
                        )

            if not case:
                # Check for Spam (Rate Limiting)
                # e.g., > 3 new cases in the last 10 minutes from this email
                recent_cases_count = CaseRecord.objects.filter(
                    requester=employee,
                    source=CaseRecord.Source.EMAIL,
                    created_at__gte=timezone.now() - timedelta(minutes=10)
                ).count()
                is_spam = recent_cases_count >= 3

                default_category = _get_or_create_default_email_category()
                
                # Determine Priority from email headers
                importance = str(email_data.get("importance", "")).lower()
                x_priority = str(email_data.get("x_priority", "")).lower()
                
                priority = CaseRecord.Priority.MEDIUM
                if "high" in importance or "urgent" in importance or "1" in x_priority or "2" in x_priority:
                    priority = CaseRecord.Priority.HIGH
                elif "low" in importance or "4" in x_priority or "5" in x_priority:
                    priority = CaseRecord.Priority.LOW
                
                case = CaseRecord.objects.create(
                    requester=employee,
                    category=default_category,
                    subject=subject[:500] or "Email Inquiry",
                    problem_description=body_text,
                    status=CaseRecord.Status.OPEN,
                    source=CaseRecord.Source.EMAIL,
                    priority=priority,
                    requester_email=employee.email,
                    requester_name=employee.full_name,
                    requester_unit_name=employee.unit.name if employee.unit else "",
                    requester_job_role=employee.job_role,
                    is_spam=is_spam,
                )
                is_new_case = True
                logger.info("Created new case %s from Email for %s with Priority %s.", case.id, employee.full_name, priority)

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
            
            case.has_unread_messages = True
            case.save(update_fields=["has_unread_messages"])

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

            # 5. Send auto-acknowledgment for new cases (Skip if SPAM)
            if is_new_case and not case.is_spam:
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
    from core.models import SiteConfig
    import html as html_mod

    site_name = SiteConfig.get_solo().site_name

    try:
        msg = Message.objects.select_related("case", "case__requester").get(id=message_id)
        if msg.direction != Message.Direction.OUTBOUND or msg.channel != Message.Channel.EMAIL:
            return "ignored:not_outbound_email"

        case = msg.case
        requester = case.requester
        requester_email = requester.email if requester else case.requester_email
        requester_name = requester.full_name if requester else case.requester_name

        if not requester_email:
            logger.warning("Cannot send email for case %s: no requester email available", case.id)
            return "error:no_requester_email"

        case_number = case.case_number  # e.g. CASE-2A8E62EA
        subject = f"Re: [{case_number}] {case.subject}"

        # Plain text fallback
        plain_body = (
            f"{msg.body}\n\n"
            f"---\n"
            f"{site_name} · Ticket {case_number}\n"
            f"Please reply to this email to add a comment or reopen the ticket."
        )

        # HTML email
        safe_body = html_mod.escape(msg.body).replace("\n", "<br>")
        html_body = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;padding:32px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,0.05);border:1px solid #e2e8f0;">

        <!-- Header -->
        <tr>
          <td style="background:#ffffff;padding:24px 32px;border-bottom:2px solid #6366f1;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <span style="color:#1e293b;font-size:20px;font-weight:800;letter-spacing:-0.5px;">🛠️ {html_mod.escape(site_name)}</span>
                </td>
                <td align="right">
                  <span style="background:#eef2ff;color:#4f46e5;font-size:12px;font-weight:700;padding:6px 12px;border-radius:20px;">Ticket {case_number}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:40px 32px 32px;">
            <div style="color:#334155;font-size:15px;line-height:1.7;">
              {safe_body}
            </div>
          </td>
        </tr>

        <!-- Divider -->
        <tr>
          <td style="padding:0 32px;">
            <hr style="border:none;border-top:1px solid #f1f5f9;margin:0;">
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:16px 28px 20px;">
            <p style="margin:0;color:#94a3b8;font-size:12px;line-height:1.5;">
              Reply to this email to continue the conversation regarding ticket <strong>{case_number}</strong>.<br>
              Your message will automatically be routed to our system.
            </p>
          </td>
        </tr>

      </table>

      <!-- Sub-footer -->
      <p style="margin:16px 0 0;color:#94a3b8;font-size:11px;text-align:center;">
        © {html_mod.escape(site_name)} · Powered by JokoUI
      </p>
    </td></tr>
  </table>
</body>
</html>"""

        from core.models import EmailConfig
        email_config = EmailConfig.get_solo()
        from_email_addr = email_config.default_from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@rocdesk.local")
        
        # Build a deterministic Message-ID for this case so all
        # emails about the same case form a thread.
        from_domain = from_email_addr.split('@')[-1] if '@' in from_email_addr else 'rocdesk.local'
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

        # Parse CC emails if provided
        cc_list = []
        if msg.cc_emails:
            cc_list = [email.strip() for email in msg.cc_emails.split(',') if email.strip()]

        email = EmailMultiAlternatives(
            subject=subject,
            body=plain_body,
            from_email=from_email_addr,
            to=[requester_email],
            cc=cc_list if cc_list else None,
            headers={
                'In-Reply-To': reply_to_id,
                'References': references_str,
                'Message-ID': f'<case-{case.id}-reply-{msg.id}@{from_domain}>',
            },
        )
        email.attach_alternative(html_body, 'text/html')

        # Handle Attachments
        attachments = msg.attachments.all()[:10]  # Max 10 files
        total_size = 0
        limit_exceeded = False
        
        for att in attachments:
            total_size += att.file_size
            if total_size > 10 * 1024 * 1024:  # 10MB limit
                limit_exceeded = True
                break
                
            try:
                with att.file.open('rb') as f:
                    email.attach(att.original_filename, f.read(), att.mime_type)
            except Exception as e:
                logger.error("Failed to attach file %s to email: %s", att.original_filename, e)
                
        if limit_exceeded:
            email.body += "\n\n[Warning: Some attachments were not included because the total size exceeded the 10MB limit.]"

        email.send(fail_silently=False)

        msg.delivery_status = Message.DeliveryStatus.SUCCESS
        msg.save(update_fields=["delivery_status"])

        logger.info("Sent outbound email for message %s to %s", msg.id, requester_email)
        return "success"

    except Message.DoesNotExist:
        logger.error("Message %s not found for outbound email", message_id)
        return "error:message_not_found"
    except Exception as exc:
        logger.exception("Failed to send outbound email for message %s: %s", message_id, exc)
        try:
            msg = Message.objects.get(id=message_id)
            msg.delivery_status = Message.DeliveryStatus.FAILED
            msg.delivery_error = str(exc)
            msg.save(update_fields=["delivery_status", "delivery_error"])
        except Exception:
            pass

        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"


@shared_task(
    bind=True,
    name="gateways.send_outbound_whatsapp_task",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def send_outbound_whatsapp_task(self, message_id: str) -> str:
    """
    Asynchronously send an outbound WhatsApp reply to the Ticket requester.
    Handles encoding attachments to base64 and hitting the Evolution API.
    """
    from cases.models import Message
    from gateways.services import EvolutionAPIService
    import base64

    try:
        msg = Message.objects.select_related("case__requester").get(id=message_id)
        case = msg.case
        requester = case.requester

        if not requester or not requester.phone_number:
            msg.delivery_status = Message.DeliveryStatus.FAILED
            msg.delivery_error = "Requester lacks phone number"
            msg.save(update_fields=["delivery_status", "delivery_error"])
            return "skipped:no_phone_number"

        # --- Validate phone number format ---
        # Strip the leading '+' if present and ensure the rest is purely numeric.
        # LID identifiers (e.g. 217188090806482 derived from @lid) are too long (≥15 digits)
        # and/or don't look like a real E.164 phone number.
        raw_digits = requester.phone_number.lstrip("+")
        is_valid_number = (
            raw_digits.isdigit()          # must be all digits
            and 7 <= len(raw_digits) <= 15 # E.164 length range
        )
        if not is_valid_number:
            error_msg = (
                f"⚠️ Invalid WhatsApp number: '{requester.phone_number}'. "
                "This may be a Linked Device ID (LID) stored in the database from before the LID fix. "
                "Please update the requester's phone number manually in the Employee profile."
            )
            logger.warning(
                "Blocked WA send to invalid number '%s' (case %s). %s",
                requester.phone_number, case.id, error_msg
            )
            msg.delivery_status = Message.DeliveryStatus.FAILED
            msg.delivery_error = error_msg
            msg.save(update_fields=["delivery_status", "delivery_error"])
            return "skipped:invalid_phone_number"


        svc = EvolutionAPIService()
        attachments = msg.attachments.all()[:10]  # Max 10 files

        response_data = None
        
        # If there are attachments, we send the FIRST attachment as the main media message with the text as caption
        # Additional attachments will be sent as separate media messages without captions
        if attachments:
            first = True
            for att in attachments:
                try:
                    # Limit file size check (10MB) before base64 encoding to prevent memory issues
                    if att.file_size > 10 * 1024 * 1024:
                        logger.warning("Skipping attachment %s: exceeds 10MB limit.", att.original_filename)
                        continue
                        
                    with att.file.open('rb') as f:
                        file_data = f.read()
                        
                    base64_data = base64.b64encode(file_data).decode('utf-8')
                    caption = msg.body if first else ""
                    
                    resp = svc.send_whatsapp_media(
                        phone_number=requester.phone_number,
                        base64_data=base64_data,
                        mime_type=att.mime_type or "application/octet-stream",
                        filename=att.original_filename,
                        caption=caption,
                    )
                    if first:
                        response_data = resp
                    first = False
                except Exception as e:
                    logger.error("Error attaching file %s to WA payload: %s", att.original_filename, e)
            
            # If all attachments failed (e.g. all >10MB) but we have text, fallback to text
            if first and msg.body:
                 response_data = svc.send_whatsapp_message(
                    phone_number=requester.phone_number,
                    text=f"{msg.body}\n\n[Warning: Attachments exceeded 10MB limit]",
                )
        else:
            # Just send text
            response_data = svc.send_whatsapp_message(
                phone_number=requester.phone_number,
                text=msg.body,
            )

        if response_data:
            msg.delivery_status = Message.DeliveryStatus.SUCCESS
            msg.external_id = response_data.get("key", {}).get("id", "")
            msg.save(update_fields=["delivery_status", "external_id"])
            return "success"
        else:
            msg.delivery_status = Message.DeliveryStatus.FAILED
            msg.delivery_error = "Evolution API returned None / Request Failed"
            msg.save(update_fields=["delivery_status", "delivery_error"])
            return "error:api_failure"

    except Message.DoesNotExist:
        logger.error("Message %s not found for outbound WA", message_id)
        return "error:message_not_found"
    except Exception as exc:
        logger.exception("Failed to send outbound WA for message %s: %s", message_id, exc)
        try:
            msg = Message.objects.get(id=message_id)
            msg.delivery_status = Message.DeliveryStatus.FAILED
            msg.delivery_error = str(exc)
            msg.save(update_fields=["delivery_status", "delivery_error"])
        except Exception:
            pass

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
    inbound email. Includes the ticket number so the user can reference it
    and future replies are threaded automatically.
    """
    from cases.models import CaseRecord, Message
    from django.core.mail import EmailMultiAlternatives
    from django.conf import settings
    from core.models import SiteConfig
    import html as html_mod

    site_name = SiteConfig.get_solo().site_name

    try:
        case = CaseRecord.objects.select_related("requester").get(id=case_id)
        requester = case.requester
        if not requester or not requester.email:
            return "skipped:no_requester_email"

        case_number = case.case_number  # e.g. CASE-E38BBD1F
        subject = f"[{case_number}] Request Received — {case.subject}"
        safe_name = html_mod.escape(requester.full_name)
        safe_subject = html_mod.escape(case.subject)

        plain_body = (
            f"Hello {requester.full_name},\n\n"
            f"Thank you for contacting us.\n\n"
            f"Your request has been received and is being reviewed by our support staff.\n"
            f"Here are your ticket details:\n"
            f"Ticket Number: {case_number}\n"
            f"Subject: {case.subject}\n\n"
            f"You will receive an update from us shortly.\n"
            f"To add additional comments, simply reply to this email.\n\n"
            f"---\n"
            f"{site_name}\n"
        )

        html_body = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;padding:32px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,0.05);border:1px solid #e2e8f0;">

        <!-- Header -->
        <tr>
          <td style="background:#ffffff;padding:24px 32px;border-bottom:2px solid #6366f1;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <span style="color:#1e293b;font-size:20px;font-weight:800;letter-spacing:-0.5px;">🛠️ {html_mod.escape(site_name)}</span>
                </td>
                <td align="right">
                  <span style="background:#eef2ff;color:#4f46e5;font-size:12px;font-weight:700;padding:6px 12px;border-radius:20px;">{case_number}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:40px 32px 12px;">
            <p style="margin:0 0 16px;color:#334155;font-size:15px;line-height:1.7;">Hello <strong style="color:#1e293b;">{safe_name}</strong>,</p>
            <p style="margin:0 0 24px;color:#475569;font-size:15px;line-height:1.6;">Thank you for reaching out to us. We have received your request and our support team will review it shortly. For your reference, here are the details of your ticket:</p>

            <!-- Ticket Details Card -->
            <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;margin:0 0 24px;">
              <tr>
                <td style="padding:16px 20px;">
                  <p style="margin:0 0 4px;font-size:12px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Ticket Number</p>
                  <p style="margin:0 0 12px;font-size:18px;font-weight:700;color:#1e293b;letter-spacing:0.5px;">{case_number}</p>
                  <p style="margin:0 0 4px;font-size:12px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Subject</p>
                  <p style="margin:0;font-size:15px;color:#334155;">{safe_subject}</p>
                </td>
              </tr>
            </table>

            <p style="margin:0 0 20px;color:#475569;font-size:14px;line-height:1.6;">To add additional comments or provide more information, please <strong>reply to this email</strong> directly.</p>
          </td>
        </tr>

        <!-- Divider -->
        <tr>
          <td style="padding:0 32px;">
            <hr style="border:none;border-top:1px solid #f1f5f9;margin:0;">
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:20px 32px;background:#f8fafc;text-align:center;">
            <p style="margin:0;color:#94a3b8;font-size:12px;line-height:1.5;">
              © {html_mod.escape(site_name)}<br>
              This is an automated message, but replies to this thread will be logged to your ticket.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""

        from core.models import EmailConfig
        email_config = EmailConfig.get_solo()
        from_email_addr = email_config.default_from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@rocdesk.local")

        # Deterministic Message-ID for threading
        from_domain = from_email_addr.split('@')[-1] if '@' in from_email_addr else 'rocdesk.local'
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
            from_email=from_email_addr,
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
@shared_task(
    bind=True,
    name="gateways.send_assignment_email_task",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def send_assignment_email_task(self, case_id: str, assigner_name: str, case_url: str) -> str:
    """
    Sends a modern card-designed email notification to the newly assigned user.
    """
    from cases.models import CaseRecord
    from core.models import SiteConfig
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    import logging

    try:
        case = CaseRecord.objects.get(id=case_id)
        if not case.assigned_to or not case.assigned_to.email:
            return "skipped:no_assignee_email"
            
        site_name = SiteConfig.get_solo().site_name

        req_name = case.requester.full_name if case.requester else case.requester_name
        if case.requester and case.requester.unit:
            req_name += f" ({case.requester.unit.name})"
        elif case.requester_unit_name:
            req_name += f" ({case.requester_unit_name})"

        context = {
            "case_number": case.case_number,
            "case_subject": case.subject,
            "requester_name": req_name,
            "priority": case.get_priority_display(),
            "assignee_name": case.assigned_to.get_full_name() or case.assigned_to.username,
            "assigned_by": assigner_name,
            "site_name": site_name,
            "case_url": case_url,
        }

        html_body = render_to_string("emails/ticket_assigned.html", context)
        
        subject = f"[{site_name}] Ticket Assigned: {case.case_number} - {case.subject}"
        
        email_msg = EmailMultiAlternatives(
            subject=subject,
            body=f"Ticket {case.case_number} has been assigned to you by {assigner_name}. View here: {case_url}",
            to=[case.assigned_to.email],
        )
        email_msg.attach_alternative(html_body, "text/html")
        email_msg.send(fail_silently=False)
        
        return "success:email_sent"
        
    except CaseRecord.DoesNotExist:
        return "error:case_not_found"
    except Exception as exc:
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"


@shared_task(bind=True, name="gateways.check_wa_session_timeout_task", max_retries=1)
def check_wa_session_timeout_task(self, case_id: str) -> str:
    """
    Checks if a WhatsApp session has been inactive for 10 minutes. 
    If yes, sends a session expiry message and keeps the case open or closed
    """
    from cases.models import CaseRecord
    from django.utils import timezone
    from datetime import timedelta
    from .services import EvolutionAPIService

    try:
        case = CaseRecord.objects.get(id=case_id)
        
        # If the case is already completed, no need to send timeout
        if case.status in [CaseRecord.Status.RESOLVED, CaseRecord.Status.CLOSED]:
            return "skipped:case_already_closed"

        # Check if the session is genuinely 10 mins old from last update
        time_since_update = timezone.now() - case.updated_at
        if time_since_update >= timedelta(minutes=10):
            # Session expired. Send warning message.
            if case.requester and case.requester.phone_number:
                svc = EvolutionAPIService()
                expiry_msg = (
                    "Your support session has ended due to 10 minutes of inactivity.\n"
                    "If you have any further questions or require additional assistance, "
                    "please feel free to send a new message, and a new ticket will be created for you."
                )
                try:
                    svc.send_whatsapp_message(case.requester.phone_number, expiry_msg)
                    logger.info("Sent WA session expiry message for case %s", case.case_number)
                except Exception as exc:
                    logger.warning("Failed to send WA expiry message for case %s: %s", case.case_number, str(exc))
            
            # Optional: auto-resolve ticket could go here. 
            # Per user request, we are just sending the message for now.

            return "success:expired"
        else:
            # Not yet 30 mins since last update (another message was sent in between)
            # The newer message would have spawned its own timeout task.
            return "skipped:session_renewed"
            
    except CaseRecord.DoesNotExist:
        return "error:case_not_found"


@shared_task(
    bind=True,
    name="gateways.escalate_case_task",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def escalate_case_task(self, case_id: str, forward_to: str, channel: str, custom_message: str, message_id: str = None) -> str:
    """
    Escalate or Forward a ticket to a third party (Email/WhatsApp).
    Formats a comprehensive message containing the agent's notes and the original problem.
    """
    from cases.models import CaseRecord, Message
    from gateways.services import EvolutionAPIService
    from django.core.mail import EmailMultiAlternatives
    from django.conf import settings
    from core.models import SiteConfig
    from django.utils import timezone

    site_name = SiteConfig.get_solo().site_name

    try:
        case = CaseRecord.objects.get(id=case_id)
        case_number = case.case_number
        
        msg_obj = None
        if message_id:
            try:
                msg_obj = Message.objects.get(id=message_id)
            except Message.DoesNotExist:
                pass

        # Build context
        requester_name = case.requester.full_name if case.requester else case.requester_name
        req_unit = case.requester.unit.name if case.requester and case.requester.unit else case.requester_unit_name

        # Collect latest attachments from case (max 10)
        attachments = []
        for m in case.messages.all().prefetch_related('attachments').order_by('created_at'):
            attachments.extend(list(m.attachments.all()))
        attachments = attachments[:10]

        if channel == 'WHATSAPP':
            import base64
            svc = EvolutionAPIService()
            wa_text = (
                f"🚨 *Ticket Escalation: {case_number}*\n\n"
                f"_*Subject:*_ {case.subject}\n"
                f"_*Requester:*_ {requester_name} ({req_unit})\n\n"
                f"*Notes from Support:*\n{custom_message}\n\n"
                f"*Original Problem:*\n{case.problem_description}\n\n"
                f"Reply to this message to add your response to the ticket (Requires linking)."
            )
            
            response_data = None
            if attachments:
                first = True
                for att in attachments:
                    try:
                        if att.file_size > 10 * 1024 * 1024:
                            logger.warning("Skipping escalate attachment %s: exceeds 10MB limit.", att.original_filename)
                            continue
                            
                        with att.file.open('rb') as f:
                            file_data = f.read()
                            
                        base64_data = base64.b64encode(file_data).decode('utf-8')
                        caption = wa_text if first else ""
                        
                        resp = svc.send_whatsapp_media(
                            phone_number=forward_to,
                            base64_data=base64_data,
                            mime_type=att.mime_type or "application/octet-stream",
                            filename=att.original_filename,
                            caption=caption,
                        )
                        if first:
                            response_data = resp
                        first = False
                    except Exception as e:
                        logger.error("Error attaching file %s to WA escalate payload: %s", att.original_filename, e)
                
                if first and wa_text:
                    response_data = svc.send_whatsapp_message(forward_to, f"{wa_text}\n\n[Warning: All attachments exceeded limits]")
            else:
                response_data = svc.send_whatsapp_message(forward_to, wa_text)
            
            if msg_obj:
                if response_data:
                    msg_obj.external_id = response_data.get("key", {}).get("id", "")
                    msg_obj.delivery_status = Message.DeliveryStatus.SUCCESS
                else:
                    msg_obj.delivery_status = Message.DeliveryStatus.FAILED
                msg_obj.save(update_fields=["external_id", "delivery_status"])
                
            return "success:wa"
        
        elif channel == 'EMAIL':
            from core.models import EmailConfig
            email_config = EmailConfig.get_solo()
            from_email_addr = email_config.default_from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@rocdesk.local")
            from_domain = from_email_addr.split('@')[-1] if '@' in from_email_addr else 'rocdesk.local'

            subject = f"[CS-{str(case.id)[:8].upper()}] Fwd: {case.subject}"
            case_thread_id = f'<case-{case.id}@{from_domain}>'

            plain_body = (
                f"Ticket Escalation: {case_number}\n\n"
                f"Notes from Support:\n{custom_message}\n\n"
                f"---\n"
                f"Original Subject: {case.subject}\n"
                f"Requester: {requester_name} ({req_unit})\n\n"
                f"Problem Description:\n{case.problem_description}\n\n"
                f"---\n"
                f"Reply to this email to add your response directly to ticket {case_number}."
            )

            from django.template.loader import render_to_string

            email = EmailMultiAlternatives(
                subject=subject,
                body=plain_body,
                from_email=from_email_addr,
                to=[forward_to],
                headers={
                    'References': case_thread_id,
                    'Message-ID': f'<case-{case.id}-escalate-{timezone.now().timestamp()}@{from_domain}>',
                },
            )
            
            # Embed Attachments
            total_size = 0
            limit_exceeded = False
            for att in attachments:
                total_size += att.file_size
                if total_size > 10 * 1024 * 1024:  # 10MB limit
                    limit_exceeded = True
                    break
                try:
                    with att.file.open('rb') as f:
                        email.attach(att.original_filename, f.read(), att.mime_type)
                except Exception as e:
                    logger.error("Failed to attach file %s to escalate email: %s", att.original_filename, e)
                    
            if limit_exceeded:
                email.body += "\n\n[Warning: Some attachments were not included because the total size exceeded the 10MB limit.]"
                
            # Render HTML body with template
            html_context = {
                "case_number": case_number,
                "case_subject": case.subject,
                "custom_message": custom_message,
                "requester_name": requester_name,
                "req_unit": req_unit,
                "problem_description": case.problem_description,
                "date_escalated": timezone.now().strftime('%d %b %Y, %H:%M'),
                "site_name": site_name,
                "limit_exceeded": limit_exceeded,
            }
            html_body = render_to_string("emails/escalate.html", html_context)
            email.attach_alternative(html_body, "text/html")

            email.send(fail_silently=False)

            if msg_obj:
                msg_obj.delivery_status = Message.DeliveryStatus.SUCCESS
                msg_obj.save(update_fields=["delivery_status"])

            return "success:email"
        
    except CaseRecord.DoesNotExist:
        return "error:case_not_found"
    except Exception as exc:
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"
