from django import forms
from .models import ShortLink
from django.core.exceptions import ValidationError
import re

class ShortLinkForm(forms.ModelForm):
    class Meta:
        model = ShortLink
        fields = ["target_url", "slug", "title", "description"]
        widgets = {
            "target_url": forms.URLInput(attrs={
                "class": "w-full border border-gray-300 rounded-lg px-4 py-2 focus:ring focus:ring-blue-100",
                "placeholder": "https://example.com/very/long/url..."
            }),
            "slug": forms.TextInput(attrs={
                "class": "w-full border border-gray-300 rounded-lg px-4 py-2 focus:ring focus:ring-blue-100",
                "placeholder": "custom-slug-here"
            }),
            "title": forms.TextInput(attrs={
                "class": "w-full border border-gray-300 rounded-lg px-4 py-2 focus:ring focus:ring-blue-100",
                "placeholder": "Catchy Title for Social Card"
            }),
            "description": forms.Textarea(attrs={
                "class": "w-full border border-gray-300 rounded-lg px-4 py-2 focus:ring focus:ring-blue-100",
                "rows": 3,
                "placeholder": "Brief description..."
            }),
        }
    
    def clean_slug(self):
        slug = self.cleaned_data.get("slug")
        if not slug:
            raise ValidationError("Slug cannot be empty.")
            
        # Alphanumeric and hyphens/underscores only
        if not re.match(r'^[\w-]+$', slug):
            raise ValidationError("Slug can only contain letters, numbers, hyphens, and underscores.")
            
        return slug
