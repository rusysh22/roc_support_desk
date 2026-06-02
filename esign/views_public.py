"""
E-Signature App — Public (Magic-Link) Signing View
=====================================================
No login required — the UUID token in the URL is the credential.
Authenticated users who are assigned as system-user signers are also
accepted if their identity matches the token's assigned user.
"""
import base64
import io

from django.contrib import messages
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods
from ipware import get_client_ip

from .forms import SignerSignForm
from .models import Signer, SignatureEvent
from .services import record_signature, reject_signature


@require_http_methods(["GET", "POST"])
def sign(request, token):
    """
    Magic-link signing page.

    Access rules:
    - Unauthenticated: the token in the URL is the sole credential.
    - Authenticated system user: must be the exact user assigned to this token.
    - Sequential: signer must be in PENDING status (WAITING = not their turn yet).
    - Already acted: show a read-only summary page.
    - Expired token: show an error page.
    """
    signer = get_object_or_404(Signer, token=token)
    document = signer.document

    # Identity check for logged-in users
    if (
        request.user.is_authenticated
        and signer.user_id
        and request.user != signer.user
    ):
        return render(request, "esign/sign_denied.html", {
            "signer": signer,
            "document": document,
        }, status=403)

    # Already acted
    if signer.status in (Signer.Status.SIGNED, Signer.Status.REJECTED):
        return render(request, "esign/sign_done.html", {
            "signer": signer,
            "document": document,
            "already_acted": True,
        })

    # Sequential — not their turn yet
    if signer.status == Signer.Status.WAITING:
        return render(request, "esign/sign_waiting.html", {
            "signer": signer,
            "document": document,
        })

    # Token expiry
    if signer.is_token_expired:
        return render(request, "esign/sign_expired.html", {
            "signer": signer,
            "document": document,
        })

    # Document no longer active
    if document.status not in (
        document.Status.PENDING,
        document.Status.DRAFT,
    ):
        return render(request, "esign/sign_done.html", {
            "signer": signer,
            "document": document,
            "already_acted": False,
        })

    ip, _ = get_client_ip(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "")

    # Log view event on first GET (once per session is fine for audit purposes)
    if request.method == "GET":
        if not request.session.get(f"esign_viewed_{signer.pk}"):
            SignatureEvent.objects.create(
                document=document,
                event=SignatureEvent.Event.VIEWED,
                actor_user=request.user if request.user.is_authenticated else None,
                actor_label=signer.display_name,
                ip=ip,
            )
            request.session[f"esign_viewed_{signer.pk}"] = True

    placements = signer.placements.order_by("page_number")
    form = SignerSignForm()

    if request.method == "POST":
        form = SignerSignForm(request.POST)
        if form.is_valid():
            action = form.cleaned_data["action"]
            notes  = form.cleaned_data.get("notes", "")

            if action == "reject":
                try:
                    reject_signature(
                        signer, notes, ip=ip,
                        actor_user=request.user if request.user.is_authenticated else None,
                    )
                    return render(request, "esign/sign_done.html", {
                        "signer": signer, "document": document, "just_rejected": True,
                    })
                except ValueError as exc:
                    messages.error(request, str(exc))

            elif action == "sign":
                sig_data = form.cleaned_data.get("signature_data", "")
                try:
                    # Decode base64 data-URL: "data:image/png;base64,<data>"
                    if "," in sig_data:
                        sig_data = sig_data.split(",", 1)[1]
                    raw_bytes = base64.b64decode(sig_data)
                    img_file  = io.BytesIO(raw_bytes)

                    actor_user = request.user if request.user.is_authenticated else None
                    record_signature(
                        signer, img_file, ip=ip,
                        user_agent=user_agent,
                        actor_user=actor_user,
                    )
                    return render(request, "esign/sign_done.html", {
                        "signer": signer, "document": document, "just_signed": True,
                    })
                except (ValueError, Exception) as exc:
                    messages.error(request, f"Gagal menyimpan tanda tangan: {exc}")

    return render(request, "esign/sign.html", {
        "signer": signer,
        "document": document,
        "placements": placements,
        "placements_json": _placements_json(placements),
        "form": form,
    })


def _placements_json(placements):
    """Serialize placements to JSON for the PDF.js overlay."""
    import json
    data = [
        {
            "page": p.page_number,
            "x": p.x,
            "y": p.y,
            "w": p.width,
            "h": p.height,
            "field_type": p.field_type,
        }
        for p in placements
    ]
    return json.dumps(data)
