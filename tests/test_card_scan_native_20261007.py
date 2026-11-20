"""Card-scan guards on the 2026-10-07 native host frames (supervised capture, dev_assets/private/fes-match-reader-20261007).

Offline only (replay controller refuses every click). These frames come from the new host's own capture path, not a
recording: the アイドル portal and the トレーニングアイドル選択 list (same sort bar / grid / scrollbar geometry as アイドル一覧,
but a different page). No native アイドル一覧 frame exists in this batch yet, so List.Guard is only checked for rejection.
Skips cleanly when the private frames, MaaFramework or the OCR model are absent.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.maa_probe import maa_ready

ROOT = Path(__file__).resolve().parents[1]
SCAN_LOADING = ROOT / "dev_assets/private/card-scan/card-scan-20261008T141647Z/frame-002.png"
SCAN_LOADED = ROOT / "dev_assets/private/card-scan/card-scan-20261008T141647Z/frame-003.png"
D = "dev_assets/private/fes-match-reader-20261007/"
PORTAL = D + "idol_portal.png"
TRAINING_LIST = D + "training_list_descending.png"
HOME = D + "home_after_present_settled.png"
FRAMES = (PORTAL, TRAINING_LIST, HOME)
# Lv printed on each tile of TRAINING_LIST (read by eye from the frame), row-major r1c1..r3c7.
TRAINING_LIST_LV = (66, 5, 1, 80, 60, 80, 60, 60, 8, 65, 60, 1, 6, 1, 1, 1, 65, 3, 76, 60, 5)


def _present():
    return all((ROOT / f).is_file() for f in FRAMES)


@unittest.skipUnless(SCAN_LOADING.is_file() and SCAN_LOADED.is_file(), "需要 2026-10-09 占位卡面与加载后原生帧")
class NativeCardLoadingTests(unittest.TestCase):
    def test_placeholders_wait_without_reading_or_scrolling(self):
        import numpy as np
        from PIL import Image
        from unittest.mock import Mock
        from agent.core import card_list_scan as cs

        loading = Image.open(SCAN_LOADING).convert("RGB")
        loaded = Image.open(SCAN_LOADED).convert("RGB")
        self.assertTrue(cs.card_thumbnails_loading(np.asarray(loading)))
        self.assertFalse(cs.card_thumbnails_loading(np.asarray(loaded)))
        read_tiles = Mock()
        session = cs.ScanSession(cs.ScanOptions(tabs=("support",), max_holds=2), read_tiles,
                                 lambda rgb, roi: "アイドル一覧")
        decisions = [session.observe(loading) for _ in range(3)]
        self.assertEqual([d.kind for d in decisions], ["hold", "hold", "stop"])
        self.assertEqual(decisions[-1].reason, "hold_budget_exhausted")
        self.assertEqual((session.tabs[0].scrolls, session.tabs[0].frames, session.records), (0, 0, []))
        read_tiles.assert_not_called()

def _rect(action_id):
    actions = json.loads((ROOT / "assets/input/pipeline_actions.json").read_text(encoding="utf-8"))["actions"]
    return next(a["rect"] for a in actions if a["id"] == action_id)


def _inside(inner, outer):
    x, y, w, h = inner
    ox, oy, ow, oh = outer
    return ox <= x and oy <= y and x + w <= ox + ow and y + h <= oy + oh


@unittest.skipUnless(_present() and maa_ready(), "需要 2026-10-07 原生宿主帧、MaaFramework、私有模板包与 OCR 模型")
class NativeHostFrameGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.maa_probe import NodeProbe

        cls.probe = NodeProbe()

    def test_portal_guards_hit_and_list_guards_miss(self):
        for node in ("SC.Nav.IdolPortal.Guard", "SC.CardScan.Anchor.Portal.ListEntry", "SC.CardScan.Portal.Settled"):
            with self.subTest(node=node):
                self.assertTrue(self.probe.hits(PORTAL, node))
        for node in ("SC.CardScan.List.Guard", "SC.CardScan.List.Settled", "SC.CardScan.Home.Settled"):
            with self.subTest(node=node, expect=False):
                self.assertFalse(self.probe.hits(PORTAL, node))

    def test_training_selection_list_is_not_taken_for_the_idol_list(self):
        # Same sort bar, grid and scrollbar as アイドル一覧, but the title differs: no list/portal/home guard may hit.
        for node in ("SC.CardScan.List.Guard", "SC.CardScan.List.Settled", "SC.CardScan.List.SupportSettled",
                     "SC.CardScan.List.ProduceSettled", "SC.Nav.IdolPortal.Guard", "SC.CardScan.Anchor.Portal.ListEntry",
                     "SC.CardScan.Home.Settled"):
            with self.subTest(node=node):
                self.assertFalse(self.probe.hits(TRAINING_LIST, node))

    def test_home_frame(self):
        self.assertTrue(self.probe.hits(HOME, "SC.CardScan.Home.Settled"))
        self.assertTrue(self.probe.hits(HOME, "SC.CardScan.HomeClean"))
        self.assertFalse(self.probe.hits(HOME, "SC.Nav.IdolPortal.Guard"))


@unittest.skipUnless(_present() and maa_ready(), "需要 2026-10-07 原生宿主帧与 OCR 模型")
class NativeHostFrameGeometryTests(unittest.TestCase):
    def test_portal_list_entry_rect_lies_on_the_ocr_label_card(self):
        from tests.maa_probe import ocr_items

        items = ocr_items(PORTAL, (760, 440, 270, 170))
        label = next(i for i in items if i["text"].startswith("アイ") and i["text"].endswith("覧"))
        rect = _rect("idol-portal-list")
        x, y, w, h = rect
        lx, ly, lw, lh = label["box"]
        self.assertTrue(lx <= x + w / 2 <= lx + lw and ly <= y + h / 2 <= ly + lh, (rect, label["box"]))
        self.assertTrue(_inside(rect, (770, 445, 248, 157)))                # アイドル一覧 card

    def test_shared_sort_bar_and_scrollbar_geometry(self):
        from agent.core import card_list_scan as cs
        from tests.maa_probe import ocr_items, rgb

        bar = cs.read_scrollbar(rgb(TRAINING_LIST))
        self.assertEqual((bar.status, bar.at_top, bar.at_bottom), ("ok", True, False))
        self.assertEqual((bar.thumb_top, bar.thumb_bottom), (191, 235))
        texts = {i["text"]: i["box"] for i in ocr_items(TRAINING_LIST, (760, 100, 500, 50))}
        self.assertTrue(_inside(texts["絞り込み"], (786, 105, 112, 42)))
        self.assertTrue(_inside(texts["デフォルト"], (990, 105, 120, 42)))
        self.assertTrue(_inside(texts["▼降順"], (1148, 104, 92, 44)))
        order, problems = cs.parse_order("デフォルト", "▼降順", "絞り込み")
        self.assertEqual((order, problems), (cs.ListOrder("デフォルト", "desc"), []))
        # The scroll actions' rects lie inside the list viewport (y 170-680, tiles x 267..1203).
        for action_id in ("idol-list-scroll-down", "idol-list-scroll-up"):
            with self.subTest(action=action_id):
                self.assertTrue(_inside(_rect(action_id), (257, 170, 974, 510)))

    def test_tile_reader_on_the_native_grid_never_misreads_a_level(self):
        from PIL import Image

        from agent.core import card_inventory as ci
        from agent.core import owned_levels as ol
        from tests.maa_probe import MODEL

        with Image.open(ROOT / TRAINING_LIST) as image:
            frame = image.convert("RGB")
        readings = ol.read_frame(frame, ci.CardIndex.from_assets(), ol.maa_ocr(MODEL), frame_name="training_list_descending")
        self.assertEqual(len(readings), 21)
        accepted = [r for r in readings if r["status"] == "accepted"]
        self.assertGreaterEqual(len(accepted), 12)
        for r in accepted:
            row, col = int(r["slot"][1]), int(r["slot"][3])
            with self.subTest(slot=r["slot"]):
                self.assertEqual(r["level"], TRAINING_LIST_LV[(row - 1) * 7 + col - 1])


# ---- 2026-10-08 native アイドル一覧 frames (supervised navigation, dev_assets/private/live-capture-20261008-knowhow) ----
# Entering the list from the アイドル portal lands on the フェスアイドル tab (idol_list_fes_initial), so the list page guard
# must accept that tab too; the scan then taps サポート/プロデュース. No scroll pair exists yet: wheel displacement is unmeasured.
K = "dev_assets/private/live-capture-20261008-knowhow/"
LIST_FES = K + "idol_list_fes_initial.png"
LIST_SUPPORT = K + "idol_list_support_top.png"
LIST_PRODUCE = K + "idol_list_produce_top.png"
PORTAL_AFTER = K + "idol_portal_after_lists.png"
HOMES = (K + "home_initial.png", K + "home_final.png")
FRAMES_1008 = (LIST_FES, LIST_SUPPORT, LIST_PRODUCE, PORTAL_AFTER) + HOMES
LISTS_1008 = (LIST_FES, LIST_SUPPORT, LIST_PRODUCE)
# Read by eye from the frames (row-major r1c1..r3c7): S Lv and 凸 (stars), P 凸. P tiles show no Lv.
SUPPORT_LV = (66, 5, 1, 80, 60, 80, 60, 60, 8, 65, 60, 1, 6, 1, 1, 1, 65, 3, 76, 60, 5)
SUPPORT_LB = (0, 0, 0, 4, 4, 4, 4, 0, 4, 4, 0, 0, 4, 4, 1, 0, 4, 1, 4, 0, 4)
PRODUCE_LB = (0, 3, 4, 1, 4, 1, 1, 0, 4, 1, 1, 4, 4, 2, 3, 4, 0, 1, 0, 0, 0)
# Left tab buttons measured on the native frames (x 21..183): フェス y 92..167, プロデュース y 178..252, サポート y 263..337.
TAB_BUTTON = {"produce": (21, 178, 162, 74), "support": (21, 263, 162, 74)}


def _present_1008():
    return all((ROOT / f).is_file() for f in FRAMES_1008)


def _slot_index(slot):
    return (int(slot[1]) - 1) * 7 + int(slot[3]) - 1


@unittest.skipUnless(_present_1008() and maa_ready(), "需要 2026-10-08 原生一覧帧、MaaFramework、私有模板包与 OCR 模型")
class NativeIdolListGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.maa_probe import NodeProbe

        cls.probe = NodeProbe()

    def test_list_entry_on_the_fes_tab_is_the_list_page_but_not_a_scan_tab(self):
        for node in ("SC.CardScan.List.Guard", "SC.CardScan.List.Settled", "SC.CardScan.Color.FesTab"):
            with self.subTest(node=node):
                self.assertTrue(self.probe.hits(LIST_FES, node))
        for node in ("SC.CardScan.Color.ScanTab", "SC.CardScan.List.SupportSettled", "SC.CardScan.List.ProduceSettled",
                     "SC.Nav.IdolPortal.Guard", "SC.CardScan.Home.Settled"):
            with self.subTest(node=node, expect=False):
                self.assertFalse(self.probe.hits(LIST_FES, node))

    def test_support_and_produce_tabs(self):
        for frame, hit, miss in ((LIST_SUPPORT, "SC.CardScan.List.SupportSettled", "SC.CardScan.List.ProduceSettled"),
                                 (LIST_PRODUCE, "SC.CardScan.List.ProduceSettled", "SC.CardScan.List.SupportSettled")):
            with self.subTest(frame=frame):
                for node in ("SC.CardScan.List.Guard", "SC.CardScan.List.Settled", "SC.CardScan.Color.ScanTab", hit):
                    self.assertTrue(self.probe.hits(frame, node), node)
                for node in (miss, "SC.CardScan.Color.FesTab", "SC.Nav.IdolPortal.Guard", "SC.CardScan.Home.Settled"):
                    self.assertFalse(self.probe.hits(frame, node), node)

    def test_return_path_guards(self):
        for node in ("SC.Nav.IdolPortal.Guard", "SC.CardScan.Anchor.Portal.ListEntry", "SC.CardScan.Portal.Settled"):
            with self.subTest(node=node):
                self.assertTrue(self.probe.hits(PORTAL_AFTER, node))
        for frame in HOMES:
            with self.subTest(frame=frame):
                for node in ("SC.CardScan.Home.Settled", "SC.Common.Anchor.Home.FesText", "SC.Common.Variant.HomeMission",
                             "SC.Common.Color.HomeUndimmed"):
                    self.assertTrue(self.probe.hits(frame, node), node)
        for frame in (PORTAL_AFTER,) + HOMES:
            for node in ("SC.CardScan.List.Guard", "SC.CardScan.List.Settled"):
                with self.subTest(frame=frame, node=node, expect=False):
                    self.assertFalse(self.probe.hits(frame, node))


@unittest.skipUnless(_present_1008() and maa_ready(), "需要 2026-10-08 原生一覧帧与 OCR 模型")
class NativeIdolListReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from agent.core import card_inventory as ci
        from agent.core import owned_levels as ol
        from tests.maa_probe import MODEL
        from tests.test_card_list_scan import ocr_reader

        cls.read_text = staticmethod(ocr_reader())
        cls.index, cls.ocr = ci.CardIndex.from_assets(), staticmethod(ol.maa_ocr(MODEL))

    def read_tiles(self, frame):
        from PIL import Image

        from agent.core import owned_levels as ol

        with Image.open(ROOT / frame) as image:
            return ol.read_frame(image.convert("RGB"), self.index, self.ocr, frame_name=Path(frame).stem)

    def test_tab_pixels_scrollbar_and_sort_bar(self):
        from agent.core import card_list_scan as cs
        from tests.maa_probe import rgb

        for frame, tab in ((LIST_FES, "fes"), (LIST_SUPPORT, "support"), (LIST_PRODUCE, "produce")):
            with self.subTest(frame=frame):
                image = rgb(frame)
                self.assertEqual(cs.selected_tab(image), tab)
                bar = cs.read_scrollbar(image)
                self.assertEqual((bar.status, bar.at_top, bar.at_bottom, bar.thumb_top), ("ok", True, False, 191))
                self.assertTrue(TITLE := self.read_text(image, cs.TITLE_ROI))
                self.assertRegex(TITLE, cs.TITLE_PATTERN)
                order, problems = cs.parse_order(self.read_text(image, cs.SORT_BOX), self.read_text(image, cs.DIRECTION_BOX),
                                                 self.read_text(image, cs.FILTER_BOX))
                self.assertEqual((order, problems), (cs.ListOrder("デフォルト", "desc"), []))
        for frame in (PORTAL_AFTER,) + HOMES:
            with self.subTest(frame=frame, expect="no list"):
                self.assertIsNone(cs.selected_tab(rgb(frame)))
                self.assertEqual(cs.read_scrollbar(rgb(frame)).status, "absent")

    def test_scan_session_taps_support_first_from_the_fes_tab(self):
        from PIL import Image

        from agent.core import card_list_scan as cs

        session = cs.ScanSession(cs.ScanOptions(), lambda frame, name: [], self.read_text)
        with Image.open(ROOT / LIST_FES) as image:
            decision = session.observe(image.convert("RGB"))
        self.assertEqual((decision.kind, decision.reason, decision.tab), ("tap_tab", "tab_not_selected", "support"))

    def test_support_tiles_levels_and_stars(self):
        readings = self.read_tiles(LIST_SUPPORT)
        accepted = [r for r in readings if r["status"] == "accepted"]
        self.assertEqual(len(readings), 21)
        self.assertGreaterEqual(len(accepted), 15)
        for r in accepted:
            with self.subTest(slot=r["slot"]):
                self.assertTrue(r["card_id"].startswith("2"))          # S card ids
                self.assertEqual(r["level"], SUPPORT_LV[_slot_index(r["slot"])])
                self.assertEqual(r["limit_break"], SUPPORT_LB[_slot_index(r["slot"])])

    def test_produce_tiles_stars_and_no_level(self):
        readings = self.read_tiles(LIST_PRODUCE)
        accepted = [r for r in readings if r["status"] == "accepted"]
        self.assertGreaterEqual(len(accepted), 19)
        for r in accepted:
            with self.subTest(slot=r["slot"]):
                self.assertTrue(r["card_id"].startswith("1"))          # P card ids
                self.assertIsNone(r["level"])
                self.assertEqual(r["limit_break"], PRODUCE_LB[_slot_index(r["slot"])])

    def test_fes_tiles_are_never_taken_for_owned_cards(self):
        self.assertEqual([r for r in self.read_tiles(LIST_FES) if r["status"] == "accepted"], [])


@unittest.skipUnless(_present_1008() and maa_ready(), "需要 2026-10-08 原生一覧帧、MaaFramework、私有模板包与 OCR 模型")
class NativeIdolListChainTests(unittest.TestCase):
    """Shipped card_scan.json on native frames (catalog enabled only in a test copy; the replay controller injects nothing).

    首页 → アイドル → 一覧（フェス标签）→ 点サポート → サポート顶部 → 滚一格 → 返回 → 返回 → 首页. The frame after the wheel step
    repeats the サポート top frame because no native scroll pair exists: it stands in for the step and ends the tab as
    ``content_stopped``. This proves the guards/decisions bind on native pixels, not the wheel displacement or coverage.
    """

    def test_entry_on_the_fes_tab_taps_support_then_scans(self):
        import tempfile

        import numpy as np
        from maa.tasker import Tasker

        from agent.core import card_scan_actions as csa
        from tests.maa_probe import NodeProbe, rgb
        from tests.test_card_list_scan import EXISTING, PROPOSED, _Sink, _enabled_override, _test_table
        from tools import run_tasks as launcher

        probe = NodeProbe()
        with tempfile.TemporaryDirectory() as tmp:
            runtime = csa.CardScanRuntime(sink=_Sink(), run_dir=Path(tmp), write_import=False)
            recognitions, actions = csa.card_scan_registrations(runtime)
            resource = probe.resource()
            for name, obj in recognitions.items():
                self.assertTrue(resource.register_custom_recognition(name, obj))
            for name, obj in actions.items():
                self.assertTrue(resource.register_custom_action(name, obj))
            frames = [HOMES[0], PORTAL_AFTER, LIST_FES, LIST_SUPPORT, LIST_SUPPORT, PORTAL_AFTER, HOMES[1]]
            images = [np.ascontiguousarray(rgb(f)[:, :, ::-1]) for f in frames]
            scenario = launcher.ReplayScenario(images, ["click"] * len(images), allow_input=True, table=_test_table(),
                                               actions=set(PROPOSED) | set(EXISTING))
            controller = launcher.create_replay_controller(scenario)
            self.assertTrue(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            job = tasker.post_task("SC.CardScan.Entry", _enabled_override({"tabs": ["support"]})).wait()
            self.assertTrue(job.succeeded, runtime.outcome)
            self.assertEqual(scenario.actions, ["home-idol", "idol-portal-list", "idol-list-tab-support",
                                                "idol-list-scroll-down", "nav-back", "nav-back"])
            self.assertEqual(scenario.refused, [])
            self.assertEqual(runtime.outcome, "scanned")
            self.assertEqual(runtime.session.holds, 0)
            accepted = [r for r in runtime.session.records if r["status"] == "accepted"]
            self.assertGreaterEqual(len(accepted), 15)                     # the サポート top frame is read once
            for r in accepted:
                self.assertEqual(r["level"], SUPPORT_LV[_slot_index(r["slot"])], r["slot"])
            self.assertEqual(runtime.result["conflicts"], {})


@unittest.skipUnless(_present_1008(), "需要 2026-10-08 原生一覧帧")
class NativeIdolListActionRectTests(unittest.TestCase):
    def test_tab_actions_land_on_the_tab_buttons(self):
        from agent.core import card_list_scan as cs

        for action_id, tab in (("idol-list-tab-support", "support"), ("idol-list-tab-produce", "produce")):
            with self.subTest(action=action_id):
                rect = _rect(action_id)
                self.assertTrue(_inside(rect, TAB_BUTTON[tab]), rect)
                x, y, w, h = rect
                tx, ty, tw, th = cs.TAB_TEXT_ROI[tab]
                self.assertTrue(tx <= x + w / 2 <= tx + tw and ty <= y + h / 2 <= ty + th)

    def test_scroll_actions_lie_on_the_grid_and_off_the_scrollbar(self):
        from agent.core import card_list_scan as cs
        from tests.maa_probe import rgb

        for action_id in ("idol-list-scroll-down", "idol-list-scroll-up"):
            rect = _rect(action_id)
            with self.subTest(action=action_id):
                self.assertTrue(_inside(rect, (cs.STRIP_X[0], cs.VIEWPORT[0], cs.STRIP_X[1] - cs.STRIP_X[0],
                                               cs.VIEWPORT[1] - cs.VIEWPORT[0])))
                self.assertLess(rect[0] + rect[2], cs.SCROLLBAR_X[0])
                for frame in LISTS_1008:                               # the list panel, not the dark page border
                    x, y, w, h = rect
                    self.assertGreater(rgb(frame)[y:y + h, x:x + w].mean(), 150, frame)


if __name__ == "__main__":
    unittest.main()
