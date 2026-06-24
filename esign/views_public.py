"""
E-Signature App — Public (Magic-Link) Signing View
=====================================================
No login required — the UUID token in the URL is the credential.
Authenticated users who are assigned as system-user signers are also
accepted if their identity matches the token's assigned user.
"""
import base64
import io
from datetime import timedelta

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from ipware import get_client_ip

from .forms import SignerSignForm
from .models import MobileDrawSession, Signer, SignatureEvent, UserSavedSignature, SignatureDocument
from .services import record_signature, reject_signature


def _is_identity_verified(request, signer):
    """True if this external signer has already passed the email+code gate this session."""
    stored = request.session.get(f"esign_verified_{signer.pk}")
    # Store the token itself so verification is invalidated when token is regenerated (reopen).
    return stored == str(signer.token)


def _mark_identity_verified(request, signer):
    request.session[f"esign_verified_{signer.pk}"] = str(signer.token)


@require_http_methods(["GET", "POST"])
def sign(request, token):
    """
    Magic-link signing page.

    Access rules:
    - External signer: must first verify identity with email + verification code.
    - Authenticated system user: must be the exact user assigned to this token.
    - Sequential: signer must be in PENDING status (WAITING = not their turn yet).
    - Already acted: show a read-only summary page.
    - Expired token: show an error page.
    """
    signer = get_object_or_404(Signer, token=token)
    document = signer.document

    if signer.user_id:
        if not request.user.is_authenticated:
            from django.conf import settings
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        if request.user != signer.user:
            return render(request, "esign/sign_denied.html", {
                "signer": signer, "document": document,
            }, status=403)

    # External signers must verify email + code before accessing the signing page.
    if not signer.user_id and not _is_identity_verified(request, signer):
        error = False
        if request.method == "POST" and request.POST.get("action") == "verify":
            email_input = request.POST.get("verify_email", "").strip().lower()
            code_input  = request.POST.get("verify_code", "").strip().upper()
            if (
                email_input == signer.external_email.lower()
                and code_input == signer.verify_code
            ):
                _mark_identity_verified(request, signer)
                return redirect(request.path)
            error = True
        return render(request, "esign/sign_verify.html", {
            "signer": signer, "document": document, "error": error,
        })

    if signer.status in (Signer.Status.SIGNED, Signer.Status.REJECTED):
        return render(request, "esign/sign_done.html", {
            "signer": signer, "document": document, "already_acted": True,
        })

    if signer.status == Signer.Status.WAITING:
        return render(request, "esign/sign_waiting.html", {
            "signer": signer, "document": document,
        })

    if signer.is_token_expired:
        return render(request, "esign/sign_expired.html", {
            "signer": signer, "document": document,
        })

    if document.status not in (document.Status.PENDING, document.Status.DRAFT):
        return render(request, "esign/sign_done.html", {
            "signer": signer, "document": document, "already_acted": False,
        })

    ip, _ = get_client_ip(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "")

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

    has_saved_sig = (
        request.user.is_authenticated
        and signer.user_id
        and UserSavedSignature.objects.filter(user=request.user).exists()
    )

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
                full_sig_data = form.cleaned_data.get("signature_data", "")
                sig_data = full_sig_data
                try:
                    if "," in sig_data:
                        sig_data = sig_data.split(",", 1)[1]
                    raw_bytes = base64.b64decode(sig_data)
                    img_file  = io.BytesIO(raw_bytes)

                    # Save stamp annotation preferences chosen by the signer.
                    signer.stamp_timestamp    = bool(form.cleaned_data.get("stamp_timestamp", True))
                    signer.stamp_name         = bool(form.cleaned_data.get("stamp_name", True))
                    signer.stamp_job_role     = bool(form.cleaned_data.get("stamp_job_role", False))
                    signer.stamp_use_logo     = bool(form.cleaned_data.get("stamp_use_logo", True))
                    signer.stamp_job_role_text = (form.cleaned_data.get("stamp_job_role_text") or "").strip()
                    signer.save(update_fields=[
                        "stamp_timestamp", "stamp_name", "stamp_job_role", "stamp_use_logo", "stamp_job_role_text"
                    ])

                    # If the user positioned their signature(s) via drag & drop,
                    # replace existing placements with the user-chosen coordinates.
                    drop_placements_json = form.cleaned_data.get("drop_placements", "")
                    if drop_placements_json:
                        import json
                        from .models import SignaturePlacement
                        try:
                            entries = json.loads(drop_placements_json)
                            if isinstance(entries, list) and entries:
                                signer.placements.all().delete()
                                for entry in entries:
                                    SignaturePlacement.objects.create(
                                        document=document,
                                        signer=signer,
                                        page_number=int(entry.get("page", 1)),
                                        x=max(0.0, min(1.0, float(entry.get("x", 0)))),
                                        y=max(0.0, min(1.0, float(entry.get("y", 0)))),
                                        width=max(0.01, min(1.0, float(entry.get("w", 0.2)))),
                                        height=max(0.01, min(1.0, float(entry.get("h", 0.08)))),
                                    )
                        except (json.JSONDecodeError, ValueError, TypeError, KeyError):
                            pass

                    actor_user = request.user if request.user.is_authenticated else None
                    record_signature(
                        signer, img_file, ip=ip,
                        user_agent=user_agent,
                        actor_user=actor_user,
                    )

                    if (
                        request.POST.get("save_signature") == "1"
                        and request.user.is_authenticated
                        and signer.user_id
                    ):
                        UserSavedSignature.objects.update_or_create(
                            user=request.user,
                            defaults={"signature_data": full_sig_data},
                        )

                    return render(request, "esign/sign_done.html", {
                        "signer": signer, "document": document, "just_signed": True,
                    })
                except (ValueError, Exception) as exc:
                    messages.error(request, f"Failed to save signature: {exc}")

    pdf_url = (
        f"{document.preview_pdf.url}?v={int(document.updated_at.timestamp())}"
        if document.preview_pdf
        else document.original_pdf.url
    )

    return render(request, "esign/sign.html", {
        "signer": signer,
        "document": document,
        "placements": placements,
        "placements_json": _placements_json(placements),
        "form": form,
        "has_saved_sig": has_saved_sig,
        "pdf_url": pdf_url,
        "has_prior_signatures": bool(document.preview_pdf),
    })


@require_POST
def create_mobile_session(request):
    """Creates a 15-minute QR session for mobile signature capture."""
    session = MobileDrawSession.objects.create(
        expires_at=timezone.now() + timedelta(minutes=15)
    )
    mobile_url = request.build_absolute_uri(
        f"/sign/mobile-draw/{session.pk}/"
    )
    poll_url = f"/sign/mobile-sig/{session.pk}/"
    return JsonResponse({
        "session_id": str(session.pk),
        "mobile_url": mobile_url,
        "poll_url": poll_url,
    })


@require_http_methods(["GET", "POST"])
def mobile_draw(request, session_id):
    """Mobile-optimised page for drawing a signature after scanning a QR code."""
    session = get_object_or_404(MobileDrawSession, pk=session_id)

    if session.is_expired:
        return render(request, "esign/mobile_draw_done.html", {"expired": True})
    if session.is_complete:
        return render(request, "esign/mobile_draw_done.html", {"already_done": True})

    if request.method == "POST":
        sig_data = request.POST.get("signature_data", "")
        if sig_data and sig_data.startswith("data:image/png;base64,"):
            session.signature_data = sig_data
            session.is_complete = True
            session.save()
        return render(request, "esign/mobile_draw_done.html", {"success": True})

    return render(request, "esign/mobile_draw.html", {"session": session})


def mobile_sig_poll(request, session_id):
    """Polling endpoint; returns signature data once the mobile user has drawn."""
    try:
        session = MobileDrawSession.objects.get(pk=session_id)
    except MobileDrawSession.DoesNotExist:
        return JsonResponse({"ready": False})

    if session.is_expired:
        return JsonResponse({"ready": False, "expired": True})
    if session.is_complete:
        return JsonResponse({"ready": True, "data": session.signature_data})
    return JsonResponse({"ready": False})


def saved_signature_api(request):
    """Returns the authenticated user's saved signature data."""
    if not request.user.is_authenticated:
        return JsonResponse({"has_saved": False})
    try:
        saved = request.user.saved_signature
        return JsonResponse({"has_saved": True, "data": saved.signature_data})
    except UserSavedSignature.DoesNotExist:
        return JsonResponse({"has_saved": False})


def _placements_json(placements):
    import json
    data = [
        {
            "page": p.page_number,
            "x": p.x, "y": p.y, "w": p.width, "h": p.height,
            "field_type": p.field_type,
        }
        for p in placements
    ]
    return json.dumps(data)

def document_progress_public(request, pk):
    """
    Publicly accessible page to view the progress of a signature document.
    Does not require login. Displays signers status and the latest PDF.
    """
    doc = get_object_or_404(SignatureDocument, pk=pk)
    signers = doc.signers.order_by("order")
    
    pdf_url = None
    if doc.status == 'completed' and doc.signed_pdf:
        pdf_url = doc.signed_pdf.url
    elif doc.preview_pdf:
        pdf_url = f"{doc.preview_pdf.url}?v={int(doc.updated_at.timestamp())}"
    elif doc.original_pdf:
        pdf_url = doc.original_pdf.url
        
    return render(request, "esign/document_progress_public.html", {
        "doc": doc,
        "signers": signers,
        "pdf_url": pdf_url,
    })
