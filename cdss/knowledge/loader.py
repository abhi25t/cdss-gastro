from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from cdss.knowledge.models import (
    Condition,
    Diagnosis,
    Flow,
    KnowledgeGraph,
    Question,
    RedFlag,
    Rule,
    Symptom,
    canonical_id,
)


class DuplicateTrackingLoader(yaml.SafeLoader):
    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self.duplicate_keys: list[str] = []


def _construct_mapping(loader: DuplicateTrackingLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            loader.duplicate_keys.append(str(key))
        value = loader.construct_object(value_node, deep=deep)
        mapping[key] = value
    return mapping


DuplicateTrackingLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _read_yaml(path: Path) -> tuple[Any, list[str]]:
    with path.open("r", encoding="utf-8") as handle:
        loader = DuplicateTrackingLoader(handle)
        try:
            data = loader.get_single_data()
            return data or {}, loader.duplicate_keys
        finally:
            loader.dispose()


def load_knowledge_graph(path: str | Path) -> KnowledgeGraph:
    kg_path = Path(path)
    if not kg_path.exists():
        raise FileNotFoundError(f"Knowledge graph path does not exist: {kg_path}")
    if not kg_path.is_dir():
        raise NotADirectoryError(f"Knowledge graph path must be a directory: {kg_path}")

    raw_files: dict[str, Any] = {}
    duplicate_keys: list[str] = []
    for yaml_file in sorted(kg_path.glob("*.yaml")):
        key = KnowledgeGraph.FILE_ALIASES.get(yaml_file.stem, yaml_file.stem)
        raw, duplicates = _read_yaml(yaml_file)
        raw_files[key] = raw
        duplicate_keys.extend(f"{yaml_file.name}:{dup}" for dup in duplicates)

    return KnowledgeGraph(
        version=kg_path.name,
        path=kg_path,
        questions=_normalize_questions(raw_files.get("questions", {})),
        flows=_normalize_flows(raw_files.get("flows", {})),
        conditions=_normalize_conditions(raw_files.get("conditions", {})),
        diagnoses=_normalize_diagnoses(raw_files.get("diagnoses", {})),
        symptoms=_normalize_symptoms(raw_files.get("symptoms", {})),
        rules=_normalize_rules(raw_files.get("rules", {})),
        red_flags=_normalize_red_flags(raw_files.get("red_flags", {})),
        investigations=_normalize_recommendation_map(raw_files.get("investigations", {}).get("investigations", {})),
        treatment_recommendations=_normalize_recommendation_map(
            raw_files.get("treatment_recommendations", {}).get("treatment_recommendations", {})
        ),
        raw_files=raw_files,
        yaml_duplicate_keys=duplicate_keys,
    )


def _normalize_questions(raw: Any) -> dict[str, Question]:
    items = raw.get("questions", []) if isinstance(raw, dict) else raw
    questions: dict[str, Question] = {}
    if isinstance(items, dict):
        iterable = [{"id": key, **(value or {})} for key, value in items.items()]
    else:
        iterable = items or []

    for item in iterable:
        if not isinstance(item, dict) or "id" not in item:
            continue
        question_id = str(item["id"])
        questions[question_id] = Question(
            id=question_id,
            text=str(item.get("text", "")),
            type=str(item.get("type", "unknown")),
            options=list(item.get("options") or []),
            group=item.get("group"),
            raw=item,
        )
    return questions


def _normalize_flows(raw: Any) -> dict[str, Flow]:
    flows_raw = raw.get("flows", {}) if isinstance(raw, dict) else raw
    flows: dict[str, Flow] = {}
    if not isinstance(flows_raw, dict):
        return flows

    for flow_id, item in flows_raw.items():
        item = item or {}
        transitions = item.get("transitions") or {}
        normalized_transitions = {
            str(question_id): {str(answer): str(target) for answer, target in (branches or {}).items()}
            for question_id, branches in transitions.items()
        }
        flows[str(flow_id)] = Flow(
            id=str(flow_id),
            start=item.get("start"),
            transitions=normalized_transitions,
            raw=item,
        )
    return flows


def _normalize_conditions(raw: Any) -> dict[str, Condition]:
    conditions_raw = raw.get("conditions", {}) if isinstance(raw, dict) else raw
    conditions: dict[str, Condition] = {}
    if isinstance(conditions_raw, dict):
        iterable = [{"id": key, **(value or {})} for key, value in conditions_raw.items()]
    else:
        iterable = conditions_raw or []

    for item in iterable:
        if not isinstance(item, dict) or "id" not in item:
            continue
        condition_id = str(item["id"])
        conditions[condition_id] = Condition(
            id=condition_id,
            expression=str(item.get("expression", condition_id)),
            label=item.get("label"),
            raw=item,
        )
    return conditions


def _normalize_diagnoses(raw: Any) -> dict[str, Diagnosis]:
    diagnoses_raw = raw.get("diagnoses", {}) if isinstance(raw, dict) else raw
    diagnoses: dict[str, Diagnosis] = {}
    if isinstance(diagnoses_raw, dict):
        iterable = [{"id": key, **(value or {})} for key, value in diagnoses_raw.items()]
    else:
        iterable = [{"id": item, "name": item} if isinstance(item, str) else item for item in (diagnoses_raw or [])]

    for item in iterable:
        if not isinstance(item, dict) or "id" not in item:
            continue
        raw_id = str(item["id"])
        diag_id = canonical_id(raw_id)
        name = str(item.get("name") or raw_id.replace("_", " ").title())
        diagnoses[diag_id] = Diagnosis(
            id=diag_id,
            name=name,
            supporting_conditions=[str(value) for value in item.get("supporting_conditions") or []],
            raw=item,
        )
    return diagnoses


def _normalize_symptoms(raw: Any) -> dict[str, Symptom]:
    symptoms_raw = raw.get("symptoms", {}) if isinstance(raw, dict) else raw
    symptoms: dict[str, Symptom] = {}
    if isinstance(symptoms_raw, dict):
        iterable = [{"id": key, **(value or {})} for key, value in symptoms_raw.items()]
    else:
        iterable = symptoms_raw or []

    for item in iterable:
        if not isinstance(item, dict) or "id" not in item:
            continue
        symptom_id = str(item["id"])
        label = str(item.get("label") or symptom_id.replace("_", " ").title())
        symptoms[symptom_id] = Symptom(
            id=symptom_id,
            label=label,
            chief_complaint_text=str(item.get("chief_complaint_text") or label.lower()),
            flow=item.get("flow"),
            workup=[str(value) for value in item.get("workup") or []],
            differential=[str(value) for value in item.get("differential") or []],
            raw=item,
        )
    return symptoms


def _normalize_rules(raw: Any) -> list[Rule]:
    rules_raw = raw.get("rules", []) if isinstance(raw, dict) else raw
    rules: list[Rule] = []
    for index, item in enumerate(rules_raw or [], start=1):
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("id") or f"rule_{index}")
        condition = item.get("condition")
        direction = str(item.get("direction") or "positive").strip().lower()
        specificity = item.get("specificity")
        rules.append(
            Rule(
                id=rule_id,
                diagnosis=str(item["diagnosis"]) if item.get("diagnosis") is not None else None,
                score=_optional_int(item.get("score")),
                weight=_optional_int(item.get("weight")),
                when=[str(value) for value in item.get("when") or []],
                requires=[str(value) for value in item.get("requires") or []],
                condition=str(condition) if condition is not None else None,
                direction=direction,
                specificity=str(specificity) if specificity is not None else None,
                raw=item,
            )
        )
    return rules


def _normalize_red_flags(raw: Any) -> list[RedFlag]:
    flags_raw = raw.get("red_flags", []) if isinstance(raw, dict) else raw
    flags: list[RedFlag] = []
    for item in flags_raw or []:
        if not isinstance(item, dict) or "id" not in item:
            continue
        flags.append(
            RedFlag(
                id=str(item["id"]),
                urgency=str(item.get("urgency", "unknown")),
                when=[str(value) for value in item.get("when") or []],
                raw=item,
            )
        )
    return flags


def _normalize_recommendation_map(raw: Any) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    return {canonical_id(key): [str(value) for value in values or []] for key, values in raw.items()}


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
