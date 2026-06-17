"""On-prem persistence for completed consultations (the doctor's note + which
suggestions were accepted/ignored).

Stored in a local SQLite file on the intranet server — NEVER in Firestore — so the
doctor's prescription data (more sensitive PHI than the intake answers) stays on the
hospital network. The schema is deliberately transactional: one `consultation` row
plus many `consultation_item` rows, so a future Apriori / FP-Growth job can read each
consultation as a single "basket" (symptom features + diagnoses + tests + meds, with
an accepted/ignored flag on every suggested item). No mining is done here — capture only.

stdlib `sqlite3` only (no new dependency), matching the project's stdlib-only constraint.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cdss import doctors

DB_PATH = doctors.DOCTORS_DIR / "consultations.db"

_SUGGESTION_KINDS = {"diagnoses": "diagnosis", "tests": "test", "medications": "medication"}
_NOTE_ITEM_FIELDS = {
    "provisional_diagnosis": "diagnosis",
    "tests": "test",
    "prescribed_medications": "medication",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS consultation (
    id              TEXT PRIMARY KEY,
    submission_id   TEXT NOT NULL,
    doctor_slug     TEXT NOT NULL,
    uhid            TEXT,
    patient_age     TEXT,
    patient_sex     TEXT,
    kg_version      TEXT,
    chief_complaint TEXT,
    final_diagnosis TEXT,
    note_json       TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consultation_item (
    consultation_id TEXT NOT NULL REFERENCES consultation(id),
    kind            TEXT NOT NULL,   -- symptom | diagnosis | test | medication
    value           TEXT NOT NULL,
    source          TEXT NOT NULL,   -- patient | suggested | doctor
    accepted        INTEGER          -- 1 accepted, 0 ignored, NULL n/a
);
CREATE INDEX IF NOT EXISTS idx_item_consultation ON consultation_item(consultation_id);
"""


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str | Path = DB_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        conn.executescript(_SCHEMA)


def save_consultation(record: dict[str, Any], path: str | Path = DB_PATH) -> str:
    """Persist one consultation. Returns the new consultation id.

    `record` carries: submission_id, doctor_slug, uhid, kg_version, chief_complaint,
    note (the left-panel sections), suggestions ({group: {offered, accepted}}), and
    optionally symptom_features (patient-reported findings).
    """
    init_db(path)
    consultation_id = uuid.uuid4().hex
    note = dict(record.get("note") or {})
    created_at = datetime.now(timezone.utc).isoformat()
    final_diagnosis = ", ".join(_as_list(note.get("provisional_diagnosis")))

    items = list(_build_items(record, note))

    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO consultation (id, submission_id, doctor_slug, uhid, patient_age, "
            "patient_sex, kg_version, chief_complaint, final_diagnosis, note_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                consultation_id,
                str(record.get("submission_id") or ""),
                str(record.get("doctor_slug") or ""),
                record.get("uhid"),
                record.get("patient_age"),
                record.get("patient_sex"),
                record.get("kg_version"),
                record.get("chief_complaint"),
                final_diagnosis,
                json.dumps(note, ensure_ascii=False),
                created_at,
            ),
        )
        conn.executemany(
            "INSERT INTO consultation_item (consultation_id, kind, value, source, accepted) "
            "VALUES (?, ?, ?, ?, ?)",
            [(consultation_id, *item) for item in items],
        )
    return consultation_id


def load_consultation(consultation_id: str, path: str | Path = DB_PATH) -> dict[str, Any] | None:
    if not Path(path).exists():
        return None
    with _connect(path) as conn:
        row = conn.execute("SELECT * FROM consultation WHERE id = ?", (consultation_id,)).fetchone()
        if row is None:
            return None
        items = conn.execute(
            "SELECT kind, value, source, accepted FROM consultation_item WHERE consultation_id = ?",
            (consultation_id,),
        ).fetchall()
    data = dict(row)
    data["note"] = json.loads(data.pop("note_json") or "{}")
    data["items"] = [dict(item) for item in items]
    return data


def list_consultations(doctor_slug: str | None = None, path: str | Path = DB_PATH) -> list[dict[str, Any]]:
    if not Path(path).exists():
        return []
    query = "SELECT id, submission_id, doctor_slug, uhid, chief_complaint, final_diagnosis, created_at FROM consultation"
    params: tuple[Any, ...] = ()
    if doctor_slug:
        query += " WHERE doctor_slug = ?"
        params = (doctor_slug.strip().lower(),)
    query += " ORDER BY created_at"
    with _connect(path) as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def _build_items(record: dict[str, Any], note: dict[str, Any]):
    """Yield (kind, value, source, accepted) tuples — one per discrete item, the
    shape association-rule mining consumes."""
    # Patient-reported symptom features (no accept/ignore semantics).
    for value in _as_list(record.get("symptom_features")):
        yield ("symptom", value, "patient", None)

    # What the doctor actually recorded in the note.
    for field, kind in _NOTE_ITEM_FIELDS.items():
        for value in _as_list(note.get(field)):
            yield (kind, value, "doctor", None)

    # Symptoms / history the doctor added during the visit. Captured as discrete
    # mineable items (the seed for a future association-rule loop that proposes new
    # questions for findings doctors frequently add). No accept/ignore semantics.
    for value in _as_list(note.get("additional_findings")):
        yield ("symptom", value, "doctor", None)

    # Suggested pills: accepted vs ignored is the supervised signal.
    suggestions = record.get("suggestions") or {}
    for group, kind in _SUGGESTION_KINDS.items():
        block = suggestions.get(group) or {}
        accepted = {str(v) for v in _as_list(block.get("accepted"))}
        for value in _as_list(block.get("offered")):
            yield (kind, value, "suggested", 1 if str(value) in accepted else 0)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]
