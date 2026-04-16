import logging
from django.core.management.base import BaseCommand
from licensing.models import LicenseRecord

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Enforces license degradation by disabling external gateways (WhatsApp/Email) if the license is inactive."

    def handle(self, *args, **options):
        license = LicenseRecord.get_current()
        status = license.get_effective_status()
        
        self.stdout.write(f"Current Effective License Status: {status.upper()}")

        if status in ['active', 'grace', 'trial']:
            self.stdout.write(self.style.SUCCESS("License is active/grace/trial. Apps connection remains intact."))
            return

        self.stdout.write(self.style.WARNING("License is inactive/partial_lock. Disconnecting gateways..."))
        
        # 1. Disconnect WhatsApp
        try:
            from gateways.services import EvolutionAPIService
            svc = EvolutionAPIService()
            # Check if instance is connected first
            state_data = svc.get_instance_state()
            state = state_data.get("instance", {}).get("state", "UNKNOWN") if state_data else "UNKNOWN"
            
            if state in ["open", "connected"]:
                svc.logout_instance()
                self.stdout.write(self.style.SUCCESS("✅ WhatsApp instance logged out successfully."))
                
                # Log the disconnection event
                from licensing.models import LicenseAuditLog
                LicenseAuditLog.objects.create(
                    event='disconnected_wa',
                    payload={'reason': 'license_inactive', 'status': status},
                    signature_valid=True
                )
            else:
                self.stdout.write(self.style.NOTICE("WhatsApp instance is already disconnected."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Failed to logout WhatsApp: {e}"))
            logger.error(f"License enforcement: Failed to logout WhatsApp: {e}")

        # 2. Disable Email Settings
        try:
            from core.models import EmailConfig
            email_config = EmailConfig.get_solo()
            updated = False
            
            if email_config.imap_enabled or email_config.smtp_enabled:
                email_config.imap_enabled = False
                email_config.smtp_enabled = False
                email_config.save()
                updated = True
                self.stdout.write(self.style.SUCCESS("✅ Email IMAP and SMTP configurations disabled."))
                
                # Log the disconnection event
                from licensing.models import LicenseAuditLog
                LicenseAuditLog.objects.create(
                    event='disconnected_email',
                    payload={'reason': 'license_inactive', 'status': status},
                    signature_valid=True
                )
            else:
                self.stdout.write(self.style.NOTICE("Email configurations already disabled."))
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Failed to disable Email settings: {e}"))
            logger.error(f"License enforcement: Failed to disable Email settings: {e}")
            
        self.stdout.write(self.style.SUCCESS("\nLicense enforcement completed."))
