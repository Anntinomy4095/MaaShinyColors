"""AgentServer registrations for SC.Present.* (present box bulk receive) and SC.Weekly.Read (read-only).

Implementations live in agent.core.present_actions; actual clicks stay in Pipeline nodes.
"""
from __future__ import annotations

from maa.agent.agent_server import AgentServer

from agent.core import present_actions as pa


@AgentServer.custom_action("SC.Present.Begin")
class PresentBegin(pa.PresentBeginAction):
    """Load options and the private ledger; stop when the task option is off."""


@AgentServer.custom_action("SC.Present.Decide")
class PresentDecide(pa.PresentDecideAction):
    """Read the present page on one frame and decide claim / empty / stop."""


@AgentServer.custom_action("SC.Present.Scan")
class PresentScan(pa.PresentScanAction):
    """After one guarded wheel step: extend the complete row list; decide once every row was read."""


@AgentServer.custom_action("SC.Present.Intent")
class PresentIntent(pa.PresentIntentAction):
    """Frame-bound pending record before the guarded bulk click."""


@AgentServer.custom_action("SC.Present.Result")
class PresentResult(pa.PresentResultAction):
    """Classify the post-press dialog (no input)."""


@AgentServer.custom_action("SC.Present.Confirm")
class PresentConfirm(pa.PresentConfirmAction):
    """Re-read the page after the dialog and finish the record."""


@AgentServer.custom_action("SC.Present.Stop")
class PresentStop(pa.PresentStopAction):
    """Report the stop reason once."""


@AgentServer.custom_action("SC.Weekly.Read")
class WeeklyRead(pa.WeeklyReadAction):
    """Read-only weekly Produce x3 count."""


@AgentServer.custom_recognition("SC.Present.Ready")
class PresentReady(pa.PresentReadyRecognition):
    """The pending attempt and stage must match the exact click."""
