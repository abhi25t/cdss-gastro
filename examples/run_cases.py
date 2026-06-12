from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cdss import CDSSPipeline


@dataclass(frozen=True)
class CaseRun:
    case_id: str
    passed: bool
    failures: list[str]
    output: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "failures": self.failures,
            "output": self.output,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CDSS patient case regression checks.")
    parser.add_argument("--case", action="append", help="Case name or JSON path. Repeat to run multiple cases.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    cases = [_load_case(value) for value in args.case] if args.case else _load_all_cases()
    results = [run_case(case) for case in cases]

    if args.json:
        print(json.dumps([result.as_dict() for result in results], indent=2))
    else:
        _print_report(results)

    return 1 if any(not result.passed for result in results) else 0


def run_case(case: dict[str, Any]) -> CaseRun:
    pipeline = CDSSPipeline.from_version(str(case["kg_version"]), ROOT / "knowledge_graph")
    output = pipeline.run(dict(case["answers"])).as_dict()
    failures = _check_expected(case.get("expected", {}), output)
    return CaseRun(
        case_id=str(case.get("id", "<unknown>")),
        passed=not failures,
        failures=failures,
        output=_compact_output(output),
    )


def _load_all_cases() -> list[dict[str, Any]]:
    return [_load_case(path) for path in sorted(_case_root().glob("*.json"))]


def _load_case(case_arg: str | Path) -> dict[str, Any]:
    case_path = _case_path(case_arg)
    with case_path.open("r", encoding="utf-8") as handle:
        case = json.load(handle)
    if "kg_version" not in case:
        raise ValueError(f"Patient case must include kg_version: {case_path}")
    if "answers" not in case or not isinstance(case["answers"], dict):
        raise ValueError(f"Patient case must include an answers object: {case_path}")
    return case


def _case_path(case_arg: str | Path) -> Path:
    path = Path(case_arg)
    if path.exists():
        return path
    if path.suffix != ".json":
        path = path.with_suffix(".json")
    case_path = _case_root() / path.name
    if not case_path.exists():
        raise FileNotFoundError(f"Patient case not found: {case_arg}")
    return case_path


def _case_root() -> Path:
    return ROOT / "examples" / "patient_cases"


def _check_expected(expected: dict[str, Any], output: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    diagnoses = output.get("diagnoses", [])
    top_diagnosis = diagnoses[0] if diagnoses else {}

    if expected.get("top_diagnosis") and top_diagnosis.get("diagnosis") != expected["top_diagnosis"]:
        failures.append(
            f"top diagnosis expected {expected['top_diagnosis']!r}, got {top_diagnosis.get('diagnosis')!r}"
        )

    if expected.get("min_top_score") is not None and int(top_diagnosis.get("score", 0)) < int(expected["min_top_score"]):
        failures.append(
            f"top score expected at least {expected['min_top_score']}, got {top_diagnosis.get('score')!r}"
        )

    for condition_id in expected.get("true_conditions", []):
        if condition_id not in output.get("true_conditions", []):
            failures.append(f"true condition missing: {condition_id}")

    if "red_flags" in expected and output.get("red_flags", []) != expected["red_flags"]:
        failures.append(f"red flags expected {expected['red_flags']!r}, got {output.get('red_flags', [])!r}")

    _check_recommendation_expectations(failures, "investigations", expected, output)
    _check_recommendation_expectations(failures, "treatments", expected, output)
    return failures


def _check_recommendation_expectations(
    failures: list[str],
    key: str,
    expected: dict[str, Any],
    output: dict[str, Any],
) -> None:
    expected_map = expected.get(key, {})
    actual_map = output.get(key, {})
    for bucket, expected_items in expected_map.items():
        actual_items = actual_map.get(bucket, [])
        for item in expected_items:
            if item not in actual_items:
                failures.append(f"{key}.{bucket} missing expected item: {item}")


def _compact_output(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": output["version"],
        "valid": output["validation"]["valid"],
        "true_conditions": output["true_conditions"],
        "red_flags": output["red_flags"],
        "diagnoses": output["diagnoses"],
        "investigations": output["investigations"],
        "treatments": output["treatments"],
    }


def _print_report(results: list[CaseRun]) -> None:
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.case_id}")
        for failure in result.failures:
            print(f"  - {failure}")
    passed = sum(1 for result in results if result.passed)
    print()
    print(f"Summary: {passed} passed, {len(results) - passed} failed")


if __name__ == "__main__":
    raise SystemExit(main())
