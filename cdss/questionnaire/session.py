from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cdss.questionnaire.flow_engine import FlowEngine


@dataclass
class QuestionnaireSession:
    flow_engine: FlowEngine
    flow_id: str = "abdominal_pain"
    answers: dict[str, Any] = field(default_factory=dict)
    current_question: str | None = None

    def start(self) -> dict[str, str | None]:
        response = self.flow_engine.start(self.flow_id)
        self.current_question = response["next_question"]
        return response

    def submit(self, question_id: str, answer: Any) -> dict[str, str | None]:
        self.answers[question_id] = answer
        response = self.flow_engine.next_question(question_id, answer, self.flow_id)
        self.current_question = response["next_question"]
        return response
