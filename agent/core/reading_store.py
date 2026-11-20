"""Private persistent store for passively observed readings.

A reading is ``(key, value dict, source, observed_at, confidence_pm)``. The store keeps the newest reading per key
(+ a short history), the best/newest owned-card level per card id, survives restarts, and exports what the UI
resources page needs: latest value, time, source and a *stale* flag after N days. It never raises into callers, never
guesses a missing value and never overwrites a ``manual`` card value with an automatic one.

Files (private, gitignored): ``dev_assets/private/readings/readings.json`` (store) and
``ui/game-assets/passive-readings.js`` (UI module, written by :meth:`ReadingStore.export` when an export path is set).
"""
from __future__ import annotations

import atexit
import datetime as _dt
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
STORE_PATH = ROOT / "dev_assets" / "private" / "readings" / "readings.json"
UI_EXPORT_PATH = ROOT / "ui" / "game-assets" / "passive-readings.js"
VERSION = 1
KEY_RE = re.compile(r"^[a-z][a-z0-9_.]{0,60}$")
CARD_ID_RE = re.compile(r"^[0-9]{6,12}$")
MAX_VALUE_BYTES = 8192
MAX_KEYS = 200
MAX_CARDS = 3000
HISTORY = 10
AUTO_ACCEPT_CONFIDENCE = 0.70          # = owned_levels.AUTO_ACCEPT_CONFIDENCE (kept literal: no heavy import here)
DAY = 86400.0
# Stale after this many seconds without a fresh reading; counters change by playing, pages by the daily reset.
STALE_AFTER_S = {
    "resource.stamina": 6 * 3600.0, "resource.money": DAY, "resource.jewel": DAY, "resource.fes_rights": DAY,
    "resource.bronze_ticket": DAY, "shop.offers": DAY, "missions.mission_daily": DAY, "missions.mission_weekly": 3 * DAY,
    "present.box": DAY, "gacha.home": DAY, "wing.hub": 12 * 3600.0, "match.main": DAY,
}
DEFAULT_STALE_S = 3 * DAY
MANUAL_SOURCES = ("manual", "user")


def utc_now():
    return _dt.datetime.now(_dt.timezone.utc)


def _parse(stamp):
    try:
        value = _dt.datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=_dt.timezone.utc)


def _safe_value(value):
    """JSON-round-trippable plain data (dict) within the size cap, else None."""
    if not isinstance(value, dict):
        return None
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None
    return json.loads(text) if len(text.encode("utf-8")) <= MAX_VALUE_BYTES else None


def stale_after(key):
    return STALE_AFTER_S.get(key, DEFAULT_STALE_S)


class ReadingStore:
    def __init__(self, path=STORE_PATH, *, export_path=None, now=utc_now, clock=time.monotonic, flush_interval_s=2.0):
        self.path = None if path is None else Path(path)
        self.export_path = None if export_path is None else Path(export_path)
        self.now, self.clock, self.flush_interval_s = now, clock, flush_interval_s
        self.lock = threading.RLock()
        self.readings = {}
        self.cards = {}
        self.dirty = False
        self.last_flush = None
        self.errors = self.accepted = self.refused = 0
        self._load()

    @classmethod
    def default(cls, **options):
        """The production store. ``SC_READINGS_DIR`` redirects both files (tests use it to stay out of the private dir)."""
        override = os.environ.get("SC_READINGS_DIR")
        if override:
            store = cls(Path(override) / STORE_PATH.name, export_path=Path(override) / UI_EXPORT_PATH.name, **options)
        else:
            store = cls(STORE_PATH, export_path=UI_EXPORT_PATH, **options)
        atexit.register(store.flush, True)
        return store

    # ------------------------------------------------------------------ persistence
    def _load(self):
        if self.path is None or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != VERSION:
                return
            for key, item in (data.get("readings") or {}).items():
                if KEY_RE.match(key) and isinstance(item, dict) and _safe_value(item.get("value")) is not None \
                        and _parse(item.get("observed_at")) is not None:
                    self.readings[key] = item
            for cid, item in (data.get("cards") or {}).items():
                if CARD_ID_RE.match(cid) and isinstance(item, dict) and type(item.get("limit_break")) is int:
                    self.cards[cid] = item
        except (OSError, ValueError, AttributeError):
            LOGGER.exception("reading store unreadable; starting empty (file kept)")
            self.errors += 1

    def _write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)

    def flush(self, force=False):
        """Write the store (and the UI export) when dirty; throttled. Never raises. Returns True when written."""
        with self.lock:
            if not self.dirty or self.path is None:
                return False
            tick = self.clock()
            if not force and self.last_flush is not None and tick - self.last_flush < self.flush_interval_s:
                return False
            try:
                self._write(self.path, json.dumps({"version": VERSION, "readings": self.readings, "cards": self.cards},
                                                  ensure_ascii=False, indent=1, sort_keys=True))
                if self.export_path is not None:
                    self.export_file()
            except Exception:
                LOGGER.exception("reading store write failed")
                self.errors += 1
                return False
            self.dirty, self.last_flush = False, tick
            return True

    # ------------------------------------------------------------------ recording
    def record(self, key, value, *, source, observed_at=None, confidence_pm=None):
        """Newest reading per key wins; an older ``observed_at`` than the stored one is ignored. Never raises."""
        try:
            stamp = observed_at or self.now().isoformat()
            if not (isinstance(key, str) and KEY_RE.match(key) and isinstance(source, str) and source
                    and _parse(stamp) is not None):
                self.refused += 1
                return False
            value = _safe_value(value)
            if value is None or (confidence_pm is not None and not (type(confidence_pm) is int and 0 <= confidence_pm <= 1000)):
                self.refused += 1
                return False
            with self.lock:
                old = self.readings.get(key)
                if old is None and len(self.readings) >= MAX_KEYS:
                    self.refused += 1
                    return False
                if old is not None and _parse(stamp) < _parse(old["observed_at"]):
                    self.refused += 1
                    return False
                history = list((old or {}).get("history", []))
                if old is not None:
                    history.append({k: old[k] for k in ("value", "observed_at", "source") if k in old})
                item = {"value": value, "observed_at": stamp, "source": source[:60], "history": history[-HISTORY:]}
                if confidence_pm is not None:
                    item["confidence_pm"] = confidence_pm
                self.readings[key] = item
                self.accepted += 1
                self.dirty = True
            self.flush()
            return True
        except Exception:
            LOGGER.exception("reading store record failed")
            self.errors += 1
            return False

    def record_card(self, card_id, *, limit_break, level=None, confidence, source, observed_at=None):
        """Owned-card reading (same pipeline as the card-scan import). A ``manual`` value is never overwritten by an
        automatic one; otherwise a newer reading replaces an older one unless it is a clearly worse reading."""
        try:
            stamp = observed_at or self.now().isoformat()
            if not (isinstance(card_id, str) and CARD_ID_RE.match(card_id) and type(limit_break) is int
                    and 0 <= limit_break <= 4 and (level is None or type(level) is int and 1 <= level <= 999)
                    and isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
                    and 0.0 <= confidence <= 1.0 and isinstance(source, str) and source
                    and _parse(stamp) is not None):
                self.refused += 1
                return False
            with self.lock:
                old = self.cards.get(card_id)
                if old is None and len(self.cards) >= MAX_CARDS:
                    self.refused += 1
                    return False
                manual_old = old is not None and old.get("source") in MANUAL_SOURCES
                if manual_old and source not in MANUAL_SOURCES:
                    self.refused += 1
                    return False
                if old is not None and not manual_old and source not in MANUAL_SOURCES:
                    newer = _parse(stamp) >= _parse(old["observed_at"])
                    if not newer or (confidence < AUTO_ACCEPT_CONFIDENCE <= old["confidence"]):
                        self.refused += 1
                        return False
                item = {"limit_break": limit_break, "confidence": round(float(confidence), 3), "source": source[:60],
                        "observed_at": stamp}
                if level is not None:
                    item["level"] = level
                self.cards[card_id] = item
                self.accepted += 1
                self.dirty = True
            self.flush()
            return True
        except Exception:
            LOGGER.exception("reading store card record failed")
            self.errors += 1
            return False

    # ------------------------------------------------------------------ export
    def latest(self, key):
        with self.lock:
            item = self.readings.get(key)
            return None if item is None else json.loads(json.dumps(item))

    def snapshot(self, now=None):
        """UI-facing data: per key latest value/time/source/confidence/stale/age; owned-card suggestions."""
        now = now or self.now()
        with self.lock:
            readings = {}
            for key, item in sorted(self.readings.items()):
                seen = _parse(item["observed_at"])
                age = max(0.0, (now - seen).total_seconds())
                entry = {"value": item["value"], "observed_at": item["observed_at"], "source": item["source"],
                         "age_s": int(age), "stale": age > stale_after(key), "stale_after_s": int(stale_after(key))}
                if "confidence_pm" in item:
                    entry["confidence_pm"] = item["confidence_pm"]
                readings[key] = entry
            cards, details = {}, {}
            for cid, item in sorted(self.cards.items()):
                auto = item["source"] in MANUAL_SOURCES or item["confidence"] >= AUTO_ACCEPT_CONFIDENCE
                cards[cid] = {**item, "auto_accept": auto}
                if auto:
                    details[cid] = {"limit_break": item["limit_break"],
                                    **({"level": item["level"]} if "level" in item else {})}
        return {"version": VERSION, "generated_by": "agent/core/reading_store.py", "generated_at": now.isoformat(),
                "note": "Passively observed while tasks ran; display/plan references only, never input or spend authority. "
                        "Cards: the UI applies auto_accept entries (confidence >= auto_accept_confidence) and never "
                        "over a user-set value.",
                "auto_accept_confidence": AUTO_ACCEPT_CONFIDENCE,
                "readings": readings, "owned_levels": {"cards": cards, "details": details}}

    def export_file(self, path=None, now=None):
        path = Path(path) if path is not None else self.export_path
        if path is None:
            raise ValueError("no export path")
        data = self.snapshot(now)
        self._write(path, "// Generated by agent/core/reading_store.py; local, gitignored. Passive readings (latest, "
                          "time, source, stale flag).\nexport default "
                          + json.dumps(data, ensure_ascii=False, indent=1) + ";\n")
        return data
