from django import template
from django.utils.html import format_html, format_html_join

from cases.category_tree import _data_attrs, category_rows

register = template.Library()


@register.simple_tag
def category_options(value="slug", selected=""):
    """<option>s for the whole category tree, for a <select data-category-cascade> (see static/js/category-cascade.js).
    ``value`` is the option value: "slug" (filters) or "id" (forms); ``selected`` is the current value."""
    selected = str(selected or "")
    pieces = []
    for row in category_rows():
        val = str(row["slug"] if value == "slug" else row["id"])
        attrs = _data_attrs(row)
        pieces.append((val, "selected" if val == selected else "", attrs["data-key"], attrs["data-parent"], attrs["data-leaf"], row["name"]))
    return format_html_join(
        "",
        '<option value="{}" {} data-key="{}" data-parent="{}" data-leaf="{}">{}</option>',
        pieces,
    )
