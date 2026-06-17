"""Human-readable derivations for the dashboard: chief complaint, main symptom, a
plain-language answer list, a draft history-of-present-illness, and waiting time.

For v3 the rich structured summary + HPI come from the pipeline (SummaryEngine);
this module reuses those when present and otherwise degrades gracefully so the
dashboard still works for v1/v2/v2.1 answers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cdss.knowledge.models import KnowledgeGraph

ENTRY_QUESTION = "q_main_complaint"


def answer_label(kg: KnowledgeGraph, question_id: str, value: Any) -> str:
    """Display text for an answer: an option's label, Yes/No, a comma-joined list
    (multi_choice), or the raw value (number/text)."""
    question = kg.questions.get(question_id)
    if isinstance(value, (list, tuple)):
        return ", ".join(_option_label(question, item) for item in value) or "—"
    norm = _norm(value)
    if question is not None:
        for option in question.options:
            if _norm(option.get("value")) == norm:
                return str(option.get("label") or option.get("value") or norm)
    if norm == "yes":
        return "Yes"
    if norm == "no":
        return "No"
    return "" if value is None else str(value)


def _option_label(question: Any, value: Any) -> str:
    norm = _norm(value)
    if question is not None:
        for option in question.options:
            if _norm(option.get("value")) == norm:
                return str(option.get("label") or option.get("value") or norm)
    return str(value)


def humanize_answers(kg: KnowledgeGraph, answers: dict[str, Any]) -> list[dict[str, str]]:
    """One {question, answer} row per answered question, in knowledge-graph order so
    unanswered/irrelevant questions are skipped and ordering is stable."""
    rows: list[dict[str, str]] = []
    ordered = list(kg.questions) + [qid for qid in answers if qid not in kg.questions]
    for qid in ordered:
        if qid not in answers:
            continue
        question = kg.questions.get(qid)
        rows.append(
            {
                "question": question.text if question and question.text else qid,
                "answer": answer_label(kg, qid, answers[qid]),
            }
        )
    return rows


def chief_complaint(kg: KnowledgeGraph, answers: dict[str, Any], result: dict[str, Any] | None = None) -> str:
    res = result or {}
    summaries = res.get("symptom_summaries") or []
    labels = [s.get("chief_complaint") for s in summaries if s.get("chief_complaint")]
    if labels:
        return ", ".join(labels)
    summary = res.get("symptom_summary") or {}
    if summary.get("chief_complaint"):
        return str(summary["chief_complaint"])
    qid = ENTRY_QUESTION if ENTRY_QUESTION in answers else _first_answered(kg, answers)
    if qid is None:
        return "—"
    return answer_label(kg, qid, answers.get(qid)) or "—"


def main_symptom(kg: KnowledgeGraph, answers: dict[str, Any], result: dict[str, Any] | None = None) -> str:
    findings = ((result or {}).get("symptom_summary") or {}).get("findings") or {}
    if findings:
        first = next((v for v in findings.values() if isinstance(v, str) and v), None)
        if first:
            return first
    for qid in kg.questions:
        if qid == ENTRY_QUESTION or qid not in answers:
            continue
        question = kg.questions[qid]
        if question.type == "single_choice":
            label = answer_label(kg, qid, answers[qid])
            if label:
                return label
    for qid in kg.questions:
        if qid == ENTRY_QUESTION or qid not in answers:
            continue
        if _norm(answers[qid]) == "yes":
            return _strip(kg.questions[qid].text)
    return "—"


def draft_hpi(kg: KnowledgeGraph, answers: dict[str, Any], result: dict[str, Any] | None = None) -> str:
    if (result or {}).get("draft_hpi"):
        return str(result["draft_hpi"])
    # Fallback narrative for non-v3 graphs: complaint + positive findings + negatives.
    complaint = chief_complaint(kg, answers, result)
    positives: list[str] = []
    negatives: list[str] = []
    for qid in kg.questions:
        if qid == ENTRY_QUESTION or qid not in answers:
            continue
        question = kg.questions[qid]
        if question.type == "yes_no":
            phrase = _strip(question.text)
            (positives if _norm(answers[qid]) == "yes" else negatives).append(phrase)
        elif question.type == "single_choice":
            positives.append(answer_label(kg, qid, answers[qid]).lower())
    sentence = f"Patient reports {complaint.lower()}"
    if positives:
        sentence += f", with {_join(positives)}"
    sentence += "."
    if negatives:
        sentence += f" Denies {_join(negatives)}."
    return sentence


def waiting_minutes(created_at: Any, now: datetime | None = None) -> int:
    started = _parse(created_at)
    if started is None:
        return 0
    reference = now or datetime.now(timezone.utc)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    delta = (reference - started).total_seconds() / 60.0
    return max(0, int(delta))


# ----------------------------------------------------------------------

def _first_answered(kg: KnowledgeGraph, answers: dict[str, Any]) -> str | None:
    for qid in kg.questions:
        if qid in answers:
            return qid
    return next(iter(answers), None)


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return items[0] if items else ""
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _strip(text: str) -> str:
    return text.rstrip("?").strip().lower()


def _norm(value: Any) -> Any:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes"}:
            return "yes"
        if lowered in {"false", "no"}:
            return "no"
        return lowered
    return value
