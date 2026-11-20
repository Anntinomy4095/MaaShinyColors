"""CardScan candidate evaluation is read-only until a guarded step / hold acknowledges it."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from agent.core import card_list_scan as cs
from agent.core import card_scan_actions as csa
from tests import card_scan_frames as F
from tests.maa_probe import maa_ready

ROOT = Path(__file__).resolve().parents[1]
WANTS = ({"want": "tap_tab", "tab": "support"}, {"want": "tap_tab", "tab": "produce"},
         {"want": "scroll", "direction": "down"}, {"want": "scroll", "direction": "up"},
         {"want": "hold"}, {"want": "exit"})


def _frame(tab="support"):
    rgb = np.random.default_rng(19).integers(0, 220, (720, 1280, 3), dtype=np.uint8)
    for roi in cs.TAB_TEXT_ROI.values():
        x, y, w, h = roi
        rgb[y:y+h, x:x+w] = 0
    x, y, w, h = cs.TAB_TEXT_ROI[tab]
    rgb[y:y+h, x:x+w] = (240, 90, 150)
    return np.ascontiguousarray(rgb[:, :, ::-1])


class CandidateStepTests(unittest.TestCase):
    def runtime(self, decision):
        runtime = csa.CardScanRuntime(save_frames=False, write_import=False)
        session = Mock(frames=0, final=None)

        def observe(frame):
            session.frames += 1
            return decision

        session.observe.side_effect = observe
        runtime.session = session
        return runtime

    def test_repeated_candidates_and_background_animation_observe_once(self):
        rt = self.runtime(cs.Decision("scroll", "scan", direction="down"))
        image = _frame()
        for _ in range(3):
            for want in WANTS:
                image[700, 1000, 0] += 1       # animated pixel outside the selected tab / list content
                self.assertEqual(rt.want(None, image, want), want == WANTS[2])
        self.assertEqual(rt.session.frames, 1)
        rt.session.observe.assert_called_once()

    def test_ack_allows_an_identical_frame_to_be_observed_again(self):
        rt = self.runtime(cs.Decision("hold", "list_loading"))
        image = _frame()
        self.assertTrue(rt.want(None, image, WANTS[4]))
        self.assertTrue(rt.want(None, image, WANTS[4]))
        self.assertEqual(rt.session.frames, 1)
        self.assertTrue(rt.advance(None, {}))
        self.assertTrue(rt.want(None, image, WANTS[4]))
        self.assertEqual(rt.session.frames, 2)

    def test_selected_tab_change_never_authorizes_cached_input(self):
        for decision, want in ((cs.Decision("scroll", "scan", direction="down"), WANTS[2]),
                               (cs.Decision("tap_tab", "next_tab", tab="produce"), WANTS[1]),
                               (cs.Decision("leave", "all_tabs_scanned"), WANTS[5])):
            with self.subTest(kind=decision.kind):
                rt = self.runtime(decision)
                self.assertTrue(rt.want(None, _frame("support"), want))
                self.assertFalse(rt.want(None, _frame("produce"), want))
                self.assertEqual(rt.session.frames, 1)

    def test_non_input_ack_registration_and_unstarted_runtime(self):
        rt = csa.CardScanRuntime(save_frames=False)
        self.assertFalse(rt.advance(None, {}))
        _, actions = csa.card_scan_registrations(rt)
        self.assertIsInstance(actions["SC.CardScan.Advance"], csa.CardScanAdvance)

    def test_missing_card_icons_stop_with_their_own_reason(self):
        # 2026-11-22 new install: no ui/game-assets/manifest.json -> "card_library_missing", not a bare abort
        from agent.core import card_inventory as ci
        rt = csa.CardScanRuntime(save_frames=False, write_import=False)
        reports = []
        rt.sink = type("Sink", (), {"add": lambda _self, kind, payload: reports.append(payload)})()
        context = type("Ctx", (), {"get_node_data": lambda _self, name: {}})()
        with patch.object(ci.CardIndex, "from_assets", side_effect=FileNotFoundError("manifest.json")):
            self.assertFalse(rt.begin(context, {"config_nodes": []}))
        self.assertFalse(rt.stop(None, {"reason": "abort"}))
        self.assertEqual([r["outcome"] for r in reports], ["stopped:card_library_missing"])

    def session_runtime(self):
        texts = {cs.TITLE_ROI: "アイドル一覧", cs.SORT_BOX: "デフォルト",
                 cs.DIRECTION_BOX: "降順", cs.FILTER_BOX: "絞り込み"}
        rt = csa.CardScanRuntime(save_frames=False, write_import=False)
        rt.session = cs.ScanSession(cs.ScanOptions(tabs=("support",)), lambda frame, name: [],
                                    lambda rgb, roi: texts[roi])
        return rt

    def test_no_real_scroll_does_not_record_a_shift_or_false_bottom(self):
        rt = self.session_runtime()
        image = _frame()
        with patch.object(cs, "read_scrollbar", return_value=cs.Scrollbar("ok", 191, 234)):
            for want in WANTS * 3:
                image[700, 1000, 0] += 1
                rt.want(None, image, want)
        self.assertEqual(rt.session.frames, 1)
        self.assertEqual(rt.session.tabs[0].scrolls, 1)
        self.assertEqual(rt.session.tabs[0].shifts, [])
        self.assertFalse(rt.session.tabs[0].complete)
        self.assertIsNone(rt.session.final)

    def test_scroll_loading_hold_ack_retains_pending_real_movement(self):
        rt = self.session_runtime()
        before = _frame()
        after = before.copy()
        after[170:372, 257:1231] = before[478:680, 257:1231]
        # The full 308 px displacement retains 202 px of real overlap.
        with patch.object(cs, "read_scrollbar", return_value=cs.Scrollbar("ok", 191, 234)):
            self.assertTrue(rt.want(None, before, WANTS[2]))
        rt.advance(None, {})
        with patch.object(cs, "read_scrollbar", return_value=cs.Scrollbar("loading")):
            for want in WANTS * 2:
                rt.want(None, after, want)          # first capture after the wheel: settle (not yet proven still)
            self.assertEqual(rt.cache[1].kind, "settle")
            rt.advance(None, {})
            for want in WANTS * 2:
                rt.want(None, after, want)          # still, but the track is loading
        self.assertEqual((rt.session.frames, rt.session.holds), (3, 1))
        self.assertEqual(rt.session.pending, ("scroll", "down"))
        rt.advance(None, {})
        with patch.object(cs, "read_scrollbar", return_value=cs.Scrollbar("ok", 280, 323)):
            self.assertTrue(rt.want(None, after, WANTS[2]))
        self.assertEqual(rt.session.frames, 4)
        self.assertEqual([s["dy"] for s in rt.session.tabs[0].shifts], [308])
        self.assertIsNone(rt.session.final)


class PipelineStepContractTests(unittest.TestCase):
    def test_only_completed_list_steps_acknowledge_observations(self):
        nodes = json.loads((ROOT / "assets/resource/base/pipeline/card_scan.json").read_text(encoding="utf-8"))
        predecessors = {name for name, node in nodes.items() if "SC.CardScan.Advance" in node.get("next", [])}
        self.assertEqual(predecessors, {"SC.CardScan.List.Settled", "SC.CardScan.List.SupportSettled",
                                        "SC.CardScan.List.ProduceSettled", "SC.CardScan.List.Hold",
                                        "SC.CardScan.List.Settle"})
        ack = nodes["SC.CardScan.Advance"]
        self.assertEqual((ack["recognition"], ack["action"], ack["custom_action"]),
                         ("DirectHit", "Custom", "SC.CardScan.Advance"))
        self.assertEqual(ack["on_error"], ["SC.CardScan.Abort"])
        for name in predecessors:
            self.assertEqual(nodes[name]["on_error"], ["SC.CardScan.Abort"])
        for name in ("SC.CardScan.List.ScrollDown", "SC.CardScan.List.ScrollUp"):
            self.assertEqual(nodes[name]["max_hit"], 2 * 80)
        self.assertEqual(nodes["SC.CardScan.Config"]["attach"]["max_scrolls_per_tab"], 30)


@unittest.skipUnless(F.present() and maa_ready(), "需要 MaaFramework 与原生列表回放帧")
class PipelineStepReplayTests(unittest.TestCase):
    def test_maa_candidate_chain_hold_then_one_scroll(self):
        from maa.tasker import Tasker
        from tools import run_tasks as launcher
        from tests.test_card_list_scan import EXISTING, PROPOSED, _test_table
        from tests.maa_probe import NodeProbe

        virtual = F.VirtualList(F.S_TOP, F.S_ROWS)
        loaded, after = virtual.render(0), virtual.render(308)
        loading = loaded.copy()
        loading.paste((255, 255, 255), (1239, 160, 1244, 702))
        images = [np.ascontiguousarray(np.asarray(im)[:, :, ::-1]) for im in (loading, loaded, after)]
        scenario = launcher.ReplayScenario(images, ["click"] * 3, allow_input=True, table=_test_table(),
                                           actions=set(PROPOSED) | set(EXISTING))
        rt = CandidateStepTests().session_runtime()
        rt.session.options = cs.ScanOptions(tabs=("support",), max_scrolls_per_tab=1)
        completed = []
        original_advance = rt.advance

        def advance(context, p):
            completed.append(rt.cache[1].kind)
            if len(completed) == 1:      # simulated asynchronous thumbnail/track loading after the completed Hold
                scenario.index = 1
                scenario.captures_here = 0
            return original_advance(context, p)

        rt.advance = advance
        resource = NodeProbe.resource()
        recos, actions = csa.card_scan_registrations(rt)
        for name, obj in recos.items():
            self.assertTrue(resource.register_custom_recognition(name, obj))
        for name, obj in actions.items():
            self.assertTrue(resource.register_custom_action(name, obj))
        controller = launcher.create_replay_controller(scenario)
        self.assertTrue(controller.set_screenshot_use_raw_size(True))
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        # Page recognition has its own native replay tests; isolate the shipped candidate/action/JumpBack wiring.
        override = {
            "SC.Test.Start": {"recognition": "DirectHit", "next": ["SC.CardScan.Main"]},
            "SC.Test.End": {"recognition": "Custom", "custom_recognition": "SC.CardScan.Want",
                            "custom_recognition_param": {"is": "finished"}},
            "SC.CardScan.Main": {"next": ["SC.Test.End"] + ["[JumpBack]SC.CardScan.List." + name
                                   for name in ("Settle", "TapSupport", "TapProduce", "ScrollDown", "ScrollUp", "Hold")]},
            "SC.CardScan.List.Guard": {"recognition": "DirectHit"},
            "SC.CardScan.Color.ScanTab": {"recognition": "DirectHit"},
            "SC.CardScan.List.Settled": {"recognition": "DirectHit"},
            "SC.CardScan.List.Hold": {"post_delay": 0},
            "SC.CardScan.List.Settle": {"post_delay": 0},
        }
        for name in ("TapSupport", "TapProduce", "ScrollDown", "ScrollUp"):
            override["SC.CardScan.List." + name] = {"enabled": True, "post_delay": 0}
        job = tasker.post_task("SC.Test.Start", override).wait()
        self.assertTrue(job.succeeded)
        self.assertEqual(scenario.actions, ["idol-list-scroll-down"])
        self.assertEqual(scenario.refused, [])
        self.assertEqual(completed, ["hold", "scroll", "settle"])   # the wheel's first capture proves nothing yet
        self.assertEqual((rt.session.frames, rt.session.tabs[0].scrolls), (4, 1))
        self.assertEqual([s["dy"] for s in rt.session.tabs[0].shifts], [308])
        self.assertEqual(rt.session.final.reason, "scroll_budget_exhausted")


class StopRecordTests(unittest.TestCase):
    def test_abort_after_partial_records_reason_and_swallowed_errors(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            rt = csa.CardScanRuntime(run_dir=tmp, save_frames=False, write_import=False)
            session = Mock(frames=3, final=None, records=[{"x": 1}])
            session.result.return_value = {"complete": False, "outcome": None, "cards": {}, "tabs": []}
            rt.session = session
            rt.note_error("want", RuntimeError("boom"))
            rt.stop(None, {"reason": "abort"})
            self.assertEqual(rt.outcome, "stopped:abort")      # not the silent "partial:None"
            self.assertEqual(rt.result["stop_reason"], "abort")
            self.assertIn("RuntimeError: boom", rt.result["errors"][0])
            report = rt.sink.drain()[-1]
            self.assertEqual(report["errors"], rt.result["errors"])


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
