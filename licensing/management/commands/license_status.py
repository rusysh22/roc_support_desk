import json
from django.core.management.base import BaseCommand
from licensing.models import LicenseRecord

class Command(BaseCommand):
    help = "Display the current physical and effective license status."

    def handle(self, *args, **options):
        license = LicenseRecord.get_current()
        status_color = self.style.SUCCESS if license.get_effective_status() in ['active', 'trial', 'grace'] else self.style.ERROR

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== RoC Support Desk License Status ==="))
        self.stdout.write(f"Installation Fingerprint : {license.install_fingerprint}")
        self.stdout.write(f"Database Status          : {license.status}")
        self.stdout.write(f"Effective Status         : {status_color(license.get_effective_status().upper())}")
        self.stdout.write(f"Plan Tier                : {license.plan_tier.upper()}")
        self.stdout.write(f"Max Agents               : {license.max_agents}")
        
        expires_str = license.expires_at.strftime('%Y-%m-%d %H:%M') if license.expires_at else "Never"
        self.stdout.write(f"Expires At               : {expires_str}")
        self.stdout.write(f"Has Expired              : {'Yes' if license.has_expired else 'No'}")
        
        if license.has_expired:
            self.stdout.write(self.style.WARNING(f"Days Since Expiry        : {license.days_since_expiry}"))
            self.stdout.write(self.style.WARNING(f"Days Until Partial Lock  : {license.days_until_partial_lock}"))

        last_ver_str = license.last_verified_at.strftime('%Y-%m-%d %H:%M') if license.last_verified_at else "Never"
        self.stdout.write(f"Last Verified Online     : {last_ver_str}")
        self.stdout.write(f"Issued To                : {license.issued_to}")
        self.stdout.write("\nFeatures:")
        for feat, val in license.features_json.items():
            state = self.style.SUCCESS("ON") if val else self.style.NOTICE("OFF")
            self.stdout.write(f"  - {feat.ljust(15)} : {state}")
        self.stdout.write("\n")
