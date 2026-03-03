import json
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy, reverse
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View
from django.http import JsonResponse, HttpResponseRedirect
from django.contrib.auth.mixins import UserPassesTestMixin
from django.contrib import messages
from django.db.models import F
from .models import ShortLink
from .forms import ShortLinkForm


class DeskUserRequiredMixin(UserPassesTestMixin):
    """
    Mixin to ensure only SuperAdmin, Manager, and SupportDesk can access these views.
    """
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.role_access in ['SuperAdmin', 'Manager', 'SupportDesk']
    
    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return redirect("login")
        messages.error(self.request, "You do not have permission to access the Shorten Link feature.")
        return redirect("desk:dashboard") # Assuming there's a desk dashboard


class RedirectLinkView(View):
    """
    Public-facing view that increments clicks and redirects to the target URL.
    """
    def get(self, request, slug, *args, **kwargs):
        # We use F() expression to avoid race conditions when clicking
        # and we use get_object_or_404 to ensure 404 if not found
        link = get_object_or_404(ShortLink, slug=slug)
        ShortLink.objects.filter(pk=link.pk).update(clicks=F('clicks') + 1)
        
        # We could add an intermediary social card page here if the user agent is a recognizable bot
        # But WhatsApp and Twitter fetch the meta tags of the redirect page or target.
        # However, to let social platforms scrape the card view, we might need a dedicated HTML page 
        # for bots, but typically raw redirect is fine as long as target URL has rich metadata, 
        # OR we inject metadata before redirecting.
        # For simplicity, if we want WhatsApp to view the card, we render the card template.
        user_agent = request.META.get('HTTP_USER_AGENT', '').lower()
        bot_agents = ['whatsapp', 'twitterbot', 'facebookbot', 'linkedinbot', 'slackbot', 'telegrambot']
        
        if any(bot in user_agent for bot in bot_agents):
            return render(request, "links/social_card.html", {"link": link})
        
        return HttpResponseRedirect(link.target_url)


class LinkListView(DeskUserRequiredMixin, ListView):
    model = ShortLink
    template_name = "desk/links/list.html"
    context_object_name = "links"
    
    def get_queryset(self):
        qs = super().get_queryset()
        
        # Base role filtering
        if self.request.user.role_access != 'SuperAdmin':
            qs = qs.filter(created_by=self.request.user)
            
        # Search integration
        search_query = self.request.GET.get('q', '').strip()
        if search_query:
            qs = qs.filter(
                slug__icontains=search_query
            ) | qs.filter(
                target_url__icontains=search_query
            ) | qs.filter(
                title__icontains=search_query
            )
            
        return qs.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('q', '')
        return context


class LinkCreateView(DeskUserRequiredMixin, CreateView):
    model = ShortLink
    form_class = ShortLinkForm
    template_name = "desk/links/form.html"
    success_url = reverse_lazy("links_desk:list")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        # Build QR code after save
        host = self.request.build_absolute_uri('/')
        self.object.build_qr_code(host)
        self.object.save()
        messages.success(self.request, "Short link created successfully.")
        return response


class LinkUpdateView(DeskUserRequiredMixin, UpdateView):
    model = ShortLink
    form_class = ShortLinkForm
    template_name = "desk/links/form.html"
    success_url = reverse_lazy("links_desk:list")

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.role_access == 'SuperAdmin':
            return qs
        return qs.filter(created_by=self.request.user)
        
    def form_valid(self, form):
        old_slug = self.get_object().slug
        response = super().form_valid(form)
        
        # If slug changed, rebuild QR code
        if old_slug != self.object.slug:
            host = self.request.build_absolute_uri('/')
            self.object.build_qr_code(host)
            self.object.save()
            
        messages.success(self.request, "Short link updated successfully.")
        return response


class LinkDeleteView(DeskUserRequiredMixin, DeleteView):
    model = ShortLink
    success_url = reverse_lazy("links_desk:list")

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.role_access == 'SuperAdmin':
            return qs
        return qs.filter(created_by=self.request.user)

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, "Short link deleted successfully.")
        return super().delete(request, *args, **kwargs)


class CheckSlugView(DeskUserRequiredMixin, View):
    """
    AJAX endpoint to check if a slug is available.
    """
    def get(self, request, *args, **kwargs):
        slug = request.GET.get("slug", "").strip()
        link_id = request.GET.get("exclude_id", None)
        
        if not slug:
            return JsonResponse({"available": False, "error": "Slug cannot be empty."})
            
        qs = ShortLink.objects.filter(slug__iexact=slug)
        if link_id:
            qs = qs.exclude(id=link_id)
            
        is_taken = qs.exists()
        return JsonResponse({"available": not is_taken})
