"""W.I.N.G. 连跑: 「Produce次数」 2–5 runs back to back in one UI run (tools/ui_engine.wing_main).

Each run keeps the one-start rules of tools/wing_segments.py; the next run starts only after the previous Produce ended
at home by Maa itself (獲得報酬 OK), its new books were read with the keep plan (knowhow_scan keep_plan), no book filled
a wanted entry the library lacked (knowhow_keep Need.fills), and the spend ledger has no unresolved start. Fake
launcher, no game input."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.core import knowhow_books as kb
from agent.core import knowhow_keep as kk
from agent.core import wing_ledger as wl
from tests.test_wing_ledger import P, summary
from tools import ui_engine, ui_wing, wing_segments as ws

ROOT = Path(__file__).resolve().parents[1]


def book(title, rows, count=1):
    return kb.KnowhowBookRecord.from_dict({"title": title, "idol": "浅倉透", "count": count, "knowhow_count": len(rows),
                                           "stats": {"vo": 1, "da": 1, "vi": 1, "me": 1},
                                           "rows": [{"name": n, "level": lv} for n, lv in rows]})


def library(*books):
    inv = kb.KnowhowInventory()
    for b in books:
        inv.books[b.fingerprint] = b
    return inv


class TargetHitTests(unittest.TestCase):
    def plan(self, new, have, wanted):
        return kk.plan_new_books([new], library(*have), wanted, kk.KeepSettings(), in_inventory=False)

    def fills(self, new, have, wanted):
        return [(n["name"], n["fills"]) for v in self.plan(new, have, wanted)["verdicts"] for n in v["needs"]]

    def test_a_missing_wanted_entry_is_a_hit(self):
        wanted = [{"name": "Center適正◎", "match": "exact"}, {"name": "スタートダッシュ", "match": "exact", "min_level": 3}]
        new = book("n", [("Center適正◎", 2), ("スタートダッシュ", 4)])
        have = [book("a", [("スタートダッシュ", 3)])]
        self.assertEqual(self.fills(new, have, wanted), [("Center適正◎", True), ("スタートダッシュ", False)])
        hits = kk.target_hits(self.plan(new, have, wanted))
        self.assertEqual(hits, [{"book": new.fingerprint, "title": "n", "entries": ["Center適正◎ Lv2"]}])

    def test_better_siblings_and_prefix_names_cover_the_entry(self):
        # ◎ covers a wanted ◯, ++ a wanted +, any matching name a prefix item; a Lv below the min Lv does not count
        cases = [
            ([{"name": "Center適正◯", "match": "exact"}], ("Center適正◯", 1), [("Center適正◎", 3)], False),
            ([{"name": "Center適正◎", "match": "exact"}], ("Center適正◎", 1), [("Center適正◯", 5)], True),
            ([{"name": "思い出(ノウハウ)+", "match": "exact"}], ("思い出(ノウハウ)+", 2), [("思い出(ノウハウ)++", 1)], False),
            ([{"name": "アイドル(", "match": "prefix"}], ("アイドル(say\"Halo\")", 3), [("アイドル(say\"Halo\")", 1)], False),
            ([{"name": "スロースターター", "match": "exact", "min_level": 3}], ("スロースターター", 3), [("スロースターター", 2)], True),
        ]
        for wanted, row, held, fills in cases:
            got = self.fills(book("n", [row]), [book("h", held)], wanted)
            self.assertEqual(got, [(row[0], fills)], (wanted, row, held))

    def test_lesser_names(self):
        self.assertEqual(kk.lesser_names("Center適正◎"), ["Center適正◎", "Center適正◯"])
        self.assertEqual(kk.lesser_names("思い出(ノウハウ)++"), ["思い出(ノウハウ)++", "思い出(ノウハウ)+"])
        self.assertEqual(kk.lesser_names("LandingPoint大成功(SP)++"), ["LandingPoint大成功(SP)++", "LandingPoint大成功(SP)+"])
        self.assertEqual(kk.lesser_names("VocalUP++"), ["VocalUP++"])       # heads count by tier elsewhere


def scan_result(*, fills=False, uncertain=None, status="succeeded"):
    needs = [{"name": "Center適正◎", "level": 2, "want": "Center適正◎", "have": 0, "fills": fills}]
    keep = {"verdicts": [{"book": "b1", "title": "【x】浅倉透", "action": "keep", "reason": "scarce", "needs": needs}],
            "uncertain": uncertain, "outcome": "done"}
    return {"tasks": [{"name": "KnowhowScan", "status": status, "fail_reasons": [], "reports": [
        {"kind": "knowhow_scan", "outcome": "newest_read", "books": 1, "keep": keep,
         "topup": {"mode": "only", "n": 1, "reason": "topup", "owned_now": 500},
         "inventory": {"topup_added": 1, "sync": {"state": "up_to_date"}}}]}], "failed": 0, "succeeded": 1}


def home_result():
    out = summary(status="succeeded", reasons=())
    out["tasks"][0]["reports"][0]["outcome"] = "produce_finished_home"
    return out


class NextRunTests(unittest.TestCase):
    def test_every_doubt_ends_the_loop(self):
        after = ws.knowhow_after_summary(scan_result())
        ok = dict(remaining_s=10_000, min_s=5_000)
        self.assertEqual(ws.next_run_verdict(home_result(), after, **ok), (True, None))
        handover = summary(status="succeeded", reasons=())
        self.assertEqual(ws.next_run_verdict(handover, after, **ok), (False, "run_not_home"))
        self.assertEqual(ws.next_run_verdict(home_result(), None, **ok), (False, "books_unread"))
        # 2026-10-27 live: read and planned, then the way home failed (a new gacha banner): the books count as read
        self.assertEqual(ws.next_run_verdict(home_result(), ws.knowhow_after_summary(scan_result(status="failed")), **ok),
                         (True, None))
        unread = ws.knowhow_after_summary(scan_result(status="failed"))
        unread["outcome"] = "stopped:lost"
        self.assertEqual(ws.next_run_verdict(home_result(), unread, **ok), (False, "books_unread"))
        budget = ws.knowhow_after_summary(scan_result(status="failed"))
        budget["keep"] = {**budget["keep"], "outcome": "budget"}
        self.assertEqual(ws.next_run_verdict(home_result(), budget, **ok), (False, "books_unread"))
        self.assertEqual(ws.next_run_verdict(home_result(), ws.knowhow_after_summary(scan_result(uncertain=3)), **ok),
                         (False, "keep_uncertain"))
        self.assertEqual(ws.next_run_verdict(home_result(), ws.knowhow_after_summary(scan_result(fills=True)), **ok),
                         (False, "target_hit"))
        self.assertEqual(ws.next_run_verdict(home_result(), after, remaining_s=100, min_s=5_000), (False, "time_cap"))
        no_library = ws.knowhow_after_summary(scan_result())
        no_library["keep"] = {**no_library["keep"], "outcome": "no_library", "verdicts": []}
        self.assertEqual(ws.next_run_verdict(home_result(), no_library, **ok), (False, "books_unread"))
        self.assertEqual(ws.next_run_verdict(home_result(), after, stop=True, **ok), (False, "stop_requested"))

    def test_panel_report_is_counts_only(self):
        report = ws.knowhow_after_report(ws.knowhow_after_summary(scan_result(fills=True)))
        self.assertEqual(report, {"status": "succeeded", "outcome": "newest_read", "read": True, "new": 1, "hits": 1,
                                  "locked": 0, "lock_uncertain": False})
        self.assertEqual(ws.knowhow_after_report(None)["read"], False)
        flat = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("浅倉透", flat)                                   # names stay in the run summary

    def test_plan_only_keep_step_needs_the_top_up(self):
        from agent.core import knowhow_scan as ks

        base = {"details": True, "topup": "only"}
        options = ks.ScanOptions.from_params({**base, "keep_plan": True})
        self.assertTrue(options.keep)
        self.assertFalse(options.keep_lock or options.keep_sell)       # nothing is pressed for a plan-only run
        with self.assertRaises(ValueError):
            ks.ScanOptions.from_params({"details": True, "topup": "off", "keep_plan": True})
        with self.assertRaises(ValueError):
            ks.ScanOptions.from_params({**base, "keep_plan": "yes"})

    def test_run_count_and_plan_option(self):
        self.assertEqual(ws.run_count({"tasks": [{"option": {"produce_config": {"caps": {"max_runs": 3}}}}]}), 3)
        self.assertEqual(ws.run_count({"tasks": [{"option": {"produce_config": {"caps": {"max_runs": 9}}}}]}), ws.RUNS_MAX)
        self.assertEqual(ws.run_count({"tasks": [{"option": {}}]}), 1)
        self.assertNotIn("keep_plan", ws.knowhow_after_config(None)["tasks"][0]["option"])
        self.assertIs(ws.knowhow_after_config(None, plan=True)["tasks"][0]["option"]["keep_plan"], True)


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "dev_assets" / "private")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.write_profile({"Produce次数": "2", "Produce报酬后OK": "Yes"})
        self.args = argparse.Namespace(profile=self.dir / "profile.json", events=self.dir / "events.jsonl",
                                       stop_file=self.dir / "stop.flag", summary=self.dir / "summary.json",
                                       session=self.dir / "session.json", offline_frames=None, wing=True,
                                       allow_produce_spend=True)
        for target, name in ((ui_wing, "record_snapshot"), (ui_wing, "check_memory")):
            patch = mock.patch.object(target, name, return_value=None)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(ws, "KNOWHOW_AFTER", True)
        patch.start()
        self.addCleanup(patch.stop)

    def write_profile(self, extra):
        (self.dir / "profile.json").write_text(json.dumps({"schema_version": 1, "tasks": [
            {"name": "AutoProduce", "enabled": True, "option": {"ProduceP卡": P, "Produce可开新局": "Yes", **extra}}]}),
            encoding="utf-8")

    def fake(self, results, ledger=None, intent_after=None):
        calls = []

        def run(config, *, control, summary_path, **kw):
            calls.append(json.loads(json.dumps(config)))
            control.emit("run.started")
            control.emit("task.started", task=config["tasks"][0]["name"])
            result = results[len(calls) - 1]
            control.emit("task.finished", task=config["tasks"][0]["name"], status=result["tasks"][0]["status"], reports_complete=True)
            control.emit("run.finished", status="succeeded")
            control.close()
            if intent_after == len(calls):       # an unresolved start left behind by this run
                ledger.record_intent("wing-produce-start")
            return result
        return run, calls

    def events(self):
        return [json.loads(line) for line in self.args.events.read_text(encoding="utf-8").splitlines()]

    def reset(self):
        for path in self.dir.glob("*"):
            if path.name != "profile.json":
                path.unlink()

    def out(self):
        return json.loads(self.args.summary.read_text(encoding="utf-8"))

    def test_two_runs_each_with_its_own_start_and_book_read(self):
        run, calls = self.fake([home_result(), scan_result(), home_result(), scan_result()])
        self.assertEqual(ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=wl.SpendLedger(None)), 0)
        self.assertEqual([c["tasks"][0]["name"] for c in calls], ["Wing", "KnowhowScan", "Wing", "KnowhowScan"])
        for wing in (calls[0], calls[2]):
            self.assertEqual((wing["allow_spend"], wing["tasks"][0]["option"]["mode"]), (["wing-produce-start"], "start"))
        for read in (calls[1], calls[3]):
            self.assertIs(read["tasks"][0]["option"]["keep_plan"], True)
            self.assertEqual(read["allow_spend"], [])
        loop = self.out()["wing_loop"]
        self.assertEqual((loop["planned"], loop["played"], loop["finished"], loop["ended"], loop["hits"]), (2, 2, 2, "planned_runs", []))
        names = [e["event"] for e in self.events()]
        self.assertEqual((names.count("run.started"), names.count("task.finished"), names.count("run.finished")), (1, 1, 1))
        loops = [e["readings"] for e in self.events() if e.get("kind") == "wing_loop"]
        self.assertEqual(loops[:2], [{"run": 1, "planned": 2, "finished": 0}, {"run": 2, "planned": 2, "finished": 1}])
        self.assertEqual(loops[-1], {"planned": 2, "finished": 2, "ended": "planned_runs", "hits": 0})
        self.assertTrue((self.dir / "run-config-2.json").exists())

    def test_a_target_hit_ends_the_loop_after_that_run(self):
        run, calls = self.fake([home_result(), scan_result(fills=True), home_result(), scan_result()])
        self.assertEqual(ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=wl.SpendLedger(None)), 0)
        self.assertEqual(len(calls), 2)
        loop = self.out()["wing_loop"]
        self.assertEqual((loop["ended"], loop["finished"]), ("target_hit", 1))
        self.assertEqual(loop["hits"], [{"book": "b1", "title": "【x】浅倉透", "entries": ["Center適正◎ Lv2"]}])

    def test_handover_unresolved_start_and_stops_end_the_loop(self):
        handover = summary(status="succeeded", reasons=())          # 獲得報酬 left to the user
        run, calls = self.fake([handover, home_result()])
        ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=wl.SpendLedger(None))
        self.assertEqual((len(calls), self.out()["wing_loop"]["ended"]), (1, "run_not_home"))
        self.reset()
        ledger = wl.SpendLedger(None)
        run, calls = self.fake([home_result(), scan_result(), home_result()], ledger=ledger, intent_after=2)
        ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=ledger)
        self.assertEqual((len(calls), self.out()["wing_loop"]["ended"]), (2, "spend_pending"))   # never a second start on doubt
        self.reset()
        stopped = summary(reasons=("wing:act_refused:x",))
        run, calls = self.fake([stopped, home_result()])
        self.assertEqual(ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=wl.SpendLedger(None)), 1)
        self.assertEqual(len(calls), 1)

    def test_more_runs_need_the_automatic_reward_ok(self):
        self.write_profile({"Produce次数": "3", "Produce报酬后OK": "No"})
        run, calls = self.fake([home_result()])
        with mock.patch("builtins.print") as printed:
            self.assertEqual(ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=wl.SpendLedger(None)), 2)
        self.assertEqual(calls, [])
        self.assertEqual(json.loads(printed.call_args_list[0].args[0])["error"], "连跑需开启「读取后自动确认」")

    def test_one_run_keeps_the_old_shape(self):
        self.write_profile({"Produce报酬后OK": "Yes"})
        run, calls = self.fake([home_result(), scan_result()])
        self.assertEqual(ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=wl.SpendLedger(None)), 0)
        self.assertNotIn("keep_plan", calls[1]["tasks"][0]["option"])
        self.assertNotIn("wing_loop", self.out())
        self.assertFalse(any(e.get("kind") == "wing_loop" for e in self.events()))

    def test_wall_clock_grows_with_the_runs_under_the_host_limit(self):
        self.assertLessEqual(ui_engine.WING_LOOP_MAX_MINUTES * 60 * 1000 + 300_000, 8 * 3600 * 1000)
        self.assertGreater(ui_engine.WING_RUN_MIN_MINUTES * 60, ui_engine.WING_MIN_SEGMENT_S)


class UiTests(unittest.TestCase):
    def node(self, body):
        script = ("import { readFileSync } from 'node:fs';"
                  "const D = await import('./ui/components/display.js');"
                  "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json','utf8'));"
                  "const annotations = JSON.parse(readFileSync('ui/annotations.json','utf8'));"
                  "const state = (option) => ({ schema, annotations, prefs: {}, run: { active: false }, taskStatus: {},"
                  " profile: { schema_version: 1, tasks: [{ name: 'AutoProduce', enabled: false, option: { 'ProduceP卡': '" + P + "', ...option } }] } });"
                  + body)
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)
        return json.loads(result.stdout)

    def test_run_count_shows_in_the_cost_and_needs_reward_ok(self):
        out = self.node(
            "const errs = (o) => D.wingBlockers(state(o)).flatMap((b) => b.errors);"
            "console.log(JSON.stringify({ cases: schema.options['Produce次数'].cases.map((c) => c.name),"
            " one: D.wingRunBar(state({})).sub, three: D.wingRunBar(state({ 'Produce次数': '3', 'Produce报酬后OK': 'Yes' })).sub,"
            " blocked: errs({ 'Produce次数': '2', 'Produce报酬后OK': 'No' }), fine: errs({ 'Produce次数': '2', 'Produce报酬后OK': 'Yes' }) }));")
        self.assertEqual(out["cases"], ["1", "2", "3", "4", "5"])
        self.assertEqual(out["one"], "新局消耗：体力 30")
        self.assertEqual(out["three"], "新局消耗：体力 30/局；最多 3 局")
        self.assertIn("连跑需开启「读取后自动确认」", out["blocked"])
        self.assertNotIn("连跑需开启「读取后自动确认」", out["fine"])

    def test_loop_progress_and_end_text(self):
        out = self.node(
            "const rows = [{ run_id: 'r1', kind: 'wing_loop', readings: { run: 1, planned: 3, finished: 0 } },"
            " { run_id: 'r1', kind: 'wing_loop', readings: { run: 2, planned: 3, finished: 1 } },"
            " { run_id: 'r0', kind: 'wing_loop', readings: { planned: 2, finished: 2, ended: 'planned_runs', hits: 0 } }];"
            "const running = { ...state({}), run: { active: true, mode: 'supervised_wing', run_id: 'r1', tasks: ['AutoProduce'] }, runReadings: rows };"
            "console.log(JSON.stringify({ main: D.wingRunBar(running).main,"
            " hit: D.wingLoopText({ planned: 3, finished: 1, ended: 'target_hit', hits: 1 }),"
            " done: D.wingLoopText(D.wingLoopReading(rows, 'r0')), single: D.wingLoopText({ planned: 1, finished: 1, ended: 'planned_runs' }) }));")
        self.assertEqual(out["main"], "W.I.N.G. 运行中 · 第 2/3 局")
        self.assertEqual(out["hit"], "连跑 1/3 局 · 补上目标，已停")
        self.assertEqual(out["done"], "连跑 2/2 局 · 已打完")
        self.assertEqual(out["single"], "")

    def test_new_book_line_sums_the_runs(self):
        out = self.node(
            "const rows = [{ run_id: 'r1', kind: 'knowhow_after', readings: { read: true, new: 1, hits: 0, locked: 0, lock_uncertain: false } },"
            " { run_id: 'r1', kind: 'knowhow_after', readings: { read: true, new: 2, hits: 1, locked: 1, lock_uncertain: false } }];"
            "console.log(JSON.stringify({ both: D.knowhowAfterText(D.knowhowAfterReading(rows)),"
            " one: D.knowhowAfterText(D.knowhowAfterReading(rows.slice(0, 1))),"
            " unread: D.knowhowAfterText(D.knowhowAfterReading([{ kind: 'knowhow_after', readings: { read: false } }])),"
            " none: D.knowhowAfterText(D.knowhowAfterReading([])) }));")
        self.assertEqual(out["both"], "新书 3 本 · 补上目标 1 本 · 锁定 1 本")
        self.assertEqual(out["one"], "新书 1 本 · 未补上目标")
        self.assertEqual(out["unread"], "新书未读完")
        self.assertEqual(out["none"], "")


if __name__ == "__main__":
    unittest.main()
