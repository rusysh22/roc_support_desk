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
