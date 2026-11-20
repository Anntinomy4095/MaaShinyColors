#!/usr/bin/env python3
"""APPEAL timing results: link each host-timed plan to the host's post-tap burst and read where the cursor stopped.

Read-only, zero input. Plans come from W.I.N.G. (``dev_assets/private/wing_timing/*/<stamp>-plan.json``) and Grade
Fes (``live_timing_plan`` records in ``dev_assets/private/run_logs/grade-*.jsonl``, ``agent/core/grade_timing.py``);
bursts (``dev_assets/private/wing_timing_burst/*/burst.json`` + PNGs, written by the host after every timed tap)
link to a plan by ``requested_down_ms == request.target_down_ms``.

For each tap: judgement (Perfect confetti, else the zone of the stop column), stop error in ms against the aim
(+ = the cursor stopped after the aim, i.e. the tap landed late: raise ``event_offset_ms`` by about that much),
Down lateness, hold (Down->Up) and capture->Down time. The stop column is the one that persists over the burst
frames; when the frames disagree, the candidate nearest the aim is used and marked ``by_aim`` (check the sheet).

    .venv\\Scripts\\python.exe tools\\appeal_timing_report.py [--since 2026-10-24] [--sheet]

Report: ``dev_assets/private/appeal_timing_report/<stamp>.json`` (+ ``.png`` contact sheet), private.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.core import wing_live_timing as wlt  # noqa: E402

PRIVATE = ROOT / "dev_assets" / "private"
PERSIST_PX = 4


def load_plans(private: Path = PRIVATE, since: str | None = None) -> list[dict]:
    """Every staged plan with a host request: {source, at, plan, extra}."""
    plans = []
    for f in sorted((private / "wing_timing").glob("*/*-plan.json")):
        if since and f.parent.name < since.replace("-", ""):
            continue
        try:
            plan = (json.loads(f.read_text(encoding="utf-8")).get("plan") or {}).get("plan") or {}
        except (OSError, ValueError):
            continue
        if plan.get("request"):
            plans.append({"source": "wing", "at": f.name.split("-")[0], "plan": plan, "extra": {}})
    for f in sorted((private / "run_logs").glob("grade-*.jsonl")):
        if since and f.stem[len("grade-"):] < since:
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            plan = record.get("plan") or {}
            if record.get("event") == "live_timing_plan" and plan.get("request"):
                plans.append({"source": "grade", "at": record.get("time"), "plan": plan,
                              "extra": {"turn": record.get("turn"), "fit": record.get("fit"),
                                        "frame_cursor_x": record.get("frame_cursor_x")}})
    return plans


def load_bursts(private: Path = PRIVATE) -> dict[float, Path]:
    bursts = {}
    for f in sorted((private / "wing_timing_burst").glob("*/burst.json")):
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(meta.get("requested_down_ms"), (int, float)):
            bursts[round(meta["requested_down_ms"], 3)] = f
    return bursts


def combine(readings: list[dict | None], aim_x: float | None) -> dict:
    """One verdict from the burst frames' ``read_appeal_result`` readings (oldest first)."""
    readings = [r for r in readings if r]
    if any(r["perfect_confetti"] for r in readings):
        return {"judgement": "Perfect", "stop_x": None, "stop_source": "confetti"}
    candidates = [x for r in readings for x in r["candidates"]]
    if not candidates:
        return {"judgement": None, "stop_x": None, "stop_source": None}
    votes = [(sum(abs(x - y) <= PERSIST_PX for y in candidates), x) for x in candidates]
    best = max(v for v, _ in votes)
    leaders = sorted({x for v, x in votes if v == best})
    if best >= 2 and max(leaders) - min(leaders) <= PERSIST_PX:
        return {"judgement": None, "stop_x": statistics.median(leaders), "stop_source": "persistent"}
    if len(candidates) == 1:
        return {"judgement": None, "stop_x": candidates[0], "stop_source": "single"}
    if aim_x is not None:
        return {"judgement": None, "stop_x": min(candidates, key=lambda x: abs(x - aim_x)), "stop_source": "by_aim"}
    return {"judgement": None, "stop_x": None, "stop_source": "ambiguous"}


def stop_error_ms(stop_x: float, aim_x: float, period_ms: float) -> float:
    """Wrapped stop - aim in ms (+ = late)."""
    return round(((wlt.phase(stop_x) - wlt.phase(aim_x) + 0.5) % 1.0 - 0.5) * period_ms, 1)


def analyse(entry: dict, burst: Path | None, read_image=None) -> dict:
    plan, request = entry["plan"], entry["plan"]["request"]
    zones = [tuple(z) for z in plan.get("zones") or ()]
    row = {"source": entry["source"], "at": entry["at"], **entry["extra"],
           "aim_x": plan.get("aim_x"), "cursor_x": plan.get("cursor_x"), "window_ms": plan.get("window_ms"),
           "layout": "".join(z[0][0] for z in zones), "event_offset_ms": plan.get("event_offset_ms"),
           "period_ms": request.get("period_ms"), "burst": burst.parent.name if burst else None}
    if burst is None:
        return row
    meta = json.loads(burst.read_text(encoding="utf-8"))
    down, up, selected = meta.get("actual_down_ms"), meta.get("actual_up_ms"), meta.get("selected_down_ms")
    if isinstance(down, (int, float)):
        row["capture_to_down_ms"] = round(down - plan.get("capture_estimated_ms", down), 1)
        if isinstance(up, (int, float)):
            row["hold_ms"] = round(up - down, 1)
        if isinstance(selected, (int, float)):
            row["lateness_ms"] = round(down - selected, 1)
    read_image = read_image or _read_rgb
    readings = [wlt.read_appeal_result(read_image(burst.parent / frame["file"]), zones)
                for frame in meta.get("frames", [])[:4]]
    verdict = combine(readings, row["aim_x"])
    if verdict["stop_x"] is not None and zones:
        verdict["judgement"] = wlt.TimingBar(tuple(zones), None).zone_at(verdict["stop_x"])
        if row["aim_x"] is not None and row["period_ms"]:
            row["stop_error_ms"] = stop_error_ms(verdict["stop_x"], row["aim_x"], row["period_ms"])
    row.update(verdict)
    return row


def _read_rgb(path: Path):
    import numpy as np
    from PIL import Image
    return np.asarray(Image.open(path).convert("RGB"))


def summarise(rows: list[dict]) -> dict:
    out = {}
    for source in sorted({r["source"] for r in rows}):
        mine = [r for r in rows if r["source"] == source]
        judged = [r["judgement"] for r in mine if r.get("judgement")]
        errors = [r["stop_error_ms"] for r in mine if r.get("stop_error_ms") is not None and r.get("stop_source") != "by_aim"]
        out[source] = {"taps": len(mine), "with_burst": sum(r["burst"] is not None for r in mine),
                       "judgements": {j: judged.count(j) for j in sorted(set(judged))},
                       "stop_error_ms": {"n": len(errors), "mean": round(statistics.mean(errors), 1) if errors else None,
                                         "sd": round(statistics.pstdev(errors), 1) if len(errors) > 1 else None}}
    return out


def contact_sheet(rows: list[dict], path: Path, private: Path = PRIVATE) -> None:
    from PIL import Image, ImageDraw
    shown = [r for r in rows if r.get("burst")]
    if not shown:
        return
    w, h = 400, 120
    sheet = Image.new("RGB", (w, h * len(shown)), "black")
    draw = ImageDraw.Draw(sheet)
    for i, row in enumerate(shown):
        frame = private / "wing_timing_burst" / row["burst"] / "00.png"
        if frame.exists():
            sheet.paste(Image.open(frame).convert("RGB").crop((520, 440, 920, 560)), (0, i * h))
        for x, colour in ((row.get("aim_x"), "cyan"), (row.get("stop_x"), "red")):
            if x is not None:
                draw.line([(x - 520, i * h), (x - 520, i * h + 18)], fill=colour, width=2)
        draw.text((4, i * h + 100), f"{row['source']} {row['at']} {row.get('judgement')} "
                                    f"err {row.get('stop_error_ms')} {row.get('stop_source')}", fill="white")
    sheet.save(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--since", help="YYYY-MM-DD: only plans from this day on")
    parser.add_argument("--sheet", action="store_true", help="also write a contact sheet of the burst frames")
    args = parser.parse_args(argv)
    bursts = load_bursts()
    rows = [analyse(entry, bursts.get(round(entry["plan"]["request"]["target_down_ms"], 3)))
            for entry in load_plans(since=args.since)]
    summary = summarise(rows)
    for row in rows:
        print(f"{row['source']:5s} {str(row['at'])[:19]:19s} {row['layout']:5s} aim {row['aim_x']} "
              f"-> {row.get('judgement')} stop {row.get('stop_x')} ({row.get('stop_source')}) err {row.get('stop_error_ms')} "
              f"hold {row.get('hold_ms')} late {row.get('lateness_ms')} cap->down {row.get('capture_to_down_ms')}")
    print(json.dumps(summary, ensure_ascii=False))
    out = PRIVATE / "appeal_timing_report"
    out.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    (out / f"{stamp}.json").write_text(json.dumps({"rows": rows, "summary": summary}, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    if args.sheet:
        contact_sheet(rows, out / f"{stamp}.png")
    print("report:", out / f"{stamp}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
