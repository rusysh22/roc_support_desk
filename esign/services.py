"""
E-Signature — Service Layer (State Machine)
============================================
All state transitions for SignatureDocument live here. Views and tasks call
these functions; templates and models never drive state changes directly.

Transitions:
  send()    DRAFT     → PENDING   (generates tokens, emails first/all signers)
  sign()    PENDING   → SIGNED    (stamps image, advances sequential chain)
  reject()  PENDING   → REJECTED  (requires a reason, stops the flow)
  reopen()  REJECTED|PENDING → PENDING (regenerates tokens, re-sends)
  remind()  —         (re-sends email to a single PENDING signer)
  cancel()  any       → CANCELLED
"""
import secrets
import string
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Signer, SignatureDocument, SignatureEvent


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Reminders to the same signer are limited to one per this window (counted from the
# last signing request email, so a fresh request also starts the window).
REMIND_COOLDOWN = timedelta(hours=6)

# Events that contributors are emailed about (views and reminders are left out).
CONTRIBUTOR_EVENTS = {
    SignatureEvent.Event.SENT,
    SignatureEvent.Event.SIGNED,
    SignatureEvent.Event.REJECTED,
    SignatureEvent.Event.COMPLETED,
    SignatureEvent.Event.REOPENED,
    SignatureEvent.Event.CANCELLED,
    SignatureEvent.Event.AMENDED,
    SignatureEvent.Event.REASSIGNED,
    SignatureEvent.Event.FORCE_COMPLETED,
}


def _log(document, event, *, actor_user=None, actor_label="", ip=None, detail=""):
    SignatureEvent.objects.create(
        document=document,
        event=event,
        actor_user=actor_user,
        actor_label=actor_label,
        ip=ip,
        detail=detail,
    )
    if event in CONTRIBUTOR_EVENTS:
        notify_contributors(document, event, actor_user=actor_user, actor_label=actor_label, detail=detail)


def notify_contributors(document, event, *, actor_user=None, actor_label="", detail=""):
    """Email every contributor (except whoever did it) that the document changed."""
    recipients = list(
        document.contributors.filter(is_active=True).exclude(email="").exclude(
            pk=getattr(actor_user, "pk", None),
        ).values_list("pk", flat=True)
    )
    if not recipients:
        return
    actor = (actor_user.get_full_name() or actor_user.username) if actor_user else (actor_label or "System")
    from .tasks import send_contributor_update_task
    send_contributor_update_task.delay(
        str(document.pk), [str(pk) for pk in recipients],
        SignatureEvent.Event(event).label, actor, detail,
    )


def _request_signature(signer, *, notify=True):
    """Mark a signing request as sent now and email it (unless ``notify`` is False)."""
    signer.last_requested_at = timezone.now()
    signer.save(update_fields=["last_requested_at"])
    if notify:
        from .tasks import send_signature_request_task
        send_signature_request_task.delay(str(signer.id))


def next_remind_at(signer):
    """When a reminder to this signer is allowed again (None = now)."""
    if not signer.last_requested_at:
        return None
    allowed = signer.last_requested_at + REMIND_COOLDOWN
    return allowed if allowed > timezone.now() else None


def compact_orders(orders):
    """Renumber sign orders to 1..n keeping ties: [1, 3, 3, 7] → [1, 2, 2, 3]."""
    ranks = {value: rank for rank, value in enumerate(sorted(set(orders)), start=1)}
    return [ranks[value] for value in orders]


def default_job_role(signer):
    """Job role to pre-fill on the signing page: the signer's last choice, the external
    signer's job title, else the Employee record linked to the user (free text either way)."""
    if signer.stamp_job_role_text:
        return signer.stamp_job_role_text
    if signer.job_title:
        return signer.job_title
    if signer.user_id:
        from django.db.models import Q
        from core.models import Employee
        match = Q(user_id=signer.user_id)
        if signer.user.email:
            match |= Q(email__iexact=signer.user.email)
        employee = Employee.objects.filter(match).exclude(job_role="").order_by("-user_id").first()
        if employee:
            return employee.job_role
    return ""


def _token_expiry(days=30):
    return timezone.now() + timedelta(days=days)


def _generate_verify_code():
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(4))


def _assign_token(signer):
    signer.token = uuid.uuid4()
    signer.token_expires_at = _token_expiry()
    # External signers get a separate verification code sent in the email.
    # System users authenticate via login; they don't need a code.
    signer.verify_code = _generate_verify_code() if not signer.user_id else ""
    signer.save(update_fields=["token", "token_expires_at", "verify_code"])


# ---------------------------------------------------------------------------
# send — DRAFT → PENDING
# ---------------------------------------------------------------------------

def send_document(document, actor_user=None, ip=None):
    """
    Transition a DRAFT document into PENDING and dispatch signing requests.

    Sequential: only the first-order signer is set PENDING; the rest WAITING.
    Parallel: all signers are set PENDING simultaneously.

    Raises ValueError if the document is not in DRAFT status or has no signers.
    """
    if document.status != SignatureDocument.Status.DRAFT:
        raise ValueError("Only a DRAFT document can be sent.")

    signers = list(document.signers.order_by("order"))
    if not signers:
        raise ValueError("Add at least one signer before sending.")

    # Generate fresh tokens for all
    for signer in signers:
        _assign_token(signer)

    if document.routing_mode == SignatureDocument.RoutingMode.SEQUENTIAL:
        first_order = signers[0].order
        for signer in signers:
            if signer.order == first_order:
                signer.status = Signer.Status.PENDING
            else:
                signer.status = Signer.Status.WAITING
            signer.save(update_fields=["status"])
        pending_signers = [s for s in signers if s.order == first_order]
    else:
        for signer in signers:
            signer.status = Signer.Status.PENDING
            signer.save(update_fields=["status"])
        pending_signers = signers

    document.status = SignatureDocument.Status.PENDING
    document.save(update_fields=["status"])

    _log(document, SignatureEvent.Event.SENT, actor_user=actor_user, ip=ip)

    # Fire email tasks (imported here to avoid circular imports)
    for signer in pending_signers:
        _request_signature(signer, notify=not (actor_user and signer.user_id == actor_user.pk))


# ---------------------------------------------------------------------------
# sign — Signer PENDING → SIGNED
# ---------------------------------------------------------------------------

def record_signature(signer, signature_image_file, ip=None, user_agent="", actor_user=None):
    """
    Record a completed signature for a signer.

    Saves the signature image, updates status and audit fields, then:
    - Sequential: activates the next signer (or finalizes if none remain).
    - Parallel: finalizes when all signers have signed.

    Raises ValueError if the signer is not currently PENDING.
    """
    # Atomically claim the PENDING -> SIGNED transition under a row lock so two
    # concurrent submits for the same signer can't both succeed. The lock is
    # released immediately after — the slower image/PDF work below runs
    # unlocked since only one caller can ever reach it per signer.
    with transaction.atomic():
        locked = Signer.objects.select_for_update().get(pk=signer.pk)
        if locked.status != Signer.Status.PENDING:
            raise ValueError("This signer is not waiting for action.")
        locked.status = Signer.Status.SIGNED
        locked.save(update_fields=["status"])

    from django.core.files.base import ContentFile
    from PIL import Image
    import io

    # Re-encode through Pillow (PNG) to strip any malicious metadata
    img = Image.open(signature_image_file).convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    signer.signature_image.save(
        f"sig_{signer.id}.png",
        ContentFile(buf.read()),
        save=False,
    )
    signer.status       = Signer.Status.SIGNED
    signer.acted_at     = timezone.now()
    signer.signed_ip    = ip
    signer.signed_user_agent = user_agent
    signer.save(update_fields=[
        "signature_image", "status", "acted_at", "signed_ip", "signed_user_agent"
    ])

    document = signer.document
    actor_label = signer.display_name if not actor_user else ""
    _log(
        document, SignatureEvent.Event.SIGNED,
        actor_user=actor_user, actor_label=actor_label, ip=ip,
    )

    _advance_or_finalize(document)


def _update_preview_pdf(document):
    """Generate and save an intermediate PDF with all completed signatures so far."""
    from .utils_stamp import stamp_document
    from django.core.files.base import ContentFile

    pdf_bytes = stamp_document(document)
    if not pdf_bytes:
        return
    import uuid
    safe_title = document.title.replace(" ", "_")[:50]
    random_hash = uuid.uuid4().hex[:6]
    if document.preview_pdf:
        document.preview_pdf.delete(save=False)
    # save=False then explicit update_fields avoids overwriting other fields
    document.preview_pdf.save(
        f"{safe_title}_{random_hash}_preview.pdf",
        ContentFile(pdf_bytes),
        save=False,
    )
    document.save(update_fields=["preview_pdf"])


def _advance_or_finalize(document):
    """After a signing action, move the workflow forward or complete it."""
    from .tasks import send_signature_completed_task

    if document.routing_mode == SignatureDocument.RoutingMode.SEQUENTIAL:
        # Find the next WAITING signer with the lowest order
        next_signer = document.signers.filter(
            status=Signer.Status.WAITING
        ).order_by("order").first()

        if next_signer:
            # Generate preview so the next signer sees all previous signatures
            _update_preview_pdf(document)
            _assign_token(next_signer)
            next_signer.status = Signer.Status.PENDING
            next_signer.save(update_fields=["status"])
            _request_signature(next_signer)
            return

    # All done (sequential exhausted, or parallel all signed)?
    remaining = document.signers.filter(
        status__in=[Signer.Status.WAITING, Signer.Status.PENDING]
    ).count()

    if remaining == 0:
        _finalize(document)
    else:
        # Parallel: some signed, others still pending — update preview for them
        _update_preview_pdf(document)


def _finalize(document):
    """Stamp the PDF, generate the signed file, and notify all parties."""
    from .tasks import send_signature_completed_task
    from .utils_stamp import stamp_document

    pdf_bytes = stamp_document(document)
    if pdf_bytes:
        from django.core.files.base import ContentFile
        import uuid
        safe_title = document.title.replace(" ", "_")[:60]
        random_hash = uuid.uuid4().hex[:6]
        document.signed_pdf.save(
            f"{safe_title}_{random_hash}_signed.pdf",
            ContentFile(pdf_bytes),
            save=False,
        )

    document.status = SignatureDocument.Status.COMPLETED
    document.save(update_fields=["status", "signed_pdf"])

    _log(document, SignatureEvent.Event.COMPLETED)
    send_signature_completed_task.delay(str(document.id))

def force_complete_document(document, actor_user=None, ip=None):
    """
    Force complete a pending document, skipping any remaining signatures.
    """
    if document.status != SignatureDocument.Status.PENDING:
        raise ValueError("Only in-progress documents can be force completed.")
        
    # Rotate tokens for remaining signers so their old links stop working.
    # `token` is unique+not-null, so it's regenerated (never set to None) —
    # the new value is simply never sent to anyone.
    for signer in document.signers.filter(status__in=[Signer.Status.WAITING, Signer.Status.PENDING]):
        signer.token = uuid.uuid4()
        signer.save(update_fields=['token'])
        
    _log(document, SignatureEvent.Event.FORCE_COMPLETED, actor_user=actor_user, ip=ip,
         detail="Document force completed by requestor. Remaining signatures skipped.")
         
    _finalize(document)

# ---------------------------------------------------------------------------
# reject — Signer PENDING → REJECTED
# ---------------------------------------------------------------------------

def reject_signature(signer, notes, ip=None, actor_user=None):
    """
    Record a rejection. Notes (reason) are mandatory.
    Stops the entire document flow and notifies the owner.

    Raises ValueError if notes is empty or the signer is not PENDING.
    """
    if not notes or not notes.strip():
        raise ValueError("A rejection reason is required.")

    with transaction.atomic():
        locked = Signer.objects.select_for_update().get(pk=signer.pk)
        if locked.status != Signer.Status.PENDING:
            raise ValueError("This signer is not waiting for action.")
        locked.status = Signer.Status.REJECTED
        locked.save(update_fields=["status"])

    signer.status   = Signer.Status.REJECTED
    signer.notes    = notes.strip()
    signer.acted_at = timezone.now()
    signer.signed_ip = ip
    signer.save(update_fields=["status", "notes", "acted_at", "signed_ip"])

    document = signer.document
    document.status = SignatureDocument.Status.REJECTED
    document.save(update_fields=["status"])

    actor_label = signer.display_name if not actor_user else ""
    _log(
        document, SignatureEvent.Event.REJECTED,
        actor_user=actor_user, actor_label=actor_label, ip=ip,
        detail=notes.strip(),
    )

    from .tasks import send_signature_rejected_task
    send_signature_rejected_task.delay(str(document.id), str(signer.id))


# ---------------------------------------------------------------------------
# reopen — REJECTED / PENDING → PENDING (reset and re-send)
# ---------------------------------------------------------------------------

def reopen_signer(signer, actor_user=None, ip=None):
    """
    Reopen a specific signer. Resets their status and regenerates their token.
    If the document was already completed, it is moved back to PENDING.
    The document's signed_pdf is deleted so it can be regenerated with the remaining signatures.
    """
    document = signer.document
    if signer.status not in (Signer.Status.SIGNED, Signer.Status.REJECTED):
        raise ValueError("Only SIGNED or REJECTED signers can be reopened.")
    
    # Reset signer
    signer.status = Signer.Status.WAITING
    signer.notes = ""
    signer.acted_at = None
    if signer.signature_image:
        signer.signature_image.delete(save=False)
    signer.signature_image = None
    _assign_token(signer)
    signer.save(update_fields=["status", "notes", "acted_at", "token", "token_expires_at", "signature_image"])

    # If document was completed, reset it
    if document.status == SignatureDocument.Status.COMPLETED:
        document.status = SignatureDocument.Status.PENDING
    if document.signed_pdf:
        document.signed_pdf.delete(save=False)
        document.signed_pdf = None
    document.save(update_fields=["status", "signed_pdf"])
    
    # Recalculate routing state and regenerate preview pdf (stamped with remaining signatures)
    _advance_or_finalize(document)
    
    _log(document, SignatureEvent.Event.REOPENED, actor_user=actor_user, ip=ip,
         detail=f"Reopened signer: {signer.display_name}")

def reopen_document(document, actor_user=None, ip=None):
    """
    Reset all signers and re-send the document for signing.

    Allowed from REJECTED or PENDING status. Regenerates all tokens so
    previously used links no longer work.

    Raises ValueError if the document cannot be reopened.
    """
    if document.status not in (
        SignatureDocument.Status.REJECTED,
        SignatureDocument.Status.PENDING,
    ):
        raise ValueError("Only REJECTED or PENDING documents can be reopened.")

    signers = list(document.signers.order_by("order"))
    for signer in signers:
        signer.status   = Signer.Status.WAITING
        signer.notes    = ""
        signer.acted_at = None
        _assign_token(signer)
        signer.save(update_fields=["status", "notes", "acted_at", "token", "token_expires_at"])

    if document.preview_pdf:
        document.preview_pdf.delete(save=False)
        document.preview_pdf = None
    document.status = SignatureDocument.Status.PENDING
    document.save(update_fields=["status", "preview_pdf"])

    _log(document, SignatureEvent.Event.REOPENED, actor_user=actor_user, ip=ip)

    # Trigger the first/all signers depending on mode
    if document.routing_mode == SignatureDocument.RoutingMode.SEQUENTIAL:
        first = signers[0] if signers else None
        if first:
            first.status = Signer.Status.PENDING
            first.save(update_fields=["status"])
            _request_signature(first)
    else:
        for signer in signers:
            signer.status = Signer.Status.PENDING
            signer.save(update_fields=["status"])
            _request_signature(signer)


# ---------------------------------------------------------------------------
# remind — re-send to a single PENDING signer
# ---------------------------------------------------------------------------

def remind_signer(signer, actor_user=None):
    """Re-send the signing request email to a PENDING signer (at most once per REMIND_COOLDOWN)."""
    if signer.status != Signer.Status.PENDING:
        raise ValueError("Reminders can only be sent to pending signers.")
    allowed_at = next_remind_at(signer)
    if allowed_at:
        local = timezone.localtime(allowed_at)
        raise ValueError(
            f"{signer.display_name} was notified less than 6 hours ago. "
            f"You can send the next reminder after {local:%d %b %Y %H:%M}."
        )

    _log(
        signer.document, SignatureEvent.Event.REMINDED,
        actor_user=actor_user,
        detail=f"Reminder sent to {signer.display_name} ({signer.email})",
    )
    _request_signature(signer)


# ---------------------------------------------------------------------------
# auto-remind — every ESIGN_AUTO_REMIND_HOURS to internal users who still have to sign
# ---------------------------------------------------------------------------

def auto_remind_interval():
    from django.conf import settings

    hours = int(getattr(settings, "ESIGN_AUTO_REMIND_HOURS", 24) or 0)
    return timedelta(hours=hours) if hours > 0 else None


def signers_due_for_auto_reminder(now=None):
    """Internal users whose turn it is, on a live document, not asked for ESIGN_AUTO_REMIND_HOURS."""
    interval = auto_remind_interval()
    if interval is None:
        return Signer.objects.none()
    now = now or timezone.now()
    return (
        Signer.objects.filter(
            status=Signer.Status.PENDING,
            user__isnull=False,
            user__is_active=True,
            document__status=SignatureDocument.Status.PENDING,
            document__is_archived=False,
            last_requested_at__isnull=False,
            last_requested_at__lte=now - interval,
        )
        .select_related("user", "document")
        .order_by("last_requested_at")
    )


def auto_remind_signer(signer):
    """Remind one signer by e-mail and Teams (each follows their notification preferences).
    The clock restarts only when at least one channel went out, so a signer in quiet hours
    or with a failed channel is retried on the next hourly run. Returns the channels used."""
    from core.models import UserNotificationPreference

    from .tasks import queue_signature_reminder_teams, send_signature_request_task

    pref = UserNotificationPreference.objects.filter(user=signer.user).first()
    channels = []
    if signer.user.email and (pref is None or pref.email_enabled):
        send_signature_request_task.delay(str(signer.id), reminder=True)
        channels.append("e-mail")
    if queue_signature_reminder_teams(signer, pref):
        channels.append("Teams")
    if not channels:
        return channels
    signer.last_requested_at = timezone.now()
    signer.save(update_fields=["last_requested_at"])
    _log(
        signer.document, SignatureEvent.Event.REMINDED,
        actor_label="System",
        detail=f"Automatic reminder sent to {signer.display_name} via {' and '.join(channels)}",
    )
    return channels


# ---------------------------------------------------------------------------
# reassign — change signer info and resend
# ---------------------------------------------------------------------------

def reassign_signer(
    signer,
    new_name="",
    new_email="",
    new_job_title="",
    new_company="",
    new_user=None,
    actor_user=None,
    ip=None,
):
    if signer.status not in (Signer.Status.WAITING, Signer.Status.PENDING):
        raise ValueError("Only WAITING or PENDING signers can be reassigned.")
    
    old_email = signer.email
    
    if new_user:
        if not new_user.is_active or not new_user.email:
            raise ValueError("The selected system user is not available.")
        if Signer.objects.filter(
            document=signer.document,
            user=new_user,
        ).exclude(pk=signer.pk).exists():
            raise ValueError("The selected system user is already a signer on this document.")
        signer.user = new_user
        signer.external_name = ""
        signer.external_email = ""
        new_name = new_user.get_full_name() or new_user.username
        new_email = new_user.email
    else:
        if not new_name or not new_email:
            raise ValueError("Name and email are required for an external signer.")
        signer.user = None
        signer.external_name = new_name
        signer.external_email = new_email
    signer.job_title = new_job_title
    signer.company = new_company
    
    # Regenerate token so the old link is invalidated
    _assign_token(signer)
    
    signer.save(update_fields=["user", "external_name", "external_email", "job_title", "company", "token", "token_expires_at"])
    
    _log(signer.document, SignatureEvent.Event.REASSIGNED, actor_user=actor_user, ip=ip,
         detail=f"Reassigned signer from {old_email} to {new_email} ({new_name})")
         
    # If the signer was PENDING (it's their turn), notify the new person immediately.
    if signer.status == Signer.Status.PENDING:
        _request_signature(signer)

# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------

def cancel_document(document, actor_user=None, ip=None):
    """Cancel a document that has not yet been completed."""
    if document.status == SignatureDocument.Status.COMPLETED:
        raise ValueError("A completed document cannot be cancelled.")

    document.status = SignatureDocument.Status.CANCELLED
    document.save(update_fields=["status"])

    _log(document, SignatureEvent.Event.CANCELLED, actor_user=actor_user, ip=ip)
