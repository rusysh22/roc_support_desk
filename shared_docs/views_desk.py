from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden

from core.list_actions import get_selected, bulk_set_archived, bulk_delete
from core.list_pagination import paginate
from core.excel_utils import export_queryset_xlsx, safe_cell

def is_manager_or_admin(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    return getattr(user, "role_access", "") in ["SuperAdmin", "Manager"]


def _can_manage_shared_doc(user, doc=None):
    """
    Creating a new document (doc=None): any Manager/SuperAdmin, or a
    PortalUser holding the 'shared_docs' module grant.

    Managing an EXISTING document (edit/delete/toggle): creator only, for
    everyone including Manager/SuperAdmin — documents are personal, not a
    shared pool, so role alone no longer grants access to someone else's doc.
    """
    if doc is None:
        if is_manager_or_admin(user):
            return True
        return getattr(user, "role_access", None) == "PortalUser" and "shared_docs" in user.get_accessible_modules()
    return doc.created_by == user


from .models import SharedDocument
from .forms import SharedDocumentForm

from licensing.decorators import feature_required

STATUS_META = {
    "Active": {"label": "Active", "badge_class": "jk-badge-resolved"},
    "Revoked": {"label": "Revoked", "badge_class": "jk-badge-cancelled"},
}


SHARED_DOC_FILTERABLE_FIELDS = {
    "is_active": {
        "label": "Status", "type": "choice", "lookup": "is_active",
        "choices": [("true", "Active"), ("false", "Revoked")],
        "python_type": "bool",
    },
}


@login_required
@feature_required('shared_docs')
def shared_doc_list(request):
    from core.dynamic_filters import apply_dynamic_filters, active_filters_json, fields_json

    # Personal list — each user only sees documents they created themselves.
    docs = SharedDocument.objects.filter(created_by=request.user).order_by("-updated_at")

    q = request.GET.get("q", "").strip()

    if q:
        docs = docs.filter(title__icontains=q)

    docs, active_filters = apply_dynamic_filters(docs, request, SHARED_DOC_FILTERABLE_FIELDS)

    # Default view hides revoked (is_active=False) documents unless the
    # user explicitly filtered on Status themselves — mirrors the
    # standardized "hide archived by default" list rule.
    if not any(af["field_key"] == "is_active" for af in active_filters):
        docs = docs.filter(is_active=True)

    viewing_archived_only = any(
        af["field_key"] == "is_active" and af["value"] == "false" for af in active_filters
    )

    context = {
        "search_query": q,
        "active_filters": active_filters,
        "active_filters_json": active_filters_json(active_filters),
        "dynamic_fields_json": fields_json(SHARED_DOC_FILTERABLE_FIELDS),
        "has_active_filters": bool(active_filters),
        "viewing_archived_only": viewing_archived_only,
        "STATUS_META": STATUS_META,
    }
    context.update(paginate(request, docs))
    context["docs"] = context["object_list"]

    return render(request, "desk/shared_docs/shared_doc_list.html", context)


@login_required
def shared_doc_create(request):
    if not _can_manage_shared_doc(request.user):
        messages.error(request, "You do not have permission to create shared documents.")
        return redirect("shared_docs_desk:list")
        
    if request.method == "POST":
        form = SharedDocumentForm(request.POST)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.created_by = request.user
            doc.updated_by = request.user
            doc.save()
            messages.success(request, "Document created.")
            return redirect("shared_docs_desk:edit", pk=doc.pk)
    else:
        form = SharedDocumentForm(initial={"is_active": True})
        
    return render(request, "desk/shared_docs/shared_doc_form.html", {
        "form": form,
        "is_edit": False
    })


@login_required
def shared_doc_edit(request, pk):
    doc = get_object_or_404(SharedDocument, pk=pk)

    if not _can_manage_shared_doc(request.user, doc):
        messages.error(request, "You do not have permission to edit shared documents.")
        return redirect("shared_docs_desk:list")

    if request.method == "POST":
        form = SharedDocumentForm(request.POST, instance=doc)
        if form.is_valid():
            d = form.save(commit=False)
            d.updated_by = request.user
            d.save()
            messages.success(request, "Document updated.")
            return redirect("shared_docs_desk:edit", pk=d.pk)
    else:
        form = SharedDocumentForm(instance=doc)
        
    return render(request, "desk/shared_docs/shared_doc_form.html", {
        "form": form,
        "is_edit": True,
        "doc_obj": doc
    })


@login_required
def shared_doc_delete(request, pk):
    doc = get_object_or_404(SharedDocument, pk=pk)
    if not _can_manage_shared_doc(request.user, doc):
        return HttpResponseForbidden()

    if request.method == "POST":
        title = doc.title
        doc.delete()
        messages.success(request, f'Document "{title}" deleted.')
    return redirect("shared_docs_desk:list")


@login_required
def shared_doc_toggle_status(request, pk):
    doc = get_object_or_404(SharedDocument, pk=pk)
    if not _can_manage_shared_doc(request.user, doc):
        return HttpResponseForbidden()

    if request.method == "POST":
        doc.is_active = not doc.is_active
        doc.save(update_fields=["is_active"])
        status = "activated" if doc.is_active else "deactivated"
        messages.success(request, f'Document "{doc.title}" {status}.')
    return redirect("shared_docs_desk:list")


def _owned_queryset(user):
    """Documents are personal — bulk actions may only ever touch rows the
    current user created themselves (see _can_manage_shared_doc)."""
    return SharedDocument.objects.filter(created_by=user)


@login_required
def shared_doc_bulk_archive(request):
    if request.method == "POST":
        selected = get_selected(request, _owned_queryset(request.user))
        count = bulk_set_archived(selected, field_name="is_active", value=False)
        if count:
            messages.success(request, f"{count} document(s) revoked.")
    return redirect("shared_docs_desk:list")


@login_required
def shared_doc_bulk_unarchive(request):
    if request.method == "POST":
        selected = get_selected(request, _owned_queryset(request.user))
        count = bulk_set_archived(selected, field_name="is_active", value=True)
        if count:
            messages.success(request, f"{count} document(s) re-activated.")
    return redirect("shared_docs_desk:list")


@login_required
def shared_doc_bulk_delete(request):
    if request.method == "POST":
        selected = get_selected(request, _owned_queryset(request.user))
        count = bulk_delete(selected)
        if count:
            messages.success(request, f"{count} document(s) permanently deleted.")
    return redirect("shared_docs_desk:list")


@login_required
def shared_doc_bulk_duplicate(request):
    """Meaningful for exactly one selected row: clone the document's content
    into a brand-new record. Because SharedDocument's pk (a UUID) doubles as
    the public share-link token, saving with a fresh pk automatically issues
    a new, non-colliding share link — no separate token field to regenerate."""
    if request.method == "POST":
        selected = list(get_selected(request, _owned_queryset(request.user))[:1])
        if selected:
            original = selected[0]
            SharedDocument.objects.create(
                title=f"{original.title} (Copy)",
                editor_mode=original.editor_mode,
                content=original.content,
                is_active=True,
                created_by=request.user,
                updated_by=request.user,
            )
            messages.success(request, f'Document "{original.title}" duplicated.')
    return redirect("shared_docs_desk:list")


@login_required
def shared_doc_bulk_export(request):
    if request.method == "POST":
        selected = get_selected(request, _owned_queryset(request.user)).order_by("-updated_at")
        columns = [
            ("Document Title", lambda d: safe_cell(d.title)),
            ("Share Link Token", lambda d: str(d.pk)),
            ("Status", lambda d: "Active" if d.is_active else "Revoked"),
            ("Created Date", lambda d: d.created_at.strftime("%Y-%m-%d %H:%M") if d.created_at else ""),
            ("Last Updated", lambda d: d.updated_at.strftime("%Y-%m-%d %H:%M") if d.updated_at else ""),
        ]
        return export_queryset_xlsx(selected, columns, "shared_documents.xlsx", sheet_title="Shared Documents")
    return redirect("shared_docs_desk:list")
