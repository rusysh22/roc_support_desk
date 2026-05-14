"""Manual app views."""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models import SiteConfig
from .models import Manual, ManualLandingConfig, ManualPage

EDITOR_ROLES = {"SuperAdmin", "Manager", "SupportDesk"}
PUBLISHER_ROLES = {"SuperAdmin", "Manager"}


def _can_edit(user):
    return user.is_authenticated and getattr(user, "role_access", None) in EDITOR_ROLES


def _can_publish(user):
    return user.is_authenticated and getattr(user, "role_access", None) in PUBLISHER_ROLES


def _require_login_if_configured(request):
    """Return redirect response if public access is restricted, else None."""
    if not request.user.is_authenticated:
        config = SiteConfig.get_solo()
        if config.require_public_login:
            return redirect(f"/auth/login/?next={request.path}")
    return None


# ---------------------------------------------------------------------------
# Public / Viewer views
# ---------------------------------------------------------------------------

def landing(request):
    gate = _require_login_if_configured(request)
    if gate:
        return gate

    if _can_edit(request.user):
        manuals = Manual.objects.all().order_by("order", "title")
    else:
        manuals = Manual.objects.filter(is_published=True).order_by("order", "title")

    return render(request, "manual/landing.html", {
        "manuals": manuals,
        "manual_config": ManualLandingConfig.get_solo(),
    })


def viewer(request, manual_slug, page_id=None):
    gate = _require_login_if_configured(request)
    if gate:
        return gate

    if _can_edit(request.user):
        manual = get_object_or_404(Manual, slug=manual_slug)
    else:
        manual = get_object_or_404(Manual, slug=manual_slug, is_published=True)

    page = None
    if page_id:
        if _can_edit(request.user):
            page = get_object_or_404(ManualPage, pk=page_id, manual=manual)
        else:
            page = get_object_or_404(ManualPage, pk=page_id, manual=manual, is_published=True)
    else:
        # Default to first published root page
        qs = manual.pages.filter(parent=None)
        if not _can_edit(request.user):
            qs = qs.filter(is_published=True)
        page = qs.order_by("order", "title").first()

    # Build full tree for sidebar navigation
    if _can_edit(request.user):
        root_pages = manual.pages.filter(parent=None).order_by("order", "title")
    else:
        root_pages = manual.pages.filter(parent=None, is_published=True).order_by("order", "title")

    return render(request, "manual/viewer.html", {
        "manual": manual,
        "page": page,
        "root_pages": root_pages,
        "can_edit": _can_edit(request.user),
        "can_publish": _can_publish(request.user),
        "page_content": page.content if page else None,
    })


# ---------------------------------------------------------------------------
# Editor views (SupportDesk + Manager + SuperAdmin)
# ---------------------------------------------------------------------------

@login_required
def manage_list(request):
    if not _can_edit(request.user):
        return redirect("manual:landing")

    is_superadmin = getattr(request.user, "role_access", None) == "SuperAdmin"

    if request.method == "POST" and request.POST.get("action") == "save_hero":
        if is_superadmin:
            config = ManualLandingConfig.get_solo()
            config.hero_title = request.POST.get("hero_title", "").strip() or config.hero_title
            config.hero_subtitle = request.POST.get("hero_subtitle", "").strip()
            config.save()
            messages.success(request, "Landing page hero updated.")
        return redirect("manual:manage_list")

    manuals = Manual.objects.all().order_by("order", "title")
    return render(request, "manual/manage_list.html", {
        "manuals": manuals,
        "can_publish": _can_publish(request.user),
        "is_superadmin": is_superadmin,
        "manual_config": ManualLandingConfig.get_solo(),
    })


@login_required
def manual_create(request):
    if not _can_edit(request.user):
        return redirect("manual:landing")

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        icon = request.POST.get("icon", "📗").strip() or "📗"
        order = int(request.POST.get("order", 0) or 0)

        if not title:
            messages.error(request, "Title is required.")
            return redirect("manual:manage_list")

        manual = Manual.objects.create(
            title=title,
            description=description,
            icon=icon,
            order=order,
            created_by=request.user,
        )
        messages.success(request, f'Manual "{manual.title}" created.')
        return redirect("manual:manage_list")

    return redirect("manual:manage_list")


@login_required
def manual_edit(request, manual_slug):
    if not _can_edit(request.user):
        return redirect("manual:landing")

    manual = get_object_or_404(Manual, slug=manual_slug)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "toggle_publish":
            if _can_publish(request.user):
                manual.is_published = not manual.is_published
                manual.save(update_fields=["is_published"])
                status = "published" if manual.is_published else "unpublished"
                messages.success(request, f'Manual "{manual.title}" {status}.')
            return redirect("manual:manage_list")

        if action == "delete":
            if _can_publish(request.user):
                title = manual.title
                manual.delete()
                messages.success(request, f'Manual "{title}" deleted.')
            return redirect("manual:manage_list")

        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        icon = request.POST.get("icon", "📗").strip() or "📗"
        order = int(request.POST.get("order", 0) or 0)

        if title:
            manual.title = title
            manual.description = description
            manual.icon = icon
            manual.order = order
            manual.save()
            messages.success(request, "Manual updated.")

        return redirect("manual:manage_list")

    root_pages = manual.pages.filter(parent=None).order_by("order", "title")
    return render(request, "manual/manual_edit.html", {
        "manual": manual,
        "root_pages": root_pages,
        "can_publish": _can_publish(request.user),
    })


@login_required
def page_edit(request, manual_slug, page_id=None):
    if not _can_edit(request.user):
        return redirect("manual:landing")

    manual = get_object_or_404(Manual, slug=manual_slug)
    page = get_object_or_404(ManualPage, pk=page_id, manual=manual) if page_id else None

    if request.method == "POST":
        action = request.POST.get("action", "save")

        if action == "delete" and page:
            if _can_publish(request.user):
                page.delete()
                messages.success(request, "Page deleted.")
            return redirect("manual:manual_edit", manual_slug=manual_slug)

        if action == "toggle_publish" and page:
            if _can_publish(request.user):
                page.is_published = not page.is_published
                page.save(update_fields=["is_published"])
            return redirect("manual:page_edit", manual_slug=manual_slug, page_id=page.pk)

        title = request.POST.get("title", "").strip()
        content_raw = request.POST.get("content", "{}").strip()
        parent_id = request.POST.get("parent_id") or None
        is_faq = request.POST.get("is_faq") == "1"
        order = int(request.POST.get("order", 0) or 0)

        try:
            content = json.loads(content_raw) if content_raw else {}
        except (json.JSONDecodeError, ValueError):
            content = {}

        parent = None
        if parent_id:
            parent = ManualPage.objects.filter(pk=parent_id, manual=manual).first()

        if not title:
            messages.error(request, "Title is required.")
            return redirect("manual:page_edit", manual_slug=manual_slug, page_id=page_id) if page_id else redirect("manual:page_new", manual_slug=manual_slug)

        if page:
            page.title = title
            page.content = content
            page.parent = parent
            page.is_faq = is_faq
            page.order = order
            page.save()
            messages.success(request, "Page saved.")
            return redirect("manual:page_edit", manual_slug=manual_slug, page_id=page.pk)
        else:
            new_page = ManualPage.objects.create(
                manual=manual,
                parent=parent,
                title=title,
                content=content,
                is_faq=is_faq,
                order=order,
                created_by=request.user,
            )
            messages.success(request, f'Page "{new_page.title}" created.')
            return redirect("manual:page_edit", manual_slug=manual_slug, page_id=new_page.pk)

    all_pages = ManualPage.objects.filter(manual=manual).order_by("order", "title")
    return render(request, "manual/page_edit.html", {
        "manual": manual,
        "page": page,
        "all_pages": all_pages,
        "can_publish": _can_publish(request.user),
        "content_json": json.dumps(page.content) if page else "{}",
    })


@login_required
def page_new(request, manual_slug):
    return page_edit(request, manual_slug, page_id=None)


@login_required
@require_POST
def page_reorder(request, manual_slug):
    """AJAX endpoint: receive ordered list of page IDs and update order field."""
    if not _can_edit(request.user):
        return JsonResponse({"ok": False}, status=403)

    manual = get_object_or_404(Manual, slug=manual_slug)
    try:
        data = json.loads(request.body)
        for item in data:
            ManualPage.objects.filter(pk=item["id"], manual=manual).update(
                order=item["order"],
                parent_id=item.get("parent_id"),
            )
    except (json.JSONDecodeError, KeyError, TypeError):
        return JsonResponse({"ok": False}, status=400)

    return JsonResponse({"ok": True})
