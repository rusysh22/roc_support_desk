"""Knowledge Base App — Views."""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Count, Q
from django.db.models.functions import Greatest
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from cases.models import CaseCategory, CaseRecord
from core.models import User

from core.models import SiteConfig
from core.excel_utils import export_queryset_xlsx, safe_cell
from core.list_actions import bulk_delete, bulk_set_archived, get_selected
from core.list_pagination import paginate

from .forms import ArticleForm
from .models import (
    Article, ArticleAttachment, ArticleChangeLog, ArticleComment, ArticleImage, ArticleLike, ArticleView,
)

from licensing.decorators import feature_required


# Status -> pill metadata for {% status_pill %} on the desk article list,
# reusing the jk-badge-* classes already defined in static/css/jokoui.css.
STATUS_META = {
    Article.Status.DRAFT: {"label": "Draft", "badge_class": "jk-badge-cancelled"},
    Article.Status.PENDING: {"label": "Pending Review", "badge_class": "jk-badge-pending"},
    Article.Status.PUBLISHED: {"label": "Published", "badge_class": "jk-badge-resolved"},
    Article.Status.REJECTED: {"label": "Rejected", "badge_class": "jk-badge-rejected"},
}


# =====================================================================
# Access Decorators
# =====================================================================

def staff_required(view_func):
    """All staff (SupportDesk, Manager, SuperAdmin, Auditor) can access.
    Auditors have read-only access."""
    from functools import wraps

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        allowed = {
            User.RoleAccess.SUPERADMIN,
            User.RoleAccess.MANAGER,
            User.RoleAccess.SUPPORTDESK,
            User.RoleAccess.ENGINEER,
            User.RoleAccess.AUDITOR,
        }
        if getattr(request.user, "role_access", None) not in allowed:
            if "knowledge_management" not in request.user.get_accessible_modules():
                return HttpResponseForbidden("Access denied.")
            
        if request.user.role_access == User.RoleAccess.AUDITOR and request.method not in ("GET", "HEAD", "OPTIONS"):
            return HttpResponseForbidden("Access denied. Auditors have read-only access.")
            
        return view_func(request, *args, **kwargs)
    return _wrapped


def log_change(article, action, user, detail=""):
    """One line in the article's change log."""
    ArticleChangeLog.objects.create(
        article=article, action=action, actor=user if getattr(user, "is_authenticated", False) else None,
        actor_name=(user.get_full_name() or user.username) if getattr(user, "is_authenticated", False) else "System",
        detail=detail[:2000],
    )


def can_publish_articles(user):
    """Approve / reject / unpublish / feature: Manager or SuperAdmin."""
    return getattr(user, "role_access", None) in {User.RoleAccess.SUPERADMIN, User.RoleAccess.MANAGER}


def _edit_summary(form, old_tags, new_tags, deleted_attachments, added_attachments):
    """'Title, Solution changed · tags +x −y · 1 attachment added' for the change log."""
    labels = [str(form.fields[name].label or name).replace("_", " ").capitalize()
              for name in form.changed_data if name in form.fields and name != "tags_input"]
    parts = [", ".join(labels) + " changed"] if labels else []
    added, removed = sorted(new_tags - old_tags), sorted(old_tags - new_tags)
    if added or removed:
        parts.append("tags " + " ".join([f"+{t}" for t in added] + [f"−{t}" for t in removed]))
    if added_attachments:
        parts.append(f"{added_attachments} attachment(s) added")
    if deleted_attachments:
        parts.append(f"{deleted_attachments} attachment(s) removed")
    return " · ".join(parts) or "Saved without changes"


def manager_or_admin_required(view_func):
    """Only Manager / SuperAdmin can approve/reject."""
    from functools import wraps

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        allowed = {User.RoleAccess.SUPERADMIN, User.RoleAccess.MANAGER}
        if getattr(request.user, "role_access", None) not in allowed:
            return HttpResponseForbidden("Manager or SuperAdmin access required.")
        return view_func(request, *args, **kwargs)
    return _wrapped


# =====================================================================
# Public — Knowledge Base Portal
# =====================================================================

def with_stats(queryset):
    """Annotate articles with how many user accounts viewed / liked them (``views_total`` / ``likes_total``)."""
    return queryset.annotate(
        views_total=Count("views", distinct=True),
        likes_total=Count("likes", distinct=True),
    )


def kb_home(request):
    """Knowledge Base Home — categories + featured + recent published articles."""
    categories = CaseCategory.objects.annotate(
        published_articles_count=Count("articles", filter=Q(articles__is_published=True))
    ).filter(published_articles_count__gt=0).order_by("name")

    featured_articles = with_stats(Article.objects.filter(is_published=True, is_featured=True)).order_by("-created_at")
    recent_articles = with_stats(Article.objects.filter(is_published=True)).order_by("-created_at")[:5]

    return render(request, "knowledge_base/home.html", {
        "categories": categories,
        "featured_articles": featured_articles,
        "recent_articles": recent_articles,
    })


def kb_category(request, slug):
    """Lists all published articles within a category, featured first."""
    category = get_object_or_404(CaseCategory, slug=slug)
    articles = with_stats(Article.objects.filter(
        category=category, is_published=True
    )).order_by("-is_featured", "-created_at")

    return render(request, "knowledge_base/category.html", {
        "category": category,
        "articles": articles,
    })


def kb_article_detail(request, slug):
    """Full article detail page (public, published only)."""
    article = get_object_or_404(Article, slug=slug, is_published=True)
    comments = list(article.comments.select_related("user").order_by("created_at"))
    for comment in comments:
        comment.can_delete = can_delete_comment(request.user, comment)
    attachments = article.attachments.all()
    # Every signed-in account is counted once, however often it comes back; visitors who are not
    # signed in cannot be told apart, so they are not counted.
    if request.user.is_authenticated:
        ArticleView.objects.get_or_create(article=article, user=request.user)
    return render(request, "knowledge_base/article_detail.html", {
        "article": article,
        "comments": comments,
        "attachments": attachments,
        "contributors": article.contributors.all(),
        "views_total": article.views.count(),
        "likes_total": article.likes.count(),
        "liked": request.user.is_authenticated and article.likes.filter(user=request.user).exists(),
    })


@login_required
@require_POST
def kb_article_like(request, slug):
    """Like / unlike an article (one like per user account). Answers JSON for the page's button and
    redirects back for a plain form post."""
    article = get_object_or_404(Article, slug=slug, is_published=True)
    like, created = ArticleLike.objects.get_or_create(article=article, user=request.user)
    if not created:
        like.delete()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
        return JsonResponse({"liked": created, "likes": article.likes.count()})
    return redirect("knowledge_base:article_detail", slug=slug)


@login_required
@require_POST
def kb_article_comment(request, slug):
    """Submit a comment on a published KB article. Login required."""
    article = get_object_or_404(Article, slug=slug, is_published=True)
    body = request.POST.get("body", "").strip()

    if not article.allow_comments:
        messages.error(request, "Comments are turned off for this article.")
    elif not body:
        messages.error(request, "Comment cannot be empty.")
    elif len(body) > 250:
        messages.error(request, "Comment must be 250 characters or less.")
    else:
        ArticleComment.objects.create(
            article=article,
            user=request.user,
            body=body,
            created_by=request.user,
            updated_by=request.user,
        )
        messages.success(request, "Comment submitted successfully.")

    return redirect("knowledge_base:article_detail", slug=slug)


def can_delete_comment(user, comment):
    """SuperAdmin removes any comment; everyone else only their own."""
    return user.is_authenticated and (
        comment.user_id == user.pk
        or user.is_superuser
        or getattr(user, "role_access", None) == User.RoleAccess.SUPERADMIN
    )


@login_required
@require_POST
def kb_article_comment_delete(request, slug, comment_id):
    """Delete a comment: its author, or a SuperAdmin."""
    comment = get_object_or_404(ArticleComment, pk=comment_id, article__slug=slug)
    if not can_delete_comment(request.user, comment):
        return HttpResponseForbidden("You can only delete your own comments.")
    comment.delete()
    messages.success(request, "Comment deleted.")
    return redirect("knowledge_base:article_detail", slug=slug)


# Below this similarity, a trigram match is too weak to trust as a real
# typo-correction — but it's still the closest thing on file, so it's shown
# as a "did you mean" suggestion rather than a result.
TRIGRAM_RESULT_THRESHOLD = 0.15


def _kb_trigram_queryset(query):
    """Published articles ranked by trigram similarity — tolerates typos
    that a plain icontains can't (e.g. "phising" vs "phishing")."""
    return (
        Article.objects.filter(is_published=True)
        .annotate(similarity=Greatest(
            TrigramSimilarity("title", query),
            TrigramSimilarity("problem_summary", query),
        ))
        .order_by("-similarity")
    )


def kb_search(request):
    """Search published articles.

    Exact substring matches (title/problem_summary/root_cause/solution) win
    when they exist — same fast, precise behavior as before. Only when there
    are none does this fall back to trigram similarity, which is what makes
    a typo like "phising" still find the "Phishing" article. If even trigram
    finds nothing above threshold, the closest articles on file are surfaced
    as suggestions instead of a purely static "try again" message.
    """
    query = request.GET.get("q", "").strip()
    results = []
    suggestions = []
    if query:
        exact_qs = Article.objects.filter(
            Q(title__icontains=query)
            | Q(problem_summary__icontains=query)
            | Q(root_cause__icontains=query)
            | Q(solution__icontains=query),
            is_published=True,
        ).order_by("-created_at")

        if exact_qs.exists():
            results = with_stats(exact_qs).order_by("-created_at")
        else:
            fuzzy_qs = with_stats(_kb_trigram_queryset(query))
            close = list(fuzzy_qs.filter(similarity__gt=TRIGRAM_RESULT_THRESHOLD)[:20])
            if close:
                results = close
            else:
                suggestions = list(fuzzy_qs[:5])

    return render(request, "knowledge_base/search_results.html", {
        "query": query,
        "results": results,
        "suggestions": suggestions,
    })


def kb_search_suggest(request):
    """Live-typing suggestions for the KB search box (JSON, AJAX).

    Same exact-then-trigram strategy as kb_search, capped small since this
    fires on every keystroke.
    """
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})

    exact_qs = Article.objects.filter(
        Q(title__icontains=query)
        | Q(problem_summary__icontains=query),
        is_published=True,
    ).order_by("-created_at")[:8]

    articles = list(exact_qs)
    if not articles:
        articles = list(
            _kb_trigram_queryset(query).filter(similarity__gt=TRIGRAM_RESULT_THRESHOLD)[:8]
        )

    return JsonResponse({
        "results": [
            {
                "title": a.title,
                "summary": a.problem_summary[:120],
                "url": reverse("knowledge_base:article_detail", args=[a.slug]),
            }
            for a in articles
        ],
    })


# =====================================================================
# Staff — Knowledge Base Management (CRUD)
# =====================================================================

@staff_required
@feature_required('kb_manage')
def kb_article_list(request):
    """Staff list of all KB articles with search and filter."""
    from core.dynamic_filters import apply_dynamic_filters, active_filters_json, fields_json

    search = request.GET.get("q", "").strip()

    qs = Article.objects.select_related("category", "created_by", "reviewed_by").order_by("-created_at")

    if search:
        qs = qs.filter(
            Q(title__icontains=search)
            | Q(problem_summary__icontains=search)
            | Q(solution__icontains=search)
        )

    kb_filterable_fields = {
        "status": {
            "label": "Status", "type": "choice", "lookup": "status",
            "choices": Article.Status.choices,
        },
        "is_archived": {
            "label": "Archived", "type": "choice", "lookup": "is_archived",
            "choices": [("false", "Active"), ("true", "Archived")],
            "python_type": "bool",
        },
    }
    qs, active_filters = apply_dynamic_filters(qs, request, kb_filterable_fields)

    show_archived = any(
        af["field_key"] == "is_archived" and af["value"] == "true" for af in active_filters
    )
    if not any(af["field_key"] == "is_archived" for af in active_filters):
        qs = qs.filter(is_archived=False)

    pagination = paginate(request, qs)

    return render(request, "desk/kb/list.html", {
        "articles": pagination["page_obj"],
        **pagination,
        "search_query": search,
        "show_archived": show_archived,
        "active_filters": active_filters,
        "active_filters_json": active_filters_json(active_filters),
        "dynamic_fields_json": fields_json(kb_filterable_fields),
        "has_active_filters": bool(active_filters),
        "STATUS_META": STATUS_META,
    })


def _save_attachments(request, article):
    """Save uploaded files from request.FILES['attachments'] to ArticleAttachment."""
    site_config = SiteConfig.get_solo()
    max_bytes = site_config.max_upload_size_mb * 1024 * 1024
    errors = []
    
    import uuid
    import os
    import mimetypes

    for f in request.FILES.getlist("attachments"):
        if f.size > max_bytes:
            errors.append(f'"{f.name}" exceeds the {site_config.max_upload_size_mb} MB size limit.')
            continue
            
        try:
            import magic
            header = f.read(2048)
            f.seek(0)
            detected_mime = magic.from_buffer(header, mime=True)
        except Exception:
            errors.append(f'"{f.name}": could not verify file securely.')
            continue
            
        original_name = f.name
        ext = os.path.splitext(original_name)[1].lower()
        dangerous_exts = {".php", ".php3", ".php4", ".php5", ".phtml", ".html", ".htm", ".exe", ".sh", ".bash", ".pl", ".py"}
        
        if ext in dangerous_exts or not ext:
            ext = mimetypes.guess_extension(detected_mime) or ".bin"
            
        safe_name = f"{uuid.uuid4().hex}{ext}"
        f.name = safe_name

        ArticleAttachment.objects.create(
            article=article,
            file=f,
            original_filename=original_name,
            mime_type=detected_mime,
            file_size=f.size,
            created_by=request.user,
            updated_by=request.user,
        )
    return errors


@staff_required
@feature_required('kb_manage')
def kb_article_create(request):
    """Create a new KB article (saved as Draft)."""
    if request.method == "POST":
        form = ArticleForm(request.POST, request.FILES)
        if form.is_valid():
            article = form.save(commit=False)
            article.status = Article.Status.DRAFT
            article.created_by = request.user
            article.updated_by = request.user
            article.save()
            form.save_m2m()
            form.save_tags(article)
            att_errors = _save_attachments(request, article)
            for err in att_errors:
                messages.warning(request, err)
            log_change(article, ArticleChangeLog.Action.CREATED, request.user)
            messages.success(request, f'Article "{article.title}" created as Draft.')
            return redirect("kb_desk:article_list")
    else:
        form = ArticleForm()

    return render(request, "desk/kb/form.html", {
        "form": form,
        "is_edit": False,
    })


def requester_account(case):
    """The user account of a ticket's requester, if there is one: the linked employee's account,
    else the account with the requester's e-mail address."""
    employee = case.requester
    if employee is not None and employee.user_id:
        return employee.user
    emails = [e for e in ((employee.email if employee else ""), case.requester_email) if e]
    for email in emails:
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user:
            return user
    return None


@staff_required
@feature_required('kb_manage')
def kb_create_from_case(request, case_id):
    """Create a KB article pre-filled from a CaseRecord's RCA data."""
    case = get_object_or_404(CaseRecord, id=case_id)

    # If an article already exists for this case, redirect to edit it
    existing = Article.objects.filter(source_case=case).first()
    if existing:
        return redirect("kb_desk:article_edit", pk=existing.pk)

    if request.method == "POST":
        form = ArticleForm(request.POST, request.FILES)
        if form.is_valid():
            article = form.save(commit=False)
            article.source_case = case
            article.status = Article.Status.DRAFT
            article.created_by = request.user
            article.updated_by = request.user
            article.save()
            form.save_m2m()
            form.save_tags(article)
            att_errors = _save_attachments(request, article)
            for err in att_errors:
                messages.warning(request, err)
            log_change(article, ArticleChangeLog.Action.CREATED, request.user, f"From ticket {case.case_number}")
            messages.success(request, f'Article "{article.title}" created from case {case.case_number}.')
            return redirect("kb_desk:article_list")
    else:
        requester = requester_account(case)
        form = ArticleForm(initial={
            "title": case.subject,
            "category": case.category_id,
            "source_case": case.id,
            "problem_summary": case.problem_description or "",
            "root_cause": case.root_cause_analysis or "",
            "solution": case.solving_steps or "",
            "contributors": [requester.pk] if requester else [],
        })

    return render(request, "desk/kb/form.html", {
        "form": form,
        "is_edit": False,
        "from_case": case,
        "requester_has_no_account": not form.is_bound and not requester,
    })


@staff_required
@feature_required('kb_manage')
def kb_article_edit(request, pk):
    """Edit an existing KB article."""
    article = get_object_or_404(Article, pk=pk)
    # Articles are shared: any Knowledge Management user may edit any of them, but a published
    # article is read-only for everyone until a Manager / SuperAdmin unpublishes it.
    locked = article.status == Article.Status.PUBLISHED
    if request.method == "POST" and locked:
        messages.error(request, "This article is published, so it is read-only. Unpublish it first to make changes.")
        return redirect("kb_desk:article_edit", pk=article.pk)

    if request.method == "POST":
        form = ArticleForm(request.POST, request.FILES, instance=article)
        old_tags = set(article.tags.values_list("name", flat=True))
        if form.is_valid():
            article = form.save(commit=False)
            article.updated_by = request.user
            # If it was rejected, reset to draft on re-edit
            if article.status == Article.Status.REJECTED:
                article.status = Article.Status.DRAFT
                article.rejection_reason = ""
            article.save()
            form.save_m2m()
            form.save_tags(article)

            # Delete attachments the user marked for removal
            delete_ids = request.POST.getlist("delete_attachment")
            deleted = 0
            if delete_ids:
                deleted, _ = article.attachments.filter(pk__in=delete_ids).delete()

            before = article.attachments.count()
            att_errors = _save_attachments(request, article)
            for err in att_errors:
                messages.warning(request, err)
            new_tags = set(article.tags.values_list("name", flat=True))
            log_change(article, ArticleChangeLog.Action.EDITED, request.user,
                       _edit_summary(form, old_tags, new_tags, deleted, article.attachments.count() - before))
            messages.success(request, f'Article "{article.title}" updated.')
            return redirect("kb_desk:article_list")
    else:
        form = ArticleForm(instance=article)

    existing_attachments = article.attachments.all()

    if locked:
        for field in form.fields.values():
            field.disabled = True

    return render(request, "desk/kb/form.html", {
        "form": form,
        "is_edit": True,
        "article": article,
        "existing_attachments": existing_attachments,
        "locked": locked,
        "can_publish": can_publish_articles(request.user),
        "change_logs": article.change_logs.select_related("actor")[:50],
    })


@staff_required
@feature_required('kb_manage')
def kb_case_search(request):
    """AJAX: search CaseRecord by ticket ID or subject for the source case picker."""
    from django.db.models.functions import Cast
    from django.db.models import CharField as DBCharField

    q = request.GET.get("q", "").strip()
    qs = CaseRecord.objects.select_related("category").order_by("-created_at")

    if q:
        # Strip common prefix patterns like "RQ-", "INC-" to get the UUID fragment
        uuid_fragment = q.lower()
        if "-" in uuid_fragment:
            parts = uuid_fragment.split("-", 1)
            if parts[0].isalpha() and len(parts[0]) <= 6:
                uuid_fragment = parts[1]

        id_filter = (
            CaseRecord.objects
            .annotate(id_str=Cast("id", output_field=DBCharField()))
            .filter(id_str__icontains=uuid_fragment)
            .values_list("id", flat=True)[:20]
        )
        subject_filter = qs.filter(subject__icontains=q).values_list("id", flat=True)[:20]
        combined_ids = set(list(id_filter) + list(subject_filter))
        qs = CaseRecord.objects.select_related("category").filter(id__in=combined_ids).order_by("-created_at")[:20]
    else:
        qs = qs[:20]

    results = [{"id": str(c.id), "case_number": c.case_number} for c in qs]
    return JsonResponse({"results": results})


@staff_required
@feature_required('kb_manage')
@require_POST
def kb_article_delete(request, pk):
    """Delete a KB article."""
    article = get_object_or_404(Article, pk=pk)

    # SupportDesk can only delete own drafts
    if request.user.role_access == User.RoleAccess.SUPPORTDESK:
        if article.created_by != request.user or article.status == Article.Status.PUBLISHED:
            return HttpResponseForbidden("You cannot delete this article.")

    title = article.title
    article.delete()
    messages.success(request, f'Article "{title}" deleted.')
    return redirect("kb_desk:article_list")


def _bulk_selectable_qs(request):
    """Base queryset for bulk actions, scoped by role like the single-object
    actions above: SupportDesk may only touch their own non-published articles."""
    qs = Article.objects.all()
    if request.user.role_access == User.RoleAccess.SUPPORTDESK:
        qs = qs.filter(created_by=request.user).exclude(status=Article.Status.PUBLISHED)
    return qs


@staff_required
@feature_required('kb_manage')
@require_POST
def kb_article_bulk_archive(request):
    """Bulk archive selected KB articles (hides them from the working list)."""
    selected = get_selected(request, _bulk_selectable_qs(request))
    count = bulk_set_archived(selected, value=True)
    messages.success(request, f"{count} article(s) archived.")
    return redirect("kb_desk:article_list")


@staff_required
@feature_required('kb_manage')
@require_POST
def kb_article_bulk_unarchive(request):
    """Bulk unarchive selected KB articles."""
    selected = get_selected(request, _bulk_selectable_qs(request))
    count = bulk_set_archived(selected, value=False)
    messages.success(request, f"{count} article(s) unarchived.")
    return redirect("kb_desk:article_list")


@staff_required
@feature_required('kb_manage')
@require_POST
def kb_article_bulk_delete(request):
    """Bulk hard-delete selected KB articles."""
    selected = get_selected(request, _bulk_selectable_qs(request))
    count = bulk_delete(selected)
    messages.success(request, f"{count} article(s) deleted.")
    return redirect("kb_desk:article_list")


@staff_required
@feature_required('kb_manage')
@require_POST
def kb_article_bulk_duplicate(request):
    """Duplicate one selected article as a new Draft ("(copy)" appended to
    the title). Meaningful for exactly one selected id; if more than one is
    submitted, only the first is duplicated."""
    selected = list(get_selected(request, _bulk_selectable_qs(request))[:1])
    if not selected:
        messages.error(request, "Select one article to duplicate.")
        return redirect("kb_desk:article_list")

    original = selected[0]
    copy = Article.objects.create(
        title=f"{original.title} (copy)",
        article_type=original.article_type,
        category=original.category,
        problem_summary=original.problem_summary,
        root_cause=original.root_cause,
        solution=original.solution,
        use_markdown=original.use_markdown,
        allow_comments=original.allow_comments,
        status=Article.Status.DRAFT,
        created_by=request.user,
        updated_by=request.user,
    )
    copy.tags.set(original.tags.all())
    copy.contributors.set(original.contributors.all())
    messages.success(request, f'Article duplicated as "{copy.title}" (Draft).')
    return redirect("kb_desk:article_list")


@staff_required
@feature_required('kb_manage')
@require_POST
def kb_article_bulk_export(request):
    """Export selected KB articles to an .xlsx file."""
    selected = get_selected(request, _bulk_selectable_qs(request)).select_related("category", "created_by")
    columns = [
        ("Title", lambda a: safe_cell(a.title)),
        ("Status", lambda a: a.get_status_display()),
        ("Author", lambda a: a.created_by.username if a.created_by else ""),
        ("Category", lambda a: a.category.name if a.category_id else ""),
        ("Created", lambda a: a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else ""),
        ("Updated", lambda a: a.updated_at.strftime("%Y-%m-%d %H:%M") if a.updated_at else ""),
    ]
    return export_queryset_xlsx(selected, columns, "kb_articles.xlsx", sheet_title="Articles")


@staff_required
@feature_required('kb_manage')
@require_POST
def kb_article_submit_review(request, pk):
    """Submit a Draft article for Manager/SuperAdmin review."""
    article = get_object_or_404(Article, pk=pk)
    if article.status not in (Article.Status.DRAFT, Article.Status.REJECTED):
        messages.error(request, "Only Draft or Rejected articles can be submitted for review.")
        return redirect("kb_desk:article_list")
    article.status = Article.Status.PENDING
    article.updated_by = request.user
    article.save()
    log_change(article, ArticleChangeLog.Action.SUBMITTED, request.user)
    from .notifications import notify_review_requested

    notify_review_requested(article, request.user)
    messages.success(request, f'Article "{article.title}" submitted for review. The approvers were notified by e-mail and Teams.')
    return redirect("kb_desk:article_edit", pk=article.pk)


@manager_or_admin_required
@feature_required('kb_manage')
@require_POST
def kb_article_approve(request, pk):
    """Approve and publish a pending article."""
    article = get_object_or_404(Article, pk=pk)
    if article.status != Article.Status.PENDING:
        messages.error(request, "Only Pending articles can be approved.")
        return redirect("kb_desk:article_list")
    article.status = Article.Status.PUBLISHED
    article.reviewed_by = request.user
    article.reviewed_at = timezone.now()
    article.updated_by = request.user
    article.save()
    log_change(article, ArticleChangeLog.Action.APPROVED, request.user)
    messages.success(request, f'Article "{article.title}" approved and published.')
    return redirect("kb_desk:article_list")


@manager_or_admin_required
@feature_required('kb_manage')
@require_POST
def kb_article_reject(request, pk):
    """Reject a pending article with a reason."""
    article = get_object_or_404(Article, pk=pk)
    if article.status != Article.Status.PENDING:
        messages.error(request, "Only Pending articles can be rejected.")
        return redirect("kb_desk:article_list")
    reason = request.POST.get("rejection_reason", "").strip()
    article.status = Article.Status.REJECTED
    article.rejection_reason = reason
    article.reviewed_by = request.user
    article.reviewed_at = timezone.now()
    article.updated_by = request.user
    article.save()
    log_change(article, ArticleChangeLog.Action.REJECTED, request.user, reason)
    messages.success(request, f'Article "{article.title}" rejected.')
    return redirect("kb_desk:article_list")


@manager_or_admin_required
@feature_required('kb_manage')
@require_POST
def kb_article_unpublish(request, pk):
    """Unpublish a published article (revert to Draft)."""
    article = get_object_or_404(Article, pk=pk)
    if article.status != Article.Status.PUBLISHED:
        messages.error(request, "Only Published articles can be unpublished.")
        return redirect("kb_desk:article_list")
    reason = request.POST.get("reason", "").strip()
    article.status = Article.Status.DRAFT
    article.is_featured = False
    article.updated_by = request.user
    article.save()
    log_change(article, ArticleChangeLog.Action.UNPUBLISHED, request.user, reason)
    messages.success(request, f'Article "{article.title}" unpublished — it can be edited now.')
    return redirect("kb_desk:article_edit", pk=article.pk)


@manager_or_admin_required
@feature_required('kb_manage')
@require_POST
def kb_article_toggle_featured(request, pk):
    """Toggle featured status for a published article (max 10 featured)."""
    article = get_object_or_404(Article, pk=pk)
    if article.status != Article.Status.PUBLISHED:
        messages.error(request, "Only published articles can be featured.")
        return redirect("kb_desk:article_list")

    if article.is_featured:
        article.is_featured = False
        article.updated_by = request.user
        article.save()
        log_change(article, ArticleChangeLog.Action.UNFEATURED, request.user)
        messages.success(request, f'"{article.title}" removed from featured articles.')
    else:
        featured_count = Article.objects.filter(is_featured=True).count()
        if featured_count >= 10:
            messages.error(
                request,
                "Maximum 10 featured articles reached. Remove a featured article first."
            )
            return redirect("kb_desk:article_list")
        article.is_featured = True
        article.updated_by = request.user
        article.save()
        log_change(article, ArticleChangeLog.Action.FEATURED, request.user)
        messages.success(request, f'"{article.title}" added to featured articles.')

    return redirect("kb_desk:article_list")


@staff_required
@feature_required('kb_manage')
@require_POST
def kb_attachment_delete(request, pk):
    """Delete a single article attachment."""
    attachment = get_object_or_404(ArticleAttachment, pk=pk)
    article = attachment.article

    if article.status == Article.Status.PUBLISHED:
        messages.error(request, "This article is published, so it is read-only. Unpublish it first to make changes.")
        return redirect("kb_desk:article_edit", pk=article.pk)

    attachment.file.delete(save=False)
    attachment.delete()
    messages.success(request, "Attachment deleted.")
    return redirect("kb_desk:article_edit", pk=article.pk)


@staff_required
@feature_required('kb_manage')
@require_POST
def kb_image_upload(request):
    """Handle image upload from Quill.js editor. Returns JSON with URL."""
    if "image" not in request.FILES:
        return JsonResponse({"error": "No image provided."}, status=400)

    image_file = request.FILES["image"]

    # Validate file type securely using magic bytes
    allowed_types = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    
    try:
        import magic
        header = image_file.read(2048)
        image_file.seek(0)
        detected_mime = magic.from_buffer(header, mime=True)
    except Exception:
        return JsonResponse({"error": "Could not verify image securely."}, status=400)
        
    if detected_mime not in allowed_types:
        return JsonResponse({"error": f"Invalid file type ({detected_mime}). Only JPEG, PNG, GIF, and WebP images are allowed."}, status=400)

    # Validate file size (max 5MB)
    if image_file.size > 5 * 1024 * 1024:
        return JsonResponse({"error": "Image must be smaller than 5MB."}, status=400)

    img = ArticleImage.objects.create(
        image=image_file,
        alt_text=image_file.name,
        created_by=request.user,
        updated_by=request.user,
    )
    return JsonResponse({"url": img.image.url})
