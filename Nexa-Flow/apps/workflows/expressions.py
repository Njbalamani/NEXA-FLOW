from __future__ import annotations

import ast
import operator
import re
from typing import Any

TEMPLATE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
MISSING = object()
MAX_SEQUENCE_LENGTH = 10_000

_COMPARE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}


class UnsafeExpression(ValueError):
    pass


def resolve_path(path: str, context: dict, default: Any = MISSING) -> Any:
    if not path:
        return default
    if path in context:
        return context[path]
    parts = str(path).split(".")
    current: Any = context
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.lstrip("-").isdigit():
            index = int(part)
            if -len(current) <= index < len(current):
                current = current[index]
            else:
                return default
        else:
            return default
    return current


def render(value: Any, context: dict) -> Any:
    if isinstance(value, str):
        match = TEMPLATE_RE.fullmatch(value.strip())
        if match:
            resolved = resolve_path(match.group(1).strip(), context, "")
            return "" if resolved is MISSING else resolved
        return TEMPLATE_RE.sub(lambda m: str(resolve_path(m.group(1).strip(), context, "")), value)
    if isinstance(value, dict):
        return {key: render(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, context) for item in value]
    return value


def flatten_context(context: dict, max_depth: int = 4) -> dict:
    flat: dict = {}

    def walk(prefix: str, value: Any, depth: int):
        flat[prefix] = value
        if depth >= max_depth or not isinstance(value, dict):
            return
        for key, child in value.items():
            if not isinstance(key, str):
                continue
            walk(f"{prefix}.{key}" if prefix else key, child, depth + 1)

    for key, value in context.items():
        walk(key, value, 0)
    return flat


def _path_from_node(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


class _SafeEvaluator(ast.NodeVisitor):
    def __init__(self, context: dict):
        self.context = flatten_context(context)

    def visit(self, node):
        method = getattr(self, f"visit_{type(node).__name__}", None)
        if method is None:
            raise UnsafeExpression(f"Unsupported expression element: {type(node).__name__}")
        return method(node)

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_Constant(self, node):
        return node.value

    def visit_Name(self, node):
        return self.context.get(node.id)

    def visit_Attribute(self, node):
        path = _path_from_node(node)
        if path is None:
            raise UnsafeExpression("Invalid attribute expression")
        return self.context.get(path)

    def visit_Subscript(self, node):
        base = self.visit(node.value)
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, (str, int)):
            if isinstance(base, dict):
                return base.get(node.slice.value)
            if isinstance(base, (list, tuple)) and isinstance(node.slice.value, int):
                return base[node.slice.value] if -len(base) <= node.slice.value < len(base) else None
        raise UnsafeExpression("Only constant subscripts are allowed")

    def visit_BoolOp(self, node):
        values = [self.visit(value) for value in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        raise UnsafeExpression("Unsupported boolean operator")

    def visit_UnaryOp(self, node):
        value = self.visit(node.operand)
        if isinstance(node.op, ast.Not):
            return not value
        if isinstance(node.op, ast.USub) and isinstance(value, (int, float)):
            return -value
        raise UnsafeExpression("Unsupported unary operator")

    def visit_Compare(self, node):
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            right = self.visit(comparator)
            operation = _COMPARE_OPS.get(type(op))
            if operation is None:
                raise UnsafeExpression(f"Unsupported comparison: {type(op).__name__}")
            try:
                result = operation(left, right)
            except TypeError:
                result = False
            if not result:
                return False
            left = right
        return True

    def visit_BinOp(self, node):
        operation = _BIN_OPS.get(type(node.op))
        if operation is None:
            raise UnsafeExpression("Unsupported arithmetic operator")
        left, right = self.visit(node.left), self.visit(node.right)
        if isinstance(node.op, ast.Mult):
            factors = []
            for value in (left, right):
                if isinstance(value, (str, list, tuple, dict)):
                    factors.append(len(value))
                elif isinstance(value, int) and not isinstance(value, bool):
                    factors.append(abs(value))
            if factors and max(factors) > MAX_SEQUENCE_LENGTH:
                raise UnsafeExpression("Expression would create an oversized sequence.")
        try:
            return operation(left, right)
        except (TypeError, ZeroDivisionError):
            return None

    def visit_List(self, node):
        if len(node.elts) > MAX_SEQUENCE_LENGTH:
            raise UnsafeExpression("Expression contains too many list items.")
        return [self.visit(item) for item in node.elts]

    def visit_Tuple(self, node):
        if len(node.elts) > MAX_SEQUENCE_LENGTH:
            raise UnsafeExpression("Expression contains too many tuple items.")
        return tuple(self.visit(item) for item in node.elts)

    def visit_Dict(self, node):
        if len(node.keys) > MAX_SEQUENCE_LENGTH:
            raise UnsafeExpression("Expression contains too many dict items.")
        return {self.visit(k): self.visit(v) for k, v in zip(node.keys, node.values)}


def safe_eval(expression: str, context: dict) -> Any:
    if not isinstance(expression, str) or len(expression) > 1000:
        raise UnsafeExpression("Expression must be a string under 1000 characters.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpression(f"Invalid expression: {exc.msg}") from exc
    return _SafeEvaluator(context).visit(tree)


def evaluate_condition(config: dict, context: dict) -> bool:
    config = config or {}
    expression = config.get("expression")
    if expression:
        return bool(safe_eval(str(expression), context))

    field = config.get("field")
    if not field:
        return True
    actual = resolve_path(str(render(field, context)), context)
    expected = render(config.get("value"), context)
    operator_name = str(config.get("operator", "eq")).lower()

    if operator_name in {"exists", "is_set"}:
        return actual is not MISSING and actual not in (None, "")
    if operator_name in {"missing", "not_exists"}:
        return actual is MISSING or actual in (None, "")
    if operator_name == "empty":
        return not actual
    if operator_name == "not_empty":
        return bool(actual)
    if actual is MISSING:
        return False

    comparisons = {
        "eq": lambda: actual == expected,
        "ne": lambda: actual != expected,
        "neq": lambda: actual != expected,
        "gt": lambda: actual > expected,
        "gte": lambda: actual >= expected,
        "lt": lambda: actual < expected,
        "lte": lambda: actual <= expected,
        "contains": lambda: str(expected) in str(actual),
        "not_contains": lambda: str(expected) not in str(actual),
        "in": lambda: actual in expected,
        "not_in": lambda: actual not in expected,
    }
    operation = comparisons.get(operator_name)
    if operation is None:
        raise UnsafeExpression(f"Unsupported operator: {operator_name}")
    try:
        return bool(operation())
    except TypeError:
        return False
