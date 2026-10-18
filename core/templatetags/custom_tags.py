from django import template
from django.utils.safestring import mark_safe
from django.http import QueryDict

register = template.Library()


# ---------------------------------------------------------------------------
# Shared icon set for the standardized list-view toolbar/bulk-bar/rows.
# Heroicons-outline style paths, matching the SVGs already hand-copied
# across list templates — centralized here so they're defined once.
# ---------------------------------------------------------------------------
_ICON_PATHS = {
    "search": "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z",
    "filter": "M3 4a1 1 0 011-1h16a1 1 0 01.8 1.6l-6.3 8.4v5.6a1 1 0 01-1.45.9l-4-2A1 1 0 019 18.5v-4.5L2.2 5.6A1 1 0 013 4z",
    "chevron-down": "M19 9l-7 7-7-7",
    "trash": "M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16",
    "archive": "M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4",
    "unarchive": "M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z",
    "duplicate": "M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z",
    "export": "M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4",
    "close": "M6 18L18 6M6 6l12 12",
    "plus": "M12 4v16m8-8H4",
}


@register.simple_tag(name='icon')
def icon_tag(name, css_class="h-4 w-4"):
    """
    Render one of the shared inline SVG icons.
    Usage: {% icon "trash" css_class="h-5 w-5 text-red-500" %}
    """
    path = _ICON_PATHS.get(name)
    if not path:
        return ""
    return mark_safe(
        f'<svg class="{css_class}" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
        f'<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="{path}"/></svg>'
    )


@register.inclusion_tag('partials/_status_pill.html')
def status_pill(value, meta_map):
    """
    Render a standardized status pill using a per-module meta map.

    meta_map: {"Active": {"label": "Active", "badge_class": "jk-badge-resolved"}, ...}
    Falls back to a neutral pill if the value isn't in the map, so an
    unrecognized status never crashes the list page.
    """
    meta = (meta_map or {}).get(value, {})
    return {
        "label": meta.get("label", value),
        "badge_class": meta.get("badge_class", "jk-badge-cancelled"),
    }


@register.filter(name='get_item')
def get_item(dictionary, key):
    """
    Template filter to allow dict key lookup by variable.
    Usage: {{ mydict|get_item:item.name }}
    """
    if isinstance(dictionary, dict):
        return dictionary.get(str(key))
    return None


@register.filter(name='idle_time')
def idle_time(value):
    """
    Returns human-readable elapsed time since the given datetime.
    E.g. '2d 5h', '45m', '3h'
    """
    if not value:
        return ""
    from django.utils import timezone
    now = timezone.now()
    delta = now - value
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "0m"
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


@register.filter(name='idle_level')
def idle_level(value):
    """
    Returns urgency level based on elapsed time: 'danger' (>2d), 'warning' (>12h), 'info'.
    """
    if not value:
        return ""
    from django.utils import timezone
    delta = timezone.now() - value
    hours = delta.total_seconds() / 3600
    if hours >= 48:
        return "danger"
    elif hours >= 12:
        return "warning"
    return "info"


_APPROVAL_STATUS_META = {
    "PendingApproval": {"label": "On Approval", "color": "#14b8a6", "icon": "⏳", "badge_class": "jk-badge-approval"},
    "RevisionRequired": {"label": "Revision Required", "color": "#f97316", "icon": "✏️", "badge_class": "jk-badge-revision"},
}


@register.filter(name='status_meta')
def status_meta(value):
    """
    Ticket status -> {label, color, icon} for the status_badge.html
    partial. Looks up CaseRecord.STATUS_META (core lifecycle statuses),
    falling back to the approval-routing statuses it deliberately
    excludes, then to a generic fallback for anything unrecognized.
    """
    from cases.models import CaseRecord
    meta = CaseRecord.STATUS_META.get(value)
    if meta:
        return meta
    meta = _APPROVAL_STATUS_META.get(value)
    if meta:
        return meta
    return {"label": str(value), "color": "#94a3b8", "icon": "", "badge_class": "jk-badge-cancelled"}


@register.filter(name='status_label')
def status_label(value):
    """Human-readable label for a status code, e.g. 'InProgress' -> 'In Progress'.
    Used where only the label is needed (e.g. JS string literals) so callers
    don't have to pull it out of the status_meta dict via {% with %}."""
    if not value:
        return ""
    return status_meta(value).get("label", value)


@register.filter(name='is_valid_phone')
def is_valid_phone(value):
    """
    Returns True if the phone number looks like a valid E.164 WhatsApp number.
    Invalid examples: LID numbers (too long), non-numeric strings.
    """
    if not value:
        return False
    raw = str(value).lstrip("+")
    return raw.isdigit() and 7 <= len(raw) <= 15


@register.tag('split_by_page_break')
def split_by_page_break(parser, token):
    """
    Splits a queryset of FormField objects into a list of lists (pages),
    splitting on fields with field_type == 'page_break'.

    Usage:  {% split_by_page_break fields as pages %}
    """
    bits = token.split_contents()
    if len(bits) != 4 or bits[2] != 'as':
        raise template.TemplateSyntaxError(
            "Usage: {% split_by_page_break <fields_var> as <pages_var> %}"
        )
    return SplitByPageBreakNode(bits[1], bits[3])


class SplitByPageBreakNode(template.Node):
    def __init__(self, fields_var, pages_var):
        self.fields_var = template.Variable(fields_var)
        self.pages_var = pages_var

    def render(self, context):
        fields = self.fields_var.resolve(context)
        pages = [[]]
        for field in fields:
            if field.field_type == 'page_break':
                # Start a new page; include the page_break field itself
                # so templates can reference it if needed
                pages.append([])
            else:
                pages[-1].append(field)
        # Remove any empty trailing pages
        pages = [p for p in pages if p]
        if not pages:
            pages = [[]]
        context[self.pages_var] = pages
        return ''


@register.tag('has_page_break')
def has_page_break(parser, token):
    """
    Check if any field in the queryset has field_type 'page_break'.
    Usage: {% has_page_break fields as is_multipage %}
    """
    bits = token.split_contents()
    if len(bits) != 4 or bits[2] != 'as':
        raise template.TemplateSyntaxError(
            "Usage: {% has_page_break <fields_var> as <result_var> %}"
        )
    return HasPageBreakNode(bits[1], bits[3])


class HasPageBreakNode(template.Node):
    def __init__(self, fields_var, result_var):
        self.fields_var = template.Variable(fields_var)
        self.result_var = result_var

    def render(self, context):
        fields = self.fields_var.resolve(context)
        context[self.result_var] = any(
            f.field_type == 'page_break' for f in fields
        )
        return ''


@register.simple_tag(takes_context=True)
def page_url(context, page_number):
    """?query for another page of a list: keeps every current GET param (repeated
    keys included, properly URL-encoded) and swaps in ``page``."""
    request = context.get("request")
    params = request.GET.copy() if request else None
    if params is None:
        return f"?page={page_number}"
    params["page"] = page_number
    return "?" + params.urlencode()


@register.simple_tag(takes_context=True)
def per_page_url(context, size):
    """?query for the same list with a different rows-per-page: keeps every current
    GET param, sets ``per_page`` and goes back to page 1."""
    request = context.get("request")
    params = request.GET.copy() if request else QueryDict(mutable=True)
    params["per_page"] = size
    params.pop("page", None)
    return "?" + params.urlencode()


@register.simple_tag(takes_context=True)
def without_search_url(context):
    """?query for the same list without the search box (``q``): keeps every other filter
    and goes back to page 1."""
    request = context.get("request")
    params = request.GET.copy() if request else QueryDict(mutable=True)
    params.pop("q", None)
    params.pop("page", None)
    return "?" + params.urlencode()


@register.filter(name="page_window")
def page_window(page_obj, radius=2):
    """Page numbers to show around the current page, with None marking a gap:
    e.g. [1, None, 4, 5, 6, 7, 8, None, 20]."""
    total, current = page_obj.paginator.num_pages, page_obj.number
    radius = int(radius)
    pages = {1, total} | set(range(max(1, current - radius), min(total, current + radius) + 1))
    out, prev = [], 0
    for n in sorted(pages):
        if n - prev > 1:
            out.append(None)
        out.append(n)
        prev = n
    return out
