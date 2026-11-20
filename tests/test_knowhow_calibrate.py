"""KnowhowScan calibration mode, the staged action-table binding and the supervised-only launcher entry.

Offline only (no host, no game input). Native replays use the private 2026-10-08 ノウハウ frames
(tests/knowhow_calibrate_frames.py); the bound table/Pipeline come from tools/knowhow_scan_build.py and are patched in
for the launcher tests, because the repository table is switched only after the running live session
.
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from agent.core import knowhow_books as kb
from agent.core import knowhow_calibrate as kc
from agent.core import knowhow_scan as ks
from agent.core import pipeline_actions as pa
from tests import knowhow_calibrate_frames as cf
from tools import knowhow_scan_build as builder
from tools import run_tasks as launcher

ROOT = Path(__file__).resolve().parents[1]
HAVE_MAA = importlib.util.find_spec("maa") is not None
CALIBRATE_1 = {"books": 1, "detail_wheels": 10, "grid_wheels": 2, "grid_seek_bottom": False, "grid_up_wheels": 1}
PROBE = {"books": 0, "grid_wheels": 0, "grid_seek_bottom": False, "grid_up_wheels": 0, "grid_jumps": 3,
         "track_taps": True, "max_holds": 6}


def bound():
    """(table, table_text, pipeline_nodes) of the staged binding, built from the repository files."""
    table_text, pipeline_text = builder.build()
    return pa.parse_table(table_text.encode("utf-8")), table_text, json.loads(pipeline_text)


@contextlib.contextmanager
def switched(tmp: Path):
    """The bound table + knowhow_scan.json in place of the repository ones (temp copy of the base bundle)."""
    from agent.core import resource_bundles
    from agent.core import webview_controller as wc

    table, _, nodes = bound()
    base = tmp / "base"
    shutil.copytree(ROOT / "assets" / "resource" / "base", base)
    (base / "pipeline" / "knowhow_scan.json").write_text(json.dumps(nodes, ensure_ascii=False, indent=4), encoding="utf-8")
    bundles = [base] + [p for p in resource_bundles.bundle_paths() if p.name != "base"]
    with mock.patch.object(pa, "_DEFAULT", table), \
            mock.patch.object(wc, "PIPELINE_FAMILIES", frozenset(table.families)), \
            mock.patch.object(wc, "FAMILIES", wc.FAMILIES | frozenset(table.families)), \
            mock.patch.object(launcher, "PIPELINE_DIR", base / "pipeline"), \
            mock.patch.object(launcher.click_node_actions, "__defaults__", (base / "pipeline", None)), \
            mock.patch.object(resource_bundles, "bundle_paths", lambda: bundles):
        yield table


def scoped(value):
    return launcher.supervised_task_config(value, live=True, offline=False, simulate_input=False)


def calib_config(option=None, clicks=None):
    option = {"mode": "calibrate", "calibration": dict(CALIBRATE_1)} if option is None else option
    cap = clicks or launcher.knowhow_scan_options(option)["cap"]
    return {"preset": None, "tasks": [{"name": "KnowhowScan", "option": option}], "max_clicks": cap,
            "task_max_clicks": {"KnowhowScan": cap}, "allow_spend": []}


# ---- pixel shift -------------------------------------------------------------------------------------------------
class ShiftTests(unittest.TestCase):
    def test_exact_shift_on_a_scrolled_strip(self):
        rng = np.random.default_rng(3)
        strip = rng.integers(0, 255, size=(1400, 1280, 3), dtype=np.uint8)
        x, y, w, h = kc.GRID_ROI
        base = np.zeros((720, 1280, 3), dtype=np.uint8)

        def window(offset):
            out = base.copy()
            out[y:y + h, x:x + w] = strip[offset:offset + h, x:x + w]
            return out

        for dy in (0, 37, 60, 157, 158, 315, -120):
            with self.subTest(dy=dy):
                r = kc.vertical_shift(window(400), window(400 + dy), kc.GRID_ROI)
                self.assertEqual(r["dy"], dy)
                self.assertEqual(r["moved"], dy != 0)

    def test_row_overlap(self):
        rows = [{"name": n, "level": lv} for n, lv in (("A", 1), ("B", 5), ("C", 4))]
        self.assertEqual(kc.row_overlap(rows, rows[1:] + [{"name": "D", "level": 2}]), 2)
        self.assertEqual(kc.row_overlap(rows, [{"name": "D", "level": 2}]), 0)
        self.assertEqual(kc.row_overlap(rows, [{"name": "C", "level": 5}]), 0)     # same name, other Lv


class OptionTests(unittest.TestCase):
    def test_defaults_bounds_and_click_cap(self):
        o = kc.CalibrationOptions.from_params(None)
        self.assertEqual((o.books, o.detail_wheels, o.grid_wheels, o.max_grid_wheels), (2, 10, 4, 120))
        self.assertEqual(o.max_inputs(), 3 + 2 * 11 + 120 + 2 + 3)
        self.assertEqual(kc.CalibrationOptions.from_params(CALIBRATE_1).max_inputs(), 20)
        for bad in ({"books": 6}, {"grid_seek_bottom": 1}, {"bogus": 1}, {"grid_wheels": 5, "max_grid_wheels": 4},
                    {"detail_wheels": -1}, {"track_taps": 1}, {"grid_jumps": 6}):
            with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                kc.CalibrationOptions.from_params(bad)
        # Jump probe: nav 3 + 3 jumps down/up + 2 track taps + exit 3, no book, no -60 wheel.
        probe = kc.CalibrationOptions.from_params(PROBE)
        self.assertEqual((probe.grid_jumps, probe.track_taps, probe.max_inputs()), (3, True, 14))
        self.assertFalse(o.track_taps or o.grid_jumps)


# ---- native sequence -----------------------------------------------------------------------------------------------
@unittest.skipUnless(cf.ready(), "需要 2026-10-08 know-how 私有原生帧、ppocr 模型、MaaFramework")
class NativeCalibrationTests(unittest.TestCase):
    _frames: dict = {}

    @classmethod
    def frame(cls, name):
        from tests import knowhow_scan_frames as kf
        from tests.maa_probe import ocr_items

        if name not in cls._frames:
            cls._frames[name] = kf.frame(cf.load(name), ocr_items(cf.D / f"{name}.png"))
        return cls._frames[name]

    @classmethod
    def sequence(cls, offsets=(158, 316, 158)):
        from tests import knowhow_scan_frames as kf

        details = [cls.frame(n) for n in cf.DETAIL]
        strip = cf.grid_strip()
        grid = [kf.frame(cf.grid_window(details[-1], strip, o), details[-1].texts) for o in offsets]
        return [cls.frame(cf.GRID_START), *details, *grid]

    def session(self, **options):
        from tests import knowhow_scan_frames as kf

        return kc.CalibrationSession(kc.CalibrationOptions.from_params({**CALIBRATE_1, **options}), kf.read_texts)

    def test_measures_detail_and_grid_steps_without_sort_input(self):
        s = self.session()
        kinds = [s.observe(f).kind for f in self.sequence()]
        self.assertEqual(kinds, ["open_tile"] + ["scroll_detail"] * 10 + ["scroll_grid"] * 3 + ["leave"])
        self.assertFalse({"open_sort", "pick_sort", "apply_sort"} & set(kinds))
        r = s.result()
        self.assertEqual((r["outcome"], r["partial"], r["scan_ready"]), ("calibrated", False, True))
        self.assertEqual((r["enter"]["sort"], r["enter"]["owned"]), (kb.SORT_EVAL, 514))
        book, = r["detail"]["books"]
        self.assertEqual((book["title"], book["knowhow_count"], book["starts_at_top"], book["end"]),
                         ("【オイサラバエル】樋口円香", 21, True, "scrollbar_at_end"))
        self.assertEqual((book["rows_merged"], book["merge_complete"]), (21, True))
        self.assertEqual([st["dy"] for st in book["steps"]], [202] + [201] * 8 + [52])   # scrollbar drags, not wheels
        self.assertEqual(r["detail"]["min_row_overlap"], 1)
        g = r["grid"]
        self.assertEqual((g["step_px"]["median"], g["step_rows"], g["whole_rows"], g["up_step_px"]), (158, 1.003, True, [-158]))
        self.assertEqual(g["bottom"]["reached"], False)

    def test_grid_bottom_is_the_first_step_without_movement(self):
        s = self.session(grid_wheels=2, grid_seek_bottom=True, max_grid_wheels=6, grid_up_wheels=0)
        decisions = [s.observe(f) for f in self.sequence((158, 316, 316))]
        self.assertEqual(decisions[-1].kind, "leave")
        g = s.result()["grid"]
        self.assertEqual(g["bottom"]["reached"], True)
        self.assertEqual(g["bottom"]["wheels"], 3)
        self.assertEqual([st["moved"] for st in g["steps"]], [True, True, False])

    def test_jump_probe_records_each_step_after_two_identical_captures(self):
        from tests import knowhow_scan_frames as kf

        last = self.frame(cf.DETAIL[-1])
        strip = cf.grid_strip()
        a, b, c = (kf.frame(cf.grid_window(last, strip, o), last.texts) for o in (158, 316, 474))
        s = self.session(**{**PROBE, "grid_jumps": 2})
        decisions = [s.observe(f) for f in (self.frame(cf.GRID_START), a, a, b, b, c, c, b, b, a, a)]
        self.assertEqual([(d.kind, d.direction or d.key) for d in decisions if d.kind != "hold"],
                         [("jump_grid", "down"), ("jump_grid", "down"), ("tap_track", "lower"), ("jump_grid", "up"),
                          ("jump_grid", "up"), ("leave", None)])
        self.assertEqual({d.reason for d in decisions if d.kind == "hold"}, {"grid_settling"})
        self.assertFalse({"scroll_grid", "open_tile", "open_sort"} & {d.kind for d in decisions})
        self.assertEqual([ks.pipeline_node(d) for d in decisions[:3:2]],
                         ["SC.KnowhowScan.Grid.JumpDown", "SC.KnowhowScan.Grid.JumpDown"])
        probe = s.result()["probe"]
        steps = probe["steps"]
        self.assertEqual([st["step"] for st in steps], ["down", "down", "upper", "lower", "up", "up"])
        self.assertEqual(steps[2]["skipped"], "thumb_not_clear")          # the thumb never left the top here
        self.assertEqual([st["shift"]["dy"] for st in steps if "shift" in st][1:], [158, 158, -158, -158])
        self.assertEqual(set(probe["track"]), {"upper", "lower"})
        self.assertEqual(s.inputs, 6)

    def test_jump_probe_stops_on_a_settle_budget(self):
        from tests import knowhow_scan_frames as kf

        last = self.frame(cf.DETAIL[-1])
        strip = cf.grid_strip()
        frames = [kf.frame(cf.grid_window(last, strip, o), last.texts) for o in (158, 200, 240, 280, 320)]
        s = self.session(**{**PROBE, "max_holds": 3})
        decisions = [s.observe(f) for f in [self.frame(cf.GRID_START), *frames]]
        self.assertEqual((decisions[-1].kind, decisions[-1].reason), ("stop", "hold_budget_exhausted"))
        self.assertEqual(s.inputs, 1)

    def test_popups_modes_and_sort_changes_stop_without_input(self):
        for name in ("knowhow_sort_options", "knowhow_protect_mode", "knowhow_sale_mode", "knowhow_mark_popup"):
            with self.subTest(frame=name):
                s = self.session()
                self.assertEqual(s.observe(self.frame(name)).kind, "stop")
                self.assertEqual(s.inputs, 0)
        s = self.session()
        s.observe(self.frame(cf.GRID_START))
        d = s.observe(self.frame("knowhow_default_grid"))     # デフォルト sort and no ✓: binding lost / sort changed
        self.assertEqual(d.kind, "stop")
        self.assertIn(d.reason, ("sort_changed", "detail_binding_lost"))


# ---- runtime (Maa glue without a controller) ------------------------------------------------------------------------
class _Sink:
    def __init__(self):
        self.items = []

    def add(self, kind, payload):
        self.items.append((kind, payload))


class _Context:
    def __init__(self, attach):
        self.attach = attach

    def get_node_data(self, name):
        return {"attach": self.attach} if name == "SC.KnowhowScan.Config" else None


@unittest.skipUnless(HAVE_MAA and cf.ready(), "需要 MaaFramework 与 2026-10-08 know-how 私有原生帧")
class CalibrationRuntimeTests(unittest.TestCase):
    def test_calibration_record_frames_and_no_inventory_write(self):
        from agent.core import knowhow_scan_actions as ksa

        base = ROOT / "dev_assets" / "private"
        tmp = Path(tempfile.mkdtemp(prefix="knowhow-calib-test-", dir=base))
        self.addCleanup(shutil.rmtree, tmp, True)
        sink = _Sink()
        rt = ksa.KnowhowScanRuntime(sink=sink, run_dir=tmp / "runs", inventory_path=tmp / "inv.json",
                                    ui_path=tmp / "knowhow.js")
        attach = launcher.knowhow_scan_options({"mode": "calibrate", "calibration": CALIBRATE_1})["attach"]
        self.assertTrue(rt.begin(_Context(attach), {"config_nodes": ["SC.KnowhowScan.Config"]}))
        self.assertIsInstance(rt.session, kc.CalibrationSession)
        from tests import knowhow_scan_frames as kf
        rt.session.read_texts = kf.read_texts
        wants = [{"want": "scroll_grid", "direction": "down"}, {"want": "scroll_grid", "direction": "up"},
                 {"want": "scroll_detail", "direction": "down"}, {"want": "leave"}, {"want": "hold"}]
        wants += [{"want": "open_tile", "row": r, "col": c} for r in range(ks.ROWS) for c in range(ks.COLS)]
        for f in NativeCalibrationTests.sequence():
            bgr = np.ascontiguousarray(np.asarray(f)[:, :, ::-1]).view(type(f))
            bgr.texts = f.texts
            hit = next((w for w in wants if rt.want(None, bgr, w)), None)
            self.assertIsNotNone(hit)
            if hit["want"] != "hold":
                self.assertTrue(rt.sent(None, {}))       # SC.KnowhowScan.Sent.*: the input was confirmed sent
        self.assertTrue(rt.want(None, bgr, {"is": "finished"}))
        self.assertTrue(rt.finish(None, {}))
        record, = (tmp / "runs").glob("knowhow-calibrate-*.json")
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertTrue(data["scan_ready"])
        frames = sorted(Path(data["frames_dir"]).glob("frame-*.png"))
        self.assertEqual(len(frames), 15)
        self.assertFalse((tmp / "inv.json").exists() or (tmp / "knowhow.js").exists())
        kind, report = sink.items[-1]
        self.assertEqual((kind, report["outcome"], report["mode"]), ("knowhow_scan", "calibrated", "calibrate"))
        self.assertEqual(report["grid_step_px"]["median"], 158)


# ---- 2026-10-12 live stall: a matched Want whose node did not run must not advance the session ------------------------
LIVE_1012 = ROOT / "dev_assets" / "private" / "knowhow-scan" / "calibrate-20261012T004241Z"
LIVE_1012_OPTION = {"mode": "calibrate", "calibration": {
    "books": 2, "detail_wheels": 10, "grid_wheels": 4, "grid_seek_bottom": True, "max_grid_wheels": 120,
    "grid_up_wheels": 2, "max_frames": 400, "max_holds": 3}}       # dev_assets/private/live-20261012-knowhow config


def _live_1012_ready():
    from tests.maa_probe import MODEL
    return HAVE_MAA and MODEL.is_dir() and all((LIVE_1012 / f"frame-{i:04d}.png").is_file() for i in range(1, 5))


def _maa_ocr(image_rgb):
    """Full-frame project Maa OCR of an RGB capture (same det+rec model as ContextOcr; no controller input)."""
    from tests import maa_probe
    from tools.replay_ocr import MaaOcrRecognizer

    if maa_probe._OCR is None:
        maa_probe._OCR = MaaOcrRecognizer(maa_probe.MODEL)
    bgr = np.ascontiguousarray(image_rgb[:, :, ::-1])
    result = maa_probe._OCR.recognize({"name": "SC.Test.OCR", "roi": [0, 0, 1280, 720], "expected": "^.*$",
                                       "threshold": 0.0}, bgr)
    assert maa_probe._OCR.input_attempts == 0
    return [(i["text"], tuple(int(v) for v in i["box"]), float(i["score"])) for i in result["results"] if i["text"]]


@unittest.skipUnless(_live_1012_ready(), "需要 2026-10-12 实机校准私有帧（calibrate-20261012T004241Z）、ppocr 模型与 MaaFramework")
class LiveStall20261012Tests(unittest.TestCase):
    """frame-0001 = grid before the r0c0 click; frame-0002..0004 = r0c0 detail open, pixel-identical (no wheel was sent).

    Live: Want.DetailScrollDown matched on frame 2, but the launcher frame guard refused the stale batch
    (``frame_stale_before_permit``: the first, slow observation outlived MAX_AGE_S), so Detail.ScrollDown never ran. The
    old runtime cleared its cache on the match, so the next Want in the same next-list re-observed the same pixels as a
    new frame (dy=0 -> content_stopped -> open r0c1 -> detail_binding_lost)."""

    @classmethod
    def setUpClass(cls):
        from tests.maa_probe import rgb
        cls.frames = [rgb(LIVE_1012 / f"frame-{i:04d}.png") for i in range(1, 5)]
        nodes = json.loads((ROOT / "assets" / "resource" / "base" / "pipeline" / "knowhow_scan.json")
                           .read_text(encoding="utf-8"))
        cls.nodes = nodes
        # the Want params in SC.KnowhowScan.Main next-list order (what Maa evaluates on one capture)
        cls.wants = []
        for entry in nodes["SC.KnowhowScan.Main"]["next"]:
            node = nodes.get(entry.replace("[JumpBack]", ""), {})
            for part in node.get("all_of", []):
                if str(part).startswith("SC.KnowhowScan.Want."):
                    cls.wants.append((entry.replace("[JumpBack]", ""), nodes[part]["custom_recognition_param"]))

    def runtime(self):
        from agent.core import knowhow_scan_actions as ksa

        tmp = Path(tempfile.mkdtemp(prefix="knowhow-stall-test-", dir=ROOT / "dev_assets" / "private"))
        self.addCleanup(shutil.rmtree, tmp, True)
        rt = ksa.KnowhowScanRuntime(sink=_Sink(), run_dir=tmp / "runs", inventory_path=tmp / "inv.json",
                                    ui_path=tmp / "knowhow.js")
        attach = launcher.knowhow_scan_options(LIVE_1012_OPTION)["attach"]
        self.assertTrue(rt.begin(_Context(attach), {"config_nodes": ["SC.KnowhowScan.Config"]}))
        rt.session.read_texts = _maa_ocr
        return rt

    def poll(self, rt, rgb, runs):
        """One Main next-list round on one capture. ``runs``: whether the matched input node's action really runs
        (False = the frame guard refused it, so Maa moves on to the next entry). Returns the node that ran."""
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        for node, want in self.wants:
            if rt.want(None, bgr, want):
                if want.get("want") == "hold":
                    return node
                if runs:
                    self.assertTrue(rt.sent(None, {}))       # SC.KnowhowScan.Sent.*: the controller confirmed it
                    return node
        return None

    def test_guard_and_wheel_binding_hold_on_the_live_frames(self):
        from tests.maa_probe import NodeProbe

        probe = NodeProbe()
        for i in range(1, 5):
            self.assertTrue(probe.hits(LIVE_1012.relative_to(ROOT) / f"frame-{i:04d}.png", "SC.KnowhowScan.List.Guard"), i)
        x, y, w, h = self.nodes["SC.KnowhowScan.Detail.ScrollDown"]["target"]
        thumb = ks.read_thumb(self.frames[1], ks.DETAIL_BAR_X, ks.DETAIL_TRACK)
        self.assertTrue(thumb and thumb["at_top"] and not thumb["at_bottom"])  # the detail list can scroll down
        self.assertLess(x + w, 360)                                            # clear of the per-row buttons

    def test_identical_capture_after_a_refused_input_is_not_a_new_frame(self):
        rt = self.runtime()
        f1, f2, f3, f4 = self.frames
        self.assertTrue(np.array_equal(f2, f3) and np.array_equal(f3, f4))
        self.assertEqual(self.poll(rt, f1, True), "SC.KnowhowScan.Grid.OpenTile.R0C0")
        # frame 2: Want.DetailScrollDown matches, the node is refused; every later Want sees the same decision
        self.assertIsNone(self.poll(rt, f2, False))
        self.assertEqual((rt.session.frames, rt.session.final), (2, None))
        self.assertEqual(rt.session.log[-1]["kind"], "scroll_detail")
        # the next capture (identical pixels): still the same decision; now the wheel is sent
        self.assertEqual(self.poll(rt, f3, True), "SC.KnowhowScan.Detail.ScrollDown")
        self.assertEqual(rt.session.frames, 2)
        # only after the confirmed send is an identical capture a real observation (dy=0 = content stopped)
        self.assertEqual(self.poll(rt, f4, True), "SC.KnowhowScan.Grid.OpenTile.R0C1")
        self.assertEqual(rt.session.frames, 3)
        self.assertIsNone(rt.session.final)
        book = rt.session.books[0]
        self.assertEqual((book["steps"][0]["dy"], book["steps"][0]["moved"], book["end"]), (0, False, "content_stopped"))
        self.assertEqual(rt.unconfirmed, 0)


# ---- staged binding --------------------------------------------------------------------------------------------------
class BindingTests(unittest.TestCase):
    def test_scan_exit_and_hold_are_checked_before_all_tiles(self):
        _, pipeline = builder.build()
        main = json.loads(pipeline)["SC.KnowhowScan.Main"]["next"]
        back = main.index("[JumpBack]SC.KnowhowScan.List.Back")
        hold = main.index("[JumpBack]SC.KnowhowScan.List.Hold")
        tiles = [i for i, name in enumerate(main) if ".Grid.Open" in name]
        self.assertLess(back, min(tiles))
        self.assertLess(hold, min(tiles))

    def test_bound_table_and_pipeline_are_consistent(self):
        table, table_text, nodes = bound()
        repo = pa.default_table()
        self.assertIn(ks.FAMILY, table.families)
        knowhow = [a for a in table.actions if a.family == ks.FAMILY]
        self.assertEqual(len(knowhow), 63)            # + 4 絞り込み popup actions + 30 drifting-grid tap slots
        #                                               + 1 獲得順 sort key (2026-10-25, newest-books scan)
        #                                               + 4 jump-probe actions (2026-10-25, calibration only)
        #                                               + 1 detail 保護 (2026-10-26, keep step, lock runs only)
        self.assertTrue(all(a.enabled and not a.consuming for a in knowhow))
        self.assertEqual({a.id for a in knowhow if a.wheel is not None},
                         {"knowhow-grid-scroll-down", "knowhow-grid-scroll-up", "knowhow-detail-scroll-down",
                          "knowhow-filter-scroll-down", "knowhow-filter-scroll-up",
                          "knowhow-grid-jump-down", "knowhow-grid-jump-up"})
        self.assertTrue(all("UNVERIFIED" in a["source"] for a in json.loads(table_text)["actions"]
                            if a.get("wheel") is not None and a["family"] == ks.FAMILY))
        # every other entry keeps its id, rect, flags; only the three navigation actions gain KnowhowScan nodes
        for a in repo.actions:
            b = table.get(a.id)
            self.assertEqual((a.family, a.rect, a.consuming, a.enabled, a.wheel), (b.family, b.rect, b.consuming, b.enabled, b.wheel))
            extra = [n for n in b.nodes if n not in a.nodes]
            # after `knowhow_scan_build.py --apply` the repository already carries the additions
            self.assertEqual(extra, [n for n in ks.NODE_ADDITIONS.get(a.id, []) if n not in a.nodes])
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "pipeline"
            shutil.copytree(ROOT / "assets" / "resource" / "base" / "pipeline", d)
            (d / "knowhow_scan.json").write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
            clickable = launcher.click_node_actions(d, table)
        self.assertEqual({n for n in clickable if n.startswith("SC.KnowhowScan.")},
                         {n for n, node in nodes.items() if node.get("action") == "Click"})
        self.assertEqual(len([n for n in nodes.values() if n.get("action") == "Click"]), 68)   # + 30 OpenSlot, + Keep.Protect
        #                                                                            + 4 jump probe
        #                                                                            + 1 獲得順 key
        self.assertFalse(any(str(n.get("doc", "")).startswith(builder.PLACEHOLDER) for n in nodes.values()))
        self.assertIn("[JumpBack]SC.Popup.CloseKnown", nodes["SC.KnowhowScan.Main"]["next"])
        self.assertNotIn("enabled", nodes["SC.KnowhowScan.Entry"])
        for name in ("SC.KnowhowScan.Grid.ScrollDown", "SC.KnowhowScan.Detail.ScrollDown",
                     "SC.KnowhowScan.Filter.ScrollDown", "SC.KnowhowScan.Filter.ScrollUp"):
            self.assertEqual(nodes[name]["post_delay"], builder.POST_DELAYS.get(nodes[name]["doc"].split()[1].split("（")[0], builder.WHEEL_POST_DELAY))

    def test_build_is_idempotent_and_keeps_line_endings(self):
        table_text, pipeline_text = builder.build()
        again_table, again_pipeline = builder.build(table_text, pipeline_text)
        self.assertEqual((again_table, again_pipeline), (table_text, pipeline_text))
        repo = builder.TABLE.read_bytes().decode("utf-8")
        self.assertEqual("\r\n" in repo, "\r\n" in table_text)

    def test_tile_and_wheel_rects_avoid_buttons(self):
        for r in range(ks.ROWS):
            for c in range(ks.COLS):
                tx, ty, _, _ = kb.list_tile_box(r, c)
                x, y, w, h = next(a for a in ks.ACTIONS if a["id"] == f"knowhow-tile-r{r}c{c}")["rect"]
                self.assertTrue(tx + 26 <= x and x + w <= tx + 80 and ty + 28 <= y and y + h <= ty + 86)
        dx, dy, dw, dh = next(a for a in ks.ACTIONS if a["id"] == "knowhow-detail-scroll-down")["rect"]
        self.assertLess(dx + dw, 360)           # per-row buttons x 360-420
        self.assertLess(dy + dh, 625)           # 保護 / 売却 buttons below


# ---- launcher ----------------------------------------------------------------------------------------------------------
class LauncherTests(unittest.TestCase):
    def test_refused_until_the_table_is_switched(self):
        if ks.FAMILY in pa.default_table().families:
            self.skipTest("动作表已切换")
        with self.assertRaisesRegex(ValueError, "KnowhowScan 尚不能运行"):
            scoped(calib_config())
        with self.assertRaisesRegex(ValueError, "未知任务"):
            launcher.plan_tasks(launcher.load_interface(), calib_config())

    def test_options_modes_and_caps(self):
        # The shipped default is the UI full scan (74d176a: scan, max_inputs 590 -> cap 595); calibrate is explicit.
        self.assertEqual(launcher.knowhow_scan_options(None)["mode"], "scan")
        o = launcher.knowhow_scan_options({"mode": "calibrate"})
        self.assertEqual((o["mode"], o["cap"]), ("calibrate", 150))
        self.assertIsNone(o["attach"]["resume_record"])
        scan = launcher.knowhow_scan_options({"mode": "scan", "details": False, "max_inputs": 150,
                                              "max_grid_scrolls": 140, "max_frames": 600, "resume_record": "latest"})
        self.assertEqual((scan["cap"], scan["attach"]["resume_record"]), (155, "latest"))
        # The task timeout bounds a scan segment: it winds down KNOWHOW_WIND_DOWN_S before (UI: 2700 s -> 2460 s).
        self.assertEqual(launcher.knowhow_scan_options(None)["attach"]["max_seconds"], 0)
        self.assertEqual(launcher.knowhow_scan_options(None, 2700)["attach"]["max_seconds"], 2460)
        self.assertEqual(launcher.knowhow_scan_options({"max_seconds": 600}, 2700)["attach"]["max_seconds"], 600)
        self.assertEqual(launcher.knowhow_scan_options({"mode": "calibrate"}, 2700)["attach"]["max_seconds"], 0)
        plan = launcher.supervised_only_plan({"name": "KnowhowScan"}, {"task_timeout_s": 2700})
        self.assertEqual(plan.pipeline_override["SC.KnowhowScan.Config"]["attach"]["max_seconds"], 2460)
        for bad in ({"mode": "sell"}, {"mode": "scan", "max_inputs": 596}, {"calibration": {"max_grid_wheels": 181}},
                    {"mode": "scan", "resume_record": "../x.json"}, {"bogus": 1}, "calibrate"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                launcher.knowhow_scan_options(bad)

    def test_newest_books_options(self):
        # 读新书: a small own budget. "auto" (after a Produce; "pending" is the same) = topup "only": the scan reads
        # 所持数 and decides N itself, never a full-scan fallback; a fresh library scan prefers a top-up.
        o = launcher.knowhow_scan_options({"mode": "scan", "newest": 2})
        self.assertEqual((o["attach"]["newest"], o["attach"]["topup"], o["attach"]["max_inputs"], o["cap"]),
                         (2, "off", 32, 37))
        for value in ("auto", "pending"):
            o = launcher.knowhow_scan_options({"mode": "scan", "newest": value})
            self.assertEqual((o["attach"]["newest"], o["attach"]["topup"], o["attach"]["max_inputs"], o["cap"]),
                             (0, "only", 8 + 12 * ks.NEWEST_MAX, 8 + 12 * ks.NEWEST_MAX + launcher.KNOWHOW_NAV_INPUTS))
        self.assertEqual(launcher.knowhow_scan_options({"mode": "scan"})["attach"]["topup"], "prefer")
        self.assertEqual(launcher.knowhow_scan_options({"mode": "scan", "topup": "off"})["attach"]["topup"], "off")
        resumed = launcher.knowhow_scan_options({"mode": "scan", "resume_record": "latest", "scan_id": "a1"})
        self.assertEqual(resumed["attach"]["topup"], "off")              # a continued full scan stays a full scan
        for bad in ({"mode": "scan", "newest": 2, "resume_record": "latest", "scan_id": "a1"},
                    {"mode": "scan", "newest": "auto", "scan_id": "a1"},
                    {"mode": "scan", "newest": 2, "topup": "only"}, {"mode": "scan", "topup": "always"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                launcher.knowhow_scan_options(bad)

    def test_only_a_lock_run_admits_the_detail_protect(self):
        # keep step: 自动锁书 / 自动卖书 ride on the post-Produce top-up; only locking adds an input
        o = launcher.knowhow_scan_options({"mode": "scan", "newest": "auto", "keep_lock": True, "keep_sell": True})
        base = 8 + 12 * ks.NEWEST_MAX
        self.assertEqual((o["attach"]["keep_lock"], o["attach"]["keep_sell"], o["attach"]["max_inputs"]),
                         (True, True, base + launcher.KNOWHOW_KEEP_INPUTS * ks.NEWEST_MAX))
        sell = launcher.knowhow_scan_options({"mode": "scan", "newest": "auto", "keep_sell": True})
        self.assertEqual(sell["attach"]["max_inputs"], base)
        for bad in ({"mode": "scan", "keep_lock": True}, {"mode": "scan", "newest": 2, "keep_lock": True},
                    {"mode": "scan", "newest": "auto", "keep_strategy": "all"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                launcher.knowhow_scan_options(bad)
        self.assertNotIn("knowhow-detail-protect", launcher.SUPERVISED_TASK_ACTIONS["KnowhowScan"])
        self.assertIn("knowhow-detail-protect", launcher.knowhow_task_actions(lock=True))
        plans = {flag: launcher.supervised_only_plan({"name": "KnowhowScan", "option": {
            "mode": "scan", "newest": "auto", flag: True}}) for flag in ("keep_lock", "keep_sell")}
        self.assertEqual({flag: launcher.knowhow_plan_lock(p) for flag, p in plans.items()},
                         {"keep_lock": True, "keep_sell": False})
        families = sorted(launcher.SUPERVISED_TASK_FAMILIES["KnowhowScan"])
        prefixes = launcher.SUPERVISED_TASK_NODE_PREFIXES["KnowhowScan"]
        on = launcher.mode_override(launcher.LIVE_INPUT, families, (), launcher.knowhow_task_actions(lock=True), prefixes)
        off = launcher.mode_override(launcher.LIVE_INPUT, families, (), launcher.SUPERVISED_TASK_ACTIONS["KnowhowScan"],
                                     prefixes)
        self.assertEqual(("SC.KnowhowScan.Keep.Protect" in on, "SC.KnowhowScan.Keep.Protect" in off), (True, False))
        self.assertFalse(pa.default_table().get("knowhow-detail-protect").consuming)

    def test_switched_table_admits_only_the_knowhow_ids_and_nodes(self):
        with tempfile.TemporaryDirectory(dir=launcher.PRIVATE, prefix="knowhow-switch-") as tmp, switched(Path(tmp)) as table:
            self.assertIsNone(launcher.knowhow_scan_table_refusal())
            value = calib_config()
            actual = scoped(value)
            self.assertEqual(set(actual["families"]), launcher.SUPERVISED_TASK_FAMILIES["KnowhowScan"])
            scoped({**value, "max_clicks": 21, "task_max_clicks": {"KnowhowScan": 21}})   # explicit caps are optional
            for over in ({**value, "allow_spend": ["training-start"]},
                         {**value, "families": ["knowhow-scan", "nav", "shop-money"]}):
                with self.subTest(over=over), self.assertRaises(ValueError):
                    scoped(over)
            plan = launcher.supervised_only_plan(launcher.supervised_only_item(actual))
            self.assertEqual((plan.name, plan.entry), ("KnowhowScan", "SC.KnowhowScan.Entry"))
            self.assertEqual(plan.pipeline_override["SC.KnowhowScan.Config"]["attach"]["mode"], "calibrate")
            actions = launcher.SUPERVISED_TASK_ACTIONS["KnowhowScan"]
            self.assertFalse(any(table.get(a).consuming for a in actions))
            override = launcher.mode_override(launcher.LIVE_INPUT, actual["families"], (), actions,
                                              launcher.SUPERVISED_TASK_NODE_PREFIXES["KnowhowScan"])
            bound_nodes = launcher.click_node_actions()
            self.assertTrue(all(n.startswith(("SC.KnowhowScan.", "SC.Popup.")) and bound_nodes[n] in actions for n in override))
            self.assertEqual({bound_nodes[n] for n in override if n.startswith("SC.KnowhowScan.")},
                             {a for a in actions if not a.startswith("popup-")})
            self.assertNotIn("SC.CardScan.Home.OpenIdol", override)
            # an ordinary run never enables the supervised-only nodes
            ordinary = launcher.mode_override(launcher.LIVE_INPUT, sorted(table.families), ())
            self.assertFalse([n for n in ordinary if n.startswith("SC.KnowhowScan.")])

    def test_sequence_and_ui_cannot_plan_it(self):
        with self.assertRaises(ValueError):
            launcher.sequence_run_config({"tasks": ["KnowhowScan"]})


@unittest.skipUnless(HAVE_MAA and cf.ready(), "需要 MaaFramework、私有模板包与 2026-10-08 know-how 原生帧")
class CalibrationLivePathTests(unittest.TestCase):
    """The first supervised calibration through the real --live --supervised-task path (WebView2 gate + fake host)."""

    def test_calibration_run_sends_only_allowlisted_inputs_and_reports_the_frames(self):
        from PIL import Image

        from agent.core import webview_controller as wc
        from tests import test_card_scan_native_20261007 as N
        from tests.test_task_launcher import RISK_POLICY, FakePipelineHost, private_ready
        if not (private_ready() and N._present_1008()):
            self.skipTest("requires the private template bundle and the 2026-10-08 native frames")
        with tempfile.TemporaryDirectory(dir=launcher.PRIVATE, prefix="knowhow-calib-live-") as directory:
            directory = Path(directory)
            strip = cf.grid_strip()
            last = cf.load(cf.DETAIL[-1])
            grid = []
            for i, offset in enumerate((158, 316, 158)):
                path = directory / f"grid-{i}.png"
                Image.fromarray(cf.grid_window(last, strip, offset)).save(path)
                grid.append(path.relative_to(ROOT))
            k = "dev_assets/private/live-capture-20261008-knowhow/"
            frames = ([N.HOMES[0], N.PORTAL_AFTER, N.LIST_FES, k + cf.GRID_START + ".png"]
                      + [k + n + ".png" for n in cf.DETAIL] + grid + [N.LIST_FES, N.PORTAL_AFTER, N.HOMES[1]])
            with switched(directory) as table:
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
                config = calib_config()
                gate = wc.WebViewInputGate(host, wc.RunConfig(True, frozenset(launcher.SUPERVISED_TASK_FAMILIES["KnowhowScan"])),
                                           {"pipeline": wc.Endpoint("pipeline", session)}, wc.SpendGate(),
                                           wc.AuditLog(directory / "audit"), host.send, policy=RISK_POLICY,
                                           limits=wc.ClickLimits(run_max=20, task_max=20))
                summary = launcher.run(copy.deepcopy(config), live=True, supervised_task=True, gate=gate,
                                           policy=RISK_POLICY, summary_path=False, save_diagnostics=False,
                                           task_options={"knowhow_scan": {"run_dir": directory / "knowhow-scan",
                                                                          "inventory_path": directory / "inv.json",
                                                                          "ui_path": directory / "knowhow.js"}})
            actions = [row["action"] for row in host.requests]
            self.assertEqual(actions, ["home-idol", "idol-portal-list", "idol-list-tab-knowhow", "knowhow-tile-r0c0"]
                             + ["knowhow-detail-scroll-down"] * 10 + ["knowhow-grid-scroll-down"] * 2
                             + ["knowhow-grid-scroll-up", "nav-back", "nav-back", "nav-back"],
                             json.dumps([{k: v for k, v in t.items() if k != "reports"} for t in summary.get("tasks", [])]
                                        + [summary.get("stop_reason"), summary.get("error")], ensure_ascii=False, default=str)[:1500])
            self.assertTrue(all(row["node_name"].startswith("SC.KnowhowScan.") for row in host.requests))
            self.assertEqual(summary["live"]["supervised_task"], "KnowhowScan")
            task, = summary["tasks"]
            report = next(r for r in task["reports"] if r["kind"] == "knowhow_scan")
            self.assertEqual((report["outcome"], report["mode"], report["scan_ready"]), ("calibrated", "calibrate", True))
            self.assertEqual(report["grid_step_px"]["median"], 158)
            self.assertEqual(len(list(Path(report["frames_dir"]).glob("frame-*.png"))), 15)
            self.assertEqual(task["status"], "succeeded")
            self.assertFalse((directory / "inv.json").exists())


if __name__ == "__main__":
    unittest.main()


_CAL_1012 = ROOT / "dev_assets/private/knowhow-scan/calibrate-20261012T022320Z"


@unittest.skipUnless((_CAL_1012 / "frame-0017.png").exists() and _live_1012_ready(),
                     "需要 2026-10-12 实机校准私有帧（calibrate-20261012T022320Z）、ppocr 模型与 MaaFramework")
class LiveDetailMerge20261012Tests(unittest.TestCase):
    """Wheel steps of 159-160 px (~1.5 rows) and stacked two-line names: rows pair by the measured shift."""

    @staticmethod
    def crop_ocr(arr):
        from tests import maa_probe

        bgr = np.ascontiguousarray(arr[:, :, ::-1])
        h, w = arr.shape[:2]
        r = maa_probe._OCR.recognize({"name": "x", "roi": [0, 0, w, h], "expected": "^.*$", "threshold": 0.0}, bgr)
        return [(i["text"], tuple(i["box"]), i["score"]) for i in r["results"]]

    def test_both_calibration_books_merge_completely(self):
        from tests.maa_probe import rgb

        for first, last in ((2, 9), (10, 17)):
            pages, shifts, prev = [], [], None
            for n in range(first, last + 1):
                image = rgb(_CAL_1012 / f"frame-{n:04d}.png")
                pages.append(kb.parse_detail_panel(kb.refine_detail_tokens(_maa_ocr(image), image, self.crop_ocr), image))
                shifts.append(None if prev is None else kc.detail_shift(prev, image)["dy"])
                prev = image
            self.assertTrue(all(150 <= d <= 170 for d in shifts[1:-1]), shifts)
            merged = kb.merge_detail_pages(pages, shifts=shifts)
            self.assertEqual((len(merged["rows"]), merged["unknowns"], merged["complete"]), (13, [], True), first)
            kinds = [kb._type_suffix(r["name"]) for r in merged["rows"] if "キラキラ" in r["name"]]
            self.assertEqual(len(kinds), len(set(kinds)))     # (Vo)/(Da)/(Vi) siblings stay separate rows

    def test_icon_merged_name_is_still_a_row(self):
        """2026-10-12 live scan (21 rows): OCR swallowed the type icon into 「スペシャリスト(G.R.A.D.)」 (box x 32)."""
        from tests.maa_probe import rgb

        scan = ROOT / "dev_assets/private/knowhow-scan/scan-20261012T130245Z"
        if not (scan / "frame-0014.png").exists():
            self.skipTest("需要 2026-10-12 实机扫描私有帧（scan-20261012T130245Z）")
        pages, shifts, prev = [], [], None
        for n in range(2, 15):
            image = rgb(scan / f"frame-{n:04d}.png")
            pages.append(kb.parse_detail_panel(kb.refine_detail_tokens(_maa_ocr(image), image, self.crop_ocr), image))
            shifts.append(None if prev is None else kc.detail_shift(prev, image)["dy"])
            prev = image
        self.assertIn("スペシャリスト(G.R.A.D.)", [r["name"] for r in pages[9]["rows"]])
        merged = kb.merge_detail_pages(pages, shifts=shifts)
        self.assertEqual((len(merged["rows"]), merged["unknowns"], merged["complete"]), (21, [], True))
        self.assertFalse(any(r["name"].startswith("Aア") for p in pages for r in p["rows"]))   # lone icon noise 「A」 never joins

    def test_live_20261023_rows_missing_books_merge_completely(self):
        """2026-10-23 chain seg1 (book 15) / seg3 (book 59) / seg6 (book 66) stopped with detail_rows_missing 21/21: a re-read band that
        dropped 「イドル！(Vo)」 and an effect box taken for an icon-merged name both left a clipped row."""
        from tests.maa_probe import rgb

        cases = (("scan-20261023T060509Z", range(108, 121), 21), ("scan-20261023T065706Z", range(118, 131), 21),
                 ("scan-20261023T074450Z", range(24, 37), 21),       # seg6 book 66: 「A.D.)」 second-line fragment
                 ("scan-20261023T114205Z", range(328, 336), 13),     # seg16 book 267: 「イドル！(Vi)」 only reads at x3
                 ("scan-20261023T133801Z", range(299, 304), 9))      # seg21 book 382: name glued to its 「Lv.1」
        for name, frames, count in cases:
            scan = ROOT / "dev_assets/private/knowhow-scan" / name
            if not (scan / f"frame-{frames[-1]:04d}.png").exists():
                self.skipTest(f"需要 2026-10-23 实机扫描私有帧（{name}）")
            with self.subTest(scan=name):
                pages, shifts, prev = [], [], None
                for n in frames:
                    image = rgb(scan / f"frame-{n:04d}.png")
                    pages.append(kb.parse_detail_panel(kb.refine_detail_tokens(_maa_ocr(image), image, self.crop_ocr),
                                                       image))
                    shifts.append(None if prev is None else kc.detail_shift(prev, image)["dy"])
                    prev = image
                merged = kb.merge_detail_pages(pages, shifts=shifts)
                self.assertEqual((len(merged["rows"]), merged["unknowns"], merged["complete"]), (count, [], True))

    def test_scan_20261012T193157Z_replay_keeps_scrolling_detail(self):
        """2026-10-12 full-library scan: 7 detail scrolls of book 7 read cleanly; the stall was the host (desktop lock),
        not the parser, so the session must still ask for the next scroll on frame 8."""
        from tests.maa_probe import rgb

        scan = ROOT / "dev_assets/private/knowhow-scan/scan-20261012T193157Z"
        record = ROOT / "dev_assets/private/knowhow-scan/knowhow-scan-20261012T191516Z-partial.json"
        if not (scan / "frame-0008.png").exists() or not record.exists():
            self.skipTest("需要 2026-10-12 全库扫描私有帧与续扫记录")
        session = ks.ScanSession(ks.ScanOptions.from_params({"max_books": 600, "max_inputs": 590, "max_frames": 8000,
                                                             "max_holds": 5, "max_grid_scrolls": 300,
                                                             "max_detail_scrolls": 30}),
                                 _maa_ocr, scan_id="replay-20261012",
                                 # resume now needs a matching scan_id; the 2026-10-12 record predates it
                                 resume={**json.loads(record.read_text(encoding="utf-8")), "scan_id": "replay-20261012"})
        kinds = [session.observe(rgb(scan / f"frame-{n:04d}.png")).kind for n in range(1, 9)]
        self.assertEqual(kinds, ["open_tile"] + ["scroll_detail"] * 7)

    def test_preset_dialog_frame_is_recognised_and_the_list_is_not(self):
        """kh-full8: book detail + ノウハウブックプリセット dialog (キャンセル/保存) after one tile tap."""
        from tests.maa_probe import NodeProbe, rgb

        shot = ROOT / "dev_assets/private/live-20261012k/after-kh8.png"
        plain = ROOT / "dev_assets/private/knowhow-scan/scan-20261013T144703Z/frame-0001.png"
        if not shot.exists() or not plain.exists():
            self.skipTest("需要 kh-full8 私有帧")
        screen = kb.classify_knowhow_screen(_maa_ocr(rgb(shot)), rgb(shot))
        self.assertEqual((screen["popup"], screen["ok"]), ("preset", False))
        self.assertIsNone(kb.classify_knowhow_screen(_maa_ocr(rgb(plain)), rgb(plain))["popup"])
        probe = NodeProbe()
        self.assertTrue(probe.hits(shot.relative_to(ROOT).as_posix(), "SC.KnowhowScan.Anchor.Preset.Title"))
        self.assertFalse(probe.hits(plain.relative_to(ROOT).as_posix(), "SC.KnowhowScan.Anchor.Preset.Title"))

    def test_scan_20261013T150217Z_title_slip_keeps_the_binding(self):
        """UI scan of the 幽谷霧子 book: the title row read 「…幽谷霧子」, 「…幽谷霧子」, then 「…幽谷鼎子」 -> binding lost."""
        from tests.maa_probe import rgb

        scan = ROOT / "dev_assets/private/knowhow-scan/scan-20261013T150217Z"
        if not (scan / "frame-0004.png").exists():
            self.skipTest("需要 2026-10-13 UI 扫描私有帧（scan-20261013T150217Z）")
        pages = []
        for n in (2, 3, 4):
            image = rgb(scan / f"frame-{n:04d}.png")
            pages.append(kb.parse_detail_panel(kb.refine_detail_tokens(_maa_ocr(image), image, self.crop_ocr), image))
        self.assertNotEqual(pages[0]["title"], pages[2]["title"])         # the slip is real
        self.assertTrue(all(kb.same_book_title(pages[0]["title"], p["title"]) for p in pages[1:]))
        self.assertEqual(len({str(p["stats"]) for p in pages}), 1)
        self.assertNotIn("merge.book_conflict", kb.merge_detail_pages(pages)["unknowns"])

    def test_icon_merged_short_name_keeps_overlap_row(self):
        """2026-10-12 book 7 scan: 「パーフェクトリィ(G.R.A.D.)」 merged with its icon at box h 23 was dropped -> detail_overlap_lost."""
        from tests.maa_probe import rgb

        scan = ROOT / "dev_assets/private/knowhow-scan/scan-20261012T144649Z"
        if not (scan / "frame-0021.png").exists():
            self.skipTest("需要 2026-10-12 实机扫描私有帧（scan-20261012T144649Z）")
        pages, shifts, prev = [], [], None
        for n in range(15, 22):
            image = rgb(scan / f"frame-{n:04d}.png")
            pages.append(kb.parse_detail_panel(kb.refine_detail_tokens(_maa_ocr(image), image, self.crop_ocr), image))
            moved = None if prev is None else kc.detail_shift(prev, image)
            shifts.append(moved["dy"] if moved else None)
            prev = image
        self.assertIn("パーフェクトリィ(G.R.A.D.)", [r["name"] for r in pages[-1]["rows"]])
        merged = kb.merge_detail_pages(pages, shifts=shifts)
        self.assertNotIn("merge.overlap_lost", merged["unknowns"])
        self.assertEqual(len(merged["rows"]), 12)

    def test_pages_join_when_the_first_new_row_is_one_pitch_below(self):
        """2026-10-24 book 414 (seg23): the full-frame first page missed its third row (WING優勝, y 490) -> no shared row
        -> detail_overlap_lost. The left-panel read finds it; and the next page's first row (y 330, dy 160) sits one
        row pitch below the last read row, so the pages join without a shared row as well."""
        from tests.maa_probe import rgb

        scan = ROOT / "dev_assets/private/knowhow-scan/scan-20261023T143814Z"
        if not (scan / "frame-0345.png").exists():
            self.skipTest("需要 2026-10-23 实机扫描私有帧（scan-20261023T143814Z）")
        a, b = (rgb(scan / f"frame-{n:04d}.png") for n in (344, 345))
        dy = kc.detail_shift(a, b)["dy"]
        self.assertEqual(dy, 160)
        left = lambda image: [t for t in _maa_ocr(image[:, :490]) if t[1][0] + t[1][2] / 2 < 490]  # noqa: E731
        page = lambda texts, image: kb.parse_detail_panel(kb.refine_detail_tokens(texts, image, self.crop_ocr), image)  # noqa: E731
        second = page(left(b), b)
        self.assertEqual([r["name"] for r in page(left(a), a)["rows"]], ["VisualUP", "ファンの声援", "WING優勝"])
        full = page(_maa_ocr(a), a)
        self.assertEqual([r["name"] for r in full["rows"]], ["VisualUP", "ファンの声援"])     # the live read
        merged = kb.merge_detail_pages([full, second], shifts=[None, dy])
        self.assertNotIn("merge.overlap_lost", merged["unknowns"])
        self.assertEqual([r["name"] for r in merged["rows"]],
                         ["VisualUP", "ファンの声援", "WING優勝", "WING準決勝(Vi)", "W.I.N.G.(Da)++"])
        self.assertIn("merge.overlap_lost", kb.merge_detail_pages([full, second], shifts=[None, dy + 104])["unknowns"])

    def test_missing_stat_is_reread_from_its_crop(self):
        """2026-10-24 book 419 【アイドルロード】福丸小糸 (seg27): メンタル 「1」 was never detected -> record incomplete
        (stats.me). The stat ROI crop reads it; the other stats are untouched."""
        from tests.maa_probe import rgb

        scan = ROOT / "dev_assets/private/knowhow-scan/scan-20261023T154901Z"
        if not (scan / "frame-0162.png").exists():
            self.skipTest("需要 2026-10-24 实机扫描私有帧（scan-20261023T154901Z）")
        image = rgb(scan / "frame-0162.png")
        texts = [t for t in _maa_ocr(image[:, :490]) if t[1][0] + t[1][2] / 2 < 490]
        page = kb.parse_detail_panel(texts, image)
        self.assertEqual(page["stats"], {"vo": 30, "da": 10, "vi": 10})
        extra = kb.reread_detail_stats(image, _maa_ocr, ["me"])
        self.assertEqual([t for t, _, _ in extra], ["1"])
        self.assertEqual(kb.parse_detail_panel(texts + extra, image)["stats"], {"vo": 30, "da": 10, "vi": 10, "me": 1})
