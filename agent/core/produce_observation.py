from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Mapping

from agent.core.models import ATTRIBUTES
from agent.core.produce_input import explicit_scenario


_FIELD_NAMES = {
    "SC.Produce.Turn": "turn",
    "SC.Produce.TurnsToExam": "turns_to_exam",
    "SC.Produce.Stamina": "stamina",
    "SC.Produce.MaxStamina": "max_stamina",
    "SC.Produce.Vocal": "vocal",
    "SC.Produce.Dance": "dance",
    "SC.Produce.Visual": "visual",
    "SC.Produce.Mental": "mental",
}
_REQUIRED_FIELDS = set(_FIELD_NAMES) - {"SC.Produce.Mental"}
_CANDIDATE_KEYS = {
    "id", "label", "category", "gains", "stamina_cost", "stamina_recovery",
    "mental_gain", "risk", "priority_bonus", "available", "confidence",
}


def _object(value: Any, name: str, allowed: set[str], required: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    if set(value) - allowed:
        raise ValueError(f"{name} contains unknown fields")
    if required - set(value):
        raise ValueError(f"{name} is missing required fields")
    return value


def _score(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number within [0, 1]")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError(f"{name} must be a finite number within [0, 1]")
    return result


def _image_hash(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise ValueError("image_sha256 must be exactly 64 hexadecimal characters")
    return value.lower()


def build_observed_scenario(
    sample: Any,
    annotation: Any,
    min_score: float = 0.95,
) -> dict[str, Any]:
    """将只读 OCR 数值与当前帧人工核对注释转换为离线建议输入。

    人工注释确认场景、目标和候选可用性；此函数不识别候选或执行输入。
    confidence 取最小 OCR score，作为保守门槛，不代表识别准确率。
    """
    threshold = _score(min_score, "min_score")
    if threshold < 0.95:
        raise ValueError("min_score cannot be below 0.95")
    sample = _object(
        sample, "sample", {"id", "image", "image_sha256", "fields", "passed"},
        {"id", "image", "image_sha256", "fields"},
    )
    for key in ("id", "image"):
        if not isinstance(sample[key], str) or not sample[key].strip():
            raise ValueError(f"sample {key} must be a nonempty string")
    if "passed" in sample and not isinstance(sample["passed"], bool):
        raise ValueError("sample passed must be a boolean")
    sample_hash = _image_hash(sample["image_sha256"])
    annotation = _object(
        annotation, "annotation", {"image_sha256", "scene_confirmed", "targets", "candidates"},
        {"image_sha256", "scene_confirmed", "targets", "candidates"},
    )
    if _image_hash(annotation["image_sha256"]) != sample_hash:
        raise ValueError("annotation image_sha256 does not match sample")
    if annotation["scene_confirmed"] is not True:
        raise ValueError("scene_confirmed must be explicitly true")
    targets = _object(annotation["targets"], "targets", set(ATTRIBUTES), set(ATTRIBUTES))
    fields = sample["fields"]
    if not isinstance(fields, list):
        raise ValueError("sample fields must be an array")
    values: dict[str, int] = {}
    scores: list[float] = []
    seen: set[str] = set()
    for field in fields:
        field = _object(
            field, "OCR field", {"name", "accepted", "text", "score", "box", "reason", "raw_detail"},
            {"name", "accepted", "text", "score", "box"},
        )
        name = field["name"]
        if not isinstance(name, str) or name not in _FIELD_NAMES:
            raise ValueError("unknown Produce OCR field name")
        if name in seen:
            raise ValueError(f"duplicate OCR field: {name}")
        seen.add(name)
        if field["accepted"] is not True:
            raise ValueError(f"OCR field must be accepted: {name}")
        score = _score(field["score"], "OCR score")
        if score < threshold:
            raise ValueError(f"OCR score below min_score: {name}")
        text = field["text"]
        if not isinstance(text, str) or re.fullmatch(r"[0-9]+", text) is None:
            raise ValueError(f"OCR text must be an ASCII nonnegative integer: {name}")
        box = field["box"]
        if not isinstance(box, list) or len(box) != 4 or any(
            isinstance(value, bool) or not isinstance(value, int) for value in box
        ):
            raise ValueError("OCR box must contain four integers")
        x, y, width, height = box
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1280 or y + height > 720:
            raise ValueError("OCR box must be within the canonical 1280x720 canvas")
        values[_FIELD_NAMES[name]] = int(text)
        scores.append(score)
    if _REQUIRED_FIELDS - seen:
        raise ValueError("missing required Produce OCR fields")

    candidates = annotation["candidates"]
    if not isinstance(candidates, list):
        raise ValueError("candidates must be an array")
    for candidate in candidates:
        candidate = _object(candidate, "candidate", _CANDIDATE_KEYS, {"id", "available", "confidence"})
        if "gains" in candidate:
            _object(candidate["gains"], "candidate gains", set(ATTRIBUTES), set())
    state = {key: values[key] for key in ("turn", "turns_to_exam", "stamina", "max_stamina")}
    state.update(
        stats={key: values[key] for key in ATTRIBUTES},
        targets=deepcopy(dict(targets)),
        recognized=True,
        confidence=min(scores),
    )
    if "mental" in values:
        state["mental"] = values["mental"]
    scenario = {"state": state, "candidates": deepcopy(candidates)}
    explicit_scenario(scenario)
    return scenario
