"""Match Live auto-advance: play the frontier stage before the daily Skip.

Before the Skip part of the MatchSkip task, Maa plays the next uncleared stage with
ライブ開始 (one フェス参加権 per live) with the chosen unit (an empty unit is filled by おまかせ編成), retries a LOSE a
bounded number of times and re-reads the rights after every live. The remaining rights then go to the Skip
(``match_custom``). One decision per fresh frame; the Pipeline click node of that decision (``SC.Match.Play.*``,
an ``And`` of its page guard and ``SC.Match.Play.Want.<Kind>``) taps it.

Native pages (dev_assets/private/wing_samples/2026-10-31, 1280x720):

* main: Match main; the selected area card is on top (「エリア1 Normal」), a locked card reads
  「エリア2のステージをすべてクリア」. Tapping the next card selects it and scrolls it to the top; tapping the top card
  opens ステージ選択.
* stages: ステージ選択 with the frontier stage selected (left 「ステージ5」, grey stars = never won) and 決定.
* info: マッチライブ詳細: 「ステージ6」, stars, マッチライブユニット編成, まとめて消費 (never touched; must be off),
  ライブ開始 with 「19/20+0」.
* unit: フェスユニット編成: 「2/20」, tabs ユニット1–10, おまかせ編成 (fills an empty unit at once, no dialog),
  「タッチして編成」 in each empty slot; ← returns to the stage info.
* touch / live / result: 「Touch」 starts the live, 「SKIP」 (top right) ends it, the result shows 「WIN!」 and
  もう1度挑戦 / エリア選択へ / 次のステージへ. 次のステージへ opens the next stage's info page.

Unsampled (handled as unknown, bounded wait, then the play part ends): a LOSE result, 次のステージへ on an area's last
stage, ユニット11–20.
"""
from __future__ import annotations

import copy
import dataclasses
import re

from agent.core import task_common as tc

SPEND_ACTION = "match-play-live-start"
APPROVER = "SC.Match.Play.Want.LiveStart"
MODES = ("advance", "off")
UNIT_TABS = 10                 # ユニット1–10 (the › page with 11–20 is unsampled)
RETRY_CHOICES = (0, 1, 2, 3, 5)
MAX_LIVES = 40                 # ライブ開始 per run: 20 rights plus losses that may not consume
MAX_AREA_TAPS = 12             # next-area cards per frontier search (エリア選択へ may show エリア1 again; live 10-31)
MAX_NAV_TAPS = 16              # non-consuming taps between two lives
MAX_RETURN_TAPS = 8            # taps back to Match main once the play part ended
MAX_WAIT_FRAMES = 8            # unread frames of a page before the play part ends
SETTLE_FRAMES = 6              # frames a tap may take to change its page before it is decided again
MAX_LIVE_FRAMES = 150          # frames from ライブ開始 to the result (a live lasts 2:19 when SKIP is not pressed)
MAX_LIVE_TAPS = 3              # Touch / SKIP taps per live
START_LEFT_FRAMES = 20         # stage info still showing the same rights this long after ライブ開始: not started
MAX_RESULT_WAITS = 4           # result frames whose WIN/LOSE word is unread before it counts as unknown
MAX_UNSENT_TAPS = 3            # decided taps in a row whose node did not run (guard or max_hit) before play ends

NODE_LOADING = "SC.Loading.Present"
NODE_MAIN = "SC.Nav.MatchMain.Guard"
NODE_STAGES = "SC.Match.Latest.Stage.Guard"
NODE_INFO = "SC.Match.Play.Info.Guard"
NODE_UNIT = "SC.Match.Play.Unit.Guard"
NODE_RESULT = "SC.Match.Play.Result.Guard"
NODE_TOUCH = "SC.Match.Play.Touch.Guard"
NODE_LIVE = "SC.Match.Play.Live.Guard"
NODE_LOSE = "SC.Match.Play.LoseNotice.Guard"   # 「敗北したため使用したフェス参加権を返却しました」 over the LOSE result
PAGES = ((NODE_LOADING, "loading"), (NODE_LOSE, "lose_notice"), (NODE_RESULT, "result"), (NODE_TOUCH, "touch"),
         (NODE_LIVE, "live"), (NODE_INFO, "info"), (NODE_UNIT, "unit"), (NODE_STAGES, "stages"), (NODE_MAIN, "main"))
# Match pages the play part can act on from a fresh start (a result or LOSE notice left by an earlier run: 閉じる/エリア選択へ)
ENGAGE_PAGES = ("main", "stages", "info", "unit", "result", "lose_notice")

# boxes (x, y, w, h) on the 1280x720 canvas
AREA_HEADERS = ((28, 218, 188, 42), (28, 354, 188, 42))   # 「エリア1 Normal」: top (selected) card, next card
NEXT_LOCK = (108, 379, 280, 39)                         # 「エリア2のステージをすべてクリア」 over a locked next card
MAIN_RIGHTS = (1132, 545, 115, 32)                      # 「20/20+0」 on the スキップ button
STAGE_AREA = (30, 88, 120, 42)                          # 「エリア2」
STAGE_NAME = (28, 432, 120, 42)                         # 「ステージ5」 of the selected stage
STAGE_STARS = (405, 135, 140, 55)
BUTTON_RIGHTS = (1115, 658, 125, 30)                    # 「19/20+0」 on 決定 / ライブ開始
INFO_STAGE = (670, 25, 140, 45)                         # 「ステージ6」
INFO_STARS = (1115, 35, 135, 40)
BULK_BOX = (527, 643, 34, 34)                           # まとめて消費 checkbox interior
UNIT_SHOWN = (460, 20, 85, 45)                          # 「2/20」
UNIT_FIRST_TAB = (18, 82, 110, 38)                      # 「ユニット1」 while the tabs show 1–10
UNIT_SLOTS = tuple((35 + 247 * i, 325, 220, 80) for i in range(5))   # 「タッチして編成」 of an empty slot
RESULT_WORD = (180, 140, 360, 170)                      # 「WIN!」
GOLD_CLEARED = 40               # gold star pixels: three gold stars of a card ~430, grey stars 0
GOLD_NONE = 8
BULK_OFF_BGR = (160, 138, 152)  # unchecked box interior (native 004449/004732: std 12.8, no pink)

CLICK_NODES = {"area_next": "SC.Match.Play.AreaNext", "area_open": "SC.Match.Play.AreaOpen",
               "stage_decide": "SC.Match.Play.StageDecide", "stage_back": "SC.Match.Play.StageBack",
               "unit_open": "SC.Match.Play.UnitOpen",
               **{f"unit_tab_{n}": f"SC.Match.Play.UnitTab{n}" for n in range(1, UNIT_TABS + 1)},
               "omakase": "SC.Match.Play.Omakase", "unit_back": "SC.Match.Play.UnitBack",
               "info_back": "SC.Match.Play.InfoBack", "live_start": "SC.Match.Play.LiveStart",
               "touch": "SC.Match.Play.Touch", "skip": "SC.Match.Play.Skip",
               "next_stage": "SC.Match.Play.NextStage", "area_select": "SC.Match.Play.AreaSelect",
               "lose_close": "SC.Match.Play.LoseClose"}


def want_node(kind):
    """"area_next" -> SC.Match.Play.Want.AreaNext."""
    return "SC.Match.Play.Want." + "".join(part.capitalize() for part in kind.split("_"))


@dataclasses.dataclass(frozen=True)
class Decision:
    kind: str            # a CLICK_NODES key, wait or none
    reason: str = ""


# ---------------------------------------------------------------- parsing
def parse_area_header(text):
    match = re.fullmatch(r"エリア([1-9])(?:Normal|Hard)", text or "")
    return int(match[1]) if match else None


def parse_lock(text):
    match = re.fullmatch(r"エリア([1-9])のステージをす[べベ]てクリア", text or "")
    return int(match[1]) if match else None


def parse_area(text):
    match = re.fullmatch(r"エリア([1-9])", text or "")
    return int(match[1]) if match else None


def parse_stage(text):
    match = re.fullmatch(r"ステージ([1-9][0-9]?)", text or "")
    return int(match[1]) if match else None


def parse_rights(text):
    """'19/20+0' -> (19, 20); the recovery-ticket count after ＋ is not a right."""
    rights = tc.ratio((text or "").split("+")[0])
    return rights if rights is not None and rights[1] == 20 else None


def _patch(image, box):
    x, y, w, h = box
    return image[y:y + h, x:x + w].astype(int)


def gold_pixels(image, box):
    patch = _patch(image, box)
    b, g, r = patch[..., 0], patch[..., 1], patch[..., 2]
    return int(((r > 200) & (g > 150) & (b < 110)).sum())


def stars(image, box):
    """"cleared" (at least one gold star: 「ステージに勝利する」 is the first mission), "uncleared" or None."""
    gold = gold_pixels(image, box)
    return "cleared" if gold >= GOLD_CLEARED else "uncleared" if gold <= GOLD_NONE else None


def bulk_off(image):
    """True only for the sampled unchecked まとめて消費 box (its checked look is unsampled: never assumed)."""
    patch = _patch(image, BULK_BOX)
    b, g, r = patch[..., 0], patch[..., 1], patch[..., 2]
    pink = int(((r > 200) & (g < 140) & (b > 150)).sum())
    mean = patch.reshape(-1, 3).mean(axis=0)
    return pink == 0 and float(patch.std()) <= 20 and all(abs(m - e) <= 25 for m, e in zip(mean, BULK_OFF_BGR))


# ---------------------------------------------------------------- reading
class Reader:
    """Fixed-box reads over a ``task_custom.Vision``-like object (``text(image, roi, threshold)``, ``hit(node, image)``)."""

    def __init__(self, vision):
        self.vision = vision

    def text(self, image, box, threshold=0.8):
        try:
            return self.vision.text(image, box, threshold)
        except Exception:
            return None

    def hit(self, node, image):
        try:
            return bool(self.vision.hit(node, image))
        except Exception:
            return False

    def page(self, image):
        return next((page for node, page in PAGES if self.hit(node, image)), "unknown")

    def main(self, image, rights=False):
        top, nxt = (parse_area_header(self.text(image, box)) for box in AREA_HEADERS)
        return {"top": top, "next": nxt, "lock": parse_lock(self.text(image, NEXT_LOCK)),
                "rights": parse_rights(self.text(image, MAIN_RIGHTS)) if rights else None}

    def stages(self, image):
        return {"area": parse_area(self.text(image, STAGE_AREA)), "stage": parse_stage(self.text(image, STAGE_NAME)),
                "stars": stars(image, STAGE_STARS), "rights": parse_rights(self.text(image, BUTTON_RIGHTS))}

    def info(self, image):
        return {"stage": parse_stage(self.text(image, INFO_STAGE)), "stars": stars(image, INFO_STARS),
                "rights": parse_rights(self.text(image, BUTTON_RIGHTS)), "bulk_off": bulk_off(image)}

    def info_rights(self, image):
        return parse_rights(self.text(image, BUTTON_RIGHTS))

    def unit(self, image):
        shown = tc.ratio(self.text(image, UNIT_SHOWN))
        empty = sum("編成" in (self.text(image, box, 0.3) or "") for box in UNIT_SLOTS)
        return {"shown": shown[0] if shown and shown[1] == 20 else None, "empty": empty,
                "first_tab": self.text(image, UNIT_FIRST_TAB)}

    def result_word(self, image):
        text = (self.text(image, RESULT_WORD, 0.3) or "").upper()
        return "win" if "WIN" in text else "lose" if "LOSE" in text else None


# ---------------------------------------------------------------- the play part of one run
class MatchPlay:
    """State of the play part of one MatchSkip run. ``step(reader, image)`` decides once per fresh frame.

    ``phase``: navigate -> live (after ライブ開始, until its result) -> navigate ... -> returning (the play part ended,
    back to Match main) -> ended. ``outcome`` is set only when the whole task must stop (uncertain consumption)."""

    STATE_EXCLUDE = ("config", "log", "authority", "now")

    def __init__(self, config, log, authority, now):
        self.config, self.log, self.authority, self.now = config, log, authority, now
        self.phase = "navigate"
        self.engaged = False        # the ordinary Match navigation leads from home until a Match page shows
        self.reason = None
        self.outcome = None
        self.unit_ready = False
        self.target = None          # (area or None, stage) being played; stage numbers run across areas
        self.lives = []             # one record per ライブ開始
        self.losses = {}            # stage -> LOSE/unknown results
        self.consumed = 0           # lives whose rights dropped by one
        self.verify = None          # rights before the last live until a page shows them again
        self.after = None           # [kind, page, signature, frames] of the last navigation tap
        self.live = None
        self.nav_taps = self.area_taps = self.return_taps = self.waits = 0
        self.unit_taps = {"tab": 0, "omakase": 0}

    # -- helpers
    def snapshot(self):
        return copy.deepcopy({k: v for k, v in vars(self).items() if k not in self.STATE_EXCLUDE})

    def restore(self, state):
        self.__dict__.update(state)

    def _log(self, record):
        try:
            self.log.append(record)
            return True
        except Exception:
            return False

    def summary(self):
        results = [live.get("result") for live in self.lives]
        return {"lives": len(self.lives), "wins": results.count("win"),
                "losses": sum(1 for r in results if r in ("lose", "unknown")), "consumed": self.consumed,
                "ended": self.reason, "last_stage": self._label(self.lives[-1]) if self.lives else None}

    @staticmethod
    def _label(live):
        return f"A{live['area']}-S{live['stage']}" if live.get("area") else f"S{live['stage']}"

    def _end(self, reason):
        """The play part ends; the pages lead back to Match main, where the Skip part starts."""
        if self.phase in ("navigate", "live"):
            self.phase, self.reason, self.waits, self.after = "returning", reason, 0, None
            self._log({"event": "play_end", "reason": reason, **self.summary()})

    def _fail(self, outcome, reason):
        self.phase, self.outcome, self.reason = "ended", outcome, reason
        self._log({"event": "play_" + outcome, "reason": reason, **self.summary()})
        return Decision("none", reason)

    def _wait(self, reason):
        self.waits += 1
        if self.waits > MAX_WAIT_FRAMES:
            if self.phase == "returning":
                return self._fail("play_page_unknown", reason)
            self._end(reason)
        return Decision("wait", reason)

    def _settled(self, page, sig):
        """While the page of the last tap still shows the same reading: wait (bounded), then decide it again."""
        after = self.after
        if after is None:
            return None
        if after[1] == page and after[2] == sig and after[3] < SETTLE_FRAMES:
            after[3] += 1
            return Decision("wait", f"{after[0]}_settling")
        self.after = None
        return None

    def _tap(self, kind, page, sig, reason=""):
        if self.phase == "returning":
            self.return_taps += 1
            if self.return_taps > MAX_RETURN_TAPS:
                return self._fail("play_return_failed", kind)
        else:
            self.nav_taps += 1
            if self.nav_taps > MAX_NAV_TAPS:
                self._end("navigation_budget")
                return Decision("wait", "navigation_budget")
        self.after, self.waits = [kind, page, sig, 0], 0
        return Decision(kind, reason)

    def _verify(self, rights):
        """Bind the last live's consumption to the next rights reading (a loss may not consume; +1 regenerates)."""
        before = self.verify
        if before is None or rights is None:
            return None
        live = self.lives[-1]
        live["rights_after"] = list(rights)
        delta = before[0] - rights[0]
        self.verify = None
        if delta not in (-1, 0, 1) or rights[1] != before[1]:
            self._log({"event": "play_uncertain", **live, "reason": "rights_changed"})
            return self._fail("spend_uncertain", f"rights_{before[0]}_to_{rights[0]}")
        live["consumed"] = delta == 1
        self.consumed += delta == 1
        self._log({"event": "play_verified", **live})
        return None

    # -- the decision of one frame
    def step(self, reader, image):
        if self.phase == "ended":
            return Decision("none", self.reason or "")
        page = reader.page(image)
        if not self.engaged:
            # 2026-10-31 live: the task begins at home; play idles (no node hits) until Match shows.
            if page not in ENGAGE_PAGES:
                return Decision("idle", "before_match")
            self.engaged = True
        if self.phase == "live":
            return self._live(reader, image, page)
        if page == "loading":
            return Decision("none", "loading")
        handler = {"main": self._main, "stages": self._stages, "info": self._info, "unit": self._unit,
                   "result": self._result_page, "lose_notice": self._lose_page}.get(page)
        if handler is None:
            return self._wait("page_" + page)
        return handler(reader, image)

    def _main(self, reader, image):
        state = reader.main(image, rights=self.verify is not None)
        if (decision := self._verify(state["rights"])) is not None:
            return decision
        if self.verify is not None:
            return self._wait("rights_unread")
        sig = (state["top"], state["next"], state["lock"])
        if (decision := self._settled("main", sig)) is not None:
            return decision
        if self.phase == "returning":
            self.phase = "ended"        # back on Match main: the Skip part takes over
            return Decision("none", "play_done")
        top, nxt, lock = sig
        if top is None:
            return self._wait("area_unread")
        if lock == top:
            return self._tap("area_open", "main", sig, f"A{top}")
        if nxt == top + 1:
            self.area_taps += 1
            if self.area_taps > MAX_AREA_TAPS:
                self._end("frontier_area_not_found")
                self.phase = "ended"
                return Decision("none", "frontier_area_not_found")
            return self._tap("area_next", "main", sig, f"A{nxt}")
        return self._wait("frontier_area_unread")

    def _stages(self, reader, image):
        state = reader.stages(image)
        if (decision := self._verify(state["rights"])) is not None:
            return decision
        sig = (state["area"], state["stage"], state["stars"])
        if (decision := self._settled("stages", sig)) is not None:
            return decision
        if self.phase == "returning":
            return self._tap("stage_back", "stages", sig)
        area, stage, cleared = sig
        if area is None or stage is None or cleared is None:
            return self._wait("stage_unread")
        if cleared == "cleared":
            self._end("frontier_cleared")
            return self._tap("stage_back", "stages", sig)
        if self.losses.get(stage, 0) > self.config.retries:
            self._end("retries_used")
            return self._tap("stage_back", "stages", sig)
        self.target = (area, stage)
        return self._tap("stage_decide", "stages", sig, f"A{area}-S{stage}")

    def _unit(self, reader, image):
        state = reader.unit(image)
        sig = (state["shown"], state["empty"])
        if (decision := self._settled("unit", sig)) is not None:
            return decision
        if self.phase == "returning" or self.unit_ready:
            return self._tap("unit_back", "unit", sig)
        shown, empty = sig
        if shown is None:
            return self._wait("unit_unread")
        want = self.config.unit
        if want and shown != want:
            if state["first_tab"] != "ユニット1" or self.unit_taps["tab"] >= 2:
                self._end("unit_tab_unavailable" if state["first_tab"] != "ユニット1" else "unit_not_selected")
                return self._tap("unit_back", "unit", sig)
            self.unit_taps["tab"] += 1
            return self._tap(f"unit_tab_{want}", "unit", sig, f"unit_{want}")
        if empty:
            if self.unit_taps["omakase"] >= 2:
                self._end("unit_still_empty")
                return self._tap("unit_back", "unit", sig)
            self.unit_taps["omakase"] += 1
            self._log({"event": "play_omakase", "unit": shown, "empty_slots": empty})
            return self._tap("omakase", "unit", sig, f"empty_{empty}")
        self.unit_ready = True
        self._log({"event": "play_unit", "unit": shown})
        return self._tap("unit_back", "unit", sig, f"unit_{shown}")

    def _info(self, reader, image):
        state = reader.info(image)
        if (decision := self._verify(state["rights"])) is not None:
            return decision
        sig = (state["stage"], state["stars"], state["rights"])
        if (decision := self._settled("info", sig)) is not None:
            return decision
        if self.phase == "returning":
            return self._tap("info_back", "info", sig)
        if not self.unit_ready:
            return self._tap("unit_open", "info", sig)
        stage, cleared, rights = sig
        if stage is None or cleared is None or rights is None or self.verify is not None:
            return self._wait("info_unread")
        if cleared == "cleared":
            self._end("stage_cleared")
            return self._tap("info_back", "info", sig)
        if not state["bulk_off"]:
            self._end("bulk_consume_on")      # まとめて消費 would spend several rights at once: never touched
            return self._tap("info_back", "info", sig)
        area = self.target[0] if self.target is not None and self.target[1] == stage else None
        stop = self._budget(rights, stage)
        if stop is None and not (self.authority is not None and self.authority.can_spend(SPEND_ACTION)):
            stop = "play_spend_not_enabled"
        intent = {"area": area, "stage": stage, "rights_before": list(rights), "unit": self.config.unit or "current",
                  "live": len(self.lives) + 1, "intent_time": self.now().isoformat(),
                  "frame": tc.image_digest(image)}     # "time" stays the log's own timestamp on every later line
        if stop is None and not self._log({"event": "play_intent", **intent}):
            stop = "log_write_failed"
        if stop is None:
            refusal = self.authority.authorize(SPEND_ACTION, image, APPROVER)
            if refusal:
                self._log({"event": "play_refused", **intent, "reason": refusal})
                stop = "spend_refused:" + str(refusal)
        if stop is not None:
            self._end(stop)
            return self._tap("info_back", "info", sig)
        self.target = (area, stage)
        self.lives.append(intent)
        self.verify = rights
        self.phase, self.after, self.nav_taps, self.area_taps, self.waits = "live", None, 0, 0, 0
        self.live = {"frames": 0, "touch": 0, "skip": 0, "info_frames": 0, "pending": None, "word_waits": 0}
        return Decision("live_start", f"A{area}-S{stage}" if area else f"S{stage}")

    def _budget(self, rights, stage):
        if len(self.lives) >= MAX_LIVES:
            return "live_cap"
        if rights[0] - self.config.rights_reserve < 1:
            return "rights_used"
        if self.config.count_mode == "fixed" and self.consumed >= self.config.count:
            return "count_reached"
        if self.losses.get(stage, 0) > self.config.retries:
            return "retries_used"
        return None

    # -- after ライブ開始
    def _live(self, reader, image, page):
        live = self.live
        live["frames"] += 1
        if live["frames"] > MAX_LIVE_FRAMES:
            self._log({"event": "play_uncertain", **self.lives[-1], "reason": "result_not_observed"})
            return self._fail("spend_uncertain", "live_result_not_observed")
        if page == "result":
            return self._result(reader, image)
        if page == "lose_notice":
            return self._lose_notice()
        if page == "info":
            live["info_frames"] += 1
            if (live["info_frames"] > START_LEFT_FRAMES and not live["touch"] and not live["skip"]
                    and reader.info_rights(image) == self.verify):
                record = self.lives.pop()   # ライブ開始 changed nothing: the live never started; never pressed again
                self._log({"event": "play_not_spent", **record, "reason": "live_not_started"})
                self.verify, self.live, self.phase = None, None, "navigate"
                self._end("live_not_started")
                return Decision("wait", "live_not_started")
            return Decision("wait", "live_starting")
        if page in ("touch", "live"):
            kind = "touch" if page == "touch" else "skip"
            pending = live["pending"]
            if pending is not None and pending[0] == kind and pending[1] < SETTLE_FRAMES:
                pending[1] += 1
                return Decision("wait", kind + "_settling")
            if live[kind] >= MAX_LIVE_TAPS:
                return Decision("wait", kind + "_taps_used")   # the live ends by itself (2:19)
            live[kind] += 1
            live["pending"] = [kind, 0]
            return Decision(kind)
        return Decision("wait", "live_" + page)

    def _result(self, reader, image):
        live, record = self.live, self.lives[-1]
        word = reader.result_word(image)
        if word is None and live["word_waits"] < MAX_RESULT_WAITS:
            live["word_waits"] += 1
            return Decision("wait", "result_unread")
        record["result"] = word or "unknown"
        self._log({"event": "play_result", **record})
        self.phase, self.live, self.nav_taps = "navigate", None, 0
        sig = ("result",)
        if word == "win":
            # 次のステージへ may cross into the next area (live 10-31: ステージ6 エリア2 -> 7 エリア3): area unknown
            self.target = (None, record["stage"] + 1)
            after = (record["rights_before"][0] - 1, record["rights_before"][1])
            stop = self._budget(after, record["stage"] + 1)
            if stop is None and self.config.count_mode == "fixed" and self.consumed + 1 >= self.config.count:
                stop = "count_reached"
            if stop is None:
                return self._tap("next_stage", "result", sig, f"S{record['stage'] + 1}")
            self._end(stop)
            return self._tap("area_select", "result", sig)
        return self._tap("area_select", "result", sig, self._loss(record["stage"]))

    def _loss(self, stage):
        self.losses[stage] = self.losses.get(stage, 0) + 1
        if self.losses[stage] > self.config.retries:
            self._end("retries_used")
        return f"loss_{self.losses[stage]}"

    def _lose_notice(self):
        """2026-10-31 live: a LOSE first shows 「…フェス参加権を返却しました」; the next rights read verifies the return."""
        record = self.lives[-1]
        record["result"], record["returned"] = "lose", True
        self._log({"event": "play_result", **record})
        self.phase, self.live, self.nav_taps = "navigate", None, 0
        reason = self._loss(record["stage"])
        return self._tap("lose_close", "lose_notice", ("lose_notice",), reason)

    def _lose_page(self, reader, image):
        """A LOSE notice outside a live of this run, or one the last 閉じる did not close yet."""
        sig = ("lose_notice",)
        if (decision := self._settled("lose_notice", sig)) is not None:
            return decision
        return self._tap("lose_close", "lose_notice", sig)

    def _result_page(self, reader, image):
        """A result page outside a live of this run, or one the last tap did not leave: エリア選択へ."""
        sig = ("result",)
        if (decision := self._settled("result", sig)) is not None:
            return decision
        return self._tap("area_select", "result", sig)
