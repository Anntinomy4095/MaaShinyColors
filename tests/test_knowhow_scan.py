"""K3 ノウハウブック一覧 scan decision core (agent/core/knowhow_scan.py) on synthetic frames, plus the Pipeline /
proposed action-table contract. Offline: no Maa controller, no input."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import unittest
from pathlib import Path

from agent.core import knowhow_books as kb
from agent.core import knowhow_grid as kg
from agent.core import knowhow_scan as ks
from tests import knowhow_scan_frames as kf

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "assets" / "resource" / "base" / "pipeline" / "knowhow_scan.json"
BIG = {"max_books": 600, "max_inputs": 6000, "max_frames": 8000, "max_grid_scrolls": 200}


def session(resume=None, scan_id=None, **options):
    """Detection tests see the raw stop; the home recovery (ScanOptions.recover_home) is tested on its own."""
    options.setdefault("recover_home", False)
    return ks.ScanSession(ks.ScanOptions.from_params(options), kf.read_texts, resume=resume, scan_id=scan_id)


def kinds(decisions):
    return [d.kind for d in decisions]


class OptionTests(unittest.TestCase):
    def test_defaults_and_validation(self):
        o = ks.ScanOptions.from_params(None)
        self.assertEqual((o.details, o.max_books, o.max_inputs), (True, 600, 590))  # full-library defaults
        for bad in ({"max_inputs": -1}, {"max_books": 601}, {"details": 1}, {"nope": 1}, {"max_holds": True}):
            with self.assertRaises(ValueError, msg=bad):
                ks.ScanOptions.from_params(bad)


class ClosedLoopTests(unittest.TestCase):
    def test_full_scan_switches_sort_reads_everything_and_restores(self):
        books = kf.library(23)
        game = kf.Game(books)
        s = session(**BIG)
        out = kf.run(s, game)
        r = s.result()
        self.assertEqual(out[-1].kind, "leave")
        self.assertEqual(game.inputs[:3], ["open_sort", "pick_sort", "apply_sort"])
        self.assertEqual(game.inputs[-4:], ["open_sort", "pick_sort", "apply_sort", "leave"])
        self.assertEqual(game.sort, "デフォルト")                      # original sort restored
        self.assertEqual((r["grid_complete"], r["details_complete"], r["partial"]), (True, True, False))
        self.assertEqual(r["grid"]["count"], 23)
        self.assertEqual(r["grid"]["values"], sorted((b.value for b in books), reverse=True))
        self.assertGreater(r["partial_reads"], 0)                   # detail steps read only the left panel
        self.assertEqual(len(r["books"]), 23)
        self.assertTrue(all(b["complete"] and b["fingerprint"].startswith("b-") for b in r["books"]))
        first = r["books"][0]
        self.assertEqual((first["title"], first["eval_value"], first["protected"]), (books[0].title, 1000, False))
        self.assertEqual([x["name"] for x in first["rows"]], [n for n, _ in books[0].rows])
        self.assertEqual({b["title"]: b["protected"] for b in r["books"]}[books[3].title], True)
        self.assertEqual({b["title"]: b["marked"] for b in r["books"]}[books[5].title], True)
        self.assertFalse(r["sort_restore_needed"])
        self.assertEqual(game.inputs.count("open_tile"), 23)
        self.assertLessEqual(r["inputs"], BIG["max_inputs"])

    def test_full_scan_from_the_date_order_restores_it(self):
        # A list kept in 獲得順 is read in 評価値順 and put back
        books = kf.library(12)
        game = kf.Game(books, sort=kb.SORT_DATE)
        s = session(**BIG)
        out = kf.run(s, game)
        self.assertEqual(out[-1].kind, "leave")
        self.assertEqual(game.inputs[:3], ["open_sort", "pick_sort", "apply_sort"])
        self.assertEqual(game.inputs[-4:], ["open_sort", "pick_sort", "apply_sort", "leave"])
        self.assertEqual(game.sort, kb.SORT_DATE)
        r = s.result()
        self.assertEqual((r["grid_complete"], r["details_complete"], r["sort_restore_needed"]), (True, True, False))

    def test_scan_already_in_eval_order_needs_no_sort_input(self):
        game = kf.Game(kf.library(12), sort=kb.SORT_EVAL)
        s = session(details=False)
        kf.run(s, game)
        r = s.result()
        self.assertNotIn("open_sort", game.inputs)
        self.assertEqual(game.inputs[-1], "leave")
        self.assertTrue(r["grid_complete"])
        self.assertFalse(r["details_complete"])
        self.assertTrue(r["partial"])

    def test_grid_only_result_feeds_location_hints(self):
        books = kf.library(18)
        s = session(details=False)
        kf.run(s, kf.Game(books))
        r = s.result()
        inv = kb.KnowhowInventory()
        summary = ks.apply_to_inventory(r, inv, scanned_at="2026-10-08T20:00:00+09:00")
        self.assertTrue(summary["grid_complete"])
        payload = kb.ui_payload(inv)
        self.assertTrue(payload["scan"]["grid_complete"])
        self.assertEqual(payload["scan"]["owned_total"], 18)
        self.assertEqual(len(payload["scan"]["grid"]["values"]), 18)
        self.assertEqual(payload["books"], [])

    def test_book_budget_keeps_scanning_the_grid_and_restores(self):
        game = kf.Game(kf.library(20))
        s = session(max_books=2, max_inputs=200, max_frames=400)
        kf.run(s, game)
        r = s.result()
        self.assertEqual(r["soft_reason"], "book_budget_exhausted")
        self.assertEqual(len(r["books"]), 2)
        self.assertTrue(r["grid_complete"])
        self.assertTrue(r["partial"])
        self.assertEqual(game.sort, "デフォルト")

    def test_input_budget_reserves_the_restore(self):
        for budget in (4, 5, 9, 15):
            game = kf.Game(kf.library(20))
            s = session(max_books=50, max_inputs=budget, max_frames=400)
            out = kf.run(s, game)
            r = s.result()
            with self.subTest(budget=budget):
                self.assertEqual(out[-1].kind, "leave")
                self.assertLessEqual(r["inputs"], budget)
                self.assertEqual(game.sort, "デフォルト")
                self.assertTrue(r["partial"])
        game = kf.Game(kf.library(20))
        s = session(max_inputs=3)
        kf.run(s, game)
        self.assertEqual(game.inputs, ["leave"])          # not even the sort switch fits with its restore

    def test_partial_book_on_budget_is_kept_incomplete(self):
        books = kf.library(6, rows=9)
        s = session(max_books=1, max_inputs=9)                     # sort switch 3 + tile + 1 scroll; 4 kept for restore+leave
        kf.run(s, kf.Game(books))
        r = s.result()
        self.assertEqual(len(r["books"]), 1)
        self.assertFalse(r["books"][0]["complete"])
        self.assertTrue(r["books"][0]["fingerprint"].startswith("p-"))

    def test_time_budget_winds_down_like_the_input_budget_before_the_task_timeout(self):
        game = kf.Game(kf.library(30))
        s = session(max_books=50, max_inputs=500, max_frames=800, max_seconds=400)
        s.clock = lambda: s.frames * 10.0                       # 10 s per capture
        out = kf.run(s, game)
        r = s.result()
        self.assertEqual(out[-1].kind, "leave")
        self.assertEqual(r["soft_reason"], "input_budget_exhausted")   # the UI continues the next segment the same way
        self.assertEqual(game.sort, "デフォルト")
        timed = [e for e in s.log if e.get("kind") == "time_budget"]
        self.assertEqual(len(timed), 1)
        self.assertLessEqual(timed[0]["seconds"], 400)
        self.assertLess(len(r["books"]), 30)
        self.assertTrue(r["books"] and all(b["complete"] for b in r["books"]))  # no new book in the last 90 s
        untimed = session(max_books=50, max_inputs=500, max_frames=800)
        untimed.clock = lambda: untimed.frames * 10.0
        kf.run(untimed, kf.Game(kf.library(30)))
        self.assertFalse(untimed.result()["partial"])           # max_seconds 0 = no time budget


class DriftClosedLoopTests(unittest.TestCase):
    """The fake grid drifts like the live one (a wheel notch 159.5 px vs a 157.5 px pitch) and animates each wheel."""

    def test_drifting_grid_full_scan_taps_slots_and_reads_every_book(self):
        books = kf.library(40)
        game = kf.Game(books, grid_step_px=159.5, animate=True)
        s = session(**BIG)
        out = kf.run(s, game)
        r = s.result()
        self.assertEqual(out[-1].kind, "leave")
        self.assertEqual((r["grid_complete"], r["details_complete"]), (True, True))
        self.assertEqual(r["grid"]["values"], sorted((b.value for b in books), reverse=True))
        self.assertEqual(game.misses, 0)
        self.assertTrue(all(key.startswith(ks.SLOT_PREFIX) for key, _ in game.taps))
        self.assertEqual(sorted(pos for _, pos in game.taps), list(range(40)))      # each tile tapped once
        order = sorted(range(40), key=lambda i: -books[i].value)                     # 評価値順 (sort restored now)
        self.assertEqual([b["title"] for b in r["books"]], [books[order[i]].title for i in range(40)])
        offsets = {round(e["offset"]) for e in r["grid"]["step_log"]}
        self.assertGreater(len(offsets), 3)                                         # the grid really drifted
        for e in r["grid"]["step_log"]:
            self.assertIn(e["source"], ("tiles+pixels", "tiles", "stalled"))

    def test_reads_only_settled_captures_after_each_wheel(self):
        game = kf.Game(kf.library(60), sort=kb.SORT_EVAL, grid_step_px=160, animate=True)
        s = session(details=False, **BIG)
        kf.run(s, game)
        log = s.log
        scrolls = [i for i, e in enumerate(log) if e["kind"] == "scroll_grid"]
        self.assertGreater(len(scrolls), 3)
        for i in scrolls:
            nxt = log[i + 1]
            self.assertEqual((nxt["kind"], nxt["reason"]), ("hold", "grid_settling"))   # the moving frame is not read
            self.assertEqual(log[i + 2]["kind"], "hold")                                # first settled pair needs 2
        read_frames = [e["frame"] for e in s.step_log]
        scroll_frames = [log[i]["frame"] for i in scrolls]
        for f in read_frames:
            last = max(x for x in scroll_frames if x < f)
            self.assertGreaterEqual(f - last, 3)
        self.assertTrue(s.result()["grid_complete"])

    def test_never_settling_grid_stops(self):
        class Jitter(kf.Game):
            def render(self):
                if self.inputs and self.inputs[-1] == "scroll_grid":
                    self.scroll_px += 1.0                     # still moving on every capture
                return super().render()
        s = session(details=False, **BIG)
        out = kf.run(s, Jitter(kf.library(30), sort=kb.SORT_EVAL, grid_step_px=160))
        self.assertEqual((out[-1].kind, out[-1].reason, out[-1].notes),
                         ("stop", "grid_not_settled", (f"captures={ks.GRID_SETTLE_HOLDS + 1}",)))

    def test_moving_captures_after_a_grid_wheel_are_never_ocr_read(self):
        """2026-10-24: the settle check runs on pixels before any OCR (a full read is ~1.6 s live)."""
        game = kf.Game(kf.library(60), sort=kb.SORT_EVAL, grid_step_px=160, animate=True)
        s = session(details=False, **BIG)
        read = []
        s.read_texts = lambda image: (read.append(s.frames), kf.read_texts(image))[1]
        kf.run(s, game)
        settling = {e["frame"] for e in s.log if e["kind"] == "hold" and e["reason"] in ks.SETTLE_REASONS}
        self.assertGreater(len(settling), 3)
        self.assertFalse(settling & set(read))
        self.assertEqual(s.result()["settle"]["grid_holds"], len(settling))

    def test_grid_wheels_are_paced_under_the_host_limit(self):
        """Fast captures: ≤ GRID_PACE[0] wheels per direction in any GRID_PACE[1] s window, waiting without input
        (a smaller pace here: the fake library stays readable up to ~150 books, 27 notches)."""
        from unittest import mock
        game = kf.Game(kf.library(150), sort=kb.SORT_EVAL, grid_step_px=159.5)
        game.scroll_px = 159.5 * (game.max_scroll // 159.5)    # seek_top first: whole notches up to the top
        s = session(details=False, **{**BIG, "max_grid_scrolls": 300})
        sent = {"down": [], "up": []}
        original = game.apply
        game.apply = lambda d: (sent[d.direction].append(s.clock()) if d.kind == "scroll_grid" else None,
                                original(d))[1]
        pace = (10, 30.0)
        with mock.patch.object(ks, "GRID_PACE", pace):
            out = kf.run(s, game, limit=4000, capture_s=0.4)
        self.assertEqual(out[-1].kind, "leave")
        self.assertTrue(s.result()["grid_complete"])
        self.assertGreater(s.settle["grid_pace_holds"], 0)
        count, window = pace
        for direction, times in sent.items():
            self.assertGreater(len(times), count, direction)
            self.assertTrue(all(sum(1 for t in times if a <= t < a + window) <= count for a in times), direction)

    def test_wheel_without_movement_is_content_stopped(self):
        class Stuck(kf.Game):
            def apply(self, d):
                if d.kind == "scroll_grid" and self.inputs.count("scroll_grid") >= 1:
                    self.inputs.append(d.kind)                 # the wheel arrives but the list does not move
                    return
                super().apply(d)
        game = Stuck(kf.library(40), sort=kb.SORT_EVAL, grid_step_px=159.5)
        s = session(details=False, **BIG)
        out = kf.run(s, game)
        r = s.result()
        self.assertEqual(out[-1].kind, "leave")
        self.assertEqual(r["grid"]["end"], "content_stopped")
        self.assertFalse(r["grid_complete"])                     # fewer tiles than 所持数: never a whole-library claim
        self.assertIn("grid_stall_check", [e["reason"] for e in s.log if e["kind"] == "hold"])
        self.assertEqual(s.step_log[-1]["source"], "stalled")

    def test_tile_changed_under_a_drifted_screen_stops(self):
        class Swap(kf.Game):
            def apply(self, d):
                super().apply(d)
                if d.kind == "scroll_grid" and self.inputs.count("scroll_grid") == 2:
                    self.books[self.order[11]].rows.append(("新", 5))    # a row already read now shows other N/M
        s = session(details=False, **BIG)
        out = kf.run(s, Swap(kf.library(40), sort=kb.SORT_EVAL, grid_step_px=159.5))
        self.assertEqual(out[-1].kind, "stop")
        self.assertIn(out[-1].reason, ("grid_overlap_lost", "grid_thumb_mismatch"))
        self.assertFalse(s.result()["grid_complete"])

    def test_tap_that_misses_the_tile_stops_on_the_check(self):
        class Offset(kf.Game):
            def tile_at(self, rect):
                return super().tile_at((rect[0], rect[1] + 70, rect[2], rect[3]))   # lands one slot lower
        game = Offset(kf.library(20), sort=kb.SORT_EVAL, grid_step_px=159.5)
        out = kf.run(session(**BIG), game)
        self.assertEqual((out[-1].kind, out[-1].reason), ("stop", "detail_binding_lost"))


class FaultLogTests(unittest.TestCase):
    def test_launcher_writes_a_fault_log_next_to_the_summary(self):
        import faulthandler
        import tempfile
        from tools import run_tasks as launcher

        with tempfile.TemporaryDirectory() as tmp:
            old = launcher._FAULT_LOG
            launcher._FAULT_LOG = None
            try:
                launcher._enable_fault_log(Path(tmp) / "run-summary.json")
                self.assertTrue((Path(tmp) / "run-summary.fault.log").exists())
                self.assertTrue(faulthandler.is_enabled())
            finally:
                faulthandler.disable()
                if launcher._FAULT_LOG is not None:
                    launcher._FAULT_LOG.close()
                launcher._FAULT_LOG = old


class StopTests(unittest.TestCase):
    def assertStops(self, game, reason, *, inputs=None, **options):
        s = session(**({**BIG, **options}))
        out = kf.run(s, game)
        self.assertEqual((out[-1].kind, out[-1].reason), ("stop", reason))
        if inputs is not None:
            self.assertEqual(game.inputs, inputs)
        return s

    def test_refuses_before_any_input(self):
        self.assertStops(kf.Game(kf.library(5), direction="▲昇順"), "direction_not_desc", inputs=[])
        self.assertStops(kf.Game(kf.library(5), sort="Lv順"), "orig_sort_not_restorable", inputs=[])
        self.assertStops(kf.Game(kf.library(5), mode="protect"), "mode_protect", inputs=[])

    def test_mode_or_popup_mid_scan_stops_and_reports_sort_restore(self):
        game = kf.Game(kf.library(20))
        s = session(**BIG)
        for _ in range(6):
            d = s.observe(game.render())
            game.apply(d)
        game.mode = "protect"
        d = s.observe(game.render())
        self.assertEqual((d.kind, d.reason), ("stop", "mode_protect"))
        self.assertIs(s.observe(game.render()), d)                    # final: no retry, no further decision
        r = s.result()
        self.assertTrue(r["stopped"])
        self.assertTrue(r["sort_restore_needed"])
        self.assertTrue(r["partial"])

    def test_owned_change_and_unknown_screen(self):
        game = kf.Game(kf.library(12))
        s = session(**BIG)
        for _ in range(4):
            game.apply(s.observe(game.render()))
        game.owned = 13
        self.assertEqual(s.observe(game.render()).reason, "owned_changed")
        s = session(**BIG)
        blank = kf.frame(kf.np.full((720, 1280, 3), 200, dtype=kf.np.uint8), [("ホーム", (40, 20, 80, 30))])
        self.assertEqual(s.observe(blank).reason, "unknown_screen")

    def test_grid_step_without_row_overlap_stops(self):
        s = self.assertStops(kf.Game(kf.library(40), grid_step=3), "grid_overlap_lost", details=False)
        self.assertFalse(s.result()["grid_complete"])

    def test_ambiguous_overlap_stops(self):
        same = [kf.Book(value=900, rows=[("A", 5), ("B", 1)], title=f"【x{i}】偶像") for i in range(15)]
        tail = [kf.Book(value=800 - i, rows=[("C", 1)], title=f"【y{i}】偶像") for i in range(5)]
        self.assertStops(kf.Game(same + tail, sort=kb.SORT_EVAL), "grid_overlap_ambiguous", details=False)

    def test_detail_binding_and_mismatch(self):
        class NoSelect(kf.Game):
            def apply(self, d):
                if d.kind != "open_tile":
                    super().apply(d)
        self.assertStops(NoSelect(kf.library(8), sort=kb.SORT_EVAL), "detail_binding_lost")

        class LostFirstTap(kf.Game):          # 2026-10-12 live book 7: the first open tap never ran; one repeat is allowed
            dropped = False

            def apply(self, d):
                if d.kind == "open_tile" and not self.dropped:
                    self.dropped = True
                    return
                super().apply(d)
        s = session(**BIG)
        out = kf.run(s, LostFirstTap(kf.library(8), sort=kb.SORT_EVAL))
        self.assertNotEqual(out[-1].reason, "detail_binding_lost")
        self.assertEqual([d.reason for d in out].count("reopen_book"), 1)

        class WrongCount(kf.Game):
            def render(self):
                f = super().render()
                f.texts = [("4/4", t[1], 0.99) if t[0] == "5/5" else t for t in f.texts]
                return f
        self.assertStops(WrongCount(kf.library(8), sort=kb.SORT_EVAL), "detail_mismatch")

    def test_detail_steps_need_overlap_and_movement(self):
        self.assertStops(kf.Game(kf.library(5, rows=9), sort=kb.SORT_EVAL, detail_step=3), "detail_overlap_lost")
        self.assertStops(kf.Game(kf.library(5, rows=9), sort=kb.SORT_EVAL, detail_step=0), "detail_stalled")

    def test_unreadable_tiles_hold_then_stop(self):
        class NoPill(kf.Game):
            def render(self):
                f = super().render()
                f.texts = [t for t in f.texts if not t[0].startswith("Lv.5")]
                return f
        s = self.assertStops(NoPill(kf.library(8), sort=kb.SORT_EVAL), "hold_budget_exhausted", inputs=[], max_holds=2)
        self.assertEqual([e["kind"] for e in s.log], ["hold", "hold", "stop"])



class RecoverHomeTests(unittest.TestCase):
    """recover_home (default): a reading/binding problem on the plain 一覧 backs out home once instead of halting."""

    class NoPill(kf.Game):
        def render(self):
            f = super().render()
            f.texts = [t for t in f.texts if not t[0].startswith("Lv.5")]
            return f

    def test_reading_problem_leaves_without_storing_and_reports_the_reason(self):
        game = self.NoPill(kf.library(8), sort=kb.SORT_EVAL)
        s = ks.ScanSession(ks.ScanOptions.from_params({**BIG, "max_holds": 2}), kf.read_texts)
        out = kf.run(s, game)
        self.assertEqual((out[-1].kind, out[-1].reason), ("leave", "recovered:hold_budget_exhausted"))
        self.assertEqual(game.inputs, ["leave"])
        r = s.result()
        self.assertEqual((r["recovered_from"]["reason"], r["stopped"], r["partial"], r["books"]),
                         ("hold_budget_exhausted", False, True, []))
        self.assertIs(s.observe(game.render()), out[-1])              # final: nothing after the leave

    def test_changed_sort_is_restored_before_leaving(self):
        class LateNoPill(kf.Game):          # pills vanish only after the scan switched to 評価値順
            def render(self):
                f = super().render()
                if self.sort == kb.SORT_EVAL:
                    f.texts = [t for t in f.texts if not t[0].startswith("Lv.5")]
                return f
        game = LateNoPill(kf.library(8))
        s = ks.ScanSession(ks.ScanOptions.from_params({**BIG, "max_holds": 2}), kf.read_texts)
        out = kf.run(s, game)
        self.assertEqual(out[-1].kind, "leave")
        self.assertEqual(game.sort, "デフォルト")
        self.assertFalse(s.result()["sort_restore_needed"])

    def test_screen_problems_still_halt(self):
        game = kf.Game(kf.library(20))
        s = ks.ScanSession(ks.ScanOptions.from_params(BIG), kf.read_texts)
        for _ in range(6):
            game.apply(s.observe(game.render()))
        game.mode = "protect"
        d = s.observe(game.render())
        self.assertEqual((d.kind, d.reason), ("stop", "mode_protect"))
        self.assertIsNone(s.result()["recovered_from"])

class ResumeTests(unittest.TestCase):
    def test_resume_reuses_books_only_while_the_grid_prefix_matches(self):
        books = kf.library(12)
        s1 = session(scan_id="continuous", max_books=4, max_inputs=500, max_frames=500)
        kf.run(s1, kf.Game(books))
        first = json.loads(json.dumps(s1.result(), ensure_ascii=False))
        self.assertEqual(len(first["books"]), 4)
        game = kf.Game(books)
        s2 = session(resume=first, scan_id="continuous", **BIG)
        kf.run(s2, game)
        r = s2.result()
        self.assertEqual(r["resumed"], [0, 1, 2, 3])
        self.assertEqual(game.inputs.count("open_tile"), 8)
        self.assertTrue(r["details_complete"])
        # A new higher-valued book shifts every position: nothing is reused, everything is read again.
        shifted = [kf.Book(value=2000, rows=[("新", 5)], title="【新】偶像")] + books
        game = kf.Game(shifted)
        s3 = session(resume=first, scan_id="continuous", **BIG)
        kf.run(s3, game)
        self.assertEqual(s3.result()["resumed"], [])
        self.assertEqual(game.inputs.count("open_tile"), 13)

    def test_early_stop_carries_unvisited_resume_books_forward(self):
        """2026-10-23 live seg4: a resumed run that stopped at index 20 must not shrink the 59-book partial."""
        books = kf.library(30)
        s1 = session(scan_id="continuous", max_books=12, max_inputs=500, max_frames=500)
        kf.run(s1, kf.Game(books))
        first = json.loads(json.dumps(s1.result(), ensure_ascii=False))
        self.assertEqual((len(first["books"]), first["grid"]["count"]), (12, 30))
        s2 = session(resume=first, scan_id="continuous", max_inputs=1, max_frames=500)
        kf.run(s2, kf.Game(books))
        second = json.loads(json.dumps(s2.result(), ensure_ascii=False))
        self.assertLess(len(s2.grid), 30)
        self.assertEqual([b["grid_index"] for b in second["books"]], list(range(12)))
        self.assertEqual(second["grid"]["tiles"], first["grid"]["tiles"])
        self.assertGreater(second["carried"], 0)
        game = kf.Game(books)
        s3 = session(resume=second, scan_id="continuous", **BIG)
        kf.run(s3, game)
        self.assertEqual(s3.result()["resumed"], list(range(12)))
        self.assertEqual(game.inputs.count("open_tile"), 18)
        # A shifted grid carries nothing forward.
        shifted = [kf.Book(value=2000, rows=[("新", 5)], title="【新】偶像")] + books
        s4 = session(resume=first, scan_id="continuous", max_inputs=1, max_frames=500)
        kf.run(s4, kf.Game(shifted))
        self.assertEqual((s4.result()["carried"], s4.result()["books"]), (0, []))

    def test_resume_keeps_the_recorded_original_sort(self):
        record = {"orig_sort": "デフォルト", "partial": True, "scan_id": "continuous", "grid": {"tiles": []}, "books": []}
        game = kf.Game(kf.library(6), sort=kb.SORT_EVAL)      # a previous run stopped after switching the sort
        s = session(resume=record, scan_id="continuous", details=False)
        kf.run(s, game)
        self.assertEqual(game.sort, "デフォルト")

    def test_new_scan_does_not_reuse_same_signature_with_different_contents(self):
        old = kf.Book(value=900, rows=[("旧词条", 5), ("共有", 1)], title="【旧】偶像")
        new = kf.Book(value=900, rows=[("新词条", 5), ("共有", 1)], title="【新】偶像")
        other = kf.Book(value=800, rows=[("其他", 1)])
        first = session(scan_id="old", max_books=1, max_inputs=100)
        kf.run(first, kf.Game([old, other]))
        saved = first.result()
        for current_id, record in (("new", saved), (None, saved), ("new", {k: v for k, v in saved.items() if k != "scan_id"})):
            with self.subTest(current_id=current_id, legacy="scan_id" not in record):
                now = session(resume=record, scan_id=current_id, **BIG)
                kf.run(now, kf.Game([new, other]))
                result = now.result()
                self.assertEqual(result["resumed"], [])
                self.assertTrue(result["details_complete"])
                self.assertEqual(result["books"][0]["title"], new.title)
                self.assertEqual(result["books"][0]["rows"][0]["name"], "新词条")

    def test_same_scan_rechecks_after_owned_count_changes(self):
        books = kf.library(4)
        first = session(scan_id="continuous", max_books=1, max_inputs=100)
        kf.run(first, kf.Game(books))
        # Add at the end: all previously seen signatures and their positions still match.
        now = session(resume=first.result(), scan_id="continuous", **BIG)
        kf.run(now, kf.Game(books + [kf.Book(value=1, rows=[("新", 1)])]))
        self.assertEqual(now.result()["resumed"], [])

    def test_only_partial_records_belong_to_an_automatic_continuation(self):
        full = session(scan_id="continuous", **BIG)
        kf.run(full, kf.Game(kf.library(2)))
        self.assertTrue(full.result()["details_complete"])
        now = session(resume=full.result(), scan_id="continuous", **BIG)
        kf.run(now, kf.Game(kf.library(2)))
        self.assertEqual(now.result()["resumed"], [])

    def test_bounded_segments_keep_progress_and_restore_sort_at_the_end(self):
        books, previous, sort, counts = kf.library(12, rows=9), None, "デフォルト", []
        for _ in range(6):
            game = kf.Game(books, sort=sort)
            now = session(resume=previous, scan_id="continuous", max_inputs=24, max_frames=800)
            kf.run(now, game)
            result = now.result()
            count = result["books_complete"]
            self.assertGreater(count, counts[-1] if counts else 0)
            counts.append(count)
            if result["details_complete"]:
                self.assertEqual(game.sort, "デフォルト")
                break
            self.assertEqual(game.sort, kb.SORT_EVAL)
            self.assertTrue(result["sort_restore_needed"])
            previous, sort = result, game.sort
        else:
            self.fail(f"segments did not finish: {counts}")
        self.assertEqual(counts[-1], len(books))


class InventoryTests(unittest.TestCase):
    def test_complete_snapshot_removes_books_no_longer_owned(self):
        inv = kb.KnowhowInventory()
        for stamp, books in (("old", [kf.Book(value=900, rows=[("X", 5)], title="【旧】偶像")]),
                             ("new", [kf.Book(value=800, rows=[("Z", 1)], title="【新】偶像")])):
            scan = session(**BIG)
            kf.run(scan, kf.Game(books))
            self.assertTrue(scan.result()["details_complete"])
            ks.apply_to_inventory(scan.result(), inv, scanned_at=stamp)
        self.assertEqual(len(inv.books), 1)
        self.assertEqual(inv.owned_total, 1)
        self.assertFalse(kb.analyze(inv.books.values(), ["X"])["wanted"][0]["owned"])

    def test_complete_grid_with_every_book_replaces_holdings_even_if_one_field_is_missing(self):
        inv = kb.KnowhowInventory()
        for stamp, title in (("old", "【旧】偶像"), ("new", "【新】偶像")):
            scan = session(**BIG)
            kf.run(scan, kf.Game([kf.Book(value=900, rows=[("X", 5)], title=title)]))
            result = {**scan.result(), "details_complete": False, "partial": True}   # e.g. one book without stats.me
            self.assertTrue(result["grid_complete"])
            ks.apply_to_inventory(result, inv, scanned_at=stamp)
        self.assertEqual([b.title for b in inv.books.values()], ["【新】偶像"])
        short = {**result, "books": [], "owned_total": 1}                             # a tile without its book: keep
        ks.apply_to_inventory(short, inv, scanned_at="short")
        self.assertEqual(len(inv.books), 1)

    def test_partial_scan_keeps_existing_copy_count_and_unseen_books(self):
        twin = dict(rows=[("X", 5)], title="【同】偶像", stats=(1, 2, 3, 4))
        books = [kf.Book(value=900, **twin) for _ in range(3)]
        books.append(kf.Book(value=800, rows=[("Z", 1)], title="【别】偶像"))
        inv = kb.KnowhowInventory()
        full = session(**BIG)
        kf.run(full, kf.Game(books))
        ks.apply_to_inventory(full.result(), inv, scanned_at="full")
        partial = session(max_books=1, max_inputs=500, max_frames=500)
        kf.run(partial, kf.Game(books))
        self.assertTrue(partial.result()["partial"])
        ks.apply_to_inventory(partial.result(), inv, scanned_at="partial")
        self.assertEqual(sorted(b.count for b in inv.books.values()), [1, 3])

    def test_identical_books_at_two_grid_indices_count_twice(self):
        twin = dict(rows=[("X", 5), ("Y", 2)], title="【同】偶像", stats=(1, 2, 3, 4))
        books = [kf.Book(value=900, **twin), kf.Book(value=900, **twin), kf.Book(value=800, rows=[("Z", 1)], title="【z】偶像")]
        s = session(**BIG)
        kf.run(s, kf.Game(books))
        inv = kb.KnowhowInventory()
        ks.apply_to_inventory(s.result(), inv, scanned_at="t")
        counts = sorted(b.count for b in inv.books.values())
        self.assertEqual(counts, [1, 2])
        self.assertTrue(inv.grid["complete"])


def dated(n, newest="2026/10/25", *, seed=1):
    """Library whose last books are the newest: book i acquired on day i (the last two on ``newest``)."""
    books = kf.library(n, seed=seed)
    for i, b in enumerate(books):
        b.acquired = newest if i >= n - 2 else f"2026/09/{1 + i % 28:02d}"
    return books


class NewestBooksTests(unittest.TestCase):
    """ScanOptions.newest (增量): 獲得順 ▼, open only the newest N, restore the sort, leave."""

    def test_reads_the_newest_books_and_restores_the_sort(self):
        books = dated(23)
        game = kf.Game(books)
        s = session(newest=2, max_inputs=60)
        out = kf.run(s, game)
        r = s.result()
        self.assertEqual(out[-1].kind, "leave")
        self.assertEqual([d.notes[0] for d in out if d.kind == "open_tile"], ["index=1", "index=0"])   # oldest first
        self.assertEqual(game.inputs[:3], ["open_sort", "pick_sort", "apply_sort"])
        self.assertEqual(game.inputs[-4:], ["open_sort", "pick_sort", "apply_sort", "leave"])
        self.assertEqual(game.sort, "デフォルト")
        self.assertNotIn("scroll_grid", game.inputs)                 # the newest books are on the top screen
        self.assertEqual(game.inputs.count("open_tile"), 2)
        self.assertEqual([b["title"] for b in r["books"]], [books[22].title, books[21].title])
        self.assertTrue(all(b["complete"] and b["eval_value"] is None for b in r["books"]))
        self.assertEqual((r["mode"], r["newest"], r["outcome"], r["sort_restore_needed"]), ("newest", 2, "newest_done", False))
        picks = [d.key for d in out if d.kind == "pick_sort"]
        self.assertEqual(picks, [kb.SORT_DATE, "デフォルト"])
        self.assertEqual(ks.pipeline_node(next(d for d in out if d.kind == "pick_sort")), "SC.KnowhowScan.Sort.PickDate")

    def test_list_already_in_date_order_needs_no_sort_input(self):
        game = kf.Game(dated(12), sort=kb.SORT_DATE)
        s = session(newest=1, max_inputs=60)
        kf.run(s, game)
        self.assertNotIn("open_sort", game.inputs)
        self.assertEqual([k for k in game.inputs if k != "scroll_detail"], ["open_tile", "leave"])
        self.assertEqual(s.result()["outcome"], "newest_done")

    def test_fewer_books_than_requested(self):
        game = kf.Game(dated(2), sort=kb.SORT_DATE)
        s = session(newest=5, max_inputs=60)
        kf.run(s, game)
        self.assertEqual((game.inputs.count("open_tile"), s.result()["outcome"]), (2, "newest_done"))

    def test_dates_out_of_order_stop_before_any_tile(self):
        class Shuffled(kf.Game):
            @property
            def order(self):
                return list(range(len(self.books)))           # oldest first: not 獲得順 ▼
        game = Shuffled(dated(12), sort=kb.SORT_DATE)
        s = session(newest=2, max_inputs=60)
        out = kf.run(s, game)
        self.assertEqual((out[-1].kind, out[-1].reason), ("stop", "grid_order_broken"))
        self.assertNotIn("open_tile", game.inputs)

    def test_options(self):
        self.assertEqual(ks.ScanOptions.from_params({"newest": ks.NEWEST_MAX}).newest, ks.NEWEST_MAX)
        for bad in ({"newest": ks.NEWEST_MAX + 1}, {"newest": -1}, {"newest": 2, "details": False}, {"newest": True}):
            with self.assertRaises(ValueError, msg=bad):
                ks.ScanOptions.from_params(bad)

    def test_inventory_keeps_the_eval_grid_and_resolves_cut_pending_books(self):
        books = dated(12)
        newest = books[-1]
        newest.rows = [("VisualUP", 1), ("ファンの声援+", 1), ("WING優勝", 1)]
        newest.title, newest.stats = "【ひとりじゃない夜のこと】風野灯織", (60, 20, 10, 20)
        # an older look-alike in the library: the same title/stats with the cut rows; never a candidate
        books[0].rows, books[0].title, books[0].stats = list(newest.rows), newest.title, newest.stats
        inv = kb.KnowhowInventory()
        full = session(**BIG)
        kf.run(full, kf.Game(books))
        ks.apply_to_inventory(full.result(), inv, scanned_at="full")
        grid = dict(inv.grid)
        cut = {"title": newest.title, "idol": "風野灯織", "stats": {"vo": 60, "da": 20, "vi": 10, "me": 20},
               "rows": [{"name": "VisualUP", "level": 1, "effect": "Visual+10"},
                        {"name": "ファンの声援+", "level": 1, "effect": "テンション+10"}], "rows_partial": True}
        self.assertEqual(kb.ingest_reward_books(inv, [cut], seen_at="2026-10-25T16:26:47"), 1)
        self.assertEqual(len(inv.pending), 1)                   # exact rule: 2 rows never equal a 3-row book
        s = session(newest=1, max_inputs=60)
        kf.run(s, kf.Game(books))
        summary = ks.apply_to_inventory(s.result(), inv, scanned_at="newest")
        self.assertEqual(inv.grid, grid)                         # 評価値 coverage untouched
        self.assertEqual(inv.scanned_at, "full")
        # two copies with this content exist (count 2): the cut reading stays pending rather than guess a copy
        self.assertEqual((summary["reconciled_pending"], len(inv.pending)), (0, 1))
        books[0].title = "【別】風野灯織"                          # only the newest book matches now
        inv2 = kb.KnowhowInventory()
        full = session(**BIG)
        kf.run(full, kf.Game(books))
        ks.apply_to_inventory(full.result(), inv2, scanned_at="full")
        kb.ingest_reward_books(inv2, [cut], seen_at="2026-10-25T16:26:47")
        s = session(newest=1, max_inputs=60)
        kf.run(s, kf.Game(books))
        summary = ks.apply_to_inventory(s.result(), inv2, scanned_at="newest")
        self.assertEqual((summary["reconciled_pending"], inv2.pending), (1, []))

    def test_cut_pending_book_never_resolves_against_an_older_look_alike(self):
        inv = kb.KnowhowInventory()
        old = kb.KnowhowBookRecord.from_dict({
            "title": "【同】偶像", "stats": {"vo": 1, "da": 2, "vi": 3, "me": 4}, "knowhow_count": 2,
            "rows": [{"name": "X", "level": 1}, {"name": "Y", "level": 2}], "source": "scan"})
        inv.books[old.fingerprint] = old
        kb.ingest_reward_books(inv, [{"title": "【同】偶像", "stats": {"vo": 1, "da": 2, "vi": 3, "me": 4},
                                      "rows": [{"name": "X", "level": 1}], "rows_partial": True}])
        self.assertEqual(len(inv.pending), 1)
        self.assertEqual(inv.reconcile_pending(), [])                           # no candidates: exact rule only
        self.assertEqual(inv.reconcile_pending(candidates=[]), [])              # not among the newest books
        self.assertEqual(len(inv.reconcile_pending(candidates=[old.fingerprint])), 1)


def topup(inv, game, mode="only", **options):
    """One top-up run against ``game`` with the inventory's basis, applied to ``inv``: (session, decisions, summary)."""
    options.setdefault("recover_home", False)
    options.setdefault("max_inputs", 128)
    s = ks.ScanSession(ks.ScanOptions.from_params({"topup": mode, **options}), kf.read_texts,
                       basis=ks.library_basis(inv))
    out = kf.run(s, game)
    return s, out, ks.apply_to_inventory(s.result(), inv, scanned_at=f"t{len(game.inputs)}")


def baseline(books):
    """Inventory after a complete library scan of ``books`` (baseline 所持数 = len(books))."""
    inv = kb.KnowhowInventory()
    full = session(**BIG)
    kf.run(full, kf.Game(list(books)))
    ks.apply_to_inventory(full.result(), inv, scanned_at="full")
    assert inv.grid["complete"]
    return inv


class TopUpTests(unittest.TestCase):
    """ScanOptions.topup: after a Produce read only the books 所持数 says are new."""

    def setUp(self):
        self.books = dated(23)
        self.inv = baseline(self.books[:21])

    def test_reads_the_new_books_oldest_first_then_is_up_to_date(self):
        game = kf.Game(list(self.books))
        s, out, summary = topup(self.inv, game)
        r = s.result()
        self.assertEqual((r["mode"], r["newest"], r["topup"]["reason"], r["topup"]["anchor_ok"]), ("newest", 2, "topup", None))
        opened = [d.notes[0] for d in out if d.kind == "open_tile"]
        self.assertEqual(opened, ["index=1", "index=0"])                      # the oldest new book first
        self.assertEqual(game.sort, "デフォルト")
        self.assertEqual(summary["topup_added"], 2)
        self.assertEqual([e["fp"] for e in self.inv.recent],
                         [kb.KnowhowBookRecord.from_dict(r["books"][1]).fingerprint,
                          kb.KnowhowBookRecord.from_dict(r["books"][0]).fingerprint])
        self.assertEqual(sum(b.count for b in self.inv.books.values()), 23)
        self.assertEqual(ks.library_sync(self.inv)["state"], "up_to_date")
        self.assertEqual((self.inv.sync["state"], self.inv.owned_seen["owned"]), ("up_to_date", 23))
        self.assertTrue(self.inv.grid["complete"])                           # the baseline grid stays

        again = kf.Game(list(self.books))
        s, out, _ = topup(self.inv, again)
        self.assertEqual(again.inputs, ["leave"])                           # nothing new: no sort, no book
        self.assertEqual((s.result()["soft_reason"], s.result()["mode"]), ("library_up_to_date", "newest"))
        self.assertTrue(self.inv.grid["complete"])

        newer = kf.Book(value=700, rows=[("Q", 2)], title="【新】偶像")
        newer.acquired = "2026/10/26"
        game = kf.Game(list(self.books) + [newer])
        s, out, summary = topup(self.inv, game)
        self.assertEqual(s.result()["topup"]["anchor_ok"], True)            # tile 1 is the newest book read before
        self.assertEqual((summary["topup_added"], len(self.inv.recent), ks.library_sync(self.inv)["state"]),
                         (1, 3, "up_to_date"))

    def test_a_partial_top_up_keeps_only_the_contiguous_older_books(self):
        s = ks.ScanSession(ks.ScanOptions.from_params({"topup": "only", "max_inputs": 128, "recover_home": False}),
                           kf.read_texts, basis=ks.library_basis(self.inv))
        kf.run(s, kf.Game(list(self.books)))
        r = s.result()
        r["books"] = [b for b in r["books"] if b["grid_index"] == 0]       # the newer book only: a gap at index 1
        summary = ks.apply_to_inventory(r, self.inv, scanned_at="cut")
        self.assertEqual((summary["topup_added"], self.inv.recent), (0, []))
        r["books"] = [b for b in s.result()["books"] if b["grid_index"] == 1]
        inv = baseline(self.books[:21])
        ks.apply_to_inventory(r, inv, scanned_at="cut")
        self.assertEqual((len(inv.recent), ks.library_sync(inv, 23)["new"]), (1, 1))
        game = kf.Game(list(self.books))
        s2, out, summary = topup(inv, game)
        self.assertEqual([d.notes[0] for d in out if d.kind == "open_tile"], ["index=0"])
        self.assertEqual((s2.result()["topup"]["anchor_ok"], summary["topup_added"]), (True, 1))

    def test_counts_that_do_not_add_up_ask_for_a_full_scan(self):
        cases = {"owned_below_inventory": self.books[:20],
                 "topup_too_many": self.books[:21] + [kf.Book(value=600 + i, rows=[("R", 1)], title=f"【多{i}】偶像")
                                                     for i in range(ks.NEWEST_MAX + 1)]}
        for reason, books in cases.items():
            with self.subTest(reason=reason):
                inv = baseline(self.books[:21])
                game = kf.Game(list(books))
                s, out, _ = topup(inv, game)
                self.assertEqual(game.inputs, ["leave"])
                self.assertEqual(s.result()["soft_reason"], reason)
                self.assertTrue(inv.grid["complete"])
                self.assertIn(ks.library_sync(inv)["state"], ("fewer_owned", "too_many"))

    def test_a_misaligned_top_up_reads_nothing_and_holds_until_a_full_scan(self):
        topup(self.inv, kf.Game(list(self.books)))                         # recent: books 21, 22
        new = [kf.Book(value=500 + i, rows=[("S", 1)], title=f"【換{i}】偶像") for i in range(2)]
        for b in new:
            b.acquired = "2026/10/27"
        game = kf.Game(self.books[1:] + new)                               # one sold, two new: the count says 1
        s, out, summary = topup(self.inv, game)
        self.assertNotIn("open_tile", game.inputs)
        self.assertEqual((s.result()["soft_reason"], s.result()["topup"]["anchor_ok"]), ("topup_misaligned", False))
        self.assertEqual(game.sort, "デフォルト")                             # sort restored
        self.assertEqual((self.inv.sync["state"], ks.library_sync(self.inv)["state"]), ("needs_full_scan", "needs_full_scan"))
        again = kf.Game(self.books[1:] + new)
        s, out, _ = topup(self.inv, again)
        self.assertEqual((again.inputs, s.result()["soft_reason"]), (["leave"], "sync_broken"))
        prefer = kf.Game(self.books[1:] + new)
        s, out, _ = topup(self.inv, prefer, mode="prefer", **{k: v for k, v in BIG.items() if k != "max_inputs"},
                          max_inputs=6000)
        self.assertEqual(s.result()["mode"], "full")                       # the button rescans the whole library
        self.assertTrue(self.inv.grid["complete"])
        self.assertEqual((self.inv.recent, self.inv.sync["state"], ks.library_sync(self.inv)["state"]), ([], "up_to_date", "up_to_date"))

    def test_no_baseline_reads_the_pending_produce_books(self):
        inv = kb.KnowhowInventory()
        game = kf.Game(list(self.books))
        s, out, _ = topup(inv, game)
        self.assertEqual((game.inputs, s.result()["soft_reason"], ks.library_sync(inv)["state"]),
                         (["leave"], "nothing_pending", "no_baseline"))
        kb.ingest_reward_books(inv, [{"title": self.books[22].title, "stats": dict(zip(kb.STATS, self.books[22].stats)),
                                      "rows": [{"name": n, "level": lv} for n, lv in self.books[22].rows[:1]],
                                      "rows_partial": True}])
        game = kf.Game(list(self.books))
        s, out, summary = topup(inv, game)
        self.assertEqual((s.result()["topup"]["reason"], game.inputs.count("open_tile")), ("pending", 1))
        self.assertEqual((summary["reconciled_pending"], inv.pending, inv.recent), (1, [], []))

    def test_prefer_tops_up_only_a_baseline_count_difference(self):
        game = kf.Game(list(self.books))
        s, out, _ = topup(self.inv, game, mode="prefer")
        self.assertEqual((s.result()["mode"], game.inputs.count("open_tile")), ("newest", 2))
        game = kf.Game(list(self.books))
        s, out, _ = topup(self.inv, game, mode="prefer", **{k: v for k, v in BIG.items() if k != "max_inputs"},
                          max_inputs=6000)                                  # up to date: the requested full scan
        self.assertEqual((s.result()["mode"], s.result()["topup"]["reason"], s.result()["grid_complete"]),
                         ("full", "library_up_to_date", True))

    def test_sold_books_lower_the_count_the_sync_compares(self):
        # knowhow_books.record_removed (自动卖书, 2026-10-25): baseline and recent drop with 所持数, the sync stays valid
        topup(self.inv, kf.Game(list(self.books)))
        by_title = {b.title: fp for fp, b in self.inv.books.items()}
        newest, old = self.inv.recent[-1]["fp"], by_title[self.books[5].title]
        self.assertEqual(self.inv.books[newest].title, self.books[22].title)
        out = kb.record_removed(self.inv, [newest, old, "b-0000000000000000"])
        self.assertEqual((out["removed"], out["recent"], out["baseline"], out["missing"], out["grid_complete"]),
                         (2, 1, 1, ["b-0000000000000000"], True))
        self.assertEqual((self.inv.grid["owned_total"], len(self.inv.grid["values"]), len(self.inv.recent),
                          self.inv.owned_seen["owned"]), (20, 20, 1, 21))
        self.assertEqual(kb.ui_payload(self.inv)["scan"]["sync"]["state"], "up_to_date")
        newer = kf.Book(value=700, rows=[("Q", 2)], title="【新】偶像")
        newer.acquired = "2026/10/26"
        game = kf.Game([b for i, b in enumerate(self.books) if i not in (5, 22)] + [newer])
        s, out, summary = topup(self.inv, game)
        self.assertEqual((s.result()["topup"]["anchor_ok"], summary["topup_added"]), (True, 1))
        self.assertEqual(ks.library_sync(self.inv)["state"], "up_to_date")
        # a baseline book without its 評価値 cannot leave the grid proven: the next sync asks for a library scan
        blind = next(fp for fp, b in self.inv.books.items() if fp not in {e["fp"] for e in self.inv.recent})
        self.inv.books[blind] = dataclasses.replace(self.inv.books[blind], eval_value=None)
        self.assertFalse(kb.record_removed(self.inv, [blind], owned_now=21)["grid_complete"])
        self.assertEqual(kb.ui_payload(self.inv)["scan"]["sync"]["state"], "no_baseline")

    def test_options_and_sync_states(self):
        self.assertEqual(ks.ScanOptions.from_params({"topup": "only"}).topup, "only")
        for bad in ({"topup": "yes"}, {"topup": "only", "newest": 2}, {"topup": "prefer", "details": False}):
            with self.assertRaises(ValueError, msg=bad):
                ks.ScanOptions.from_params(bad)
        self.assertEqual(ks.topup_plan({"baseline": 498, "recent": 0}, 500), (2, "topup"))
        self.assertEqual(ks.topup_plan({"baseline": 498, "recent": 2}, 500), (0, "library_up_to_date"))
        self.assertEqual(ks.topup_plan({"baseline": None, "pending": 14}, 500), (ks.NEWEST_MAX, "pending"))
        inv = kb.KnowhowInventory()
        self.assertEqual(ks.library_sync(inv)["state"], "no_baseline")
        self.assertEqual(ks.library_sync(self.inv)["state"], "up_to_date")            # owned_seen of the full scan
        self.assertEqual(ks.library_sync(self.inv, 25)["state"], "new_books")
        self.assertEqual(ks.library_sync(self.inv, 21 + ks.NEWEST_MAX + 1)["state"], "too_many")
        self.assertEqual(ks.library_sync(self.inv, 20)["state"], "fewer_owned")
        round_trip = kb.KnowhowInventory.from_dict(json.loads(json.dumps(self.inv.to_dict())))
        self.assertEqual((round_trip.owned_seen, round_trip.recent, round_trip.sync),
                         (self.inv.owned_seen, self.inv.recent, self.inv.sync))


def keep_library(target=21):
    """dated(23) whose book ``target`` (21: grid index 1, 22: index 0 in 獲得順) holds a wanted row the library lacks
    (lock) and whose other new book holds only low-value rows (sell); the baseline is the first 21 books."""
    books = dated(23)
    other = 43 - target
    books[target].rows = [("目标A", 5), ("条目甲", 3)]
    books[other].rows = [("ファンの声援", 2), ("目標達成", 1)]
    for b in books[21:]:
        b.protected = False
    return books, baseline(books[:21])


WANTED = [{"name": "目标A", "min_level": 4}]


def keep_run(inv, game, *, lock=True, sell=True, wanted=WANTED, **options):
    options.setdefault("recover_home", False)
    options.setdefault("max_inputs", 158)
    s = ks.ScanSession(ks.ScanOptions.from_params({"topup": "only", "keep_lock": lock, "keep_sell": sell, **options}),
                       kf.read_texts, basis=ks.library_basis(inv), library=inv, wanted=wanted)
    out = kf.run(s, game)
    return s, out


class KeepTests(unittest.TestCase):
    """Keep step: after a W.I.N.G. run lock the planned new books; sales are reported only."""

    def test_locks_the_planned_book_and_only_reports_the_planned_sale(self):
        books, inv = keep_library()
        game = kf.Game(list(books))
        s, out = keep_run(inv, game)
        r = s.result()
        keep = r["keep"]
        self.assertEqual((keep["locked"], keep["would_sell"], keep["outcome"], keep["uncertain"]), ([1], [0], "done", None))
        self.assertEqual((keep["counts_ok"], keep["synced"], keep["sell_executor"]), (True, True, False))
        self.assertEqual(game.presses, [21])                                   # one press, on the bound book
        self.assertTrue(books[21].protected)
        self.assertFalse(books[22].protected)                                 # the sale is not executed
        tail = game.inputs[game.inputs.index("press_protect") - 1:]
        self.assertEqual(tail, ["open_tile", "press_protect", "open_sort", "pick_sort", "apply_sort", "leave"])
        self.assertEqual((out[-1].kind, out[-1].reason, game.sort), ("leave", "newest_done", "デフォルト"))
        self.assertFalse(r["stopped"] or r["sort_restore_needed"])
        summary = ks.apply_to_inventory(r, inv, scanned_at="keep")
        self.assertEqual((summary["topup_added"], inv.sync["state"]), (2, "up_to_date"))
        locked = kb.KnowhowBookRecord.from_dict(next(b for b in r["books"] if b["grid_index"] == 1))
        self.assertIs(inv.books[locked.fingerprint].protected, True)
        self.assertIs(r["books"][0]["signature"][3], False)
        self.assertIs(next(b for b in r["books"] if b["grid_index"] == 1)["signature"][3], True)

    def test_the_book_on_the_panel_is_locked_without_another_tap(self):
        books, inv = keep_library(target=22)                                   # the newest book is read last
        game = kf.Game(list(books))
        s, out = keep_run(inv, game, sell=False)
        self.assertEqual((s.result()["keep"]["locked"], game.presses), ([0], [22]))
        self.assertEqual([d.reason for d in out if d.kind == "open_tile"], ["read_new_book"] * 2)   # the reads only

    def test_switches_off_or_without_a_wanted_list_press_nothing(self):
        for lock, sell, wanted in ((False, True, WANTED), (True, False, []), (False, False, WANTED)):
            with self.subTest(lock=lock, sell=sell, wanted=bool(wanted)):
                books, inv = keep_library()
                game = kf.Game(list(books))
                s, out = keep_run(inv, game, lock=lock, sell=sell, wanted=wanted)
                self.assertEqual((game.presses, out[-1].kind), ([], "leave"))
                keep = s.result()["keep"]
                if not (lock or sell):
                    self.assertIsNone(keep)
                    continue
                self.assertEqual(keep["locked"], [])
                self.assertEqual(keep["would_sell"], [0] if sell and wanted else [])
                if not wanted:
                    self.assertEqual({v["reason"] for v in keep["verdicts"]}, {"no_wanted"})

    def test_a_press_without_the_lock_showing_stops_and_is_never_repeated(self):
        books, inv = keep_library()
        game = kf.Game(list(books), protect_effect="none")
        s, out = keep_run(inv, game)
        r = s.result()
        self.assertEqual((out[-1].kind, out[-1].reason), ("stop", "keep_protect_unconfirmed"))
        self.assertEqual((game.presses, r["keep"]["uncertain"], r["keep"]["presses"]), ([21], 1, 1))
        self.assertTrue(r["stopped"] and r["sort_restore_needed"])             # left where it is: nothing restored
        self.assertEqual(game.inputs[-1], "press_protect")

    def test_a_dialog_after_the_press_stops_with_the_lock_uncertain(self):
        books, inv = keep_library()
        game = kf.Game(list(books), protect_effect="popup")
        s, out = keep_run(inv, game)
        self.assertEqual(out[-1].kind, "stop")
        self.assertIn(out[-1].reason, ("unknown_screen", "popup_unknown", "popup_confirm"))
        self.assertEqual((game.presses, s.result()["keep"]["uncertain"], game.inputs[-1]), ([21], 1, "press_protect"))

    def test_a_book_locked_meanwhile_is_not_pressed_and_the_run_leaves_normally(self):
        class LockedByHand(kf.Game):
            def apply(self, d):
                super().apply(d)
                if d.reason == "keep_open":
                    self.books[self.selected].protected = True

        books, inv = keep_library()
        game = LockedByHand(list(books))
        s, out = keep_run(inv, game)
        keep = s.result()["keep"]
        self.assertEqual((game.presses, keep["outcome"], keep["uncertain"], keep["locked"]),
                         ([], "keep_grid_changed", None, []))
        self.assertEqual((out[-1].kind, game.sort), ("leave", "デフォルト"))   # read-only wind-down

    def test_options(self):
        ok = ks.ScanOptions.from_params({"topup": "only", "keep_lock": True, "keep_strategy": "keep3"})
        self.assertTrue(ok.keep)
        for bad in ({"keep_lock": True}, {"topup": "prefer", "keep_sell": True}, {"newest": 2, "keep_lock": True},
                    {"topup": "only", "keep_lock": 1}, {"topup": "only", "keep_sell": True, "keep_strategy": "all"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ks.ScanOptions.from_params(bad)
        self.assertFalse(ks.ScanOptions().keep)


class ReaderTests(unittest.TestCase):
    def test_thumb_and_popup_selection_on_synthetic_frames(self):
        game = kf.Game(kf.library(30), sort=kb.SORT_EVAL)
        f = game.render()
        self.assertTrue(ks.read_thumb(f, ks.GRID_BAR_X, ks.GRID_TRACK)["at_top"])
        self.assertIsNone(ks.read_thumb(f, ks.DETAIL_BAR_X, ks.DETAIL_TRACK))
        game.top_row = game.max_top
        self.assertTrue(ks.read_thumb(game.render(), ks.GRID_BAR_X, ks.GRID_TRACK)["at_bottom"])
        game.popup, game.popup_selected = "sort", "Lv順"
        self.assertEqual(ks.sort_popup_selection(game.render()), ["Lv順"])


class DetailSettleTests(unittest.TestCase):
    """2026-10-24: the fixed 2 s wait after a detail wheel became 600 ms + a settle proof + host pacing."""

    @staticmethod
    def frames(*shifts):
        import numpy as np
        tall = np.random.default_rng(7).integers(0, 255, (1400, 1280, 3), dtype=np.uint8)
        return [tall[dy:dy + 720].copy() for dy in shifts]

    def test_one_full_notch_is_read_at_once_otherwise_two_identical_captures(self):
        pre, notch, mid, end = self.frames(0, 160, 80, 120)
        s = session()
        s.detail_settle = {"pre": pre, "since": 0}
        self.assertIsNone(s._detail_settling(notch))
        s.detail_settle, s.last_image = {"pre": pre, "since": 0}, pre
        self.assertEqual(s._detail_settling(mid).reason, "detail_settling")     # still moving: no OCR, no input
        s.last_image = mid
        self.assertEqual(s._detail_settling(end).reason, "detail_settling")
        s.last_image = end
        self.assertIsNone(s._detail_settling(end))                               # list end: stopped short, unchanged
        self.assertEqual(s.settle, {"notch": 1, "still": 1, "holds": 2, "pace_holds": 0, "grid_holds": 0,
                                    "grid_pace_holds": 0})

    def test_rows_that_never_settle_stop(self):
        holds = ks.DETAIL_SETTLE_HOLDS
        frames = self.frames(*range(0, 20 * (holds + 2), 20))
        s = session()
        s.detail_settle = {"pre": frames[0], "since": 0}
        out = []
        for img in frames[1:]:
            out.append(s._detail_settling(img))
            s.last_image = img
        self.assertEqual([d.reason for d in out], ["detail_settling"] * holds + ["detail_not_settled"])

    def test_pacing_keeps_the_detail_wheel_under_the_host_limit(self):
        books = kf.library(4, rows=21)
        game = kf.Game(books)
        s = session(**BIG)
        sent = []
        original = game.apply
        game.apply = lambda d: (sent.append(s.clock()) if d.kind == "scroll_detail" else None, original(d))[1]
        out = kf.run(s, game, capture_s=0.6)
        self.assertEqual(out[-1].kind, "leave")
        self.assertTrue(s.result()["details_complete"])
        self.assertGreater(s.settle["pace_holds"], 0)
        self.assertGreater(len(sent), 30)
        self.assertTrue(all(sum(1 for t in sent if a <= t < a + 60) <= 30 for a in sent))


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


@unittest.skipUnless(importlib.util.find_spec("maa") is not None, "MaaFramework Python binding missing")
class RuntimeTests(unittest.TestCase):
    """Maa glue without a controller: Want matching per capture, Halted on stop, Finish writes private files only."""

    def setUp(self):
        import shutil
        import tempfile
        from agent.core import knowhow_scan_actions as ksa
        self.ksa = ksa
        base = ROOT / "dev_assets" / "private"
        base.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="knowhow-scan-test-", dir=base))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sink = _Sink()
        self.rt = ksa.KnowhowScanRuntime(sink=self.sink, run_dir=self.tmp / "runs", inventory_path=self.tmp / "inv.json",
                                         ui_path=self.tmp / "knowhow.js")

    def test_runtime_latest_partial_uses_only_the_current_automatic_scan(self):
        prior = session(scan_id="current", max_books=1, max_inputs=100)
        kf.run(prior, kf.Game(kf.library(3)))
        self.rt.run_dir.mkdir(parents=True)
        matching = self.rt.run_dir / "knowhow-scan-20261018T000001Z-partial.json"
        unrelated = self.rt.run_dir / "knowhow-scan-20261018T000002Z-partial.json"
        matching.write_text(json.dumps(prior.result()), encoding="utf-8")
        unrelated.write_text(json.dumps({**prior.result(), "scan_id": "another"}), encoding="utf-8")
        config = {"scan_id": "current", "resume_record": "latest", "max_inputs": 100}
        self.assertTrue(self.rt.begin(_Context(config), {"config_nodes": ["SC.KnowhowScan.Config"]}))
        self.assertEqual(self.rt.latest_partial("current"), matching)
        self.assertIsNotNone(self.rt.session.resume)
        self.assertEqual(self.rt.session.scan_id, "current")
        # An old CLI option or a new user scan cannot opt into positional reuse just by naming 'latest'.
        self.assertTrue(self.rt.begin(_Context({"resume_record": "latest", "max_inputs": 100}),
                                      {"config_nodes": ["SC.KnowhowScan.Config"]}))
        self.assertIsNone(self.rt.session.resume)

    def drive(self, game):
        """Mimic the Main next-list: the first matching Want node 'performs' its input on the fake game."""
        wants = [{"want": "open_sort"}, {"want": "pick_sort", "key": kb.SORT_EVAL}, {"want": "pick_sort", "key": "デフォルト"},
                 {"want": "pick_sort", "key": kb.SORT_DATE},
                 {"want": "apply_sort"}, {"want": "scroll_grid", "direction": "down"}, {"want": "scroll_grid", "direction": "up"},
                 {"want": "scroll_detail", "direction": "down"}, {"want": "leave"}, {"want": "hold"},
                 {"want": "press_protect"}]
        wants += [{"want": "open_tile", "row": r, "col": c, "key": None} for r in range(ks.ROWS) for c in range(ks.COLS)]
        wants += [{"want": "open_tile", "key": a["id"]} for a in kg.SLOT_ACTIONS]
        for _ in range(800):
            bgr = game.render()[:, :, ::-1]
            if self.rt.want(None, bgr, {"is": "halted"}):
                return "halted"
            hit = next((w for w in wants if self.rt.want(None, bgr, w)), None)
            if hit is None:
                continue
            if hit["want"] == "leave":
                return "left"
            if hit["want"] != "hold":
                game.apply(self.rt.session.pending)
                self.assertTrue(self.rt.sent(None, {}))      # SC.KnowhowScan.Sent.*: the click was confirmed sent
        raise AssertionError("no end")

    def test_animated_background_is_the_same_screen_but_scrolls_and_selections_are_not(self):
        """2026-10-13 kh-full2: every poll differed by background animation, so the slow first observation was redone
        each time, outlived the permit TTL and no open_tile click could pass the frame guard."""
        from tests.maa_probe import rgb

        live = ROOT / "dev_assets/private/knowhow-scan"
        a, b = live / "scan-20261013T101154Z/frame-0001.png", live / "scan-20261013T101154Z/frame-0002.png"
        d7, d8 = live / "scan-20261012T193157Z/frame-0007.png", live / "scan-20261012T193157Z/frame-0008.png"
        empty = live / "scan-20261012T185124Z/frame-0001.png"
        if not all(p.exists() for p in (a, b, d7, d8, empty)):
            self.skipTest("需要 2026-10-12/13 实机扫描私有帧")
        self.assertNotEqual(self.ksa._image_digest(rgb(a)), self.ksa._image_digest(rgb(b)))
        self.assertTrue(self.ksa._same_screen(rgb(a), rgb(b)))
        self.assertFalse(self.ksa._same_screen(rgb(d7), rgb(d8)))        # a detail page scroll
        self.assertFalse(self.ksa._same_screen(rgb(empty), rgb(d8)))     # empty panel -> book opened

    def test_unsent_input_decision_survives_background_animation(self):
        import numpy as np

        self.assertTrue(self.rt.begin(_Context({"max_books": 50, "max_inputs": 400, "max_frames": 400}),
                                      {"config_nodes": ["SC.KnowhowScan.Config"]}))
        self.rt.session.read_texts = kf.read_texts
        game = kf.Game(kf.library(9), sort=kb.SORT_EVAL)
        first = game.render()[:, :, ::-1].copy()
        decision = self.rt.decision(None, first)
        self.assertTrue(decision.is_input)
        shimmer = first.copy()
        shimmer[0:60, 0:300] = np.clip(shimmer[0:60, 0:300].astype(int) + 6, 0, 255).astype("uint8")   # outside the grid
        frames = self.rt.session.frames
        self.assertIs(self.rt.decision(None, shimmer), decision)       # not re-observed, nothing "unconfirmed"
        self.assertEqual((self.rt.session.frames, self.rt.unconfirmed), (frames, 0))

    def test_settle_want_matches_only_a_wheels_settle_check(self):
        for reason, settle in (("grid_settling", True), ("grid_stall_check", True), ("detail_settling", True),
                               ("grid_pace", False), ("detail_pace", False), ("meta_unreadable", False)):
            self.rt.decision = lambda context, image, r=reason: ks.Decision("hold", r)
            self.assertIs(self.rt.want(None, None, {"want": "settle"}), settle, reason)
            self.assertTrue(self.rt.want(None, None, {"want": "hold"}), reason)
        self.rt.decision = lambda context, image: ks.Decision("scroll_grid", "next_screen", direction="down")
        self.assertFalse(self.rt.want(None, None, {"want": "settle"}))

    def test_peek_returns_only_the_decision_already_made_for_this_capture(self):
        self.assertTrue(self.rt.begin(_Context({"max_books": 50, "max_inputs": 400, "max_frames": 400}),
                                      {"config_nodes": ["SC.KnowhowScan.Config"]}))
        self.rt.session.read_texts = kf.read_texts
        game = kf.Game(kf.library(9), sort=kb.SORT_EVAL)
        frame = game.render()[:, :, ::-1].copy()
        self.assertIsNone(self.rt.peek(frame))                          # nothing observed yet: never observes itself
        self.assertFalse(self.rt.want(None, frame, {"want": "open_sort", "peek": True}))
        self.assertEqual(self.rt.session.frames, 0)
        decision = self.rt.decision(None, frame)
        self.assertIs(self.rt.peek(frame.copy()), decision)              # same capture (other buffer, same pixels)
        other = frame.copy()
        other[0, 0] = (other[0, 0].astype(int) + 1) % 256
        self.assertIsNone(self.rt.peek(other))
        p = {"want": decision.kind, **{k: getattr(decision, k) for k in ("key", "row", "col", "direction")
                                       if getattr(decision, k, None) is not None}}
        frames = self.rt.session.frames
        self.assertTrue(self.rt.want(None, frame, {**p, "peek": True}))
        self.assertFalse(self.rt.want(None, other, {**p, "peek": True}))
        self.assertEqual(self.rt.session.frames, frames)

    def _pending_input(self):
        self.assertTrue(self.rt.begin(_Context({"max_books": 50, "max_inputs": 400, "max_frames": 400}),
                                      {"config_nodes": ["SC.KnowhowScan.Config"]}))
        self.rt.session.read_texts = kf.read_texts
        game = kf.Game(kf.library(9), sort=kb.SORT_EVAL)
        self.assertTrue(self.rt.decision(None, game.render()[:, :, ::-1].copy()).is_input)

    def test_denied_input_is_retried_at_most_three_times_and_never_after_a_send(self):
        """2026-10-13 kh-full3: the first open_tile was denied (frame too old, not sent) and the scan aborted at once."""
        history = []
        self.rt.click_history = lambda: history
        self._pending_input()
        history.append({"result": "denied", "reason": "frame_or_host_binding_failed", "attempted": None})
        self.assertEqual([self.rt.retry(None, {}) for _ in range(4)], [True, True, True, False])
        self.assertFalse(self.rt.fresh)                      # the same decision stays pending for the next capture
        self.rt.sent(None, {})                               # a confirmed send resets the budget
        self.assertEqual(self.rt.retries, 0)
        self.rt.fresh = False
        for result in ("sent", "unknown", "refused"):        # sent / uncertain / refused-after-attempt: never re-tried
            history.append({"result": result, "reason": "x", "attempted": True})
            self.assertFalse(self.rt.retry(None, {}), result)
        history.append({"result": "refused", "reason": "x", "attempted": False})
        self.assertTrue(self.rt.retry(None, {}))
        self.rt.click_history = None
        self.assertFalse(self.rt.retry(None, {}))            # unknown history: Abort

    def test_abort_reports_that_the_game_was_left_in_place(self):
        self._pending_input()
        self.rt.stop(None, {"reason": "abort"})
        outcome = self.sink.items[-1][1]
        self.assertEqual(outcome["outcome"], "stopped:abort")
        self.assertTrue(outcome["left_in_place"])

    def test_preset_dialog_is_an_overlay_that_only_stops(self):
        """2026-10-13 kh-full8: the tile tap opened the book and the ノウハウブックプリセット dialog together; the dimmed
        screen matched no node, Sent.List timed out and the run aborted without saying why. Never press 保存."""
        nodes = json.loads((ROOT / "assets/resource/base/pipeline/knowhow_scan.json").read_text(encoding="utf-8"))
        self.assertIn("SC.KnowhowScan.Anchor.Preset.Title", nodes["SC.KnowhowScan.Sent.List"]["any_of"])
        main = nodes["SC.KnowhowScan.Main"]["next"]
        self.assertLess(main.index("SC.KnowhowScan.Overlay.Preset"), main.index("SC.KnowhowScan.Halted"))
        overlay = nodes["SC.KnowhowScan.Overlay.Preset"]
        self.assertEqual((overlay["action"], overlay["custom_action"], overlay["custom_action_param"]),
                         ("Custom", "SC.KnowhowScan.Stop", {"reason": "popup_preset"}))
        self.assertNotIn("next", overlay)
        self.assertEqual(kb.classify_knowhow_screen([("ノウハウブックプリセット", (606, 29, 324, 27), 0.99),
                                                     ("ノウハウブックー覧", (49, 18, 231, 36), 0.97)])["popup"], "preset")
        self.assertFalse(kb.classify_knowhow_screen([("ノウハウブックプリセット", (606, 29, 324, 27), 0.99)])["ok"])

    def test_every_input_node_retries_before_aborting(self):
        nodes = json.loads((ROOT / "assets/resource/base/pipeline/knowhow_scan.json").read_text(encoding="utf-8"))
        inputs = [k for k, v in nodes.items() if v.get("action") == "Click" and v.get("next")
                  and all(n.startswith("SC.KnowhowScan.Sent") for n in v["next"])]
        self.assertGreater(len(inputs), 40)
        self.assertEqual({tuple(nodes[k]["on_error"]) for k in inputs}, {("SC.KnowhowScan.Retry",)})
        self.assertEqual(nodes["SC.KnowhowScan.Retry"]["next"], ["SC.KnowhowScan.Main"])
        self.assertEqual(nodes["SC.KnowhowScan.Retry"]["on_error"], ["SC.KnowhowScan.Abort"])

    def test_native_ocr_failure_stops_cleanly_and_is_never_called_again(self):
        """2026-10-13 kh-full5: an access violation inside run_recognition_direct, then the process died on the next
        poll. One failure (or a stopping task) latches the scan stopped; the native call is not repeated."""
        import types
        import numpy as np

        calls = []

        class Context:
            tasker = types.SimpleNamespace(stopping=False)

            def get_task_job(self):
                return types.SimpleNamespace(running=True)

            def run_recognition_direct(self, *args):
                calls.append(args)
                raise OSError("exception: access violation writing 0x0000000000000020")

        ocr = self.ksa.ContextOcr(Context())
        with self.assertRaises(self.ksa.OcrUnavailable):
            ocr(np.zeros((8, 8, 3), np.uint8))
        with self.assertRaises(self.ksa.OcrUnavailable):
            ocr(np.zeros((8, 8, 3), np.uint8))
        self.assertEqual(len(calls), 1)

        stopping = Context()
        stopping.tasker = types.SimpleNamespace(stopping=True)
        with self.assertRaises(self.ksa.OcrUnavailable):
            self.ksa.ContextOcr(stopping)(np.zeros((8, 8, 3), np.uint8))
        self.assertEqual(len(calls), 1)                      # a stopping task never reaches the native call

        self.assertTrue(self.rt.begin(_Context({"max_books": 50, "max_inputs": 400, "max_frames": 400}),
                                      {"config_nodes": ["SC.KnowhowScan.Config"]}))
        attempts = []

        def broken(image):
            attempts.append(1)
            raise self.ksa.OcrUnavailable("native_ocr_failed:OSError")
        self.rt.session.read_texts = broken
        frame = kf.Game(kf.library(9), sort=kb.SORT_EVAL).render()[:, :, ::-1].copy()
        decision = self.rt.decision(None, frame)
        self.assertEqual((decision.kind, decision.reason), ("stop", "ocr_failed"))
        n = len(attempts)
        self.assertEqual(self.rt.decision(None, frame[:, ::-1].copy()).reason, "ocr_failed")   # even another capture
        self.assertEqual(len(attempts), n)
        self.assertTrue(self.rt.want(None, frame, {"is": "halted"}))
        self.rt.stop(None, {"reason": "abort"})
        self.assertIn("stopped:abort", self.sink.items[-1][1]["outcome"])

    def test_task_ended_mid_detail_keeps_partial_record_and_reports_why(self):
        """2026-10-12 live: desktop locked mid-detail, captures failed, Abort never ran -> no report at all."""
        self.assertTrue(self.rt.begin(_Context({"max_books": 50, "max_inputs": 400, "max_frames": 400}),
                                      {"config_nodes": ["SC.KnowhowScan.Config"]}))
        self.rt.session.read_texts = kf.read_texts
        game = kf.Game(kf.library(9), sort=kb.SORT_EVAL)
        for _ in range(300):                                  # until the first book detail is open
            if self.rt.session.phase == "detail" and self.rt.session.books:
                break
            decision = self.rt.session.observe(game.render())
            self.rt.cache, self.rt.fresh = None, True
            if decision.is_input:
                game.apply(decision)
        self.assertTrue(self.rt.session.books)
        self.assertTrue(self.rt.flush_on_task_end(7))
        [record] = list((self.tmp / "runs").glob("knowhow-scan-*-partial.json"))
        self.assertTrue(json.loads(record.read_text(encoding="utf-8"))["books"])
        self.assertEqual(self.sink.items[-1][1]["outcome"], "stopped:host_capture_failed_streak=7")
        self.assertFalse(self.rt.flush_on_task_end(7))        # reported once

    def test_complete_scan_writes_record_inventory_and_ui_module(self):
        self.assertTrue(self.rt.begin(_Context({"max_books": 50, "max_inputs": 400, "max_frames": 400}),
                                      {"config_nodes": ["SC.KnowhowScan.Config"]}))
        self.rt.session.read_texts = kf.read_texts
        game = kf.Game(kf.library(9))
        self.assertEqual(self.drive(game), "left")
        self.assertTrue(self.rt.want(None, game.render()[:, :, ::-1], {"is": "finished"}))
        self.assertTrue(self.rt.finish(None, {}))
        [record] = list((self.tmp / "runs").glob("knowhow-scan-*.json"))
        self.assertNotIn("partial", record.name)
        inv = kb.load_inventory(self.tmp / "inv.json")
        self.assertEqual(len(inv.books), 9)
        self.assertTrue(inv.grid["complete"])
        self.assertIn('"grid_complete": true', (self.tmp / "knowhow.js").read_text(encoding="utf-8"))
        self.assertEqual(self.sink.items[-1][1]["outcome"], "scanned")

    def test_top_up_statuses_after_a_produce_and_from_the_scan_button(self):
        books = dated(23)
        kb.save_inventory(baseline(books[:21]), self.tmp / "inv.json")
        newer = kf.Book(value=700, rows=[("Q", 2)], title="【新】偶像")
        newer.acquired = "2026/10/26"
        runs = [("only", books, "newest_read", 2), ("only", books, "library_up_to_date", 0),
                ("prefer", books + [newer], "scanned", 1)]       # the 扫描ノウハウ button: a top-up is a scan
        for mode, library, outcome, opened in runs:
            with self.subTest(mode=mode, outcome=outcome):
                self.assertTrue(self.rt.begin(_Context({"topup": mode, "max_inputs": 128}),
                                              {"config_nodes": ["SC.KnowhowScan.Config"]}))
                self.rt.session.read_texts = kf.read_texts
                game = kf.Game(list(library))
                self.assertEqual(self.drive(game), "left")
                self.assertTrue(self.rt.finish(None, {}))
                report = self.sink.items[-1][1]
                self.assertEqual((report["outcome"], game.inputs.count("open_tile")), (outcome, opened))
                self.assertEqual(game.sort, "デフォルト")
        inv = kb.load_inventory(self.tmp / "inv.json")
        self.assertEqual((len(inv.recent), inv.sync["state"], inv.owned_seen["owned"]), (3, "up_to_date", 24))
        self.assertTrue(inv.grid["complete"])
        self.assertFalse(list((self.tmp / "runs").glob("knowhow-scan-*.json")))   # never a full-scan resume record

    def test_lock_run_plans_from_the_saved_wanted_list_and_reports_the_keep_step(self):
        books, inv = keep_library()
        kb.save_inventory(inv, self.tmp / "inv.json")
        self.rt.profile_path = self.tmp / "profile.json"
        self.rt.profile_path.write_text(json.dumps({"prefs": {"knowhow_wanted": WANTED}}), encoding="utf-8")
        config = {"topup": "only", "max_inputs": 158, "keep_lock": True, "keep_sell": True}
        self.assertTrue(self.rt.begin(_Context(config), {"config_nodes": ["SC.KnowhowScan.Config"]}))
        self.rt.session.read_texts = kf.read_texts
        game = kf.Game(list(books))
        self.assertEqual(self.drive(game), "left")
        self.assertTrue(self.rt.finish(None, {}))
        report = self.sink.items[-1][1]
        keep = report["keep"]
        self.assertEqual((report["outcome"], keep["outcome"], keep["uncertain"], keep["sell_executor"]),
                         ("newest_read", "done", None, False))
        self.assertEqual(([b["grid_index"] for b in keep["locked"]], [b["grid_index"] for b in keep["would_sell"]]),
                         ([1], [0]))
        self.assertEqual(game.presses, [21])
        saved = kb.load_inventory(self.tmp / "inv.json")
        locked = [b for b in saved.books.values() if any(r.name == "目标A" for r in b.rows)]
        self.assertEqual([b.protected for b in locked], [True])

    def test_stop_halts_without_matching_any_input_node(self):
        self.assertTrue(self.rt.begin(_Context({}), {"config_nodes": ["SC.KnowhowScan.Config"]}))
        self.rt.session.read_texts = kf.read_texts
        game = kf.Game(kf.library(9), mode="protect")
        self.assertEqual(self.drive(game), "halted")
        self.assertEqual(game.inputs, [])
        self.assertFalse(self.rt.stop(None, {"reason": "halted"}))
        self.assertEqual(self.sink.items[-1][1]["outcome"], "stopped:halted")

    def test_bad_config_is_refused(self):
        self.assertFalse(self.rt.begin(_Context({"max_inputs": -5}), {"config_nodes": ["SC.KnowhowScan.Config"]}))
        self.assertEqual(self.sink.items[-1][1]["outcome"], "config_invalid")


class PipelineContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = json.loads(PIPELINE.read_text(encoding="utf-8"))
        cls.table = json.loads((ROOT / "assets" / "input" / "pipeline_actions.json").read_text(encoding="utf-8"))

    def test_clicks_only_on_bound_nodes(self):
        # Applied state (tools/knowhow_scan_build.py --apply, 2026-10-12): like SC.CardScan.*, the entry is reached only
        # through the supervised KnowhowScan task; no node carries a raw input action.
        # a Click node exists only when the action table binds it (the gate refuses any other click)
        bound = {n for a in self.table["actions"] for n in a["nodes"]}
        for name, node in self.nodes.items():
            self.assertTrue(name.startswith("SC.KnowhowScan."), name)
            action = node.get("action", "Custom")
            self.assertIn(action, ("Custom", "DoNothing", "Click"), name)
            if action == "Click":
                self.assertIn(name, bound)

    def test_actions_are_bound_in_the_table(self):
        existing = {a["id"]: a for a in self.table["actions"]}
        for a in ks.ACTIONS:
            self.assertIn(a["id"], existing)
            self.assertEqual(existing[a["id"]]["family"], ks.FAMILY)
            self.assertFalse(existing[a["id"]]["consuming"], a["id"])
            for node in existing[a["id"]]["nodes"]:
                self.assertIn(node, self.nodes)
        for action_id, nodes in ks.NODE_ADDITIONS.items():
            self.assertIn(action_id, existing)
            for node in nodes:
                self.assertIn(node, existing[action_id]["nodes"])
        self.assertFalse([n for n, v in self.nodes.items() if str(v.get("doc", "")).startswith("input_action_missing:")])

    def test_proposed_rects_are_on_canvas_and_wheel_only_scrolls(self):
        ids = [a["id"] for a in ks.PROPOSED_ACTIONS]
        self.assertEqual(len(ids), len(set(ids)))
        for a in ks.PROPOSED_ACTIONS:
            x, y, w, h = a["rect"]
            self.assertTrue(0 <= x and 0 <= y and x + w <= 1280 and y + h <= 720, a["id"])
            self.assertEqual("wheel" in a, "scroll" in a["id"] or "jump" in a["id"], a["id"])
        # tile rects stay inside the calibrated tile and clear of its マーク / ✓ / lock corners
        for r in range(ks.ROWS):
            for c in range(ks.COLS):
                tx, ty, tw, th = kb.list_tile_box(r, c)
                x, y, w, h = next(a for a in ks.PROPOSED_ACTIONS if a["id"] == f"knowhow-tile-r{r}c{c}")["rect"]
                self.assertTrue(tx + 26 <= x and x + w <= tx + 80 and ty + 28 <= y and y + h <= ty + 86)
        # detail wheel stays clear of the per-row buttons (x 360-420)
        dx, _, dw, _ = next(a for a in ks.PROPOSED_ACTIONS if a["id"] == "knowhow-detail-scroll-down")["rect"]
        self.assertLess(dx + dw, 360)

    def test_every_decision_maps_to_a_pipeline_node(self):
        decisions = [ks.Decision("open_sort", ""), ks.Decision("pick_sort", "", key="デフォルト"),
                     ks.Decision("pick_sort", "", key=kb.SORT_EVAL), ks.Decision("apply_sort", ""),
                     ks.Decision("scroll_grid", "", direction="down"), ks.Decision("scroll_grid", "", direction="up"),
                     ks.Decision("scroll_detail", "", direction="down"), ks.Decision("leave", "")]
        decisions += [ks.Decision("open_tile", "", row=r, col=c) for r in range(ks.ROWS) for c in range(ks.COLS)]
        decisions += [ks.Decision("open_tile", "", key=a["id"], row=0, col=int(a["id"][-1])) for a in kg.SLOT_ACTIONS]
        for d in decisions:
            node = ks.pipeline_node(d)
            self.assertIn(node, self.nodes, d)
            part = next(p for p in self.nodes[node]["all_of"] if p.startswith("SC.KnowhowScan.Want."))
            want = self.nodes[part]["custom_recognition_param"]
            self.assertEqual(want["want"], d.kind)
            for key in ("key", "row", "col", "direction"):
                if key in want:
                    self.assertEqual(want[key], getattr(d, key))
        self.assertIsNone(ks.pipeline_node(ks.Decision("hold", "")))
        self.assertIsNone(ks.pipeline_node(ks.Decision("stop", "")))
        # a slot decision never matches a fixed-tile Want (key null) and vice versa
        fixed = self.nodes["SC.KnowhowScan.Want.GridOpenTileR0C0"]["custom_recognition_param"]
        self.assertIn("key", fixed)
        self.assertIsNone(fixed["key"])

    def test_keep_protect_is_guarded_bound_and_inside_the_button(self):
        name = ks.pipeline_node(ks.Decision("press_protect", "lock_book"))
        node = self.nodes[name]
        self.assertEqual(name, "SC.KnowhowScan.Keep.Protect")
        self.assertIn("SC.KnowhowScan.List.Guard", node["all_of"])
        want = next(p for p in node["all_of"] if p.startswith("SC.KnowhowScan.Want."))
        self.assertEqual(self.nodes[want]["custom_recognition_param"]["want"], "press_protect")
        self.assertIs(node["enabled"], False)
        (action,) = ks.KEEP_ACTIONS
        self.assertEqual(node["target"], action["rect"])
        x, y, w, h = action["rect"]
        self.assertTrue(121 <= x and x + w <= 282 and 642 <= y and y + h <= 685)   # 保護 button; 売却 starts at x 302
        px, py, pw, ph = kb.DETAIL_PROTECT_ROI
        self.assertTrue(px <= x and x + w <= px + pw and py <= y and y + h <= py + ph)
        self.assertIn(f"[JumpBack]{name}", self.nodes["SC.KnowhowScan.Main"]["next"])
        entry = next(a for a in self.table["actions"] if a["id"] == action["id"])
        self.assertEqual((entry["family"], entry["consuming"], entry["nodes"]), (ks.FAMILY, False, [name]))

    def test_slot_nodes_are_guarded_bound_and_listed(self):
        main = self.nodes["SC.KnowhowScan.Main"]["next"]
        table = {a["id"]: a for a in self.table["actions"]}
        for a in kg.SLOT_ACTIONS:
            name = ks.slot_node(a["id"])
            node = self.nodes[name]
            self.assertEqual((node["action"], node["enabled"]), ("Click", False))
            self.assertEqual(node["all_of"][1], "SC.KnowhowScan.List.Guard")     # after the cached Want peek
            self.assertEqual(node["target"], a["rect"])
            self.assertEqual(table[a["id"]]["rect"], a["rect"])
            self.assertEqual(table[a["id"]]["nodes"], [name])
            self.assertFalse(table[a["id"]]["consuming"])
            self.assertIn(f"[JumpBack]{name}", main)
            self.assertEqual(node["on_error"], ["SC.KnowhowScan.Retry"])
        # the fixed tiles stay (calibration mode taps the top screen with them)
        self.assertIn("SC.KnowhowScan.Grid.OpenTile.R0C0", self.nodes)

    def test_settle_holds_recapture_sooner_than_other_holds(self):
        """2026-10-24: a wheel's settle check re-captures through List.Settle (300 ms); List.Hold keeps 800 ms."""
        main = [n.replace("[JumpBack]", "") for n in self.nodes["SC.KnowhowScan.Main"]["next"]]
        self.assertLess(main.index("SC.KnowhowScan.List.Settle"), main.index("SC.KnowhowScan.List.Hold"))
        settle = self.nodes["SC.KnowhowScan.List.Settle"]
        self.assertEqual((settle["all_of"], settle["post_delay"]),
                         (["SC.KnowhowScan.List.Guard", "SC.KnowhowScan.Want.Settle"], 300))
        self.assertEqual(self.nodes["SC.KnowhowScan.Want.Settle"]["custom_recognition_param"], {"want": "settle"})
        self.assertEqual(self.nodes["SC.KnowhowScan.List.Hold"]["post_delay"], 800)
        for name in ("SC.KnowhowScan.Grid.ScrollDown", "SC.KnowhowScan.Grid.ScrollUp", "SC.KnowhowScan.Detail.ScrollDown"):
            self.assertEqual(self.nodes[name]["post_delay"], 600, name)

    def test_hold_node_hit_cap_never_ends_a_segment_before_the_session_does(self):
        """2026-10-23 live seg11: List.Hold max_hit 30 ran out after ~30 grid_settling holds; no node matched and
        SC.KnowhowScan.Main timed out (stopped:abort). The session bounds holds itself (max_holds, max_frames)."""
        for name in ("SC.KnowhowScan.List.Hold", "SC.KnowhowScan.List.Settle", "SC.KnowhowScan.Filter.Hold"):
            self.assertGreaterEqual(self.nodes[name].get("max_hit", 10 ** 9), 2 * ks.DEFAULTS["max_inputs"], name)

    def test_list_page_nodes_after_hold_peek_the_cached_decision_before_the_ocr_guard(self):
        """2026-10-23 round audit: ~50 list-page nodes each re-read the list-title OCR (last slot ~4.5 s from capture).
        List.Back / List.Hold observe the capture first; every later [List.Guard, Want] node peeks, then guards."""
        main = [n.replace("[JumpBack]", "") for n in self.nodes["SC.KnowhowScan.Main"]["next"]]
        hold = main.index("SC.KnowhowScan.List.Hold")
        self.assertLess(main.index("SC.KnowhowScan.List.Back"), hold)
        for name in ("SC.KnowhowScan.List.Back", "SC.KnowhowScan.List.Hold"):
            self.assertEqual(self.nodes[name]["all_of"][0], "SC.KnowhowScan.List.Guard")
            self.assertNotIn("peek", self.nodes[self.nodes[name]["all_of"][1]]["custom_recognition_param"])
        self.assertTrue(self.nodes["SC.KnowhowScan.List.Hold"].get("enabled", True))   # always observes the capture
        peeked = []
        for name in main[hold + 1:]:
            parts = self.nodes.get(name, {}).get("all_of", [])
            if "SC.KnowhowScan.List.Guard" in parts:
                self.assertEqual(len(parts), 2, name)
                self.assertEqual(parts[1], "SC.KnowhowScan.List.Guard", name)
                self.assertIs(self.nodes[parts[0]]["custom_recognition_param"].get("peek"), True, name)
                peeked.append(name)
        self.assertGreaterEqual(len(peeked), 50)
        self.assertIn("SC.KnowhowScan.Grid.OpenSlot.Y5C4", peeked)

    def test_not_in_tasks_interface_or_policy(self):
        for path in [ROOT / "assets" / "interface.json", ROOT / "project_policy.json", *sorted((ROOT / "assets" / "tasks").glob("*.json"))]:
            if path.is_file():
                self.assertNotIn("SC.KnowhowScan", path.read_text(encoding="utf-8"), path)
        self.assertIn("knowhow-scan", self.table["families"])      # applied 2026-10-12; supervised task only

    def test_config_matches_the_option_defaults(self):
        from agent.core import knowhow_calibrate as kc

        attach = dict(self.nodes["SC.KnowhowScan.Config"]["attach"])
        self.assertIsNone(attach.pop("resume_record"))
        self.assertEqual(attach.pop("mode"), "scan")               # default flipped from calibrate to a full scan
        self.assertEqual(kc.CalibrationOptions.from_params(attach.pop("calibration")), kc.CalibrationOptions())
        from agent.core import knowhow_filter as kf

        self.assertEqual(kf.FilterCalibrationOptions.from_params(attach.pop("filter_calibration")),
                         kf.FilterCalibrationOptions())
        self.assertEqual(ks.ScanOptions.from_params(attach), ks.ScanOptions())


# ---- live replay: 2026-10-12 drifting grid ---------------------------------------------------------------------------
LIVE_CAL = ROOT / "dev_assets" / "private" / "knowhow-scan" / "calibrate-20261012T022320Z"
LIVE_RECORD = ROOT / "dev_assets" / "private" / "knowhow-scan" / "knowhow-calibrate-20261012T022820Z-partial.json"
LIVE_FRAMES = tuple(range(17, 41))           # consecutive grid wheel steps 17 -> 40 (41 left the page)
LIVE_VALUE = 900                              # 評価値 stand-in: the run was in デフォルト order (no value overlay)


def _live_ready() -> bool:
    if not (LIVE_RECORD.is_file() and all((LIVE_CAL / f"frame-{i:04d}.png").is_file() for i in LIVE_FRAMES)):
        return False
    if importlib.util.find_spec("maa") is None:
        return False
    from tests.maa_probe import MODEL
    return MODEL.is_dir()


class _ReplayEnd(Exception):
    pass


class _LiveReplay:
    """Each scroll_grid shows the next live frame; holds re-capture the same frame (a static screen)."""

    def __init__(self, frames):
        self.frames, self.i, self.inputs = frames, 0, []

    def render(self):
        return self.frames[self.i]

    def apply(self, d):
        self.inputs.append(d.kind)
        if d.kind != "scroll_grid" or d.direction != "down":
            raise AssertionError(d)
        if self.i + 1 >= len(self.frames):
            raise _ReplayEnd
        self.i += 1


@unittest.skipUnless(_live_ready(), "需要 2026-10-12 实机校准私有帧与记录、ppocr 模型与 MaaFramework")
class LiveDriftReplayTests(unittest.TestCase):
    """ScanSession through the drifted live calibration frames. Offline: frames + Maa OCR, no input.

    The calibration run kept the デフォルト sort, so the replay relabels the sort token as 評価値順 and adds one constant
    評価値 token per OCR pill: tile signatures are then (900, real Lv5 N, real M, real lock) — identity still comes from
    the real pills/locks. Ground truth for the absolute index of every pill comes from the record's measured shifts
    (independent of the locator) and the pill OCR position."""

    @classmethod
    def setUpClass(cls):
        from tests.maa_probe import rgb
        from tests.test_knowhow_grid import _maa_ocr

        cls.images = {i: rgb(LIVE_CAL / f"frame-{i:04d}.png") for i in LIVE_FRAMES}
        # Ground-truth scroll per frame, without tiles: the whole-row step closest to the thumb movement (one thumb px
        # ~37 px here; error <= 48 px < half a row) plus the located sub-row offset. The record's pixel dy is not used:
        # it misread three steps on the repeating same-idol covers (317 / 2 px where the thumb moved one step).
        offsets = {i: kg.locate_rows([], cls.images[i])["offset_px"] for i in LIVE_FRAMES}
        bars = {i: ks.read_thumb(cls.images[i], ks.GRID_BAR_X, ks.GRID_TRACK) for i in LIVE_FRAMES}
        owned = 516
        travel = (ks.GRID_TRACK[1] - ks.GRID_TRACK[0]) - (bars[LIVE_FRAMES[0]]["bottom"] - bars[LIVE_FRAMES[0]]["top"] + 1)
        ratio = (-(-owned // ks.COLS) - ks.ROWS) * kg.PITCH_Y / travel
        cls.scroll = {LIVE_FRAMES[0]: -offsets[LIVE_FRAMES[0]]}
        for i in LIVE_FRAMES[1:]:
            est = (bars[i]["top"] - bars[i - 1]["top"]) * ratio
            steps = [n * kg.PITCH_Y - (offsets[i] - offsets[i - 1]) for n in range(4)]
            best = min(steps, key=lambda d: abs(d - est))
            assert abs(best - est) < kg.PITCH_Y / 3, (i, est, steps)
            cls.scroll[i] = cls.scroll[i - 1] + best
        cls.ocr = {i: _maa_ocr(cls.images[i]) for i in LIVE_FRAMES}
        cls.truth = {}            # absolute index -> (N, M) from the pill OCR
        cls.tops = {}             # frame -> {absolute index: true tile top (pill OCR centre)}
        for i in LIVE_FRAMES:
            cls.tops[i] = {}
            for t, b, _ in kb._tokens(cls.ocr[i]):
                m = kb._LV5.fullmatch(t)
                if not m or b[0] < 500:
                    continue
                cx, cy = kb._center(b)
                top = cy - kg.OCR_PILL_CENTER
                col = round((cx - (kg.GRID_X + kg.TILE / 2)) / kg.PITCH_X)
                index = round((top - kg.GRID_Y + cls.scroll[i]) / kg.PITCH_Y) * ks.COLS + col
                cls.truth.setdefault(index, (int(m[1]), int(m[2])))
                assert cls.truth[index] == (int(m[1]), int(m[2])), (i, index)
                cls.tops[i][index] = top

    @classmethod
    def frame(cls, i, tamper=None):
        texts = []
        for t, b, sc in cls.ocr[i]:
            if t == "デフォルト":
                t = kb.SORT_EVAL
            if tamper is not None:
                t = tamper(t, b)
            texts.append((t, b, sc))
            if kb._LV5.fullmatch(t) and b[0] >= 500:
                cx, cy = kb._center(b)
                top = round(cy - kg.OCR_PILL_CENTER)
                col = round((cx - (kg.GRID_X + kg.TILE / 2)) / kg.PITCH_X)
                if top + 86 >= kg.VIEW_TOP:
                    texts.append((str(LIVE_VALUE), (kg.column_x(col) + 60, top + 86, 44, 22), 0.99))
        return kf.frame(cls.images[i], texts)

    def replay(self, frames, *, until=None, **options):
        s = session(**{**BIG, "details": False, **options})
        game = _LiveReplay(frames)
        screens = []
        for _ in range(400):
            d = s.observe(game.render())
            if s.screen_tiles and (not screens or screens[-1][1] is not s.screen_tiles):
                screens.append((LIVE_FRAMES[game.i], s.screen_tiles, s.screen_n, s.screen_offset))
            if d.kind in ("stop", "leave") or (until is not None and LIVE_FRAMES[game.i] == until and d.kind != "hold"
                                                and screens and screens[-1][0] == until):
                return s, game, d, screens
            if d.is_input:
                try:
                    game.apply(d)
                except _ReplayEnd:
                    return s, game, d, screens
        raise AssertionError("replay did not end")

    def test_full_replay_tracks_offsets_and_absolute_indexes(self):
        s, game, last, screens = self.replay([self.frame(i) for i in LIVE_FRAMES])
        self.assertIsNone(s.final, last)
        self.assertEqual(game.i, len(LIVE_FRAMES) - 1)                       # every frame was read
        self.assertEqual(s.grid_steps, len(LIVE_FRAMES) - 1)
        # settled-gating: the first capture after every wheel is never read
        log = s.log
        for k, e in enumerate(log):
            if e["kind"] == "scroll_grid" and k + 1 < len(log):
                self.assertEqual((log[k + 1]["kind"], log[k + 1]["reason"]), ("hold", "grid_settling"))
        # offset tracking: the bound scroll position equals the record's accumulated shift (px)
        for frame, _, n, offset in screens:
            self.assertLessEqual(abs(n * kg.PITCH_Y - offset - self.scroll[frame]), 4.0, frame)
        self.assertGreater(max(abs(o) for *_, o in screens), 50)              # half a row off the fixed grid
        # absolute indexing: every recorded tile equals the OCR ground truth at that index
        present = {i: sig for i, sig in s.grid.items() if sig is not None}
        self.assertEqual(sorted(present), list(range(len(present))))
        self.assertGreaterEqual(len(present), 5 * 25)                         # 24 frames ~ 3,840 px of content
        checked = 0
        for index, sig in present.items():
            if index in self.truth:
                self.assertEqual(sig[1:3], self.truth[index], index)
                checked += 1
        self.assertGreaterEqual(checked, len(present) - 5)
        sources = {e.get("source") for e in s.step_log}
        self.assertLessEqual(sources, {"tiles+pixels", "tiles"})

    def test_slot_per_tile_lies_in_the_true_cover(self):
        _, _, _, screens = self.replay([self.frame(i) for i in LIVE_FRAMES])
        self.assertGreaterEqual(len(screens), len(LIVE_FRAMES))
        for frame, tiles, _, _ in screens:
            slots = set()
            for index, tile in tiles.items():
                true_top = self.tops[frame].get(index)
                self.assertIsNotNone(true_top, (frame, index))
                x, y, w, h = kg.slot_rect(tile["slot"])
                with self.subTest(frame=frame, index=index):
                    self.assertGreaterEqual(y, true_top + kg.SAFE_MARGIN)
                    self.assertLessEqual(y + h, true_top + kg.TILE - kg.SAFE_MARGIN)
                    self.assertTrue(tile["box"][0] + 30 < x and x + w < tile["box"][0] + 80)
                slots.add(tile["slot"])
            self.assertEqual(len(slots), len(tiles))

    def test_open_decision_on_a_drifted_screen_names_the_right_slot(self):
        s, game, _, screens = self.replay([self.frame(i) for i in LIVE_FRAMES], until=33)
        self.assertEqual(screens[-1][0], 33)
        self.assertGreater(abs(s.screen_offset), 50)
        s.options = dataclasses.replace(s.options, details=True, max_books=5)
        d = s._next_step()
        self.assertEqual(d.kind, "open_tile")
        index = int(d.notes[0].split("=")[1])
        x, y, w, h = kg.slot_rect(d.key)
        true_top = self.tops[33][index]
        self.assertTrue(true_top + kg.SAFE_MARGIN <= y and y + h <= true_top + kg.TILE - kg.SAFE_MARGIN)
        self.assertEqual(ks.pipeline_node(d), ks.slot_node(d.key))
        nodes = json.loads(PIPELINE.read_text(encoding="utf-8"))
        self.assertEqual(nodes[ks.pipeline_node(d)]["target"], [x, y, w, h])

    def test_changed_tile_after_a_wheel_stops(self):
        frames = [self.frame(i) for i in LIVE_FRAMES]
        top_row = min(t for t in self.tops[30].values())

        def tamper(t, b):                    # first row of frame 30 was read on frame 29: change its first pill
            m = kb._LV5.fullmatch(t)
            if m and 520 <= b[0] <= 560 and abs(kb._center(b)[1] - kg.OCR_PILL_CENTER - top_row) < 4:
                n, total = int(m[1]), int(m[2])
                return f"Lv.5+x{n + 1 if n < total else n - 1}/{total}"     # still a valid pill, other N
            return t
        frames[LIVE_FRAMES.index(30)] = self.frame(30, tamper)
        s, game, last, _ = self.replay(frames)
        self.assertEqual(last.kind, "stop")
        self.assertIn(last.reason, ("grid_overlap_lost", "grid_thumb_mismatch"))
        self.assertEqual(LIVE_FRAMES[game.i], 30)
        self.assertEqual(game.inputs.count("scroll_grid"), LIVE_FRAMES.index(30))   # no input after the mismatch


if __name__ == "__main__":
    unittest.main()
