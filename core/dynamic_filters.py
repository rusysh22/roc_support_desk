"""
Generic field + operator + value filter builder for list views.

Each view declares an allowlist of filterable fields (never trust a raw
field name from the querystring directly against the ORM — that's how you
accidentally expose unrelated model relations or let someone probe schema),
then calls apply_dynamic_filters() to both filter the queryset and get back
the data the "Advanced Filter" template partial needs to render active-filter
chips and the add-row builder.

Usage in a view::

    from core.dynamic_filters import apply_dynamic_filters, fields_json

    FILTERABLE_FIELDS = {
        "email": {"label": "Email", "type": "text", "lookup": "email"},
        "role_access": {
            "label": "Role", "type": "choice", "lookup": "role_access",
            "choices": User.RoleAccess.choices,
        },
        "is_active": {
            "label": "Status", "type": "choice", "lookup": "is_active",
            "choices": [("true", "Active"), ("false", "Inactive")],
            "python_type": "bool",  # coerce "true"/"false" to real booleans for .filter()
        },
    }

    qs, active_filters = apply_dynamic_filters(qs, request, FILTERABLE_FIELDS)
    context["dynamic_fields_json"] = fields_json(FILTERABLE_FIELDS)
    context["active_filters"] = active_filters
    context["has_active_filters"] = bool(search or active_filters)

Template side: include partials/list/_dynamic_filter_builder.html as the
page's filter_panel_template (see templates/partials/list/_toolbar.html).

Field meta also accepts:
- "distinct": True — call .distinct() after filtering this field, for a
  many-to-many lookup (e.g. "categories__id") that can otherwise duplicate
  rows once per matching related row.
"""
import json

from django.db.models import Q

OPERATOR_LABELS = {
    "contains": "Contains",
    "equals": "Equals",
    "is_set": "Is Set",
    "is_not_set": "Is Not Set",
}

# Which operators make sense per field type. "choice" skips "contains" —
# matching a dropdown/boolean value against a substring isn't meaningful.
TYPE_OPERATORS = {
    "text": ["contains", "equals", "is_set", "is_not_set"],
    "choice": ["equals", "is_set", "is_not_set"],
}

# Operators that don't need a value at all (rendered with no value input).
NO_VALUE_OPERATORS = {"is_set", "is_not_set"}


def _coerce_value(value, meta):
    """Turn the raw querystring string into what .filter() expects."""
    if meta.get("python_type") == "bool":
        return value == "true"
    return value


def apply_dynamic_filters(queryset, request, fields):
    """
    Read repeated filter_field / filter_op / filter_value GET params (one
    triple per row from the filter builder UI) and apply them as AND
    conditions against `queryset`. Unknown field keys or operators not
    valid for that field's type are silently skipped rather than raising,
    so a stale/hand-edited URL never 500s.

    Returns (queryset, active_filters) — active_filters is a list of dicts
    ready for the chip template, each carrying its own `remove_url` (the
    current querystring with just that one condition removed).
    """
    raw_fields = request.GET.getlist("filter_field")
    raw_ops = request.GET.getlist("filter_op")
    raw_values = request.GET.getlist("filter_value")

    active_filters = []
    for i, (field_key, op, value) in enumerate(zip(raw_fields, raw_ops, raw_values)):
        meta = fields.get(field_key)
        if not meta or op not in OPERATOR_LABELS:
            continue
        if op not in TYPE_OPERATORS.get(meta["type"], []):
            continue
        lookup = meta["lookup"]
        needs_value = op not in NO_VALUE_OPERATORS
        if needs_value and not value:
            continue

        if op == "contains":
            queryset = queryset.filter(**{f"{lookup}__icontains": value})
        elif op == "equals":
            queryset = queryset.filter(**{lookup: _coerce_value(value, meta)})
        elif op == "is_set":
            q = ~Q(**{f"{lookup}__isnull": True})
            if meta["type"] == "text":
                q &= ~Q(**{lookup: ""})
            queryset = queryset.filter(q)
        elif op == "is_not_set":
            q = Q(**{f"{lookup}__isnull": True})
            if meta["type"] == "text":
                q |= Q(**{lookup: ""})
            queryset = queryset.filter(q)

        if meta.get("distinct"):
            # Filtering across a many-to-many lookup (e.g. "categories__id")
            # can duplicate rows — one per matching related row.
            queryset = queryset.distinct()

        value_label = value
        if meta["type"] == "choice":
            choice_map = dict(meta.get("choices", []))
            value_label = choice_map.get(value, value)

        active_filters.append({
            "field_key": field_key,
            "field_label": meta["label"],
            "op": op,
            "op_label": OPERATOR_LABELS[op],
            "value": value,
            "value_label": value_label if needs_value else "",
            "remove_url": _remove_url(request, raw_fields, raw_ops, raw_values, i),
        })

    return queryset, active_filters


def _remove_url(request, raw_fields, raw_ops, raw_values, index):
    """Current querystring with the filter triple at `index` dropped, page reset."""
    qd = request.GET.copy()
    qd.pop("filter_field", None)
    qd.pop("filter_op", None)
    qd.pop("filter_value", None)
    qd.pop("page", None)
    for i, (f, o, v) in enumerate(zip(raw_fields, raw_ops, raw_values)):
        if i == index:
            continue
        qd.appendlist("filter_field", f)
        qd.appendlist("filter_op", o)
        qd.appendlist("filter_value", v)
    encoded = qd.urlencode()
    return f"{request.path}?{encoded}" if encoded else request.path


def active_filters_json(active_filters):
    """Trim active_filters down to what the Alpine builder needs to rehydrate rows."""
    return json.dumps([
        {"field_key": af["field_key"], "op": af["op"], "value": af["value"]}
        for af in active_filters
    ])


def fields_json(fields):
    """Serialize the fields allowlist to JSON for the Alpine filter builder."""
    out = []
    for key, meta in fields.items():
        entry = {
            "key": key,
            "label": meta["label"],
            "type": meta["type"],
            "operators": TYPE_OPERATORS.get(meta["type"], []),
        }
        if meta["type"] == "choice":
            entry["choices"] = [{"value": str(v), "label": label} for v, label in meta.get("choices", [])]
        out.append(entry)
    return json.dumps(out)
