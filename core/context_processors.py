def site_config(request):
    """
    Context processor to inject global site configuration into all templates.
    """
    from .models import SiteConfig, SSOConfig
    return {
        "site_config": SiteConfig.get_solo(),
        "sso_config": SSOConfig.get_solo(),
    }
