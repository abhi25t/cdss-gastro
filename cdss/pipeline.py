from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cdss.knowledge import KnowledgeGraph, validate
from cdss.knowledge.models import DiagnosisResult, ValidationReport
from cdss.recommendations import InvestigationEngine, SummaryEngine, TreatmentEngine
from cdss.rules import ConditionEngine, RedFlagEngine, ScoringEngine


@dataclass(frozen=True)
class CDSSPipelineResult:
    version: str
    validation: ValidationReport
    conditions: dict[str, bool]
    red_flags: list[dict[str, str]]
    diagnoses: list[DiagnosisResult]
    investigations: dict[str, list[str]]
    treatments: dict[str, list[str]]
    # v3 symptom-first additions; None for older versions so the v1 contract is unchanged.
    symptom_summary: dict[str, Any] | None = None
    draft_hpi: str | None = None

    @property
    def true_conditions(self) -> list[str]:
        return [condition_id for condition_id, active in self.conditions.items() if active]

    def as_dict(self) -> dict[str, Any]:
        data = {
            "version": self.version,
            "validation": {
                "valid": self.validation.is_valid,
                "error_count": len(self.validation.errors),
                "warning_count": len(self.validation.warnings),
                "issues": [issue.__dict__ for issue in self.validation.issues],
            },
            "conditions": self.conditions,
            "true_conditions": self.true_conditions,
            "red_flags": self.red_flags,
            "diagnoses": [diagnosis.as_dict() for diagnosis in self.diagnoses],
            "investigations": self.investigations,
            "treatments": self.treatments,
        }
        if self.symptom_summary is not None:
            data["symptom_summary"] = self.symptom_summary
        if self.draft_hpi is not None:
            data["draft_hpi"] = self.draft_hpi
        return data


class CDSSPipeline:
    def __init__(self, kg: KnowledgeGraph) -> None:
        self.kg = kg
        self.validation_report = validate(kg)
        self.condition_engine = ConditionEngine(kg)
        self.red_flag_engine = RedFlagEngine(kg)
        self.scoring_engine = ScoringEngine(kg)
        self.investigation_engine = InvestigationEngine(kg)
        self.treatment_engine = TreatmentEngine(kg)
        self.summary_engine = SummaryEngine(kg)

    @classmethod
    def from_version(
        cls,
        version: str,
        knowledge_graph_root: str | Path = "knowledge_graph",
    ) -> "CDSSPipeline":
        return cls(KnowledgeGraph.load(Path(knowledge_graph_root) / version))

    @classmethod
    def from_path(cls, path: str | Path) -> "CDSSPipeline":
        return cls(KnowledgeGraph.load(path))

    def run(self, answers: dict[str, Any]) -> CDSSPipelineResult:
        conditions = self.condition_engine.evaluate(answers)
        red_flags = self.red_flag_engine.detect(conditions)
        diagnoses = self.scoring_engine.score(conditions)
        investigations = self.investigation_engine.recommend(diagnoses)
        treatments = self.treatment_engine.recommend(diagnoses)
        symptom_summary, draft_hpi = self.summary_engine.summarize(answers)

        return CDSSPipelineResult(
            version=self.kg.version,
            validation=self.validation_report,
            conditions=conditions,
            red_flags=red_flags,
            diagnoses=diagnoses,
            investigations=investigations,
            treatments=treatments,
            symptom_summary=symptom_summary,
            draft_hpi=draft_hpi,
        )
