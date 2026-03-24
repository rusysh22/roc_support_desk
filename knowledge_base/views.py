"""Knowledge Base App — Views."""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from cases.models import CaseCategory, CaseRecord
from core.models import User

from .forms import ArticleForm
from .models import Article, ArticleImage


# =====================================================================
# Access Decorators
# =====================================================================

def staff_required(view_func):
    """All staff (SupportDesk, Manager, SuperAdmin) can access."""
    from functools import wraps

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        allowed = {
            User.RoleAccess.SUPERADMIN,
            User.RoleAccess.MANAGER,
            User.RoleAccess.SUPPORTDESK,
        }
        if getattr(request.user, "role_access", None) not in allowed:
            return HttpResponseForbidden("Access denied.")
        return view_func(request, *args, **kwargs)
    return _wrapped


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

def kb_home(request):
    """Knowledge Base Home — categories + recent published articles."""
    categories = CaseCategory.objects.annotate(
        published_articles_count=Count("articles", filter=Q(articles__is_published=True))
    ).filter(published_articles_count__gt=0).order_by("name")

    recent_articles = Article.objects.filter(is_published=True).order_by("-created_at")[:5]

    return render(request, "knowledge_base/home.html", {
        "categories": categories,
        "recent_articles": recent_articles,
    })


def kb_category(request, slug):
    """Lists all published articles within a category."""
    category = get_object_or_404(CaseCategory, slug=slug)
    articles = Article.objects.filter(category=category, is_published=True).order_by("-created_at")

    return render(request, "knowledge_base/category.html", {
        "category": category,
        "articles": articles,
    })


def kb_article_detail(request, slug):
    """Full article detail page (public, published only)."""
    article = get_object_or_404(Article, slug=slug, is_published=True)
    return render(request, "knowledge_base/article_detail.html", {
        "article": article,
    })


def kb_search(request):
    """Search published articles."""
    query = request.GET.get("q", "").strip()
    results = []
    if query:
        results = Article.objects.filter(
            Q(title__icontains=query)
            | Q(problem_summary__icontains=query)
            | Q(root_cause__icontains=query)
            | Q(solution__icontains=query),
            is_published=True,
        ).order_by("-created_at")

    return render(request, "knowledge_base/search_results.html", {
        "query": query,
        "results": results,
    })


# =====================================================================
# Staff — Knowledge Base Management (CRUD)
# =====================================================================

@staff_required
def kb_article_list(request):
    """Staff list of all KB articles with search and filter."""
    search = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "")

    qs = Article.objects.select_related("category", "created_by", "reviewed_by").order_by("-created_at")

    if search:
        qs = qs.filter(
            Q(title__icontains=search)
            | Q(problem_summary__icontains=search)
            | Q(solution__icontains=search)
        )
    if status_filter:
        qs = qs.filter(status=status_filter)

    paginator = Paginator(qs, 15)
    page = paginator.get_page(request.GET.get("page", 1))

    return render(request, "desk/kb/list.html", {
        "articles": page,
        "search_query": search,
        "status_filter": status_filter,
        "status_choices": Article.Status.choices,
    })


@staff_required
def kb_article_create(request):
    """Create a new KB article (saved as Draft)."""
    if request.method == "POST":
        form = ArticleForm(request.POST)
        if form.is_valid():
            article = form.save(commit=False)
            article.status = Article.Status.DRAFT
            article.created_by = request.user
            article.updated_by = request.user
            article.save()
            form.save_tags(article)
            messages.success(request, f'Article "{article.title}" created as Draft.')
            return redirect("kb_desk:article_list")
    else:
        form = ArticleForm()

    return render(request, "desk/kb/form.html", {
        "form": form,
        "is_edit": False,
    })


@staff_required
def kb_create_from_case(request, case_id):
    """Create a KB article pre-filled from a CaseRecord's RCA data."""
    case = get_object_or_404(CaseRecord, id=case_id)

    # If an article already exists for this case, redirect to edit it
    existing = Article.objects.filter(source_case=case).first()
    if existing:
        return redirect("kb_desk:article_edit", pk=existing.pk)

    if request.method == "POST":
        form = ArticleForm(request.POST)
        if form.is_valid():
            article = form.save(commit=False)
            article.source_case = case
            article.status = Article.Status.DRAFT
            article.created_by = request.user
            article.updated_by = request.user
            article.save()
            form.save_tags(article)
            messages.success(request, f'Article "{article.title}" created from case {case.case_number}.')
            return redirect("kb_desk:article_list")
    else:
        form = ArticleForm(initial={
            "title": case.subject,
            "category": case.category_id,
            "source_case": case.id,
            "problem_summary": case.problem_description or "",
            "root_cause": case.root_cause_analysis or "",
            "solution": case.solving_steps or "",
        })

    return render(request, "desk/kb/form.html", {
        "form": form,
        "is_edit": False,
        "from_case": case,
    })


@staff_required
def kb_article_edit(request, pk):
    """Edit an existing KB article."""
    article = get_object_or_404(Article, pk=pk)

    # SupportDesk can only edit their own drafts/rejected articles
    if request.user.role_access == User.RoleAccess.SUPPORTDESK:
        if article.created_by != request.user:
            return HttpResponseForbidden("You can only edit your own articles.")
        if article.status == Article.Status.PUBLISHED:
            return HttpResponseForbidden("Published articles can only be edited by Manager/SuperAdmin.")

    if request.method == "POST":
        form = ArticleForm(request.POST, instance=article)
        if form.is_valid():
            article = form.save(commit=False)
            article.updated_by = request.user
            # If it was rejected, reset to draft on re-edit
            if article.status == Article.Status.REJECTED:
                article.status = Article.Status.DRAFT
                article.rejection_reason = ""
            article.save()
            form.save_tags(article)
            messages.success(request, f'Article "{article.title}" updated.')
            return redirect("kb_desk:article_list")
    else:
        form = ArticleForm(instance=article)

    return render(request, "desk/kb/form.html", {
        "form": form,
        "is_edit": True,
        "article": article,
    })


@staff_required
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


@staff_required
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
    messages.success(request, f'Article "{article.title}" submitted for review.')
    return redirect("kb_desk:article_list")


@manager_or_admin_required
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
    messages.success(request, f'Article "{article.title}" approved and published.')
    return redirect("kb_desk:article_list")


@manager_or_admin_required
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
    messages.success(request, f'Article "{article.title}" rejected.')
    return redirect("kb_desk:article_list")


@manager_or_admin_required
@require_POST
def kb_article_unpublish(request, pk):
    """Unpublish a published article (revert to Draft)."""
    article = get_object_or_404(Article, pk=pk)
    if article.status != Article.Status.PUBLISHED:
        messages.error(request, "Only Published articles can be unpublished.")
        return redirect("kb_desk:article_list")
    article.status = Article.Status.DRAFT
    article.updated_by = request.user
    article.save()
    messages.success(request, f'Article "{article.title}" unpublished.')
    return redirect("kb_desk:article_list")


@staff_required
@require_POST
def kb_image_upload(request):
    """Handle image upload from Quill.js editor. Returns JSON with URL."""
    if "image" not in request.FILES:
        return JsonResponse({"error": "No image provided."}, status=400)

    image_file = request.FILES["image"]

    # Validate file type
    allowed_types = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    if image_file.content_type not in allowed_types:
        return JsonResponse({"error": "Only JPEG, PNG, GIF, and WebP images are allowed."}, status=400)

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
