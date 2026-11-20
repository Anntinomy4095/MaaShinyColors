"""W.I.N.G. SP learning on the 振り返り panel (2026-10-09b, sp_mode "auto"): canonical layout, fixed node slots, info
bar / dialog parsers, the spending session in ``WingNavRuntime`` and the generated catalog. Offline, zero input."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent.core import produce_config as pc
from agent.core import wing_nav as nav
from agent.core import wing_skill_panel as wsp
from agent.core import wing_sp

ROOT = Path(__file__).resolve().parent.parent
DEMO2 = ROOT / "dev_assets/private/recordings/wing-demo2-20261006-0106-02/index.jsonl"
DEMO3 = ROOT / "dev_assets/private/recordings/wing-demo3-20261006-1643-03/index.jsonl"
OCR2 = ROOT / "dev_assets/private/wing-demo2-analysis/ocr.jsonl"
OCR3 = ROOT / "dev_assets/private/wing-demo3-analysis/ocr-03.jsonl"


def label_box(slot, w=51, h=20, dx=0.0, dy=0.0):
    lx, ly = wsp.slot_label(slot)
    return (round(lx - w / 2 + dx), round(ly - h / 2 + dy), w, h)


def panel_nodes(costs=None, dx=0.0, dy=0.0, w=51):
    """Synthetic skill_nodes on every slot (inner ring cost 20 open, others 40 unreachable unless given)."""
    costs = costs or {}
    out = []
    for slot in wsp.SLOTS:
        cost, state = costs.get(slot, (20, "open") if slot in wsp.INNER_RING else (40, "unreachable"))
        out.append({"cost": cost, "state": state, "label": label_box(slot, w=w, dx=dx, dy=dy)})
    return out


class GeometryTests(unittest.TestCase):
    def test_slots_are_fixed_rects_clear_of_the_ui(self):
        self.assertEqual(len(wsp.SLOTS), 37)
        self.assertEqual(len(wsp.SLOT_INTENTS), 37)
        for slot in wsp.SLOTS:
            x, y, w, h = wsp.slot_rect(slot)
            self.assertTrue(0 <= x and 0 <= y and x + w <= 1280 and y + h <= 720, slot)
            for zx, zy, zw, zh in ((0, 84, 328, 208), (1188, 222, 78, 76), (1188, 422, 78, 76), wsp.INFO_BAR):
                self.assertTrue(x + w <= zx or zx + zw <= x or y + h <= zy or zy + zh <= y, (slot, zx, zy))
            self.assertEqual(nav.TARGETS[wsp.slot_intent(slot)], ((x, y, w, h), True))
        self.assertEqual(wsp.slot_intent((1, -2)), "skill_node_c08_r06")
        self.assertEqual(wsp.petal_of((1, -2)), "top_right")
        self.assertEqual(wsp.slot_source((1, -2)), "p_card")
        self.assertEqual(wsp.slot_source((-1, -2)), "unknown")
        self.assertEqual(wsp.petal_of((-2, 1)), "left")

    def test_canonical_view_only(self):
        good = wsp.read_panel(panel_nodes())
        self.assertTrue(good["canonical"], good)
        self.assertEqual(len(good["slots"]), 37)
        self.assertEqual(good["slots"]["skill_node_c08_r06"]["petal"], "top_right")
        self.assertEqual(wsp.read_panel(panel_nodes(w=73))["reason"], "zoom_not_canonical")
        self.assertEqual(wsp.read_panel(panel_nodes(dx=30))["reason"], "off_lattice")
        # a view panned by whole lattice steps fits the lattice but not the inner ring (third demo #386)
        shifted = [{**n, "label": (n["label"][0], n["label"][1] + round(2 * wsp.LATTICE_DY), *n["label"][2:])}
                   for n in panel_nodes()]
        self.assertEqual(wsp.read_panel(shifted)["reason"], "inner_ring_mismatch")
        self.assertEqual(wsp.read_panel(panel_nodes()[:5])["reason"], "too_few_labels")
        # 2026-10-29 live: three ring labels unread -> three suffice while the avatar centre has no label
        self.assertFalse(set(wsp.CENTRE_SLOTS) & set(wsp.SLOTS))
        three = [n for n, slot in zip(panel_nodes(), wsp.SLOTS) if slot not in wsp.INNER_RING[:3]]
        self.assertTrue(wsp.read_panel(three)["canonical"])
        centre = {"cost": 30, "state": "open", "label": label_box(wsp.CENTRE_SLOTS[0])}
        self.assertEqual(wsp.read_panel(three + [centre])["reason"], "inner_ring_mismatch")
        two = [n for n, slot in zip(panel_nodes(), wsp.SLOTS) if slot not in wsp.INNER_RING[:4]]
        self.assertEqual(wsp.read_panel(two)["reason"], "inner_ring_mismatch")


class ParserTests(unittest.TestCase):
    def test_classify_skill(self):
        cases = [
            (("Vocal上限UP", "Vocal上限+25"), ("cap_up", ("Vo",), 25.0)),
            (("Vocal&Visual上限UP", "Vocal&Visual上限+100"), ("cap_up", ("Vo", "Vi"), 100.0)),
            (("あなたと", "Vi3倍アピール[興味無視]/自身のメンタルを30％減らし"), ("live_skill", ("Vi",), 3.0)),
            (("x", "Voca|3.5倍アピ一ル[興味無视]"), ("live_skill", ("Vo",), 3.5)),
            (("ボーカルアピール", "Vocall倍アピ-"), ("live_skill", ("Vo",), 1.0)),
            (("心に住む人", "Excellent3倍アピール[興味無視]"), ("live_skill", (), 3.0)),
            (("メンタルキュアI", "メンタル5％回復"), ("live_skill", (), 0.0)),
            (("Vocal&Visual35%UP", "[条件:2ターン以前][確率：30%][最大:1回]"), ("passive", ("Vo", "Vi"), 0.105)),
            (("Vocal50%UP/注目度100%UP", "[確率:20%]"), ("passive", ("Vo",), 0.1)),
            (("???", "注目度アップ"), (None, (), None)),
        ]
        for (name, desc), want in cases:
            with self.subTest(name):
                got = wsp.classify_skill(name, desc)
                self.assertEqual((got["kind"], got["attributes"], got["value"]), want)

    def test_dialog_parsers(self):
        glued = [("SPを20消費してスキルを習得しますか?", (418, 223, 430, 27)), ("8666", (662, 415, 128, 28)),
                 ("あなたと、月の満ちる頃", (413, 288, 231, 27))]
        self.assertEqual(wsp.parse_learn_confirm(glued, sp=86),
                         {"cost": 20, "sp_before": 86, "sp_after": 66, "name": "あなたと、月の満ちる頃"})
        split = [("SPを50消費して", (418, 223, 430, 27)), ("151", (646, 414, 64, 29)), ("▶101", (709, 414, 95, 29))]
        self.assertEqual(wsp.parse_learn_confirm(split)["sp_after"], 101)
        rows = [{"row": i, "name": n, "attributes": a, "multiplier": m} for i, (n, a, m) in enumerate(
            [("ボーカル", ("Vo",), 1.0), ("ダンス", ("Da",), 1.0), ("ビジュアル", ("Vi",), 1.0), ("キュア", (), 0.0)], 1)]
        self.assertEqual(wsp.choose_forget(rows, 3.0, "Vo")["row"], 4)       # the user forgot メンタルキュアI (demo 3)
        strong = [dict(r, multiplier=5.0, attributes=("Vo",)) for r in rows]
        self.assertEqual(wsp.choose_forget(strong, 3.0, "Vo")["reason"], "no_weaker_skill")
        self.assertEqual(wsp.choose_forget(rows, None, "Vo")["reason"], "new_skill_unread")
        self.assertTrue(wsp.same_skill("ポーカルアピール", "ボーカルアピールI"))
        self.assertFalse(wsp.same_skill("DIVEIN!", "DIVEIN!+"))

    def test_p_card_skill_is_tier_one_whatever_its_attribute(self):
        node = wing_sp.SPNode("p", "live_skill", 20, 3.0, ("Vi",), source="p_card")
        cap = wing_sp.SPNode("cap", "cap_up", 20, 25.0, ("Vo",))
        plan = wing_sp.plan_sp([node, cap], sp=20, primary="Vo", stat=300, cap=520)
        self.assertEqual(plan.steps[0].node_id, "p")
        off = wing_sp.plan_sp([node, cap], sp=20, primary="Vo", stat=300, cap=520,
                              params={"p_card_any_attribute": False})
        self.assertNotIn("p", [s.node_id for s in off.steps])

    def test_caps_first_at_the_cap_and_weak_p_card_skills_after_main(self):
        """200+ SP went to an SR P card's route while Vo sat at its cap."""
        p_skill = wing_sp.SPNode("p", "live_skill", 20, 3.5, ("Vo",), source="p_card")
        s_skill = wing_sp.SPNode("s", "live_skill", 20, 3.0, ("Vo",), source="s_main")
        cap = wing_sp.SPNode("cap", "cap_up", 20, 25.0, ("Vo",))
        at_cap = wing_sp.plan_sp([p_skill, cap], sp=20, primary="Vo", stat=505, cap=505)
        self.assertEqual(at_cap.steps[0].node_id, "cap")
        strong = wing_sp.plan_sp([p_skill, s_skill], sp=20, primary="Vo", stat=300, cap=520)
        self.assertEqual(strong.steps[0].node_id, "p")
        weak = wing_sp.plan_sp([p_skill, s_skill], sp=20, primary="Vo", stat=300, cap=520,
                               params={"p_card_weak": True})
        self.assertEqual(weak.steps[0].node_id, "s")
        from agent.core import wing_nav
        self.assertTrue(wing_nav.p_card_weak("1030010010"))      # P_SR
        self.assertFalse(wing_nav.p_card_weak("1040010010"))     # P_SSR
        self.assertFalse(wing_nav.p_card_weak("2030010010"))     # an S card is never "the P card"
        self.assertFalse(wing_nav.p_card_weak(None))


INFO = {
    "skill_node_c08_r06": {"name": "あなたと、月の満ちる頃", "kind": "live_skill", "attributes": ("Vi",), "value": 3.0,
                           "multiplier": 3.0, "cost": 20},
    "skill_node_c06_r06": {"name": "Vocal上限UP", "kind": "cap_up", "attributes": ("Vo",), "value": 25.0,
                           "multiplier": None, "cost": 20},
    "skill_node_c05_r09": {"name": "Vocal&Visual35%UP", "kind": "passive", "attributes": ("Vo", "Vi"),
                           "value": 0.105, "multiplier": None, "cost": 20},
}


def runtime(sp_mode="auto", week=None, small=False):   # default-view flow; small view: own tests
    config = pc.ProduceConfig.from_dict(pc.default_config("true_end"))
    hooks = {"week": week} if week else {}
    rt = nav.WingNavRuntime(nav.RunMemory.from_produce_config(config), hooks=hooks,
                            options=nav.RunOptions(sp_mode=sp_mode, sp_small_view=small))
    rt.begin()
    return rt


def audition_week(state, strategy, goal, params):
    return {"kind": "audition", "audition_id": "s1_10k", "pre_actions": ["spend_sp"], "plan": {}}


WEEK = {"season": 1, "weeks_left": 4, "stats": {"Vo": 300, "Da": 100, "Vi": 100, "Me": 100}, "fans": 1000, "sp": 86,
        "rooms": [], "selected_room": None}


def review(sp, selected=None, state=None, open_slots=("skill_node_c08_r06", "skill_node_c06_r06",
                                                       "skill_node_c05_r09"), learned=(), deep="unknown"):
    """Panel frame of the session tests. Other nodes are ``unknown`` (colour not read, never planned) by default so these
    tests stay on the executor contract; the lookahead tests (test_wing_sp_lookahead) use real geometry."""
    costs = {}
    for intent, slot in wsp.SLOT_INTENTS.items():
        if intent in learned:
            costs[slot] = (0, "learned")
        elif intent in open_slots:
            costs[slot] = (INFO[intent]["cost"], "open")
        else:
            costs[slot] = (20, deep) if slot in wsp.INNER_RING else (40, deep)
    r = {"sp": sp, "panel": wsp.read_panel(panel_nodes(costs)), "skill_info": None, "skill_text": None}
    if selected:
        info = INFO[selected]
        r["skill_info"] = {"cost": 0 if state == "learned" else info["cost"], "state": state or "learnable"}
        r["skill_text"] = {k: info[k] for k in ("name", "kind", "attributes", "value", "multiplier")}
    return r


OWNED = {"owned": {"Vo": (500, 520), "Da": (100, 520), "Vi": (100, 520), "Me": (100, 500)}}


class SessionTests(unittest.TestCase):
    def open_session(self):
        rt = runtime(week=audition_week)
        rt.memory.auditions_seen = {"key": [1, 4], "pages": [], "trend": None, "done": True}
        go = rt.decide("Schedule", WEEK)
        self.assertEqual((go.intent, go.reason), ("page_return", "spend_sp_auto"))
        self.assertEqual(rt.memory.pending, "spend_sp")
        self.assertEqual(rt.decide("Hub").intent, "hub_review")
        return rt

    def test_full_session_learns_in_tier_order_and_ends_once(self):
        rt = self.open_session()
        self.assertEqual(rt.decide("Review", review(86)).intent, "skill_owned")
        self.assertEqual(rt.decide("Owned", OWNED).intent, "page_return")
        self.assertEqual(rt.memory.sp_session["caps"]["Vo"], 520)
        self.assertEqual(rt.decide("Hub").intent, "hub_review")
        seen = []
        d = rt.decide("Review", review(86))
        while d.reason == "sp_inspect":
            seen.append(d.intent)
            d = rt.decide("Review", review(86, selected=d.intent))
        self.assertEqual(sorted(seen), sorted(INFO))                 # every affordable open node inspected once
        # P skill first (top-right petal, tier p_card): select it, then 習得する
        if d.reason == "sp_select":
            self.assertEqual(d.intent, "skill_node_c08_r06")
            d = rt.decide("Review", review(86, selected="skill_node_c08_r06"))
        self.assertEqual((d.intent, d.reason), ("skill_learn", "sp_learn"))
        confirm = {"learn_confirm": {"cost": 20, "sp_before": 86, "sp_after": 66, "name": "あなたと、月の満ちる頃"}}
        self.assertEqual(rt.decide("LearnConfirm", confirm).intent, "learn_ok")
        d = rt.decide("Review", review(66, selected="skill_node_c08_r06", state="learned",
                                       open_slots=("skill_node_c06_r06", "skill_node_c05_r09"),
                                       learned=("skill_node_c08_r06",)))
        # This session precedes an audition (zero growth now), but the run's later growth needs the cap: it comes
        # before the passive, then the passive takes what is left.
        self.assertEqual(d.intent, "skill_node_c06_r06")
        self.assertGreater(rt.memory.sp_run["plan"]["final_stat"], 520)
        rt.decide("Review", review(66, selected="skill_node_c06_r06", open_slots=("skill_node_c06_r06", "skill_node_c05_r09"),
                                   learned=("skill_node_c08_r06",)))
        rt.decide("LearnConfirm", {"learn_confirm": {"cost": 20, "sp_before": 66, "sp_after": 46, "name": "Vocal上限UP"}})
        d = rt.decide("Review", review(46, selected="skill_node_c06_r06", state="learned",
                                       open_slots=("skill_node_c05_r09",),
                                       learned=("skill_node_c08_r06", "skill_node_c06_r06")))
        self.assertEqual(d.intent, "skill_node_c05_r09")
        rt.decide("Review", review(46, selected="skill_node_c05_r09", open_slots=("skill_node_c05_r09",),
                                   learned=("skill_node_c08_r06", "skill_node_c06_r06")))
        rt.decide("LearnConfirm", {"learn_confirm": {"cost": 20, "sp_before": 46, "sp_after": 26,
                                                     "name": "Vocal&Visual35%UP"}})
        done = rt.decide("Review", review(26, selected="skill_node_c05_r09", state="learned", open_slots=(),
                                          learned=("skill_node_c08_r06", "skill_node_c06_r06", "skill_node_c05_r09")))
        self.assertEqual((done.intent, done.reason), ("page_return", "sp_session_done"))
        self.assertIsNone(rt.memory.pending)
        self.assertEqual(rt.memory.sp_done_keys, [[1, 4]])
        learned = [e for e in rt.log if e.get("event") == "sp_learned"]
        self.assertEqual([e["name"] for e in learned], ["あなたと、月の満ちる頃", "Vocal上限UP", "Vocal&Visual35%UP"])
        # back on Schedule the same week: the request is satisfied, the audition goes ahead
        self.assertEqual(rt.decide("Hub").intent, "hub_schedule")
        self.assertEqual(rt.decide("Schedule", {**WEEK, "sp": 26}).intent, "schedule_audition_tab")

    def test_guards(self):
        rt = self.open_session()
        rt.memory.sp_session["owned"] = "unread"
        # non-canonical frames: bounded wait, one re-entry, then the week's session ends unspent
        bad = {"sp": 86, "panel": wsp.read_panel(panel_nodes(dx=30))}
        for _ in range(nav.SP_UNSTABLE_WAITS):
            self.assertEqual(rt.decide("Review", bad).reason, "sp_panel_settling")
        self.assertEqual(rt.decide("Review", bad).reason, "sp_panel_reenter")
        for _ in range(nav.SP_UNSTABLE_WAITS):
            rt.decide("Review", bad)
        skipped = rt.decide("Review", bad)
        self.assertEqual((skipped.action, skipped.intent, skipped.reason), ("click", "page_return", "sp_panel_unreadable_skip"))
        self.assertIsNone(rt.memory.pending)
        # a mismatching confirm dialog is cancelled and the node skipped
        rt = self.open_session()
        rt.memory.sp_session["owned"] = "unread"
        rt.memory.sp_session["catalog"] = {k: {**v, "state": "learnable", "source": wsp.slot_source(
            wsp.SLOT_INTENTS[k])} for k, v in INFO.items()}
        rt.memory.sp_session["last_tap"] = "skill_node_c08_r06"
        self.assertEqual(rt.decide("Review", review(86, selected="skill_node_c08_r06")).intent, "skill_learn")
        cancel = rt.decide("LearnConfirm", {"learn_confirm": {"cost": 30, "sp_before": 86, "sp_after": 56}})
        self.assertEqual((cancel.intent, cancel.reason), ("learn_cancel", "sp_confirm_mismatch"))
        nxt = rt.decide("Review", review(86, selected="skill_node_c08_r06"))
        # 2026-10-14: a cancelled Live skill is skipped and its cost held for the next session; the rest is planned
        self.assertIn("skill_node_c08_r06", rt.memory.sp_session["skipped"])
        self.assertEqual(rt.memory.sp_session["hold_sp"], 20)          # the cancelled node's cost
        self.assertNotEqual(nxt.intent, "skill_node_c08_r06")
        # SP did not drop after OK -> waits a few frames, then stops (never repeats the learn)
        rt = self.open_session()
        rt.memory.sp_session.update(owned="unread", last_tap="skill_node_c08_r06", catalog={
            k: {**v, "state": "learnable", "source": "unknown"} for k, v in INFO.items()})
        rt.decide("Review", review(86, selected="skill_node_c08_r06"))
        rt.decide("LearnConfirm", {"learn_confirm": {"cost": 20, "sp_before": 86, "sp_after": 66}})
        reasons = [rt.decide("Review", review(86, selected="skill_node_c08_r06")).reason for _ in range(6)]
        self.assertEqual(reasons[-1], "sp_learn_unconfirmed")

    def test_forget_select_hands_over_until_sampled(self):
        rt = self.open_session()
        rt.memory.sp_session.update(owned="unread", last_tap="skill_node_c08_r06", catalog={
            k: {**v, "state": "learnable", "source": wsp.slot_source(wsp.SLOT_INTENTS[k])} for k, v in INFO.items()})
        rt.decide("Review", review(86, selected="skill_node_c08_r06"))
        rt.decide("LearnConfirm", {"learn_confirm": {"cost": 20, "sp_before": 86, "sp_after": 66}})
        rows = [{"row": i, "name": n, "attributes": a, "multiplier": m} for i, (n, a, m) in enumerate(
            [("ボーカル", ("Vo",), 1.0), ("ダンス", ("Da",), 1.0), ("ビジュアル", ("Vi",), 1.0), ("キュア", (), 0.0)], 1)]
        # forget_select_* rows are verified (live 2026-10-12): the planned row is tapped once instead of handing over
        stop = rt.decide("ForgetSelect", {"forget_rows": rows})
        self.assertEqual((stop.action, stop.reason, stop.intent), ("click", "sp_forget_recovery_first", "forget_select_4"))
        self.assertEqual(stop.detail["choice"]["row"], 4)
        self.assertTrue(nav.TARGETS["forget_select_4"][1])
        # no weaker skill -> cancel, skip the node
        rt2 = self.open_session()
        rt2.memory.sp_session.update(owned="unread", last_tap="skill_node_c08_r06", catalog={
            k: {**v, "state": "learnable", "source": "p_card"} for k, v in INFO.items()})
        rt2.decide("Review", review(86, selected="skill_node_c08_r06"))
        rt2.decide("LearnConfirm", {"learn_confirm": {"cost": 20, "sp_before": 86, "sp_after": 66}})
        strong = [dict(r, multiplier=5.0, attributes=("Vo",)) for r in rows]
        self.assertEqual(rt2.decide("ForgetSelect", {"forget_rows": strong}).intent, "forget_cancel")

    def test_selected_node_label_lost_under_its_highlight(self):
        rt = self.open_session()
        rt.memory.sp_session["owned"] = "unread"
        first = rt.decide("Review", review(86))
        self.assertEqual(first.reason, "sp_inspect")
        hidden = review(86, selected=first.intent)
        hidden["panel"]["slots"].pop(first.intent)                     # OCR missed the selected node's label
        nxt = rt.decide("Review", hidden)
        self.assertIn(first.intent, rt.memory.sp_session["catalog"])
        self.assertNotEqual(nxt.intent, first.intent)

    def test_other_modes_unchanged(self):
        rt = runtime(sp_mode="handover", week=audition_week)
        rt.memory.auditions_seen = {"key": [1, 4], "pages": [], "trend": None, "done": True}
        self.assertEqual(rt.decide("Schedule", WEEK).reason, "sp_learn_handover")
        rt = runtime(sp_mode="skip", week=audition_week)
        rt.memory.auditions_seen = {"key": [1, 4], "pages": [], "trend": None, "done": True}
        self.assertEqual(rt.decide("Schedule", WEEK).intent, "schedule_audition_tab")
        cheap = runtime(week=audition_week)
        cheap.memory.auditions_seen = {"key": [1, 4], "pages": [], "trend": None, "done": True}
        self.assertEqual(cheap.decide("Schedule", {**WEEK, "sp": 9}).intent, "schedule_audition_tab")
        discounted = runtime(week=audition_week)
        discounted.memory.auditions_seen = {"key": [1, 4], "pages": [], "trend": None, "done": True}
        self.assertEqual(discounted.decide("Schedule", {**WEEK, "sp": 10}).reason, "spend_sp_auto")
        # Review without a session still leaves; dialogs without a planned step are cancelled
        idle = runtime()
        self.assertEqual(idle.decide("Review", review(86)).reason, "review_without_plan")
        self.assertEqual(idle.decide("LearnConfirm", {}).intent, "learn_cancel")
        self.assertEqual(idle.decide("ForgetSelect", {}).intent, "forget_cancel")
        self.assertEqual(idle.decide("ReplaceDialog", {}).intent, "replace_dialog_cancel")
        self.assertEqual(idle.decide("Owned", {}).intent, "page_return")
        with self.assertRaises(ValueError):
            nav.RunOptions(sp_mode="all")


class CatalogTests(unittest.TestCase):
    def test_generated_actions(self):
        from tools import wing_nav_build
        catalog = {a["id"]: a for a in wing_nav_build.action_catalog()}
        nodes = [a for a in catalog.values() if a["id"].startswith("wing-skill-node-")
                 and not a["id"].startswith("wing-skill-node-small-")]
        self.assertEqual(len(nodes), 37)
        self.assertTrue(all(a["family"] == "wing-skill" and a["enabled"] is False and not a["consuming"]
                            for a in nodes))
        for action in ("wing-skill-owned", "wing-skill-learn", "wing-learn-ok", "wing-forget-select-1",
                       "wing-replace-ok"):
            self.assertEqual(catalog[action]["family"], "wing-skill")
        self.assertNotIn("wing-owned-close", catalog)
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/wing.json").read_text(encoding="utf-8"))
        review = pipeline["SC.Wing.On.Review"]["next"]
        self.assertIn("SC.Wing.Click.Review.skill_node_c08_r06", review)
        self.assertEqual(pipeline["SC.Wing.Click.Review.skill_learn"]["target"], [1092, 620, 145, 58])
        self.assertIn("SC.Wing.State.Wait.Review", pipeline["SC.Wing.Hold.Review"]["all_of"])


def _ocr(path, seq):
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["seq"] == seq:
            return [(t, tuple(b)) for t, b in row["texts"]]
    raise KeyError(seq)


@unittest.skipUnless(DEMO2.is_file() and DEMO3.is_file() and OCR2.is_file() and OCR3.is_file(),
                     "private W.I.N.G. recordings not present")
class NativeFrameTests(unittest.TestCase):
    """Readers on the native recordings (whole-frame OCR texts + the PNG for the node colours)."""

    def frame(self, demo, ocr, seq):
        from tools import wing_demo_replay as replay
        from agent.core import wing_screen as wsc
        texts = _ocr(ocr, seq)
        image = replay.load_rgb(demo.parent / f"frame-{seq:06d}.png")
        return texts, image

    def read(self, demo, ocr, seq, screen):
        from agent.core import wing_screen as wsc
        texts, image = self.frame(demo, ocr, seq)
        evaluator = wsc.Evaluator(wsc.load_nodes(), wsc.BoxBackend(), {"SC.Wing.State": lambda p, f: True})
        return wsc.read_screen(screen, evaluator, wsc.Frame(texts, lambda: image, key=f"{seq}"))

    def test_canonical_entries_of_two_teams(self):
        for demo, ocr, seq in ((DEMO3, OCR3, 491), (DEMO2, OCR2, 671), (DEMO2, OCR2, 1516)):
            with self.subTest(seq):
                panel = self.read(demo, ocr, seq, "Review")["panel"]
                self.assertTrue(panel["canonical"], panel["reason"])
                self.assertGreaterEqual(len(panel["slots"]), 25)
        self.assertEqual(self.read(DEMO3, OCR3, 386, "Review")["panel"]["reason"], "inner_ring_mismatch")
        self.assertFalse(self.read(DEMO3, OCR3, 424, "Review")["panel"]["canonical"])      # max zoom
        p_skill = self.read(DEMO3, OCR3, 491, "Review")["panel"]["slots"]["skill_node_c08_r06"]
        self.assertEqual((p_skill["cost"], p_skill["state"], p_skill["petal"]), (0, "learned", "top_right"))

    def test_info_bar_and_dialogs(self):
        r = self.read(DEMO3, OCR3, 397, "Review")
        self.assertEqual((r["skill_text"]["kind"], r["skill_text"]["value"], r["skill_info"]),
                         ("cap_up", 25.0, {"cost": 20, "state": "learnable"}))
        r = self.read(DEMO3, OCR3, 424, "Review")
        self.assertEqual((r["skill_text"]["multiplier"], r["skill_info"]["cost"]), (3.0, 20))
        self.assertEqual(self.read(DEMO3, OCR3, 426, "LearnConfirm")["learn_confirm"],
                         {"cost": 20, "sp_before": 86, "sp_after": 66, "name": "あなたと、月の満ちる頃"})
        rows = self.read(DEMO3, OCR3, 428, "ForgetSelect")["forget_rows"]
        self.assertEqual([r["multiplier"] for r in rows], [1.0, 1.0, 1.0, 0.0])
        self.assertEqual(wsp.choose_forget(rows, 3.0, "Vo")["row"], 4)
        rep = self.read(DEMO2, OCR2, 744, "ReplaceDialog")["replace"]
        self.assertTrue(wsp.same_skill(rep["forget"], "ボーカルアピールI") and rep["new"] == "DIVEIN!+")
        owned = self.read(DEMO2, OCR2, 748, "Owned")["owned"]
        self.assertEqual(owned["Vo"], (310, 694))


if __name__ == "__main__":
    unittest.main()
