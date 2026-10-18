import logging

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)

TRANSIENT_FAILURE_REASONS = {
    "marketplace_unreachable",
    "marketplace_invalid_json",
    "marketplace_invalid_payload",
}
RETRY_DELAYS_SECONDS = (5 * 60, 15 * 60, 60 * 60, 6 * 60 * 60)


def _is_retryable_failure(since) -> bool:
    """Return true only for a temporary Marketplace failure."""
    from .models import LicenseAuditLog

    entry = LicenseAuditLog.objects.filter(
        event="verification_failed", created_at__gte=since
    ).first()
    if not entry:
        return False
    payload = entry.payload or {}
    if payload.get("reason") in TRANSIENT_FAILURE_REASONS:
        return True
    if payload.get("status") == "rate_limited":
        return True
    if payload.get("reason") == "marketplace_http_error":
        return int(payload.get("status_code", 0)) >= 500
    return False


@shared_task(name="licensing.verify_license_periodic", bind=True, max_retries=4)
def verify_license_periodic(self, force=False, trigger="periodic"):
    """Verify the installed license and retry only transient failures.

    Normal periodic executions are skipped within VERIFY_INTERVAL_HOURS. Manual
    checks and scheduled backoff retries pass ``force=True``.
    """
    from django.utils import timezone

    from .models import LicenseRecord
    from .validators import verify_license_online

    cfg = getattr(settings, "LICENSE_SETTINGS", {})
    interval_hours = cfg.get("VERIFY_INTERVAL_HOURS", 24)
    license_obj = LicenseRecord.get_current()

    if not license_obj.license_key:
        logger.info("[License] No license key installed; skipping verification.")
        return False

    if license_obj.last_verified_at and not force:
        hours_since = (timezone.now() - license_obj.last_verified_at).total_seconds() / 3600
        if hours_since < interval_hours:
            logger.info(
                "[License] Verified %.1fh ago (interval=%dh); skipping.",
                hours_since,
                interval_hours,
            )
            return True

    started_at = timezone.now()
    logger.info("[License] Running %s online verification.", trigger)
    is_valid = verify_license_online(license_obj)
    if is_valid:
        logger.info(
            "[License] Verification OK; max_agents=%d, tier=%s.",
            license_obj.max_agents,
            license_obj.plan_tier,
        )
        return True

    logger.warning("[License] Verification failed; license may be suspended.")
    if _is_retryable_failure(started_at) and self.request.retries < len(RETRY_DELAYS_SECONDS):
        delay = RETRY_DELAYS_SECONDS[self.request.retries]
        logger.info(
            "[License] Temporary Marketplace failure; retrying in %d seconds (attempt %d/%d).",
            delay,
            self.request.retries + 1,
            len(RETRY_DELAYS_SECONDS),
        )
        raise self.retry(
            kwargs={"force": True, "trigger": "retry"},
            countdown=delay,
        )
    return False
