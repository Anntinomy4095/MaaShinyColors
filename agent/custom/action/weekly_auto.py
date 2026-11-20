"""AgentServer registrations of the weekly official Auto Produce task (agent/core/weekly_auto.py).

The runtime here has NO spend authority: under a PI shell the task can navigate and read, but the start dialog always
ends with キャンセル / ``spend_not_enabled``. All of its Click nodes and table actions ship disabled.
"""
from __future__ import annotations

from maa.agent.agent_server import AgentServer

from agent.core.task_custom import RuntimeAction, RuntimeRecognition
from agent.core.weekly_auto import WeeklyAutoRuntime

RUNTIME = WeeklyAutoRuntime()


def _recognition(method):
    class Bound(RuntimeRecognition):
        def __init__(self):
            super().__init__(RUNTIME, method)
    return Bound


def _action(method):
    class Bound(RuntimeAction):
        def __init__(self):
            super().__init__(RUNTIME, method)
    return Bound


AgentServer.custom_recognition("SC.WeeklyProduce.Check")(_recognition("check"))
AgentServer.custom_recognition("SC.WeeklyProduce.State")(_recognition("state"))


@AgentServer.custom_action("SC.WeeklyProduce.Begin")
class WeeklyProduceBegin(_action("begin")):
    """Merge the option nodes' configuration and start a fresh run."""


@AgentServer.custom_action("SC.WeeklyProduce.Finish")
class WeeklyProduceFinish(_action("finish_action")):
    """Home shows 「オートプロデュース中」: report success."""


@AgentServer.custom_action("SC.WeeklyProduce.Abort")
class WeeklyProduceAbort(_action("abort")):
    """Stop reason / unknown screen / timeout: report and fail (no input)."""


@AgentServer.custom_action("SC.WeeklyProduce.SpendError")
class WeeklyProduceSpendError(_action("spend_error")):
    """プロデュース開始 / スキップ without an observed result: log uncertain (or refused), never retry."""
