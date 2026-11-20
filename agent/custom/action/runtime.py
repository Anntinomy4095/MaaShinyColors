from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from agent.core.produce_input import decide_from_params, parse_params
from agent.core.runtime_state import get_runtime_mode, set_runtime_mode
from agent.core.task_actions import EnvironmentCheckAction


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _params(argv: CustomAction.RunArg) -> dict[str, Any]:
    return parse_params(argv.custom_action_param)


@AgentServer.custom_action("SC.RuntimeMode")
class RuntimeModeAction(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        del context
        try:
            params = _params(argv)
            mode = set_runtime_mode(
                mode=str(params.get("mode", "observe")),
                allow_input=bool(params.get("allow_input", False)),
                require_confirmation=bool(
                    params.get("require_confirmation", True)
                ),
            )
            LOGGER.info("Runtime mode: %s", mode)
            return True
        except (TypeError, ValueError, json.JSONDecodeError):
            LOGGER.exception("Invalid runtime mode")
            return False


@AgentServer.custom_action("SC.Diagnostics")
class DiagnosticsAction(EnvironmentCheckAction):
    """Shared with tools/run_tasks.py via agent.core.task_actions."""


@AgentServer.custom_action("SC.SupervisionCheckpoint")
class SupervisionCheckpointAction(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        del context
        try:
            params = _params(argv)
        except (ValueError, json.JSONDecodeError):
            LOGGER.exception("Invalid supervision checkpoint")
            return False

        requested_input = bool(params.get("allow_input", False))
        runtime = get_runtime_mode()
        if requested_input or runtime.allow_input:
            LOGGER.error(
                "Input request blocked at checkpoint: %s",
                json.dumps(params, ensure_ascii=False),
            )
            return False

        LOGGER.info(
            "Supervision checkpoint: %s",
            json.dumps(params, ensure_ascii=False, sort_keys=True),
        )
        return True


@AgentServer.custom_action("SC.ProduceDecision")
class ProduceDecisionAction(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        del context
        try:
            decision = decide_from_params(argv.custom_action_param)
            LOGGER.info(
                "Produce decision (no input): %s",
                json.dumps(
                    decision.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
            return True
        except (TypeError, ValueError, json.JSONDecodeError):
            LOGGER.exception("Produce decision failed")
            return False
