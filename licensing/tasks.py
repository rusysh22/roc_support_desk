import logging

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


@shared_task(name="licensing.verify_license_periodic", bind=True, max_retries=0)
def verify_license_periodic(self):
    """
    Periodic online re-verification of the installed license.

    Skips if verified recently (within VERIFY_INTERVAL_HOURS) so it is safe
    to schedule this task more frequently than needed.
    """
    from django.utils import timezone
    from .models import LicenseRecord
    from .validators import verify_license_online

    cfg = getattr(settings, 'LICENSE_SETTINGS', {})
    interval_hours = cfg.get('VERIFY_INTERVAL_HOURS', 24)

    license_obj = LicenseRecord.get_current()

    if not license_obj.license_key:
        logger.info("[License] No license key installed — skipping periodic verification.")
        return

    # Skip if verified recently enough
    if license_obj.last_verified_at:
        hours_since = (timezone.now() - license_obj.last_verified_at).total_seconds() / 3600
        if hours_since < interval_hours:
            logger.info(
                "[License] Verified %.1fh ago (interval=%dh) — skipping.",
                hours_since,
                interval_hours,
            )
            return

    logger.info("[License] Running periodic online verification.")
    is_valid = verify_license_online(license_obj)

    if is_valid:
        logger.info("[License] Periodic verification OK — max_agents=%d, tier=%s.",
                    license_obj.max_agents, license_obj.plan_tier)
    else:
        logger.warning("[License] Periodic verification FAILED — license may be suspended.")
