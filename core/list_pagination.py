"""
Standardized pagination for the list views (25 rows/page by default everywhere).

Function-based views should call paginate(request, queryset) and merge the
returned dict into their template context so templates/partials/list/_pagination.html
(which expects `page_obj`) works identically whether the view is function-based
or a class-based ListView (use PageSizeMixin, which provides page_obj/paginator/
is_paginated under those same names).

Rows per page: the user picks 25 / 50 / 75 / 100 / All from the "Show" dropdown
(templates/partials/list/_page_size.html) → ``?per_page=``. The choice is remembered
in the session per list, so it survives paging, filtering and coming back later.
"""
from django.core.paginator import Paginator

LIST_PAGE_SIZE = 25
PAGE_SIZE_OPTIONS = (25, 50, 75, 100)
PAGE_SIZE_ALL = "all"


def _session_key(request, key):
    return f"list_page_size:{key or request.path}"


def get_page_size(request, default=LIST_PAGE_SIZE, key=None):
    """
    Rows per page for this list: ``?per_page=`` if valid (and remember it), else the
    remembered choice, else ``default``. Returns an int, or PAGE_SIZE_ALL.
    ``key`` names the list in the session; defaults to the request path — pass one
    explicitly when several URLs serve the same list (e.g. page + htmx partials).
    """
    session_key = _session_key(request, key)
    raw = (request.GET.get("per_page") or "").strip().lower()
    if raw == PAGE_SIZE_ALL or (raw.isdigit() and int(raw) in PAGE_SIZE_OPTIONS):
        value = PAGE_SIZE_ALL if raw == PAGE_SIZE_ALL else int(raw)
        if hasattr(request, "session"):
            request.session[session_key] = value
        return value
    stored = request.session.get(session_key) if hasattr(request, "session") else None
    if stored == PAGE_SIZE_ALL or stored in PAGE_SIZE_OPTIONS:
        return stored
    return default


def make_paginator(queryset, page_size):
    """Paginator for a page size from get_page_size() — 'all' puts every row on one page."""
    if page_size == PAGE_SIZE_ALL:
        count = queryset.count() if hasattr(queryset, "count") and not isinstance(queryset, list) else len(queryset)
        return Paginator(queryset, max(count, 1))
    return Paginator(queryset, page_size)


def page_size_context(page_size):
    return {"page_size": page_size, "page_size_options": PAGE_SIZE_OPTIONS}


def paginate(request, queryset, page_size=None, key=None, default=LIST_PAGE_SIZE):
    size = page_size or get_page_size(request, default=default, key=key)
    paginator = make_paginator(queryset, size)
    page_obj = paginator.get_page(request.GET.get("page"))
    return {
        "paginator": paginator,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "object_list": page_obj.object_list,
        **page_size_context(size),
    }


class PageSizeMixin:
    """For ListViews: honours the Show 25/50/75/100/All choice (replaces paginate_by)."""
    paginate_by = LIST_PAGE_SIZE

    def get_paginate_by(self, queryset):
        size = get_page_size(self.request, default=self.paginate_by)
        self._page_size = size
        if size == PAGE_SIZE_ALL:
            return max(queryset.count(), 1)
        return size

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(page_size_context(getattr(self, "_page_size", self.paginate_by)))
        return ctx
