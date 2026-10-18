"""Ticket Portal service cards: per-user favorites and usage (recently / most used).

Usage is counted from the user's own tickets. A ticket counts for its own category and
for every category above it, so a top-level card shows the use of everything under it.
"""
from django.db.models import Count, Max, Q

from .models import CaseCategory, CaseRecord, FavoriteService


def user_tickets(user):
    match = Q(requester__user=user) | Q(created_by=user)
    if user.email:
        match |= Q(requester__email__iexact=user.email)
    return CaseRecord.objects.filter(match)


def _lineage(category_id, parents):
    """The category and all its ancestors (guarded against cycles)."""
    seen = []
    while category_id and category_id not in seen:
        seen.append(category_id)
        category_id = parents.get(category_id)
    return seen


def service_usage(user):
    """{category id: {"count": tickets, "last": latest ticket datetime}} for every category
    that has the user's tickets in it or below it."""
    if not user.is_authenticated:
        return {}
    parents = dict(CaseCategory.objects.values_list("id", "parent_id"))
    usage = {}
    rows = user_tickets(user).order_by().values("category_id").annotate(n=Count("id", distinct=True), last=Max("created_at"))
    for row in rows:
        for category_id in _lineage(row["category_id"], parents):
            entry = usage.setdefault(category_id, {"count": 0, "last": None})
            entry["count"] += row["n"]
            if row["last"] and (entry["last"] is None or row["last"] > entry["last"]):
                entry["last"] = row["last"]
    return usage


def annotate_cards(categories, user, usage=None, favorites=None):
    """Set is_favorite / use_count / last_used (epoch seconds) on each category for the cards."""
    favorites = favorite_ids(user) if favorites is None else favorites
    usage = service_usage(user) if usage is None else usage
    for category in categories:
        stats = usage.get(category.id, {})
        category.is_favorite = category.id in favorites
        category.use_count = stats.get("count", 0)
        category.last_used = int(stats["last"].timestamp()) if stats.get("last") else 0
    return categories


def favorite_subcategories(user, exclude_slugs=()):
    """Starred categories below the top level, with their path, for the portal's ★ filter."""
    if not user.is_authenticated:
        return []
    categories = list(
        CaseCategory.objects.filter(favorited_by__user=user, parent__isnull=False)
        .exclude(slug__in=exclude_slugs).prefetch_related("children").order_by("name")
    )
    names = dict(CaseCategory.objects.values_list("id", "name"))
    parents = dict(CaseCategory.objects.values_list("id", "parent_id"))
    for category in categories:
        ancestors = _lineage(category.parent_id, parents)
        category.path_label = " › ".join(names[c] for c in reversed(ancestors))
    return categories


def favorite_ids(user):
    if not user.is_authenticated:
        return set()
    return set(FavoriteService.objects.filter(user=user).values_list("category_id", flat=True))


def toggle_favorite(user, category):
    """Star or unstar; returns the new state."""
    deleted, _ = FavoriteService.objects.filter(user=user, category=category).delete()
    if deleted:
        return False
    FavoriteService.objects.create(user=user, category=category)
    return True
