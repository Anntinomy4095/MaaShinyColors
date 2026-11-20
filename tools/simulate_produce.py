#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.models import Scenario  # noqa: E402
from agent.core.produce_input import explicit_scenario  # noqa: E402
from agent.core.produce_policy import PROFILES, ProducePolicy  # noqa: E402
from agent.core.scenarios import default_scenario  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run an offline, no-input Produce decision."
    )
    parser.add_argument("scenario", type=Path, nargs="?")
    parser.add_argument("--sample", action="store_true", help="显式使用内置样例空跑。")
    parser.add_argument("--require-confidence", action="store_true", help="要求真实状态和候选提供置信度及可用性。")
    parser.add_argument("--low-stamina-threshold", type=float, help="绝对体力阈值（例如 50）；省略则保留策略比例阈值。")
    parser.add_argument("--min-confidence", type=float, default=0.95)
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="balanced",
    )
    args = parser.parse_args(argv)

    if args.sample == (args.scenario is not None):
        parser.error("必须指定场景文件或 --sample，且二者互斥。")
    try:
        if args.sample:
            scenario = default_scenario()
        else:
            payload = json.loads(args.scenario.read_text(encoding="utf-8"))
            scenario = (explicit_scenario if args.require_confidence else Scenario.from_dict)(payload)
        decision = ProducePolicy(
            args.profile,
            low_stamina_threshold=args.low_stamina_threshold,
            min_confidence=args.min_confidence,
        ).decide(scenario.state, scenario.candidates)
    except (OSError, TypeError, ValueError) as exc:
        parser.exit(2, f"Produce 输入拒绝：{exc}\n")
    if hasattr(sys.stdout, "reconfigure"):  # piped/redirected output on a cp1252/cp437 console (README step 6)
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
