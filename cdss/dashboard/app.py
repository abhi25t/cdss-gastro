from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from cdss.api.registry import PipelineRegistry
from cdss.dashboard.source import get_source
from cdss.dashboard.triage import triage

STATIC_DIR = Path(__file__).resolve().parent / "static"

registry = PipelineRegistry()
source = get_source()

app = FastAPI(title="CDSS Triage Dashboard", version="0.1.0")


@app.get("/api/triage")
def api_triage(include_seen: bool = False) -> dict[str, Any]:
    submissions = source.fetch(include_seen=include_seen)
    ranked = triage(submissions, registry)
    return {
        "count": len(ranked),
        "source": type(source).__name__,
        "patients": ranked,
    }


@app.post("/api/seen/{submission_id}")
def api_mark_seen(submission_id: str) -> dict[str, str]:
    try:
        source.mark_seen(submission_id)
    except Exception as exc:  # noqa: BLE001 — surface backend errors as 400
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "id": submission_id}


@app.post("/api/cleanup")
def api_cleanup() -> dict[str, Any]:
    deleted = source.cleanup()
    return {"status": "ok", "deleted": deleted}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "dashboard.html")


# Static assets (dashboard.html/.js/.css). Mounted last so /api/* and / win.
app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
