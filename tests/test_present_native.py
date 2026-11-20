"""プレゼント: replay on the native unclaimed-list frames of 2026-10-07 (private, not committed).

Three captures exist (``external_evidence_final``, ``external_present_restored``, ``external_present_received_tab``); all three
show the 「未受け取り」 tab with 2 rows (the read-only tap on 「受け取り済」 did not switch). Variants built in memory from
the native pixels cover the other tab, a grey 一括受取 and a hidden row; the received tab, empty box, result dialog
and errors still have no native sample.
"""
from __future__ import annotations

import time
import unittest

from agent.core import present_claim as pc
from tests.maa_probe import NodeProbe, maa_ready, ocr_items, rgb

P = "dev_assets/private/supervised-tasks-20261007/"
FRAMES = (P + "external_evidence_final.png", P + "external_present_restored.png", P + "external_present_received_tab.png")
MISSION = "dev_assets/private/in_app_browser/20261001-daily-research/daily-v8-Mission-native.png"
IDOL_LIST = "dev_assets/private/recordings/wing-demo3-20261006-1643-02/frame-000008.png"
HOME = "dev_assets/private/supervised-tasks-20261007/daily_final.png"
ON = pc.PresentOptions(enabled=True)


def fresh():
    return pc.FrameRef("c" * 64, 1, time.monotonic())


def decide(page, image, options=ON):
    from agent.core import present_actions as pa

    bgr = image[:, :, ::-1]
    return pc.decide(page, options=options, page_guard=True, overlay=False, bulk_enabled=pa.bulk_enabled(bgr),
                     frame=fresh())


@unittest.skipUnless(maa_ready(*FRAMES, MISSION, IDOL_LIST, HOME), "需要 MaaFramework、OCR 模型与 2026-10-07 礼物箱原生帧")
class PresentNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = {f: ocr_items(f) for f in FRAMES}
        cls.images = {f: rgb(f) for f in FRAMES}

    def page(self, frame):
        image = self.images[frame]
        return pc.parse_page(self.items[frame], tab=pc.tab_selection(image)["selected"])

    def test_unclaimed_list_is_read_exactly(self):
        for frame in FRAMES:
            with self.subTest(frame=frame):
                page = self.page(frame)
                self.assertTrue(page.title and page.footer)
                self.assertEqual((page.tab, page.count, page.bulk_buttons, page.blockers), ("unclaimed", 2, 1, ()))
                self.assertEqual([(r["name"], r["quantity"], r["kind"], r["deadline"]) for r in page.rows], [
                    ("8.5thAnniversary特別10連ガシャチケット", 1, "gacha_ticket", "2027-03-01T01:59+09:00"),
                    ("シルバートレーニングチケット", 1, "training_ticket", "none")])
                self.assertEqual([r["y"] for r in page.rows], [171, 309])

    def test_tab_and_bulk_pixels(self):
        for frame in FRAMES:
            tabs = pc.tab_selection(self.images[frame])
            self.assertEqual(tabs["selected"], "unclaimed")
            self.assertGreaterEqual(tabs["unclaimed"], 1000)
            self.assertEqual(tabs["received"], 0)

    def test_enabled_option_claims_only_with_everything_proven(self):
        frame = FRAMES[0]
        decision = decide(self.page(frame), self.images[frame])
        self.assertEqual((decision.kind, decision.target, decision.rows, decision.input_authorized),
                         ("claim", "present-bulk-receive", 2, False))
        self.assertEqual(decide(self.page(frame), self.images[frame], pc.PresentOptions()).reasons, ("disabled",))

    def test_variants_from_native_pixels(self):
        frame = FRAMES[0]
        image = self.images[frame]
        # 1. other tab selected: swap the two label regions' pink.
        swapped = image.copy()
        (ux, uy, uw, uh), (rx, ry, rw, rh) = pc.LAYOUT["tab_text_roi"]["unclaimed"], pc.LAYOUT["tab_text_roi"]["received"]
        swapped[uy:uy + uh, ux:ux + uw] = 70
        swapped[ry + 4:ry + 4 + 30, rx + 10:rx + 130] = (253, 100, 180)
        page = pc.parse_page(self.items[frame], tab=pc.tab_selection(swapped)["selected"])
        self.assertEqual(decide(page, swapped).reasons, ("wrong_tab:received",))
        # 2. grey 一括受取: nothing to press, a skip.
        grey = image.copy()
        x, y, w, h = pc.LAYOUT["bulk_roi"]
        grey[y:y + h, x:x + w] = grey[y:y + h, x:x + w].mean(axis=2, keepdims=True).astype(grey.dtype)
        self.assertEqual(decide(self.page(frame), grey).reasons, ("bulk_disabled",))
        # 3.-5. unread rows, counter or name lines never stop an enabled 一括受取.
        variants = ([i for i in self.items[frame] if not (i["text"] == "受け取る" and i["box"][1] > 250)],
                    [i for i in self.items[frame] if i["text"] != "2件"],
                    [i for i in self.items[frame] if "ガシャチケット" not in i["text"]])
        for items in variants:
            self.assertEqual(decide(pc.parse_page(items, tab="unclaimed"), image).kind, "claim")

    def test_other_native_pages_are_not_the_present_box(self):
        for frame in (MISSION, IDOL_LIST, HOME):
            with self.subTest(frame=frame):
                image = rgb(frame)
                page = pc.parse_page(ocr_items(frame), tab=pc.tab_selection(image)["selected"])
                self.assertFalse(page.title and page.footer)
                self.assertEqual(decide(page, image).kind, "stop")


@unittest.skipUnless(maa_ready(*FRAMES, MISSION, IDOL_LIST, HOME), "需要 MaaFramework、私有模板包、OCR 模型与原生帧")
class PresentGuardNodeTests(unittest.TestCase):
    """The Pipeline guard nodes of present.json, run by Maa itself on native frames."""

    GUARDS = ("SC.Present.Guard.Title", "SC.Present.Guard.Footer", "SC.Present.Color.UnclaimedTab",
              "SC.Present.Guard.Bulk", "SC.Common.Color.BackUndimmed")

    @classmethod
    def setUpClass(cls):
        cls.probe = NodeProbe()

    def test_guards_hit_on_the_present_list(self):
        for frame in FRAMES:
            for node in self.GUARDS:
                with self.subTest(frame=frame, node=node):
                    self.assertTrue(self.probe.hits(frame, node))

    def test_page_guard_misses_elsewhere(self):
        for frame in (MISSION, IDOL_LIST, HOME):
            with self.subTest(frame=frame):
                self.assertFalse(self.probe.hits(frame, "SC.Present.Guard.Title")
                                 and self.probe.hits(frame, "SC.Present.Guard.Footer")
                                 and self.probe.hits(frame, "SC.Present.Color.UnclaimedTab"))
                self.assertFalse(self.probe.hits(frame, "SC.Present.Guard.Footer"))


R = "dev_assets/private/fes-match-reader-20261007/"
OPENED_POSITIVE = (R + "present_unclaimed_initial.png", R + "present_received_tab.png", R + "present_empty_after_claim.png",
                   R + "present_received_after_claim.png")
OPENED_NEGATIVE = (R + "home_after_present_settled.png", R + "home_before_claim.png", R + "present_bulk_confirm.png",
                   R + "present_bulk_receipt.png")


@unittest.skipUnless(maa_ready(*OPENED_POSITIVE, *OPENED_NEGATIVE), "需要 MaaFramework、OCR 模型与 2026-10-07 礼物箱原生全过程帧")
class PresentOpenedGuardTests(unittest.TestCase):
    """the post-click guard of SC.Present.OpenIntent on the native box/home/dialog frames."""

    @classmethod
    def setUpClass(cls):
        cls.probe = NodeProbe()

    def test_opened_hits_on_either_tab_and_the_empty_box(self):
        for frame in OPENED_POSITIVE:
            with self.subTest(frame=frame):
                self.assertTrue(self.probe.hits(frame, "SC.Present.Opened"))

    def test_opened_misses_on_home_and_dialogs(self):
        for frame in OPENED_NEGATIVE:
            with self.subTest(frame=frame):
                self.assertFalse(self.probe.hits(frame, "SC.Present.Opened"))




L = "dev_assets/private/sample-live-20261011/"
LIVE_LIST, LIVE_RECEIPT, LIVE_EMPTY = L + "31-present-stopped.png", L + "32-present-inclusive-stopped.png", L + "33-present-empty.png"


@unittest.skipUnless(maa_ready(LIVE_LIST, LIVE_RECEIPT, LIVE_EMPTY), "需要 MaaFramework、OCR 模型与 2026-10-11 礼物箱原生帧")
class PresentGenericReceiptNativeTests(unittest.TestCase):
    """2026-10-11 live: three free rows incl. 「283プロのタオル」, the bulk receipt says 「プロデュースアイテム ×1」."""

    def test_towel_list_claims_and_generic_receipt_is_received(self):
        image = rgb(LIVE_LIST)
        before = pc.parse_page(ocr_items(LIVE_LIST), tab=pc.tab_selection(image)["selected"])
        self.assertEqual((before.tab, before.count, before.blockers), ("unclaimed", 3, ()))
        self.assertEqual([(r["kind"], r["quantity"]) for r in before.rows],
                         [("gacha_ticket", 1), ("training_ticket", 1), ("produce_item", 1)])
        decision = decide(before, image)
        self.assertEqual((decision.kind, decision.target), ("claim", "present-bulk-receive"), decision.reasons)
        receipt_items = ocr_items(LIVE_RECEIPT)
        self.assertEqual(pc.classify_result(receipt_items), ("receipt", []))
        self.assertTrue(pc.receipt_lines(receipt_items))
        after = pc.parse_page(ocr_items(LIVE_EMPTY), tab=pc.tab_selection(rgb(LIVE_EMPTY))["selected"])
        self.assertEqual(pc.confirm(before, after, "receipt"), "received")


FAILED_20261027 = "dev_assets/private/run_logs/diagnostics/failures/20261027T113506230840Z.png"


@unittest.skipUnless(maa_ready(FAILED_20261027), "需要 MaaFramework、OCR 模型与 2026-10-27 日常失败帧")
class PresentFailedRun20261027Tests(unittest.TestCase):
    """Run ui-20261027-223446 stopped on 「ダンス応用知識本 ×1」 (a セレクトプレゼント reward, unknown kind)."""

    def test_unknown_row_with_an_enabled_bulk_button_is_claimed(self):
        image = rgb(FAILED_20261027)
        page = pc.parse_page(ocr_items(FAILED_20261027), tab=pc.tab_selection(image)["selected"])
        self.assertEqual((page.title, page.tab, page.count, page.bulk_buttons, page.blockers), (True, "unclaimed", 5, 1, ()))
        self.assertEqual([(r["name"], r["kind"]) for r in page.rows][0], ("ダンス応用知識本", None))
        decision = decide(page, image)
        self.assertEqual((decision.kind, decision.reasons, decision.target), ("claim", ("bulk_receive",), "present-bulk-receive"))


if __name__ == "__main__":
    unittest.main()


THANKS = "dev_assets/private/live-20261031/present-maintenance-thanks.png"   # daily 10-31 04:34 JST, stopped as maintenance


@unittest.skipUnless(maa_ready(THANKS), "需要 MaaFramework、OCR 模型与 2026-10-31 礼物箱原生帧")
class PresentMaintenanceThanksTests(unittest.TestCase):
    def test_apology_row_description_is_not_a_game_error(self):
        image = rgb(THANKS)
        page = pc.parse_page(ocr_items(THANKS), tab=pc.tab_selection(image)["selected"])
        self.assertEqual(page.blockers, ())
        self.assertIn(("フェザージュエル", 100), [(r["name"], r["quantity"]) for r in page.rows])
        self.assertEqual(decide(page, image).kind, "claim")
