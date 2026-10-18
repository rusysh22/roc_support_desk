"""
Shared helpers for the standardized list-view bulk actions
(Archive / Unarchive / Delete / Duplicate / Export) used across
E-Sign, Form Creator, Shared Docs, Shorten Link, Knowledge Management,
Location, Document Templates, Job Roles, Company Units, Employees, and
User Management.

Each module keeps its own thin, permission-checked view function
(mirroring the existing user_bulk_delete / document_bulk_delete
convention) and calls into these helpers for the actual work, rather
than routing through one shared dispatched URL — permission levels
differ per module.
"""


def get_selected(request, queryset):
    """Filter a queryset down to the checkbox-selected rows.

    Reads the `selected_ids` POST list that the shared bulk-action bar
    (templates/partials/list/_bulk_bar.html) submits.
    """
    ids = request.POST.getlist("selected_ids")
    if not ids:
        return queryset.none()
    return queryset.filter(pk__in=ids)


def bulk_set_archived(queryset, field_name="is_archived", value=True):
    """Set the archive flag on every row in the queryset.

    Returns the number of rows updated.
    """
    return queryset.update(**{field_name: value})


def bulk_delete(queryset):
    """Hard-delete every row in the queryset. Returns the count deleted."""
    count = queryset.count()
    queryset.delete()
    return count
