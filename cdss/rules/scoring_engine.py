from __future__ import annotations

from collections import defaultdict

from cdss.knowledge.models import DiagnosisResult, KnowledgeGraph, Rule, canonical_id


class ScoringEngine:
    def __init__(self, kg: KnowledgeGraph) -> None:
        self.kg = kg

    def score(self, conditions: dict[str, bool]) -> list[DiagnosisResult]:
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
