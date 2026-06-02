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
import uuid
from datetime import timedelta

from django.utils import timezone

from .models import Signer, SignatureDocument, SignatureEvent


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _log(document, event, *, actor_user=None, actor_label="", ip=None, detail=""):
    SignatureEvent.objects.create(
        document=document,
        event=event,
        actor_user=actor_user,
        actor_label=actor_label,
        ip=ip,
        detail=detail,
    )


def _token_expiry(days=30):
    return timezone.now() + timedelta(days=days)


def _assign_token(signer):
    signer.token = uuid.uuid4()
    signer.token_expires_at = _token_expiry()
    signer.save(update_fields=["token", "token_expires_at"])


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
    from .tasks import send_signature_request_task
    for signer in pending_signers:
        send_signature_request_task.delay(str(signer.id))


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
    if signer.status != Signer.Status.PENDING:
        raise ValueError("This signer is not waiting for action.")

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


def _advance_or_finalize(document):
    """After a signing action, move the workflow forward or complete it."""
    from .tasks import send_signature_request_task, send_signature_completed_task

    if document.routing_mode == SignatureDocument.RoutingMode.SEQUENTIAL:
        # Find the next WAITING signer with the lowest order
        next_signer = document.signers.filter(
            status=Signer.Status.WAITING
        ).order_by("order").first()

        if next_signer:
            _assign_token(next_signer)
            next_signer.status = Signer.Status.PENDING
            next_signer.save(update_fields=["status"])
            send_signature_request_task.delay(str(next_signer.id))
            return

    # All done (sequential exhausted, or parallel all signed)?
    remaining = document.signers.filter(
        status__in=[Signer.Status.WAITING, Signer.Status.PENDING]
    ).count()

    if remaining == 0:
        _finalize(document)


def _finalize(document):
    """Stamp the PDF, generate the signed file, and notify all parties."""
    from .tasks import send_signature_completed_task
    from .utils_stamp import stamp_document

    pdf_bytes = stamp_document(document)
    if pdf_bytes:
        from django.core.files.base import ContentFile
        safe_title = document.title.replace(" ", "_")[:60]
        document.signed_pdf.save(
            f"{safe_title}_signed.pdf",
            ContentFile(pdf_bytes),
            save=False,
        )

    document.status = SignatureDocument.Status.COMPLETED
    document.save(update_fields=["status", "signed_pdf"])

    _log(document, SignatureEvent.Event.COMPLETED)
    send_signature_completed_task.delay(str(document.id))


# ---------------------------------------------------------------------------
# reject — Signer PENDING → REJECTED
# ---------------------------------------------------------------------------

def reject_signature(signer, notes, ip=None, actor_user=None):
    """
    Record a rejection. Notes (reason) are mandatory.
    Stops the entire document flow and notifies the owner.

    Raises ValueError if notes is empty or the signer is not PENDING.
    """
    if signer.status != Signer.Status.PENDING:
        raise ValueError("This signer is not waiting for action.")
    if not notes or not notes.strip():
        raise ValueError("A rejection reason is required.")

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

    document.status = SignatureDocument.Status.PENDING
    document.save(update_fields=["status"])

    _log(document, SignatureEvent.Event.REOPENED, actor_user=actor_user, ip=ip)

    # Trigger the first/all signers depending on mode
    from .tasks import send_signature_request_task
    if document.routing_mode == SignatureDocument.RoutingMode.SEQUENTIAL:
        first = signers[0] if signers else None
        if first:
            first.status = Signer.Status.PENDING
            first.save(update_fields=["status"])
            send_signature_request_task.delay(str(first.id))
    else:
        for signer in signers:
            signer.status = Signer.Status.PENDING
            signer.save(update_fields=["status"])
            send_signature_request_task.delay(str(signer.id))


# ---------------------------------------------------------------------------
# remind — re-send to a single PENDING signer
# ---------------------------------------------------------------------------

def remind_signer(signer, actor_user=None):
    """Re-send the signing request email to a PENDING signer."""
    if signer.status != Signer.Status.PENDING:
        raise ValueError("Reminders can only be sent to pending signers.")

    _log(
        signer.document, SignatureEvent.Event.REMINDED,
        actor_user=actor_user,
        detail=f"Reminder sent to {signer.display_name} ({signer.email})",
    )

    from .tasks import send_signature_request_task
    send_signature_request_task.delay(str(signer.id))


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
