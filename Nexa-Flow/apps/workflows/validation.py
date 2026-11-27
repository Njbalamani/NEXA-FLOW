from __future__ import annotations

from collections import defaultdict, deque

from django.conf import settings

from .expressions import UnsafeExpression, safe_eval

ACTION_TYPES = {
    "create_task",
    "send_email",
    "http_request",
    "webhook",
    "slack",
    "notification",
    "log",
    "ai",
}
NODE_TYPES = {"trigger", "condition", "action", "delay", "transform", "webhook"}
TRIGGER_TYPES = {"manual", "schedule", "webhook", "api", "event"}


class WorkflowValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_expression(expression: str) -> list[str]:
    try:
        safe_eval(str(expression), {})
        return []
    except UnsafeExpression as exc:
        return [f"Unsafe or invalid condition expression: {exc}"]
    except Exception as exc:  # pragma: no cover - defensive
        return [f"Invalid condition expression: {exc}"]


def _valid_cron_field(pattern: str, low: int, high: int) -> bool:
    parts = str(pattern).strip().split(",")
    if not parts or any(not part.strip() for part in parts):
        return False
    for part in parts:
        base, _, step_text = part.strip().partition("/")
        if step_text and (not step_text.isdigit() or int(step_text) < 1):
            return False
        if base in {"*", "?"}:
            start, end = low, high
        elif "-" in base:
            pieces = base.split("-", 2)
            if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
                return False
            start, end = (int(piece) for piece in pieces)
        elif base.isdigit():
            start = end = int(base)
        else:
            return False
        if not (low <= start <= high and low <= end <= high and start <= end):
            return False
    return True


def validate_schedule_config(config: dict) -> list[str]:
    config = config or {}
    mode = config.get("mode", "interval")
    errors = []
    if mode == "interval":
        try:
            minutes = int(config.get("minutes", 0))
        except (TypeError, ValueError):
            return ["Schedule interval must be a number of minutes."]
        if minutes < 1:
            errors.append("Schedule interval must be at least one minute.")
    elif mode == "cron":
        cron = str(config.get("cron", "")).strip().split()
        ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
        if len(cron) != 5 or not all(
            _valid_cron_field(field, low, high) for field, (low, high) in zip(cron, ranges)
        ):
            errors.append("Cron schedule must contain five valid fields.")
    else:
        errors.append(f"Unsupported schedule mode: {mode}")
    return errors


def validate_definition(definition: dict) -> dict:
    definition = definition or {}
    nodes = definition.get("nodes") or []
    connections = definition.get("connections") or []
    triggers = definition.get("triggers") or []
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(nodes, list) or not isinstance(connections, list) or not isinstance(triggers, list):
        raise WorkflowValidationError(["Workflow definition must contain node, connection and trigger lists."])

    max_nodes = int(settings.NEXAFLOW["MAX_NODES_PER_WORKFLOW"])
    if len(nodes) > max_nodes:
        errors.append(f"A workflow may contain at most {max_nodes} nodes.")
    if not nodes:
        errors.append("A workflow needs at least one node.")

    by_key = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"Node #{index + 1} is not an object.")
            continue
        key = str(node.get("key") or "").strip()
        node_type = str(node.get("type") or "").strip()
        if not key:
            errors.append(f"Node #{index + 1} is missing a key.")
            continue
        if key in by_key:
            errors.append(f"Duplicate node key: {key}")
            continue
        by_key[key] = node
        if node_type not in NODE_TYPES:
            errors.append(f"Node '{key}' has unsupported type '{node_type}'.")
        if not str(node.get("name") or "").strip():
            errors.append(f"Node '{key}' needs a name.")
        config = node.get("config") or {}
        if not isinstance(config, dict):
            errors.append(f"Node '{key}' config must be an object.")
            continue
        if node_type == "action":
            action = str(config.get("action") or "").strip()
            if not action:
                errors.append(f"Action node '{key}' needs an action type.")
            elif action not in ACTION_TYPES:
                errors.append(f"Action node '{key}' uses unsupported action '{action}'.")
        if node_type == "condition":
            if config.get("expression"):
                errors.extend(validate_expression(config["expression"]))
            elif not config.get("field"):
                errors.append(f"Condition node '{key}' needs a field or expression.")
        if node_type == "delay":
            try:
                seconds = int(config.get("seconds", 0))
                if seconds < 0:
                    errors.append(f"Delay node '{key}' cannot have negative seconds.")
            except (TypeError, ValueError):
                errors.append(f"Delay node '{key}' needs numeric seconds.")
        if node_type == "webhook":
            url = str(config.get("url") or "")
            if not url.startswith(("http://", "https://")):
                errors.append(f"Webhook node '{key}' needs an http(s) URL.")

    adjacency = defaultdict(list)
    incoming = defaultdict(int)
    for index, connection in enumerate(connections):
        if not isinstance(connection, dict):
            errors.append(f"Connection #{index + 1} is not an object.")
            continue
        source = str(connection.get("source") or "")
        target = str(connection.get("target") or "")
        if source not in by_key:
            errors.append(f"Connection #{index + 1} references unknown source '{source}'.")
        if target not in by_key:
            errors.append(f"Connection #{index + 1} references unknown target '{target}'.")
        if source == target:
            errors.append("A node cannot connect to itself.")
        if source in by_key and target in by_key:
            adjacency[source].append(target)
            incoming[target] += 1

    for trigger_index, trigger in enumerate(triggers):
        if not isinstance(trigger, dict):
            errors.append(f"Trigger #{trigger_index + 1} is not an object.")
            continue
        trigger_type = str(trigger.get("type") or "manual")
        if trigger_type not in TRIGGER_TYPES:
            errors.append(f"Unsupported trigger type '{trigger_type}'.")
        config = trigger.get("config") or {}
        if trigger_type == "schedule":
            errors.extend(validate_schedule_config(config))

    trigger_nodes = [key for key, node in by_key.items() if node.get("type") == "trigger"]
    has_triggers = bool(triggers) or bool(trigger_nodes)
    if not has_triggers:
        errors.append("A workflow needs at least one trigger.")

    indegree = {key: incoming[key] for key in by_key}
    queue = deque(key for key, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for neighbor in adjacency[current]:
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)
    if visited != len(by_key):
        errors.append("Workflow graph contains a cycle; cycles are not executable.")

    starts = trigger_nodes or [key for key, degree in incoming.items() if degree == 0]
    reachable = set(starts)
    queue = deque(starts)
    while queue:
        current = queue.popleft()
        for neighbor in adjacency[current]:
            if neighbor not in reachable:
                reachable.add(neighbor)
                queue.append(neighbor)
    for key in by_key:
        if key not in reachable:
            warnings.append(f"Node '{key}' is unreachable from any trigger.")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "node_count": len(by_key),
        "connection_count": len(connections),
        "start_keys": starts,
    }


def validate_workflow(workflow) -> dict:
    from .services import workflow_definition

    return validate_definition(workflow_definition(workflow))
