"""Passive resource observer: read resource counters from frames a task already captured. Never acts.

Design:

* Hook = one Maa custom recognition ``SC.Resource.Observe`` placed in a task's polling ``next`` list right before
  ``[JumpBack]SC.<Task>.AtHome`` (node ``SC.Resource.Observe.Home`` in ``pipeline/resources.json``). Maa evaluates
  it on the frame the polling batch already captured (``argv.image``); it never captures, never clicks and ALWAYS
  returns "no hit", so the next list continues exactly as before. The node ships ``enabled: false`` and only
  :func:`install` (called by the launcher after registering the task customs) enables it.
* Cost is bounded: a monotonic throttle between attempts (a throttled call returns at once), a per-run OCR read
  cap, a per-read time budget (one slow read disables the observer for the rest of the run) and an error cap.
  It never overrides the Pipeline except the one ``enabled`` switch of its own nodes in :func:`install`.
* Reading reuses :mod:`agent.core.item_inventory` (``home_counters``) with stricter OCR gates; screen guards are the
  existing Pipeline guards (``SC.Common.Home.Guard`` positive, ``SC.Common.HomeClean`` foreign anchors absent).
* Output: ``sink.add("resources", {...})`` with time-stamped ``items`` ({id: {count, max?, plus?, confidence_pm,
  indicator_only?}}). Values are display readings, never input or spend authority. Jewels are shown only.
* Home ガシャ hint (:mod:`agent.core.gacha_hint`): on the same guarded Home frame, the orange 「1日1回無料」-style tag on
  the ガシャ button is read (pixel gate, OCR only when the tag is there) and emitted as ``gacha`` in the same report
  (only definite reads: ``free`` / ``no_tag``). Display reminder only; the tool never opens or draws the gacha.
* Layouts: ``home_counters`` (above) and ``training_list`` (visible S-card Lv / 凸 on the トレーニング list the
  Training task already shows; same tile reader as the card scan, emitted as an ``owned_levels`` report). Each
  layout has its own throttle, read cap and time budget; the training reader needs the card-thumbnail index, which a
  background thread loads (a frame is skipped until it is ready, never waited for).
* :func:`install` also attaches a :class:`~agent.core.report_readings.ReportTap` so the readings that tasks already
  put in their reports (and this observer's own reports) are persisted in the private reading store
  (``agent/core/reading_store.py``), and an ``owned_levels`` report updates the owned-card readings there.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
import time

from agent.core import gacha_hint as gh
from agent.core import item_inventory as ii

LOGGER = logging.getLogger(__name__)
NAME = "SC.Resource.Observe"
NODES = ("SC.Resource.Observe.Home", "SC.Resource.Observe.TrainingList")
LAYOUTS = ("home_counters", "training_list")
TRAINING_GUARD = "SC.Training.List.Guard"
TRAINING_BLOCKERS = ("SC.Popup.TrainingHelp.Guard",)
HOME_GUARD = "SC.Common.Home.Guard"
# Context.run_recognition ignores the node-level ``inverse`` (MaaFramework 5.12.3, see task_actions.ClassifyScreen):
# a raw hit of SC.Common.HomeClean means a foreign page / pop-up anchor is present, i.e. the Home is NOT clean.
HOME_FOREIGN = "SC.Common.HomeClean"

DEFAULTS = {
    "min_interval_s": 20.0,     # between two attempts (monotonic)
    "max_reads": 12,            # OCR reads per run (one launcher process / one observer instance)
    "max_read_s": 1.5,          # one read slower than this disables the observer for the run
    "max_errors": 2,            # exceptions before the observer disables itself
    "min_ocr": 0.90,            # stricter than the offline scanner (0.60): a live read must be clean
    "refresh_s": 300.0,         # re-emit unchanged values at most this often
    # training_list layout (own budget: the tile reader OCRs ~21 tiles, ~1 s measured on a native frame)
    "list_min_interval_s": 15.0,   # between two tile reads
    "list_miss_interval_s": 1.5,   # between two guard checks while the list is not on screen
    "list_max_reads": 8,
    "list_max_read_s": 4.0,
    "list_min_confidence": 0.50,   # accepted tiles below this are dropped; < 0.70 is stored for review (no auto-apply)
}


def utc_now():
    return _dt.datetime.now(_dt.timezone.utc)


def _image_ok(image):
    shape = getattr(image, "shape", None)
    return isinstance(shape, tuple) and len(shape) == 3 and shape[:2] == (ii.CANVAS[1], ii.CANVAS[0]) and shape[2] >= 3


def _pil(image):
    from PIL import Image

    return Image.fromarray(image[:, :, 2::-1].copy())


class MaaVision:
    """Read-only access through the Maa context, on the frame that Maa passed in (no capture)."""

    def __init__(self, context):
        self.context = context

    def hit(self, node, image):
        detail = self.context.run_recognition(node, image)
        return bool(detail is not None and detail.hit)

    def ocr(self, image, roi):
        from maa.pipeline import JOCR, JRecognitionType

        detail = self.context.run_recognition_direct(JRecognitionType.OCR, JOCR(roi=tuple(roi), threshold=0.0), image)
        rows = []
        for result in (getattr(detail, "all_results", None) or []) if detail is not None else []:
            text, box = getattr(result, "text", None), getattr(result, "box", None)
            if isinstance(text, str) and box is not None:
                rows.append((text, float(getattr(result, "score", 0.0)), tuple(int(v) for v in list(box)[:4])))
        return rows


# OCR boxes narrower than the scanner's counter ROI where an icon would merge into the text (measured on the native
# home frames of 2026-10-02/03: the jewel icon at x 480–505 was read as "②"). The centre stays inside the scanner ROI.
OCR_ROIS = {"jewel": (508, 8, 100, 40)}


def home_rois():
    layout = ii.LAYOUTS["home_counters"]
    return [roi for roi, _ in layout["anchors"].values()] + [OCR_ROIS.get(key, spec["roi"])
                                                              for key, spec in layout["counters"].items()]


def readings_from_result(result, min_ocr):
    """item_inventory result -> ({id: reading}, [rejected ids]). A counter needs an OCR score >= ``min_ocr``."""
    items, rejected = {}, []
    for key, entry in (result.get("counters") or {}).items():
        item = (result.get("items") or {}).get(key)
        score = entry.get("ocr_score")
        if item is None or score is None or score < min_ocr or type(item.get("count")) is not int:
            rejected.append(key)
            continue
        reading = {"count": item["count"], "confidence_pm": int(round(min(1.0, score) * 1000))}
        for extra in ("max", "plus"):
            if type(item.get(extra)) is int:
                reading[extra] = item[extra]
        if item.get("indicator_only"):
            reading["indicator_only"] = True
        items[key] = reading
    return items, sorted(rejected)


def gacha_reading(vision, image):
    """Definite ガシャ hint reads only (``free`` / ``no_tag``); anything uncertain is dropped (None). Never raises."""
    try:
        out = gh.read(image, vision.ocr)
    except Exception:
        LOGGER.exception("gacha hint read failed")
        return None
    if out["state"] not in ("free", "no_tag"):
        return None
    return {key: out[key] for key in ("state", "kind", "fill_pm")}   # no raw OCR text leaves the observer


class ResourceObserver:
    """One instance per launcher run. ``observe`` never raises and never returns a hit."""

    def __init__(self, sink, *, clock=time.monotonic, now=utc_now, vision=MaaVision, **options):
        unknown = set(options) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"未知观测参数: {sorted(unknown)}")
        self.sink, self.clock, self.now, self.vision = sink, clock, now, vision
        self.options = {**DEFAULTS, **options}
        self.lock = threading.Lock()
        self.last_attempt = None
        self.attempts = self.reads = self.emitted = self.errors = 0
        self.refusals = {}
        self.disabled = None
        self.last_items, self.last_emit = None, None
        self.list = {"last": None, "reads": 0, "disabled": None, "emitted": 0, "last_cards": None, "last_emit": None}
        self.store = self.tap = None
        self.index = None             # CardIndex (training_list), loaded lazily in a background thread
        self._index_state = None      # None | "loading" | "ready" | "failed"

    # ------------------------------------------------------------------ bookkeeping
    def _refuse(self, reason):
        self.refusals[reason] = self.refusals.get(reason, 0) + 1
        return None

    def _due(self):
        if self.disabled is not None:
            return False
        if self.reads >= self.options["max_reads"]:
            self.disabled = "read_cap_reached"
            return False
        now = self.clock()
        if self.last_attempt is not None and now - self.last_attempt < self.options["min_interval_s"]:
            return False
        self.last_attempt = now
        self.attempts += 1
        return True

    def summary(self):
        out = {"attempts": self.attempts, "reads": self.reads, "emitted": self.emitted, "errors": self.errors,
               "disabled": self.disabled, "refusals": dict(sorted(self.refusals.items()))}
        if self.list["last"] is not None:   # the training list layout was ever polled
            out["training_list"] = {"reads": self.list["reads"], "emitted": self.list["emitted"],
                                    "disabled": self.list["disabled"], "index": self._index_state}
        return out

    # ------------------------------------------------------------------ entry
    def observe(self, context, image, layout="home_counters"):
        """Called from the custom recognition. True only after a training-list read (the hook then hits, so the task
        decides on a fresh frame: 2026-11-19 live, the ~3 s read plus the list decision aged the frame past the host's
        5 s binding and the card tap was refused); otherwise None (no hit)."""
        if not self.lock.acquire(blocking=False):
            return None  # another read in progress: skip, never wait
        try:
            if layout == "training_list":
                reads = self.list["reads"]
                self._observe_list(context, image)
                return True if self.list["reads"] > reads else None
            if layout not in LAYOUTS or not self._due():
                return None
            started = self.clock()
            try:
                self._read_home(self.vision(context), image)
            except Exception:  # never propagates into Maa; a broken reader turns itself off
                self.errors += 1
                LOGGER.exception("resource observer read failed")
                self._refuse("reader_error")
                if self.errors >= self.options["max_errors"]:
                    self.disabled = "reader_errors"
            if self.clock() - started > self.options["max_read_s"]:
                self.disabled = "read_too_slow"
            return None
        finally:
            self.lock.release()

    # ------------------------------------------------------------------ training list (owned Lv / 凸)
    def _load_index(self):
        try:
            from agent.core import card_inventory as ci

            self.index = ci.CardIndex.from_assets()
            self._index_state = "ready"
        except Exception:
            LOGGER.exception("card index unavailable; training list observer off")
            self._index_state = "failed"

    def _list_due(self):
        state, now = self.list, self.clock()
        if state["disabled"] is not None:
            return False
        if state["reads"] >= self.options["list_max_reads"]:
            state["disabled"] = "read_cap_reached"
            return False
        if state["last"] is not None and now - state["last"] < self.options["list_miss_interval_s"]:
            return False
        state["last"] = now
        return True

    def _observe_list(self, context, image):
        if not self._list_due():
            return
        started = self.clock()
        state = self.list
        try:
            vision = self.vision(context)
            if not _image_ok(image):
                return self._refuse("frame_not_1280x720")
            if not vision.hit(TRAINING_GUARD, image):
                return self._refuse("not_training_list")
            if any(vision.hit(node, image) for node in TRAINING_BLOCKERS):
                return self._refuse("list_overlay")
            if self._index_state is None:
                self._index_state = "loading"
                threading.Thread(target=self._load_index, name="sc-observer-index", daemon=True).start()
                return self._refuse("index_loading")
            if self._index_state != "ready":
                if self._index_state == "failed":
                    state["disabled"] = "index_unavailable"
                return self._refuse("index_loading")
            state["reads"] += 1
            state["last"] = self.clock() + self.options["list_min_interval_s"] - self.options["list_miss_interval_s"]
            self._read_training_list(vision, image)
        except Exception:
            self.errors += 1
            LOGGER.exception("training list observer failed")
            self._refuse("reader_error")
            if self.errors >= self.options["max_errors"]:
                state["disabled"] = "reader_errors"
        if self.clock() - started > self.options["list_max_read_s"]:
            state["disabled"] = "read_too_slow"

    def _read_training_list(self, vision, image):
        from agent.core import owned_levels as ol

        def ocr(bgr, roi):
            return [(text, score) for text, score, _ in vision.ocr(bgr, roi)]

        records = ol.read_frame(_pil(image), self.index, ocr, frame_name="")
        floor = self.options["list_min_confidence"]
        cards = {}
        for record in records:
            if record.get("status") != "accepted" or record.get("confidence", 0.0) < floor:
                continue
            entry = {"limit_break": int(record["limit_break"]), "confidence_pm": int(round(record["confidence"] * 1000))}
            if record.get("level") is not None:
                entry["level"] = int(record["level"])
            cards[record["card_id"]] = entry
        if not cards:
            return self._refuse("no_confident_tile")
        clock = self.clock()
        if (cards == self.list["last_cards"] and self.list["last_emit"] is not None
                and clock - self.list["last_emit"] < self.options["refresh_s"]):
            return self._refuse("unchanged")
        self.list["last_cards"], self.list["last_emit"] = cards, clock
        self.list["emitted"] += 1
        if self.sink is not None:
            self.sink.add("owned_levels", {"ok": True, "source": "passive_observer:training_list",
                                           "observed_at": self.now().isoformat(), "cards": cards,
                                           "tiles": len(records), "rejected_tiles": len(records) - len(cards)})

    def _read_home(self, vision, image):
        if not _image_ok(image):
            return self._refuse("frame_not_1280x720")
        if not vision.hit(HOME_GUARD, image):
            return self._refuse("not_home")
        if vision.hit(HOME_FOREIGN, image):
            return self._refuse("home_overlay")
        self.reads += 1  # the OCR budget counts from here, whatever the outcome
        texts = []
        for roi in home_rois():
            texts.extend(vision.ocr(image, roi))
        result = ii.recognise(_pil(image), None, "home_counters", texts)
        if not result.get("screen_ok"):
            return self._refuse(str(result.get("refused") or "screen_guard_failed"))
        items, rejected = readings_from_result(result, self.options["min_ocr"])
        gacha = gacha_reading(vision, image)
        if not items and gacha is None:
            return self._refuse("no_confident_counter")
        self._emit("home_counters", items, rejected, gacha)
        return None

    def _emit(self, layout, items, rejected, gacha=None):
        clock = self.clock()
        if ((items, gacha) == self.last_items and self.last_emit is not None
                and clock - self.last_emit < self.options["refresh_s"]):
            self._refuse("unchanged")
            return
        self.last_items, self.last_emit = (items, gacha), clock
        self.emitted += 1
        payload = {"ok": True, "source": "passive_observer", "layout": layout,
                   "observed_at": self.now().isoformat(), "items": items, "rejected": rejected}
        if gacha is not None:
            payload["gacha"] = gacha
        if self.sink is not None:
            self.sink.add("resources", payload)


def observer_recognition(observer):
    from maa.custom_recognition import CustomRecognition

    class ObserveRecognition(CustomRecognition):
        def analyze(self, context, argv):
            try:
                params = json.loads(argv.custom_recognition_param or "{}")
                layout = params.get("layout") if isinstance(params, dict) else None
                if observer.observe(context, argv.image, layout) is True:
                    return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="observed")
            except Exception:
                LOGGER.exception("resource observer hook failed")
            return None

    return ObserveRecognition()


_DEFAULT_STORE = object()


def install(resource, sink, *, store=_DEFAULT_STORE, **options):
    """Register the hook on ``resource`` and enable its nodes. Call before the frame guard is installed.

    ``store``: the :class:`~agent.core.reading_store.ReadingStore` that persists readings. Default: the private store
    under dev_assets/private/readings (UI export under ui/game-assets) **only when the launcher CLI opted in**
    (``SC_READINGS_PERSIST=1``, set by the launcher CLI entry); otherwise (tests, imports) nothing is persisted.
    ``None`` always turns persistence off."""
    observer = ResourceObserver(sink, **options)
    if store is _DEFAULT_STORE:
        store = None
        if os.environ.get("SC_READINGS_PERSIST") == "1":
            try:
                from agent.core.reading_store import ReadingStore

                store = ReadingStore.default()
            except Exception:
                LOGGER.exception("reading store unavailable; readings are not persisted")
    observer.store = store
    if store is not None and sink is not None and hasattr(sink, "add_listener"):
        from agent.core.report_readings import ReportTap

        observer.tap = ReportTap(store)
        sink.add_listener(observer.tap)
    recognition = observer_recognition(observer)
    if not resource.register_custom_recognition(NAME, recognition):
        raise ValueError("资源观测识别注册失败")
    if not resource.override_pipeline({name: {"enabled": True} for name in NODES}):
        raise ValueError("资源观测节点启用失败")
    observer._sc_keepalive = recognition
    return observer
