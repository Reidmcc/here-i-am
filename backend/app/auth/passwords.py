"""
The researcher's password: hashed with scrypt from the standard library,
so there is no new dependency to keep patched (issue #396).

A stored hash carries its own parameters,
`scrypt$<n>$<r>$<p>$<salt>$<key>` with the last two in URL-safe base64, so
the cost can be raised later without invalidating what is already stored:
verification uses the parameters the hash was made with, and
`needs_rehash` says when they are behind the current ones.
"""
import base64
import hashlib
import hmac
import secrets
from typing import Optional

# n=2**16, r=8 is 64 MB and roughly a tenth of a second per guess on a
# desktop core. OWASP's floor for scrypt is n=2**17; this sits one step
# under it so a small cloud VM can hash without swapping. The login
# throttle (throttle.py), not this cost, is what bounds online guessing.
SCRYPT_N = 2 ** 16
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LENGTH = 32
SALT_BYTES = 16
# hashlib refuses parameters needing more than its default 32 MB unless
# told the ceiling; also the cap on what a stored hash may ask for, so a
# tampered row can't make verification allocate without bound
MAX_MEMORY_BYTES = 256 * 1024 * 1024

MIN_PASSWORD_LENGTH = 12
# scrypt takes any length; this only keeps a request body from being hashed
# megabytes at a time
MAX_PASSWORD_LENGTH = 1024

PREFIX = "scrypt"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def password_problem(password: str) -> Optional[str]:
    """Why this can't be the password, or None when it can."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        return f"The password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"The password must be at most {MAX_PASSWORD_LENGTH} characters."
    return None


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt, n=n, r=r, p=p,
        dklen=KEY_LENGTH,
        maxmem=MAX_MEMORY_BYTES,
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    key = _derive(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"{PREFIX}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(key)}"


def _parse(stored: str):
    prefix, n, r, p, salt, key = stored.split("$")
    if prefix != PREFIX:
        raise ValueError("not an scrypt hash")
    return int(n), int(r), int(p), _unb64(salt), _unb64(key)


def verify_password(password: str, stored: str) -> bool:
    """Whether `password` is the one `stored` was made from. False, never
    an exception, for anything malformed: a damaged row must fail closed."""
    if not isinstance(password, str) or len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        n, r, p, salt, key = _parse(stored)
        candidate = _derive(password, salt, n, r, p)
    except Exception:
        return False
    return hmac.compare_digest(candidate, key)


def needs_rehash(stored: str) -> bool:
    """Whether a hash was made with weaker parameters than today's."""
    try:
        n, r, p, _salt, _key = _parse(stored)
    except Exception:
        return True
    return (n, r, p) != (SCRYPT_N, SCRYPT_R, SCRYPT_P)
