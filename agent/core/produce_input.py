from __future__ import annotations

import json
from typing import Any, Mapping

from agent.core.models import ATTRIBUTES, Decision, Scenario
from agent.core.produce_policy import ProducePolicy
from agent.core.scenarios import default_scenario


def parse_params(raw: Any) -> dict[str, Any]:
    """Parse binding parameters without importing MaaFramework."""
    if raw is None or raw == "":
        return {}
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, Mapping):
        raise ValueError("custom_action_param must be an object")
    return dict(value)


def explicit_scenario(payload: Any) -> Scenario:
    """Require observed state and availability evidence at the binding boundary."""
    if not isinstance(payload, Mapping):
        raise ValueError("scenario must be an object")
    state = payload.get("state")
    if not isinstance(state, Mapping):
        raise ValueError("state must be an object")
    for key in ("turn", "turns_to_exam", "stamina", "max_stamina", "stats", "targets", "confidence"):
        if key not in state:
            raise ValueError(f"explicit state requires {key}")
    for key in ("stats", "targets"):
        values = state[key]
        if not isinstance(values, Mapping) or any(name not in values for name in ATTRIBUTES):
            raise ValueError(f"explicit {key} requires vocal, dance and visual")
    scenario = Scenario.from_dict(payload)
    for candidate in payload["candidates"]:
        if "available" not in candidate or "confidence" not in candidate:
            raise ValueError("explicit candidate requires available and confidence")
    return scenario


def decide_from_params(raw: Any) -> Decision:
    params = parse_params(raw)
    sample = params.get("sample", False)
    if not isinstance(sample, bool):
        raise ValueError("sample must be a boolean")
    if "scenario" in params:
        if sample:
            raise ValueError("scenario and sample are mutually exclusive")
        scenario = explicit_scenario(params["scenario"])
    elif sample:
        scenario = default_scenario()
    else:
        raise ValueError("explicit scenario or sample:true is required; no sample fallback")
    profile = params.get("profile", "balanced")
    if not isinstance(profile, str):
        raise ValueError("profile must be a string")
    policy = ProducePolicy(
        profile,
        low_stamina_threshold=params.get("low_stamina_threshold"),
        min_confidence=params.get("min_confidence", 0.95),
    )
    return policy.decide(scenario.state, scenario.candidates)
