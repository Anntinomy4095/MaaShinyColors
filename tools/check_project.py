#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parent.parent
INTERFACE_PATH = ROOT / "assets" / "interface.json"
POLICY_PATH = ROOT / "project_policy.json"
RESOURCE_ROOT = ROOT / "assets" / "resource"
AGENT_ROOT = ROOT / "agent"
# Templates cropped from private captures live in a gitignored bundle that the runtime loads
# after assets/resource/base; Pipeline names them "native/...".
PRIVATE_TEMPLATE_PREFIX = "native/"
PRIVATE_BUNDLE = Path("dev_assets") / "private" / "resource_native"

INPUT_ACTIONS = {
    "Click",
    "LongPress",
    "Swipe",
    "MultiSwipe",
    "Scroll",
    "ClickKey",
    "LongPressKey",
    "KeyDown",
    "KeyUp",
    "InputText",
    "TouchDown",
    "TouchMove",
    "TouchUp",
}

RECOGNITION_TYPES = {
    "DirectHit",
    "TemplateMatch",
    "FeatureMatch",
    "ColorMatch",
    "OCR",
    "NeuralNetworkClassify",
    "NeuralNetworkDetect",
    "Custom",
    "And",
    "Or",
}


def strip_jsonc_comments(text: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False

    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            index += 1
        elif char == "/" and following == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
        elif char == "/" and following == "*":
            index += 2
            while index + 1 < len(text):
                if text[index] == "*" and text[index + 1] == "/":
                    index += 2
                    break
                if text[index] in "\r\n":
                    result.append(text[index])
                index += 1
        else:
            result.append(char)
            index += 1

    return "".join(result)


def load_jsonc(path: Path) -> Any:
    return json.loads(strip_jsonc_comments(path.read_text(encoding="utf-8")))


def interface_documents(interface_path: Path, interface: Mapping[str, Any] | None = None):
    """[(path, document)] for interface.json followed by its PI v2 ``import`` files, in order."""
    interface = load_jsonc(interface_path) if interface is None else interface
    documents = [(interface_path, interface)]
    for raw in interface.get("import", []) or []:
        path = (interface_path.parent / raw).resolve()
        documents.append((path, load_jsonc(path) if path.is_file() else None))
    return documents


def interface_tasks(interface_path: Path, interface: Mapping[str, Any] | None = None) -> list[Mapping[str, Any]]:
    """All task declarations (interface.json ``task`` first, then imported files, in import order)."""
    tasks: list[Mapping[str, Any]] = []
    for _, document in interface_documents(interface_path, interface):
        if isinstance(document, Mapping):
            tasks.extend(task for task in document.get("task", []) or [] if isinstance(task, Mapping))
    return tasks


def pipeline_files(root: Path = ROOT) -> Iterable[Path]:
    resource_root = root / "assets" / "resource"
    # Maa loads pipeline directories recursively (e.g. base/pipeline/common/*.json).
    yield from sorted(set(resource_root.glob("**/pipeline/**/*.json")))
    yield from sorted(set(resource_root.glob("**/pipeline/**/*.jsonc")))


def registered_custom_actions(root: Path = ROOT) -> set[str]:
    pattern = re.compile(
        r'@AgentServer\.custom_action\(\s*["\']([^"\']+)["\']\s*\)'
    )
    names: set[str] = set()
    for path in (root / "agent").rglob("*.py"):
        names.update(pattern.findall(path.read_text(encoding="utf-8")))
    return names


def referenced_templates(node: Mapping[str, Any]) -> Iterable[str]:
    for key, value in node.items():
        if key == "template":
            if isinstance(value, str):
                yield value
            elif isinstance(value, list):
                yield from (item for item in value if isinstance(item, str))
        elif isinstance(value, Mapping):
            yield from referenced_templates(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    yield from referenced_templates(item)


def _node_references(value: Any) -> tuple[list[str], bool]:
    """Extract Maa node references, allowing JumpBack and Anchor syntax."""
    if isinstance(value, str):
        name = value.strip()
        if not name or name.startswith("[Anchor]"):
            return [], bool(name)
        if name.startswith("[JumpBack]"):
            name = name[len("[JumpBack]") :]
        return [name], bool(name)
    if isinstance(value, Mapping):
        name = value.get("name")
        if not isinstance(name, str) or not name.strip():
            return [], False
        if value.get("anchor") is True:
            return [], True
        name = name.strip()
        if name.startswith("[Anchor]"):
            return [], True
        if name.startswith("[JumpBack]"):
            name = name[len("[JumpBack]") :]
        return [name], bool(name)
    if isinstance(value, list):
        names: list[str] = []
        valid = True
        for item in value:
            item_names, item_valid = _node_references(item)
            names.extend(item_names)
            valid = valid and item_valid
        return names, valid
    return [], False


def _contains_anchor_reference(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().startswith("[Anchor]")
    if isinstance(value, Mapping):
        return value.get("anchor") is True or (
            isinstance(value.get("name"), str)
            and value["name"].strip().startswith("[Anchor]")
        )
    if isinstance(value, list):
        return any(_contains_anchor_reference(item) for item in value)
    return False


def _recognition_type(node: Mapping[str, Any]) -> str:
    recognition = node.get("recognition", "DirectHit")
    if isinstance(recognition, Mapping):
        return str(recognition.get("type", "DirectHit"))
    return str(recognition)


def _risk_decision_exists(root: Path, reference: Any) -> bool:
    """``docs/SAFETY.md#<anchor>`` must name an existing ADR heading about risk acceptance."""
    if not isinstance(reference, str) or "#" not in reference:
        return False
    path, anchor = reference.split("#", 1)
    if path != "docs/SAFETY.md" or not anchor:
        return False
    decisions = root / path
    if not decisions.is_file():
        return False
    for line in decisions.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            slug = "".join(ch for ch in line[3:].strip().lower() if ch.isalnum() or ch in "-_")
            if slug == anchor.lower() and "风险" in line:
                return True
    return False


def _is_positive_guard(
    node: Mapping[str, Any],
    nodes: Mapping[str, tuple[Path, Mapping[str, Any]]] | None = None,
    _depth: int = 0,
) -> bool:
    recognition = node.get("recognition", "DirectHit")
    if isinstance(recognition, Mapping):
        recognition_type = str(recognition.get("type", "DirectHit"))
        param = recognition.get("param", {})
        inverse = isinstance(param, Mapping) and param.get("inverse") is True
        param = param if isinstance(param, Mapping) else {}
    else:
        recognition_type = str(recognition)
        inverse = node.get("inverse", False) is True
        param = node
    if (
        recognition_type not in RECOGNITION_TYPES
        or recognition_type == "DirectHit"
        or inverse
    ):
        return False
    if recognition_type not in ("And", "Or"):
        return True
    # And/Or are positive only through positive sub-recognitions. Maa evaluates a node-name
    # sub-recognition with that node's algorithm only (its own `inverse` is not applied), so
    # inverse or DirectHit sub-nodes never count as evidence here.
    items = param.get("all_of" if recognition_type == "And" else "any_of")
    if not isinstance(items, list) or not items or _depth > 8:
        return False
    verdicts = []
    for item in items:
        if isinstance(item, str):
            target = nodes.get(item) if nodes is not None else None
            verdicts.append(
                target is not None
                and _is_positive_guard(target[1], nodes, _depth + 1)
            )
        elif isinstance(item, Mapping):
            verdicts.append(_is_positive_guard(item, nodes, _depth + 1))
        else:
            verdicts.append(False)
    return any(verdicts) if recognition_type == "And" else all(verdicts)


def _action_type(node: Mapping[str, Any]) -> str:
    action = node.get("action", "DoNothing")
    if isinstance(action, Mapping):
        return str(action.get("type", "DoNothing"))
    return str(action)


@dataclass
class CheckResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    node_count: int = 0
    file_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def _action_table_errors(root: Path, nodes: Mapping[str, tuple[Path, Mapping[str, Any]]]) -> list[str]:
    """Fixed clicks match their catalog rect; Match row clicks use recognized boxes within its column."""
    table_path = root / "assets" / "input" / "pipeline_actions.json"
    base = (root / "assets" / "resource" / "base" / "pipeline").resolve()
    inputs = {name: (path, node) for name, (path, node) in nodes.items()
              if _action_type(node) in INPUT_ACTIONS and Path(path).resolve().is_relative_to(base)}
    if not table_path.is_file():
        return [f"pipeline action table is missing: {table_path}"] if inputs else []
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from agent.core.pipeline_actions import parse_table, is_drag_node, is_match_row_target

    try:
        table = parse_table(table_path.read_bytes())
    except (ValueError, KeyError) as exc:
        return [f"pipeline action table is invalid: {exc}"]
    errors = []
    bound = table.node_actions(include_disabled=True)
    for name, (path, node) in inputs.items():
        if _action_type(node) == "Swipe" and name in bound and is_drag_node(node, table.get(bound[name])):
            continue
        if _action_type(node) != "Click":
            errors.append(f"{name}: base Pipeline input must be a table-bound Click")
            continue
        if name not in bound:
            errors.append(f"{name}: Click node is not bound to any pipeline action")
            continue
        action = table.get(bound[name])
        dynamic_match = is_match_row_target(name, node, action.id)
        if (not dynamic_match and node.get("target") != list(action.rect)) or node.get("target_offset") not in (None, [0, 0, 0, 0]):
            errors.append(f"{name}: target must equal the rect of action {action.id} {list(action.rect)}")
    for name, action_id in bound.items():
        action = table.get(action_id)
        if not action.enabled:
            if name not in nodes:
                continue  # Explicitly disabled catalog entries may reserve future Pipeline nodes.
            if nodes[name][1].get("enabled") is not False:
                errors.append(f"disabled pipeline action {action_id} has an enabled node: {name}")
        if name not in nodes or (_action_type(nodes[name][1]) != "Click"
                                 and not is_drag_node(nodes[name][1], action)):
            errors.append(f"pipeline action {action_id} lists a missing or non-Click node: {name}")
        elif action.drag_rect is not None and _action_type(nodes[name][1]) == "Click":
            errors.append(f"drag action {action_id} must not be clicked: {name}")
    return errors


def inspect_project(root: Path = ROOT) -> CheckResult:
    root = root.resolve()
    interface_path = root / "assets" / "interface.json"
    policy_path = root / "project_policy.json"
    result = CheckResult()
    interface = load_jsonc(interface_path)
    policy = load_jsonc(policy_path)

    # Resource switches describe future opt-ins; they do not enable device input.
    if "resource_usage" in policy:
        usage = policy["resource_usage"]
        hard_blocks = {"premium_currency_allowed", "paid_items_allowed", "other_inventory_allowed"}
        options = {"stamina_items", "fes_tickets"}
        if not isinstance(usage, Mapping) or set(usage) != hard_blocks | options:
            result.errors.append("resource_usage fields are invalid")
        else:
            for key in hard_blocks:
                if usage[key] is not False:
                    result.errors.append(f"resource_usage.{key} must remain false")
            for key in options:
                setting = usage[key]
                if (not isinstance(setting, Mapping) or set(setting) != {"enabled", "max_per_run"}
                        or type(setting["enabled"]) is not bool or type(setting["max_per_run"]) is not int
                        or setting["max_per_run"] < 0
                        or (setting["enabled"] and setting["max_per_run"] == 0)
                        or (not setting["enabled"] and setting["max_per_run"] != 0)):
                    result.errors.append(f"resource_usage.{key} requires an explicit bounded opt-in")

    if interface.get("interface_version") != 2:
        result.errors.append("interface_version must be 2")

    agent = interface.get("agent", {})
    child_args = agent.get("child_args", [])
    script_args = [
        value
        for value in child_args
        if isinstance(value, str) and value.endswith(".py")
    ]
    if not script_args:
        result.errors.append("interface agent has no Python child script")
    else:
        script = (interface_path.parent / script_args[-1]).resolve()
        if not script.exists():
            result.errors.append(f"agent child script does not exist: {script}")

    for resource in interface.get("resource", []):
        for raw_path in resource.get("path", []):
            path = (interface_path.parent / raw_path).resolve()
            if not path.exists():
                result.errors.append(f"resource path does not exist: {path}")

    prefix = str(policy.get("pipeline_prefix", "SC."))
    nodes: dict[str, tuple[Path, Mapping[str, Any]]] = {}
    for path in pipeline_files(root):
        result.file_count += 1
        payload = load_jsonc(path)
        if not isinstance(payload, Mapping):
            result.errors.append(f"pipeline root must be an object: {path}")
            continue
        for name, node in payload.items():
            if name in nodes:
                result.errors.append(
                    f"duplicate node {name}: {nodes[name][0]} and {path}"
                )
                continue
            if not isinstance(node, Mapping):
                result.errors.append(f"node {name} must be an object")
                continue
            nodes[name] = (path, node)
            if name != "Default" and not name.startswith(prefix):
                result.errors.append(
                    f"node {name} must use project prefix {prefix}"
                )

    result.node_count = len(nodes)
    for name, (path, node) in nodes.items():
        for relation in ("next", "on_error"):
            if relation not in node:
                continue
            refs, valid = _node_references(node[relation])
            if not valid:
                result.errors.append(
                    f"{name} has invalid {relation} node reference ({path})"
                )
            for ref in refs:
                if ref not in nodes:
                    result.errors.append(
                        f"{name} references missing {relation} node: {ref} ({path})"
                    )
    documents = interface_documents(interface_path, interface)
    options: set[str] = set()
    presets: list[Mapping[str, Any]] = []
    for path, document in documents[1:]:
        if not isinstance(document, Mapping):
            result.errors.append(f"interface import is missing or not an object: {path}")
    for _, document in documents:
        if isinstance(document, Mapping):
            options.update(document.get("option", {}) or {})
            presets.extend(p for p in document.get("preset", []) or [] if isinstance(p, Mapping))
    task_names: set[str] = set()
    for task in interface_tasks(interface_path, interface):
        entry = task.get("entry")
        if entry not in nodes:
            result.errors.append(f"task entry does not exist: {entry}")
        name = task.get("name")
        if name in task_names:
            result.errors.append(f"duplicate task name: {name}")
        task_names.add(name)
        for option in task.get("option", []) or []:
            if option not in options:
                result.errors.append(f"task {name} references missing option: {option}")
    for preset in presets:
        for item in preset.get("task", []) or []:
            if isinstance(item, Mapping) and item.get("name") not in task_names:
                result.errors.append(
                    f"preset {preset.get('name')} references missing task: {item.get('name')}"
                )

    registrations = registered_custom_actions(root)
    allowed_input_nodes = set(policy.get("allowed_live_input_nodes", []))
    live_input_enabled = bool(policy.get("live_input_enabled", False))
    authorization_required = bool(
        policy.get("live_input_requires_service_authorization", True)
    )
    authorization_reference = policy.get("service_authorization_reference")
    forbidden_actions = set(policy.get("forbidden_actions", []))
    offline_harness = policy.get("offline_harness", {})
    offline_input_nodes = set(
        offline_harness.get("allowed_input_nodes", [])
        if isinstance(offline_harness, Mapping)
        else []
    )

    # Without provider permission, live input may only rest on a recorded user risk decision
    # (an ADR heading in docs/SAFETY.md); it never pretends to be a provider authorization.
    risk_reference = policy.get("risk_acceptance_reference")
    if (
        live_input_enabled
        and authorization_required
        and not authorization_reference
        and not _risk_decision_exists(root, risk_reference)
    ):
        result.errors.append(
            "live input requires a service authorization or a recorded risk acceptance decision"
        )

    resource_paths = [
        (interface_path.parent / raw_path).resolve()
        for resource in interface.get("resource", [])
        for raw_path in resource.get("path", [])
    ]

    private_bundle = root / PRIVATE_BUNDLE
    missing_private: set[str] = set()
    for name, (path, node) in nodes.items():
        action = _action_type(node)
        enabled = node.get("enabled", True) is not False

        if action == "Custom":
            custom_name = node.get("custom_action")
            if custom_name not in registrations:
                result.errors.append(
                    f"{name} references unregistered custom action: {custom_name}"
                )

        if enabled and action in forbidden_actions:
            result.errors.append(
                f"{name} uses forbidden action while enabled: {action}"
            )

        if (
            enabled
            and action in INPUT_ACTIONS
            and (not live_input_enabled or name not in allowed_input_nodes)
        ):
            result.errors.append(
                f"{name} sends input but is not explicitly allowed by policy"
            )

        if action in INPUT_ACTIONS:
            if not _is_positive_guard(node, nodes):
                result.errors.append(
                    f"{name} input action lacks a positive screen-state guard"
                )
            next_refs, next_valid = _node_references(node.get("next", []))
            if not next_valid or not next_refs:
                result.errors.append(
                    f"{name} input action lacks a post-action success guard"
                )
            elif _contains_anchor_reference(node.get("next", [])) or not all(
                ref in nodes and _is_positive_guard(nodes[ref][1], nodes)
                for ref in next_refs
            ):
                result.errors.append(
                    f"{name} input action next path has no positive success guard"
                )
            error_refs, error_valid = _node_references(node.get("on_error", []))
            if not error_valid or not error_refs:
                result.errors.append(
                    f"{name} input action lacks an explicit error recovery path"
                )

        for template in referenced_templates(node):
            if any(
                (root_path / "image" / template).exists()
                for root_path in resource_paths
            ):
                continue
            if template.startswith(PRIVATE_TEMPLATE_PREFIX):
                if not private_bundle.is_dir():
                    missing_private.add(template)
                    continue
                if (private_bundle / "image" / template).is_file():
                    continue
            result.errors.append(
                f"{name} references missing template {template} ({path})"
            )

    result.errors.extend(_action_table_errors(root, nodes))

    for name in sorted(offline_input_nodes):
        if name not in nodes:
            result.errors.append(
                f"offline harness input node does not exist: {name}"
            )
            continue
        _, node = nodes[name]
        if node.get("enabled", True) is not False:
            result.errors.append(
                f"offline harness input node must be disabled in resources: {name}"
            )
        if _action_type(node) not in INPUT_ACTIONS:
            result.errors.append(
                f"offline harness allowlist node is not an input action: {name}"
            )

    if missing_private:
        result.warnings.append(
            f"private template bundle {PRIVATE_BUNDLE.as_posix()} is absent; "
            f"{len(missing_private)} native/ templates were not checked "
            "(run tools/build_native_resource.py locally)"
        )
    if not nodes:
        result.errors.append("no pipeline nodes found")
    if live_input_enabled and not allowed_input_nodes:
        result.warnings.append(
            "live_input_enabled is true but no input nodes are allowlisted"
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check MaaShinyColors project contracts without external packages."
    )
    parser.parse_args(argv)
    try:
        result = inspect_project()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: project check could not run: {exc}")
        return 2

    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for error in result.errors:
        print(f"ERROR: {error}")

    if result.ok:
        print(
            "Project contract OK: "
            f"{result.file_count} pipeline files, {result.node_count} nodes, "
            f"{len(registered_custom_actions(ROOT))} custom actions."
        )
        return 0
    print(f"Project contract failed with {len(result.errors)} error(s).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
