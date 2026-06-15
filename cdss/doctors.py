"""Load the doctor registry and per-doctor credentials from the doctors/ folder.

Shared by the email listener (cdss/notify) and the dashboard login (cdss/dashboard).
Only the registry (slug + name) is ever public; per-doctor files hold secrets and live
git-ignored on the server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCTORS_DIR = ROOT / "doctors"
REGISTRY_PATH = DOCTORS_DIR / "doctors.yaml"
EMAIL_PARAMS_PATH = DOCTORS_DIR / "email_params.yaml"


def canonical_slug(value: str) -> str:
    return str(value or "").strip().lower()


def load_registry() -> dict[str, dict[str, str]]:
    """Return {slug: {"name": ...}} from doctors/doctors.yaml."""
    if not REGISTRY_PATH.exists():
        return {}
    raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or []
    out: dict[str, dict[str, str]] = {}
    for entry in raw:
        slug = canonical_slug(entry.get("slug", ""))
        name = str(entry.get("name", "")).strip()
        if slug and name:
            out[slug] = {"name": name}
    return out


def doctor_name(slug: str) -> str:
    """Display name for a slug, falling back to a title-cased slug."""
    reg = load_registry()
    entry = reg.get(canonical_slug(slug))
    return entry["name"] if entry else canonical_slug(slug).replace("_", " ").title()


def load_credentials(slug: str) -> dict[str, Any] | None:
    """Return the contents of doctors/<slug>.yaml, or None if the doctor has no
    credential file yet (valid state — they just have no email/login configured)."""
    slug = canonical_slug(slug)
    if not slug:
        return None
    path = DOCTORS_DIR / f"{slug}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_email_params() -> dict[str, Any]:
    """Shared SMTP server settings (smtp_server, port)."""
    if not EMAIL_PARAMS_PATH.exists():
        return {}
    return yaml.safe_load(EMAIL_PARAMS_PATH.read_text(encoding="utf-8")) or {}
