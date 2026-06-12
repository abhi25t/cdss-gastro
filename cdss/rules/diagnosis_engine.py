from __future__ import annotations

from typing import Any

from cdss.knowledge.models import DiagnosisResult, KnowledgeGraph
from cdss.rules.condition_engine import ConditionEngine
from cdss.rules.scoring_engine import ScoringEngine


class DiagnosisEngine:
    def __init__(self, kg: KnowledgeGraph) -> None:
        self.condition_engine = ConditionEngine(kg)
        self.scoring_engine = ScoringEngine(kg)

    def diagnose(self, answers: dict[str, Any]) -> list[DiagnosisResult]:
        conditions = self.condition_engine.evaluate(answers)
        return self.scoring_engine.score(conditions)
