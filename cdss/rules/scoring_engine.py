from __future__ import annotations

from collections import defaultdict

from cdss.knowledge.models import DiagnosisResult, KnowledgeGraph, Rule, canonical_id


class ScoringEngine:
    def __init__(self, kg: KnowledgeGraph) -> None:
        self.kg = kg

    def score(self, conditions: dict[str, bool]) -> list[DiagnosisResult]:
        if self.kg.is_symptom_first:
            return self._score_weighted(conditions)
        return self._score_legacy(conditions)

    # ------------------------------------------------------------------
    # v1/v2/v2.1: rigid rule weights + supporting_conditions fallback.
    # Behaviour is intentionally byte-identical to the pre-v3 engine.
    # ------------------------------------------------------------------
    def _score_legacy(self, conditions: dict[str, bool]) -> list[DiagnosisResult]:
        scores: dict[str, int] = defaultdict(int)
        matched_conditions: dict[str, list[str]] = defaultdict(list)

        for rule in self.kg.rules:
            refs = rule.condition_refs
            if not refs or not all(conditions.get(ref, False) for ref in refs):
                continue
            diagnosis_id = _rule_diagnosis_id(rule)
            if diagnosis_id not in self.kg.diagnoses:
                continue
            scores[diagnosis_id] += rule.score if rule.score is not None else (rule.weight or 0)
            matched_conditions[diagnosis_id].extend(refs)

        for diagnosis in self.kg.diagnoses.values():
            refs = diagnosis.supporting_conditions
            active_refs = [ref for ref in refs if conditions.get(ref, False)]
            if not active_refs:
                continue
            if diagnosis.id not in scores:
                scores[diagnosis.id] = round((len(active_refs) / len(refs)) * 100)
            matched_conditions[diagnosis.id].extend(active_refs)

        results = [
            DiagnosisResult(
                diagnosis=self.kg.diagnoses[diagnosis_id].name,
                score=min(score, 100),
                supporting_evidence=_evidence(self.kg, matched_conditions[diagnosis_id]),
                matched_conditions=list(dict.fromkeys(matched_conditions[diagnosis_id])),
            )
            for diagnosis_id, score in scores.items()
            if score > 0 and matched_conditions[diagnosis_id]
        ]
        return sorted(results, key=lambda item: item.score, reverse=True)

    # ------------------------------------------------------------------
    # v3 symptom-first: each finding contributes a signed, weighted nudge to
    # one or more diagnoses. Produces a ranked differential with a normalised
    # confidence (sums to ~100% across the active differential) plus explicit
    # evidence for and against — every number traceable to a rule.
    # ------------------------------------------------------------------
    def _score_weighted(self, conditions: dict[str, bool]) -> list[DiagnosisResult]:
        raw: dict[str, int] = defaultdict(int)
        supporting: dict[str, list[str]] = defaultdict(list)
        against: dict[str, list[str]] = defaultdict(list)

        for rule in self.kg.rules:
            refs = rule.condition_refs
            if not refs or not all(conditions.get(ref, False) for ref in refs):
                continue
            diagnosis_id = _rule_diagnosis_id(rule)
            weight = rule.weight if rule.weight is not None else (rule.score or 0)
            if rule.is_negative:
                raw[diagnosis_id] -= weight
                against[diagnosis_id].extend(refs)
            else:
                raw[diagnosis_id] += weight
                supporting[diagnosis_id].extend(refs)

        # Confidence = relu(score) normalised across the active differential.
        total = sum(value for value in raw.values() if value > 0)

        results: list[DiagnosisResult] = []
        for diagnosis_id, score in raw.items():
            if score <= 0 or not supporting.get(diagnosis_id):
                # A diagnosis only enters the differential on net-positive evidence
                # backed by at least one supporting finding.
                continue
            confidence = round(100 * score / total) if total else 0
            results.append(
                DiagnosisResult(
                    diagnosis=self._diagnosis_name(diagnosis_id),
                    score=score,
                    supporting_evidence=_evidence(self.kg, supporting[diagnosis_id]),
                    matched_conditions=list(dict.fromkeys(supporting[diagnosis_id])),
                    confidence=confidence,
                    evidence_against=_evidence(self.kg, against.get(diagnosis_id, [])),
                )
            )
        return sorted(results, key=lambda item: item.score, reverse=True)

    def _diagnosis_name(self, diagnosis_id: str) -> str:
        diagnosis = self.kg.diagnoses.get(diagnosis_id)
        if diagnosis:
            return diagnosis.name
        return diagnosis_id.replace("_", " ").title()


def _rule_diagnosis_id(rule: Rule) -> str:
    if rule.diagnosis:
        return canonical_id(rule.diagnosis)
    for prefix in ("probable_", "possible_"):
        if rule.id.startswith(prefix):
            return canonical_id(rule.id.removeprefix(prefix))
    return canonical_id(rule.id)


def _evidence(kg: KnowledgeGraph, condition_ids: list[str]) -> list[str]:
    labels: list[str] = []
    for condition_id in dict.fromkeys(condition_ids):
        condition = kg.conditions.get(condition_id)
        labels.append(condition.evidence_label if condition else condition_id.replace("_", " ").title())
    return labels
