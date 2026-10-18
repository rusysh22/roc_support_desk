"""
User Roles master data (SuperAdmin only)
========================================
Roles bundle a base access level and permissions (core.models.PERMISSION_REGISTRY).
Module defaults per role are edited on the Module Defaults page.
"""
from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .dynamic_filters import active_filters_json, apply_dynamic_filters, fields_json
from .excel_utils import export_queryset_xlsx, safe_cell
from .forms import UserRoleForm
from .list_actions import get_selected
from .list_pagination import paginate
from .models import ALL_PERMISSIONS, PERMISSION_REGISTRY, User, UserRole
from .views_user import superadmin_required

URL_ROLE_LIST = "users_desk:role_list"

ROLE_FILTERABLE_FIELDS = {
    "base_access": {
        "label": "Base Access Level", "type": "choice", "lookup": "base_access",
        "choices": User.RoleAccess.choices,
    },
    "is_active": {
        "label": "Status", "type": "choice", "lookup": "is_active",
        "choices": [("true", "Active"), ("false", "Inactive")],
        "python_type": "bool",
    },
    "is_system": {
        "label": "Type", "type": "choice", "lookup": "is_system",
        "choices": [("true", "Built-in"), ("false", "Custom")],
        "python_type": "bool",
    },
}


def _permission_labels(role):
    labels = dict((code, label) for code, label, _group in PERMISSION_REGISTRY)
    perms = role.permissions if isinstance(role.permissions, list) else []
    if ALL_PERMISSIONS in perms:
        return ["All permissions"]
    return [labels.get(code, code) for code in perms]


@superadmin_required
def role_list(request):
    search = request.GET.get("q", "").strip()
    roles = UserRole.objects.annotate(user_count=Count("users")).order_by("sort_order", "name")
    if search:
        roles = roles.filter(Q(name__icontains=search) | Q(code__icontains=search) | Q(description__icontains=search))
    roles, active_filters = apply_dynamic_filters(roles, request, ROLE_FILTERABLE_FIELDS)

    context = paginate(request, roles)
    context["roles"] = list(context["page_obj"])
    for role in context["roles"]:
        role.permission_labels = _permission_labels(role)
    context.update({
        "search_query": search,
        "active_filters": active_filters,
        "active_filters_json": active_filters_json(active_filters),
        "dynamic_fields_json": fields_json(ROLE_FILTERABLE_FIELDS),
        "has_active_filters": bool(search or active_filters),
    })
    return render(request, "desk/users/roles/list.html", context)


def _role_form(request, role=None):
    old_base = role.base_access if role else None  # the form edits ``role`` in place
    form = UserRoleForm(request.POST or None, instance=role, initial=None if role else {"is_active": True})
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if role is None:
            obj.created_by = request.user
        obj.updated_by = request.user
        obj.save()
        if old_base is not None and old_base != obj.base_access:
            # Users of a custom role follow its new base level.
            for user in obj.users.all():
                user.save()
        messages.success(request, f'User Role "{obj.name}" {"updated" if role else "created"}.')
        return redirect(URL_ROLE_LIST)
    user_count = role.users.count() if role else 0
    return render(request, "desk/users/roles/form.html", {"form": form, "role": role, "user_count": user_count})


@superadmin_required
def role_create(request):
    return _role_form(request)


@superadmin_required
def role_edit(request, pk):
    return _role_form(request, get_object_or_404(UserRole, pk=pk))


@superadmin_required
@require_POST
def role_delete(request, pk):
    role = get_object_or_404(UserRole, pk=pk)
    if role.is_system:
        messages.error(request, f'"{role.name}" is a built-in role and cannot be deleted. Deactivate custom roles instead.')
    elif role.users.exists():
        messages.error(request, f'"{role.name}" is still assigned to {role.users.count()} user(s). Move them to another role first.')
    else:
        name = role.name
        role.delete()
        messages.success(request, f'User Role "{name}" deleted.')
    return redirect(URL_ROLE_LIST)


@superadmin_required
@require_POST
def role_bulk_delete(request):
    """Delete the selected custom roles; built-in roles and roles still in use are skipped."""
    selected = get_selected(request, UserRole.objects.annotate(user_count=Count("users")))
    skipped_system, skipped_used, deleted = [], [], 0
    for role in selected:
        if role.is_system:
            skipped_system.append(role.name)
        elif role.user_count:
            skipped_used.append(role.name)
        else:
            role.delete()
            deleted += 1
    if skipped_system:
        messages.error(request, f"Built-in roles cannot be deleted: {', '.join(skipped_system)}.")
    if skipped_used:
        messages.error(request, f"Still assigned to users, move them first: {', '.join(skipped_used)}.")
    if deleted:
        messages.success(request, f"{deleted} role(s) deleted.")
    return redirect(URL_ROLE_LIST)


@superadmin_required
@require_POST
def role_bulk_export(request):
    """Export the selected roles (all roles when none are selected) to Excel."""
    roles = UserRole.objects.annotate(user_count=Count("users")).order_by("sort_order", "name")
    ids = request.POST.getlist("selected_ids")
    if ids:
        roles = roles.filter(pk__in=ids)
    columns = [
        ("Role", lambda o: safe_cell(o.name)),
        ("Code", lambda o: safe_cell(o.code)),
        ("Base Access Level", lambda o: o.get_base_access_display()),
        ("Permissions", lambda o: safe_cell(", ".join(_permission_labels(o)))),
        ("Users", lambda o: o.user_count),
        ("Type", lambda o: "Built-in" if o.is_system else "Custom"),
        ("Status", lambda o: "Active" if o.is_active else "Inactive"),
        ("Description", lambda o: safe_cell(o.description)),
    ]
    return export_queryset_xlsx(roles, columns, "user_roles.xlsx", sheet_title="User Roles")
