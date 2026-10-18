"""Verification for Marketplace-signed Ed25519 entitlements."""
import base64
import binascii
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class EntitlementSignatureError(ValueError):
    """The Marketplace response cannot be trusted as a license entitlement."""


def canonical_entitlement_payload(entitlement: dict) -> bytes:
    """Return the exact deterministic JSON bytes signed by the Marketplace."""
    return json.dumps(
        entitlement,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    ).encode('utf-8')


def _decode_base64(value: str, field_name: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise EntitlementSignatureError(f'{field_name} is missing.')
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EntitlementSignatureError(f'{field_name} is not valid base64.') from exc


def verify_entitlement_signature(entitlement: dict, signature_b64: str, public_key_b64: str) -> dict:
    """Verify an Ed25519 signature and return the trusted entitlement payload."""
    if not isinstance(entitlement, dict):
        raise EntitlementSignatureError('entitlement must be a JSON object.')
    public_key_bytes = _decode_base64(public_key_b64, 'Marketplace public key')
    signature = _decode_base64(signature_b64, 'entitlement_signature')
    try:
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(signature, canonical_entitlement_payload(entitlement))
    except (ValueError, InvalidSignature) as exc:
        raise EntitlementSignatureError('entitlement signature is invalid.') from exc
    return entitlement


def verify_entitlement_binding(entitlement: dict, *, fingerprint: str, product_id: str) -> None:
    """Ensure a valid signature was issued for this product and installation."""
    required = ('license_id', 'fingerprint', 'product_id', 'status', 'issued_at', 'entitlements')
    missing = [key for key in required if key not in entitlement]
    if missing:
        raise EntitlementSignatureError(f'entitlement is missing required fields: {", ".join(missing)}.')
    if entitlement['fingerprint'] != fingerprint:
        raise EntitlementSignatureError('entitlement fingerprint does not match this installation.')
    if entitlement['product_id'] != product_id:
        raise EntitlementSignatureError('entitlement product_id does not match this product.')
