from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe
import re

register = template.Library()

from django.utils.html import urlize

@register.filter(needs_autoescape=True)
def urlize_target_blank(value, autoescape=True):
    """
    Converts URLs in text into clickable links that open in a new tab.
    Uses Django's built-in urlize for robust URL parsing including query params,
    then injects target="_blank" and Tailwind styling.
    """
    if not value:
        return value

    # urlize already handles escaping internally if autoescape=True
    html = urlize(value, autoescape=autoescape)

    # Inject our attributes into the generated <a> tags
    html = html.replace('<a href=', '<a target="_blank" rel="noopener noreferrer" class="text-indigo-600 hover:underline hover:text-indigo-800 break-all" href=')

    return mark_safe(html)


@register.filter(is_safe=True)
def render_chat_body(value):
    """
    Render a chat message body for display in a bubble.
    Quill-generated HTML (starts with '<') is passed through as-is.
    Plain text gets urlized so raw URLs become clickable links.
    """
    if not value:
        return ''
    stripped = value.strip()
    if stripped.startswith('<'):
        return mark_safe(stripped)
    html = urlize(stripped, autoescape=True)
    html = html.replace(
        '<a href=',
        '<a target="_blank" rel="noopener noreferrer" class="underline opacity-80 hover:opacity-100 break-all" href='
    )
    return mark_safe(html)


@register.filter
def strip_html_tags(value):
    """Strip HTML tags and collapse whitespace — used for quote previews."""
    if not value:
        return ''
    clean = re.sub(r'<[^>]+>', ' ', value)
    return re.sub(r'\s+', ' ', clean).strip()
