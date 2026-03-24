"""Template tags/filters for the Knowledge Base app."""
import re

from django import template
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe

register = template.Library()


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
