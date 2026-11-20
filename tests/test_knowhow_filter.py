"""絞り込み popup calibration (read-only): session rules, Pipeline/table binding and the supervised launcher path."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from agent.core import knowhow_books as kb
from agent.core import knowhow_filter as kf
from agent.core import knowhow_scan as ks
from tests import knowhow_filter_frames as ff

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "assets" / "resource" / "base" / "pipeline" / "knowhow_scan.json"


class Frame(np.ndarray):
    texts: list = []


def frame(image, texts) -> Frame:
    out = np.asarray(image).view(Frame)
    out.texts = list(texts)
    return out


def popup_texts(count, sections=()):
    return [("絞り込み", (190, 42, 119, 37), 1.0), ("該当するノウハウブック", (836, 46, 185, 26), 1.0),
            (str(count), (1060, 43, 54, 29), 1.0), ("キャンセル", (394, 607, 135, 33), 1.0),
            ("リセット", (580, 606, 120, 38), 1.0), ("決定", (780, 600, 76, 45), 1.0)] + list(sections)


def list_texts(owned=498, filter_word="絞り込み"):
    return [("ノウハウブック一覧", (49, 18, 230, 34), 1.0), ("デフォルト", (990, 110, 110, 30), 1.0),
            ("▼降順", (1149, 104, 79, 33), 1.0), (filter_word, (818, 107, 100, 29), 1.0),
            (f"{owned}/600", (1156, 660, 89, 24), 1.0)]


def run(session, frames, limit=200):
    out = []
    for f in frames:
        while True:
            d = session.observe(f)
            out.append(d)
            if d.kind != "hold" or len(out) > limit:
                break
        if d.kind in ("stop", "leave"):
            break
    return out


@unittest.skipUnless(ff.ready(), "需要 2026-10-12 ノウハウ 原生采样帧（knowhow-sample-20261012）")
class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.list_image = ff.load(1)
        cls.base = ff.load(131)
        cls.strip = ff.strip()

    def setUp(self):
        guards = mock.patch.object(kb, "_list_guards", return_value={"sale_pink": 0.0, "protect_pink": 0.0,
                                                                    "toggles_off": True, "separator_pink": 0.0})
        guards.start()
        self.addCleanup(guards.stop)

    def popup(self, offset, count=498):
        return frame(ff.popup_window(self.base, self.strip, offset), popup_texts(count))

    def session(self, **options):
        return kf.FilterCalibration(kf.FilterCalibrationOptions(**options), lambda image: image.texts)

    def test_full_calibration_measures_every_step_then_cancels(self):
        offsets = ff.offsets()
        s = self.session(up_wheels=2)
        frames = ([frame(self.list_image, list_texts())] + [self.popup(o) for o in offsets]
                  + [self.popup(offsets[-2]), self.popup(offsets[-3])] + [frame(self.list_image, list_texts())])
        # after each wheel the frame is captured twice (settle check); the bottom is the thumb at the track end
        decisions = run(s, frames)
        inputs = [d.kind + (f":{d.direction}" if d.direction else "") for d in decisions if d.is_input]
        down = len(offsets) - 1
        self.assertEqual(inputs, ["open_filter"] + ["scroll_filter:down"] * down + ["scroll_filter:up"] * 2
                         + ["cancel_filter", "leave"], [d.to_dict() for d in decisions][-6:])
        result = s.result()
        self.assertFalse(result["partial"], result["outcome"])
        steps = [o2 - o1 for o1, o2 in zip(offsets, offsets[1:])]
        self.assertEqual(result["down_step_px"], steps)
        self.assertEqual(result["up_step_px"], [-(offsets[-1] - offsets[-2]), -(offsets[-2] - offsets[-3])])
        self.assertEqual((result["bottom"]["down_wheels"], result["bottom"]["by"]), (down, "thumb"))
        self.assertEqual([st["state"] for st in result["states"]][:2], ["top", "top+1"])
        self.assertEqual(result["after_cancel"]["filter_active"], False)

    def test_refuses_an_active_filter_or_preset_conditions(self):
        s = self.session()
        self.assertEqual(run(s, [frame(self.list_image, list_texts(filter_word="絞り込みON"))])[-1].reason,
                         "filter_already_active")
        s = self.session()
        out = run(s, [frame(self.list_image, list_texts()), self.popup(0, count=195)])
        self.assertEqual((out[-1].kind, out[-1].reason), ("stop", "filter_condition_preset"))

    def test_other_popup_or_count_change_stops_without_input(self):
        s = self.session()
        sub = frame(self.base, popup_texts(498) + [("絞り込みノウハウ選択", (360, 60, 330, 36), 1.0)])
        sub.texts = [t for t in sub.texts if t[0] != "該当するノウハウブック"]
        out = run(s, [frame(self.list_image, list_texts()), sub])
        self.assertEqual((out[-1].kind, out[-1].reason), ("stop", "filter_popup_missing"))
        s = self.session()
        out = run(s, [frame(self.list_image, list_texts()), self.popup(0), self.popup(160, count=403)])
        self.assertEqual((out[-1].kind, out[-1].reason), ("stop", "filter_count_changed"))
        self.assertEqual([d.kind for d in out if d.is_input], ["open_filter", "scroll_filter"])

    def test_cancel_must_return_to_the_unchanged_list(self):
        s = self.session(max_down_wheels=0, up_wheels=0)
        out = run(s, [frame(self.list_image, list_texts()), self.popup(0),
                      frame(self.list_image, list_texts(filter_word="絞り込みON"))])
        self.assertEqual((out[-1].kind, out[-1].reason), ("stop", "filter_cancel_changed_list"))


class OptionTests(unittest.TestCase):
    def test_bounds_and_cap(self):
        o = kf.FilterCalibrationOptions()
        self.assertEqual(o.max_inputs(), 3 + 1 + 30 + 3 + 1 + 3)
        for bad in ({"max_down_wheels": 61}, {"up_wheels": -1}, {"bogus": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                kf.FilterCalibrationOptions.from_params(bad)

    def test_launcher_mode(self):
        from tools import run_tasks as launcher

        o = launcher.knowhow_scan_options({"mode": "filter_calibrate", "filter_calibration": {"up_wheels": 2}})
        self.assertEqual((o["mode"], o["cap"]), ("filter_calibrate", 3 + 1 + 30 + 2 + 1 + 3))
        self.assertEqual(o["attach"]["filter_calibration"]["up_wheels"], 2)


class BindingTests(unittest.TestCase):
    def test_filter_nodes_bind_only_open_wheel_and_cancel(self):
        nodes = json.loads(PIPELINE.read_text(encoding="utf-8"))
        actions = {a["id"]: a for a in ks.ACTIONS if a["id"].startswith("knowhow-filter-")}
        self.assertEqual(set(actions), {"knowhow-filter-open", "knowhow-filter-scroll-down", "knowhow-filter-scroll-up",
                                        "knowhow-filter-cancel"})
        for aid, action in actions.items():
            node, = action["nodes"]
            self.assertEqual(nodes[node]["target"], action["rect"], aid)
            self.assertIs(nodes[node]["enabled"], False)
            # Filter.Open sits after List.Hold: it peeks the cached decision first, then the list guard (round audit)
            guard = nodes[node]["all_of"][1] if aid == "knowhow-filter-open" else nodes[node]["all_of"][0]
            self.assertEqual(guard, "SC.KnowhowScan.List.Guard" if aid == "knowhow-filter-open"
                             else "SC.KnowhowScan.Filter.Guard")
        # キャンセル never overlaps リセット / 決定; the wheel rect stays off every checkbox column and the scrollbar
        cx, cy, cw, ch = ks.FILTER_CANCEL
        self.assertLess(cx + cw, 558)
        wx, wy, ww, wh = ks.FILTER_POPUP_WHEEL
        self.assertTrue(wx > 1000 and wx + ww < 1110)
        main = nodes["SC.KnowhowScan.Main"]["next"]
        for name in ("Filter.Open", "Filter.ScrollDown", "Filter.ScrollUp", "Filter.Cancel", "Filter.Hold"):
            self.assertIn(f"[JumpBack]SC.KnowhowScan.{name}", main)
        for d in (ks.Decision("open_filter", "x"), ks.Decision("scroll_filter", "x", direction="down"),
                  ks.Decision("scroll_filter", "x", direction="up"), ks.Decision("cancel_filter", "x")):
            self.assertIn(ks.pipeline_node(d), nodes)


def _maa_ready():
    try:
        from tests.test_task_launcher import private_ready
        from tests import test_card_scan_native_20261007 as N
    except Exception:
        return False
    return ff.ready() and private_ready() and N._present_1008()


@unittest.skipUnless(_maa_ready(), "需要 MaaFramework、私有模板包、2026-10-08 导航帧与 2026-10-12 ノウハウ 采样帧")
class FilterCalibrationLivePathTests(unittest.TestCase):
    """Offline replay through the real --live --supervised-task path (WebView2 gate + fake host): the only inputs are
    the navigation, 絞り込み open, popup wheels and キャンセル; no checkbox / リセット / 決定 is ever bound."""

    def test_filter_calibration_sends_only_open_wheels_cancel_and_back(self):
        import tempfile

        from PIL import Image

        from agent.core import webview_controller as wc
        from tests import test_card_scan_native_20261007 as N
        from tests.test_knowhow_calibrate import _maa_ocr, switched
        from tests.test_task_launcher import RISK_POLICY, FakePipelineHost
        from tools import run_tasks as launcher

        owned = kb.classify_knowhow_screen(_maa_ocr(ff.load(1)), ff.load(1))["owned"]
        self.assertIsNotNone(owned)
        with tempfile.TemporaryDirectory(dir=launcher.PRIVATE, prefix="knowhow-filter-live-") as directory:
            directory = Path(directory)
            base, strip = ff.load(131), ff.strip()
            offsets = ff.offsets()
            popups = []
            for i, o in enumerate(offsets + [offsets[-2]]):
                path = directory / f"popup-{i}.png"
                Image.fromarray(ff.popup_window(base, strip, o)).save(path)
                popups.append(path.relative_to(ROOT))
            listing = ff.path(1).relative_to(ROOT)
            frames = [N.HOMES[0], N.PORTAL_AFTER, N.LIST_FES, listing] + popups + [listing, N.LIST_FES,
                                                                                    N.PORTAL_AFTER, N.HOMES[1]]
            option = {"mode": "filter_calibrate", "filter_calibration": {"up_wheels": 1}}
            cap = launcher.knowhow_scan_options(option)["cap"]
            config = {"preset": None, "tasks": [{"name": "KnowhowScan", "option": option}], "max_clicks": cap,
                      "task_max_clicks": {"KnowhowScan": cap}, "allow_spend": []}
            real_popup = kb.parse_filter_popup
            fixed = lambda texts: {**real_popup(texts), "count": owned} if real_popup(texts)["open"] else real_popup(texts)  # noqa: E731
            with switched(directory) as table, mock.patch.object(kb, "parse_filter_popup", fixed):
                host = FakePipelineHost([(frame, "click") for frame in frames])
                session = {"version": 2, "frame_binding_protocol": wc.PIPELINE_FRAME_BINDING_PROTOCOL,
                           "host": "127.0.0.1", "port": 50009, "token": "a" * 64, "pid": host.PID,
                           "input_enabled": True, "browser_demo_only": False, "transport": wc.PIPELINE_TRANSPORT,
                           "actions": table.ids, "consuming_actions": table.consuming_ids,
                           "action_table_sha256": table.sha256, "spend_secret": "07" * 32,
                           "audit_root": str(launcher.PRIVATE / "run_logs"),
                           "limits": {"min_interval_ms": 800, "interval_jitter_max_ms": 600, "per_action_per_minute": 12,
                                      "run_max": 600, "run_spend_max": 8, "grant_max_ttl_ms": 5000,
                                      "session_max_ms": 3_600_000}}
                gate = wc.WebViewInputGate(host, wc.RunConfig(True, frozenset(launcher.SUPERVISED_TASK_FAMILIES["KnowhowScan"])),
                                           {"pipeline": wc.Endpoint("pipeline", session)}, wc.SpendGate(),
                                           wc.AuditLog(directory / "audit"), host.send, policy=RISK_POLICY,
                                           limits=wc.ClickLimits(run_max=cap, task_max=cap))
                summary = launcher.run(copy.deepcopy(config), live=True, supervised_task=True, gate=gate,
                                       policy=RISK_POLICY, summary_path=False, save_diagnostics=False,
                                       task_options={"knowhow_scan": {"run_dir": directory / "knowhow-scan",
                                                                      "inventory_path": directory / "inv.json",
                                                                      "ui_path": directory / "knowhow.js"}})
            actions = [row["action"] for row in host.requests]
            down = len(offsets) - 1
            self.assertEqual(actions, ["home-idol", "idol-portal-list", "idol-list-tab-knowhow", "knowhow-filter-open"]
                             + ["knowhow-filter-scroll-down"] * down + ["knowhow-filter-scroll-up",
                                "knowhow-filter-cancel", "nav-back", "nav-back", "nav-back"],
                             json.dumps([{k: v for k, v in t.items() if k != "reports"} for t in summary.get("tasks", [])],
                                        ensure_ascii=False, default=str)[:2000])
            task, = summary["tasks"]
            report = next(r for r in task["reports"] if r["kind"] == "knowhow_scan")
            self.assertEqual((report["outcome"], report["mode"]), ("filter_calibrated", "filter_calibrate"))
            self.assertEqual(report["bottom"]["down_wheels"], down)
            self.assertEqual(task["status"], "succeeded")
            self.assertFalse((directory / "inv.json").exists())


if __name__ == "__main__":
    unittest.main()
