from __future__ import annotations

from pathlib import Path
from typing import Any

# firebase-admin is imported lazily inside __init__ so the rest of the dashboard
# (and the test suite) works without it installed or without a service key.


class FirestoreSubmissions:
    """Reads patient submissions from Firestore using a service-account key.

    The Admin SDK bypasses Firestore security rules, so this runs only on the
    doctor's machine where serviceAccountKey.json lives. Patients (browser) can
    only create documents; only this server side can read/update/delete them."""

    COLLECTION = "submissions"

    def __init__(self, key_path: str | Path) -> None:
        import firebase_admin
        from firebase_admin import credentials, firestore

        key_path = Path(key_path)
        if not key_path.exists():
            raise FileNotFoundError(
                f"Service account key not found at {key_path}. See webapp/SETUP_FIREBASE.md Part D."
            )
        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(str(key_path)))
        self._db = firestore.client()

    def _collection(self):
        return self._db.collection(self.COLLECTION)

    def fetch(self, include_seen: bool = False) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for doc in self._collection().stream():
            data = doc.to_dict() or {}
            data["id"] = doc.id
            if data.get("status") == "seen" and not include_seen:
                continue
            data["created_at"] = _to_iso(data.get("created_at"))
            out.append(data)
        return out

    def mark_seen(self, submission_id: str) -> None:
        self._collection().document(submission_id).update({"status": "seen"})

    def cleanup(self) -> int:
        """Delete submissions already marked seen (end-of-day data minimization)."""
        deleted = 0
        for doc in self._collection().where("status", "==", "seen").stream():
            doc.reference.delete()
            deleted += 1
        return deleted


def _to_iso(value: Any) -> Any:
    """Firestore server timestamps come back as datetime objects; make them
    JSON-serializable."""
    if value is None or isinstance(value, str):
        return value
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else str(value)
