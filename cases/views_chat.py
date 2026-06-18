"""
Cases App — Chat Portal Views
==============================
Public-facing views for the live-chat widget and the "My Tickets" portal.

Access control:
  Authenticated PortalUser  — matched by email against CaseRecord.requester_email.
  Authenticated staff       — all staff roles can view any chat.
  Anonymous guest           — matched by guest_token stored in a signed cookie.
"""
import datetime
import io
import json
import os
import re
import secrets
import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.conf import settings
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from ipware import get_client_ip as _get_client_ip

from core.models import CompanyUnit, Employee, User
from .models import (
    Attachment, CaseAuditLog, CaseCategory, CaseRecord,
    ChangeRequestApproval, ChangeRequestDocument, Message,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAT_RATE_LIMIT_KEY = "chat_start_{ip}"
CHAT_RATE_LIMIT_MAX = 5
CHAT_RATE_LIMIT_WINDOW = 600  # 10 minutes

REOPEN_WINDOW_DAYS = 7

ALLOWED_MIME_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain", "text/csv",
}
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_FILE_SIZE = 10 * 1024 * 1024   # 10 MB
MAX_FILES_PER_MESSAGE = 3

STAFF_ROLES = {
    User.RoleAccess.SUPERADMIN,
    User.RoleAccess.MANAGER,
    User.RoleAccess.SUPPORTDESK,
    User.RoleAccess.AUDITOR,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_guest_token(request, case_uuid):
    return request.COOKIES.get(f"chat_token_{case_uuid}", "")


def _dispatch_status_notification(case, actor_user, new_status_label):
    """Notify all ticket participants about a status change."""
    from core.notifications import notify_user
    from core.models import User

    context = {
        "ticket_id": str(case.id),
        "ticket_subject": case.subject or "",
        "sender_name": actor_user.username or actor_user.get_full_name() or "System",
        "new_status": new_status_label,
        "ticket_url": f"/portal/chat/{case.id}/",
    }

    participants = []
    if case.requester_email:
        req_user = User.objects.filter(email__iexact=case.requester_email).first()
        if req_user:
            participants.append(req_user)
    for follower in case.followers.all():
        if follower not in participants:
            participants.append(follower)

    for participant in participants:
        if participant.id == actor_user.id:
            continue
        notify_user(participant, "status_change", context)


def _can_access_case(request, case):
    """Return True if the request is allowed to access this case's chat."""
    user = request.user
    if user.is_authenticated:
        if user.role_access in STAFF_ROLES:
            if getattr(case.category, 'is_confidential', False):
                if user.role_access != User.RoleAccess.SUPERADMIN and not getattr(user, 'can_handle_confidential', False):
                    return False
            return True
        if user.role_access == User.RoleAccess.PORTALUSER:
            is_requester = (user.email or "").lower() == (case.requester_email or "").lower()
            is_follower = case.followers.filter(id=user.id).exists()
            return is_requester or is_follower
    token = _get_guest_token(request, str(case.id))
    return bool(token and case.guest_token and token == case.guest_token)


def _set_guest_cookie(response, case_uuid, token):
    response.set_cookie(
        f"chat_token_{case_uuid}",
        token,
        max_age=30 * 24 * 3600,  # 30 days
        httponly=True,
        samesite="Lax",
    )


def _broadcast_message(case_uuid, message, sender_name):
    """Push a new chat message to all WebSocket clients in the room."""
    quoted_body = None
    quoted_sender = None
    if message.quoted_message_id:
        try:
            qm = Message.objects.select_related(
                "sender_staff", "sender_employee"
            ).get(id=message.quoted_message_id)
            quoted_body = qm.body[:120] if not qm.is_deleted else None
            if qm.sender_staff:
                quoted_sender = qm.sender_staff.get_full_name() or str(qm.sender_staff)
            elif qm.sender_employee:
                quoted_sender = qm.sender_employee.full_name or "User"
            else:
                quoted_sender = "System"
        except Exception:
            pass

    sender_email = ""
    if message.sender_staff:
        sender_email = (message.sender_staff.email or "").lower()
    elif message.sender_employee:
        sender_email = (message.sender_employee.email or "").lower()

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"chat_{case_uuid}",
        {
            "type": "chat.message",
            "message_id": str(message.id),
            "body": message.body,
            "direction": message.direction,
            "sender_name": sender_name,
            "sender_email": sender_email,
            "sent_at": message.sent_at.isoformat(),
            "is_system": message.is_system,
            "quoted_message_id": str(message.quoted_message_id) if message.quoted_message_id else None,
            "quoted_body": quoted_body,
            "quoted_sender": quoted_sender,
        },
    )


def _broadcast_delete(case_uuid, message_id):
    """Notify all WebSocket clients that a message was retracted."""
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"chat_{case_uuid}",
        {
            "type": "chat.delete",
            "message_id": str(message_id),
        },
    )


def _broadcast_status(case_uuid, status, status_display):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"chat_{case_uuid}",
        {
            "type": "chat.status_update",
            "status": status,
            "status_display": status_display,
        },
    )


def _leaf_categories():
    parent_ids = CaseCategory.objects.filter(
        parent__isnull=False
    ).values_list("parent_id", flat=True)
    return (
        CaseCategory.objects
        .exclude(slug__in=["whatsapp-general", "email-general"])
        .exclude(id__in=parent_ids)
    )


def _strip_exif(uploaded_file):
    """Return a BytesIO of the image with EXIF stripped. Falls back to original."""
    try:
        from PIL import Image, ImageOps
        img = Image.open(uploaded_file)
        
        # Apply EXIF rotation before stripping so it doesn't display sideways
        img = ImageOps.exif_transpose(img)
        
        buf = io.BytesIO()
        fmt = img.format or "JPEG"
        
        # JPEG doesn't support RGBA or P; convert to RGB to avoid save crashes or black boxes
        if fmt.upper() in ("JPEG", "JPG") and img.mode != "RGB":
            # If it has alpha channel, paste it on a white background
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])
                img = background
            else:
                img = img.convert("RGB")
                
        # Saving without passing exif= parameter automatically drops EXIF
        img.save(buf, format=fmt)
        buf.seek(0)
        return buf
    except Exception:
        uploaded_file.seek(0)
        return uploaded_file


def _rename_upload(uploaded_file, mime_type=None):
    """Return (cleaned_file, safe_filename) with UUID-based name and safe extension."""
    import mimetypes
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    dangerous_exts = {".php", ".php3", ".php4", ".php5", ".phtml", ".html", ".htm", ".exe", ".sh", ".bash", ".pl", ".py"}
    
    if ext in dangerous_exts or not ext:
        if mime_type:
            ext = mimetypes.guess_extension(mime_type) or ".bin"
        else:
            ext = ".bin"
            
    safe_name = f"{uuid.uuid4().hex}{ext}"
    return uploaded_file, safe_name


def _validate_upload(f):
    """Return error string or None."""
    from core.models import SiteConfig
    site_config = SiteConfig.get_solo()
    max_bytes = site_config.max_upload_size_mb * 1024 * 1024

    if f.size > max_bytes:
        return f"File '{f.name}' exceeds the {site_config.max_upload_size_mb} MB limit."
    # Use python-magic for MIME detection
    try:
        import magic as libmagic
        mime = libmagic.from_buffer(f.read(2048), mime=True)
        f.seek(0)
    except Exception:
        return f"File type for '{f.name}' could not be verified securely."
        
    if mime not in ALLOWED_MIME_TYPES:
        return f"File type '{mime}' is not allowed."
    return None


def _save_attachment(message, uploaded_file, original_name):
    """Save attachment with UUID filename and EXIF stripping for images."""
    try:
        import magic as libmagic
        mime = libmagic.from_buffer(uploaded_file.read(2048), mime=True)
        uploaded_file.seek(0)
    except Exception:
        mime = "application/octet-stream"

    _, safe_name = _rename_upload(uploaded_file, mime)

    if mime in IMAGE_MIME_TYPES:
        content = _strip_exif(uploaded_file)
    else:
        content = uploaded_file

    attachment = Attachment(
        message=message,
        original_filename=original_name,
        mime_type=mime,
        file_size=uploaded_file.size,
    )
    attachment.file.save(safe_name, content, save=True)
    return attachment


# ---------------------------------------------------------------------------
# Public API — unit list for widget dropdown
# ---------------------------------------------------------------------------

@require_GET
def chat_units(request):
    """JSON list of all company units for the pre-chat unit picker."""
    units = (
        CompanyUnit.objects
        .select_related("parent")
        .order_by("name")
    )
    data = [
        {
            "id": str(u.id),
            "name": u.name,
            "code": u.code,
            "parent_name": u.parent.name if u.parent else None,
        }
        for u in units
    ]
    return JsonResponse({"units": data})


# ---------------------------------------------------------------------------
# Public API — category list for widget
# ---------------------------------------------------------------------------

@require_GET
def chat_categories(request):
    """JSON list of root categories for the pre-chat category picker."""
    roots = (
        CaseCategory.objects
        .filter(parent__isnull=True)
        .exclude(slug__in=["whatsapp-general", "email-general"])
        .exclude(is_confidential=True)
        .prefetch_related("children")
    )
    data = []
    for cat in roots:
        children = [
            {"id": str(c.id), "name": c.name, "icon": c.icon}
            for c in cat.children.all()
            if not c.is_confidential
        ]
        data.append({
            "id": str(cat.id),
            "name": cat.name,
            "icon": cat.icon or "📝",
            "description": cat.description,
            "has_children": bool(children),
            "children": children,
        })
    return JsonResponse({"categories": data})


# ---------------------------------------------------------------------------
# Chat — start (create ticket from widget)
# ---------------------------------------------------------------------------

@require_POST
def chat_start(request):
    """
    Create a CaseRecord from the chat widget pre-chat form.

    Accepts JSON body:
      category_id  — UUID of the leaf category
      name         — requester full name
      email        — requester email
      unit_id      — CompanyUnit PK (selected from dropdown)
      message      — first message body

    Returns JSON: { case_uuid, case_number, guest_token (if guest) }
    Sets cookie:  chat_token_<case_uuid> for guests.
    """
    import json

    # Rate limit by IP
    client_ip, _ = _get_client_ip(request)
    client_ip = client_ip or "unknown"
    rate_key = CHAT_RATE_LIMIT_KEY.format(ip=client_ip)
    attempts = cache.get(rate_key, 0)
    if attempts >= CHAT_RATE_LIMIT_MAX:
        return JsonResponse(
            {"error": "Too many requests. Please wait 10 minutes."},
            status=429,
        )

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    # Honeypot — bots fill the invisible "_hp" field; real users never see it
    if body.get("_hp"):
        return JsonResponse({"case_uuid": str(uuid.uuid4()), "case_number": "RQ-00000000", "guest_token": ""})

    category_id = (body.get("category_id") or "").strip()
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip()
    unit_id = (body.get("unit_id") or "").strip()
    first_message = (body.get("message") or "").strip()

    if not category_id:
        return JsonResponse({"error": "Category is required."}, status=400)
    if not first_message:
        return JsonResponse({"error": "Please describe your issue."}, status=400)
    if not unit_id:
        return JsonResponse({"error": "Unit is required."}, status=400)

    # Resolve identity: use logged-in user's info if available
    user = request.user
    if user.is_authenticated and user.role_access == User.RoleAccess.PORTALUSER:
        email = user.email or email
        name = name or user.get_full_name() or user.username

    if not email:
        return JsonResponse({"error": "Email is required."}, status=400)
    if not name:
        return JsonResponse({"error": "Name is required."}, status=400)

    category = get_object_or_404(CaseCategory, id=category_id)
    if category.is_confidential:
        return JsonResponse({"error": "Category not available."}, status=403)

    unit = get_object_or_404(CompanyUnit, id=unit_id)
    unit_name = unit.name

    # Auto-link or create Employee; always assign the selected unit
    employee, created = Employee.objects.get_or_create(
        email=email,
        defaults={"full_name": name, "unit": unit},
    )
    if not created:
        update_fields = []
        if name and employee.full_name != name:
            employee.full_name = name
            update_fields.append("full_name")
        if employee.unit_id != unit.pk:
            employee.unit = unit
            update_fields.append("unit")
        if update_fields:
            employee.save(update_fields=update_fields)

    subject = (category.template_subject or f"[Chat] {category.name}").replace("{requester_name}", name)

    # Guest token — only assigned if user is not authenticated
    guest_token = ""
    if not user.is_authenticated:
        guest_token = secrets.token_urlsafe(32)

    case = CaseRecord.objects.create(
        requester=employee,
        requester_email=email,
        requester_name=name,
        requester_unit_name=unit_name,
        category=category,
        subject=subject,
        problem_description=first_message,
        source=CaseRecord.Source.WEBCHAT,
        status=CaseRecord.Status.OPEN,
        has_unread_messages=True,
        guest_token=guest_token,
    )

    # First message (the user's opening text)
    msg = Message.objects.create(
        case=case,
        sender_employee=employee,
        body=first_message,
        direction=Message.Direction.INBOUND,
        channel=Message.Channel.WEB,
        is_system=False,
    )

    # System confirmation message
    system_body = (
        f"Ticket {case.case_number} has been created. "
        f"Our support team will respond shortly."
    )
    Message.objects.create(
        case=case,
        body=system_body,
        direction=Message.Direction.OUTBOUND,
        channel=Message.Channel.WEB,
        is_system=True,
    )

    CaseAuditLog.objects.create(
        case=case,
        action=CaseAuditLog.ActionText.CREATED,
        new_value="Case created via Chat Widget",
    )

    from gateways.tasks import _dispatch_new_ticket_notifs
    _dispatch_new_ticket_notifs(str(case.id))

    cache.set(rate_key, attempts + 1, timeout=CHAT_RATE_LIMIT_WINDOW)

    response = JsonResponse({
        "case_uuid": str(case.id),
        "case_number": case.case_number,
        "guest_token": guest_token,
    })
    if guest_token:
        _set_guest_cookie(response, str(case.id), guest_token)
    return response


# ---------------------------------------------------------------------------
# Chat — room page
# ---------------------------------------------------------------------------

def chat_room(request, case_uuid):
    """
    Full-page chat view for a specific ticket.
    Renders the chat thread and exposes WebSocket URL to the template.
    """
    case = get_object_or_404(CaseRecord, id=case_uuid)
    if not _can_access_case(request, case):
        return HttpResponseForbidden()

    messages_qs = (
        case.messages
        .select_related(
            "sender_staff", "sender_employee",
            "quoted_message__sender_staff", "quoted_message__sender_employee",
        )
        .prefetch_related("attachments")
        .order_by("sent_at")
    )

    can_send = case.status not in (CaseRecord.Status.CLOSED,)
    can_reopen = (
        case.status == CaseRecord.Status.RESOLVED
        and (timezone.now() - case.updated_at).days < REOPEN_WINDOW_DAYS
    )

    user = request.user
    is_staff = user.is_authenticated and user.role_access in STAFF_ROLES
    is_requester = (
        user.is_authenticated
        and (user.email or "").lower() == (case.requester_email or "").lower()
    )
    # Staff who are also the case requester should see the chat from the
    # portal user's perspective (their own messages on the right).
    user_direction = "OUT" if (is_staff and not is_requester) else "IN"

    # WhatsApp-style unread divider: find the first unread staff (OUT) message
    first_unread_id = None
    if not is_staff or is_requester:
        session_key = f"chat_lr_{case_uuid}"
        last_read_str = request.session.get(session_key)
        # Fall back to DB value when session was cleared (e.g. after re-login)
        if not last_read_str and case.client_last_read_at:
            last_read_str = case.client_last_read_at.isoformat()
        if last_read_str:
            try:
                last_read_dt = datetime.datetime.fromisoformat(last_read_str)
                if last_read_dt.tzinfo is None:
                    last_read_dt = last_read_dt.replace(tzinfo=datetime.timezone.utc)
                first_unread = (
                    case.messages
                    .filter(direction="OUT", is_system=False, sent_at__gt=last_read_dt)
                    .order_by("sent_at")
                    .values_list("id", flat=True)
                    .first()
                )
                first_unread_id = str(first_unread) if first_unread else None
            except (ValueError, TypeError):
                pass
        now_iso = timezone.now().isoformat()
        request.session[session_key] = now_iso
        # Persist to DB so the read state survives session expiry / re-login
        case.client_last_read_at = timezone.now()
        case.save(update_fields=["client_last_read_at"])

    followers = list(
        case.followers.values("id", "username", "email")
    )
    followers_json = json.dumps([
        {"id": str(f["id"]), "name": f["username"], "email": f["email"]}
        for f in followers
    ])

    # Build @mention participant list: requester + all followers, excluding current user
    participants = []
    # Add the requester
    if case.requester_email:
        requester_emp = Employee.objects.filter(email__iexact=case.requester_email).first()
        requester_display = (
            requester_emp.full_name if requester_emp
            else case.requester_name or case.requester_email
        )
        participants.append({
            "name": requester_display,
            "email": case.requester_email,
            "role": "requester",
        })
    # Add followers
    for f in followers:
        participants.append({
            "name": f["username"],
            "email": f["email"],
            "role": "follower",
        })
    # Exclude current user from mention list
    current_email = (user.email or "").lower() if user.is_authenticated else ""
    participants = [
        p for p in participants
        if (p["email"] or "").lower() != current_email
    ]
    participants_json = json.dumps(participants)

    is_follower = (
        user.is_authenticated
        and not is_staff
        and not is_requester
        and case.followers.filter(id=user.id).exists()
    )

    # Supporting letter history for the sidebar panel
    cr_docs_qs = (
        case.change_requests
        .select_related("document_template")
        .prefetch_related("approvals__approver")
        .order_by("-revision_number")
    )
    cr_panel = []
    for cr_doc in cr_docs_qs:
        cr_panel.append({
            "doc": cr_doc,
            "approvals": sorted(cr_doc.approvals.all(), key=lambda a: a.order),
            "has_pdf": bool(cr_doc.generated_pdf),
            "pdf_url": cr_doc.generated_pdf.url if cr_doc.generated_pdf else None,
        })

    return render(request, "client/chat_room.html", {
        "case": case,
        "chat_messages": messages_qs,
        "can_send": can_send,
        "can_reopen": can_reopen,
        "guest_token": _get_guest_token(request, case_uuid),
        "user_direction": user_direction,
        "is_staff": is_staff and not is_requester,
        "is_follower": is_follower,
        "first_unread_id": first_unread_id,
        "followers_json": followers_json,
        "participants_json": participants_json,
        "current_user_email": (user.email or "").lower() if user.is_authenticated else "",
        "cr_panel": cr_panel,
    })


# ---------------------------------------------------------------------------
# Chat — send text message
# ---------------------------------------------------------------------------

@require_POST
def chat_send(request, case_uuid):
    """Send a plain-text message. Used as fallback when WebSocket is unavailable."""
    import json

    case = get_object_or_404(CaseRecord, id=case_uuid)
    if not _can_access_case(request, case):
        return HttpResponseForbidden()

    if case.status == CaseRecord.Status.CLOSED:
        return JsonResponse({"error": "This ticket is closed."}, status=403)

    try:
        body_data = json.loads(request.body)
        text = (body_data.get("body") or "").strip()
        quoted_message_id = (body_data.get("quoted_message_id") or "").strip() or None
    except (json.JSONDecodeError, ValueError):
        text = (request.POST.get("body") or "").strip()
        quoted_message_id = None

    user = request.user
    is_staff = user.is_authenticated and user.role_access in STAFF_ROLES

    # Check if outbound WA is enabled for staff replies
    if is_staff and case.source == CaseRecord.Source.EVOLUTION_WA:
        from core.models import SiteConfig
        if not SiteConfig.get_solo().wa_outbound_enabled:
            return JsonResponse({"error": "Outbound WhatsApp replies are currently disabled."}, status=403)

    if not text:
        return JsonResponse({"error": "Message cannot be empty."}, status=400)

    sender_employee = None
    sender_name = "User"
    if not is_staff:
        # Use the actual user's email, not always the requester's
        sender_email = (user.email or "") if user.is_authenticated else case.requester_email
        sender_employee = Employee.objects.filter(
            email__iexact=sender_email
        ).first()
        if sender_employee:
            sender_name = sender_employee.full_name or "User"
        elif user.is_authenticated:
            sender_name = user.get_full_name() or user.username or "User"
        else:
            sender_name = case.requester_name or "User"

    msg = Message.objects.create(
        case=case,
        body=text,
        direction=Message.Direction.OUTBOUND if is_staff else Message.Direction.INBOUND,
        channel=Message.Channel.WEB,
        sender_staff=user if is_staff else None,
        sender_employee=sender_employee,
        is_system=False,
        quoted_message_id=quoted_message_id,
    )

    if not is_staff:
        CaseRecord.objects.filter(id=case_uuid).update(has_unread_messages=True)
        # Notify staff of new chat reply (fires async, skipped if staff is active)
        try:
            from gateways.tasks import notify_staff_chat_reply_task
            notify_staff_chat_reply_task.delay(str(msg.id))
        except Exception:
            pass
    if is_staff:
        sender_name = user.get_full_name() or str(user)

    _broadcast_message(case_uuid, msg, sender_name)

    return JsonResponse({
        "message_id": str(msg.id),
        "body": msg.body,
        "direction": msg.direction,
        "sent_at": msg.sent_at.isoformat(),
        "sender_name": sender_name,
    })


# ---------------------------------------------------------------------------
# Chat — delete (retract) a message
# ---------------------------------------------------------------------------

@require_POST
def chat_delete_message(request, case_uuid):
    """
    Soft-delete a message. Only the original sender can retract, within 1 hour.
    """
    import json as _json
    from django.utils import timezone as _tz

    case = get_object_or_404(CaseRecord, id=case_uuid)
    if not _can_access_case(request, case):
        return HttpResponseForbidden()

    try:
        body_data = _json.loads(request.body)
        message_id = (body_data.get("message_id") or "").strip()
    except Exception:
        return JsonResponse({"error": "Invalid request."}, status=400)

    if not message_id:
        return JsonResponse({"error": "message_id required."}, status=400)

    try:
        msg = Message.objects.select_related(
            "sender_staff", "sender_employee"
        ).get(id=message_id, case=case)
    except Message.DoesNotExist:
        return JsonResponse({"error": "Message not found."}, status=404)

    if msg.is_deleted:
        return JsonResponse({"ok": True})

    user = request.user
    is_staff = user.is_authenticated and user.role_access in STAFF_ROLES

    # Ownership check
    if is_staff:
        if msg.direction != Message.Direction.OUTBOUND or msg.sender_staff_id != user.id:
            return JsonResponse({"error": "Cannot retract this message."}, status=403)
    else:
        if msg.direction != Message.Direction.INBOUND:
            return JsonResponse({"error": "Cannot retract this message."}, status=403)
        # Verify the actual sender matches the current user
        sender_email = (msg.sender_employee.email if msg.sender_employee else case.requester_email) or ""
        current_email = (user.email or "") if user.is_authenticated else ""
        if sender_email.lower() != current_email.lower():
            return JsonResponse({"error": "Cannot retract this message."}, status=403)

    # Time window: 1 hour
    if (_tz.now() - msg.sent_at).total_seconds() > 3600:
        return JsonResponse({"error": "Retract window expired (1 hour)."}, status=403)

    msg.is_deleted = True
    msg.save(update_fields=["is_deleted"])

    _broadcast_delete(str(case.id), message_id)

    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# Chat — upload attachment
# ---------------------------------------------------------------------------

@require_POST
def chat_upload(request, case_uuid):
    """
    Upload one or more files attached to a new message.
    Validates MIME type allowlist, 10 MB limit, max 3 files.
    Strips EXIF from images.
    Broadcasts via WebSocket after saving.
    """
    case = get_object_or_404(CaseRecord, id=case_uuid)
    if not _can_access_case(request, case):
        return HttpResponseForbidden()
    if case.status == CaseRecord.Status.CLOSED:
        return JsonResponse({"error": "This ticket is closed."}, status=403)

    uploaded_files = request.FILES.getlist("files")
    if not uploaded_files:
        return JsonResponse({"error": "No files received."}, status=400)
    if len(uploaded_files) > MAX_FILES_PER_MESSAGE:
        return JsonResponse(
            {"error": f"Maximum {MAX_FILES_PER_MESSAGE} files per message."},
            status=400,
        )

    errors = []
    for f in uploaded_files:
        err = _validate_upload(f)
        if err:
            errors.append(err)
    if errors:
        return JsonResponse({"error": " ".join(errors)}, status=400)

    user = request.user
    is_staff = user.is_authenticated and user.role_access in STAFF_ROLES

    sender_employee = None
    sender_name = "User"
    if not is_staff:
        # Use the actual user's email, not always the requester's
        sender_email = (user.email or "") if user.is_authenticated else case.requester_email
        sender_employee = Employee.objects.filter(
            email__iexact=sender_email
        ).first()
        if sender_employee:
            sender_name = sender_employee.full_name or "User"
        elif user.is_authenticated:
            sender_name = user.get_full_name() or user.username or "User"
        else:
            sender_name = case.requester_name or "User"
    else:
        sender_name = user.get_full_name() or str(user)

    caption = (request.POST.get("caption") or "").strip()
    msg = Message.objects.create(
        case=case,
        body=caption,
        direction=Message.Direction.OUTBOUND if is_staff else Message.Direction.INBOUND,
        channel=Message.Channel.WEB,
        sender_staff=user if is_staff else None,
        sender_employee=sender_employee,
        is_system=False,
    )

    if not is_staff:
        CaseRecord.objects.filter(id=case_uuid).update(has_unread_messages=True)
        try:
            from gateways.tasks import notify_staff_chat_reply_task
            notify_staff_chat_reply_task.delay(str(msg.id))
        except Exception:
            pass

    saved = []
    channel_layer = get_channel_layer()
    for f in uploaded_files:
        original_name = f.name
        attachment = _save_attachment(msg, f, original_name)
        is_image = attachment.mime_type in IMAGE_MIME_TYPES
        file_url = request.build_absolute_uri(attachment.file.url)

        saved.append({
            "file_url": file_url,
            "filename": original_name,
            "mime_type": attachment.mime_type,
            "is_image": is_image,
        })

        att_sender_email = ""
        if msg.sender_staff:
            att_sender_email = (msg.sender_staff.email or "").lower()
        elif msg.sender_employee:
            att_sender_email = (msg.sender_employee.email or "").lower()

        async_to_sync(channel_layer.group_send)(
            f"chat_{case_uuid}",
            {
                "type": "chat.attachment",
                "message_id": str(msg.id),
                "file_url": file_url,
                "filename": original_name,
                "mime_type": attachment.mime_type,
                "is_image": is_image,
                "sent_at": msg.sent_at.isoformat(),
                "direction": msg.direction,
                "sender_name": sender_name,
                "sender_email": att_sender_email,
            },
        )

    return JsonResponse({"message_id": str(msg.id), "attachments": saved})


# ---------------------------------------------------------------------------
# Chat — poll (HTMX fallback)
# ---------------------------------------------------------------------------

@require_GET
def chat_poll(request, case_uuid):
    """
    HTMX partial: return the full message thread HTML fragment.
    Used when WebSocket connection is unavailable.
    """
    case = get_object_or_404(CaseRecord, id=case_uuid)
    if not _can_access_case(request, case):
        return HttpResponseForbidden()

    user = request.user
    is_staff = user.is_authenticated and user.role_access in STAFF_ROLES
    is_requester = (
        user.is_authenticated
        and (user.email or "").lower() == (case.requester_email or "").lower()
    )
    user_direction = "OUT" if (is_staff and not is_requester) else "IN"

    messages_qs = (
        case.messages
        .select_related(
            "sender_staff", "sender_employee",
            "quoted_message__sender_staff", "quoted_message__sender_employee",
        )
        .prefetch_related("attachments")
        .order_by("sent_at")
    )
    return render(request, "client/partials/chat_messages.html", {
        "case": case,
        "messages": messages_qs,
        "user_direction": user_direction,
        "first_unread_id": None,
    })


# ---------------------------------------------------------------------------
# Chat — resolve (user closes own ticket)
# ---------------------------------------------------------------------------

@require_POST
def chat_resolve(request, case_uuid):
    """User marks their own ticket as Resolved."""
    import json as _json

    case = get_object_or_404(CaseRecord, id=case_uuid)
    if not _can_access_case(request, case):
        return HttpResponseForbidden()
    # Only the requester or staff can resolve — not followers
    user = request.user
    is_staff = user.is_authenticated and user.role_access in STAFF_ROLES
    is_requester = user.is_authenticated and (user.email or "").lower() == (case.requester_email or "").lower()
    if not is_staff and not is_requester:
        return JsonResponse({"error": "Only the ticket owner can resolve this ticket."}, status=403)
    if case.status in (CaseRecord.Status.RESOLVED, CaseRecord.Status.CLOSED):
        return JsonResponse({"error": "Ticket is already resolved or closed."}, status=400)

    try:
        comment = (_json.loads(request.body).get("comment") or "").strip()
    except (ValueError, AttributeError):
        comment = ""

    if not comment:
        comment = f"No comment from {case.requester_name or 'User'}"

    case.status = CaseRecord.Status.RESOLVED
    case.save(update_fields=["status", "updated_at"])

    # System announcement
    Message.objects.create(
        case=case,
        body="The requester has marked this ticket as resolved.",
        direction=Message.Direction.OUTBOUND,
        channel=Message.Channel.WEB,
        is_system=True,
    )

    # Closing note from requester — visible to staff as resolution context
    sender_employee = Employee.objects.filter(
        email__iexact=case.requester_email
    ).first()
    closing_msg = Message.objects.create(
        case=case,
        body=comment,
        direction=Message.Direction.INBOUND,
        channel=Message.Channel.WEB,
        sender_employee=sender_employee,
        is_system=False,
    )

    # Store as root cause analysis if not already filled
    if not case.root_cause_analysis:
        CaseRecord.objects.filter(id=case_uuid).update(root_cause_analysis=comment)

    CaseAuditLog.objects.create(
        case=case,
        action=CaseAuditLog.ActionText.STATUS_CHANGE,
        new_value=CaseRecord.Status.RESOLVED,
    )

    _broadcast_status(case_uuid, CaseRecord.Status.RESOLVED, "Resolved")
    _broadcast_message(case_uuid, closing_msg, case.requester_name or "User")

    # Notify participants about status change
    try:
        _dispatch_status_notification(case, user, "Resolved")
    except Exception:
        pass

    return JsonResponse({"status": CaseRecord.Status.RESOLVED})


# ---------------------------------------------------------------------------
# Chat — reopen
# ---------------------------------------------------------------------------

@require_POST
def chat_reopen(request, case_uuid):
    """Reopen a resolved ticket within the reopen window."""
    case = get_object_or_404(CaseRecord, id=case_uuid)
    if not _can_access_case(request, case):
        return HttpResponseForbidden()
    # Only the requester or staff can reopen — not followers
    user = request.user
    is_staff = user.is_authenticated and user.role_access in STAFF_ROLES
    is_requester = user.is_authenticated and (user.email or "").lower() == (case.requester_email or "").lower()
    if not is_staff and not is_requester:
        return JsonResponse({"error": "Only the ticket owner can reopen this ticket."}, status=403)
    if case.status != CaseRecord.Status.RESOLVED:
        return JsonResponse({"error": "Only resolved tickets can be reopened."}, status=400)

    days_since = (timezone.now() - case.updated_at).days
    if days_since >= REOPEN_WINDOW_DAYS:
        return JsonResponse(
            {"error": f"Tickets can only be reopened within {REOPEN_WINDOW_DAYS} days of resolution."},
            status=403,
        )

    case.status = CaseRecord.Status.OPEN
    case.save(update_fields=["status", "updated_at"])

    Message.objects.create(
        case=case,
        body="The requester has reopened this ticket.",
        direction=Message.Direction.OUTBOUND,
        channel=Message.Channel.WEB,
        is_system=True,
    )

    CaseAuditLog.objects.create(
        case=case,
        action=CaseAuditLog.ActionText.STATUS_CHANGE,
        new_value=CaseRecord.Status.OPEN,
    )

    _broadcast_status(case_uuid, CaseRecord.Status.OPEN, "Open")

    # Notify participants about status change
    try:
        _dispatch_status_notification(case, user, "Reopened")
    except Exception:
        pass

    return JsonResponse({"status": CaseRecord.Status.OPEN})


# ---------------------------------------------------------------------------
# My Tickets — login required
# ---------------------------------------------------------------------------

@login_required
def my_tickets(request):
    """
    Portal history page for authenticated users.
    Shows all CaseRecords where requester_email matches the logged-in user's email.
    Staff roles are redirected to the main desk dashboard.
    Supports filtering by status/keyword and pagination (20 per page).
    """
    from django.core.paginator import Paginator
    user = request.user

    if user.role_access in STAFF_ROLES:
        from django.shortcuts import redirect
        return redirect("desk:case_list")

    from django.db.models import Q, Count
    qs = (
        CaseRecord.objects
        .filter(Q(requester_email__iexact=user.email) | Q(followers=user))
        .select_related("category")
        .prefetch_related(
            "messages", "followers",
            "change_requests",
            "change_requests__approvals__approver",
        )
        .distinct()
    )

    # Filters
    status_filter = request.GET.get("status", "").strip()
    search_query = request.GET.get("q", "").strip()
    role_filter = request.GET.get("role", "").strip()  # "mine" | "following" | ""
    sort_by = request.GET.get("sort", "newest").strip()
    show_closed = request.GET.get("closed", "").strip()

    # Default: hide Closed tickets unless user explicitly asks
    if status_filter != CaseRecord.Status.CLOSED and not show_closed:
        qs = qs.exclude(status=CaseRecord.Status.CLOSED)

    if role_filter == "mine":
        qs = qs.filter(requester_email__iexact=user.email)
    elif role_filter == "following":
        qs = qs.filter(followers=user).exclude(requester_email__iexact=user.email)

    if status_filter:
        qs = qs.filter(status=status_filter)

    # Search: subject, ticket number, AND chat message body
    message_hits = {}  # case_id -> list of matched message snippets
    if search_query:
        dash_idx = search_query.find('-')
        uuid_part = search_query[dash_idx + 1:].lower() if dash_idx != -1 else None
        q = Q(subject__icontains=search_query) | Q(id__icontains=search_query)
        if uuid_part:
            q |= Q(id__istartswith=uuid_part)
        # Also match messages body
        q |= Q(messages__body__icontains=search_query)
        qs = qs.filter(q).distinct()

        # Collect matched message snippets (up to 3 per ticket)
        from cases.models import Message as Msg
        all_case_ids = list(qs.values_list("id", flat=True))
        if all_case_ids:
            matched = (
                Msg.objects
                .filter(case_id__in=all_case_ids, body__icontains=search_query, is_deleted=False)
                .select_related("sender_staff", "sender_employee")
                .order_by("-sent_at")[:60]  # cap for performance
            )
            for m in matched:
                cid = str(m.case_id)
                if cid not in message_hits:
                    message_hits[cid] = []
                if len(message_hits[cid]) < 3:
                    sender = ""
                    if m.sender_staff:
                        sender = m.sender_staff.get_full_name() or str(m.sender_staff)
                    elif m.sender_employee:
                        sender = m.sender_employee.full_name or "User"
                    import re
                    plain = re.sub(r'<[^>]+>', '', m.body or '')
                    message_hits[cid].append({
                        "sender": sender,
                        "body": plain[:160],
                        "sent_at": m.sent_at,
                    })

    STATUS_LABEL = {
        CaseRecord.Status.PENDING_APPROVAL: ("On Approval", "amber"),
        CaseRecord.Status.REVISION_REQUIRED: ("Revision Required", "red"),
        CaseRecord.Status.OPEN: ("Waiting", "yellow"),
        CaseRecord.Status.INVESTIGATING: ("In Progress", "blue"),
        CaseRecord.Status.PENDING_INFO: ("Need Info", "orange"),
        CaseRecord.Status.RESOLVED: ("Resolved", "green"),
        CaseRecord.Status.CLOSED: ("Closed", "slate"),
    }
    # Sorting
    SORT_MAP = {
        "newest": "-created_at",
        "oldest": "created_at",
        "updated": "-updated_at",
    }
    qs = qs.order_by(SORT_MAP.get(sort_by, "-created_at"))

    paginator = Paginator(qs, 20)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    now = timezone.now()
    enriched = []
    for c in page_obj:
        label, color = STATUS_LABEL.get(c.status, (c.status, "slate"))
        can_chat = c.status not in (CaseRecord.Status.CLOSED,)
        can_reopen = (
            c.status == CaseRecord.Status.RESOLVED
            and (now - c.updated_at).days < REOPEN_WINDOW_DAYS
        )
        # Unread staff replies
        session_key = f"chat_lr_{c.id}"
        last_read_str = request.session.get(session_key)
        if not last_read_str and c.client_last_read_at:
            last_read_str = c.client_last_read_at.isoformat()
        unread_count = 0
        if last_read_str:
            try:
                last_read_dt = datetime.datetime.fromisoformat(last_read_str)
                if last_read_dt.tzinfo is None:
                    last_read_dt = last_read_dt.replace(tzinfo=datetime.timezone.utc)
                unread_count = c.messages.filter(
                    direction="OUT", is_system=False, sent_at__gt=last_read_dt
                ).count()
            except (ValueError, TypeError):
                pass
        else:
            unread_count = c.messages.filter(direction="OUT", is_system=False).count()

        # Is follower?
        is_follower = (user.email or "").lower() != (c.requester_email or "").lower()

        # Last message preview
        last_msg = c.messages.filter(is_deleted=False, is_system=False).order_by("-sent_at").first()
        import re
        last_msg_preview = ""
        last_msg_sender = ""
        last_msg_time = None
        if last_msg:
            last_msg_preview = re.sub(r'<[^>]+>', '', last_msg.body or '')[:100]
            if last_msg.sender_staff:
                last_msg_sender = last_msg.sender_staff.get_full_name() or str(last_msg.sender_staff)
            elif last_msg.sender_employee:
                last_msg_sender = last_msg.sender_employee.full_name or "User"
            last_msg_time = last_msg.sent_at

        total_messages = c.messages.filter(is_deleted=False).count()

        # CR history — all CR docs sorted newest first, with their approvals
        all_cr_docs = sorted(c.change_requests.all(), key=lambda d: d.revision_number, reverse=True)
        cr_history = []
        for cr_doc in all_cr_docs:
            cr_approvals = sorted(cr_doc.approvals.all(), key=lambda a: a.order)
            cr_history.append({
                "doc": cr_doc,
                "approvals": cr_approvals,
                "has_pdf": bool(cr_doc.generated_pdf),
                "pdf_url": cr_doc.generated_pdf.url if cr_doc.generated_pdf else None,
            })

        # Active CR doc for the action button
        active_cr_doc = None
        if c.status in (CaseRecord.Status.PENDING_APPROVAL, CaseRecord.Status.REVISION_REQUIRED):
            active_cr_doc = next(
                (cr for cr in c.change_requests.all()
                 if cr.status in ("pending_approval", "rejected")),
                None,
            )

        enriched.append({
            "case": c,
            "status_label": label,
            "status_color": color,
            "can_chat": can_chat,
            "can_reopen": can_reopen,
            "active_cr_doc": active_cr_doc,
            "cr_history": cr_history,
            "unread_count": unread_count,
            "is_follower": is_follower,
            "total_messages": total_messages,
            "last_msg_preview": last_msg_preview,
            "last_msg_sender": last_msg_sender,
            "last_msg_time": last_msg_time,
            "matched_messages": message_hits.get(str(c.id), []),
        })

    # Counts for tab badges
    all_base = CaseRecord.objects.filter(
        Q(requester_email__iexact=user.email) | Q(followers=user)
    ).distinct()
    count_all = all_base.count()
    count_mine = all_base.filter(requester_email__iexact=user.email).count()
    count_following = count_all - count_mine

    return render(request, "client/my_tickets.html", {
        "tickets": enriched,
        "page_obj": page_obj,
        "status_filter": status_filter,
        "search_query": search_query,
        "role_filter": role_filter,
        "count_all": count_all,
        "count_mine": count_mine,
        "count_following": count_following,
        "sort_by": sort_by,
        "show_closed": show_closed,
        "status_choices": [
            ("", "All statuses"),
            (CaseRecord.Status.PENDING_APPROVAL, "On Approval"),
            (CaseRecord.Status.REVISION_REQUIRED, "Revision Required"),
            (CaseRecord.Status.OPEN, "Waiting"),
            (CaseRecord.Status.INVESTIGATING, "In Progress"),
            (CaseRecord.Status.PENDING_INFO, "Need Info"),
            (CaseRecord.Status.RESOLVED, "Resolved"),
            (CaseRecord.Status.CLOSED, "Closed"),
        ],
    })


@login_required
def my_approvals(request):
    """
    Shows all ChangeRequestApprovals assigned to the logged-in user.
    Pending approvals are listed first; completed ones follow.
    """
    pending = (
        ChangeRequestApproval.objects
        .filter(approver=request.user, status=ChangeRequestApproval.Status.PENDING)
        .select_related("document__case__category", "document__document_template", "document__case")
        .order_by("created_at")
    )
    completed = (
        ChangeRequestApproval.objects
        .filter(approver=request.user, status__in=[
            ChangeRequestApproval.Status.APPROVED,
            ChangeRequestApproval.Status.REJECTED,
        ])
        .select_related("document__case__category", "document__document_template", "document__case")
        .order_by("-acted_at")[:30]
    )
    return render(request, "client/my_approvals.html", {
        "pending_approvals": pending,
        "completed_approvals": completed,
        "pending_count": pending.count(),
    })


@login_required
@require_GET
def my_tickets_status(request):
    """JSON endpoint: returns unread counts per open ticket for the portal user. Used for polling."""
    user = request.user
    if user.role_access in STAFF_ROLES:
        return JsonResponse({"tickets": []})

    from django.db.models import Q
    cases = (
        CaseRecord.objects
        .filter(Q(requester_email__iexact=user.email) | Q(followers=user))
        .exclude(status=CaseRecord.Status.CLOSED)
        .prefetch_related("messages")
        .distinct()
        .only("id", "subject", "status")
    )

    result = []
    for c in cases:
        session_key = f"chat_lr_{c.id}"
        last_read_str = request.session.get(session_key)
        if not last_read_str and c.client_last_read_at:
            last_read_str = c.client_last_read_at.isoformat()
        unread_count = 0
        if last_read_str:
            try:
                last_read_dt = datetime.datetime.fromisoformat(last_read_str)
                if last_read_dt.tzinfo is None:
                    last_read_dt = last_read_dt.replace(tzinfo=datetime.timezone.utc)
                unread_count = c.messages.filter(
                    direction="OUT", is_system=False, sent_at__gt=last_read_dt
                ).count()
            except (ValueError, TypeError):
                pass
        else:
            unread_count = c.messages.filter(direction="OUT", is_system=False).count()

        result.append({"id": str(c.id), "subject": c.subject, "unread": unread_count})

    return JsonResponse({"tickets": result})


# ---------------------------------------------------------------------------
# OG link preview
# ---------------------------------------------------------------------------

# Regex for private / reserved IP ranges — blocks SSRF
_PRIVATE_IP_RE = __import__("re").compile(
    r"^("
    r"127\.|"
    r"10\.|"
    r"172\.(1[6-9]|2\d|3[01])\.|"
    r"192\.168\.|"
    r"169\.254\.|"
    r"::1$|"
    r"fd[0-9a-f]{2}:|"
    r"fc[0-9a-f]{2}:"
    r")",
    __import__("re").IGNORECASE,
)

_OG_CACHE_PREFIX = "og_preview_"
_OG_CACHE_TTL = 3600  # 1 hour


@require_GET
def link_preview(request):
    """
    Fetch Open Graph metadata for a URL and return JSON.

    Security:
    - Only HTTP/HTTPS allowed.
    - Private / loopback IPs are blocked (SSRF protection).
    - 3-second connect + read timeout.
    - Response capped at 100 KB to prevent memory abuse.
    - Results cached 1 hour.
    """
    import re
    import socket
    from urllib.parse import urlparse

    url = (request.GET.get("url") or "").strip()
    if not url:
        return JsonResponse({"error": "url required"}, status=400)

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return JsonResponse({"error": "Only http/https links are supported."}, status=400)

    hostname = parsed.hostname or ""
    if not hostname:
        return JsonResponse({"error": "Invalid URL."}, status=400)

    # Reject if hostname is directly a private IP
    if _PRIVATE_IP_RE.match(hostname):
        return JsonResponse({"error": "URL not allowed."}, status=403)

    # DNS resolve and block private IPs reached via hostname
    try:
        addr = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)[0][4][0]
        if _PRIVATE_IP_RE.match(addr):
            return JsonResponse({"error": "URL not allowed."}, status=403)
    except Exception:
        return JsonResponse({"error": "Could not resolve URL."}, status=400)

    cache_key = _OG_CACHE_PREFIX + str(hash(url))
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse(cached)

    try:
        import requests as http_requests

        resp = http_requests.get(
            url,
            timeout=(3, 3),
            stream=True,
            headers={"User-Agent": "RoCDesk-LinkPreview/1.0"},
            allow_redirects=True,
        )
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type:
            return JsonResponse({"error": "Not an HTML page."}, status=400)

        # Cap at 100 KB to avoid memory abuse
        raw = b""
        for chunk in resp.iter_content(chunk_size=8192):
            raw += chunk
            if len(raw) > 100 * 1024:
                break

        html_text = raw.decode("utf-8", errors="replace")
    except Exception:
        return JsonResponse({"error": "Could not fetch URL."}, status=400)

    def _og(prop):
        m = re.search(
            rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=["\'](.*?)["\']',
            html_text, re.IGNORECASE | re.DOTALL,
        )
        if m:
            return m.group(1).strip()
        m = re.search(
            rf'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:{prop}["\']',
            html_text, re.IGNORECASE | re.DOTALL,
        )
        return m.group(1).strip() if m else ""

    def _title():
        m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else ""

    import html as html_mod
    result = {
        "url": url,
        "title": html_mod.unescape(_og("title") or _title())[:120],
        "description": html_mod.unescape(_og("description"))[:200],
        "image": _og("image"),
        "site_name": html_mod.unescape(_og("site_name"))[:60],
    }

    cache.set(cache_key, result, _OG_CACHE_TTL)
    return JsonResponse(result)


# ---------------------------------------------------------------------------
# Followers
# ---------------------------------------------------------------------------

@login_required
@require_GET
def chat_search_users(request, case_uuid):
    """Return users matching a query, excluding current followers."""
    case = get_object_or_404(CaseRecord, id=case_uuid)
    if not _can_access_case(request, case):
        return HttpResponseForbidden()

    q = request.GET.get("q", "").strip()
    existing_ids = list(case.followers.values_list("id", flat=True))
    qs = User.objects.filter(is_active=True).exclude(id__in=existing_ids)
    if q:
        from django.db.models import Q as _Q
        qs = qs.filter(_Q(username__icontains=q) | _Q(email__icontains=q))
    qs = qs[:10]
    return JsonResponse({
        "users": [{"id": str(u.id), "name": u.username, "email": u.email} for u in qs]
    })


@login_required
@require_POST
def chat_add_follower(request, case_uuid):
    """Add a user as a follower and notify them by email."""
    case = get_object_or_404(CaseRecord, id=case_uuid)
    if not _can_access_case(request, case):
        return HttpResponseForbidden()

    data = json.loads(request.body)
    user = get_object_or_404(User, id=data.get("user_id"))
    case.followers.add(user)

    if user.email:
        chat_url = request.build_absolute_uri(
            reverse("chat:room", kwargs={"case_uuid": case.id})
        )
        try:
            send_mail(
                subject=f"[{case.case_number}] You were added as a follower",
                message=(
                    f"Hi {user.username},\n\n"
                    f"You have been added as a follower on ticket:\n"
                    f"#{case.case_number} — {case.subject}\n\n"
                    f"Click the link below to join the discussion:\n{chat_url}\n\n"
                    f"Regards,\nSupport Team"
                ),
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@example.com"),
                recipient_list=[user.email],
                fail_silently=True,
            )
        except Exception:
            pass

    return JsonResponse({"ok": True, "id": str(user.id), "name": user.username, "email": user.email})


@login_required
@require_POST
def chat_remove_follower(request, case_uuid):
    """Remove a follower from the case."""
    case = get_object_or_404(CaseRecord, id=case_uuid)
    if not _can_access_case(request, case):
        return HttpResponseForbidden()

    data = json.loads(request.body)
    user = get_object_or_404(User, id=data.get("user_id"))
    case.followers.remove(user)
    return JsonResponse({"ok": True})
