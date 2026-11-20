"""AgentServer registrations of the W.I.N.G. navigation framework (assets/resource/base/pipeline/wing.json).

Rules and decisions live in ``agent.core.wing_nav`` (pure), screen reading in ``agent.core.wing_screen`` and the Maa
glue in ``agent.core.wing_custom``. The AgentServer session is created WITHOUT input authority
(``WingSession(input_enabled=False)``): ``WingNavRuntime.act`` refuses every click intent here, so this path can only
classify, read, decide, log and stop. Live input exists only in the supervised launcher entry (``tools/run_tasks.py``
``Wing``), which builds its own session; every ``SC.Wing.Click.*`` node also stays ``enabled: false`` in the shipped
Pipeline; in the action table only live-verified ``wing-*`` actions are enabled (2026-10-11 review P1-5), and they
are still refused here because this session has no input authority.
"""
from __future__ import annotations

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from agent.core import wing_custom as wc
from agent.core import wing_nav as nav

SESSION = wc.WingSession()
_RECOGNITIONS, _ACTIONS = wc.registrations(SESSION)


def runtime() -> nav.WingNavRuntime:
    return SESSION.runtime


def configure(config, *, owned=None) -> nav.WingNavRuntime:
    """Replace the runtime with one built from the user's Produce config (no input authority)."""
    SESSION.runtime = nav.WingNavRuntime.from_produce_config(config, owned=owned)
    return SESSION.runtime


@AgentServer.custom_recognition("SC.Wing.State")
class WingState(CustomRecognition):
    def analyze(self, context, argv):
        return _RECOGNITIONS["SC.Wing.State"].analyze(context, argv)


@AgentServer.custom_recognition("SC.Wing.Decide")
class WingDecide(CustomRecognition):
    def analyze(self, context, argv):
        return _RECOGNITIONS["SC.Wing.Decide"].analyze(context, argv)


@AgentServer.custom_action("SC.Wing.Begin")
class WingBegin(CustomAction):
    def run(self, context, argv):
        return _ACTIONS["SC.Wing.Begin"].run(context, argv)


@AgentServer.custom_action("SC.Wing.Stop")
class WingStop(CustomAction):
    def run(self, context, argv):
        return _ACTIONS["SC.Wing.Stop"].run(context, argv)


@AgentServer.custom_action("SC.Wing.Finish")
class WingFinish(CustomAction):
    def run(self, context, argv):
        return _ACTIONS["SC.Wing.Finish"].run(context, argv)
