"""Pure helpers for SC.Common.FailTask; no Maa imports so they stay unit-testable."""
from __future__ import annotations

import json

MAX_REASON = 120


def fail_reason(raw: object) -> str:
    """Return the bounded reason string from custom_action_param; never raises."""
    try:
        params = json.loads(raw) if isinstance(raw, str) and raw else raw
    except (TypeError, ValueError):
        params = None
    reason = params.get("reason") if isinstance(params, dict) else None
    return reason[:MAX_REASON] if isinstance(reason, str) and reason else "unspecified"
