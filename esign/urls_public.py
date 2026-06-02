"""E-Signature — Public URL patterns (namespace: esign_public)."""
from django.urls import path

from .views_public import sign

app_name = "esign_public"

urlpatterns = [
    path("<uuid:token>/", sign, name="sign"),
]
