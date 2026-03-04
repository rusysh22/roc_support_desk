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
    path("auth/forgot-password/", core_views.ForgotPasswordView.as_view(), name="forgot_password"),
    path("auth/reset-password/", core_views.ResetPasswordOTPView.as_view(), name="reset_password_otp"),
    path("auth/", include("django.contrib.auth.urls")),

    # Client portal (public)
    path("", include("cases.urls", namespace="cases")),

    # Admin / Support desk
    path("desk/", include("cases.urls_desk", namespace="desk")),
    path("desk/links/", include("links.urls_desk", namespace="links_desk")),

    # Short Links Public Redirect
    path("s/", include("links.urls", namespace="links")),

    # Evolution API webhook
    path("api/gateways/", include("gateways.urls", namespace="gateways")),

    # Knowledge base
    path("kb/", include("knowledge_base.urls", namespace="knowledge_base")),
]

# Custom Error Handlers
handler404 = 'core.views.custom_404_view'

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # Add a route to test the 404 page directly while in DEBUG mode
    urlpatterns += [
        path('404/', core_views.custom_404_view, name='test_404'),
    ]
