from django.shortcuts import render, get_object_or_404
from django.http import Http404

from .models import SharedDocument

def public_doc_view(request, pk):
    """
    Render a SharedDocument by its UUID public key.
    Returns 404 if the document is inactive (soft-revoked).
    """
    doc = get_object_or_404(SharedDocument, pk=pk)
    if not doc.is_active:
        raise Http404("This document is no longer available.")
    return render(request, "shared_docs/public/doc.html", {"doc": doc})
