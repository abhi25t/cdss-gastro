"""Deterministic structured-summary + draft-HPI generator for the v3 symptom-first
knowledge graph. No LLM — pure templating over the patient's structured answers and
the question metadata in the knowledge graph.

It produces two artefacts that pre-fill the doctor's note on the dashboard:
  * a structured summary keyed by semiology slot (Site / Onset / Character / ...),
  * a draft History of Present Illness narrative.

For non-symptom-first graphs (v1/v2/v2.1) it returns (None, None) and the pipeline
simply omits the block, so older versions are unaffected.
"""

from __future__ import annotations

from typing import Any

from cdss.knowledge.models import KnowledgeGraph, Question, Symptom

# Fixed clinical order for the SOCRATES-style single-choice slots in the HPI.
SEMIOLOGY_ORDER = [
    "site",
    "onset",
    "character",
    "radiation",
    "severity",
    "timing",
    "aggravating",
    "relieving",
]

ASSOCIATED_SLOT = "Associated symptoms"
NEGATIVES_SLOT = "Pertinent negatives"


class SummaryEngine:
    def __init__(self, kg: KnowledgeGraph) -> None:
        self.kg = kg

    def summarize(self, answers: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        if not self.kg.is_symptom_first:
            return None, None
        symptom = self._active_symptom(answers)
        if symptom is None:
            return None, None

        slot_findings: dict[str, str] = {}
        associated: list[str] = []
        negatives: list[str] = []
        hpi_fragments: list[tuple[int, str]] = []

        for qid in symptom.workup:
            question = self.kg.questions.get(qid)
            if question is None or qid not in answers:
                continue
            self._consume(question, answers[qid], slot_findings, associated, negatives, hpi_fragments)

        findings: dict[str, Any] = dict(_ordered_slots(slot_findings))
        if associated:
            findings[ASSOCIATED_SLOT] = associated
        if negatives:
            findings[NEGATIVES_SLOT] = negatives

        summary = {"chief_complaint": symptom.label, "findings": findings}
        hpi = self._build_hpi(symptom, hpi_fragments, associated, negatives)
        return summary, hpi

    # ------------------------------------------------------------------

    def _active_symptom(self, answers: dict[str, Any]) -> Symptom | None:
        entry = "q_main_complaint" if "q_main_complaint" in self.kg.questions else None
        value = _norm(answers.get(entry)) if entry else None
        if value:
            for symptom in self.kg.symptoms.values():
                if symptom.id == value or symptom.flow == value:
                    return symptom
        # Fallback: the only symptom whose work-up questions were actually answered.
        for symptom in self.kg.symptoms.values():
            if any(qid in answers for qid in symptom.workup):
                return symptom
        return None

    def _consume(
        self,
        question: Question,
        answer: Any,
        slot_findings: dict[str, str],
        associated: list[str],
        negatives: list[str],
        hpi_fragments: list[tuple[int, str]],
    ) -> None:
        value = _norm(answer)
        raw = question.raw or {}
        slot = str(raw.get("semiology") or "").strip().lower()

        if question.type == "yes_no":
            # Associated symptom / pertinent negative. `finding` is a short noun phrase.
            finding = str(raw.get("finding") or _strip_question(question.text))
            if value == "yes":
                associated.append(finding)
            elif value == "no":
                negatives.append(f"no {finding}")
            return

        # single_choice (and any other choice type): resolve the option's display text.
        display = self._option_text(question, value)
        if not display:
            return
        slot_label = slot.title() if slot else (question.group or "Finding").replace("_", " ").title()
        slot_findings[slot_label] = display

        template = raw.get("summary_template")
        fragment = template.format(answer=display) if template else f"{slot or 'is'} {display}"
        order = SEMIOLOGY_ORDER.index(slot) if slot in SEMIOLOGY_ORDER else len(SEMIOLOGY_ORDER)
        hpi_fragments.append((order, fragment))

    def _option_text(self, question: Question, value: Any) -> str:
        for option in question.options:
            if _norm(option.get("value")) == value:
                return str(option.get("hpi") or option.get("label") or option.get("value") or "")
        return "" if value in (None, "") else str(value)

    def _build_hpi(
        self,
        symptom: Symptom,
        hpi_fragments: list[tuple[int, str]],
        associated: list[str],
        negatives: list[str],
    ) -> str:
        parts = [fragment for _, fragment in sorted(hpi_fragments, key=lambda item: item[0])]
        lead = f"Patient presents with {symptom.chief_complaint_text}"
        if parts:
            lead = f"{lead}, {', '.join(parts)}"
        if associated:
            lead = f"{lead}, associated with {_join(associated)}"
        sentence = lead + "."
        if negatives:
            denied = _join([neg[3:] if neg.startswith("no ") else neg for neg in negatives])
            sentence = f"{sentence} Denies {denied}."
        return sentence


def _ordered_slots(slot_findings: dict[str, str]) -> list[tuple[str, str]]:
    order = {slot.title(): index for index, slot in enumerate(SEMIOLOGY_ORDER)}
    return sorted(slot_findings.items(), key=lambda item: order.get(item[0], len(order)))


def _join(items: list[str]) -> str:
    items = [item for item in items if item]
    if len(items) <= 1:
        return items[0] if items else ""
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _strip_question(text: str) -> str:
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
