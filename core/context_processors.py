def site_config(request):
    """
    Context processor to inject global site configuration into all templates.
    """
    from django.conf import settings
    from .models import SiteConfig, SSOConfig, AIConfig
    return {
        "site_config": SiteConfig.get_solo(),
        "sso_config": SSOConfig.get_solo(),
        "ai_config": AIConfig.get_solo(),
        "app_version": settings.APP_VERSION,
        "wa_enabled": settings.WA_ENABLED,
    }


def user_modules(request):
    """
    Inject the set of module slugs the current user can access.
    Templates use: {% if 'module_slug' in user_modules %}
    """
    if request.user.is_authenticated:
        return {"user_modules": sorted(request.user.get_accessible_modules())}
    return {"user_modules": []}


def my_tickets_badge(request):
    """
    Personal unread ticket counts for the portal and Task Management sidebar.
    Opening a ticket acknowledges activity only for the signed-in participant.
    """
    user = request.user
    if not user.is_authenticated:
        return {"my_tickets_unread_count": 0}

    from cases.activity import unread_cases
    count = unread_cases(user).count()
    if user.role_access == user.RoleAccess.PORTALUSER:
        return {"my_tickets_unread_count": count, "task_management_unread_count": 0}
    return {"my_tickets_unread_count": 0, "task_management_unread_count": count}


def pending_actions_badge(request):
    """
    Sidebar counts for "My Approvals" and "My Signatures": items still waiting
    on the signed-in user. Mirrors the pending_count those pages compute.
    """
    user = request.user
    if not user.is_authenticated:
        return {"my_approvals_pending_count": 0, "my_signatures_pending_count": 0}

    from esign.models import Signer

    from .my_approvals import pending_count

    # tickets and Knowledge Base items waiting on this person
    approvals = pending_count(user)

    signatures = Signer.objects.filter(user=user, status=Signer.Status.PENDING).count()
    return {"my_approvals_pending_count": approvals, "my_signatures_pending_count": signatures}


def widget_user_unit(request):
    """
    The logged-in user's Employee.unit — used by the Support Chat bubble
    (templates/base.html) to prefill the "Unit" confirmation field the same
    way it already prefills Requestor name/email, instead of leaving it
    blank for the user to re-select something already on file.
    """
    if request.user.is_authenticated and request.user.email:
        from .models import Employee

        unit = (
            Employee.objects
            .filter(email=request.user.email, unit__isnull=False)
            .select_related("unit", "unit__parent")
            .values_list("unit__id", "unit__name", "unit__parent__name")
            .first()
        )
        if unit:
            return {
                "widget_user_unit_id": unit[0],
                "widget_user_unit_name": unit[1],
                "widget_user_unit_parent": unit[2] or "",
            }
    return {"widget_user_unit_id": "", "widget_user_unit_name": "", "widget_user_unit_parent": ""}
