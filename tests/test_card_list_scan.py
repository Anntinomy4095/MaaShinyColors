"""Self-scrolling アイドル一覧 scan (agent/core/card_list_scan.py, card_scan_actions.py, pipeline/card_scan.json).

Offline only. Native frames: third demo recording -02/-03 (idol list P/S, filter ON, 並び替え dialog, loading),
the 2026-10-07 Lv順 frame, idol portal and home. Closed-loop runs use ``tests/card_scan_frames.VirtualList`` (native
tile rows stacked into one list, see that module) because the recording's frames never overlap.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from agent.core import card_list_scan as cs
from tests import card_scan_frames as F
from tests.maa_probe import MODEL, NodeProbe, maa_ready

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "assets/resource/base/pipeline/card_scan.json"
PROPOSED = {   # native geometry in the catalog (enabled 2026-10-08 for the supervised CardScan entry only)
    "idol-portal-list": ([830, 456, 170, 44], 0, ["SC.CardScan.Portal.OpenList", "SC.KnowhowScan.Portal.OpenList"]),
    "idol-list-tab-produce": ([30, 184, 140, 44], 0, ["SC.CardScan.List.TapProduce"]),
    "idol-list-tab-support": ([26, 296, 72, 36], 0, ["SC.CardScan.List.TapSupport"]),
    "idol-list-scroll-down": ([270, 430, 130, 40], -60, ["SC.CardScan.List.ScrollDown"]),
    "idol-list-scroll-up": ([420, 430, 130, 40], 60, ["SC.CardScan.List.ScrollUp"]),
}
EXISTING = {"home-idol": ["SC.CardScan.Home.OpenIdol"], "nav-back": ["SC.CardScan.List.Back", "SC.CardScan.Portal.Back"]}


def ocr_reader():
    from tools.replay_ocr import MaaOcrRecognizer

    recognizer = MaaOcrRecognizer(MODEL)

    def read_text(rgb, roi):
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        result = recognizer.recognize({"name": "SC.Test.Text", "roi": list(roi), "expected": "^.*$", "threshold": 0.0}, bgr)
        assert recognizer.input_attempts == 0
        return "".join(i["text"] for i in sorted(result["results"], key=lambda i: i["box"][0])) or None
    return read_text


def run(device, options, read_tiles, read_text, limit=80):
    session = cs.ScanSession(options, read_tiles, read_text)
    decisions = []
    for _ in range(limit):
        decision = session.observe(device.frame())
        decisions.append(decision)
        if decision.kind in ("stop", "leave"):
            break
        device.apply(decision)
    return session, decisions


class PureRuleTests(unittest.TestCase):
    def test_order_parse(self):
        self.assertEqual(cs.parse_order("加入順", "▲昇順", "絞り込み"), (cs.ListOrder("加入順", "asc"), []))
        self.assertEqual(cs.parse_order("lv順", "▼降順", "絞り込み")[0], cs.ListOrder("Lv順", "desc"))
        self.assertEqual(cs.parse_order("デフォルト", "▼降順", "絞り込みON")[1], ["filter_active:絞り込みON"])
        self.assertEqual(cs.parse_order("謎順", "▼降順", "絞り込み")[1], ["sort_unknown:謎順"])
        self.assertEqual(cs.parse_order(None, None, None)[1],
                         ["filter_unreadable", "sort_unreadable", "direction_unreadable"])

    def test_options(self):
        self.assertEqual(cs.ScanOptions.from_params({}), cs.ScanOptions())
        self.assertEqual(cs.ScanOptions.from_params({"tabs": ["produce"], "required_order": ["Lv順", "desc"]}).tabs,
                         ("produce",))
        for bad in ({"tabs": []}, {"tabs": ["fes"]}, {"tabs": ["support", "support"]}, {"max_scrolls_per_tab": 81},
                    {"max_frames": -1}, {"required_order": ["謎", "desc"]}, {"x": 1}):
            with self.subTest(bad), self.assertRaises(ValueError):
                cs.ScanOptions.from_params(bad)

    def test_shift_on_synthetic_strip(self):
        rng = np.random.default_rng(1)
        tall = rng.integers(0, 255, (1200, 312)).astype(np.float32)
        prev, cur = tall[0:510], tall[200:710]
        self.assertEqual(cs.content_shift(prev, cur, "down")[0], 200)
        self.assertEqual(cs.content_shift(cur, prev, "up")[0], -200)
        self.assertEqual(cs.content_shift(prev, prev, "down")[0], 0)
        self.assertIsNone(cs.content_shift(prev, tall[400:910], "down")[0])     # 110 px overlap < one row
        self.assertIsNone(cs.content_shift(prev, cur, "up")[0])                 # wrong direction never matches


@unittest.skipUnless(F.present(), "需要第三次录制的一覧原生帧与首页/アイドル页帧")
class NativeReaderTests(unittest.TestCase):
    def test_loading_after_a_wheel_keeps_the_measured_position(self):
        virtual = F.VirtualList(F.S_TOP, F.S_ROWS)
        before, after = virtual.render(0), virtual.render(2 * F.PITCH)
        loading = after.copy()
        loading.paste((255, 255, 255), (1239, 160, 1244, 702))
        texts = {cs.TITLE_ROI: "アイドル一覧", cs.SORT_BOX: "デフォルト",
                 cs.DIRECTION_BOX: "降順", cs.FILTER_BOX: "絞り込み"}
        session = cs.ScanSession(cs.ScanOptions(tabs=("support",)), lambda frame, name: [],
                                 lambda rgb, roi: texts[roi])
        self.assertEqual(session.observe(before).kind, "scroll")
        self.assertEqual(session.observe(loading).kind, "settle")      # 2026-10-24: first capture after a wheel
        self.assertEqual(session.observe(loading).kind, "hold")        # still, but the list is loading
        self.assertEqual(session.observe(after).kind, "scroll")
        tab, = session.result()["tabs"]
        self.assertEqual((tab["content_px"], tab["scrolls"], tab["frames"]), (308, 2, 2))
        self.assertEqual([shift["dy"] for shift in tab["shifts"]], [308])

    def test_list_still_and_host_pace(self):
        """2026-10-24: after a wheel the list is read once it no longer moves; at most 28 wheels per 61 s."""
        virtual = F.VirtualList(F.S_TOP, F.S_ROWS)
        strip = lambda px: cs.content_strip(np.asarray(virtual.render(px)))  # noqa: E731
        self.assertTrue(cs.list_still(strip(150), strip(150)))
        self.assertTrue(cs.list_still(strip(150), strip(151)))             # sub-pixel settling counts as still
        self.assertFalse(cs.list_still(strip(150), strip(170)))            # still coasting
        session = cs.ScanSession(cs.ScanOptions(tabs=("support",)), lambda frame, name: [], lambda rgb, roi: None)
        now = [100.0]
        session.clock = lambda: now[0]
        session.wheels.extend([50.0] * 28)
        blank = np.zeros((720, 1280, 3), dtype=np.uint8)
        decision = session._settling(blank)
        self.assertEqual((decision.kind, decision.reason), ("settle", "host_pace"))
        now[0] = 111.5
        self.assertIsNone(session._settling(blank))
        self.assertEqual(session.settle["pace_holds"], 1)

    def test_tabs_and_scrollbar(self):
        cases = {F.S_TOP: ("support", "ok", True, False), F.P_MID: ("produce", "ok", False, False),
                 F.P_END: ("produce", "ok", False, True), F.LV_DESC: ("support", "ok", False, False),
                 F.S_LOADING: ("support", "loading", False, False), F.P_ASC: ("produce", "ok", True, False)}
        for frame, (tab, status, top, bottom) in cases.items():
            with self.subTest(frame=frame):
                image = F.load(frame)
                bar = cs.read_scrollbar(image)
                self.assertEqual((cs.selected_tab(image), bar.status, bar.at_top, bar.at_bottom), (tab, status, top, bottom))
        self.assertIsNone(cs.selected_tab(F.load(F.SORT_DIALOG)))     # modal dialog dims every tab
        self.assertIsNone(cs.selected_tab(F.load(F.PORTAL)))
        self.assertEqual(cs.read_scrollbar(F.load(F.HOME)).status, "absent")

    def test_recorded_frames_never_overlap_but_rendered_steps_do(self):
        a, b = cs.content_strip(F.load(F.S_TOP)), cs.content_strip(F.load(F.S_ROWS[1][0]))
        self.assertIsNone(cs.content_shift(a, b, "down")[0])
        virtual = F.VirtualList(F.S_TOP, F.S_ROWS)
        s0, s1 = (cs.content_strip(np.asarray(virtual.render(v))) for v in (0, 2 * F.PITCH))
        self.assertEqual(cs.content_shift(s0, s1, "down"), (2 * F.PITCH, 0.0))


@unittest.skipUnless(F.present() and maa_ready(), "需要 MaaFramework、OCR 模型与一覧原生帧")
class StopReplayTests(unittest.TestCase):
    """Stops and holds on native frames; tile reading is not needed here (no tiles are read before the stop)."""

    @classmethod
    def setUpClass(cls):
        cls.read_text = staticmethod(ocr_reader())

    def scan(self, device, **options):
        return run(device, cs.ScanOptions(**options), lambda frame, name: [], self.read_text)

    def test_filter_on_stops_without_input(self):
        device = F.ScriptedList([F.S_FILTER_ON])
        session, decisions = self.scan(device, tabs=("support",))
        self.assertEqual((decisions[-1].kind, decisions[-1].reason, device.inputs), ("stop", "list_filter_active", []))

    def test_required_order_mismatch_stops_and_never_resorts(self):
        device = F.ScriptedList([F.P_ASC])
        session, decisions = self.scan(device, tabs=("produce",), required_order=("Lv順", "desc"))
        self.assertEqual((decisions[-1].reason, device.inputs), ("list_order_mismatch", []))
        self.assertEqual(session.tabs[0].order, cs.ListOrder("加入順", "asc"))

    def test_recorded_scroll_without_overlap_is_a_partial_stop(self):
        device = F.ScriptedList([F.S_TOP, F.S_ROWS[1][0]])
        session, decisions = self.scan(device, tabs=("support",))
        self.assertEqual([d.kind for d in decisions], ["scroll", "settle", "stop"])
        self.assertEqual(decisions[-1].reason, "overlap_lost")
        self.assertFalse(session.result()["complete"])

    def test_order_change_mid_scan_stops(self):
        virtual = F.VirtualList(F.S_TOP, F.S_ROWS)
        device = F.ScriptedList([F.S_TOP, F.LV_DESC])
        device.frames[0] = virtual.render(0)
        session, decisions = self.scan(device, tabs=("support",))
        self.assertEqual(decisions[-1].reason, "list_order_changed")

    def test_loading_and_dialog_hold_then_stop_within_budget(self):
        for frame in (F.S_LOADING, F.SORT_DIALOG):
            with self.subTest(frame=frame):
                device = F.ScriptedList([frame])
                session, decisions = self.scan(device, tabs=("support",), max_holds=2)
                self.assertEqual([d.kind for d in decisions], ["hold", "hold", "stop"])
                self.assertEqual((decisions[-1].reason, device.inputs), ("hold_budget_exhausted", []))

    def test_tab_tap_that_does_not_switch_stops(self):
        lists = {"support": F.VirtualList(F.S_TOP, F.S_ROWS), "produce": F.VirtualList(F.P_MID, F.P_ROWS)}
        device = F.FakeList(lists, "produce", tap_works=False)
        session, decisions = self.scan(device, tabs=("support",))
        self.assertEqual(device.inputs, [("tap_tab", "support")])
        self.assertEqual(decisions[-1].reason, "tab_switch_failed")

    def test_calibration_step_band(self):
        """9a rule: one down notch must move 154..356 px (one row .. viewport - one row); otherwise stop, never guess."""
        cases = {100: ("overlap_lost", "step_below_min:100<154"), 400: ("overlap_lost", None),
                 200: ("scroll_budget_exhausted", None)}
        for step, (reason, note) in cases.items():
            with self.subTest(step=step):
                device = F.FakeList({"support": F.VirtualList(F.S_TOP, F.S_ROWS)}, "support", step=step)
                session, decisions = self.scan(device, tabs=("support",), max_scrolls_per_tab=1)
                self.assertEqual(device.inputs, [("scroll", "down")])
                self.assertEqual((decisions[-1].kind, decisions[-1].reason), ("stop", reason))
                if note:
                    self.assertIn(note, decisions[-1].notes)
                shift, = session.result()["tabs"][0]["shifts"]
                self.assertEqual(set(shift), {"frame", "direction", "phase", "dy", "overlap_px", "score",
                                              "thumb_top", "thumb_bottom", "at_top", "at_bottom"})
                self.assertEqual((shift["frame"], shift["direction"], shift["phase"]), (3, "down", "scan"))  # + settle
                self.assertEqual(shift["dy"], None if step == 400 else step)
                self.assertEqual(shift["overlap_px"], None if step == 400 else 510 - step)
                self.assertFalse(shift["at_bottom"])
                self.assertFalse(session.result()["complete"])

    def test_short_last_step_at_the_list_end_is_accepted(self):
        virtual = F.VirtualList(F.S_TOP, F.S_ROWS)
        step = next(s for s in range(cs.STEP_MAX_PX, cs.STEP_MIN_PX, -1)
                    if cs.SHIFT_STILL_MAX < virtual.max_scroll % s < cs.STEP_MIN_PX)
        device = F.FakeList({"support": virtual}, "support", step=step)
        session, decisions = self.scan(device, tabs=("support",))
        self.assertLess(shifts_last := virtual.max_scroll % step, cs.STEP_MIN_PX)
        shifts = session.result()["tabs"][0]["shifts"]
        self.assertEqual(decisions[-1].reason, "all_tabs_scanned")
        self.assertEqual(shifts[-1]["dy"], shifts_last)
        self.assertTrue(shifts[-1]["at_bottom"])
        self.assertTrue(all(cs.STEP_MIN_PX <= s["dy"] <= cs.STEP_MAX_PX for s in shifts[:-1]))
        self.assertTrue(session.result()["complete"])

    def test_scroll_budget(self):
        device = F.FakeList({"support": F.VirtualList(F.S_TOP, F.S_ROWS)}, "support")
        session, decisions = self.scan(device, tabs=("support",), max_scrolls_per_tab=2)
        self.assertEqual(device.inputs, [("scroll", "down")] * 2)
        self.assertEqual(decisions[-1].reason, "scroll_budget_exhausted")
        self.assertFalse(session.result()["complete"])


@unittest.skipUnless(F.present() and maa_ready(), "需要 MaaFramework、OCR 模型、卡面参考与一覧原生帧")
class ClosedLoopScanTests(unittest.TestCase):
    """Both tabs, starting mid-list on the wrong tab, with real tile/Lv/star reading (Maa OCR)."""

    @classmethod
    def setUpClass(cls):
        from agent.core import card_inventory as ci
        from agent.core import owned_levels as ol

        index, ocr = ci.CardIndex.from_assets(), ol.maa_ocr(MODEL)
        cls.lists = {"support": F.VirtualList(F.S_TOP, F.S_ROWS), "produce": F.VirtualList(F.P_MID, F.P_ROWS)}
        cls.device = F.FakeList(cls.lists, "produce", start_scroll=500)
        cls.session, cls.decisions = run(cls.device, cs.ScanOptions(),
                                         lambda frame, name: ol.read_frame(frame, index, ocr, frame_name=name),
                                         ocr_reader())
        cls.result = cls.session.result()

    def test_input_sequence_and_completion(self):
        self.assertEqual(self.device.inputs, [("tap_tab", "support")] + [("scroll", "down")] * 5
                         + [("tap_tab", "produce")] + [("scroll", "down")] * 4)
        self.assertEqual(self.decisions[-1].reason, "all_tabs_scanned")
        tabs = {t["tab"]: t for t in self.result["tabs"]}
        self.assertTrue(self.result["complete"])
        self.assertEqual((tabs["support"]["end_reason"], tabs["produce"]["end_reason"]),
                         ("scrollbar_at_end", "scrollbar_at_end"))
        self.assertEqual(tabs["support"]["slots"], 7 * self.lists["support"].rows)
        self.assertEqual(tabs["produce"]["slots"], 7 * self.lists["produce"].rows)
        self.assertTrue(all(s["score"] == 0.0 for t in tabs.values() for s in t["shifts"]))
        self.assertEqual({t["order"]["key"] for t in tabs.values()}, {"デフォルト"})

    def test_cards_are_deduplicated_and_shaped_for_the_reviewed_import(self):
        cards, details = self.result["cards"], self.result["details"]
        self.assertGreaterEqual(len(cards), 100)
        self.assertEqual(self.result["conflicts"], {})
        self.assertEqual(set(cards), set(details))
        accepted = [r for r in self.session.records if r["status"] == "accepted"]
        self.assertGreater(len(accepted), len(cards))          # overlapping rows were read more than once
        for cid, entry in details.items():
            self.assertEqual(("level" in entry), cid.startswith("2"), cid)
        payloads = cs.import_payloads(self.result, "2026-10-07T00:00:00+00:00")
        self.assertEqual(payloads["owned_levels"]["details"], details)
        self.assertTrue(payloads["inventory"]["complete"])
        self.assertEqual(len(payloads["inventory"]["cards"]), len(cards))


def _test_table():
    from agent.core.pipeline_actions import parse_table

    data = json.loads((ROOT / "assets/input/pipeline_actions.json").read_text(encoding="utf-8"))
    for action in data["actions"]:
        if action["id"] in PROPOSED:
            action["enabled"] = True  # local screenshot simulation only
    return parse_table(json.dumps(data, ensure_ascii=False).encode("utf-8"))


@unittest.skipUnless(F.present() and maa_ready(), "需要 MaaFramework、私有模板包、OCR 模型与原生帧")
class PipelineReplayTests(unittest.TestCase):
    """Shipped card_scan.json run by Maa on native/native-derived frames; catalog enabled only in a test copy."""

    @classmethod
    def setUpClass(cls):
        cls.probe = NodeProbe()

    def test_guards_on_native_frames(self):
        hits = {
            "SC.CardScan.List.Guard": ((F.S_TOP, F.P_MID, F.P_END, F.LV_DESC, F.S_FILTER_ON, F.P_ASC),
                                       (F.PORTAL, F.HOME, F.SORT_DIALOG)),
            "SC.CardScan.Anchor.Portal.ListEntry": ((F.PORTAL, F.PORTAL_FROM_LIST), (F.HOME, F.S_TOP)),
            "SC.Nav.IdolPortal.Guard": ((F.PORTAL, F.PORTAL_FROM_LIST), (F.S_TOP, F.HOME)),
            "SC.CardScan.HomeClean": ((F.HOME,), ()),
        }
        for node, (positive, negative) in hits.items():
            for frame in positive:
                with self.subTest(node=node, frame=frame):
                    self.assertTrue(self.probe.hits(frame, node))
            for frame in negative:
                with self.subTest(node=node, frame=frame, expect=False):
                    self.assertFalse(self.probe.hits(frame, node))

    def test_post_action_guards_distinguish_target_tab_and_wrong_pages(self):
        cases = {
            "SC.CardScan.Portal.Settled": (F.PORTAL, F.S_TOP),
            "SC.CardScan.List.Settled": (F.S_TOP, F.PORTAL),
            "SC.CardScan.List.SupportSettled": (F.S_TOP, F.P_MID),
            "SC.CardScan.List.ProduceSettled": (F.P_MID, F.S_TOP),
            "SC.CardScan.Home.Settled": (F.HOME, F.PORTAL),
        }
        for node, (good, bad) in cases.items():
            with self.subTest(node=node):
                self.assertTrue(self.probe.hits(good, node))
                self.assertFalse(self.probe.hits(bad, node))

    def test_full_chain_with_proposed_actions(self):
        from maa.tasker import Tasker
        from PIL import Image

        from agent.core import card_scan_actions as csa
        from tools import run_tasks as launcher

        with tempfile.TemporaryDirectory() as tmp:
            runtime = csa.CardScanRuntime(sink=_Sink(), run_dir=Path(tmp), write_import=False)
            recognitions, actions = csa.card_scan_registrations(runtime)
            resource = self.probe.resource()
            for name, obj in recognitions.items():
                self.assertTrue(resource.register_custom_recognition(name, obj))
            for name, obj in actions.items():
                self.assertTrue(resource.register_custom_action(name, obj))
            virtual = F.VirtualList(F.S_TOP, F.S_ROWS)
            steps = [F.HOME, F.PORTAL]
            images = [F.load(F.HOME), F.load(F.PORTAL)]
            scroll = 0
            while True:
                images.append(np.asarray(virtual.render(scroll)))
                if scroll >= virtual.max_scroll:
                    break
                scroll = min(scroll + 2 * F.PITCH, virtual.max_scroll)
            images += [F.load(F.PORTAL_FROM_LIST), F.load(F.HOME)]
            images = [np.ascontiguousarray(i[:, :, ::-1]) for i in images]        # BGR like read_png
            del steps
            scenario = launcher.ReplayScenario(images, ["click"] * len(images), allow_input=True, table=_test_table(),
                                               actions=set(PROPOSED) | set(EXISTING))
            controller = launcher.create_replay_controller(scenario)
            self.assertTrue(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            override = _enabled_override({"tabs": ["support"]})
            job = tasker.post_task("SC.CardScan.Entry", override).wait()
            self.assertTrue(job.succeeded, runtime.outcome)
            scroll_steps = len(images) - 5
            self.assertEqual(scenario.actions, ["home-idol", "idol-portal-list"] + ["idol-list-scroll-down"] * scroll_steps
                             + ["nav-back", "nav-back"])
            self.assertEqual(scenario.refused, [])
            self.assertEqual(runtime.outcome, "scanned")
            session = runtime.session
            self.assertEqual(session.frames, 2 * scroll_steps + 1)   # each wheel: one settle capture, then the read
            self.assertEqual(session.settle["still"], scroll_steps)
            self.assertEqual(session.holds, 0)
            self.assertTrue(runtime.result["complete"])
            self.assertEqual(len(list(Path(tmp).glob("card-scan-*.json"))), 1)
            del Image


class _Sink:
    def __init__(self):
        self.items = []

    def add(self, kind, payload):
        self.items.append((kind, payload))


def _enabled_override(config):
    nodes = json.loads(PIPELINE.read_text(encoding="utf-8"))
    override = {}
    for name, node in nodes.items():
        patch = {"enabled": True} if "enabled" in node else {}
        if patch:
            override[name] = patch
    for name in ("SC.CardScan.List.ScrollDown", "SC.CardScan.List.ScrollUp"):
        override[name]["post_delay"] = 300   # replay frames never animate; the shipped 5 s pacing is for the host
    override["SC.CardScan.Config"] = {"attach": config}
    return override


class PipelineContractTests(unittest.TestCase):
    def setUp(self):
        self.nodes = json.loads(PIPELINE.read_text(encoding="utf-8"))

    def test_click_nodes_stay_disabled_and_the_flow_is_runnable(self):
        # 2026-10-08: the non-input flow is enabled; every Click node stays disabled in the JSON and only the supervised
        # CardScan entry of tools/run_tasks.py enables them (by action id). Scroll steps wait 1 s; the session proves the
        # list still and paces to the host's 30/min (2026-10-24; 5 s was for the old 12/min).
        for name, node in self.nodes.items():
            self.assertTrue(name.startswith("SC.CardScan."), name)
            self.assertNotIn(node.get("action", "DoNothing"), ("LongPress", "Swipe", "MultiSwipe", "Scroll",
                                                               "InputText", "ClickKey"), name)
            if node.get("action") == "Click":
                self.assertIs(node.get("enabled"), False, name)
            else:
                self.assertNotIn("enabled", node, name)
        for name in ("SC.CardScan.List.ScrollDown", "SC.CardScan.List.ScrollUp"):
            self.assertEqual(self.nodes[name]["post_delay"], 1000)
        settle = self.nodes["SC.CardScan.List.Settle"]
        self.assertEqual((settle["all_of"], settle["next"], settle["post_delay"]),
                         (["SC.CardScan.List.Guard", "SC.CardScan.Want.Settle"], ["SC.CardScan.Advance"], 300))

    def test_clicks_bind_native_rects_with_guards_and_error_paths(self):
        from agent.core.pipeline_actions import default_table

        table = default_table()
        bound = table.node_actions(include_disabled=True)
        clicks = {name: node for name, node in self.nodes.items() if node.get("action") == "Click"}
        # An action may also bind SC.KnowhowScan.* nodes (shared idol-portal-list); this test covers the CardScan nodes only.
        wanted = {n: a for a, (_, _, nodes) in PROPOSED.items() for n in nodes if n.startswith("SC.CardScan.")}
        wanted.update({n: a for a, nodes in EXISTING.items() for n in nodes})
        self.assertEqual({n: bound[n] for n in clicks}, wanted)
        for name, node in clicks.items():
            self.assertEqual(node["target"], list(table.get(bound[name]).rect))
            self.assertEqual(node["on_error"], ["SC.CardScan.Abort"])
            self.assertEqual(len(node["next"]), 1)
            successor = self.nodes[node["next"][0]]
            self.assertIn(successor["recognition"], ("And", "Or"))
            self.assertEqual(successor["on_error"], ["SC.CardScan.Abort"])
            self.assertEqual(node["recognition"], "And", name)
            self.assertTrue(any(g.endswith("Guard") or g.startswith("SC.Common.Anchor") for g in node["all_of"]), name)
            self.assertTrue(any(g.startswith("SC.CardScan.Want.") or g.startswith("SC.CardScan.State.")
                                for g in node["all_of"]), name)
        main = self.nodes["SC.CardScan.Main"]
        self.assertEqual(main["on_error"], ["SC.CardScan.Abort"])

    def test_not_in_tasks_or_policy(self):
        for path in ("assets/interface.json", "project_policy.json"):
            self.assertNotIn("SC.CardScan", (ROOT / path).read_text(encoding="utf-8"), path)
        for path in (ROOT / "assets/tasks").glob("*.json"):
            self.assertNotIn("SC.CardScan", path.read_text(encoding="utf-8"), path.name)

    def test_native_rects_match_the_catalog_and_are_free_of_other_rects(self):
        table = json.loads((ROOT / "assets/input/pipeline_actions.json").read_text(encoding="utf-8"))
        by_id = {a["id"]: a for a in table["actions"]}
        for action_id, (rect, wheel, nodes) in PROPOSED.items():
            self.assertEqual((by_id[action_id]["rect"], by_id[action_id].get("wheel", 0), by_id[action_id]["nodes"]),
                             (rect, wheel, nodes))
            self.assertIs(by_id[action_id]["enabled"], True)
            self.assertIs(by_id[action_id]["consuming"], False)
            x, y, w, h = rect
            for action in table["actions"]:
                if action["id"] == action_id:
                    continue
                ax, ay, aw, ah = action["rect"]
                overlap = x < ax + aw and ax < x + w and y < ay + ah and ay < y + h
                with self.subTest(action_id=action_id, other=action["id"]):
                    # Version 2 tables allow overlap only between disjoint exact node bindings (e.g. home-present on the
                    # home screen vs the list's プロデュース tab); the controller resolves by node, never by point alone.
                    self.assertFalse(overlap and not (table["version"] == 2 and set(nodes).isdisjoint(action["nodes"])))

    def test_default_launcher_cannot_enable_the_scan_or_plan_it(self):
        from agent.core.webview_controller import PIPELINE_FAMILIES
        from tools import run_tasks

        for mode in (run_tasks.OFFLINE_SIMULATED, run_tasks.LIVE_INPUT):
            for families in (["card-scan", "nav", "training-bronze"], sorted(PIPELINE_FAMILIES)):
                overrides = run_tasks.mode_override(mode, families=families)
                self.assertEqual([n for n in overrides if n.startswith("SC.CardScan.")], [])
        interface = run_tasks.load_interface()
        self.assertNotIn("CardScan", interface.tasks)
        for config in ({"tasks": ["CardScan"]}, {"preset": None, "tasks": ["CardScan"]}):
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, "未知任务"):
                run_tasks.plan_tasks(interface, config)


if __name__ == "__main__":
    unittest.main()
