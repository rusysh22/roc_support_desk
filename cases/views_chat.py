"""
Cases App — Chat Portal Views
==============================
Public-facing views for the live-chat widget and the "My Tickets" portal.

Access control:
  Authenticated PortalUser  — matched by email against CaseRecord.requester_email.
  Authenticated staff       — all staff roles can view any chat.
  Anonymous guest           — matched by guest_token stored in a signed cookie.
"""
import io
import os
import secrets
import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from ipware import get_client_ip as _get_client_ip

from core.models import CompanyUnit, Employee, User
from .models import Attachment, CaseAuditLog, CaseCategory, CaseRecord, Message


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


def _can_access_case(request, case):
    """Return True if the request is allowed to access this case's chat."""
    user = request.user
    if user.is_authenticated:
        if user.role_access in STAFF_ROLES:
            return True
        if user.role_access == User.RoleAccess.PORTALUSER:
            return (user.email or "").lower() == (case.requester_email or "").lower()
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

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"chat_{case_uuid}",
        {
            "type": "chat.message",
            "message_id": str(message.id),
            "body": message.body,
            "direction": message.direction,
            "sender_name": sender_name,
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
        from PIL import Image
        img = Image.open(uploaded_file)
        buf = io.BytesIO()
        fmt = img.format or "JPEG"
        # Re-save without metadata
        clean = Image.new(img.mode, img.size)
        clean.putdata(list(img.getdata()))
        clean.save(buf, format=fmt)
        buf.seek(0)
        return buf
    except Exception:
        uploaded_file.seek(0)
        return uploaded_file


def _rename_upload(uploaded_file):
    """Return (cleaned_file, safe_filename) with UUID-based name."""
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    safe_name = f"{uuid.uuid4().hex}{ext}"
    return uploaded_file, safe_name


def _validate_upload(f):
    """Return error string or None."""
    if f.size > MAX_FILE_SIZE:
        return f"File '{f.name}' exceeds the 10 MB limit."
    # Use python-magic for MIME detection (already in requirements)
    try:
        import magic as libmagic
        mime = libmagic.from_buffer(f.read(2048), mime=True)
        f.seek(0)
    except Exception:
        mime = f.content_type or ""
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
        mime = getattr(uploaded_file, "content_type", "application/octet-stream")

    _, safe_name = _rename_upload(uploaded_file)

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

    subject = category.template_subject or f"[Chat] {category.name}"

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
        source=CaseRecord.Source.WEBFORM,
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
    user_direction = "OUT" if is_staff else "IN"

    return render(request, "client/chat_room.html", {
        "case": case,
        "messages": messages_qs,
        "can_send": can_send,
        "can_reopen": can_reopen,
        "guest_token": _get_guest_token(request, case_uuid),
        "user_direction": user_direction,
        "is_staff": is_staff,
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

    if not text:
        return JsonResponse({"error": "Message cannot be empty."}, status=400)

    user = request.user
    is_staff = user.is_authenticated and user.role_access in STAFF_ROLES

    sender_employee = None
    sender_name = "User"
    if not is_staff:
        sender_employee = Employee.objects.filter(
            email__iexact=case.requester_email
        ).first()
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
    sender_name = case.requester_name or "User"
    if not is_staff:
        sender_employee = Employee.objects.filter(
            email__iexact=case.requester_email
        ).first()
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

    messages_qs = (
        case.messages
        .prefetch_related("attachments")
        .order_by("sent_at")
    )
    return render(request, "client/partials/chat_messages.html", {
        "case": case,
        "messages": messages_qs,
    })


# ---------------------------------------------------------------------------
# Chat — resolve (user closes own ticket)
# ---------------------------------------------------------------------------

@require_POST
def chat_resolve(request, case_uuid):
    """User marks their own ticket as Resolved."""
    case = get_object_or_404(CaseRecord, id=case_uuid)
    if not _can_access_case(request, case):
        return HttpResponseForbidden()
    if case.status in (CaseRecord.Status.RESOLVED, CaseRecord.Status.CLOSED):
        return JsonResponse({"error": "Ticket is already resolved or closed."}, status=400)

    case.status = CaseRecord.Status.RESOLVED
    case.save(update_fields=["status", "updated_at"])

    Message.objects.create(
        case=case,
        body="The requester has marked this ticket as resolved.",
        direction=Message.Direction.OUTBOUND,
        channel=Message.Channel.WEB,
        is_system=True,
    )

    CaseAuditLog.objects.create(
        case=case,
        action=CaseAuditLog.ActionText.STATUS_CHANGE,
        new_value=CaseRecord.Status.RESOLVED,
    )

    _broadcast_status(case_uuid, CaseRecord.Status.RESOLVED, "Resolved")

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
    """
    user = request.user

    # Staff shouldn't land here — they have the desk dashboard
    if user.role_access in STAFF_ROLES:
        from django.shortcuts import redirect
        return redirect("desk:case_list")

    cases = (
        CaseRecord.objects
        .filter(requester_email__iexact=user.email)
        .select_related("category")
        .order_by("-created_at")
    )

    # Map internal status → user-facing label
    STATUS_LABEL = {
        CaseRecord.Status.OPEN: ("Waiting", "yellow"),
        CaseRecord.Status.INVESTIGATING: ("In Progress", "blue"),
        CaseRecord.Status.PENDING_INFO: ("Need Info", "orange"),
        CaseRecord.Status.RESOLVED: ("Resolved", "green"),
        CaseRecord.Status.CLOSED: ("Closed", "slate"),
    }

    enriched = []
    now = timezone.now()
    for c in cases:
        label, color = STATUS_LABEL.get(c.status, (c.status, "slate"))
        can_chat = c.status not in (CaseRecord.Status.CLOSED,)
        can_reopen = (
            c.status == CaseRecord.Status.RESOLVED
            and (now - c.updated_at).days < REOPEN_WINDOW_DAYS
        )
        enriched.append({
            "case": c,
            "status_label": label,
            "status_color": color,
            "can_chat": can_chat,
            "can_reopen": can_reopen,
        })

    return render(request, "client/my_tickets.html", {"tickets": enriched})


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
