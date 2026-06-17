from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cdss import doctors
from cdss.api.registry import PipelineRegistry
from cdss.dashboard import auth, consultations
from cdss.dashboard.source import get_source
from cdss.dashboard.triage import assess_detail, triage

STATIC_DIR = Path(__file__).resolve().parent / "static"
COOKIE_NAME = "cdss_session"

registry = PipelineRegistry()
source = get_source()

app = FastAPI(title="CDSS Triage Dashboard", version="0.2.0")


def require_doctor(request: Request) -> str:
    """Auth dependency: returns the logged-in doctor's slug, or 401."""
    slug = auth.read_token(request.cookies.get(COOKIE_NAME))
    if not slug:
        raise HTTPException(status_code=401, detail="Not logged in")
    return slug


def _owned_submission(submission_id: str, doctor: str) -> dict[str, Any]:
    """Fetch a submission and enforce ownership. Returns a flat 404 for both
    missing and not-owned so a doctor can't enumerate other doctors' patients."""
    sub = source.fetch_one(submission_id)
    if not sub or str(sub.get("doctor_slug", "")).strip().lower() != doctor:
        raise HTTPException(status_code=404, detail="Submission not found")
    return sub


class _SuggestionGroup(BaseModel):
    offered: list[str] = Field(default_factory=list)
    accepted: list[str] = Field(default_factory=list)


class _Suggestions(BaseModel):
    diagnoses: _SuggestionGroup = Field(default_factory=_SuggestionGroup)
    tests: _SuggestionGroup = Field(default_factory=_SuggestionGroup)
    medications: _SuggestionGroup = Field(default_factory=_SuggestionGroup)


class _Note(BaseModel):
    chief_complaint: str = ""
    history_present_illness: str = ""
    past_history: str = ""
    current_medications: str = ""
    allergies: str = ""
    family_history: str = ""
    findings: str = ""
    provisional_diagnosis: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    prescribed_medications: list[str] = Field(default_factory=list)
    advice_followup: str = ""
    # Symptoms/history the doctor adds during the visit — captured as discrete items
    # for the future association-rule loop (consultations.py turns these into rows).
    additional_findings: list[str] = Field(default_factory=list)


class _ConsultationBody(BaseModel):
    note: _Note
    suggestions: _Suggestions = Field(default_factory=_Suggestions)


# ---- Auth routes -----------------------------------------------------------
@app.get("/login")
def login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/api/doctors")
def api_doctors() -> dict[str, dict[str, str]]:
    """Public: slug -> {name}, for the login dropdown. No secrets."""
    return doctors.load_registry()


@app.post("/login")
def login_submit(slug: str = Form(...), password: str = Form(...)) -> RedirectResponse:
    if auth.authenticate(slug, password):
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE_NAME, auth.make_token(slug), httponly=True, samesite="lax")
        return resp
    return RedirectResponse("/login?error=1", status_code=303)


@app.get("/logout")
def logout() -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ---- API (scoped to the logged-in doctor) ----------------------------------
@app.get("/api/triage")
def api_triage(include_seen: bool = False, doctor: str = Depends(require_doctor)) -> dict[str, Any]:
    submissions = source.fetch(include_seen=include_seen, doctor_slug=doctor)
    ranked = triage(submissions, registry)
    return {
        "count": len(ranked),
        "source": type(source).__name__,
        "doctor": doctor,
        "doctor_name": doctors.doctor_name(doctor),
        "patients": ranked,
    }


@app.post("/api/seen/{submission_id}")
def api_mark_seen(submission_id: str, doctor: str = Depends(require_doctor)) -> dict[str, str]:
    try:
        source.mark_seen(submission_id)
    except Exception as exc:  # noqa: BLE001 — surface backend errors as 400
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "id": submission_id}


@app.post("/api/cleanup")
def api_cleanup(doctor: str = Depends(require_doctor)) -> dict[str, Any]:
    deleted = source.cleanup(doctor_slug=doctor)
    return {"status": "ok", "deleted": deleted}


# ---- Per-patient consultation page ----------------------------------------
@app.get("/patient/{submission_id}", response_model=None)
def patient_page(submission_id: str, request: Request) -> FileResponse | RedirectResponse:
    slug = auth.read_token(request.cookies.get(COOKIE_NAME))
    if not slug:
        return RedirectResponse("/login")
    _owned_submission(submission_id, slug)  # 404 if not this doctor's patient
    return FileResponse(STATIC_DIR / "patient.html")


@app.get("/api/patient/{submission_id}")
def api_patient(submission_id: str, doctor: str = Depends(require_doctor)) -> dict[str, Any]:
    sub = _owned_submission(submission_id, doctor)
    return assess_detail(sub, registry)


@app.post("/api/patient/{submission_id}/consultation")
def api_save_consultation(
    submission_id: str, body: _ConsultationBody, doctor: str = Depends(require_doctor)
) -> dict[str, str]:
    sub = _owned_submission(submission_id, doctor)
    detail = assess_detail(sub, registry)
    record = {
        "submission_id": submission_id,
        "doctor_slug": doctor,
        "uhid": sub.get("uhid"),
        "patient_age": sub.get("patient_age"),
        "patient_sex": sub.get("patient_sex"),
        "kg_version": sub.get("kg_version"),
        "chief_complaint": body.note.chief_complaint or detail.get("chief_complaint"),
        "note": body.note.model_dump(),
        "suggestions": body.suggestions.model_dump(),
        "symptom_features": detail.get("true_conditions", []),
    }
    consultation_id = consultations.save_consultation(record, path=consultations.DB_PATH)
    # Saving a note implies the patient has been seen; remove from the queue.
    try:
        source.mark_seen(submission_id)
    except Exception:  # noqa: BLE001 — never fail the save on a mark-seen hiccup
        pass
    return {"status": "ok", "consultation_id": consultation_id}


@app.get("/", response_model=None)
def index(request: Request) -> FileResponse | RedirectResponse:
    if not auth.read_token(request.cookies.get(COOKIE_NAME)):
        return RedirectResponse("/login")
    return FileResponse(STATIC_DIR / "dashboard.html")


# Static assets (dashboard.html/.js/.css, login.html). Mounted last so routes win.
app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
