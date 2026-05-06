"""
Core App — allauth Adapters
============================
Custom Account and SocialAccount adapters for SSO via Microsoft and Google.

Flow for new SSO users:
  1. pre_social_login  — validate config & domain; connect to existing email if found
  2. save_user         — create core.User (is_active=False) + whitelist ticket
  3. respond_user_inactive (AccountAdapter) — redirect to SSO pending page

Flow for returning inactive SSO users:
  1. pre_social_login  — connect social account to existing user
  2. respond_user_inactive — redirect to SSO pending page

Flow for active SSO users:
  1. pre_social_login  — connect social account
  2. Normal login → /desk/cases/
"""
from allauth.account.adapter import DefaultAccountAdapter
from allauth.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib import messages
from django.http import HttpResponseRedirect


class AccountAdapter(DefaultAccountAdapter):
    """Disable regular signup through allauth; handle inactive SSO redirect."""

    def is_open_for_signup(self, request):
        return False

    def respond_user_inactive(self, request, user):
        try:
            from allauth.socialaccount.models import SocialAccount
            if SocialAccount.objects.filter(user=user).exists():
                from cases.models import CaseRecord
                ticket = (
                    CaseRecord.objects.filter(
                        requester_email=user.email,
                        tags__contains="sso-whitelist",
                    )
                    .order_by("-created_at")
                    .first()
                )
                request.session["sso_pending_email"] = user.email
                request.session["sso_pending_name"] = user.username
                request.session["sso_pending_ticket"] = ticket.case_number if ticket else None
                return HttpResponseRedirect("/auth/sso-pending/")
        except Exception:
            pass
        return super().respond_user_inactive(request, user)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Handle SSO login validation, user provisioning, and whitelist ticket creation."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_email(self, sociallogin) -> str:
        extra = sociallogin.account.extra_data
        provider = sociallogin.account.provider
        if provider == "microsoft":
            return (extra.get("mail") or extra.get("userPrincipalName") or "").strip()
        if provider == "google":
            return (extra.get("email") or "").strip()
        return ""

    def _get_display_name(self, sociallogin) -> str:
        extra = sociallogin.account.extra_data
        provider = sociallogin.account.provider
        if provider == "microsoft":
            name = extra.get("displayName") or ""
            if not name:
                name = f"{extra.get('givenName', '')} {extra.get('surname', '')}".strip()
        else:
            name = extra.get("name") or ""
            if not name:
                name = f"{extra.get('given_name', '')} {extra.get('family_name', '')}".strip()
        return name

    def _make_initials(self, full_name: str, fallback: str = "U") -> str:
        parts = full_name.split() if full_name else []
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        if parts:
            return parts[0][:2].upper()
        return fallback[:2].upper() if fallback else "U"

    def _get_or_create_whitelist_category(self):
        from cases.models import CaseCategory
        from core.models import SSOConfig

        config = SSOConfig.get_solo()
        if config.sso_whitelist_category_id:
            return config.sso_whitelist_category

        category, _ = CaseCategory.objects.get_or_create(
            slug="sso-whitelist-request",
            defaults={
                "name": "SSO Access Request",
                "prefix_code": "SR",
                "description": "Permintaan akses untuk pengguna baru yang login via SSO.",
                "icon": "🔑",
            },
        )
        return category

    def _create_whitelist_ticket(self, user, provider: str, full_name: str):
        from cases.models import CaseRecord

        provider_label = "Microsoft" if provider == "microsoft" else "Google"
        category = self._get_or_create_whitelist_category()

        ticket = CaseRecord.objects.create(
            category=category,
            subject=f"[SSO] Permintaan Whitelist Akun — {full_name or user.email}",
            problem_description=(
                f"Pengguna baru berhasil autentikasi melalui SSO ({provider_label}) "
                f"namun belum mendapatkan akses ke sistem.\n\n"
                f"Detail Akun:\n"
                f"  • Nama     : {full_name or '-'}\n"
                f"  • Email    : {user.email}\n"
                f"  • Provider : {provider_label}\n"
                f"  • Status   : Non-Aktif (menunggu whitelist)\n\n"
                f"Mohon tinjau dan aktifkan akun ini dari User Management jika sesuai."
            ),
            requester_email=user.email,
            requester_name=full_name or user.email,
            case_type=CaseRecord.Type.REQUEST,
            source=CaseRecord.Source.WEBFORM,
            priority=CaseRecord.Priority.MEDIUM,
            status=CaseRecord.Status.OPEN,
            tags="sso-whitelist,akses-baru",
        )
        return ticket

    # ------------------------------------------------------------------
    # allauth hooks
    # ------------------------------------------------------------------

    def pre_social_login(self, request, sociallogin):
        from core.models import SSOConfig

        config = SSOConfig.get_solo()

        if not config.sso_enabled:
            messages.error(request, "Login SSO saat ini tidak aktif.")
            raise ImmediateHttpResponse(HttpResponseRedirect("/auth/login/"))

        provider = sociallogin.account.provider

        if provider == "microsoft" and not config.microsoft_enabled:
            messages.error(request, "Login via Microsoft SSO tidak aktif.")
            raise ImmediateHttpResponse(HttpResponseRedirect("/auth/login/"))

        if provider == "google":
            if not config.google_enabled:
                messages.error(request, "Login via Google SSO tidak aktif.")
                raise ImmediateHttpResponse(HttpResponseRedirect("/auth/login/"))

            if config.google_allowed_domains:
                allowed = {
                    d.strip().lower()
                    for d in config.google_allowed_domains.split(",")
                    if d.strip()
                }
                email = self._get_email(sociallogin)
                domain = email.split("@")[-1].lower() if "@" in email else ""
                if allowed and domain not in allowed:
                    messages.error(
                        request,
                        f"Domain email '{domain}' tidak diizinkan untuk login SSO.",
                    )
                    raise ImmediateHttpResponse(HttpResponseRedirect("/auth/login/"))

        # Skip if social account already connected to a user
        if sociallogin.is_existing:
            return

        # Connect to existing core.User by email to avoid account takeover error
        email = self._get_email(sociallogin)
        if not email:
            messages.error(request, "Tidak dapat membaca email dari akun SSO Anda.")
            raise ImmediateHttpResponse(HttpResponseRedirect("/auth/login/"))

        from django.contrib.auth import get_user_model

        User = get_user_model()
        try:
            existing_user = User.objects.get(email=email)
            sociallogin.connect(request, existing_user)
        except User.DoesNotExist:
            pass

    def save_user(self, request, sociallogin, form=None):
        """Create new inactive user + whitelist ticket on first SSO login."""
        from django.contrib.auth import get_user_model

        User = get_user_model()

        extra = sociallogin.account.extra_data
        provider = sociallogin.account.provider
        email = self._get_email(sociallogin)
        full_name = self._get_display_name(sociallogin)

        user = sociallogin.user
        user.email = email
        user.username = full_name or email.split("@")[0]
        user.login_username = email
        user.initials = self._make_initials(full_name, fallback=email[:1])
        user.role_access = User.RoleAccess.PORTALUSER
        user.is_active = False
        user.must_change_password = False
        user.set_unusable_password()

        # nik is nullable — leave empty for SSO users
        user.nik = None

        user.save()

        ticket = self._create_whitelist_ticket(user, provider, full_name)

        # Store info so pending page can display it
        request.session["sso_pending_email"] = email
        request.session["sso_pending_name"] = full_name or email
        request.session["sso_pending_ticket"] = ticket.case_number

        # AuditLog
        try:
            from core.models import AuditLog
            from django_ipware import get_client_ip

            ip, _ = get_client_ip(request)
            AuditLog.objects.create(
                actor=None,
                actor_username=email,
                action=AuditLog.Action.LOGIN_SUCCESS,
                ip_address=ip or "",
                details={
                    "method": f"{provider}_sso",
                    "result": "pending_whitelist",
                    "ticket": ticket.case_number,
                },
            )
        except Exception:
            pass

        return user

    def is_auto_signup_allowed(self, request, sociallogin):
        return True
