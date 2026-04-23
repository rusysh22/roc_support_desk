"""
Core App — Views
===================
"""
import secrets
from django.conf import settings
from ipware import get_client_ip as _get_client_ip
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from .forms import ForgotPasswordForm, ResetPasswordOTPForm
from .models import OTPToken

from licensing.decorators import FeatureRequiredMixin

User = get_user_model()


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
        if not getattr(request.user, "must_change_password", False):
            return redirect(settings.LOGIN_REDIRECT_URL)
        return render(request, self.template_name)

    def post(self, request):
        if not request.user.is_authenticated:
            return redirect(reverse("login"))
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
            status=CaseRecord.Status.OPEN,
            has_unread_messages=True,
        )
        Message.objects.create(
            case=case,
            body=description,
            direction=Message.Direction.INBOUND,
            channel=Message.Channel.WEB,
        )

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

from django.contrib.auth.decorators import login_required
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
    user = request.user

    if request.method == 'POST':
        action = request.POST.get('action', 'profile')

        if action == 'profile':
            user.username     = request.POST.get('display_name', user.username).strip()
            user.phone_number = request.POST.get('phone_number', '').strip()
            user.save(update_fields=['username', 'phone_number'])
            messages.success(request, "Profile updated successfully.")
            return redirect('profile')

        elif action == 'password':
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

    return render(request, 'core/profile.html', {'profile_user': user})


def help_and_about(request):
    """Help & About page — accessible by anyone."""
    config = SiteConfig.get_solo()
    superadmins = User.objects.filter(
        role_access=User.RoleAccess.SUPERADMIN, is_active=True
    ).values("username", "email", "initials")
    return render(request, "help_and_about.html", {
        "config": config,
        "django_version": django.get_version(),
        "feedback_types": Feedback.FeedbackType.choices,
        "superadmins": superadmins,
    })


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
        User.RoleAccess.AUDITOR,
    }
    if getattr(request.user, "role_access", None) not in STAFF_ROLES:
        return HttpResponseForbidden("Akses ditolak. Halaman ini hanya untuk staf.")
    config = SiteConfig.get_solo()
    return render(request, "docs/support_docs.html", {"config": config})


@login_required
def docs_superadmin(request):
    """Documentation for SuperAdmin only."""
    if getattr(request.user, "role_access", None) != User.RoleAccess.SUPERADMIN:
        return HttpResponseForbidden("Akses ditolak. Halaman ini hanya untuk SuperAdmin.")
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
        fields = ['name', 'code']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'jk-input', 'placeholder': 'e.g. Information Technology'}),
            'code': forms.TextInput(attrs={'class': 'jk-input', 'placeholder': 'e.g. IT'}),
        }

class CompanyUnitListView(FeatureRequiredMixin, SuperAdminRequiredMixin, ListView):
    feature_required = 'company_units'
    model = CompanyUnit
    template_name = 'core/company_unit_list.html'
    context_object_name = 'company_units'
    paginate_by = 50
    
    def get_queryset(self):
        qs = CompanyUnit.objects.all().order_by('code')
        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(name__icontains=q) | qs.filter(code__icontains=q)
        return qs

class CompanyUnitCreateView(FeatureRequiredMixin, SuperAdminRequiredMixin, SuccessMessageMixin, CreateView):
    feature_required = 'company_units'
    model = CompanyUnit
    form_class = CompanyUnitForm
    template_name = 'core/company_unit_form.html'
    success_url = reverse_lazy('desk:company_unit_list')
    success_message = "Company unit created successfully"

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
    success_message = "Company unit updated successfully"

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        return super().form_valid(form)

class CompanyUnitDeleteView(FeatureRequiredMixin, SuperAdminRequiredMixin, DeleteView):
    feature_required = 'company_units'
    model = CompanyUnit
    template_name = 'core/company_unit_confirm_delete.html'
    success_url = reverse_lazy('desk:company_unit_list')
    
    def delete(self, request, *args, **kwargs):
        messages.success(self.request, "Company unit deleted successfully")
        return super().delete(request, *args, **kwargs)
