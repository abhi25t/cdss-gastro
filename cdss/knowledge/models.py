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
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def condition_refs(self) -> list[str]:
        return list(dict.fromkeys([*self.when, *self.requires]))


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "diagnosis": self.diagnosis,
            "score": self.score,
            "supporting_evidence": self.supporting_evidence,
        }


@dataclass
class KnowledgeGraph:
    version: str
    path: Path
    questions: dict[str, Question] = field(default_factory=dict)
    flows: dict[str, Flow] = field(default_factory=dict)
    conditions: dict[str, Condition] = field(default_factory=dict)
    diagnoses: dict[str, Diagnosis] = field(default_factory=dict)
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

    def diagnosis_for(self, value: str) -> Diagnosis | None:
        return self.diagnoses.get(canonical_id(value))
