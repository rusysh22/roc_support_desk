"""
Core App — User Master Data Views
====================
Provides CRUD operations for User management.
Restricted to SuperAdmin only.
"""
import io

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import UserAdminForm
from .models import User


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
    role_filter = request.GET.get("role", "")
    status_filter = request.GET.get("status", "active") # Default to active

    qs = User.objects.all().order_by("login_username")

    if status_filter == "active":
        qs = qs.filter(is_active=True)
    elif status_filter == "archived":
        qs = qs.filter(is_active=False)

    if search:
        qs = qs.filter(
            Q(username__icontains=search)
            | Q(login_username__icontains=search)
            | Q(email__icontains=search)
            | Q(nik__icontains=search)
        )
    if role_filter:
        qs = qs.filter(role_access=role_filter)

    paginator = Paginator(qs, 15)
    page = paginator.get_page(request.GET.get("page", 1))

    return render(request, "desk/users/list.html", {
        "users": page,
        "search_query": search,
        "role_filter": role_filter,
        "status_filter": status_filter,
        "role_choices": User.RoleAccess.choices,
    })


@superadmin_required
def user_create(request):
    """Create a new user."""
    if request.method == "POST":
        form = UserAdminForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, f'User "{user.login_username}" created successfully.')
            return redirect("users_desk:user_list")
    else:
        form = UserAdminForm()

    return render(request, "desk/users/form.html", {
        "form": form,
        "is_edit": False,
    })


@superadmin_required
def user_edit(request, pk):
    """Edit an existing user."""
    user_obj = get_object_or_404(User, pk=pk)

    if request.method == "POST":
        form = UserAdminForm(request.POST, instance=user_obj)
        if form.is_valid():
            form.save()
            messages.success(request, f'User "{user_obj.login_username}" updated successfully.')
            return redirect("users_desk:user_list")
    else:
        form = UserAdminForm(instance=user_obj)

    return render(request, "desk/users/form.html", {
        "form": form,
        "is_edit": True,
        "user_obj": user_obj,
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


def _make_xlsx_response(filename):
    """Return an HttpResponse pre-configured for xlsx download."""
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _style_header_row(sheet):
    """Apply a dark-indigo header style to row 1 of the worksheet."""
    header_fill = PatternFill(start_color="3730A3", end_color="3730A3", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.row_dimensions[1].height = 20


@superadmin_required
def user_export(request):
    """Export selected users (by checkbox IDs) to Excel (.xlsx).
    If no IDs supplied, exports ALL users."""
    selected_ids = request.POST.getlist("selected_ids") or request.GET.getlist("ids")

    qs = User.objects.all().order_by("login_username")
    if selected_ids:
        qs = qs.filter(pk__in=selected_ids)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Users"

    # Header row
    ws.append(EXCEL_HEADERS)
    _style_header_row(ws)

    # Auto column widths
    col_widths = {h: len(h) + 4 for h in EXCEL_HEADERS}

    # Data rows
    for u in qs:
        row = [
            u.login_username,
            u.username,
            u.email,
            u.nik or "",
            u.role_access,
            u.initials,
            "TRUE" if u.can_handle_confidential else "FALSE",
        ]
        ws.append(row)
        for idx, val in enumerate(row):
            col_widths[EXCEL_HEADERS[idx]] = max(col_widths[EXCEL_HEADERS[idx]], len(str(val or "")) + 2)

    for idx, header in enumerate(EXCEL_HEADERS, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = col_widths[header]

    # Write to response
    response = _make_xlsx_response("users_export.xlsx")
    wb.save(response)
    return response


@superadmin_required
def user_import_template(request):
    """Download a blank Excel template with the correct column headers and one sample row."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Import Template"

    ws.append(EXCEL_HEADERS)
    _style_header_row(ws)

    # One sample row
    ws.append(["jdoe", "John Doe", "jdoe@example.com", "EMP001", "SupportDesk", "jd", "FALSE"])

    # Add a Notes sheet explaining each column
    notes_ws = wb.create_sheet(title="Notes")
    notes_ws.append(["Column", "Description", "Required", "Valid Values"])
    _style_header_row(notes_ws)
    valid_roles = ", ".join(r[0] for r in User.RoleAccess.choices)
    notes_data = [
        ["login_username", "Unique username used to log in", "Yes", "-"],
        ["display_name", "Full name displayed in the system", "Yes", "-"],
        ["email", "Unique email address", "Yes", "-"],
        ["nik", "Employee ID (Nomor Induk Karyawan)", "No", "-"],
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

    response = _make_xlsx_response("user_import_template.xlsx")
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

    for i, row in enumerate(data_rows, start=2):  # start=2 because row 1 is header
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

        # Check for duplicates in the existing database
        if lu and User.objects.filter(login_username=lu).exists():
            errors.append(f"Row {i}: login_username '{lu}' already exists.")
        if email and User.objects.filter(email=email).exists():
            errors.append(f"Row {i}: email '{email}' already exists.")
        if nik and User.objects.filter(nik=nik).exists():
            errors.append(f"Row {i}: NIK '{nik}' already exists.")

    if errors:
        for err in errors:
            messages.error(request, err)
        return redirect("users_desk:user_list")

    # --- All OK — create users inside an atomic transaction ---
    DEFAULT_PASSWORD = "RoCDesk123!"
    created_count = 0

    try:
        with transaction.atomic():
            for row in data_rows:
                lu = get_col(row, "login_username")
                confidence_raw = get_col(row, "can_handle_confidential").upper()
                can_confidential = confidence_raw in ("TRUE", "1", "YES")
                role = get_col(row, "role_access") or User.RoleAccess.SUPPORTDESK

                user_obj = User(
                    login_username=lu,
                    username=get_col(row, "display_name"),
                    email=get_col(row, "email"),
                    nik=get_col(row, "nik") or None,
                    role_access=role,
                    initials=get_col(row, "initials"),
                    can_handle_confidential=can_confidential,
                )
                user_obj.set_password(DEFAULT_PASSWORD)
                user_obj.save()
                created_count += 1
    except Exception as e:
        messages.error(request, f"Import failed due to an unexpected error: {e}")
        return redirect("users_desk:user_list")

    messages.success(
        request,
        f"Successfully imported {created_count} user(s). Default password: {DEFAULT_PASSWORD}",
    )
    return redirect("users_desk:user_list")


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
    count = qs.count()
    qs.update(is_active=True)

    messages.success(request, f"Unarchived {count} user(s).")
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
    user_obj.is_active = True
    user_obj.save()
    messages.success(request, f'User "{user_obj.login_username}" unarchived.')
    return redirect("users_desk:user_list")

