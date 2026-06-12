from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from cdss.knowledge.validator import VALIDATION_PROFILES
from cdss.api.registry import PipelineRegistry, UnknownVersionError

DISCLAIMER = (
    "Decision support only. Output standardizes history-taking and generates "
    "diagnostic suggestions for physician review; it does not replace clinical "
    "judgement."
)

API_DESCRIPTION = (
    "REST interface to the CDSS rule engine.\n\n"
    "MVP scope: `/run` is the **batch** path — the client submits a complete set "
    "of answers and receives ranked diagnoses, red flags, and recommendations in "
    "one call. The stateful **dynamic questionnaire** (interactive "
    "question-by-question navigation via the flow engine) is deferred to a future "
    "`/questionnaire` endpoint set."
)

registry = PipelineRegistry()
app = FastAPI(title="CDSS API", version="0.1.0", description=API_DESCRIPTION)


class RunRequest(BaseModel):
    kg_version: str = Field(..., description="Knowledge graph version, e.g. 'v1'.")
    answers: dict[str, Any] = Field(
        default_factory=dict,
        description="Complete map of question_id -> answer for the batch run.",
    )


class ValidateRequest(BaseModel):
    kg_version: str = Field(..., description="Knowledge graph version to validate.")
    profile: str = Field(
        "prototype",
        description="Validation strictness profile.",
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/versions")
def versions() -> dict[str, list[str]]:
    return {"versions": registry.versions()}


@app.post("/run")
def run(request: RunRequest) -> dict[str, Any]:
    pipeline = _pipeline_or_404(request.kg_version)
    result = pipeline.run(request.answers)
    return {**result.as_dict(), "disclaimer": DISCLAIMER}


@app.post("/validate")
def validate_kg(request: ValidateRequest) -> dict[str, Any]:
    if request.profile not in VALIDATION_PROFILES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown validation profile '{request.profile}'. "
                f"Expected one of: {sorted(VALIDATION_PROFILES)}"
            ),
        )
    if not registry.has_version(request.kg_version):
        raise _version_not_found(request.kg_version)
    report = registry.validation_report(request.kg_version, request.profile)
    return {
        "version": request.kg_version,
        "profile": request.profile,
        **report.as_dict(),
    }


def _pipeline_or_404(version: str):
    try:
        return registry.pipeline(version)
    except UnknownVersionError as exc:
        raise _version_not_found(version, exc.available) from exc


def _version_not_found(version: str, available: list[str] | None = None) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "message": f"Unknown knowledge graph version '{version}'",
            "available_versions": available if available is not None else registry.versions(),
        },
    )
