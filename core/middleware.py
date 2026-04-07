"""
Core App — Middleware
======================
Custom middlewares for RoC Desk.
"""
from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import resolve

from .models import SiteConfig


class PublicLoginRestrictionMiddleware:
    """
    Middleware that:
    1. Enforces login on public portal routes if SiteConfig 'require_public_login' is True.
    2. Blocks Portal Users from accessing the internal /desk/ management area.
    """
    STAFF_NAMESPACES = {"desk", "users_desk", "kb_desk", "links_desk", "forms_desk"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            current_route = resolve(request.path_info)
            namespace = current_route.namespace
        except Exception:
            return self.get_response(request)

        # ---- Rule 1: Portal User must NOT access internal desk routes ----
        if request.user.is_authenticated:
            role = getattr(request.user, "role_access", None)
            if role == "PortalUser" and namespace in self.STAFF_NAMESPACES:
                return HttpResponseForbidden(
                    "Access denied. Portal Users cannot access the management area."
                )

        # ---- Rule 2: Unauthenticated users blocked from public portal if config requires login ----
        if not request.user.is_authenticated:
            if namespace in ("cases", "knowledge_base"):
                config = SiteConfig.get_solo()
                if config.require_public_login:
                    return redirect(f"{settings.LOGIN_URL}?next={request.path}")

        return self.get_response(request)

