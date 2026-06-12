from __future__ import annotations

from cdss.knowledge.models import KnowledgeGraph


class RedFlagEngine:
    def __init__(self, kg: KnowledgeGraph) -> None:
        self.kg = kg

    def detect(self, conditions: dict[str, bool]) -> list[dict[str, str]]:
        flags: list[dict[str, str]] = []
        for flag in self.kg.red_flags:
            if flag.when and all(conditions.get(condition_id, False) for condition_id in flag.when):
                flags.append({"flag": _flag_label(flag.id), "urgency": flag.urgency})
        return flags


def _flag_label(flag_id: str) -> str:
    label = flag_id.replace("_", " ").title()
    return label.replace("Gi ", "GI ")
