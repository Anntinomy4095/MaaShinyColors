"""从严格人工 JSON 生成新的只读日常计划报告，不调用 Maa 或设备。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.daily_config import load_json, plan_daily_tasks


def main(argv: list[str] | None = None) -> int:
    # Chinese messages must not crash on a cp1252 console or captured pipe.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "daily_tasks.json", help="日常计划合同 JSON")
    parser.add_argument("--observations", type=Path, help="人工观测 JSON，source 必须为 manual")
    parser.add_argument("--output", type=Path, required=True, help="新报告路径；禁止覆盖任何已有文件")
    args = parser.parse_args(argv)
    try:
        inputs = {args.config.resolve()}
        if args.observations:
            inputs.add(args.observations.resolve())
        if args.output.resolve() in inputs:
            raise ValueError("输出不得覆盖配置或人工观测输入")
        report = plan_daily_tasks(load_json(args.config), load_json(args.observations) if args.observations else {})
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    except (OSError, ValueError) as exc:
        print(f"日常计划生成失败：{exc}", file=sys.stderr)
        return 2
    print(f"已生成只读日常计划：{args.output.resolve()}；实时输入仍关闭。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
