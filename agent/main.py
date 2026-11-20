from __future__ import annotations

import json
import logging
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def self_check() -> int:
    from agent.core.produce_policy import ProducePolicy
    from agent.core.scenarios import default_scenario

    scenario = default_scenario()
    decision = ProducePolicy("balanced").decide(
        scenario.state,
        scenario.candidates,
    )
    print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
    return 0


def run_agent(socket_id: str) -> int:
    try:
        from maa.agent.agent_server import AgentServer
        from maa.toolkit import Toolkit
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "未安装 MaaFramework Python 绑定。请执行："
            "python -m pip install -r requirements.txt"
        ) from exc

    # Importing registers the decorated custom actions.
    import agent.custom.action.common  # noqa: F401
    import agent.custom.action.runtime  # noqa: F401
    import agent.custom.action.tasks  # noqa: F401
    import agent.custom.action.shop  # noqa: F401
    import agent.custom.action.daily_b5  # noqa: F401
    import agent.custom.action.wing  # noqa: F401  (W.I.N.G. navigation; all nodes disabled, Act refuses input)
    import agent.custom.action.present  # noqa: F401  (プレゼント/周常只读；Intent 不发送输入)
    import agent.custom.action.missions  # noqa: F401  (每日登录单项领取；默认关闭)
    import agent.custom.action.card_scan  # noqa: F401  (持有卡自滚动扫描；全部节点禁用，不发送输入)
    import agent.custom.action.weekly_auto  # noqa: F401  (周常官方 Auto；点击节点全部禁用，无消耗授权)
    import agent.custom.action.resource_read  # noqa: F401  (只读取资源：首页读数，无自己的点击)
    import agent.custom.action.collabo  # noqa: F401  (Collabo Fes；点击/拖动节点全部禁用，无消耗授权)

    Toolkit.init_option(str(PROJECT_ROOT))
    logging.getLogger(__name__).info("Starting MaaShinyColors Agent")
    AgentServer.start_up(socket_id)
    AgentServer.join()
    AgentServer.shut_down()
    logging.getLogger(__name__).info("MaaShinyColors Agent stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = list(sys.argv[1:] if argv is None else argv)

    if args == ["--self-check"]:
        return self_check()
    if len(args) != 1:
        print("Usage:")
        print("  python agent/main.py --self-check")
        print("  python agent/main.py <socket_id>")
        return 2
    return run_agent(args[0])


if __name__ == "__main__":
    raise SystemExit(main())
