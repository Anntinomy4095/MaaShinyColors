"""Maa custom recognitions/actions of the W.I.N.G. navigator (``assets/resource/base/pipeline/wing.json``).

Shared by the AgentServer (``agent/custom/action/wing.py``, never sends input) and the supervised launcher entry
(``tools/run_tasks.py`` ``Wing``). Python decides, the Pipeline clicks:

- ``SC.Wing.On.<Screen>`` (native screen guard + ``SC.Wing.State{want: round}``) opens a decision round;
- each ``SC.Wing.Click.<Screen>.<intent>`` is ``And[SC.Wing.Pick.<Screen>.<intent>, SC.Wing.Screen.<Screen>, Active]``
  with a fixed table rect. ``SC.Wing.Decide`` (the Pick) computes the round's decision ONCE on the click frame
  (screen re-verified in Python first, readers, scorer hooks) and hits only for the decided intent, after the
  runtime's act gate (repeat/budget/verified/input/spend) accepted it. A consuming intent additionally needs a
  frame-bound SpendGate grant for its table action from the launcher's spend authority;
- ``SC.Wing.State{want: held}`` stops with the latched reason when the round's decision was not a click,
  ``{want: requeue}`` re-classifies when the screen vanished before the click (bounded), ``{want: halted}`` ends a
  stopped run at once.

Input authority never comes from Pipeline parameters: ``WingSession(input_enabled=..., allow_start_spend=...)`` is
set by the launcher; the AgentServer session keeps both False, so ``WingNavRuntime.act`` refuses every click there.
"""
from __future__ import annotations

import collections
import copy
import dataclasses
import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path

from agent.core import task_common as tc
from agent.core import wing_ledger as wl
from agent.core import wing_nav as nav
from agent.core import wing_numbers as wnum
from agent.core import wing_options as wo
from agent.core import wing_screen as wsc
from agent.core import wing_speed as wspeed
from agent.core.wing_live_feed import WingFeed

LOGGER = logging.getLogger(__name__)
APPROVER = "SC.Wing.Decide"
MAX_REQUEUES = 20               # consecutive re-classifications without a click or wait -> screen_unstable
# 2026-10-14 live a3: one APPEAL frame with the cursor hidden by a sparkle stopped the run. Nothing is sent before a
# plan exists, so these refusals re-read a fresh frame (bounded) instead of stopping.
TIMING_REREAD = ("appeal_timing_unread", "frame_missing_or_stale", "analysed_image_is_not_the_latest_frame")
MAX_TIMING_REREADS = 6
# Rare random events (記者活動, 親愛度急上昇, ...) have no samples yet: an unclassified frame with real UI text is kept
# (PNG + OCR boxes, private) so a rule can be added offline. Read-only; capped per run.
UNKNOWN_DIR = Path(__file__).resolve().parents[2] / "dev_assets" / "private" / "wing_unknown"
MAX_UNKNOWN_SAMPLES = 20
# Frames whose number was rejected / left unconfirmed by wing_numbers, or whose decision stopped or waited on an
# unread field, go to <unknown_dir>/../ocr-eval/<date>/ (PNG + field, ROI, read, reason) for the per-field OCR
# evaluation (tools/evaluate_ocr_models.py --wing). Live runs only, bounded per run.
MAX_OCR_EVAL_SAMPLES = 20
OCR_EVAL_SKIP_REASONS = ("live:phase_unread",)   # Live animation frames: nothing to read, not an OCR miss
APPEAL_FOLLOW_FRAMES = 4         # frames kept after each sent timed APPEAL (our judgement; Perfect calibration)
UNKNOWN_MIN_TEXTS = 3
MAX_UNRUN_CLICKS = 5            # consecutive rounds whose click decision found no runnable Click node -> stop
LOG_TAIL = 60
# Frame-permit TTL margin (agent.core.frame_permit.MAX_AGE_S = 5 s, measured from the batch capture): a click decision
# that took longer than this is not acted on -- the frame guard would refuse it as ``frame_stale_before_permit``
# (live seg8: a 15.9 s decision) -- but re-decided on a fresh frame with the pre-decision runtime state restored.
DECISION_STALE_S = 3.5
# APPEAL alignment (review P2-6): the wait is measured from the decision start, but the frame was captured earlier
# (capture -> recognition plus the host's input interval, ≤ ~1 s). Keep this margin so the tap's frame stays inside
# frame_permit.MAX_AGE_S: decision time + delay + margin <= DECISION_STALE_S (3.5 + 1.2 < 5 s).
APPEAL_FRAME_MARGIN_S = 1.2
# Taps sent while a page is still sliding in are ignored by the game (2026-10-09 live). A click waits until its screen
# has been on view this long since it was first classified; decisions usually take longer, so most clicks wait 0.
SETTLE_S = 0.5
# The timed APPEAL is scheduled by its own timing plan (host clock or _align_appeal): no settle wait.
NO_SETTLE_INTENTS = frozenset({"live_appeal_timing"})
# Speed: the Review decision's whole-frame read takes the whole-frame OCR of the round's classified frame
# when the decision frame is the same view (wing_speed.same_view) and the last act was a Review tap. Learning /
# replacing / forgetting (other screens) change node labels with little pixel change: those Review rounds read anew.
FULL_REUSE_SCREENS = frozenset({"Review"})
# Memory outcomes that end the Produce (獲得報酬 handover / home after the end): the memory is archived, not kept.
PRODUCE_END_OUTCOMES = ("produce_end_review", "produce_finished_home", "finished")
# Align the APPEAL tap to the timing bar's Perfect zone (live 2026-10-10: untimed taps were mostly Good/Normal).
TIMING_ALIGN = True
# Clicks proven not to have reached the game (controller denied / no gesture) are re-decided at most this many times
# in a row for the same screen+intent; consuming intents are never re-decided.
UNSENT_RETRIES = 1
ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "dev_assets" / "private"
# One run-memory file for the UI and the CLI (review P2-4): tools/ui_wing.py uses it; CLI configs should name it as
# ``memory_file`` (tools/wing_segments.py warns when they do not). Older private CLI files stay where they are.
SHARED_MEMORY_FILE = PRIVATE / "wing-ui" / "memory.json"


def _digest(image):
    """sha256 of the frame's pixels (same value as hashing ``image.tobytes()``, without the copy)."""
    import numpy as np
    if isinstance(image, np.ndarray) and image.dtype != object:
        return hashlib.sha256(np.ascontiguousarray(image).data).hexdigest()
    return hashlib.sha256(image.tobytes()).hexdigest()   # offline fakes


def _samples(image):
    """80x45 pixel samples of the frame: proof that a memoised digest belongs to this image."""
    try:
        return image[::THUMB_STEP, ::THUMB_STEP]
    except Exception:
        return None


class MaaContextBackend(wsc.BoxBackend):
    """Reader backend for a live Maa context: ROI OCR through ``run_recognition_direct`` (no capture, no node)."""
    name = "maa_context"

    def __init__(self, context):
        self.context = context

    def ocr(self, node, frame):
        from maa.pipeline import JOCR, JRecognitionType
        # ``only_rec``: the ROI is one text line (no detector), e.g. wing_screen.status_digit_rois' SP digits.
        param = JOCR(expected=[], roi=tuple(node.get("roi", [0, 0, *wsc.CANVAS])),
                     threshold=float(node.get("threshold", 0.3)), only_rec=bool(node.get("only_rec", False)))
        detail = self.context.run_recognition_direct(JRecognitionType.OCR, param, frame.image_bgr)
        results = list(getattr(detail, "all_results", None) or []) if detail is not None else []
        return [(wsc.apply_replace(r.text, node.get("replace")), tuple(r.box)) for r in results
                if getattr(r, "text", None)]


class FrameOcrBackend(wsc.BoxBackend):
    """Speed (2026-10-09c, user: 「执行过慢 commu的5秒都会来不及操作」): ONE whole-frame OCR per frame, then every anchor /
    reader is answered from its boxes exactly like the offline replay's ``BoxBackend`` (both backends classified the
    replays identically). The live Loop used to evaluate up to ~70 ROI OCR calls
    (one per anchor, in dispatch order) per frame: measured offline 2.3–2.8 s for a Hub frame against 0.6–0.9 s for one
    whole-frame OCR + 3 ms of box matching. ``cache`` maps a frame digest to its boxes (shared by the classifier and
    the decision readers of the same frame)."""
    name = "frame_ocr"
    FULL = {"roi": [0, 0, *wsc.CANVAS], "threshold": 0.3}

    def __init__(self, inner, cache: dict, digest: str):
        self.inner, self.cache, self.digest = inner, cache, digest

    def ocr(self, node, frame):
        if self.digest not in self.cache:
            while len(self.cache) >= 4:
                self.cache.pop(next(iter(self.cache)))
            self.cache[self.digest] = [(t, tuple(b)) for t, b in self.inner.ocr(self.FULL, frame)]
        return wsc.BoxBackend.ocr(self, node, wsc.Frame(self.cache[self.digest]))


class RoiOcrBackend(wsc.BoxBackend):
    """ROI OCR (``ocr: roi``) for the decision readers. A whole-frame read (``SC.Wing.Read.*.Full``: same ROI and
    threshold as FrameOcrBackend.FULL) takes the frame's whole-frame OCR when the guard or the classifier already ran
    it, and stores it otherwise; every other node is its own ROI OCR."""
    name = "maa_context"

    def __init__(self, inner, cache: dict, digest: str, alias: str | None = None):
        self.inner, self.cache, self.digest, self.alias = inner, cache, digest, alias

    def ocr(self, node, frame):
        full = FrameOcrBackend.FULL
        if (list(node.get("roi", full["roi"])) != full["roi"]
                or float(node.get("threshold", full["threshold"])) != full["threshold"]):
            return self.inner.ocr(node, frame)
        if self.digest not in self.cache and self.alias in self.cache:
            # Same view as the frame classified this round (WingSession._full_alias): its whole-frame OCR.
            return [(wsc.apply_replace(t, node.get("replace")), b) for t, b in self.cache[self.alias]]
        if self.digest not in self.cache:
            while len(self.cache) >= 4:
                self.cache.pop(next(iter(self.cache)))
            self.cache[self.digest] = [(t, tuple(b)) for t, b in self.inner.ocr(full, frame)]
        return [(wsc.apply_replace(t, node.get("replace")), b) for t, b in self.cache[self.digest]]


def atomic_write_json(path: Path, data) -> None:
    """Temp file + os.replace (review P2-3): a killed process never leaves a half-written memory file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=1, default=str))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp, path)


def archive_memory(path: Path | None, reason: str) -> Path | None:
    """Move a memory file aside as ``memory-<stamp>-<reason>.json`` (never deleted). None when absent."""
    if path is None or not path.is_file():
        return None
    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    target = path.with_name(f"{path.stem}-{stamp}-{reason}.json")
    n = 1
    while target.exists():
        n += 1
        target = path.with_name(f"{path.stem}-{stamp}-{reason}-{n}.json")
    os.replace(path, target)
    return target


THUMB_STEP = 16            # 1280x720 -> 80x45 grey samples
SAME_VIEW_MEAN_DIFF = 6.0  # mean |Δ| (0-255) still treated as the confirmed view (animated idols, sparkles)


def _thumb(image):
    try:
        return image[::THUMB_STEP, ::THUMB_STEP].astype("int16").mean(axis=2)
    except Exception:   # not a frame array (offline fakes): no shortcut
        return None


def _same_view(confirmed, screen, image) -> bool:
    if not confirmed or confirmed[0] != screen or confirmed[1] is None:
        return False
    try:
        thumb = _thumb(image)
        return thumb.shape == confirmed[1].shape and float(abs(thumb - confirmed[1]).mean()) <= SAME_VIEW_MEAN_DIFF
    except Exception:   # an odd frame only loses the shortcut
        return False


def frame_of(image):
    """Reader frame of a Maa BGR image (readers use RGB; the OCR backend uses the BGR original)."""
    frame = wsc.Frame((), lambda: image[:, :, ::-1], key="live")
    frame.image_bgr = image
    return frame


def private_path(raw) -> Path:
    path = Path(raw)
    path = (path if path.is_absolute() else ROOT / path).resolve()
    if not (path.is_relative_to(PRIVATE.resolve()) and path.suffix.lower() == ".json"):
        raise ValueError("运行记忆文件须为 dev_assets/private 下的 JSON")
    return path


class WingRun:
    """Per-task bookkeeping the launcher flushes into its summary (``reported`` / ``notes`` like other runtimes)."""

    def __init__(self):
        self.reported = True          # nothing to report before SC.Wing.Begin
        self.notes = []
        self.started_at = None


class WingSession:
    """One navigator session (one launcher task or the AgentServer process)."""

    def __init__(self, sink=None, authority=None, *, input_enabled=False, allow_start_spend=False,
                 backend_factory=None, clock=time.monotonic, allow_reward_reroll=False, ledger=None, settings=None,
                 capture_seq=None):
        if type(input_enabled) is not bool or type(allow_start_spend) is not bool or type(allow_reward_reroll) is not bool:
            raise ValueError("输入/消费授权必须为布尔值")
        self.sink = sink
        self.authority = authority
        self.input_enabled = input_enabled
        self.allow_start_spend = allow_start_spend and input_enabled
        self.allow_reward_reroll = allow_reward_reroll and input_enabled   # 獲得報酬 再抽選 (stamina 30); launcher-only
        self.backend_factory = backend_factory or MaaContextBackend
        self.clock = clock
        self.runtime = nav.WingNavRuntime()
        self.run = WingRun()
        self.nodes = None
        self.capture_seq = capture_seq  # controller capture number (callable); the frame digest is hashed once per capture
        self._digest_memo = None       # (capture number, pixel samples, digest) of the last hashed frame
        self.screen_since = None       # (screen, clock) when the current view was first classified as that screen
        self.settle_since = None       # first-classified clock of the approved click's screen (SC.Wing.State settled)
        self.sleep = time.sleep
        self.spend_resolved = None     # spend key whose ledger entry no longer needs a running-Produce proof
        self.ocr_cache = {}            # frame digest -> whole-frame OCR boxes (FrameOcrBackend)
        self.screens = {}              # frame digest -> classified screen (SC.Wing.State want is/expect)
        # Classification shortcuts (agent/core/wing_speed.py, 2026-10-25): the last full/hint classification and its
        # thumbnail (cleared by every approved click), the screen that followed each (screen, intent) click, counts.
        self.view_ref = None
        self.full_ref = None           # (screen, view, digest) of this round's whole-frame classification
        self.last_screen = None
        self.last_act = None
        self.successors = {}
        self.classify_counts = collections.Counter()
        self.ocr_mode = "roi"          # Begin param ``ocr``: roi (per-node ROI OCR, default) | frame
        self.memory_path = None
        self._reset_round()
        self.requeues = 0
        self.decisions = 0
        self.pending_act = None        # approved click awaiting proof of send (see settle_act)
        self.round_pre = None          # runtime state before this round's decide (rollback of an unsent click)
        self.unsent = {}               # (screen, intent) -> consecutive unsent re-decisions
        self.stale_decisions = 0       # consecutive click decisions dropped as too slow for the frame permit
        self.timing_rereads = 0        # consecutive unread APPEAL frames (TIMING_REREAD)
        self.unknown_dir = UNKNOWN_DIR if input_enabled else None   # live runs only; tests may set a temp dir
        self.unknown_saved: set[str] = set()
        self.ocr_eval_saved: set[str] = set()
        self.number_events: list = []  # this decision's wing_numbers events (for the OCR evaluation sample)
        self.number_raw = None         # this decision's number fields as read, before wing_numbers
        self.numbers = wnum.NumberGuard()   # plausibility + two-capture agreement of the number readings
        self.appeal_followup = None
        self.live_feed = WingFeed()    # display only: decision log -> UI 「运行中」 view (agent.core.live_status)
        # Spend ledger (agent.core.wing_ledger): the launcher passes the private file for live runs; otherwise an
        # in-memory ledger (offline replay / tests) with the same rules inside this session.
        self.ledger = ledger if isinstance(ledger, wl.SpendLedger) else wl.SpendLedger(ledger)
        self.run_id = uuid.uuid4().hex[:12]
        self.spend_key = None          # ledger key of this run's own プロデュース開始
        self.ledger_pending = []       # unresolved entries of earlier runs found at begin (no start until resolved)
        self.rechecked = []            # their keys once Home showed no Produce in progress
        self._written = None           # last memory snapshot written (checkpoint de-duplication)
        # In-game プロデュース設定 record (agent.core.wing_options): the launcher passes the private file for live runs;
        # None (offline replay / tests) skips the check.
        self.settings_store = wo.SettingsStore(settings) if isinstance(settings, (str, Path)) else settings
        self.frame_seen = None         # (thumb digest, clock) of the first W.I.N.G. recognition on the current capture

    def _live(self):
        """Fold new decision-log entries into the active live status (no-op without a live run; never raises)."""
        try:
            self.live_feed.feed(self.runtime.log, self.runtime.memory)
        except Exception:
            pass

    # ---- lifecycle ------------------------------------------------------------------------------------------
    def _reset_round(self, screen=None):
        self.round_screen = screen
        self.round_decision = None    # (digest, NavDecision)

    def load_nodes(self):
        if self.nodes is None:
            self.nodes = wsc.load_nodes()
        return self.nodes

    def begin(self, params):
        """``SC.Wing.Begin``: optional ``produce_config`` (ProduceConfig dict), ``run`` (RunOptions parameters),
        ``memory_file`` (private JSON of a previous segment's snapshot, attach resume)."""
        from agent.core import produce_config as pc
        p = dict(params or {})
        run = dict(p.get("run") or {}) if isinstance(p.get("run"), dict) else p.get("run")
        # The launcher passes the Wing option ``ocr`` inside ``run`` (tools/run_tasks.py); it is not a RunOptions key.
        ocr = run.pop("ocr", p.get("ocr", "roi")) if isinstance(run, dict) else p.get("ocr", "roi")
        if ocr not in ("roi", "frame"):
            raise ValueError("ocr 只能为 roi/frame")
        self.ocr_mode = ocr
        options = nav.RunOptions.from_params(run, input_enabled=self.input_enabled,
                                             allow_start_spend=self.allow_start_spend,
                                             allow_reward_reroll=self.allow_reward_reroll)
        # Spend ledger (review P0-1): an unresolved プロデュース開始 of an earlier run blocks プロデュース開始 until a fresh
        # observation proves the state (Produce in progress -> confirmed; home without one -> rechecked).
        # An unreadable ledger forces attach without spend authority (never spend on doubt).
        self.run_id = uuid.uuid4().hex[:12]
        self.spend_key = None
        try:
            self.ledger_pending = self.ledger.pending()
            ledger_error = None
        except wl.LedgerError as exc:
            self.ledger_pending, ledger_error = [{"key": "unreadable", "state": "uncertain"}], str(exc)
        forced = None
        if ledger_error is not None and (options.mode == "start" or options.allow_start_spend):
            forced = options.mode
            options = dataclasses.replace(options, mode="attach", allow_start_spend=False, unit_search=False,
                                          minigame="off")
        config = pc.ProduceConfig.from_dict(p["produce_config"]) if isinstance(p.get("produce_config"), dict) else None
        if config is not None:
            runtime = nav.WingNavRuntime.from_produce_config(config, owned=p.get("owned_cards"), options=options)
        else:
            runtime = nav.WingNavRuntime(options=options)
        self.config = config
        if self.ledger_pending:
            runtime.log.append({"event": "spend_ledger_unresolved", "forced_attach_from": forced,
                                "entries": [{k: e.get(k) for k in ("key", "state", "action")} for e in self.ledger_pending],
                                "error": ledger_error})
        # A refused restore must not leave memory_path set: report() would overwrite the saved file.
        self.memory_path = None
        path = private_path(p["memory_file"]) if p.get("memory_file") else None
        self._written = None
        if path is not None and path.is_file():
            self._restore_memory(runtime, path)
        self.memory_path = path
        self.runtime = runtime
        configure_timing = getattr(self.authority, "configure_appeal_timing", None)
        if callable(configure_timing):
            configure_timing(options.live_timed_click)
        self.run = WingRun()
        self.run.reported = False
        self.run.started_at = self.clock()
        self._reset_round()
        self.requeues = self.decisions = 0
        self.pending_act, self.round_pre, self.unsent, self.stale_decisions = None, None, {}, 0
        self.numbers = wnum.NumberGuard()
        if self.settings_store is not None:
            try:
                record, settings_error = self.settings_store.load(), None
            except (OSError, ValueError) as exc:
                record, settings_error = None, str(exc)
            runtime.settings = wo.SettingsGate(record)
            runtime.log.append({"event": "produce_settings_loaded", "status": runtime.settings.status(),
                                "error": settings_error})
        if options.input_enabled:
            from agent.core import wing_hand_cache as whc
            runtime.hand_cache_path = whc.PATH          # 设置 → 存储「手牌识别」
        runtime.begin()
        runtime.log.append({"event": "options", "mode": options.mode, "input_enabled": options.input_enabled,
                            "allow_start_spend": options.allow_start_spend, "sp_mode": options.sp_mode,
                            "segment_acts": options.segment_acts,
                            "allow_reward_reroll": options.allow_reward_reroll})
        self._live()
        return True

    def _restore_memory(self, runtime, path) -> None:
        """Restore only a matching Produce. Changed known settings leave the checkpoint intact for recovery;
        legacy identities without a verified context are checked again on the next Hub."""
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("运行记忆格式无效")
        ident = data.get("produce") if isinstance(data.get("produce"), dict) else None
        p_card = runtime.memory.team_slots.get("P")
        mismatch = None
        if ident is not None:
            if p_card and ident.get("p_card") != p_card:
                mismatch = "p_card"
            elif ident.get("scenario", "wing") != runtime.scenario():
                mismatch = "scenario"           # a W.I.N.G. memory never resumes a say "Halo" run, nor the reverse
        if mismatch:
            target = archive_memory(path, "mismatch")
            runtime.log.append({"event": "memory_identity_mismatch", "field": mismatch, "saved": ident,
                                "config_p_card": p_card, "archived": target.name if target else None})
            return
        if ident is not None:
            saved_difficulty = ident.get("difficulty")
            if saved_difficulty in nav.DIFFICULTY_CHOICES and saved_difficulty != runtime.memory.difficulty:
                raise ValueError("当前育成的难度与设置不一致，请恢复原难度后继续")
            context = ident.get("verified_context")
            if isinstance(context, dict) and context != runtime.resume_context():
                raise ValueError("当前育成的编队或等级、凸数设置已变化，请恢复本局设置后继续")
        runtime.memory.restore(data)
        runtime.memory.segment_resumed = ident is not None
        sp = runtime.memory.sp_session
        if isinstance(sp, dict) and sp.get("learning") is None:
            # 2026-10-25 live (wing7): a segment that stopped on an unreadable panel had used its one re-entry; the
            # resumed segment may re-enter 振り返り once more (back key + 振り返り, nothing spent).
            sp.update(reentered=False, unstable=0, zoom_attempted=False)   # view unknown: zoom out again if needed
            if sp.get("last_tap") is not None:
                # 2026-10-25 live (wing6): the saved inspect tap of the last segment had used up its waits and the info
                # bar was gone, so the resumed segment stopped on its first frame (sp_info_unread). An inspect tap
                # spends nothing: wing_nav drops it after a short wait (``resumed_tap``, never set before).
                sp["resumed_tap"] = True
        runtime.log.append({"event": "memory_restored", "wins": len(runtime.memory.wins), "identity": ident})
        self._written = json.dumps(runtime.memory.snapshot(), ensure_ascii=False, sort_keys=True, default=str)

    # ---- memory checkpoints / spend ledger ------------------------------------------------------------------
    def checkpoint(self, force: bool = False) -> bool:
        """Write the memory snapshot atomically when it changed (review P2-3: also mid-run, not only at report)."""
        if self.memory_path is None:
            return False
        snap = self.runtime.memory.snapshot()
        text = json.dumps(snap, ensure_ascii=False, sort_keys=True, default=str)
        if not force and text == self._written:
            return False
        try:
            atomic_write_json(self.memory_path, snap)
        except OSError:
            LOGGER.exception("W.I.N.G. memory checkpoint not written")
            return False
        self._written = text
        return True

    def _ledger_mark(self, key, state, note=None):
        try:
            entry = self.ledger.mark(key, state, note)
        except wl.LedgerError as exc:
            self.runtime.log.append({"event": "spend_ledger_error", "key": key, "state": state, "error": str(exc)})
            return None
        self.runtime.log.append({"event": "spend_ledger", "key": key, "state": state, "note": note})
        return entry

    def observe(self, screen) -> None:
        """A classified screen of a fresh frame: resolves the spend ledger on proof of a running Produce."""
        if screen not in nav.IN_PRODUCE_SCREENS and screen != "ResumeDialog":
            return
        rt = self.runtime
        if self.spend_key is not None and self.spend_resolved != self.spend_key and screen in nav.IN_PRODUCE_SCREENS:
            entry = None
            try:
                entry = self.ledger.get(self.spend_key)
            except wl.LedgerError:
                pass
            if entry is not None and entry.get("state") in ("intent", "sent"):
                if self._ledger_mark(self.spend_key, "confirmed", f"observed:{screen}") is not None:
                    self.spend_resolved = self.spend_key
                self._start_confirmed()
            elif entry is not None:
                self.spend_resolved = self.spend_key   # confirmed/uncertain: no later observation changes it
        if self.ledger_pending:
            for entry in self.ledger_pending:
                if entry.get("key") != "unreadable":
                    self._ledger_mark(entry["key"], "confirmed", f"produce_in_progress_observed:{screen}")
            rt.log.append({"event": "spend_ledger_resolved", "screen": screen,
                           "keys": [e.get("key") for e in self.ledger_pending]})
            self.ledger_pending = []

    def _start_confirmed(self) -> None:
        """Maa's own プロデュース開始 took effect: archive the previous Produce's memory (review P1-1: only here, never
        on attach) and bind a fresh memory to this Produce."""
        rt = self.runtime
        target = archive_memory(self.memory_path, "replaced") if self.memory_path is not None else None
        rt.memory.reset_for_new_produce()
        rt.adopt_identity("start", run_id=self.spend_key)
        rt.log.append({"event": "produce_started", "ledger": self.spend_key,
                       "archived": target.name if target else None})
        self._written = None
        self.checkpoint(force=True)

    def report(self):
        """One ``wing`` report (+ a ``fail`` report naming the stop reason) and the memory snapshot for attach."""
        rt = self.runtime
        if self.run.reported:
            return
        self.run.reported = True
        self._live()
        outcome = rt.finished or ("running" if rt.active else "not_started")
        ledger = self._ledger_report()
        payload = {"spend_ledger": ledger, "outcome": outcome, "acts": rt.acts, "decisions": self.decisions,
                   "classify": dict(self.classify_counts),
                   "mode": rt.options.mode, "input_enabled": rt.options.input_enabled,
                   "start_attempted": rt.memory.start_attempted, "wins": [list(w) for w in rt.memory.wins],
                   "true_end_seen": rt.memory.true_end_seen, "notes": list(self.run.notes),
                   "resume": rt.memory.resume_info, "stop_text": getattr(rt, "stop_text", None),
                   "log": rt.log[-LOG_TAIL:]}
        if getattr(rt, "end_payload", None) is not None:
            payload["end_summary"] = rt.end_payload   # produce_end.summary_payload -> UI result view (ANN-29)
        if self.memory_path is not None:
            try:  # full decision trace (the report keeps only LOG_TAIL); private, next to the memory file
                trace = self.memory_path.parent / "decision-log"
                trace.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
                with open(trace / f"{stamp}.jsonl", "w", encoding="utf-8") as fh:
                    for entry in rt.log:
                        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            except Exception:
                LOGGER.exception("W.I.N.G. decision trace not written")
            rv = rt.memory.reward_review
            if isinstance(rv, dict) and rv.get("books") and not rv.get("ingested"):
                try:  # 獲得報酬 books -> private knowhow inventory (pending until a K-series scan reconciles them)
                    from agent.core import knowhow_books as kb
                    inv = kb.load_inventory()
                    stamp_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
                    payload["knowhow_ingested"] = kb.ingest_reward_books(inv, rv["books"].values(), seen_at=stamp_iso)
                    kb.save_inventory(inv)
                    rv["ingested"] = True
                    kb.export_ui_module(inv)   # ui/game-assets/knowhow.js (gitignored) -> ノウハウ page「W.I.N.G. 新获得」
                except Exception:
                    LOGGER.exception("W.I.N.G. reward books not stored in the knowhow inventory")
            try:
                if outcome in PRODUCE_END_OUTCOMES and rt.memory.end is not None:
                    # Confirmed end (獲得報酬 handover / home after the end flow): archive, never resume it again.
                    self.checkpoint(force=True)
                    target = archive_memory(self.memory_path, "ended")
                    payload["memory_archived"] = str(target) if target else None
                else:
                    self.checkpoint(force=True)
                    payload["memory_file"] = str(self.memory_path)
            except OSError as exc:
                payload["memory_file_error"] = str(exc)
        if self.sink is not None:
            self.sink.add("wing", json.loads(json.dumps(payload, ensure_ascii=False, default=str)))
            if outcome not in ("produce_finished_home", "finished"):
                self.sink.add("fail", {"reason": "wing:" + str(outcome)})
        LOGGER.info("W.I.N.G.: %s", json.dumps(payload, ensure_ascii=False, default=str)[:2000])

    def _ledger_report(self) -> dict:
        """End of run: this run's own start still unconfirmed -> ``uncertain`` (never retried). Summary for the
        segment loops (tools/wing_segments.py, tools/ui_engine.py): ``uncertain`` stops them."""
        state = None
        if self.spend_key is not None:
            try:
                entry = self.ledger.get(self.spend_key)
                state = entry.get("state") if entry else "missing"
            except wl.LedgerError:
                state = "unreadable"
            if state in ("intent", "sent", "missing", "unreadable"):
                self._ledger_mark(self.spend_key, "uncertain", f"run_ended:{self.runtime.finished}")
                state = "uncertain"
        pending = [e.get("key") for e in self.ledger_pending]
        return {"key": self.spend_key, "state": state, "unresolved_before": pending, "rechecked": list(self.rechecked),
                "uncertain": state == "uncertain" or bool(pending)}

    # ---- recognitions ---------------------------------------------------------------------------------------
    def frame_digest(self, image) -> str:
        """Pixel digest of the frame. All checks of one Nav.Loop pass see the same capture, so the digest is reused
        for the same capture number when the frame's pixel samples are identical; otherwise it is hashed."""
        try:
            seq = self.capture_seq() if self.capture_seq is not None else None
        except Exception:
            seq = None
        samples = _samples(image) if seq is not None else None
        memo = self._digest_memo
        if memo is not None and samples is not None and memo[0] == seq:
            try:
                if memo[1].shape == samples.shape and (memo[1] == samples).all():
                    return memo[2]
            except Exception:
                pass
        digest = _digest(image)
        if samples is not None:
            try:
                self._digest_memo = (seq, samples.copy(), digest)
            except Exception:
                self._digest_memo = None
        return digest

    def backend(self, context, digest, alias=None):
        inner = self.backend_factory(context)
        return (FrameOcrBackend(inner, self.ocr_cache, digest) if self.ocr_mode == "frame"
                else RoiOcrBackend(inner, self.ocr_cache, digest, alias))

    def _full_alias(self, screen, image):
        """Digest of this round's whole-frame classification whose OCR may answer the decision frame's whole-frame
        read (FULL_REUSE_SCREENS), or None."""
        ref, last = self.full_ref, self.last_act
        if (screen not in FULL_REUSE_SCREENS or ref is None or ref[0] != screen or ref[2] not in self.ocr_cache
                or last is None or last[0] != screen):
            return None
        try:
            return ref[2] if wspeed.same_view(ref[1], wspeed.thumb(image)) else None
        except Exception:
            return None

    def classify(self, context, image, hint=None) -> str | None:
        """Screen of the frame (first guard hit in dispatch order) from one whole-frame OCR; cached per digest.

        Shortcuts (agent/core/wing_speed.py, audited on every labelled demo frame): a frame that is the same view as
        the last full/hint classification keeps it (the reference thumbnail is not moved by reuse, so a slow fade
        cannot creep); otherwise ``hint`` (the screen that followed the same click before / the last screen) is
        tested with its native guard and its audited confusers. A miss of both runs the full classification."""
        digest = self.frame_digest(image)
        if digest not in self.screens:
            while len(self.screens) >= 8:
                self.screens.pop(next(iter(self.screens)))
            view = wspeed.thumb(image)
            ref = self.view_ref
            if ref is not None and wspeed.same_view(ref[1], view):
                screen, how = ref[0], "same_view"
            elif hint is not None and self._hint_hit(context, image, hint):
                screen, how = hint, "hint"
                self.view_ref, self.full_ref = (screen, view), None
            else:
                backend = FrameOcrBackend(self.backend_factory(context), self.ocr_cache, digest)
                classifier = wsc.ScreenClassifier(self.load_nodes(), backend, {"SC.Wing.State": lambda p, f: True})
                screen = classifier.classify(frame_of(image)).screen
                how = "full" if hint is None or hint not in wspeed.HINT_CONFUSERS else "hint_miss"
                self.view_ref = (screen, view) if screen is not None and view is not None else None
                self.full_ref = (screen, view, digest) if self.view_ref is not None else None
                if screen is None:
                    self._keep_unknown(image, digest)
            self.screens[digest] = screen
            self.classify_counts[how] += 1
            if screen is not None:
                self.last_screen = screen
        screen = self.screens[digest]
        if screen is None:
            self.screen_since = None
        elif self.screen_since is None or self.screen_since[0] != screen:
            self.screen_since = (screen, self.clock())
        return screen

    def _schedule_static(self, screen):
        """Header/status/promise of the Schedule round right before a room tap (wing_screen.SCHEDULE_STATIC_KEY): valid
        while that tap is the only act since (a room tap only raises a card; 2026-10-25 speed batch)."""
        kept = getattr(self, "schedule_static", None)
        last = self.last_act
        if (screen != "Schedule" or not kept or last is None or last[0] != "Schedule"
                or not str(last[1]).startswith("schedule_room_") or self.runtime.acts != kept[0] + 1):
            return None
        return kept[1]

    def _hint_hit(self, context, image, screen) -> bool:
        """The screen's native guard hits and none of its audited confusers' guards does (wing_speed.HINT_CONFUSERS);
        a screen without an audit entry is never a hint."""
        confusers = wspeed.HINT_CONFUSERS.get(screen)
        if confusers is None:
            return False
        try:
            evaluator = wsc.Evaluator(self.load_nodes(), self.backend_factory(context),
                                      {"SC.Wing.State": lambda p, f: True})
            frame = frame_of(image)
            return (evaluator.hit(wsc.SCREEN_PREFIX + screen, frame)
                    and not any(evaluator.hit(wsc.SCREEN_PREFIX + other, frame) for other in confusers))
        except Exception:   # an odd frame only loses the shortcut
            LOGGER.exception("W.I.N.G. hint check failed on %s", screen)
            return False

    def _keep_appeal_frame(self, image) -> None:
        """2026-10-14 (Perfect calibration): the first frames after a sent timed APPEAL show our own judgement. Keep
        a few (PNG + the timing plan, private) to compare the planned aim with the result; read-only, never raises."""
        f = getattr(self, "appeal_followup", None)
        if not f or f["left"] <= 0 or image is None:
            return
        try:
            digest = self.frame_digest(image)
            if digest in f["digests"]:
                return
            from PIL import Image
            folder = Path(self.unknown_dir).parent / "wing_timing" / f["stamp"][:8]
            folder.mkdir(parents=True, exist_ok=True)
            name = f"{f['stamp']}-{f['n']}"
            Image.fromarray(image[:, :, ::-1].copy()).save(folder / f"{name}.png")
            if f["n"] == 0:
                atomic_write_json(folder / f"{f['stamp']}-plan.json", {"plan": f["plan"], "saved_at": time.time()})
            f["digests"].add(digest)
            f["n"] += 1
            f["left"] -= 1
        except Exception:   # diagnostics only: never blocks a run
            LOGGER.exception("saving an APPEAL follow-up frame failed")
            self.appeal_followup = None

    def _check_numbers(self, screen, readings, digest):
        """wing_numbers on this decision's readings; events go to the decision log (and the eval sample)."""
        try:
            seq = self.capture_seq() if self.capture_seq is not None else None
        except Exception:
            seq = None
        # A new controller capture is a new frame for the vote; without capture numbers (offline) each decision is.
        capture = (seq if seq is not None else ("decision", self.decisions), digest)
        rt = self.runtime
        if rt.memory.numbers is None:
            rt.memory.numbers = {}
        self.number_raw = wnum.flatten(readings)     # as read (the eval sample keeps the misread)
        verdict = self.numbers.check(screen, readings, rt.memory.numbers, capture, rt.memory.lesson_probe)
        rt.log.extend(verdict.events)
        self.number_events = [e for e in verdict.events if e["event"] != "number_confirmed"]
        return verdict

    def _keep_ocr_eval(self, image, digest, screen, decision, readings) -> None:
        """Save the frame of a rejected / unconfirmed number or of a decision that stopped or waited on an unread field
        (``*unread*`` in its reason or problems) for the OCR evaluation; never raises."""
        events, raw = self.number_events, self.number_raw
        self.number_events, self.number_raw = [], None
        detail = decision.detail or {}
        problems = [str(p) for p in detail.get("problems") or ()] if isinstance(detail, dict) else []
        # 2026-10-29 wing6: all 20 samples were Live animation waits (live:phase_unread, no numbers on screen).
        unread = decision.action in ("stop", "wait") and decision.reason not in OCR_EVAL_SKIP_REASONS and any(
            "unread" in text for text in [str(decision.reason or ""), *problems])
        # Live runs only (as _live_trace): replay tests build input-enabled sessions on the shared private folder.
        if (self.unknown_dir is None or type(self.authority).__name__ != "LiveSpendAuthority"
                or getattr(image, "ndim", 0) != 3 or not (events or unread)
                or len(self.ocr_eval_saved) >= MAX_OCR_EVAL_SAMPLES or digest in self.ocr_eval_saved):
            return
        try:
            from PIL import Image
            stamp = time.strftime("%Y%m%dT%H%M%S")
            folder = Path(self.unknown_dir).parent / "ocr-eval" / stamp[:8]
            folder.mkdir(parents=True, exist_ok=True)
            name = f"{stamp}-{screen}-{digest[:8]}"
            Image.fromarray(image[:, :, ::-1].copy()).save(folder / f"{name}.png")
            rois = wsc.number_rois(screen, self.load_nodes(), image[:, :, ::-1])
            atomic_write_json(folder / f"{name}.json", {
                "screen": screen, "events": events, "reason": decision.reason, "problems": problems,
                "read": raw if raw is not None else wnum.flatten(readings), "rois": rois, "truth": None})
            self.ocr_eval_saved.add(digest)
            self.runtime.log.append({"event": "ocr_eval_saved", "file": f"{stamp[:8]}/{name}.png"})
        except Exception:   # diagnostics only: never blocks a run
            LOGGER.exception("saving an OCR evaluation frame failed")

    def _keep_unknown(self, image, digest) -> None:
        """Save an unclassified frame that shows real UI text (not a fade/loading frame); never raises."""
        if self.unknown_dir is None or len(self.unknown_saved) >= MAX_UNKNOWN_SAMPLES or digest in self.unknown_saved:
            return
        boxes = self.ocr_cache.get(digest) or []
        texts = [(t, list(b)) for t, b in boxes if str(t).strip()]
        if len(texts) < UNKNOWN_MIN_TEXTS:
            return
        try:
            from PIL import Image
            stamp = time.strftime("%Y%m%dT%H%M%S")
            folder = Path(self.unknown_dir) / stamp[:8]
            folder.mkdir(parents=True, exist_ok=True)
            name = f"{stamp}-{digest[:10]}"
            Image.fromarray(image[:, :, ::-1].copy()).save(folder / f"{name}.png")
            atomic_write_json(folder / f"{name}.json", {"digest": digest, "after": getattr(self, "round_screen", None),
                                                        "decisions": self.decisions, "texts": texts})
            self.unknown_saved.add(digest)
            self.runtime.log.append({"event": "unknown_screen_saved", "file": f"{stamp[:8]}/{name}.png",
                                     "texts": [t for t, _ in texts[:12]]})
        except Exception:   # diagnostics only: never blocks a run
            LOGGER.exception("saving an unknown W.I.N.G. frame failed")

    def _note_frame(self, image):
        """Clock of the first W.I.N.G. recognition on this capture. The frame permit (frame_permit.MAX_AGE_S) counts
        from the capture, so a decision's age does too: on SC.Wing.On.Review the guards and Picks before the decision
        already spend part of the 5 s (2026-10-23 round audit). A coarse thumbnail digest keeps this cheap."""
        try:
            key = hashlib.sha256(image[::16, ::16].tobytes()).hexdigest()
        except Exception:
            return
        if self.frame_seen is None or self.frame_seen[0] != key:
            self.frame_seen = (key, self.clock())

    def _frame_started(self, image, fallback):
        try:
            key = hashlib.sha256(image[::16, ::16].tobytes()).hexdigest()
        except Exception:
            return fallback
        seen = getattr(self, "frame_seen", None)
        return min(seen[1], fallback) if seen and seen[0] == key else fallback

    def state(self, params, image, context=None):
        self._note_frame(image)
        rt = self.runtime
        want = params.get("want")
        if want == "settled":
            # Last part of a Click node's And, after the decision approved the click and before it is sent.
            ok = rt.active_for(None, False)
            if ok:
                self._settle()
            return ok
        if want != "active" or params.get("wait"):
            # Every observation after the Click node's And (After/Hold/Requeue/Loop/next round); never the plain
            # ``active`` part of the Click And itself, which runs before the click is sent.
            self.settle_act()
            self._keep_appeal_frame(image)
        if want == "is":
            # Cheap pre-guard of SC.Wing.On.<Screen>: the native screen guard after it still has to hit.
            if context is None or not rt.is_active():
                return False
            screen = self.classify(context, image, hint=self.last_screen)
            self.observe(screen)
            hit = screen == params.get("screen")
            self.confirmed_thumb = (screen, _thumb(image)) if hit else None
            return hit
        if want == "expect":
            # After a click: the frame shows one of the expected screens (the Progress guard demands a new frame).
            # ``loose`` (plain advance taps, wing_nav.REOBSERVE_INTENTS): any recognised screen is accepted and the
            # next round re-observes it (2026-10-10: commu after the final -> 社長からの評価 stopped the run).
            if context is None or not rt.is_active():
                return False
            screen = self.classify(context, image, hint=self.successors.get(self.last_act))
            self.observe(screen)
            # Narrowed / widened per sent intent and the run's プロデュース設定 (WingNavRuntime.expect_screens).
            if screen in rt.expect_screens(params.get("screens") or ()):
                if self.last_act is not None:
                    self.successors[self.last_act] = screen    # the hint for this click next time
                return True
            if params.get("loose") and screen is not None and screen in nav.RULES:
                rt.log.append({"event": "reobserve", "screen": screen, "after": self.round_screen})
                return True
            return False
        if want == "progress":
            return rt.is_active() and rt.progress(self.frame_digest(image))
        if want == "round":
            if not rt.active_for(params.get("screen")):
                return False
            self.observe(params.get("screen"))
            self._reset_round(params.get("screen"))
            self.checkpoint()
            return True
        if want == "held":
            # A round whose click candidates are all disabled for this run (e.g. the Produce start without the spend
            # flag) still gets its decision here, so the latched stop reason is the real one.
            if (self.round_decision is None and context is not None and rt.is_active()
                    and self.round_screen == params.get("screen")):
                self.decision(context, image, params.get("screen"))
            decision = self.round_decision[1] if self.round_decision else None
            return (self.round_screen == params.get("screen") and decision is not None
                    and decision.action in ("stop", "finish"))
        if want == "requeue":
            if not rt.is_active():
                return False
            self.requeues += 1
            if self.requeues > MAX_REQUEUES:
                rt.stop(self.round_screen, "screen_unstable")
                return False
            rt.log.append({"event": "requeue", "screen": self.round_screen})
            # 2026-10-25 live (wing4): a static screen gives every capture the same thumbnail digest, so the frame
            # clock never moved and each re-decision on 振り返り looked older (4.9 s ... 62 s). The next round is a new
            # capture: its first recognition starts the clock again.
            self.frame_seen = None
            return True
        if want == "halted":
            return not rt.is_active()
        if params.get("wait") and self.round_decision and self.round_screen == params.get("screen"):
            decision = self.round_decision[1]
            if decision.action == "click":
                # 2026-10-13 live: the decision was a click whose Click node is disabled (unit_page_prev); the Hold
                # matched every round (each round resets the wait count) and the run spun for 8 minutes. Allow a few
                # rounds for a transient miss, then stop.
                key = (params.get("screen"), decision.intent)
                self.unrun_clicks = self.unrun_clicks + 1 if getattr(self, "unrun_key", None) == key else 1
                self.unrun_key = key
                if self.unrun_clicks > MAX_UNRUN_CLICKS:
                    rt.stop(params.get("screen"), f"click_unavailable:{decision.intent}")
                    return False
            else:
                self.unrun_key, self.unrun_clicks = None, 0
        ok = rt.active_for(params.get("screen"), bool(params.get("wait")))
        if ok and params.get("wait"):
            self.requeues = 0
            self.frame_seen = None     # a held round: the next round is a new capture (see ``requeue``)
        return ok

    # ---- click send proof -----------------------------------------------------------------------------------
    def _runtime_state(self):
        rt = self.runtime
        return {"memory": copy.deepcopy(rt.memory), "acts": rt.acts, "last_intent": rt._last_intent,
                "repeat": rt._repeat}

    def _rollback(self, pre) -> None:
        rt = self.runtime
        rt.memory = pre["memory"]
        rt.acts, rt._last_intent, rt._repeat = pre["acts"], pre["last_intent"], pre["repeat"]
        rt.acted_digest = None

    def _click_outcome(self, mark) -> str:
        """``sent`` / ``not_sent`` / ``unknown`` from the launcher's controller history (no authority: unknown)."""
        since = getattr(self.authority, "clicks_since", None)
        if mark is None or not callable(since):
            return "unknown"
        try:
            return since(mark)
        except Exception:  # never guess a send
            return "unknown"

    def settle_act(self):
        """Resolve the pending approved click against the controller history. ``sent`` keeps the decision's effects;
        ``not_sent`` (frame guard / gate / host refused before any gesture, live seg6/7/8) restores the runtime state
        from before the decision so the next round re-decides on a fresh frame -- once per screen+intent, never for a
        consuming intent. ``unknown`` keeps everything (the After/result checks stop the run)."""
        pending, self.pending_act = self.pending_act, None
        if pending is None:
            return None
        rt = self.runtime
        outcome = self._click_outcome(pending["mark"])
        rt.log.append({"event": "act_result", "screen": pending["screen"], "intent": pending["intent"],
                       "sent": outcome})
        key = (pending["screen"], pending["intent"])
        if pending.get("ledger"):
            if outcome == "sent":
                self._ledger_mark(pending["ledger"], "sent")
            elif outcome == "not_sent":
                self._ledger_mark(pending["ledger"], "not_sent", "controller_history_no_gesture")
        if outcome == "sent":
            self.unsent.clear()
            if pending["intent"] == "live_appeal_timing" and self.unknown_dir is not None:
                self.appeal_followup = {"left": APPEAL_FOLLOW_FRAMES, "n": 0, "digests": set(),
                                        "stamp": time.strftime("%Y%m%dT%H%M%S"),
                                        "plan": next((e for e in reversed(rt.log) if e.get("event") == "timing_plan"),
                                                     None)}
            return outcome
        if outcome != "not_sent":
            return outcome
        if pending["intent"] in nav.CONSUMING_INTENTS or pending["pre"] is None:
            rt.stop(pending["screen"], "click_not_sent:" + pending["intent"])
            return outcome
        if self.unsent.get(key, 0) >= UNSENT_RETRIES:
            # Nothing reached the game: the saved memory must not claim the tap (live seg29/30: an unsent SP inspect
            # tap stayed in last_tap and the next segment stopped on sp_info_slot_mismatch).
            self._rollback(pending["pre"])
            rt.stop(pending["screen"], "click_not_sent_again:" + pending["intent"])
            return outcome
        self._rollback(pending["pre"])
        self.unsent[key] = self.unsent.get(key, 0) + 1
        rt.log.append({"event": "act_rolled_back", "screen": pending["screen"], "intent": pending["intent"]})
        return outcome

    def decision(self, context, image, screen):
        """The round's decision, computed once on the first click candidate's frame (cached for the others)."""
        digest = self.frame_digest(image)
        if self.round_decision is not None:
            cached_digest, decision = self.round_decision
            if cached_digest != digest:   # a re-polled next list on a new frame: never reuse a stale decision
                self.runtime.stop(screen, "decision_frame_changed")
                return nav.NavDecision("stop", reason="decision_frame_changed")
            return decision
        rt = self.runtime
        readings = {}
        started = self.clock()
        frame_started = self._frame_started(image, started)   # the permit's reference point (capture), not this call
        laps = {}           # 2026-10-15: per-stage seconds, to find intermittent 5-8 s decisions in live runs
        try:
            frame = frame_of(image)
            alias = self._full_alias(screen, image)
            evaluator = wsc.Evaluator(self.load_nodes(), self.backend(context, digest, alias),
                                      {"SC.Wing.State": lambda p, f: True})
            laps["full_reused"] = alias is not None
            # Speed: the screen re-check uses the cached whole-frame OCR of this frame (the classifier's
            # guards); readers keep ROI OCR. The click node's native ROI guard still runs right before the tap.
            guard = wsc.Evaluator(self.load_nodes(), FrameOcrBackend(self.backend_factory(context), self.ocr_cache,
                                                                     digest), {"SC.Wing.State": lambda p, f: True})
            laps["setup"] = round(self.clock() - started, 3)
            # Speed (2026-10-15 live: a fresh whole-frame OCR here cost 3-7 s and pushed Hub/Schedule decisions past
            # DECISION_STALE_S): a frame that barely differs from the one SC.Wing.Is just confirmed for this screen
            # skips the re-check. The Click node's native screen guard still runs on this frame before the tap.
            same = _same_view(getattr(self, "confirmed_thumb", None), screen, image)
            laps["guard_skipped"] = same
            if screen != self.round_screen or not (same or guard.hit(wsc.SCREEN_PREFIX + screen, frame)):
                decision = nav.NavDecision("requeue", reason="screen_not_confirmed")
            else:
                laps["guard"] = round(self.clock() - started, 3)
                # 2026-10-15 live: a full Schedule read took 6-8 s on some frames and was requeued 4x in a row. The
                # readings of a too-slow decision are kept; the next frame of the same unchanged view reuses them.
                kept, self.slow_readings = getattr(self, "slow_readings", None), None
                timing = self._timing_shortcut(screen, frame)
                if timing is not None:
                    readings, laps["timing_shortcut"] = timing, True
                elif kept and kept[:2] == (screen, rt.acts) and _same_view((screen, kept[2]), screen, image):
                    readings, laps["readings_reused"] = copy.deepcopy(kept[3]), True
                else:
                    static = self._schedule_static(screen)
                    readings = (wsc.read_screen(screen, evaluator, frame) if static is None
                                else wsc.read_screen(screen, evaluator, frame, static=static))
                    laps["schedule_static"] = static is not None
                static = readings.pop(wsc.SCHEDULE_STATIC_KEY, None)
                verdict = self._check_numbers(screen, readings, digest)
                readings = verdict.readings
                if screen == "Live" and not laps.get("timing_shortcut"):
                    self.live_readings = (rt.acts, copy.deepcopy(readings))
                if screen != "Schedule":
                    self.schedule_static = None
                elif static is not None:
                    self.schedule_static = (rt.acts, static)
                laps["read"] = round(self.clock() - started, 3)
                self.round_pre = self._runtime_state()
                if verdict.hold:
                    # wing_numbers: a changed or impossible number is read again on the next capture before acting.
                    decision = nav.NavDecision("requeue", reason="number_recheck", detail={"fields": verdict.hold})
                else:
                    decision = self._ledger_home(screen, readings) or rt.decide(screen, readings)
                laps["decide"] = round(self.clock() - started, 3)
                if decision.action == "click" and self.clock() - frame_started > DECISION_STALE_S:
                    # Too slow for the frame permit: re-decide on a fresh frame from the same starting state.
                    self._rollback(self.round_pre)
                    rt.log.append({"event": "decision_stale", "screen": screen, "intent": decision.intent,
                                   "seconds": round(self.clock() - started, 3),
                                   "frame_seconds": round(self.clock() - frame_started, 3)})
                    decision = nav.NavDecision("requeue", reason="decision_too_slow")
                    self.slow_readings = (screen, rt.acts, _thumb(image), copy.deepcopy(readings))
                    self.stale_decisions += 1
                    if self.stale_decisions > MAX_REQUEUES:   # Hold re-opens rounds: bound it here
                        decision = nav.NavDecision("stop", reason="decision_too_slow")
                elif decision.action == "click":
                    self.stale_decisions = 0
                    if decision.intent == "giveup_hold":
                        refusal = self._stage_giveup_hold()
                        if refusal:
                            self._rollback(self.round_pre)
                            decision = nav.NavDecision("stop", reason=refusal)
                    if decision.intent == "live_appeal_timing":
                        if rt.options.live_timed_click:
                            refusal = self._stage_appeal_timing(image, frame)
                            if refusal:
                                self._rollback(self.round_pre)
                                self.timing_rereads += 1
                                if refusal in TIMING_REREAD and self.timing_rereads <= MAX_TIMING_REREADS:
                                    decision = nav.NavDecision("requeue", reason=refusal)
                                else:
                                    decision = nav.NavDecision("stop", reason=refusal)
                            else:
                                self.timing_rereads = 0
                        elif TIMING_ALIGN:
                            self._align_appeal(frame, started)
        except Exception as exc:  # unreadable or scorer failure -> stop, never guess
            LOGGER.exception("SC.Wing.Decide failed on %s", screen)
            decision = nav.NavDecision("stop", reason=f"decide_error:{type(exc).__name__}")
        self.decisions += 1
        self._keep_ocr_eval(image, digest, screen, decision, readings)
        rt.log.append({"event": "decide", "screen": screen, "decision": decision.to_dict(),
                       "seconds": round(self.clock() - started, 3), "laps": laps,
                       "readings": {k: v for k, v in readings.items() if k not in ("skill_nodes", "texts")}})
        self._live_trace(screen, decision, round(self.clock() - started, 3), laps, readings)
        self._save_settings()
        if decision.action == "stop":
            text = dict(decision.detail or {}).get("text")
            if rt.finished is None and isinstance(text, str) and text.strip():
                rt.stop_text = text.strip()[:160]   # UX review #12: the UI shows it after the code's own text
            rt.stop(screen, decision.reason or "no_decision")
        elif decision.action in ("click", "wait"):
            self.requeues = 0
        elif decision.action == "finish":
            rt.finished, rt.active = decision.reason or "finished", False
        rt.pending_decision = decision
        self.round_decision = (digest, decision)
        self._live()
        return decision

    def _save_settings(self) -> None:
        g = self.runtime.settings
        if g is None or not g.dirty or self.settings_store is None:
            return
        try:
            self.settings_store.save(g.record)
            g.dirty = False
            self.runtime.log.append({"event": "produce_settings_saved", "status": g.status()})
        except OSError as exc:
            self.runtime.log.append({"event": "produce_settings_save_error", "error": str(exc)})

    def _live_trace(self, screen, decision, seconds, laps, readings=None) -> None:
        """2026-10-15 speed work: one line per decision, flushed at once (the decision log is only written at segment
        end), next to the unknown-frame samples. Diagnostics only; never raises."""
        # Live runs only (the launcher's LiveSpendAuthority): unit-test sessions must not write the shared trace.
        if self.unknown_dir is None or type(self.authority).__name__ != "LiveSpendAuthority":
            return
        try:
            path = Path(self.unknown_dir).parent / "decision-live.jsonl"
            row = {"t": time.strftime("%H:%M:%S"), "screen": screen, "action": decision.action,
                   "intent": decision.intent, "reason": decision.reason, "seconds": seconds, "laps": laps}
            if screen in ("AuditionTab", "AuditionLocked") and readings:
                # 強化週間 survey: which page was read as which audition
                row["page"] = {k: readings.get(k) for k in ("season", "weeks_left", "reward", "special",
                                                            "required_rank", "recommended")}
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            LOGGER.exception("live decision trace failed")

    def _ledger_home(self, screen, readings):
        """Home while an earlier start is unresolved: 「プロデュース中」 proves a running Produce (resolved, attach);
        plain 「プロデュース」 means the stamina may have been spent without a Produce -> stop for the user."""
        if not self.ledger_pending or screen != "Home":
            return None
        if readings.get("home_produce_resume_label") is True:
            self.observe("ResumeDialog")      # a Produce is in progress: same proof as its resume dialog
            return None
        if readings.get("home_produce_label") is True and all(e.get("key") != "unreadable" for e in self.ledger_pending):
            # No Produce in progress: the earlier start is rechecked (不确定只提醒) and this run decides
            # from Home as usual (a start run may open one).
            for entry in self.ledger_pending:
                self._ledger_mark(entry["key"], "rechecked", "home_without_produce")
            self.runtime.log.append({"event": "spend_ledger_rechecked", "screen": screen,
                                     "keys": [e.get("key") for e in self.ledger_pending]})
            self.rechecked = [e.get("key") for e in self.ledger_pending]
            self.ledger_pending = []
            return None
        if readings.get("home_produce_label") is True:
            return nav.NavDecision("stop", reason="start_spend_uncertain",
                                   detail={"ledger": [e.get("key") for e in self.ledger_pending],
                                           "hint": "上次开新一局（体力 30）结果不确定：请在游戏里确认体力与是否在培养中，"
                                                   "再用 tools/wing_spend_ledger.py resolve 记录"})
        return None

    def _stage_giveup_hold(self):
        """The next wing-giveup-hold click is a long press (host hold v2); a refusal stops before any input."""
        rt = self.runtime
        stage = getattr(self.authority, "stage_hold", None)
        action = nav.action_id("giveup_hold")
        refusal = stage(action, nav.GIVEUP_HOLD_MS) if callable(stage) else "hold_host_unavailable"
        rt.log.append({"event": "hold", "action": action, "hold_ms": nav.GIVEUP_HOLD_MS, "refusal": refusal})
        return refusal

    def _stage_appeal_timing(self, image, frame):
        from agent.core import wing_live_timing as wlt
        rt = self.runtime
        stage = getattr(self.authority, "stage_appeal_timing", None)
        if not callable(stage):
            return "appeal_timing_host_unavailable"
        refusal, plan = stage(image, wlt.read_timing_bar(frame.image),
                              period_s=rt.options.live_timing_period_s,
                              event_offset_ms=rt.options.live_timing_event_offset_ms)
        rt.log.append({"event": "timing_plan", "mode": "host_clock", "refusal": refusal, "plan": plan})
        return refusal

    def _align_appeal(self, frame, started) -> None:
        """Delay the single APPEAL tap so it lands in the Perfect zone (agent.core.wing_live_timing: the cursor is a
        ~0.97 s sawtooth; decision return -> pointer-down ≈ 0.71 s). Unreadable bar -> tap now (old behaviour). The
        wait is at most one period and stays inside DECISION_STALE_S; still exactly one guarded tap."""
        from agent.core import wing_live_timing as wlt
        rt = self.runtime
        try:
            delay, plan = wlt.align_delay(frame.image, elapsed_s=self.clock() - started)
        except Exception as exc:  # timing is an optimisation: never block the tap on it
            rt.log.append({"event": "timing_plan", "error": type(exc).__name__})
            return
        if delay and self.clock() - started + delay + APPEAL_FRAME_MARGIN_S <= DECISION_STALE_S:
            time.sleep(delay)
        else:
            delay = 0.0
        rt.log.append({"event": "timing_plan", "delay_s": round(delay, 3),
                       "plan": dataclasses.asdict(plan) if plan is not None and dataclasses.is_dataclass(plan) else None})

    def _timing_shortcut(self, screen, frame) -> dict | None:
        """Speed (2026-10-28: the APPEAL frame's whole-frame OCR took ~1.2 s and the host found no Perfect time left
        before the frame's 5 s deadline). Right after the turn's judge tap, a Live frame showing the APPEAL bar can
        only be the timing phase: the turn's readings taken before that tap, with phase ``timing``, decide the APPEAL
        without OCR. The runtime gates and the Click node's native screen guard still run. None otherwise."""
        rt = self.runtime
        kept = getattr(self, "live_readings", None)
        step = rt.memory.live_step if isinstance(rt.memory.live_step, dict) else {}
        if (screen != "Live" or not self.last_act or not str(self.last_act[1]).startswith("live_judge_")
                or step.get("step") != "judge" or not kept or kept[0] != rt.acts - 1):
            return None
        from agent.core import wing_live_timing as wlt
        try:
            bar = wlt.read_timing_bar(frame.image)
        except Exception:
            return None
        if bar is None or bar.cursor_x is None or bar.perfect_aim() is None:
            return None
        return {**copy.deepcopy(kept[1]), "live_phase": "timing", "tooltip": None, "timing_shortcut": True}

    def _settle(self) -> None:
        """Wait out the rest of SETTLE_S since the approved click's screen was first classified (unknown: all of it)."""
        intent = self.last_act[1] if self.last_act else None
        since, self.settle_since = self.settle_since, None
        if intent in NO_SETTLE_INTENTS:
            return
        remaining = SETTLE_S if since is None else SETTLE_S - (self.clock() - since)
        if remaining > 0:
            self.sleep(min(remaining, SETTLE_S))

    def pick(self, context, image, screen, intent):
        """``SC.Wing.Decide``: hit (box = the intent's rect) only for the decided intent after every Agent gate."""
        if not self.runtime.is_active() or screen != self.round_screen:
            return None
        decision = self.decision(context, image, screen)
        if decision.action != "click" or decision.intent != intent:
            return None
        rt = self.runtime
        if intent == "start_confirm_ok" and self.ledger_pending:
            # An earlier start is still unresolved: Home without a Produce was not observed in this run. No tap.
            rt.stop(screen, "start_spend_uncertain")
            self.round_decision = (self.round_decision[0], nav.NavDecision("stop", reason="start_spend_uncertain"))
            return None
        sent, why = rt.act(screen, decision)
        # ``approved`` only: whether the tap reached the game is logged later by ``act_result`` (controller history).
        rt.log.append({"event": "act", "screen": screen, "intent": intent, "approved": sent, "why": why})
        if not sent:
            rt.stop(screen, "act_refused:" + why)
            self.round_decision = (self.round_decision[0], nav.NavDecision("stop", reason="act_refused:" + why))
            return None
        if intent in nav.CONSUMING_INTENTS:
            refusal = ("spend_authority_missing" if self.authority is None else
                       self.authority.authorize(nav.CONSUMING_INTENTS[intent], image, APPROVER))
            rt.log.append({"event": "spend_grant", "action": nav.CONSUMING_INTENTS[intent], "refusal": refusal})
            if refusal:
                rt.stop(screen, "spend_grant_refused:" + str(refusal))
                self.round_decision = (self.round_decision[0], nav.NavDecision("stop", reason="spend_grant_refused"))
                return None
            if intent == "start_confirm_ok":
                # Durable intent BEFORE the tap (review P0-1); no ledger entry -> no tap.
                try:
                    self.spend_key = self.ledger.record_intent(
                        nav.CONSUMING_INTENTS[intent], run_id=self.run_id, frame_sha=self.frame_digest(image),
                        detail={"screen": screen, **{k: v for k, v in decision.detail.items() if k == "stamina"}})
                except wl.LedgerError as exc:
                    rt.stop(screen, "spend_ledger_unwritable")
                    rt.log.append({"event": "spend_ledger_error", "error": str(exc)})
                    self.round_decision = (self.round_decision[0],
                                           nav.NavDecision("stop", reason="spend_ledger_unwritable"))
                    return None
                rt.log.append({"event": "spend_ledger", "key": self.spend_key, "state": "intent"})
        rt.acted_digest = self.frame_digest(image)
        since = self.screen_since
        self.settle_since = since[1] if since is not None and since[0] == screen else None
        self.screen_since = None
        # The click changes the view: the next frames are classified afresh (or by the successor hint).
        self.view_ref, self.full_ref, self.last_act = None, None, (screen, intent)
        mark = getattr(self.authority, "click_mark", None)
        try:
            mark = mark() if callable(mark) else None
        except Exception:
            mark = None
        self.pending_act = {"screen": screen, "intent": intent, "mark": mark, "pre": self.round_pre,
                            "ledger": self.spend_key if intent == "start_confirm_ok" else None}
        return decision

    # ---- actions --------------------------------------------------------------------------------------------
    def stop(self, params):
        rt = self.runtime
        if self.pending_act is not None:
            intent = self.pending_act["intent"]
            active = rt.is_active()
            outcome = self.settle_act()
            if outcome == "not_sent" and active and rt.is_active() and params.get("reason") == "clickUnconfirmed":
                # The controller refused the tap before any gesture (live seg6/7: the host frame binding changed after
                # a manual mouse move). The frame guard stays poisoned for this Maa task, so the segment ends with the
                # runtime state rolled back; the memory file lets the next attach re-decide from the live screen.
                rt.stop(params.get("screen"), "click_not_sent_resumable:" + intent)
        pending = rt.pending_decision
        if pending is not None and pending.action == "finish":
            return self.finish({"reason": pending.reason})
        reason = params.get("reason") or "stop"
        if pending is not None and pending.action == "stop" and reason in ("no_decision", "noDecision", "halted"):
            reason = pending.reason
        # An unknown frame where the unsampled W.I.N.G. warning popup may show (setting ON): say so.
        reason = rt.explain_stop(params.get("screen") or self.round_screen, reason)
        rt.stop(params.get("screen"), reason)
        self.report()
        return False

    def finish(self, params):
        rt = self.runtime
        rt.log.append({"event": "finish", "reason": params.get("reason"), "true_end_seen": rt.memory.true_end_seen})
        rt.finished = params.get("reason") or "finished"
        rt.active = False
        self.report()
        return True


def registrations(session: WingSession):
    """({name: CustomRecognition}, {name: CustomAction}) bound to ``session`` (launcher / tests)."""
    from maa.custom_action import CustomAction
    from maa.custom_recognition import CustomRecognition

    class State(CustomRecognition):
        def analyze(self, context, argv):
            p = tc.params(argv.custom_recognition_param)
            ok = session.state(p, argv.image, context)
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail=p) if ok else None

    class Decide(CustomRecognition):
        def analyze(self, context, argv):
            p = tc.params(argv.custom_recognition_param)
            decision = session.pick(context, argv.image, p.get("screen"), p.get("intent"))
            if decision is None:
                return None
            return CustomRecognition.AnalyzeResult(box=tuple(decision.target), detail=decision.to_dict())

    class Begin(CustomAction):
        def run(self, context, argv):
            try:
                return session.begin(tc.params(argv.custom_action_param))
            except (TypeError, ValueError, KeyError, OSError) as exc:
                LOGGER.error("SC.Wing.Begin refused: %s", exc)
                session.run.notes.append(f"begin_refused:{exc}")
                session.runtime.stop(None, "begin_refused")
                session.run.reported = False
                session.report()
                return False

    class Stop(CustomAction):
        def run(self, context, argv):
            return session.stop(tc.params(argv.custom_action_param))

    class Finish(CustomAction):
        def run(self, context, argv):
            return session.finish(tc.params(argv.custom_action_param))

    return ({"SC.Wing.State": State(), "SC.Wing.Decide": Decide()},
            {"SC.Wing.Begin": Begin(), "SC.Wing.Stop": Stop(), "SC.Wing.Finish": Finish()})
