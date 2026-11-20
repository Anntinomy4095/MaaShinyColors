"""Turn task reports into stored readings. Zero extra OCR/captures/clicks.

Every task already reads its own page (Shop money/offers, Training ticket count, Match/Grade rights, mission rows,
present rows, W.I.N.G. hub). :class:`ReportTap` listens to the report sink and persists those values with source and
time through :class:`agent.core.reading_store.ReadingStore`. Values are display/plan references only; a task never
takes spend authority from them. Anything that is not a clean int/short string is dropped, never guessed.
"""
from __future__ import annotations

import logging
import re

from agent.core import reading_store as rs

LOGGER = logging.getLogger(__name__)
MAX_ROWS = 12
FES_RIGHTS = re.compile(r"^([0-9]{1,2})/([0-9]{1,2})$")


def _int(value, high=10**9):
    return value if type(value) is int and 0 <= value <= high else None


def _short(value, limit=60):
    return value[:limit] if isinstance(value, str) and value else None


def _pair(value):
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(type(v) is int and 0 <= v <= 99 for v in value):
        return list(value)
    match = FES_RIGHTS.match(value) if isinstance(value, str) else None
    return [int(match[1]), int(match[2])] if match else None


def _decisions(report):
    return [d for d in report.get("decisions") or [] if isinstance(d, dict)]


def _walk(decision):
    """The decision dict and its one-level nested dicts (shop plan, main_observation)."""
    yield decision
    for value in decision.values():
        if isinstance(value, dict):
            yield value


def _last(report, key, convert):
    found = None
    for decision in _decisions(report):
        for part in _walk(decision):
            value = convert(part.get(key))
            if value is not None:
                found = value
    return found


def _rights(report):
    """Last trustworthy Fes rights pair of a Match/Grade report (the newest reading, after > current > before)."""
    for key in ("rights_after", "rights", "rights_before"):
        pair = _last(report, key, _pair)
        if pair is not None and 1 <= pair[1] and pair[0] <= pair[1]:
            return pair
    return None


def _fes(report):
    pair = _rights(report)
    if pair is None:
        return []
    return [("resource.fes_rights", {"count": pair[0], "max": pair[1]}, 1000)]


def resources(report):
    out = []
    for name, item in (report.get("items") or {}).items() if isinstance(report.get("items"), dict) else ():
        count = _int(item.get("count")) if isinstance(item, dict) else None
        if count is None or not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", str(name)):
            continue
        value = {"count": count}
        for extra in ("max", "plus"):
            if _int(item.get(extra)) is not None:
                value[extra] = item[extra]
        if item.get("indicator_only") is True:
            value["indicator_only"] = True
        key = "resource.fes_rights" if name == "fes" else f"resource.{name}"
        out.append((key, value, _int(item.get("confidence_pm"), 1000)))
    gacha = gacha_hint(report.get("gacha"))
    if gacha is not None:
        out.append(("gacha.home", gacha, None))
    return out


GACHA_STATES = ("free", "no_tag")
GACHA_KINDS = (None, "free", "free_daily", "free_ten", "free_single")


def gacha_hint(value):
    """Home ガシャ tag read (agent.core.gacha_hint): only a definite state with a known kind; display reminder only."""
    if not isinstance(value, dict) or value.get("state") not in GACHA_STATES or value.get("kind") not in GACHA_KINDS:
        return None
    if (value["state"] == "free") != (value["kind"] is not None):
        return None
    return {"state": value["state"], "kind": value["kind"]}


def shop(report):
    out = []
    money = _last(report, "money_after", _int)
    money = money if money is not None else _last(report, "money", _int)
    if money is not None:
        out.append(("resource.money", {"count": money}, 1000))
    visible = _last(report, "visible", lambda v: [[_short(n), _int(p)] for n, p in v][:6]
                    if isinstance(v, list) and all(isinstance(r, (list, tuple)) and len(r) == 2 for r in v) else None)
    bought = [r for r in report.get("bought_ranks") or [] if r in (1, 2, 3)]
    if visible is not None or bought or report.get("outcome") in ("bought", "nothing_to_do", "skipped_rules"):
        value = {"bought_ranks": sorted(set(bought)), "outcome": _short(report.get("outcome"))}
        if visible is not None:
            value["visible"] = visible
        out.append(("shop.offers", value, None))
    return out


def training(report):
    out = []
    count = None
    for decision in _decisions(report):
        ocr = decision.get("ocr")
        if isinstance(ocr, dict) and isinstance(ocr.get("inventory"), str) and re.fullmatch(r"[0-9]{1,6}", ocr["inventory"]):
            count = int(ocr["inventory"])
    if count is not None:
        out.append(("resource.bronze_ticket", {"count": count}, 1000))
    return out


def match(report):
    out = list(_fes(report))
    observation = _last(report, "main_observation", lambda v: v if isinstance(v, dict) else None)
    if observation:
        areas = [{"area_id": a.get("area_id"), "difficulty": a.get("difficulty"), "achievement": a.get("achievement")}
                 for a in observation.get("areas") or [] if isinstance(a, dict) and _int(a.get("area_id")) is not None][:6]
        deadline = observation.get("ranking_deadline")
        value = {"areas_visible": areas, "stage_list_complete": False}
        if isinstance(deadline, dict) and all(_int(deadline.get(k)) is not None for k in ("month", "day", "hour", "minute")):
            value["ranking_deadline"] = {k: deadline[k] for k in ("month", "day", "hour", "minute")}
        out.append(("match.main", value, None))
    return out


def mission_status(report):
    if report.get("ok") is not True or not isinstance(report.get("page"), str) \
            or not re.fullmatch(r"mission_[a-z]{1,12}", report["page"]):
        return []
    counter = report.get("counter")
    value = {"counter": list(counter) if isinstance(counter, (list, tuple)) and len(counter) == 2
             and all(type(v) is int for v in counter) else None,
             "claimable": len(report.get("claimable") or []), "claimed": len(report.get("claimed") or []),
             "incomplete": len(report.get("incomplete") or []), "unknown": len(report.get("unknown") or []),
             "rows_visible_only": True,
             "rows": [{"title": _short(r.get("title"), 40), "status": _short(r.get("status"), 12),
                       "progress": list(r["progress"]) if isinstance(r.get("progress"), (list, tuple)) else None}
                      for r in report.get("rows") or [] if isinstance(r, dict)][:MAX_ROWS]}
    return [(f"missions.{report['page']}", value, None)]


def present(report):
    after = report.get("page_after")
    page = after if isinstance(after, dict) and after.get("tab") in ("unclaimed", "received") else report.get("page")
    if not isinstance(page, dict):
        return []
    rows = [{"name": _short(r.get("name"), 40), "quantity": _int(r.get("quantity"), 10**7), "kind": _short(r.get("kind"), 20)}
            for r in page.get("rows") or [] if isinstance(r, dict) and _short(r.get("name"), 40)][:MAX_ROWS]
    value = {"tab": _short(page.get("tab"), 12), "count": _int(page.get("count"), 999), "rows": rows,
             "outcome": _short(report.get("outcome"), 40)}
    return [("present.box", value, None)]


def wing(report):
    found = {}
    for event in report.get("log") or []:
        reading = event.get("readings") if isinstance(event, dict) else None
        if event.get("screen") == "Hub" and isinstance(reading, dict):
            for key in ("season", "weeks_left", "fans_left", "stamina_pct"):
                if _int(reading.get(key), 10**7) is not None:
                    found[key] = reading[key]
            for key in ("hub_speaker", "hub_cheer"):
                if _short(reading.get(key), 30) is not None:
                    found[key] = reading[key]
        elif isinstance(reading, dict):
            for key in ("unit_index", "unit_total"):
                if _int(reading.get(key), 999) is not None:
                    found[key] = reading[key]
    return [("wing.hub", found, None)] if found else []


def knowhow_scan(report):
    value = {"outcome": _short(report.get("outcome"), 60)}
    for key in ("books", "complete", "total"):
        if _int(report.get(key)) is not None or type(report.get(key)) is bool:
            value[key] = report[key]
    return [("knowhow.scan", value, None)] if value["outcome"] else []


def owned_levels(report, store, observed_at, source):
    """Owned-card readings from a passive list read: handled directly on the store (per-card rules)."""
    recorded = 0
    cards = report.get("cards") if report.get("ok") is True else None
    for card_id, item in (cards or {}).items() if isinstance(cards, dict) else ():
        if not isinstance(item, dict) or type(item.get("confidence_pm")) is not int:
            continue
        if store.record_card(card_id, limit_break=item.get("limit_break"), level=item.get("level"),
                             confidence=item["confidence_pm"] / 1000.0, source=source, observed_at=observed_at):
            recorded += 1
    return recorded


EXTRACTORS = {"resources": resources, "shop": shop, "training": training, "grade": lambda r: _fes(r), "match": match,
              "mission_status": mission_status, "present": present, "wing": wing, "knowhow_scan": knowhow_scan}


class ReportTap:
    """Sink listener: ``tap(report)`` persists the report's readings. Never raises, never blocks a task."""

    def __init__(self, store, source_prefix="task_report"):
        self.store, self.source_prefix = store, source_prefix
        self.recorded = self.errors = 0

    def __call__(self, report):
        try:
            kind = report.get("kind")
            observed_at = report.get("observed_at") if isinstance(report.get("observed_at"), str) else None
            if kind == "owned_levels":
                source = report.get("source") if isinstance(report.get("source"), str) else f"{self.source_prefix}:{kind}"
                self.recorded += owned_levels(report, self.store, observed_at, source)
                return
            extractor = EXTRACTORS.get(kind)
            if extractor is None:
                return
            source = report.get("source") if kind == "resources" and isinstance(report.get("source"), str) \
                else f"{self.source_prefix}:{kind}"
            for key, value, confidence in extractor(report):
                if self.store.record(key, value, source=source, observed_at=observed_at, confidence_pm=confidence):
                    self.recorded += 1
        except Exception:
            self.errors += 1
            LOGGER.exception("report tap failed")
