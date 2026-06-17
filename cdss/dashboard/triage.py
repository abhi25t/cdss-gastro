from __future__ import annotations

from typing import Any

from cdss.api.registry import PipelineRegistry, UnknownVersionError
from cdss.dashboard import summary
from cdss.recommendations.summary_engine import resolve_symptoms

# How urgent each red-flag level is. Higher = called first. Unknown urgencies
# fall in the middle so a misconfigured flag is never silently ignored. Doctors
# asked to queue first-come-first-serve (see triage() below), so this no longer
# drives ordering — it is kept to compute the optional red-flag safety badge and
# so risk ranking can be re-enabled later without rework.
URGENCY_RANK = {"immediate": 4, "urgent": 3, "soon": 2, "routine": 1}
UNKNOWN_URGENCY_RANK = 2


def urgency_rank(urgency: str) -> int:
    return URGENCY_RANK.get(str(urgency).strip().lower(), UNKNOWN_URGENCY_RANK)


def triage(submissions: list[dict[str, Any]], registry: PipelineRegistry) -> list[dict[str, Any]]:
    """Run each submission through the CDSS pipeline and return them in arrival
    order (first come, first served), by trusted server timestamp.

    The pipeline still runs and red-flag urgency is still computed (it feeds the
    suggestion panel and an optional safety badge). To re-enable risk ranking,
    swap the sort key for the commented one below."""
    rows = [_assess(sub, registry) for sub in submissions]
    rows.sort(key=lambda r: (r.get("created_at") or r.get("submitted_at") or ""))
    # Risk-based ordering (disabled per doctors' request; kept for later):
    # rows.sort(key=lambda r: (r["max_urgency_rank"], r["top_score"]), reverse=True)
    for position, row in enumerate(rows, start=1):
        row["position"] = position
    return rows


def _assess(sub: dict[str, Any], registry: PipelineRegistry) -> dict[str, Any]:
    version = str(sub.get("kg_version") or "v1")
    created_at = sub.get("created_at") or sub.get("submitted_at")
    base = {
        "id": sub.get("id"),
        "uhid": sub.get("uhid"),
        "patient_name": sub.get("patient_name"),
        "patient_age": sub.get("patient_age"),
        "patient_sex": sub.get("patient_sex"),
        "doctor_slug": sub.get("doctor_slug"),
        "status": sub.get("status", "waiting"),
        "submitted_at": sub.get("submitted_at"),
        "created_at": created_at,
        "waiting_minutes": summary.waiting_minutes(created_at),
        "kg_version": version,
    }

    answers = dict(sub.get("answers") or {})
    try:
        result = registry.pipeline(version).run(answers).as_dict()
        kg = registry.knowledge_graph(version)
    except UnknownVersionError:
        return {
            **base,
            "chief_complaint": "—", "main_symptom": "—",
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
        "chief_complaint": summary.chief_complaint(kg, answers, result),
        "main_symptom": summary.main_symptom(kg, answers, result),
        "red_flags": red_flags,
        "max_urgency_rank": max_rank,
        "diagnoses": diagnoses[:3],
        "top_diagnosis": top["diagnosis"] if top else None,
        "top_score": top["score"] if top else 0,
        "evidence": top["supporting_evidence"] if top else [],
        "risk_tier": _tier(max_rank, top["score"] if top else 0),
    }


def assess_detail(sub: dict[str, Any], registry: PipelineRegistry) -> dict[str, Any]:
    """Full per-patient view for the consultation page: raw + humanised answers, a
    draft note (chief complaint + HPI), the complete ranked differential with evidence,
    and flattened suggested tests/medications for the right-panel pills."""
    version = str(sub.get("kg_version") or "v1")
    answers = dict(sub.get("answers") or {})
    created_at = sub.get("created_at") or sub.get("submitted_at")
    base = {
        "id": sub.get("id"),
        "uhid": sub.get("uhid"),
        "patient_name": sub.get("patient_name"),
        "patient_age": sub.get("patient_age"),
        "patient_sex": sub.get("patient_sex"),
        "doctor_slug": sub.get("doctor_slug"),
        "status": sub.get("status", "waiting"),
        "submitted_at": sub.get("submitted_at"),
        "created_at": created_at,
        "waiting_minutes": summary.waiting_minutes(created_at),
        "kg_version": version,
    }

    try:
        pipeline = registry.pipeline(version)
        result = pipeline.run(answers).as_dict()
        kg = pipeline.kg
    except UnknownVersionError:
        return {
            **base,
            "error": f"Unknown knowledge graph version '{version}'",
            "chief_complaint": "—", "main_symptom": "—", "draft_hpi": "",
            "answers": answers, "answers_summary": [], "symptom_summary": None,
            "symptom_summaries": [], "red_flags": [], "differential": [],
            "suggested_tests": [], "suggested_medications": [],
            "tests_by_diagnosis": {}, "medications_by_diagnosis": {}, "true_conditions": [],
        }

    diagnoses = _tag_with_complaints(result["diagnoses"], kg, answers)
    return {
        **base,
        "chief_complaint": summary.chief_complaint(kg, answers, result),
        "main_symptom": summary.main_symptom(kg, answers, result),
        "draft_hpi": summary.draft_hpi(kg, answers, result),
        "answers": answers,
        "answers_summary": summary.humanize_answers(kg, answers),
        "symptom_summary": result.get("symptom_summary"),
        "symptom_summaries": result.get("symptom_summaries")
        or ([result["symptom_summary"]] if result.get("symptom_summary") else []),
        "red_flags": result["red_flags"],
        "differential": diagnoses,
        "suggested_tests": _flatten_recommendations(result["investigations"], diagnoses),
        "suggested_medications": _flatten_recommendations(result["treatments"], diagnoses),
        # Per-diagnosis maps (keyed by diagnosis name) so the consultation page can float a
        # clicked diagnosis's specific tests/medicines to the top of the pool.
        "tests_by_diagnosis": result["investigations"],
        "medications_by_diagnosis": result["treatments"],
        "true_conditions": result["true_conditions"],
    }


def _tag_with_complaints(
    diagnoses: list[dict[str, Any]], kg: Any, answers: dict[str, Any]
) -> list[dict[str, Any]]:
    """When the patient has more than one chief complaint, tag each diagnosis with the
    complaint(s) it belongs to (so the deduped differential shows "from: …"). Single
    complaint → no tags (avoids noise)."""
    if not getattr(kg, "is_symptom_first", False):
        return diagnoses
    active = resolve_symptoms(kg, answers)
    if len(active) <= 1:
        return diagnoses
    name_to_complaints: dict[str, list[str]] = {}
    for symptom in active:
        for dx_id in symptom.differential:
            dx = kg.diagnoses.get(dx_id)
            if dx and symptom.label not in name_to_complaints.setdefault(dx.name, []):
                name_to_complaints[dx.name].append(symptom.label)
    tagged: list[dict[str, Any]] = []
    for diagnosis in diagnoses:
        entry = dict(diagnosis)
        complaints = name_to_complaints.get(diagnosis.get("diagnosis"))
        if complaints:
            entry["chief_complaints"] = complaints
        tagged.append(entry)
    return tagged


def _flatten_recommendations(mapping: dict[str, list[str]], diagnoses: list[dict[str, Any]]) -> list[str]:
    """Flatten per-diagnosis recommendations into one ordered, de-duplicated list,
    following the differential ranking so the most likely diagnosis's items come first."""
    out: list[str] = []
    seen: set[str] = set()
    ordered_keys = [d["diagnosis"] for d in diagnoses] + list(mapping)
    for key in ordered_keys:
        for item in mapping.get(key, []):
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


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
