"""Knowledge Base App — Forms."""
from django import forms

from cases.models import CaseCategory

from .models import Article, ArticleTag


class NoRequireTextarea(forms.Textarea):
    """Textarea that never renders the HTML 'required' attribute.

    Used for hidden textareas backed by Quill.js — the browser cannot
    focus a hidden element for native validation, so we skip it and
    let Django's server-side validation handle the 'required' check.
    """

    def use_required_attribute(self, initial):
        return False


class ArticleForm(forms.ModelForm):
    """Form for creating / editing a knowledge base article."""

    tags_input = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "jk-input",
            "placeholder": "e.g. network, vpn, login  (comma-separated)",
        }),
        help_text="Comma-separated tags. New tags are created automatically.",
        label="Tags",
    )

    class Meta:
        model = Article
        fields = [
            "title",
            "article_type",
            "category",
            "source_case",
            "problem_summary",
            "root_cause",
            "solution",
        ]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "jk-input",
                "placeholder": "Article title",
            }),
            "article_type": forms.Select(attrs={"class": "jk-input"}),
            "category": forms.Select(attrs={"class": "jk-input"}),
            "source_case": forms.Select(attrs={"class": "jk-input"}),
            "problem_summary": NoRequireTextarea(attrs={"class": "hidden", "id": "id_problem_summary"}),
            "root_cause": NoRequireTextarea(attrs={"class": "hidden", "id": "id_root_cause"}),
            "solution": NoRequireTextarea(attrs={"class": "hidden", "id": "id_solution"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_case"].required = False
        self.fields["source_case"].queryset = self.fields["source_case"].queryset.select_related("category")


        # Pre-fill tags_input for editing
        if self.instance and self.instance.pk:
            self.fields["tags_input"].initial = ", ".join(
                self.instance.tags.values_list("name", flat=True)
            )

    def save_tags(self, article):
        """Parse comma-separated tags and attach to the article."""
        raw = self.cleaned_data.get("tags_input", "")
        tag_names = [t.strip() for t in raw.split(",") if t.strip()]
        tags = []
        for name in tag_names:
            tag, _ = ArticleTag.objects.get_or_create(
                name__iexact=name,
                defaults={"name": name},
            )
            tags.append(tag)
        article.tags.set(tags)
