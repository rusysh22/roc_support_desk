import json
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden
from django.db.models import Q
from django.urls import reverse

from .models import Project, ProjectPhase, ProjectUpdate, PhaseChecklist
from .forms import ProjectForm, ProjectPhaseForm, ProjectUpdateForm


def is_manager_or_admin(user):
    """
    Return True if the user has sufficient privileges to create/edit/delete
    projects and shared documents.

    Covers:
      - Django superusers (created via createsuperuser) → is_superuser
      - Django staff users → is_staff
      - App-level Manager or SuperAdmin role → role_access field
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    return getattr(user, "role_access", "") in ["SuperAdmin", "Manager"]


def can_edit_project(user, project):
    """
    Return True if the user has sufficient privileges to edit THIS project.
    Covers:
      - Public CRUD allowed
      - Manager or Admin
      - Specific followers (editors)
    """
    if project.allow_public_crud:
        return True
    if is_manager_or_admin(user):
        return True
    if user.is_authenticated and project.followers.filter(id=user.id).exists():
        return True
    return False


# ==============================================================================
# PROJECTS
# ==============================================================================

@login_required
def project_list(request):
    """
    List all projects.
    """
    projects = Project.objects.all().order_by("-created_at")
    
    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "")
    
    if q:
        projects = projects.filter(Q(name__icontains=q) | Q(client_name__icontains=q))
    if status_filter:
        projects = projects.filter(status=status_filter)
        
    context = {
        "projects": projects,
        "search_query": q,
        "status_filter": status_filter,
    }
    return render(request, "desk/projects/project_list.html", context)


@login_required
def project_create(request):
    if not is_manager_or_admin(request.user):
        messages.error(request, "You do not have permission to create projects.")
        return redirect("projects_desk:project_list")
        
    if request.method == "POST":
        form = ProjectForm(request.POST, request.FILES)
        if form.is_valid():
            project = form.save(commit=False)
            project.created_by = request.user
            project.updated_by = request.user
            project.save()
            messages.success(request, f"Project '{project.name}' created successfully.")
            return redirect("projects_desk:project_edit", pk=project.pk)
    else:
        form = ProjectForm()
        
    return render(request, "desk/projects/project_form.html", {
        "form": form,
        "is_edit": False
    })


@login_required
def project_edit(request, pk):
    project = get_object_or_404(Project, pk=pk)
    
    if not can_edit_project(request.user, project):
        messages.error(request, "You do not have permission to edit this project.")
        return redirect("projects_desk:project_list")
    
    if request.method == "POST":
        # Handle Project Main Form
        if "action" in request.POST and request.POST["action"] == "edit_project":
            form = ProjectForm(request.POST, request.FILES, instance=project)
            if form.is_valid():
                p = form.save(commit=False)
                p.updated_by = request.user
                p.save()
                messages.success(request, "Project updated successfully.")
                return redirect("projects_desk:project_edit", pk=project.pk)
    else:
        form = ProjectForm(instance=project)
        
    phases = project.phases.all().prefetch_related("updates", "checklists")
    
    phase_form = ProjectPhaseForm()
    update_form = ProjectUpdateForm()
    
    return render(request, "desk/projects/project_form.html", {
        "project_obj": project,
        "form": form,
        "is_edit": True,
        "phases": phases,
        "phase_form": phase_form,
        "update_form": update_form,
    })


@login_required
def project_delete(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if not can_edit_project(request.user, project):
        return HttpResponseForbidden()
        
    if request.method == "POST":
        project = get_object_or_404(Project, pk=pk)
        name = project.name
        project.delete()
        messages.success(request, f"Project '{name}' deleted.")
    return redirect("projects_desk:project_list")


@login_required
def project_toggle_public(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if not can_edit_project(request.user, project):
        return HttpResponseForbidden()
        
    if request.method == "POST":
        project = get_object_or_404(Project, pk=pk)
        project.is_public = not project.is_public
        project.updated_by = request.user
        project.save()
        messages.success(request, f"Project visibility changed to {'Public' if project.is_public else 'Private'}.")
        return redirect("projects_desk:project_edit", pk=project.pk)
    return redirect("projects_desk:project_list")


# ==============================================================================
# PHASES & UPDATES
# ==============================================================================

@login_required
def phase_create(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    if not can_edit_project(request.user, project):
        return HttpResponseForbidden()
    if request.method == "POST":
        form = ProjectPhaseForm(request.POST)
        if form.is_valid():
            phase = form.save(commit=False)
            phase.project = project
            phase.created_by = request.user
            phase.updated_by = request.user
            phase.save()
            messages.success(request, "Phase added.")
        else:
            print("phase_create errors:", form.errors)
            messages.error(request, "Error adding phase.")
        next_url = request.POST.get("next")
        if next_url: return redirect(next_url)
    return redirect("projects_desk:project_edit", pk=project.pk)


@login_required
def phase_edit(request, phase_id):
    phase = get_object_or_404(ProjectPhase, pk=phase_id)
    if not can_edit_project(request.user, phase.project):
        return HttpResponseForbidden()
    if request.method == "POST":
        form = ProjectPhaseForm(request.POST, instance=phase)
        if form.is_valid():
            p = form.save(commit=False)
            p.updated_by = request.user
            p.save()
            messages.success(request, "Phase updated.")
        else:
            print("phase_edit errors:", form.errors)
            messages.error(request, "Error updating phase.")
        next_url = request.POST.get("next")
        if next_url: return redirect(next_url)
    return redirect("projects_desk:project_edit", pk=phase.project.pk)


@login_required
def phase_delete(request, phase_id):
    phase = get_object_or_404(ProjectPhase, pk=phase_id)
    if not can_edit_project(request.user, phase.project):
        return HttpResponseForbidden()
        
    if request.method == "POST":
        project_id = phase.project.pk
        phase.delete()
        messages.success(request, "Phase deleted.")
        next_url = request.POST.get("next")
        if next_url: return redirect(next_url)
        return redirect("projects_desk:project_edit", pk=project_id)
    return redirect("projects_desk:project_list")


@login_required
def update_create(request, phase_id):
    phase = get_object_or_404(ProjectPhase, pk=phase_id)
    if not can_edit_project(request.user, phase.project):
        return HttpResponseForbidden()
    if request.method == "POST":
        form = ProjectUpdateForm(request.POST, request.FILES)
        if form.is_valid():
            update = form.save(commit=False)
            update.phase = phase
            update.created_by = request.user
            update.updated_by = request.user
            update.save()
            messages.success(request, "Update added.")
        else:
            messages.error(request, "Error adding update.")
        next_url = request.POST.get("next")
        if next_url: return redirect(next_url)
    return redirect("projects_desk:project_edit", pk=phase.project.pk)


@login_required
def update_edit(request, update_id):
    update = get_object_or_404(ProjectUpdate, pk=update_id)
    if not can_edit_project(request.user, update.phase.project):
        return HttpResponseForbidden()
    if request.method == "POST":
        form = ProjectUpdateForm(request.POST, request.FILES, instance=update)
        if form.is_valid():
            u = form.save(commit=False)
            u.updated_by = request.user
            u.save()
            messages.success(request, "Update modified.")
        else:
            messages.error(request, "Error modifying update.")
        next_url = request.POST.get("next")
        if next_url: return redirect(next_url)
    return redirect("projects_desk:project_edit", pk=update.phase.project.pk)


@login_required
def update_delete(request, update_id):
    update = get_object_or_404(ProjectUpdate, pk=update_id)
    if not can_edit_project(request.user, update.phase.project):
        return HttpResponseForbidden()
        
    if request.method == "POST":
        project_id = update.phase.project.pk
        update.delete()
        messages.success(request, "Update deleted.")
        next_url = request.POST.get("next")
        if next_url: return redirect(next_url)
        return redirect("projects_desk:project_edit", pk=project_id)
    return redirect("projects_desk:project_list")


# ==============================================================================
# CHECKLIST
# ==============================================================================

@login_required
def checklist_create(request, phase_id):
    phase = get_object_or_404(ProjectPhase, pk=phase_id)
    if not can_edit_project(request.user, phase.project):
        return HttpResponseForbidden()
    
    if request.method == "POST":
        task_name = request.POST.get("task_name", "").strip()
        if task_name:
            PhaseChecklist.objects.create(
                phase=phase,
                task_name=task_name,
                created_by=request.user,
                updated_by=request.user
            )
            messages.success(request, "Checklist item added.")
        else:
            messages.error(request, "Task name cannot be empty.")
        
        next_url = request.POST.get("next")
        if next_url: return redirect(next_url)
    return redirect("projects_desk:project_edit", pk=phase.project.pk)


@login_required
def checklist_toggle(request, checklist_id):
    checklist = get_object_or_404(PhaseChecklist, pk=checklist_id)
    if not can_edit_project(request.user, checklist.phase.project):
        return HttpResponseForbidden()
    
    if request.method == "POST":
        checklist.is_completed = not checklist.is_completed
        checklist.updated_by = request.user
        checklist.save()
        
        next_url = request.POST.get("next")
        if next_url: return redirect(next_url)
        return redirect("projects_desk:project_edit", pk=checklist.phase.project.pk)
    return redirect("projects_desk:project_list")


@login_required
def checklist_delete(request, checklist_id):
    checklist = get_object_or_404(PhaseChecklist, pk=checklist_id)
    if not can_edit_project(request.user, checklist.phase.project):
        return HttpResponseForbidden()
        
    if request.method == "POST":
        project_id = checklist.phase.project.pk
        checklist.delete()
        messages.success(request, "Checklist item deleted.")
        next_url = request.POST.get("next")
        if next_url: return redirect(next_url)
        return redirect("projects_desk:project_edit", pk=project_id)
    return redirect("projects_desk:project_list")
