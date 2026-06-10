"""
E-Signature App — Staff Views
================================
All views here require login and are role-gated (no PortalUser access).
The public magic-link signing view lives in views_public.py.
"""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from ipware import get_client_ip

from core.models import User
from .forms import DocumentUploadForm
from .models import Signer, SignatureDocument, SignaturePlacement, SignatureFlowTemplate
from .services import (
    cancel_document,
    remind_signer,
    reopen_document,
    send_document,
)


# ---------------------------------------------------------------------------
# Role guard helpers
# ---------------------------------------------------------------------------

def _is_staff(user):
    """PortalUser cannot access e-sign management."""
    return user.is_authenticated and user.role_access != "PortalUser"


def _staff_required(view_func):
    """Combined login + role check decorator."""
    @login_required
    def wrapper(request, *args, **kwargs):
        if not _is_staff(request.user):
            return HttpResponseForbidden("Access restricted to staff users.")
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


# ---------------------------------------------------------------------------
# Document list
# ---------------------------------------------------------------------------

@_staff_required
def document_list(request):
    documents = (
        SignatureDocument.objects
        .filter(created_by=request.user)
        .prefetch_related("signers")
        .order_by("-created_at")
    )
    pending_count = documents.filter(status=SignatureDocument.Status.PENDING).count()
    return render(request, "esign/document_list.html", {
        "documents": documents,
        "pending_count": pending_count,
    })


# ---------------------------------------------------------------------------
# Document upload (step 1)
# ---------------------------------------------------------------------------

@_staff_required
def document_create(request):
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.created_by = request.user
            doc.updated_by = request.user

            # Count pages and compute hash via pypdf
            try:
                from pypdf import PdfReader
                pdf_file = form.cleaned_data["original_pdf"]
                pdf_file.seek(0)
                reader = PdfReader(pdf_file)
                doc.page_count = len(reader.pages)
                pdf_file.seek(0)
            except Exception:
                doc.page_count = 1

            doc.save()

            # Compute SHA-256 hash after file is saved (field now has a path)
            try:
                doc.compute_hash()
                doc.save(update_fields=["document_hash"])
            except Exception:
                pass

            return redirect("esign:document_configure", pk=doc.pk)
    else:
        form = DocumentUploadForm()

    return render(request, "esign/document_create.html", {"form": form})

@_staff_required
def document_duplicate(request, pk):
    original_doc = get_object_or_404(SignatureDocument, pk=pk)
    if original_doc.created_by != request.user:
        return HttpResponseForbidden()

    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.created_by = request.user
            doc.updated_by = request.user
            doc.routing_mode = original_doc.routing_mode
            doc.stamp_show_datetime = original_doc.stamp_show_datetime
            doc.stamp_show_name = original_doc.stamp_show_name
            doc.stamp_show_job_title = original_doc.stamp_show_job_title
            doc.stamp_show_company_logo = original_doc.stamp_show_company_logo

            # Count pages and compute hash via pypdf
            try:
                from pypdf import PdfReader
                pdf_file = form.cleaned_data["original_pdf"]
                pdf_file.seek(0)
                reader = PdfReader(pdf_file)
                doc.page_count = len(reader.pages)
                pdf_file.seek(0)
            except Exception:
                doc.page_count = 1

            doc.save()
            
            try:
                doc.compute_hash()
                doc.save(update_fields=["document_hash"])
            except Exception:
                pass

            # Copy signers and placements
            for orig_signer in original_doc.signers.all():
                new_signer = Signer.objects.create(
                    document=doc,
                    user=orig_signer.user,
                    external_name=orig_signer.external_name,
                    external_email=orig_signer.external_email,
                    job_title=orig_signer.job_title,
                    company=orig_signer.company,
                    order=orig_signer.order,
                    role=orig_signer.role,
                    notes="",
                    status=Signer.Status.WAITING,
                )
                for orig_placement in orig_signer.placements.all():
                    SignaturePlacement.objects.create(
                        document=doc,
                        signer=new_signer,
                        field_type=orig_placement.field_type,
                        page_number=orig_placement.page_number,
                        x=orig_placement.x,
                        y=orig_placement.y,
                        width=orig_placement.width,
                        height=orig_placement.height,
                        required=orig_placement.required,
                    )

            return redirect("esign:document_configure", pk=doc.pk)
    else:
        form = DocumentUploadForm(initial={'title': f"Copy of {original_doc.title}"})

    return render(request, "esign/document_duplicate.html", {"form": form, "original_doc": original_doc})



# ---------------------------------------------------------------------------
# Replace PDF (DRAFT only) — creator can swap the file before sending
# ---------------------------------------------------------------------------

@require_POST
@_staff_required
def document_replace_pdf(request, pk):
    doc = get_object_or_404(SignatureDocument, pk=pk)
    if doc.created_by != request.user:
        return HttpResponseForbidden()
    if doc.status not in [SignatureDocument.Status.DRAFT, SignatureDocument.Status.PENDING]:
        messages.error(request, "The PDF can only be replaced while the document is a draft or pending.")
        if doc.status == SignatureDocument.Status.PENDING:
            return redirect("esign:document_detail", pk=pk)
        return redirect("esign:document_configure", pk=pk)

    new_pdf = request.FILES.get("original_pdf")
    def redirect_back():
        if doc.status == SignatureDocument.Status.PENDING:
            return redirect("esign:document_detail", pk=pk)
        return redirect("esign:document_configure", pk=pk)

    if not new_pdf:
        messages.error(request, "No file selected.")
        return redirect_back()

    # Validate MIME type
    try:
        import magic as _magic
        mime = _magic.from_buffer(new_pdf.read(2048), mime=True)
        new_pdf.seek(0)
        if mime != "application/pdf" and not new_pdf.name.lower().endswith(".pdf"):
            messages.error(request, f"Only PDF files are accepted. (Detected: {mime})")
            return redirect_back()
    except Exception:
        pass  # python-magic unavailable — skip mime check, trust extension

    # Delete old file and save new one
    if doc.original_pdf:
        doc.original_pdf.delete(save=False)
    doc.original_pdf = new_pdf

    # Recount pages
    try:
        from pypdf import PdfReader
        new_pdf.seek(0)
        reader = PdfReader(new_pdf)
        doc.page_count = len(reader.pages)
        new_pdf.seek(0)
    except Exception:
        doc.page_count = 1

    doc.save(update_fields=["original_pdf", "page_count"])

    try:
        doc.compute_hash()
        doc.save(update_fields=["document_hash"])
    except Exception:
        pass

    if doc.status == SignatureDocument.Status.PENDING:
        from .services import _update_preview_pdf, _log
        from .models import SignatureEvent
        
        # Regenerate preview with existing signatures
        _update_preview_pdf(doc)
        
        # Log the amendment
        ip, _ = get_client_ip(request)
        _log(
            doc, SignatureEvent.Event.AMENDED,
            actor_user=request.user, ip=ip,
            detail="Document amended (PDF replaced)"
        )
        
        messages.success(request, f"Document amended successfully ({doc.page_count} page(s) detected). Existing signatures have been restamped.")
        return redirect("esign:document_detail", pk=doc.pk)

    messages.success(request, f"PDF replaced successfully ({doc.page_count} page(s) detected).")
    return redirect("esign:document_list")


@_staff_required
@require_POST
def document_force_complete(request, pk):
    from .services import force_complete_document
    doc = get_object_or_404(SignatureDocument, pk=pk)
    if doc.created_by != request.user:
        return HttpResponseForbidden()

    ip, _ = get_client_ip(request)
    try:
        force_complete_document(doc, actor_user=request.user, ip=ip)
        messages.success(request, f"Document '{doc.title}' has been forcefully completed.")
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("esign:document_detail", pk=doc.pk)

# ---------------------------------------------------------------------------
# Document configure (step 2) — add signers + place signature boxes
# ---------------------------------------------------------------------------

@_staff_required
def document_configure(request, pk):
    import json as _json

    doc = get_object_or_404(SignatureDocument, pk=pk)

    if doc.created_by != request.user:
        return HttpResponseForbidden("You do not have permission to configure this document.")

    if doc.status != SignatureDocument.Status.DRAFT:
        messages.info(request, "This document has already been sent and can no longer be configured.")
        return redirect("esign:document_detail", pk=doc.pk)

    flow_templates = SignatureFlowTemplate.objects.filter(is_active=True)
    available_users = User.objects.filter(
        is_active=True,
    ).exclude(role_access="PortalUser").order_by("first_name", "last_name")

    signers = list(doc.signers.order_by("order").select_related("user"))
    placements = list(doc.placements.order_by("page_number", "signer__order").select_related("signer"))

    # Build signer-ID → index map for placement pre-load
    signer_id_to_idx = {str(s.id): i for i, s in enumerate(signers)}

    initial_signers = [
        {
            "type": "user" if s.user_id else "external",
            "userId": str(s.user_id) if s.user_id else None,
            "name": s.display_name,
            "email": s.email,
            "order": s.order,
            "role": s.role,
        }
        for s in signers
    ]

    initial_placements = [
        {
            "signerIndex": signer_id_to_idx.get(str(p.signer_id), 0),
            "page": p.page_number,
            "x": p.x,
            "y": p.y,
            "w": p.width,
            "h": p.height,
        }
        for p in placements
    ]

    return render(request, "esign/document_configure.html", {
        "doc": doc,
        "signers": signers,
        "placements": placements,
        "flow_templates": flow_templates,
        "available_users": available_users,
        "page_range": range(1, doc.page_count + 1),
        "initial_signers_json": _json.dumps(initial_signers),
        "initial_placements_json": _json.dumps(initial_placements),
    })


# ---------------------------------------------------------------------------
# AJAX: apply a flow template (pre-fill signers)
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def apply_flow_template(request, pk):
    doc = get_object_or_404(SignatureDocument, pk=pk)
    if doc.created_by != request.user or doc.status != SignatureDocument.Status.DRAFT:
        return JsonResponse({"ok": False}, status=403)

    template_id = request.POST.get("template_id")
    template = get_object_or_404(SignatureFlowTemplate, pk=template_id, is_active=True)

    doc.signers.all().delete()
    doc.placements.all().delete()
    doc.routing_mode = template.routing_mode
    doc.save(update_fields=["routing_mode"])

    for step in template.steps.order_by("order").select_related("user"):
        Signer.objects.create(
            document=doc,
            user=step.user,
            order=step.order,
            role=step.role,
            created_by=request.user,
            updated_by=request.user,
        )

    return JsonResponse({"ok": True, "routing_mode": template.routing_mode})


# ---------------------------------------------------------------------------
# AJAX: save signers + placements (called before Send)
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def save_configuration(request, pk):
    """
    Accept JSON body with signers and placements, replacing all existing ones.

    Expected body:
    {
      "routing_mode": "sequential" | "parallel",
      "signers": [
        {"type": "user", "user_id": "<uuid>", "order": 1, "role": "signer"},
        {"type": "external", "name": "Budi", "email": "budi@x.com", "order": 2, "role": "approver"}
      ],
      "placements": [
        {
          "signer_index": 0,   // index into signers array
          "page": 1,
          "x": 0.1, "y": 0.2, "w": 0.2, "h": 0.08,
          "field_type": "signature"
        }
      ]
    }
    """
    doc = get_object_or_404(SignatureDocument, pk=pk)
    if doc.created_by != request.user or doc.status != SignatureDocument.Status.DRAFT:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    routing_mode      = data.get("routing_mode", "sequential")
    signer_data       = data.get("signers", [])
    placement_data    = data.get("placements", [])

    if not signer_data:
        return JsonResponse({"ok": False, "error": "At least one signer is required."}, status=400)

    doc.placements.all().delete()
    doc.signers.all().delete()
    doc.routing_mode = routing_mode
    doc.save(update_fields=["routing_mode"])

    created_signers = []
    for entry in signer_data:
        try:
            if entry.get("type") == "user":
                user = User.objects.get(pk=entry["user_id"])
                signer = Signer.objects.create(
                    document=doc,
                    user=user,
                    order=int(entry.get("order", 1)),
                    role=entry.get("role", Signer.Role.SIGNER),
                    created_by=request.user,
                    updated_by=request.user,
                )
            else:
                signer = Signer.objects.create(
                    document=doc,
                    external_name=entry.get("name", "").strip(),
                    external_email=entry.get("email", "").strip(),
                    order=int(entry.get("order", 1)),
                    role=entry.get("role", Signer.Role.SIGNER),
                    created_by=request.user,
                    updated_by=request.user,
                )
            created_signers.append(signer)
        except Exception as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    for p in placement_data:
        idx = int(p.get("signer_index", 0))
        if idx >= len(created_signers):
            continue
        signer = created_signers[idx]
        SignaturePlacement.objects.create(
            document=doc,
            signer=signer,
            field_type=p.get("field_type", SignaturePlacement.FieldType.SIGNATURE),
            page_number=int(p.get("page", 1)),
            x=float(p.get("x", 0)),
            y=float(p.get("y", 0)),
            width=float(p.get("w", 0.2)),
            height=float(p.get("h", 0.08)),
            created_by=request.user,
            updated_by=request.user,
        )

    return JsonResponse({"ok": True, "signer_count": len(created_signers)})


# ---------------------------------------------------------------------------
# Send document (step 3)
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def document_send(request, pk):
    doc = get_object_or_404(SignatureDocument, pk=pk)
    if doc.created_by != request.user:
        return HttpResponseForbidden()

    ip, _ = get_client_ip(request)
    try:
        send_document(doc, actor_user=request.user, ip=ip)
        messages.success(request, "Document sent to signers.")
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("esign:document_detail", pk=doc.pk)


# ---------------------------------------------------------------------------
# Document detail
# ---------------------------------------------------------------------------

@_staff_required
def document_detail(request, pk):
    doc = get_object_or_404(SignatureDocument, pk=pk)
    if doc.created_by != request.user:
        return HttpResponseForbidden()

    signers    = doc.signers.order_by("order").select_related("user")
    placements = doc.placements.order_by("page_number", "signer__order").select_related("signer")
    events     = doc.events.order_by("-created_at")[:50]

    return render(request, "esign/document_detail.html", {
        "doc": doc,
        "signers": signers,
        "placements": placements,
        "events": events,
    })


# ---------------------------------------------------------------------------
# Resend / remind
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def signer_remind(request, pk, signer_pk):
    doc    = get_object_or_404(SignatureDocument, pk=pk)
    signer = get_object_or_404(Signer, pk=signer_pk, document=doc)
    if doc.created_by != request.user:
        return HttpResponseForbidden()

    try:
        remind_signer(signer, actor_user=request.user)
        messages.success(request, f"Reminder sent to {signer.display_name}.")
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("esign:document_detail", pk=doc.pk)


@_staff_required
@require_POST
def signer_reopen(request, pk, signer_pk):
    from .services import reopen_signer
    doc    = get_object_or_404(SignatureDocument, pk=pk)
    signer = get_object_or_404(Signer, pk=signer_pk, document=doc)
    if doc.created_by != request.user:
        return HttpResponseForbidden()

    ip, _ = get_client_ip(request)
    try:
        reopen_signer(signer, actor_user=request.user, ip=ip)
        messages.success(request, f"Signer {signer.display_name} has been reopened.")
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("esign:document_detail", pk=doc.pk)

@_staff_required
@require_POST
def signer_reassign(request, pk, signer_pk):
    from .services import reassign_signer
    doc = get_object_or_404(SignatureDocument, pk=pk)
    signer = get_object_or_404(Signer, pk=signer_pk, document=doc)
    if doc.created_by != request.user:
        return HttpResponseForbidden()

    new_name = request.POST.get("name", "").strip()
    new_email = request.POST.get("email", "").strip()
    new_job_title = request.POST.get("job_title", "").strip()
    new_company = request.POST.get("company", "").strip()

    if not new_name or not new_email:
        messages.error(request, "Name and Email are required to reassign a signer.")
        return redirect("esign:document_detail", pk=doc.pk)

    ip, _ = get_client_ip(request)
    try:
        reassign_signer(
            signer, 
            new_name=new_name, 
            new_email=new_email, 
            new_job_title=new_job_title, 
            new_company=new_company, 
            actor_user=request.user, 
            ip=ip
        )
        messages.success(request, f"Signer reassigned to {new_name} ({new_email}).")
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("esign:document_detail", pk=doc.pk)

# ---------------------------------------------------------------------------
# Reopen
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def document_reopen(request, pk):
    doc = get_object_or_404(SignatureDocument, pk=pk)
    if doc.created_by != request.user:
        return HttpResponseForbidden()

    ip, _ = get_client_ip(request)
    try:
        reopen_document(doc, actor_user=request.user, ip=ip)
        messages.success(request, "Document reopened and resent to all signers.")
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("esign:document_detail", pk=doc.pk)


@_staff_required
@require_POST
def document_revert_to_draft(request, pk):
    doc = get_object_or_404(SignatureDocument, pk=pk)
    if doc.created_by != request.user:
        return HttpResponseForbidden()
    
    if doc.status == SignatureDocument.Status.PENDING and doc.signed_count == 0:
        doc.status = SignatureDocument.Status.DRAFT
        doc.save(update_fields=["status"])
        messages.success(request, "Document reverted to Draft. You can now edit its configuration.")
        return redirect("esign:document_configure", pk=doc.pk)
    
    messages.error(request, "Cannot edit configuration unless document is pending and has 0 signatures.")
    return redirect("esign:document_detail", pk=doc.pk)


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def document_cancel(request, pk):
    doc = get_object_or_404(SignatureDocument, pk=pk)
    if doc.created_by != request.user:
        return HttpResponseForbidden()

    ip, _ = get_client_ip(request)
    try:
        cancel_document(doc, actor_user=request.user, ip=ip)
        messages.success(request, "Document cancelled.")
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("esign:document_detail", pk=doc.pk)


# ---------------------------------------------------------------------------
# My Signatures (signed-in user sees their pending/completed signing tasks)
# ---------------------------------------------------------------------------

@login_required
def my_signatures(request):
    pending = (
        Signer.objects
        .filter(user=request.user, status=Signer.Status.PENDING)
        .select_related("document__created_by")
        .order_by("document__due_at", "created_at")
    )
    completed = (
        Signer.objects
        .filter(
            user=request.user,
            status__in=[Signer.Status.SIGNED, Signer.Status.REJECTED],
        )
        .select_related("document__created_by")
        .order_by("-acted_at")[:30]
    )
    return render(request, "esign/my_signatures.html", {
        "pending_signers": pending,
        "completed_signers": completed,
        "pending_count": pending.count(),
    })


# ---------------------------------------------------------------------------
# My Signature Detail — signer's personal view of one signing record
# ---------------------------------------------------------------------------

@login_required
def my_signature_detail(request, pk):
    signer = get_object_or_404(
        Signer.objects.select_related("document__created_by").prefetch_related("placements"),
        pk=pk,
        user=request.user,
    )
    document = signer.document
    return render(request, "esign/my_signature_detail.html", {
        "signer": signer,
        "document": document,
        "placements": signer.placements.order_by("page_number"),
    })


# ---------------------------------------------------------------------------
# AJAX: search system users for the signer picker
# ---------------------------------------------------------------------------

@_staff_required
def user_search(request):
    from django.db.models import Q

    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})

    users = (
        User.objects
        .filter(is_active=True)
        .exclude(role_access="PortalUser")
        .filter(
            Q(first_name__icontains=q) |
            Q(last_name__icontains=q) |
            Q(email__icontains=q) |
            Q(username__icontains=q)
        )
        .values("id", "first_name", "last_name", "email", "role_access")[:20]
    )
    results = [
        {
            "id": str(u["id"]),
            "name": f"{u['first_name']} {u['last_name']}".strip() or u["email"],
            "email": u["email"],
            "role": u["role_access"],
        }
        for u in users
    ]
    return JsonResponse({"results": results})


@_staff_required
def employee_search(request):
    from django.db.models import Q
    from core.models import Employee

    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})

    employees = (
        Employee.objects
        .filter(
            Q(full_name__icontains=q) |
            Q(email__icontains=q)
        )
        .values("id", "full_name", "email", "unit__code", "job_role")[:20]
    )
    results = [
        {
            "id": str(e["id"]),
            "name": e["full_name"],
            "email": e["email"] or "",
            "unit": e["unit__code"] or "",
            "job_role": e["job_role"] or "",
        }
        for e in employees
    ]
    return JsonResponse({"results": results})
