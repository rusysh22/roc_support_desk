import logging
from django.core.mail.backends.smtp import EmailBackend
from core.models import EmailConfig

logger = logging.getLogger(__name__)


class DynamicEmailBackend(EmailBackend):
    """
    A custom email backend that ignores django.conf.settings and instead
    fetches the SMTP credentials directly from the ``EmailConfig`` database model
    every time an email needs to be sent.
    """
    
    def __init__(self, host=None, port=None, username=None, password=None, 
                 use_tls=None, fail_silently=False, use_ssl=None, timeout=None,
                 ssl_keyfile=None, ssl_certfile=None,
                 **kwargs):
        
        try:
            config = EmailConfig.get_solo()
            
            # If the config specifies a host and port, we inject them
            if config.smtp_host:
                host = config.smtp_host
                port = config.smtp_port
                username = config.smtp_user
                password = config.smtp_password
                use_tls = config.smtp_use_tls
                use_ssl = config.smtp_use_ssl
        except Exception as exc:
            logger.error("Failed to load EmailConfig for DynamicEmailBackend: %s", exc)

        super().__init__(
            host=host, 
            port=port, 
            username=username, 
            password=password, 
            use_tls=use_tls, 
            fail_silently=fail_silently, 
            use_ssl=use_ssl, 
            timeout=timeout, 
            ssl_keyfile=ssl_keyfile, 
            ssl_certfile=ssl_certfile, 
            **kwargs
        )
