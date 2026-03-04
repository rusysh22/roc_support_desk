from django.shortcuts import render, get_object_or_404
from django.db.models import Count, Q
from cases.models import CaseCategory
from .models import Article

def kb_home(request):
    """
    Knowledge Base Home Page.
    Shows search bar, categories with published article counts, and recent articles.
    """
    categories = CaseCategory.objects.annotate(
        published_articles_count=Count('articles', filter=Q(articles__is_published=True))
    ).filter(published_articles_count__gt=0).order_by('name')

    recent_articles = Article.objects.filter(is_published=True).order_by('-created_at')[:5]

    return render(request, "knowledge_base/home.html", {
        "categories": categories,
        "recent_articles": recent_articles,
    })

def kb_category(request, slug):
    """
    Lists all published articles within a specific category.
    """
    category = get_object_or_404(CaseCategory, slug=slug)
    articles = Article.objects.filter(category=category, is_published=True).order_by('-created_at')
    
    return render(request, "knowledge_base/category.html", {
        "category": category,
        "articles": articles,
    })

def kb_article_detail(request, slug):
    """
    Displays the full details of a single knowledge base article.
    """
    article = get_object_or_404(Article, slug=slug, is_published=True)
    
    # Increment view count or log analytics here in the future if needed
    
    return render(request, "knowledge_base/article_detail.html", {
        "article": article,
    })

def kb_search(request):
    """
    Search across published article titles and content.
    """
    query = request.GET.get('q', '').strip()
    results = []
    
    if query:
        results = Article.objects.filter(
            Q(title__icontains=query) |
            Q(problem_summary__icontains=query) |
            Q(root_cause__icontains=query) |
            Q(solution__icontains=query),
            is_published=True
        ).order_by('-created_at')
        
    return render(request, "knowledge_base/search_results.html", {
        "query": query,
        "results": results,
    })
