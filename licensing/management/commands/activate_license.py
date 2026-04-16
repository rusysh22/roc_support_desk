from django.core.management.base import BaseCommand
from licensing.validators import activate_license_with_marketplace

class Command(BaseCommand):
    help = "Manually activate a license key via CLI."

    def add_arguments(self, parser):
        parser.add_argument('license_key', type=str, help='The license key string.')

    def handle(self, *args, **options):
        key = options['license_key']
        self.stdout.write(f"Attempting to activate license key: {key[:8]}...")
        
        result = activate_license_with_marketplace(key)
        
        if result['success']:
            self.stdout.write(self.style.SUCCESS(f"✅ License activated! Plan: {result.get('plan')}"))
        else:
            self.stdout.write(self.style.ERROR(f"❌ Activation failed: {result.get('error')}"))
