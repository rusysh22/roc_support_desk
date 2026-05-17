"""Template tags/filters for the Knowledge Base app."""
import re

import markdown as _md

from django import template
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(name="render_md")
def render_md(text):
    """Render a Markdown string to safe HTML for display in a kb-content block."""
    if not text:
        return mark_safe("")
    rendered = _md.markdown(
        text,
        extensions=["extra", "nl2br", "sane_lists", "toc"],
    )
    
    import bleach
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
        rendered, 
        tags=allowed_tags, 
        attributes=allowed_attrs, 
        strip=True
    )
    return mark_safe(clean_html)


@register.filter(name="highlight")
def highlight(text, query):
    """Strip HTML, truncate around the first match, and highlight all
    occurrences of *query* with a <mark> tag.

    Usage: {{ article.problem_summary|highlight:query }}

    Returns ~30 words of context centred on the first match so the user
    can immediately see *why* the article matched.
    """
    if not query or not text:
        return text

    # 1. Strip HTML tags to get plain text
    plain = strip_tags(text)

    # 2. Escape HTML entities in the plain text so it's safe
    from django.utils.html import escape
    plain = escape(plain)
    query_escaped = escape(query)

    # 3. Find the first occurrence (case-insensitive) to centre the snippet
    lower_plain = plain.lower()
    lower_query = query_escaped.lower()
    idx = lower_plain.find(lower_query)

    if idx == -1:
        # Query not found in this field — just truncate
        words = plain.split()
        snippet = " ".join(words[:30])
        if len(words) > 30:
            snippet += "…"
        return mark_safe(snippet)

    # 4. Build a snippet: ~150 chars before and after the match
    start = max(0, idx - 150)
    end = min(len(plain), idx + len(query_escaped) + 150)
    snippet = plain[start:end]

    # Add ellipsis if truncated
    if start > 0:
        snippet = "…" + snippet
    if end < len(plain):
        snippet += "…"

    # 5. Highlight all occurrences of the query (case-insensitive)
    pattern = re.compile(re.escape(query_escaped), re.IGNORECASE)
    snippet = pattern.sub(
        lambda m: f'<mark class="bg-yellow-200 text-yellow-900 px-0.5 rounded">{m.group()}</mark>',
        snippet,
    )

    return mark_safe(snippet)
