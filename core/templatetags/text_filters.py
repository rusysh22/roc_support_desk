from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe
import re

register = template.Library()

@register.filter(needs_autoescape=True)
def urlize_target_blank(value, autoescape=True):
    """
    Converts URLs in text into clickable links that open in a new tab.
    Works by first escaping the text, then applying a regex to find URLs and convert them.
    Unlike Django's built-in urlize, this adds target="_blank" and rel="noopener noreferrer".
    """
    if not value:
        return value
        
    if autoescape:
        value = escape(value)
        
    # Basic Regex for finding URLs (http, https, ftp)
    url_pattern = re.compile(
        r'((?:http|ftp)s?://' # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|' #domain...
        r'localhost|' #localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})' # ...or ip
        r'(?::\d+)?' # optional port
        r'(?:/?|[/?]\S+))', re.IGNORECASE)

    def replace_url(match):
        url = match.group(0)
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer" class="text-indigo-600 hover:underline hover:text-indigo-800 break-all">{url}</a>'
        
    return mark_safe(url_pattern.sub(replace_url, value))
