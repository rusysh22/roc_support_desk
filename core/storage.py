"""
Core — Cached Azure Storage Backend
=====================================
Wraps django-storages' AzureStorage to cache the generated SAS URL per blob
name instead of minting a brand-new signature on every ``.url()`` call.

Why: the chat thread (both staff admin and the client widget) polls its
partial every 5s via HTMX, which re-renders every attachment's <img>/<audio>
tag. Because AzureStorage.url() signs a fresh SAS token (different "st"/"se"
query params) on each call, the resulting <img src> string changes on every
poll even though the underlying blob hasn't — so the browser can never reuse
its cache and re-downloads every attachment's full bytes every 5 seconds.
Caching the signed URL for most of its validity window makes the src string
stable across polls, so the browser's normal HTTP cache takes over.
"""
from __future__ import annotations

from django.core.cache import cache
from storages.backends.azure_storage import AzureStorage

_CACHE_PREFIX = "azure_sas_url:"
# Stay comfortably under `expiration_secs` (3600) so we never hand out a URL
# that expires before the browser gets to use it.
_CACHE_TTL = 3300


class CachedAzureStorage(AzureStorage):
    """AzureStorage that reuses a blob's signed URL until it's close to expiry."""

    def url(self, name, *args, **kwargs):  # noqa: D102
        cache_key = f"{_CACHE_PREFIX}{name}"
        cached_url = cache.get(cache_key)
        if cached_url:
            return cached_url

        generated_url = super().url(name, *args, **kwargs)
        cache.set(cache_key, generated_url, timeout=_CACHE_TTL)
        return generated_url
