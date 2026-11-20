"""プレゼント bulk receive: synthetic contract in the native unclaimed-list geometry (native replay: test_present_native)."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from agent.core import present_claim as pc

ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=pc.JST)


def ocr(text, x, y, w=160, h=26):
    return {"text": text, "box": [x, y, w, h], "score": 0.9}


def page_items(*names, bulk=True, title=True, extra=(), count=None, footer=True):
    """OCR items in the native unclaimed-list geometry (external_evidence_final.png, 2026-10-07): row pitch 138,
    name line 45 px above the 「受け取る」 centre, 「未受取プレゼント N件」 counter, fixed footer, 一括受取."""
    items = [ocr("プレゼント", 49, 18, 127, 36)] if title else []
    for index, name in enumerate(names):
        dy = 138 * index
        items += [ocr(name, 394, 113 + dy, 360, 27), ocr("ログインボーナスのプレゼントです", 396, 152 + dy, 286, 18),
                  ocr("獲得日時", 397, 203 + dy, 83, 23), ocr("受取期限", 709, 201 + dy, 80, 26),
                  ocr("期限なし", 798, 204 + dy, 78, 23), ocr("受け取る", 1061, 153 + dy, 127, 37)]
    items += [ocr("未受取プレゼント", 1010, 578, 148, 22), ocr(f"{len(names) if count is None else count}件", 1209, 575, 44, 27)]
    if bulk:
        items.append(ocr("一括受取", 1132, 641, 110, 39))
    if footer:
        items += [ocr("期限が切れたプレゼントは自動的に削除されます。", 293, 637, 408, 23),
                  ocr("また、一度に受け取れるプレゼントは100件までです。", 293, 660, 447, 24)]
    return items + list(extra)


def frame(seq=1, sha="b" * 64, at=None):
    return pc.FrameRef(sha, seq, time.monotonic() if at is None else at)


ON = pc.PresentOptions(enabled=True)


def decide(items, options=ON, **kw):
    args = dict(options=options, page_guard=True, overlay=False, bulk_enabled=True, frame=frame())
    args.update(kw)
    return pc.decide(pc.parse_page(items, tab=args.pop("tab", "unclaimed")), **args)


class ParseAndDecideTests(unittest.TestCase):
    def test_free_rows_claim(self):
        page = pc.parse_page(page_items("フェザージュエル×300", "ゴールドトレーニングチケット×1"))
        self.assertTrue(page.title)
        self.assertEqual([r["kind"] for r in page.rows], ["jewel", "training_ticket"])
        self.assertEqual(page.bulk_buttons, 1)
        self.assertEqual(page.blockers, ())
        decision = decide(page_items("フェザージュエル×300", "マニー×10000"))
        self.assertEqual((decision.kind, decision.target, decision.input_authorized), ("claim", "present-bulk-receive", False))

    def test_default_options_never_claim(self):
        decision = decide(page_items("フェザージュエル×300"), options=pc.PresentOptions())
        self.assertEqual(decision.kind, "stop")
        self.assertIn("disabled", decision.reasons)

    def test_only_game_errors_stop(self):
        for word, reason in (("通信エラーが発生しました", "blocked:error"), ("メンテナンス中です", "blocked:maintenance")):
            with self.subTest(word):
                decision = decide(page_items("フェザージュエル×300", extra=[ocr(word, 400, 400)]))
                self.assertEqual(decision.kind, "stop")
                self.assertIn(reason, decision.reasons)
        # 2026-10-31: a jewel row described 「メンテナンス協力のお礼です」 stopped the daily run as a maintenance error.
        thanks = page_items("フェザージュエル×100")
        thanks[2] = ocr("メンテナンス協力のお礼です", 397, 152, 230, 18)
        self.assertEqual(pc.parse_page(thanks).blockers, ())
        self.assertEqual(decide(thanks).kind, "claim")
        dialog = decide(page_items("フェザージュエル×100", extra=[ocr("メンテナンス中です", 520, 152)]))   # centred, not a row line
        self.assertIn("blocked:maintenance", dialog.reasons)
        # Item names and descriptions are not warnings (2026-10-27: a セレクトプレゼント row stopped the daily run).
        for word in ("所持上限に達しています", "交換所へ", "有償ジュエル", "好きなアイテムを選択できます", "プロデュースで使用できます"):
            with self.subTest(word):
                self.assertEqual(decide(page_items("フェザージュエル×300", extra=[ocr(word, 400, 400)])).kind, "claim")

    def test_any_rows_are_claimed_with_an_enabled_bulk_button(self):
        """有东西就直接点一键领取. The rows of the failed daily run (ui-20261027-223446) now claim."""
        failed_run = decide(page_items("8.5thAnniversary特別10連ガシャチケット×1", "フェザージュエル×300",
                                       "ダンス応用知識本×1", count=5))
        self.assertEqual((failed_run.kind, failed_run.reasons), ("claim", ("bulk_receive",)))
        for items in (page_items("SSRセレクトチケット×1"), page_items("謎のアイテム×1"), page_items("謎"),
                      page_items("マニー×1", count=120), page_items("マニー×1", "フェザージュエル×10", count=1),
                      page_items("マニー×1", footer=False),
                      [i for i in page_items("マニー×1") if not i["text"].endswith("件")]):
            with self.subTest(items=[i["text"] for i in items][1:3]):
                self.assertEqual(decide(items).kind, "claim")

    def test_guards(self):
        items = page_items("フェザージュエル×300")
        cases = [({"page_guard": False}, "present_page_not_proven"), ({"overlay": True}, "overlay_present"),
                 ({"bulk_enabled": None}, "bulk_button_unread"), ({"frame": None}, "frame_unbound"),
                 ({"tab": None}, "unclaimed_tab_not_selected"), ({"tab": "received"}, "wrong_tab:received")]
        for kw, reason in cases:
            with self.subTest(reason):
                decision = decide(items, **kw)
                self.assertEqual(decision.kind, "stop")
                self.assertIn(reason, decision.reasons)
        self.assertIn("present_page_not_proven", decide(page_items("フェザージュエル", title=False)).reasons)
        self.assertEqual(decide(page_items("フェザージュエル", bulk=False)).reasons, ("bulk_button_count_0",))
        two = page_items("フェザージュエル", extra=[ocr("一括受取", 1100, 680, 80, 20)])
        self.assertEqual(decide(two).reasons, ("bulk_button_count_2",))
        page = pc.parse_page(page_items("マニー×1000", "シルバートレーニングチケット×1"))
        self.assertEqual([(r["name"], r["quantity"], r["deadline"]) for r in page.rows],
                         [("マニー", 1000, "none"), ("シルバートレーニングチケット", 1, "none")])

    def test_grey_or_missing_bulk_button_is_a_skip(self):
        empty = [ocr("プレゼント", 60, 24, 100, 28), ocr("受け取れるプレゼントはありません", 450, 330, 380, 30)]
        self.assertEqual(decide(empty, bulk_enabled=False).reasons, ("box_empty",))
        self.assertEqual(decide([ocr("プレゼント", 60, 24, 100, 28)], bulk_enabled=False).kind, "empty")
        grey = decide(page_items("マニー×100"), bulk_enabled=False)
        self.assertEqual((grey.kind, grey.reasons), ("empty", ("bulk_disabled",)))
        mixed = page_items("マニー×100", extra=[ocr("プレゼントはありません", 450, 600, 300, 30)])
        self.assertEqual(decide(mixed).kind, "claim")


class FrameAndResultTests(unittest.TestCase):
    def test_produce_items_are_a_known_kind(self):
        before = pc.parse_page(page_items("283プロのタオル×1", "8.5thAnniversary特別10連ガシャチケット×1"), tab="unclaimed")
        self.assertEqual([r["kind"] for r in before.rows], ["produce_item", "gacha_ticket"])

    def test_frame_binding(self):
        decision = decide(page_items("マニー×100"))
        f = decision.frame
        self.assertIsNone(pc.frame_matches(decision, f, f.captured_at + 1))
        self.assertEqual(pc.frame_matches(decision, pc.FrameRef("c" * 64, f.seq, f.captured_at), f.captured_at), "frame_changed")
        self.assertEqual(pc.frame_matches(decision, f, f.captured_at + 6), "frame_stale")
        self.assertEqual(pc.frame_matches(decide(page_items("謎"), bulk_enabled=False), f, f.captured_at),
                         "no_claim_decision")

    def test_result_classes(self):
        self.assertEqual(pc.classify_result([ocr("プレゼントを受け取りました", 400, 300)]), ("receipt", []))
        self.assertEqual(pc.classify_result([ocr("プレゼントを受け取りました", 400, 300), ocr("SSRセレクトチケット×1", 554, 341),
                                             ocr("プロデュースで使用できます", 400, 380)]), ("receipt", []))
        self.assertEqual(pc.classify_result([ocr("所持上限のため受け取れませんでした", 400, 300)])[0], "overflow")
        partial = pc.classify_result([ocr("プレゼントを受け取りました", 400, 260), ocr("所持上限のため一部受け取れません", 400, 300)])
        self.assertEqual(partial, ("receipt", ["limit_warning", "cannot_receive"]))
        self.assertEqual(pc.classify_result([ocr("エラーが発生しました(1012)", 400, 300)])[0], "error")
        self.assertEqual(pc.classify_result([ocr("どれを選択しますか", 400, 300)]), ("uncertain", ["no_positive_result_text"]))
        self.assertEqual(pc.classify_result([]), ("uncertain", ["no_positive_result_text"]))

    def test_confirm(self):
        before = pc.parse_page(page_items("マニー×1", "フェザージュエル×1"), tab="unclaimed")
        empty = pc.parse_page([ocr("プレゼント", 60, 24, 100, 28), ocr("プレゼントはありません", 450, 330, 300, 30)],
                              tab="unclaimed")
        self.assertEqual(pc.confirm(before, empty, "receipt"), "received")
        self.assertEqual(pc.confirm(before, pc.parse_page(page_items("マニー×1"), tab="received"), "receipt"), "uncertain")
        self.assertEqual(pc.confirm(before, pc.parse_page(page_items("マニー×1"), tab="unclaimed"), "receipt"),
                         "received_partial")
        self.assertEqual(pc.confirm(before, before, "receipt"), "uncertain")
        described = pc.parse_page(page_items("SSRセレクトチケット×1", extra=[ocr("好きなアイテムを選択できます", 400, 152)]),
                                  tab="unclaimed")
        self.assertEqual(pc.confirm(pc.parse_page(page_items("マニー×1", "SSRセレクトチケット×1"), tab="unclaimed"),
                                    described, "receipt"), "received_partial")
        self.assertEqual(pc.confirm(before, empty, "overflow"), "overflow")
        self.assertEqual(pc.confirm(before, empty, "weird"), "uncertain")

    def test_options_validation(self):
        self.assertEqual(pc.PresentOptions.from_params({}), pc.PresentOptions())
        retired = {"enabled": True, "max_claims_per_day": 1, "allow_choice_items": False, "stop_on_unknown_item": True,
                   "max_scroll_steps": 30, "verify_all_rows": False}
        self.assertEqual(pc.PresentOptions.from_params(retired), ON)   # an older override still loads
        for bad in ({"enabled": "yes"}, {"x": 1}):
            with self.subTest(bad), self.assertRaises(ValueError):
                pc.PresentOptions.from_params(bad)

    def test_daily_cycle_boundary(self):
        self.assertEqual(pc.cycle_id(dt.datetime(2026, 10, 6, 4, 59, tzinfo=pc.JST)), "2026-10-05")
        self.assertEqual(pc.cycle_id(dt.datetime(2026, 10, 6, 5, 0, tzinfo=pc.JST)), "2026-10-06")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_pending_persists_and_never_blocks_a_later_present_press(self):
        ledger = pc.PresentLedger(self.path)
        cycle = pc.cycle_id(NOW)
        record = ledger.begin(cycle, decide(page_items("マニー×1")), NOW)
        self.assertEqual(record["state"], "pending")
        reloaded = pc.PresentLedger(self.path)
        self.assertEqual(reloaded.block_reason(cycle), "pending_today")
        later = reloaded.begin(cycle, decide(page_items("マニー×1")), NOW)    # Press whenever enabled
        self.assertEqual(later["state"], "pending")
        reloaded.transition(later["id"], "refused", NOW, "live_input_closed")
        reloaded.transition(record["id"], "uncertain", NOW, "timeout")
        self.assertEqual(pc.PresentLedger(self.path).block_reason(cycle), "uncertain_today")
        with self.assertRaises(ValueError):  # uncertain is final; never cleared by a new screenshot
            reloaded.transition(record["id"], "received", NOW)
        self.assertIsNone(reloaded.block_reason("2026-10-07"))

    def test_refused_does_not_block_or_count(self):
        ledger = pc.PresentLedger(self.path)
        cycle = pc.cycle_id(NOW)
        record = ledger.begin(cycle, decide(page_items("マニー×1")), NOW)
        ledger.transition(record["id"], "refused", NOW, "live_input_closed")
        self.assertIsNone(ledger.block_reason(cycle))
        self.assertEqual(ledger.claims_today(cycle), 0)
        second = ledger.begin(cycle, decide(page_items("マニー×1")), NOW)
        ledger.transition(second["id"], "receipt_observed", NOW)
        ledger.transition(second["id"], "received", NOW)
        self.assertEqual(ledger.claims_today(cycle), 1)
        self.assertIsNone(ledger.block_reason(cycle))

    def test_only_claim_decisions_begin_and_corrupt_file_raises(self):
        ledger = pc.PresentLedger(self.path)
        with self.assertRaises(ValueError):
            ledger.begin("2026-10-06", decide(page_items("謎"), bulk_enabled=False), NOW)
        self.path.write_text(json.dumps({"version": 1, "records": [{"id": "x", "cycle": "c", "state": "done"}]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            pc.PresentLedger(self.path)


def _detail(items, hit=True):
    return SimpleNamespace(hit=hit, all_results=[SimpleNamespace(text=i["text"], box=i["box"], score=0.9) for i in items])


class FakeContext:
    def __init__(self, image, page, overlay=False):
        self.tasker = SimpleNamespace(controller=SimpleNamespace(cached_image=image))
        self.page, self.overlay, self.attach = page, overlay, {}

    def run_recognition(self, node, image):
        if node.endswith("Overlay"):
            return _detail([]) if self.overlay else None
        return _detail(self.page)

    def get_node_data(self, name):
        return {"attach": self.attach}


def pink_page_image(bulk=True):
    image = np.full((720, 1280, 3), 220, np.uint8)
    x, y, w, h = pc.LAYOUT["tab_text_roi"]["unclaimed"]
    image[y + 8:y + 40, x + 10:x + 130] = (160, 80, 240)  # BGR of RGB (240,80,160): selected 未受け取り label
    if bulk:
        x, y, w, h = pc.LAYOUT["bulk_roi"]
        image[y:y + h, x:x + w] = (160, 80, 240)
    return image


class ActionTests(unittest.TestCase):
    """Offline action chain: the Intent action never sends; it writes pending then refused."""

    def setUp(self):
        from agent.core import present_actions as pa
        from agent.core.task_actions import ReportSink

        self.pa = pa
        self.tmp = tempfile.TemporaryDirectory()
        self.sink = ReportSink()
        self.rt = pa.PresentRuntime(self.sink, Path(self.tmp.name) / "ledger.json", clock=lambda: NOW)
        self.argv = SimpleNamespace(custom_action_param="{}", node_name="n")

    def tearDown(self):
        self.tmp.cleanup()

    def begin(self, context, enabled=True, **options):
        context.attach = {"enabled": enabled, **options}
        argv = SimpleNamespace(custom_action_param=json.dumps({"config_nodes": ["SC.Present.Config"]}), node_name="n")
        return self.pa.PresentBeginAction(self.rt).run(context, argv)

    def test_live_input_constant_is_closed(self):
        self.assertFalse(self.pa.PRESENT_LIVE_INPUT)

    def test_claim_decision_is_refused_not_sent(self):
        context = FakeContext(pink_page_image(), page_items("フェザージュエル×300"))
        self.assertTrue(self.begin(context))
        self.assertTrue(self.pa.PresentDecideAction(self.rt).run(context, self.argv))
        self.assertFalse(self.pa.PresentIntentAction(self.rt).run(context, self.argv))
        self.assertEqual(self.rt.record["state"], "refused")
        self.assertEqual(self.rt.record["history"], ["pending", "refused"])
        self.assertEqual(self.sink.drain()[-1]["outcome"], "not_sent_live_input_closed")
        self.assertFalse(self.pa.PresentStopAction(self.rt).run(context, self.argv))

    def test_changed_frame_rejects_intent_without_record(self):
        context = FakeContext(pink_page_image(), page_items("フェザージュエル×300"))
        self.begin(context)
        self.assertTrue(self.pa.PresentDecideAction(self.rt).run(context, self.argv))
        changed = pink_page_image()
        changed[0, 0] = 0
        context.tasker.controller.cached_image = changed
        self.assertFalse(self.pa.PresentIntentAction(self.rt).run(context, self.argv))
        self.assertIsNone(self.rt.record)
        self.assertEqual(self.sink.drain()[-1]["reason"], "frame_changed")

    def test_grey_bulk_button_is_a_skip_and_disabled_option_stops(self):
        context = FakeContext(pink_page_image(bulk=False), page_items("フェザージュエル×300"))
        self.begin(context)
        self.assertFalse(self.pa.PresentDecideAction(self.rt).run(context, self.argv))
        self.assertEqual(self.rt.decision.reasons, ("bulk_disabled",))
        self.assertTrue(self.pa.PresentStopAction(self.rt).run(context, self.argv))
        self.assertEqual(self.rt.outcome, "bulk_disabled")
        self.assertFalse(self.begin(context, enabled=False))
        self.assertEqual(self.sink.drain()[-1]["outcome"], "disabled")

    def test_empty_box_stop_is_success(self):
        empty = [ocr("プレゼント", 60, 24, 100, 28), ocr("プレゼントはありません", 450, 330, 300, 30)]
        context = FakeContext(pink_page_image(bulk=False), empty)
        self.begin(context)
        self.assertFalse(self.pa.PresentDecideAction(self.rt).run(context, self.argv))
        self.assertTrue(self.pa.PresentStopAction(self.rt).run(context, self.argv))
        self.assertEqual(self.rt.outcome, "box_empty")

    def test_earlier_attempts_today_do_not_stop_a_press(self):
        """2026-10-27: neither a received nor an uncertain record of the same day stops a later enabled 一括受取."""
        context = FakeContext(pink_page_image(), page_items("フェザージュエル×300", "ダンス応用知識本×1", count=5))
        self.begin(context, max_claims_per_day=1, stop_on_unknown_item=True)    # retired keys of an older override
        ledger = pc.PresentLedger(self.rt.ledger_path)
        cycle = pc.cycle_id(NOW)
        done = ledger.begin(cycle, decide(page_items("マニー×1")), NOW)
        ledger.transition(done["id"], "receipt_observed", NOW)
        ledger.transition(done["id"], "received", NOW)
        open_record = ledger.begin(cycle, decide(page_items("マニー×1")), NOW)
        ledger.transition(open_record["id"], "uncertain", NOW, "timeout")
        self.rt.ledger = ledger
        self.assertTrue(self.pa.PresentDecideAction(self.rt).run(context, self.argv))
        self.assertEqual(self.rt.decision.kind, "claim")
        self.assertFalse(self.pa.PresentIntentAction(self.rt).run(context, self.argv))   # offline: pending -> refused
        self.assertEqual(self.rt.record["history"], ["pending", "refused"])


class PresentFreshClickTests(unittest.TestCase):
    setUp = ActionTests.setUp
    tearDown = ActionTests.tearDown
    begin = ActionTests.begin

    def prepare(self):
        self.rt.input_enabled = True
        context = FakeContext(pink_page_image(), page_items("フェザージュエル×300"))
        self.assertTrue(self.begin(context))
        self.assertTrue(self.pa.PresentDecideAction(self.rt).run(context, self.argv))
        self.assertTrue(self.pa.PresentIntentAction(self.rt).run(context, self.argv))
        return context

    def ready(self, context, image):
        argv = SimpleNamespace(custom_recognition_param=json.dumps({"stage": "bulk"}), image=image)
        return self.pa.PresentReadyRecognition(self.rt).analyze(context, argv)

    def test_new_frame_with_only_background_animation_keeps_the_read_items(self):
        context = self.prepare()
        image = context.tasker.controller.cached_image.copy()
        image[550, 500] = 0
        self.assertIsNotNone(self.ready(context, image))

    def test_changed_item_text_cannot_use_the_old_read(self):
        context = self.prepare()
        image = context.tasker.controller.cached_image.copy()
        image[self.rt.page_before.rows[0]["y"] - 40, 500] = 0
        self.assertIsNone(self.ready(context, image))
        self.assertEqual(self.rt.phase, "bulk_ready")

    def test_changed_count_cannot_use_the_old_read(self):
        context = self.prepare()
        context.page = page_items("フェザージュエル×300", count=2)
        self.assertIsNone(self.ready(context, context.tasker.controller.cached_image))


def scrolled_items(names, offset, count):
    """OCR items of a list scrolled by ``offset`` px (row pitch 138); every row has a distinct 獲得日時."""
    items = [ocr("プレゼント", 49, 18, 127, 36)]
    for index, name in enumerate(names):
        by = 171 + 138 * index - offset
        items += [ocr(name, 394, by - 58, 360, 27), ocr("獲得日時", 397, by + 32, 83, 23),
                  ocr(f"2026/10/{index + 1:02d}12:43", 493, by + 32, 180, 23), ocr("受取期限", 709, by + 30, 80, 26),
                  ocr("期限なし", 798, by + 33, 78, 23), ocr("受け取る", 1061, by - 18, 127, 37)]
    items += [ocr("未受取プレゼント", 1010, 578, 148, 22), ocr(f"{count}件", 1209, 575, 44, 27),
              ocr("一括受取", 1132, 641, 110, 39), ocr("期限が切れたプレゼントは自動的に削除されます。", 293, 637, 408, 23),
              ocr("また、一度に受け取れるプレゼントは100件までです。", 293, 660, 447, 24)]
    return items


def thumb_image(top, bottom, bulk=True):
    image = pink_page_image(bulk)
    image[109:564, 1242] = (135, 121, 131)
    image[top:bottom + 1, 1242] = (255, 255, 255)
    return image


NAMES = [f"フェザージュエル×{100 + i}" for i in range(8)]


def decide_page(page):
    return pc.decide(page, options=ON, page_guard=True, overlay=False, bulk_enabled=True, frame=frame())


class PresentScanTests(unittest.TestCase):
    """2026-10-10: a box longer than one screen (native: 12 items, 3 complete rows) is read completely first."""

    def page(self, offset, count=8, names=NAMES):
        return pc.parse_page(scrolled_items(names, offset, count), tab="unclaimed")

    def test_overlapping_pages_build_the_complete_list(self):
        scan = pc.PresentScan(8, 10)
        self.assertTrue(scan.add(self.page(0), (111, 200)))
        self.assertEqual([r["quantity"] for r in scan.rows], [100, 101, 102])
        self.assertTrue(scan.wants_scroll())
        self.assertTrue(scan.add(self.page(276), (180, 270)))      # rows 2..4 complete, row 2 overlaps
        self.assertTrue(scan.add(self.page(552), (300, 450)))      # rows 4..6, row 4 overlaps
        self.assertTrue(scan.add(self.page(690), (400, 563)))      # rows 5..7, rows 5..6 overlap
        self.assertTrue(scan.complete)
        self.assertEqual([r["quantity"] for r in scan.rows], [100 + i for i in range(8)])
        self.assertEqual(decide_page(scan.merged_page(self.page(690))).kind, "claim")

    def test_every_gap_or_doubt_stops(self):
        cases = {"list_start_not_observed": [(0, (150, 250))],
                 "scroll_no_effect": [(0, (111, 200)), (276, (111, 200))],
                 "scroll_direction_changed": [(0, (111, 200)), (276, (105, 199))],
                 "overlap_lost": [(0, (111, 200)), (552, (300, 400))],
                 "scrollbar_unread": [(0, None)]}
        for reason, steps in cases.items():
            with self.subTest(reason=reason):
                scan = pc.PresentScan(8, 10)
                for offset, thumb in steps:
                    scan.add(self.page(offset), thumb)
                self.assertEqual(scan.reason, reason)
        scan = pc.PresentScan(8, 10)
        scan.add(self.page(0), (111, 200))
        self.assertFalse(scan.add(self.page(276, count=7), (180, 270)))
        self.assertEqual(scan.reason, "count_changed")
        scan = pc.PresentScan(9, 10)                                 # the end is reached with fewer rows than counted
        scan.add(self.page(0, count=9), (111, 200))
        self.assertFalse(scan.add(self.page(276, count=9), (300, 563)))
        self.assertEqual(scan.reason, "count_rows_mismatch")
        scan = pc.PresentScan(8, 1)
        scan.add(self.page(0), (111, 200))
        scan.steps = 1
        self.assertFalse(scan.wants_scroll())
        self.assertEqual(scan.reason, "scroll_budget_exhausted")

    def test_identical_rows_make_the_overlap_ambiguous(self):
        same = ["フェザージュエル×300"] * 8

        def items(offset):
            return [i for i in scrolled_items(same, offset, 8) if not i["text"].startswith("2026/")]
        scan = pc.PresentScan(8, 10)
        scan.add(pc.parse_page(items(0), tab="unclaimed"), (111, 200))
        self.assertFalse(scan.add(pc.parse_page(items(276), tab="unclaimed"), (180, 270)))
        self.assertEqual(scan.reason, "overlap_ambiguous")

    def test_thumb_reading_and_receipt_lines(self):
        self.assertEqual(pc.scroll_thumb(thumb_image(111, 237)[:, :, ::-1]), (111, 237))
        self.assertIsNone(pc.scroll_thumb(pink_page_image()[:, :, ::-1]))
        unseen = [ocr("ガシャチケット×2", 554, 300), ocr("トレーニングチケット×4", 554, 341)]
        self.assertEqual(pc.receipt_lines(unseen)[0], {"name": "ガシャチケット", "quantity": 2, "kind": "gacha_ticket"})
        before = pc.parse_page(page_items("マニー×1", count=11), tab="unclaimed")
        after = pc.parse_page(page_items("フェザージュエル×1", count=1), tab="unclaimed")
        self.assertEqual(pc.confirm(before, after, "receipt"), "received_partial")


class PresentScanActionTests(unittest.TestCase):
    setUp = ActionTests.setUp
    tearDown = ActionTests.tearDown
    begin = ActionTests.begin

    def ready(self, context, stage):
        argv = SimpleNamespace(custom_recognition_param=json.dumps({"stage": stage}),
                               image=context.tasker.controller.cached_image)
        return self.pa.PresentReadyRecognition(self.rt).analyze(context, argv)

    def test_long_box_claims_by_default_without_scrolling(self):
        # 2026-10-12 user decision: 一括受取 by default; the wheel action is never needed.
        context = FakeContext(thumb_image(111, 200), scrolled_items(NAMES, 0, 8))
        self.assertTrue(self.begin(context))
        self.assertTrue(self.pa.PresentDecideAction(self.rt).run(context, self.argv))
        self.assertEqual((self.rt.decision.kind, self.rt.phase, self.rt.scan), ("claim", "decided", None))

    def test_retired_full_list_option_no_longer_scans(self):
        context = FakeContext(thumb_image(111, 200), scrolled_items(NAMES, 0, 8))
        self.assertTrue(self.begin(context, verify_all_rows=True, max_scroll_steps=30))
        self.assertTrue(self.pa.PresentDecideAction(self.rt).run(context, self.argv))
        self.assertEqual((self.rt.decision.kind, self.rt.phase), ("claim", "decided"))
        self.assertIsNone(self.ready(context, "scroll"))


class PresentPipelineContractTests(unittest.TestCase):
    def setUp(self):
        self.nodes = json.loads((ROOT / "assets/resource/base/pipeline/present.json").read_text(encoding="utf-8"))

    def test_every_input_node_is_disabled_and_the_option_is_off(self):
        # Entry now runs the real flow (Start/Route/Page/Intent); every Click stays enabled:false and the
        # claim sender stays off (PRESENT_LIVE_INPUT False), so a run observes and stops without input.
        entry = self.nodes["SC.Present.Entry"]
        self.assertEqual(entry["next"], ["SC.Present.Start"])
        clicks = {name for name, node in self.nodes.items() if node.get("action", "DoNothing") != "DoNothing"
                  and node.get("action") != "Custom"}
        self.assertEqual(clicks, {"SC.Present.OpenIntent", "SC.Present.UnclaimedTab", "SC.Present.BulkClick",
                                  "SC.Present.ConfirmClick", "SC.Present.ResultClose", "SC.Present.Return",
                                  "SC.Present.ScrollDown"})
        for name, node in self.nodes.items():
            self.assertTrue(name.startswith("SC.Present."), name)
            if name in clicks:
                self.assertEqual(node["action"], "Click", name)
                self.assertIs(node.get("enabled"), False, name)
                # one-shot clicks; the wheel is bounded by max_scroll_steps (<= 60) in the Agent
                self.assertEqual(node.get("max_hit"), 60 if name == "SC.Present.ScrollDown" else 1, name)
                self.assertTrue(node.get("on_error"), name)
        self.assertEqual(self.nodes["SC.Present.Config"]["attach"]["enabled"], False)
        from agent.core import present_actions
        self.assertIs(present_actions.PRESENT_LIVE_INPUT, False)

    def test_open_click_has_a_positive_post_guard(self):
        # check_project requires a positive guard after OpenIntent before returning to Route.
        self.assertEqual(self.nodes["SC.Present.OpenIntent"]["next"], ["SC.Present.Opened"])
        opened = self.nodes["SC.Present.Opened"]
        self.assertEqual((opened["recognition"], opened["next"], opened["on_error"]),
                         ("Or", ["SC.Present.Route"], ["SC.Present.Stop.Open"]))
        self.assertIn("SC.Loading.Present", opened["any_of"])
        page = next(i for i in opened["any_of"] if isinstance(i, dict))
        self.assertEqual(page["all_of"][:2], ["SC.Present.Guard.Title", "SC.Common.Color.BackUndimmed"])
        self.assertEqual(page["all_of"][2]["any_of"], ["SC.Present.Color.UnclaimedTab", "SC.Present.Color.ReceivedTab"])
        self.assertNotIn("action", opened)

    def test_intent_is_guarded_and_has_recovery(self):
        intent = self.nodes["SC.Present.ClaimIntent"]
        self.assertEqual(intent["recognition"], "And")
        self.assertIn("SC.Present.Guard.Bulk", intent["all_of"])
        self.assertEqual(intent["max_hit"], 1)
        self.assertEqual(intent["on_error"], ["SC.Present.Stop.Intent"])

    def test_no_spend_authority_and_input_requires_explicit_task_option(self):
        policy = (ROOT / "project_policy.json").read_text(encoding="utf-8")
        table = json.loads((ROOT / "assets/input/pipeline_actions.json").read_text(encoding="utf-8"))
        self.assertNotIn("SC.Present", policy)
        self.assertNotIn("SC.Weekly", policy)
        # WeeklyProduce (official Auto): navigation enabled 2026-10-25, ±/決定/キャンセル/開始 2026-10-29; スキップ stays off;
        # 2026-10-31 ループ start with recovery soda (user 「允许」, its own opt-in and cap).
        weekly = [a for a in table["actions"] if any(n.startswith("SC.Weekly") for n in a["nodes"])]
        self.assertTrue(weekly and all(a["family"] == "weekly-auto" for a in weekly))
        self.assertEqual({a["id"] for a in weekly if a.get("enabled") is False}, {"weekly-auto-skip"})
        self.assertEqual({a["id"] for a in weekly if a["consuming"] and a.get("enabled") is not False},
                         {"weekly-auto-start", "weekly-auto-soda-start"})
        bound = {}
        for action in table["actions"]:
            for node in action["nodes"]:
                if node.startswith("SC.Present."):
                    bound[node] = action
        for name, action in bound.items():
            with self.subTest(node=name):
                self.assertIs(self.nodes[name].get("enabled"), False)
                self.assertFalse(action["consuming"])
                if action["family"] != "present":     # SC.Present.Return reuses the shared back button
                    self.assertEqual((name, action["id"]), ("SC.Present.Return", "nav-back"))
        self.assertEqual(set(bound), {"SC.Present.OpenIntent", "SC.Present.UnclaimedTab", "SC.Present.BulkClick",
                                      "SC.Present.ConfirmClick", "SC.Present.ResultClose", "SC.Present.Return",
                                  "SC.Present.ScrollDown"})
        task = json.loads((ROOT / "assets/tasks/present_claim.json").read_text(encoding="utf-8"))["task"][0]
        self.assertTrue(task["default_check"])   # 2026-10-10 user: on by default (receiving never spends)

    def test_agent_registrations_exist(self):
        from tools.check_project import registered_custom_actions
        names = registered_custom_actions(ROOT)
        for name in ("SC.Present.Begin", "SC.Present.Decide", "SC.Present.Intent", "SC.Present.Result",
                     "SC.Present.Confirm", "SC.Present.Stop", "SC.Weekly.Read"):
            self.assertIn(name, names)


if __name__ == "__main__":
    unittest.main()
