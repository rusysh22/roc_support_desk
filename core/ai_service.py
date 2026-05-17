"""
core/ai_service.py
==================
Google Gemini RAG service for the AI Assistant widget.

Handles:
- Knowledge retrieval (full-text search over KB articles + Manual pages)
- Prompt construction
- Gemini API call
- Rate limiting via Django cache (per session/IP)

All parameters are driven by AIConfig (admin-configurable singleton).
No hardcoded values.
"""
import hashlib
import logging

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Knowledge retrieval
# ─────────────────────────────────────────────────────────────────────

def _extract_manual_page_text(content_json: dict) -> str:
    """
    Recursively extract plain text from a TipTap JSON document.
    ManualPage.content is stored as TipTap JSON.
    """
    if not content_json:
        return ""
    text_parts = []

    def _walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                text_parts.append(node.get("text", ""))
            for child in node.get("content", []):
                _walk(child)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(content_json)
    return " ".join(text_parts).strip()


def search_knowledge(query: str, sources: str, max_docs: int) -> list[dict]:
    """
    Full-text search over published KB articles and/or Manual pages.

    Returns a list of dicts:
        {title, snippet, source_type ('kb'|'manual'), url}

    Uses PostgreSQL's icontains for broad matching; ranking is done by
    counting query word hits so the most relevant docs come first.
    """
    from django.urls import reverse

    results = []
    query_words = [w.lower() for w in query.split() if len(w) > 2]

    def relevance_score(text: str) -> int:
        """Count how many query words appear in the text."""
        text_lower = text.lower()
        return sum(1 for w in query_words if w in text_lower)

    # ── KB Articles ────────────────────────────────────────────────────
    if sources in ("kb_only", "both"):
        try:
            from knowledge_base.models import Article

            # Only published articles
            qs = Article.objects.filter(is_published=True)

            # Filter: at least one word appears in title or any content field
            from django.db.models import Q
            q_filter = Q()
            for word in query_words:
                q_filter |= (
                    Q(title__icontains=word)
                    | Q(problem_summary__icontains=word)
                    | Q(root_cause__icontains=word)
                    | Q(solution__icontains=word)
                )
            if q_filter:
                qs = qs.filter(q_filter)

            for article in qs[:max_docs * 2]:  # fetch extra, rank below
                full_text = " ".join([
                    article.title,
                    article.problem_summary,
                    article.root_cause,
                    article.solution,
                ])
                score = relevance_score(full_text)
                if score == 0:
                    continue

                # Build a clean snippet (strip HTML tags simply)
                import re
                clean = re.sub(r"<[^>]+>", " ", article.problem_summary or article.solution or "")
                snippet = clean[:300].strip()

                try:
                    url = reverse("knowledge_base:article_detail", kwargs={"slug": article.slug})
                except Exception:
                    url = ""

                results.append({
                    "title": article.title,
                    "snippet": snippet,
                    "content": "\n".join(filter(None, [
                        f"Title: {article.title}",
                        f"Problem Summary: {re.sub(r'<[^>]+>', ' ', article.problem_summary or '')}",
                        f"Root Cause: {re.sub(r'<[^>]+>', ' ', article.root_cause or '')}",
                        f"Solution: {re.sub(r'<[^>]+>', ' ', article.solution or '')}",
                    ])),
                    "source_type": "kb",
                    "source_label": "Knowledge Base",
                    "url": url,
                    "_score": score,
                })
        except Exception:
            logger.exception("Error searching KB articles")

    # ── User Manual Pages ──────────────────────────────────────────────
    if sources in ("manual_only", "both"):
        try:
            from manual.models import ManualPage

            qs = ManualPage.objects.filter(is_published=True).select_related("manual")

            from django.db.models import Q
            q_filter = Q()
            for word in query_words:
                q_filter |= Q(title__icontains=word)
            if q_filter:
                qs = qs.filter(q_filter)

            for page in qs[:max_docs * 2]:
                page_text_raw = _extract_manual_page_text(page.content)
                full_text = f"{page.title} {page_text_raw}"
                score = relevance_score(full_text)

                # Also check body text for content-only matches
                if score == 0:
                    body_score = relevance_score(page_text_raw)
                    if body_score == 0:
                        continue
                    score = body_score

                snippet = page_text_raw[:300].strip()

                try:
                    from django.urls import reverse
                    url = reverse(
                        "manual:page_detail",
                        kwargs={"manual_slug": page.manual.slug, "page_slug": page.slug},
                    )
                except Exception:
                    url = ""

                results.append({
                    "title": f"{page.manual.title} — {page.title}",
                    "snippet": snippet,
                    "content": "\n".join(filter(None, [
                        f"Manual: {page.manual.title}",
                        f"Page: {page.title}",
                        f"Content: {page_text_raw[:1000]}",
                    ])),
                    "source_type": "manual",
                    "source_label": "User Manual",
                    "url": url,
                    "_score": score,
                })
        except Exception:
            logger.exception("Error searching Manual pages")

    # Sort by relevance score descending, return top max_docs
    results.sort(key=lambda x: x["_score"], reverse=True)
    return results[:max_docs]


# ─────────────────────────────────────────────────────────────────────
# Rate limiting
# ─────────────────────────────────────────────────────────────────────

def _rate_limit_key(identifier: str) -> str:
    """Build a cache key for rate limiting, hashed for privacy."""
    h = hashlib.sha256(identifier.encode()).hexdigest()[:16]
    return f"ai_rl_{h}"


def check_rate_limit(identifier: str, limit_per_hour: int) -> tuple[bool, int]:
    """
    Check and increment rate limit counter for a given identifier.

    Returns:
        (allowed: bool, remaining: int)
    """
    if limit_per_hour == 0:
        return True, 9999  # disabled

    key = _rate_limit_key(identifier)
    count = cache.get(key, 0)

    if count >= limit_per_hour:
        ttl = cache.ttl(key) if hasattr(cache, "ttl") else 3600
        return False, 0

    # Increment with 1-hour TTL (set only if first request in window)
    if count == 0:
        cache.set(key, 1, timeout=3600)
    else:
        cache.incr(key)

    remaining = limit_per_hour - (count + 1)
    return True, max(remaining, 0)


# ─────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────

def ask_ai(question: str, identifier: str) -> dict:
    """
    Main entry point for the AI assistant.

    Args:
        question:   The user's question text.
        identifier: Rate-limit identifier (session key or IP address).

    Returns dict with keys:
        success (bool)
        answer  (str)   — AI's response text
        sources (list)  — [{title, url, source_label}]
        error   (str)   — present only when success=False
    """
    from core.models import AIConfig, SiteConfig

    # ── Load config ────────────────────────────────────────────────────
    try:
        config = AIConfig.get_solo()
    except Exception:
        return {"success": False, "error": "AI configuration not available."}

    if not config.ai_enabled:
        return {"success": False, "error": "AI Assistant is currently disabled."}

    if not config.ai_api_key:
        return {"success": False, "error": "AI is not configured (missing API key)."}

    # ── Rate limiting ──────────────────────────────────────────────────
    allowed, remaining = check_rate_limit(identifier, config.ai_rate_limit_per_hour)
    if not allowed:
        return {
            "success": False,
            "error": (
                "You have reached the hourly question limit. "
                "Please try again later."
            ),
        }

    # ── Retrieve relevant knowledge ────────────────────────────────────
    context_docs = search_knowledge(
        query=question,
        sources=config.ai_sources,
        max_docs=config.ai_max_context_docs,
    )

    # ── Build prompt ───────────────────────────────────────────────────
    try:
        site_name = SiteConfig.get_solo().site_name
    except Exception:
        site_name = "Support Desk"

    system_prompt = config.ai_system_prompt.replace("{site_name}", site_name)

    if context_docs:
        context_text = "\n\n---\n\n".join(
            f"[Document {i+1}: {doc['title']}]\n{doc['content']}"
            for i, doc in enumerate(context_docs)
        )
        full_prompt = (
            f"{system_prompt}\n\n"
            f"=== DOCUMENT CONTEXT ===\n{context_text}\n\n"
            f"=== USER QUESTION ===\n{question}"
        )
    else:
        full_prompt = (
            f"{system_prompt}\n\n"
            f"Note: No relevant documents were found for this question.\n\n"
            f"=== USER QUESTION ===\n{question}"
        )

    # ── Call Gemini API ────────────────────────────────────────────────
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.ai_api_key)

        response = client.models.generate_content(
            model=config.ai_model_name,
            contents=full_prompt,
            config=types.GenerateContentConfig(
                temperature=config.ai_temperature,
                max_output_tokens=config.ai_max_output_tokens,
            ),
        )

        answer = response.text or ""

    except Exception as exc:
        logger.exception("Gemini API call failed: %s", exc)
        return {
            "success": False,
            "error": (
                f"An error occurred while contacting the AI: {str(exc)}. "
                "Please try again or contact the Support Desk."
            ),
        }

    # ── Build source list ──────────────────────────────────────────────
    sources = [
        {
            "title": doc["title"],
            "url": doc["url"],
            "source_label": doc["source_label"],
        }
        for doc in context_docs
        if doc.get("url")
    ]

    return {
        "success": True,
        "answer": answer,
        "sources": sources,
    }
