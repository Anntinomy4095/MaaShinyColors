"""AgentServer registrations for SC.CardScan.* (read-only self-scrolling アイドル一覧 scan).

Implementations live in agent.core.card_scan_actions; none of them sends input. Click nodes stay
``enabled: false`` until the supervised entry enables their action-table bindings.
"""
from __future__ import annotations

from maa.agent.agent_server import AgentServer

from agent.core import card_scan_actions as csa


@AgentServer.custom_recognition("SC.CardScan.Want")
class CardScanWant(csa.CardScanWant):
    """Step-bound scan decision / state (tab tap, wheel step, hold, exit, active, finished)."""


@AgentServer.custom_action("SC.CardScan.Begin")
class CardScanBegin(csa.CardScanBegin):
    """Load options and start a scan session."""


@AgentServer.custom_action("SC.CardScan.Advance")
class CardScanAdvance(csa.CardScanAdvance):
    """Permit the next observation only after the guarded step or hold has completed."""


@AgentServer.custom_action("SC.CardScan.Finish")
class CardScanFinish(csa.CardScanFinish):
    """At home: private run record; complete scans also refresh the reviewed-import files."""


@AgentServer.custom_action("SC.CardScan.Stop")
class CardScanStop(csa.CardScanStop):
    """Report the stop reason once (partial results stay private)."""
