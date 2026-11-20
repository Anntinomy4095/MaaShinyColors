"""Shared custom actions for the SC.Common Pipeline nodes."""
from __future__ import annotations

import logging

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from agent.core.task_failure import fail_reason

LOGGER = logging.getLogger(__name__)


@AgentServer.custom_action("SC.FailTask")
class FailTaskAction(CustomAction):
    """Record why the current task stopped and fail it; sends no input.

    Returning False makes this node's action fail, so the task ends as failed instead of
    a silent StopTask success. A UI shell then continues with the next queued task.
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        del context
        LOGGER.warning("SC.Common.FailTask: node=%s reason=%s", argv.node_name,
                       fail_reason(argv.custom_action_param))
        return False
