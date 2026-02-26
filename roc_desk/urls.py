"""
RoC Desk — Root URL Configuration
====================================
"""
from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Django admin
    path("admin/", admin.site.urls),

    # Authentication
    path("auth/", include("django.contrib.auth.urls")),

    # Client portal (public)
    path("", include("cases.urls", namespace="cases")),

    # Admin / Support desk
    path("desk/", include("cases.urls_desk", namespace="desk")),

    # Evolution API webhook
    path("api/gateways/", include("gateways.urls", namespace="gateways")),

    # Knowledge base
    path("kb/", include("knowledge_base.urls", namespace="knowledge_base")),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
