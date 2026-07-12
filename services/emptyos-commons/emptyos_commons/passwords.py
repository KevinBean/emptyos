"""In-service password hashing — stdlib only (pbkdf2_hmac), no external deps.

The simple trusted-team auth path. Kept deliberately small + standard so it can
coexist with (and later yield to) a real IdP: switching to Firebase/OIDC just
flips COMMONS_AUTH_PROVIDER — these hashes simply go unused, sessions/content are
unaffected because both paths produce the same VerifiedIdentity.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 200_000


def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    """Return a self-describing `pbkdf2_sha256$iters$salt$hash` string."""
    if not password:
        raise ValueError("empty password")
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(dk).decode("ascii")
    return f"{_ALGO}${iterations}${salt_b64}${hash_b64}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of *password* against a stored hash. Never raises."""
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False
