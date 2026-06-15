"""Dashboard login — password hashing + signed session cookies.

Doctors share one intranet dashboard, so each logs in and sees only their own
patients. No new dependencies: passwords are PBKDF2-hashed (stdlib hashlib) and stored
as `dashboard_password_hash` in doctors/<slug>.yaml; the session cookie is an HMAC-signed
token. The threat model is "separate doctors on a trusted intranet", not public exposure.

Set a doctor's password:
    python -m cdss.dashboard.auth set-password <slug>
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sys
from getpass import getpass

import yaml

from cdss import doctors

PBKDF2_ITERATIONS = 200_000
_ALGO = "pbkdf2_sha256"
SECRET_PATH = doctors.DOCTORS_DIR / ".session_secret"


# ---- Password hashing ------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"{_ALGO}${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = str(stored).split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ---- Session tokens (HMAC-signed, stateless) -------------------------------
def _secret() -> bytes:
    env = os.environ.get("CDSS_SESSION_SECRET")
    if env:
        return env.encode()
    if SECRET_PATH.exists():
        return SECRET_PATH.read_bytes()
    secret = secrets.token_bytes(32)
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRET_PATH.write_bytes(secret)
    return secret


def make_token(slug: str) -> str:
    slug = doctors.canonical_slug(slug)
    sig = hmac.new(_secret(), slug.encode(), hashlib.sha256).hexdigest()
    return f"{slug}.{sig}"


def read_token(token: str | None) -> str | None:
    """Return the slug from a valid token, or None if missing/tampered."""
    if not token or "." not in token:
        return None
    slug, _, sig = token.rpartition(".")
    expected = hmac.new(_secret(), slug.encode(), hashlib.sha256).hexdigest()
    return slug if hmac.compare_digest(sig, expected) else None


def authenticate(slug: str, password: str) -> bool:
    creds = doctors.load_credentials(slug)
    stored = (creds or {}).get("dashboard_password_hash")
    return bool(stored) and verify_password(password, stored)


# ---- CLI: set-password -----------------------------------------------------
def _set_password(slug: str) -> int:
    slug = doctors.canonical_slug(slug)
    if not slug:
        print("Usage: python -m cdss.dashboard.auth set-password <slug>")
        return 2
    if slug not in doctors.load_registry():
        print(f"Warning: '{slug}' is not in doctors/doctors.yaml — add it there too.")
    path = doctors.DOCTORS_DIR / f"{slug}.yaml"
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    pw = getpass(f"New dashboard password for {slug}: ")
    if not pw:
        print("Empty password — aborted.")
        return 1
    if pw != getpass("Confirm: "):
        print("Passwords do not match — aborted.")
        return 1

    data["dashboard_password_hash"] = hash_password(pw)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"Set dashboard password for {slug} -> {path}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "set-password":
        return _set_password(argv[1])
    print("Usage: python -m cdss.dashboard.auth set-password <slug>")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
