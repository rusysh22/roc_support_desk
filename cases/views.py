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
    Display the service catalogue as a responsive grid of CaseCategory cards,
    plus any DynamicForms configured to appear on the portal.
    """
    from core.models import DynamicForm
    categories = CaseCategory.objects.exclude(slug__in=["whatsapp-general", "email-general"])
    portal_forms = DynamicForm.objects.filter(is_published=True, show_on_portal=True)
    return render(request, "client/dashboard.html", {"categories": categories, "portal_forms": portal_forms})


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
# Dynamic Form Public Renderer
# =====================================================================

def public_form_view(request, slug):
    """
    Renders a published DynamicForm in a beautiful client-facing view.
    Handles the submission logic and transforms raw POST data into the JSON answers format.
    """
    from core.models import DynamicForm, FormSubmission, SiteConfig
    from django.contrib import messages
    from django.http import Http404
    from django.core.files.storage import FileSystemStorage
    import os
    from django.conf import settings

    form_obj = get_object_or_404(DynamicForm, slug=slug)

    is_preview = request.GET.get('preview') == '1'

    # Check if form is published (skip check in preview mode for staff)
    if not form_obj.is_published and not request.user.is_staff:
        raise Http404("This form is not currently available.")

    # Check if form requires login
    if form_obj.requires_login and not request.user.is_authenticated:
        messages.warning(request, "You must be logged in to view and submit this form.")
        # Assuming you have a 'accounts:login' or using reverse
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    fields = form_obj.fields.all()

    if request.method == "POST":
        # Build the JSON response dict
        answers = {}
        errors = {}

        site_config = SiteConfig.get_solo()
        max_size_bytes = site_config.max_upload_size_mb * 1024 * 1024
        fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'form_uploads'), base_url=f"{settings.MEDIA_URL}form_uploads/")

        for field in fields:
            if 'attachment' in field.field_type:
                files = request.FILES.getlist(f"field_{field.id}")
                if field.is_required and not files:
                    errors[str(field.id)] = "This field is required."
                    answers[str(field.id)] = None
                    continue

                saved_files = []
                for f in files:
                    if f.size > max_size_bytes:
                        errors[str(field.id)] = f"File {f.name} exceeds {site_config.max_upload_size_mb}MB limit."
                        break
                    
                    filename = fs.save(f.name, f)
                    saved_files.append(fs.url(filename))
                
                if field.field_type == 'attachment':
                    answers[str(field.id)] = saved_files[0] if saved_files else None
                else:
                    answers[str(field.id)] = saved_files

            elif field.field_type == 'checkbox':
                # Checkbox fields use getlist because they can have multiple values
                val = request.POST.getlist(f"field_{field.id}")
                if field.is_required and not val:
                    errors[str(field.id)] = "This field is required."
                answers[str(field.id)] = val
            else:
                val = request.POST.get(f"field_{field.id}", "").strip()
                if field.is_required and not val:
                    errors[str(field.id)] = "This field is required."
                answers[str(field.id)] = val

        if errors:
            messages.error(request, "Please fill out all required fields marked with *")
            return render(request, "client/public_form.html", {
                "dynamic_form": form_obj,
                "fields": fields,
                "answers": answers,
                "errors": errors
            })
        
        # Save submission
        FormSubmission.objects.create(
            form=form_obj,
            submitted_by=request.user if request.user.is_authenticated else None,
            answers=answers
        )

        messages.success(request, form_obj.success_message)
        # Using a simple success flag in context or simply rendering directly
        return render(request, "client/public_form.html", {
            "dynamic_form": form_obj,
            "success": True
        })

    return render(request, "client/public_form.html", {
        "dynamic_form": form_obj,
        "fields": fields,
        "is_preview": is_preview,
    })


# =====================================================================
# Auth Required Desk (Admin/Staff) Views below
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
    folder = request.GET.get("folder", "inbox")
    status_filter = request.GET.get("status")
    source_filter = request.GET.get("source")
    category_filter = request.GET.get("category")
    search_query = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    # Folder specific base filtering
    if folder == "spam":
        cases = cases.filter(is_spam=True)
    elif folder == "archive":
        cases = cases.filter(is_archived=True)
    else:  # inbox
        cases = cases.filter(is_spam=False, is_archived=False, master_ticket__isnull=True)

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

    from django.core.paginator import Paginator
    paginator = Paginator(cases, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, "admin/case_list.html", {
        "cases": page_obj,
        "statuses": CaseRecord.Status.choices,
        "sources": CaseRecord.Source.choices,
        "categories": CaseCategory.objects.all(),
        "current_filters": {
            "folder": folder,
            "status": status_filter or "",
            "source": source_filter or "",
            "category": category_filter or "",
            "q": search_query,
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
    })

@staff_required
def case_bulk_action(request):
    """
    Handles bulk actions applied to multiple cases: Merge, Archive, or Spam.
    """
    if request.method == "POST":
        action = request.POST.get("action")
        case_ids = request.POST.getlist("case_ids")
        master_id = request.POST.get("master_id")

        if not action or not case_ids:
            return redirect("desk:case_list")

        cases_qs = CaseRecord.objects.filter(id__in=case_ids)

        if action == "spam":
            cases_qs.update(is_spam=True, is_archived=False)
        elif action == "archive":
            cases_qs.update(is_archived=True, is_spam=False)
        elif action == "unspam":
            cases_qs.update(is_spam=False)
        elif action == "unarchive":
            cases_qs.update(is_archived=False)
        elif action == "merge" and master_id:
            try:
                master = CaseRecord.objects.get(id=master_id)
                # Exclude the master itself from being set as its own sub-ticket
                sub_tickets = cases_qs.exclude(id=master_id)
                sub_tickets.update(master_ticket=master)
            except CaseRecord.DoesNotExist:
                pass

    return redirect(request.META.get("HTTP_REFERER", "desk:case_list"))


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

    # Sub-ticket / Master-ticket contextual tabs
    master = case.master_ticket if case.master_ticket else case
    related_cases = [master] + list(master.sub_tickets.exclude(id=master.id).all())

    return render(request, "admin/case_detail.html", {
        "case": case,
        "chat_messages": messages,
        "unread_msg_ids": unread_msg_ids,
        "rca_form": rca_form,
        "reply_form": reply_form,
        "back_url": back_url,
        "related_cases": related_cases,
        "master_case": master,
        "company_units": CompanyUnit.objects.all(),
    })


@staff_required
def case_update_requester(request, case_id):
    """
    HTMX endpoint / Standard POST to update the Requester (Employee) information.
    Used mainly to fix typos for tickets incoming from WhatsApp / Email.
    """
    if request.method == "POST":
        case = get_object_or_404(CaseRecord, id=case_id)
        if case.requester:
            full_name = request.POST.get("full_name")
            email = request.POST.get("email")
            phone_number = request.POST.get("phone_number")
            job_role = request.POST.get("job_role")
            unit_id = request.POST.get("unit_id")

            if full_name:
                case.requester.full_name = full_name
            if email:
                case.requester.email = email
            if phone_number:
                case.requester.phone_number = phone_number
            if job_role:
                case.requester.job_role = job_role
            if unit_id:
                try:
                    new_unit = CompanyUnit.objects.get(id=unit_id)
                    case.requester.unit = new_unit
                    # Sync the denormalized field on CaseRecord
                    case.requester_unit_name = new_unit.name
                    case.save(update_fields=["requester_unit_name"])
                except CompanyUnit.DoesNotExist:
                    pass

            case.requester.save()

    return redirect("desk:case_detail", case_id=case_id)


@staff_required
def case_forward_escalation(request, case_id):
    """
    POST endpoint to Forward or Escalate a case to an external Email or WhatsApp number.
    Triggers gateways.tasks.escalate_case_task.
    """
    if request.method == "POST":
        case = get_object_or_404(CaseRecord, id=case_id)
        forward_to = request.POST.get("forward_to", "").strip()
        channel = request.POST.get("channel", "EMAIL").upper()
        custom_message = request.POST.get("custom_message", "").strip()

        if forward_to and channel in ["EMAIL", "WHATSAPP"]:
            from gateways.tasks import escalate_case_task
            escalate_case_task.delay(str(case.id), forward_to, channel, custom_message)

            # Log this as an internal comment so the staff knows it was forwarded
            from cases.models import Message
            Message.objects.create(
                case=case,
                sender_staff=request.user,
                body=f"*** ESKALASI TIKET VIA {channel} KE: {forward_to} ***\n\nCatatan Internal Agjen:\n{custom_message}",
                is_internal=True,
                direction=Message.Direction.OUTBOUND,
                channel=Message.Channel.WEB,
            )

            from django.contrib import messages
            messages.success(request, f"Tiket berhasil diekskalasi ke {forward_to} via {channel}.")

    return redirect("desk:case_detail", case_id=case_id)


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
                cc_emails=form.cleaned_data.get("cc_emails", ""),
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
                from gateways.tasks import send_outbound_whatsapp_task
                send_outbound_whatsapp_task.delay(str(msg.id))
                
                # Reset the 10-minute session countdown for the employee
                from gateways.tasks import check_wa_session_timeout_task
                check_wa_session_timeout_task.apply_async((str(case.id),), countdown=600)

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
def case_quick_view(request, case_id):
    """
    HTMX endpoint — returns a quick view modal for a specific case card.
    """
    case = get_object_or_404(CaseRecord, id=case_id)
    return render(request, "partials/quick_view_modal.html", {
        "case": case,
    })

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
    priority_filter = request.GET.get("priority")
    search_query = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if priority_filter:
        cases = cases.filter(priority=priority_filter)
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
        "priorities": CaseRecord.Priority.choices,
        "current_filters": {
            "source": source_filter or "",
            "category": category_filter or "",
            "priority": priority_filter or "",
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
    ).prefetch_related("followers").order_by("-created_at")

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
        "No", "Case Number", "Subject", "Status", "Priority", "Type", "Source", "Category",
        "Requester Name", "Requester Email", "Requester Phone",
        "Requester Unit", "Requester Job Role",
        "Problem Description", "Root Cause Analysis", "Solving Steps", "Quick Notes",
        "Tags", "Followers", "Reference Link",
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
            case.get_priority_display(),
            case.get_case_type_display(),
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
            case.quick_notes or "",
            case.tags or "",
            ", ".join([f.username for f in case.followers.all()]),
            case.link or "",
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
# WhatsApp Integration Dashboard
# =====================================================================

@staff_required
def whatsapp_status_view(request):
    """
    Dashboard view for monitoring Evolution API WhatsApp connection.
    Retrieves instance state and QR code if requested/disconnected.
    """
    from gateways.services import EvolutionAPIService
    from datetime import datetime
    
    svc = EvolutionAPIService()
    state_data = svc.get_instance_state()
    info_data = svc.get_instance_info()
    
    qr_data = None
    
    # Check if we are connected; if not, fetch QR code
    instance_state = state_data.get("instance", {}).get("state", "UNKNOWN") if state_data else "ERROR"
    
    last_connected = None
    if info_data and "updatedAt" in info_data:
        # e.g. "2026-02-28T06:50:37.380Z" -> Convert to aware datetime
        dt_str = info_data.get("updatedAt").replace('Z', '+00:00')
        try:
            last_connected = datetime.fromisoformat(dt_str)
        except ValueError:
            pass
            
    if instance_state not in ["open", "connected"]:
        # Instance is likely disconnected, fetching QR
        qr_data = svc.get_qr_code()
        
    qr_base64 = None
    if qr_data and "base64" in qr_data:
        qr_base64 = qr_data.get("base64")
        
    # If the user is on HTMX, just return the partial piece to swap
    if request.headers.get('HX-Request') == 'true':
        return render(request, "partials/whatsapp_status_card.html", {
            "instance_state": instance_state,
            "qr_base64": qr_base64,
            "last_connected": last_connected,
        })
        
    return render(request, "desk/whatsapp_status.html", {
        "instance_state": instance_state,
        "qr_base64": qr_base64,
        "last_connected": last_connected,
    })


# =====================================================================
# Email Settings Dashboard
# =====================================================================

@staff_required
def email_settings_view(request):
    """
    Dashboard view for monitoring and updating global Email Settings.
    Requires Staff/Admin access.
    """
    from core.models import EmailConfig
    from core.forms import EmailConfigForm
    from django.contrib import messages

    email_config = EmailConfig.get_solo()

    if request.method == "POST":
        form = EmailConfigForm(request.POST, instance=email_config)
        if form.is_valid():
            form.save()
            messages.success(request, "Email configuration updated successfully! Background services will use the new credentials on their next run.")
            return redirect("desk:email_settings")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = EmailConfigForm(instance=email_config)

    return render(request, "desk/email_settings.html", {
        "form": form,
    })


# =====================================================================
# Dynamic Form Builder
# =====================================================================

@staff_required
def form_list_view(request):
    """
    List of all Dynamic Forms.
    """
    from core.models import DynamicForm
    from django.db.models import Q
    from django.core.paginator import Paginator

    query = request.GET.get('q', '').strip()
    
    forms_qs = DynamicForm.objects.all().order_by('-created_at')
    
    if query:
        forms_qs = forms_qs.filter(
            Q(title__icontains=query) | 
            Q(slug__icontains=query)
        )

    paginator = Paginator(forms_qs, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, "desk/forms/list.html", {
        "forms": page_obj,
        "search_query": query
    })


@staff_required
def form_create_view(request):
    """
    Create a new DynamicForm (Settings only).
    """
    from core.forms import DynamicFormForm
    from django.contrib import messages
    import uuid

    if request.method == "POST":
        form = DynamicFormForm(request.POST, request.FILES)
        if form.is_valid():
            new_form = form.save(commit=False)
            new_form.created_by = request.user
            # Ensure slug is truly unique if not explicitly provided or if duplicate
            if not new_form.slug:
                new_form.slug = str(uuid.uuid4())[:8]
            new_form.save()
            messages.success(request, f"Form '{new_form.title}' created! Now add some fields.")
            return redirect("desk:form_edit", pk=new_form.pk)
    else:
        form = DynamicFormForm()

    return render(request, "desk/forms/create.html", {"form": form})


@staff_required
def form_edit_view(request, pk):
    """
    Drag and drop builder to edit form fields and form settings.
    """
    from core.models import DynamicForm, FormField
    from core.forms import DynamicFormForm
    from django.contrib import messages
    import json

    instance = get_object_or_404(DynamicForm, pk=pk)
    
    # Handle AJAX/HTMX Field updates
    if request.headers.get('HX-Request') == 'true':
        action = request.POST.get('action')
        
        if action == 'add_field':
            field_type = request.POST.get('field_type', FormField.FieldTypes.TEXT)
            label = request.POST.get('label', 'New Question')
            help_text = request.POST.get('help_text', '')
            is_required = request.POST.get('is_required') == 'true'
            
            choices = []
            if field_type in [FormField.FieldTypes.DROPDOWN, FormField.FieldTypes.RADIO, FormField.FieldTypes.CHECKBOX, FormField.FieldTypes.SURVEY]:
                choices = request.POST.getlist('choices[]')
                
            # Put at bottom
            last_order = instance.fields.count()
            
            FormField.objects.create(
                form=instance,
                field_type=field_type,
                label=label,
                help_text=help_text,
                is_required=is_required,
                choices=choices,
                order=last_order + 1,
                created_by=request.user
            )
            return render(request, "desk/forms/partials/field_list.html", {"fields": instance.fields.all(), "dynamic_form": instance})
            
        elif action == 'delete_field':
            field_id = request.POST.get('field_id')
            FormField.objects.filter(id=field_id, form=instance).delete()
            return render(request, "desk/forms/partials/field_list.html", {"fields": instance.fields.all(), "dynamic_form": instance})
            
        elif action == 'reorder':
            order_data = json.loads(request.POST.get('order_data', '[]'))
            for item in order_data:
                FormField.objects.filter(id=item['id'], form=instance).update(order=item['order'])
            return HttpResponse(status=200)

        elif action == 'edit_field':
            field_id = request.POST.get('field_id')
            field = get_object_or_404(FormField, id=field_id, form=instance)
            
            field.label = request.POST.get('label', field.label)
            field.help_text = request.POST.get('help_text', '')
            field.is_required = request.POST.get('is_required') == 'true'
            
            new_type = request.POST.get('field_type')
            if new_type:
                field.field_type = new_type
            
            if field.field_type in [FormField.FieldTypes.DROPDOWN, FormField.FieldTypes.RADIO, FormField.FieldTypes.CHECKBOX, FormField.FieldTypes.SURVEY]:
                choices = request.POST.getlist('choices[]')
                if choices:
                    field.choices = choices
            else:
                field.choices = []
            
            field.updated_by = request.user
            field.save()
            return render(request, "desk/forms/partials/field_list.html", {"fields": instance.fields.all(), "dynamic_form": instance})

        elif action == 'send_invitation':
            from django.core.mail import send_mail
            from django.conf import settings
            import json as _json

            raw_emails = request.POST.get('emails', '')
            email_list = [e.strip() for e in raw_emails.replace(';', ',').split(',') if e.strip()]
            form_url = request.build_absolute_uri(f'/f/{instance.slug}/')
            
            sent_count = 0
            for email in email_list:
                try:
                    send_mail(
                        subject=f'You are invited to fill out: {instance.title}',
                        message=f'Hello,\n\nYou have been invited to fill out the form "{instance.title}".\n\n{instance.description}\n\nPlease click the link below to access the form:\n{form_url}\n\nBest regards,\n{request.user.get_full_name() or request.user.username}',
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[email],
                        fail_silently=True,
                    )
                    sent_count += 1
                except Exception:
                    pass
            
            return JsonResponse({'status': 'ok', 'sent': sent_count})

    # Standard form updating
    if request.method == "POST" and not request.headers.get('HX-Request'):
        form = DynamicFormForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            f = form.save(commit=False)
            f.updated_by = request.user
            f.save()
            messages.success(request, "Form settings updated successfully.")
            return redirect("desk:form_edit", pk=instance.pk)
    else:
        form = DynamicFormForm(instance=instance)

    return render(request, "desk/forms/builder.html", {
        "dynamic_form": instance,
        "form": form,
        "fields": instance.fields.all()
    })


@staff_required
def form_responses_view(request, pk):
    """
    View all submissions for a specific DynamicForm.
    Also calculates aggregated metrics for choice-based fields to power the dashboard.
    """
    from core.models import DynamicForm
    from django.core.paginator import Paginator
    from django.db.models import Q
    import collections
    
    instance = get_object_or_404(DynamicForm, pk=pk)
    
    query = request.GET.get('q', '').strip()
    
    submissions_qs = instance.submissions.all().order_by('-submitted_at')
    
    if query:
        submissions_qs = submissions_qs.filter(
            Q(submitted_by__email__icontains=query) |
            Q(submitted_by__first_name__icontains=query) |
            Q(submitted_by__last_name__icontains=query) |
            Q(submitted_by__username__icontains=query)
        )
        
    paginator = Paginator(submissions_qs, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    fields = instance.fields.all()
    
    # Calculate metrics for chart/progress bar visualization
    metrics = {}
    
    # Pre-filter fields that are choice-based
    choice_fields = [f for f in fields if f.field_type in ['dropdown', 'radio', 'checkbox', 'survey']]
    
    if choice_fields and submissions_qs.exists():
        # Iterate over all submissions recursively for metrics instead of just the page
        for field in choice_fields:
            field_id_str = str(field.id)
            counter = collections.Counter()
            
            for sub in submissions_qs:
                ans = sub.answers.get(field_id_str)
                if ans:
                    if isinstance(ans, list):
                        for a in ans:
                            counter[a] += 1
                    else:
                        counter[ans] += 1
            
            total_responses = sum(counter.values())
            
            if total_responses > 0:
                # Get Top 5 most frequent
                top_5 = counter.most_common(5)
                top_5_count = sum(count for _, count in top_5)
                
                # If there are more than 5 distinct answers, group remaining as "Others"
                others_count = total_responses - top_5_count
                
                chart_data = []
                for label, count in top_5:
                    pct = int(round((count / total_responses) * 100)) if total_responses else 0
                    chart_data.append({'label': label, 'count': count, 'percent': pct})
                
                if others_count > 0:
                    pct = int(round((others_count / total_responses) * 100)) if total_responses else 0
                    chart_data.append({'label': 'Others', 'count': others_count, 'percent': pct, 'is_other': True})
                
                metrics[field.id] = {
                    'total': total_responses,
                    'chart_data': chart_data
                }
    
    return render(request, "desk/forms/responses.html", {
        "dynamic_form": instance,
        "submissions": page_obj,
        "search_query": query,
        "fields": fields,
        "metrics": metrics
    })


@staff_required
def form_responses_export(request, pk):
    """
    Export all submissions for a DynamicForm to a CSV file.
    """
    from core.models import DynamicForm
    import csv
    from django.http import HttpResponse
    
    instance = get_object_or_404(DynamicForm, pk=pk)
    submissions = instance.submissions.all()
    fields = instance.fields.all()
    
    
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    filename = f"{instance.title}_responses_{instance.pk}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    writer = csv.writer(response)
    
    # Header Row
    header = ['Date Submitted', 'Submitted By']
    field_ids = []
    
    for f in fields:
        # We export all fields except structural ones
        if f.field_type not in ['title_desc', 'page_break']:
            header.append(f.label)
            field_ids.append(str(f.id))
            
    writer.writerow(header)
    
    # Data Rows
    for sub in submissions:
        username = "Guest User"
        if sub.submitted_by:
            username = f"{sub.submitted_by.first_name} {sub.submitted_by.last_name}".strip()
            if not username:
                username = sub.submitted_by.username
                
        row = [
            sub.submitted_at.strftime('%Y-%m-%d %H:%M:%S'),
            username
        ]
        
        for fid in field_ids:
            ans = sub.answers.get(fid, "")
            
            # Format lists (e.g checkboxes, multiple attachments) safely
            if isinstance(ans, list):
                # If the list consists of dicts or complicated strings, try to flatten it
                row.append(" | ".join([str(a) for a in ans]))
            else:
                row.append(str(ans))
                
        writer.writerow(row)
        
    return response

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
