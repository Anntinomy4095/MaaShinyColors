"""デイリー 一括受取 (2026-10-10 user request): pure rules on synthetic OCR plus a native offline replay.

No device. The mission-claim bulk actions are enabled in the table since 2026-10-12 (user approval); the replay runs
offline simulated input only. 2026-10-12: several bulk claims per day, ``not_sent`` vs ``uncertain``.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent.core import mission_bulk as mb
from agent.core import present_claim as pc

ROOT = Path(__file__).resolve().parents[1]
BASE = "dev_assets/private/fes-match-reader-20261007/"
HOME = BASE + "home_after_present_settled.png"
BEFORE = BASE + "missions_before_bulk.png"
CONFIRM = BASE + "missions_bulk_confirm.png"
RESULT = BASE + "missions_bulk_result.png"
AFTER = BASE + "missions_after_bulk_bottom.png"
LATE_POPUP = "dev_assets/private/missions-popup-20261123/home-mission-completed-late.png"
MISSION_HELP = "dev_assets/private/help-popups-20261123/mission-help.png"
LIVE = "dev_assets/private/wing_samples/2026-10-10/20261010T122544308778Z-capture.png"   # silver login + shop, Fes 詳細
NOW = dt.datetime(2026, 10, 7, 13, 35, tzinfo=dt.timezone.utc)


def ocr(text, x, y, w=200, h=30):
    return {"text": text, "box": [x, y, w, h], "score": 0.9}


def row(title, y, progress="1/1", button="受け取る"):
    items = [ocr(title, 392, y), ocr(progress, 600, y + 45, 40, 26)]
    if button:
        items.append(ocr(button, 1065, y + 20, 120, 34))
    return items


def frame():
    return pc.FrameRef("c" * 64, 1, time.monotonic())


BULK = [ocr("一括受取", 1132, 641, 110, 39)]


def decide(list_items, page=BULK, **kw):
    args = dict(page_guard=True, overlay=False, bulk_enabled=True, ledger_block=None, frame=frame())
    args.update(kw)
    return mb.decide(mb.read_list(list_items, page), **args)


class BulkRuleTests(unittest.TestCase):
    LIVE_ROWS = (row("ゲームにログインしよう", 252) + row("マニーショップで買い物をしよう", 390)
                 + row("フェスをプレイしよう", 528, progress="0/1", button="詳細"))

    def test_live_daily_list_claims_once(self):
        decision = decide(self.LIVE_ROWS)
        self.assertEqual((decision.kind, decision.target, decision.rows), ("claim", "mission-bulk-receive", 2))
        self.assertFalse(decision.input_authorized)

    def test_every_doubt_stops(self):
        two = row("ゲームにログインしよう", 252) + row("マニーショップで買い物をしよう", 390)
        # 2026-10-11: no terminating row is needed any more (the list may continue below the screen).
        self.assertEqual(decide(two).kind, "claim")
        cut = row("フェスをプレイしよう", 252) + row("サポートアイドルをトレーニングしよう", 528, progress="")
        self.assertIn("サポートアイドルをトレーニングしよう", mb.read_list(cut, BULK).claimable)   # progress cut off at the edge
        banner = [ocr("購入可能！", 392, 150)] + row("フェスをプレイしよう", 252)
        self.assertEqual(mb.read_list(banner, BULK).claimable, ("フェスをプレイしよう",))
        odd = row("謎のミッション", 252) + row("フェスをプレイしよう", 390, "0/1", "詳細")
        self.assertIn("unknown_daily_mission", decide(odd).reasons)
        self.assertIn("bulk_button_not_enabled", decide(self.LIVE_ROWS, bulk_enabled=False).reasons)
        self.assertIn("bulk_button_count_0", decide(self.LIVE_ROWS, page=[]).reasons)
        self.assertEqual(decide(self.LIVE_ROWS, ledger_block="pending_today").reasons, ("ledger:pending_today",))
        self.assertIn("overlay_present", decide(self.LIVE_ROWS, overlay=True).reasons)

    def test_nothing_to_claim_is_proven_by_rows_and_a_grey_button(self):
        done = row("ゲームにログインしよう", 252, button="CLEAR!!") + row("フェスをプレイしよう", 390, "0/1", "詳細")
        self.assertEqual(decide(done, bulk_enabled=False).kind, "empty")
        self.assertEqual(decide([], bulk_enabled=False).reasons, ("no_claimable_rows_unproven",))

    def test_result_and_terminal(self):
        state, lines, _ = mb.classify_result([ocr("以下の報酬を受け取りました", 495, 210, 300, 30),
                                              ocr("トレーニングチケット ×2", 530, 350, 220, 28)])
        self.assertEqual((state, lines), ("receipt", [{"name": "トレーニングチケット", "kind": "training_ticket",
                                                         "quantity": 2}]))
        self.assertEqual(mb.classify_result([ocr("以下の報酬を受け取りました", 495, 210, 300, 30),
                                             ocr("SSR確定ガシャチケット ×1", 530, 350, 220, 28)])[0], "uncertain")
        self.assertEqual(mb.classify_result([ocr("通信エラー", 495, 210, 300, 30)])[0], "uncertain")
        before = mb.read_list(self.LIVE_ROWS, BULK)
        after = mb.read_list(row("フェスをプレイしよう", 252, "0/1", "詳細")
                             + row("ゲームにログインしよう", 390, button="CLEAR!!"), BULK)
        self.assertEqual(mb.terminal(before, after, False), "received")
        self.assertEqual(mb.terminal(before, after, True), "uncertain")          # button still pink
        self.assertEqual(mb.terminal(before, before, False), "uncertain")        # 受け取る still shown

        # a claimed row with covered progress still shows the CLEAR!! stamp; a 詳細 row stays uncertain
        covered = mb.read_list(row("ゲームにログインしよう", 252, button="CLEAR!!")
                               + row("マニーショップで買い物をしよう", 390, progress="", button="CLEAR!!"), BULK)
        self.assertEqual(mb.terminal(before, covered, False), "received")
        bare = mb.read_list(row("ゲームにログインしよう", 252, button="CLEAR!!")
                            + row("マニーショップで買い物をしよう", 390, progress="", button="詳細"), BULK)
        self.assertEqual(mb.terminal(before, bare, False), "uncertain")


try:
    from tests.maa_probe import maa_ready
    from agent.core import pipeline_actions
    from tools import run_tasks as launcher
    HAVE = maa_ready(HOME, BEFORE, CONFIRM, RESULT, AFTER)
except Exception:  # pragma: no cover
    HAVE = False


class Sink:
    def __init__(self):
        self.items = []

    def add(self, kind, data):
        self.items.append((kind, data))


class Authority:
    def __init__(self, outcome):
        self.outcome, self.marks = outcome, 0

    def click_mark(self):
        self.marks += 1
        return 7

    def clicks_since(self, mark):
        return self.outcome


LOGIN, SHOP, FES = "ゲームにログインしよう", "マニーショップで買い物をしよう", "フェスをプレイしよう"


class BulkLedgerTests(unittest.TestCase):
    """2026-10-12 user decision: several bulk claims per day; only an unproven press blocks, until re-observed."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "bulk.json"
        self.ledger = mb.MissionBulkLedger(self.path)
        self.cycle = pc.cycle_id(NOW)

    def tearDown(self):
        self.dir.cleanup()

    def claim(self, titles):
        decision = decide(sum((row(t, 252 + 138 * i) for i, t in enumerate(titles)), []))
        self.assertEqual(decision.kind, "claim")
        return self.ledger.begin_bulk(self.cycle, decision, NOW, titles)

    def test_confirmed_claims_do_not_block_new_claimable_rows(self):
        first = self.claim([LOGIN])
        self.assertEqual(first["titles"], [LOGIN])
        self.ledger.transition(first["id"], "receipt_observed", NOW)
        self.ledger.transition(first["id"], "received", NOW)
        self.assertIsNone(self.ledger.block_reason(self.cycle))
        # later the same day the shop mission is done: a fresh page shows it claimable -> claim again
        later = row(SHOP, 252) + row(FES, 390, "0/1", "詳細") + row(LOGIN, 528, button="CLEAR!!")
        self.assertEqual(decide(later, ledger_block=self.ledger.block_reason(self.cycle)).kind, "claim")
        second = self.claim([SHOP])
        self.ledger.transition(second["id"], "receipt_observed", NOW)
        self.ledger.transition(second["id"], "received", NOW)
        self.assertEqual([r["state"] for r in mb.MissionBulkLedger(self.path).records], ["received", "received"])

    def test_not_sent_never_blocks(self):
        record = self.claim([LOGIN, SHOP])
        self.ledger.transition(record["id"], "not_sent", NOW, "intent")
        self.assertIsNone(self.ledger.block_reason(self.cycle))
        self.assertEqual(mb.MissionBulkLedger(self.path).records[0]["history"], ["pending", "not_sent"])
        with self.assertRaises(ValueError):    # a not_sent record is final
            self.ledger.transition(record["id"], "uncertain", NOW)

    def test_uncertain_blocks_until_a_fresh_page_resolves_it(self):
        record = self.claim([LOGIN, SHOP])
        self.ledger.transition(record["id"], "uncertain", NOW, "terminal")
        self.assertEqual(self.ledger.block_reason(self.cycle), "uncertain_today")
        claimed = mb.read_list(row(FES, 252, "0/1", "詳細") + row(LOGIN, 390, button="CLEAR!!")
                               + row(SHOP, 528, button="CLEAR!!"), BULK)
        unchanged = mb.read_list(row(LOGIN, 252) + row(SHOP, 390) + row(FES, 528, "0/1", "詳細"), BULK)
        partial = mb.read_list(row(LOGIN, 252) + row(FES, 390, "0/1", "詳細") + row(SHOP, 528, button="CLEAR!!"), BULK)
        # ambiguous pages keep the block
        self.assertEqual(self.ledger.reobserve(self.cycle, partial, True, NOW), [])
        self.assertEqual(self.ledger.reobserve(self.cycle, unchanged, False, NOW), [])   # grey button, rows claimable
        self.assertEqual(self.ledger.reobserve(self.cycle, claimed, True, NOW), [])      # pink button, nothing claimable
        self.assertEqual(self.ledger.block_reason(self.cycle), "uncertain_today")
        self.assertEqual(self.ledger.reobserve(self.cycle, claimed, False, NOW), [(record["id"], "received")])
        self.assertIsNone(self.ledger.block_reason(self.cycle))

    def test_an_earlier_runs_uncertain_record_only_blocks_that_run(self):
        # A later run starts after the existing records (bulk_ledger_start) and decides from its page.
        record = self.claim([LOGIN, SHOP])
        self.ledger.transition(record["id"], "uncertain", NOW, "terminal")
        later_run = len(mb.MissionBulkLedger(self.path).records)
        self.assertEqual(self.ledger.block_reason(self.cycle, 0), "uncertain_today")
        self.assertIsNone(self.ledger.block_reason(self.cycle, later_run))
        page = row(LOGIN, 252) + row(SHOP, 390) + row(FES, 528, "0/1", "詳細")
        self.assertEqual(decide(page, ledger_block=self.ledger.block_reason(self.cycle, later_run)).kind, "claim")

    def test_uncertain_resolved_unclaimed_allows_a_new_press(self):
        record = self.claim([LOGIN, SHOP])
        self.ledger.transition(record["id"], "uncertain", NOW, "intent")
        unchanged = mb.read_list(row(LOGIN, 252) + row(SHOP, 390) + row(FES, 528, "0/1", "詳細"), BULK)
        self.assertEqual(self.ledger.reobserve(self.cycle, unchanged, True, NOW),
                         [(record["id"], "resolved_unclaimed")])
        self.assertEqual(decide(row(LOGIN, 252) + row(SHOP, 390),
                                ledger_block=self.ledger.block_reason(self.cycle)).kind, "claim")

    def test_new_rows_after_a_claim_resolve_received(self):
        record = self.claim([LOGIN])
        self.ledger.transition(record["id"], "uncertain", NOW)
        page = mb.read_list(row(SHOP, 252) + row(FES, 390, "0/1", "詳細") + row(LOGIN, 528, button="CLEAR!!"), BULK)
        self.assertEqual(self.ledger.reobserve(self.cycle, page, True, NOW), [(record["id"], "received")])

    def test_receipt_contradiction_and_legacy_records_stay_blocked(self):
        record = self.claim([LOGIN])
        self.ledger.transition(record["id"], "receipt_observed", NOW)
        self.ledger.transition(record["id"], "uncertain", NOW)
        unchanged = mb.read_list(row(LOGIN, 252) + row(FES, 390, "0/1", "詳細"), BULK)
        self.assertEqual(self.ledger.reobserve(self.cycle, unchanged, True, NOW), [])
        self.assertIsNone(mb.reobserved_state(None, unchanged, True))      # no titles: only an empty page resolves
        empty = mb.read_list(row(FES, 252, "0/1", "詳細") + row(LOGIN, 390, button="CLEAR!!"), BULK)
        self.assertEqual(mb.reobserved_state(None, empty, False), "received")

    def test_other_cycles_are_untouched(self):
        record = self.claim([LOGIN])
        self.ledger.transition(record["id"], "uncertain", NOW)
        self.assertIsNone(self.ledger.block_reason("2026-10-08"))
        page = mb.read_list(row(LOGIN, 252) + row(FES, 390, "0/1", "詳細"), BULK)
        self.assertEqual(self.ledger.reobserve("2026-10-08", page, True, NOW), [])


try:
    from agent.core.mission_claim_actions import MissionRuntime
    HAVE_RUNTIME = True
except Exception:  # pragma: no cover
    HAVE_RUNTIME = False


@unittest.skipUnless(HAVE_RUNTIME, "Requires the maa package")
class BulkFlushTests(unittest.TestCase):
    """2026-10-12 bug: a stop before any press (Bulk.Click disabled) wrote ``uncertain``; it is ``not_sent`` now."""

    def flush(self, phase, authority=None, mark=True):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bulk.json"
            sink = Sink()
            rt = MissionRuntime(sink, input_enabled=True, bulk_ledger_path=path, clock=lambda: NOW, sample_root=None,
                                authority=authority)
            rt.started, rt.mode = True, "bulk"
            rt.bulk_ledger = mb.MissionBulkLedger(path)
            rt.bulk = mb.read_list(row(LOGIN, 252) + row(SHOP, 390), BULK)
            rt.bulk_record = rt.bulk_ledger.begin_bulk(pc.cycle_id(NOW), decide(row(LOGIN, 252) + row(SHOP, 390)),
                                                       NOW, rt.bulk.claimable)
            rt.phase = phase
            if mark and authority is not None:
                rt.bulk_click_mark = authority.click_mark()
            rt.reason = "intent"
            rt.flush_on_task_end()
            record, = mb.MissionBulkLedger(path).records
            return record, sink.items[-1][1]

    def test_guard_never_passed_is_not_sent(self):
        record, report = self.flush("bulk_ready")
        self.assertEqual((record["history"], report["outcome"]), (["pending", "not_sent"], "bulk_not_sent"))
        self.assertEqual(record["titles"], [LOGIN, SHOP])

    def test_controller_refusal_is_not_sent(self):
        record, report = self.flush("bulk_requested", Authority("not_sent"))
        self.assertEqual((record["state"], report["outcome"]), ("not_sent", "bulk_not_sent"))

    def test_sent_or_unknown_press_is_uncertain(self):
        for authority in (Authority("sent"), Authority("unknown"), None):
            record, report = self.flush("bulk_requested", authority)
            self.assertEqual((record["state"], report["outcome"]), ("uncertain", "uncertain"), authority)
        record, _ = self.flush("bulk_requested", Authority("not_sent"), mark=False)   # no mark: cannot prove
        self.assertEqual(record["state"], "uncertain")
        record, _ = self.flush("bulk_confirm_requested", Authority("not_sent"))      # the press itself went out
        self.assertEqual(record["state"], "uncertain")

    def test_ready_guard_takes_the_controller_mark(self):
        import numpy as np
        from agent.core import mission_claim_actions as mca

        authority = Authority("sent")
        rt = MissionRuntime(Sink(), input_enabled=True, sample_root=None, authority=authority)
        rt.mode, rt.phase, rt.bulk_record = "bulk", "bulk_ready", {"id": "x", "state": "pending"}
        image = np.zeros((720, 1280, 3), "uint8")
        rt.bulk = "rows-read-before"
        recognizer = mca.MissionReadyRecognition(rt)
        context = Mock()
        # 2026-10-12 live: the banner above the rows animates, so the press binds to a semantic re-read, not pixels
        for reread, ready in (("rows-changed", False), ("rows-read-before", True)):
            with patch.object(mca, "_overlay_clear", return_value=True), \
                    patch("agent.core.present_actions.bulk_enabled", return_value=True), \
                    patch.object(mca, "_ocr_items", return_value=[]), \
                    patch.object(mca.mb, "read_list", return_value=reread):
                result = mca._bulk_ready(recognizer, context, image, "bulk_click")
            self.assertEqual(result is not None, ready, reread)
            if not ready:
                self.assertEqual((rt.phase, authority.marks), ("bulk_ready", 0))
        self.assertEqual((rt.phase, rt.bulk_click_mark, authority.marks), ("bulk_requested", 7, 1))


class EnabledTableTests(unittest.TestCase):
    """2026-10-12 user approval: the three bulk actions are enabled and reach their Click nodes for MissionsClaim."""

    IDS = ("mission-bulk-receive", "mission-bulk-confirm", "mission-bulk-result-close")
    NODES = ("SC.Missions.Bulk.Click", "SC.Missions.Bulk.ConfirmClick", "SC.Missions.Bulk.ResultClose")

    def test_table_entries_are_enabled_non_consuming(self):
        from agent.core import pipeline_actions as pa

        table = pa.default_table()
        raw = {a["id"]: a for a in json.loads((ROOT / "assets/input/pipeline_actions.json").read_text(
            encoding="utf-8"))["actions"]}
        for aid in self.IDS:
            action = table.get(aid)
            self.assertTrue(action.enabled, aid)
            self.assertFalse(action.consuming, aid)
            self.assertEqual(action.family, "mission-claim")
            self.assertIn("2026-10-12", raw[aid]["source"])
            self.assertNotIn("RESERVED", raw[aid]["source"])

    def test_live_mode_enables_the_bulk_click_nodes_for_missions_claim(self):
        try:
            from tools import run_tasks
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"launcher unavailable: {exc}")
        override = run_tasks.mode_override(run_tasks.LIVE_INPUT, run_tasks.SUPERVISED_TASK_FAMILIES["MissionsClaim"])
        for node in self.NODES:
            self.assertEqual(override.get(node), {"enabled": True}, node)
        # the default task option reaches the bulk mode; the developer single-login option is unchanged
        task = json.loads((ROOT / "assets/tasks/missions_claim.json").read_text(encoding="utf-8"))
        option = task["option"]["任务奖励领取"]
        default = [c for c in option["cases"] if c["name"] == option["default_case"]][0]
        self.assertEqual(default["pipeline_override"]["SC.Missions.Config"]["attach"],
                         {"enabled": True, "mode": "bulk", "max_attempts": 1, "weekly": True})
        single = [c for c in option["cases"] if c["name"] == "开启（每日登录，单次）"][0]
        self.assertEqual(single["pipeline_override"]["SC.Missions.Config"]["attach"],
                         {"enabled": True, "mission_id": "daily.login", "max_attempts": 1})


@unittest.skipUnless(HAVE, "Requires Maa OCR and the private native mission frames")
class BulkReplayTests(unittest.TestCase):
    def test_shared_bulk_pipeline_chain_allows_two_passes(self):
        """Exercise Maa's max_hit counters for the shared chain without game recognition or input."""
        from tests.maa_probe import NodeProbe
        names = ["SC.Missions.Bulk." + name for name in ("Intent", "Click", "ConfirmClick", "ResultClose")]
        overrides = {"SC.Test.Start": {"recognition": "DirectHit", "next": [names[0], "SC.Test.Done"]},
                     "SC.Test.Done": {"recognition": "DirectHit", "action": "DoNothing", "next": []}}
        for index, name in enumerate(names):
            overrides[name] = {"enabled": True, "recognition": "DirectHit", "action": "DoNothing",
                               "pre_delay": 0, "post_delay": 0, "on_error": [],
                               "next": [names[index + 1] if index + 1 < len(names) else "SC.Test.Start"]}
        visited = NodeProbe().run(HOME, overrides)
        for name in names:
            self.assertEqual(visited.count(name), 2, (name, visited))
        self.assertEqual(visited[-1], "SC.Test.Done")

    def replay(self, frames, option="开启（仅デイリー一括）", activate=True, ledger=None, advances=None):
        table = pipeline_actions.default_table()
        copy = dataclasses.replace(table, actions=tuple(
            dataclasses.replace(a, enabled=activate) if a.id.startswith("mission-bulk-") else a
            for a in table.actions))
        with tempfile.TemporaryDirectory(dir=ROOT / "dev_assets/private") as directory:
            ledger = ledger or Path(directory) / "bulk.json"
            with patch.object(pipeline_actions, "default_table", return_value=copy):
                summary = launcher.run({"task_timeout_s": 45, "tasks": [
                    {"name": "MissionsClaim", "option": {"任务奖励领取": option}}]},
                    offline_steps=[(ROOT / f, (advances or {}).get(i, "click")) for i, f in enumerate(frames)], simulate_input=True,
                    task_options={"missions": {"bulk_ledger_path": ledger, "clock": lambda: NOW,
                                              "sample_root": Path(directory) / "samples"}},
                    summary_path=False, save_diagnostics=False)
            records = json.loads(ledger.read_text(encoding="utf-8"))["records"] if ledger.exists() else []
        task, = summary["tasks"]
        report = [r for r in task["reports"] if r["kind"] == "missions_claim"][-1]
        return task, report, records

    def test_native_bulk_claim_is_verified_and_returns_home(self):
        task, report, records = self.replay([BEFORE, CONFIRM, RESULT, AFTER, HOME])
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "bulk_received"), report)
        self.assertEqual(task["simulated_actions"], ["mission-bulk-receive", "mission-bulk-confirm",
                                                     "mission-bulk-result-close", "nav-back"])
        self.assertEqual(task["simulated_spends"], [])
        self.assertEqual(records[0]["history"], ["pending", "receipt_observed", "received"])
        self.assertEqual(report["receipt"]["items"][0]["kind"], "training_ticket")

    @unittest.skipUnless((ROOT / LATE_POPUP).is_file(), "Requires the 2026-11-23 late ミッション達成 frame")
    def test_mission_completed_popup_after_home_is_closed_not_a_stop(self):
        # 2026-11-23 live: back home from Match, AtHome matched, then ミッション達成 opened inside HomeClean's 3 s and
        # the task stopped. AtHome, HomeClean and Open now also try CloseKnown (Open may click once more after it).
        # The popup opens after 4 captures (inside HomeClean, the live case), 5 (before Open) or 6 (after Open's click).
        for captures in (4, 5, 6):
            with self.subTest(captures=captures):
                task, report, _ = self.replay([HOME, LATE_POPUP, HOME, BEFORE, CONFIRM, RESULT, AFTER, HOME],
                                              advances={0: captures})
                self.assertEqual((task["status"], report["outcome"]), ("succeeded", "bulk_received"), report)
                self.assertIn("popup-mission-completed-ok", task["simulated_actions"])
                self.assertEqual(task["simulated_actions"].count("mission-bulk-receive"), 1)

    @unittest.skipUnless((ROOT / MISSION_HELP).is_file(), "Requires the 2026-11-23 new-PC デイリーミッション help frame")
    def test_first_visit_help_over_the_list_is_closed(self):
        # 2026-11-23 on a new PC: the list showed, then デイリーミッションについて opened over it and the task stopped.
        # 4 (help opens as BulkRead captures: the live stop) and 5 (before the intent) failed before every waiting node
        # ended with the [JumpBack]SC.Popup.CloseKnown fallback (tools/add_popup_fallback.py).
        for captures in (1, 2, 3, 4, 5):
            with self.subTest(captures=captures):
                task, report, _ = self.replay([BEFORE, MISSION_HELP, BEFORE, CONFIRM, RESULT, AFTER, HOME],
                                              advances={0: captures})
                self.assertEqual((task["status"], report["outcome"]), ("succeeded", "bulk_received"), report)
                self.assertIn("popup-help-close", task["simulated_actions"])
                self.assertEqual(task["simulated_actions"].count("mission-bulk-receive"), 1)

    def test_reserved_actions_send_nothing(self):
        # the 2026-10-12 live run: Bulk.Click disabled -> on_error Stop.Intent before any press -> not_sent
        task, report, records = self.replay([BEFORE, BEFORE], activate=False)
        self.assertEqual(task["simulated_actions"], [])
        self.assertEqual(report["outcome"], "bulk_not_sent", report)
        self.assertEqual(records[0]["history"], ["pending", "not_sent"])

    def test_second_claim_same_day_after_a_confirmed_claim(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "dev_assets/private") as directory:
            ledger = Path(directory) / "bulk.json"
            self.replay([BEFORE, CONFIRM, RESULT, AFTER, HOME], ledger=ledger)
            # a later run of the same game day reads claimable rows again: a new press is allowed
            task, report, records = self.replay([BEFORE, CONFIRM, RESULT, AFTER, HOME], ledger=ledger)
        self.assertEqual(report["outcome"], "bulk_received", report)
        self.assertEqual(task["simulated_actions"][0], "mission-bulk-receive")
        self.assertEqual([r["state"] for r in records], ["received", "received"])

    @unittest.skipUnless(maa_ready(LIVE), "Requires the 2026-10-10 live mission frame")
    def test_live_silver_login_day_is_a_bulk_candidate(self):
        from tests.maa_probe import ocr_items
        daily = mb.read_list(ocr_items(LIVE, (255, 100, 975, 492)), ocr_items(LIVE, mb.BULK_ROI))
        self.assertEqual(daily.claimable, ("ゲームにログインしよう", "マニーショップで買い物をしよう"))
        self.assertTrue(daily.terminated)
        self.assertEqual(daily.bulk_buttons, 1)


WEEKLY_FRAME = "dev_assets/private/missions-weekly-20261015/weekly-claimable.png"
DAILY_FRAME = "dev_assets/private/missions-weekly-20261015/daily-claimable.png"


class WeeklyTabTests(unittest.TestCase):
    """2026-10-15 user: デイリー and ウィークリー rewards in one visit; progress = the topmost unfinished row."""

    def test_combined_claim_finishes_each_tab_once_and_rejects_a_repeat(self):
        import numpy as np
        from agent.core import mission_claim_actions as mca
        rt = MissionRuntime(Sink(), input_enabled=True, sample_root=None)
        action = mca.MissionAction(rt)
        rt.mode, rt.tab, rt.phase = "bulk", "daily", "bulk_received"
        action._after_tab()
        self.assertEqual((rt.tabs_done, rt.next_tab, rt.phase), ({"daily"}, "weekly", "tab_switch"))
        rt.tab, rt.phase = "weekly", "bulk_received"
        action._after_tab()
        self.assertEqual((rt.tabs_done, rt.phase), ({"daily", "weekly"}, "bulk_received"))
        rt.phase = "bulk_reading"
        with patch.object(mca, "_latest_image", return_value=np.zeros((720, 1280, 3), dtype="uint8")), \
                patch.object(mca.mb, "selected_tab", return_value="daily"), patch.object(rt, "save_evidence"):
            self.assertFalse(action.bulk_read(Mock(), {}))
        self.assertEqual(rt.reason, "mission_tab_repeated")

    def test_daily_only_from_weekly_switches_once_then_daily_finishes(self):
        import numpy as np
        from agent.core import mission_claim_actions as mca
        rt = MissionRuntime(Sink(), input_enabled=True, sample_root=None)
        action = mca.MissionAction(rt)
        context = Mock()
        context.get_node_data.return_value = {"attach": {"enabled": True, "mode": "bulk", "max_attempts": 1,
                                                         "weekly": False}}
        self.assertTrue(action.begin(context, {}))
        with patch.object(mca, "_latest_image", return_value=np.zeros((720, 1280, 3), dtype="uint8")), \
                patch.object(mca.mb, "selected_tab", return_value="weekly"):
            self.assertTrue(action.bulk_read(context, {}))
        self.assertEqual((rt.phase, rt.next_tab, rt.tabs_done), ("tab_switch", "daily", set()))
        rt.tab, rt.phase = "daily", "bulk_already_claimed"
        action._after_tab()
        self.assertEqual(rt.tabs_done, {"daily"})
        self.assertNotEqual(rt.phase, "tab_switch")

    def test_weekly_cycle_starts_sunday_0500_jst(self):
        import datetime as dt
        jst = dt.timezone(dt.timedelta(hours=9))
        self.assertEqual(mb.weekly_cycle_id(dt.datetime(2026, 10, 15, 22, 0, tzinfo=jst)), "W-2026-10-11")
        self.assertEqual(mb.weekly_cycle_id(dt.datetime(2026, 10, 18, 4, 59, tzinfo=jst)), "W-2026-10-11")
        self.assertEqual(mb.weekly_cycle_id(dt.datetime(2026, 10, 18, 5, 0, tzinfo=jst)), "W-2026-10-18")

    def test_top_incomplete_row_is_the_progress(self):
        rows = [{"title": "a", "status": "claimable", "title_y": 150, "progress": [3, 3]},
                {"title": "b", "status": "incomplete", "title_y": 290, "progress": [2, 5]},
                {"title": "c", "status": "incomplete", "title_y": 430, "progress": [0, 15]},
                {"title": "d", "status": "claimed", "title_y": 570, "progress": [5, 5]}]
        self.assertEqual(mb.top_incomplete(rows), {"title": "b", "progress": [2, 5]})
        self.assertIsNone(mb.top_incomplete(rows[:1]))

    def test_weekly_titles_are_known_only_on_the_weekly_tab(self):
        daily = mb.DailyList(({"title": "フェスアイドルを3人誕生させよう", "progress": [3, 3], "button": "受け取る",
                               "status": "claimable", "title_y": 150},), ("フェスアイドルを3人誕生させよう",), True, 1)
        args = dict(page_guard=True, overlay=False, bulk_enabled=True, ledger_block=None,
                    frame=mb.pc.FrameRef("a" * 64, 1, 0.0))
        self.assertIn("unknown_daily_mission", mb.decide(daily, **args).reasons)
        self.assertEqual(mb.decide(daily, titles=mb.WEEKLY_TITLES, **args).kind, "claim")

    @unittest.skipUnless((ROOT / WEEKLY_FRAME).is_file() and (ROOT / DAILY_FRAME).is_file(), "private mission frames absent")
    def test_selected_tab_on_native_frames(self):
        from PIL import Image
        import numpy as np
        self.assertEqual(mb.selected_tab(np.array(Image.open(ROOT / WEEKLY_FRAME).convert("RGB"))), "weekly")
        self.assertEqual(mb.selected_tab(np.array(Image.open(ROOT / DAILY_FRAME).convert("RGB"))), "daily")


if __name__ == "__main__":
    unittest.main()


TERMINAL = "dev_assets/private/run_logs/samples/missions-bulk-terminal-20261013T070303775422Z/frame-001.png"


@unittest.skipUnless((ROOT / TERMINAL).is_file() and HAVE, "Requires Maa OCR and the private 2026-10-13 terminal frame")
class BulkTerminalFrameTests(unittest.TestCase):
    """2026-10-13 live: every row CLEAR!!, 一括受取 grey. Must verify; the same rows with a pink button must not."""

    def test_all_claimed_frame_is_a_verified_terminal(self):
        import numpy as np
        from PIL import Image

        from agent.core import present_actions as pa
        from tests.maa_probe import ocr_items

        li = ocr_items(TERMINAL, (255, 100, 975, 492))
        page = ocr_items(TERMINAL, mb.BULK_ROI)
        after = mb.read_list(li, page)
        with Image.open(ROOT / TERMINAL) as im:
            enabled = pa.bulk_enabled(np.ascontiguousarray(np.asarray(im.convert("RGB"))[:, :, ::-1]))
        self.assertIs(enabled, False)
        self.assertEqual(after.claimable, ())
        before = mb.read_list(BulkRuleTests.LIVE_ROWS, BULK)
        self.assertEqual(mb.terminal(before, after, enabled), "received")
        self.assertEqual(mb.terminal(before, after, True), "uncertain")
        self.assertEqual(mb.terminal(before, after, None), "uncertain")
