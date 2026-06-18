from django import forms
from .models import Project, ProjectPhase, ProjectUpdate


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["name", "client_name", "description", "status", "is_public", "allow_public_crud", "followers"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "jk-input w-full", "placeholder": "E.g. ERP System Upgrade"}),
            "client_name": forms.TextInput(attrs={"class": "jk-input w-full", "placeholder": "E.g. PT Maju Bersama"}),
            "description": forms.Textarea(attrs={"class": "jk-input w-full", "rows": 3, "placeholder": "Project summary..."}),
            "status": forms.Select(attrs={"class": "jk-input w-full"}),
            "is_public": forms.CheckboxInput(attrs={"class": "rounded border-slate-300 text-indigo-600 shadow-sm focus:ring-indigo-500"}),
            "allow_public_crud": forms.CheckboxInput(attrs={"class": "rounded border-slate-300 text-indigo-600 shadow-sm focus:ring-indigo-500"}),
            "followers": forms.SelectMultiple(attrs={"class": "jk-input w-full h-32"}),
        }


class ProjectPhaseForm(forms.ModelForm):
    class Meta:
        model = ProjectPhase
        fields = ["name", "description", "order", "status", "start_date", "end_date"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "jk-input w-full", "placeholder": "E.g. Planning"}),
            "description": forms.Textarea(attrs={"class": "jk-input w-full", "rows": 2}),
            "order": forms.NumberInput(attrs={"class": "jk-input w-full"}),
            "status": forms.Select(attrs={"class": "jk-input w-full"}),
            "start_date": forms.DateInput(attrs={"type": "date", "class": "jk-input w-full"}),
            "end_date": forms.DateInput(attrs={"type": "date", "class": "jk-input w-full"}),
        }


class ProjectUpdateForm(forms.ModelForm):
    class Meta:
        model = ProjectUpdate
        fields = ["title", "content", "update_date", "shared_doc", "attachment"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "jk-input w-full"}),
            "content": forms.Textarea(attrs={"class": "jk-input w-full", "rows": 3}),
            "update_date": forms.DateInput(attrs={"type": "date", "class": "jk-input w-full"}),
            "shared_doc": forms.Select(attrs={"class": "jk-input w-full"}),
            "attachment": forms.FileInput(attrs={"class": "block w-full text-sm text-slate-600 file:mr-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-sm file:font-semibold file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100 cursor-pointer border border-slate-200 rounded-xl p-1"}),
        }

