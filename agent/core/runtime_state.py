from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class RuntimeMode:
    mode: str = "observe"
    allow_input: bool = False
    require_confirmation: bool = True

    def validate(self) -> "RuntimeMode":
        if self.mode not in {"observe", "confirm_each_action"}:
            raise ValueError(f"unsupported runtime mode: {self.mode}")
        if self.mode == "observe" and self.allow_input:
            raise ValueError("observe mode cannot enable input")
        if self.allow_input and not self.require_confirmation:
            raise ValueError("input requires per-action confirmation")
        return self


_runtime_mode = RuntimeMode()


def get_runtime_mode() -> RuntimeMode:
    return _runtime_mode


def set_runtime_mode(**changes: object) -> RuntimeMode:
    global _runtime_mode
    candidate = replace(_runtime_mode, **changes).validate()
    _runtime_mode = candidate
    return _runtime_mode
