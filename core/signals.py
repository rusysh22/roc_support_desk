import logging

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import CompanyUnit, Employee, User

logger = logging.getLogger(__name__)


def _default_employee_unit():
    code = getattr(settings, "DEFAULT_EMPLOYEE_UNIT_CODE", "UNMAPPED") or "UNMAPPED"
    unit, _ = CompanyUnit.objects.get_or_create(
        code=code,
        defaults={"name": "Unmapped Employees"},
    )
    return unit


@receiver(post_save, sender=User)
def ensure_employee_for_user(sender, instance, created, **kwargs):
    """
    Keep every login user attached to an Employee profile.

    User.nik remains the single employee identifier. Employee exists for
    operational workflows that need requester/manager/contact data.
    """
    email = (instance.email or "").strip()
    if not email:
        return

    try:
        employee = instance.employee_profile
    except ObjectDoesNotExist:
        employee = None

    if employee is None:
        employee = Employee.objects.filter(email__iexact=email, user__isnull=True).first()
    if employee is None:
        employee = Employee(user=instance, unit=_default_employee_unit())

    employee.user = instance
    employee.full_name = instance.username or instance.login_username or email
    employee.email = email
    if not employee.unit_id:
        employee.unit = _default_employee_unit()
    try:
        employee.save()
    except IntegrityError:
        logger.exception("Could not create/update Employee profile for user %s", instance.pk)
