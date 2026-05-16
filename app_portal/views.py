from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from .models import ACCENT_CHOICES, AppPortalEntry


def _is_superadmin(request):
    return request.user.is_authenticated and getattr(request.user, "role_access", None) == "SuperAdmin"


def _safe_int(value, default=0):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


@login_required
def portal_view(request):
    user_role = getattr(request.user, "role_access", None) or ""
    is_superadmin = user_role == "SuperAdmin"

    qs = AppPortalEntry.objects.all().order_by("order", "name")
    if not is_superadmin:
        qs = qs.filter(is_active=True)

    # SuperAdmin sees everything; others are filtered by role access
    entries = list(qs) if is_superadmin else [e for e in qs if e.is_accessible_by(user_role)]
    return render(request, "app_portal/portal.html", {"entries": entries})


@login_required
@require_POST
def entry_create(request):
    if not _is_superadmin(request):
        return JsonResponse({"success": False, "error": "Permission denied."}, status=403)

    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"success": False, "error": "Application name is required."})

    url = request.POST.get("url", "").strip()
    if not url:
        return JsonResponse({"success": False, "error": "App URL is required."})

    valid_colors = {c[0] for c in ACCENT_CHOICES}
    accent_color = request.POST.get("accent_color", "indigo")
    if accent_color not in valid_colors:
        accent_color = "indigo"

    entry = AppPortalEntry(
        name=name,
        description=request.POST.get("description", "").strip(),
        url=url,
        icon_emoji=request.POST.get("icon_emoji", "🌐").strip() or "🌐",
        badge_text=request.POST.get("badge_text", "").strip(),
        accent_color=accent_color,
        accessible_roles=request.POST.get("accessible_roles", "all") or "all",
        order=_safe_int(request.POST.get("order", 0)),
        is_active=bool(request.POST.get("is_active")),
    )
    if request.FILES.get("icon_image"):
        entry.icon_image = request.FILES["icon_image"]
    entry.save()
    return JsonResponse({"success": True})


@login_required
@require_POST
def entry_update(request, pk):
    if not _is_superadmin(request):
        return JsonResponse({"success": False, "error": "Permission denied."}, status=403)

    entry = get_object_or_404(AppPortalEntry, pk=pk)

    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"success": False, "error": "Application name is required."})

    url = request.POST.get("url", "").strip()
    if not url:
        return JsonResponse({"success": False, "error": "App URL is required."})

    valid_colors = {c[0] for c in ACCENT_CHOICES}
    accent_color = request.POST.get("accent_color", "indigo")
    if accent_color not in valid_colors:
        accent_color = "indigo"

    entry.name = name
    entry.description = request.POST.get("description", "").strip()
    entry.url = url
    entry.icon_emoji = request.POST.get("icon_emoji", "🌐").strip() or "🌐"
    entry.badge_text = request.POST.get("badge_text", "").strip()
    entry.accent_color = accent_color
    entry.accessible_roles = request.POST.get("accessible_roles", "all") or "all"
    entry.order = _safe_int(request.POST.get("order", 0))
    entry.is_active = bool(request.POST.get("is_active"))
    if request.FILES.get("icon_image"):
        entry.icon_image = request.FILES["icon_image"]
    entry.save()
    return JsonResponse({"success": True})


@login_required
@require_POST
def entry_delete(request, pk):
    if not _is_superadmin(request):
        return JsonResponse({"success": False, "error": "Permission denied."}, status=403)

    entry = get_object_or_404(AppPortalEntry, pk=pk)
    entry.delete()
    return JsonResponse({"success": True})
