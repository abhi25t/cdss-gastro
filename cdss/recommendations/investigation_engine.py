from __future__ import annotations

from cdss.knowledge.models import DiagnosisResult, KnowledgeGraph, canonical_id


class InvestigationEngine:
    def __init__(self, kg: KnowledgeGraph) -> None:
        self.kg = kg

    def recommend(self, diagnoses: list[DiagnosisResult | dict[str, object] | str]) -> dict[str, list[str]]:
        return _recommend(self.kg.investigations, diagnoses)


def _recommend(source: dict[str, list[str]], diagnoses: list[DiagnosisResult | dict[str, object] | str]) -> dict[str, list[str]]:
    recommendations: dict[str, list[str]] = {}
    shared = [*source.get("common", []), *source.get("baseline", [])]
    if shared:
        recommendations["baseline"] = list(dict.fromkeys(shared))
    for diagnosis in diagnoses:
        name = _diagnosis_name(diagnosis)
        items = source.get(canonical_id(name), [])
        if items:
            recommendations[name] = items
    return recommendations


def _diagnosis_name(diagnosis: DiagnosisResult | dict[str, object] | str) -> str:
    if isinstance(diagnosis, DiagnosisResult):
        return diagnosis.diagnosis
    if isinstance(diagnosis, dict):
        return str(diagnosis.get("diagnosis", ""))
    return str(diagnosis)
