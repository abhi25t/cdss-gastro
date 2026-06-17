from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar


def canonical_id(value: str) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    type: str
    options: list[dict[str, Any]] = field(default_factory=list)
    group: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Flow:
    id: str
    start: str | None
    transitions: dict[str, dict[str, str]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Condition:
    id: str
    expression: str
    label: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def evidence_label(self) -> str:
        return self.label or self.id.replace("_", " ").title()


@dataclass(frozen=True)
class Diagnosis:
    id: str
    name: str
    supporting_conditions: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    id: str
    diagnosis: str | None = None
    score: int | None = None
    weight: int | None = None
    when: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    # v3 symptom-first additions (defaults keep v1/v2/v2.1 behaviour identical):
    # `condition` is sugar for a single-element `requires`; `direction` lets a finding
    # argue against a diagnosis; `specificity` is an explainability label.
    condition: str | None = None
    direction: str = "positive"
    specificity: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def condition_refs(self) -> list[str]:
        refs = [*self.when, *self.requires]
        if self.condition:
            refs.append(self.condition)
        return list(dict.fromkeys(refs))

    @property
    def is_negative(self) -> bool:
        return str(self.direction).strip().lower() == "negative"


@dataclass(frozen=True)
class Symptom:
    """A chief complaint and its complete clinical work-up (v3 symptom-first model).

    `workup` is the ordered list of question ids that fully characterise the symptom;
    `differential` is the set of diagnoses this symptom can lead to. Both are used by
    the validator (reachability/completeness) and the summary engine.
    """

    id: str
    label: str
    chief_complaint_text: str = ""
    flow: str | None = None
    workup: list[str] = field(default_factory=list)
    differential: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RedFlag:
    id: str
    urgency: str
    when: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    location: str | None = None


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        location: str | None = None,
    ) -> None:
        self.issues.append(ValidationIssue(severity, code, message, location))

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.is_valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [issue.__dict__ for issue in self.issues],
        }


@dataclass(frozen=True)
class DiagnosisResult:
    diagnosis: str
    score: int
    supporting_evidence: list[str]
    matched_conditions: list[str] = field(default_factory=list)
    # v3 weighted-differential additions; emitted only when populated so the v1
    # /run + dashboard contracts stay byte-identical.
    confidence: int | None = None
    evidence_against: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "diagnosis": self.diagnosis,
            "score": self.score,
            "supporting_evidence": self.supporting_evidence,
        }
        if self.confidence is not None:
            data["confidence"] = self.confidence
        if self.evidence_against:
            data["evidence_against"] = self.evidence_against
        return data


@dataclass
class KnowledgeGraph:
    version: str
    path: Path
    questions: dict[str, Question] = field(default_factory=dict)
    flows: dict[str, Flow] = field(default_factory=dict)
    conditions: dict[str, Condition] = field(default_factory=dict)
    diagnoses: dict[str, Diagnosis] = field(default_factory=dict)
    symptoms: dict[str, Symptom] = field(default_factory=dict)
    rules: list[Rule] = field(default_factory=list)
    red_flags: list[RedFlag] = field(default_factory=list)
    investigations: dict[str, list[str]] = field(default_factory=dict)
    treatment_recommendations: dict[str, list[str]] = field(default_factory=dict)
    raw_files: dict[str, Any] = field(default_factory=dict)
    yaml_duplicate_keys: list[str] = field(default_factory=list)

    FILE_ALIASES: ClassVar[dict[str, str]] = {
        "flow": "flows",
        "diagnosis": "diagnoses",
    }

    @classmethod
    def load(cls, path: str | Path) -> "KnowledgeGraph":
        from cdss.knowledge.loader import load_knowledge_graph

        return load_knowledge_graph(path)

    @property
    def is_symptom_first(self) -> bool:
        """True for v3-style graphs that define symptoms; gates weighted scoring,
        the summary engine, and the extra validator checks so older versions are
        unaffected."""
        return bool(self.symptoms)

    def diagnosis_for(self, value: str) -> Diagnosis | None:
        return self.diagnoses.get(canonical_id(value))
