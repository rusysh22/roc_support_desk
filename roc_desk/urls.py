"""
RoC Desk — Root URL Configuration
====================================
"""
from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views

from core import views as core_views
from core.auth_forms import CustomAuthenticationForm

from core.models import SiteConfig
from django.utils.functional import lazy

def get_admin_site_name():
    try:
        from django.db import connection
        if 'core_siteconfig' in connection.introspection.table_names():
            name = SiteConfig.get_solo().site_name
            return name if name else "RoC Desk Admin"
        return "RoC Desk Admin"
    except Exception:
        return "RoC Desk Admin"

admin.site.site_header = lazy(get_admin_site_name, str)()
admin.site.site_title = lazy(get_admin_site_name, str)()
admin.site.index_title = "Administration"

urlpatterns = [
    # Override Admin logout to redirect to public login
    path("admin/logout/", auth_views.LogoutView.as_view(next_page="/auth/login/")),
    # Django admin
    path("admin/", admin.site.urls),

    # Authentication
    path("auth/request-account/", core_views.RequestAccountView.as_view(), name="request_account"),
    path("auth/forgot-password/", core_views.ForgotPasswordView.as_view(), name="forgot_password"),
    path("auth/reset-password/", core_views.ResetPasswordOTPView.as_view(), name="reset_password_otp"),
    path("auth/change-password/", core_views.ForceChangePasswordView.as_view(), name="force_change_password"),
    path("auth/login/", auth_views.LoginView.as_view(authentication_form=CustomAuthenticationForm, redirect_authenticated_user=True), name="login"),
    path("auth/", include("django.contrib.auth.urls")),

    # Help & About (any logged-in user)
    path("help/", core_views.help_and_about, name="help_about"),
    path("help/feedback/", core_views.submit_feedback, name="submit_feedback"),

    # User profile
    path("desk/profile/", core_views.profile_view, name="profile"),

    # Documentation pages
    path("docs/", core_views.docs_portal_user, name="docs_portal"),
    path("supportdocs/", core_views.docs_support_desk, name="docs_support"),
    path("superadmindocs/", core_views.docs_superadmin, name="docs_admin"),

    # Client portal (public)
    path("", include("cases.urls", namespace="cases")),

    # Admin / Support desk
    path("desk/", include("cases.urls_desk", namespace="desk")),
    path("desk/links/", include("links.urls_desk", namespace="links_desk")),
    path("desk/kb/", include("knowledge_base.urls_desk", namespace="kb_desk")),
    path("desk/users/", include("core.urls_desk", namespace="users_desk")),

    # Short Links Public Redirect
    path("s/", include("links.urls", namespace="links")),

    # Evolution API webhook
    path("api/gateways/", include("gateways.urls", namespace="gateways")),

    # Knowledge base
    path("kb/", include("knowledge_base.urls", namespace="knowledge_base")),

    # License management
    path("license/", include("licensing.urls", namespace="licensing")),
]

# Custom Error Handlers
handler404 = 'core.views.custom_404_view'
handler403 = 'core.views.custom_csrf_failure_view'

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # Add a route to test the 404 page directly while in DEBUG mode
    urlpatterns += [
        path('404/', core_views.custom_404_view, name='test_404'),
        path('403/', core_views.custom_csrf_failure_view, name='test_403'),
    ]
