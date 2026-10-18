"""
Core App — Middleware
======================
Custom middlewares for RoC Desk.
"""
import zoneinfo

from django.conf import settings
from django.shortcuts import redirect
from django.urls import resolve
from django.utils import timezone as tz

from .models import SiteConfig

_DEFAULT_TZ = "Asia/Jakarta"


class TimezoneMiddleware:
    """
    Activates the requesting user's preferred timezone for each request
    so that all Django template date/time filters render in local time.

    Priority order:
      1. Authenticated user's saved ``timezone`` field
      2. ``browser_timezone`` cookie (set by client-side JS for anonymous users)
      3. DEFAULT_TZ fallback (Asia/Jakarta)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tz_name = None

        if request.user.is_authenticated:
            tz_name = getattr(request.user, "timezone", None)

        if not tz_name:
            tz_name = request.COOKIES.get("browser_timezone")

        if not tz_name:
            tz_name = _DEFAULT_TZ

        try:
            tz.activate(zoneinfo.ZoneInfo(tz_name))
        except (zoneinfo.ZoneInfoNotFoundError, KeyError, Exception):
            tz.activate(zoneinfo.ZoneInfo(_DEFAULT_TZ))

        response = self.get_response(request)
        tz.deactivate()
        return response


class ContentSecurityPolicyMiddleware:
    """
    Adds a Content-Security-Policy header to every response.

    Policy is intentionally permissive on script-src because the app uses
    Tailwind CDN (requires inline execution), Alpine.js (uses eval() internally
    to process x-data/x-bind/x-on expressions), and HTMX (also uses eval()).
    Removing unsafe-eval breaks all Alpine.js interactivity. Locking to
    nonce-based scripts is tracked as a future hardening step (M2-b).
    The meaningful wins here are:
      - object-src 'none'  — blocks Flash / plugin-based code execution
      - base-uri 'self'    — prevents base tag hijacking
      - form-action 'self' — prevents cross-origin form submission
      - frame-ancestors 'none' — layered defence with X-Frame-Options
    """

    # Extra hosts appended to connect-src / img-src so the browser can
    # fetch time-limited SAS URLs from cloud storage. pdf.js loads the
    # e-signature PDF via fetch(), which connect-src governs — without
    # this the Configure page shows a blank viewer and a CSP violation.
    @staticmethod
    def _storage_hosts():
        hosts = []
        if getattr(settings, "USE_AZURE_STORAGE", False):
            account = getattr(settings, "AZURE_ACCOUNT_NAME", "")
            custom = getattr(settings, "AZURE_CUSTOM_DOMAIN", "")
            if custom:
                hosts.append(f"https://{custom}")
            elif account:
                hosts.append(f"https://{account}.blob.core.windows.net")
            else:
                hosts.append("https://*.blob.core.windows.net")
        if getattr(settings, "USE_S3_STORAGE", False):
            bucket = getattr(settings, "AWS_STORAGE_BUCKET_NAME", "")
            if bucket:
                hosts.append(f"https://{bucket}.s3.amazonaws.com")
            hosts.append("https://*.amazonaws.com")
        return " ".join(hosts)

    CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "https://cdn.tailwindcss.com "
            "https://cdn.jsdelivr.net "
            "https://unpkg.com "
            "https://esm.sh; "
        "style-src 'self' 'unsafe-inline' "
            "https://fonts.googleapis.com "
            "https://cdn.jsdelivr.net "
            "https://unpkg.com; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https: ; "
        "media-src 'self' data: blob: https: ; "
        "frame-src 'self' "
            "https://www.youtube.com "
            "https://www.youtube-nocookie.com "
            "https://youtu.be "
            "https://www.tiktok.com "
            "https://*.sharepoint.com; "
        "connect-src 'self' "
            "https://cdn.jsdelivr.net "
            "https://unpkg.com "
            "https://esm.sh "
            "https://libur.deno.dev "
            "https://nominatim.openstreetmap.org; "
        "worker-src blob: https://cdn.jsdelivr.net; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none';"
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self.debug = getattr(settings, 'DEBUG', False)
        storage_hosts = self._storage_hosts()
        if storage_hosts:
            self.csp = self.CSP.replace(
                "frame-src 'self' ",
                f"frame-src 'self' {storage_hosts} ",
            ).replace(
                "connect-src 'self' ",
                f"connect-src 'self' {storage_hosts} ",
            )
        else:
            self.csp = self.CSP

    @staticmethod
    def _extra_frame_hosts():
        """
        Reads admin-configurable embed domains (SiteConfig.csp_allowed_frame_hosts)
        so new embed providers (Canva, Figma, ...) can be allowed without a redeploy.
        """
        try:
            hosts = SiteConfig.get_solo().csp_allowed_frame_hosts
        except Exception:
            return ""
        return " ".join(hosts.split()) if hosts else ""

    def __call__(self, request):
        response = self.get_response(request)
        content_type = response.get('Content-Type', '')
        if 'text/html' in content_type and not self.debug:
            csp = self.csp
            extra_frame_hosts = self._extra_frame_hosts()
            if extra_frame_hosts:
                csp = csp.replace(
                    "frame-src 'self' ",
                    f"frame-src 'self' {extra_frame_hosts} ",
                )
            response['Content-Security-Policy'] = csp
        return response


# Known link-preview crawlers only — deliberately NOT a generic "bot|crawler"
# substring match, since that would turn the User-Agent header (trivially
# spoofable) into a blanket login bypass. Each of these fetches a page once
# to read its og:/twitter: tags for a chat/social share card; they don't
# execute JS or follow the meta-refresh a human browser would.
LINK_PREVIEW_BOT_USER_AGENTS = (
    "facebookexternalhit", "Twitterbot", "LinkedInBot", "Slackbot",
    "TelegramBot", "Discordbot", "WhatsApp", "SkypeUriPreview",
    "TeamsUserAgent", "BingPreview", "Applebot",
)


def is_link_preview_bot(request):
    ua = request.META.get("HTTP_USER_AGENT", "")
    return any(bot.lower() in ua.lower() for bot in LINK_PREVIEW_BOT_USER_AGENTS)


class PublicLoginRestrictionMiddleware:
    """
    Middleware that:
    1. Enforces login on public portal routes if SiteConfig 'require_public_login' is True.
    2. Blocks Portal Users from accessing the internal /desk/ management area,
       except for the specific modules an admin has explicitly granted them
       (see core.models.MODULE_REGISTRY / User.module_access).
    """
    STAFF_NAMESPACES = {"desk", "users_desk", "kb_desk", "links_desk", "forms_desk"}

    # Namespaces that map 1:1 to a single grantable module — reachable by a
    # PortalUser only if that module is in their get_accessible_modules().
    NAMESPACE_MODULE_MAP = {
        "kb_desk": "knowledge_management",
        "links_desk": "shorten_link",
        "shared_docs_desk": "shared_docs",
    }

    # Path prefixes living inside the broad "desk" namespace that a PortalUser
    # may reach if they hold the matching grant. Everything else under "desk"
    # (case management, employees, categories, users, etc.) stays fully
    # blocked regardless — this is a narrow, explicit carve-out, not a blanket
    # namespace exception, because "desk" also covers staff-only features that
    # must never be reachable via a module grant.
    DESK_MODULE_PREFIXES = (
        ("/desk/forms/", "form_creator"),
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def _portal_user_module_blocked(self, request, namespace):
        """Return True if this PortalUser request must be blocked."""
        modules = request.user.get_accessible_modules()

        if namespace in self.NAMESPACE_MODULE_MAP:
            return self.NAMESPACE_MODULE_MAP[namespace] not in modules

        if namespace == "desk":
            for prefix, required_module in self.DESK_MODULE_PREFIXES:
                if request.path_info.startswith(prefix):
                    return required_module not in modules
            # Any other /desk/ path — not a grantable module, stays blocked.
            return True

        return namespace in self.STAFF_NAMESPACES

    def __call__(self, request):
        try:
            current_route = resolve(request.path_info)
            namespace = current_route.namespace
        except Exception:
            return self.get_response(request)

        # ---- Rule 1: Portal User must NOT access internal desk routes, ----
        # ---- unless explicitly granted the specific module involved.   ----
        if request.user.is_authenticated:
            role = getattr(request.user, "role_access", None)
            if role == "PortalUser" and self._portal_user_module_blocked(request, namespace):
                from django.urls import reverse
                # Desk ticket links (Teams card, email, shared URL) → the same ticket in
                # the portal chat, which runs its own requester/follower check.
                # Other Task Management pages → My Tickets.
                if namespace == "desk" and request.method == "GET":
                    case_id = current_route.kwargs.get("case_id")
                    if case_id:
                        return redirect(reverse("chat:room", kwargs={"case_uuid": case_id}))
                    if request.path_info.startswith("/desk/cases/"):
                        return redirect(reverse("chat:my_tickets"))
                return redirect(reverse("cases:dashboard"))


        # ---- Rule 2: Unauthenticated users blocked from public portal if config requires login ----
        if not request.user.is_authenticated:
            if namespace in ("cases", "knowledge_base", "manual"):
                # Let known link-preview crawlers through to a single published
                # KB article so a shared link still unfurls into a proper card
                # (title/description/image) in chat apps and social platforms,
                # even when the org otherwise requires login to browse the KB.
                is_kb_article = namespace == "knowledge_base" and current_route.url_name == "article_detail"
                if is_kb_article and is_link_preview_bot(request):
                    return self.get_response(request)

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
            from .adapters import user_has_sso_account
            if user_has_sso_account(request.user):
                request.user.must_change_password = False
                request.user.save(update_fields=["must_change_password"])
                return self.get_response(request)
            return redirect("/auth/change-password/")
        return self.get_response(request)


class HtmxRedirectMiddleware:
    """
    htmx-boosted navigation (hx-boost + hx-select="#jk-main-content" on the
    sidebar nav, see base.html) swaps only a fragment of the response into
    the page. When the server needs to send the browser somewhere else
    entirely instead — the login page after a session expires, the forced
    password-change page, a license-gate page — that full page doesn't
    contain #jk-main-content, so htmx's swap silently fails
    (htmx:targetError in the console) and the boosted click leaves the page
    blank with a stale URL, looking like navigation hung (reported as:
    clicking "My Tickets" after the session expired did redirect to
    /auth/login/ in the URL bar, but the page itself never rendered).

    Converts any redirect response to an htmx request into an HX-Redirect
    header instead, which tells htmx to do a full browser navigation
    (window.location) rather than attempt a partial swap — the standard
    fix for this class of bug in htmx-boosted apps.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            request.headers.get("HX-Request") == "true"
            and response.status_code in (301, 302, 303, 307, 308)
            and response.has_header("Location")
        ):
            from django.http import HttpResponse
            redirect_response = HttpResponse(status=200)
            redirect_response["HX-Redirect"] = response["Location"]
            return redirect_response
        return response


class SiteUrlAutoDiscoveryMiddleware:
    """
    Automatically populates SiteConfig.site_url from the current request's Host header
    if it hasn't been set yet. This eliminates the need to manually configure SITE_URL
    in the database or .env for standard deployments.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            # Prevent catching internal Docker healthchecks which use localhost:8001
            if "localhost:8001" not in request.get_host():
                config = SiteConfig.get_solo()
                if not config.site_url:
                    # Construct base URL, e.g., "https://finance.santika.com"
                    base_url = request.build_absolute_uri('/').rstrip('/')
                    config.site_url = base_url
                    config.save(update_fields=['site_url'])
        except Exception:
            pass

        return self.get_response(request)

