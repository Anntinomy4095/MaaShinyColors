"""AgentServer registrations for the B3 PI tasks; implementations live in agent.core.task_actions."""
from __future__ import annotations

from maa.agent.agent_server import AgentServer

from agent.core.task_actions import ClassifyScreenAction, FailureSnapshotAction, MissionStatusReadAction


@AgentServer.custom_action("SC.MissionStatus.Read")
class MissionStatusRead(MissionStatusReadAction):
    """Read-only デイリー row OCR (see agent/core/mission_status.py)."""


@AgentServer.custom_action("SC.Diagnostics.ClassifyScreen")
class DiagnosticsClassifyScreen(ClassifyScreenAction):
    """Read-only screenshot + guard classification."""


@AgentServer.custom_action("SC.Diagnostics.FailureSnapshot")
class DiagnosticsFailureSnapshot(FailureSnapshotAction):
    """Read-only screenshot + page-guard near-miss diagnosis after a failed task."""
