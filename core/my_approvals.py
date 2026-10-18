"""Everything waiting on one person's decision, for the My Approvals page and its sidebar count.

- Ticket approvals: supporting letters / change requests (ChangeRequestApproval) and routing approvals (TicketApproval).
- Knowledge Base: articles waiting for review (Manager / SuperAdmin with Knowledge Management).
"""
from django.urls import reverse


def _employee(user):
    from .models import Employee

    return Employee.objects.filter(email__iexact=user.email).first() if user.email else None


def kb_items(user):
    from .models import User

    if user.role_access not in (User.RoleAccess.MANAGER, User.RoleAccess.SUPERADMIN):
        return []
    if "knowledge_management" not in user.get_accessible_modules():
        return []
    from knowledge_base.models import Article

    articles = Article.objects.filter(status=Article.Status.PENDING).select_related("category", "updated_by").order_by("updated_at")
    return [{
        "kind": "Knowledge Base", "action": "Review & approve", "title": article.title,
        "subtitle": " · ".join(filter(None, [
            article.category.name if article.category_id else "",
            f"submitted by {article.updated_by.get_full_name() or article.updated_by.username}" if article.updated_by_id else "",
        ])),
        "when": article.updated_at, "url": reverse("kb_desk:article_edit", args=[article.pk]),
    } for article in articles]


def pending_count(user):
    """The sidebar badge: every item waiting on this person."""
    from cases.models import ChangeRequestApproval, TicketApproval

    total = ChangeRequestApproval.objects.filter(approver=user, status=ChangeRequestApproval.Status.PENDING).count()
    employee = _employee(user)
    if employee:
        total += TicketApproval.objects.filter(approver=employee, status=TicketApproval.Status.PENDING).count()
    return total + len(kb_items(user))
