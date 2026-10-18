import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.views_user import superadmin_required
from .models import Employee, CompanyUnit
from .forms import EmployeeForm
from .excel_utils import safe_cell, export_queryset_xlsx
from .list_actions import get_selected, bulk_set_archived, bulk_delete
from .list_pagination import paginate


def _phone_search_variants(token):
    variants = {token}
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) < 3:
        return variants
    variants.add(digits)
    if digits.startswith("0"):
        variants.add("62" + digits[1:])
        variants.add("+62" + digits[1:])
    elif digits.startswith("62"):
        variants.add("+" + digits)
        variants.add("0" + digits[2:])
    elif digits.startswith("8"):
        variants.add("+62" + digits)
        variants.add("62" + digits)
        variants.add("0" + digits)
    return variants


def filter_employees(qs, q):
    q = (q or "").strip()
    if not q:
        return qs
    for token in q.split():
        phone_q = Q()
        for variant in _phone_search_variants(token):
            phone_q |= Q(phone_number__icontains=variant)
        qs = qs.filter(
            Q(full_name__icontains=token)
            | Q(email__icontains=token)
            | phone_q
            | Q(job_role__icontains=token)
            | Q(unit__code__icontains=token)
            | Q(unit__name__icontains=token)
            | Q(user__nik__icontains=token)
            | Q(user__login_username__icontains=token)
            | Q(user__username__icontains=token)
        )
    return qs.distinct()


@login_required
@superadmin_required
def employee_list(request):
    from core.dynamic_filters import apply_dynamic_filters, active_filters_json, fields_json

    q = request.GET.get('q', '').strip()
    qs = Employee.objects.select_related("user", "unit", "reports_to")
    qs = filter_employees(qs, q)

    employee_filterable_fields = {
        "unit": {
            "label": "Company Unit", "type": "choice", "lookup": "unit_id",
            "choices": [(str(u.pk), f"{u.code} — {u.name}") for u in CompanyUnit.objects.order_by('code')],
        },
        "job_role": {"label": "Job Role / Department", "type": "text", "lookup": "job_role"},
        "is_archived": {
            "label": "Status", "type": "choice", "lookup": "is_archived",
            "choices": [("false", "Active"), ("true", "Archived")],
            "python_type": "bool",
        },
    }
    qs, active_filters = apply_dynamic_filters(qs, request, employee_filterable_fields)

    viewing_archived_only = any(
        af["field_key"] == "is_archived" and af["value"] == "true" for af in active_filters
    )

    context = paginate(request, qs)
    context.update({
        "q": q,
        "active_filters": active_filters,
        "active_filters_json": active_filters_json(active_filters),
        "dynamic_fields_json": fields_json(employee_filterable_fields),
        "has_active_filters": bool(active_filters),
        "viewing_archived_only": viewing_archived_only,
    })
    return render(request, "desk/employees/list.html", context)

@login_required
@superadmin_required
def employee_create(request):
    if request.method == "POST":
        form = EmployeeForm(request.POST)
        if form.is_valid():
            employee = form.save(commit=False)
            employee.created_by = request.user
            employee.save()
            messages.success(request, f"Employee {employee.full_name} created successfully.")
            return redirect("users_desk:employee_list") # Will fix namespace in urls
    else:
        form = EmployeeForm()
    
    return render(request, "desk/employees/form.html", {
        "form": form,
        "title": "Create Employee"
    })

@login_required
@superadmin_required
def employee_edit(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=employee)
        if form.is_valid():
            employee = form.save(commit=False)
            employee.updated_by = request.user
            employee.save()
            messages.success(request, f"Employee {employee.full_name} updated successfully.")
            return redirect("users_desk:employee_list")
    else:
        form = EmployeeForm(instance=employee)
    
    return render(request, "desk/employees/form.html", {
        "form": form,
        "title": "Edit Employee",
        "employee": employee
    })

@login_required
@superadmin_required
def employee_delete(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        name = employee.full_name
        employee.delete()
        messages.success(request, f"Employee {name} deleted.")
        return redirect("users_desk:employee_list")
    return redirect("users_desk:employee_list")

@login_required
@superadmin_required
def employee_export_template(request):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Employees Import"
    headers = [
        "Full Name", "Email", "Phone Number (E164)", "Job Role", 
        "Unit Code", "Manager Email (Reports To)"
    ]
    ws.append(headers)
    
    # Add an example row
    ws.append([
        "John Doe", "john.doe@corp.com", "+6281234567890", "IT Staff", 
        "IT", "jane.manager@corp.com"
    ])
    
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="employee_import_template.xlsx"'
    wb.save(response)
    return response

@login_required
@superadmin_required
def employee_import(request):
    if request.method == "POST":
        file = request.FILES.get('excel_file')
        if not file:
            messages.error(request, "Please upload an Excel file.")
            return redirect("users_desk:employee_list")
            
        try:
            wb = openpyxl.load_workbook(file, data_only=True)
            ws = wb.active
            
            rows = list(ws.rows)
            if len(rows) < 2:
                messages.error(request, "Excel file is empty or missing data.")
                return redirect("users_desk:employee_list")
                
            success = 0
            errors = 0
            
            for row in rows[1:]:
                if not row[0].value:
                    continue
                
                full_name = str(row[0].value).strip()
                email = str(row[1].value).strip() if row[1].value else None
                phone = str(row[2].value).strip() if row[2].value else None
                job_role = str(row[3].value).strip() if row[3].value else ""
                unit_code = str(row[4].value).strip() if row[4].value else None
                manager_email = str(row[5].value).strip() if row[5].value else None
                
                if not email and not phone:
                    errors += 1
                    continue
                    
                # Get or create unit
                unit = None
                if unit_code:
                    unit = CompanyUnit.objects.filter(code__iexact=unit_code).first()
                if not unit:
                    # Fallback to first unit if not found (or raise error)
                    unit = CompanyUnit.objects.first()
                    
                employee, created = Employee.objects.update_or_create(
                    email=email if email else phone, # email is unique, but fallback
                    defaults={
                        "full_name": full_name,
                        "phone_number": phone if phone and str(phone).startswith('+') else None,
                        "job_role": job_role,
                        "unit": unit
                    }
                )
                success += 1
            
            # Second pass for managers to ensure employees exist
            for row in rows[1:]:
                if not row[0].value:
                    continue
                email = str(row[1].value).strip() if row[1].value else None
                manager_email = str(row[5].value).strip() if row[5].value else None
                
                if email and manager_email:
                    emp = Employee.objects.filter(email=email).first()
                    mgr = Employee.objects.filter(email=manager_email).first()
                    if emp and mgr:
                        emp.reports_to = mgr
                        emp.save()
            
            messages.success(request, f"Import complete: {success} successful, {errors} skipped.")
        except Exception as e:
            messages.error(request, f"Error processing file: {str(e)}")
            
        return redirect("users_desk:employee_list")

    return render(request, "desk/employees/import.html")


# ---------------------------------------------------------------------------
# Standardized bulk actions (Archive / Unarchive / Delete / Duplicate / Export)
# ---------------------------------------------------------------------------

@login_required
@superadmin_required
@require_POST
def employee_bulk_archive(request):
    qs = get_selected(request, Employee.objects.all())
    count = bulk_set_archived(qs, field_name="is_archived", value=True)
    messages.success(request, f"Archived {count} employee(s).")
    return redirect("users_desk:employee_list")


@login_required
@superadmin_required
@require_POST
def employee_bulk_unarchive(request):
    qs = get_selected(request, Employee.objects.all())
    count = bulk_set_archived(qs, field_name="is_archived", value=False)
    messages.success(request, f"Unarchived {count} employee(s).")
    return redirect("users_desk:employee_list")


@login_required
@superadmin_required
@require_POST
def employee_bulk_delete(request):
    qs = get_selected(request, Employee.objects.all())
    count = bulk_delete(qs)
    messages.success(request, f"Deleted {count} employee(s).")
    return redirect("users_desk:employee_list")


@login_required
@superadmin_required
@require_POST
def employee_bulk_duplicate(request):
    """Clone the job role / department / entity assignment of a single
    selected employee, blanking out identity fields (name/NIK/contact) so
    the admin fills in a real new employee rather than getting an exact
    clone of an existing person."""
    ids = request.POST.getlist("selected_ids")
    if len(ids) != 1:
        messages.warning(request, "Select exactly one employee to duplicate.")
        return redirect("users_desk:employee_list")
    original = get_object_or_404(Employee, pk=ids[0])
    new_employee = Employee.objects.create(
        full_name="New Employee",
        email=None,
        phone_number=None,
        job_role=original.job_role,
        unit=original.unit,
        reports_to=original.reports_to,
        created_by=request.user,
        updated_by=request.user,
    )
    messages.success(
        request,
        f'Created a new draft employee in "{new_employee.unit.code}" with job role '
        f'"{new_employee.job_role or "-"}" copied from {original.full_name}. '
        "Please fill in the name, NIK, and contact details.",
    )
    return redirect("users_desk:employee_edit", pk=new_employee.pk)


@login_required
@superadmin_required
@require_POST
def employee_bulk_export(request):
    qs = get_selected(request, Employee.objects.select_related("user", "unit"))
    if not qs.exists():
        qs = Employee.objects.select_related("user", "unit").all()
    columns = [
        ("Full Name", lambda o: safe_cell(o.full_name)),
        ("NIK", lambda o: safe_cell(o.user.nik if o.user else "")),
        ("Job Role", lambda o: safe_cell(o.job_role)),
        ("Unit", lambda o: safe_cell(o.unit.code if o.unit else "")),
        ("Email", lambda o: safe_cell(o.email or "")),
        ("Phone", lambda o: safe_cell(o.phone_number or "")),
        ("Archived", lambda o: "Yes" if o.is_archived else "No"),
    ]
    return export_queryset_xlsx(qs.order_by("full_name"), columns, "employees_export.xlsx", sheet_title="Employees")
