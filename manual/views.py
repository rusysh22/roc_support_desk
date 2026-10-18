"""Manual app views."""
import json
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.models import SiteConfig
from .models import Manual, ManualLandingConfig, ManualPage

EDITOR_ROLES = {"SuperAdmin", "Manager", "SupportDesk"}
PUBLISHER_ROLES = {"SuperAdmin", "Manager"}


def _can_edit(user):
    return user.is_authenticated and getattr(user, "role_access", None) in EDITOR_ROLES


def _can_publish(user):
    return user.is_authenticated and getattr(user, "role_access", None) in PUBLISHER_ROLES


def _require_login_if_configured(request):
    """Return redirect response if public access is restricted, else None."""
    if not request.user.is_authenticated:
        config = SiteConfig.get_solo()
        if config.require_public_login:
            return redirect(f"/auth/login/?next={request.path}")
    return None


# ---------------------------------------------------------------------------
# Public / Viewer views
# ---------------------------------------------------------------------------

def landing(request):
    gate = _require_login_if_configured(request)
    if gate:
        return gate

    if _can_edit(request.user):
        manuals = Manual.objects.all().order_by("order", "title")
    else:
        manuals = Manual.objects.filter(is_published=True).order_by("order", "title")

    return render(request, "manual/landing.html", {
        "manuals": manuals,
        "manual_config": ManualLandingConfig.get_solo(),
    })


def _nav_tree(manual, include_drafts):
    """Sidebar tree in one query: root pages with `.nav_children` filled in
    recursively (the template used to call get_children() per node — one query
    per page, ~550 queries on the D365 manual)."""
    qs = manual.pages.only("id", "title", "parent_id", "order", "is_published", "is_faq", "manual_id")
    if not include_drafts:
        qs = qs.filter(is_published=True)
    nodes = list(qs.order_by("order", "title"))
    by_parent = {}
    for n in nodes:
        by_parent.setdefault(n.parent_id, []).append(n)
    for n in nodes:
        n.nav_children = by_parent.get(n.pk, [])
    return by_parent.get(None, [])


def viewer(request, manual_slug, page_id=None):
    gate = _require_login_if_configured(request)
    if gate:
        return gate

    if _can_edit(request.user):
        manual = get_object_or_404(Manual, slug=manual_slug)
    else:
        manual = get_object_or_404(Manual, slug=manual_slug, is_published=True)

    page = None
    if page_id:
        if _can_edit(request.user):
            page = get_object_or_404(ManualPage, pk=page_id, manual=manual)
        else:
            page = get_object_or_404(ManualPage, pk=page_id, manual=manual, is_published=True)
    else:
        # Default to first published root page
        qs = manual.pages.filter(parent=None)
        if not _can_edit(request.user):
            qs = qs.filter(is_published=True)
        page = qs.order_by("order", "title").first()

    root_pages = _nav_tree(manual, include_drafts=_can_edit(request.user))

    return render(request, "manual/viewer.html", {
        "manual": manual,
        "page": page,
        "root_pages": root_pages,
        "can_edit": _can_edit(request.user),
        "can_publish": _can_publish(request.user),
        "page_content": page.content if page else None,
        "meta_description": _meta_description(page, manual),
        # sidebar keeps the current page's branch expanded (everything else
        # collapses below level 3 by default — see _tree_nav.html)
        "open_ids": [c.pk for c in page.breadcrumb()] if page else [],
        "canonical_url": request.build_absolute_uri(request.path),
    })


def _meta_description(page, manual, limit=160):
    """SEO description: the page's first real paragraph (skipping screenshot
    placeholders), falling back to the manual description."""
    text = ""
    for node in (page.content or {}).get("content", []) if page else []:
        if node.get("type") == "paragraph":
            text = _extract_text_from_node(node)
            if text and "TEMPEL GAMBAR" not in text:
                break
            text = ""
    text = " ".join((text or manual.description or "").split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------------------
# Editor views (SupportDesk + Manager + SuperAdmin)
# ---------------------------------------------------------------------------

@login_required
def manage_list(request):
    if not _can_edit(request.user):
        return redirect("manual:landing")

    is_superadmin = getattr(request.user, "role_access", None) == "SuperAdmin"

    if request.method == "POST" and request.POST.get("action") == "save_hero":
        if is_superadmin:
            config = ManualLandingConfig.get_solo()
            config.hero_title = request.POST.get("hero_title", "").strip() or config.hero_title
            config.hero_subtitle = request.POST.get("hero_subtitle", "").strip()
            if request.POST.get("clear_hero_image") == "1":
                if config.hero_image:
                    config.hero_image.delete(save=False)
                config.hero_image = None
            elif "hero_image" in request.FILES:
                if config.hero_image:
                    config.hero_image.delete(save=False)
                config.hero_image = request.FILES["hero_image"]
            config.save()
            messages.success(request, "Landing page hero updated.")
        return redirect("manual:manage_list")

    manuals = Manual.objects.all().order_by("order", "title")
    return render(request, "manual/manage_list.html", {
        "manuals": manuals,
        "can_publish": _can_publish(request.user),
        "is_superadmin": is_superadmin,
        "manual_config": ManualLandingConfig.get_solo(),
    })


@login_required
def manual_create(request):
    if not _can_edit(request.user):
        return redirect("manual:landing")

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        icon = request.POST.get("icon", "📗").strip() or "📗"
        order = int(request.POST.get("order", 0) or 0)

        if not title:
            messages.error(request, "Title is required.")
            return redirect("manual:manage_list")

        manual = Manual.objects.create(
            title=title,
            description=description,
            icon=icon,
            order=order,
            created_by=request.user,
        )
        messages.success(request, f'Manual "{manual.title}" created.')
        return redirect("manual:manage_list")

    return redirect("manual:manage_list")


@login_required
def manual_edit(request, manual_slug):
    if not _can_edit(request.user):
        return redirect("manual:landing")

    manual = get_object_or_404(Manual, slug=manual_slug)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "toggle_publish":
            if _can_publish(request.user):
                manual.is_published = not manual.is_published
                manual.save(update_fields=["is_published"])
                status = "published" if manual.is_published else "unpublished"
                messages.success(request, f'Manual "{manual.title}" {status}.')
            return redirect("manual:manage_list")

        if action == "delete":
            if _can_publish(request.user):
                title = manual.title
                manual.delete()
                messages.success(request, f'Manual "{title}" deleted.')
            return redirect("manual:manage_list")

        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        icon = request.POST.get("icon", "📗").strip() or "📗"
        order = int(request.POST.get("order", 0) or 0)

        if title:
            manual.title = title
            manual.description = description
            manual.icon = icon
            manual.order = order
            manual.save()
            messages.success(request, "Manual updated.")

        return redirect("manual:manage_list")

    root_pages = manual.pages.filter(parent=None).order_by("order", "title")
    return render(request, "manual/manual_edit.html", {
        "manual": manual,
        "root_pages": root_pages,
        "can_publish": _can_publish(request.user),
    })


@login_required
def page_edit(request, manual_slug, page_id=None):
    if not _can_edit(request.user):
        return redirect("manual:landing")

    manual = get_object_or_404(Manual, slug=manual_slug)
    page = get_object_or_404(ManualPage, pk=page_id, manual=manual) if page_id else None

    if request.method == "POST":
        action = request.POST.get("action", "save")

        if action == "delete" and page:
            if _can_publish(request.user):
                page.delete()
                messages.success(request, "Page deleted.")
            return redirect("manual:manual_edit", manual_slug=manual_slug)

        if action == "toggle_publish" and page:
            if _can_publish(request.user):
                page.is_published = not page.is_published
                page.save(update_fields=["is_published"])
            return redirect("manual:page_edit", manual_slug=manual_slug, page_id=page.pk)

        title = request.POST.get("title", "").strip()
        content_raw = request.POST.get("content", "{}").strip()
        parent_id = request.POST.get("parent_id") or None
        is_faq = request.POST.get("is_faq") == "1"
        order = int(request.POST.get("order", 0) or 0)

        try:
            content = json.loads(content_raw) if content_raw else {}
        except (json.JSONDecodeError, ValueError):
            content = {}

        parent = None
        if parent_id:
            parent = ManualPage.objects.filter(pk=parent_id, manual=manual).first()

        if not title:
            messages.error(request, "Title is required.")
            return redirect("manual:page_edit", manual_slug=manual_slug, page_id=page_id) if page_id else redirect("manual:page_new", manual_slug=manual_slug)

        if page:
            page.title = title
            page.content = content
            page.parent = parent
            page.is_faq = is_faq
            page.order = order
            page.save()
            messages.success(request, "Page saved.")
            return redirect("manual:page_edit", manual_slug=manual_slug, page_id=page.pk)
        else:
            new_page = ManualPage.objects.create(
                manual=manual,
                parent=parent,
                title=title,
                content=content,
                is_faq=is_faq,
                order=order,
                created_by=request.user,
            )
            messages.success(request, f'Page "{new_page.title}" created.')
            return redirect("manual:page_edit", manual_slug=manual_slug, page_id=new_page.pk)

    all_pages = ManualPage.objects.filter(manual=manual).order_by("order", "title")
    return render(request, "manual/page_edit.html", {
        "manual": manual,
        "page": page,
        "all_pages": all_pages,
        "can_publish": _can_publish(request.user),
        "content_json": json.dumps(page.content) if page else "{}",
    })


@login_required
def page_new(request, manual_slug):
    return page_edit(request, manual_slug, page_id=None)


def _extract_text_from_node(node):
    """Recursively extract plain text from a TipTap JSON node."""
    if not node or not isinstance(node, dict):
        return ""
    text = node.get("text", "")
    for child in node.get("content", []) or []:
        text += " " + _extract_text_from_node(child)
    return text.strip()


def _snippet(text, terms, window=160):
    """A ~window-char excerpt of text around the densest cluster of search terms."""
    lower = text.lower()
    phrase = lower.find(terms[0]) if terms else -1  # terms[0] is the whole query
    hits = [phrase] if phrase != -1 else sorted(i for t in terms[1:] for i in [lower.find(t)] if i != -1)
    if not hits:
        return text[:window].strip() + ("…" if len(text) > window else "")
    start = max(0, hits[0] - window // 3)
    end = min(len(text), start + window)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "…" + snippet.split(" ", 1)[-1]
    if end < len(text):
        snippet = snippet.rsplit(" ", 1)[0] + "…"
    return snippet


# ---------------------------------------------------------------------------
# Search index — plain text of every page, rebuilt only when pages change
# ---------------------------------------------------------------------------

_PLACEHOLDER_PREFIX = "📷 TEMPEL GAMBAR"
_STEP_PREFIX = re.compile(r"^Langkah\s+[\d.]+\s*[:–-]\s*", re.I)
_INDEX_CACHE = {}


def _page_text(node, headings):
    """Plain text of a TipTap doc, skipping screenshot placeholders; collects H2/H3 texts."""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "paragraph":
        text = _extract_text_from_node(node)
        return "" if text.startswith(_PLACEHOLDER_PREFIX) else text
    if node.get("type") == "heading":
        text = _extract_text_from_node(node)
        if (node.get("attrs") or {}).get("level", 1) <= 3 and text:
            headings.append(text)
        return text
    return " ".join(filter(None, (_page_text(c, headings) for c in node.get("content", []) or [])))


def _search_index(can_edit):
    pages = ManualPage.objects.all() if can_edit else ManualPage.objects.filter(is_published=True, manual__is_published=True)
    manuals = Manual.objects.all() if can_edit else Manual.objects.filter(is_published=True)
    stamp = (can_edit, pages.count(), str(pages.order_by("-updated_at").values_list("updated_at", flat=True).first()),
             manuals.count(), str(manuals.order_by("-updated_at").values_list("updated_at", flat=True).first()))
    cached = _INDEX_CACHE.get(can_edit)
    if cached and cached["stamp"] == stamp:
        return cached

    manual_map = {m.pk: m for m in manuals}
    rows = list(pages.values("id", "title", "parent_id", "manual_id", "content"))
    titles = {r["id"]: (r["title"], r["parent_id"]) for r in rows}
    entries = []
    for r in rows:
        if r["manual_id"] not in manual_map:
            continue
        crumbs, pid = [], r["parent_id"]
        while pid and pid in titles and len(crumbs) < 8:
            crumbs.insert(0, titles[pid][0])
            pid = titles[pid][1]
        headings = []
        body = " ".join(_page_text(r["content"] or {}, headings).split())
        entries.append({
            "id": r["id"], "title": r["title"], "manual": manual_map[r["manual_id"]], "crumbs": crumbs,
            "body": body, "headings": headings,
            "title_l": r["title"].lower(), "body_l": body.lower(), "crumbs_l": " ".join(crumbs).lower(),
        })
    cached = {"stamp": stamp, "entries": entries, "manuals": list(manuals)}
    _INDEX_CACHE[can_edit] = cached
    return cached


def _score(e, query_l, terms):
    s = 0
    if query_l == e["title_l"]:
        s += 200
    elif e["title_l"].startswith(query_l):
        s += 120
    elif query_l in e["title_l"]:
        s += 90
    s += sum(30 for t in terms if t in e["title_l"])
    s += sum(6 for t in terms if t in e["crumbs_l"])
    phrase_hits = e["body_l"].count(query_l)
    s += (40 + min(phrase_hits, 10) * 6) if phrase_hits else 0
    s += sum(min(e["body_l"].count(t), 15) for t in terms)
    s += sum(15 for h in e["headings"] if query_l in h.lower())
    return s


def _run_search(query, can_edit, limit=50):
    """Ranked results + typeahead suggestions for a query."""
    query_l = " ".join(query.lower().split())
    terms = [t for t in dict.fromkeys(query_l.split()) if len(t) > 1] or [query_l]
    idx = _search_index(can_edit)

    def matches(e, need_all):
        hay = (e["title_l"], e["body_l"], e["crumbs_l"])
        check = all if need_all else any
        return check(any(t in h for h in hay) for t in terms)

    candidates = [e for e in idx["entries"] if matches(e, True)] or [e for e in idx["entries"] if matches(e, False)]
    scored = sorted(((_score(e, query_l, terms), e) for e in candidates), key=lambda x: (-x[0], x[1]["title"]))

    results = []
    for m in idx["manuals"]:
        if query_l in m.title.lower() or (m.description and query_l in m.description.lower()):
            results.append({
                "type": "manual", "title": m.title, "icon": m.icon, "breadcrumb": [],
                "subtitle": (m.description or "")[:160],
                "url": reverse("manual:viewer", kwargs={"manual_slug": m.slug}),
            })
    for _, e in scored[:limit]:
        results.append({
            "type": "page", "title": e["title"], "breadcrumb": e["crumbs"],
            "subtitle": _snippet(e["body"], [query_l] + terms) if e["body"] else "",
            "manual_title": e["manual"].title, "manual_icon": e["manual"].icon,
            "section": e["crumbs"][0] if e["crumbs"] else e["title"],
            "url": reverse("manual:viewer_page", kwargs={"manual_slug": e["manual"].slug, "page_id": e["id"]}),
        })

    # Predictive suggestions: titles/headings containing the query, word-prefix matches first
    seen, prefix, contains = set(), [], []
    for e in idx["entries"]:
        for phrase in [e["title"]] + [_STEP_PREFIX.sub("", h) for h in e["headings"]]:
            pl = phrase.lower()
            if pl in seen or query_l not in pl or pl == query_l:
                continue
            seen.add(pl)
            (prefix if any(w.startswith(query_l) for w in pl.split()) or pl.startswith(query_l) else contains).append(phrase)
    suggestions = sorted(prefix, key=len)[:6] + sorted(contains, key=len)[: max(0, 6 - len(prefix))]
    return results, suggestions, terms


def search(request):
    gate = _require_login_if_configured(request)
    if gate:
        return gate

    query = request.GET.get("q", "").strip()[:120]
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    results, suggestions, terms = ([], [], [])
    if query:
        results, suggestions, terms = _run_search(query, _can_edit(request.user))

    if is_ajax:
        return JsonResponse({"results": results[:8], "total": len(results), "suggestions": suggestions,
                             "terms": terms, "query": query})

    sections = []
    for r in results:
        sec = r.get("section") or ("Manual" if r["type"] == "manual" else "")
        if sec and sec not in sections:
            sections.append(sec)
    return render(request, "manual/search_results.html", {
        "query": query,
        "results": results,
        "suggestions": suggestions,
        "sections": sections,
        "manual_config": ManualLandingConfig.get_solo(),
    })


@login_required
@require_POST
def page_reorder(request, manual_slug):
    """AJAX endpoint: receive ordered list of page IDs and update order field."""
    if not _can_edit(request.user):
        return JsonResponse({"ok": False}, status=403)

    manual = get_object_or_404(Manual, slug=manual_slug)
    try:
        data = json.loads(request.body)
        for item in data:
            ManualPage.objects.filter(pk=item["id"], manual=manual).update(
                order=item["order"],
                parent_id=item.get("parent_id"),
            )
    except (json.JSONDecodeError, KeyError, TypeError):
        return JsonResponse({"ok": False}, status=400)

    return JsonResponse({"ok": True})
