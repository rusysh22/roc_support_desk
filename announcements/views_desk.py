"""Announcement management (module: announcement_management)."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.dynamic_filters import active_filters_json, apply_dynamic_filters, fields_json
from core.excel_utils import export_queryset_xlsx, safe_cell
from core.list_actions import get_selected
from core.list_pagination import paginate

from .forms import AnnouncementForm
from .models import Announcement
from .services import log_published, notify_by_email

MODULE = "announcement_management"
URL_LIST = "announcements_desk:list"

FILTERABLE_FIELDS = {
    "status": {"label": "Status", "type": "choice", "lookup": "status", "choices": Announcement.Status.choices},
    "source_type": {
        "label": "Source", "type": "choice", "lookup": "source_type", "choices": Announcement.SourceType.choices,
    },
    "visibility": {
        "label": "Audience", "type": "choice", "lookup": "visibility", "choices": Announcement.Visibility.choices,
    },
    "is_pinned": {
        "label": "Pinned", "type": "choice", "lookup": "is_pinned",
        "choices": [("true", "Yes"), ("false", "No")], "python_type": "bool",
    },
}


def announcement_manager_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if MODULE not in request.user.get_accessible_modules():
            return HttpResponseForbidden("You do not have access to Announcements.")
        return view_func(request, *args, **kwargs)
    return wrapper


@announcement_manager_required
def manage_list(request):
    q = request.GET.get("q", "").strip()
    announcements = Announcement.objects.annotate(
        read_count=Count("receipts", distinct=True),
        ack_count=Count("receipts", filter=Q(receipts__acknowledged_at__isnull=False), distinct=True),
    ).order_by("-publish_at")
    if q:
        announcements = announcements.filter(Q(title__icontains=q) | Q(summary__icontains=q))
    announcements, active_filters = apply_dynamic_filters(announcements, request, FILTERABLE_FIELDS)
    context = {
        "search_query": q,
        "active_filters": active_filters,
        "active_filters_json": active_filters_json(active_filters),
        "dynamic_fields_json": fields_json(FILTERABLE_FIELDS),
        "has_active_filters": bool(q or active_filters),
    }
    context.update(paginate(request, announcements))
    context["announcements"] = context["object_list"]
    return render(request, "announcements/desk/list.html", context)


def _save(request, announcement=None):
    was_published = bool(announcement and announcement.status == Announcement.Status.PUBLISHED)
    form = AnnouncementForm(request.POST or None, instance=announcement)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            if announcement is None:
                obj.created_by = request.user
            obj.updated_by = request.user
            obj.save()
            form.save_m2m()
            if obj.status == Announcement.Status.PUBLISHED and not was_published:
                log_published(obj, request.user)
            if obj.status == Announcement.Status.PUBLISHED and form.cleaned_data["send_email"]:
                transaction.on_commit(lambda: notify_by_email(obj))
        messages.success(request, f'Announcement "{obj.title}" saved.')
        return redirect(URL_LIST)
    return render(request, "announcements/desk/form.html", {"form": form, "announcement": announcement})


@announcement_manager_required
def manage_create(request):
    return _save(request)


@announcement_manager_required
def manage_edit(request, pk):
    return _save(request, get_object_or_404(Announcement, pk=pk))


@announcement_manager_required
@require_POST
def manage_delete(request, pk):
    announcement = get_object_or_404(Announcement, pk=pk)
    announcement.delete()
    messages.success(request, f'Announcement "{announcement.title}" deleted.')
    return redirect(URL_LIST)


@announcement_manager_required
@require_POST
def bulk_archive(request):
    count = get_selected(request, Announcement.objects.all()).update(status=Announcement.Status.ARCHIVED)
    messages.success(request, f"{count} announcement(s) archived.")
    return redirect(URL_LIST)


@announcement_manager_required
@require_POST
def bulk_delete(request):
    _, per_model = get_selected(request, Announcement.objects.all()).delete()
    messages.success(request, f"{per_model.get(Announcement._meta.label, 0)} announcement(s) deleted.")
    return redirect(URL_LIST)


@announcement_manager_required
@require_POST
def bulk_export(request):
    qs = Announcement.objects.annotate(read_count=Count("receipts")).order_by("-publish_at")
    ids = request.POST.getlist("selected_ids")
    if ids:
        qs = qs.filter(pk__in=ids)
    columns = [
        ("Title", lambda o: safe_cell(o.title)),
        ("Source", lambda o: o.get_source_type_display()),
        ("Audience", lambda o: o.get_visibility_display()),
        ("Status", lambda o: o.get_status_display()),
        ("Publish At", lambda o: o.publish_at.strftime("%Y-%m-%d %H:%M")),
        ("Expires At", lambda o: o.expires_at.strftime("%Y-%m-%d %H:%M") if o.expires_at else ""),
        ("Read By", lambda o: o.read_count),
    ]
    return export_queryset_xlsx(qs, columns, "announcements.xlsx", sheet_title="Announcements")
