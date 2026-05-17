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
