from __future__ import annotations

from collections import Counter

from cdss.knowledge.models import KnowledgeGraph, ValidationReport, canonical_id

VALIDATION_PROFILES = {"prototype", "clinical"}


def validate(kg: KnowledgeGraph, profile: str = "prototype") -> ValidationReport:
    if profile not in VALIDATION_PROFILES:
        raise ValueError(f"Unknown validation profile '{profile}'. Expected one of: {sorted(VALIDATION_PROFILES)}")

    report = ValidationReport()
    clinical = profile == "clinical"

    for duplicate in kg.yaml_duplicate_keys:
        report.add("error", "duplicate_yaml_key", f"Duplicate YAML key found: {duplicate}", duplicate)

    _check_duplicate_ids(report, "question", [item.get("id") for item in _raw_items(kg.raw_files.get("questions", {}), "questions")])
    _check_duplicate_ids(report, "condition", [item.get("id") for item in _raw_items(kg.raw_files.get("conditions", {}), "conditions")])
    _check_duplicate_ids(report, "diagnosis", [_raw_diagnosis_id(item) for item in _raw_items(kg.raw_files.get("diagnoses", {}), "diagnoses")])
    _check_duplicate_ids(report, "rule", [item.get("id") for item in _raw_items(kg.raw_files.get("rules", {}), "rules") if isinstance(item, dict) and item.get("id")])
    _check_duplicate_ids(report, "red_flag", [item.get("id") for item in _raw_items(kg.raw_files.get("red_flags", {}), "red_flags")])

    for flow in kg.flows.values():
        if not flow.start:
            report.add("error", "missing_flow_start", f"Flow '{flow.id}' has no start question", f"flows.{flow.id}")
        elif flow.start not in kg.questions:
            report.add(
                "error",
                "broken_flow_start",
                f"Flow '{flow.id}' starts at missing question '{flow.start}'",
                f"flows.{flow.id}.start",
            )
        for source_id, branches in flow.transitions.items():
            if source_id not in kg.questions:
                report.add(
                    "error",
                    "broken_flow_source",
                    f"Flow '{flow.id}' has transition source missing question '{source_id}'",
                    f"flows.{flow.id}.transitions.{source_id}",
                )
            for answer, target_id in branches.items():
                if target_id not in kg.questions:
                    report.add(
                        "error",
                        "broken_flow_target",
                        f"Flow '{flow.id}' answer '{answer}' points to missing question '{target_id}'",
                        f"flows.{flow.id}.transitions.{source_id}.{answer}",
                    )

    for diagnosis in kg.diagnoses.values():
        for condition_id in diagnosis.supporting_conditions:
            if condition_id not in kg.conditions:
                report.add(
                    "error",
                    "missing_condition",
                    f"Diagnosis '{diagnosis.name}' references missing condition '{condition_id}'",
                    f"diagnoses.{diagnosis.id}.supporting_conditions",
                )

    for rule in kg.rules:
        diagnosis_id = _rule_diagnosis_id(rule.id, rule.diagnosis)
        if diagnosis_id and diagnosis_id not in kg.diagnoses:
            severity = "error" if rule.diagnosis else "warning"
            report.add(
                severity,
                "missing_diagnosis",
                f"Rule '{rule.id}' references missing diagnosis '{diagnosis_id}'",
                f"rules.{rule.id}.diagnosis",
            )
        if not rule.condition_refs:
            report.add(
                "error" if clinical else "warning",
                "rule_without_conditions",
                f"Rule '{rule.id}' has no condition requirements and will not score by itself",
                f"rules.{rule.id}",
            )
        for condition_id in rule.condition_refs:
            if condition_id not in kg.conditions:
                report.add(
                    "error",
                    "missing_condition",
                    f"Rule '{rule.id}' references missing condition '{condition_id}'",
                    f"rules.{rule.id}",
                )

    for flag in kg.red_flags:
        if not flag.when:
            if clinical:
                report.add(
                    "error",
                    "red_flag_without_conditions",
                    f"Red flag '{flag.id}' has no condition requirements and will not trigger automatically",
                    f"red_flags.{flag.id}",
                )
        for condition_id in flag.when:
            if condition_id not in kg.conditions:
                report.add(
                    "error",
                    "missing_condition",
                    f"Red flag '{flag.id}' references missing condition '{condition_id}'",
                    f"red_flags.{flag.id}",
                )

    _check_recommendations(report, kg, "investigations", kg.investigations, profile)
    _check_recommendations(report, kg, "treatment_recommendations", kg.treatment_recommendations, profile)
    return report


def _check_duplicate_ids(report: ValidationReport, kind: str, values: list[object]) -> None:
    ids = [str(value) for value in values if value is not None]
    for item_id, count in Counter(ids).items():
        if count > 1:
            report.add("error", "duplicate_id", f"Duplicate {kind} id '{item_id}' appears {count} times", kind)


def _raw_items(raw: object, root_key: str) -> list[object]:
    if not isinstance(raw, dict):
        return raw if isinstance(raw, list) else []
    items = raw.get(root_key, [])
    if isinstance(items, dict):
        return [{"id": key, **(value or {})} for key, value in items.items()]
    return items if isinstance(items, list) else []


def _raw_diagnosis_id(item: object) -> str | None:
    if isinstance(item, str):
        return canonical_id(item)
    if isinstance(item, dict) and item.get("id") is not None:
        return canonical_id(str(item["id"]))
    return None


def _rule_diagnosis_id(rule_id: str, explicit_diagnosis: str | None) -> str | None:
    if explicit_diagnosis:
        return canonical_id(explicit_diagnosis)
    for prefix in ("probable_", "possible_"):
        if rule_id.startswith(prefix):
            return canonical_id(rule_id.removeprefix(prefix))
    return canonical_id(rule_id) if rule_id else None


def _check_recommendations(
    report: ValidationReport,
    kg: KnowledgeGraph,
    label: str,
    recommendations: dict[str, list[str]],
    profile: str,
) -> None:
    clinical = profile == "clinical"
    non_diagnosis_buckets = {"common", "baseline", "upper_gi", "lower_gi", "hepatobiliary", "pancreatic"}
    for key in recommendations:
        if key not in kg.diagnoses and key not in non_diagnosis_buckets:
            report.add(
                "error" if clinical else "warning",
                "recommendation_unknown_diagnosis",
                f"{label} contains bucket '{key}' that is not a known diagnosis",
                f"{label}.{key}",
            )

    for diagnosis_id, diagnosis in kg.diagnoses.items():
        if diagnosis_id not in recommendations:
            if not clinical:
                continue
            report.add(
                "error",
                "missing_recommendation",
                f"Diagnosis '{diagnosis.name}' has no {label} entry",
                f"{label}.{diagnosis_id}",
            )
