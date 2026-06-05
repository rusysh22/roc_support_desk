"""E-Signature — Public URL patterns (namespace: esign_public)."""
from django.urls import path

from .views_public import (
    create_mobile_session,
    mobile_draw,
    mobile_sig_poll,
    saved_signature_api,
    sign,
    document_progress_public,
)

app_name = "esign_public"

urlpatterns = [
    path("<uuid:token>/", sign, name="sign"),
    path("mobile-draw/<uuid:session_id>/", mobile_draw, name="mobile_draw"),
    path("mobile-sig/<uuid:session_id>/", mobile_sig_poll, name="mobile_sig_poll"),
    path("mobile-session/", create_mobile_session, name="create_mobile_session"),
    path("saved-sig/", saved_signature_api, name="saved_signature_api"),
    path("progress/<uuid:pk>/", document_progress_public, name="document_progress_public"),
]
