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


class ContentSecurityPolicyMiddleware:
    """
    Adds a Content-Security-Policy header to every response.

    Policy is intentionally permissive on script-src because the app uses
    Tailwind CDN (requires inline execution), Alpine.js (inline x-data), and
    several inline <script> blocks. Locking script-src to 'self' only would
    require migrating every template to nonce-based scripts — tracked as a
    future hardening step (M2-b). The meaningful wins here are:
      - object-src 'none'  — blocks Flash / plugin-based code execution
      - base-uri 'self'    — prevents base tag hijacking
      - form-action 'self' — prevents cross-origin form submission
      - frame-ancestors 'none' — layered defence with X-Frame-Options
    """

    CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' "
            "https://cdn.tailwindcss.com "
            "https://cdn.jsdelivr.net "
            "https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' "
            "https://fonts.googleapis.com "
            "https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none';"
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self.debug = getattr(settings, 'DEBUG', False)

    def __call__(self, request):
        response = self.get_response(request)
        content_type = response.get('Content-Type', '')
        if 'text/html' in content_type and not self.debug:
            response['Content-Security-Policy'] = self.CSP
        return response


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


class ForcePasswordChangeMiddleware:
    """
    Redirects authenticated users with must_change_password=True to the
    change-password page before they can access anything else.
    """
    EXEMPT_PATHS = {
        "/auth/change-password/",
        "/auth/logout/",
        "/auth/login/",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and getattr(request.user, "must_change_password", False)
            and request.path not in self.EXEMPT_PATHS
            and not request.path.startswith("/static/")
            and not request.path.startswith("/media/")
        ):
            return redirect("/auth/change-password/")
        return self.get_response(request)

