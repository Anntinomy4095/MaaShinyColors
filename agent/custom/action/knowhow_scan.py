"""AgentServer registrations for SC.KnowhowScan.* (K3 read-only ノウハウブック一覧 scan).

Implementations live in agent.core.knowhow_scan_actions; none of them sends input. Every SC.KnowhowScan.* node is
``enabled: false`` and the input steps are ``input_action_missing:*`` placeholders until the action table binds them
. Not imported by agent/main.py yet (optional import).
"""
from __future__ import annotations

from maa.agent.agent_server import AgentServer

from agent.core import knowhow_scan_actions as ksa


@AgentServer.custom_recognition("SC.KnowhowScan.Want")
class KnowhowScanWant(ksa.KnowhowScanWant):
    """Frame-bound scan decision / state (sort steps, wheel, tile, hold, exit, active, finished)."""


@AgentServer.custom_action("SC.KnowhowScan.Begin")
class KnowhowScanBegin(ksa.KnowhowScanBegin):
    """Load options (and an optional private resume record) and start a scan session."""


@AgentServer.custom_action("SC.KnowhowScan.Finish")
class KnowhowScanFinish(ksa.KnowhowScanFinish):
    """At home: private run record, inventory and UI module (partial results flagged)."""


@AgentServer.custom_action("SC.KnowhowScan.Stop")
class KnowhowScanStop(ksa.KnowhowScanStop):
    """Report the stop reason once (what was read is kept, flagged partial)."""


@AgentServer.custom_action("SC.KnowhowScan.Sent")
class KnowhowScanSent(ksa.KnowhowScanSent):
    """After a Want-bound Click/wheel succeeded: consume the decision (the next capture is a new observation)."""


@AgentServer.custom_action("SC.KnowhowScan.Retry")
class KnowhowScanRetry(ksa.KnowhowScanRetry):
    """An input the controller proved it never sent: re-capture and re-recognise (bounded, never after a send)."""
