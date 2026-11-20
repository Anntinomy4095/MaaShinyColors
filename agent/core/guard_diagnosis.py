"""Read-only page-guard diagnosis: which guards hit, and which miss by exactly one anchor (``near_miss``).

2026-10-22 live: the shop opened on プレミアム in a new layout; one of four anchors of ``SC.Shop.Premium.Guard``
(PremiumSelected, whose template carried the edge of a vanished 「NEW!!」 badge) fell below its threshold, the Shop
task timed out and every later task started on that unknown page. A near miss names the guard and the failing anchor
(with its best template score), so such a layout change is diagnosed from one frame.

Used by ``SC.Diagnostics.FailureSnapshot`` (after a failed task, before the next one) and by
``tools/audit_guard_margins.py`` (offline over saved native frames). Never clicks.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = ROOT / "assets" / "resource" / "base" / "pipeline"
MIN_ANCHORS = 3          # guards with fewer components are too small for a meaningful "all but one"
DEFAULT_BUDGET_S = 20.0  # live failure snapshot: never hold the run longer than this


def _load(path):
    text = Path(path).read_text(encoding="utf-8")
    return json.loads(text)


def pipeline_nodes(pipeline_dir=PIPELINE_DIR):
    nodes = {}
    for path in sorted(Path(pipeline_dir).rglob("*.json")):
        for name, node in _load(path).items():
            if isinstance(node, dict):
                nodes.setdefault(name, node)
    return nodes


def page_guards(nodes):
    """{guard: [component, ...]}: ``*.Guard`` And-nodes of >= MIN_ANCHORS named components, none of them Custom
    (a Custom step needs its task runtime and cannot be judged from one frame)."""
    def custom(name, depth=0):
        node = nodes.get(name) or {}
        if node.get("recognition") == "Custom":
            return True
        if depth > 4:
            return False
        return any(custom(c, depth + 1) for c in (node.get("all_of") or []) + (node.get("any_of") or [])
                   if isinstance(c, str))

    guards = {}
    for name, node in nodes.items():
        parts = node.get("all_of")
        if (not name.endswith(".Guard") or node.get("recognition") != "And" or not isinstance(parts, list)
                or len(parts) < MIN_ANCHORS or not all(isinstance(p, str) and p in nodes for p in parts)):
            continue
        if any(custom(p) for p in parts):
            continue
        guards[name] = list(parts)
    return guards


CHEAP = ("TemplateMatch", "ColorMatch")


def evaluate(context, image, nodes, guards, budget_s=None, clock=time.monotonic):
    """Each needed component once: hit, and for TemplateMatch the best score (also below its threshold).

    Cheap components (template/colour) of every guard run first; the costly ones (OCR, nested Or/And) only for guards
    that can still be a hit or a near miss (at most one cheap component missing).

    Returns {"hits": [guard...], "near_miss": [{guard, anchor, score, threshold}], "evaluated": n, "complete": bool}."""
    started = clock()
    results, complete = {}, True

    def run(part):
        node = nodes.get(part) or {}
        if node.get("recognition") == "TemplateMatch":
            threshold = node.get("threshold", 0.7)
            threshold = max(threshold) if isinstance(threshold, list) else threshold
            detail = context.run_recognition(part, image)  # all_results keeps the best score below threshold
            return (bool(detail is not None and detail.hit), _score(detail), threshold)
        detail = context.run_recognition(part, image)
        return (bool(detail is not None and detail.hit), None, None)

    def cheap(part):
        return (nodes.get(part) or {}).get("recognition") in CHEAP

    for costly in (False, True):
        for guard, parts in guards.items():
            if costly:
                missing = [p for p in parts if cheap(p) and not results.get(p, (True,))[0]]
                if len(missing) > 1:
                    continue  # neither a hit nor a near miss, whatever the costly parts read
            for part in parts:
                if part in results or cheap(part) == costly:
                    continue
                if budget_s is not None and clock() - started > budget_s:
                    complete = False
                    break
                results[part] = run(part)
            if not complete:
                break
        if not complete:
            break
    hits, near = [], []
    for guard, parts in guards.items():
        if any(p not in results for p in parts):
            continue
        missing = [p for p in parts if not results[p][0]]
        if not missing:
            hits.append(guard)
        elif len(missing) == 1:
            _, score, threshold = results[missing[0]]
            near.append({"guard": guard, "anchor": missing[0],
                         "score": None if score is None else round(score, 3), "threshold": threshold})
    near.sort(key=lambda n: -(n["score"] if n["score"] is not None else 0))
    return {"hits": hits, "near_miss": near, "evaluated": len(results), "complete": complete}


def _score(detail):
    if detail is None:
        return None
    best = getattr(detail, "best_result", None)
    score = getattr(best, "score", None) if best is not None else None
    if score is None:
        for item in getattr(detail, "all_results", None) or []:
            value = getattr(item, "score", None)
            if value is not None and (score is None or value > score):
                score = value
    return None if score is None else float(score)


LAYOUT_HINT_MIN_SCORE = 0.6  # a template anchor this close, on a frame no page guard recognises, suggests a layout change


def layout_hint(report):
    """{page, anchor, score} for the player-facing note 「画面与已知布局不一致」, or None.

    Only when no page guard hit (a recognised page means the near miss is just a similar page) and the best near miss
    is a template anchor scoring >= LAYOUT_HINT_MIN_SCORE (an OCR/colour miss carries no score and is mostly another page)."""
    if not report or report.get("hits"):
        return None
    for item in report.get("near_miss", []):
        score = item.get("score")
        if score is None or score < LAYOUT_HINT_MIN_SCORE:
            continue
        guard = item["guard"]
        page = guard[3:] if guard.startswith("SC.") else guard
        page = page[:-6] if page.endswith(".Guard") else page
        return {"page": page, "anchor": item["anchor"], "score": score}
    return None


def summary_lines(report, limit=3):
    """Short codes for a run record, e.g. ``near_miss:SC.Shop.Premium.Guard/SC.Shop.Anchor.Premium.PremiumSelected@0.91``."""
    out = []
    for item in report.get("near_miss", [])[:limit]:
        score = "" if item["score"] is None else f"@{item['score']}"
        out.append(f"near_miss:{item['guard']}/{item['anchor']}{score}")
    return out
