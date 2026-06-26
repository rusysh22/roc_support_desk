import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.views_user import superadmin_required
from .models import Employee, CompanyUnit
from .forms import EmployeeForm
from .excel_utils import safe_cell

@login_required
@superadmin_required
def employee_list(request):
    q = request.GET.get('q', '').strip()
    qs = Employee.objects.all()
    if q:
        qs = qs.filter(
            Q(full_name__icontains=q) | 
            Q(email__icontains=q) |
            Q(phone_number__icontains=q)
        )
    
    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    
    context = {
        "page_obj": page_obj,
        "q": q,
    }
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
