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
        from core.models import SiteConfig
        site_config = SiteConfig.get_solo()
        if not site_config.wa_inbound_enabled:
            logger.info("Webhook ignored — Inbound WA is disabled in SiteConfig.")
            return "ignored:inbound_disabled"

        from gateways.parsers import parse_evolution_webhook

        
        parsed_data = parse_evolution_webhook(payload)
        if not parsed_data:
            # The parser already logs the specific ignore reason
            return "ignored:parsed_none"

        # Handle protocolMessage (delete/revoke) detected in messages_upsert
        if parsed_data.get("protocol_action") == "delete":
            target_id = parsed_data.get("protocol_target_id")
            if target_id:
                updated = Message.objects.filter(
                    external_id=target_id,
                    is_deleted=False,
                ).update(is_deleted=True)
                if updated:
                    logger.info("Message %s marked as deleted via protocolMessage in upsert.", target_id)
                    return f"processed:delete:{target_id}"
                logger.debug("Delete target %s not found or already deleted.", target_id)
            return "ignored:protocol_delete_no_target"

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
        # 3. Validate phone number & Employee lookup
        # ---------------------------------------------------------
        # Final safety net: reject phone numbers that are not valid E.164
        # even if the parser let them through (defense in depth).
        raw_digits = sender_phone.lstrip("+")
        is_invalid_phone = not (raw_digits.isdigit() and 7 <= len(raw_digits) <= 15)

        if is_invalid_phone:
            logger.warning(
                "Webhook from invalid phone number '%s' (likely LID). "
                "Will NOT auto-create Employee — marking as spam.",
                sender_phone,
            )

        try:
            employee: Employee = Employee.objects.get(phone_number=sender_phone)
            if is_invalid_phone and not employee.has_valid_phone():
                logger.warning(
                    "Existing Employee %s has invalid phone '%s' — case will be marked as spam.",
                    employee.full_name, sender_phone,
                )
        except Employee.DoesNotExist:
            if is_invalid_phone:
                # Do NOT persist an Employee with a garbage phone number.
                # Create a transient object (unsaved) just to proceed with
                # spam case creation so the message is not silently lost.
                logger.warning(
                    "Skipping Employee creation for invalid phone '%s'. "
                    "Message will be logged as spam case.",
                    sender_phone,
                )
                default_unit = _get_or_create_external_unit()
                display_name = sender_name if sender_name else f"WA User {sender_phone}"
                employee = Employee(
                    phone_number=sender_phone,
                    full_name=display_name,
                    unit=default_unit,
                    job_role="WhatsApp User",
                )
                # Don't save — just return early with a spam skip
                return "skipped:invalid_phone"

            default_unit = _get_or_create_external_unit()
            display_name = sender_name if sender_name else f"WA User {sender_phone}"
            employee = Employee.objects.create(
                phone_number=sender_phone,
                full_name=display_name,
                unit=default_unit,
                job_role="WhatsApp User",
            )
            logger.info("Auto-registered new Employee from WA: %s (%s)", sender_phone, display_name)

        # ---------------------------------------------------------
        # 3b. Record last inbound timestamp for this phone number in cache.
        #     Used by broadcast guard (opt-in check) to skip recipients who
        #     have never initiated a conversation with us in the past 7 days.
        # ---------------------------------------------------------
        if not is_invalid_phone:
            from django.core.cache import cache as _cache
            _clean = sender_phone.lstrip("+")
            try:
                from core.models import SiteConfig as _SC
                _opt_in_days = _SC.get_solo().wa_opt_in_ttl_days
            except Exception:
                _opt_in_days = 7
            _cache.set(f"wa_last_inbound:{_clean}", 1, timeout=_opt_in_days * 86400)

        # ---------------------------------------------------------
        # 4. Session threading
        #    a) Check if the user is quoting a previous message we sent
        #    b) Otherwise, thread into a RECENT WhatsApp case
        # ---------------------------------------------------------
        from datetime import timedelta
        
        active_case: Optional[CaseRecord] = None
        quoted_msg_obj = None  # For storing the quoted_message FK
        # quoted_id is already extracted via the parser

        # If parser didn't find quoted_id, try fetching from Evolution API
        if not quoted_id and external_id:
            remote_jid = parsed_data.get("remote_jid", "")
            if remote_jid:
                logger.info("No quoted_id from parser, fetching message %s from Evolution API...", external_id)
                full_msg = svc.find_message_by_id(remote_jid, external_id)
                if full_msg:
                    full_message = full_msg.get("message", {})
                    # Check extendedTextMessage.contextInfo
                    ext_text = full_message.get("extendedTextMessage", {})
                    if ext_text and "contextInfo" in ext_text:
                        quoted_id = ext_text["contextInfo"].get("stanzaId")
                    # Check media messages
                    if not quoted_id:
                        for mk in ("imageMessage", "videoMessage", "documentMessage", "audioMessage"):
                            media_msg = full_message.get(mk, {})
                            if media_msg and "contextInfo" in media_msg:
                                quoted_id = media_msg["contextInfo"].get("stanzaId")
                                break
                    if quoted_id:
                        logger.info("Quoted message found via Evolution API fetch: stanzaId=%s", quoted_id)

        if quoted_id:
            logger.info("Looking up quoted message with external_id=%s", quoted_id)
            orig_msg = Message.objects.filter(external_id=quoted_id).first()
            if orig_msg:
                active_case = orig_msg.case
                quoted_msg_obj = orig_msg
                logger.info("Threaded WA reply via quoted message %s to case %s.", quoted_id, active_case.case_number)
            else:
                logger.warning("Quoted message external_id=%s not found in database.", quoted_id)
        
        if not active_case:
            from django.db.models import Q
            session_window = timezone.now() - timedelta(minutes=60)

            # Fallback A: Did we recently send an outbound message to this user's phone?
            # Escalations include the phone number in the body, e.g., "*** TICKET ESCALATED VIA WHATSAPP TO: 628... ***"
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
            session_window = timezone.now() - timedelta(minutes=60)  # 60 mins for session
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
                created_at__gte=timezone.now() - timedelta(minutes=60)
            ).count()
            is_spam = recent_cases_count >= 3

            # Also mark as spam if the phone number is invalid (likely LID)
            if is_invalid_phone:
                is_spam = True
                logger.warning("Marking case as spam: invalid phone number '%s' (likely LID).", sender_phone)

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
            if not case.is_spam:
                _dispatch_new_ticket_notifs(str(case.id))

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
                quoted_message=quoted_msg_obj,
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
            from gateways.spintax import WA_ACK_USER, pick_template, greeting_by_hour
            from gateways.throttle import WARateLimiter

            site_config = SiteConfig.get_solo()
            site_name = getattr(site_config, 'site_name', 'Support Desk')

            try:
                allowed, reason = WARateLimiter.check_and_record(sender_phone)
                if not allowed:
                    logger.warning("WA ACK throttled for case %s: %s", case.case_number, reason)
                else:
                    from django.utils import timezone
                    from zoneinfo import ZoneInfo
                    now_wib = timezone.now().astimezone(ZoneInfo("Asia/Jakarta"))
                    greet_time = greeting_by_hour(now_wib.hour)

                    ack_text = pick_template(WA_ACK_USER, seed=case.case_number).format(
                        name=employee.full_name,
                        site=site_name,
                        ticket_num=case.case_number,
                        subject=case.subject[:80],
                        greet_time=greet_time,
                    )

                    ack_response = svc.send_with_human_pacing(sender_phone, ack_text)
                    ack_ext_id = ack_response.get("key", {}).get("id", "") if ack_response else ""
                    Message.objects.create(
                        case=case,
                        body=ack_text,
                        direction=Message.Direction.OUTBOUND,
                        channel=Message.Channel.WHATSAPP,
                        external_id=ack_ext_id,
                        delivery_status=Message.DeliveryStatus.SUCCESS if ack_response else Message.DeliveryStatus.FAILED,
                    )
                    logger.info(
                        "Sent WA acknowledgment for case %s to %s (ext_id=%s)",
                        case.case_number, sender_phone, ack_ext_id,
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
# Message Update Handler (Delete / Revoke from WhatsApp)
# =====================================================================

@shared_task(
    bind=True,
    name="gateways.process_message_update_task",
    max_retries=2,
    default_retry_delay=10,
    acks_late=True,
)
def process_message_update_task(self, payload: dict[str, Any]) -> str:
    """
    Process Evolution API ``messages_update`` webhook.

    Handles:
    - Message deleted/revoked by WhatsApp user → mark is_deleted=True
    """
    from cases.models import Message
    from gateways.parsers import parse_message_update

    try:
        parsed = parse_message_update(payload)
        if not parsed:
            logger.debug("messages_update ignored — no actionable updates.")
            return "ignored:no_updates"

        processed = 0
        for update in parsed.get("updates", []):
            action = update.get("action")
            msg_ext_id = update.get("message_id")

            if not msg_ext_id:
                continue

            if action == "delete":
                updated = Message.objects.filter(
                    external_id=msg_ext_id,
                    is_deleted=False,
                ).update(is_deleted=True)
                if updated:
                    logger.info("Message %s marked as deleted (revoked by sender).", msg_ext_id)
                    processed += 1
                else:
                    logger.debug("Message %s not found or already deleted.", msg_ext_id)

            elif action in ("sent", "delivered", "read", "played"):
                # Map WA ACK action → DeliveryStatus
                STATUS_MAP = {
                    "sent": Message.DeliveryStatus.SUCCESS,
                    "delivered": Message.DeliveryStatus.SUCCESS,
                    "read": Message.DeliveryStatus.SUCCESS,
                    "played": Message.DeliveryStatus.SUCCESS,
                }
                new_status = STATUS_MAP[action]
                updated = Message.objects.filter(
                    external_id=msg_ext_id,
                    delivery_status=Message.DeliveryStatus.PENDING,
                ).update(delivery_status=new_status)
                if updated:
                    logger.info(
                        "Message %s delivery confirmed: %s → %s",
                        msg_ext_id, action, new_status,
                    )
                    processed += 1

        return f"processed:{processed}_updates"

    except Exception as exc:
        logger.exception("Error processing messages_update: %s", exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
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
    bind=True,
    name="gateways.poll_imap_emails_task",
    max_retries=1,
    default_retry_delay=60,
)
def poll_imap_emails_task(self) -> str:
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
                if not is_spam:
                    _dispatch_new_ticket_notifs(str(case.id))

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
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"


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

        # Get history messages for context thread
        history_msgs = (
            Message.objects.filter(case=case, is_deleted=False)
            .exclude(id=message_id)
            .select_related("sender_staff", "sender_employee")
            .order_by("-sent_at")[:5]
        )
        
        plain_history = ""
        html_history = ""
        
        if history_msgs:
            plain_history += "\n\n---\nPrevious Messages:\n\n"
            html_history += """
            <div style="margin-top: 32px; border-top: 1px solid #e2e8f0; padding-top: 24px;">
              <p style="color:#64748b;font-size:13px;font-weight:600;text-transform:uppercase;margin:0 0 16px;">Previous Messages</p>
            """
            for h_msg in history_msgs:
                if h_msg.sender_staff:
                    sender_name = h_msg.sender_staff.get_full_name() or h_msg.sender_staff.username
                elif h_msg.sender_employee:
                    sender_name = h_msg.sender_employee.full_name
                elif h_msg.is_system:
                    sender_name = "System"
                else:
                    sender_name = "System"
                    
                h_date_str = h_msg.sent_at.strftime("%Y-%m-%d %H:%M") if h_msg.sent_at else ""
                plain_body_part = h_msg.body or "[Attachment/Media]"
                plain_history += f"On {h_date_str}, {sender_name} wrote:\n> {plain_body_part.replace(chr(10), chr(10)+'> ')}\n\n"
                
                html_history += f"""
              <div style="margin-bottom: 16px; background: #f8fafc; padding: 12px 16px; border-radius: 8px; border-left: 3px solid #cbd5e1;">
                <p style="margin: 0 0 4px; font-size: 12px; color: #475569;">
                  <strong>{html_mod.escape(sender_name)}</strong> <span style="color:#94a3b8;">on {h_date_str}</span>
                </p>
                <div style="color: #334155; font-size: 14px; line-height: 1.5;">
                  {html_mod.escape(plain_body_part).replace(chr(10), '<br>')}
                </div>
              </div>
                """
            html_history += "</div>"

        # Plain text fallback
        plain_body = (
            f"{msg.body}\n"
            f"{plain_history}\n"
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
            {html_history}
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
    max_retries=3,
    default_retry_delay=60,
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
        msg = Message.objects.select_related("case__requester", "quoted_message").get(id=message_id)
        case = msg.case
        requester = case.requester

        # Get external_id of quoted message for WA reply threading
        quoted_ext_id = None
        if msg.quoted_message and msg.quoted_message.external_id:
            quoted_ext_id = msg.quoted_message.external_id

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
                f"⚠️ '{requester.phone_number}' is not a valid WhatsApp phone number. "
                "This is likely a Linked Device ID (LID), not a real phone number. "
                "Please update the requester's phone number via Edit Requester Info before sending messages."
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

        from gateways.throttle import WARateLimiter, is_within_business_hours, is_circuit_open, record_disconnect

        # --- Check circuit breaker first ---
        if is_circuit_open():
            error_msg = "WA circuit breaker is open — instance disconnected too many times today. Retrying later."
            logger.warning("Circuit breaker blocked WA send for case %s.", case.id)
            msg.delivery_status = Message.DeliveryStatus.FAILED
            msg.delivery_error = error_msg
            msg.save(update_fields=["delivery_status", "delivery_error"])
            raise self.retry(exc=RuntimeError(error_msg), countdown=300)

        # --- Check WA instance is connected before attempting to send ---
        instance_state_data = svc.get_instance_state()
        instance_state = (
            instance_state_data.get("instance", {}).get("state")
            if instance_state_data
            else None
        )
        if instance_state != "open":
            record_disconnect()
            error_msg = (
                f"WhatsApp instance is not connected (state: {instance_state or 'unknown'}). "
                "Message will be retried automatically."
            )
            logger.warning(
                "Blocked WA send — instance not connected (state=%s) for case %s.",
                instance_state, case.id,
            )
            msg.delivery_status = Message.DeliveryStatus.FAILED
            msg.delivery_error = error_msg
            msg.save(update_fields=["delivery_status", "delivery_error"])
            # Retry with longer delay to give time for reconnection
            raise self.retry(exc=RuntimeError(error_msg), countdown=60)
        allowed, reason = WARateLimiter.check_and_record(requester.phone_number)
        if not allowed:
            logger.warning("WA rate limit for outbound msg %s: %s", message_id, reason)
            msg.delivery_status = Message.DeliveryStatus.FAILED
            msg.delivery_error = f"Throttled: {reason}"
            msg.save(update_fields=["delivery_status", "delivery_error"])
            raise self.retry(exc=RuntimeError(reason), countdown=15)

        # Conversation window: if the last inbound from this user was > 24 h ago,
        # add extra reading delay to simulate picking up an old thread.
        import time as _time
        from cases.models import Message as _Message
        last_inbound = (
            _Message.objects.filter(
                case=case,
                direction=_Message.Direction.INBOUND,
                channel=_Message.Channel.WHATSAPP,
            )
            .order_by("-sent_at")
            .values_list("sent_at", flat=True)
            .first()
        )
        if last_inbound:
            import random as _rand
            from django.utils import timezone as _tz
            hours_since = (_tz.now() - last_inbound).total_seconds() / 3600
            if hours_since > 48:
                _time.sleep(_rand.uniform(30, 90))
            elif hours_since > 24:
                _time.sleep(_rand.uniform(10, 30))

        has_audio_att = msg.attachments.filter(mime_type__startswith="audio/").exists()

        attachments = msg.attachments.all()[:10]  # Max 10 files

        response_data = None

        # If there are attachments, we send the FIRST attachment as the main media message with the text as caption
        # Additional attachments will be sent as separate media messages without captions
        if attachments:
            # Human-like pacing before sending media (read pause + composing)
            import random, time
            read_pause = random.uniform(1.0, 3.5)
            time.sleep(read_pause)
            typing_duration = max(1.5, min(len(msg.body or "") / 25.0, 8.0) + random.uniform(-0.3, 1.5))
            presence_type = "recording" if has_audio_att else "composing"
            svc.send_presence(requester.phone_number, presence=presence_type, delay=int(typing_duration * 1000))
            time.sleep(typing_duration)
            time.sleep(random.uniform(0.3, 0.8))

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
                    mime = att.mime_type or "application/octet-stream"

                    # Send audio as PTT voice note (green waveform bubble)
                    if mime.startswith("audio/"):
                        # Send text separately first if this is the first attachment with body
                        if first and msg.body:
                            svc.send_whatsapp_message(
                                phone_number=requester.phone_number,
                                text=msg.body,
                                quoted_msg_id=quoted_ext_id,
                            )
                        resp = svc.send_whatsapp_audio(
                            phone_number=requester.phone_number,
                            base64_data=base64_data,
                            mime_type=mime,
                        )
                    else:
                        resp = svc.send_whatsapp_media(
                            phone_number=requester.phone_number,
                            base64_data=base64_data,
                            mime_type=mime,
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
                response_data = svc.send_with_human_pacing(
                    requester.phone_number,
                    f"{msg.body}\n\n[Warning: Attachments exceeded 10MB limit]",
                    quoted_msg_id=quoted_ext_id,
                )
        else:
            # Just send text — use paced send
            response_data = svc.send_with_human_pacing(
                requester.phone_number,
                msg.body,
                quoted_msg_id=quoted_ext_id,
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


@shared_task(bind=True, name="gateways.check_wa_session_warning_task", max_retries=1)
def check_wa_session_warning_task(self, case_id: str) -> str:
    """
    Sends a confirmation message at ~45 minutes of inactivity asking if the
    user is still active. The actual expiry task runs at 60 minutes.
    """
    from cases.models import CaseRecord
    from django.utils import timezone
    from datetime import timedelta
    from .services import EvolutionAPIService

    try:
        case = CaseRecord.objects.get(id=case_id)

        if case.status in [CaseRecord.Status.RESOLVED, CaseRecord.Status.CLOSED]:
            return "skipped:case_already_closed"
        if case.hold_wa_session:
            return "skipped:session_on_hold"

        time_since_update = timezone.now() - case.updated_at
        if time_since_update >= timedelta(minutes=45):
            if case.requester and case.requester.phone_number:
                from gateways.spintax import WA_SESSION_WARNING, pick_template
                from gateways.throttle import WARateLimiter, is_circuit_open

                svc = EvolutionAPIService()
                try:
                    if is_circuit_open():
                        logger.warning("WA session warning skipped — circuit open (case %s)", case.case_number)
                    else:
                        allowed, reason = WARateLimiter.check_and_record(case.requester.phone_number)
                        if not allowed:
                            logger.warning("WA session warning throttled for case %s: %s", case.case_number, reason)
                        else:
                            warning_msg = pick_template(WA_SESSION_WARNING, seed=f"warn-{case.id}")
                            svc.send_with_human_pacing(case.requester.phone_number, warning_msg)
                            logger.info("Sent WA session warning message for case %s", case.case_number)
                except Exception as exc:
                    logger.warning("Failed to send WA warning message for case %s: %s", case.case_number, str(exc))
            return "success:warning_sent"
        else:
            return "skipped:session_renewed"

    except CaseRecord.DoesNotExist:
        return "error:case_not_found"


@shared_task(bind=True, name="gateways.check_wa_session_timeout_task", max_retries=1)
def check_wa_session_timeout_task(self, case_id: str) -> str:
    """
    Checks if a WhatsApp session has been inactive for 60 minutes.
    If yes, sends a session expiry message.
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

        # If the session is held by staff, do not expire
        if case.hold_wa_session:
            return "skipped:session_on_hold"

        # Check if the session is genuinely 60 mins old from last update
        time_since_update = timezone.now() - case.updated_at
        if time_since_update >= timedelta(minutes=60):
            # Session expired. Send expiry message.
            if case.requester and case.requester.phone_number:
                from gateways.spintax import WA_SESSION_EXPIRED, pick_template
                from gateways.throttle import WARateLimiter, is_circuit_open

                svc = EvolutionAPIService()
                try:
                    if is_circuit_open():
                        logger.warning("WA session expiry skipped — circuit open (case %s)", case.case_number)
                    else:
                        allowed, reason = WARateLimiter.check_and_record(case.requester.phone_number)
                        if not allowed:
                            logger.warning("WA session expiry throttled for case %s: %s", case.case_number, reason)
                        else:
                            expiry_msg = pick_template(WA_SESSION_EXPIRED, seed=f"expiry-{case.id}")
                            svc.send_with_human_pacing(case.requester.phone_number, expiry_msg)
                            logger.info("Sent WA session expiry message for case %s", case.case_number)
                except Exception as exc:
                    logger.warning("Failed to send WA expiry message for case %s: %s", case.case_number, str(exc))

            return "success:expired"
        else:
            # Not yet 60 mins since last update (another message was sent in between)
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
def escalate_case_task(self, case_id: str, forward_to: str, channel: str, custom_message: str, message_id: str = None, selected_message_ids: str = "") -> str:
    """
    Escalate or Forward a ticket to a third party (Email/WhatsApp).
    If selected_message_ids is provided, only those messages (with their attachments) are included.
    Otherwise falls back to the original problem description + all case attachments.
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

        # Parse selected message IDs
        sel_ids = [mid.strip() for mid in selected_message_ids.split(",") if mid.strip()] if selected_message_ids else []

        # Collect selected messages and their attachments
        selected_messages = []
        attachments = []
        if sel_ids:
            selected_messages = list(
                Message.objects.filter(id__in=sel_ids, case=case)
                .prefetch_related('attachments')
                .order_by('created_at')
            )
            for m in selected_messages:
                attachments.extend(list(m.attachments.all()))
            attachments = attachments[:10]
        else:
            # Fallback: all attachments from case
            for m in case.messages.all().prefetch_related('attachments').order_by('created_at'):
                attachments.extend(list(m.attachments.all()))
            attachments = attachments[:10]

        # Build conversation transcript from selected messages
        def build_conversation_text(messages, fmt="plain"):
            """Build a chronological transcript of selected messages."""
            lines = []
            for m in messages:
                sender = m.sender_staff.username if m.direction == 'OUT' and m.sender_staff else (
                    m.sender_employee.full_name if m.direction == 'IN' and m.sender_employee else "Unknown"
                )
                ts = m.sent_at.strftime('%d %b %Y, %H:%M') if m.sent_at else ""
                direction_label = "Staff" if m.direction == 'OUT' else "Customer"
                body = m.body or ""
                if m.is_deleted:
                    body = "[Message deleted]"

                if fmt == "wa":
                    lines.append(f"[{ts}] *{direction_label} ({sender})*:\n{body}")
                    if m.attachments.exists():
                        att_names = [a.original_filename or "Attachment" for a in m.attachments.all()]
                        lines.append(f"  📎 {', '.join(att_names)}")
                else:
                    lines.append(f"[{ts}] {direction_label} ({sender}):\n{body}")
                    if m.attachments.exists():
                        att_names = [a.original_filename or "Attachment" for a in m.attachments.all()]
                        lines.append(f"  Attachments: {', '.join(att_names)}")
            return "\n\n".join(lines)

        if channel == 'WHATSAPP':
            import base64
            from gateways.throttle import WARateLimiter, is_circuit_open

            if is_circuit_open():
                if msg_obj:
                    msg_obj.delivery_status = Message.DeliveryStatus.FAILED
                    msg_obj.delivery_error = "WA circuit breaker open"
                    msg_obj.save(update_fields=["delivery_status", "delivery_error"])
                return "skipped:circuit_open"

            allowed, reason = WARateLimiter.check_and_record(forward_to)
            if not allowed:
                if msg_obj:
                    msg_obj.delivery_status = Message.DeliveryStatus.FAILED
                    msg_obj.delivery_error = f"Throttled: {reason}"
                    msg_obj.save(update_fields=["delivery_status", "delivery_error"])
                return f"skipped:{reason}"

            svc = EvolutionAPIService()

            # Build WA text — minimal formatting to reduce template fingerprint
            wa_header = (
                f"Eskalasi Tiket: {case_number}\n\n"
                f"Perihal: {case.subject}\n"
                f"Pemohon: {requester_name} ({req_unit})\n\n"
                f"Catatan:\n{custom_message}\n"
            )
            if selected_messages:
                conversation = build_conversation_text(selected_messages, fmt="wa")
                wa_text = f"{wa_header}\n--- Percakapan ({len(selected_messages)} pesan) ---\n\n{conversation}\n\nBalas pesan ini untuk merespons."
            else:
                wa_text = f"{wa_header}\nDeskripsi:\n{case.problem_description}\n\nBalas pesan ini untuk merespons."

            response_data = None
            if attachments:
                import random as _rand, time as _time
                # Human-like pacing before escalation send
                _time.sleep(_rand.uniform(2.0, 5.0))
                svc.send_presence(forward_to, presence="composing", delay=int(_rand.uniform(2, 4) * 1000))
                _time.sleep(_rand.uniform(2.0, 4.0))

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
                    response_data = svc.send_with_human_pacing(forward_to, f"{wa_text}\n\n[Note: all attachments exceeded the size limit]")
            else:
                response_data = svc.send_with_human_pacing(forward_to, wa_text)

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

            # Build plain text body
            if selected_messages:
                conversation_plain = build_conversation_text(selected_messages, fmt="plain")
                plain_body = (
                    f"Ticket Escalation: {case_number}\n\n"
                    f"Notes from Support:\n{custom_message}\n\n"
                    f"---\n"
                    f"Original Subject: {case.subject}\n"
                    f"Requester: {requester_name} ({req_unit})\n\n"
                    f"--- Conversation ({len(selected_messages)} messages) ---\n\n"
                    f"{conversation_plain}\n\n"
                    f"---\n"
                    f"Reply to this email to add your response directly to ticket {case_number}."
                )
            else:
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

            # Build conversation data for HTML template
            conversation_html_data = []
            for m in selected_messages:
                sender = m.sender_staff.username if m.direction == 'OUT' and m.sender_staff else (
                    m.sender_employee.full_name if m.direction == 'IN' and m.sender_employee else "Unknown"
                )
                conversation_html_data.append({
                    "direction": m.direction,
                    "sender": sender,
                    "timestamp": m.sent_at.strftime('%d %b %Y, %H:%M') if m.sent_at else "",
                    "body": m.body if not m.is_deleted else "[Message deleted]",
                    "has_attachments": m.attachments.exists(),
                    "attachment_names": [a.original_filename or "Attachment" for a in m.attachments.all()],
                })

            # Render HTML body with template
            html_context = {
                "case_number": case_number,
                "case_subject": case.subject,
                "custom_message": custom_message,
                "requester_name": requester_name,
                "req_unit": req_unit,
                "problem_description": case.problem_description if not selected_messages else "",
                "selected_messages": conversation_html_data,
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


@shared_task(
    bind=True,
    name="gateways.mark_wa_messages_read_task",
    max_retries=1,
    default_retry_delay=10,
    acks_late=True,
)
def mark_wa_messages_read_task(self, case_id: str, message_external_ids: list[str]) -> str:
    """
    Send read receipts (blue checkmarks) to WhatsApp for messages
    that staff has viewed in the case detail page.
    """
    from cases.models import CaseRecord
    from gateways.services import EvolutionAPIService

    import random
    import time

    try:
        case = CaseRecord.objects.select_related("requester").get(id=case_id)
        if not case.requester or not case.requester.phone_number:
            return "skipped:no_phone"

        # Humans don't read messages the instant they arrive — add realistic delay
        # ~90% of the time we wait 5-90 s; ~10% skip read receipt entirely
        if random.random() < 0.10:
            return "skipped:natural_skip"
        time.sleep(random.uniform(5, 90))

        svc = EvolutionAPIService()
        result = svc.mark_messages_as_read(
            case.requester.phone_number,
            message_external_ids,
        )
        if result:
            return f"success:marked_{len(message_external_ids)}_read"
        return "error:api_returned_none"

    except CaseRecord.DoesNotExist:
        return "error:case_not_found"
    except Exception as exc:
        logger.error("mark_wa_messages_read_task failed: %s", exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"


@shared_task(
    bind=True,
    name="gateways.delete_whatsapp_message_task",
    max_retries=1,
    default_retry_delay=10,
    acks_late=True,
)
def delete_whatsapp_message_task(self, message_id: str) -> str:
    """Delete (revoke) a WhatsApp message for everyone."""
    from cases.models import Message
    from gateways.services import EvolutionAPIService

    try:
        msg = Message.objects.select_related("case__requester").get(id=message_id)
        phone = msg.case.requester.phone_number if msg.case.requester else None
        if not phone or not msg.external_id:
            return "skipped:no_phone_or_external_id"

        svc = EvolutionAPIService()
        result = svc.delete_message_for_everyone(phone, msg.external_id)
        return "success" if result else "error:api_returned_none"

    except Message.DoesNotExist:
        return "error:message_not_found"
    except Exception as exc:
        logger.error("delete_whatsapp_message_task failed: %s", exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"


@shared_task(
    bind=True,
    name="gateways.edit_whatsapp_message_task",
    max_retries=1,
    default_retry_delay=10,
    acks_late=True,
)
def edit_whatsapp_message_task(self, message_id: str) -> str:
    """Edit/update a sent WhatsApp message text."""
    from cases.models import Message
    from gateways.services import EvolutionAPIService

    try:
        msg = Message.objects.select_related("case__requester").get(id=message_id)
        phone = msg.case.requester.phone_number if msg.case.requester else None
        if not phone or not msg.external_id:
            return "skipped:no_phone_or_external_id"

        svc = EvolutionAPIService()
        result = svc.update_message(phone, msg.external_id, msg.body)
        return "success" if result else "error:api_returned_none"

    except Message.DoesNotExist:
        return "error:message_not_found"
    except Exception as exc:
        logger.error("edit_whatsapp_message_task failed: %s", exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"


@shared_task(
    bind=True,
    name="gateways.react_whatsapp_message_task",
    max_retries=1,
    default_retry_delay=10,
    acks_late=True,
)
def react_whatsapp_message_task(self, message_id: str, emoji: str) -> str:
    """Send an emoji reaction to a WhatsApp message."""
    from cases.models import Message
    from gateways.services import EvolutionAPIService

    try:
        msg = Message.objects.select_related("case__requester").get(id=message_id)
        phone = msg.case.requester.phone_number if msg.case.requester else None
        if not phone or not msg.external_id:
            return "skipped:no_phone_or_external_id"

        svc = EvolutionAPIService()
        from_me = msg.direction == "OUT"
        result = svc.send_reaction(phone, msg.external_id, emoji, from_me=from_me)
        return "success" if result else "error:api_returned_none"

    except Message.DoesNotExist:
        return "error:message_not_found"
    except Exception as exc:
        logger.error("react_whatsapp_message_task failed: %s", exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"


# =====================================================================
# Internal New-Ticket Notification Tasks
# =====================================================================

def _get_site_base_url() -> str:
    """Return the base URL for building absolute case links inside tasks."""
    from django.conf import settings
    trusted = getattr(settings, "CSRF_TRUSTED_ORIGINS", [])
    for origin in trusted:
        if origin.startswith("https://") and "*" not in origin:
            return origin.rstrip("/")
    hosts = getattr(settings, "ALLOWED_HOSTS", [])
    for host in hosts:
        if host not in ("localhost", "127.0.0.1", "*"):
            return f"https://{host}"
    return "http://localhost"


def _dispatch_new_ticket_notifs(case_id: str) -> None:
    """
    Dispatch enabled new-ticket notification tasks for the given case ID.
    Reads NotificationConfig once and only queues tasks whose channel is active,
    so disabled channels incur zero Celery overhead.
    """
    from core.models import NotificationConfig
    cfg = NotificationConfig.get_solo()
    if cfg.notify_new_ticket_teams:
        send_teams_notification_task.delay(case_id)
    if cfg.notify_new_ticket_whatsapp:
        send_new_ticket_wa_notif_task.delay(case_id)
    if cfg.notify_new_ticket_email:
        send_new_ticket_email_notif_task.delay(case_id)


@shared_task(
    bind=True,
    name="gateways.send_teams_notification_task",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def send_teams_notification_task(self, case_id: str) -> str:
    """
    Post an Adaptive Card to the configured Teams Incoming Webhook
    when a new support ticket is created.
    """
    import requests as http_requests
    from cases.models import CaseRecord
    from core.models import TeamsConfig, SiteConfig

    try:
        from core.models import NotificationConfig
        if not NotificationConfig.get_solo().notify_new_ticket_teams:
            return "skipped:teams_notif_disabled"

        teams_cfg = TeamsConfig.get_solo()
        if not teams_cfg.is_notification_enabled or not teams_cfg.incoming_webhook_url:
            return "skipped:teams_not_configured"

        case = CaseRecord.objects.select_related("requester", "category").get(id=case_id)
        site_name = SiteConfig.get_solo().site_name
        base_url = _get_site_base_url()
        case_url = f"{base_url}/desk/cases/{case.id}/"

        requester_display = (
            case.requester.full_name if case.requester else (case.requester_name or "—")
        )
        category_display = case.category.name if case.category else "—"

        priority_emoji = {
            "Critical": "🔴",
            "High": "🟠",
            "Medium": "🟡",
            "Low": "🟢",
        }.get(case.priority, "⚪")

        source_emoji = {
            "EvolutionAPI_WA": "💬",
            "Email": "📧",
            "WebForm": "🌐",
            "Teams_Bot": "🟦",
        }.get(case.source, "📋")

        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": f"🎫 New Ticket — {site_name}",
                                "weight": "Bolder",
                                "size": "Medium",
                                "wrap": True,
                            },
                            {
                                "type": "FactSet",
                                "facts": [
                                    {"title": "Ticket #", "value": case.case_number},
                                    {"title": "From", "value": requester_display},
                                    {"title": "Subject", "value": case.subject[:120]},
                                    {"title": "Priority", "value": f"{priority_emoji} {case.get_priority_display()}"},
                                    {"title": "Source", "value": f"{source_emoji} {case.get_source_display()}"},
                                    {"title": "Category", "value": category_display},
                                ],
                            },
                        ],
                        "actions": [
                            {
                                "type": "Action.OpenUrl",
                                "title": "Open Ticket",
                                "url": case_url,
                            }
                        ],
                    },
                }
            ],
        }

        response = http_requests.post(
            teams_cfg.incoming_webhook_url,
            json=payload,
            timeout=10,
        )

        if response.status_code == 200:
            logger.info("Teams notification sent for case %s", case.case_number)
            return "success"

        logger.warning(
            "Teams webhook returned %s for case %s: %s",
            response.status_code, case.case_number, response.text[:200],
        )
        raise Exception(f"Teams webhook HTTP {response.status_code}: {response.text[:200]}")

    except CaseRecord.DoesNotExist:
        return "error:case_not_found"
    except Exception as exc:
        logger.error("send_teams_notification_task failed for case %s: %s", case_id, exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"


@shared_task(
    bind=True,
    name="gateways.send_new_ticket_wa_notif_task",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def send_new_ticket_wa_notif_task(self, case_id: str) -> str:
    """
    Dispatch staggered per-recipient WA notifications for a new ticket.

    Each recipient gets its own task scheduled with a 20-45 s gap so
    back-to-back identical sends — a primary spam signal — are avoided.
    """
    import random
    from cases.models import CaseRecord
    from core.models import NotificationConfig, SiteConfig
    from gateways.spintax import WA_NOTIF_INTERNAL, pick_template

    try:
        notif_cfg = NotificationConfig.get_solo()
        recipients = notif_cfg.get_whatsapp_recipients_list()
        if not recipients:
            return "skipped:no_whatsapp_recipients"

        case = CaseRecord.objects.select_related("requester", "category").get(id=case_id)
        site_name = SiteConfig.get_solo().site_name
        base_url = _get_site_base_url()
        case_url = f"{base_url}/desk/cases/{case.id}/"

        requester_display = (
            case.requester.full_name if case.requester else (case.requester_name or "—")
        )
        source_label = case.get_source_display()

        # Shuffle to avoid always sending to the same person first
        recipients_shuffled = list(recipients)
        random.shuffle(recipients_shuffled)

        for i, phone in enumerate(recipients_shuffled):
            # Pick a distinct template variant per recipient using phone as seed
            text = pick_template(WA_NOTIF_INTERNAL, seed=f"{case.case_number}-{phone}").format(
                site=site_name,
                ticket_num=case.case_number,
                requester_name=requester_display,
                subject=case.subject[:100],
                priority=case.get_priority_display(),
                source_label=source_label,
                case_url=case_url,
            )
            # Stagger: first recipient after 5-10 s, each subsequent one 20-45 s later
            countdown = random.randint(5, 10) + i * random.randint(20, 45)
            _send_single_wa_notif_task.apply_async(args=[phone, text], countdown=countdown)
            logger.info(
                "Scheduled WA notif to %s for case %s (countdown=%ss)",
                phone, case.case_number, countdown,
            )

        return f"dispatched:{len(recipients_shuffled)}_recipients"

    except CaseRecord.DoesNotExist:
        return "error:case_not_found"
    except Exception as exc:
        logger.error("send_new_ticket_wa_notif_task failed: %s", exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"


@shared_task(
    name="gateways._send_single_wa_notif_task",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def _send_single_wa_notif_task(phone: str, text: str) -> str:
    """Send one pre-composed WA notification to a single internal recipient."""
    from django.core.cache import cache as _cache
    from gateways.services import EvolutionNotifService
    from gateways.throttle import WARateLimiter, is_within_business_hours, is_circuit_open

    # Internal broadcast notifications only go out during business hours
    if not is_within_business_hours():
        logger.info("WA notif to %s deferred: outside business hours", phone)
        return "skipped:outside_business_hours"

    if is_circuit_open():
        logger.warning("WA notif to %s skipped: circuit breaker is open", phone)
        return "skipped:circuit_open"

    # Opt-in guard: only send to recipients who have messaged us in the last 7 days
    clean_phone = phone.lstrip("+")
    if not _cache.get(f"wa_last_inbound:{clean_phone}"):
        logger.info("WA notif to %s skipped: no inbound in last 7 days (opt-in guard)", phone)
        return "skipped:no_recent_inbound"

    allowed, reason = WARateLimiter.check_and_record(phone)
    if not allowed:
        logger.warning("WA notif throttled for %s: %s", phone, reason)
        return f"skipped:{reason}"

    try:
        # Use the dedicated notification instance if configured (EVOLUTION_NOTIF_INSTANCE_NAME),
        # otherwise falls back to the main customer-facing instance transparently.
        svc = EvolutionNotifService()
        svc.send_with_human_pacing(phone, text)
        logger.info("WA notif sent to %s via instance '%s'", phone, svc.instance)
        return "success"
    except Exception as exc:
        logger.error("_send_single_wa_notif_task failed for %s: %s", phone, exc)
        return "error:send_failed"


@shared_task(
    bind=True,
    name="gateways.send_new_ticket_email_notif_task",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def send_new_ticket_email_notif_task(self, case_id: str) -> str:
    """
    Send an email notification to internal recipients (agents/admins)
    when a new support ticket is created.
    """
    import html as html_mod
    from cases.models import CaseRecord
    from core.models import NotificationConfig, SiteConfig
    from django.core.mail import EmailMultiAlternatives

    try:
        notif_cfg = NotificationConfig.get_solo()
        recipients = notif_cfg.get_email_recipients_list()
        if not recipients:
            return "skipped:no_email_recipients"

        case = CaseRecord.objects.select_related("requester", "category").get(id=case_id)
        site_name = SiteConfig.get_solo().site_name
        base_url = _get_site_base_url()
        case_url = f"{base_url}/desk/cases/{case.id}/"

        requester_display = (
            case.requester.full_name if case.requester else (case.requester_name or "—")
        )
        priority_colors = {
            "critical": "#ef4444", "high": "#f97316",
            "medium": "#eab308", "low": "#22c55e",
        }
        priority_color = priority_colors.get(case.priority, "#6b7280")

        safe_subject = html_mod.escape(case.subject)
        safe_requester = html_mod.escape(requester_display)
        safe_site = html_mod.escape(site_name)
        safe_case_number = html_mod.escape(case.case_number)

        subject = f"[{safe_site}] New Ticket: {safe_case_number} — {safe_subject}"

        plain_body = (
            f"A new support ticket has been submitted in {site_name}.\n\n"
            f"Ticket #  : {case.case_number}\n"
            f"From      : {requester_display}\n"
            f"Subject   : {case.subject}\n"
            f"Priority  : {case.get_priority_display()}\n"
            f"Source    : {case.get_source_display()}\n\n"
            f"Open ticket: {case_url}\n"
        )

        html_body = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;padding:32px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);">
        <tr>
          <td style="background:#1e293b;padding:20px 28px;">
            <span style="color:#ffffff;font-size:16px;font-weight:700;">{safe_site}</span>
            <span style="color:#94a3b8;font-size:13px;margin-left:8px;">Support Desk</span>
          </td>
        </tr>
        <tr>
          <td style="padding:28px;">
            <p style="margin:0 0 16px;font-size:18px;font-weight:700;color:#0f172a;">🎫 New Ticket Received</p>
            <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
              <tr style="background:#f8fafc;">
                <td style="padding:10px 14px;font-size:12px;font-weight:600;color:#64748b;width:110px;">Ticket #</td>
                <td style="padding:10px 14px;font-size:13px;font-weight:700;color:#0f172a;font-family:monospace;">{safe_case_number}</td>
              </tr>
              <tr>
                <td style="padding:10px 14px;font-size:12px;font-weight:600;color:#64748b;border-top:1px solid #e2e8f0;">From</td>
                <td style="padding:10px 14px;font-size:13px;color:#0f172a;border-top:1px solid #e2e8f0;">{safe_requester}</td>
              </tr>
              <tr style="background:#f8fafc;">
                <td style="padding:10px 14px;font-size:12px;font-weight:600;color:#64748b;border-top:1px solid #e2e8f0;">Subject</td>
                <td style="padding:10px 14px;font-size:13px;color:#0f172a;border-top:1px solid #e2e8f0;">{safe_subject}</td>
              </tr>
              <tr>
                <td style="padding:10px 14px;font-size:12px;font-weight:600;color:#64748b;border-top:1px solid #e2e8f0;">Priority</td>
                <td style="padding:10px 14px;border-top:1px solid #e2e8f0;">
                  <span style="background:{priority_color};color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:4px;">{html_mod.escape(case.get_priority_display())}</span>
                </td>
              </tr>
              <tr style="background:#f8fafc;">
                <td style="padding:10px 14px;font-size:12px;font-weight:600;color:#64748b;border-top:1px solid #e2e8f0;">Source</td>
                <td style="padding:10px 14px;font-size:13px;color:#0f172a;border-top:1px solid #e2e8f0;">{html_mod.escape(case.get_source_display())}</td>
              </tr>
            </table>
            <div style="margin-top:20px;text-align:center;">
              <a href="{case_url}" style="display:inline-block;background:#1e293b;color:#ffffff;text-decoration:none;font-size:13px;font-weight:600;padding:10px 24px;border-radius:6px;">Open Ticket →</a>
            </div>
          </td>
        </tr>
        <tr>
          <td style="padding:16px 28px;background:#f8fafc;border-top:1px solid #e2e8f0;text-align:center;">
            <span style="font-size:11px;color:#94a3b8;">Automated notification from {safe_site} — do not reply to this email</span>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

        email_msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_body,
            to=recipients,
        )
        email_msg.attach_alternative(html_body, "text/html")
        email_msg.send(fail_silently=False)

        logger.info(
            "Email notif sent for case %s to %d recipient(s)",
            case.case_number, len(recipients),
        )
        return "success"

    except CaseRecord.DoesNotExist:
        return "error:case_not_found"
    except Exception as exc:
        logger.error("send_new_ticket_email_notif_task failed: %s", exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"


# =====================================================================
# Chat Portal — Staff Reply Notification
# =====================================================================

@shared_task(
    bind=True,
    name="gateways.notify_staff_chat_reply_task",
    max_retries=2,
    default_retry_delay=15,
    acks_late=True,
)
def notify_staff_chat_reply_task(self, message_id: str) -> str:
    """
    Email staff when a portal user sends a new message in an existing ticket.

    Only fires when:
    - NotificationConfig.notify_new_ticket_email is enabled (reuses same setting)
    - The ticket has not been viewed by staff in the last 2 minutes
      (avoids flooding when a staff member is actively watching the thread)
    """
    import html as html_mod
    from datetime import timedelta

    from cases.models import CaseRecord, Message
    from core.models import NotificationConfig, SiteConfig
    from django.core.mail import EmailMultiAlternatives
    from django.utils import timezone

    try:
        msg = Message.objects.select_related(
            "case__requester", "case__category", "case__assigned_to"
        ).get(id=message_id)
    except Message.DoesNotExist:
        return "error:message_not_found"

    try:
        case = msg.case

        notif_cfg = NotificationConfig.get_solo()
        if not notif_cfg.notify_new_ticket_email:
            return "skipped:email_notif_disabled"

        # Skip if staff has been on this ticket in the last 2 min
        if case.last_viewed_at and (timezone.now() - case.last_viewed_at) < timedelta(minutes=2):
            return "skipped:staff_active"

        recipients = notif_cfg.get_email_recipients_list()
        # Prefer assigned staff's email if set
        if case.assigned_to and case.assigned_to.email:
            recipients = [case.assigned_to.email]
        if not recipients:
            return "skipped:no_recipients"

        site_name = SiteConfig.get_solo().site_name
        base_url = _get_site_base_url()
        case_url = f"{base_url}/desk/cases/{case.id}/"

        safe_site = html_mod.escape(site_name)
        safe_num = html_mod.escape(case.case_number)
        safe_subj = html_mod.escape(case.subject)
        safe_from = html_mod.escape(case.requester_name or case.requester_email or "Portal User")
        safe_body = html_mod.escape((msg.body or "")[:300])

        email_subject = f"[{safe_site}] New chat message in {safe_num}"

        plain = (
            f"A portal user sent a new message in ticket {case.case_number}.\n\n"
            f"From   : {safe_from}\n"
            f"Ticket : {case.subject}\n"
            f"Message: {msg.body[:300]}\n\n"
            f"Open ticket: {case_url}\n"
        )

        html = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;padding:32px 0;">
    <tr><td align="center">
      <table width="540" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);">
        <tr>
          <td style="background:#4f46e5;padding:20px 28px;">
            <span style="color:#fff;font-size:15px;font-weight:700;">{safe_site}</span>
            <span style="color:#a5b4fc;font-size:12px;margin-left:8px;">Chat Notification</span>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 28px;">
            <p style="margin:0 0 12px;font-size:16px;font-weight:700;color:#0f172a;">💬 New Portal Chat Message</p>
            <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;margin-bottom:16px;">
              <tr style="background:#f8fafc;">
                <td style="padding:8px 12px;font-size:11px;font-weight:600;color:#64748b;width:80px;">Ticket</td>
                <td style="padding:8px 12px;font-size:12px;font-weight:700;color:#0f172a;font-family:monospace;">{safe_num}</td>
              </tr>
              <tr>
                <td style="padding:8px 12px;font-size:11px;font-weight:600;color:#64748b;border-top:1px solid #e2e8f0;">From</td>
                <td style="padding:8px 12px;font-size:12px;color:#0f172a;border-top:1px solid #e2e8f0;">{safe_from}</td>
              </tr>
              <tr style="background:#f8fafc;">
                <td style="padding:8px 12px;font-size:11px;font-weight:600;color:#64748b;border-top:1px solid #e2e8f0;">Subject</td>
                <td style="padding:8px 12px;font-size:12px;color:#0f172a;border-top:1px solid #e2e8f0;">{safe_subj}</td>
              </tr>
            </table>
            <div style="background:#f1f5f9;border-left:3px solid #4f46e5;border-radius:4px;padding:12px 14px;font-size:13px;color:#334155;line-height:1.5;">
              {safe_body}
            </div>
            <div style="margin-top:20px;text-align:center;">
              <a href="{case_url}" style="background:#4f46e5;color:#fff;text-decoration:none;padding:10px 24px;border-radius:8px;font-size:13px;font-weight:600;">
                Open in Support Desk →
              </a>
            </div>
          </td>
        </tr>
        <tr>
          <td style="padding:12px 28px;background:#f8fafc;border-top:1px solid #e2e8f0;font-size:11px;color:#94a3b8;text-align:center;">
            {safe_site} — automated notification. Do not reply to this email.
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

        email_msg = EmailMultiAlternatives(
            subject=email_subject,
            body=plain,
            to=recipients,
        )
        email_msg.attach_alternative(html, "text/html")
        email_msg.send(fail_silently=False)
        return f"success:{len(recipients)}_recipients"

    except Exception as exc:
        logger.error("notify_staff_chat_reply_task failed: %s", exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"


# =====================================================================
# WhatsApp Presence Lifecycle — Business Hours Online/Offline
# =====================================================================

@shared_task(name="gateways.wa_circuit_health_check_task")
def wa_circuit_health_check_task() -> str:
    """
    Periodically test the WA instance state and auto-reset the circuit breaker
    if the instance is back online. Scheduled every 30 minutes via Celery Beat.
    """
    from gateways.services import EvolutionAPIService
    from gateways.throttle import is_circuit_open, record_disconnect, reset_circuit

    try:
        svc = EvolutionAPIService()
        state_data = svc.get_instance_state()
        state = (
            state_data.get("instance", {}).get("state")
            if state_data
            else None
        )

        if state == "open":
            if is_circuit_open():
                reset_circuit()
                logger.info("wa_circuit_health_check: instance is open — circuit reset.")
            return "ok:connected"
        else:
            record_disconnect()
            logger.warning(
                "wa_circuit_health_check: instance state=%s — disconnect recorded.",
                state,
            )
            return f"warning:state={state}"

    except Exception as exc:
        logger.error("wa_circuit_health_check_task failed: %s", exc)
        return f"error:{exc}"


@shared_task(name="gateways.wa_set_online_task")
def wa_set_online_task() -> str:
    """
    Set WhatsApp presence to 'available' at the start of business hours.
    Schedule this via Celery beat at 07:00 WIB Mon-Fri.
    """
    import random
    import time
    from gateways.services import EvolutionAPIService

    try:
        # Small jitter so the cron doesn't fire at the exact same second every day
        time.sleep(random.uniform(10, 90))
        svc = EvolutionAPIService()
        # Evolution API sets instance-level presence via /chat/updatePresence
        url = svc._build_url("chat/updatePresence")
        import requests as _req
        _req.post(url, json={"presence": "available"}, headers=svc._headers(), timeout=15)
        logger.info("WA instance presence set to available")
        return "success"
    except Exception as exc:
        logger.warning("wa_set_online_task failed: %s", exc)
        return f"error:{exc}"


@shared_task(name="gateways.wa_set_offline_task")
def wa_set_offline_task() -> str:
    """
    Set WhatsApp presence to 'unavailable' at end of business hours.
    Schedule this via Celery beat at 20:00 WIB Mon-Fri.
    """
    import random
    import time
    from gateways.services import EvolutionAPIService

    try:
        time.sleep(random.uniform(10, 90))
        svc = EvolutionAPIService()
        url = svc._build_url("chat/updatePresence")
        import requests as _req
        _req.post(url, json={"presence": "unavailable"}, headers=svc._headers(), timeout=15)
        logger.info("WA instance presence set to unavailable")
        return "success"
    except Exception as exc:
        logger.warning("wa_set_offline_task failed: %s", exc)
        return f"error:{exc}"


# =====================================================================
# Teams Bot 2-Way Webhook Processing
# =====================================================================

def _validate_teams_jwt(token: str, bot_app_id: str) -> bool:
    """
    Validate a Microsoft Bot Framework JWT Bearer token.

    Uses the cryptography package (already a transitive dependency via
    django-encrypted-model-fields) to verify the RS256 signature against
    Microsoft's published JWKS endpoint — no extra pip packages required.
    """
    import base64
    import json as _json
    import time

    import requests as http_requests
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

    def _b64url_decode(s: str) -> bytes:
        pad = 4 - len(s) % 4
        if pad != 4:
            s += "=" * pad
        return base64.urlsafe_b64decode(s)

    def _int_from_b64url(s: str) -> int:
        return int.from_bytes(_b64url_decode(s), "big")

    try:
        parts = token.split(".")
        if len(parts) != 3:
            logger.warning("Teams JWT: malformed token (not 3 parts)")
            return False

        header = _json.loads(_b64url_decode(parts[0]))
        claims = _json.loads(_b64url_decode(parts[1]))

        # Verify basic claims
        now = time.time()
        if claims.get("exp", 0) < now:
            logger.warning("Teams JWT: token expired")
            return False
        if claims.get("nbf", now) > now + 300:
            logger.warning("Teams JWT: token not yet valid (clock skew)")
            return False
        if claims.get("aud") != bot_app_id:
            logger.warning(
                "Teams JWT: audience mismatch (got %s, expected %s)",
                claims.get("aud"), bot_app_id,
            )
            return False
        valid_issuers = {
            "https://api.botframework.com",
            f"https://sts.windows.net/{claims.get('tid', '')}/",
            f"https://login.microsoftonline.com/{claims.get('tid', '')}/v2.0",
        }
        if claims.get("iss") not in valid_issuers:
            logger.warning("Teams JWT: unrecognised issuer %s", claims.get("iss"))
            return False

        alg = header.get("alg", "")
        kid = header.get("kid", "")
        if alg != "RS256" or not kid:
            logger.warning("Teams JWT: unexpected alg=%s or missing kid", alg)
            return False

        # Fetch JWKS from Microsoft
        oid_resp = http_requests.get(
            "https://login.botframework.com/v1/.well-known/openid-configuration",
            timeout=5,
        )
        oid_resp.raise_for_status()
        jwks_uri = oid_resp.json().get("jwks_uri")
        if not jwks_uri:
            logger.warning("Teams JWT: could not find jwks_uri in OpenID config")
            return False

        jwks_resp = http_requests.get(jwks_uri, timeout=5)
        jwks_resp.raise_for_status()
        keys = jwks_resp.json().get("keys", [])

        jwk = next((k for k in keys if k.get("kid") == kid), None)
        if not jwk:
            logger.warning("Teams JWT: no matching key for kid=%s", kid)
            return False

        # Build RSA public key from JWK (n, e fields)
        pub_key = RSAPublicNumbers(
            e=_int_from_b64url(jwk["e"]),
            n=_int_from_b64url(jwk["n"]),
        ).public_key(default_backend())

        # Verify RS256 signature
        signing_input = f"{parts[0]}.{parts[1]}".encode()
        signature = _b64url_decode(parts[2])
        pub_key.verify(signature, signing_input, asym_padding.PKCS1v15(), hashes.SHA256())
        return True

    except InvalidSignature:
        logger.warning("Teams JWT: signature verification failed")
        return False
    except Exception as exc:
        logger.warning("Teams JWT: validation error — %s", exc)
        return False


@shared_task(
    bind=True,
    name="gateways.process_teams_webhook_task",
    max_retries=3,
    default_retry_delay=10,
    acks_late=True,
)
def process_teams_webhook_task(self, payload: dict, auth_token: str = "") -> str:
    """
    Process an inbound Microsoft Teams Bot Framework activity.

    - Validates the JWT Bearer token (if bot_app_id is configured)
    - Matches or creates a CaseRecord keyed on the Teams conversation ID
    - Creates an inbound Message record
    - Dispatches new-ticket notifications on first message in a session
    """
    from cases.models import CaseRecord, Message
    from core.models import TeamsConfig, Employee

    try:
        teams_cfg = TeamsConfig.get_solo()
        if not teams_cfg.is_bot_enabled:
            return "skipped:bot_disabled"

        # JWT validation — skip only if app ID is not yet configured
        if teams_cfg.bot_app_id and auth_token:
            if not _validate_teams_jwt(auth_token, teams_cfg.bot_app_id):
                logger.warning(
                    "process_teams_webhook_task: JWT validation failed, dropping activity"
                )
                return "error:jwt_invalid"
        elif teams_cfg.bot_app_id and not auth_token:
            logger.warning("process_teams_webhook_task: no auth token provided")
            return "error:no_auth_token"

        activity_type = payload.get("type", "")
        if activity_type != "message":
            logger.debug("Teams activity type '%s' ignored.", activity_type)
            return f"skipped:activity_type_{activity_type}"

        # Extract Teams activity fields
        text = (payload.get("text") or "").strip()
        if not text:
            # Ignore empty messages (e.g. attachments only, typing indicators)
            return "skipped:empty_text"

        from_info = payload.get("from") or {}
        teams_user_id = from_info.get("id", "")
        teams_user_name = from_info.get("name", "Unknown Teams User")

        conversation = payload.get("conversation") or {}
        conversation_id = conversation.get("id", "")
        if not conversation_id:
            return "error:no_conversation_id"

        activity_id = payload.get("id", "")
        service_url = payload.get("serviceUrl", "")
        tenant_id = (payload.get("channelData") or {}).get("tenant", {}).get("id", "")

        # Dedup: skip if we already have this activity
        if activity_id and Message.objects.filter(
            channel=Message.Channel.TEAMS,
            external_id=activity_id,
        ).exists():
            return "skipped:duplicate"

        # Try to match an Employee by display name (best-effort)
        employee = None
        if teams_user_name:
            employee = Employee.objects.filter(
                full_name__iexact=teams_user_name,
            ).first()

        # Find an open case threaded on this Teams conversation
        session_window = timezone.now() - timedelta(minutes=120)
        active_case = (
            CaseRecord.objects.filter(
                source=CaseRecord.Source.TEAMS_BOT,
                status__in=[
                    CaseRecord.Status.OPEN,
                    CaseRecord.Status.INVESTIGATING,
                    CaseRecord.Status.PENDING_INFO,
                ],
                updated_at__gte=session_window,
            )
            .filter(form_data__teams_conversation_id=conversation_id)
            .order_by("-updated_at")
            .first()
        )

        is_new_case = False
        if active_case:
            case = active_case
            logger.info(
                "Threading Teams message into case %s (conv=%s).",
                case.case_number, conversation_id[:20],
            )
        else:
            default_category = _get_or_create_default_category()
            case = CaseRecord.objects.create(
                requester=employee,
                category=default_category,
                subject=f"Teams: {text[:80]}",
                problem_description=text,
                status=CaseRecord.Status.OPEN,
                source=CaseRecord.Source.TEAMS_BOT,
                requester_name=teams_user_name,
                requester_email=(employee.email if employee else ""),
                requester_unit_name=(employee.unit.name if employee and employee.unit else ""),
                requester_job_role=(employee.job_role if employee else ""),
                form_data={
                    "teams_conversation_id": conversation_id,
                    "teams_user_id": teams_user_id,
                    "teams_service_url": service_url,
                    "teams_tenant_id": tenant_id,
                },
            )
            is_new_case = True
            logger.info(
                "Created new case %s from Teams Bot (conv=%s).",
                case.case_number, conversation_id[:20],
            )

        # Update service_url on the TeamsConfig singleton (latest value wins)
        if service_url and service_url != teams_cfg.service_url:
            TeamsConfig.objects.filter(pk=teams_cfg.pk).update(service_url=service_url)

        Message.objects.create(
            case=case,
            sender_employee=employee,
            body=text,
            direction=Message.Direction.INBOUND,
            channel=Message.Channel.TEAMS,
            external_id=activity_id,
        )

        # Mark case as having unread messages
        case.has_unread_messages = True
        case.save(update_fields=["has_unread_messages"])

        if is_new_case:
            _dispatch_new_ticket_notifs(str(case.id))

        return "success"

    except Exception as exc:
        logger.exception("process_teams_webhook_task failed: %s", exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return "error:max_retries"
