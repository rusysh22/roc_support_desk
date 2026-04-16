from django.core.management.base import BaseCommand
from licensing.models import LicenseRecord
from licensing.validators import verify_license_online

class Command(BaseCommand):
    help = "Triggers an online verification of the current license against the marketplace."

    def handle(self, *args, **options):
        license = LicenseRecord.get_current()
        
        if not license.license_key:
            self.stdout.write(self.style.ERROR("No license key installed. Cannot verify online."))
            return

        self.stdout.write("Verifying license key with marketplace...")
        
        is_valid = verify_license_online(license)
        
        if is_valid:
            self.stdout.write(self.style.SUCCESS("✅ License successfully verified and updated from marketplace."))
        else:
            self.stdout.write(self.style.ERROR("❌ License verification failed. It has been marked as suspended."))
