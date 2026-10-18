import json
import os
from functools import wraps
from urllib.parse import urlparse

from django.contrib import messages
from django.core.files.base import ContentFile
from django.contrib.auth.views import redirect_to_login
from django.db import models as db_models
from django.views import View
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, HttpResponseForbidden
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from core.list_pagination import LIST_PAGE_SIZE, PageSizeMixin
from core.excel_utils import export_queryset_xlsx, safe_cell
from core.list_actions import get_selected, bulk_set_archived, bulk_delete as core_bulk_delete
from core.models import User
from licensing.decorators import FeatureRequiredMixin, feature_required

from .models import ShortLink
from .forms import ShortLinkForm

class StaffRequiredMixin(LoginRequiredMixin):
    """
    Mixin to ensure user is authenticated AND has staff-level access.
    Auditors can only view the list and QR codes. A PortalUser is allowed
    through only if explicitly granted the 'shorten_link' module — and even
    then, views must scope querysets to their own records (see
    PortalOwnQuerysetMixin below).
    """
    # Set to True on views that Auditors are allowed to access.
    auditor_allowed = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        allowed_roles = {
            User.RoleAccess.SUPERADMIN,
            User.RoleAccess.MANAGER,
            User.RoleAccess.SUPPORTDESK,
            User.RoleAccess.ENGINEER,
            User.RoleAccess.AUDITOR,
        }
        role = getattr(request.user, "role_access", None)
        if role not in allowed_roles:
            if role != User.RoleAccess.PORTALUSER or "shorten_link" not in request.user.get_accessible_modules():
                return HttpResponseForbidden("Access denied.")

        if role == User.RoleAccess.AUDITOR and not self.auditor_allowed:
            return HttpResponseForbidden("Access denied. Auditors can only view the link list.")

        return super().dispatch(request, *args, **kwargs)


class PortalOwnQuerysetMixin:
    """Personal per-user scoping — every role only sees/edits/deletes the
    short links they created themselves, regardless of role — except
    Auditors, whose whole purpose is cross-user oversight, and who
    StaffRequiredMixin already restricts to view-only (list) access."""

    def get_queryset(self):
        qs = super().get_queryset()
        if getattr(self.request.user, "role_access", None) == User.RoleAccess.AUDITOR:
            return qs
        return qs.filter(created_by=self.request.user)


def staff_required(auditor_allowed=False):
    """
    Function-view equivalent of StaffRequiredMixin above, for the bulk-action
    endpoints (which are plain function views, not CBVs). Mirrors the exact
    same role gate so behavior stays identical between the list/edit CBVs
    and the bulk actions attached to them.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())

            allowed_roles = {
                User.RoleAccess.SUPERADMIN,
                User.RoleAccess.MANAGER,
                User.RoleAccess.SUPPORTDESK,
                User.RoleAccess.ENGINEER,
                User.RoleAccess.AUDITOR,
            }
            role = getattr(request.user, "role_access", None)
            if role not in allowed_roles:
                if role != User.RoleAccess.PORTALUSER or "shorten_link" not in request.user.get_accessible_modules():
                    return HttpResponseForbidden("Access denied.")

            if role == User.RoleAccess.AUDITOR and not auditor_allowed:
                return HttpResponseForbidden("Access denied. Auditors can only view the link list.")

            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def _own_queryset(request):
    """Function-view equivalent of PortalOwnQuerysetMixin.get_queryset above."""
    qs = ShortLink.objects.all()
    if getattr(request.user, "role_access", None) == User.RoleAccess.AUDITOR:
        return qs
    return qs.filter(created_by=request.user)


def _generate_unique_slug(base_slug):
    """Generate a free slug for a duplicated ShortLink, e.g. 'promo' -> 'promo-copy',
    'promo-copy', 'promo-copy-2', ... until an unused one is found."""
    candidate = f"{base_slug}-copy"
    suffix = 2
    while ShortLink.objects.filter(slug=candidate).exists():
        candidate = f"{base_slug}-copy-{suffix}"
        suffix += 1
    return candidate


# ---------------------------------------------------------
# Public — Redirect
# ---------------------------------------------------------

class RedirectLinkView(View):
    """
    Catch-all short link redirect. Increments click counter.

    Renders the social_card.html interstitial (title/description/QR og: and
    twitter: tags, 0s meta-refresh + JS fallback to the real target) instead
    of a bare HTTP redirect, so link-preview crawlers (Facebook, Twitter,
    Slack, Microsoft Teams, WhatsApp, ...) — which read the fetched HTML but
    don't execute meta-refresh/JS — see the card's own title/description/
    image rather than nothing (a raw 3xx has no OG tags to read) or whatever
    the destination page happens to render.
    """

    def get(self, request, slug):
        link = get_object_or_404(ShortLink, slug=slug)

        if link.require_login and not request.user.is_authenticated:
            # Link-preview crawlers can't sign in — give them the card (title/
            # description/QR) but never the destination URL.
            if _is_link_preview_bot(request):
                return render(request, "links/social_card.html", {"link": link, "hide_target": True})
            return redirect_to_login(request.get_full_path())

        link.clicks += 1
        link.save(update_fields=["clicks"])
        if link.portal_enabled:
            return render(request, "links/redirect_portal.html", {
                "link": link,
                "target_host": urlparse(link.target_url).netloc,
            })
        return render(request, "links/social_card.html", {"link": link})


LINK_PREVIEW_BOT_TOKENS = (
    "facebookexternalhit", "facebot", "twitterbot", "slackbot", "linkedinbot",
    "whatsapp", "telegrambot", "discordbot", "skypeuripreview", "microsoftpreview",
    "googlebot", "bingbot", "applebot", "embedly", "pinterest",
)


def _is_link_preview_bot(request):
    ua = request.META.get("HTTP_USER_AGENT", "").lower()
    return any(token in ua for token in LINK_PREVIEW_BOT_TOKENS)


# ---------------------------------------------------------
# Desk — Management (login required)
# ---------------------------------------------------------

LINK_STATUS_META = {
    "Active": {"label": "Active", "badge_class": "jk-badge-resolved"},
    "Archived": {"label": "Archived", "badge_class": "jk-badge-cancelled"},
}


class LinkListView(FeatureRequiredMixin, StaffRequiredMixin, PortalOwnQuerysetMixin, PageSizeMixin, ListView):
    feature_required = 'short_links'
    auditor_allowed = True
    model = ShortLink
    template_name = "desk/links/list.html"
    context_object_name = "links"
    paginate_by = LIST_PAGE_SIZE

    LINK_FILTERABLE_FIELDS = {
        "is_archived": {
            "label": "Status", "type": "choice", "lookup": "is_archived",
            "choices": [("false", "Active"), ("true", "Archived")],
            "python_type": "bool",
        },
    }

    def get_queryset(self):
        from core.dynamic_filters import apply_dynamic_filters

        qs = super().get_queryset()
        qs, self.active_filters = apply_dynamic_filters(qs, self.request, self.LINK_FILTERABLE_FIELDS)

        self.show_archived = any(
            af["field_key"] == "is_archived" and af["value"] == "true" for af in self.active_filters
        )
        if not any(af["field_key"] == "is_archived" for af in self.active_filters):
            qs = qs.filter(is_archived=False)

        query = self.request.GET.get("q", "").strip()
        if query:
            qs = qs.filter(
                db_models.Q(slug__icontains=query)
                | db_models.Q(target_url__icontains=query)
                | db_models.Q(title__icontains=query)
            )
        return qs

    def get_context_data(self, **kwargs):
        from core.dynamic_filters import active_filters_json, fields_json

        ctx = super().get_context_data(**kwargs)
        ctx["search_query"] = self.request.GET.get("q", "")
        ctx["show_archived"] = self.show_archived
        ctx["active_filters"] = self.active_filters
        ctx["active_filters_json"] = active_filters_json(self.active_filters)
        ctx["dynamic_fields_json"] = fields_json(self.LINK_FILTERABLE_FIELDS)
        ctx["has_active_filters"] = bool(self.active_filters)
        ctx["status_meta_map"] = LINK_STATUS_META
        ctx["archive_url"] = reverse("links_desk:bulk_archive")
        ctx["unarchive_url"] = reverse("links_desk:bulk_unarchive") if self.show_archived else None
        ctx["duplicate_url"] = reverse("links_desk:bulk_duplicate")
        ctx["export_url"] = reverse("links_desk:bulk_export")
        ctx["delete_url"] = reverse("links_desk:bulk_delete")
        return ctx


def _warn_if_silent(request, link):
    """Say so when an uploaded video has no sound track — the speaker button cannot work then."""
    if link.portal_media and link.portal_media_type == "video" and link.portal_media_has_audio is False:
        messages.warning(request, "This video has no sound track, so visitors will not hear anything. "
                                  "Upload a version that includes audio if sound is wanted.")



class LinkCreateView(FeatureRequiredMixin, StaffRequiredMixin, CreateView):
    feature_required = 'short_links'
    model = ShortLink
    form_class = ShortLinkForm
    template_name = "desk/links/form.html"
    success_url = reverse_lazy("links_desk:list")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        response = super().form_valid(form)
        _warn_if_silent(self.request, self.object)
        return response


class LinkUpdateView(FeatureRequiredMixin, StaffRequiredMixin, PortalOwnQuerysetMixin, UpdateView):
    feature_required = 'short_links'
    model = ShortLink
    form_class = ShortLinkForm
    template_name = "desk/links/form.html"
    success_url = reverse_lazy("links_desk:list")

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        response = super().form_valid(form)
        _warn_if_silent(self.request, self.object)
        return response


class LinkDeleteView(FeatureRequiredMixin, StaffRequiredMixin, PortalOwnQuerysetMixin, DeleteView):
    feature_required = 'short_links'
    model = ShortLink
    template_name = "desk/links/confirm_delete.html"
    success_url = reverse_lazy("links_desk:list")


# ---------------------------------------------------------
# API — Slug Availability Check
# ---------------------------------------------------------

class CheckSlugView(FeatureRequiredMixin, StaffRequiredMixin, View):
    """AJAX endpoint: returns {available: true/false} for the requested slug."""
    feature_required = 'short_links'

    def get(self, request):
        slug = request.GET.get("slug", "").strip()
        exclude_pk = request.GET.get("exclude", None)

        if not slug:
            return JsonResponse({"available": False, "error": "Slug is required."})

        qs = ShortLink.objects.filter(slug=slug)
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)

        return JsonResponse({"available": not qs.exists()})


# ---------------------------------------------------------
# Desk — Bulk actions (Archive / Unarchive / Delete / Duplicate / Export)
# ---------------------------------------------------------

@feature_required('short_links')
@staff_required()
@require_POST
def link_bulk_archive(request):
    qs = get_selected(request, _own_queryset(request))
    count = bulk_set_archived(qs, value=True)
    if count:
        messages.success(request, f"Archived {count} link(s).")
    else:
        messages.warning(request, "No links selected.")
    return redirect("links_desk:list")


@feature_required('short_links')
@staff_required()
@require_POST
def link_bulk_unarchive(request):
    qs = get_selected(request, _own_queryset(request))
    count = bulk_set_archived(qs, value=False)
    if count:
        messages.success(request, f"Unarchived {count} link(s).")
    else:
        messages.warning(request, "No links selected.")
    return redirect("links_desk:list")


@feature_required('short_links')
@staff_required()
@require_POST
def link_bulk_delete(request):
    qs = get_selected(request, _own_queryset(request))
    count = core_bulk_delete(qs)
    if count:
        messages.success(request, f"Deleted {count} link(s).")
    else:
        messages.warning(request, "No links selected.")
    return redirect("links_desk:list")


@feature_required('short_links')
@staff_required()
@require_POST
def link_bulk_duplicate(request):
    qs = get_selected(request, _own_queryset(request))
    if qs.count() != 1:
        messages.warning(request, "Select exactly one link to duplicate.")
        return redirect("links_desk:list")

    original = qs.first()
    new_slug = _generate_unique_slug(original.slug)
    copy = ShortLink(
        target_url=original.target_url,
        slug=new_slug,
        title=original.title,
        description=original.description,
        require_login=original.require_login,
        portal_enabled=original.portal_enabled,
        portal_countdown=original.portal_countdown,
        portal_layout=original.portal_layout,
        portal_message=original.portal_message,
        portal_media_type=original.portal_media_type,
        portal_video_url=original.portal_video_url,
        portal_media_sound=original.portal_media_sound,
        portal_media_has_audio=original.portal_media_has_audio,
        created_by=request.user,
        updated_by=request.user,
    )
    if original.portal_media:
        # Own copy of the file, so replacing/removing media on one link never breaks the other
        try:
            with original.portal_media.open("rb") as fh:
                copy.portal_media.save(os.path.basename(original.portal_media.name), ContentFile(fh.read()), save=False)
        except Exception:
            copy.portal_media_type = ""
    copy.save()
    messages.success(request, f"Duplicated link as '{new_slug}'.")
    return redirect("links_desk:list")


@feature_required('short_links')
@staff_required()
@require_POST
def link_bulk_export(request):
    qs = get_selected(request, _own_queryset(request)).order_by("-created_at")
    columns = [
        ("Short Code", lambda l: l.slug),
        ("Target URL", lambda l: safe_cell(l.target_url)),
        ("Clicks", lambda l: l.clicks),
        ("Created", lambda l: l.created_at.strftime("%Y-%m-%d %H:%M") if l.created_at else ""),
        ("Status", lambda l: "Archived" if l.is_archived else "Active"),
    ]
    return export_queryset_xlsx(qs, columns, "short_links.xlsx", sheet_title="Short Links")
