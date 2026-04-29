"""HMAC-SHA256 device authentication — ported from V1.

V2 upgrades:
- Nonce store moved to Redis (see redis_service.py) instead of in-memory dict
- Rate limiting moved to Redis (see redis_service.py)
- Device secret verification uses bcrypt (stored in Device.secret_key_hash)
"""

from __future__ import annotations

import hashlib
import hmac
import logging

import bcrypt

logger = logging.getLogger(__name__)


# ── HMAC Payload Signing ──────────────────────────────────────────


def compute_payload_digest(
    image_bytes: bytes, device_id: int, timestamp: str
) -> str:
    """Compute SHA-256 digest of ingest payload components."""
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    return f"{device_id}:{timestamp}:{image_hash}"


def sign_payload(payload: str, nonce: str, secret_key: str) -> str:
    """Sign a payload + nonce with HMAC-SHA256."""
    data = f"{payload}:{nonce}".encode("utf-8")
    return hmac.new(
        secret_key.encode("utf-8"), data, hashlib.sha256
    ).hexdigest()


def verify_signature(
    payload: str, nonce: str, secret_key: str, provided_signature: str
) -> bool:
    """Verify an HMAC-SHA256 signature using constant-time comparison."""
    expected = sign_payload(payload=payload, nonce=nonce, secret_key=secret_key)
    return hmac.compare_digest(expected, provided_signature)


# ── Device Secret Hashing (bcrypt) ────────────────────────────────


def hash_device_secret(raw_secret: str) -> str:
    """Hash a device secret_key with bcrypt for storage."""
    return bcrypt.hashpw(
        raw_secret.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")


def verify_device_secret(raw_secret: str, hashed: str) -> bool:
    """Verify a raw device secret against its bcrypt hash.

    Supports both bcrypt hashes and legacy plaintext (constant-time).
    """
    if hashed.startswith("$2"):
        return bcrypt.checkpw(
            raw_secret.encode("utf-8"), hashed.encode("utf-8")
        )
    # Legacy plaintext comparison (constant-time)
    return hmac.compare_digest(raw_secret, hashed)
