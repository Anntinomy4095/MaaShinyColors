"""Maa glue shared by the B5 tasks (Training, GradeFes, MatchSkip): screen reading and runtime-bound
custom recognitions/actions. Rules live in the per-task ``*_task.py`` modules; no input happens here.

Each task has one runtime per launcher process (tools/run_tasks.py builds it with the controller's spend
authority; the AgentServer registrations under agent/custom/action use runtimes WITHOUT authority, so a PI shell
can navigate and read but never spend). A runtime exposes plain methods; ``RuntimeRecognition`` /
``RuntimeAction`` bind one Maa registration to one method.
"""
from __future__ import annotations

import json
import logging
import re

from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from agent.core import task_common as tc

LOGGER = logging.getLogger(__name__)
THRESHOLD = 0.95


class Vision:
    """Screen reading through the Maa context: named template/colour nodes and fixed-box OCR."""

    def __init__(self, context):
        self.context = context

    def hit(self, node, image):
        detail = self.context.run_recognition(node, image)
        return bool(detail is not None and detail.hit)

    def _rec(self, pixels, pattern):
        from maa.pipeline import JOCR, JRecognitionType
        height, width = pixels.shape[:2]
        param = JOCR(expected=[pattern], roi=(0, 0, width, height), threshold=THRESHOLD, only_rec=True)
        detail = self.context.run_recognition_direct(JRecognitionType.OCR, param, pixels)
        results = list(getattr(detail, "all_results", None) or []) if detail is not None else []
        if len(results) != 1 or float(results[0].score) < THRESHOLD:
            return None, None
        text = re.sub(r"\s+", "", results[0].text or "")
        return (text, float(results[0].score)) if re.fullmatch(pattern, text) else (None, None)

    def number(self, image, crop, kind, pattern, cutoff=128, unanimous=False):
        """Fixed-crop numeric OCR. ``unanimous``: every LEVEL_VARIANTS preprocessing must read the same text
        and the best must reach 0.95 (the lone-level-digit rule of the verified training runner)."""
        if not unanimous:
            pixels = tc.numeric_pixels(image, crop, kind, cutoff)
            return None if pixels is None else self._rec(pixels, pattern)[0]
        texts, best = set(), 0.0
        for variant in tc.LEVEL_VARIANTS:
            pixels = tc.numeric_pixels(image, crop, kind, cutoff, variant)
            text, score = (None, None) if pixels is None else self._rec_loose(pixels, pattern)
            texts.add(text)
            best = max(best, score or 0.0)
        if len(texts) != 1 or None in texts or best < THRESHOLD:
            return None
        return texts.pop()

    def rec(self, pixels, pattern):
        """only_rec OCR of already processed pixels (score >= 0.95, full-pattern match); text or None."""
        return self._rec(pixels, pattern)[0]

    def _rec_loose(self, pixels, pattern):
        """only_rec without the score threshold (unanimity decides); still a full-pattern match."""
        from maa.pipeline import JOCR, JRecognitionType
        height, width = pixels.shape[:2]
        param = JOCR(expected=[pattern], roi=(0, 0, width, height), threshold=0.0, only_rec=True)
        detail = self.context.run_recognition_direct(JRecognitionType.OCR, param, pixels)
        results = list(getattr(detail, "all_results", None) or []) if detail is not None else []
        if len(results) != 1:
            return None, None
        text = re.sub(r"\s+", "", results[0].text or "")
        return (text, float(results[0].score)) if re.fullmatch(pattern, text) else (None, None)

    def text(self, image, roi, threshold=THRESHOLD):
        """Japanese det+rec OCR inside one fixed box; every line must score >= ``threshold`` (0.95) and lie inside it."""
        from maa.pipeline import JOCR, JRecognitionType
        param = JOCR(expected=[".+"], roi=tuple(roi), threshold=threshold)
        detail = self.context.run_recognition_direct(JRecognitionType.OCR, param, image)
        rows = [{"text": r.text, "score": float(r.score), "box": list(r.box)}
                for r in (getattr(detail, "all_results", None) or [])] if detail is not None else []
        x, y, w, h = roi
        if not rows or not all(isinstance(r["text"], str) and r["text"].strip() and r["score"] >= threshold
                               and r["box"][0] >= x and r["box"][1] >= y and r["box"][0] + r["box"][2] <= x + w
                               and r["box"][1] + r["box"][3] <= y + h for r in rows):
            return None
        lines = []
        for row in sorted(rows, key=lambda r: r["box"][1] + r["box"][3] / 2):
            centre = row["box"][1] + row["box"][3] / 2
            if lines and abs(lines[-1][0] - centre) < 10:
                lines[-1][1].append(row)
            else:
                lines.append([centre, [row]])
        text = "".join(r["text"] for _, line in lines for r in sorted(line, key=lambda r: r["box"][0]))
        return tc.normalize_text(text) or None


class TaskRuntime:
    """Base runtime: config merge, report, abort and spend-error bookkeeping shared by the B5 tasks."""
    KIND = None            # report kind / log prefix
    RUN = tc.TaskRun
    CONFIG = None          # config class with from_params / to_dict
    SPEND_ACTION = None    # the one consuming table action of the task (None: never spends)

    def __init__(self, sink=None, authority=None, log_root=tc.LOG_ROOT, now=None, vision=Vision,
                 sample_root=tc.SAMPLE_ROOT):
        self.sink, self.authority, self.vision = sink, authority, vision
        self.now = now or tc.utc_now
        self.log = tc.JsonlLog(self.KIND, log_root, self.now)
        self.sample_root = sample_root
        self.run = None

    # -- helpers
    def can_spend(self):
        return (self.SPEND_ACTION is not None and self.authority is not None
                and self.authority.can_spend(self.SPEND_ACTION))

    def _log(self, record):
        try:
            self.log.append(record)
            return True
        except Exception:  # never spend without the log line
            LOGGER.exception("%s log write failed", self.KIND)
            return False

    def report(self):
        run = self.run
        if run is None or run.reported:
            return
        run.reported = True
        payload = run.summary()
        if self.sink is not None:
            self.sink.add(self.KIND, payload)
        LOGGER.info("%s: %s", self.KIND, json.dumps(payload, ensure_ascii=False, default=str))

    # -- actions
    def begin(self, context, p):
        merged = {}
        try:
            for name in p.get("config_nodes", []):
                data = context.get_node_data(name) or {}
                merged.update(data.get("attach") or {})
            config = self.CONFIG.from_params(merged)
        except Exception as exc:
            self.run = self.RUN(self.CONFIG(), self.log)
            self.run.finish("config_invalid", str(exc)[:200])
            self.report()
            return False
        self.run = self.RUN(config, self.log)
        self.started(self.run)
        return True

    def started(self, run):
        """Hook: inspect the day's log right after Begin (e.g. nothing to do today)."""

    def finish_action(self, context, p):
        run = self.run
        self.report()
        return bool(run is not None and run.succeeded)

    def awaiting_spend(self):
        return self.run is not None and self.run.phase == "awaiting_result"

    def abort(self, context, p):
        run = self.run
        if run is not None:
            if self.awaiting_spend():
                self.spend_error(context, p)
            else:
                run.finish("timeout", "unknown_or_unhandled_screen")
        self.report()
        return False

    def spend_error(self, context, p):
        run = self.run
        if run is None:
            return False
        outcome = self.authority.last_outcome(self.SPEND_ACTION) if self.authority is not None else None
        intent = run.intent or {}
        if outcome == "denied":
            self._log({"event": "refused", **intent, "reason": "click_denied_before_send"})
            run.phase = "spend_error"
            run.finish("spend_refused", "click_denied_before_send")
        else:
            self._log({"event": "uncertain", **intent, "reason": "result_not_observed:" + str(outcome)})
            run.phase = "spend_error"
            run.finish("spend_uncertain", "result_not_observed")
        self.report()
        return False

    def state(self, context, image, p):
        """``{"is": "active" | "finished" | "halted" | <task extras>}``; no screen reading."""
        run, want = self.run, p.get("is")
        if run is None:
            return False
        if want == "active":
            return run.active
        if want == "finished":
            return run.finished and not run.halted
        if want == "halted":
            return run.halted
        return self.extra_state(want, context, image, p)

    def extra_state(self, want, context, image, p):
        return False

    def authorize(self, image, approver):
        """One-shot grant for the task's consuming action, bound to this exact frame; None on success."""
        if not self.can_spend():
            return "spend_not_enabled"
        return self.authority.authorize(self.SPEND_ACTION, image, approver)


def _hit(argv, detail):
    roi = argv.roi
    box = (int(roi[0]), int(roi[1]), max(1, int(roi[2])), max(1, int(roi[3]))) if roi is not None else (0, 0, 1, 1)
    return CustomRecognition.AnalyzeResult(box=box, detail=detail)


class RuntimeRecognition(CustomRecognition):
    """Maa custom recognition bound to ``runtime.<method>(context, image, params) -> bool``."""

    def __init__(self, runtime, method):
        super().__init__()
        self.runtime, self.method = runtime, method

    def analyze(self, context, argv):
        p = tc.params(argv.custom_recognition_param)
        try:
            ok = getattr(self.runtime, self.method)(context, argv.image, p)
        except Exception as exc:  # unreadable -> no hit; the runtime records why when it can
            LOGGER.exception("%s.%s failed", self.runtime.KIND, self.method)
            run = self.runtime.run
            if run is not None:
                run.note({"error": self.method, "detail": str(exc)[:160]})
            return None
        return _hit(argv, {"method": self.method, **p}) if ok else None


class RuntimeAction(CustomAction):
    """Maa custom action bound to ``runtime.<method>(context, params) -> bool``."""

    def __init__(self, runtime, method):
        super().__init__()
        self.runtime, self.method = runtime, method

    def run(self, context, argv):
        try:
            return bool(getattr(self.runtime, self.method)(context, tc.params(argv.custom_action_param)))
        except Exception:
            LOGGER.exception("%s.%s failed", self.runtime.KIND, self.method)
            return False


def registrations(runtime, prefix, recognitions, actions):
    """({name: recognition}, {name: action}) for one runtime; ``recognitions``/``actions`` map suffix -> method."""
    return ({f"{prefix}.{suffix}": RuntimeRecognition(runtime, method) for suffix, method in recognitions.items()},
            {f"{prefix}.{suffix}": RuntimeAction(runtime, method) for suffix, method in actions.items()})
