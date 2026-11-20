"""AgentServer registrations of the Shop task (B4); implementations live in agent.core.shop_custom.

These share ``shop_custom.RUNTIME``, which has NO spend authority: under a PI shell the Shop task can read the
マニー list but every exchange is refused (the task ends ``spend_not_enabled`` without spending). Spending is
only possible through tools/run_tasks.py, whose WebView2 controller owns the one-shot SpendGate.
"""
from __future__ import annotations

from maa.agent.agent_server import AgentServer

from agent.core import shop_custom


@AgentServer.custom_recognition("SC.Shop.State")
class ShopState(shop_custom.ShopStateRecognition):
    """Run phase checks (active / finished / confirm_cancel)."""


@AgentServer.custom_recognition("SC.Shop.Plan")
class ShopPlan(shop_custom.ShopPlanRecognition):
    """Reads the 本日限定 cards and decides the next screen position to open."""


@AgentServer.custom_recognition("SC.Shop.Check")
class ShopCheck(shop_custom.ShopCheckRecognition):
    """交換確認 / 交換結果 verification."""


@AgentServer.custom_action("SC.Shop.Begin")
class ShopBegin(shop_custom.ShopBeginAction):
    """Read the option nodes' configuration and start a fresh run."""


@AgentServer.custom_action("SC.Shop.Finish")
class ShopFinish(shop_custom.ShopFinishAction):
    """Report at the clean Home; success unless the run outcome is a failure."""


@AgentServer.custom_action("SC.Shop.Abort")
class ShopAbort(shop_custom.ShopAbortAction):
    """Timeout / unknown screen: report and fail the task (no input)."""


@AgentServer.custom_action("SC.Shop.SpendError")
class ShopSpendError(shop_custom.ShopSpendErrorAction):
    """交換する without an observed 交換結果: log uncertain (or refused), never retry."""
