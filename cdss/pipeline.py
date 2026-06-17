from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cdss.knowledge import KnowledgeGraph, validate
from cdss.knowledge.models import DiagnosisResult, ValidationReport
from cdss.knowledge.models import canonical_id
from cdss.recommendations import InvestigationEngine, SummaryEngine, TreatmentEngine
from cdss.recommendations.summary_engine import resolve_symptoms
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
    # v4 multi-symptom: one structured summary per active chief complaint (symptom_summary
    # stays = the first, for back-compat). None/empty for older versions.
    symptom_summaries: list[dict[str, Any]] | None = None

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
        if self.symptom_summaries:
            data["symptom_summaries"] = self.symptom_summaries
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
        # In the symptom-first model, restrict the differential to the active chief
        # complaint(s)' candidate diagnoses so cross-cutting findings don't leak in
        # diagnoses from other symptoms. With multiple complaints the scope is the UNION
        # of their differentials; the scorer aggregates by diagnosis, so a diagnosis shared
        # by two complaints appears once with the evidence from both merged.
        allowed: set[str] | None = None
        if self.kg.is_symptom_first:
            symptoms = resolve_symptoms(self.kg, answers)
            if symptoms:
                allowed = set()
                for symptom in symptoms:
                    allowed |= {canonical_id(d) for d in symptom.differential}
        diagnoses = self.scoring_engine.score(conditions, allowed_diagnoses=allowed)
        investigations = self.investigation_engine.recommend(diagnoses)
        treatments = self.treatment_engine.recommend(diagnoses)
        symptom_summaries, draft_hpi = self.summary_engine.summarize_all(answers)
        symptom_summary = symptom_summaries[0] if symptom_summaries else None

        return CDSSPipelineResult(
            version=self.kg.version,
            validation=self.validation_report,
            conditions=conditions,
            red_flags=red_flags,
            diagnoses=diagnoses,
            investigations=investigations,
            treatments=treatments,
            symptom_summary=symptom_summary,
            symptom_summaries=symptom_summaries or None,
            draft_hpi=draft_hpi,
        )
