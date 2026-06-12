from __future__ import annotations

from cdss.knowledge.models import DiagnosisResult, KnowledgeGraph
from cdss.recommendations.investigation_engine import _recommend


class TreatmentEngine:
    def __init__(self, kg: KnowledgeGraph) -> None:
        self.kg = kg

    def recommend(self, diagnoses: list[DiagnosisResult | dict[str, object] | str]) -> dict[str, list[str]]:
        return _recommend(self.kg.treatment_recommendations, diagnoses)
