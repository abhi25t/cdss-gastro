from __future__ import annotations

from typing import Any

from cdss.api.registry import PipelineRegistry, UnknownVersionError

# How urgent each red-flag level is. Higher = called first. Unknown urgencies
# fall in the middle so a misconfigured flag is never silently ignored.
URGENCY_RANK = {"immediate": 4, "urgent": 3, "soon": 2, "routine": 1}
UNKNOWN_URGENCY_RANK = 2


def urgency_rank(urgency: str) -> int:
    return URGENCY_RANK.get(str(urgency).strip().lower(), UNKNOWN_URGENCY_RANK)


def triage(submissions: list[dict[str, Any]], registry: PipelineRegistry) -> list[dict[str, Any]]:
    """Run each submission through the CDSS pipeline and return them ranked by
    risk: highest red-flag urgency first, then top diagnosis score."""
    rows = [_assess(sub, registry) for sub in submissions]
    rows.sort(key=lambda r: (r["max_urgency_rank"], r["top_score"]), reverse=True)
    for position, row in enumerate(rows, start=1):
        row["position"] = position
    return rows


def _assess(sub: dict[str, Any], registry: PipelineRegistry) -> dict[str, Any]:
    version = str(sub.get("kg_version") or "v1")
    base = {
        "id": sub.get("id"),
        "uhid": sub.get("uhid"),
        "status": sub.get("status", "waiting"),
        "submitted_at": sub.get("submitted_at"),
        "kg_version": version,
    }

    try:
        result = registry.pipeline(version).run(dict(sub.get("answers") or {})).as_dict()
    except UnknownVersionError:
        return {
            **base,
            "error": f"Unknown knowledge graph version '{version}'",
            "red_flags": [], "max_urgency_rank": 0,
            "diagnoses": [], "top_diagnosis": None, "top_score": 0,
            "evidence": [], "risk_tier": "Unknown",
        }

    red_flags = result["red_flags"]
    max_rank = max((urgency_rank(f["urgency"]) for f in red_flags), default=0)
    diagnoses = result["diagnoses"]
    top = diagnoses[0] if diagnoses else None

    return {
        **base,
        "red_flags": red_flags,
        "max_urgency_rank": max_rank,
        "diagnoses": diagnoses[:3],
        "top_diagnosis": top["diagnosis"] if top else None,
        "top_score": top["score"] if top else 0,
        "evidence": top["supporting_evidence"] if top else [],
        "risk_tier": _tier(max_rank, top["score"] if top else 0),
    }


def _tier(max_urgency_rank: int, top_score: int) -> str:
    if max_urgency_rank >= URGENCY_RANK["immediate"]:
        return "Critical"
    if max_urgency_rank >= URGENCY_RANK["urgent"]:
        return "Urgent"
    if max_urgency_rank > 0:
        return "Watch"
    if top_score >= 80:
        return "High"
    if top_score >= 50:
        return "Moderate"
    return "Low"
