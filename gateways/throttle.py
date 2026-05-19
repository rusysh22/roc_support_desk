"""
Gateways — WhatsApp Rate Limiter & Business Hours Guard

Two utilities used by all outbound WhatsApp tasks:

1. is_within_business_hours() — returns True if current WIB time is within
   the Mon-Fri 07:00-20:00 window. Calling code defers sends that fall
   outside this window rather than dropping them.

2. WARateLimiter.check_and_record(phone) — checks per-recipient (1/10 s),
   burst (8/min), per-minute (15), per-hour (200), and per-day (1500)
   limits. Returns (allowed, reason). Records the send only when allowed.
   All counters live in the Django cache (Redis).
"""
from __future__ import annotations

import logging
from datetime import datetime

from django.core.cache import cache

logger = logging.getLogger(__name__)

_JAKARTA_TZ_NAME = "Asia/Jakarta"

BUSINESS_HOUR_START = 7    # 07:00 WIB
BUSINESS_HOUR_END = 20     # 20:00 WIB (inclusive start, exclusive end)
BUSINESS_DAYS = frozenset({0, 1, 2, 3, 4})  # Monday–Friday

# --- Per-recipient ---
_RECIPIENT_WINDOW_SECONDS = 10
_RECIPIENT_MAX = 1          # at most 1 msg per 10 s to the same number

# --- Per-instance burst ---
_BURST_WINDOW_SECONDS = 60
_BURST_MAX = 8              # at most 8 msgs in any rolling 60 s window

# --- Per-instance time-box caps ---
_INST_MINUTE_MAX = 15
_INST_HOUR_MAX = 200
_INST_DAY_MAX = 1500


def _get_business_hours_config() -> tuple[int, int, frozenset]:
    """
    Return (hour_start, hour_end, business_days) from SiteConfig DB,
    falling back to module-level defaults if DB is unavailable.
    """
    try:
        from core.models import SiteConfig
        cfg = SiteConfig.get_solo()
        hour_start = cfg.wa_business_hour_start
        hour_end = cfg.wa_business_hour_end
        days = frozenset(
            int(d.strip()) for d in cfg.wa_business_days.split(",") if d.strip().isdigit()
        )
        return hour_start, hour_end, days
    except Exception:
        return BUSINESS_HOUR_START, BUSINESS_HOUR_END, BUSINESS_DAYS


def is_within_business_hours(now: datetime | None = None) -> bool:
    """
    Return True if the given moment (default: now) falls within the
    configured WA business hours window (reads from SiteConfig DB).
    """
    from zoneinfo import ZoneInfo
    from datetime import timezone as dt_timezone

    tz = ZoneInfo(_JAKARTA_TZ_NAME)
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=dt_timezone.utc).astimezone(tz)
    else:
        now = now.astimezone(tz)

    hour_start, hour_end, business_days = _get_business_hours_config()
    return now.weekday() in business_days and hour_start <= now.hour < hour_end


def _cache_incr(key: str, window_seconds: int) -> int:
    """
    Atomically increment a counter in cache, creating it with TTL if absent.
    Returns the new count.
    """
    try:
        return cache.incr(key)
    except ValueError:
        # Key did not exist yet
        cache.set(key, 1, timeout=window_seconds)
        return 1


def _get_rate_config() -> dict:
    """
    Load rate-limit and circuit-breaker config from SiteConfig, cached 60s.
    Falls back to module-level defaults if the DB is unreachable.
    """
    _CACHE_KEY = "wa_rate_cfg"
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        from core.models import SiteConfig
        cfg = SiteConfig.get_solo()
        config = {
            "recipient_cooldown": cfg.wa_rate_recipient_cooldown,
            "burst_max": cfg.wa_rate_burst_max,
            "minute_max": cfg.wa_rate_minute_max,
            "hour_max": cfg.wa_rate_hour_max,
            "cb_max_disconnects": cfg.wa_cb_max_disconnects,
            "cb_block_hours": cfg.wa_cb_block_hours,
        }
    except Exception:
        config = {
            "recipient_cooldown": _RECIPIENT_WINDOW_SECONDS,
            "burst_max": _BURST_MAX,
            "minute_max": _INST_MINUTE_MAX,
            "hour_max": _INST_HOUR_MAX,
            "cb_max_disconnects": _CB_MAX_DISCONNECTS,
            "cb_block_hours": 4,
        }
    cache.set(_CACHE_KEY, config, timeout=60)
    return config


class WARateLimiter:
    """
    Check and record outbound WhatsApp rate limits.

    Usage::

        allowed, reason = WARateLimiter.check_and_record(phone)
        if not allowed:
            logger.warning("Throttled: %s", reason)
            return f"skipped:{reason}"
        # proceed with send
    """

    @classmethod
    def check_and_record(cls, phone: str) -> tuple[bool, str]:
        """
        Check all limits for one outbound send to *phone*.
        Records the send (increments all counters) only when allowed.
        Returns (True, "ok") on success or (False, reason) when throttled.
        """
        from django.utils import timezone

        now = timezone.now()
        ts_day = now.strftime("%Y%m%d")
        ts_hour = now.strftime("%Y%m%d%H")
        ts_min = now.strftime("%Y%m%d%H%M")

        clean = phone.lstrip("+")
        rc = _get_rate_config()

        recipient_key = f"wa_rl:rcpt:{clean}"
        burst_key = f"wa_rl:burst:{ts_min}"
        inst_min_key = f"wa_rl:inst:m:{ts_min}"
        inst_hour_key = f"wa_rl:inst:h:{ts_hour}"
        inst_day_key = f"wa_rl:inst:d:{ts_day}"

        # --- Read-only checks first ---
        if (cache.get(recipient_key) or 0) >= _RECIPIENT_MAX:
            return False, f"rate_limit:recipient:{clean}"
        if (cache.get(burst_key) or 0) >= rc["burst_max"]:
            return False, "rate_limit:burst"
        if (cache.get(inst_min_key) or 0) >= rc["minute_max"]:
            return False, "rate_limit:instance_per_minute"
        if (cache.get(inst_hour_key) or 0) >= rc["hour_max"]:
            return False, "rate_limit:instance_per_hour"
        # Respect warm-up daily cap
        from core.models import SiteConfig
        daily_limit = SiteConfig.get_solo().get_wa_daily_limit()
        if (cache.get(inst_day_key) or 0) >= daily_limit:
            return False, f"rate_limit:instance_per_day(limit={daily_limit})"

        # --- All clear — record ---
        _cache_incr(recipient_key, rc["recipient_cooldown"])
        _cache_incr(burst_key, _BURST_WINDOW_SECONDS)
        _cache_incr(inst_min_key, 60)
        _cache_incr(inst_hour_key, 3600)
        _cache_incr(inst_day_key, 86400)

        return True, "ok"

    @classmethod
    def get_daily_count(cls) -> int:
        """Return today's outbound send count (for monitoring dashboards)."""
        from django.utils import timezone

        ts_day = timezone.now().strftime("%Y%m%d")
        return cache.get(f"wa_rl:inst:d:{ts_day}") or 0


# ---------------------------------------------------------------------------
# Circuit Breaker
# Trips when the WA instance disconnects more than _CB_MAX_DISCONNECTS times
# within a 24-hour window. All outbound sends are blocked until the instance
# reconnects and the circuit is reset manually or by the health-check task.
# ---------------------------------------------------------------------------

_CB_OPEN_KEY = "wa_cb:open"           # truthy = circuit is open (blocked)
_CB_DISCONNECT_KEY = "wa_cb:disconnects:{date}"
_CB_MAX_DISCONNECTS = 2               # 2 disconnects within 24 h trips the circuit


def is_circuit_open() -> bool:
    """Return True if the circuit breaker is tripped (all outbound blocked)."""
    return bool(cache.get(_CB_OPEN_KEY))


def record_disconnect() -> int:
    """
    Increment the disconnect counter for today. If the threshold is crossed,
    trip the circuit breaker and return the new count.
    """
    from django.utils import timezone

    rc = _get_rate_config()
    date_key = _CB_DISCONNECT_KEY.format(date=timezone.now().strftime("%Y%m%d"))
    count = _cache_incr(date_key, window_seconds=86400)
    if count >= rc["cb_max_disconnects"] and not cache.get(_CB_OPEN_KEY):
        block_seconds = rc["cb_block_hours"] * 3600
        cache.set(_CB_OPEN_KEY, 1, timeout=block_seconds)
        logger.warning(
            "WA circuit breaker TRIPPED after %d disconnects today. "
            "All outbound WA sends blocked for %d hour(s).",
            count,
            rc["cb_block_hours"],
        )
    return count


def reset_circuit() -> None:
    """
    Reset the circuit breaker (called after instance successfully reconnects).
    Does NOT reset the disconnect counter — that expires naturally at midnight.
    """
    cache.delete(_CB_OPEN_KEY)
    logger.info("WA circuit breaker RESET — outbound sends re-enabled.")


def get_circuit_status() -> dict:
    """Return circuit state dict suitable for dashboard rendering."""
    from django.utils import timezone

    date_key = _CB_DISCONNECT_KEY.format(date=timezone.now().strftime("%Y%m%d"))
    rc = _get_rate_config()
    return {
        "open": bool(cache.get(_CB_OPEN_KEY)),
        "disconnect_count_today": cache.get(date_key) or 0,
        "max_disconnects": rc["cb_max_disconnects"],
        "block_hours": rc["cb_block_hours"],
    }
