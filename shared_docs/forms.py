from django import forms
from .models import SharedDocument

class SharedDocumentForm(forms.ModelForm):
    class Meta:
        model = SharedDocument
        fields = ["title", "editor_mode", "content", "is_active"]
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "w-full border-slate-300 rounded-lg shadow-sm focus:border-indigo-500 focus:ring-indigo-500",
                    "placeholder": "Enter document title...",
                }
            ),
            "editor_mode": forms.Select(
                attrs={
                    "class": "w-full border-slate-300 rounded-lg shadow-sm focus:border-indigo-500 focus:ring-indigo-500",
                }
            ),
            "content": forms.Textarea(
                attrs={
                    "id": "quill-editor-fallback",
                    "class": "hidden",
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={
                    "class": "h-5 w-5 text-indigo-600 border-slate-300 rounded focus:ring-indigo-500",
                }
            ),
        }
