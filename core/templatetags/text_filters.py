from django import template
from django.utils.html import escape, urlize
from django.utils.safestring import mark_safe
import re

import bleach

register = template.Library()


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
    Quill-generated HTML is sanitized using bleach before being marked safe.
    Plain text gets urlized so raw URLs become clickable links.
    """
    if not value:
        return ''
    stripped = value.strip()
    if stripped.startswith('<'):
        allowed_tags = [
            'p', 'br', 'strong', 'b', 'em', 'i', 'u', 'a', 
            'ul', 'ol', 'li', 'span', 'div', 'h1', 'h2', 'h3',
            'h4', 'h5', 'h6', 'blockquote', 'pre', 'code', 'img'
        ]
        allowed_attrs = {
            '*': ['class', 'style'],
            'a': ['href', 'target', 'rel', 'title'],
            'img': ['src', 'alt', 'width', 'height']
        }
        clean_html = bleach.clean(
            stripped, 
            tags=allowed_tags, 
            attributes=allowed_attrs, 
            strip=True
        )
        return mark_safe(clean_html)
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
        return ""
    plain = bleach.clean(value, tags=[], attributes={}, strip=True)
    return " ".join(plain.split())


@register.filter(is_safe=True)
def sanitize_html(value):
    """Sanitize raw HTML using bleach, preserving safe tags and attributes."""
    if not value:
        return ''
    allowed_tags = [
        'p', 'br', 'strong', 'b', 'em', 'i', 'u', 'a', 
        'ul', 'ol', 'li', 'span', 'div', 'h1', 'h2', 'h3',
        'h4', 'h5', 'h6', 'blockquote', 'pre', 'code', 'img',
        'table', 'thead', 'tbody', 'tr', 'th', 'td'
    ]
    allowed_attrs = {
        '*': ['class', 'style', 'id'],
        'a': ['href', 'target', 'rel', 'title'],
        'img': ['src', 'alt', 'width', 'height', 'title']
    }
    clean_html = bleach.clean(
        value, 
        tags=allowed_tags, 
        attributes=allowed_attrs, 
        strip=True
    )
    return mark_safe(clean_html)


@register.simple_tag
def is_msg_self(msg, user_direction, current_user_email=""):
    """
    Multi-party chat: determine if a message was sent by the current viewer.

    For INBOUND messages when current_user_email is set, we compare against
    sender_employee.email to distinguish requester from follower messages.
    Returns "true" or "false" as a string for template use.
    """
    if msg.direction != user_direction:
        return "false"
    # If this is an INBOUND message and we know the current user's email,
    # verify the actual sender matches
    if user_direction == "IN" and current_user_email and hasattr(msg, "sender_employee") and msg.sender_employee:
        sender_email = (msg.sender_employee.email or "").lower()
        if sender_email and sender_email != current_user_email.lower():
            return "false"
    return "true"
