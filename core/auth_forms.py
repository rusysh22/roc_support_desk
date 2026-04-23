from datetime import timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.utils import timezone

User = get_user_model()

MAX_ATTEMPTS = 5
ATTEMPT_WINDOW = timedelta(hours=1)


class CustomAuthenticationForm(AuthenticationForm):
    # Honeypot field — must be left empty by human users
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'style': 'display: none !important; opacity: 0; position: absolute; top: -9999px; left: -9999px;',
            'autocomplete': 'off',
            'tabindex': '-1',
        }),
        label="Website",
    )

    def clean(self):
        # Anti-bot: honeypot filled → reject silently
        if self.cleaned_data.get('website'):
            raise forms.ValidationError(
                "Anti-bot verification failed. Please try again.",
                code="bot_detected",
            )

        username = self.cleaned_data.get('username')

        if username:
            # Check if account is locked (unusable password)
            try:
                user_obj = User.objects.get(login_username=username)
                if not user_obj.has_usable_password():
                    raise forms.ValidationError(
                        "This account is locked due to too many failed login attempts. "
                        "Please use the 'Forgot Password' feature to reset your password.",
                        code="account_locked",
                    )
            except User.DoesNotExist:
                pass

        # Attempt standard authentication
        try:
            cleaned_data = super().clean()

            # Success — record it and return
            if username:
                self._record_attempt(username, success=True)
                self._audit(username, success=True)
            return cleaned_data

        except forms.ValidationError as e:
            if e.code == 'invalid_login' and username:
                from .models import LoginAttempt
                ip = self._get_ip()
                LoginAttempt.objects.create(
                    login_username=username,
                    ip_address=ip,
                    success=False,
                )
                self._audit(username, success=False)

                # Count recent failures within window
                window_start = timezone.now() - ATTEMPT_WINDOW
                recent_failures = LoginAttempt.objects.filter(
                    login_username=username,
                    attempted_at__gte=window_start,
                    success=False,
                ).count()

                if recent_failures >= MAX_ATTEMPTS:
                    try:
                        user_obj = User.objects.get(login_username=username)
                        user_obj.set_unusable_password()
                        user_obj.save(update_fields=['password'])
                        raise forms.ValidationError(
                            "You have failed to log in 5 times. For security, your account has been "
                            "temporarily locked. Please use the 'Forgot Password' feature to reset via OTP.",
                            code="account_locked_just_now",
                        )
                    except User.DoesNotExist:
                        pass
                else:
                    remaining = MAX_ATTEMPTS - recent_failures
                    raise forms.ValidationError(
                        f"Invalid username or password. Warning: You have {remaining} attempt(s) "
                        f"remaining before your account is locked.",
                        code="invalid_login_warning",
                    )
            raise e

    def _record_attempt(self, username, success):
        from .models import LoginAttempt
        LoginAttempt.objects.create(
            login_username=username,
            ip_address=self._get_ip(),
            success=success,
        )

    def _get_ip(self):
        request = self.request if hasattr(self, 'request') else None
        if request is None:
            return None
        from ipware import get_client_ip
        ip, _ = get_client_ip(request)
        return ip

    def _audit(self, username: str, success: bool):
        from .models import AuditLog
        request = self.request if hasattr(self, 'request') else None
        action = AuditLog.Action.LOGIN_SUCCESS if success else AuditLog.Action.LOGIN_FAIL
        AuditLog.log(
            action,
            request=request,
            ip_address=self._get_ip() or "",
            details={"login_username": username},
        )
