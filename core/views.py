"""
Core App — Views
===================
"""
import secrets
from urllib.parse import quote

from django.conf import settings
from ipware import get_client_ip as _get_client_ip
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import LoginView as DjangoLoginView, LogoutView
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from core.list_pagination import LIST_PAGE_SIZE, PageSizeMixin
from .auth_forms import CustomAuthenticationForm
from .forms import ForgotPasswordForm, ResetPasswordOTPForm
from .models import OTPToken

from licensing.decorators import FeatureRequiredMixin

User = get_user_model()


class RoleAwareLoginView(DjangoLoginView):
    authentication_form = CustomAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        user = self.request.user
        if user.is_authenticated and getattr(user, "role_access", None) == "PortalUser":
            return reverse("cases:dashboard")
        return super().get_success_url()


class ForgotPasswordView(View):
    """
    Handles the request for a password reset OTP.
    """
    template_name = "registration/forgot_password.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("cases:dashboard")
            
        form = ForgotPasswordForm()
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        if request.user.is_authenticated:
            return redirect("cases:dashboard")
            
        form = ForgotPasswordForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]

            # Rate-limit OTP sends: max 3 per 10 minutes per email address
            rate_key = f"otp_send_rate:{email}"
            send_count = cache.get(rate_key, 0)
            if send_count >= 3:
                messages.warning(request, "Too many requests. Please wait a few minutes before requesting another OTP.")
                return render(request, self.template_name, {"form": form})

            # Always show the same message regardless of whether the email exists (prevent enumeration)
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                messages.success(request, "If that email is registered, you will receive an OTP shortly.")
                return redirect(reverse("reset_password_otp"))

            # Generate a cryptographically secure 6-digit OTP
            otp_code = f"{secrets.randbelow(900000) + 100000}"

            # Invalidate all previous OTPs for this user
            OTPToken.objects.filter(user=user, is_used=False).update(is_used=True)

            # Save new OTP (attempt counter reset)
            OTPToken.objects.create(user=user, token=otp_code)

            # Dispatch the email task
            try:
                from .tasks import send_password_reset_otp_task
                send_password_reset_otp_task.delay(user.email, otp_code, user.username)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error("Could not send OTP task: %s", e)
                form.add_error("email", "System error while dispatching OTP email. Please contact support.")
                return render(request, self.template_name, {"form": form})

            # Increment the rate-limit counter (10 minutes TTL)
            cache.set(rate_key, send_count + 1, timeout=600)

            # Store email in session for the next step
            request.session['reset_email'] = user.email

            messages.success(request, "If that email is registered, you will receive an OTP shortly.")
            return redirect(reverse("reset_password_otp"))
            
        return render(request, self.template_name, {"form": form})


class ResetPasswordOTPView(View):
    """
    Handles the validation of the OTP and setting the new password.
    """
    template_name = "registration/reset_password_otp.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("cases:dashboard")
            
        if 'reset_email' not in request.session:
            messages.warning(request, "Please enter your email to request a new password.")
            return redirect(reverse("forgot_password"))
            
        form = ResetPasswordOTPForm()
        return render(request, self.template_name, {"form": form, "email": request.session.get('reset_email')})

    def post(self, request):
        if request.user.is_authenticated:
            return redirect("cases:dashboard")
            
        email = request.session.get('reset_email')
        if not email:
            messages.warning(request, "Session expired. Please start over.")
            return redirect(reverse("forgot_password"))
            
        form = ResetPasswordOTPForm(request.POST)
        if form.is_valid():
            otp = form.cleaned_data["otp"]
            new_password = form.cleaned_data["new_password"]
            
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                messages.error(request, "Account error. Please start over.")
                return redirect(reverse("forgot_password"))
                
            # Check OTP attempt rate limit (max 5 wrong attempts per session)
            attempt_key = f"otp_attempts:{email}"
            attempts = cache.get(attempt_key, 0)
            if attempts >= 5:
                OTPToken.objects.filter(user=user, is_used=False).update(is_used=True)
                del request.session['reset_email']
                messages.error(request, "Too many incorrect OTP attempts. Please request a new OTP.")
                return redirect(reverse("forgot_password"))

            # Check for a valid OTP
            token_obj = OTPToken.objects.filter(user=user, token=otp, is_used=False).order_by('-created_at').first()

            if not token_obj or not token_obj.is_valid():
                cache.set(attempt_key, attempts + 1, timeout=900)
                form.add_error("otp", "Invalid or expired OTP code.")
                return render(request, self.template_name, {"form": form, "email": email})

            # Valid OTP — clear attempt counter and mark as used
            cache.delete(attempt_key)
            token_obj.is_used = True
            token_obj.save()
            
            # Reset password
            user.set_password(new_password)
            user.save()
            
            # Clear session
            del request.session['reset_email']
            
            messages.success(request, "Your password has been reset successfully. You can now sign in.")
            return redirect(reverse("login"))
            
        return render(request, self.template_name, {"form": form, "email": email})


class ForceChangePasswordView(View):
    """
    Forces users with must_change_password=True to set a new password
    before they can use any other part of the application.
    """
    template_name = "registration/force_change_password.html"

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(reverse("login"))
        from .adapters import user_has_sso_account
        if user_has_sso_account(request.user):
            request.user.must_change_password = False
            request.user.save(update_fields=["must_change_password"])
            return redirect(settings.LOGIN_REDIRECT_URL)
        if not getattr(request.user, "must_change_password", False):
            return redirect(settings.LOGIN_REDIRECT_URL)
        return render(request, self.template_name)

    def post(self, request):
        if not request.user.is_authenticated:
            return redirect(reverse("login"))
        from .adapters import user_has_sso_account
        if user_has_sso_account(request.user):
            request.user.must_change_password = False
            request.user.save(update_fields=["must_change_password"])
            return redirect(settings.LOGIN_REDIRECT_URL)
        new_password = request.POST.get("new_password", "").strip()
        confirm_password = request.POST.get("confirm_password", "").strip()
        if len(new_password) < 8:
            messages.error(request, "Password must be at least 8 characters.")
            return render(request, self.template_name)
        if new_password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, self.template_name)
        user = request.user
        user.set_password(new_password)
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        from .models import AuditLog
        AuditLog.log(AuditLog.Action.PASSWORD_CHANGE, request=request, target=user)
        messages.success(request, "Password changed successfully. Please log in again.")
        return redirect(reverse("login"))


class RequestAccountView(View):
    """
    Public view — allows anyone to request a new account.
    Creates a CaseRecord (source=WebForm) under a system category
    and shows the requester their ticket number.
    """
    template_name = "registration/request_account.html"

    def _get_or_create_category(self):
        """Get or create the 'Account Request' system category."""
        from cases.models import CaseCategory
        cat, _ = CaseCategory.objects.get_or_create(
            slug="account-request",
            defaults={
                "name": "Account Request",
                "prefix_code": "AR",
                "description": "System category for new account requests submitted via the login page.",
                "icon": "👤",
            },
        )
        return cat

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("cases:dashboard")
        return render(request, self.template_name)

    def post(self, request):
        if request.user.is_authenticated:
            return redirect("cases:dashboard")

        email = request.POST.get("email", "").strip()
        description = request.POST.get("description", "").strip()
        requester_name = request.POST.get("requester_name", "").strip()

        errors = {}
        if not email:
            errors["email"] = "Email is required."
        elif "@" not in email or "." not in email.split("@")[-1]:
            errors["email"] = "Enter a valid email address."
        if not description:
            errors["description"] = "Please describe your request."

        if errors:
            return render(request, self.template_name, {
                "field_errors": errors,
                "email": email,
                "requester_name": requester_name,
                "description": description,
            })

        # Rate limiting
        from django.core.cache import cache
        client_ip, _ = _get_client_ip(request)
        client_ip = client_ip or "unknown"
        cache_key = f"request_account_rate_{client_ip}"
        attempts = cache.get(cache_key, 0)
        if attempts >= 3:
            return render(request, self.template_name, {
                "global_error": "Too many requests. Please wait a few minutes and try again.",
                "email": email,
                "requester_name": requester_name,
                "description": description,
            })

        from django.utils import timezone
        from cases.models import CaseRecord, Message
        category = self._get_or_create_category()

        subject = f"Account Request — {email}"
        case = CaseRecord.objects.create(
            requester_email=email,
            requester_name=requester_name or email,
            category=category,
            subject=subject,
            problem_description=description,
            source=CaseRecord.Source.WEBFORM,
            status=CaseRecord.Status.NEW,
            has_unread_messages=True,
            last_user_reply_at=timezone.now(),
        )
        Message.objects.create(
            case=case,
            body=description,
            direction=Message.Direction.INBOUND,
            channel=Message.Channel.WEB,
        )

        from gateways.tasks import _dispatch_new_ticket_notifs
        _dispatch_new_ticket_notifs(str(case.id))

        cache.set(cache_key, attempts + 1, timeout=300)

        return render(request, self.template_name, {
            "success": True,
            "ticket_number": case.case_number,
            "email": email,
        })


def custom_404_view(request, exception=None):
    """
    Custom 404 Error Handler to display a branded, modern animated
    not found page instead of Django's default.
    """
    return render(request, "404.html", status=404)


def custom_csrf_failure_view(request, reason=""):
    """
    Custom CSRF failure handler (403) — replaces Django's plain yellow page
    with a branded session-expired page.
    """
    return render(request, "403_csrf.html", status=403)


# =====================================================================
# Help & About
# =====================================================================

from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST
from django.http import HttpResponseForbidden

from .models import Feedback, SiteConfig, User
import django


# =====================================================================
# User Profile
# =====================================================================

@login_required
def profile_view(request):
    """Current user's profile — view and edit own info + change password."""
    from .models import UserNotificationPreference
    from .adapters import user_has_sso_account
    user = request.user

    # Get or create notification preferences
    notif_pref, _ = UserNotificationPreference.objects.get_or_create(user=user)

    if request.method == 'POST':
        action = request.POST.get('action', 'profile')

        if action == 'profile':
            user.username     = request.POST.get('display_name', user.username).strip()
            user.phone_number = request.POST.get('phone_number', '').strip()
            user.save(update_fields=['username', 'phone_number'])
            messages.success(request, "Profile updated successfully.")
            return redirect('profile')

        elif action == 'timezone':
            import zoneinfo
            new_tz = request.POST.get('timezone', '').strip()
            try:
                zoneinfo.ZoneInfo(new_tz)
                user.timezone = new_tz
                user.save(update_fields=['timezone'])
                messages.success(request, f"Timezone updated to {new_tz}.")
            except Exception:
                messages.error(request, "Invalid timezone selected.")
            return redirect('profile')

        elif action == 'notifications':
            # Channel toggles
            notif_pref.email_enabled = request.POST.get('email_enabled') == 'on'
            if _whatsapp_notifications_available():
                notif_pref.whatsapp_enabled = request.POST.get('whatsapp_enabled') == 'on'
                notif_pref.whatsapp_number = request.POST.get('whatsapp_number', '').strip()
            else:
                notif_pref.whatsapp_enabled = False
            # Teams (personal chat with the Workflows bot) — only offered when the
            # admin enabled it and this user's email is in the Microsoft 365 tenant
            if _teams_personal_available(user):
                notif_pref.teams_enabled = request.POST.get('teams_enabled') == 'on'
            notif_pref.teams_webhook_url = ""

            # Event toggles
            notif_pref.on_new_message = request.POST.get('on_new_message') == 'on'
            notif_pref.on_mention = request.POST.get('on_mention') == 'on'
            notif_pref.on_status_change = request.POST.get('on_status_change') == 'on'
            notif_pref.on_follower_added = request.POST.get('on_follower_added') == 'on'

            # Quiet hours
            quiet_start = request.POST.get('quiet_start', '').strip()
            quiet_end = request.POST.get('quiet_end', '').strip()
            from datetime import time as dt_time
            try:
                if quiet_start:
                    h, m = map(int, quiet_start.split(':'))
                    notif_pref.quiet_start = dt_time(h, m)
                else:
                    notif_pref.quiet_start = None
            except (ValueError, TypeError):
                notif_pref.quiet_start = None
            try:
                if quiet_end:
                    h, m = map(int, quiet_end.split(':'))
                    notif_pref.quiet_end = dt_time(h, m)
                else:
                    notif_pref.quiet_end = None
            except (ValueError, TypeError):
                notif_pref.quiet_end = None

            notif_pref.save()
            messages.success(request, "Notification preferences saved.")
            return redirect('profile')

        elif action == 'password':
            if user_has_sso_account(user):
                messages.error(request, "Password is managed by your SSO provider and cannot be changed here.")
                return redirect('profile')
            current = request.POST.get('current_password', '')
            new_pw  = request.POST.get('new_password', '')
            confirm = request.POST.get('confirm_password', '')

            if not user.check_password(current):
                messages.error(request, "Current password is incorrect.")
            elif len(new_pw) < 8:
                messages.error(request, "New password must be at least 8 characters.")
            elif new_pw != confirm:
                messages.error(request, "New passwords do not match.")
            else:
                user.set_password(new_pw)
                user.save()
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, user)
                messages.success(request, "Password changed successfully.")
            return redirect('profile')

    timezone_choices = [
        # Indonesia
        ("Asia/Jakarta",      "WIB — Jakarta, Sumatera, Jawa, Kalbar (UTC+7)"),
        ("Asia/Makassar",     "WITA — Bali, NTB, Sulawesi, Kalteng (UTC+8)"),
        ("Asia/Jayapura",     "WIT — Papua, Maluku (UTC+9)"),
        # Southeast Asia
        ("Asia/Singapore",    "SGT — Singapore (UTC+8)"),
        ("Asia/Kuala_Lumpur", "MYT — Kuala Lumpur, Malaysia (UTC+8)"),
        ("Asia/Bangkok",      "ICT — Bangkok, Thailand (UTC+7)"),
        ("Asia/Manila",       "PHT — Manila, Philippines (UTC+8)"),
        ("Asia/Ho_Chi_Minh",  "ICT — Ho Chi Minh, Vietnam (UTC+7)"),
        # East Asia
        ("Asia/Hong_Kong",    "HKT — Hong Kong (UTC+8)"),
        ("Asia/Shanghai",     "CST — Shanghai, Beijing, China (UTC+8)"),
        ("Asia/Taipei",       "CST — Taipei, Taiwan (UTC+8)"),
        ("Asia/Tokyo",        "JST — Tokyo, Japan (UTC+9)"),
        ("Asia/Seoul",        "KST — Seoul, South Korea (UTC+9)"),
        # Other
        ("UTC",               "UTC — Coordinated Universal Time (UTC+0)"),
        ("Asia/Dubai",        "GST — Dubai, UAE (UTC+4)"),
        ("Europe/London",     "GMT/BST — London, UK (UTC+0/+1)"),
    ]
    return render(request, 'core/profile.html', {
        'profile_user': user,
        'timezone_choices': timezone_choices,
        'notif_pref': notif_pref,
        'is_sso_user': user_has_sso_account(user),
        'teams_personal_available': _teams_personal_available(user),
        'whatsapp_notifications_available': _whatsapp_notifications_available(),
    })


def _whatsapp_notifications_available():
    """Personal WhatsApp notifications are allowed by the admin and the gateway is on."""
    return settings.WA_ENABLED and SiteConfig.get_solo().allow_whatsapp_notifications


def _teams_personal_available(user):
    """Personal Teams notifications are switched on and this user's email is in the tenant."""
    from gateways.teams_notify import allowed_teams_email, personal_enabled
    try:
        return personal_enabled() and allowed_teams_email(user.email)
    except Exception:
        return False


def help_and_about(request):
    """Help & About page — accessible by anyone."""
    config = SiteConfig.get_solo()
    email = (config.developer_contact_email or "").strip()
    if email:
        # A dedicated contact is configured — show only that one.
        name = (config.developer_contact_name or "").strip() or "IT Support"
        parts = name.split()
        if len(parts) >= 2:
            initials = (parts[0][0] + parts[-1][0]).upper()
        else:
            initials = (name[:3] if len(name) >= 3 else name[:2] or "IT").upper()
        developer_contacts = [{"username": name, "email": email, "initials": initials}]
    else:
        developer_contacts = User.objects.filter(
            role_access=User.RoleAccess.SUPERADMIN, is_active=True
        ).values("username", "email", "initials")
    return render(request, "help_and_about.html", {
        "config": config,
        "django_version": django.get_version(),
        "feedback_types": Feedback.FeedbackType.choices,
        "superadmins": developer_contacts,
    })


def _send_feedback_to_it_support(request, feedback_type, subject, message_text):
    import logging
    from django.core.mail import EmailMultiAlternatives
    from .models import EmailConfig
    from .utils.email_branding import attach_logo, render_branded_from_template

    try:
        config = SiteConfig.get_solo()
        to_email = (config.developer_contact_email or "").strip()
        if not to_email:
            # No dedicated contact configured: feedback is only stored, as before.
            return
        site_name = config.site_name or "Support Desk"
        sender_name = request.user.get_full_name() or request.user.username
        sender_email = (request.user.email or "").strip()
        email_config = EmailConfig.get_solo()
        from_email = (
            email_config.default_from_email
            or email_config.smtp_user
            or getattr(settings, "DEFAULT_FROM_EMAIL", "")
            or "noreply@rocdesk.local"
        )
        html_body = render_branded_from_template(
            "emails/bodies/feedback.html",
            {
                "sender_name": sender_name,
                "sender_email": sender_email or "—",
                "feedback_type": feedback_type,
                "subject": subject,
                "message_text": message_text,
                "site_name": site_name,
            },
            site_name=site_name,
            title="New user feedback",
            eyebrow=f"{site_name} · Help & About",
            footer_note="Submitted from Help & About. Reply to this email to reach the sender.",
        )
        mail = EmailMultiAlternatives(
            subject=f"[{site_name}] Feedback: {subject}",
            body=(
                f"New feedback from {sender_name} ({sender_email or 'no email'}).\n"
                f"Type: {feedback_type}\n"
                f"Subject: {subject}\n\n"
                f"{message_text}"
            ),
            from_email=from_email,
            to=[to_email],
            reply_to=[sender_email] if sender_email else None,
        )
        mail.attach_alternative(html_body, "text/html")
        attach_logo(mail)
        mail.send(fail_silently=True)
    except Exception:
        logging.getLogger(__name__).exception("Failed to email feedback to IT Support")


@login_required
@require_POST
def submit_feedback(request):
    """Handle feedback form submission."""
    feedback_type = request.POST.get("feedback_type", "Other").strip()
    subject = request.POST.get("subject", "").strip()
    message_text = request.POST.get("message", "").strip()

    if not subject or not message_text:
        messages.error(request, "Subject and message are required.")
        return redirect("help_about")

    Feedback.objects.create(
        user=request.user,
        feedback_type=feedback_type,
        subject=subject,
        message=message_text,
        created_by=request.user,
        updated_by=request.user,
    )
    _send_feedback_to_it_support(request, feedback_type, subject, message_text)
    messages.success(request, "Thank you! Your feedback has been submitted.")
    return redirect("help_about")


# =====================================================================
# Documentation Pages
# =====================================================================

def docs_portal_user(request):
    """Public documentation page for Portal Users — no login required."""
    config = SiteConfig.get_solo()
    return render(request, "docs/portal_user_docs.html", {"config": config})


@login_required
def docs_support_desk(request):
    """Documentation for Support Desk staff (SupportDesk, Manager, Auditor, SuperAdmin)."""
    STAFF_ROLES = {
        User.RoleAccess.SUPERADMIN,
        User.RoleAccess.MANAGER,
        User.RoleAccess.SUPPORTDESK,
        User.RoleAccess.ENGINEER,
        User.RoleAccess.AUDITOR,
    }
    if getattr(request.user, "role_access", None) not in STAFF_ROLES:
        return HttpResponseForbidden("Access denied. This page is for staff only.")
    config = SiteConfig.get_solo()
    return render(request, "docs/support_docs.html", {"config": config})


@login_required
def docs_superadmin(request):
    """Documentation for SuperAdmin only."""
    if getattr(request.user, "role_access", None) != User.RoleAccess.SUPERADMIN:
        return HttpResponseForbidden("Access denied. This page is for SuperAdmin only.")
    config = SiteConfig.get_solo()
    return render(request, "docs/admin_docs.html", {"config": config})


# =====================================================================
# Company Units Management (SuperAdmin only)
# =====================================================================

from django.urls import reverse_lazy
from django.contrib.auth.mixins import UserPassesTestMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django import forms
from .models import CompanyUnit

class SuperAdminRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.role_access == 'SuperAdmin'

class CompanyUnitForm(forms.ModelForm):
    class Meta:
        model = CompanyUnit
        fields = ['parent', 'name', 'code', 'address', 'city', 'province', 'latitude', 'longitude']
        widgets = {
            'parent':    forms.Select(attrs={'class': 'jk-input'}),
            'name':      forms.TextInput(attrs={'class': 'jk-input', 'placeholder': 'e.g. Information Technology'}),
            'code':      forms.TextInput(attrs={'class': 'jk-input', 'placeholder': 'e.g. IT'}),
            'address':   forms.TextInput(attrs={'class': 'jk-input', 'placeholder': 'Jl. Sudirman No. 1, Jakarta Pusat', 'autocomplete': 'off'}),
            'city':      forms.TextInput(attrs={'class': 'jk-input', 'placeholder': 'e.g. Jakarta'}),
            'province':  forms.TextInput(attrs={'class': 'jk-input', 'placeholder': 'e.g. DKI Jakarta'}),
            'latitude':  forms.NumberInput(attrs={'class': 'jk-input', 'step': 'any', 'placeholder': '-6.2088000', 'id': 'id_latitude'}),
            'longitude': forms.NumberInput(attrs={'class': 'jk-input', 'step': 'any', 'placeholder': '106.8456000', 'id': 'id_longitude'}),
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        super().__init__(*args, **kwargs)
        self.fields['parent'].required = False
        self.fields['parent'].empty_label = '— No parent (top-level unit) —'
        # Exclude self from parent choices to prevent circular reference
        if instance and instance.pk:
            self.fields['parent'].queryset = CompanyUnit.objects.exclude(pk=instance.pk)

class CompanyUnitListView(FeatureRequiredMixin, SuperAdminRequiredMixin, PageSizeMixin, ListView):
    feature_required = 'company_units'
    model = CompanyUnit
    template_name = 'core/company_unit_list.html'
    context_object_name = 'company_units'
    paginate_by = LIST_PAGE_SIZE

    def get_filterable_fields(self):
        return {
            "parent": {
                "label": "Parent Unit", "type": "choice", "lookup": "parent_id",
                "choices": [
                    (str(u.pk), f"{u.code} — {u.name}")
                    for u in CompanyUnit.objects.filter(parent__isnull=True).order_by('code')
                ],
            },
            "is_archived": {
                "label": "Status", "type": "choice", "lookup": "is_archived",
                "choices": [("false", "Active"), ("true", "Archived")],
                "python_type": "bool",
            },
        }

    def get_queryset(self):
        from core.dynamic_filters import apply_dynamic_filters

        qs = CompanyUnit.objects.select_related('parent').order_by('code')
        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(name__icontains=q) | qs.filter(code__icontains=q)
        qs, self.active_filters = apply_dynamic_filters(qs, self.request, self.get_filterable_fields())
        return qs

    def get_context_data(self, **kwargs):
        from core.dynamic_filters import active_filters_json, fields_json

        context = super().get_context_data(**kwargs)
        fields = self.get_filterable_fields()
        context['active_filters'] = self.active_filters
        context['active_filters_json'] = active_filters_json(self.active_filters)
        context['dynamic_fields_json'] = fields_json(fields)
        context['has_active_filters'] = bool(self.active_filters)
        context['viewing_archived_only'] = any(
            af["field_key"] == "is_archived" and af["value"] == "true" for af in self.active_filters
        )
        return context

class CompanyUnitCreateView(FeatureRequiredMixin, SuperAdminRequiredMixin, SuccessMessageMixin, CreateView):
    feature_required = 'company_units'
    model = CompanyUnit
    form_class = CompanyUnitForm
    template_name = 'core/company_unit_form.html'
    success_url = reverse_lazy('desk:company_unit_list')
    success_message = "Unit created successfully"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        return super().form_valid(form)

class CompanyUnitUpdateView(FeatureRequiredMixin, SuperAdminRequiredMixin, SuccessMessageMixin, UpdateView):
    feature_required = 'company_units'
    model = CompanyUnit
    form_class = CompanyUnitForm
    template_name = 'core/company_unit_form.html'
    success_url = reverse_lazy('desk:company_unit_list')
    success_message = "Unit updated successfully"

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        return super().form_valid(form)

class CompanyUnitDeleteView(FeatureRequiredMixin, SuperAdminRequiredMixin, DeleteView):
    feature_required = 'company_units'
    model = CompanyUnit
    template_name = 'core/company_unit_confirm_delete.html'
    success_url = reverse_lazy('desk:company_unit_list')

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, "Unit deleted successfully")
        return super().delete(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Company Units — standardized bulk actions
# ---------------------------------------------------------------------------
from django.views.decorators.http import require_POST as _require_POST_cu
from .list_actions import get_selected as _get_selected_cu, bulk_set_archived as _bulk_set_archived_cu, bulk_delete as _bulk_delete_cu
from .excel_utils import export_queryset_xlsx as _export_queryset_xlsx_cu


def _superadmin_only(u):
    return u.is_authenticated and u.role_access == 'SuperAdmin'


@login_required
@user_passes_test(_superadmin_only)
@_require_POST_cu
def company_unit_bulk_archive(request):
    qs = _get_selected_cu(request, CompanyUnit.objects.all())
    count = _bulk_set_archived_cu(qs, field_name="is_archived", value=True)
    messages.success(request, f"Archived {count} entity(ies).")
    return HttpResponseRedirect(reverse_lazy('desk:company_unit_list'))


@login_required
@user_passes_test(_superadmin_only)
@_require_POST_cu
def company_unit_bulk_unarchive(request):
    qs = _get_selected_cu(request, CompanyUnit.objects.all())
    count = _bulk_set_archived_cu(qs, field_name="is_archived", value=False)
    messages.success(request, f"Unarchived {count} entity(ies).")
    return HttpResponseRedirect(reverse_lazy('desk:company_unit_list'))


@login_required
@user_passes_test(_superadmin_only)
@_require_POST_cu
def company_unit_bulk_delete(request):
    qs = _get_selected_cu(request, CompanyUnit.objects.all())
    count = _bulk_delete_cu(qs)
    messages.success(request, f"Deleted {count} entity(ies).")
    return HttpResponseRedirect(reverse_lazy('desk:company_unit_list'))


@login_required
@user_passes_test(_superadmin_only)
@_require_POST_cu
def company_unit_bulk_duplicate(request):
    ids = request.POST.getlist("selected_ids")
    if len(ids) != 1:
        messages.warning(request, "Select exactly one entity to duplicate.")
        return HttpResponseRedirect(reverse_lazy('desk:company_unit_list'))
    original = get_object_or_404(CompanyUnit, pk=ids[0])
    base_code = f"{original.code}-COPY"
    new_code = base_code
    suffix = 2
    while CompanyUnit.objects.filter(code=new_code).exists():
        new_code = f"{base_code}{suffix}"
        suffix += 1
    CompanyUnit.objects.create(
        parent=original.parent,
        name=f"{original.name} (copy)",
        code=new_code,
        address=original.address,
        city=original.city,
        province=original.province,
        latitude=original.latitude,
        longitude=original.longitude,
        created_by=request.user,
        updated_by=request.user,
    )
    messages.success(request, f'Duplicated "{original.name}" as "{new_code}".')
    return HttpResponseRedirect(reverse_lazy('desk:company_unit_list'))


@login_required
@user_passes_test(_superadmin_only)
@_require_POST_cu
def company_unit_bulk_export(request):
    qs = _get_selected_cu(request, CompanyUnit.objects.select_related('parent'))
    if not qs.exists():
        qs = CompanyUnit.objects.select_related('parent').all()
    columns = [
        ("Code", lambda o: o.code),
        ("Name", lambda o: o.name),
        ("Parent Unit", lambda o: o.parent.code if o.parent else ""),
        ("Geocoded", lambda o: "Yes" if (o.latitude and o.longitude) else "No"),
        ("Archived", lambda o: "Yes" if o.is_archived else "No"),
    ]
    return _export_queryset_xlsx_cu(qs.order_by('code'), columns, "company_units_export.xlsx", sheet_title="Company Units")


# =====================================================================
# Job Role Master Data (SuperAdmin / Manager only)
# =====================================================================

from .models import JobRole
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect


class ManagerOrSuperAdminMixin(UserPassesTestMixin):
    def test_func(self):
        return (
            self.request.user.is_authenticated
            and self.request.user.role_access in ('SuperAdmin', 'Manager')
        )


class JobRoleForm(forms.ModelForm):
    class Meta:
        model = JobRole
        fields = ['name', 'order', 'is_active']
        widgets = {
            'name':  forms.TextInput(attrs={'class': 'jk-input', 'placeholder': 'e.g. Staff IT'}),
            'order': forms.NumberInput(attrs={'class': 'jk-input', 'min': '0'}),
        }


class JobRoleListView(ManagerOrSuperAdminMixin, PageSizeMixin, ListView):
    model = JobRole
    template_name = 'core/job_role_list.html'
    context_object_name = 'job_roles'
    paginate_by = LIST_PAGE_SIZE

    JOB_ROLE_FILTERABLE_FIELDS = {
        "is_active": {
            "label": "Status", "type": "choice", "lookup": "is_active",
            "choices": [("true", "Active"), ("false", "Inactive")],
            "python_type": "bool",
        },
    }

    def get_queryset(self):
        from core.dynamic_filters import apply_dynamic_filters

        qs = JobRole.objects.all()
        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(name__icontains=q)
        qs, self.active_filters = apply_dynamic_filters(qs, self.request, self.JOB_ROLE_FILTERABLE_FIELDS)
        return qs

    def get_context_data(self, **kwargs):
        from core.dynamic_filters import active_filters_json, fields_json

        context = super().get_context_data(**kwargs)
        context['active_filters'] = self.active_filters
        context['active_filters_json'] = active_filters_json(self.active_filters)
        context['dynamic_fields_json'] = fields_json(self.JOB_ROLE_FILTERABLE_FIELDS)
        context['has_active_filters'] = bool(self.active_filters)
        context['viewing_inactive_only'] = any(
            af["field_key"] == "is_active" and af["value"] == "false" for af in self.active_filters
        )
        return context


class JobRoleCreateView(ManagerOrSuperAdminMixin, SuccessMessageMixin, CreateView):
    model = JobRole
    form_class = JobRoleForm
    template_name = 'core/job_role_form.html'
    success_url = reverse_lazy('desk:job_role_list')
    success_message = "Job role created successfully"


class JobRoleUpdateView(ManagerOrSuperAdminMixin, SuccessMessageMixin, UpdateView):
    model = JobRole
    form_class = JobRoleForm
    template_name = 'core/job_role_form.html'
    success_url = reverse_lazy('desk:job_role_list')
    success_message = "Job role updated successfully"


class JobRoleDeleteView(ManagerOrSuperAdminMixin, DeleteView):
    model = JobRole
    template_name = 'core/job_role_confirm_delete.html'
    success_url = reverse_lazy('desk:job_role_list')

    def delete(self, request, *args, **kwargs):
        messages.success(request, "Job role deleted successfully")
        return super().delete(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Job Role — standardized bulk actions (Archive / Unarchive / Delete /
# Duplicate / Export). is_active doubles as the archive flag.
# ---------------------------------------------------------------------------
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import get_object_or_404
from .list_actions import get_selected, bulk_set_archived, bulk_delete
from .excel_utils import export_queryset_xlsx, safe_cell


@login_required
@user_passes_test(lambda u: u.is_authenticated and u.role_access in ('SuperAdmin', 'Manager'))
@require_POST
def job_role_bulk_archive(request):
    qs = get_selected(request, JobRole.objects.all())
    count = bulk_set_archived(qs, field_name="is_active", value=False)
    messages.success(request, f"Deactivated {count} job role(s).")
    return HttpResponseRedirect(reverse_lazy('desk:job_role_list'))


@login_required
@user_passes_test(lambda u: u.is_authenticated and u.role_access in ('SuperAdmin', 'Manager'))
@require_POST
def job_role_bulk_unarchive(request):
    qs = get_selected(request, JobRole.objects.all())
    count = bulk_set_archived(qs, field_name="is_active", value=True)
    messages.success(request, f"Activated {count} job role(s).")
    return HttpResponseRedirect(reverse_lazy('desk:job_role_list'))


@login_required
@user_passes_test(lambda u: u.is_authenticated and u.role_access in ('SuperAdmin', 'Manager'))
@require_POST
def job_role_bulk_delete(request):
    qs = get_selected(request, JobRole.objects.all())
    count = bulk_delete(qs)
    messages.success(request, f"Deleted {count} job role(s).")
    return HttpResponseRedirect(reverse_lazy('desk:job_role_list'))


@login_required
@user_passes_test(lambda u: u.is_authenticated and u.role_access in ('SuperAdmin', 'Manager'))
@require_POST
def job_role_bulk_duplicate(request):
    ids = request.POST.getlist("selected_ids")
    if len(ids) != 1:
        messages.warning(request, "Select exactly one job role to duplicate.")
        return HttpResponseRedirect(reverse_lazy('desk:job_role_list'))
    original = get_object_or_404(JobRole, pk=ids[0])
    base_name = f"{original.name} (copy)"
    new_name = base_name
    suffix = 2
    while JobRole.objects.filter(name=new_name).exists():
        new_name = f"{base_name} {suffix}"
        suffix += 1
    JobRole.objects.create(name=new_name, is_active=original.is_active, order=original.order)
    messages.success(request, f'Duplicated "{original.name}" as "{new_name}".')
    return HttpResponseRedirect(reverse_lazy('desk:job_role_list'))


@login_required
@user_passes_test(lambda u: u.is_authenticated and u.role_access in ('SuperAdmin', 'Manager'))
@require_POST
def job_role_bulk_export(request):
    qs = get_selected(request, JobRole.objects.all())
    if not qs.exists():
        qs = JobRole.objects.all()
    config = SiteConfig.get_solo()
    columns = [
        ("Name", lambda o: safe_cell(o.name)),
        ("Status", lambda o: "Active" if o.is_active else "Inactive"),
        ("Order", lambda o: o.order),
        ("Job Role Input Mode", lambda o: config.job_role_mode),
    ]
    return export_queryset_xlsx(qs.order_by('order', 'name'), columns, "job_roles_export.xlsx", sheet_title="Job Roles")


# =====================================================================
# SSO Pending Whitelist View
# =====================================================================

def sso_pending_view(request):
    """
    Legacy page for inactive SSO accounts that still have a whitelist ticket.
    New SSO users are activated as Portal Users and no longer land here.
    """
    email = request.session.get("sso_pending_email", "")
    name = request.session.get("sso_pending_name", "")
    ticket = request.session.get("sso_pending_ticket", "")

    # If someone navigates here directly without going through SSO, redirect to login
    if not email:
        return redirect("login")

    return render(request, "registration/sso_pending.html", {
        "pending_email": email,
        "pending_name": name,
        "pending_ticket": ticket,
    })



def _absolute_login_url(request) -> str:
    from core.models import SiteConfig

    try:
        site_url = (SiteConfig.get_solo().site_url or "").rstrip("/")
    except Exception:
        site_url = ""
    if site_url:
        return f"{site_url}/auth/login/"
    return request.build_absolute_uri("/auth/login/")


class SSOAwareLogoutView(LogoutView):
    next_page = "/auth/login/"

    def dispatch(self, request, *args, **kwargs):
        self._sso_providers = set()
        if getattr(request.user, "is_authenticated", False):
            try:
                from allauth.socialaccount.models import SocialAccount

                self._sso_providers = set(
                    SocialAccount.objects.filter(user=request.user).values_list(
                        "provider", flat=True
                    )
                )
            except Exception:
                self._sso_providers = set()
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        login_url = _absolute_login_url(self.request)
        encoded = quote(login_url, safe="")
        if "microsoft" in self._sso_providers:
            try:
                from core.models import SSOConfig
                tenant = SSOConfig.get_solo().microsoft_tenant_id or "organizations"
            except Exception:
                tenant = "organizations"
            return (
                f"https://login.microsoftonline.com/{quote(tenant, safe='')}/oauth2/v2.0/logout"
                f"?post_logout_redirect_uri={encoded}"
            )
        if "google" in self._sso_providers:
            return f"https://accounts.google.com/Logout?continue={encoded}"
        return super().get_success_url()
