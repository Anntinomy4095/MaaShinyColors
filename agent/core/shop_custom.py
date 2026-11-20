"""Maa custom recognitions/actions of the Shop task (B4). Rules live in ``agent.core.shop_task``.

One ``ShopRuntime`` instance is shared by every registration of one launcher run (tools/run_tasks.py builds it
with the controller's spend authority). The AgentServer registrations (agent/custom/action/shop.py) use a
runtime WITHOUT authority: a PI shell can read the shop but never spend.

Input never happens here: the Pipeline Click nodes click; these classes only read frames, decide, write the
spend log and, for the single consuming action, ask the authority for a one-shot grant bound to the frame.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import re

from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from agent.core import shop_task as st

LOGGER = logging.getLogger(__name__)
THRESHOLD = 0.95
OCR_SCALE, OCR_PADDING, OCR_CUTOFF = 3, 6, 128
CONFIG_NODES = st.config_nodes()


def _params(raw):
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw else raw
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def image_digest(image):
    return hashlib.sha256(image.tobytes()).hexdigest()


# ---------------------------------------------------------------- reading
def resize_numeric(image, scale=OCR_SCALE):
    """Deterministic bilinear enlargement (same arithmetic as the verified training/shop OCR path)."""
    import numpy as np
    height, width = image.shape
    ys, xs = np.linspace(0, height - 1, height * scale), np.linspace(0, width - 1, width * scale)
    y0, x0 = ys.astype(int), xs.astype(int)
    y1, x1 = np.minimum(y0 + 1, height - 1), np.minimum(x0 + 1, width - 1)
    wy, wx = ys - y0, xs - x0
    top = image[y0[:, None], x0[None, :]] * (1 - wx)[None, :] + image[y0[:, None], x1[None, :]] * wx[None, :]
    bottom = image[y1[:, None], x0[None, :]] * (1 - wx)[None, :] + image[y1[:, None], x1[None, :]] * wx[None, :]
    return np.round(top * (1 - wy)[:, None] + bottom * wy[:, None]).astype(np.uint8)


def digit_pixels(image, roi, kind):
    """Fixed crop -> channel -> normalise -> threshold 128 -> bilinear x3 -> pad 6; None on low contrast."""
    import numpy as np
    x, y, w, h = roi
    crop = image[y:y + h, x:x + w].astype(float)  # BGR
    raw = 255 - crop[:, :, 0] if kind == "blue_inv" else 255 - crop.mean(axis=2) if kind == "light" else crop.mean(axis=2)
    contrast = float(raw.max() - raw.min())
    if contrast < 20:
        return None
    gray = (raw - raw.min()) * 255 / contrast
    binary = np.where(gray > OCR_CUTOFF, 255, 0)
    padded = np.pad(resize_numeric(binary), ((OCR_PADDING,) * 2,) * 2, constant_values=255)
    return np.ascontiguousarray(np.repeat(padded[:, :, None], 3, axis=2).astype(np.uint8))


class Vision:
    """Screen reading through the Maa context: named template/colour nodes and fixed-box OCR."""

    def __init__(self, context):
        self.context = context

    def hit(self, node, image):
        detail = self.context.run_recognition(node, image)
        return bool(detail is not None and detail.hit)

    def digits(self, image, name, position=None):
        from maa.pipeline import JOCR, JRecognitionType
        roi, kind, pattern = st.digit_field(name, position)
        pixels = digit_pixels(image, roi, kind)
        if pixels is None:
            return None
        height, width = pixels.shape[:2]
        param = JOCR(expected=[pattern], roi=(0, 0, width, height), threshold=THRESHOLD, only_rec=True)
        detail = self.context.run_recognition_direct(JRecognitionType.OCR, param, pixels)
        results = list(getattr(detail, "all_results", None) or []) if detail is not None else []
        if len(results) != 1 or float(results[0].score) < THRESHOLD:
            return None
        text = re.sub(r"\s+", "", results[0].text or "")
        return text if re.fullmatch(pattern, text) else None

    def text(self, image, roi):
        """Japanese det+rec OCR inside one fixed box; every line must score >= 0.95 and lie inside the box."""
        from maa.pipeline import JOCR, JRecognitionType
        param = JOCR(expected=[".+"], roi=tuple(roi), threshold=THRESHOLD)
        detail = self.context.run_recognition_direct(JRecognitionType.OCR, param, image)
        rows = [{"text": r.text, "score": float(r.score), "box": list(r.box)}
                for r in (getattr(detail, "all_results", None) or [])] if detail is not None else []
        x, y, w, h = roi
        if not rows or not all(isinstance(r["text"], str) and r["text"].strip() and r["score"] >= THRESHOLD
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
        return st.normalize_name(text) or None

    def read_list(self, image):
        cards, ended = [], False
        for position in st.RANKS:
            prefix = f"SC.Shop.Anchor.Card{position}."
            banner = self.hit(prefix + "Banner", image)
            if banner and not ended:
                if not (self.hit(prefix + "LimitLabel", image) and self.hit(prefix + "Coin", image)):
                    return st.ListView(False)
                enabled = self.hit(f"SC.Shop.Color.Card{position}.ButtonEnabled", image)
                cards.append(st.Card(position, self.text(image, st.card_roi(st.LIST_NAME_ROI, position)),
                                     st.to_int(self.digits(image, "card.price", position)),
                                     self.digits(image, "card.limit", position), enabled))
            elif not banner and self.hit(prefix + "Monthly", image):
                ended = True
            else:
                return st.ListView(False)
        return st.ListView(True, tuple(cards), st.to_int(self.digits(image, "list.money")))

    def read_confirm(self, image):
        fields = {k.split(".", 1)[1]: self.digits(image, k) for k in st.DIGIT_FIELDS if k.startswith("confirm.")}
        fields["name"] = self.text(image, st.CONFIRM_NAME_ROI)
        fields["prompt"] = self.text(image, st.CURRENCY_PROMPT_ROI)
        return fields

    def read_result(self, image):
        return {"sentence": self.text(image, st.RESULT_SENTENCE_ROI), "money": self.digits(image, "result.money"),
                "items": self.digits(image, "result.items")}


# ---------------------------------------------------------------- runtime
class ShopRuntime:
    """Shared state for one launcher (or agent) process; ``begin`` starts a fresh ``ShopRun`` per task."""

    def __init__(self, sink=None, authority=None, log_root=st.LOG_ROOT, now=None, vision=Vision):
        self.sink, self.authority, self.vision = sink, authority, vision
        self.now = now or (lambda: _dt.datetime.now(_dt.timezone.utc))
        self.log = st.SpendLog(log_root, self.now)
        self.run = None
        self._cache = {}

    def can_spend(self):
        return self.authority is not None and self.authority.can_spend(st.SPEND_ACTION)

    def begin(self, config):
        self.run, self._cache = st.ShopRun(config, self.log), {}
        return self.run

    def report(self):
        run = self.run
        if run is None or run.reported:
            return
        run.reported = True
        payload = run.summary()
        if self.sink is not None:
            self.sink.add("shop", payload)
        LOGGER.info("Shop: %s", json.dumps(payload, ensure_ascii=False))

    def _log(self, record):
        try:
            self.log.append(record)
            return True
        except Exception:  # never spend without the log line
            LOGGER.exception("shop spend log write failed")
            return False

    # -- list
    def decision(self, context, image):
        run = self.run
        key = image_digest(image)
        if key in self._cache:
            return self._cache[key]
        vision = self.vision(context)
        view = vision.read_list(image)
        state = st.log_state(self.log.events())
        # The day's snapshot needs every name and the price of every offer this run may buy; an unselected offer's
        # unread price is stored as null and matches any price later (shop_task.same_cards).
        if state.snapshot is None and view.layout and len(view.cards) == 3 and all(
                c.name and (c.price is not None or not run.config.wants_name(c.name)) for c in view.cards):
            if not self._log({"event": "snapshot", "cards": [{"rank": c.position, "name": c.name, "price": c.price}
                                                               for c in view.cards], "money": view.money}):
                run.finish("log_write_failed")
                return self._remember(key, st.Decision("fail", outcome="log_write_failed"), view)
            state = st.log_state(self.log.events())
        decision = st.decide(run.config, view, state, frozenset(run.bought), self.can_spend())
        if decision.kind == "wait":
            run.unreadable += 1
            if run.unreadable > st.MAX_UNREADABLE_FRAMES:
                run.finish("list_unreadable", *decision.notes)
        elif decision.kind == "inconsistent":
            if run.pre_purchase_key is not None and view.key() == run.pre_purchase_key and run.waits < st.MAX_WAIT_FRAMES:
                run.waits += 1
                decision = st.Decision("wait", notes=("waiting_for_list_refresh_after_purchase",))
            else:
                self._log({"event": "inconsistent", "visible": [list(k) for k in view.key()],
                           "reason": list(decision.notes), "money": view.money})
                run.finish("skipped_inconsistent", *(("after_purchase",) if run.bought else ()), *decision.notes)
        elif decision.kind == "done":
            run.finish("bought" if run.bought else decision.outcome, *decision.notes)
        elif decision.kind == "fail":
            run.finish(decision.outcome, *decision.notes)
        return self._remember(key, decision, view)

    def _remember(self, key, decision, view):
        self.run.decisions.append({"decision": decision.to_dict(), "visible": [list(k) for k in view.key()],
                                   "money": view.money, "layout": view.layout})
        self._cache = {key: decision}
        return decision

    def plan_hit(self, context, image, position):
        run = self.run
        if run is None or not run.active:
            return False
        decision = self.decision(context, image)
        if position == 0:
            return decision.kind == "wait" and run.active
        if decision.kind == "open" and decision.position == position and run.active:
            run.plan, run.confirm_rejected = decision.plan, None
            return True
        return False

    # -- dialogs
    def check_confirm(self, context, image):
        run = self.run
        if run is None or not run.active:
            return False
        if run.plan is None:
            run.confirm_rejected = ["unexpected_confirm"]
            return False
        fields = self.vision(context).read_confirm(image)
        reasons = st.check_confirm(run.config, run.plan, fields)
        if not self.can_spend():
            reasons.append("spend_not_enabled")
        run.decisions.append({"confirm": fields, "reasons": reasons})
        if reasons:
            run.confirm_rejected = reasons
            return False
        plan = run.plan
        intent = {"rank": plan["rank"], "name": plan["name"], "price": plan["price"],
                  "money_before": st.to_int(fields["money_before"]), "item_before": st.to_int(fields["item_before"])}
        if not self._log({"event": "intent", **intent}):
            run.confirm_rejected = ["log_write_failed"]
            return False
        refusal = self.authority.authorize(st.SPEND_ACTION, image, st.APPROVER)
        if refusal:
            self._log({"event": "refused", "rank": plan["rank"], "reason": refusal})
            run.confirm_rejected = ["spend_grant_refused:" + str(refusal)]
            return False
        run.intent, run.phase = intent, "awaiting_result"
        return True

    def confirm_cancel(self):
        run = self.run
        if run is None:
            return True  # a dialog no task opened: cancelling is the safe, non-consuming way out
        if run.phase == "awaiting_result":
            return False
        if run.phase == "finished":
            return True
        if run.plan is None or run.confirm_rejected or not self.can_spend():
            reasons = run.confirm_rejected or (["unexpected_confirm"] if run.plan is None else ["spend_not_enabled"])
            outcome = ("unexpected_confirm" if run.plan is None else
                       "spend_not_enabled" if reasons == ["spend_not_enabled"] else "confirm_mismatch")
            run.finish(outcome, *reasons)
            return True
        return False

    def check_result(self, context, image):
        run = self.run
        if run is None:
            return False
        fields = self.vision(context).read_result(image)
        if run.phase != "awaiting_result":
            self._log({"event": "unexpected_result", "fields": fields})
            run.finish("unexpected_result_dialog")
            return True
        intent, reasons = run.intent, st.check_result(run.intent, fields)
        run.decisions.append({"result": fields, "reasons": reasons})
        if reasons:
            self._log({"event": "uncertain", "rank": intent["rank"], "reason": reasons, "fields": fields})
            run.phase = "active"
            run.finish("result_mismatch", *reasons)
        else:
            self._log({"event": "verified", "rank": intent["rank"], "name": intent["name"], "price": intent["price"],
                       "money_after": st.to_int(fields["money"]), "items_after": st.to_int(fields["items"])})
            run.bought.append(intent["rank"])
            run.pre_purchase_key = tuple(tuple(v) for v in run.plan["visible"])
            run.phase, run.plan, run.intent, run.waits, run.unreadable = "active", None, None, 0, 0
        return True  # 閉じる is non-consuming and the result has been recorded either way

    def spend_error(self):
        run = self.run
        if run is None:
            return
        rank = (run.intent or run.plan or {}).get("rank")
        outcome = self.authority.last_outcome(st.SPEND_ACTION) if self.authority is not None else None
        if outcome == "denied":
            self._log({"event": "refused", "rank": rank, "reason": "click_denied_before_send"})
            run.phase = "active"
            run.finish("spend_refused", "click_denied_before_send")
        else:
            self._log({"event": "uncertain", "rank": rank, "reason": "result_not_observed:" + str(outcome)})
            run.phase = "active"
            run.finish("spend_uncertain", "result_not_observed")

    def abort(self):
        run = self.run
        if run is None:
            return
        if run.phase == "awaiting_result":
            self.spend_error()
        else:
            run.finish("timeout", "unknown_or_unhandled_screen")
        self.report()


RUNTIME = ShopRuntime()


# ---------------------------------------------------------------- Maa registrations
def _hit(argv, detail):
    roi = argv.roi
    box = (int(roi[0]), int(roi[1]), max(1, int(roi[2])), max(1, int(roi[3]))) if roi is not None else (0, 0, 1, 1)
    return CustomRecognition.AnalyzeResult(box=box, detail=detail)


class ShopStateRecognition(CustomRecognition):
    """``{"is": "active" | "finished" | "confirm_cancel"}``; no screen reading."""

    def __init__(self, runtime=RUNTIME):
        super().__init__()
        self.runtime = runtime

    def analyze(self, context, argv):
        want = _params(argv.custom_recognition_param).get("is")
        run = self.runtime.run
        if want == "active":
            ok = run is not None and run.active
        elif want == "finished":
            ok = run is not None and run.phase == "finished"
        elif want == "confirm_cancel":
            ok = self.runtime.confirm_cancel()
        else:
            ok = False
        return _hit(argv, {"is": want}) if ok else None


class ShopPlanRecognition(CustomRecognition):
    """``{"position": p}``: hit when the next purchase opens screen position p (0 = list needs another frame)."""

    def __init__(self, runtime=RUNTIME):
        super().__init__()
        self.runtime = runtime

    def analyze(self, context, argv):
        position = _params(argv.custom_recognition_param).get("position")
        try:
            ok = self.runtime.plan_hit(context, argv.image, position)
        except Exception as exc:  # unreadable -> no hit; the run state records why
            LOGGER.exception("shop plan failed")
            if self.runtime.run is not None:
                self.runtime.run.finish("list_unreadable", "plan_error:" + str(exc)[:120])
            return None
        return _hit(argv, {"position": position}) if ok else None


class ShopCheckRecognition(CustomRecognition):
    """``{"stage": "confirm" | "result"}``: verify the dialog; confirm issues the one-shot spend grant."""

    def __init__(self, runtime=RUNTIME):
        super().__init__()
        self.runtime = runtime

    def analyze(self, context, argv):
        stage = _params(argv.custom_recognition_param).get("stage")
        try:
            ok = (self.runtime.check_confirm(context, argv.image) if stage == "confirm"
                  else self.runtime.check_result(context, argv.image) if stage == "result" else False)
        except Exception as exc:
            LOGGER.exception("shop check failed")
            if self.runtime.run is not None and stage == "confirm":
                self.runtime.run.confirm_rejected = ["check_error:" + str(exc)[:120]]
            return None
        return _hit(argv, {"stage": stage}) if ok else None


class ShopBeginAction(CustomAction):
    def __init__(self, runtime=RUNTIME):
        super().__init__()
        self.runtime = runtime

    def run(self, context, argv):
        params = {}
        try:
            for name in _params(argv.custom_action_param).get("config_nodes", CONFIG_NODES):
                data = context.get_node_data(name) or {}
                params.update(data.get("attach") or {})
            config = st.ShopConfig.from_params(params)
        except Exception as exc:
            run = self.runtime.begin(st.ShopConfig())
            run.finish("config_invalid", str(exc)[:200])
            self.runtime.report()
            return False
        self.runtime.begin(config)
        return True


class ShopFinishAction(CustomAction):
    def __init__(self, runtime=RUNTIME):
        super().__init__()
        self.runtime = runtime

    def run(self, context, argv):
        del context, argv
        run = self.runtime.run
        self.runtime.report()
        return bool(run is not None and run.succeeded)


class ShopAbortAction(CustomAction):
    def __init__(self, runtime=RUNTIME):
        super().__init__()
        self.runtime = runtime

    def run(self, context, argv):
        del context, argv
        self.runtime.abort()
        return False


class ShopSpendErrorAction(CustomAction):
    def __init__(self, runtime=RUNTIME):
        super().__init__()
        self.runtime = runtime

    def run(self, context, argv):
        del context, argv
        self.runtime.spend_error()
        self.runtime.report()
        return False


def shop_recognitions(runtime):
    return {"SC.Shop.State": ShopStateRecognition(runtime), "SC.Shop.Plan": ShopPlanRecognition(runtime),
            "SC.Shop.Check": ShopCheckRecognition(runtime)}


def shop_actions(runtime):
    return {"SC.Shop.Begin": ShopBeginAction(runtime), "SC.Shop.Finish": ShopFinishAction(runtime),
            "SC.Shop.Abort": ShopAbortAction(runtime), "SC.Shop.SpendError": ShopSpendErrorAction(runtime)}
