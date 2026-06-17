from __future__ import annotations

import ast
from typing import Any

from cdss.knowledge.models import KnowledgeGraph


class ConditionEngine:
    def __init__(self, kg: KnowledgeGraph) -> None:
        self.kg = kg

    def evaluate(self, answers: dict[str, Any]) -> dict[str, bool]:
        context = {key: _normalize_answer(value) for key, value in answers.items()}
        conditions: dict[str, bool] = {}
        for condition in self.kg.conditions.values():
            merged_context = {**context, **conditions}
            conditions[condition.id] = _safe_eval(condition.expression, merged_context)
        return conditions


def _normalize_answer(value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes"}:
            return "yes"
        if lowered in {"false", "no"}:
            return "no"
        return lowered
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        # Multi-select answers (multi_choice / region_select / bristol_select) stay a
        # list; normalize each element so membership comparisons match condition values.
        return [_normalize_answer(item) for item in value]
    return value


def _safe_eval(expression: str, context: dict[str, Any]) -> bool:
    try:
        node = ast.parse(expression, mode="eval")
    except SyntaxError:
        return bool(context.get(expression, False))
    return bool(_eval_node(node.body, context))


def _eval_node(node: ast.AST, context: dict[str, Any]) -> Any:
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(value, context) for value in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval_node(node.operand, context)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, context)
            if isinstance(op, ast.Eq) and not _eq(left, right):
                return False
            if isinstance(op, ast.NotEq) and _eq(left, right):
                return False
            if not isinstance(op, (ast.Eq, ast.NotEq)):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        return context.get(node.id, False)
    if isinstance(node, ast.Constant):
        return node.value
    return False


def _eq(left: Any, right: Any) -> bool:
    """Equality that treats `list == scalar` (and vice-versa) as membership, so a
    multi-select answer satisfies `field == 'value'` when that value was chosen."""
    if isinstance(left, (list, tuple, set)) and not isinstance(right, (list, tuple, set)):
        return right in left
    if isinstance(right, (list, tuple, set)) and not isinstance(left, (list, tuple, set)):
        return left in right
    return left == right
