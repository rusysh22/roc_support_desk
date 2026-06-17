from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden

def is_manager_or_admin(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    return getattr(user, "role_access", "") in ["SuperAdmin", "Manager"]
from .models import SharedDocument
from .forms import SharedDocumentForm

@login_required
def shared_doc_list(request):
    docs = SharedDocument.objects.all().order_by("-updated_at")
    
    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "")
    
    if q:
        docs = docs.filter(title__icontains=q)
    if status_filter == "active":
        docs = docs.filter(is_active=True)
    elif status_filter == "inactive":
        docs = docs.filter(is_active=False)
        
    return render(request, "desk/shared_docs/shared_doc_list.html", {
        "docs": docs,
        "search_query": q,
        "status_filter": status_filter,
    })


@login_required
def shared_doc_create(request):
    if not is_manager_or_admin(request.user):
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
            return redirect("shared_docs_desk:list")
    else:
        form = SharedDocumentForm(initial={"is_active": True})
        
    return render(request, "desk/shared_docs/shared_doc_form.html", {
        "form": form,
        "is_edit": False
    })


@login_required
def shared_doc_edit(request, pk):
    if not is_manager_or_admin(request.user):
        messages.error(request, "You do not have permission to edit shared documents.")
        return redirect("shared_docs_desk:list")
        
    doc = get_object_or_404(SharedDocument, pk=pk)
    
    if request.method == "POST":
        form = SharedDocumentForm(request.POST, instance=doc)
        if form.is_valid():
            d = form.save(commit=False)
            d.updated_by = request.user
            d.save()
            messages.success(request, "Document updated.")
            return redirect("shared_docs_desk:list")
    else:
        form = SharedDocumentForm(instance=doc)
        
    return render(request, "desk/shared_docs/shared_doc_form.html", {
        "form": form,
        "is_edit": True,
        "doc_obj": doc
    })


@login_required
def shared_doc_delete(request, pk):
    if not is_manager_or_admin(request.user):
        return HttpResponseForbidden()
        
    if request.method == "POST":
        doc = get_object_or_404(SharedDocument, pk=pk)
        title = doc.title
        doc.delete()
        messages.success(request, f'Document "{title}" deleted.')
    return redirect("shared_docs_desk:list")


@login_required
def shared_doc_toggle_status(request, pk):
    if not is_manager_or_admin(request.user):
        return HttpResponseForbidden()
        
    if request.method == "POST":
        doc = get_object_or_404(SharedDocument, pk=pk)
        doc.is_active = not doc.is_active
        doc.save(update_fields=["is_active"])
        status = "activated" if doc.is_active else "deactivated"
        messages.success(request, f'Document "{doc.title}" {status}.')
    return redirect("shared_docs_desk:list")
