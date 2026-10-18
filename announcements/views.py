"""Portal-facing announcement pages (list, detail, acknowledge)."""
from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.list_pagination import paginate
from core.models import SiteConfig

from .models import Announcement
from .services import acknowledge, mark_read, visible_announcements_for


def _login_redirect_if_required(request):
    """Guests follow the portal's own "require login" setting."""
    if not request.user.is_authenticated and SiteConfig.get_solo().require_public_login:
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")
    return None


def _visible_or_404(request, pk):
    announcement = visible_announcements_for(request.user).filter(pk=pk).first()
    if announcement is None:
        raise Http404("Announcement not found.")
    return announcement


def announcement_list(request):
    if response := _login_redirect_if_required(request):
        return response
    source = request.GET.get("source", "")
    announcements = visible_announcements_for(request.user)
    if source in Announcement.SourceType.values:
        announcements = announcements.filter(source_type=source)
    context = paginate(request, announcements.order_by("-is_pinned", "-publish_at"))
    context.update({
        "announcements": context["object_list"],
        "source": source,
        "source_choices": Announcement.SourceType.choices,
    })
    return render(request, "announcements/list.html", context)


def announcement_detail(request, pk):
    if response := _login_redirect_if_required(request):
        return response
    announcement = _visible_or_404(request, pk)
    mark_read(announcement, request.user)
    return render(request, "announcements/detail.html", {"announcement": announcement})


@login_required
@require_POST
def announcement_acknowledge(request, pk):
    announcement = _visible_or_404(request, pk)
    acknowledge(announcement, request.user)
    messages.success(request, "Thanks — your acknowledgement has been recorded.")
    return redirect("announcements:detail", pk=announcement.pk)
