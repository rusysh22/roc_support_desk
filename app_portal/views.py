from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .models import AppPortalEntry


@login_required
def portal_view(request):
    all_entries = AppPortalEntry.objects.filter(is_active=True).order_by("order", "name")
    user_role = getattr(request.user, "role_access", None) or ""
    entries = [e for e in all_entries if e.is_accessible_by(user_role)]
    return render(request, "app_portal/portal.html", {"entries": entries})
