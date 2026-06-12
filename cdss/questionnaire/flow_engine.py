from __future__ import annotations

from cdss.knowledge.models import KnowledgeGraph


class FlowEngine:
    def __init__(self, kg: KnowledgeGraph) -> None:
        self.kg = kg

    def start(self, flow_id: str = "abdominal_pain") -> dict[str, str | None]:
        flow = self.kg.flows.get(flow_id)
        return {"next_question": flow.start if flow else None}

    def next_question(
        self,
        question_id: str,
        answer: object,
        flow_id: str | None = None,
    ) -> dict[str, str | None]:
        answer_key = _answer_key(answer)

        if question_id == "q_main_complaint" and answer_key in self.kg.flows:
            return {"next_question": self.kg.flows[answer_key].start}

        flows = [self.kg.flows[flow_id]] if flow_id and flow_id in self.kg.flows else self.kg.flows.values()
        for flow in flows:
            branches = flow.transitions.get(question_id)
            if not branches:
                continue
            return {
                "next_question": branches.get(answer_key)
                or branches.get(str(answer))
                or branches.get("default")
            }
        return {"next_question": None}


def _answer_key(answer: object) -> str:
    if isinstance(answer, bool):
        return "yes" if answer else "no"
    return str(answer).strip().lower()
