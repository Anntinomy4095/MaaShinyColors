"""Shared popup fallback for the daily tasks (2026-11-23, help dialogs on a new PC).

At load time every waiting node of a daily-task Pipeline (one with a ``next`` list) gets
``[JumpBack]SC.Popup.HelpFallback`` as its last branch: when a first-visit help dialog (assistant
avatar + 閉じる) covers the page the node waits for, it is closed and the node keeps waiting. Being
last, it runs only when none of the node's own branches match, and it checks the small avatar
template first, so an ordinary page costs one template match per poll (the full CloseKnown list
with its OCR made waiting nodes time out). Nodes that already list CloseKnown are left as they are.

The JSON files stay unchanged; the override is applied by ``tools/run_tasks.build_tasker`` for live
runs and offline replays alike.
"""
from __future__ import annotations

import json
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[2] / "assets" / "resource" / "base" / "pipeline"
DAILY_FILES = ("business", "collabo", "grade", "match", "mission_claim", "missions",
               "present", "shop", "startup", "training")
CLOSE_KNOWN = "SC.Popup.CloseKnown"
HELP_FALLBACK = "SC.Popup.HelpFallback"
FALLBACK = "[JumpBack]" + HELP_FALLBACK
# Endpoints and failure routing wait for nothing: no fallback.
TERMINAL_MARKERS = ("FailTask", ".Stop", "Abort", "Diagnostics", "SC.Popup.")


def _target(entry) -> str:
    return entry.split("]", 1)[-1] if isinstance(entry, str) else str(entry.get("name", ""))


def eligible(name: str, node) -> bool:
    if not isinstance(node, dict) or name.startswith("SC.Popup."):
        return False
    nxt = node.get("next") or []
    if not nxt or any(_target(e) == CLOSE_KNOWN for e in nxt):
        return False
    return not all(any(m in _target(e) for m in TERMINAL_MARKERS) for e in nxt)


def override(pipeline_dir: Path = PIPELINE) -> dict:
    """``{node: {"next": [..., FALLBACK]}}`` for every eligible daily-task node."""
    out = {}
    for name in DAILY_FILES:
        data = json.loads((pipeline_dir / f"{name}.json").read_text(encoding="utf-8"))
        for node, spec in data.items():
            if eligible(node, spec):
                out[node] = {"next": list(spec["next"]) + [FALLBACK]}
    return out


def install(resource) -> int:
    nodes = override()
    if not resource.override_pipeline(nodes):
        raise ValueError("弹窗兜底 Pipeline 接入失败")
    return len(nodes)
