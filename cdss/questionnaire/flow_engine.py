from __future__ import annotations

from cdss.knowledge.models import Flow, KnowledgeGraph

# v4 asks symptom-specific questions first, then ONE shared general block. When a
# symptom flow ends, the questionnaire chains into this flow (asked once per patient).
GENERAL_FLOW_ID = "general"


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
        chosen_symptoms: list[str] | None = None,
    ) -> dict[str, str | None]:
        answer_key = _answer_key(answer)

        # Entry question: route into the first selected chief complaint's flow.
        # q_main_complaint may be a single value or a list (multi-symptom intake).
        if question_id == "q_main_complaint":
            chosen = chosen_symptoms or _as_symptom_list(answer)
            for candidate in chosen:
                if candidate in self.kg.flows:
                    return {"next_question": self.kg.flows[candidate].start}
            return {"next_question": None}

        flows = [self.kg.flows[flow_id]] if flow_id and flow_id in self.kg.flows else self.kg.flows.values()
        for flow in flows:
            branches = flow.transitions.get(question_id)
            if not branches:
                continue
            nxt = branches.get(answer_key) or branches.get(str(answer)) or branches.get("default")
            if nxt:
                return {"next_question": nxt}
            break
        # End of a symptom flow → next selected complaint (if any), else the general block.
        return {"next_question": self._after_symptom_flow(question_id, chosen_symptoms)}

    def _after_symptom_flow(self, question_id: str, chosen_symptoms: list[str] | None) -> str | None:
        if chosen_symptoms:
            owning = self._owning_symptom_flow(question_id, chosen_symptoms)
            if owning is not None:
                for next_flow in chosen_symptoms[owning + 1:]:
                    flow = self.kg.flows.get(next_flow)
                    if flow and flow.start:
                        return flow.start
        return self._chain_to_general(question_id)

    def _owning_symptom_flow(self, question_id: str, chosen_symptoms: list[str]) -> int | None:
        for index, flow_id in enumerate(chosen_symptoms):
            flow = self.kg.flows.get(flow_id)
            if flow and question_id in _flow_question_ids(flow):
                return index
        return None

    def _chain_to_general(self, question_id: str) -> str | None:
        general = self.kg.flows.get(GENERAL_FLOW_ID)
        if not general or question_id in _flow_question_ids(general):
            return None
        return general.start


def _flow_question_ids(flow: Flow) -> set[str]:
    ids: set[str] = set()
    if flow.start:
        ids.add(flow.start)
    for source, branches in flow.transitions.items():
        ids.add(source)
        ids.update(target for target in branches.values() if target)
    return ids


def _answer_key(answer: object) -> str:
    if isinstance(answer, bool):
        return "yes" if answer else "no"
    return str(answer).strip().lower()


def _as_symptom_list(answer: object) -> list[str]:
    if isinstance(answer, (list, tuple)):
        return [str(item).strip().lower() for item in answer]
    return [str(answer).strip().lower()] if answer is not None else []
