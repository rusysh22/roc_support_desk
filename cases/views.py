"""
Cases App — Views
==================
Client-facing (public) and Admin/Staff (auth-required) views.

Public views:
    - ``client_dashboard``  — grid of CaseCategory cards.
    - ``create_case``       — form to submit a new case.
    - ``case_submitted``    — confirmation page.

Staff views (login + role_access required):
    - ``case_list``         — filterable list / kanban of all cases.
    - ``case_detail``       — split-panel: chat thread + RCA form.
    - ``case_update_rca``   — HTMX partial to save RCA fields.
    - ``case_send_reply``   — HTMX partial to send a staff reply.
    - ``chat_thread``       — HTMX partial to refresh chat messages.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.db.models import Q, F, Value, BooleanField, Case as DBCase, When, Exists, OuterRef
from django.http import HttpResponseForbidden, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.core.cache import cache

from core.models import CompanyUnit, Employee, User
from .forms import CaseCreateForm, CaseRCAForm, StaffReplyForm
from .models import Attachment, CaseCategory, CaseRecord, Message, CaseComment, CaseAuditLog, RCATemplate


# =====================================================================
# Access Control Decorators
# =====================================================================

def staff_required(view_func):
    """
    Decorator to ensure user is authenticated AND has staff-level
    role_access (SuperAdmin, Manager, or SupportDesk).
    """
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not hasattr(request.user, "role_access"):
            return HttpResponseForbidden("Access denied.")
        allowed_roles = {
            User.RoleAccess.SUPERADMIN,
            User.RoleAccess.MANAGER,
            User.RoleAccess.SUPPORTDESK,
        }
        if request.user.role_access not in allowed_roles:
            return HttpResponseForbidden("Insufficient role access.")
        return view_func(request, *args, **kwargs)
    return _wrapped


def _case_is_closed(case, request):
    """Return True and add an error message if the case is closed."""
    if case.status == CaseRecord.Status.CLOSED:
        messages.error(request, "Cannot modify a closed ticket.")
        return True
    return False


def _check_confidential_access(case, user):
    """Return True if the user is allowed to access this case's details."""
    if not getattr(case.category, "is_confidential", False):
        return True
    return user.role_access == "SuperAdmin" or user.can_handle_confidential


def _annotate_confidential_access(qs, user):
    """Annotate a CaseRecord queryset with ``has_confidential_access_flag``."""
    if user.role_access == "SuperAdmin" or user.can_handle_confidential:
        return qs.annotate(
            has_confidential_access_flag=Value(True, output_field=BooleanField()),
        )
    return qs.annotate(
        has_confidential_access_flag=DBCase(
            When(category__is_confidential=False, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        )
    )


def manager_or_admin_required(view_func):
    """Decorator for Manager/SuperAdmin only actions."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        allowed = {User.RoleAccess.SUPERADMIN, User.RoleAccess.MANAGER}
        if request.user.role_access not in allowed:
            return HttpResponseForbidden("Manager or SuperAdmin access required.")
        return view_func(request, *args, **kwargs)
    return _wrapped


# =====================================================================
# Staff — Analytics Dashboard
# =====================================================================

@staff_required
def dashboard(request):
    """
    Analytics dashboard for staff — summary cards, charts, tables.
    Supports date-range, assigned_to, category, source, and priority filters.
    """
    from django.db.models import Count, Avg, Sum, ExpressionWrapper, DurationField
    from django.db.models.functions import TruncDate, TruncMonth, Coalesce
    from django.utils.timezone import localtime, make_aware
    from datetime import datetime, timedelta

    now = timezone.now()
    today = now.date()

    # ── Filter params ──────────────────────────────────────────────
    date_from_str = request.GET.get("date_from", "")
    date_to_str = request.GET.get("date_to", "")
    filter_assigned = request.GET.get("assigned_to", "")
    filter_category = request.GET.get("category", "")
    filter_source = request.GET.get("source", "")
    filter_priority = request.GET.get("priority", "")

    # Default: first day of current month → today
    if date_from_str:
        try:
            date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
        except ValueError:
            date_from = today.replace(day=1)
    else:
        date_from = today.replace(day=1)

    if date_to_str:
        try:
            date_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
        except ValueError:
            date_to = today
    else:
        date_to = today

    dt_from = make_aware(datetime.combine(date_from, datetime.min.time()))
    dt_to = make_aware(datetime.combine(date_to, datetime.max.time()))

    # ── Base queryset (non-spam, non-sub-ticket) ───────────────────
    qs = CaseRecord.objects.filter(
        is_spam=False,
        master_ticket__isnull=True,
        created_at__range=(dt_from, dt_to),
    ).select_related("category", "assigned_to", "requester", "requester__unit")

    # Apply filters
    if filter_assigned:
        if filter_assigned == "unassigned":
            qs = qs.filter(assigned_to__isnull=True)
        else:
            qs = qs.filter(assigned_to_id=filter_assigned)
    if filter_category:
        qs = qs.filter(category__slug=filter_category)
    if filter_source:
        qs = qs.filter(source=filter_source)
    if filter_priority:
        qs = qs.filter(priority=filter_priority)

    # ── Also build an "all time active" queryset (not filtered by date) for summary cards ──
    active_qs = CaseRecord.objects.filter(
        is_spam=False,
        master_ticket__isnull=True,
        status__in=[CaseRecord.Status.OPEN, CaseRecord.Status.INVESTIGATING, CaseRecord.Status.PENDING_INFO],
    )
    if filter_assigned:
        if filter_assigned == "unassigned":
            active_qs = active_qs.filter(assigned_to__isnull=True)
        else:
            active_qs = active_qs.filter(assigned_to_id=filter_assigned)
    if filter_category:
        active_qs = active_qs.filter(category__slug=filter_category)
    if filter_source:
        active_qs = active_qs.filter(source=filter_source)
    if filter_priority:
        active_qs = active_qs.filter(priority=filter_priority)

    # ── SUMMARY CARDS ──────────────────────────────────────────────
    total_in_range = qs.count()
    total_active = active_qs.count()
    total_unassigned = active_qs.filter(assigned_to__isnull=True).count()

    resolved_in_range = qs.filter(status__in=[CaseRecord.Status.RESOLVED, CaseRecord.Status.CLOSED]).count()
    resolved_today = CaseRecord.objects.filter(
        is_spam=False, master_ticket__isnull=True,
        status__in=[CaseRecord.Status.RESOLVED, CaseRecord.Status.CLOSED],
        updated_at__date=today,
    ).count()

    unread_count = active_qs.filter(has_unread_messages=True).count()

    # SLA breached: resolution_due_at < now AND status not closed/resolved
    sla_breached = active_qs.filter(
        resolution_due_at__lt=now,
    ).exclude(status__in=[CaseRecord.Status.RESOLVED, CaseRecord.Status.CLOSED]).count()

    # SLA at risk: due within 24h
    sla_at_risk = active_qs.filter(
        resolution_due_at__gt=now,
        resolution_due_at__lte=now + timedelta(hours=24),
    ).exclude(status__in=[CaseRecord.Status.RESOLVED, CaseRecord.Status.CLOSED]).count()

    # Avg resolution time (for closed tickets in range)
    closed_in_range = qs.filter(status=CaseRecord.Status.CLOSED, resolution_due_at__isnull=False)
    avg_resolution = None
    if closed_in_range.exists():
        from django.db.models import ExpressionWrapper, DurationField
        avg_dur = closed_in_range.annotate(
            dur=ExpressionWrapper(F("updated_at") - F("created_at"), output_field=DurationField())
        ).aggregate(avg=Avg("dur"))["avg"]
        if avg_dur:
            total_hours = avg_dur.total_seconds() / 3600
            if total_hours >= 24:
                avg_resolution = f"{total_hours / 24:.1f} days"
            else:
                avg_resolution = f"{total_hours:.1f} hrs"

    # ── CHART DATA: Ticket Trend (auto-granularity) ──────────────
    day_span = (date_to - date_from).days + 1
    trend_granularity = request.GET.get("granularity", "")
    if not trend_granularity:
        # Auto: daily ≤ 31 days, weekly ≤ 90, monthly beyond
        if day_span <= 31:
            trend_granularity = "daily"
        elif day_span <= 90:
            trend_granularity = "weekly"
        else:
            trend_granularity = "monthly"

    if trend_granularity == "monthly":
        from django.db.models.functions import TruncMonth as TruncPeriod
        fmt = "%b %Y"
    elif trend_granularity == "weekly":
        from django.db.models.functions import TruncWeek as TruncPeriod
        fmt = "%d %b"
    else:
        TruncPeriod = TruncDate
        fmt = "%d %b"

    trend_data = (
        qs.annotate(period=TruncPeriod("created_at"))
        .values("period")
        .annotate(created=Count("id"))
        .order_by("period")
    )
    resolved_trend = (
        qs.filter(status__in=[CaseRecord.Status.RESOLVED, CaseRecord.Status.CLOSED])
        .annotate(period=TruncPeriod("updated_at"))
        .values("period")
        .annotate(resolved=Count("id"))
        .order_by("period")
    )
    created_map = {}
    for row in trend_data:
        key = row["period"].date() if hasattr(row["period"], "date") else row["period"]
        created_map[key] = row["created"]

    resolved_map = {}
    for r in resolved_trend:
        key = r["period"].date() if hasattr(r["period"], "date") else r["period"]
        resolved_map[key] = r["resolved"]

    # Merge all dates from both datasets so no data points are lost
    all_dates = sorted(set(created_map.keys()) | set(resolved_map.keys()))

    trend_labels = []
    trend_created = []
    trend_resolved = []
    for d in all_dates:
        trend_labels.append(d.strftime(fmt))
        trend_created.append(created_map.get(d, 0))
        trend_resolved.append(resolved_map.get(d, 0))

    # ── CHART DATA: Status Distribution ────────────────────────────
    status_dist = qs.values("status").annotate(count=Count("id")).order_by("status")
    status_labels = []
    status_counts = []
    status_colors = {
        "Open": "#6366f1",
        "Investigating": "#f59e0b",
        "PendingInfo": "#0ea5e9",
        "Resolved": "#10b981",
        "Closed": "#64748b",
    }
    status_bg = []
    for s in status_dist:
        status_labels.append(s["status"])
        status_counts.append(s["count"])
        status_bg.append(status_colors.get(s["status"], "#94a3b8"))

    # ── CHART DATA: Source Channel ─────────────────────────────────
    source_dist = qs.values("source").annotate(count=Count("id")).order_by("-count")
    source_labels = []
    source_counts = []
    source_display = {"EvolutionAPI_WA": "WhatsApp", "Email": "Email", "WebForm": "Web Form"}
    for s in source_dist:
        source_labels.append(source_display.get(s["source"], s["source"]))
        source_counts.append(s["count"])

    # ── CHART DATA: Priority Distribution ──────────────────────────
    priority_dist = qs.values("priority").annotate(count=Count("id")).order_by("-count")
    priority_labels = []
    priority_counts = []
    priority_colors_map = {"Low": "#10b981", "Medium": "#f59e0b", "High": "#ef4444", "Critical": "#7c3aed"}
    priority_bg = []
    for p in priority_dist:
        priority_labels.append(p["priority"])
        priority_counts.append(p["count"])
        priority_bg.append(priority_colors_map.get(p["priority"], "#94a3b8"))

    # ── CHART DATA: Top Categories ─────────────────────────────────
    cat_dist = (
        qs.filter(category__isnull=False)
        .values("category__name")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )
    cat_labels = [c["category__name"] for c in cat_dist]
    cat_counts = [c["count"] for c in cat_dist]

    # ── CHART DATA: Agent Workload ─────────────────────────────────
    agent_dist = (
        active_qs.filter(assigned_to__isnull=False)
        .values("assigned_to__username")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )
    agent_labels = [a["assigned_to__username"] for a in agent_dist]
    agent_counts = [a["count"] for a in agent_dist]

    # ── CHART DATA: By Company Unit ────────────────────────────────
    unit_dist = (
        qs.filter(requester__unit__isnull=False)
        .values("requester__unit__name")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )
    unit_labels = [u["requester__unit__name"] for u in unit_dist]
    unit_counts = [u["count"] for u in unit_dist]

    # ── SLA Performance (closed tickets with SLA data) ─────────────
    closed_with_sla = qs.filter(
        status=CaseRecord.Status.CLOSED,
        resolution_due_at__isnull=False,
    )
    sla_total = closed_with_sla.count()
    sla_met = closed_with_sla.filter(updated_at__lte=F("resolution_due_at")).count()
    sla_pct = round((sla_met / sla_total * 100), 1) if sla_total > 0 else 0

    # ── TABLES: Recent Tickets ─────────────────────────────────────
    recent_tickets = qs.order_by("-created_at")[:8]

    # ── TABLES: SLA At Risk ────────────────────────────────────────
    sla_at_risk_tickets = active_qs.filter(
        resolution_due_at__gt=now,
        resolution_due_at__lte=now + timedelta(hours=24),
    ).order_by("resolution_due_at")[:5]

    sla_breached_tickets = active_qs.filter(
        resolution_due_at__lt=now,
    ).order_by("resolution_due_at")[:5]

    # ── TABLES: Top Requesters ─────────────────────────────────────
    top_requesters = (
        qs.filter(requester__isnull=False)
        .values("requester__full_name", "requester__unit__name")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    # ── CHART DATA: First Response Time (FRT) Trend ────────────────
    from django.db.models import Min, Subquery, OuterRef
    frt_subquery = Message.objects.filter(
        case=OuterRef("pk"),
        direction=Message.Direction.OUTBOUND,
    ).order_by("sent_at").values("sent_at")[:1]

    frt_qs = (
        qs.annotate(first_reply_at=Subquery(frt_subquery))
        .filter(first_reply_at__isnull=False)
        .annotate(
            frt_seconds=ExpressionWrapper(
                F("first_reply_at") - F("created_at"),
                output_field=DurationField(),
            )
        )
        .annotate(period=TruncPeriod("created_at"))
        .values("period")
        .annotate(avg_frt=Avg("frt_seconds"))
        .order_by("period")
    )
    frt_labels = []
    frt_values = []
    for row in frt_qs:
        p = row["period"]
        d = p.date() if hasattr(p, "date") else p
        frt_labels.append(d.strftime(fmt))
        frt_hours = row["avg_frt"].total_seconds() / 3600 if row["avg_frt"] else 0
        frt_values.append(round(frt_hours, 1))

    # Overall avg FRT
    overall_frt_qs = (
        qs.annotate(first_reply_at=Subquery(frt_subquery))
        .filter(first_reply_at__isnull=False)
        .annotate(
            frt_seconds=ExpressionWrapper(
                F("first_reply_at") - F("created_at"),
                output_field=DurationField(),
            )
        )
        .aggregate(avg=Avg("frt_seconds"))
    )
    avg_frt = None
    if overall_frt_qs["avg"]:
        frt_h = overall_frt_qs["avg"].total_seconds() / 3600
        avg_frt = f"{frt_h:.1f} hrs" if frt_h < 24 else f"{frt_h / 24:.1f} days"

    # ── CHART DATA: Ticket Aging (active tickets) ────────────────
    aging_buckets = [
        ("< 1 day", 0, 1),
        ("1-3 days", 1, 3),
        ("3-7 days", 3, 7),
        ("7-14 days", 7, 14),
        ("> 14 days", 14, 9999),
    ]
    aging_labels = []
    aging_counts = []
    for label, d_min, d_max in aging_buckets:
        cutoff_max = now - timedelta(days=d_min)
        cutoff_min = now - timedelta(days=d_max)
        cnt = active_qs.filter(created_at__lte=cutoff_max, created_at__gt=cutoff_min).count()
        aging_labels.append(label)
        aging_counts.append(cnt)

    # ── CHART DATA: Peak Hours Heatmap ───────────────────────────
    from django.db.models.functions import ExtractHour, ExtractIsoWeekDay
    peak_data = (
        qs.annotate(
            hour=ExtractHour("created_at"),
            weekday=ExtractIsoWeekDay("created_at"),  # 1=Mon … 7=Sun
        )
        .values("weekday", "hour")
        .annotate(count=Count("id"))
        .order_by("weekday", "hour")
    )
    # Build 7x24 matrix [weekday][hour]
    heatmap_matrix = [[0] * 24 for _ in range(7)]
    for row in peak_data:
        heatmap_matrix[row["weekday"] - 1][row["hour"]] = row["count"]

    # ── CHART DATA: Resolution Rate Trend ────────────────────────
    resrate_labels = []
    resrate_values = []
    for d in all_dates:
        c = created_map.get(d, 0)
        r = resolved_map.get(d, 0)
        resrate_labels.append(d.strftime(fmt))
        pct = round((r / c * 100), 1) if c > 0 else 0
        resrate_values.append(pct)

    # ── CHART DATA: Reopen Rate ──────────────────────────────────
    # Tickets that were resolved/closed but now active again
    reopened_count = CaseRecord.objects.filter(
        is_spam=False,
        master_ticket__isnull=True,
        created_at__range=(dt_from, dt_to),
        status__in=[CaseRecord.Status.OPEN, CaseRecord.Status.INVESTIGATING, CaseRecord.Status.PENDING_INFO],
        edit_permission_status__isnull=False,  # had amendment workflow = was closed before
    ).count()
    reopen_pct = round((reopened_count / total_in_range * 100), 1) if total_in_range > 0 else 0

    # ── TABLE: Stale Tickets (no message activity > 3 days) ──────
    stale_cutoff = now - timedelta(days=3)
    stale_tickets = (
        active_qs.annotate(
            last_msg_at=Subquery(
                Message.objects.filter(case=OuterRef("pk"))
                .order_by("-sent_at")
                .values("sent_at")[:1]
            )
        )
        .filter(
            Q(last_msg_at__lt=stale_cutoff) | Q(last_msg_at__isnull=True, created_at__lt=stale_cutoff)
        )
        .select_related("category", "assigned_to", "requester")
        .order_by("created_at")[:8]
    )

    # ── WORD CLOUD: Tags ────────────────────────────────────────────
    import re
    from collections import Counter

    STOPWORDS = {
        "yang", "dan", "di", "ke", "dari", "ini", "itu", "untuk", "dengan",
        "pada", "adalah", "akan", "sudah", "tidak", "bisa", "ada", "juga",
        "atau", "oleh", "karena", "saat", "telah", "belum", "user", "the",
        "and", "for", "that", "this", "with", "was", "has", "been", "but",
        "are", "were", "had", "have", "can", "will", "its", "may", "our",
        "all", "one", "two", "new", "used", "more", "than", "each", "into",
        "done", "pagi", "selamat", "malam", "siang", "sore", "tim", "gws", "citis",
    }

    tag_counter = Counter()
    for row in qs.filter(tags__isnull=False).exclude(tags="").values_list("tags", flat=True):
        for tag in row.split(","):
            t = tag.strip().lower()
            if t and len(t) > 1:
                tag_counter[t] += 1

    tag_cloud = [[word, count] for word, count in tag_counter.most_common(60)]

    # ── WORD CLOUD: RCA & Solving Steps ──────────────────────────────
    rca_counter = Counter()
    rca_fields = qs.filter(
        Q(root_cause_analysis__isnull=False) | Q(solving_steps__isnull=False)
    ).values_list("root_cause_analysis", "solving_steps")

    word_pattern = re.compile(r'[a-zA-Z\u00C0-\u024F]+')
    for rca, steps in rca_fields:
        for text in (rca or "", steps or ""):
            for word in word_pattern.findall(text.lower()):
                if len(word) > 2 and word not in STOPWORDS:
                    rca_counter[word] += 1

    rca_cloud = [[word, count] for word, count in rca_counter.most_common(80)]

    # ── Filter options for dropdowns ───────────────────────────────
    staff_users = User.objects.filter(
        role_access__in=[User.RoleAccess.SUPERADMIN, User.RoleAccess.MANAGER, User.RoleAccess.SUPPORTDESK]
    ).order_by("username")
    categories = CaseCategory.objects.filter(parent__isnull=True).order_by("name")

    import json
    context = {
        # Summary cards
        "total_in_range": total_in_range,
        "total_active": total_active,
        "total_unassigned": total_unassigned,
        "resolved_in_range": resolved_in_range,
        "resolved_today": resolved_today,
        "unread_count": unread_count,
        "sla_breached": sla_breached,
        "sla_at_risk": sla_at_risk,
        "avg_resolution": avg_resolution,
        # Charts (JSON for Chart.js)
        "trend_labels": json.dumps(trend_labels),
        "trend_created": json.dumps(trend_created),
        "trend_resolved": json.dumps(trend_resolved),
        "status_labels": json.dumps(status_labels),
        "status_counts": json.dumps(status_counts),
        "status_bg": json.dumps(status_bg),
        "source_labels": json.dumps(source_labels),
        "source_counts": json.dumps(source_counts),
        "priority_labels": json.dumps(priority_labels),
        "priority_counts": json.dumps(priority_counts),
        "priority_bg": json.dumps(priority_bg),
        "cat_labels": json.dumps(cat_labels),
        "cat_counts": json.dumps(cat_counts),
        "agent_labels": json.dumps(agent_labels),
        "agent_counts": json.dumps(agent_counts),
        "unit_labels": json.dumps(unit_labels),
        "unit_counts": json.dumps(unit_counts),
        "sla_pct": sla_pct,
        "sla_met": sla_met,
        "sla_total": sla_total,
        # Tables
        "recent_tickets": recent_tickets,
        "sla_at_risk_tickets": sla_at_risk_tickets,
        "sla_breached_tickets": sla_breached_tickets,
        "top_requesters": top_requesters,
        # Filters
        "staff_users": staff_users,
        "categories": categories,
        "filter_date_from": date_from.strftime("%Y-%m-%d"),
        "filter_date_to": date_to.strftime("%Y-%m-%d"),
        "filter_assigned": filter_assigned,
        "filter_category": filter_category,
        "filter_source": filter_source,
        "filter_priority": filter_priority,
        "date_range_label": f"{date_from.strftime('%d %b %Y')} — {date_to.strftime('%d %b %Y')}",
        "trend_granularity": trend_granularity,
        # Word clouds
        "tag_cloud": json.dumps(tag_cloud),
        "rca_cloud": json.dumps(rca_cloud),
        # New analytics
        "frt_labels": json.dumps(frt_labels),
        "frt_values": json.dumps(frt_values),
        "avg_frt": avg_frt,
        "aging_labels": json.dumps(aging_labels),
        "aging_counts": json.dumps(aging_counts),
        "heatmap_matrix": json.dumps(heatmap_matrix),
        "resrate_labels": json.dumps(resrate_labels),
        "resrate_values": json.dumps(resrate_values),
        "reopened_count": reopened_count,
        "reopen_pct": reopen_pct,
        "stale_tickets": stale_tickets,
    }
    return render(request, "admin/dashboard.html", context)


# =====================================================================
# Public — Client Portal
# =====================================================================

def client_dashboard(request):
    """
    Display the service catalogue as a responsive grid of CaseCategory cards,
    plus any DynamicForms configured to appear on the portal.

    Only root categories (parent=None) are displayed.  Categories that have
    children act as "Main Categories" — clicking them shows sub-categories.
    Categories without children link directly to the ticket form.
    """
    from core.models import DynamicForm
    categories = (
        CaseCategory.objects
        .filter(parent__isnull=True)
        .exclude(slug__in=["whatsapp-general", "email-general"])
        .prefetch_related("children")
    )
    portal_forms = DynamicForm.objects.filter(is_published=True, show_on_portal=True)
    return render(request, "client/dashboard.html", {"categories": categories, "portal_forms": portal_forms})


def category_children(request, slug):
    """
    Display sub-categories of a category at any level.
    If the category has no children (leaf), redirect to the ticket form.
    """
    parent = get_object_or_404(CaseCategory, slug=slug)
    children = parent.children.prefetch_related("children").all()
    if not children.exists():
        return redirect("cases:create_case_category", slug=slug)

    # Build breadcrumb trail: current → parent → grandparent → ...
    breadcrumbs = []
    node = parent.parent
    while node:
        breadcrumbs.insert(0, node)
        node = node.parent

    return render(request, "client/category_children.html", {
        "parent": parent,
        "children": children,
        "breadcrumbs": breadcrumbs,
    })


def create_case(request, slug=None):
    """
    Public case submission form.

    If ``slug`` is provided, the category is pre-selected.
    """
    import json
    initial = {}
    selected_category = None

    if slug:
        selected_category = get_object_or_404(CaseCategory, slug=slug)
        # If this is a parent category with children, redirect to sub-category selection
        if selected_category.children.exists():
            return redirect("cases:category_children", slug=slug)
        initial["category"] = selected_category

    # Only show leaf categories (no children) in the form dropdown
    categories_qs = (
        CaseCategory.objects
        .exclude(slug__in=["whatsapp-general", "email-general"])
        .filter(children__isnull=True)
    )
    category_templates_json = json.dumps({
        str(c.id): c.template_text for c in categories_qs if c.template_text
    })
    category_templates_subject_json = json.dumps({
        str(c.id): c.template_subject for c in categories_qs if c.template_subject
    })

    if request.method == "POST":
        # Rate Limiting Check
        client_ip = request.META.get('REMOTE_ADDR', 'unknown_ip')
        cache_key = f"create_case_rate_limit_{client_ip}"
        attempts = cache.get(cache_key, 0)
        
        if attempts >= 5:
            messages.error(request, "Too many requests. Please wait 10 minutes before submitting a new ticket.")
            return render(request, "client/create_case.html", {
                "categories": categories_qs,
                "company_units": CompanyUnit.objects.all(),
            }, status=429)

        form = CaseCreateForm(request.POST, request.FILES)
        if form.is_valid():
            # Validate attachment sizes (10 MB limit)
            uploaded_files = request.FILES.getlist("attachments")
            file_errors = form.validate_attachments(uploaded_files)
            if file_errors:
                for err in file_errors:
                    form.add_error(None, err)
                return render(request, "client/create_case.html", {
                    "form": form,
                    "selected_category": selected_category,
                    "categories": categories_qs,
                    "company_units": CompanyUnit.objects.all(),
                    "category_templates_json": category_templates_json,
                    "category_templates_subject_json": category_templates_subject_json,
                })

            email = form.cleaned_data["requester_email"]
            company_unit = form.cleaned_data["company_unit"]

            # Auto-link or auto-create Employee safely
            employee, created = Employee.objects.get_or_create(
                email=email,
                defaults={
                    "full_name": form.cleaned_data["requester_name"],
                    "job_role": form.cleaned_data["job_role"],
                    "unit": company_unit,
                }
            )

            # Update existing employee details if they changed
            if not created:
                updated = False
                if form.cleaned_data["requester_name"] and employee.full_name != form.cleaned_data["requester_name"]:
                    employee.full_name = form.cleaned_data["requester_name"]
                    updated = True
                if company_unit and employee.unit != company_unit:
                    employee.unit = company_unit
                    updated = True
                if form.cleaned_data["job_role"] and employee.job_role != form.cleaned_data["job_role"]:
                    employee.job_role = form.cleaned_data["job_role"]
                    updated = True
                if updated:
                    employee.save(update_fields=["full_name", "unit", "job_role"])

            case = CaseRecord.objects.create(
                requester=employee,  # None if no matching Employee
                requester_email=email,
                requester_name=form.cleaned_data["requester_name"],
                requester_job_role=form.cleaned_data["job_role"],
                requester_unit_name=company_unit.name,
                category=form.cleaned_data["category"],
                subject=form.cleaned_data["subject"],
                problem_description=form.cleaned_data["problem_description"],
                link=form.cleaned_data.get("link", ""),
                source=CaseRecord.Source.WEBFORM,
                status=CaseRecord.Status.OPEN,
                has_unread_messages=True,
            )

            # Create the initial message from the problem description
            msg = Message.objects.create(
                case=case,
                sender_employee=employee,
                body=form.cleaned_data["problem_description"],
                direction=Message.Direction.INBOUND,
                channel=Message.Channel.WEB,
            )

            # Handle multiple attachments
            for uploaded_file in request.FILES.getlist("attachments"):
                Attachment.objects.create(
                    message=msg,
                    file=uploaded_file,
                    original_filename=uploaded_file.name,
                    mime_type=getattr(uploaded_file, "content_type", ""),
                    file_size=uploaded_file.size,
                )

            # Create Audit Log entry
            CaseAuditLog.objects.create(
                case=case,
                created_by=User.objects.filter(is_superuser=True).first() if not request.user.is_authenticated else request.user,
                action=CaseAuditLog.ActionText.CREATED,
                new_value="Case created via Public Portal"
            )

            messages.success(request, f"Your ticket ({case.case_number}) has been created successfully.")
            
            # Increment Rate Limit Counter
            cache.set(cache_key, attempts + 1, timeout=600)  # 10 minutes timeout

            return redirect("cases:case_submitted", case_id=case.id)
    else:
        form = CaseCreateForm(initial=initial)

    return render(request, "client/create_case.html", {
        "form": form,
        "selected_category": selected_category,
        "categories": categories_qs,
        "company_units": CompanyUnit.objects.all(),
        "category_templates_json": category_templates_json,
        "category_templates_subject_json": category_templates_subject_json,
    })


def case_submitted(request, case_id):
    """Confirmation page after a case has been submitted."""
    case = get_object_or_404(CaseRecord, id=case_id)
    return render(request, "client/case_submitted.html", {"case": case})

def send_case_email(request, case_id):
    """View to trigger an email to the requester with case details."""
    from django.contrib import messages
    from gateways.tasks import send_case_acknowledgment_task
    
    case = get_object_or_404(CaseRecord, id=case_id)
    
    if request.method == "POST":
        try:
            send_case_acknowledgment_task.delay(str(case.id))
            messages.success(request, f"Ticket information has been successfully sent to {case.requester_email}.")
        except Exception as e:
            messages.error(request, f"Failed to send ticket information email. The mail service might be temporarily down. Please take a screenshot of this page for your records. (Error: {e})")
            
    return redirect("cases:case_submitted", case_id=case.id)


# =====================================================================
# Dynamic Form Public Renderer
# =====================================================================

def public_form_view(request, slug):
    """
    Renders a published DynamicForm in a beautiful client-facing view.
    Handles the submission logic and transforms raw POST data into the JSON answers format.
    """
    from core.models import DynamicForm, FormSubmission, SiteConfig
    from django.contrib import messages
    from django.http import Http404
    from django.core.files.storage import FileSystemStorage
    import os
    from django.conf import settings

    form_obj = get_object_or_404(DynamicForm, slug=slug)

    is_preview = request.GET.get('preview') == '1'

    # Check if form is published (skip check in preview mode for staff)
    if not form_obj.is_published and not request.user.is_staff:
        raise Http404("This form is not currently available.")

    # Check if form requires login
    if form_obj.requires_login and not request.user.is_authenticated:
        messages.warning(request, "You must be logged in to view and submit this form.")
        # Assuming you have a 'accounts:login' or using reverse
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    fields = form_obj.fields.all()

    client_ip = request.META.get('REMOTE_ADDR', 'unknown_ip')
    cache_key = f"public_form_rate_limit_{client_ip}_{slug}"

    if request.method == "POST":
        # Rate Limiting Check
        attempts = cache.get(cache_key, 0)
        if attempts >= 5:
            messages.error(request, "Too many requests. Please wait 10 minutes before submitting this form again.")
            return render(request, "client/public_form.html", {
                "dynamic_form": form_obj,
                "fields": fields,
                "is_preview": is_preview,
            }, status=429)

        # Build the JSON response dict
        answers = {}
        errors = {}

        site_config = SiteConfig.get_solo()
        max_size_bytes = site_config.max_upload_size_mb * 1024 * 1024
        fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'form_uploads'), base_url=f"{settings.MEDIA_URL}form_uploads/")

        for field in fields:
            if 'attachment' in field.field_type:
                files = request.FILES.getlist(f"field_{field.id}")
                if field.is_required and not files:
                    errors[str(field.id)] = "This field is required."
                    answers[str(field.id)] = None
                    continue

                saved_files = []
                for f in files:
                    if f.size > max_size_bytes:
                        errors[str(field.id)] = f"File {f.name} exceeds {site_config.max_upload_size_mb}MB limit."
                        break
                    
                    filename = fs.save(f.name, f)
                    saved_files.append(fs.url(filename))
                
                if field.field_type == 'attachment':
                    answers[str(field.id)] = saved_files[0] if saved_files else None
                else:
                    answers[str(field.id)] = saved_files

            elif field.field_type == 'checkbox':
                # Checkbox fields use getlist because they can have multiple values
                val = request.POST.getlist(f"field_{field.id}")
                if field.is_required and not val:
                    errors[str(field.id)] = "This field is required."
                answers[str(field.id)] = val
            else:
                val = request.POST.get(f"field_{field.id}", "").strip()
                if field.is_required and not val:
                    errors[str(field.id)] = "This field is required."
                answers[str(field.id)] = val

        if errors:
            messages.error(request, "Please fill out all required fields marked with *")
            return render(request, "client/public_form.html", {
                "dynamic_form": form_obj,
                "fields": fields,
                "answers": answers,
                "errors": errors
            })
        
        # Save submission
        FormSubmission.objects.create(
            form=form_obj,
            submitted_by=request.user if request.user.is_authenticated else None,
            answers=answers
        )

        # Increment Rate Limit Counter
        if not is_preview:
            cache.set(cache_key, attempts + 1, timeout=600)  # 10 minutes timeout

        messages.success(request, form_obj.success_message)
        # Using a simple success flag in context or simply rendering directly
        return render(request, "client/public_form.html", {
            "dynamic_form": form_obj,
            "success": True
        })

    return render(request, "client/public_form.html", {
        "dynamic_form": form_obj,
        "fields": fields,
        "is_preview": is_preview,
    })


# =====================================================================
# Auth Required Desk (Admin/Staff) Views below
# =====================================================================

@staff_required
def case_list(request):
    """
    Filterable list of all cases for the support desk.
    Supports status/source/category filtering via GET params.
    """
    cases = CaseRecord.objects.select_related(
        "requester", "requester__unit", "category", "assigned_to"
    ).all()

    # --- Filtering ---
    folder = request.GET.get("folder", "inbox")
    status_filter = request.GET.get("status")
    source_filter = request.GET.get("source")
    category_filter = request.GET.get("category")
    assigned_to_filter = request.GET.get("assigned_to")
    type_filter = request.GET.get("type")
    tags_filter = request.GET.get("tags", "").strip()
    followers_filter = request.GET.get("followers")
    read_filter = request.GET.get("read_status", "")

    search_query = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    # Folder specific base filtering
    if folder == "spam":
        cases = cases.filter(is_spam=True)
    elif folder == "archive":
        cases = cases.filter(is_archived=True)
    else:  # inbox
        cases = cases.filter(is_spam=False, is_archived=False, master_ticket__isnull=True)

    if status_filter:
        status_list = [s.strip() for s in status_filter.split(',') if s.strip()]
        cases = cases.filter(status__in=status_list)
    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if assigned_to_filter:
        if assigned_to_filter == "unassigned":
            cases = cases.filter(assigned_to__isnull=True)
        else:
            cases = cases.filter(assigned_to_id=assigned_to_filter)
    if type_filter:
        cases = cases.filter(case_type=type_filter)
    if tags_filter:
        cases = cases.filter(tags__icontains=tags_filter)
    if followers_filter:
        cases = cases.filter(followers__id=followers_filter)
    if read_filter == "unread":
        cases = cases.filter(has_unread_messages=True)
    elif read_filter == "unopened":
        cases = cases.filter(last_viewed_at__isnull=True)
    elif read_filter == "read":
        cases = cases.filter(has_unread_messages=False, last_viewed_at__isnull=False)

    if search_query:
        cases = cases.filter(
            Q(id__icontains=search_query) |
            Q(subject__icontains=search_query) |
            Q(requester_name__icontains=search_query) |
            Q(requester__full_name__icontains=search_query) |
            Q(requester_email__icontains=search_query) |
            Q(requester__email__icontains=search_query) |
            Q(requester__phone_number__icontains=search_query) |
            Q(requester_unit_name__icontains=search_query) |
            Q(requester__unit__name__icontains=search_query)
        )
    if date_from:
        cases = cases.filter(created_at__date__gte=date_from)
    if date_to:
        cases = cases.filter(created_at__date__lte=date_to)

    # --- Sorting ---
    sort_field = request.GET.get("sort", "created_at")
    sort_order = request.GET.get("order", "desc")
    ALLOWED_SORT_FIELDS = {
        "case_number": "id",
        "subject": "subject",
        "status": "status",
        "source": "source",
        "requester": "requester__full_name",
        "category": "category__name",
        "assigned_to": "assigned_to__username",
        "created_at": "created_at",
        "last_viewed_at": "last_viewed_at",
    }
    # Annotate confidential access for the current user
    cases = _annotate_confidential_access(cases, request.user)

    db_field = ALLOWED_SORT_FIELDS.get(sort_field, "created_at")
    if sort_order == "asc":
        cases = cases.order_by(F(db_field).asc(nulls_last=True))
    else:
        cases = cases.order_by(F(db_field).desc(nulls_last=True))

    from django.core.paginator import Paginator
    paginator = Paginator(cases, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Calculate unread counts
    unread_inbox = CaseRecord.objects.filter(is_spam=False, is_archived=False, master_ticket__isnull=True, has_unread_messages=True).count()
    unread_archive = CaseRecord.objects.filter(is_archived=True, has_unread_messages=True).count()
    unread_spam = CaseRecord.objects.filter(is_spam=True, has_unread_messages=True).count()

    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    assignees = User.objects.filter(is_staff=True, is_active=True).order_by("first_name", "username")
    all_followers = User.objects.filter(is_active=True).order_by("first_name", "username")

    return render(request, "admin/case_list.html", {
        "cases": page_obj,
        "statuses": CaseRecord.Status.choices,
        "sources": CaseRecord.Source.choices,
        "categories": CaseCategory.objects.all(),
        "types": CaseRecord.Type.choices,
        "assignees": assignees,
        "all_followers": all_followers,
        "current_filters": {
            "folder": folder,
            "status": status_filter or "",
            "source": source_filter or "",
            "category": category_filter or "",
            "assigned_to": assigned_to_filter or "",
            "type": type_filter or "",
            "tags": tags_filter or "",
            "followers": followers_filter or "",
            "read_status": read_filter or "",
            "q": search_query,
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
        "current_sort": sort_field,
        "current_order": sort_order,
        "unread_inbox": unread_inbox,
        "unread_archive": unread_archive,
        "unread_spam": unread_spam,
    })


# ---------------------------------------------------------------
# Auto-Refresh Partial Views
# ---------------------------------------------------------------

@staff_required
def case_list_partial(request):
    """
    Returns only the table rows HTML fragment for the auto-refresh mechanism
    on the case list page. Accepts the same GET params as case_list.
    """
    cases = CaseRecord.objects.select_related(
        "requester", "requester__unit", "category", "assigned_to"
    ).all()

    folder = request.GET.get("folder", "inbox")
    status_filter = request.GET.get("status")
    source_filter = request.GET.get("source")
    category_filter = request.GET.get("category")
    assigned_to_filter = request.GET.get("assigned_to")
    type_filter = request.GET.get("type")
    tags_filter = request.GET.get("tags", "").strip()
    followers_filter = request.GET.get("followers")
    read_filter = request.GET.get("read_status", "")
    search_query = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    if folder == "spam":
        cases = cases.filter(is_spam=True)
    elif folder == "archive":
        cases = cases.filter(is_archived=True)
    else:
        cases = cases.filter(is_spam=False, is_archived=False, master_ticket__isnull=True)

    if status_filter:
        status_list = [s.strip() for s in status_filter.split(',') if s.strip()]
        cases = cases.filter(status__in=status_list)
    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if assigned_to_filter:
        if assigned_to_filter == "unassigned":
            cases = cases.filter(assigned_to__isnull=True)
        else:
            cases = cases.filter(assigned_to_id=assigned_to_filter)
    if type_filter:
        cases = cases.filter(case_type=type_filter)
    if tags_filter:
        cases = cases.filter(tags__icontains=tags_filter)
    if followers_filter:
        cases = cases.filter(followers__id=followers_filter)
    if read_filter == "unread":
        cases = cases.filter(has_unread_messages=True)
    elif read_filter == "unopened":
        cases = cases.filter(last_viewed_at__isnull=True)
    elif read_filter == "read":
        cases = cases.filter(has_unread_messages=False, last_viewed_at__isnull=False)
    if search_query:
        cases = cases.filter(
            Q(id__icontains=search_query) |
            Q(subject__icontains=search_query) |
            Q(requester_name__icontains=search_query) |
            Q(requester__full_name__icontains=search_query) |
            Q(requester_email__icontains=search_query) |
            Q(requester__email__icontains=search_query) |
            Q(requester__phone_number__icontains=search_query) |
            Q(requester_unit_name__icontains=search_query) |
            Q(requester__unit__name__icontains=search_query)
        )
    if date_from:
        cases = cases.filter(created_at__date__gte=date_from)
    if date_to:
        cases = cases.filter(created_at__date__lte=date_to)

    sort_field = request.GET.get("sort", "created_at")
    sort_order = request.GET.get("order", "desc")
    ALLOWED_SORT_FIELDS = {
        "case_number": "id", "subject": "subject", "status": "status",
        "source": "source", "requester": "requester__full_name",
        "category": "category__name", "assigned_to": "assigned_to__username",
        "created_at": "created_at", "last_viewed_at": "last_viewed_at",
    }
    # Annotate confidential access for the current user
    cases = _annotate_confidential_access(cases, request.user)

    db_field = ALLOWED_SORT_FIELDS.get(sort_field, "created_at")
    if sort_order == "asc":
        cases = cases.order_by(F(db_field).asc(nulls_last=True))
    else:
        cases = cases.order_by(F(db_field).desc(nulls_last=True))

    from django.core.paginator import Paginator
    paginator = Paginator(cases, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    unread_inbox = CaseRecord.objects.filter(is_spam=False, is_archived=False, master_ticket__isnull=True, has_unread_messages=True).count()
    unread_archive = CaseRecord.objects.filter(is_archived=True, has_unread_messages=True).count()
    unread_spam = CaseRecord.objects.filter(is_spam=True, has_unread_messages=True).count()

    return render(request, "partials/case_table_rows.html", {
        "cases": page_obj,
        "unread_inbox": unread_inbox,
        "unread_archive": unread_archive,
        "unread_spam": unread_spam,
    })


@staff_required
def case_kanban_partial(request):
    """
    Returns only the kanban columns HTML fragment for the auto-refresh mechanism.
    """
    cases = CaseRecord.objects.select_related(
        "requester", "requester__unit", "category", "assigned_to"
    ).all()

    folder = request.GET.get("folder", "inbox")
    source_filter = request.GET.get("source")
    category_filter = request.GET.get("category")
    priority_filter = request.GET.get("priority")
    status_filter = request.GET.get("status")
    assigned_to_filter = request.GET.get("assigned_to")
    type_filter = request.GET.get("type")
    tags_filter = request.GET.get("tags", "").strip()
    followers_filter = request.GET.get("followers")
    read_filter = request.GET.get("read_status", "")
    search_query = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    if folder == "spam":
        cases = cases.filter(is_spam=True)
    elif folder == "archive":
        cases = cases.filter(is_archived=True)
    else:
        cases = cases.filter(is_spam=False, is_archived=False, master_ticket__isnull=True)

    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if priority_filter:
        cases = cases.filter(priority=priority_filter)
    if assigned_to_filter:
        if assigned_to_filter == "unassigned":
            cases = cases.filter(assigned_to__isnull=True)
        else:
            cases = cases.filter(assigned_to_id=assigned_to_filter)
    if type_filter:
        cases = cases.filter(case_type=type_filter)
    if tags_filter:
        cases = cases.filter(tags__icontains=tags_filter)
    if followers_filter:
        cases = cases.filter(followers__id=followers_filter)
    if read_filter == "unread":
        cases = cases.filter(has_unread_messages=True)
    elif read_filter == "unopened":
        cases = cases.filter(last_viewed_at__isnull=True)
    elif read_filter == "read":
        cases = cases.filter(has_unread_messages=False, last_viewed_at__isnull=False)
    if search_query:
        cases = cases.filter(
            Q(id__icontains=search_query) |
            Q(subject__icontains=search_query) |
            Q(requester_name__icontains=search_query) |
            Q(requester__full_name__icontains=search_query) |
            Q(requester_email__icontains=search_query) |
            Q(requester__email__icontains=search_query) |
            Q(requester__phone_number__icontains=search_query) |
            Q(requester_unit_name__icontains=search_query) |
            Q(requester__unit__name__icontains=search_query)
        )
    if date_from:
        cases = cases.filter(created_at__date__gte=date_from)
    if date_to:
        cases = cases.filter(created_at__date__lte=date_to)

    # Annotate confidential access for the current user
    cases = _annotate_confidential_access(cases, request.user)

    status_columns = [
        ("Open", "⏳", "Open", "#6366f1"),
        ("Investigating", "🔍", "Investigating", "#f59e0b"),
        ("PendingInfo", "⏸️", "Pending Info", "#0ea5e9"),
        ("Resolved", "✅", "Resolved", "#10b981"),
        ("Closed", "🔒", "Closed", "#64748b"),
    ]
    if status_filter:
        status_list = [s.strip() for s in status_filter.split(',') if s.strip()]
        status_columns = [col for col in status_columns if col[0] in status_list]

    kanban_data = []
    for status_val, icon, label, color in status_columns:
        column_cases = [c for c in cases if c.status == status_val]
        kanban_data.append({
            "status": status_val, "icon": icon, "label": label,
            "color": color, "cases": column_cases, "count": len(column_cases),
        })

    return render(request, "partials/case_kanban_columns.html", {
        "kanban_data": kanban_data,
    })


@staff_required
@require_POST
def case_unmerge(request, case_id):
    """
    Unmerges a sub-ticket from its master ticket.
    """
    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")
    if case.master_ticket:
        master_num = case.master_ticket.case_number
        case.master_ticket = None
        case.save(update_fields=["master_ticket"])
        messages.success(request, f"Ticket {case.case_number} has been unmerged from {master_num}.")
    return redirect("desk:case_detail", case_id=case.id)

@staff_required
def case_bulk_action(request):
    """
    Handles bulk actions applied to multiple cases: Merge, Archive, or Spam.
    """
    if request.method == "POST":
        action = request.POST.get("action")
        case_ids = request.POST.getlist("case_ids")
        master_id = request.POST.get("master_id")

        if not action or not case_ids:
            return redirect("desk:case_list")

        cases_qs = CaseRecord.objects.filter(id__in=case_ids)

        if action == "spam":
            cases_qs.update(is_spam=True, is_archived=False)
        elif action == "archive":
            cases_qs.update(is_archived=True, is_spam=False)
        elif action == "unspam":
            cases_qs.update(is_spam=False)
        elif action == "unarchive":
            cases_qs.update(is_archived=False)
        elif action == "merge" and master_id:
            try:
                master = CaseRecord.objects.get(id=master_id)
                # Exclude the master itself from being set as its own sub-ticket
                sub_tickets = cases_qs.exclude(id=master_id)
                sub_tickets.update(master_ticket=master)
            except CaseRecord.DoesNotExist:
                pass

    return redirect(request.META.get("HTTP_REFERER", "desk:case_list"))


@staff_required
def case_detail(request, case_id):
    """
    Split-panel case detail view:
    - Left panel:  real-time chat/thread with the employee.
    - Right panel: RCA + solving steps form.
    """
    case = get_object_or_404(
        CaseRecord.objects.select_related("requester", "category", "assigned_to"),
        id=case_id,
    )
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")

    unread_msg_ids = []
    if case.has_unread_messages:
        unread_msg_ids = list(case.messages.filter(direction="IN", is_read=False).values_list("id", flat=True))
        # Collect WhatsApp external IDs for read receipts before marking as read
        wa_external_ids = list(
            case.messages.filter(
                id__in=unread_msg_ids,
                channel="WhatsApp",
                external_id__gt="",
            ).values_list("external_id", flat=True)
        )
        case.has_unread_messages = False
        case.save(update_fields=["has_unread_messages"])
        case.messages.filter(id__in=unread_msg_ids).update(is_read=True)
        # Send read receipts (blue checkmarks) to WhatsApp asynchronously
        if wa_external_ids:
            from gateways.tasks import mark_wa_messages_read_task
            try:
                mark_wa_messages_read_task.delay(str(case.id), wa_external_ids)
            except Exception:
                pass  # Don't break page load if Celery is unavailable

    # Record when staff last viewed this ticket
    from django.utils import timezone
    case.last_viewed_at = timezone.now()
    case.save(update_fields=["last_viewed_at"])

    messages_qs = case.messages.select_related(
        "sender_employee", "sender_staff",
        "quoted_message__sender_employee", "quoted_message__sender_staff",
    ).prefetch_related("attachments", "reactions").all()
    rca_form = CaseRCAForm(instance=case)
    reply_form = StaffReplyForm()

    # Capture the referring URL (the dashboard/list with filters)
    back_url = request.GET.get("back") or request.META.get("HTTP_REFERER", "/desk/cases/")
    # If the referer is just the current page itself, fallback to the list
    if f"/desk/cases/{case_id}" in back_url:
        back_url = "/desk/cases/"

    # Sub-ticket / Master-ticket contextual tabs
    master = case.master_ticket if case.master_ticket else case
    related_cases = [master] + list(master.sub_tickets.exclude(id=master.id).all())

    # Prev / Next ticket navigation (ordered by created_at DESC — same as list)
    prev_case = (
        CaseRecord.objects.filter(created_at__gt=case.created_at)
        .order_by("created_at")
        .values_list("id", flat=True)
        .first()
    )
    next_case = (
        CaseRecord.objects.filter(created_at__lt=case.created_at)
        .order_by("-created_at")
        .values_list("id", flat=True)
        .first()
    )

    # (Confidential access is now category-based; no per-ticket user list needed)

    return render(request, "admin/case_detail.html", {
        "case": case,
        "chat_messages": messages_qs,
        "unread_msg_ids": unread_msg_ids,
        "rca_form": rca_form,
        "reply_form": reply_form,
        "back_url": back_url,
        "related_cases": related_cases,
        "master_case": master,
        "company_units": CompanyUnit.objects.all(),
        "all_employees": Employee.objects.select_related("unit").all(),
        "all_categories": CaseCategory.objects.select_related("parent").prefetch_related("children").all(),
        "rca_templates": RCATemplate.objects.filter(
            Q(category=case.category) | Q(category__isnull=True)
        ).select_related("category"),
        "prev_case_id": prev_case,
        "next_case_id": next_case,
    })


@staff_required
def case_update_requester(request, case_id):
    """
    HTMX endpoint / Standard POST to update the Requester (Employee) information.
    Used mainly to fix typos for tickets incoming from WhatsApp / Email.
    """
    if request.method == "POST":
        case = get_object_or_404(CaseRecord, id=case_id)
        if not _check_confidential_access(case, request.user):
            return HttpResponseForbidden("You do not have access to this confidential ticket.")
        if _case_is_closed(case, request):
            return redirect("desk:case_detail", case_id=case_id)
        if case.requester:
            full_name = request.POST.get("full_name")
            email = request.POST.get("email")
            phone_number = request.POST.get("phone_number")
            job_role = request.POST.get("job_role")
            unit_id = request.POST.get("unit_id")

            # Track changes for audit log
            changes = []
            if full_name and full_name != case.requester.full_name:
                changes.append(("full_name", case.requester.full_name, full_name))
                case.requester.full_name = full_name
            if email and email != (case.requester.email or ""):
                changes.append(("email", case.requester.email or "", email))
                case.requester.email = email
            if phone_number and phone_number != (case.requester.phone_number or ""):
                changes.append(("phone_number", case.requester.phone_number or "", phone_number))
                case.requester.phone_number = phone_number
            if job_role and job_role != (case.requester.job_role or ""):
                changes.append(("job_role", case.requester.job_role or "", job_role))
                case.requester.job_role = job_role
            if unit_id:
                try:
                    new_unit = CompanyUnit.objects.get(id=unit_id)
                    old_unit_name = case.requester.unit.name if case.requester.unit else ""
                    if new_unit.name != old_unit_name:
                        changes.append(("unit", old_unit_name, new_unit.name))
                    case.requester.unit = new_unit
                    case.requester_unit_name = new_unit.name
                    case.save(update_fields=["requester_unit_name"])
                except CompanyUnit.DoesNotExist:
                    pass

            case.requester.save()

            # Write audit logs for each changed field
            for field_name, old_val, new_val in changes:
                CaseAuditLog.objects.create(
                    case=case,
                    action=CaseAuditLog.ActionText.UPDATED,
                    field_name=f"requester_{field_name}",
                    old_value=old_val,
                    new_value=new_val,
                    created_by=request.user,
                )

    return redirect("desk:case_detail", case_id=case_id)


@staff_required
def case_update_subject(request, case_id):
    """HTMX/POST endpoint — update the case subject (title)."""
    if request.method == "POST":
        case = get_object_or_404(CaseRecord, id=case_id)
        if not _check_confidential_access(case, request.user):
            return HttpResponseForbidden("You do not have access to this confidential ticket.")
        if _case_is_closed(case, request):
            return redirect("desk:case_detail", case_id=case_id)
        new_subject = request.POST.get("subject", "").strip()
        if new_subject and new_subject != case.subject:
            old_subject = case.subject
            case.subject = new_subject
            case.save(update_fields=["subject"])
            CaseAuditLog.objects.create(
                case=case,
                action=CaseAuditLog.ActionText.UPDATED,
                field_name="subject",
                old_value=old_subject,
                new_value=new_subject,
                created_by=request.user,
            )
    return redirect("desk:case_detail", case_id=case_id)


@staff_required
def case_update_category(request, case_id):
    """HTMX/POST endpoint — update the case category."""
    if request.method == "POST":
        case = get_object_or_404(CaseRecord, id=case_id)
        if not _check_confidential_access(case, request.user):
            return HttpResponseForbidden("You do not have access to this confidential ticket.")
        if _case_is_closed(case, request):
            return redirect("desk:case_detail", case_id=case_id)
        new_category_id = request.POST.get("category_id", "").strip()
        if new_category_id:
            new_category = get_object_or_404(CaseCategory, id=new_category_id)
            if new_category != case.category:
                old_category_name = case.category.name
                case.category = new_category
                case.save(update_fields=["category"])
                CaseAuditLog.objects.create(
                    case=case,
                    action=CaseAuditLog.ActionText.UPDATED,
                    field_name="category",
                    old_value=old_category_name,
                    new_value=new_category.name,
                    created_by=request.user,
                )
    return redirect("desk:case_detail", case_id=case_id)


@staff_required
def case_change_requester(request, case_id):
    """
    POST endpoint to change (replace) the Requester on a CaseRecord.
    Swaps the linked Employee and syncs denormalized fields.
    """
    if request.method == "POST":
        case = get_object_or_404(CaseRecord, id=case_id)
        if not _check_confidential_access(case, request.user):
            return HttpResponseForbidden("You do not have access to this confidential ticket.")
        if _case_is_closed(case, request):
            return redirect("desk:case_detail", case_id=case_id)
        new_employee_id = request.POST.get("new_employee_id")
        if new_employee_id:
            try:
                new_employee = Employee.objects.select_related("unit").get(id=new_employee_id)
                old_requester_name = case.requester.full_name if case.requester else "N/A"
                case.requester = new_employee
                case.requester_name = new_employee.full_name
                case.requester_email = new_employee.email or ""
                case.requester_job_role = new_employee.job_role or ""
                case.requester_unit_name = new_employee.unit.name if new_employee.unit else ""
                case.save(update_fields=[
                    "requester", "requester_name", "requester_email",
                    "requester_job_role", "requester_unit_name",
                ])

                CaseAuditLog.objects.create(
                    case=case,
                    action=CaseAuditLog.ActionText.UPDATED,
                    field_name="requester",
                    old_value=old_requester_name,
                    new_value=new_employee.full_name,
                    created_by=request.user,
                )
            except Employee.DoesNotExist:
                pass
    return redirect("desk:case_detail", case_id=case_id)


@staff_required
def case_forward_escalation(request, case_id):
    """
    POST endpoint to Forward or Escalate a case to an external Email or WhatsApp number.
    Triggers gateways.tasks.escalate_case_task.
    """
    if request.method == "POST":
        case = get_object_or_404(CaseRecord, id=case_id)
        if not _check_confidential_access(case, request.user):
            return HttpResponseForbidden("You do not have access to this confidential ticket.")
        if _case_is_closed(case, request):
            return redirect("desk:case_detail", case_id=case_id)
        forward_to = request.POST.get("forward_to", "").strip()
        channel = request.POST.get("channel", "EMAIL").upper()
        custom_message = request.POST.get("custom_message", "").strip()
        selected_message_ids = request.POST.get("selected_message_ids", "").strip()

        if forward_to and channel in ["EMAIL", "WHATSAPP"]:
            from cases.models import Message
            # Use body prefix "*** ESKALASI TIKET VIA" to identify it as escalation later
            msg_channel = Message.Channel.EMAIL if channel == 'EMAIL' else Message.Channel.WHATSAPP

            # Append Initials for Escalate
            staff_initials = getattr(request.user, "initials", "sys")
            full_body = f"*** TICKET ESCALATED VIA {channel} TO: {forward_to} ***\n\nInternal Agent Note:\n{custom_message}\n\n-{staff_initials}"

            msg = Message.objects.create(
                case=case,
                sender_staff=request.user,
                body=full_body,
                direction=Message.Direction.OUTBOUND,
                channel=msg_channel,
                delivery_status=Message.DeliveryStatus.PENDING,
            )

            # Fire celery task to send via API
            from gateways.tasks import escalate_case_task
            import random

            try:
                # Use apply_async with countdown to simulate human delay (2-6 seconds)
                delay_secs = random.randint(2, 6)
                escalate_case_task.apply_async(
                    args=[str(case.id), forward_to, channel, custom_message, str(msg.id)],
                    kwargs={"selected_message_ids": selected_message_ids},
                    countdown=delay_secs
                )
                from django.contrib import messages
                messages.success(request, f"Ticket successfully escalated to {forward_to} via {channel}.")
            except Exception as e:
                msg.delivery_status = Message.DeliveryStatus.FAILED
                msg.delivery_error = f"System Error: {str(e)}"
                msg.save(update_fields=["delivery_status", "delivery_error"])
                from django.contrib import messages
                messages.error(request, f"Failed to dispatch escalation task: {str(e)}")

    return redirect("desk:case_detail", case_id=case_id)


@staff_required
def case_update_rca(request, case_id):
    """
    HTMX endpoint — update root cause analysis, solving steps, status,
    and SLA fields.
    """
    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")

    if request.method == "POST":
        original_status = case.status
        form = CaseRCAForm(request.POST, instance=case)
        if form.is_valid():
            desired_status = form.cleaned_data.get("status")
            
            # SLA validation if attempting to Close for the first time
            if desired_status == CaseRecord.Status.CLOSED and original_status != CaseRecord.Status.CLOSED:
                if not form.cleaned_data.get("root_cause_analysis") or not form.cleaned_data.get("solving_steps"):
                    # Revert instance mutation so the template doesn't think it's closed yet
                    case.status = original_status
                    return render(request, "partials/rca_form.html", {
                        "case": case,
                        "rca_form": form,
                        "error_message": "SLA Details (Root Cause Analysis & Solving Steps) are required to close the ticket.",
                    })
                    
                # Save changes but revert status until confirmed in the modal
                case = form.save(commit=False)
                case.status = original_status
                case.updated_by = request.user
                
                # Check what changed before saving to create audit logs
                if form.changed_data:
                    for field in form.changed_data:
                        if field == 'status': 
                            continue # we reverted status above
                        old_val = form.initial.get(field)
                        new_val = form.cleaned_data.get(field)
                        
                        # Followers is M2M, handled after save typically, but we log the attempt
                        if field == 'followers':
                            old_val = ", ".join([u.username for u in case.followers.all()])
                            new_val = ", ".join([u.username for u in new_val]) if new_val else ""

                        CaseAuditLog.objects.create(
                            case=case,
                            action=CaseAuditLog.ActionText.UPDATED,
                            field_name=field,
                            old_value=str(old_val) if old_val else "",
                            new_value=str(new_val) if new_val else "",
                            created_by=request.user,
                        )

                case.save()
                form.save_m2m() # Important for followers
                # Check if we should notify about assignment change during RCA save

                
                if 'assigned_to' in form.changed_data and case.assigned_to:
                    from gateways.tasks import send_assignment_email_task
                    case_url = request.build_absolute_uri(reverse("desk:case_detail", kwargs={"case_id": case.id}))
                    send_assignment_email_task.delay(str(case.id), str(request.user.id), case_url)
                
                from django.template.loader import render_to_string
                form_response = render(request, "partials/rca_form.html", {
                    "case": case,
                    "rca_form": CaseRCAForm(instance=case),
                    "success_message": "Details saved. Please confirm ticket closure below.",
                })
                
                modal_html = render_to_string("partials/closure_modal.html", {"case": case}, request=request)
                oob_modal = f'<div id="htmx-modal" hx-swap-oob="innerHTML">{modal_html}</div>'
                
                return HttpResponse(form_response.content.decode() + oob_modal)

            # Normal save for non-closing updates OR amending an already closed ticket
            case = form.save(commit=False)
            case.updated_by = request.user
            
            # --- Amendment Logic ---
            if original_status == CaseRecord.Status.CLOSED and case.edit_permission_status == CaseRecord.EditPermissionStatus.APPROVED:
                if form.changed_data:
                    case.amendment_count += 1
                case.edit_permission_status = CaseRecord.EditPermissionStatus.NONE
            # -----------------------
            
            # Check what changed before saving to create audit logs
            if form.changed_data:
                for field in form.changed_data:
                    old_val = form.initial.get(field)
                    new_val = form.cleaned_data.get(field)
                    
                    action_type = CaseAuditLog.ActionText.UPDATED
                    if field == 'status':
                        action_type = CaseAuditLog.ActionText.STATUS_CHANGE
                    elif field in ['assigned_to', 'followers']:
                        action_type = CaseAuditLog.ActionText.ASSIGNED
                    
                    if field == 'followers':
                        old_val = ", ".join([u.username for u in case.followers.all()])
                        new_val = ", ".join([u.username for u in new_val]) if new_val else ""
                    elif field == 'assigned_to':
                        old_val = str(old_val) if old_val else "Unassigned"
                        new_val = str(new_val) if new_val else "Unassigned"

                    CaseAuditLog.objects.create(
                        case=case,
                        action=action_type,
                        field_name=field,
                        old_value=str(old_val) if old_val is not None else "",
                        new_value=str(new_val) if new_val is not None else "",
                        created_by=request.user,
                    )

            case.save()
            form.save_m2m() # Important for followers

            if 'assigned_to' in form.changed_data and case.assigned_to:
                from gateways.tasks import send_assignment_email_task
                case_url = request.build_absolute_uri(reverse("desk:case_detail", kwargs={"case_id": case.id}))
                send_assignment_email_task.delay(str(case.id), str(request.user.id), case_url)

            # Redirect to the previous page (likely the filtered table list)
            # using the back_url passed through the form
            redirect_url = request.POST.get("back_url", "/desk/cases/")
            
            # safeguard against infinite reloads on the same detail page
            if f"/desk/cases/{case_id}" in redirect_url:
                redirect_url = "/desk/cases/"
                
            return HttpResponse(
                f'<script>window.location.href = "{redirect_url}";</script>',
                content_type="text/html"
            )
        else:
            # Revert instance mutation if validation fails so the template logic maintains the true DB status
            case.status = original_status
            return render(request, "partials/rca_form.html", {
                "case": case,
                "rca_form": form,
                "error_message": "Please fix the errors below.",
                "back_url": request.POST.get("back_url", "/desk/cases/"),
            })

    return HttpResponse(status=405)


@staff_required
def case_send_reply(request, case_id):
    """
    HTMX endpoint — staff sends a reply message within the case thread.
    """
    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")
    if _case_is_closed(case, request):
        return redirect("desk:case_detail", case_id=case_id)

    if request.method == "POST":
        form = StaffReplyForm(request.POST, request.FILES)
        if form.is_valid():
            # Must have at least body or attachment
            if not form.cleaned_data.get("body") and not form.cleaned_data.get("attachment"):
                messages = case.messages.select_related(
                    "sender_employee", "sender_staff"
                ).prefetch_related("attachments").all()
                return render(request, "partials/chat_thread.html", {
                    "case": case,
                    "chat_messages": messages,
                })

            # Determine the channel based on the original case source
            reply_channel = Message.Channel.WEB
            
            send_as_email = request.POST.get("send_as_email") == "true"
            if case.source == CaseRecord.Source.WEBFORM and send_as_email:
                reply_channel = Message.Channel.EMAIL
            elif case.source == CaseRecord.Source.EMAIL:
                reply_channel = Message.Channel.EMAIL
            elif case.source == CaseRecord.Source.EVOLUTION_WA:
                reply_channel = Message.Channel.WHATSAPP  # Prep for future WA outbound support

            # Handle quote reply
            quoted_message = None
            quoted_id = request.POST.get("quoted_message_id", "").strip()
            if quoted_id:
                quoted_message = Message.objects.filter(id=quoted_id, case=case).first()

            msg = Message.objects.create(
                case=case,
                sender_staff=request.user,
                body=form.cleaned_data.get("body") or "",
                cc_emails=form.cleaned_data.get("cc_emails", ""),
                direction=Message.Direction.OUTBOUND,
                channel=reply_channel,
                delivery_status=Message.DeliveryStatus.PENDING if reply_channel != Message.Channel.WEB else Message.DeliveryStatus.SUCCESS,
                quoted_message=quoted_message,
            )

            # Handle optional attachment
            uploaded_file = form.cleaned_data.get("attachment")
            if uploaded_file:
                Attachment.objects.create(
                    message=msg,
                    file=uploaded_file,
                    original_filename=uploaded_file.name,
                    mime_type=getattr(uploaded_file, "content_type", ""),
                    file_size=uploaded_file.size,
                )

            # Update case timestamp
            case.updated_by = request.user
            case.save(update_fields=["updated_at", "updated_by"])

            # Trigger Outbound Async Tasks based on Channel
            try:
                if reply_channel == Message.Channel.EMAIL:
                    from gateways.tasks import send_outbound_email_task
                    send_outbound_email_task.delay(str(msg.id))
                elif reply_channel == Message.Channel.WHATSAPP:
                    from gateways.tasks import send_outbound_whatsapp_task
                    send_outbound_whatsapp_task.delay(str(msg.id))
                    
                    # Reset the 60-minute session countdown for the employee
                    from gateways.tasks import check_wa_session_warning_task, check_wa_session_timeout_task
                    check_wa_session_warning_task.apply_async((str(case.id),), countdown=2700)   # 45 min: confirmation
                    check_wa_session_timeout_task.apply_async((str(case.id),), countdown=3600)   # 60 min: expiry
            except Exception as e:
                msg.delivery_status = Message.DeliveryStatus.FAILED
                msg.delivery_error = f"Celery connection failed: {str(e)}"
                msg.save(update_fields=["delivery_status", "delivery_error"])

            # Return the refreshed chat thread partial
            messages = case.messages.select_related(
                "sender_employee", "sender_staff"
            ).prefetch_related("attachments").all()
            return render(request, "partials/chat_thread.html", {
                "case": case,
                "chat_messages": messages,
            })

    return HttpResponse(status=405)


@staff_required
def chat_thread(request, case_id):
    """
    HTMX endpoint — returns the chat thread partial for polling refresh.
    """
    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")

    unread_msg_ids = []
    if case.has_unread_messages:
        unread_msg_ids = list(case.messages.filter(direction="IN", is_read=False).values_list("id", flat=True))
        wa_external_ids = list(
            case.messages.filter(
                id__in=unread_msg_ids, channel="WhatsApp", external_id__gt=""
            ).values_list("external_id", flat=True)
        )
        case.has_unread_messages = False
        case.save(update_fields=["has_unread_messages"])
        case.messages.filter(id__in=unread_msg_ids).update(is_read=True)
        if wa_external_ids:
            from gateways.tasks import mark_wa_messages_read_task
            try:
                mark_wa_messages_read_task.delay(str(case.id), wa_external_ids)
            except Exception:
                pass

    messages = case.messages.select_related(
        "sender_employee", "sender_staff",
        "quoted_message__sender_employee", "quoted_message__sender_staff",
    ).prefetch_related("attachments", "reactions").all()

    return render(request, "partials/chat_thread.html", {
        "case": case,
        "chat_messages": messages,
        "unread_msg_ids": unread_msg_ids,
    })


# =====================================================================
# Message Actions (Delete, Edit, React)
# =====================================================================

@staff_required
@require_POST
def message_delete(request, case_id, message_id):
    """Soft-delete an outbound WhatsApp message and revoke it on WhatsApp."""
    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")
    if _case_is_closed(case, request):
        return redirect("desk:case_detail", case_id=case_id)
    msg = get_object_or_404(Message, id=message_id, case=case, direction="OUT")

    if not msg.is_deleted:
        msg.is_deleted = True
        msg.original_body = msg.body
        msg.body = ""
        msg.save(update_fields=["is_deleted", "original_body", "body"])

        if msg.channel == "WhatsApp" and msg.external_id:
            from gateways.tasks import delete_whatsapp_message_task
            try:
                delete_whatsapp_message_task.delay(str(msg.id))
            except Exception:
                pass

    messages = case.messages.select_related(
        "sender_employee", "sender_staff"
    ).prefetch_related("attachments", "reactions").all()
    return render(request, "partials/chat_thread.html", {
        "case": case,
        "chat_messages": messages,
        "unread_msg_ids": [],
    })


@staff_required
@require_POST
def message_edit(request, case_id, message_id):
    """Edit an outbound WhatsApp message text."""
    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")
    if _case_is_closed(case, request):
        return redirect("desk:case_detail", case_id=case_id)
    msg = get_object_or_404(Message, id=message_id, case=case, direction="OUT")

    new_body = request.POST.get("body", "").strip()
    if not new_body or msg.is_deleted:
        messages = case.messages.select_related(
            "sender_employee", "sender_staff"
        ).prefetch_related("attachments", "reactions").all()
        return render(request, "partials/chat_thread.html", {
            "case": case, "chat_messages": messages, "unread_msg_ids": [],
        })

    if not msg.original_body:
        msg.original_body = msg.body
    msg.body = new_body
    msg.is_edited = True
    msg.save(update_fields=["body", "is_edited", "original_body"])

    if msg.channel == "WhatsApp" and msg.external_id:
        from gateways.tasks import edit_whatsapp_message_task
        try:
            edit_whatsapp_message_task.delay(str(msg.id))
        except Exception:
            pass

    messages = case.messages.select_related(
        "sender_employee", "sender_staff"
    ).prefetch_related("attachments", "reactions").all()
    return render(request, "partials/chat_thread.html", {
        "case": case, "chat_messages": messages, "unread_msg_ids": [],
    })


@staff_required
@require_POST
def message_react(request, case_id, message_id):
    """Send an emoji reaction to a WhatsApp message."""
    from cases.models import MessageReaction

    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")
    msg = get_object_or_404(Message, id=message_id, case=case)
    emoji = request.POST.get("emoji", "").strip()

    if emoji:
        # Toggle: if same emoji already exists, remove it (unreact)
        existing = MessageReaction.objects.filter(
            message=msg, reacted_by=request.user, emoji=emoji
        ).first()
        if existing:
            existing.delete()
            # Send empty reaction to WhatsApp to remove it
            if msg.channel == "WhatsApp" and msg.external_id:
                from gateways.tasks import react_whatsapp_message_task
                try:
                    react_whatsapp_message_task.delay(str(msg.id), "")
                except Exception:
                    pass
        else:
            MessageReaction.objects.update_or_create(
                message=msg,
                reacted_by=request.user,
                defaults={"emoji": emoji},
            )
            if msg.channel == "WhatsApp" and msg.external_id:
                from gateways.tasks import react_whatsapp_message_task
                try:
                    react_whatsapp_message_task.delay(str(msg.id), emoji)
                except Exception:
                    pass

    messages = case.messages.select_related(
        "sender_employee", "sender_staff"
    ).prefetch_related("attachments", "reactions").all()
    return render(request, "partials/chat_thread.html", {
        "case": case, "chat_messages": messages, "unread_msg_ids": [],
    })


# =====================================================================
# Kanban Board
# =====================================================================

@staff_required
def case_quick_view(request, case_id):
    """
    HTMX endpoint — returns a quick view modal for a specific case card.
    """
    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")
    return render(request, "partials/quick_view_modal.html", {
        "case": case,
    })

@staff_required
def case_kanban(request):
    """
    Kanban board — cases grouped by status columns.
    Supports same filters as case_list.
    """
    cases = CaseRecord.objects.select_related(
        "requester", "requester__unit", "category", "assigned_to"
    ).all()

    # --- Filtering ---
    folder = request.GET.get("folder", "inbox")
    source_filter = request.GET.get("source")
    category_filter = request.GET.get("category")
    priority_filter = request.GET.get("priority")
    status_filter = request.GET.get("status")
    assigned_to_filter = request.GET.get("assigned_to")
    type_filter = request.GET.get("type")
    tags_filter = request.GET.get("tags", "").strip()
    followers_filter = request.GET.get("followers")
    read_filter = request.GET.get("read_status", "")

    search_query = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    # Folder specific base filtering
    if folder == "spam":
        cases = cases.filter(is_spam=True)
    elif folder == "archive":
        cases = cases.filter(is_archived=True)
    else:  # inbox
        cases = cases.filter(is_spam=False, is_archived=False, master_ticket__isnull=True)

    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if priority_filter:
        cases = cases.filter(priority=priority_filter)
    if assigned_to_filter:
        if assigned_to_filter == "unassigned":
            cases = cases.filter(assigned_to__isnull=True)
        else:
            cases = cases.filter(assigned_to_id=assigned_to_filter)
    if type_filter:
        cases = cases.filter(case_type=type_filter)
    if tags_filter:
        cases = cases.filter(tags__icontains=tags_filter)
    if followers_filter:
        cases = cases.filter(followers__id=followers_filter)
    if read_filter == "unread":
        cases = cases.filter(has_unread_messages=True)
    elif read_filter == "unopened":
        cases = cases.filter(last_viewed_at__isnull=True)
    elif read_filter == "read":
        cases = cases.filter(has_unread_messages=False, last_viewed_at__isnull=False)

    if search_query:
        cases = cases.filter(
            Q(id__icontains=search_query) |
            Q(subject__icontains=search_query) |
            Q(requester_name__icontains=search_query) |
            Q(requester__full_name__icontains=search_query) |
            Q(requester_email__icontains=search_query) |
            Q(requester__email__icontains=search_query) |
            Q(requester__phone_number__icontains=search_query) |
            Q(requester_unit_name__icontains=search_query) |
            Q(requester__unit__name__icontains=search_query)
        )
    if date_from:
        cases = cases.filter(created_at__date__gte=date_from)
    if date_to:
        cases = cases.filter(created_at__date__lte=date_to)

    # Annotate confidential access for the current user
    cases = _annotate_confidential_access(cases, request.user)

    # Group by status
    status_columns = [
        ("Open", "⏳", "Open", "#6366f1"),
        ("Investigating", "🔍", "Investigating", "#f59e0b"),
        ("PendingInfo", "⏸️", "Pending Info", "#0ea5e9"),
        ("Resolved", "✅", "Resolved", "#10b981"),
        ("Closed", "🔒", "Closed", "#64748b"),
    ]

    # If a status filter is active, only show matching columns
    if status_filter:
        status_list = [s.strip() for s in status_filter.split(',') if s.strip()]
        status_columns = [col for col in status_columns if col[0] in status_list]

    kanban_data = []
    for status_val, icon, label, color in status_columns:
        column_cases = [c for c in cases if c.status == status_val]
        kanban_data.append({
            "status": status_val,
            "icon": icon,
            "label": label,
            "color": color,
            "cases": column_cases,
            "count": len(column_cases),
        })

    # Calculate unread counts
    unread_inbox = CaseRecord.objects.filter(is_spam=False, is_archived=False, master_ticket__isnull=True, has_unread_messages=True).count()
    unread_archive = CaseRecord.objects.filter(is_archived=True, has_unread_messages=True).count()
    unread_spam = CaseRecord.objects.filter(is_spam=True, has_unread_messages=True).count()

    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    assignees = User.objects.filter(is_staff=True, is_active=True).order_by("first_name", "username")
    all_followers = User.objects.filter(is_active=True).order_by("first_name", "username")

    return render(request, "admin/case_kanban.html", {
        "kanban_data": kanban_data,
        "sources": CaseRecord.Source.choices,
        "categories": CaseCategory.objects.all(),
        "priorities": CaseRecord.Priority.choices,
        "types": CaseRecord.Type.choices,
        "assignees": assignees,
        "all_followers": all_followers,
        "current_filters": {
            "folder": folder,
            "status": status_filter or "",
            "source": source_filter or "",
            "category": category_filter or "",
            "priority": priority_filter or "",
            "assigned_to": assigned_to_filter or "",
            "type": type_filter or "",
            "tags": tags_filter or "",
            "followers": followers_filter or "",
            "read_status": read_filter or "",
            "q": search_query,
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
        "unread_inbox": unread_inbox,
        "unread_archive": unread_archive,
        "unread_spam": unread_spam,
    })


@staff_required
def case_update_status(request, case_id):
    """
    HTMX endpoint — update case status (drag-and-drop from Kanban).
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")
    new_status = request.POST.get("status", "")

    valid_statuses = [s[0] for s in CaseRecord.Status.choices]
    if new_status not in valid_statuses:
        return HttpResponse("Invalid status", status=400)

    # Prevent direct closing without SLA details from Kanban
    if new_status == CaseRecord.Status.CLOSED:
        if not case.root_cause_analysis or not case.solving_steps:
            return HttpResponse("SLA details (Root Cause Analysis & Solving Steps) are required before closing. Please click the case to open the detail panel and fill them out.", status=400)
            
        # Generate and return the modal directly
        return render(request, "partials/closure_modal.html", {"case": case})

    case.status = new_status
    case.updated_by = request.user
    case.save(update_fields=["status", "updated_at", "updated_by"])

    return HttpResponse(status=204)


@staff_required
def case_close_and_notify(request, case_id):
    """
    HTMX endpoint — processes the final SLA closure message from the preview modal,
    closes the ticket, and sends the OUTBOUND message via WA/Email.
    """
    if request.method != "POST":
        return HttpResponse(status=405)
        
    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")
    closure_msg_body = request.POST.get("closure_message_body", "").strip()
    
    if not closure_msg_body:
        return HttpResponse("Message body cannot be empty.", status=400)
        
    # Mark the case as closed
    case.status = CaseRecord.Status.CLOSED
    case.updated_by = request.user
    case.save(update_fields=["status", "updated_at", "updated_by"])
    
    # Determine the reply channel based on the source
    reply_channel = Message.Channel.WEB
    if case.source in [CaseRecord.Source.WEBFORM, CaseRecord.Source.EMAIL]:
        reply_channel = Message.Channel.EMAIL
    elif case.source == CaseRecord.Source.EVOLUTION_WA:
        reply_channel = Message.Channel.WHATSAPP
        
    # Create the Outbound Message containing the closure payload
    msg = Message.objects.create(
        case=case,
        sender_staff=request.user,
        body=closure_msg_body,
        direction=Message.Direction.OUTBOUND,
        channel=reply_channel,
    )
    
    # Trigger appropriate sending mechanisms
    if reply_channel == Message.Channel.EMAIL:
        from gateways.tasks import send_outbound_email_task
        send_outbound_email_task.delay(str(msg.id))
    elif reply_channel == Message.Channel.WHATSAPP:
        from gateways.tasks import send_outbound_whatsapp_task
        msg.delivery_status = Message.DeliveryStatus.PENDING
        msg.save(update_fields=["delivery_status"])
        send_outbound_whatsapp_task.delay(str(msg.id))
                
    # Return a success script to trigger a reload or update
    return HttpResponse(
        '<script>window.location.reload();</script>',
        content_type="text/html"
    )

@staff_required
def case_request_edit(request, case_id):
    """
    HTMX endpoint — Staff requests permission to edit a closed ticket.
    """
    if request.method != "POST":
        return HttpResponse(status=405)
        
    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")
    reason = request.POST.get("reason", "").strip()
    
    if case.status != CaseRecord.Status.CLOSED:
        return HttpResponse("Ticket is not closed.", status=400)
        
    case.edit_permission_status = CaseRecord.EditPermissionStatus.REQUESTED
    case.edit_requested_by = request.user
    case.edit_request_reason = reason
    case.save(update_fields=["edit_permission_status", "edit_requested_by", "edit_request_reason"])
    
    CaseAuditLog.objects.create(
        case=case,
        action=CaseAuditLog.ActionText.UPDATED,
        field_name="edit_permission_status",
        old_value="None",
        new_value="Requested",
        created_by=request.user,
    )
    
    return HttpResponse('<script>window.location.reload();</script>')

@staff_required
def case_approve_edit(request, case_id):
    """
    HTMX endpoint — SuperAdmin/Manager approves an edit request.
    """
    if request.method != "POST":
        return HttpResponse(status=405)
        
    if not (request.user.is_superuser or request.user.role_access == "Manager"):
        return HttpResponse("Unauthorized", status=403)

    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")

    case.edit_permission_status = CaseRecord.EditPermissionStatus.APPROVED
    case.save(update_fields=["edit_permission_status"])
    
    CaseAuditLog.objects.create(
        case=case,
        action=CaseAuditLog.ActionText.UPDATED,
        field_name="edit_permission_status",
        old_value="Requested",
        new_value="Approved",
        created_by=request.user,
    )
    
    return HttpResponse('<script>window.location.reload();</script>')

@staff_required
def case_reject_edit(request, case_id):
    """
    HTMX endpoint — SuperAdmin/Manager rejects an edit request.
    """
    if request.method != "POST":
        return HttpResponse(status=405)
        
    if not (request.user.is_superuser or request.user.role_access == "Manager"):
        return HttpResponse("Unauthorized", status=403)

    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")

    case.edit_permission_status = CaseRecord.EditPermissionStatus.REJECTED
    case.save(update_fields=["edit_permission_status"])
    
    CaseAuditLog.objects.create(
        case=case,
        action=CaseAuditLog.ActionText.UPDATED,
        field_name="edit_permission_status",
        old_value="Requested",
        new_value="Rejected",
        created_by=request.user,
    )
    
    return HttpResponse('<script>window.location.reload();</script>')

    
# =====================================================================
# Calendar View
# =====================================================================

@staff_required
def case_calendar(request):
    """
    Calendar view — shows cases on a monthly calendar.
    """
    import json
    from django.utils.timezone import localtime

    cases = CaseRecord.objects.select_related(
        "requester", "requester__unit", "category", "assigned_to"
    ).all()

    folder = request.GET.get("folder", "inbox")
    source_filter = request.GET.get("source")
    category_filter = request.GET.get("category")
    status_filter = request.GET.get("status")
    assigned_to_filter = request.GET.get("assigned_to")
    type_filter = request.GET.get("type")
    tags_filter = request.GET.get("tags", "").strip()
    followers_filter = request.GET.get("followers")
    read_filter = request.GET.get("read_status", "")

    search_query = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    # Folder specific base filtering
    if folder == "spam":
        cases = cases.filter(is_spam=True)
    elif folder == "archive":
        cases = cases.filter(is_archived=True)
    else:  # inbox
        cases = cases.filter(is_spam=False, is_archived=False, master_ticket__isnull=True)

    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if status_filter:
        status_list = [s.strip() for s in status_filter.split(',') if s.strip()]
        cases = cases.filter(status__in=status_list)
    if assigned_to_filter:
        if assigned_to_filter == "unassigned":
            cases = cases.filter(assigned_to__isnull=True)
        else:
            cases = cases.filter(assigned_to_id=assigned_to_filter)
    if type_filter:
        cases = cases.filter(case_type=type_filter)
    if tags_filter:
        cases = cases.filter(tags__icontains=tags_filter)
    if followers_filter:
        cases = cases.filter(followers__id=followers_filter)
    if read_filter == "unread":
        cases = cases.filter(has_unread_messages=True)
    elif read_filter == "unopened":
        cases = cases.filter(last_viewed_at__isnull=True)
    elif read_filter == "read":
        cases = cases.filter(has_unread_messages=False, last_viewed_at__isnull=False)

    if search_query:
        cases = cases.filter(
            Q(id__icontains=search_query) |
            Q(subject__icontains=search_query) |
            Q(requester_name__icontains=search_query) |
            Q(requester__full_name__icontains=search_query) |
            Q(requester_email__icontains=search_query) |
            Q(requester__email__icontains=search_query) |
            Q(requester__phone_number__icontains=search_query) |
            Q(requester_unit_name__icontains=search_query) |
            Q(requester__unit__name__icontains=search_query)
        )
    if date_from:
        cases = cases.filter(created_at__date__gte=date_from)
    if date_to:
        cases = cases.filter(created_at__date__lte=date_to)

    # Annotate confidential access for the current user
    cases = _annotate_confidential_access(cases, request.user)

    # --- Dynamic date source for calendar ---
    date_source = request.GET.get("date_source", "created_at")
    DATE_SOURCE_FIELDS = {
        "created_at": "created_at",
        "last_viewed_at": "last_viewed_at",
        "resolution_due_at": "resolution_due_at",
        "response_due_at": "response_due_at",
    }
    date_field = DATE_SOURCE_FIELDS.get(date_source, "created_at")

    # Build JSON events for the calendar
    status_colors = {
        "Open": "#6366f1",
        "Investigating": "#f59e0b",
        "PendingInfo": "#0ea5e9",
        "Resolved": "#10b981",
        "Closed": "#64748b",
    }

    events = []
    for c in cases:
        dt_value = getattr(c, date_field, None)
        if not dt_value:
            continue  # Skip cases without the selected date
        has_access = getattr(c, "has_confidential_access_flag", True)
        events.append({
            "id": str(c.id),
            "title": f"[{c.case_number}] {'Confidential Ticket' if not has_access else c.subject[:40]}",
            "start": localtime(dt_value).strftime("%Y-%m-%d"),
            "url": f"/desk/cases/{c.id}/" if has_access else "",
            "color": "#94a3b8" if not has_access else status_colors.get(c.status, "#6366f1"),
            "status": c.status,
            "requester": c.requester.full_name if c.requester else "—",
        })

    # Calculate unread counts
    unread_inbox = CaseRecord.objects.filter(is_spam=False, is_archived=False, master_ticket__isnull=True, has_unread_messages=True).count()
    unread_archive = CaseRecord.objects.filter(is_archived=True, has_unread_messages=True).count()
    unread_spam = CaseRecord.objects.filter(is_spam=True, has_unread_messages=True).count()

    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    assignees = User.objects.filter(is_staff=True, is_active=True).order_by("first_name", "username")
    all_followers = User.objects.filter(is_active=True).order_by("first_name", "username")

    return render(request, "admin/case_calendar.html", {
        "events_json": json.dumps(events),
        "statuses": CaseRecord.Status.choices,
        "sources": CaseRecord.Source.choices,
        "categories": CaseCategory.objects.all(),
        "types": CaseRecord.Type.choices,
        "assignees": assignees,
        "all_followers": all_followers,
        "current_filters": {
            "folder": folder,
            "status": status_filter or "",
            "source": source_filter or "",
            "category": category_filter or "",
            "assigned_to": assigned_to_filter or "",
            "type": type_filter or "",
            "tags": tags_filter or "",
            "followers": followers_filter or "",
            "read_status": read_filter or "",
            "q": search_query,
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
        "unread_inbox": unread_inbox,
        "unread_archive": unread_archive,
        "unread_spam": unread_spam,
        "date_source": date_source,
    })


# =====================================================================
# Export to Excel
# =====================================================================

@staff_required
def case_export_excel(request):
    """
    Export all cases to Excel (.xlsx) with all fields.
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from django.utils.timezone import localtime

    cases = CaseRecord.objects.select_related(
        "requester", "requester__unit", "category", "assigned_to",
        "created_by", "updated_by"
    ).prefetch_related("followers").order_by("-created_at")

    # Apply filters if provided
    folder = request.GET.get("folder", "inbox")
    status_filter = request.GET.get("status")
    source_filter = request.GET.get("source")
    category_filter = request.GET.get("category")
    search_query = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    # Folder specific base filtering
    if folder == "spam":
        cases = cases.filter(is_spam=True)
    elif folder == "archive":
        cases = cases.filter(is_archived=True)
    else:  # inbox
        cases = cases.filter(is_spam=False, is_archived=False, master_ticket__isnull=True)

    if status_filter:
        cases = cases.filter(status=status_filter)
    if source_filter:
        cases = cases.filter(source=source_filter)
    if category_filter:
        cases = cases.filter(category__slug=category_filter)
    if search_query:
        cases = cases.filter(subject__icontains=search_query)
    if date_from:
        cases = cases.filter(created_at__date__gte=date_from)
    if date_to:
        cases = cases.filter(created_at__date__lte=date_to)

    # Annotate confidential access for the current user
    cases = _annotate_confidential_access(cases, request.user)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tickets"

    # Header style
    header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin", color="D1D5DB"),
        right=Side(style="thin", color="D1D5DB"),
        top=Side(style="thin", color="D1D5DB"),
        bottom=Side(style="thin", color="D1D5DB"),
    )

    headers = [
        "No", "Ticket Number", "Subject", "Status", "Priority", "Type", "Source", "Category",
        "Requester Name", "Requester Email", "Requester Phone",
        "Requester Unit", "Requester Job Role",
        "Problem Description", "Root Cause Analysis", "Solving Steps", "Quick Notes",
        "Tags", "Followers", "Reference Link",
        "Assigned To", "Response SLA", "Resolution SLA",
        "Created At", "Updated At",
    ]

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

    # Data rows
    data_align = Alignment(vertical="top", wrap_text=True)
    MASKED = "[Confidential]"
    for row_idx, case in enumerate(cases, 2):
        has_access = getattr(case, "has_confidential_access_flag", True)
        row_data = [
            row_idx - 1,
            case.case_number,
            case.subject if has_access else MASKED,
            case.get_status_display(),
            case.get_priority_display(),
            case.get_case_type_display(),
            case.get_source_display(),
            case.category.name if case.category else "",
            case.requester.full_name if case.requester else case.requester_name,
            case.requester.email if case.requester else case.requester_email,
            case.requester.phone_number if case.requester else "",
            case.requester.unit.name if case.requester and case.requester.unit else case.requester_unit_name,
            case.requester.job_role if case.requester else case.requester_job_role,
            (case.problem_description or "") if has_access else MASKED,
            (case.root_cause_analysis or "") if has_access else MASKED,
            (case.solving_steps or "") if has_access else MASKED,
            (case.quick_notes or "") if has_access else MASKED,
            case.tags or "",
            ", ".join([f.username for f in case.followers.all()]) if has_access else MASKED,
            (case.link or "") if has_access else MASKED,
            (case.assigned_to.username if case.assigned_to else "Unassigned") if has_access else MASKED,
            localtime(case.response_due_at).strftime("%d/%m/%Y %H:%M") if case.response_due_at else "",
            localtime(case.resolution_due_at).strftime("%d/%m/%Y %H:%M") if case.resolution_due_at else "",
            localtime(case.created_at).strftime("%d/%m/%Y %H:%M") if case.created_at else "",
            localtime(case.updated_at).strftime("%d/%m/%Y %H:%M") if case.updated_at else "",
        ]

        for col_idx, value in enumerate(row_data, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = data_align
            cell.border = thin_border

    # Auto-width columns
    for col_idx in range(1, len(headers) + 1):
        col_letter = get_column_letter(col_idx)
        max_length = len(str(headers[col_idx - 1]))
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=col_idx, max_col=col_idx):
            for cell in row:
                if cell.value:
                    max_length = max(max_length, min(len(str(cell.value)), 50))
        ws.column_dimensions[col_letter].width = max_length + 4

    # Freeze header row
    ws.freeze_panes = "A2"

    # Response
    from io import BytesIO
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    now_str = timezone.now().strftime("%Y%m%d_%H%M")
    filename = f"RoC_Desk_Cases_{now_str}.xlsx"

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# =====================================================================
# RCA Templates — Create / Delete
# =====================================================================

@manager_or_admin_required
@require_POST
def rca_template_create(request):
    """Create a new RCA Template. Managers/SuperAdmins only."""
    name = request.POST.get("name", "").strip()
    rca_text = request.POST.get("rca_text", "").strip()
    solving_steps_text = request.POST.get("solving_steps_text", "").strip()
    category_id = request.POST.get("category_id", "").strip()
    redirect_case_id = request.POST.get("redirect_case_id", "").strip()

    if name:
        category = None
        if category_id:
            category = CaseCategory.objects.filter(id=category_id).first()
        RCATemplate.objects.create(
            name=name,
            rca_text=rca_text,
            solving_steps_text=solving_steps_text,
            category=category,
            created_by=request.user,
            updated_by=request.user,
        )
        messages.success(request, f"Template '{name}' created.")
    else:
        messages.error(request, "Template name is required.")

    if redirect_case_id:
        return redirect("desk:case_detail", case_id=redirect_case_id)
    return redirect("desk:case_list")


@manager_or_admin_required
@require_POST
def rca_template_delete(request, template_id):
    """Delete an RCA Template. Managers/SuperAdmins only."""
    tpl = get_object_or_404(RCATemplate, id=template_id)
    tpl_name = tpl.name
    tpl.delete()
    messages.success(request, f"Template '{tpl_name}' deleted.")

    redirect_case_id = request.POST.get("redirect_case_id", "").strip()
    if redirect_case_id:
        return redirect("desk:case_detail", case_id=redirect_case_id)
    return redirect("desk:case_list")


@staff_required
def notification_bell(request):
    """
    HTMX endpoint — returns a dropdown of recent cases and unread incoming messages (last 24 hours).
    """
    from datetime import timedelta
    from django.utils import timezone
    
    time_threshold = timezone.now() - timedelta(hours=24)
    
    # Get previously clicked notifications from the session
    read_notifs = request.session.get("read_notifications", [])
    read_case_ids = [n.replace("case_", "") for n in read_notifs if n.startswith("case_")]
    read_msg_ids = [n.replace("msg_", "") for n in read_notifs if n.startswith("msg_")]
    read_mention_ids = [n.replace("mention_", "") for n in read_notifs if n.startswith("mention_")]
    
    # 1. New Tickets in the last 24h (exclude confidential without access)
    _conf_filter = Q()  # no filter needed
    if request.user.role_access != "SuperAdmin" and not request.user.can_handle_confidential:
        _conf_filter = Q(category__is_confidential=False)
    recent_cases = CaseRecord.objects.select_related("requester").filter(
        created_at__gte=time_threshold
    ).filter(_conf_filter).exclude(id__in=read_case_ids).order_by("-created_at")[:10]

    # 2. Unread Incoming Messages in the last 24h (exclude confidential without access)
    _msg_conf_filter = Q()
    if request.user.role_access != "SuperAdmin" and not request.user.can_handle_confidential:
        _msg_conf_filter = Q(case__category__is_confidential=False)
    recent_unread_messages = Message.objects.select_related(
        "case", "sender_employee"
    ).filter(
        direction=Message.Direction.INBOUND,
        is_read=False,
        created_at__gte=time_threshold
    ).filter(_msg_conf_filter).exclude(id__in=read_msg_ids).order_by("-created_at")[:10]
    
    # 3. Recent Mentions in Internal Notes
    recent_mentions = CaseComment.objects.select_related("case", "author").filter(
        mentions=request.user,
        created_at__gte=time_threshold
    ).exclude(id__in=read_mention_ids).order_by("-created_at")[:10]
    
    total_notifications = recent_cases.count() + recent_unread_messages.count() + recent_mentions.count()
    
    return render(request, "partials/notification_bell.html", {
        "recent_cases": recent_cases,
        "recent_unread_messages": recent_unread_messages,
        "recent_mentions": recent_mentions,
        "total_notifications": total_notifications,
    })


@staff_required
def mark_notification_read(request, notif_type, notif_id):
    """
    Marks a specific notification as 'read' in the user's session 
    so it no longer appears in the bell dropdown, then redirects
    to the target case detail URL.
    """
    notif_key = f"{notif_type}_{notif_id}"
    read_notifs = request.session.get("read_notifications", [])
    if notif_key not in read_notifs:
        read_notifs.append(notif_key)
        request.session["read_notifications"] = read_notifs
    
    # Both types ultimately lead to the Ticket Detail page
    target_case_id = notif_id
    if notif_type == "msg":
        try:
            msg = Message.objects.get(id=notif_id)
            target_case_id = msg.case.id
        except Message.DoesNotExist:
            target_case_id = None
    elif notif_type == "mention":
        try:
            comment = CaseComment.objects.get(id=notif_id)
            target_case_id = comment.case.id
        except CaseComment.DoesNotExist:
            target_case_id = None
            
    if target_case_id:
        return redirect("desk:case_detail", case_id=target_case_id)
    return redirect("desk:case_list")


# =====================================================================
# Internal Ticket Comments
# =====================================================================

@staff_required
def case_add_comment(request, case_id):
    """
    HTMX endpoint for saving and retrieving internal staff comments on a case.
    Also parses @username mentions.
    """
    import re
    from core.models import User
    
    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")

    if request.method == "POST":
        body = request.POST.get("comment_body", "").strip()
        if body:
            comment = CaseComment.objects.create(
                case=case,
                author=request.user,
                body=body
            )
            
            # Parse @usernames
            usernames = set(re.findall(r"@([a-zA-Z0-9_]+)", body))
            if usernames:
                mentioned_users = User.objects.filter(username__in=usernames)
                if mentioned_users.exists():
                    comment.mentions.add(*mentioned_users)

    # Return the refreshed comments partial
    return render(request, "partials/case_comments.html", {
        "case": case,
        "comments": case.internal_comments.all(),
    })

# =====================================================================
# WhatsApp Integration Dashboard
# =====================================================================

@staff_required
def whatsapp_status_view(request):
    """
    Dashboard view for monitoring Evolution API WhatsApp connection.
    Retrieves instance state and QR code if requested/disconnected.
    """
    from gateways.services import EvolutionAPIService
    from datetime import datetime
    
    svc = EvolutionAPIService()
    state_data = svc.get_instance_state()
    info_data = svc.get_instance_info()
    
    qr_data = None
    
    # Check if we are connected; if not, fetch QR code
    instance_state = state_data.get("instance", {}).get("state", "UNKNOWN") if state_data else "ERROR"
    
    last_connected = None
    if info_data and "updatedAt" in info_data:
        # e.g. "2026-02-28T06:50:37.380Z" -> Convert to aware datetime
        dt_str = info_data.get("updatedAt").replace('Z', '+00:00')
        try:
            last_connected = datetime.fromisoformat(dt_str)
        except ValueError:
            pass
            
    if instance_state not in ["open", "connected"]:
        # Instance is likely disconnected, fetching QR
        qr_data = svc.get_qr_code()
        
    qr_base64 = None
    if qr_data and "base64" in qr_data:
        qr_base64 = qr_data.get("base64")
        
    # If the user is on HTMX, just return the partial piece to swap
    if request.headers.get('HX-Request') == 'true':
        return render(request, "partials/whatsapp_status_card.html", {
            "instance_state": instance_state,
            "qr_base64": qr_base64,
            "last_connected": last_connected,
        })
        
    return render(request, "desk/whatsapp_status.html", {
        "instance_state": instance_state,
        "qr_base64": qr_base64,
        "last_connected": last_connected,
    })


# =====================================================================
# Email Settings Dashboard
# =====================================================================

@staff_required
def email_settings_view(request):
    """
    Dashboard view for monitoring and updating global Email Settings.
    Requires Staff/Admin access.
    """
    from core.models import EmailConfig
    from core.forms import EmailConfigForm
    from django.contrib import messages

    email_config = EmailConfig.get_solo()

    if request.method == "POST":
        form = EmailConfigForm(request.POST, instance=email_config)
        if form.is_valid():
            form.save()
            messages.success(request, "Email configuration updated successfully! Background services will use the new credentials on their next run.")
            return redirect("desk:email_settings")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = EmailConfigForm(instance=email_config)

    return render(request, "desk/email_settings.html", {
        "form": form,
    })


# =====================================================================
# Dynamic Form Builder
# =====================================================================

@staff_required
def form_list_view(request):
    """
    List of all Dynamic Forms.
    """
    from core.models import DynamicForm
    from django.db.models import Q
    from django.core.paginator import Paginator

    query = request.GET.get('q', '').strip()
    
    forms_qs = DynamicForm.objects.all().order_by('-created_at')
    
    if query:
        forms_qs = forms_qs.filter(
            Q(title__icontains=query) | 
            Q(slug__icontains=query)
        )

    paginator = Paginator(forms_qs, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, "desk/forms/list.html", {
        "forms": page_obj,
        "search_query": query
    })


@staff_required
def form_create_view(request):
    """
    Create a new DynamicForm (Settings only).
    """
    from core.forms import DynamicFormForm
    from django.contrib import messages
    import uuid

    if request.method == "POST":
        form = DynamicFormForm(request.POST, request.FILES)
        if form.is_valid():
            new_form = form.save(commit=False)
            new_form.created_by = request.user
            # Ensure slug is truly unique if not explicitly provided or if duplicate
            if not new_form.slug:
                new_form.slug = str(uuid.uuid4())[:8]
            new_form.save()
            messages.success(request, f"Form '{new_form.title}' created! Now add some fields.")
            return redirect("desk:form_edit", pk=new_form.pk)
    else:
        form = DynamicFormForm()

    return render(request, "desk/forms/create.html", {"form": form})


@staff_required
@require_POST
def form_delete_view(request, pk):
    """Delete a DynamicForm and all related fields/submissions."""
    from core.models import DynamicForm
    from django.contrib import messages

    form = get_object_or_404(DynamicForm, pk=pk)
    title = form.title
    form.delete()
    messages.success(request, f"Form '{title}' has been deleted.")
    return redirect("desk:form_list")


@staff_required
@require_POST
def form_duplicate_view(request, pk):
    """
    Duplicate a DynamicForm and all its fields.
    Does NOT copy background_image and header_image.
    Appends '(copy)' to the title and generates a new slug.
    """
    from core.models import DynamicForm, FormField
    from django.contrib import messages
    import uuid

    original_form = get_object_or_404(DynamicForm, pk=pk)

    # Clone the form instance
    new_form = DynamicForm.objects.get(pk=pk)
    new_form.pk = None
    new_form.id = uuid.uuid4()
    new_form.title = f"{original_form.title} (copy)"
    # Ensure slug is unique by generating a new one
    new_form.slug = str(uuid.uuid4())[:8]
    new_form.created_by = request.user
    
    # Do NOT copy images
    new_form.background_image = None
    new_form.header_image = None
    
    new_form.save()

    # Clone all fields, maintaining order
    original_fields = original_form.fields.all()
    for field in original_fields:
        field.pk = None
        field.id = uuid.uuid4()
        field.form = new_form
        field.save()

    messages.success(request, f"Form duplicated successfully!")
    return redirect("desk:form_edit", pk=new_form.pk)


@staff_required
def form_edit_view(request, pk):
    """
    Drag and drop builder to edit form fields and form settings.
    """
    from core.models import DynamicForm, FormField
    from core.forms import DynamicFormForm
    from django.contrib import messages
    from django.db import models
    import json
    import uuid

    instance = get_object_or_404(DynamicForm, pk=pk)
    
    # Handle AJAX/HTMX Field updates
    if request.headers.get('HX-Request') == 'true':
        action = request.POST.get('action')
        
        if action == 'add_field':
            field_type = request.POST.get('field_type', FormField.FieldTypes.TEXT)
            label = request.POST.get('label', 'New Question')
            help_text = request.POST.get('help_text', '')
            is_required = request.POST.get('is_required') == 'true'
            
            choices = []
            if field_type in [FormField.FieldTypes.DROPDOWN, FormField.FieldTypes.RADIO, FormField.FieldTypes.CHECKBOX, FormField.FieldTypes.SURVEY]:
                choices = request.POST.getlist('choices[]')
                
            # Put at bottom
            last_order = instance.fields.count()
            
            FormField.objects.create(
                form=instance,
                field_type=field_type,
                label=label,
                help_text=help_text,
                is_required=is_required,
                choices=choices,
                order=last_order + 1,
                created_by=request.user
            )
            return render(request, "desk/forms/partials/field_list.html", {"fields": instance.fields.all(), "dynamic_form": instance})
            
        elif action == 'duplicate_field':
            field_id = request.POST.get('field_id')
            original_field = get_object_or_404(FormField, id=field_id, form=instance)
            
            # Shift order of subsequent fields down by 1
            FormField.objects.filter(form=instance, order__gt=original_field.order).update(
                order=models.F('order') + 1
            )
            
            # Clone field
            new_field = original_field
            new_field.pk = None
            new_field.id = uuid.uuid4()
            new_field.order = original_field.order + 1
            # Append copy to label so user knows it's the duplicate
            new_field.label = f"{original_field.label} (copy)"
            new_field.save()
            
            return render(request, "desk/forms/partials/field_list.html", {"fields": instance.fields.all(), "dynamic_form": instance})
            
        elif action == 'delete_field':
            field_id = request.POST.get('field_id')
            FormField.objects.filter(id=field_id, form=instance).delete()
            return render(request, "desk/forms/partials/field_list.html", {"fields": instance.fields.all(), "dynamic_form": instance})
            
        elif action == 'reorder':
            order_data = json.loads(request.POST.get('order_data', '[]'))
            for item in order_data:
                FormField.objects.filter(id=item['id'], form=instance).update(order=item['order'])
            return HttpResponse(status=200)

        elif action == 'edit_field':
            field_id = request.POST.get('field_id')
            field = get_object_or_404(FormField, id=field_id, form=instance)
            
            field.label = request.POST.get('label', field.label)
            field.help_text = request.POST.get('help_text', '')
            field.is_required = request.POST.get('is_required') == 'true'
            
            new_type = request.POST.get('field_type')
            if new_type:
                field.field_type = new_type
            
            if field.field_type in [FormField.FieldTypes.DROPDOWN, FormField.FieldTypes.RADIO, FormField.FieldTypes.CHECKBOX, FormField.FieldTypes.SURVEY]:
                choices = request.POST.getlist('choices[]')
                if choices:
                    field.choices = choices
            else:
                field.choices = []
            
            field.updated_by = request.user
            field.save()
            return render(request, "desk/forms/partials/field_list.html", {"fields": instance.fields.all(), "dynamic_form": instance})

        elif action == 'send_invitation':
            from django.core.mail import send_mail
            from django.conf import settings
            import json as _json

            raw_emails = request.POST.get('emails', '')
            email_list = [e.strip() for e in raw_emails.replace(';', ',').split(',') if e.strip()]
            form_url = request.build_absolute_uri(f'/f/{instance.slug}/')
            
            sent_count = 0
            for email in email_list:
                try:
                    send_mail(
                        subject=f'You are invited to fill out: {instance.title}',
                        message=f'Hello,\n\nYou have been invited to fill out the form "{instance.title}".\n\n{instance.description}\n\nPlease click the link below to access the form:\n{form_url}\n\nBest regards,\n{request.user.get_full_name() or request.user.username}',
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[email],
                        fail_silently=True,
                    )
                    sent_count += 1
                except Exception:
                    pass
            
            return JsonResponse({'status': 'ok', 'sent': sent_count})

    # Standard form updating
    if request.method == "POST" and not request.headers.get('HX-Request'):
        form = DynamicFormForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            f = form.save(commit=False)
            f.updated_by = request.user
            f.save()
            messages.success(request, "Form settings updated successfully.")
            return redirect("desk:form_edit", pk=instance.pk)
    else:
        form = DynamicFormForm(instance=instance)

    return render(request, "desk/forms/builder.html", {
        "dynamic_form": instance,
        "form": form,
        "fields": instance.fields.all()
    })


@staff_required
def form_responses_view(request, pk):
    """
    View all submissions for a specific DynamicForm.
    Also calculates aggregated metrics for choice-based fields to power the dashboard.
    """
    from core.models import DynamicForm
    from django.core.paginator import Paginator
    from django.db.models import Q
    import collections
    
    instance = get_object_or_404(DynamicForm, pk=pk)
    
    query = request.GET.get('q', '').strip()
    
    submissions_qs = instance.submissions.all().order_by('-submitted_at')
    
    if query:
        submissions_qs = submissions_qs.filter(
            Q(submitted_by__email__icontains=query) |
            Q(submitted_by__first_name__icontains=query) |
            Q(submitted_by__last_name__icontains=query) |
            Q(submitted_by__username__icontains=query)
        )
        
    paginator = Paginator(submissions_qs, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    fields = instance.fields.all()
    
    # Calculate metrics for chart/progress bar visualization
    metrics = {}
    
    # Pre-filter fields that are choice-based
    choice_fields = [f for f in fields if f.field_type in ['dropdown', 'radio', 'checkbox', 'survey']]
    
    if choice_fields and submissions_qs.exists():
        # Iterate over all submissions recursively for metrics instead of just the page
        for field in choice_fields:
            field_id_str = str(field.id)
            counter = collections.Counter()
            
            for sub in submissions_qs:
                ans = sub.answers.get(field_id_str)
                if ans:
                    if isinstance(ans, list):
                        for a in ans:
                            counter[a] += 1
                    else:
                        counter[ans] += 1
            
            total_responses = sum(counter.values())
            
            if total_responses > 0:
                # Get Top 5 most frequent
                top_5 = counter.most_common(5)
                top_5_count = sum(count for _, count in top_5)
                
                # If there are more than 5 distinct answers, group remaining as "Others"
                others_count = total_responses - top_5_count
                
                chart_data = []
                for label, count in top_5:
                    pct = int(round((count / total_responses) * 100)) if total_responses else 0
                    chart_data.append({'label': label, 'count': count, 'percent': pct})
                
                if others_count > 0:
                    pct = int(round((others_count / total_responses) * 100)) if total_responses else 0
                    chart_data.append({'label': 'Others', 'count': others_count, 'percent': pct, 'is_other': True})
                
                metrics[field.id] = {
                    'total': total_responses,
                    'chart_data': chart_data
                }
    
    return render(request, "desk/forms/responses.html", {
        "dynamic_form": instance,
        "submissions": page_obj,
        "search_query": query,
        "fields": fields,
        "metrics": metrics
    })


@staff_required
def form_responses_export(request, pk):
    """
    Export all submissions for a DynamicForm to an Excel (.xlsx) file.
    """
    from core.models import DynamicForm
    import openpyxl
    from django.http import HttpResponse
    
    instance = get_object_or_404(DynamicForm, pk=pk)
    submissions = instance.submissions.all()
    fields = instance.fields.all()
    
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    filename = f"{instance.title}_responses_{instance.pk}.xlsx"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Responses"
    
    # Header Row
    header = ['Date Submitted', 'Submitted By']
    field_ids = []
    
    for f in fields:
        # We export all fields except structural ones
        if f.field_type not in ['title_desc', 'page_break']:
            header.append(f.label)
            field_ids.append(str(f.id))
            
    ws.append(header)
    
    # Data Rows
    for sub in submissions:
        username = "Guest User"
        if sub.submitted_by:
            username = f"{sub.submitted_by.first_name} {sub.submitted_by.last_name}".strip()
            if not username:
                username = sub.submitted_by.username
                
        row = [
            sub.submitted_at.replace(tzinfo=None) if sub.submitted_at else "", # Excel prefers naive datetimes
            username
        ]
        
        for fid in field_ids:
            ans = sub.answers.get(fid, "")
            
            # Format lists (e.g checkboxes, multiple attachments) safely
            if isinstance(ans, list):
                # If the list consists of dicts or complicated strings, try to flatten it
                row.append(" | ".join([str(a) for a in ans]))
            else:
                row.append(str(ans))
                
        ws.append(row)
        
    wb.save(response)
    return response

# =====================================================================
# API Endpoints
# =====================================================================

@login_required
def api_users_list(request):
    """
    Returns a JSON list of active users to power the @mention autocomplete.
    """
    from django.http import JsonResponse
    from core.models import User
    
    users = User.objects.filter(is_active=True).values("username", "first_name", "last_name")
    
    data = []
    for u in users:
        # Prefer full name if exists, otherwise fallback to username
        name = f"{u['first_name']} {u['last_name']}".strip()
        display = name if name else u['username']
        
        data.append({
            "key": display,
            "value": u['username']
        })
        
    return JsonResponse(data, safe=False)

@login_required
@require_POST
def toggle_wa_session(request, case_id):
    """
    Toggle the hold_wa_session flag for a specific case via HTMX.
    """
    case = get_object_or_404(CaseRecord, id=case_id)
    if not _check_confidential_access(case, request.user):
        return HttpResponseForbidden("You do not have access to this confidential ticket.")

    # Check permissions
    if request.user.role_access not in ['SuperAdmin', 'Manager', 'SupportDesk']:
        return HttpResponseForbidden("You do not have permission to perform this action.")
        
    case.hold_wa_session = not case.hold_wa_session
    case.save(update_fields=['hold_wa_session'])
    
    # Return the updated button HTML
    if case.hold_wa_session:
        return HttpResponse(f"""
        <button type="button" 
                hx-post="{request.path}" 
                hx-swap="outerHTML" 
                class="jk-btn jk-btn-sm border border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100 shadow-sm"
                title="WA Session (Currently Held)">
            <svg class="w-3.5 h-3.5 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 9v6m4-6v6m7-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
            Release Session
        </button>
        """)
    else:
        return HttpResponse(f"""
        <button type="button" 
                hx-post="{request.path}" 
                hx-swap="outerHTML" 
                class="jk-btn jk-btn-sm" 
                style="background:rgba(255,255,255,0.12); border:1px solid rgba(255,255,255,0.2); color:#cbd5e1;" 
                title="WA Session (Currently Active)">
            <svg class="w-3.5 h-3.5 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
            Hold Session
        </button>
        """)
