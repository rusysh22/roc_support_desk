"""Knowledge Base App — Forms."""
from django import forms
from django.db.models import Q

from cases.category_tree import CategoryTreeSelect
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


class ContributorField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, user):
        name = user.get_full_name() or user.username
        return f"{name} ({user.email})" if user.email else name


class ArticleForm(forms.ModelForm):
    """Form for creating / editing a knowledge base article."""

    contributors = ContributorField(
        queryset=None, required=False, label="Contributors",
        widget=forms.SelectMultiple(attrs={
            "class": "jk-select-search w-full", "data-placeholder": "Search users…", "id": "id_contributors",
        }),
        help_text="Credited on the article page. Several users can be added.",
    )

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
            "use_markdown",
            "problem_summary",
            "root_cause",
            "solution",
            "allow_comments",
            "contributors",
        ]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "jk-input",
                "placeholder": "Article title",
            }),
            "article_type": forms.Select(attrs={"class": "jk-input"}),
            "category": CategoryTreeSelect(attrs={"class": "jk-input"}),
            "source_case": forms.Select(attrs={"class": "jk-input"}),
            "use_markdown": forms.CheckboxInput(attrs={"class": "hidden", "id": "id_use_markdown"}),
            "problem_summary": NoRequireTextarea(attrs={"class": "hidden", "id": "id_problem_summary"}),
            "root_cause": NoRequireTextarea(attrs={"class": "hidden", "id": "id_root_cause"}),
            "solution": NoRequireTextarea(attrs={"class": "hidden", "id": "id_solution"}),
            "allow_comments": forms.CheckboxInput(attrs={"class": "jk-checkbox", "id": "id_allow_comments"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import User

        # Active accounts, plus whoever is already a contributor (even if deactivated since).
        current = self.instance.contributors.values_list("pk", flat=True) if self.instance.pk else []
        self.fields["contributors"].queryset = (
            User.objects.filter(Q(is_active=True) | Q(pk__in=list(current))).order_by("first_name", "username")
        )
        self.fields["problem_summary"].required = True
        self.fields["source_case"].required = False
        self.fields["source_case"].queryset = self.fields["source_case"].queryset.select_related("category")
        self.fields["category"].queryset = (
            CaseCategory.objects
            .exclude(id__in=CaseCategory.objects.filter(children__isnull=False).values("id"))
            .select_related("parent")
        )


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
