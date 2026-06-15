from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from cdss import doctors
from cdss.api.registry import PipelineRegistry
from cdss.dashboard import auth
from cdss.dashboard.source import get_source
from cdss.dashboard.triage import triage

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


@app.get("/", response_model=None)
def index(request: Request) -> FileResponse | RedirectResponse:
    if not auth.read_token(request.cookies.get(COOKIE_NAME)):
        return RedirectResponse("/login")
    return FileResponse(STATIC_DIR / "dashboard.html")


# Static assets (dashboard.html/.js/.css, login.html). Mounted last so routes win.
app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
