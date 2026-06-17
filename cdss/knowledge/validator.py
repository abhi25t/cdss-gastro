from __future__ import annotations

import ast
from collections import Counter

from cdss.knowledge.models import Flow, KnowledgeGraph, ValidationReport, canonical_id

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

    if kg.symptoms:
        _check_symptom_first(report, kg, clinical)
    return report


def _check_symptom_first(report: ValidationReport, kg: KnowledgeGraph, clinical: bool) -> None:
    """v3 symptom-first checks: every work-up question must be reachable in its flow,
    every rule condition must be collectable somewhere, and every listed differential
    diagnosis must be able to accumulate score on its symptom's pathway. Directly
    prevents the historical 'diagnosis unreachable because a question is never asked' bug.
    """
    per_flow_reachable: dict[str, set[str]] = {}
    global_reachable: set[str] = set()
    for symptom in kg.symptoms.values():
        flow = kg.flows.get(symptom.flow) if symptom.flow else None
        reachable = _reachable_questions(flow)
        per_flow_reachable[symptom.id] = reachable
        global_reachable |= reachable

    # 1. Work-up question reachability.
    for symptom in kg.symptoms.values():
        if not symptom.flow or symptom.flow not in kg.flows:
            report.add(
                "error",
                "symptom_missing_flow",
                f"Symptom '{symptom.id}' references missing flow '{symptom.flow}'",
                f"symptoms.{symptom.id}.flow",
            )
            continue
        reachable = per_flow_reachable[symptom.id]
        for qid in symptom.workup:
            if qid not in kg.questions:
                report.add(
                    "error",
                    "missing_workup_question",
                    f"Symptom '{symptom.id}' work-up references missing question '{qid}'",
                    f"symptoms.{symptom.id}.workup",
                )
            elif qid not in reachable:
                report.add(
                    "error",
                    "unreachable_workup_question",
                    f"Symptom '{symptom.id}' work-up question '{qid}' is never asked in flow '{symptom.flow}'",
                    f"symptoms.{symptom.id}.workup",
                )

    # 2. Every condition used by a rule must be collectable from some flow.
    cond_cache: dict[str, set[str]] = {}
    for rule in kg.rules:
        for cond_id in rule.condition_refs:
            if cond_id not in kg.conditions:
                continue  # missing-condition already reported above
            qset = _condition_questions(kg, cond_id, cond_cache)
            if qset and not (qset & global_reachable):
                report.add(
                    "error" if clinical else "warning",
                    "condition_not_collectable",
                    f"Condition '{cond_id}' (used by rule '{rule.id}') needs question(s) "
                    f"{sorted(qset)} that are unreachable in any symptom flow",
                    f"conditions.{cond_id}",
                )

    # 3. Every differential diagnosis must be reachable on its symptom's pathway.
    for symptom in kg.symptoms.values():
        reachable = per_flow_reachable.get(symptom.id, set())
        for diag in symptom.differential:
            diag_id = canonical_id(diag)
            if not _diagnosis_reachable(kg, diag_id, reachable, cond_cache):
                report.add(
                    "error" if clinical else "warning",
                    "unreachable_diagnosis",
                    f"Diagnosis '{diag}' in symptom '{symptom.id}' differential cannot accumulate "
                    f"score from its flow (no positive rule with collectable conditions)",
                    f"symptoms.{symptom.id}.differential",
                )

    # 4. Work-up completeness (semiology slots), only when the symptom declares requirements.
    for symptom in kg.symptoms.values():
        required = [str(slot).strip().lower() for slot in (symptom.raw.get("required_semiology") or [])]
        if not required:
            continue
        present = {
            str((kg.questions[q].raw or {}).get("semiology") or "").strip().lower()
            for q in symptom.workup
            if q in kg.questions
        }
        for slot in required:
            if slot not in present:
                report.add(
                    "warning",
                    "incomplete_workup",
                    f"Symptom '{symptom.id}' work-up is missing semiology slot '{slot}'",
                    f"symptoms.{symptom.id}.workup",
                )

    # 5. Rule hygiene for the new fields.
    for rule in kg.rules:
        if rule.direction not in {"positive", "negative"}:
            report.add(
                "error",
                "invalid_rule_direction",
                f"Rule '{rule.id}' has invalid direction '{rule.direction}' (expected positive|negative)",
                f"rules.{rule.id}.direction",
            )
        if rule.specificity is not None and str(rule.specificity).strip().lower() not in {"low", "moderate", "high"}:
            report.add(
                "warning",
                "invalid_rule_specificity",
                f"Rule '{rule.id}' has invalid specificity '{rule.specificity}' (expected low|moderate|high)",
                f"rules.{rule.id}.specificity",
            )


def _reachable_questions(flow: Flow | None) -> set[str]:
    if flow is None or not flow.start:
        return set()
    reachable: set[str] = set()
    queue = [flow.start]
    while queue:
        question_id = queue.pop()
        if question_id in reachable:
            continue
        reachable.add(question_id)
        for target in flow.transitions.get(question_id, {}).values():
            if target not in reachable:
                queue.append(target)
    return reachable


def _condition_questions(
    kg: KnowledgeGraph,
    condition_id: str,
    cache: dict[str, set[str]],
    _stack: frozenset[str] = frozenset(),
) -> set[str]:
    """Question ids a condition transitively depends on (conditions may reference
    other conditions). Used to decide whether a condition is collectable from a flow."""
    if condition_id in cache:
        return cache[condition_id]
    if condition_id in _stack:
        return set()
    condition = kg.conditions.get(condition_id)
    if condition is None:
        return set()
    questions: set[str] = set()
    for name in _expression_names(condition.expression):
        if name in kg.questions:
            questions.add(name)
        elif name in kg.conditions and name != condition_id:
            questions |= _condition_questions(kg, name, cache, _stack | {condition_id})
    cache[condition_id] = questions
    return questions


def _expression_names(expression: str) -> set[str]:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return {expression}
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _diagnosis_reachable(
    kg: KnowledgeGraph,
    diagnosis_id: str,
    reachable_questions: set[str],
    cond_cache: dict[str, set[str]],
) -> bool:
    for rule in kg.rules:
        if _rule_diagnosis_id(rule.id, rule.diagnosis) != diagnosis_id or rule.is_negative:
            continue
        refs = rule.condition_refs
        if not refs:
            continue
        if all(
            not (qset := _condition_questions(kg, cond_id, cond_cache)) or (qset & reachable_questions)
            for cond_id in refs
        ):
            return True
    return False


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
