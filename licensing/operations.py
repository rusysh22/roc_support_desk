"""Client for Marketplace-issued, short-lived premium-operation grants.

This module is product-agnostic: callers provide an operation name such as
``api_access`` or ``whatsapp_relay``. The Marketplace resolves the actual
product, plan, and entitlement policy from the installed license.
"""
import json

import requests
from django.conf import settings
from django.core import signing
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .entitlements import EntitlementSignatureError, verify_entitlement_signature
from .models import LicenseRecord
from .validators import _activation_headers, _marketplace_url, generate_fingerprint


class OperationAuthorizationError(ValueError):
    """Marketplace did not issue a valid operation authorization."""


def operation_request_hash(payload: dict) -> str:
    """Return a deterministic hash that binds an authorization to a request."""
    import hashlib

    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def request_operation_authorization(operation: str, *, request_hash: str = "") -> dict:
    """Request and verify a short-lived, Marketplace-signed operation grant.

    The grant is fail-closed: an unsigned response, a binding mismatch, an
    expired grant, or a Marketplace rejection raises OperationAuthorizationError.
    """
    record = LicenseRecord.get_current()
    if not record.license_key or not record.activation_token:
        raise OperationAuthorizationError("The installed license is not activated.")

    try:
        raw_key = signing.loads(record.license_key, salt="roc-license-v1")
    except signing.BadSignature as exc:
        raise OperationAuthorizationError("The stored license key is invalid.") from exc

    fingerprint = generate_fingerprint()
    try:
        response = requests.post(
            f"{_marketplace_url()}/v1/operation-authorize",
            json={
                "license_key": raw_key,
                "fingerprint": fingerprint,
                "token": record.activation_token,
                "operation": operation,
                "request_hash": request_hash,
            },
            headers=_activation_headers(),
            timeout=10,
        )
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise OperationAuthorizationError("Marketplace operation authorization is unavailable.") from exc

    if response.status_code != 200 or not isinstance(data, dict):
        raise OperationAuthorizationError("Marketplace returned an invalid operation authorization response.")
    if data.get("status") != "authorized":
        raise OperationAuthorizationError(data.get("message") or "Marketplace denied this operation.")

    cfg = getattr(settings, "LICENSE_SETTINGS", {})
    try:
        grant = verify_entitlement_signature(
            data.get("authorization"),
            data.get("authorization_signature", ""),
            cfg.get("ENTITLEMENT_PUBLIC_KEY", ""),
        )
    except EntitlementSignatureError as exc:
        raise OperationAuthorizationError("Marketplace operation authorization signature is invalid.") from exc

    expected_product = cfg.get("PRODUCT_ID", "roc-support-desk")
    if grant.get("license_id") != raw_key:
        raise OperationAuthorizationError("Operation authorization belongs to a different license.")
    if grant.get("fingerprint") != fingerprint or grant.get("product_id") != expected_product:
        raise OperationAuthorizationError("Operation authorization is not bound to this installation.")
    if grant.get("operation") != operation or grant.get("request_hash", "") != request_hash:
        raise OperationAuthorizationError("Operation authorization does not match this request.")
    expires_at = parse_datetime(grant.get("expires_at", ""))
    if not expires_at or expires_at <= timezone.now():
        raise OperationAuthorizationError("Operation authorization has expired.")
    return grant
