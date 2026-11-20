"""Local route planning, cached learning and opt-in recorded small view."""
import dataclasses
import json
import unittest

from agent.core import wing_nav as nav, wing_skill_panel as wsp, wing_sp_view as view
from tests.test_wing_sp_catalog import catalog, KNOWN
from tests import test_wing_sp_learning as learning

runtime, DEMO3, OCR3 = learning.runtime, learning.DEMO3, learning.OCR3


class RuntimeRoutesTests(unittest.TestCase):
    def setup_route(self):
        rt = runtime()
        rt.memory.team_slots = {"P": "1049990010", "S": ["2049990010", "2049990030", "2049990040", "2049990050"],
                                "guest": "2049990060"}
        signature = json.dumps({"team": rt.memory.team_slots, "owned": None}, sort_keys=True)
        rt._sp_local_cache = {"signature": signature, "catalog": catalog()}
        root = wsp.slot_intent((-2, 1))
        entry = {**KNOWN["root"], "source": "unknown"}
        rt.memory.pending = "spend_sp"
        rt.memory.sp_session = {"key": ["HubFinal"], "sp_start": 60, "taps": 1, "learned": [], "skipped": [],
            "catalog": {root: entry}, "last_tap": root, "learning": None, "owned": "read",
            "stats": {"Vo": 400}, "caps": {"Vo": 600}, "reentered": False, "unstable": 0, "inspected": 0}
        rt.memory.live_skills = [{"name": "weak", "attributes": ["Vo"], "multiplier": 1.}]
        frame = {"sp": 60, "panel": {"canonical": True, "slots": {
            root: {"slot": [-2, 1], "petal": "left", "cost": 10, "state": "open"}}},
            "skill_info": {"cost": 10, "state": "learnable"}, "skill_text": entry}
        return rt, root, frame

    def test_known_bridge_to_deep_live_is_learned_without_scanning_every_node(self):
        rt, root, frame = self.setup_route()
        action = rt.decide("Review", frame)
        self.assertEqual(action.intent, "skill_learn")
        self.assertEqual(rt.memory.sp_session["learning"]["cost"], 10)
        self.assertEqual(rt.memory.sp_session["inspected"], 0)
        self.assertEqual(rt.memory.sp_session["route_summary"][0]["remaining_cost"], 60)
        self.assertEqual(rt.memory.sp_run["petal_cards"], {"left": "2049990010"})

    def test_learning_stays_learned_in_next_session_and_segment(self):
        rt, root, frame = self.setup_route()
        rt.decide("Review", frame)
        learning = rt.memory.sp_session["learning"]
        learning["confirmed"] = True
        # The newly opened next node is not in this frame; do not fabricate a click.
        rt.decide("Review", {**frame, "sp": 50, "skill_info": {"cost": 0, "state": "learned"}})
        self.assertIn(root, rt.memory.sp_run["learned"])
        restored = nav.RunMemory()
        restored.restore(rt.memory.snapshot())
        self.assertIn(root, restored.sp_run["learned"])
        self.assertEqual(restored.sp_run["catalog"][root]["state"], "learned")
        bound = rt._sp_local_routes(frame["panel"]["slots"], rt.memory.sp_run["catalog"], [root])
        self.assertEqual(bound["routes"][0]["remaining_cost"], 50)

    def test_old_inspect_does_not_override_a_newly_mastered_free_bridge(self):
        from agent.core.true_end.skill_identity import strict_live_identity
        effects = {"components": {"Vo": 4.5}}
        identity = strict_live_identity("同技能", effects)
        for proof in ("history", "equipped"):
            with self.subTest(proof=proof):
                rt, root, frame = self.setup_route()
                node = rt._sp_local_cache["catalog"]["cards"]["2049990010"]["nodes"]["scdb-11"]
                node.update(name="同技能", kind="live_skill", value=4.5, multiplier=4.5,
                            effects=effects, skill_identity=identity)
                cap = wsp.slot_intent((2, 1))
                s = rt.memory.sp_session
                s.update(key=[4, 8], last_tap=None, caps={"Vo": 400}, growth_preview={"Vo": 25})
                s["catalog"][root] = {"name": "同技能", "kind": "live_skill", "attributes": ["Vo"],
                                      "value": 4.5, "multiplier": 4.5, "cost": 10, "state": "learnable",
                                      "duplicate_skill": False}
                s["catalog"][cap] = {"name": "Vocal上限UP", "kind": "cap_up", "attributes": ["Vo"],
                                     "value": 100, "cost": 50, "state": "learnable"}
                rt.memory.sp_run = {"catalog": {}, "spent": 0, "blocked": [], "plan": None,
                    "petal_cards": {"left": "2049990010"}, "team_signature": rt._sp_local_cache["signature"],
                    "mastered_skills": [identity] if proof == "history" else []}
                if proof == "equipped":
                    rt.memory.live_skills = [{"name": "同技能", "attributes": ["Vo"],
                                             "multiplier": 4.5, "skill_identity": identity}]
                frame.update(skill_info=None, skill_text=None)
                frame["panel"]["slots"][cap] = {"slot": [2, 1], "petal": "right", "cost": 50, "state": "open"}
                action = rt.decide("Review", frame)
                self.assertEqual(action.intent, cap)
                self.assertIn(root, s["zero_cost_bridges_unverified"])
                self.assertNotIn(root, rt.memory.sp_run["learned"])
                restored = nav.RunMemory()
                restored.restore(rt.memory.snapshot())
                self.assertIn(identity, restored.sp_run["mastered_skills"])

    def test_sp_uses_the_chosen_growth_preview_and_no_growth_before_audition(self):
        rt, _, _ = self.setup_route()
        readings = {"rooms": [
            {"id": "lesson_vo", "attribute": "Vo", "preview": {"Vo": 25, "Me": 3}},
            {"id": "work_radio", "attribute": "Vo", "preview": {"Vo": 12, "Me": 20}}]}
        self.assertEqual(rt._sp_growth_preview({"kind": "lesson", "option_id": "lesson_vo"}, readings),
                         {"Vo": 25, "Me": 3})
        self.assertEqual(rt._sp_growth_preview({"kind": "work", "option_id": "work_radio"}, readings),
                         {"Vo": 12, "Me": 20})
        self.assertEqual(rt._sp_growth_preview({"kind": "spend_sp", "rule": "primary_at_cap_spend_sp_first"}, readings),
                         {"Vo": 25, "Me": 3})
        self.assertEqual(rt._sp_growth_preview({"kind": "audition"}, readings), {"Vo": 0, "Da": 0, "Vi": 0})
        self.assertIsNone(rt._sp_growth_preview({"kind": "lesson", "option_id": "lesson_vo"},
                                              {"rooms": [{"id": "lesson_vo", "preview": {}}]}))

    def test_confirmed_learning_keeps_the_bound_skill_identity(self):
        from agent.core.true_end.skill_identity import strict_live_identity
        rt, root, frame = self.setup_route()
        effects = {"components": {"Vo": 4.5}}
        identity = strict_live_identity("同技能", effects)
        node = rt._sp_local_cache["catalog"]["cards"]["2049990010"]["nodes"]["scdb-11"]
        node.update(name="同技能", kind="live_skill", value=4.5, multiplier=4.5,
                    effects=effects, skill_identity=identity)
        entry = {"name": "同技能", "kind": "live_skill", "attributes": ["Vo"],
                 "value": 4.5, "multiplier": 4.5, "cost": 10, "state": "learnable"}
        rt.memory.sp_session["catalog"][root] = entry
        frame["skill_text"] = entry
        self.assertEqual(rt.decide("Review", frame).intent, "skill_learn")
        self.assertEqual(rt.memory.sp_session["learning"]["skill_identity"], identity)
        rt.memory.sp_session["learning"]["confirmed"] = True
        rt.decide("Review", {**frame, "sp": 50, "skill_info": {"cost": 0, "state": "learned"}})
        self.assertIn(identity, rt.memory.sp_run["mastered_skills"])
        self.assertTrue(any(k.get("skill_identity") == identity for k in rt.memory.live_skills))

    def hidden_route(self, lb, small=False):
        """The card's root is off screen (planning-only first step); a visible right-petal cap is open."""
        from tests.test_wing_sp_catalog import catalog as make
        rt, root, _ = self.setup_route()
        rt.options = dataclasses.replace(rt.options, sp_small_view=small)
        rt._sp_local_cache["catalog"] = make(lb=lb)
        cap = wsp.slot_intent((2, 1))
        s = rt.memory.sp_session
        s["catalog"] = {cap: {"name": "Vocal上限UP", "kind": "cap_up", "value": 100, "attributes": ["Vo"], "cost": 50,
                              "source": "unknown"}}
        s["last_tap"], s["caps"] = None, {"Vo": 400}  # Vo at its cap
        s["key"], s["growth_preview"] = [4, 8], {"Vo": 25}  # next lesson needs capacity; not the final
        rt.memory.sp_run = {"catalog": {}, "spent": 0, "blocked": [], "plan": None, "petal_cards": {"left": "2049990010"},
                            "team_signature": rt._sp_local_cache["signature"]}
        frame = {"sp": 60, "panel": {"canonical": True, "slots": {cap: {"slot": [2, 1], "petal": "right", "cost": 50, "state": "open"}}},
                 "skill_info": None, "skill_text": None}
        return rt, cap, rt.decide("Review", frame)

    def test_hidden_cap_route_falls_back_to_visible_node(self):
        """2026-10-14 live: a planning-only P-card 「Vocal上限UP」 ended every 振り返り with Vo at its cap."""
        rt, cap, action = self.hidden_route(lb=1)
        self.assertEqual(action.intent, cap)
        self.assertTrue(any(e.get("event") == "sp_route_fallback" for e in rt.log))

    def test_selected_step_is_learned_when_its_bar_hides_it(self):
        """2026-10-15 live (Vo 1114/1114, 84 SP): the planned Vo上限+50 was selected; its own info bar covered the node,
        the re-plan lost it and the bar was closed. Three sessions learned nothing."""
        rt, cap, action = self.hidden_route(lb=1)
        self.assertEqual((action.intent, action.reason), (cap, "sp_select"))
        bar = {"sp": 60, "panel": {"canonical": True, "slots": {}},
               "skill_info": {"cost": 50, "state": "learnable"}, "skill_text": {"name": "Vocal上限UP", "kind": "cap_up"}}
        learn = rt.decide("Review", bar)
        self.assertEqual((learn.intent, learn.reason), ("skill_learn", "sp_learn"))
        self.assertEqual(rt.memory.sp_session["learning"]["intent"], cap)
        self.assertTrue(learn.detail.get("selected_frame"))
        # A bar for a different node (name or cost mismatch) is not learned through this path.
        rt2, cap2, _ = self.hidden_route(lb=1)
        other = {**bar, "skill_text": {"name": "Vocal50%UP", "kind": "passive"}}
        self.assertNotEqual(rt2.decide("Review", other).intent, "skill_learn")

    def test_a_re_entered_default_view_zooms_out_again(self):
        """2026-10-25 live (wing7): 振り返り re-entered from the Hub opened zoomed in, the session had already zoomed out
        once, and taps on outer nodes scrolled the default view under the info bar until sp_panel_unreadable."""
        rt, cap, action = self.hidden_route(lb=1, small=True)
        self.assertEqual(action.reason, "sp_zoom_out_session")
        slot = {"slot": [2, 1], "petal": "right", "cost": 50, "state": "open"}

        def frame(view):
            return {"sp": 60, "panel": {"canonical": True, "view": view, "slots": {cap: dict(slot)}},
                    "skill_info": None, "skill_text": None}

        reasons = []
        for _ in range(nav.SP_MAX_ZOOMS):
            s = rt.memory.sp_session
            rt.decide("Review", frame("small"))                     # the zoom-out worked: re-armed
            self.assertFalse(s["zoom_attempted"])
            s["last_tap"] = None
            s.pop("selected", None)
            reasons.append(rt.decide("Review", frame("canonical")).reason)
            s["last_tap"] = None
            s.pop("selected", None)
        self.assertEqual(reasons[:-1], ["sp_zoom_out_session"] * (nav.SP_MAX_ZOOMS - 1))
        self.assertNotEqual(reasons[-1], "sp_zoom_out_session")     # capped per session
        self.assertEqual(rt.memory.sp_session["zooms"], nav.SP_MAX_ZOOMS)
        rt.memory.sp_session["zoom_attempted"] = True
        self.assertEqual(rt.decide("Hub").intent, "hub_review")       # re-entry: the fresh panel opens zoomed in
        self.assertFalse(rt.memory.sp_session["zoom_attempted"])

    def test_hidden_live_skill_route_does_not_hold_sp(self):
        """Live 4-5/4-4: an S card's 4x skill without a tap target in the canonical view kept 77 SP unspent."""
        rt, cap, action = self.hidden_route(lb=2)
        self.assertEqual(action.intent, cap)
        self.assertTrue(any(e.get("event") == "sp_route_fallback" for e in rt.log))

    def test_reserve_for_a_hidden_target_does_not_block_visible_nodes(self):
        """2026-10-15: no step yet, but the plan reserves SP for a catalog-only target -> the
        visible cap is learned instead of keeping all SP."""
        from agent.core import wing_sp
        from tests.test_wing_sp_catalog import catalog as make
        rt, _, _ = self.setup_route()
        rt._sp_local_cache["catalog"] = make(lb=2)
        cap = wsp.slot_intent((2, 1))
        s = rt.memory.sp_session
        s["catalog"] = {cap: {"name": "Vocal上限UP", "kind": "cap_up", "value": 100, "attributes": ["Vo"], "cost": 50,
                              "source": "unknown"}}
        s["last_tap"], s["caps"] = None, {"Vo": 400}
        rt.memory.sp_run = {"catalog": {}, "spent": 0, "blocked": [], "plan": None,
                            "petal_cards": {"left": "2049990010"}, "team_signature": rt._sp_local_cache["signature"]}
        calls = []

        def reserving(nodes, **kw):
            plan = wing_sp.plan_sp(nodes, **kw)
            calls.append(plan)
            hidden = next((n.id for n in nodes if n.id.startswith("catalog:")), None)
            if len(calls) == 1 and hidden:      # first plan: nothing affordable, SP kept for the hidden target
                return dataclasses.replace(plan, steps=(), target=(hidden,), reserve=kw["sp"])
            return plan
        rt.overrides["sp"] = reserving
        frame = {"sp": 60, "panel": {"canonical": True, "slots": {cap: {"slot": [2, 1], "petal": "right", "cost": 50,
                                                                         "state": "open"}}},
                 "skill_info": None, "skill_text": None}
        action = rt.decide("Review", frame)
        self.assertEqual(action.intent, cap)
        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(any(e.get("event") == "sp_route_fallback" for e in rt.log))
        self.assertEqual(rt.memory.sp_run["plan"]["reserve"], 0)        # the fallback plan's reserve, not 60

    def test_hidden_target_petal_is_scouted_and_an_unread_one_keeps_the_reserve(self):
        """scout the hidden target's own petal; a spent inspect budget is not proof."""
        from agent.core import wing_sp
        from tests.test_wing_sp_catalog import catalog as make
        for inspected, expect in ((0, "sp_route_inspect"), (99, None)):
            with self.subTest(inspected=inspected):
                rt, _, _ = self.setup_route()
                rt._sp_local_cache["catalog"] = make(lb=2)
                cap, scout = wsp.slot_intent((2, 1)), wsp.slot_intent((-3, 2))
                s = rt.memory.sp_session
                s["catalog"] = {cap: {"name": "Vocal上限UP", "kind": "cap_up", "value": 100, "attributes": ["Vo"],
                                      "cost": 50, "source": "unknown"}}
                s["last_tap"], s["caps"], s["inspected"] = None, {"Vo": 400}, inspected
                rt.memory.sp_run = {"catalog": {}, "spent": 0, "blocked": [], "plan": None,
                                    "petal_cards": {"left": "2049990010"},
                                    "team_signature": rt._sp_local_cache["signature"]}

                def reserving(nodes, **kw):
                    plan = wing_sp.plan_sp(nodes, **kw)
                    hidden = next((n.id for n in nodes if n.id.startswith("catalog:")), None)
                    return dataclasses.replace(plan, steps=(), target=(hidden,), reserve=kw["sp"]) if hidden else plan
                rt.overrides["sp"] = reserving
                frame = {"sp": 60, "panel": {"canonical": True, "slots": {
                    cap: {"slot": [2, 1], "petal": "right", "cost": 50, "state": "open"},
                    scout: {"slot": [-3, 2], "petal": "left", "cost": 20, "state": "open"}}},
                    "skill_info": None, "skill_text": None}
                action = rt.decide("Review", frame)
                if expect:
                    self.assertEqual((action.intent, action.reason), (scout, expect))
                else:
                    self.assertNotEqual(action.intent, cap)                 # reserve kept: the cap is not bought
                    self.assertTrue(any(e.get("event") == "sp_reserve_unverified" for e in rt.log))

    def test_unbound_petal_is_inspected_before_route_planning(self):
        """Live 2026-10-14 (S3, 274 SP): only the P petal was bound; every S-card node planned as a 0-value passive."""
        rt, cap, _ = self.hidden_route(lb=1)
        rt.memory.pending = "spend_sp"
        rt.memory.sp_session.update(learned=[], last_tap=None, learning=None, inspected=0)
        other = wsp.slot_intent((1, -2))
        frame = {"sp": 60, "panel": {"canonical": True, "slots": {
            cap: {"slot": [2, 1], "petal": "right", "cost": 50, "state": "open"},
            other: {"slot": [1, -2], "petal": "top_right", "cost": 20, "state": "open"}}},
            "skill_info": None, "skill_text": None}
        action = rt.decide("Review", frame)
        self.assertEqual((action.intent, action.reason), (other, "sp_bind_inspect"))

    def test_cancelled_node_is_not_retried_in_the_session(self):
        """Live a8: 「にゃんにゃんにゃ～ん♡」 was cancelled at the confirm dialog and re-tapped 20 times."""
        from agent.core.wing_skill_panel import same_skill
        self.assertTrue(same_skill("にゃんにゃんにゃ～ん♡", "にゃんにゃんにゃーん"))
        self.assertFalse(same_skill("Vocal上限UP", "Vocal60%UP"))
        rt, cap, first = self.hidden_route(lb=1)
        self.assertEqual(first.intent, cap)
        rt.memory.sp_session.update(skipped=[cap], learning=None, last_tap=None)
        frame = {"sp": 60, "panel": {"canonical": True, "slots": {cap: {"slot": [2, 1], "petal": "right", "cost": 50,
                                                                          "state": "open"}}},
                 "skill_info": None, "skill_text": None}
        self.assertNotEqual(rt.decide("Review", frame).intent, cap)

    def test_fullwidth_ampersand_and_offscreen_learned_node_bind(self):
        """Live 2026-10-14: 「Vocal＆メンタル上限UP」 (ShinyColorsDB) vs OCR 「Vocal&…」, and a learned node whose label
        the frame missed, left both roots unbound, so the planner routed to nodes that were already learned."""
        from agent.core.wing_skill_panel import same_skill
        from agent.core.wing_sp_catalog import bind_sp_catalog
        from tests.test_wing_sp_catalog import catalog as make, KNOWN
        self.assertTrue(same_skill("Vocal&メンタル上限UP", "Vocal＆メンタル上限UP"))
        root = wsp.slot_intent((-2, 1))
        known = {root: {**KNOWN["root"], "state": "learned"}}
        bound = bind_sp_catalog(make(), {}, known, petal_cards={"left": "2049990010"}, learned=[root])
        self.assertEqual(bound["screen_map"].get("catalog:2049990010:scdb-11"), root)
        self.assertNotIn(root, bound["graph"])

    def test_small_view_is_default_and_its_actions_enabled(self):
        """2026-10-14 supervised live sample: zoom-out, small-view node taps and the info-bar close are verified."""
        from tools import run_tasks, wing_nav_build
        params, _ = run_tasks.wing_options({"sp_small_view": False})
        self.assertFalse(params["run"]["sp_small_view"])
        self.assertTrue(nav.RunOptions().sp_small_view)
        actions = wing_nav_build.action_catalog(enabled=True)
        small = [a for a in actions if a["id"].startswith("wing-skill-node-small-")
                 or a["id"] in ("wing-skill-zoom-out", "wing-skill-info-close")]
        self.assertTrue(small)
        self.assertTrue(all(a["enabled"] for a in small))
        rt, cap, action = self.hidden_route(lb=1, small=True)
        self.assertEqual(action.intent, "skill_zoom_out")          # hidden route: zoom out before falling back

    def test_lower_node_under_open_info_bar_closes_the_bar_first(self):
        from agent.core import wing_sp_view as wsv
        low = next(s for s in wsv.SMALL_SLOTS if wsv.under_info_bar(s))
        self.assertIn(wsv.action_intent(low), wsv.SMALL_INTENTS)

    def test_repeated_info_bar_close_ends_the_session(self):
        """2026-10-15 live: select c08_r06 -> its bar covers c09_r15 -> close undoes the select -> select again,
        for minutes. The second identical close ends the session (SP kept) once the bar is gone."""
        from unittest import mock
        from agent.core import wing_sp_view as wsv
        rt, _, _ = self.setup_route()
        low = next(s for s in wsv.SMALL_SLOTS if wsv.under_info_bar(s))
        wanted = wsv.action_intent(low)
        bar = {"sp": 337, "skill_info": {"cost": 20, "state": "learnable"}, "skill_text": {"name": "Vocal4%UP"}}
        closed = {"sp": 337, "skill_info": None, "skill_text": None}
        rt.memory.sp_session["last_tap"] = "skill_node_c08_r06"
        key = list(rt.memory.sp_session["key"])
        with mock.patch.object(type(rt), "_sp_review", lambda self, r, s, fn: nav.NavDecision(
                "click", intent=wanted, reason="sp_select")):
            for _ in range(2):
                rt.memory.sp_session["last_tap"] = "skill_node_c08_r06"
                self.assertEqual(rt._decide_sp("Review", bar).reason, "sp_close_info_bar")
            end = rt._decide_sp("Review", closed)
        self.assertEqual((end.intent, end.reason), ("page_return", "sp_info_bar_loop"))
        self.assertIsNone(rt.memory.sp_session)
        self.assertNotIn(key, rt.memory.sp_done_keys)          # first loop of the week: still open

    def test_closed_bar_taps_the_covered_target_next(self):
        """2026-10-15 live: after closing the bar, the covered target is tapped instead of re-planning."""
        from unittest import mock
        from agent.core import wing_skill_panel as wsp
        from agent.core import wing_sp_view as wsv
        rt, _, _ = self.setup_route()
        low = next(s for s in wsv.SMALL_SLOTS if wsv.under_info_bar(s))
        wanted = wsv.action_intent(low)
        bar = {"sp": 337, "skill_info": {"cost": 20, "state": "learnable"}, "skill_text": {"name": "Vocal4%UP"}}
        node = wsp.slot_intent(tuple(low))
        closed = {"sp": 337, "skill_info": None, "skill_text": None,
                  "panel": {"canonical": True, "slots": {node: {"slot": list(low), "action_intent": wanted}}}}
        rt.memory.sp_session["last_tap"] = "skill_node_c08_r06"
        with mock.patch.object(type(rt), "_sp_review", lambda self, r, s, fn: nav.NavDecision(
                "click", intent="replanned", reason="sp_select")):
            rt.memory.sp_session["after_close"] = {"intent": wanted, "node": node}
            nxt = rt._decide_sp("Review", closed)
            self.assertEqual((nxt.intent, nxt.reason), (wanted, "sp_select_after_close"))
            self.assertEqual(rt.memory.sp_session["last_tap"], node)
            # an unreadable panel (no SP / not canonical / target gone) re-plans instead
            for frame in ({**closed, "sp": None}, {**closed, "panel": {"canonical": False, "slots": {}}}):
                rt.memory.sp_session["after_close"] = {"intent": wanted, "node": node}
                self.assertEqual(rt._decide_sp("Review", frame).intent, "replanned")

    def test_review_warning_entry_opens_an_sp_session(self):
        """2026-10-15: 振り返り opened by the audition's 振り返り warning spends SP instead of leaving at once."""
        rt, _, _ = self.setup_route()
        rt.memory.pending, rt.memory.sp_session, rt.memory.sp_done_keys = None, None, []
        rt.memory.auditions_seen = {"key": [1, 1], "pages": [], "trend": None, "done": True}
        rt._last_intent = ("WingReviewWarning", "warning_to_review")
        frame = {"sp": 60, "panel": {"canonical": True, "slots": {}}, "skill_info": None, "skill_text": None}
        action = rt._decide_sp("Review", frame)
        self.assertNotEqual(action.reason, "review_without_plan")
        start = next(e for e in rt.log if e.get("event") == "sp_session_start")
        self.assertEqual(start["key"], [1, 1])
        rt.memory.pending, rt.memory.sp_session = None, None
        rt._last_intent = ("Schedule", "schedule_audition_tab")
        self.assertEqual(rt._decide_sp("Review", frame).reason, "review_without_plan")


@unittest.skipUnless(DEMO3.is_file() and OCR3.is_file(), "private native sample absent")
class SmallViewNativeTests(unittest.TestCase):
    def test_recorded_zoom_pair_and_moved_view(self):
        reader = learning.NativeFrameTests()
        normal = reader.read(DEMO3, OCR3, 624, "Review")["panel"]
        small = reader.read(DEMO3, OCR3, 625, "Review")["panel"]
        self.assertTrue(normal["canonical"])
        self.assertEqual(normal["view"], "canonical")
        self.assertTrue(small["canonical"], small)
        self.assertEqual(small["view"], "small")
        self.assertGreaterEqual(len(small["slots"]), 30)
        self.assertEqual(small["slots"][wsp.slot_intent((1, -2))]["cost"], 0)
        for slot in small["slots"].values():
            x, y, w, h = view.node_rect(slot["slot"])
            lx, ly, lw, lh = slot["label"]
            self.assertLess(abs((x + w / 2) - (lx + lw / 2)), 6)
            self.assertLess(abs((y + h / 2 + view.SMALL_LIFT) - (ly + lh / 2)), 6)
        self.assertFalse(reader.read(DEMO3, OCR3, 394, "Review")["panel"]["canonical"])
        self.assertFalse(reader.read(DEMO3, OCR3, 401, "Review")["panel"]["canonical"])


if __name__ == "__main__":
    unittest.main()
