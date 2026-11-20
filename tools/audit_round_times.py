#!/usr/bin/env python3
"""离线审计一轮 next 识别耗时：找出点击节点之前累计识别时间可能超过帧许可（5 s）的等待节点。

Maa 每轮只截一帧，然后按 next 顺序逐个识别，第一个命中者执行；同一锚点在不同节点里会重复识别（无缓存）。
实机点击节点外包一层帧守卫（``agent.core.pipeline_frame_guard``），识别到守卫时若距截图已超过
``agent.core.frame_permit.MAX_AGE_S`` 就不发许可（``frame_stale_before_permit``）并重试——长 next 列表里排在后面的点击
节点于是永远点不到（2026-10-23 ``SC.KnowhowScan.Main`` 一轮约 8.8 s）。

本工具在已保存的 1280x720 原生帧上，对每个 next 含已启用点击节点（``assets/input/pipeline_actions.json``）的节点，
逐个运行 next 中的识别并计时，模拟“该点击节点命中、它之前的节点全部未中”的一轮，得到点击节点识别完成时距截图的累计耗时。

Agent 自定义识别（``SC.*.State`` 等）离线不存在，用桩代替并跑两遍：
- 下界（lower）：桩一律不中，节点在桩处短路——即 Maa 离线回放的行为；
- 上界（upper）：桩一律命中，节点继续跑桩后的模板/OCR——该节点状态门打开时的最大代价。
点击节点自身总按上界计（它要命中，状态门必然打开）。W.I.N.G. ``Pick``（SC.Wing.Decide）与 KnowhowScan ``Want`` 这类
互斥决策门一帧只开一个，下界即实际；上界假设所有门同时打开，只对 ``State`` 类常开门有意义（risk=possible 需人工判断）。在本帧上排在它之前、且不含自定义识别就已命中的节点会先执行，
该点击节点在这帧不可达（计为 shadowed）。

只读、离线：回放控制器拒绝一切输入；输出写入 dev_assets/private/round-audit/。

    .venv\\Scripts\\python.exe tools\\audit_round_times.py                       # 测试引用的原生帧
    .venv\\Scripts\\python.exe tools\\audit_round_times.py "dev_assets/private/knowhow-scan/**/*.png" --parents KnowhowScan
"""
from __future__ import annotations

import argparse
import ast
import glob
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRIVATE = ROOT / "dev_assets" / "private"
DEFAULT_OUT = PRIVATE / "round-audit" / "report.json"
DEFAULT_THRESHOLD_S = 3.5  # 帧许可 5 s 减去截图传输、主机间隔与实机负载余量
SKIP_DIRS = {"recordings", "ui-dock-selftest", "ui-selftest", "game-assets-src", "resource_native", "models",
             "offline-replay", "round-audit", "guard-audit", "browser_tasks", "in_app_browser", "ui-shots",
             "ui-icons-20261008", "ui-icons-20261008b", "ui-style-20261009", "promo"}


def _test_literals():
    sources = sorted((ROOT / "tests").glob("*.py")) + sorted((ROOT / "tools").glob("replay_*.py"))
    for source in sources:
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and 2 < len(node.value) < 200:
                yield node.value.replace("\\", "/")


def test_frames(per_dir=8):
    """1280x720 候选帧：tests/ 与 tools/replay_*.py 中引用的私有 PNG 文件与帧目录（每目录均匀抽 ``per_dir`` 张）。"""
    files, dirs = set(), set()
    for text in set(_test_literals()):
        rel = text[len("dev_assets/private/"):] if text.startswith("dev_assets/private/") else text
        if not rel or rel.startswith(("/", ".")) or ":" in rel:
            continue
        path = PRIVATE / rel
        try:
            if path.suffix.lower() == ".png" and path.is_file():
                files.add(path.resolve())
            elif path.is_dir() and rel.split("/")[0] not in SKIP_DIRS:
                dirs.add(path.resolve())
        except OSError:
            continue
    for folder in sorted(dirs):
        pngs = sorted(p for p in folder.glob("*.png"))
        if not pngs:
            pngs = sorted(p for p in folder.glob("*/*.png"))
        files.update(_spread(pngs, per_dir))
    return sorted(files)


def _spread(items, limit):
    if limit and len(items) > limit:
        step = len(items) / limit
        return [items[int(i * step)] for i in range(limit)]
    return list(items)


def frames_from(patterns, limit):
    files = sorted({Path(f).resolve() for p in patterns for f in glob.glob(p, recursive=True)})
    return _spread(files, limit)


def _custom_names(recognition, data, out, seen=None):
    """Agent custom recognitions a node may run, following And/Or parts and referenced nodes."""
    seen = set() if seen is None else seen
    if isinstance(recognition, str):
        if recognition in seen or recognition not in data:
            return out
        seen.add(recognition)
        recognition = data[recognition]["recognition"]
    if not isinstance(recognition, dict):
        return out
    if recognition.get("type") == "Custom":
        out.add(recognition.get("param", {}).get("custom_recognition"))
    param = recognition.get("param", {})
    for part in param.get("all_of", []) + param.get("any_of", []):
        _custom_names(part, data, out, seen)
    return out


def _next_names(node):
    for item in node.get("next") or []:
        if isinstance(item, dict):
            if not item.get("anchor"):
                yield item["name"]
        elif isinstance(item, str):
            yield re.sub(r"^\[[A-Za-z]+\]", "", item)


def plan(resource, table, parent_filter=None):
    """{parent: [next names]} for nodes whose next list holds an action-table click node. Actions the shipped table
    keeps disabled (W.I.N.G., weekly Auto Produce) are included: a run that enables them uses the same next lists."""
    clicks = set(table.node_actions(include_disabled=True))
    data = {name: resource.get_node_data(name) for name in resource.node_list}
    parents = {}
    for name, node in data.items():
        names = [n for n in _next_names(node) if n in data]
        if any(n in clicks for n in names) and (parent_filter is None or re.search(parent_filter, name)):
            parents[name] = names
    return parents, data, clicks


class _Measure:
    """Times ``context.run_recognition`` per (node, stub mode) on one frame."""

    def __init__(self, data, clicks, mode, runtime_enabled=()):
        self.data, self.clicks, self.mode = data, clicks, mode
        self.runtime_enabled = set(runtime_enabled)
        self.customs = {name: bool(_custom_names(name, data, set())) for name in data}

    def active(self, name):
        """Maa skips a disabled next entry; the launcher enables click nodes and the W.I.N.G. flow at run time."""
        return name in self.clicks or name in self.runtime_enabled or self.data[name].get("enabled", True)

    def run(self, context, image, name, passing):
        if not self.active(name):
            return {"ms": 0.0, "hit": False, "disabled": True}
        override = {name: {"enabled": True}} if name in self.clicks else {}
        self.mode["pass"] = passing
        start = time.perf_counter()
        detail = context.run_recognition(name, image, override)
        ms = (time.perf_counter() - start) * 1000.0
        return {"ms": ms, "hit": bool(detail is not None and detail.hit)}


def simulate(parents, clicks, customs, results):
    """Per parent: full-round costs and, for each click node that hits on this frame, the cumulative cost up to it."""
    rows = []
    for parent, names in parents.items():
        lower = upper = 0.0
        shadow = None
        for index, name in enumerate(names):
            low, high = results[name]
            if name in clicks and high["hit"] and shadow is None:
                rows.append({"parent": parent, "click": name, "index": index,
                             "lower_ms": lower + high["ms"], "upper_ms": max(upper, lower) + high["ms"],
                             "preceding": [(n, results[n][0]["ms"], results[n][1]["ms"]) for n in names[:index]]})
            elif name in clicks and high["hit"]:
                rows.append({"parent": parent, "click": name, "index": index, "shadowed_by": shadow})
            if shadow is None and low["hit"] and not customs[name]:
                shadow = name  # Maa runs this one first on this frame; later nodes are unreachable here
            lower += low["ms"]
            upper += high["ms"]
        rows.append({"parent": parent, "round": True, "lower_ms": lower, "upper_ms": max(upper, lower)})
    return rows


def audit(files, parent_filter=None, progress=True):
    from maa.custom_recognition import CustomRecognition
    from maa.define import LoggingLevelEnum
    from maa.tasker import Tasker
    from PIL import Image

    from agent.core.pipeline_actions import default_table
    from tests.maa_probe import NodeProbe

    class Probe(NodeProbe):
        _resource = None  # own Resource: the custom-recognition stubs must not leak into NodeProbe users

    Tasker.set_stdout_level(LoggingLevelEnum.Off)  # every stub miss logs "recognition is null"
    probe = Probe()
    resource = probe.resource()
    table = default_table()
    parents, data, clicks = plan(resource, table, parent_filter)
    needed = sorted({n for names in parents.values() for n in names})  # stubs cover every custom they reach
    mode = {"pass": False}

    class Stub(CustomRecognition):
        def analyze(self, context, argv):
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="") if mode["pass"] else None

    stub = Stub()
    for name in sorted(set().union(*(_custom_names(n, data, set()) for n in needed)) - {None}):
        resource.register_custom_recognition(name, stub)
    from tools.run_tasks import wing_flow_nodes
    measure = _Measure(data, clicks, mode, wing_flow_nodes())
    current = {}

    class Round(CustomRecognition):
        def analyze(self, context, argv):
            results = {}

            def get(name):
                if name not in results:
                    low = measure.run(context, argv.image, name, False)
                    high = measure.run(context, argv.image, name, True) if measure.customs[name] else low
                    results[name] = low, high
                return results[name]

            active = {}
            for parent, names in parents.items():
                # Only a page whose click node can hit matters; its whole next list is then timed in order.
                if any(get(n)[1]["hit"] for n in names if n in clicks):
                    for name in names:
                        get(name)
                    active[parent] = names
            current["results"], current["active"] = results, active
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="")

    resource.register_custom_recognition("SC.Audit.RoundTimes", Round())
    override = {"SC.Test.Start": {"recognition": "DirectHit", "next": ["SC.Test.Audit"]},
                "SC.Test.Audit": {"recognition": "Custom", "custom_recognition": "SC.Audit.RoundTimes"}}
    rows, skipped, used = [], [], []
    warmed = False
    for index, path in enumerate(files, 1):
        try:
            with Image.open(path) as image:
                if image.size != (1280, 720):
                    skipped.append(str(path))
                    continue
        except OSError:
            skipped.append(str(path))
            continue
        frame = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        if not warmed:  # first OCR call loads the model; do not charge it to any node
            probe.run(frame, override)
            warmed = True
        current.clear()
        probe.run(frame, override)
        for row in simulate(current["active"], clicks, measure.customs, current["results"]):
            row["frame"] = str(frame).replace("\\", "/")
            rows.append(row)
        used.append(str(frame))
        if progress and index % 20 == 0:
            print(f"{index}/{len(files)}", file=sys.stderr, flush=True)
    return {"parents": parents, "clicks": sorted(clicks), "table_disabled": sorted(clicks - set(table.node_actions())),
            "frames": used, "skipped": skipped, "rows": rows}


def summarize(raw, threshold_s=DEFAULT_THRESHOLD_S):
    threshold = threshold_s * 1000.0
    clicks, rounds = {}, {}
    for row in raw["rows"]:
        if row.get("round"):
            item = rounds.setdefault(row["parent"], {"parent": row["parent"], "next": len(raw["parents"][row["parent"]]),
                                                     "max_lower_ms": 0.0, "max_upper_ms": 0.0, "frame": None})
            if row["lower_ms"] > item["max_lower_ms"]:
                item["max_lower_ms"], item["frame"] = row["lower_ms"], row["frame"]
            item["max_upper_ms"] = max(item["max_upper_ms"], row["upper_ms"])
            continue
        key = row["parent"], row["click"]
        item = clicks.setdefault(key, {"parent": row["parent"], "click": row["click"], "index": row["index"],
                                       "samples": 0, "shadowed": 0, "lower": [], "upper": [], "worst": None})
        if "shadowed_by" in row:
            item["shadowed"] += 1
            continue
        item["samples"] += 1
        item["lower"].append(row["lower_ms"])
        item["upper"].append(row["upper_ms"])
        if item["worst"] is None or row["lower_ms"] > item["worst"]["lower_ms"]:
            top = sorted(row["preceding"], key=lambda p: -p[2])[:5]
            item["worst"] = {"frame": row["frame"], "lower_ms": round(row["lower_ms"]),
                             "upper_ms": round(row["upper_ms"]),
                             "top_preceding": [{"node": n, "lower_ms": round(lo), "upper_ms": round(hi)}
                                               for n, lo, hi in top]}
    table = []
    for item in clicks.values():
        if not item["samples"]:
            continue
        lower, upper = item.pop("lower"), item.pop("upper")
        item.update(max_lower_ms=round(max(lower)), median_lower_ms=round(statistics.median(lower)),
                    max_upper_ms=round(max(upper)))
        item["risk"] = ("confirmed" if item["max_lower_ms"] >= threshold
                        else "possible" if item["max_upper_ms"] >= threshold else "ok")
        table.append(item)
    order = {"confirmed": 0, "possible": 1, "ok": 2}
    table.sort(key=lambda i: (order[i["risk"]], -i["max_upper_ms"]))
    reached = {(i["parent"], i["click"]) for i in table}
    enabled = set(raw["clicks"])
    unsampled = [{"parent": p, "click": n, "index": names.index(n)}
                 for p, names in raw["parents"].items() for n in names if (p, n) not in reached and n in enabled]
    round_rows = sorted(rounds.values(), key=lambda r: -r["max_upper_ms"])
    for r in round_rows:
        r["max_lower_ms"], r["max_upper_ms"] = round(r["max_lower_ms"]), round(r["max_upper_ms"])
    return {"threshold_ms": round(threshold), "frames": len(raw["frames"]), "skipped": len(raw["skipped"]),
            "clicks": table, "rounds": round_rows, "unsampled_clicks": unsampled}


def markdown(report):
    lines = [f"# 一轮 next 识别耗时审计", "",
             f"帧 {report['frames']}，阈值 {report['threshold_ms']} ms（下界=自定义识别桩不中；上界=桩命中）。", "",
             "## 点击节点前累计耗时", "",
             "| 风险 | 父节点 | 点击节点 | 序号 | 样本 | 下界最大 ms | 上界最大 ms | 最慢帧 | 最贵前序节点（上界 ms） |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i in report["clicks"]:
        if i["risk"] == "ok" and i["max_upper_ms"] < report["threshold_ms"] * 0.5:
            continue
        top = "；".join(f"{p['node']} {p['upper_ms']}" for p in i["worst"]["top_preceding"][:3])
        lines.append(f"| {i['risk']} | {i['parent']} | {i['click']} | {i['index']} | {i['samples']} | "
                     f"{i['max_lower_ms']} | {i['max_upper_ms']} | {i['worst']['frame']} | {top} |")
    lines += ["", "## 整轮（全部未中）最大耗时", "", "| 父节点 | next 数 | 下界最大 ms | 上界最大 ms | 帧 |",
              "|---|---|---|---|---|"]
    for r in report["rounds"][:40]:
        lines.append(f"| {r['parent']} | {r['next']} | {r['max_lower_ms']} | {r['max_upper_ms']} | {r['frame']} |")
    lines += ["", f"无命中样本的已启用点击节点：{len(report['unsampled_clicks'])} 个（见 report.json `unsampled_clicks`）。", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线审计一轮 next 识别耗时（只读）")
    parser.add_argument("patterns", nargs="*", help="原生帧 glob（1280x720）；缺省为测试引用的私有帧")
    parser.add_argument("--per-dir", type=int, default=8, help="缺省帧集每个帧目录均匀抽取的张数")
    parser.add_argument("--limit", type=int, default=0, help="glob 帧总数上限（均匀抽样，0 不限）")
    parser.add_argument("--parents", help="只审计名字匹配此正则的父节点")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_S, help="风险阈值（秒）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if not out.is_relative_to(PRIVATE.resolve()):
        parser.error("输出须写入 dev_assets/private")
    files = frames_from(args.patterns, args.limit) if args.patterns else test_frames(args.per_dir)
    started = time.time()
    raw = audit(files, args.parents)
    report = summarize(raw, args.threshold)
    report["elapsed_s"] = round(time.time() - started)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_name(out.stem + "-raw.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    out.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    risky = [i for i in report["clicks"] if i["risk"] != "ok"]
    print(json.dumps({"frames": report["frames"], "clicks": len(report["clicks"]), "risky": len(risky),
                      "out": str(out)}, ensure_ascii=False))
    for i in risky[:40]:
        print(f"{i['risk']}\t{i['max_lower_ms']}\t{i['max_upper_ms']}\t{i['parent']} -> {i['click']} (#{i['index']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
