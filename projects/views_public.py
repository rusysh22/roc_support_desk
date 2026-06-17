"""
Projects App — Public Views
============================
Public-facing views for SharedDocument and Project timeline pages.
No authentication required; access is gated by ``is_active`` / ``is_public`` flags.
"""
from django.shortcuts import render, get_object_or_404, redirect
from django.http import Http404
from django.contrib import messages
from django.core.cache import cache
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from ipware import get_client_ip as _get_client_ip
from .models import Project, ProjectPhase, ProjectComment

# ---------------------------------------------------------------------------

def public_project_timeline(request, pk):
    """
    Render the public project timeline with phases, updates, and comments.
    Returns 404 if the project is marked private (is_public=False).
    Only visible comments (is_visible=True) are shown to public visitors.
    """
    project = get_object_or_404(Project, pk=pk)
    if not project.is_public:
        raise Http404("This project timeline is private.")

    phases = (
        project.phases
        .prefetch_related(
            "updates",
            "updates__shared_doc",
            "comments",
            "comments__user",
        )
        .all()
    )

    can_edit = False
    if project.allow_public_crud:
        can_edit = True
    elif request.user.is_authenticated:
        if request.user.is_superuser or request.user.is_staff or getattr(request.user, "role_access", "") in ["SuperAdmin", "Manager"]:
            can_edit = True
        elif request.user in project.followers.all():
            can_edit = True

    latest_date = project.updated_at
    # Try to find the absolute latest update date
    for phase in phases:
        if phase.updated_at > latest_date:
            latest_date = phase.updated_at
        for update in phase.updates.all():
            if update.created_at > latest_date:
                latest_date = update.created_at

    return render(request, "projects/public/timeline.html", {
        "project": project,
        "phases": phases,
        "can_edit": can_edit,
        "latest_date": latest_date,
    })


# ---------------------------------------------------------------------------
# Add Comment (positive + negative flow)
# ---------------------------------------------------------------------------

_COMMENT_MAX_LENGTH = 2000
_GUEST_NAME_MAX_LENGTH = 100
_RATE_LIMIT_WINDOW = 300   # 5 minutes
_RATE_LIMIT_MAX    = 5     # max 5 comments per window per IP


def add_comment_view(request, pk, phase_id):
    """
    Accept a POST comment on a specific phase.

    Positive flow:
      - Validates all inputs (comment length, guest name/email for guests).
      - Rate-limits by IP: max 5 comments per IP per 5 minutes.
      - Associates with authenticated user or stores guest info.
      - Saves and redirects back with a success message.

    Negative flow:
      - Empty comment → error message, redirect back.
      - Comment too long → error message, redirect back.
      - Guest submits without name → error message, redirect back.
      - Invalid guest email → error message, redirect back.
      - Rate limit exceeded → 429-style error message, redirect back.
      - Non-POST request → 404 (no GET form rendering from this view).
      - Project not public → 404.
      - Phase not belonging to project → 404.
    """
    if request.method != "POST":
        raise Http404()

    project = get_object_or_404(Project, pk=pk)
    if not project.is_public:
        raise Http404("This project timeline is private.")

    phase = get_object_or_404(ProjectPhase, pk=phase_id, project=project)
    redirect_url_name = "projects_public:public_timeline"

    # ── Rate limiting (same pattern as core.views.RequestAccountView) ──────
    client_ip, _ = _get_client_ip(request)
    client_ip = client_ip or "unknown"
    rate_key = f"project_comment_rate:{client_ip}:{phase_id}"
    attempt_count = cache.get(rate_key, 0)

    if attempt_count >= _RATE_LIMIT_MAX:
        messages.error(
            request,
            "You have posted too many comments recently. Please wait a few minutes before trying again."
        )
        return redirect(redirect_url_name, pk=project.pk)

    # ── Input validation ────────────────────────────────────────────────────
    comment_text = request.POST.get("comment", "").strip()

    if not comment_text:
        messages.error(request, "Comment cannot be empty.")
        return redirect(redirect_url_name, pk=project.pk)

    if len(comment_text) > _COMMENT_MAX_LENGTH:
        messages.error(
            request,
            f"Comment is too long. Maximum {_COMMENT_MAX_LENGTH} characters allowed."
        )
        return redirect(redirect_url_name, pk=project.pk)

    # Guest-specific validation
    if not request.user.is_authenticated:
        guest_name  = request.POST.get("guest_name", "").strip()
        guest_email = request.POST.get("guest_email", "").strip()

        if not guest_name:
            messages.error(request, "Please enter your name before posting a comment.")
            return redirect(redirect_url_name, pk=project.pk)

        if len(guest_name) > _GUEST_NAME_MAX_LENGTH:
            messages.error(request, "Name is too long.")
            return redirect(redirect_url_name, pk=project.pk)

        if guest_email:
            try:
                validate_email(guest_email)
            except ValidationError:
                messages.error(request, "Please enter a valid email address.")
                return redirect(redirect_url_name, pk=project.pk)

    # ── Save comment ────────────────────────────────────────────────────────
    comment = ProjectComment(phase=phase, comment=comment_text)

    if request.user.is_authenticated:
        comment.user = request.user
    else:
        comment.guest_name  = guest_name
        comment.guest_email = guest_email

    comment.save()

    # Increment rate limit counter
    cache.set(rate_key, attempt_count + 1, timeout=_RATE_LIMIT_WINDOW)

    messages.success(request, "Your comment has been posted.")
    return redirect(redirect_url_name, pk=project.pk)

