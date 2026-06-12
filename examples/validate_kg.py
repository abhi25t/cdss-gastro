from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cdss.knowledge import KnowledgeGraph, validate
from cdss.knowledge.validator import VALIDATION_PROFILES
from cdss.knowledge.models import ValidationReport


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate CDSS knowledge graph YAML files.")
    parser.add_argument("--kg-version", default="v1", help="Knowledge graph version under knowledge_graph/")
    parser.add_argument("--all", action="store_true", help="Validate every version under knowledge_graph/")
    parser.add_argument("--profile", default="prototype", choices=sorted(VALIDATION_PROFILES), help="Validation strictness profile.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--max-issues", type=int, default=50, help="Maximum issues to print in text mode.")
    args = parser.parse_args()

    versions = _versions() if args.all else [args.kg_version]
    results = [_validate_version(version, args.profile) for version in versions]

    if args.json:
        print(json.dumps([_result_as_dict(version, report, args.profile) for version, report in results], indent=2))
    else:
        _print_text_report(results, args.max_issues, args.profile)

    return 1 if any(not report.is_valid for _, report in results) else 0


def _versions() -> list[str]:
    graph_root = ROOT / "knowledge_graph"
    return sorted(path.name for path in graph_root.iterdir() if path.is_dir())


def _validate_version(version: str, profile: str) -> tuple[str, ValidationReport]:
    kg = KnowledgeGraph.load(ROOT / "knowledge_graph" / version)
    return version, validate(kg, profile=profile)


def _result_as_dict(version: str, report: ValidationReport, profile: str) -> dict[str, object]:
    data = report.as_dict()
    return {"version": version, "profile": profile, **data}


def _print_text_report(results: list[tuple[str, ValidationReport]], max_issues: int, profile: str) -> None:
    print(f"Validation profile: {profile}")
    for index, (version, report) in enumerate(results):
        print()
        status = "PASS" if report.is_valid else "FAIL"
        print(f"{version}: {status} ({len(report.errors)} errors, {len(report.warnings)} warnings)")
        if not report.issues:
            print("  No validation issues.")
            continue

        for issue in report.issues[:max_issues]:
            location = f" [{issue.location}]" if issue.location else ""
            print(f"  - {issue.severity.upper()} {issue.code}{location}: {issue.message}")

        remaining = len(report.issues) - max_issues
        if remaining > 0:
            print(f"  ... {remaining} more issues not shown.")


if __name__ == "__main__":
    raise SystemExit(main())
