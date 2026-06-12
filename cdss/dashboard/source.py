from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KEY_PATH = ROOT / "serviceAccountKey.json"
PATIENT_CASES = ROOT / "examples" / "patient_cases"


class SubmissionSource(Protocol):
    """A read/update interface over patient submissions, so the dashboard works
    against either live Firestore or local sample data."""

    def fetch(self, include_seen: bool = False) -> list[dict[str, Any]]: ...
    def mark_seen(self, submission_id: str) -> None: ...
    def cleanup(self) -> int: ...


class SampleSource:
    """Local sample submissions built from examples/patient_cases/*.json.

    Lets the dashboard, ranking, and UI run with no Firebase connection. Marking
    seen / cleanup operate on in-memory state only."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def fetch(self, include_seen: bool = False) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for case_file in sorted(PATIENT_CASES.glob("*.json")):
            case = json.loads(case_file.read_text(encoding="utf-8"))
            sub_id = str(case.get("id") or case_file.stem)
            status = "seen" if sub_id in self._seen else "waiting"
            if status == "seen" and not include_seen:
                continue
            out.append({
                "id": sub_id,
                "uhid": f"DEMO-{sub_id.upper()}",
                "kg_version": str(case.get("kg_version") or "v1"),
                "answers": dict(case.get("answers") or {}),
                "submitted_at": "2026-06-12T08:15:00",
                "status": status,
            })
        return out

    def mark_seen(self, submission_id: str) -> None:
        self._seen.add(submission_id)

    def cleanup(self) -> int:
        count = len(self._seen)
        self._seen.clear()
        return count


def get_source() -> SubmissionSource:
    """Pick the data source. CDSS_SOURCE=sample|firestore overrides; otherwise use
    Firestore when serviceAccountKey.json is present, else fall back to samples."""
    choice = os.environ.get("CDSS_SOURCE", "").strip().lower()
    if choice == "sample":
        return SampleSource()
    if choice == "firestore" or DEFAULT_KEY_PATH.exists():
        from cdss.dashboard.firestore_client import FirestoreSubmissions
        return FirestoreSubmissions(DEFAULT_KEY_PATH)
    return SampleSource()
