def site_config(request):
    """
    Context processor to inject global site configuration into all templates.
    """
    from .models import SiteConfig, SSOConfig, AIConfig
    return {
        "site_config": SiteConfig.get_solo(),
        "sso_config": SSOConfig.get_solo(),
        "ai_config": AIConfig.get_solo(),
    }


def user_modules(request):
    """
    Inject the set of module slugs the current user can access.
    Templates use: {% if 'module_slug' in user_modules %}
    """
    if request.user.is_authenticated:
        return {"user_modules": request.user.get_accessible_modules()}
    return {"user_modules": set()}
