import json
from django.views import View
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, HttpResponseForbidden
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from core.models import User

from .models import ShortLink
from .forms import ShortLinkForm

class StaffRequiredMixin(LoginRequiredMixin):
    """
    Mixin to ensure user is authenticated AND has staff-level access.
    Portal users are blocked. Auditors are blocked from POST operations.
    """
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        allowed_roles = {
            User.RoleAccess.SUPERADMIN,
            User.RoleAccess.MANAGER,
            User.RoleAccess.SUPPORTDESK,
            User.RoleAccess.AUDITOR,
        }
        if getattr(request.user, "role_access", None) not in allowed_roles:
            return HttpResponseForbidden("Access denied.")

        if request.user.role_access == User.RoleAccess.AUDITOR and request.method not in ("GET", "HEAD", "OPTIONS"):
            return HttpResponseForbidden("Access denied. Auditors have read-only access.")

        return super().dispatch(request, *args, **kwargs)


# ---------------------------------------------------------
# Public — Redirect
# ---------------------------------------------------------

class RedirectLinkView(View):
    """Catch-all short link redirect. Increments click counter."""

    def get(self, request, slug):
        link = get_object_or_404(ShortLink, slug=slug)
        link.clicks += 1
        link.save(update_fields=["clicks"])
        return redirect(link.target_url)


# ---------------------------------------------------------
# Desk — Management (login required)
# ---------------------------------------------------------

class LinkListView(StaffRequiredMixin, ListView):
    model = ShortLink
    template_name = "desk/links/list.html"
    context_object_name = "links"
    paginate_by = 25


class LinkCreateView(StaffRequiredMixin, CreateView):
    model = ShortLink
    form_class = ShortLinkForm
    template_name = "desk/links/form.html"
    success_url = reverse_lazy("links_desk:list")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        return super().form_valid(form)


class LinkUpdateView(StaffRequiredMixin, UpdateView):
    model = ShortLink
    form_class = ShortLinkForm
    template_name = "desk/links/form.html"
    success_url = reverse_lazy("links_desk:list")

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        return super().form_valid(form)


class LinkDeleteView(StaffRequiredMixin, DeleteView):
    model = ShortLink
    template_name = "desk/links/confirm_delete.html"
    success_url = reverse_lazy("links_desk:list")


# ---------------------------------------------------------
# API — Slug Availability Check
# ---------------------------------------------------------

class CheckSlugView(StaffRequiredMixin, View):
    """AJAX endpoint: returns {available: true/false} for the requested slug."""

    def get(self, request):
        slug = request.GET.get("slug", "").strip()
        exclude_pk = request.GET.get("exclude", None)

        if not slug:
            return JsonResponse({"available": False, "error": "Slug is required."})

        qs = ShortLink.objects.filter(slug=slug)
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)

        return JsonResponse({"available": not qs.exists()})
