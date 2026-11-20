"""Maa glue for the self-scrolling アイドル一覧 scan (SC.CardScan.*): readers bound to the Maa context, the
step-bound decision recognitions, and the begin / advance / finish / stop actions. Rules live in ``card_list_scan``.

**No action here sends input.** The input steps of ``card_scan.json`` (open アイドル, open アイドル一覧, tab taps,
wheel steps, back) are table-bound Click nodes guarded by ``SC.CardScan.Want`` below. Their Click nodes stay
``enabled: false`` in the JSON; only the one-shot ``tools/run_tasks.py --live --supervised-task`` CardScan entry enables
them, for exactly the action ids in ``run_tasks.SUPERVISED_TASK_ACTIONS["CardScan"]``.

Every observed list capture is saved as ``<run_dir>/card-scan-<UTC>/frame-NNN.png`` (NNN = the session frame number used
in the decision log and in each tab's ``shifts``), next to the run record ``<run_dir>/card-scan-<UTC>[-partial].json``,
so a supervised calibration run can be checked frame by frame.

The scan output goes only to the reviewed-import files (``ui/game-assets/owned-levels.js`` and
``inventory-scan.js``, both gitignored) and to a private run record; it never writes the user profile. A partial
scan (any tab not proven complete) is kept private and does not replace the import files.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
from pathlib import Path

from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from agent.core import card_list_scan as cs
from agent.core.task_actions import SINK, _params

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = ROOT / "dev_assets" / "private" / "card-scan"


class ContextReaders:
    """OCR through the Maa context (det+rec in a fixed box) and the tile reader of ``owned_levels``."""

    def __init__(self, context, index):
        self.context, self.index = context, index

    def _ocr(self, image_bgr, roi):
        from maa.pipeline import JOCR, JRecognitionType

        param = JOCR(expected=[".*"], roi=tuple(int(v) for v in roi), threshold=0.0)
        detail = self.context.run_recognition_direct(JRecognitionType.OCR, param, image_bgr)
        return [{"text": r.text, "score": float(r.score), "box": list(r.box)}
                for r in (getattr(detail, "all_results", None) or [])] if detail is not None else []

    def text(self, image_rgb, roi):
        import numpy as np

        rows = self._ocr(np.ascontiguousarray(image_rgb[:, :, ::-1]), roi)
        text = "".join(r["text"] for r in sorted(rows, key=lambda r: r["box"][0]) if r["text"])
        return text or None

    def level_ocr(self, bgr, roi):
        return [(r["text"], r["score"]) for r in self._ocr(bgr, roi)]

    def tiles(self, frame, name):
        from agent.core import owned_levels as ol

        return ol.read_frame(frame, self.index, self.level_ocr, frame_name=name)


class CardScanRuntime:
    def __init__(self, sink=SINK, run_dir=RUN_DIR, clock=None, card_index=None, write_import=True, save_frames=True):
        self.sink = sink
        self.run_dir = Path(run_dir)
        self.save_frames = save_frames
        self.clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))
        self._index = card_index
        self.write_import = write_import
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.options = None
        self.session = None
        self.cache = None            # (source selected tab, Decision) until the step's post-action guard / hold completes
        self.outcome = None
        self.result = None
        self.run_name = None
        self.frame_dir = None
        self.errors = []             # exceptions swallowed by the Maa glue (never silent: kept in the record)
        self.stop_reason = None

    def note_error(self, where, exc):
        self.errors.append(f"{where}: {type(exc).__name__}: {str(exc)[:160]}")

    @property
    def index(self):
        if self._index is None:
            from agent.core import card_inventory as ci

            self._index = ci.CardIndex.from_assets()
        return self._index

    def report(self, outcome, **extra):
        self.outcome = outcome
        payload = {"outcome": outcome, "options": None if self.options is None else {
            "tabs": list(self.options.tabs), "max_scrolls_per_tab": self.options.max_scrolls_per_tab,
            "max_frames": self.options.max_frames}, **extra}
        self.sink.add("card_scan", payload)
        LOGGER.info("CardScan: %s", json.dumps(payload, ensure_ascii=False, default=str)[:2000])

    # -- actions
    def begin(self, context, p):
        self.reset()
        merged = {}
        try:
            for name in p.get("config_nodes", []):
                merged.update((context.get_node_data(name) or {}).get("attach") or {})
            self.options = cs.ScanOptions.from_params(merged)
        except Exception as exc:
            self.report("config_invalid", error=str(exc)[:200])
            return False
        try:
            index = self.index
        except FileNotFoundError as exc:
            # 2026-11-22 (new install): no local card icons (ui/game-assets/manifest.json) -> name it, not a bare abort
            self.note_error("begin", exc)
            self.stop_reason = "card_library_missing"
            self.report("stopped:card_library_missing", errors=list(self.errors))
            return False
        readers = ContextReaders(context, index)
        self.session = cs.ScanSession(self.options, readers.tiles, readers.text)
        self.run_name = f"card-scan-{self.clock().strftime('%Y%m%dT%H%M%SZ')}"
        if self.save_frames:
            self.frame_dir = self.run_dir / self.run_name
            self.frame_dir.mkdir(parents=True, exist_ok=True)
        return True

    def decision(self, context, image):
        """Observe once for a Pipeline step; candidate checks must not simulate an input or a wait."""
        from PIL import Image

        if self.session is None:
            return None
        if self.cache is not None:
            return self.cache[1]
        frame = Image.fromarray(image[:, :, ::-1].copy())
        seen = self.session.frames
        decision = self.session.observe(frame)
        self.cache = (cs.selected_tab(image[:, :, ::-1]), decision)
        if self.frame_dir is not None and self.session.frames > seen:
            try:
                frame.save(self.frame_dir / f"frame-{self.session.frames:03d}.png")
            except OSError:
                LOGGER.exception("CardScan frame save failed")
        return decision

    def advance(self, context, p):
        """A guarded input result or a completed no-input hold permits the next observation, even for identical pixels."""
        del context, p
        if self.session is None:
            return False
        self.cache = None
        return True

    def want(self, context, image, p):
        """Match the fixed step decision; the Pipeline's Advance action, never a candidate match, clears it."""
        state = p.get("is")
        if state is not None:
            if self.session is None:
                return state == "halted"
            final = self.session.final
            if state == "active":
                return final is None
            if state == "finished":
                return final is not None
            if state == "halted":
                return final is not None and final.kind == "stop"
            return False
        decision = self.decision(context, image)
        if decision is None:
            return False
        # Every input candidate also has List.Guard, and the controller binds its fresh frame through FrameGate.
        # A different selected tab must not inherit a cached wheel / tab / back decision during candidate evaluation.
        if decision.kind not in ("hold", "settle") and cs.selected_tab(image[:, :, ::-1]) != self.cache[0]:
            return False
        want = p.get("want")
        ok = False
        if want == "tap_tab":
            ok = decision.kind == "tap_tab" and decision.tab == p.get("tab")
        elif want == "scroll":
            ok = decision.kind == "scroll" and decision.direction == p.get("direction")
        elif want == "hold":
            ok = decision.kind == "hold"
        elif want == "settle":
            ok = decision.kind == "settle"
        elif want == "exit":
            ok = decision.kind in ("leave", "stop")
        return ok

    def finish(self, context, p):
        """At home after the list was left: store the run record and, for a complete scan, the import files."""
        del context, p
        if self.session is None:
            self.report("not_started")
            return False
        self.result = result = self.session.result()
        if self.frame_dir is not None:
            result["frames_dir"] = str(self.frame_dir)
        result["errors"] = list(self.errors)
        result["stop_reason"] = self.stop_reason
        name = self.run_name or f"card-scan-{self.clock().strftime('%Y%m%dT%H%M%SZ')}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        record = self.run_dir / f"{name}{'' if result['complete'] else '-partial'}.json"
        record.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        written = []
        if result["complete"] and self.write_import:
            from tools import card_inventory_scan, owned_levels_scan

            payloads = cs.import_payloads(result, self.clock().isoformat(timespec="seconds"))
            owned_levels_scan.write_module(payloads["owned_levels"])
            card_inventory_scan.write_module(payloads["inventory"])
            written = ["ui/game-assets/owned-levels.js", "ui/game-assets/inventory-scan.js"]
        self.report("scanned" if result["complete"] else f"partial:{result['outcome']}", record=str(record),
                    frames_dir=result.get("frames_dir"), written=written, cards=len(result["cards"]),
                    tabs=result["tabs"])
        return result["complete"]

    def stop(self, context, p):
        """Terminal node of every failure path: keep what was read (private record only) and report once."""
        del context
        reason = p.get("reason", "stop")
        if self.stop_reason is None:
            self.stop_reason = reason
            if self.session is not None and self.session.records and self.result is None:
                self.finish(None, {})
            # Always report the stop (a finish() "partial:None" alone would hide why the scan ended).
            self.report(f"stopped:{reason}", previous=self.outcome, errors=list(self.errors),
                        session_outcome=None if self.session is None or self.session.final is None
                        else self.session.final.reason)
        return False


RUNTIME = CardScanRuntime()


class _Recognition(CustomRecognition):
    def __init__(self, runtime=RUNTIME):
        super().__init__()
        self.rt = runtime

    def analyze(self, context, argv):
        p = _params(argv.custom_recognition_param)
        try:
            ok = self.rt.want(context, argv.image, p)
        except Exception as exc:
            LOGGER.exception("CardScan want failed")
            self.rt.note_error("want", exc)
            return None
        if not ok:
            return None
        roi = argv.roi
        box = (int(roi[0]), int(roi[1]), max(1, int(roi[2])), max(1, int(roi[3]))) if roi is not None else (0, 0, 1, 1)
        return CustomRecognition.AnalyzeResult(box=box, detail={"want": p})


class _Action(CustomAction):
    METHOD = ""

    def __init__(self, runtime=RUNTIME):
        super().__init__()
        self.rt = runtime

    def run(self, context, argv):
        try:
            return bool(getattr(self.rt, self.METHOD)(context, _params(argv.custom_action_param)))
        except Exception as exc:
            LOGGER.exception("CardScan %s failed", self.METHOD)
            self.rt.note_error(self.METHOD, exc)
            return False


class CardScanWant(_Recognition):
    """Step-bound decision of the scan session (tab tap / wheel step / hold / exit / state)."""


class CardScanBegin(_Action):
    METHOD = "begin"


class CardScanAdvance(_Action):
    METHOD = "advance"


class CardScanFinish(_Action):
    METHOD = "finish"


class CardScanStop(_Action):
    METHOD = "stop"


def card_scan_registrations(runtime=None):
    """({recognitions}, {actions}) for registering on a Resource outside the AgentServer (tests / launcher)."""
    runtime = runtime or CardScanRuntime()
    return ({"SC.CardScan.Want": CardScanWant(runtime)},
            {"SC.CardScan.Begin": CardScanBegin(runtime), "SC.CardScan.Advance": CardScanAdvance(runtime),
             "SC.CardScan.Finish": CardScanFinish(runtime),
             "SC.CardScan.Stop": CardScanStop(runtime)})
