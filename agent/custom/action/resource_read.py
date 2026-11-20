"""AgentServer registrations for SC.ResourceRead.* (「只读取资源」); implementations in agent.core.resource_read.

Neither sends input: the recognition reads the home counters on the frame Maa captured, the action reports them.
"""
from __future__ import annotations

from maa.agent.agent_server import AgentServer

from agent.core import resource_read as rr


@AgentServer.custom_recognition("SC.ResourceRead.Home")
class ResourceReadHome(rr.ResourceReadHome):
    """Clean home with マニー / 体力 / フェス参加権 read (or given up after a few unreadable frames)."""


@AgentServer.custom_action("SC.ResourceRead.Report")
class ResourceReadReport(rr.ResourceReadReport):
    """Report the counters read on the clean home (display readings only)."""
