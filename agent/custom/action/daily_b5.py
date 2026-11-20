"""AgentServer registrations of the B5 tasks (Training, GradeFes, MatchSkip).

Implementations live in agent.core.{training,grade,match}_custom. These share each module's ``RUNTIME``, which has
NO spend authority: under a PI shell the tasks navigate and read, but every consuming step ends the task with
``spend_not_enabled``. Spending is only possible through tools/run_tasks.py, whose WebView2 controller owns the
one-shot SpendGate.
"""
from __future__ import annotations

from maa.agent.agent_server import AgentServer

from agent.core import business_custom, grade_custom, match_custom, training_custom
from agent.core.task_custom import RuntimeAction, RuntimeRecognition


def _recognition(runtime, method):
    class Bound(RuntimeRecognition):
        def __init__(self):
            super().__init__(runtime, method)
    return Bound


def _action(runtime, method):
    class Bound(RuntimeAction):
        def __init__(self):
            super().__init__(runtime, method)
    return Bound


# ---- Training
AgentServer.custom_recognition("SC.Training.State")(_recognition(training_custom.RUNTIME, "state"))
AgentServer.custom_recognition("SC.Training.Plan")(_recognition(training_custom.RUNTIME, "plan"))
AgentServer.custom_recognition("SC.Training.Check")(_recognition(training_custom.RUNTIME, "check"))
AgentServer.custom_recognition("SC.Training.List")(_recognition(training_custom.RUNTIME, "list_step"))


@AgentServer.custom_action("SC.Training.Begin")
class TrainingBegin(_action(training_custom.RUNTIME, "begin")):
    """Merge the option nodes' configuration and start a fresh run."""


@AgentServer.custom_action("SC.Training.Finish")
class TrainingFinish(_action(training_custom.RUNTIME, "finish_action")):
    """Report at the clean home; success unless the outcome is a failure."""


@AgentServer.custom_action("SC.Training.Abort")
class TrainingAbort(_action(training_custom.RUNTIME, "abort")):
    """Unknown screen / timeout: report and fail (no input)."""


@AgentServer.custom_action("SC.Training.SpendError")
class TrainingSpendError(_action(training_custom.RUNTIME, "spend_error")):
    """トレーニング開始 without an observed result: log uncertain (or refused), never retry."""


# ---- GradeFes
AgentServer.custom_recognition("SC.Grade.State")(_recognition(grade_custom.RUNTIME, "state"))
AgentServer.custom_recognition("SC.Grade.Plan")(_recognition(grade_custom.RUNTIME, "plan"))
AgentServer.custom_recognition("SC.Grade.Check")(_recognition(grade_custom.RUNTIME, "check"))
AgentServer.custom_recognition("SC.Grade.Watch")(_recognition(grade_custom.RUNTIME, "watch"))


@AgentServer.custom_action("SC.Grade.Begin")
class GradeBegin(_action(grade_custom.RUNTIME, "begin")):
    """Merge the option nodes' configuration and start a fresh run."""


@AgentServer.custom_action("SC.Grade.Finish")
class GradeFinish(_action(grade_custom.RUNTIME, "finish_action")):
    """Report; success unless the outcome is a failure."""


@AgentServer.custom_action("SC.Grade.Abort")
class GradeAbort(_action(grade_custom.RUNTIME, "abort")):
    """Unknown screen / timeout: report and fail (no input)."""


@AgentServer.custom_action("SC.Grade.SpendError")
class GradeSpendError(_action(grade_custom.RUNTIME, "spend_error")):
    """テンション決定 without a recognisable follow-up: log uncertain (or refused), never retry."""


# ---- MatchSkip (sampling by default; skipping only with sample_only off)
AgentServer.custom_recognition("SC.Match.State")(_recognition(match_custom.RUNTIME, "state"))
@AgentServer.custom_recognition("SC.Match.Plan")
class MatchPlan(match_custom.MatchPlanRecognition):
    def __init__(self):
        super().__init__(match_custom.RUNTIME, "plan")

AgentServer.custom_recognition("SC.Match.Sample")(_recognition(match_custom.RUNTIME, "sample"))
AgentServer.custom_recognition("SC.Match.Check")(_recognition(match_custom.RUNTIME, "check"))
AgentServer.custom_recognition("SC.Match.Watch")(_recognition(match_custom.RUNTIME, "watch"))


@AgentServer.custom_action("SC.Match.Begin")
class MatchBegin(_action(match_custom.RUNTIME, "begin")):
    """Merge the option nodes' configuration and start a fresh run."""


@AgentServer.custom_action("SC.Match.Finish")
class MatchFinish(_action(match_custom.RUNTIME, "finish_action")):
    """Report at the clean home."""


@AgentServer.custom_action("SC.Match.Abort")
class MatchAbort(_action(match_custom.RUNTIME, "abort")):
    """Unknown screen / timeout / unrecognised skip dialog: report and fail (no input)."""


@AgentServer.custom_action("SC.Match.SpendError")
class MatchSpendError(_action(match_custom.RUNTIME, "spend_error")):
    """スキップする without a recognisable follow-up: log uncertain (or refused), never retry."""


@AgentServer.custom_action("SC.Match.MarkAbort")
class MatchMarkAbort(_action(match_custom.RUNTIME, "mark_abort")):
    """A non-consuming dialog click failed: switch to クリア + キャンセル."""


@AgentServer.custom_action("SC.Match.Advance")
class MatchAdvance(_action(match_custom.RUNTIME, "advance")):
    """Consume the current dialog decision after a guarded action or a read-only wait."""


AgentServer.custom_recognition("SC.Match.Latest.Plan")(_recognition(match_custom.RUNTIME, "latest_plan"))


@AgentServer.custom_action("SC.Match.Latest.Advance")
class MatchLatestAdvance(_action(match_custom.RUNTIME, "latest_advance")):
    """Read another frame after guarded, non-consuming progress navigation."""


@AgentServer.custom_action("SC.Match.Latest.Error")
class MatchLatestError(_action(match_custom.RUNTIME, "latest_error")):
    """End progress navigation without guessing the latest open stage."""


# 2026-10-31 Match 推进 (agent.core.match_play): one decision per frame, tapped by its SC.Match.Play.* node.
AgentServer.custom_recognition("SC.Match.Play.Check")(_recognition(match_custom.RUNTIME, "play_check"))


@AgentServer.custom_action("SC.Match.Play.Failed")
class MatchPlayFailed(_action(match_custom.RUNTIME, "play_failed")):
    """A non-consuming play tap failed: end the play part (the Skip takes the rights left)."""


@AgentServer.custom_action("SC.Match.Play.SpendError")
class MatchPlaySpendError(_action(match_custom.RUNTIME, "play_spend_error")):
    """ライブ開始 failed: denied before sending ends the play part, anything else is uncertain (no retry)."""


# ---- Business (B6, sampling first: 営業 entry + read-only frames + back; collect/dispatch ends needs_sampling)
AgentServer.custom_recognition("SC.Business.State")(_recognition(business_custom.RUNTIME, "state"))
AgentServer.custom_recognition("SC.Business.Plan")(_recognition(business_custom.RUNTIME, "plan"))
AgentServer.custom_recognition("SC.Business.Check")(_recognition(business_custom.RUNTIME, "check"))


@AgentServer.custom_action("SC.Business.Begin")
class BusinessBegin(_action(business_custom.RUNTIME, "begin")):
    """Merge the option nodes' configuration and start a fresh run."""


@AgentServer.custom_action("SC.Business.Finish")
class BusinessFinish(_action(business_custom.RUNTIME, "finish_action")):
    """Report at the clean home."""


@AgentServer.custom_action("SC.Business.Abort")
class BusinessAbort(_action(business_custom.RUNTIME, "abort")):
    """needs_sampling / unknown screen / no usable back key: report and fail (no input)."""
