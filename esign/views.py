"""
E-Signature App — Staff Views
================================
All views here require login and are role-gated (no PortalUser access).
The public magic-link signing view lives in views_public.py.
"""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import models
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from ipware import get_client_ip

from core.excel_utils import export_queryset_xlsx, safe_cell
from core.list_actions import bulk_delete, bulk_set_archived, get_selected
from core.list_pagination import paginate
from core.models import SiteConfig, User
from .forms import DocumentUploadForm
from .models import Signer, SignatureDocument, SignaturePlacement, SignatureFlowTemplate
from .services import (
    _request_signature,
    cancel_document,
    compact_orders,
    next_remind_at,
    remind_signer,
    reopen_document,
    send_document,
)


# ---------------------------------------------------------------------------
# Status pill meta map for the standardized list view (core/templatetags/
# custom_tags.py {% status_pill %}). Badge classes reuse the existing
# jk-badge-* palette already defined in static/css/jokoui.css.
# ---------------------------------------------------------------------------
STATUS_META = {
    SignatureDocument.Status.DRAFT: {"label": "Draft", "badge_class": "jk-badge-cancelled"},
    SignatureDocument.Status.PENDING: {"label": "Pending Signatures", "badge_class": "jk-badge-pending"},
    SignatureDocument.Status.COMPLETED: {"label": "Completed", "badge_class": "jk-badge-resolved"},
    SignatureDocument.Status.REJECTED: {"label": "Rejected", "badge_class": "jk-badge-rejected"},
    SignatureDocument.Status.CANCELLED: {"label": "Cancelled", "badge_class": "jk-badge-cancelled"},
}


# ---------------------------------------------------------------------------
# Role guard helpers
# ---------------------------------------------------------------------------

def _is_staff(user):
    """Staff roles always allowed; PortalUser allowed only with the 'esign' module grant."""
    if not user.is_authenticated:
        return False
    if user.role_access != "PortalUser":
        return True
    return "esign" in user.get_accessible_modules()


def _can_view(doc, user):
    """The owner, or a contributor the owner added (read-only)."""
    return doc.created_by_id == user.pk or doc.contributors.filter(pk=user.pk).exists()


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

from licensing.decorators import feature_required

@_staff_required
@feature_required('esign')
def document_list(request):
    show_archived = request.GET.get("show_archived") == "1"
    documents = (
        SignatureDocument.objects
        .filter(models.Q(created_by=request.user) | models.Q(contributors=request.user))
        .distinct()
        .prefetch_related("signers")
        .order_by("-created_at")
    )
    if not show_archived:
        documents = documents.filter(is_archived=False)

    search_query = request.GET.get("q", "").strip()
    if search_query:
        documents = documents.filter(
            models.Q(title__icontains=search_query)
            | models.Q(document_code__icontains=search_query)
        )

    status_filter = request.GET.get("status", "").strip()
    if status_filter:
        documents = documents.filter(status=status_filter)

    context = {
        "documents": documents,
        "search_query": search_query,
        "status_filter": status_filter,
        "show_archived": show_archived,
        "has_active_filters": bool(status_filter or show_archived),
        "STATUS_META": STATUS_META,
        "status_choices": SignatureDocument.Status.choices,
    }
    context.update(paginate(request, documents))
    context["documents"] = context["object_list"]
    return render(request, "esign/document_list.html", context)


def _purge_document_files(doc):
    for signer in doc.signers.all():
        if signer.signature_image:
            signer.signature_image.delete(save=False)
    for field_name in ("original_pdf", "signed_pdf", "preview_pdf"):
        f = getattr(doc, field_name)
        if f:
            f.delete(save=False)


@_staff_required
@feature_required('esign')
@require_POST
def document_delete(request, pk):
    doc = get_object_or_404(SignatureDocument, pk=pk)
    if doc.created_by != request.user:
        return HttpResponseForbidden()
    if doc.status != SignatureDocument.Status.DRAFT:
        messages.error(request, "Only draft documents can be deleted.")
        return redirect("esign:document_list")
    title = doc.title
    _purge_document_files(doc)
    doc.delete()
    messages.success(request, f'Document "{title}" deleted.')
    return redirect("esign:document_list")


@_staff_required
@feature_required('esign')
@require_POST
def document_bulk_delete(request):
    # Only DRAFT documents may be hard-deleted. Non-draft rows among the
    # selection are silently skipped (with a message) rather than rejecting
    # the whole batch — this mirrors the row-click-to-open / bulk-bar UX
    # where a mixed selection is expected and the ineligible rows just
    # don't get bulk_delete()'d.
    own_selected = get_selected(
        request,
        SignatureDocument.objects.filter(created_by=request.user),
    )
    selected_ids = request.POST.getlist("selected_ids")
    if not selected_ids:
        messages.warning(request, "No documents selected for deletion.")
        return redirect("esign:document_list")

    draft_qs = own_selected.filter(status=SignatureDocument.Status.DRAFT).prefetch_related("signers")
    skipped = own_selected.exclude(status=SignatureDocument.Status.DRAFT).count()
    for doc in draft_qs:
        _purge_document_files(doc)
    count = bulk_delete(draft_qs)

    if count:
        messages.success(request, f"Deleted {count} draft document(s).")
    if skipped:
        messages.warning(request, f"Skipped {skipped} document(s) that are not drafts.")
    if not count and not skipped:
        messages.warning(request, "No draft documents were deleted.")
    return redirect("esign:document_list")


@_staff_required
@feature_required('esign')
@require_POST
def document_bulk_archive(request):
    qs = get_selected(
        request,
        SignatureDocument.objects.filter(created_by=request.user),
    )
    count = bulk_set_archived(qs, value=True)
    if count:
        messages.success(request, f"Archived {count} document(s).")
    else:
        messages.warning(request, "No documents selected to archive.")
    return redirect("esign:document_list")


@_staff_required
@feature_required('esign')
@require_POST
def document_bulk_unarchive(request):
    qs = get_selected(
        request,
        SignatureDocument.objects.filter(created_by=request.user),
    )
    count = bulk_set_archived(qs, value=False)
    if count:
        messages.success(request, f"Unarchived {count} document(s).")
    else:
        messages.warning(request, "No documents selected to unarchive.")
    return redirect("esign:document_list")


@_staff_required
@feature_required('esign')
@require_POST
def document_bulk_duplicate(request):
    # The bulk bar only enables "Duplicate" when exactly one row is checked
    # (see canDuplicate in static/js/list-table.js), but selected_ids is a
    # generic multi-value field, so guard here too: if more than one id is
    # somehow submitted, duplicate only the first and ignore the rest rather
    # than silently duplicating N documents from one click.
    selected_ids = request.POST.getlist("selected_ids")
    if not selected_ids:
        messages.warning(request, "No document selected to duplicate.")
        return redirect("esign:document_list")

    original_doc = get_object_or_404(
        SignatureDocument, pk=selected_ids[0], created_by=request.user
    )

    new_doc = SignatureDocument(
        title=f"Copy of {original_doc.title}",
        message=original_doc.message,
        routing_mode=original_doc.routing_mode,
        due_at=original_doc.due_at,
        created_by=request.user,
        updated_by=request.user,
    )
    if original_doc.original_pdf:
        original_doc.original_pdf.open("rb")
        try:
            new_doc.original_pdf.save(
                original_doc.original_pdf.name.rsplit("/", 1)[-1],
                ContentFile(original_doc.original_pdf.read()),
                save=False,
            )
        finally:
            original_doc.original_pdf.close()
    new_doc.page_count = original_doc.page_count
    new_doc.save()

    try:
        new_doc.compute_hash()
        new_doc.save(update_fields=["document_hash"])
    except Exception:
        pass

    for orig_signer in original_doc.signers.all():
        new_signer = Signer.objects.create(
            document=new_doc,
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
                document=new_doc,
                signer=new_signer,
                field_type=orig_placement.field_type,
                page_number=orig_placement.page_number,
                x=orig_placement.x,
                y=orig_placement.y,
                width=orig_placement.width,
                height=orig_placement.height,
                required=orig_placement.required,
            )

    if len(selected_ids) > 1:
        messages.warning(request, "Only the first selected document was duplicated.")
    messages.success(request, f'Document duplicated as "{new_doc.title}".')
    return redirect("esign:document_configure", pk=new_doc.pk)


@_staff_required
@feature_required('esign')
@require_POST
def document_bulk_export(request):
    qs = get_selected(
        request,
        SignatureDocument.objects.filter(created_by=request.user).prefetch_related("signers"),
    )
    columns = [
        ("Document Code", lambda d: safe_cell(d.document_code)),
        ("Title", lambda d: safe_cell(d.title)),
        ("Status", lambda d: d.get_status_display()),
        ("Routing Mode", lambda d: d.get_routing_mode_display()),
        ("Created At", lambda d: d.created_at.strftime("%Y-%m-%d %H:%M") if d.created_at else ""),
        ("Due At", lambda d: d.due_at.strftime("%Y-%m-%d %H:%M") if d.due_at else ""),
        ("Signed / Total", lambda d: f"{d.signed_count}/{d.total_count}"),
        ("Archived", lambda d: "Yes" if d.is_archived else "No"),
    ]
    return export_queryset_xlsx(qs, columns, "esign_documents.xlsx", sheet_title="Documents")


# ---------------------------------------------------------------------------
# Document upload (step 1)
# ---------------------------------------------------------------------------

@_staff_required
def document_create(request):
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            # Terms & Conditions must be accepted (and still within its 1-year
            # validity window) before a document can be uploaded for signing.
            if not request.user.esign_tnc_is_valid:
                if request.POST.get("accept_tnc") != "1":
                    messages.error(request, "You must accept the E-Sign Terms & Conditions to continue.")
                    return render(request, "esign/document_create.html", {
                        "form": form,
                        "tnc_required": True,
                    })
                from django.utils import timezone
                request.user.esign_tnc_accepted_at = timezone.now()
                request.user.save(update_fields=["esign_tnc_accepted_at"])

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

    return render(request, "esign/document_create.html", {
        "form": form,
        "tnc_required": not request.user.esign_tnc_is_valid,
        "max_upload_mb": getattr(SiteConfig.get_solo(), "max_upload_size_mb", 10) or 10,
    })

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
    if doc.status not in [SignatureDocument.Status.DRAFT, SignatureDocument.Status.PENDING, SignatureDocument.Status.COMPLETED]:
        messages.error(request, "The PDF can only be replaced while the document is a draft, pending, or completed.")
        if doc.status in [SignatureDocument.Status.PENDING, SignatureDocument.Status.COMPLETED]:
            return redirect("esign:document_detail", pk=pk)
        return redirect("esign:document_configure", pk=pk)

    new_pdf = request.FILES.get("original_pdf")
    def redirect_back():
        if doc.status in [SignatureDocument.Status.PENDING, SignatureDocument.Status.COMPLETED]:
            return redirect("esign:document_detail", pk=pk)
        return redirect("esign:document_configure", pk=pk)

    if not new_pdf:
        messages.error(request, "No file selected.")
        return redirect_back()

    from core.models import SiteConfig
    from core.upload_security import validate_upload

    max_mb = SiteConfig.get_solo().max_upload_size_mb
    is_valid, error, _mime = validate_upload(new_pdf, max_mb, {"application/pdf"})
    if not is_valid:
        messages.error(request, error)
        return redirect_back()

    # Delete old file and save new one
    if doc.original_pdf:
        doc.original_pdf.delete(save=False)

    # Random filename (also breaks browser cache on the replaced file)
    import uuid
    new_pdf.name = f"{uuid.uuid4().hex}.pdf"
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

    if doc.status in [SignatureDocument.Status.PENDING, SignatureDocument.Status.COMPLETED]:
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
        
        # Clear signed_pdf if it exists, since the layout changed
        if doc.signed_pdf:
            doc.signed_pdf.delete(save=False)
            doc.save(update_fields=["signed_pdf"])
        
        messages.success(request, f"PDF replaced successfully ({doc.page_count} page(s) detected). Please verify and adjust assignments if the layout has changed.")
        return redirect("esign:document_adjust_placements", pk=doc.pk)

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
    ).exclude(email="").order_by("first_name", "last_name", "email")

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

    me = request.user
    current_user = {
        "id": str(me.pk), "name": me.get_full_name() or me.username, "email": me.email,
    } if me.email else None
    contributors = [
        {"id": str(u.pk), "name": u.get_full_name() or u.username, "email": u.email}
        for u in doc.contributors.order_by("first_name", "last_name")
    ]

    return render(request, "esign/document_configure.html", {
        "doc": doc,
        "signers": signers,
        "placements": placements,
        "current_user": current_user,
        "initial_contributors": contributors,
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
        {"type": "external", "name": "John", "email": "john@example.com", "order": 2, "role": "approver"}
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

    from core.models import SiteConfig
    if not SiteConfig.get_solo().esign_allow_external_signers:
        if any(entry.get("type") != "user" for entry in signer_data):
            return JsonResponse({
                "ok": False,
                "error": "External Email signers are disabled by the Super Admin. Only Internal (System) Users are allowed.",
            }, status=403)

    # Sign orders always run 1, 2, 3... - gaps left by removed signers are closed (ties kept).
    for entry, order in zip(signer_data, compact_orders([_order_of(e) for e in signer_data])):
        entry["order"] = order

    doc.placements.all().delete()
    doc.signers.all().delete()
    doc.routing_mode = routing_mode
    doc.save(update_fields=["routing_mode"])
    if "contributors" in data:
        _set_contributors(doc, data.get("contributors") or [])

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


def _order_of(entry):
    try:
        return max(1, int(entry.get("order", 1)))
    except (TypeError, ValueError):
        return 1


def _set_contributors(doc, user_ids):
    """Contributors are active system users with an email; signers can be contributors too."""
    users = User.objects.filter(pk__in=[u for u in user_ids if u], is_active=True).exclude(email="")
    doc.contributors.set(users.exclude(pk=doc.created_by_id))


# ---------------------------------------------------------------------------
# Adjust placements (PENDING only)
# ---------------------------------------------------------------------------

@_staff_required
def document_adjust_placements(request, pk):
    import json as _json

    doc = get_object_or_404(SignatureDocument, pk=pk)

    if doc.created_by != request.user:
        return HttpResponseForbidden("You do not have permission to configure this document.")

    if doc.status not in [SignatureDocument.Status.PENDING, SignatureDocument.Status.COMPLETED]:
        messages.error(request, "Assignments can only be manually adjusted while the document is pending or completed.")
        return redirect("esign:document_detail", pk=doc.pk)

    signers = list(doc.signers.order_by("order").select_related("user"))
    placements = list(doc.placements.order_by("page_number", "signer__order").select_related("signer"))

    # Build signer-ID → index map for placement pre-load
    signer_id_to_idx = {str(s.id): i for i, s in enumerate(signers)}

    initial_signers = [
        {
            "id": str(s.id),
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

    me = request.user
    return render(request, "esign/document_adjust_placements.html", {
        "doc": doc,
        "current_user": {
            "id": str(me.pk), "name": me.get_full_name() or me.username, "email": me.email,
        } if me.email else None,
        "signers": signers,
        "placements": placements,
        "page_range": range(1, doc.page_count + 1),
        "initial_signers_json": _json.dumps(initial_signers),
        "initial_placements_json": _json.dumps(initial_placements),
    })


@_staff_required
@require_POST
def save_placements(request, pk):
    """
    Accept JSON body with placements and signers, replacing existing ones for PENDING/COMPLETED document.
    Re-generates preview PDF (or final PDF) after saving.
    """
    doc = get_object_or_404(SignatureDocument, pk=pk)
    if doc.created_by != request.user or doc.status not in [SignatureDocument.Status.PENDING, SignatureDocument.Status.COMPLETED]:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    placement_data = data.get("placements", [])
    signer_data = data.get("signers", [])

    from core.models import SiteConfig
    if not SiteConfig.get_solo().esign_allow_external_signers:
        if any(not s.get("id") and s.get("type") != "user" for s in signer_data):
            return JsonResponse({
                "ok": False,
                "error": "External Email signers are disabled by the Super Admin. Only Internal (System) Users are allowed.",
            }, status=403)

    for entry, order in zip(signer_data, compact_orders([_order_of(e) for e in signer_data])):
        entry["order"] = order

    frontend_signer_ids = [s.get("id") for s in signer_data if s.get("id")]
    
    # Delete signers that were removed
    doc.signers.exclude(id__in=frontend_signer_ids).delete()

    created_or_updated_signers = []
    for s_data in signer_data:
        s_id = s_data.get("id")
        if s_id:
            # Update existing
            signer = Signer.objects.get(id=s_id, document=doc)
            signer.order = int(s_data.get("order", 1))
            signer.role = s_data.get("role", Signer.Role.SIGNER)
            signer.save(update_fields=["order", "role"])
        else:
            # Create new
            if s_data.get("type") == "user":
                user = User.objects.get(pk=s_data["userId"])
                signer = Signer.objects.create(
                    document=doc,
                    user=user,
                    order=int(s_data.get("order", 1)),
                    role=s_data.get("role", Signer.Role.SIGNER),
                    status=Signer.Status.WAITING,
                    created_by=request.user,
                    updated_by=request.user,
                )
            else:
                signer = Signer.objects.create(
                    document=doc,
                    external_name=s_data.get("name", "").strip(),
                    external_email=s_data.get("email", "").strip(),
                    order=int(s_data.get("order", 1)),
                    role=s_data.get("role", Signer.Role.SIGNER),
                    status=Signer.Status.WAITING,
                    created_by=request.user,
                    updated_by=request.user,
                )
        created_or_updated_signers.append(signer)

    # Replace all placements
    doc.placements.all().delete()
    for p in placement_data:
        idx = int(p.get("signer_index", 0))
        if idx >= len(created_or_updated_signers):
            continue
        signer = created_or_updated_signers[idx]
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

    # Handle state transitions for new signers
    from .services import _assign_token

    # Assign tokens to new signers (those without an expiration date)
    for signer in doc.signers.filter(token_expires_at__isnull=True):
        _assign_token(signer)

    if doc.routing_mode == SignatureDocument.RoutingMode.PARALLEL:
        # In parallel, all WAITING become PENDING
        for signer in doc.signers.filter(status=Signer.Status.WAITING):
            signer.status = Signer.Status.PENDING
            signer.save(update_fields=["status"])
            _request_signature(signer)
    else:
        # Sequential: Check if there's any PENDING signer. If not, advance to the first WAITING.
        if not doc.signers.filter(status=Signer.Status.PENDING).exists():
            next_signer = doc.signers.filter(status=Signer.Status.WAITING).order_by("order").first()
            if next_signer:
                next_signer.status = Signer.Status.PENDING
                next_signer.save(update_fields=["status"])
                _request_signature(next_signer)

    if doc.status == SignatureDocument.Status.COMPLETED and not doc.is_complete:
        doc.status = SignatureDocument.Status.PENDING
        doc.save(update_fields=["status"])

    # Re-stamp the preview or final PDF
    from .services import _update_preview_pdf, _finalize, _log
    from .models import SignatureEvent
    
    if doc.is_complete:
        _finalize(doc)
    else:
        _update_preview_pdf(doc)

    ip, _ = get_client_ip(request)
    _log(
        doc, SignatureEvent.Event.AMENDED,
        actor_user=request.user, ip=ip,
        detail="Signers and placements manually adjusted."
    )

    return JsonResponse({"ok": True})


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
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("esign:document_detail", pk=doc.pk)

    # Sender is (one of) the first signers: go straight to the signing page.
    mine = doc.signers.filter(user=request.user, status=Signer.Status.PENDING).first()
    if mine:
        others = doc.signers.exclude(pk=mine.pk).exists()
        messages.success(
            request, "Document sent. It's your turn - please sign below." if others else "Ready - please sign below.",
        )
        return redirect("esign_public:sign", token=mine.token)
    messages.success(request, "Document sent to signers.")
    return redirect("esign:document_detail", pk=doc.pk)


# ---------------------------------------------------------------------------
# Document detail
# ---------------------------------------------------------------------------

@_staff_required
def document_detail(request, pk):
    doc = get_object_or_404(SignatureDocument, pk=pk)
    if not _can_view(doc, request.user):
        return HttpResponseForbidden()

    signers    = list(doc.signers.order_by("order").select_related("user"))
    placements = doc.placements.order_by("page_number", "signer__order").select_related("signer")
    events     = doc.events.order_by("-created_at")[:50]
    for signer in signers:
        signer.next_remind_at = next_remind_at(signer)

    return render(request, "esign/document_detail.html", {
        "doc": doc,
        "signers": signers,
        "placements": placements,
        "events": events,
        "is_owner": doc.created_by_id == request.user.pk,
        "my_pending_signer": next(
            (s for s in signers if s.user_id == request.user.pk and s.status == Signer.Status.PENDING), None,
        ),
        "contributors": doc.contributors.order_by("first_name", "last_name"),
    })


@_staff_required
@require_POST
def document_contributors(request, pk):
    """Owner adds or removes a contributor from the detail page."""
    doc = get_object_or_404(SignatureDocument, pk=pk)
    if doc.created_by != request.user:
        return HttpResponseForbidden()
    user = User.objects.filter(pk=request.POST.get("user_id"), is_active=True).exclude(email="").first()
    if user is None or user.pk == doc.created_by_id:
        messages.error(request, "Select an active system user other than yourself.")
    elif request.POST.get("action") == "remove":
        doc.contributors.remove(user)
        messages.success(request, f"{user.get_full_name() or user.username} is no longer a contributor.")
    else:
        doc.contributors.add(user)
        messages.success(request, f"{user.get_full_name() or user.username} added as a contributor.")
    return redirect("esign:document_detail", pk=doc.pk)


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


@require_POST
@_staff_required
def document_regenerate_progress_link(request, pk):
    """Rotate the public progress-page token, revoking any previously shared link."""
    import uuid
    doc = get_object_or_404(SignatureDocument, pk=pk)
    if doc.created_by != request.user:
        return HttpResponseForbidden()

    doc.progress_token = uuid.uuid4()
    doc.save(update_fields=["progress_token"])
    messages.success(request, "Progress link regenerated — the old link no longer works.")
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

    signer_type = request.POST.get("signer_type", "external").strip()
    new_name = request.POST.get("name", "").strip()
    new_email = request.POST.get("email", "").strip()
    new_job_title = request.POST.get("job_title", "").strip()
    new_company = request.POST.get("company", "").strip()
    new_user = None

    from core.models import SiteConfig
    if signer_type != "user" and not SiteConfig.get_solo().esign_allow_external_signers:
        messages.error(request, "External Email signers are disabled by the Super Admin. Only Internal (System) Users are allowed.")
        return redirect("esign:document_detail", pk=doc.pk)

    if signer_type == "user":
        try:
            new_user = User.objects.filter(
                pk=request.POST.get("user_id"),
                is_active=True,
            ).exclude(email="").first()
        except (TypeError, ValueError):
            new_user = None
        if not new_user:
            messages.error(request, "Please select an active system user.")
            return redirect("esign:document_detail", pk=doc.pk)
        new_name = new_user.get_full_name() or new_user.username
        new_email = new_user.email
        new_job_title = ""
        new_company = ""
    elif signer_type != "external":
        messages.error(request, "Invalid signer type.")
        return redirect("esign:document_detail", pk=doc.pk)
    elif not new_name or not new_email:
        messages.error(request, "Name and Email are required for an external signer.")
        return redirect("esign:document_detail", pk=doc.pk)

    ip, _ = get_client_ip(request)
    try:
        reassign_signer(
            signer, 
            new_name=new_name, 
            new_email=new_email, 
            new_job_title=new_job_title, 
            new_company=new_company, 
            new_user=new_user,
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
        .exclude(email="")
        .filter(
            Q(first_name__icontains=q) |
            Q(last_name__icontains=q) |
            Q(email__icontains=q) |
            Q(username__icontains=q)
        )
        .order_by("first_name", "last_name", "email")
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
