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
from django.http import HttpResponseForbidden, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import CompanyUnit, Employee, User
from .forms import CaseCreateForm, CaseRCAForm, StaffReplyForm
from .models import Attachment, CaseCategory, CaseRecord, Message


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
    categories = CaseCategory.objects.all()
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

            # Try to auto-link Employee if email matches (optional)
            employee = Employee.objects.filter(email=email).first()

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
        "categories": CaseCategory.objects.all(),
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

    if status_filter:
        cases = cases.filter(status=status_filter)
    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if search_query:
        cases = cases.filter(subject__icontains=search_query)

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
    messages = case.messages.select_related(
        "sender_employee", "sender_staff"
    ).prefetch_related("attachments").all()
    rca_form = CaseRCAForm(instance=case)
    reply_form = StaffReplyForm()

    return render(request, "admin/case_detail.html", {
        "case": case,
        "messages": messages,
        "rca_form": rca_form,
        "reply_form": reply_form,
    })


@staff_required
def case_update_rca(request, case_id):
    """
    HTMX endpoint — update root cause analysis, solving steps, status,
    and SLA fields.
    """
    case = get_object_or_404(CaseRecord, id=case_id)

    if request.method == "POST":
        form = CaseRCAForm(request.POST, instance=case)
        if form.is_valid():
            case = form.save(commit=False)
            case.updated_by = request.user
            case.save()

            # Return updated form partial for HTMX swap
            return render(request, "partials/rca_form.html", {
                "case": case,
                "rca_form": CaseRCAForm(instance=case),
                "success_message": "Case updated successfully.",
            })
        else:
            return render(request, "partials/rca_form.html", {
                "case": case,
                "rca_form": form,
                "error_message": "Please fix the errors below.",
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
            if case.source == CaseRecord.Source.EMAIL:
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

            # Return the refreshed chat thread partial
            messages = case.messages.select_related(
                "sender_employee", "sender_staff"
            ).prefetch_related("attachments").all()
            return render(request, "partials/chat_thread.html", {
                "case": case,
                "messages": messages,
            })

    return HttpResponse(status=405)


@staff_required
def chat_thread(request, case_id):
    """
    HTMX endpoint — returns the chat thread partial for polling refresh.
    """
    case = get_object_or_404(CaseRecord, id=case_id)
    messages = case.messages.select_related(
        "sender_employee", "sender_staff"
    ).prefetch_related("attachments").all()

    return render(request, "partials/chat_thread.html", {
        "case": case,
        "messages": messages,
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

    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if search_query:
        cases = cases.filter(subject__icontains=search_query)

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

    case.status = new_status
    case.updated_by = request.user
    case.save(update_fields=["status", "updated_at", "updated_by"])

    return HttpResponse(status=204)


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

    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if status_filter:
        cases = cases.filter(status=status_filter)

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

    if status_filter:
        cases = cases.filter(status=status_filter)
    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if search_query:
        cases = cases.filter(subject__icontains=search_query)

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

