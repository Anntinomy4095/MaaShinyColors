"""AgentServer registrations of the Collabo Fes task (agent/core/collabo_task.py, collabo_custom.py).

Under a PI shell the runtime has no spend authority: テンション決定 ends the run with ``spend_not_enabled``. All of its
Click/Swipe nodes and table actions ship disabled; ``tools/run_tasks.py`` enables them for a supervised run.
"""
from __future__ import annotations

from maa.agent.agent_server import AgentServer

from agent.core import collabo_custom
from agent.core.task_custom import RuntimeAction, RuntimeRecognition

RUNTIME = collabo_custom.RUNTIME


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


AgentServer.custom_recognition("SC.Collabo.State")(_recognition("state"))
AgentServer.custom_recognition("SC.Collabo.Ended")(_recognition("ended"))


@AgentServer.custom_recognition("SC.Collabo.Check")
class CollaboCheck(collabo_custom.CollaboCheckRecognition):
    def __init__(self):
        super().__init__(RUNTIME, "check")


@AgentServer.custom_action("SC.Collabo.Begin")
class CollaboBegin(_action("begin")):
    """Merge the option nodes' configuration and start a fresh run."""


@AgentServer.custom_action("SC.Collabo.Finish")
class CollaboFinish(_action("finish_action")):
    """Back on the home screen: report."""


@AgentServer.custom_action("SC.Collabo.Abort")
class CollaboAbort(_action("abort")):
    """Unknown screen / timeout / halt: report and fail (no input)."""


@AgentServer.custom_action("SC.Collabo.Failed")
class CollaboFailed(_action("failed")):
    """A non-consuming tap failed: stop, never tap again."""


@AgentServer.custom_action("SC.Collabo.TensionError")
class CollaboTensionError(_action("tension_error")):
    """テンション決定 without an observed result: refused before send ends cleanly, anything else is uncertain."""
