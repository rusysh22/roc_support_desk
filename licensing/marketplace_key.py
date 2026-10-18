"""Build-time root of trust for Marketplace entitlements.

Set this to the base64-encoded, 32-byte Ed25519 *public* key before creating a
production release. It is intentionally public: only the Marketplace holds the
corresponding private key and can issue a valid entitlement.

Left empty, signed-entitlement verification stays off unless
LICENSE_ENTITLEMENT_PUBLIC_KEY / LICENSE_REQUIRE_SIGNED_ENTITLEMENT are set —
only embed a key once the Marketplace actually signs its responses.
"""

MARKETPLACE_ED25519_PUBLIC_KEY_B64 = ""
