from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cdss.questionnaire import FlowEngine
from cdss.pipeline import CDSSPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a CDSS knowledge graph demo.")
    parser.add_argument("--kg-version", default="v1", help="Knowledge graph version under knowledge_graph/")
    parser.add_argument("--case", help="Patient case name from examples/patient_cases/ or path to a JSON case file.")
    args = parser.parse_args()

    case = _load_case(args.case, args.kg_version)
    kg_version = str(case.get("kg_version") or args.kg_version)
    pipeline = CDSSPipeline.from_version(kg_version, ROOT / "knowledge_graph")

    flow = FlowEngine(pipeline.kg)
    result = pipeline.run(dict(case["answers"]))
    output = result.as_dict()

    output["case"] = {
        "id": case.get("id"),
        "name": case.get("name"),
    }
    output["validation"]["first_issues"] = output["validation"]["issues"][:10]
    del output["validation"]["issues"]
    output["flow"] = _case_flow(case, flow)
    print(json.dumps(output, indent=2))


def _load_case(case_arg: str | None, kg_version: str) -> dict[str, Any]:
    case_path = _case_path(case_arg or _default_case_name(kg_version))
    with case_path.open("r", encoding="utf-8") as handle:
        case = json.load(handle)
    if "answers" not in case or not isinstance(case["answers"], dict):
        raise ValueError(f"Patient case must contain an answers object: {case_path}")
    return case


def _default_case_name(kg_version: str) -> str:
    if kg_version == "v2.1":
        return "v2_1_gerd_generated"
    return "v1_biliary_pain"


def _case_path(case_arg: str) -> Path:
    path = Path(case_arg)
    if path.exists():
        return path
    if path.suffix != ".json":
        path = path.with_suffix(".json")
    case_path = ROOT / "examples" / "patient_cases" / path.name
    if not case_path.exists():
        raise FileNotFoundError(f"Patient case not found: {case_arg}")
    return case_path


def _case_flow(case: dict[str, Any], flow: FlowEngine) -> list[dict[str, object]]:
    steps = case.get("flow") or []
    output: list[dict[str, object]] = []
    for step in steps:
        if step.get("start"):
            flow_id = str(step.get("flow_id", "abdominal_pain"))
            output.append({"flow_id": flow_id, **flow.start(flow_id)})
            continue

        question_id = str(step["question_id"])
        answer = step["answer"]
        flow_id = step.get("flow_id")
        output.append(
            {
                "input": {"question_id": question_id, "answer": answer},
                **flow.next_question(question_id, answer, str(flow_id) if flow_id else None),
            }
        )
    return output


if __name__ == "__main__":
    main()
