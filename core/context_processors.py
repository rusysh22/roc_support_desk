def site_config(request):
    """
    Context processor to inject global site configuration into all templates.
    """
    from .models import SiteConfig
    return {
        'site_config': SiteConfig.get_solo()
    }
