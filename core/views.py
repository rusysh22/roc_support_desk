"""
Core App — Views
===================
"""
import random
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from .forms import ForgotPasswordForm, ResetPasswordOTPForm
from .models import OTPToken

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
            user = User.objects.get(email=email)
            
            # Generate a random 6-digit OTP
            otp_code = f"{random.randint(100000, 999999)}"
            
            # Invalidate all previous OTPs for this user
            OTPToken.objects.filter(user=user, is_used=False).update(is_used=True)
            
            # Save new OTP
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
                
            # Store the email in session for the next step so the user doesn't have to re-enter it internally
            request.session['reset_email'] = user.email
            
            messages.success(request, "An OTP has been sent to your email address.")
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
                
            # Check for a valid OTP
            token_obj = OTPToken.objects.filter(user=user, token=otp, is_used=False).order_by('-created_at').first()
            
            if not token_obj or not token_obj.is_valid():
                form.add_error("otp", "Invalid or expired OTP code.")
                return render(request, self.template_name, {"form": form, "email": email})
                
            # Mark OTP as used
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


def custom_404_view(request, exception=None):
    """
    Custom 404 Error Handler to display a branded, modern animated
    not found page instead of Django's default.
    """
    return render(request, "404.html", status=404)


# =====================================================================
# Help & About
# =====================================================================

from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST

from .models import Feedback, SiteConfig
import django


def help_and_about(request):
    """Help & About page — accessible by anyone."""
    config = SiteConfig.get_solo()
    return render(request, "help_and_about.html", {
        "config": config,
        "django_version": django.get_version(),
        "feedback_types": Feedback.FeedbackType.choices,
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
