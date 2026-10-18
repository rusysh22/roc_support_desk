"""
Core App — User Master Data Views
====================
Provides CRUD operations for User management.
Restricted to SuperAdmin only.
"""
import io
import json
import secrets
import string

import openpyxl

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .dynamic_filters import apply_dynamic_filters, active_filters_json, fields_json
from .excel_utils import safe_cell, make_xlsx_response, style_header_row, export_queryset_xlsx
from .forms import UserAdminForm
from .list_pagination import paginate
from .models import MODULE_REGISTRY, SiteConfig, User

# Allowlisted fields for the dynamic Advanced Filter builder on the user
# list (see core.dynamic_filters) — this REPLACES the old fixed Role/Status
# dropdowns, not supplements them.
USER_FILTERABLE_FIELDS = {
    "email": {"label": "Email", "type": "text", "lookup": "email"},
    "login_username": {"label": "Login ID", "type": "text", "lookup": "login_username"},
    "nik": {"label": "NIK", "type": "text", "lookup": "nik"},
    "role_access": {
        "label": "Role", "type": "choice", "lookup": "role_access",
        "choices": User.RoleAccess.choices,
    },
    "is_active": {
        "label": "Status", "type": "choice", "lookup": "is_active",
        "choices": [("true", "Active"), ("false", "Inactive")],
        "python_type": "bool",
    },
    "can_handle_confidential": {
        "label": "Confidential Access", "type": "choice", "lookup": "can_handle_confidential",
        "choices": [("true", "Yes"), ("false", "No")],
        "python_type": "bool",
    },
}

# Roles that count against the max_agents license limit
AGENT_ROLES = {
    User.RoleAccess.SUPPORTDESK,
    User.RoleAccess.MANAGER,
    User.RoleAccess.ENGINEER,
}


def _build_module_sections():
    module_sections = {}
    for slug, label, emoji, section in MODULE_REGISTRY:
        module_sections.setdefault(section, []).append((slug, label, emoji))
    return module_sections


def _role_default_modules(role):
    return sorted(SiteConfig.get_role_modules(role))


def _build_module_context(user_obj=None):
    """Return template context variables for the Module Access section."""
    module_sections = _build_module_sections()

    if user_obj is not None:
        is_module_override = user_obj.module_access is not None
        checked_modules = set(user_obj.module_access) if is_module_override else set()
        role = user_obj.role_access
    else:
        is_module_override = False
        checked_modules = set()
        role = User.RoleAccess.SUPPORTDESK

    return {
        "module_sections": module_sections,
        "is_module_override": is_module_override,
        "checked_modules": checked_modules,
        "role_defaults_json": json.dumps(_role_default_modules(role)),
    }


def _save_module_access(user_obj, post_data):
    override = post_data.get("module_override") == "1"
    if override:
        valid = {slug for slug, _label, _emoji, _section in MODULE_REGISTRY}
        selected = [s for s in post_data.getlist("module_access") if s in valid]
        user_obj.module_access = selected
    else:
        user_obj.module_access = None
    user_obj.save(update_fields=["module_access"])


def _get_agent_limit():
    """
    Return (current_count, max_allowed) for agent-role users.
    Fails closed if licensing is unavailable, so an outage cannot create seats
    beyond the purchased entitlement.
    """
    try:
        from licensing.models import LicenseRecord
        license_obj = LicenseRecord.get_current()
        max_allowed = license_obj.max_agents
    except Exception:
        max_allowed = 0

    current_count = User.objects.filter(role_access__in=AGENT_ROLES, is_active=True).count()
    return current_count, max_allowed


def _generate_password(length=16):
    """Generate a cryptographically random password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def superadmin_required(view_func):
    """Decorator to restrict access to SuperAdmin only."""
    from functools import wraps

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if getattr(request.user, "role_access", None) != User.RoleAccess.SUPERADMIN:
            return HttpResponseForbidden("Access denied. SuperAdmin privileges required.")
        return view_func(request, *args, **kwargs)

    return _wrapped


@superadmin_required
def user_list(request):
    """List all users."""
    search = request.GET.get("q", "").strip()

    qs = User.objects.select_related("user_role").order_by("login_username")

    if search:
        qs = qs.filter(
            Q(username__icontains=search)
            | Q(login_username__icontains=search)
            | Q(email__icontains=search)
            | Q(nik__icontains=search)
        )

    qs, active_filters = apply_dynamic_filters(qs, request, USER_FILTERABLE_FIELDS)

    # Preserve the old "Active users by default" UX: only implicitly hide
    # inactive users when the user hasn't explicitly added their own Status
    # filter condition (any value — Active, Inactive, Is Set, etc.).
    status_condition = next((af for af in active_filters if af["field_key"] == "is_active"), None)
    if status_condition is None:
        qs = qs.filter(is_active=True)
    # Drives the bulk-action bar's Archive vs. Unarchive button below.
    viewing_archived_only = bool(
        status_condition and status_condition["op"] == "equals" and status_condition["value"] == "false"
    )

    current_agents, max_agents = _get_agent_limit()
    module_sections = {}
    for slug, label, emoji, section in MODULE_REGISTRY:
        module_sections.setdefault(section, []).append((slug, label, emoji))

    context = paginate(request, qs)
    context.update({
        "users": context["page_obj"],
        "search_query": search,
        "active_filters": active_filters,
        "active_filters_json": active_filters_json(active_filters),
        "dynamic_fields_json": fields_json(USER_FILTERABLE_FIELDS),
        "has_active_filters": bool(search or active_filters),
        "viewing_archived_only": viewing_archived_only,
        "current_agents": current_agents,
        "max_agents": max_agents,
        "agent_limit_reached": max_agents is not None and current_agents >= max_agents,
        "module_sections": module_sections,
    })
    return render(request, "desk/users/list.html", context)


@superadmin_required
def user_create(request):
    """Create a new user."""
    current_agents, max_agents = _get_agent_limit()
    module_context = _build_module_context()

    if request.method == "POST":
        form = UserAdminForm(request.POST)
        if form.is_valid():
            new_role = form.cleaned_data.get('role_access')
            if new_role in AGENT_ROLES and max_agents is not None and current_agents >= max_agents:
                messages.error(
                    request,
                    f"Agent limit reached ({current_agents}/{max_agents}). "
                    "Upgrade your license to add more Support Desk or Manager accounts."
                )
                return render(request, "desk/users/form.html", {
                    "form": form,
                    "is_edit": False,
                    "current_agents": current_agents,
                    "max_agents": max_agents,
                    **_build_module_context(),
                })
            user = form.save()
            _save_module_access(user, request.POST)
            messages.success(request, f'User "{user.login_username}" created successfully.')
            return redirect("users_desk:user_list")
    else:
        duplicate_from = request.GET.get("duplicate_from")
        source_user = None
        if duplicate_from:
            source_user = User.objects.filter(pk=duplicate_from).first()

        if source_user:
            form = UserAdminForm(initial={
                "role_access": source_user.role_access,
                "user_role": source_user.user_role_id,
                "initials": source_user.initials,
                "can_handle_confidential": source_user.can_handle_confidential,
            })
            messages.info(
                request,
                f'Pre-filled from "{source_user.login_username}" — role, confidential access, '
                "and module grants copied. Enter a new login username and email to finish."
            )
            module_context = _build_module_context(source_user)
        else:
            form = UserAdminForm()
            module_context = _build_module_context()

    return render(request, "desk/users/form.html", {
        "form": form,
        "is_edit": False,
        "current_agents": current_agents,
        "max_agents": max_agents,
        **module_context,
    })


@superadmin_required
def user_edit(request, pk):
    """Edit an existing user."""
    user_obj = get_object_or_404(User, pk=pk)
    # Read old_role BEFORE form.is_valid() — Django's _post_clean() updates
    # user_obj's fields in-place, so reading after is_valid() gives the new value.
    old_role = user_obj.role_access
    current_agents, max_agents = _get_agent_limit()

    if request.method == "POST":
        form = UserAdminForm(request.POST, instance=user_obj)
        if form.is_valid():
            new_role = form.cleaned_data.get('role_access')
            # Only check limit when promoting a non-agent to an agent role
            if (new_role in AGENT_ROLES and old_role not in AGENT_ROLES
                    and max_agents is not None and current_agents >= max_agents):
                messages.error(
                    request,
                    f"Agent limit reached ({current_agents}/{max_agents}). "
                    "Upgrade your license to promote this user to a Support Desk or Manager role."
                )
                return render(request, "desk/users/form.html", {
                    "form": form,
                    "is_edit": True,
                    "user_obj": user_obj,
                    "current_agents": current_agents,
                    "max_agents": max_agents,
                    **_build_module_context(user_obj),
                })
            form.save()
            _save_module_access(user_obj, request.POST)
            messages.success(request, f'User "{user_obj.login_username}" updated successfully.')
            return redirect("users_desk:user_list")
    else:
        form = UserAdminForm(instance=user_obj)

    return render(request, "desk/users/form.html", {
        "form": form,
        "is_edit": True,
        "user_obj": user_obj,
        "current_agents": current_agents,
        "max_agents": max_agents,
        **_build_module_context(user_obj),
    })


@superadmin_required
@require_POST
def user_delete(request, pk):
    """Delete a user."""
    user_obj = get_object_or_404(User, pk=pk)
    
    if user_obj == request.user:
        messages.error(request, "You cannot delete your own account.")
        return redirect("users_desk:user_list")
        
    username = user_obj.login_username
    user_obj.delete()
    messages.success(request, f'User "{username}" deleted successfully.')
    return redirect("users_desk:user_list")


# ---------------------------------------------------------------------------
# Excel columns used both for export and as the import template
# ---------------------------------------------------------------------------
EXCEL_HEADERS = [
    "login_username",
    "display_name",
    "email",
    "nik",
    "role_access",
    "initials",
    "can_handle_confidential",
]


@superadmin_required
def user_export(request):
    """Export selected users (by checkbox IDs) to Excel (.xlsx).
    If no IDs supplied, exports ALL users."""
    selected_ids = request.POST.getlist("selected_ids") or request.GET.getlist("ids")

    qs = User.objects.all().order_by("login_username")
    if selected_ids:
        qs = qs.filter(pk__in=selected_ids)

    columns = [
        ("login_username", lambda u: safe_cell(u.login_username)),
        ("display_name", lambda u: safe_cell(u.username)),
        ("email", lambda u: safe_cell(u.email)),
        ("nik", lambda u: safe_cell(u.nik or "")),
        ("role_access", lambda u: safe_cell(u.role_access)),
        ("initials", lambda u: safe_cell(u.initials)),
        ("can_handle_confidential", lambda u: "TRUE" if u.can_handle_confidential else "FALSE"),
    ]
    response = export_queryset_xlsx(qs, columns, "users_export.xlsx", sheet_title="Users")
    from .models import AuditLog
    AuditLog.log(AuditLog.Action.EXPORT, request=request,
                 details={"type": "user_list", "filename": "users_export.xlsx"})
    return response


@superadmin_required
def user_import_template(request):
    """Download a blank Excel template with the correct column headers and one sample row."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Import Template"

    ws.append(EXCEL_HEADERS)
    style_header_row(ws)

    # One sample row
    ws.append(["jdoe", "John Doe", "jdoe@example.com", "EMP001", "SupportDesk", "jd", "FALSE"])

    # Add a Notes sheet explaining each column
    notes_ws = wb.create_sheet(title="Notes")
    notes_ws.append(["Column", "Description", "Required", "Valid Values"])
    style_header_row(notes_ws)
    valid_roles = ", ".join(r[0] for r in User.RoleAccess.choices)
    notes_data = [
        ["login_username", "Unique username used to log in", "Yes", "-"],
        ["display_name", "Full name displayed in the system", "Yes", "-"],
        ["email", "Unique email address", "Yes", "-"],
        ["nik", "Employee ID number (NIK)", "No", "-"],
        ["role_access", "Permission level", "Yes", valid_roles],
        ["initials", "Short initials (e.g. jd)", "Yes", "-"],
        ["can_handle_confidential", "Access to confidential tickets", "No", "TRUE / FALSE"],
    ]
    for r in notes_data:
        notes_ws.append(r)
    for col in notes_ws.columns:
        notes_ws.column_dimensions[col[0].column_letter].width = max(len(str(c.value or "")) for c in col) + 4

    # Auto column widths for main sheet
    for idx, header in enumerate(EXCEL_HEADERS, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = max(len(header) + 4, 20)

    response = make_xlsx_response("user_import_template.xlsx")
    wb.save(response)
    return response


@superadmin_required
def user_import(request):
    """Import users from an uploaded Excel (.xlsx) file.
    Duplicates (existing login_username, email, or nik) — entire import fails, nothing saved."""
    if request.method != "POST":
        return redirect("users_desk:user_list")

    xlsx_file = request.FILES.get("csv_file")  # same input name, different format now
    if not xlsx_file:
        messages.error(request, "No file uploaded. Please select an Excel (.xlsx) file.")
        return redirect("users_desk:user_list")

    if not xlsx_file.name.endswith(".xlsx"):
        messages.error(request, "Invalid file type. Please upload a .xlsx Excel file.")
        return redirect("users_desk:user_list")

    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_file.read()), data_only=True)
        ws = wb.active
    except Exception as e:
        messages.error(request, f"Could not read the Excel file: {e}")
        return redirect("users_desk:user_list")

    rows_raw = list(ws.iter_rows(values_only=True))
    if len(rows_raw) < 2:
        messages.error(request, "The uploaded file has no data rows (only headers or empty).")
        return redirect("users_desk:user_list")

    # Read header from first row, map to expected fields
    header = [str(h).strip().lower() if h else "" for h in rows_raw[0]]
    expected = [h.lower() for h in EXCEL_HEADERS]
    missing = [h for h in expected if h not in header]
    if missing:
        messages.error(request, f"Missing required columns: {', '.join(missing)}. Please use the official template.")
        return redirect("users_desk:user_list")

    def get_col(row, field):
        idx = header.index(field.lower())
        return str(row[idx]).strip() if row[idx] is not None else ""

    data_rows = rows_raw[1:]
    if not data_rows:
        messages.error(request, "The uploaded file is empty.")
        return redirect("users_desk:user_list")

    # --- Validate ALL rows first before touching the database ---
    valid_roles = {r[0] for r in User.RoleAccess.choices}
    errors = []

    seen_usernames = {}
    seen_emails = {}
    seen_niks = {}

    for i, row in enumerate(data_rows, start=2):  # start=2 because row 1 is header
        # Skip entirely blank rows (Excel often appends these)
        if all(cell is None for cell in row):
            continue

        lu = get_col(row, "login_username")
        email = get_col(row, "email")
        nik = get_col(row, "nik")
        role = get_col(row, "role_access")
        initials = get_col(row, "initials")
        display_name = get_col(row, "display_name")

        if not lu:
            errors.append(f"Row {i}: login_username is required.")
        if not email:
            errors.append(f"Row {i}: email is required.")
        if not display_name:
            errors.append(f"Row {i}: display_name is required.")
        if not initials:
            errors.append(f"Row {i}: initials is required.")
        if role and role not in valid_roles:
            errors.append(f"Row {i}: invalid role_access '{role}'. Valid options: {', '.join(valid_roles)}.")

        # Check for duplicates within the file first, then against the database
        if lu:
            if lu in seen_usernames:
                errors.append(f"Row {i}: login_username '{lu}' duplicates row {seen_usernames[lu]}.")
            else:
                seen_usernames[lu] = i
                if User.objects.filter(login_username=lu).exists():
                    errors.append(f"Row {i}: login_username '{lu}' already exists.")
        if email:
            if email in seen_emails:
                errors.append(f"Row {i}: email '{email}' duplicates row {seen_emails[email]}.")
            else:
                seen_emails[email] = i
                if User.objects.filter(email=email).exists():
                    errors.append(f"Row {i}: email '{email}' already exists.")
        if nik:
            if nik in seen_niks:
                errors.append(f"Row {i}: NIK '{nik}' duplicates row {seen_niks[nik]}.")
            else:
                seen_niks[nik] = i
                if User.objects.filter(nik=nik).exists():
                    errors.append(f"Row {i}: NIK '{nik}' already exists.")

    if errors:
        for err in errors:
            messages.error(request, err)
        return redirect("users_desk:user_list")

    # --- Check agent limit before creating ---
    current_agents, max_agents = _get_agent_limit()
    if max_agents is not None:
        new_agents_in_file = sum(
            1 for row in data_rows
            if (get_col(row, "role_access") or User.RoleAccess.SUPPORTDESK) in AGENT_ROLES
        )
        if current_agents + new_agents_in_file > max_agents:
            messages.error(
                request,
                f"Import would exceed your agent limit. "
                f"Current: {current_agents}/{max_agents} agents. "
                f"This file contains {new_agents_in_file} agent-role user(s). "
                "Remove non-agent roles or upgrade your license."
            )
            return redirect("users_desk:user_list")

    # --- All OK — create users inside an atomic transaction ---
    created_count = 0
    generated_passwords = []  # [(login_username, display_name, password)]

    try:
        with transaction.atomic():
            for row in data_rows:
                if all(cell is None for cell in row):
                    continue
                lu = get_col(row, "login_username")
                confidence_raw = get_col(row, "can_handle_confidential").upper()
                can_confidential = confidence_raw in ("TRUE", "1", "YES")
                role = get_col(row, "role_access") or User.RoleAccess.SUPPORTDESK
                pw = _generate_password()

                user_obj = User(
                    login_username=lu,
                    username=get_col(row, "display_name"),
                    email=get_col(row, "email"),
                    nik=get_col(row, "nik") or None,
                    role_access=role,
                    initials=get_col(row, "initials"),
                    can_handle_confidential=can_confidential,
                    must_change_password=True,
                )
                user_obj.set_password(pw)
                user_obj.save()
                generated_passwords.append((lu, get_col(row, "display_name"), pw))
                created_count += 1
    except Exception as e:
        messages.error(request, f"Import failed due to an unexpected error: {e}")
        return redirect("users_desk:user_list")

    # Export generated passwords as a downloadable Excel file
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Generated Passwords"
    ws.append(["login_username", "display_name", "generated_password"])
    style_header_row(ws)
    for lu, dn, pw in generated_passwords:
        ws.append([lu, dn, pw])

    response = make_xlsx_response(f"imported_users_passwords_{created_count}.xlsx")
    from io import BytesIO
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    response.write(buf.read())
    from .models import AuditLog
    AuditLog.log(AuditLog.Action.BULK_IMPORT, request=request,
                 details={"created_count": created_count})
    messages.success(request, f"Successfully imported {created_count} user(s). A password sheet has been downloaded — store it securely and delete after distribution.")
    return response


@superadmin_required
@require_POST
def user_bulk_delete(request):
    """Bulk delete selected users. Skips the currently logged-in user."""
    selected_ids = request.POST.getlist("selected_ids")
    if not selected_ids:
        messages.warning(request, "No users selected for deletion.")
        return redirect("users_desk:user_list")

    qs = User.objects.filter(pk__in=selected_ids).exclude(pk=request.user.pk)
    count = qs.count()
    qs.delete()

    skipped = len(selected_ids) - count
    msg = f"Deleted {count} user(s)."
    if skipped:
        msg += f" {skipped} skipped (your own account cannot be deleted)."
    messages.success(request, msg)
    return redirect("users_desk:user_list")


@superadmin_required
@require_POST
def user_bulk_duplicate(request):
    """Duplicate a single selected user's role/permissions/module grants.

    Login credentials (login_username, email, nik, password) must be unique
    per user, so rather than creating an exact clone, this redirects to the
    create-user form pre-filled with the source user's role, initials,
    confidential access, and module grants — the admin only needs to type
    a new login username and email for the new account.
    """
    selected_ids = request.POST.getlist("selected_ids")
    if len(selected_ids) != 1:
        messages.warning(request, "Select exactly one user to duplicate.")
        return redirect("users_desk:user_list")

    source = get_object_or_404(User, pk=selected_ids[0])
    return redirect(f"{reverse('users_desk:user_create')}?duplicate_from={source.pk}")


# ---------------------------------------------------------------------------
# Password reset helpers
# ---------------------------------------------------------------------------

PASS_HEADERS = ["login_username", "display_name", "new_password"]
UPDATE_HEADERS = EXCEL_HEADERS + ["new_password"]


@superadmin_required
@require_POST
def user_export_password_template(request):
    """Export selected users as a password-reset Excel template (login_username + new_password)."""
    selected_ids = request.POST.getlist("selected_ids")
    if not selected_ids:
        messages.warning(request, "No users selected.")
        return redirect("users_desk:user_list")

    qs = User.objects.filter(pk__in=selected_ids).order_by("login_username")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Password Reset"
    ws.append(PASS_HEADERS)
    style_header_row(ws)

    for u in qs:
        ws.append([u.login_username, u.username, ""])

    for idx, w in enumerate([22, 28, 22], start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = w

    notes_ws = wb.create_sheet("Notes")
    notes_ws.append(["Column", "Description"])
    style_header_row(notes_ws)
    notes_ws.append(["login_username", "Identifier — DO NOT change"])
    notes_ws.append(["display_name", "For reference only — not updated"])
    notes_ws.append(["new_password", "New password to set. Leave blank to skip that user."])
    for col in notes_ws.columns:
        notes_ws.column_dimensions[col[0].column_letter].width = (
            max(len(str(c.value or "")) for c in col) + 4
        )

    response = make_xlsx_response("password_reset_template.xlsx")
    wb.save(response)
    return response


@superadmin_required
@require_POST
def user_import_bulk_password(request):
    """Import Excel with login_username + new_password. Updates passwords only."""
    xlsx_file = request.FILES.get("csv_file")
    if not xlsx_file:
        messages.error(request, "No file uploaded.")
        return redirect("users_desk:user_list")
    if not xlsx_file.name.endswith(".xlsx"):
        messages.error(request, "Please upload a .xlsx file.")
        return redirect("users_desk:user_list")

    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_file.read()), data_only=True)
        ws = wb.active
    except Exception as e:
        messages.error(request, f"Could not read file: {e}")
        return redirect("users_desk:user_list")

    rows_raw = list(ws.iter_rows(values_only=True))
    if len(rows_raw) < 2:
        messages.error(request, "File has no data rows.")
        return redirect("users_desk:user_list")

    header = [str(h).strip().lower() if h else "" for h in rows_raw[0]]
    if "login_username" not in header or "new_password" not in header:
        messages.error(request, "File must have 'login_username' and 'new_password' columns. Use the exported template.")
        return redirect("users_desk:user_list")

    lu_idx = header.index("login_username")
    pw_idx = header.index("new_password")

    updated = skipped = 0
    not_found = []

    for i, row in enumerate(rows_raw[1:], start=2):
        lu = str(row[lu_idx]).strip() if row[lu_idx] else ""
        pw = str(row[pw_idx]).strip() if row[pw_idx] else ""

        if not lu or not pw:
            skipped += 1
            continue

        try:
            user_obj = User.objects.get(login_username=lu)
            user_obj.set_password(pw)
            user_obj.save(update_fields=["password"])
            updated += 1
        except User.DoesNotExist:
            not_found.append(f"Row {i}: '{lu}' not found.")

    for err in not_found[:5]:
        messages.warning(request, err)
    if len(not_found) > 5:
        messages.warning(request, f"... and {len(not_found) - 5} more users not found.")

    messages.success(
        request,
        f"Password reset complete: {updated} updated, {skipped} skipped (blank password).",
    )
    return redirect("users_desk:user_list")


@superadmin_required
@require_POST
def user_bulk_reset_password(request):
    """Quick bulk reset: apply one password to all selected users."""
    selected_ids = request.POST.getlist("selected_ids")
    new_password = request.POST.get("new_password", "").strip()

    if not selected_ids:
        messages.warning(request, "No users selected.")
        return redirect("users_desk:user_list")
    if len(new_password) < 6:
        messages.error(request, "Password must be at least 6 characters.")
        return redirect("users_desk:user_list")

    qs = User.objects.filter(pk__in=selected_ids).exclude(pk=request.user.pk)
    count = 0
    for u in qs:
        u.set_password(new_password)
        u.save(update_fields=["password"])
        count += 1

    skipped = len(selected_ids) - count
    msg = f"Password reset for {count} user(s)."
    if skipped:
        msg += f" {skipped} skipped (your own account)."
    messages.success(request, msg)
    return redirect("users_desk:user_list")


# ---------------------------------------------------------------------------
# Update existing users via Excel
# ---------------------------------------------------------------------------

@superadmin_required
def user_export_update_template(request):
    """Export all users (or selected) with all editable fields + optional new_password column."""
    selected_ids = request.POST.getlist("selected_ids") if request.method == "POST" else request.GET.getlist("ids")
    qs = User.objects.all().order_by("login_username")
    if selected_ids:
        qs = qs.filter(pk__in=selected_ids)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Update Users"
    ws.append(UPDATE_HEADERS)
    style_header_row(ws)

    col_widths = {h: len(h) + 4 for h in UPDATE_HEADERS}
    for u in qs:
        row = [
            safe_cell(u.login_username),
            safe_cell(u.username),
            safe_cell(u.email),
            safe_cell(u.nik or ""),
            safe_cell(u.role_access),
            safe_cell(u.initials),
            "TRUE" if u.can_handle_confidential else "FALSE",
            "",  # new_password — blank = keep current
        ]
        ws.append(row)
        for idx, val in enumerate(row):
            col_widths[UPDATE_HEADERS[idx]] = max(col_widths[UPDATE_HEADERS[idx]], len(str(val or "")) + 2)

    for idx, h in enumerate(UPDATE_HEADERS, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = col_widths[h]

    notes_ws = wb.create_sheet("Notes")
    notes_ws.append(["Column", "Description", "Notes"])
    style_header_row(notes_ws)
    valid_roles = ", ".join(r[0] for r in User.RoleAccess.choices)
    for r in [
        ["login_username",           "User identifier",          "DO NOT change — used to match existing user"],
        ["display_name",             "Full display name",         "Will be updated if filled"],
        ["email",                    "Email address",             "Will be updated if filled"],
        ["nik",                      "Employee ID",               "Will be updated (blank = clear)"],
        ["role_access",              "Permission level",          f"Valid: {valid_roles}"],
        ["initials",                 "Short initials",            "Will be updated if filled"],
        ["can_handle_confidential",  "Confidential ticket access","TRUE / FALSE"],
        ["new_password",             "New password (optional)",   "Leave BLANK to keep current password"],
    ]:
        notes_ws.append(r)
    for col in notes_ws.columns:
        notes_ws.column_dimensions[col[0].column_letter].width = max(len(str(c.value or "")) for c in col) + 4

    response = make_xlsx_response("users_update_template.xlsx")
    wb.save(response)
    return response


@superadmin_required
@require_POST
def user_import_update(request):
    """Update existing users from Excel. Matches by login_username.
    - Known username  → update fields; update password if new_password is filled.
    - Unknown username → skip with warning (does NOT create new users).
    """
    xlsx_file = request.FILES.get("csv_file")
    if not xlsx_file:
        messages.error(request, "No file uploaded.")
        return redirect("users_desk:user_list")
    if not xlsx_file.name.endswith(".xlsx"):
        messages.error(request, "Please upload a .xlsx file.")
        return redirect("users_desk:user_list")

    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_file.read()), data_only=True)
        ws = wb.active
    except Exception as e:
        messages.error(request, f"Could not read file: {e}")
        return redirect("users_desk:user_list")

    rows_raw = list(ws.iter_rows(values_only=True))
    if len(rows_raw) < 2:
        messages.error(request, "File has no data rows.")
        return redirect("users_desk:user_list")

    header = [str(h).strip().lower() if h else "" for h in rows_raw[0]]
    if "login_username" not in header:
        messages.error(request, "Missing 'login_username' column. Please use the update template.")
        return redirect("users_desk:user_list")

    def get_col(row, field):
        if field not in header:
            return ""
        idx = header.index(field)
        return str(row[idx]).strip() if row[idx] is not None else ""

    valid_roles = {r[0] for r in User.RoleAccess.choices}
    updated = skipped = 0
    warnings = []

    try:
        with transaction.atomic():
            for i, row in enumerate(rows_raw[1:], start=2):
                lu = get_col(row, "login_username")
                if not lu:
                    skipped += 1
                    continue

                try:
                    user_obj = User.objects.get(login_username=lu)
                except User.DoesNotExist:
                    warnings.append(f"Row {i}: '{lu}' not found — skipped.")
                    skipped += 1
                    continue

                role = get_col(row, "role_access")
                if role and role not in valid_roles:
                    warnings.append(f"Row {i}: invalid role '{role}' for '{lu}' — skipped.")
                    skipped += 1
                    continue

                # Check agent limit when promoting a non-agent to an agent role
                if (role and role in AGENT_ROLES and user_obj.role_access not in AGENT_ROLES):
                    cur, mx = _get_agent_limit()
                    if mx is not None and cur >= mx:
                        warnings.append(
                            f"Row {i}: cannot promote '{lu}' to '{role}' — "
                            f"agent limit reached ({cur}/{mx}). Skipped."
                        )
                        skipped += 1
                        continue

                if "display_name" in header:
                    v = get_col(row, "display_name")
                    if v:
                        user_obj.username = v
                if "email" in header:
                    v = get_col(row, "email")
                    if v:
                        user_obj.email = v
                if "nik" in header:
                    user_obj.nik = get_col(row, "nik") or None
                if "role_access" in header and role:
                    user_obj.role_access = role
                if "initials" in header:
                    v = get_col(row, "initials")
                    if v:
                        user_obj.initials = v
                if "can_handle_confidential" in header:
                    raw = get_col(row, "can_handle_confidential").upper()
                    user_obj.can_handle_confidential = raw in ("TRUE", "1", "YES")

                new_pw = get_col(row, "new_password")
                if new_pw:
                    user_obj.set_password(new_pw)

                user_obj.save()
                updated += 1

    except Exception as e:
        messages.error(request, f"Update failed: {e}")
        return redirect("users_desk:user_list")

    for w in warnings[:5]:
        messages.warning(request, w)
    if len(warnings) > 5:
        messages.warning(request, f"... and {len(warnings) - 5} more rows skipped.")

    messages.success(request, f"Update complete: {updated} user(s) updated, {skipped} skipped.")
    return redirect("users_desk:user_list")


@superadmin_required
@require_POST
def user_bulk_archive(request):
    """Bulk archive selected users."""
    selected_ids = request.POST.getlist("selected_ids")
    if not selected_ids:
        messages.warning(request, "No users selected for archiving.")
        return redirect("users_desk:user_list")

    qs = User.objects.filter(pk__in=selected_ids).exclude(pk=request.user.pk)
    count = qs.count()
    qs.update(is_active=False)

    skipped = len(selected_ids) - count
    msg = f"Archived {count} user(s)."
    if skipped:
        msg += f" {skipped} skipped (you cannot archive yourself)."
    messages.success(request, msg)
    return redirect("users_desk:user_list")


@superadmin_required
@require_POST
def user_bulk_unarchive(request):
    """Bulk unarchive selected users."""
    selected_ids = request.POST.getlist("selected_ids")
    if not selected_ids:
        messages.warning(request, "No users selected for unarchiving.")
        return redirect("users_desk:user_list")

    qs = User.objects.filter(pk__in=selected_ids)
    current_agents, max_agents = _get_agent_limit()
    if max_agents is not None:
        new_agents = qs.filter(role_access__in=AGENT_ROLES, is_active=False).count()
        if current_agents + new_agents > max_agents:
            messages.error(
                request,
                f"Unarchiving would exceed your agent limit ({current_agents}/{max_agents}). "
                f"This selection contains {new_agents} inactive agent-role user(s). "
                "Upgrade your license or unarchive fewer users."
            )
            return redirect("users_desk:user_list")

    count = qs.count()
    qs.update(is_active=True)

    messages.success(request, f"Unarchived {count} user(s).")
    return redirect("users_desk:user_list")


@superadmin_required
@require_POST
def user_bulk_grant_modules(request):
    """Bulk-add module access (e.g. E-Sign, Form Creator, Shared Docs, Shorten
    Link) to selected users. Only adds — never removes a module the user
    already has, whether from their role defaults or an earlier override."""
    selected_ids = request.POST.getlist("selected_ids")
    modules_to_grant = [m for m in request.POST.getlist("bulk_grant_modules") if m]

    if not selected_ids:
        messages.warning(request, "No users selected.")
        return redirect("users_desk:user_list")
    if not modules_to_grant:
        messages.warning(request, "No modules selected to grant.")
        return redirect("users_desk:user_list")

    valid_slugs = {slug for slug, _label, _emoji, _section in MODULE_REGISTRY}
    modules_to_grant = [m for m in modules_to_grant if m in valid_slugs]

    updated = 0
    for user_obj in User.objects.filter(pk__in=selected_ids):
        current = user_obj.get_accessible_modules()
        merged = current | set(modules_to_grant)
        if merged != current:
            user_obj.module_access = sorted(merged)
            user_obj.save(update_fields=["module_access"])
            updated += 1

    module_labels = ", ".join(
        label for slug, label, _emoji, _section in MODULE_REGISTRY if slug in modules_to_grant
    )
    skipped = len(selected_ids) - updated
    msg = f"Granted {module_labels} access to {updated} user(s)."
    if skipped:
        msg += f" {skipped} already had all selected modules."
    messages.success(request, msg)
    return redirect("users_desk:user_list")


@superadmin_required
@require_POST
def user_archive(request, pk):
    """Archive a single user."""
    user_obj = get_object_or_404(User, pk=pk)
    if user_obj == request.user:
        messages.error(request, "You cannot archive your own account.")
    else:
        user_obj.is_active = False
        user_obj.save()
        messages.success(request, f'User "{user_obj.login_username}" archived.')
    return redirect("users_desk:user_list")


@superadmin_required
@require_POST
def user_unarchive(request, pk):
    """Unarchive a single user."""
    user_obj = get_object_or_404(User, pk=pk)

    if user_obj.role_access in AGENT_ROLES:
        current_agents, max_agents = _get_agent_limit()
        if max_agents is not None and current_agents >= max_agents:
            messages.error(
                request,
                f"Agent limit reached ({current_agents}/{max_agents}). "
                "Upgrade your license to reactivate this agent-role user."
            )
            return redirect("users_desk:user_list")

    user_obj.is_active = True
    user_obj.save()
    messages.success(request, f'User "{user_obj.login_username}" unarchived.')
    return redirect("users_desk:user_list")


@superadmin_required
def portal_modules_settings(request):
    from .models import UserRole
    config = SiteConfig.get_solo()
    valid_slugs = {slug for slug, _label, _emoji, _section in MODULE_REGISTRY}
    # Every active User Role (built-in and custom) gets its own module defaults.
    role_choices = list(UserRole.objects.filter(is_active=True).order_by("sort_order", "name").values_list("code", "name"))

    if request.method == "POST":
        defaults = {}
        for role, _label in role_choices:
            selected = list(dict.fromkeys(
                s for s in request.POST.getlist(f"modules_{role}") if s in valid_slugs
            ))
            defaults[role] = selected
        config.role_module_defaults = defaults
        config.sync_portal_user_modules_from_role_defaults()
        config.updated_by = request.user
        config.save()
        messages.success(
            request,
            "Role module defaults saved. Every user of that role receives the checked modules.",
        )
        active = request.POST.get("active_role") or User.RoleAccess.PORTALUSER
        return redirect(f"{reverse('users_desk:portal_modules_settings')}?role={active}")

    stored = config.role_module_defaults if isinstance(config.role_module_defaults, dict) else {}
    role_panels = []
    for role, label in role_choices:
        if role in stored and isinstance(stored.get(role), list):
            checked = [s for s in stored[role] if s in valid_slugs]
        else:
            checked = sorted(SiteConfig.get_role_modules(role))
        role_panels.append({
            "role": role,
            "label": label,
            "checked": checked,
        })

    return render(request, "desk/users/portal_modules.html", {
        "module_sections": _build_module_sections(),
        "role_panels": role_panels,
        "active_role": request.GET.get("role") or User.RoleAccess.PORTALUSER,
    })

