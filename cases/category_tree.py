"""Category dropdowns shown as a cascade (group, then sub-category, ...) by static/js/category-cascade.js.

The script needs every category in the <select> (groups included), each option carrying
``data-key`` (its id), ``data-parent`` (the parent's id) and ``data-leaf`` (1 = final level, 0 = has sub-categories).
"""
from django import forms

from .models import CaseCategory


def category_rows(exclude_slugs=()):
    rows = list(
        CaseCategory.objects.exclude(slug__in=exclude_slugs)
        .order_by("name").values("id", "slug", "name", "parent_id")
    )
    parents = {r["parent_id"] for r in rows if r["parent_id"]}
    for r in rows:
        r["leaf"] = r["id"] not in parents
    return rows


def _data_attrs(row):
    return {"data-key": str(row["id"]), "data-parent": str(row["parent_id"] or ""), "data-leaf": "1" if row["leaf"] else "0"}


class CategoryTreeSelect(forms.Select):
    """A model-choice select whose options are the whole category tree, picked level by level in the browser.
    The field's own queryset still decides what is accepted on submit."""

    def __init__(self, attrs=None, exclude_slugs=("whatsapp-general", "email-general")):
        super().__init__({"data-category-cascade": "", **(attrs or {})})
        self.exclude_slugs = tuple(exclude_slugs)

    def optgroups(self, name, value, attrs=None):
        selected = {str(v) for v in (value or [])}
        groups = []
        first = list(self.choices)[:1]
        if first and first[0][0] in ("", None):
            groups.append((None, [self.create_option(name, "", first[0][1], not selected, 0)], 0))
        for row in category_rows(self.exclude_slugs):
            pk = str(row["id"])
            index = len(groups)
            option = self.create_option(name, pk, row["name"], pk in selected, index)
            option["attrs"].update(_data_attrs(row))  # Select drops create_option(attrs=...)
            groups.append((None, [option], index))
        return groups
