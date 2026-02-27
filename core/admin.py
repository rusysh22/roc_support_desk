"""
Core App — Django Admin Registration
======================================
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import CompanyUnit, Employee, User


# =====================================================================
# Custom User Admin
# =====================================================================

@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    """Admin configuration for the custom User model."""
    
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    list_display = (
        "login_username",
        "username",
        "email",
        "nik",
        "role_access",
        "initials",
        "is_staff",
        "is_active",
    )
    list_filter = ("role_access", "is_staff", "is_active")
    search_fields = ("login_username", "username", "email", "nik", "initials")
    ordering = ("login_username",)

    fieldsets = (
        (None, {"fields": ("login_username", "password")}),
        (
            "Personal Info",
            {"fields": ("username", "email", "nik", "role_access", "initials")},
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        ("Important Dates", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "login_username",
                    "username",
                    "email",
                    "nik",
                    "role_access",
                    "initials",
                    "password1",
                    "password2",
                ),
            },
        ),
    )


# =====================================================================
# Company Unit Admin
# =====================================================================

@admin.register(CompanyUnit)
class CompanyUnitAdmin(ModelAdmin):
    """Admin configuration for CompanyUnit."""

    list_display = ("code", "name", "created_at", "updated_at")
    search_fields = ("name", "code")
    ordering = ("code",)
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")

    def save_model(self, request, obj, form, change):
        """Auto-populate audit fields on save."""
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# =====================================================================
# Employee Admin
# =====================================================================

@admin.register(Employee)
class EmployeeAdmin(ModelAdmin):
    """Admin configuration for Employee."""

    list_display = (
        "full_name",
        "email",
        "phone_number",
        "job_role",
        "unit",
        "created_at",
    )
    list_filter = ("unit",)
    search_fields = ("full_name", "email", "phone_number")
    ordering = ("full_name",)
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")
    autocomplete_fields = ("unit",)

    def save_model(self, request, obj, form, change):
        """Auto-populate audit fields on save."""
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
