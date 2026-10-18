# Signed Marketplace entitlements

Once a production public key is embedded in `licensing/marketplace_key.py`,
`/v1/activate` and `/v1/validate` must return this envelope:

```json
{
  "status": "active",
  "token": "opaque-activation-token",
  "entitlement": {
    "license_id": "lic_123",
    "fingerprint": "sha256-installation-fingerprint",
    "product_id": "roc-support-desk",
    "status": "active",
    "issued_at": "2026-07-13T00:00:00Z",
    "license_expires_at": "2027-07-13T00:00:00Z",
    "token_expires_at": "2026-07-20T00:00:00Z",
    "entitlements": {"MAX_AGENTS": 10, "WHATSAPP": true}
  },
  "entitlement_signature": "base64-ed25519-signature"
}
```

The Marketplace signs the UTF-8 bytes of `entitlement` serialized with JSON
keys sorted, no whitespace, and commas/colons as the only separators. The
private Ed25519 key must remain exclusively on the Marketplace. The product
contains only the base64 raw 32-byte public key.

`license_expires_at` is the commercial subscription/license date shown as
**Valid Until**. `token_expires_at` only controls when the product should
refresh its heartbeat token; it must never be displayed or enforced as the
subscription end date.

The client rejects an invalid signature, a mismatched product/fingerprint, or
a mismatch between the response status and the signed status. On heartbeat,
such a response suspends the local installation rather than extending access.

For production, set `MARKETPLACE_ED25519_PUBLIC_KEY_B64` in
`licensing/marketplace_key.py` before building the release. The environment
variable is intended only for development and key-rotation staging.
