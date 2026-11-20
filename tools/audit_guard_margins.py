#!/usr/bin/env python3
"""离线审计页面守卫余量：在已保存的原生帧上找出“只差一个锚点”的守卫（near miss），提前发现布局变化。

2026-10-22 商店プレミアム页因一个锚点模板带有已消失的「NEW!!」角标边缘而低于阈值，日常全部超时。本工具对一批
1280x720 原生帧逐帧运行 ``agent.core.guard_diagnosis``，按（守卫, 锚点）汇总差一项未命中的次数、最高分与示例帧。
分数接近阈值（如 0.80–0.95）且该页其它锚点全中，多半是模板被页面装饰污染或布局小改，应复核并重裁模板。

只读、离线：不连接宿主，不发送输入；输出写入 dev_assets/private。

    .venv\\Scripts\\python.exe tools\\audit_guard_margins.py "dev_assets/private/run_logs/samples/*/*.png" --limit 600
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRIVATE = ROOT / "dev_assets" / "private"
DEFAULT_OUT = PRIVATE / "guard-audit" / "report.json"


def frames_from(patterns, limit):
    files = sorted({Path(f).resolve() for p in patterns for f in glob.glob(p, recursive=True)})
    if limit and len(files) > limit:  # evenly spaced sample keeps every period represented
        step = len(files) / limit
        files = [files[int(i * step)] for i in range(limit)]
    return files


def audit(files, min_score=0.6, include_untemplated=False):
    from maa.custom_recognition import CustomRecognition
    from PIL import Image

    from agent.core import guard_diagnosis as gd
    from tests.maa_probe import NodeProbe

    nodes = gd.pipeline_nodes()
    guards = gd.page_guards(nodes)
    current = {}

    class Probe(CustomRecognition):
        def analyze(self, context, argv):
            current["report"] = gd.evaluate(context, argv.image, nodes, guards)
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="")  # hit: evaluate once

    probe = NodeProbe()
    probe.resource().register_custom_recognition("SC.Audit.GuardMargins", Probe())
    override = {"SC.Test.Start": {"recognition": "DirectHit", "next": ["SC.Test.Audit"]},
                "SC.Test.Audit": {"recognition": "Custom", "custom_recognition": "SC.Audit.GuardMargins"}}
    summary, skipped = {}, []
    for index, path in enumerate(files, 1):
        with Image.open(path) as image:
            if image.size != (1280, 720):
                skipped.append(str(path))
                continue
        current.clear()
        probe.run(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path, override)
        report = current.get("report") or {}
        for item in report.get("near_miss", []):
            if item["score"] is None and not include_untemplated:
                continue  # OCR/colour anchors carry no score: a miss there is mostly another page
            if item["score"] is not None and item["score"] < min_score:
                continue
            key = f"{item['guard']} / {item['anchor']}"
            row = summary.setdefault(key, {"guard": item["guard"], "anchor": item["anchor"],
                                           "threshold": item["threshold"], "count": 0, "max_score": None,
                                           "examples": []})
            row["count"] += 1
            if item["score"] is not None and (row["max_score"] is None or item["score"] > row["max_score"]):
                row["max_score"] = item["score"]
            if len(row["examples"]) < 5:
                row["examples"].append({"frame": str(path), "score": item["score"]})
        if index % 50 == 0:
            print(f"{index}/{len(files)}", file=sys.stderr, flush=True)
    rows = sorted(summary.values(), key=lambda r: (-(r["max_score"] or 0), -r["count"]))
    return {"frames": len(files) - len(skipped), "skipped": skipped, "guards": len(guards), "near_miss": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线审计页面守卫余量（只读）")
    parser.add_argument("patterns", nargs="+", help="原生帧 glob（1280x720）")
    parser.add_argument("--limit", type=int, default=600)
    parser.add_argument("--min-score", type=float, default=0.6, help="只报告分数不低于此值的模板锚点（非模板锚点总报告）")
    parser.add_argument("--include-untemplated", action="store_true", help="也报告 OCR/颜色锚点的差一项（噪声较多）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if not out.is_relative_to(PRIVATE.resolve()):
        parser.error("输出须写入 dev_assets/private")
    report = audit(frames_from(args.patterns, args.limit), args.min_score, args.include_untemplated)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"frames": report["frames"], "near_miss_rows": len(report["near_miss"]), "out": str(out)},
                     ensure_ascii=False))
    for row in report["near_miss"][:30]:
        print(f"{row['max_score']}\t{row['count']}\t{row['guard']} / {row['anchor']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
