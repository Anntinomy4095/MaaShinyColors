"""Passive resource observer: never acts, never blocks, refuses low confidence, reads
native home frames correctly, and leaves the frame-bound click chain of a task unchanged."""
import hashlib
import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from agent.core import resource_observer as ro
from agent.core.task_actions import ReportSink
from tools.check_project import load_jsonc

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "assets" / "resource" / "base" / "pipeline"
P = "dev_assets/private/"
HOME_NOBADGE = P + "manual-capture-kept-20261002/home-20261003-nobadge.png"
HOME_SHOP = P + "shop-20261002-01-home-frames/frame-001.png"
HOME_DIMMED = P + "manual-capture-kept-20261002/home-20261003-nobadge-dimmed-synthetic.png"
FES_MAIN = P + "grade-run-03c-entry-frames/frame-008.png"
ANCHORS = [("プロデュース", 0.99, (997, 640, 120, 32)), ("フェス", 0.99, (1150, 639, 75, 33)), ("営業", 0.99, (777, 667, 53, 30))]
COUNTERS = [("17,678,700", 0.999, (354, 17, 104, 22)), ("48,157", 0.999, (535, 15, 60, 26)),
            ("300/300", 0.999, (1005, 665, 103, 33)), ("20/20+0", 0.999, (1138, 671, 95, 22))]


def frame():
    return np.zeros((720, 1280, 3), dtype=np.uint8)


class FakeVision:
    """Stands in for MaaVision: guard hits by node name and OCR rows filtered by ROI (centre inside)."""
    calls = []

    def __init__(self, home=True, foreign=False, texts=ANCHORS + COUNTERS, error=None, on_ocr=None):
        self.home, self.foreign, self.texts, self.error, self.on_ocr = home, foreign, texts, error, on_ocr

    def __call__(self, context):
        return self

    def hit(self, node, image):
        FakeVision.calls.append(("hit", node))
        if self.error:
            raise self.error
        return {ro.HOME_GUARD: self.home, ro.HOME_FOREIGN: self.foreign}[node]

    def ocr(self, image, roi):
        FakeVision.calls.append(("ocr", tuple(roi)))
        if self.on_ocr:
            self.on_ocr()
        x, y, w, h = roi
        return [t for t in self.texts if x <= t[2][0] + t[2][2] / 2 < x + w and y <= t[2][1] + t[2][3] / 2 < y + h]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def observer(vision, clock=None, **options):
    sink = ReportSink()
    return ro.ResourceObserver(sink, clock=clock or Clock(), vision=vision, **options), sink


class ObserverRuleTests(unittest.TestCase):
    def test_reads_home_counters_with_time_stamp_and_never_hits(self):
        obs, sink = observer(FakeVision())
        self.assertIsNone(obs.observe(None, frame()))
        report, = sink.drain()
        self.assertEqual((report["kind"], report["layout"], report["source"]), ("resources", "home_counters", "passive_observer"))
        self.assertEqual(report["items"], {
            "money": {"count": 17678700, "confidence_pm": 999},
            "jewel": {"count": 48157, "confidence_pm": 999, "indicator_only": True},
            "stamina": {"count": 300, "max": 300, "confidence_pm": 999},
            "fes": {"count": 20, "max": 20, "plus": 0, "confidence_pm": 999}})
        self.assertTrue(report["observed_at"].endswith("+00:00"))
        self.assertNotIn("text", json.dumps(report))  # no raw OCR text leaves the observer

    def test_low_confidence_and_malformed_counters_are_refused_never_guessed(self):
        texts = ANCHORS + [("17,678,700", 0.85, (354, 17, 104, 22)), ("17/20D+0", 0.99, (1138, 671, 95, 22)),
                           ("300/300", 0.999, (1005, 665, 103, 33))]
        obs, sink = observer(FakeVision(texts=texts))
        obs.observe(None, frame())
        report, = sink.drain()
        self.assertEqual(report["items"], {"stamina": {"count": 300, "max": 300, "confidence_pm": 999}})
        self.assertEqual(report["rejected"], ["fes", "jewel", "money"])
        obs2, sink2 = observer(FakeVision(texts=ANCHORS + [("17,678,700", 0.5, (354, 17, 104, 22))]))
        with mock.patch.object(ro, "gacha_reading", return_value=None):
            obs2.observe(None, frame())
        self.assertEqual(sink2.drain(), [])
        self.assertEqual(obs2.summary()["refusals"], {"no_confident_counter": 1})

    def test_gacha_free_tag_is_reported_only_on_a_definite_read(self):
        orange = frame()
        orange[630:668, 390:502] = (20, 100, 245)      # BGR: the tag fill of the native free frames
        tag = [("1日1回無料", 0.98, (400, 636, 96, 24))]
        cases = ((frame(), [], {"state": "no_tag", "kind": None, "fill_pm": 0}),
                 (orange, tag, {"state": "free", "kind": "free_daily", "fill_pm": 1000}),
                 (orange, [("超成功発生中", 0.98, (400, 636, 96, 24))], None),   # orange tag, other notice
                 (orange, [("1日1回無料", 0.60, (400, 636, 96, 24))], None))     # unclean OCR: no reminder
        for image, extra, want in cases:
            obs, sink = observer(FakeVision(texts=ANCHORS + COUNTERS + extra))
            obs.observe(None, image)
            report, = sink.drain()
            self.assertEqual(report.get("gacha"), want)
            self.assertEqual(report["items"]["money"]["count"], 17678700)
        obs, sink = observer(FakeVision(texts=ANCHORS))        # no counter, but a definite plain plate: still reported
        obs.observe(None, frame())
        report, = sink.drain()
        self.assertEqual((report["items"], report["gacha"]["state"]), ({}, "no_tag"))
        self.assertNotIn("無料", json.dumps(report, ensure_ascii=False))

    def test_wrong_screen_overlay_dialog_and_canvas_are_refused_without_ocr(self):
        for vision, image, reason in ((FakeVision(home=False), frame(), "not_home"),
                                      (FakeVision(foreign=True), frame(), "home_overlay"),
                                      (FakeVision(), np.zeros((360, 640, 3), np.uint8), "frame_not_1280x720"),
                                      (FakeVision(texts=COUNTERS), frame(), "screen_guard_failed"),
                                      (FakeVision(texts=ANCHORS + COUNTERS + [("OK", 0.99, (520, 560, 40, 30))]), frame(), None)):
            obs, sink = observer(vision)
            FakeVision.calls = []
            obs.observe(None, image)
            if reason is None:  # the OK button lies outside every observer ROI: nothing to see, counters still read
                self.assertEqual(len(sink.drain()), 1)
                continue
            self.assertEqual(sink.drain(), [], reason)
            self.assertEqual(obs.summary()["refusals"], {reason: 1})
            if reason in ("not_home", "home_overlay", "frame_not_1280x720"):
                self.assertFalse([c for c in FakeVision.calls if c[0] == "ocr"], reason)

    def test_dialog_text_inside_a_counter_roi_refuses_the_frame(self):
        obs, sink = observer(FakeVision(texts=ANCHORS + COUNTERS + [("キャンセル", 0.99, (1000, 670, 80, 20))]))
        obs.observe(None, frame())
        self.assertEqual(sink.drain(), [])
        self.assertEqual(obs.summary()["refusals"], {"dialog_overlay": 1})

    def test_throttle_read_cap_and_unchanged_values_bound_the_cost(self):
        clock = Clock()
        obs, sink = observer(FakeVision(), clock, min_interval_s=20.0, max_reads=2, refresh_s=300.0)
        FakeVision.calls = []
        for t in (0.0, 5.0, 10.0):          # one attempt; the throttled calls touch nothing
            clock.t = t
            obs.observe(None, frame())
        self.assertEqual(len([c for c in FakeVision.calls if c[0] == "hit"]), 2)
        clock.t = 25.0
        obs.observe(None, frame())          # second read: same values -> not re-emitted
        clock.t = 50.0
        FakeVision.calls = []
        obs.observe(None, frame())          # read cap reached: no recognition at all
        self.assertEqual(FakeVision.calls, [])
        self.assertEqual(len(sink.drain()), 1)
        self.assertEqual(obs.summary(), {"attempts": 2, "reads": 2, "emitted": 1, "errors": 0,
                                         "disabled": "read_cap_reached", "refusals": {"unchanged": 1}})

    def test_reader_errors_and_slow_reads_never_propagate_and_disable_the_observer(self):
        clock = Clock()
        obs, sink = observer(FakeVision(error=RuntimeError("boom")), clock, min_interval_s=0.0, max_errors=2)
        for _ in range(4):
            self.assertIsNone(obs.observe(None, frame()))
        self.assertEqual((obs.errors, obs.disabled, sink.drain()), (2, "reader_errors", []))

        def slow():
            clock.t += 1.0
        obs, sink = observer(FakeVision(on_ocr=slow), clock, min_interval_s=0.0, max_read_s=1.5)
        obs.observe(None, frame())
        self.assertEqual(obs.disabled, "read_too_slow")
        self.assertEqual(len(sink.drain()), 1)  # the slow read itself is still valid
        obs.observe(None, frame())
        self.assertEqual(sink.drain(), [])

    def test_a_concurrent_call_skips_instead_of_waiting(self):
        obs, sink = observer(FakeVision(), min_interval_s=0.0)
        obs.lock.acquire()
        try:
            self.assertIsNone(obs.observe(None, frame()))
        finally:
            obs.lock.release()
        self.assertEqual((obs.attempts, sink.drain()), (0, []))

    def test_unknown_layout_and_options_are_rejected(self):
        obs, sink = observer(FakeVision(), min_interval_s=0.0)
        obs.observe(None, frame(), "item_list")
        self.assertEqual((obs.attempts, sink.drain()), (0, []))
        with self.assertRaises(ValueError):
            ro.ResourceObserver(None, max_clicks=1)


class ObserverContractTests(unittest.TestCase):
    """The hook is a never-hitting, disabled-by-default recognition node; it is not a click and holds no authority."""

    def test_node_is_disabled_custom_do_nothing_and_not_in_the_action_table(self):
        nodes = load_jsonc(PIPELINE / "resources.json")
        self.assertEqual(set(nodes), set(ro.NODES))
        node = nodes["SC.Resource.Observe.Home"]
        self.assertIs(node["enabled"], False)
        self.assertEqual((node["recognition"], node["custom_recognition"], node["action"]), ("Custom", ro.NAME, "DoNothing"))
        self.assertNotIn("next", node)
        self.assertNotIn("target", node)
        table = json.loads((ROOT / "assets/input/pipeline_actions.json").read_text(encoding="utf-8"))
        self.assertFalse([a for a in table["actions"] if set(a["nodes"]) & set(ro.NODES)])
        policy = load_jsonc(ROOT / "project_policy.json")
        self.assertNotIn("SC.Resource", json.dumps(policy))

    def test_hook_precedes_the_home_handler_of_the_opus_daily_tasks_only(self):
        users = {}
        for path in PIPELINE.rglob("*.json"):
            for name, node in load_jsonc(path).items():
                if isinstance(node, dict) and any(str(n).endswith("SC.Resource.Observe.Home") for n in node.get("next", []) or []):
                    users[name] = node["next"]
        self.assertEqual(set(users), {"SC.Shop.Main", "SC.Training.Main", "SC.Grade.Main", "SC.Business.Main"})
        for name, entries in users.items():
            # Plain entry (never [JumpBack]) right before the task's home handler: it sees the clean-home frames that
            # the earlier recovery/halt/pop-up nodes did not claim, and leaves the head-of-list contracts unchanged.
            task = name.split(".")[1]
            index = entries.index("SC.Resource.Observe.Home")
            self.assertEqual(entries[index + 1], f"[JumpBack]SC.{task}.AtHome", name)
            self.assertEqual(entries.count("SC.Resource.Observe.Home"), 1, name)
            self.assertNotEqual(index, 0, name)
        for path in (ROOT / "assets/tasks").glob("*.json"):
            self.assertNotIn("SC.Resource.Observe", path.read_text(encoding="utf-8"))   # 只读取资源 is its own task

    def test_module_has_no_input_capture_or_pipeline_mutation_besides_enabling_its_node(self):
        source = (ROOT / "agent/core/resource_observer.py").read_text(encoding="utf-8")
        for forbidden in ("post_click", "post_screencap", "cached_image", "run_task", "run_action", "override_next",
                          "webview_controller", "SpendGate", "authorize", "allow_spend"):
            self.assertNotIn(forbidden, source, forbidden)
        self.assertEqual(source.count("override_pipeline("), 1)


def _maa_ready(*frames):
    try:
        from tests.test_task_launcher import private_ready
    except Exception:
        return False
    return private_ready() and all((ROOT / f).is_file() for f in frames)


@unittest.skipUnless(_maa_ready(HOME_NOBADGE, HOME_SHOP, HOME_DIMMED, FES_MAIN),
                     "需要 MaaFramework v5.12.3、私有模板包、OCR 模型与原生首页帧")
class ObserverNativeFrameTests(unittest.TestCase):
    """Real Maa recognition on native frames through a tiny Pipeline whose next list starts with the hook."""

    @classmethod
    def setUpClass(cls):
        from maa.resource import Resource

        from agent.core.resource_bundles import bundle_paths, ocr_model_dir

        cls.sink = ReportSink()
        cls.resource = Resource()
        for bundle in bundle_paths():
            assert cls.resource.post_bundle(bundle).wait().succeeded
        model = ocr_model_dir()
        if model is not None:
            assert cls.resource.post_ocr_model(model).wait().succeeded
        cls.observer = ro.install(cls.resource, cls.sink, store=None, min_interval_s=0.0, refresh_s=0.0)

    def read(self, path):
        hooked = self.run_once(path, ["SC.Resource.Observe.Home", "SC.Test.Done"])
        reports = self.sink.drain()
        plain = self.run_once(path, ["SC.Test.Done"])
        self.assertEqual(self.sink.drain(), [])
        self.assertEqual(hooked, plain)  # no capture beyond the polling batch the task makes anyway
        return reports

    def run_once(self, path, next_list):
        from maa.tasker import Tasker

        from tools import run_tasks as launcher

        images, advances = launcher.load_frames([(ROOT / path, 1)])
        scenario = launcher.ReplayScenario(images, advances, allow_input=False)
        controller = launcher.create_replay_controller(scenario)
        self.assertTrue(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded)
        tasker = Tasker()
        self.assertTrue(tasker.bind(self.resource, controller))
        job = tasker.post_task("SC.Test.Observe", {
            "SC.Test.Observe": {"recognition": "DirectHit", "next": next_list},
            "SC.Test.Done": {"recognition": "DirectHit"}}).wait()
        self.assertTrue(job.succeeded)
        detail = tasker.get_task_detail(job.job_id)
        names = [n.name for n in detail.nodes if n is not None]
        self.assertNotIn("SC.Resource.Observe.Home", names)  # the hook never becomes the current node
        self.assertEqual((scenario.clicks, scenario.other_calls), ([], 0))
        return scenario.captures

    def test_native_home_counters(self):
        report, = self.read(HOME_NOBADGE)
        self.assertEqual(report["items"], {
            "money": {"count": 17678700, "confidence_pm": 1000},
            "jewel": {"count": 48157, "confidence_pm": 1000, "indicator_only": True},
            "stamina": {"count": 300, "max": 300, "confidence_pm": 1000},
            "fes": {"count": 20, "max": 20, "plus": 0, "confidence_pm": 1000}})
        self.assertEqual((report["gacha"]["state"], report["gacha"]["kind"]), ("free", "free_daily"))   # 「1日1回無料」

    def test_native_misread_is_refused_not_guessed(self):
        report, = self.read(HOME_SHOP)   # Fes ROI reads "17/20D+0" on this frame: refused, other counters kept
        self.assertEqual(report["items"]["money"]["count"], 17678492)
        self.assertNotIn("fes", report["items"])
        self.assertIn("fes", report["rejected"])
        self.assertEqual(report["gacha"]["state"], "no_tag")      # plain ガシャ plate

    def test_dimmed_home_and_other_pages_emit_nothing(self):
        self.assertEqual(self.read(HOME_DIMMED), [])
        self.assertEqual(self.read(FES_MAIN), [])


def _shop_live_ready():
    try:
        from tests import test_shop_task as shop_tests
    except Exception:
        return False
    return _maa_ready() and shop_tests.HAVE_MAA and shop_tests.frames_present() and shop_tests.replay_ready()


@unittest.skipUnless(_shop_live_ready(), "需要 MaaFramework v5.12.3、私有模板包、OCR 模型与 2026-10-02 商店实机帧")
class ObserverFrameGuardTests(unittest.TestCase):
    """With the hook registered and enabled (the wiring in build_tasker), the frame-guarded --live
    Shop chain against a fake host sends exactly the same actions and grants as without it, and emits readings."""

    def test_shop_live_chain_is_unchanged_and_home_counters_are_reported(self):
        from agent.core import webview_controller as wc
        from tests import test_shop_task as s
        from tests.test_task_launcher import RISK_POLICY, TABLE, FakePipelineHost
        from tools import run_tasks as launcher

        root = ROOT / P / "run_logs" / "_unittest_observer_shop_live"
        shutil.rmtree(root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, root, True)
        steps = [(f, "click") for f in (s.HOME, s.PREMIUM, s.LIST, s.CONFIRM, s.RESULT, s.AFTER_RANK1, s.MISSION_POPUP, s.HOME)]
        host = FakePipelineHost(steps)
        session = {"version": 2, "frame_binding_protocol": wc.PIPELINE_FRAME_BINDING_PROTOCOL, "host": "127.0.0.1",
                   "port": 50009, "token": "a" * 64, "pid": host.PID, "input_enabled": True, "browser_demo_only": False,
                   "transport": wc.PIPELINE_TRANSPORT, "actions": TABLE.ids, "consuming_actions": TABLE.consuming_ids,
                   "action_table_sha256": TABLE.sha256, "spend_secret": "07" * 32, "audit_root": str(ROOT / P / "run_logs"),
                   "limits": {"min_interval_ms": 800, "interval_jitter_max_ms": 600, "per_action_per_minute": 12,
                              "run_max": 600, "run_spend_max": 8, "grant_max_ttl_ms": 5000, "session_max_ms": 3_600_000}}
        gate = wc.WebViewInputGate(host, wc.RunConfig(True, frozenset(TABLE.families)),
                                   {"pipeline": wc.Endpoint("pipeline", session)},
                                   wc.SpendGate(s.SPEND, max_per_action=3), wc.AuditLog(root), host.send, policy=RISK_POLICY)
        observers = []
        original_install = ro.install

        def install(resource, sink):
            observers.append(original_install(resource, sink, store=None, min_interval_s=0.0))
            return observers[-1]

        with mock.patch.object(ro, "install", install):
            summary = launcher.run({"tasks": [{"name": "Shop", "option": s.ONLY_SKIP}], "allow_spend": s.SPEND},
                                   live=True, gate=gate, policy=RISK_POLICY, summary_path=False, save_diagnostics=False,
                                   shop_options={"log_root": root, "now": lambda: s.NOW}, frame_guard_enabled=True)
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual([r["action"] for r in host.requests],
                         ["home-shop", "shop-money-tab", "shop-offer-open-1", "shop-exchange-confirm",
                          "shop-result-close", "nav-back", "popup-mission-completed-ok"])
        spend, = [r for r in host.requests if "spend_grant" in r]
        self.assertEqual(spend["frame_sha256"], hashlib.sha256((ROOT / s.CONFIRM).read_bytes()).hexdigest())
        self.assertGreaterEqual(observers[-1].reads, 1)
        resources = [r for r in task["reports"] if r["kind"] == "resources"]
        self.assertTrue(resources, task["reports"])
        self.assertEqual(resources[0]["items"]["money"]["count"], 17678492)
        self.assertEqual(observers[-1].errors, 0)
        self.assertEqual(summary["resource_observer"], observers[-1].summary())
        resource_readings = [row for row in summary["readings"] if row["kind"] == "resources"]
        self.assertEqual(resource_readings[0]["readings"]["items"]["money"]["count"], 17678492)


if __name__ == "__main__":
    unittest.main()
