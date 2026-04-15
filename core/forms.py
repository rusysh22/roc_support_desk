"""
Core App — Forms
====================
"""
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

User = get_user_model()


class ForgotPasswordForm(forms.Form):
    """
    Form for requesting a password reset OTP.
    Validates that the email exists in the system.
    """
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            "class": "jk-input",
            "placeholder": "Enter your registered email address",
            "autofocus": True,
        })
    )

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if not User.objects.filter(email=email).exists():
            raise ValidationError("We could not find an account with that email address.")
        return email


class ResetPasswordOTPForm(forms.Form):
    """
    Form for validating the OTP and resetting the password.
    """
    otp = forms.CharField(
        max_length=6,
        required=True,
        widget=forms.TextInput(attrs={
            "class": "jk-input text-center text-xl tracking-widest font-mono",
            "placeholder": "000000",
            "autofocus": True,
        }),
        label="6-Digit OTP"
    )
    new_password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={
            "class": "jk-input",
            "placeholder": "Enter new password",
        }),
        label="New Password"
    )
    confirm_password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={
            "class": "jk-input",
            "placeholder": "Confirm new password",
        }),
        label="Confirm Password"
    )

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get("new_password")
        confirm_password = cleaned_data.get("confirm_password")

        if new_password and confirm_password:
            if new_password != confirm_password:
                self.add_error("confirm_password", "Passwords do not match.")

        return cleaned_data


class EmailConfigForm(forms.ModelForm):
    """
    Form for updating global Email Settings in the Support Desk admin.
    """
    class Meta:
        from .models import EmailConfig
        model = EmailConfig
        exclude = ["id", "created_at", "updated_at", "created_by", "updated_by"]
        widgets = {
            "imap_host": forms.TextInput(attrs={"class": "jk-input"}),
            "imap_port": forms.NumberInput(attrs={"class": "jk-input"}),
            "imap_user": forms.TextInput(attrs={"class": "jk-input"}),
            "imap_password": forms.PasswordInput(
                attrs={"class": "jk-input", "placeholder": "Leave blank to keep unchanged"}, 
                render_value=False
            ),
            
            "smtp_host": forms.TextInput(attrs={"class": "jk-input"}),
            "smtp_port": forms.NumberInput(attrs={"class": "jk-input"}),
            "smtp_user": forms.TextInput(attrs={"class": "jk-input"}),
            "smtp_password": forms.PasswordInput(
                attrs={"class": "jk-input", "placeholder": "Leave blank to keep unchanged"}, 
                render_value=False
            ),
            "smtp_use_tls": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
            "smtp_use_ssl": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
            "default_from_email": forms.TextInput(attrs={"class": "jk-input"}),
        }

    def clean_imap_password(self):
        new_password = self.cleaned_data.get('imap_password')
        if not new_password and self.instance and self.instance.pk:
            # If the field is left blank in the form, retain the original password
            return self.instance.imap_password
        return new_password

    def clean_smtp_password(self):
        new_password = self.cleaned_data.get('smtp_password')
        if not new_password and self.instance and self.instance.pk:
            # If the field is left blank in the form, retain the original password
            return self.instance.smtp_password
        return new_password

class DynamicFormForm(forms.ModelForm):
    """
    Form for creating/editing the main settings of a DynamicForm.
    """
    slug = forms.SlugField(required=False, widget=forms.TextInput(attrs={"class": "jk-input"}))

    def clean_slug(self):
        slug = self.cleaned_data.get("slug")
        if slug:
            from .models import DynamicForm
            qs = DynamicForm.objects.filter(slug=slug)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("A form with this slug already exists. Please choose a different one.")
        return slug

    class Meta:
        from .models import DynamicForm
        model = DynamicForm
        exclude = ["id", "created_at", "updated_at", "created_by", "updated_by"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "jk-input", "maxlength": "255"}),
            "description": forms.Textarea(attrs={"class": "jk-textarea", "rows": 3, "maxlength": "10000"}),
            "success_message": forms.Textarea(attrs={"class": "jk-textarea", "rows": 3, "maxlength": "5000"}),
            "background_color": forms.TextInput(attrs={"class": "w-10 h-10 p-0 border-0 rounded shadow-sm cursor-pointer", "type": "color"}),
            "background_image": forms.ClearableFileInput(attrs={"class": "jk-file-input"}),
            "header_image": forms.ClearableFileInput(attrs={"class": "jk-file-input"}),
            "is_published": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
            "requires_login": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
            "show_on_portal": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
            "collect_user": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
            "collect_company": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
        }

class UserAdminForm(forms.ModelForm):
    """
    Form for creating/editing users in the User Management section.
    """
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            "class": "jk-input",
            "placeholder": "Leave blank to keep current, or type a new one",
        }),
        help_text="For new users, if left blank, it will default to 'RoCDesk123!'."
    )

    class Meta:
        model = User
        fields = [
            "login_username", "username", "email", "nik", "role_access", 
            "initials", "can_handle_confidential"
        ]
        widgets = {
            "login_username": forms.TextInput(attrs={"class": "jk-input"}),
            "username": forms.TextInput(attrs={"class": "jk-input"}),
            "email": forms.EmailInput(attrs={"class": "jk-input"}),
            "nik": forms.TextInput(attrs={"class": "jk-input"}),
            "role_access": forms.Select(attrs={"class": "jk-input"}),
            "initials": forms.TextInput(attrs={"class": "jk-input", "maxlength": "5"}),
            "can_handle_confidential": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        
        if password:
            user.set_password(password)
        elif not user.pk:
            # If creating a new user and no password provided, set default
            user.set_password("RoCDesk123!")
            
        if commit:
            user.save()
        return user
