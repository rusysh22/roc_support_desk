"""
licensing/urls.py
=================
URL patterns for the /license/ prefix.
"""
from django.urls import path

from . import views

app_name = "licensing"

urlpatterns = [
    # Manual activation — SuperAdmin enters key when webhook was missed
    path("activate/",    views.activate_license,  name="activate"),

    # Start / reset trial mode
    path("start-trial/", views.start_trial,        name="start_trial"),

    # Deactivate — SuperAdmin clears the license record
    path("deactivate/",  views.deactivate_license, name="deactivate"),

    # Status dashboard — SuperAdmin only
    path("status/",      views.license_status,    name="status"),
    path("verify-now/",  views.verify_license_now, name="verify_now"),

    # License state pages — redirected to by middleware (Phase 3)
    path("expired/",     views.license_expired,   name="expired"),
    path("suspended/",   views.license_suspended, name="suspended"),
    path("upgrade/",     views.license_upgrade,   name="upgrade"),
]
