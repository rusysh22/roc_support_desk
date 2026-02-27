"""
Cases App — Views
==================
Client-facing (public) and Admin/Staff (auth-required) views.

Public views:
    - ``client_dashboard``  — grid of CaseCategory cards.
    - ``create_case``       — form to submit a new case.
    - ``case_submitted``    — confirmation page.

Staff views (login + role_access required):
    - ``case_list``         — filterable list / kanban of all cases.
    - ``case_detail``       — split-panel: chat thread + RCA form.
    - ``case_update_rca``   — HTMX partial to save RCA fields.
    - ``case_send_reply``   — HTMX partial to send a staff reply.
    - ``chat_thread``       — HTMX partial to refresh chat messages.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseForbidden, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import CompanyUnit, Employee, User
from .forms import CaseCreateForm, CaseRCAForm, StaffReplyForm
from .models import Attachment, CaseCategory, CaseRecord, Message, CaseComment, CaseAuditLog


# =====================================================================
# Access Control Decorators
# =====================================================================

def staff_required(view_func):
    """
    Decorator to ensure user is authenticated AND has staff-level
    role_access (SuperAdmin, Manager, or SupportDesk).
    """
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not hasattr(request.user, "role_access"):
            return HttpResponseForbidden("Access denied.")
        allowed_roles = {
            User.RoleAccess.SUPERADMIN,
            User.RoleAccess.MANAGER,
            User.RoleAccess.SUPPORTDESK,
        }
        if request.user.role_access not in allowed_roles:
            return HttpResponseForbidden("Insufficient role access.")
        return view_func(request, *args, **kwargs)
    return _wrapped


def manager_or_admin_required(view_func):
    """Decorator for Manager/SuperAdmin only actions."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        allowed = {User.RoleAccess.SUPERADMIN, User.RoleAccess.MANAGER}
        if request.user.role_access not in allowed:
            return HttpResponseForbidden("Manager or SuperAdmin access required.")
        return view_func(request, *args, **kwargs)
    return _wrapped


# =====================================================================
# Public — Client Portal
# =====================================================================

def client_dashboard(request):
    """
    Display the service catalogue as a responsive grid of CaseCategory cards.
    Completely public — no authentication required.
    """
    categories = CaseCategory.objects.exclude(slug__in=["whatsapp-general", "email-general"])
    return render(request, "client/dashboard.html", {"categories": categories})


def create_case(request, slug=None):
    """
    Public case submission form.

    If ``slug`` is provided, the category is pre-selected.
    """
    initial = {}
    selected_category = None

    if slug:
        selected_category = get_object_or_404(CaseCategory, slug=slug)
        initial["category"] = selected_category

    if request.method == "POST":
        form = CaseCreateForm(request.POST, request.FILES)
        if form.is_valid():
            # Validate attachment sizes (10 MB limit)
            uploaded_files = request.FILES.getlist("attachments")
            file_errors = form.validate_attachments(uploaded_files)
            if file_errors:
                for err in file_errors:
                    form.add_error(None, err)
                return render(request, "client/create_case.html", {
                    "form": form,
                    "selected_category": selected_category,
                })

            email = form.cleaned_data["requester_email"]
            company_unit = form.cleaned_data["company_unit"]

            # Auto-link or auto-create Employee safely
            employee, created = Employee.objects.get_or_create(
                email=email,
                defaults={
                    "full_name": form.cleaned_data["requester_name"],
                    "job_role": form.cleaned_data["job_role"],
                    "unit": company_unit,
                }
            )

            # Update existing employee details if they changed
            if not created:
                updated = False
                if form.cleaned_data["requester_name"] and employee.full_name != form.cleaned_data["requester_name"]:
                    employee.full_name = form.cleaned_data["requester_name"]
                    updated = True
                if company_unit and employee.unit != company_unit:
                    employee.unit = company_unit
                    updated = True
                if form.cleaned_data["job_role"] and employee.job_role != form.cleaned_data["job_role"]:
                    employee.job_role = form.cleaned_data["job_role"]
                    updated = True
                if updated:
                    employee.save(update_fields=["full_name", "unit", "job_role"])

            case = CaseRecord.objects.create(
                requester=employee,  # None if no matching Employee
                requester_email=email,
                requester_name=form.cleaned_data["requester_name"],
                requester_job_role=form.cleaned_data["job_role"],
                requester_unit_name=company_unit.name,
                category=form.cleaned_data["category"],
                subject=form.cleaned_data["subject"],
                problem_description=form.cleaned_data["problem_description"],
                link=form.cleaned_data.get("link", ""),
                source=CaseRecord.Source.WEBFORM,
                status=CaseRecord.Status.OPEN,
                has_unread_messages=True,
            )

            # Create the initial message from the problem description
            msg = Message.objects.create(
                case=case,
                sender_employee=employee,
                body=form.cleaned_data["problem_description"],
                direction=Message.Direction.INBOUND,
                channel=Message.Channel.WEB,
            )

            # Handle multiple attachments
            for uploaded_file in request.FILES.getlist("attachments"):
                Attachment.objects.create(
                    message=msg,
                    file=uploaded_file,
                    original_filename=uploaded_file.name,
                    mime_type=getattr(uploaded_file, "content_type", ""),
                    file_size=uploaded_file.size,
                )

            return redirect("cases:case_submitted", case_id=case.id)
    else:
        form = CaseCreateForm(initial=initial)

    return render(request, "client/create_case.html", {
        "form": form,
        "selected_category": selected_category,
        "categories": CaseCategory.objects.exclude(slug__in=["whatsapp-general", "email-general"]),
        "company_units": CompanyUnit.objects.all(),
    })


def case_submitted(request, case_id):
    """Confirmation page after a case has been submitted."""
    case = get_object_or_404(CaseRecord, id=case_id)
    return render(request, "client/case_submitted.html", {"case": case})


# =====================================================================
# Staff — Admin / Support Desk
# =====================================================================

@staff_required
def case_list(request):
    """
    Filterable list of all cases for the support desk.
    Supports status/source/category filtering via GET params.
    """
    cases = CaseRecord.objects.select_related(
        "requester", "category", "assigned_to"
    ).all()

    # --- Filtering ---
    status_filter = request.GET.get("status")
    source_filter = request.GET.get("source")
    category_filter = request.GET.get("category")
    search_query = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    if status_filter:
        cases = cases.filter(status=status_filter)
    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if search_query:
        cases = cases.filter(
            Q(id__icontains=search_query) |
            Q(subject__icontains=search_query) |
            Q(requester_name__icontains=search_query) |
            Q(requester__full_name__icontains=search_query) |
            Q(requester_email__icontains=search_query) |
            Q(requester__email__icontains=search_query) |
            Q(requester__phone_number__icontains=search_query) |
            Q(requester_unit_name__icontains=search_query) |
            Q(requester__unit__name__icontains=search_query)
        )
    if date_from:
        cases = cases.filter(created_at__date__gte=date_from)
    if date_to:
        cases = cases.filter(created_at__date__lte=date_to)

    return render(request, "admin/case_list.html", {
        "cases": cases,
        "statuses": CaseRecord.Status.choices,
        "sources": CaseRecord.Source.choices,
        "categories": CaseCategory.objects.all(),
        "current_filters": {
            "status": status_filter or "",
            "source": source_filter or "",
            "category": category_filter or "",
            "q": search_query,
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
    })


@staff_required
def case_detail(request, case_id):
    """
    Split-panel case detail view:
    - Left panel:  real-time chat/thread with the employee.
    - Right panel: RCA + solving steps form.
    """
    case = get_object_or_404(
        CaseRecord.objects.select_related("requester", "category", "assigned_to"),
        id=case_id,
    )
    
    unread_msg_ids = []
    if case.has_unread_messages:
        unread_msg_ids = list(case.messages.filter(direction="IN", is_read=False).values_list("id", flat=True))
        case.has_unread_messages = False
        case.save(update_fields=["has_unread_messages"])
        case.messages.filter(id__in=unread_msg_ids).update(is_read=True)

    messages = case.messages.select_related(
        "sender_employee", "sender_staff"
    ).prefetch_related("attachments").all()
    rca_form = CaseRCAForm(instance=case)
    reply_form = StaffReplyForm()

    # Capture the referring URL (the dashboard/list with filters)
    back_url = request.GET.get("back") or request.META.get("HTTP_REFERER", "/desk/cases/")
    # If the referer is just the current page itself, fallback to the list
    if f"/desk/cases/{case_id}" in back_url:
        back_url = "/desk/cases/"

    return render(request, "admin/case_detail.html", {
        "case": case,
        "chat_messages": messages,
        "unread_msg_ids": unread_msg_ids,
        "rca_form": rca_form,
        "reply_form": reply_form,
        "back_url": back_url,
    })


@staff_required
def case_update_rca(request, case_id):
    """
    HTMX endpoint — update root cause analysis, solving steps, status,
    and SLA fields.
    """
    case = get_object_or_404(CaseRecord, id=case_id)

    if request.method == "POST":
        original_status = case.status
        form = CaseRCAForm(request.POST, instance=case)
        if form.is_valid():
            desired_status = form.cleaned_data.get("status")
            
            # SLA validation if attempting to Close
            if desired_status == CaseRecord.Status.CLOSED:
                if not form.cleaned_data.get("root_cause_analysis") or not form.cleaned_data.get("solving_steps"):
                    return render(request, "partials/rca_form.html", {
                        "case": case,
                        "rca_form": form,
                        "error_message": "SLA Details (Root Cause Analysis & Solving Steps) are required to close the ticket.",
                    })
                    
                # Save changes but revert status until confirmed in the modal
                case = form.save(commit=False)
                case.status = original_status
                case.updated_by = request.user
                
                # Check what changed before saving to create audit logs
                if form.changed_data:
                    for field in form.changed_data:
                        if field == 'status': 
                            continue # we reverted status above
                        old_val = form.initial.get(field)
                        new_val = form.cleaned_data.get(field)
                        
                        # Followers is M2M, handled after save typically, but we log the attempt
                        if field == 'followers':
                            old_val = ", ".join([u.username for u in case.followers.all()])
                            new_val = ", ".join([u.username for u in new_val]) if new_val else ""

                        CaseAuditLog.objects.create(
                            case=case,
                            action=CaseAuditLog.ActionText.UPDATED,
                            field_name=field,
                            old_value=str(old_val) if old_val else "",
                            new_value=str(new_val) if new_val else "",
                            created_by=request.user,
                        )

                case.save()
                form.save_m2m() # Important for followers
                
                from django.template.loader import render_to_string
                form_response = render(request, "partials/rca_form.html", {
                    "case": case,
                    "rca_form": CaseRCAForm(instance=case),
                    "success_message": "Details saved. Please confirm ticket closure below.",
                })
                
                modal_html = render_to_string("partials/closure_modal.html", {"case": case}, request=request)
                oob_modal = f'<div id="htmx-modal" hx-swap-oob="innerHTML">{modal_html}</div>'
                
                return HttpResponse(form_response.content.decode() + oob_modal)

            # Normal save for non-closing updates
            case = form.save(commit=False)
            case.updated_by = request.user
            
            # Check what changed before saving to create audit logs
            if form.changed_data:
                for field in form.changed_data:
                    old_val = form.initial.get(field)
                    new_val = form.cleaned_data.get(field)
                    
                    action_type = CaseAuditLog.ActionText.UPDATED
                    if field == 'status':
                        action_type = CaseAuditLog.ActionText.STATUS_CHANGE
                    elif field in ['assigned_to', 'followers']:
                        action_type = CaseAuditLog.ActionText.ASSIGNED
                    
                    if field == 'followers':
                        old_val = ", ".join([u.username for u in case.followers.all()])
                        new_val = ", ".join([u.username for u in new_val]) if new_val else ""
                    elif field == 'assigned_to':
                        old_val = str(old_val) if old_val else "Unassigned"
                        new_val = str(new_val) if new_val else "Unassigned"

                    CaseAuditLog.objects.create(
                        case=case,
                        action=action_type,
                        field_name=field,
                        old_value=str(old_val) if old_val is not None else "",
                        new_value=str(new_val) if new_val is not None else "",
                        created_by=request.user,
                    )

            case.save()
            form.save_m2m() # Important for followers

            # Redirect to the previous page (likely the filtered table list)
            # using the back_url passed through the form
            redirect_url = request.POST.get("back_url", "/desk/cases/")
            
            # safeguard against infinite reloads on the same detail page
            if f"/desk/cases/{case_id}" in redirect_url:
                redirect_url = "/desk/cases/"
                
            return HttpResponse(
                f'<script>window.location.href = "{redirect_url}";</script>',
                content_type="text/html"
            )
        else:
            return render(request, "partials/rca_form.html", {
                "case": case,
                "rca_form": form,
                "error_message": "Please fix the errors below.",
                "back_url": request.POST.get("back_url", "/desk/cases/"),
            })

    return HttpResponse(status=405)


@staff_required
def case_send_reply(request, case_id):
    """
    HTMX endpoint — staff sends a reply message within the case thread.
    """
    case = get_object_or_404(CaseRecord, id=case_id)

    if request.method == "POST":
        form = StaffReplyForm(request.POST, request.FILES)
        if form.is_valid():
            # Determine the channel based on the original case source
            reply_channel = Message.Channel.WEB
            
            send_as_email = request.POST.get("send_as_email") == "true"
            if case.source == CaseRecord.Source.WEBFORM and send_as_email:
                reply_channel = Message.Channel.EMAIL
            elif case.source == CaseRecord.Source.EMAIL:
                reply_channel = Message.Channel.EMAIL
            elif case.source == CaseRecord.Source.EVOLUTION_WA:
                reply_channel = Message.Channel.WHATSAPP  # Prep for future WA outbound support

            msg = Message.objects.create(
                case=case,
                sender_staff=request.user,
                body=form.cleaned_data["body"],
                direction=Message.Direction.OUTBOUND,
                channel=reply_channel,
            )

            # Handle optional attachment
            uploaded_file = form.cleaned_data.get("attachment")
            if uploaded_file:
                Attachment.objects.create(
                    message=msg,
                    file=uploaded_file,
                    original_filename=uploaded_file.name,
                    mime_type=getattr(uploaded_file, "content_type", ""),
                    file_size=uploaded_file.size,
                )

            # Update case timestamp
            case.updated_by = request.user
            case.save(update_fields=["updated_at", "updated_by"])

            # Trigger Outbound Async Tasks based on Channel
            if reply_channel == Message.Channel.EMAIL:
                from gateways.tasks import send_outbound_email_task
                send_outbound_email_task.delay(str(msg.id))
            elif reply_channel == Message.Channel.WHATSAPP:
                # Send reply via WhatsApp (Evolution API)
                if case.requester and case.requester.phone_number:
                    try:
                        from gateways.services import EvolutionAPIService
                        svc = EvolutionAPIService()
                        svc.send_whatsapp_message(
                            case.requester.phone_number,
                            form.cleaned_data["body"],
                        )
                    except Exception as exc:
                        import logging
                        logging.getLogger(__name__).warning(
                            "Failed to send WA reply for case %s: %s",
                            case.case_number, exc,
                        )
                        # Update message status to FAILED
                        msg.delivery_status = Message.DeliveryStatus.FAILED
                        msg.delivery_error = str(exc)
                        msg.save(update_fields=["delivery_status", "delivery_error"])

            # Return the refreshed chat thread partial
            messages = case.messages.select_related(
                "sender_employee", "sender_staff"
            ).prefetch_related("attachments").all()
            return render(request, "partials/chat_thread.html", {
                "case": case,
                "chat_messages": messages,
            })

    return HttpResponse(status=405)


@staff_required
def chat_thread(request, case_id):
    """
    HTMX endpoint — returns the chat thread partial for polling refresh.
    """
    case = get_object_or_404(CaseRecord, id=case_id)
    
    unread_msg_ids = []
    if case.has_unread_messages:
        unread_msg_ids = list(case.messages.filter(direction="IN", is_read=False).values_list("id", flat=True))
        case.has_unread_messages = False
        case.save(update_fields=["has_unread_messages"])
        case.messages.filter(id__in=unread_msg_ids).update(is_read=True)

    messages = case.messages.select_related(
        "sender_employee", "sender_staff"
    ).prefetch_related("attachments").all()

    return render(request, "partials/chat_thread.html", {
        "case": case,
        "chat_messages": messages,
        "unread_msg_ids": unread_msg_ids,
    })


# =====================================================================
# Kanban Board
# =====================================================================

@staff_required
def case_kanban(request):
    """
    Kanban board — cases grouped by status columns.
    Supports same filters as case_list.
    """
    cases = CaseRecord.objects.select_related(
        "requester", "category", "assigned_to"
    ).all()

    # --- Filtering ---
    source_filter = request.GET.get("source")
    category_filter = request.GET.get("category")
    search_query = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if search_query:
        cases = cases.filter(
            Q(id__icontains=search_query) |
            Q(subject__icontains=search_query) |
            Q(requester_name__icontains=search_query) |
            Q(requester__full_name__icontains=search_query) |
            Q(requester_email__icontains=search_query) |
            Q(requester__email__icontains=search_query) |
            Q(requester__phone_number__icontains=search_query) |
            Q(requester_unit_name__icontains=search_query) |
            Q(requester__unit__name__icontains=search_query)
        )
    if date_from:
        cases = cases.filter(created_at__date__gte=date_from)
    if date_to:
        cases = cases.filter(created_at__date__lte=date_to)

    # Group by status
    status_columns = [
        ("Open", "⏳", "Open", "#6366f1"),
        ("Investigating", "🔍", "Investigating", "#f59e0b"),
        ("PendingInfo", "⏸️", "Pending Info", "#0ea5e9"),
        ("Resolved", "✅", "Resolved", "#10b981"),
        ("Closed", "🔒", "Closed", "#64748b"),
    ]

    kanban_data = []
    for status_val, icon, label, color in status_columns:
        column_cases = [c for c in cases if c.status == status_val]
        kanban_data.append({
            "status": status_val,
            "icon": icon,
            "label": label,
            "color": color,
            "cases": column_cases,
            "count": len(column_cases),
        })

    return render(request, "admin/case_kanban.html", {
        "kanban_data": kanban_data,
        "sources": CaseRecord.Source.choices,
        "categories": CaseCategory.objects.all(),
        "current_filters": {
            "source": source_filter or "",
            "category": category_filter or "",
            "q": search_query,
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
    })


@staff_required
def case_update_status(request, case_id):
    """
    HTMX endpoint — update case status (drag-and-drop from Kanban).
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    case = get_object_or_404(CaseRecord, id=case_id)
    new_status = request.POST.get("status", "")

    valid_statuses = [s[0] for s in CaseRecord.Status.choices]
    if new_status not in valid_statuses:
        return HttpResponse("Invalid status", status=400)

    # Prevent direct closing without SLA details from Kanban
    if new_status == CaseRecord.Status.CLOSED:
        if not case.root_cause_analysis or not case.solving_steps:
            return HttpResponse("SLA details (Root Cause Analysis & Solving Steps) are required before closing. Please click the case to open the detail panel and fill them out.", status=400)
            
        # Generate and return the modal directly
        return render(request, "partials/closure_modal.html", {"case": case})

    case.status = new_status
    case.updated_by = request.user
    case.save(update_fields=["status", "updated_at", "updated_by"])

    return HttpResponse(status=204)


@staff_required
def case_close_and_notify(request, case_id):
    """
    HTMX endpoint — processes the final SLA closure message from the preview modal,
    closes the ticket, and sends the OUTBOUND message via WA/Email.
    """
    if request.method != "POST":
        return HttpResponse(status=405)
        
    case = get_object_or_404(CaseRecord, id=case_id)
    closure_msg_body = request.POST.get("closure_message_body", "").strip()
    
    if not closure_msg_body:
        return HttpResponse("Message body cannot be empty.", status=400)
        
    # Mark the case as closed
    case.status = CaseRecord.Status.CLOSED
    case.updated_by = request.user
    case.save(update_fields=["status", "updated_at", "updated_by"])
    
    # Determine the reply channel based on the source
    reply_channel = Message.Channel.WEB
    if case.source in [CaseRecord.Source.WEBFORM, CaseRecord.Source.EMAIL]:
        reply_channel = Message.Channel.EMAIL
    elif case.source == CaseRecord.Source.EVOLUTION_WA:
        reply_channel = Message.Channel.WHATSAPP
        
    # Create the Outbound Message containing the closure payload
    msg = Message.objects.create(
        case=case,
        sender_staff=request.user,
        body=closure_msg_body,
        direction=Message.Direction.OUTBOUND,
        channel=reply_channel,
    )
    
    # Trigger appropriate sending mechanisms
    if reply_channel == Message.Channel.EMAIL:
        from gateways.tasks import send_outbound_email_task
        send_outbound_email_task.delay(str(msg.id))
    elif reply_channel == Message.Channel.WHATSAPP:
        if case.requester and case.requester.phone_number:
            try:
                from gateways.services import EvolutionAPIService
                svc = EvolutionAPIService()
                svc.send_whatsapp_message(
                    case.requester.phone_number,
                    closure_msg_body,
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to send WA closure for case %s: %s",
                    case.case_number, exc,
                )
                msg.delivery_status = Message.DeliveryStatus.FAILED
                msg.delivery_error = str(exc)
                msg.save(update_fields=["delivery_status", "delivery_error"])
                
    # Return a success script to trigger a reload or update
    return HttpResponse(
        '<script>window.location.reload();</script>',
        content_type="text/html"
    )

    
# =====================================================================
# Calendar View
# =====================================================================

@staff_required
def case_calendar(request):
    """
    Calendar view — shows cases on a monthly calendar.
    """
    import json
    from django.utils.timezone import localtime

    cases = CaseRecord.objects.select_related(
        "requester", "category", "assigned_to"
    ).all()

    source_filter = request.GET.get("source")
    category_filter = request.GET.get("category")
    status_filter = request.GET.get("status")
    search_query = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if status_filter:
        cases = cases.filter(status=status_filter)
    if search_query:
        cases = cases.filter(
            Q(id__icontains=search_query) |
            Q(subject__icontains=search_query) |
            Q(requester_name__icontains=search_query) |
            Q(requester__full_name__icontains=search_query) |
            Q(requester_email__icontains=search_query) |
            Q(requester__email__icontains=search_query) |
            Q(requester__phone_number__icontains=search_query) |
            Q(requester_unit_name__icontains=search_query) |
            Q(requester__unit__name__icontains=search_query)
        )
    if date_from:
        cases = cases.filter(created_at__date__gte=date_from)
    if date_to:
        cases = cases.filter(created_at__date__lte=date_to)

    # Build JSON events for the calendar
    status_colors = {
        "Open": "#6366f1",
        "Investigating": "#f59e0b",
        "PendingInfo": "#0ea5e9",
        "Resolved": "#10b981",
        "Closed": "#64748b",
    }

    events = []
    for c in cases:
        events.append({
            "id": str(c.id),
            "title": f"[{c.case_number}] {c.subject[:40]}",
            "start": localtime(c.created_at).strftime("%Y-%m-%d"),
            "url": f"/desk/cases/{c.id}/",
            "color": status_colors.get(c.status, "#6366f1"),
            "status": c.status,
            "requester": c.requester.full_name if c.requester else "—",
        })

    return render(request, "admin/case_calendar.html", {
        "events_json": json.dumps(events),
        "statuses": CaseRecord.Status.choices,
        "sources": CaseRecord.Source.choices,
        "categories": CaseCategory.objects.all(),
        "current_filters": {
            "status": status_filter or "",
            "source": source_filter or "",
            "category": category_filter or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
    })


# =====================================================================
# Export to Excel
# =====================================================================

@staff_required
def case_export_excel(request):
    """
    Export all cases to Excel (.xlsx) with all fields.
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from django.utils.timezone import localtime

    cases = CaseRecord.objects.select_related(
        "requester", "requester__unit", "category", "assigned_to",
        "created_by", "updated_by"
    ).order_by("-created_at")

    # Apply filters if provided
    status_filter = request.GET.get("status")
    source_filter = request.GET.get("source")
    category_filter = request.GET.get("category")
    search_query = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    if status_filter:
        cases = cases.filter(status=status_filter)
    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if search_query:
        cases = cases.filter(subject__icontains=search_query)
    if date_from:
        cases = cases.filter(created_at__date__gte=date_from)
    if date_to:
        cases = cases.filter(created_at__date__lte=date_to)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cases"

    # Header style
    header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin", color="D1D5DB"),
        right=Side(style="thin", color="D1D5DB"),
        top=Side(style="thin", color="D1D5DB"),
        bottom=Side(style="thin", color="D1D5DB"),
    )

    headers = [
        "No", "Case Number", "Subject", "Status", "Source", "Category",
        "Requester Name", "Requester Email", "Requester Phone",
        "Requester Unit", "Requester Job Role",
        "Problem Description", "Root Cause Analysis", "Solving Steps",
        "Assigned To", "Response SLA", "Resolution SLA",
        "Created At", "Updated At",
    ]

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

    # Data rows
    data_align = Alignment(vertical="top", wrap_text=True)
    for row_idx, case in enumerate(cases, 2):
        row_data = [
            row_idx - 1,
            case.case_number,
            case.subject,
            case.get_status_display(),
            case.get_source_display(),
            case.category.name if case.category else "",
            case.requester.full_name if case.requester else case.requester_name,
            case.requester.email if case.requester else case.requester_email,
            case.requester.phone_number if case.requester else "",
            case.requester.unit.name if case.requester and case.requester.unit else case.requester_unit_name,
            case.requester.job_role if case.requester else case.requester_job_role,
            case.problem_description or "",
            case.root_cause_analysis or "",
            case.solving_steps or "",
            case.assigned_to.username if case.assigned_to else "Unassigned",
            localtime(case.response_due_at).strftime("%d/%m/%Y %H:%M") if case.response_due_at else "",
            localtime(case.resolution_due_at).strftime("%d/%m/%Y %H:%M") if case.resolution_due_at else "",
            localtime(case.created_at).strftime("%d/%m/%Y %H:%M") if case.created_at else "",
            localtime(case.updated_at).strftime("%d/%m/%Y %H:%M") if case.updated_at else "",
        ]

        for col_idx, value in enumerate(row_data, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = data_align
            cell.border = thin_border

    # Auto-width columns
    for col_idx in range(1, len(headers) + 1):
        col_letter = get_column_letter(col_idx)
        max_length = len(str(headers[col_idx - 1]))
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=col_idx, max_col=col_idx):
            for cell in row:
                if cell.value:
                    max_length = max(max_length, min(len(str(cell.value)), 50))
        ws.column_dimensions[col_letter].width = max_length + 4

    # Freeze header row
    ws.freeze_panes = "A2"

    # Response
    from io import BytesIO
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    now_str = timezone.now().strftime("%Y%m%d_%H%M")
    filename = f"RoC_Desk_Cases_{now_str}.xlsx"

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@staff_required
def notification_bell(request):
    """
    HTMX endpoint — returns a dropdown of recent cases and unread incoming messages (last 24 hours).
    """
    from datetime import timedelta
    from django.utils import timezone
    
    time_threshold = timezone.now() - timedelta(hours=24)
    
    # Get previously clicked notifications from the session
    read_notifs = request.session.get("read_notifications", [])
    read_case_ids = [n.replace("case_", "") for n in read_notifs if n.startswith("case_")]
    read_msg_ids = [n.replace("msg_", "") for n in read_notifs if n.startswith("msg_")]
    read_mention_ids = [n.replace("mention_", "") for n in read_notifs if n.startswith("mention_")]
    
    # 1. New Cases in the last 24h
    recent_cases = CaseRecord.objects.select_related("requester").filter(
        created_at__gte=time_threshold
    ).exclude(id__in=read_case_ids).order_by("-created_at")[:10]
    
    # 2. Unread Incoming Messages in the last 24h
    recent_unread_messages = Message.objects.select_related(
        "case", "sender_employee"
    ).filter(
        direction=Message.Direction.INBOUND,
        is_read=False,
        created_at__gte=time_threshold
    ).exclude(id__in=read_msg_ids).order_by("-created_at")[:10]
    
    # 3. Recent Mentions in Internal Notes
    recent_mentions = CaseComment.objects.select_related("case", "author").filter(
        mentions=request.user,
        created_at__gte=time_threshold
    ).exclude(id__in=read_mention_ids).order_by("-created_at")[:10]
    
    total_notifications = recent_cases.count() + recent_unread_messages.count() + recent_mentions.count()
    
    return render(request, "partials/notification_bell.html", {
        "recent_cases": recent_cases,
        "recent_unread_messages": recent_unread_messages,
        "recent_mentions": recent_mentions,
        "total_notifications": total_notifications,
    })


@staff_required
def mark_notification_read(request, notif_type, notif_id):
    """
    Marks a specific notification as 'read' in the user's session 
    so it no longer appears in the bell dropdown, then redirects
    to the target case detail URL.
    """
    notif_key = f"{notif_type}_{notif_id}"
    read_notifs = request.session.get("read_notifications", [])
    if notif_key not in read_notifs:
        read_notifs.append(notif_key)
        request.session["read_notifications"] = read_notifs
    
    # Both types ultimately lead to the Case Detail page
    target_case_id = notif_id
    if notif_type == "msg":
        try:
            msg = Message.objects.get(id=notif_id)
            target_case_id = msg.case.id
        except Message.DoesNotExist:
            target_case_id = None
    elif notif_type == "mention":
        try:
            comment = CaseComment.objects.get(id=notif_id)
            target_case_id = comment.case.id
        except CaseComment.DoesNotExist:
            target_case_id = None
            
    if target_case_id:
        return redirect("desk:case_detail", case_id=target_case_id)
    return redirect("desk:case_list")


# =====================================================================
# Internal Case Comments
# =====================================================================

@staff_required
def case_add_comment(request, case_id):
    """
    HTMX endpoint for saving and retrieving internal staff comments on a case.
    Also parses @username mentions.
    """
    import re
    from core.models import User
    
    case = get_object_or_404(CaseRecord, id=case_id)
    
    if request.method == "POST":
        body = request.POST.get("comment_body", "").strip()
        if body:
            comment = CaseComment.objects.create(
                case=case,
                author=request.user,
                body=body
            )
            
            # Parse @usernames
            usernames = set(re.findall(r"@([a-zA-Z0-9_]+)", body))
            if usernames:
                mentioned_users = User.objects.filter(username__in=usernames)
                if mentioned_users.exists():
                    comment.mentions.add(*mentioned_users)

    # Return the refreshed comments partial
    return render(request, "partials/case_comments.html", {
        "case": case,
        "comments": case.internal_comments.all(),
    })

# =====================================================================
# API Endpoints
# =====================================================================

@login_required
def api_users_list(request):
    """
    Returns a JSON list of active users to power the @mention autocomplete.
    """
    from django.http import JsonResponse
    from core.models import User
    
    users = User.objects.filter(is_active=True).values("username", "first_name", "last_name")
    
    data = []
    for u in users:
        # Prefer full name if exists, otherwise fallback to username
        name = f"{u['first_name']} {u['last_name']}".strip()
        display = name if name else u['username']
        
        data.append({
            "key": display,
            "value": u['username']
        })
        
    return JsonResponse(data, safe=False)
