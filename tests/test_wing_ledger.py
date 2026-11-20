"""2026-10-11 review fixes, offline only (no host, no game, no input):

- P0-1 durable spend ledger of the W.I.N.G. プロデュース開始: intent before the tap, sent / confirmed / uncertain, an
  unresolved entry forces attach (no spend) in every later segment and UI start, home without a Produce stops;
- P1-1 / P1-2 memory identity, archive only on a confirmed start / identity mismatch / confirmed end, atomic writes;
- P1-4 / P2-1 / P2-2 the segment loops (tools/wing_segments.py and the UI loop in tools/ui_engine.py);
- P1-7 再抽選 planning needs the launcher authority;
- P2-5 / P1-7 UI wiring of sp_mode and allow_reward_reroll; P2-6 APPEAL alignment margin.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.core import produce_config as pc
from agent.core import wing_custom as wcu
from agent.core import wing_ledger as wl
from agent.core import wing_nav as nav
from agent.core import wing_screen as wsc
from tests.test_wing_live_wiring import FakeImage
from tools import run_tasks, ui_engine, ui_schema, ui_wing, wing_segments as ws

ROOT = Path(__file__).resolve().parent.parent
P = "1040170010"
START = {"start_confirm": {"layout": "plain", "multiplier": 1, "stamina_before": 100, "stamina_after": 70}}


class History:
    def __init__(self):
        self.entries = []

    def click_mark(self):
        return len(self.entries)

    def clicks_since(self, mark):
        clicks = self.entries[mark:]
        if all(e == "denied" for e in clicks):
            return "not_sent"
        return "sent" if "sent" in clicks else "unknown"

    def authorize(self, action, image, approver):
        return None


def produce_config():
    return dict(pc.ProduceConfig.from_task_options({"p_card": P}).data)


class LedgerTests(unittest.TestCase):
    def test_transitions_are_durable_and_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            ledger = wl.SpendLedger(path)
            key = ledger.record_intent("wing-produce-start", run_id="r1")
            self.assertTrue(key.endswith(":r1"))
            self.assertEqual([e["key"] for e in wl.SpendLedger(path).pending()], [key])   # a new process sees it
            ledger.mark(key, "sent")
            ledger.mark(key, "confirmed", "observed:Hub")
            self.assertEqual(wl.SpendLedger(path).pending(), [])
            entry = wl.SpendLedger(path).get(key)
            self.assertEqual([h[0] for h in entry["history"]], ["intent", "sent", "confirmed"])
            self.assertFalse((Path(tmp) / "ledger.json.tmp").exists())
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(wl.LedgerError):
                wl.SpendLedger(path).pending()

    def test_only_the_user_resolves_an_uncertain_entry(self):
        from tools import wing_spend_ledger
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            ledger = wl.SpendLedger(path)
            key = ledger.record_intent("wing-produce-start", run_id="r2")
            ledger.mark(key, "uncertain")
            self.assertEqual(wing_spend_ledger.main(["--ledger", str(path), "resolve", key, "--as", "spent"]), 0)
            self.assertEqual(wl.SpendLedger(path).pending(), [])
            self.assertEqual(wing_spend_ledger.main(["--ledger", str(path), "resolve", key, "--as", "spent"]), 2)

    def test_game_day_rolls_over_at_05_jst(self):
        import datetime as dt
        utc = dt.timezone.utc
        self.assertEqual(wl.game_day(dt.datetime(2026, 10, 10, 19, 59, tzinfo=utc)), "2026-10-10")   # 04:59 JST
        self.assertEqual(wl.game_day(dt.datetime(2026, 10, 10, 20, 0, tzinfo=utc)), "2026-10-11")    # 05:00 JST


class SessionLedgerTests(unittest.TestCase):
    """One WingSession = one launcher run (segment); the ledger file connects them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "dev_assets" / "private")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.ledger_path = self.dir / "ledger.json"
        self.memory = self.dir / "memory.json"
        self.readings = {}
        for p in (mock.patch.object(wsc.Evaluator, "hit", return_value=True),
                  mock.patch.object(wsc, "read_screen", side_effect=lambda screen, ev, fr: dict(self.readings))):
            p.start()
            self.addCleanup(p.stop)

    def session(self, mode="start", spend=True, history=None, **run):
        s = wcu.WingSession(input_enabled=True, allow_start_spend=spend, authority=history or History(),
                            ledger=self.ledger_path)
        s.backend_factory = lambda context: wsc.BoxBackend()
        s.begin({"produce_config": produce_config(), "run": {"mode": mode, **run},
                 "memory_file": str(self.memory.relative_to(ROOT))})
        return s

    def round(self, s, screen, tag, intent, readings):
        self.readings = readings
        self.assertTrue(s.state({"want": "round", "screen": screen}, FakeImage(tag)))
        return s.pick(None, FakeImage(tag), screen, intent)

    def press_start(self, s, history):
        hit = self.round(s, "StartConfirm", "a", "start_confirm_ok", START)
        self.assertEqual(hit.intent, "start_confirm_ok")
        entry, = wl.SpendLedger(self.ledger_path).load()
        self.assertEqual(entry["state"], "intent")        # written BEFORE the Pipeline sends the tap
        history.entries.append("sent")
        return entry["key"]

    def test_uncertain_start_is_never_spent_again_in_a_later_segment(self):
        history = History()
        s = self.session(history=history)
        key = self.press_start(s, history)
        # The After guard never saw the Produce (long Loading / error dialog): clickUnconfirmed ends the segment.
        with mock.patch.object(s, "classify", return_value="Loading"):
            self.assertFalse(s.state({"want": "expect", "screens": ["Hub"]}, FakeImage("b"), context=object()))
        self.assertEqual(wl.SpendLedger(self.ledger_path).get(key)["state"], "sent")
        s.stop({"reason": "clickUnconfirmed", "screen": "StartConfirm"})
        self.assertEqual(wl.SpendLedger(self.ledger_path).get(key)["state"], "uncertain")
        # Next segment (new process, same start config + spend flag): no start before Home proved no Produce.
        s2 = self.session(history=History())
        self.assertIsNone(self.round(s2, "StartConfirm", "c", "start_confirm_ok", START))
        self.assertEqual(s2.runtime.finished, "start_spend_uncertain")
        self.assertEqual(len(wl.SpendLedger(self.ledger_path).load()), 1)          # no second intent
        self.assertTrue(s2._ledger_report()["uncertain"])

    def test_home_without_a_produce_rechecks_the_entry_and_the_run_goes_on(self):
        # An earlier uncertain start only warns; Home without a Produce lets this run decide as usual.
        ledger = wl.SpendLedger(self.ledger_path)
        key = ledger.record_intent("wing-produce-start", run_id="old")
        ledger.mark(key, "uncertain")
        s = self.session()
        self.round(s, "Home", "h", "home_produce", {"home_produce_label": True})
        # decided by the usual Home rules (this session has no from_home: outside_produce), not by the old entry
        self.assertNotEqual(s.runtime.finished, "start_spend_uncertain")
        self.assertEqual(wl.SpendLedger(self.ledger_path).get(key)["state"], "rechecked")
        self.assertEqual(s._ledger_report()["rechecked"], [key])
        self.assertFalse(s._ledger_report()["uncertain"])

    def test_an_unreadable_ledger_still_stops_on_home(self):
        self.ledger_path.write_text("{", encoding="utf-8")
        s = self.session()
        self.assertEqual(s.runtime.options.mode, "attach")
        self.assertIsNone(self.round(s, "Home", "h", "home_produce", {"home_produce_label": True}))
        self.assertEqual(s.runtime.finished, "start_spend_uncertain")

    def test_a_produce_in_progress_resolves_the_entry_and_attaches(self):
        ledger = wl.SpendLedger(self.ledger_path)
        key = ledger.record_intent("wing-produce-start", run_id="old")
        s = self.session()
        self.assertTrue(s.state({"want": "round", "screen": "Hub"}, FakeImage("x")))
        self.assertEqual(wl.SpendLedger(self.ledger_path).get(key)["state"], "confirmed")
        self.assertFalse(s._ledger_report()["uncertain"])

    def test_confirmed_start_archives_the_old_memory_and_binds_the_new_produce(self):
        old = nav.RunMemory.from_produce_config(pc.ProduceConfig.from_dict(produce_config()))
        old.wins = [(1, 30000)]
        self.memory.write_text(json.dumps(old.snapshot()), encoding="utf-8")
        history = History()
        s = self.session(history=history)
        self.assertEqual(s.runtime.memory.wins, [(1, 30000)])      # legacy memory restored (no identity yet)
        self.assertFalse(s.runtime.memory.segment_resumed)          # ...but the idol check is not skipped
        key = self.press_start(s, history)
        self.assertTrue(s.state({"want": "round", "screen": "Hub"}, FakeImage("b")))
        self.assertEqual(wl.SpendLedger(self.ledger_path).get(key)["state"], "confirmed")
        self.assertEqual(s.runtime.memory.wins, [])
        self.assertEqual(s.runtime.memory.produce["run_id"], key)
        self.assertEqual(s.runtime.memory.produce["p_card"], P)
        self.assertTrue(list(self.dir.glob("memory-*-replaced.json")))
        saved = json.loads(self.memory.read_text(encoding="utf-8"))
        self.assertEqual(saved["produce"]["run_id"], key)          # checkpoint written atomically, mid-run

    def test_halo_facts_and_give_up_survive_a_restart_and_scenarios_never_mix(self):
        # H2d live 2026-11-06: each engine run forgot 施設効率相談 done, the 成長 weeks and 諦める (the end pages after
        # the give-up then stopped as fes_birth_without_evaluation).
        mem = nav.RunMemory.from_produce_config(pc.ProduceConfig.from_dict(produce_config()))
        mem.produce = {"p_card": P, "difficulty": "middle", "run_id": "h", "started_at": None, "source": "start",
                       "scenario": "halo"}
        mem.halo = nav.hp.HaloMemory(facility_done=True, completion=60, fans_seen=114204)
        mem.halo.growth_weeks.add((3, 7))
        mem.giveup = "held"
        mem.numbers = {"values": {"season": ["camp", 23], "fans": [114204, 17]}, "week": 23}   # 合宿 (was refused)
        self.memory.write_text(json.dumps(mem.snapshot()), encoding="utf-8")
        s = self.session(mode="attach", spend=False, halo_route="dig")
        m = s.runtime.memory
        self.assertEqual((m.giveup, m.segment_resumed), ("held", True))
        halo, _ = s.runtime._halo()
        self.assertEqual((halo.facility_done, halo.completion, halo.fans_seen, halo.growth_weeks),
                         (True, 60, 114204, {(3, 7)}))
        self.assertEqual(s.runtime.decide("FesBirth", {}).intent, "fes_birth")
        # The same file never resumes a W.I.N.G. run (nor a W.I.N.G. memory a Halo run).
        s = self.session(mode="attach", spend=False)
        self.assertIsNone(s.runtime.memory.giveup)
        self.assertIn("memory_identity_mismatch", [e["event"] for e in s.runtime.log])
        self.assertTrue(list(self.dir.glob("memory-*-mismatch.json")))

    def test_attach_never_archives_and_identity_mismatch_starts_fresh(self):
        mem = nav.RunMemory.from_produce_config(pc.ProduceConfig.from_dict(produce_config()))
        mem.wins = [(2, 60000)]
        mem.produce = {"p_card": P, "difficulty": "middle", "run_id": "k", "started_at": None, "source": "start"}
        self.memory.write_text(json.dumps(mem.snapshot()), encoding="utf-8")
        s = self.session(mode="attach", spend=False)
        self.assertEqual((s.runtime.memory.wins, s.runtime.memory.segment_resumed), ([(2, 60000)], True))
        self.assertTrue(self.memory.exists())
        mem.produce["p_card"] = "1050080010"
        self.memory.write_text(json.dumps(mem.snapshot()), encoding="utf-8")
        s = self.session(mode="attach", spend=False)
        self.assertEqual(s.runtime.memory.wins, [])
        self.assertTrue(list(self.dir.glob("memory-*-mismatch.json")))
        self.assertIn("memory_identity_mismatch", [e["event"] for e in s.runtime.log])

    def test_confirmed_end_archives_the_memory(self):
        s = self.session(mode="attach", spend=False)
        from agent.core import produce_end as pe
        s.runtime.memory.end = pe.EndCollector(wing_won=True, true_end_seen=False)
        s.runtime.stop("Reward", "produce_end_review")
        s.report()
        self.assertFalse(self.memory.exists())
        self.assertTrue(list(self.dir.glob("memory-*-ended.json")))

    def test_restored_context_keeps_same_team_fast_and_rejects_changed_settings(self):
        team = {"P": P, "S": ["2040040090"], "guest": "2040070080"}
        owned = {"details": {"2040040090": {"level": 60, "limit_break": 2}}}

        def make(*, difficulty="middle", slots=None, levels=None):
            rt = nav.WingNavRuntime(nav.RunMemory(team_slots=slots or dict(team), difficulty=difficulty),
                                    options=nav.RunOptions(verify_resume=True))
            rt.owned_cards = levels or owned
            return rt

        old = make()
        old.adopt_identity("start")
        old.memory.wins = [(2, 60000)]
        original = json.dumps(old.memory.snapshot())
        self.memory.write_text(original, encoding="utf-8")
        session = wcu.WingSession(ledger=self.ledger_path)
        same = make()
        session._restore_memory(same, self.memory)
        self.assertEqual(same.memory.wins, [(2, 60000)])
        self.assertTrue(same.memory.segment_resumed)
        self.assertFalse(same._resume_team_needed())
        changes = [make(difficulty="master"), make(slots={**team, "guest": "2040170060"}),
                   make(slots={**team, "S": ["2040230040"]}),
                   make(levels={"details": {"2040040090": {"level": 65, "limit_break": 2}}}),
                   make(levels={"details": {"2040040090": {"level": 60, "limit_break": 3}}})]
        for changed in changes:
            with self.subTest(context=changed.resume_context()):
                with self.assertRaises(ValueError):
                    session._restore_memory(changed, self.memory)
                self.assertEqual(self.memory.read_text(encoding="utf-8"), original)

    def test_legacy_known_difficulty_mismatch_leaves_memory_intact(self):
        old = nav.RunMemory(team_slots={"P": P})
        old.produce = {"p_card": P, "difficulty": "middle", "run_id": "old", "source": "start"}
        original = json.dumps(old.snapshot())
        self.memory.write_text(original, encoding="utf-8")
        changed = nav.WingNavRuntime(nav.RunMemory(team_slots={"P": P}, difficulty="master"))
        with self.assertRaisesRegex(ValueError, "难度"):
            wcu.WingSession(ledger=self.ledger_path)._restore_memory(changed, self.memory)
        self.assertEqual(self.memory.read_text(encoding="utf-8"), original)

    def test_resume_gate_runs_for_a_restored_segment(self):
        rt = nav.WingNavRuntime.from_produce_config(pc.ProduceConfig.from_dict(produce_config()))
        rt.memory.segment_resumed = True
        rt.memory.p_idol = "風野灯織"
        self.assertEqual(rt._resume_gate("Hub", {"hub_speaker": "めぐる"}).reason, "resume_config_mismatch")
        rt.memory.resume_info = None
        self.assertIsNone(rt._resume_gate("Hub", {"hub_speaker": "灯織"}))
        self.assertEqual(rt.memory.produce["source"], "attach")


class RerollGateTests(unittest.TestCase):
    def runtime(self, authorized):
        params = {"end": {"read": True, "protect_lv5": False, "reroll": True, "press_ok": False}}
        rt = nav.WingNavRuntime(params=params, options=nav.RunOptions(input_enabled=True,
                                                                     allow_reward_reroll=authorized))
        rt.memory.reward_review = {"ex": [], "ex_partial": False, "books": {0: {"max_level": 3}}, "items": 1,
                                   "taps": 0, "protected": [], "rerolls": 0, "reroll_cap": None, "pending": None,
                                   "unsettled": 0, "notes": [], "complete": True, "done": False}
        return rt

    def test_reroll_is_planned_only_with_the_launcher_authority(self):
        r = {"reward_tab": "knowhow", "reward_items": 1, "reroll": {"left": 2, "stamina": 30}, "stamina_now": 100}
        rt = self.runtime(False)
        self.assertIsNone(rt._reward_review(r))
        self.assertIn("reroll_skipped:not_authorized", rt.memory.reward_review["notes"])
        rt = self.runtime(True)
        self.assertIsNone(rt._reward_review(r))   # authorized, but the target is unverified -> recorded, no tap
        self.assertIn("target_unverified:reward_reroll", rt.memory.reward_review["notes"])

    def test_launcher_wires_the_setting_default_off(self):
        option = {"produce_config": produce_config()}
        run_tasks.wing_options(option)
        self.assertFalse(run_tasks.wing_reroll_option({"option": option}))
        with self.assertRaisesRegex(ValueError, "重新抽选"):
            run_tasks.wing_options({**option, "allow_reward_reroll": True})
        data = produce_config()
        data["manual"]["reward_reroll"] = True
        run_tasks.wing_options({"produce_config": data, "allow_reward_reroll": True})
        self.assertTrue(run_tasks.wing_reroll_option({"option": {"allow_reward_reroll": True}}))
        sessions = run_tasks.task_runtimes(None, wing_input=True, wing_reroll=True)
        self.assertTrue(sessions["wing"].allow_reward_reroll)
        self.assertFalse(run_tasks.task_runtimes(None, wing_input=False, wing_reroll=True)["wing"].allow_reward_reroll)
        self.assertIsNone(run_tasks.task_runtimes(None)["wing"].ledger.path)     # offline: in-memory ledger


class UiWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = ui_schema.export_schema()
        cls.interface = run_tasks.load_interface()

    def profile(self, **option):
        return {"schema_version": 1, "preset": None, "tasks": [
            {"name": "AutoProduce", "enabled": True, "option": {"ProduceP卡": P, **option}}]}

    def build(self, prof, **kw):
        kw.setdefault("ledger", wl.SpendLedger(None))
        kw.setdefault("allow_produce_spend", True)     # 2026-10-13: always passed by the host for a 「Produce」 开始
        return ui_wing.wing_run_config(self.schema, prof, interface=self.interface, snapshot={}, **kw)

    def test_uncertain_ledger_is_noted_and_the_agent_rechecks_it(self):
        ledger = wl.SpendLedger(None)
        ledger.record_intent("wing-produce-start", run_id="x")
        config, info = self.build(self.profile(Produce可开新局="Yes"), allow_produce_spend=True, ledger=ledger)
        self.assertEqual((config["tasks"][0]["option"]["mode"], config["allow_spend"]), ("start", ["wing-produce-start"]))
        self.assertTrue(info["spend_uncertain"])
        config, info = self.build(self.profile(Produce可开新局="Yes"), allow_produce_spend=True)
        self.assertEqual((config["tasks"][0]["option"]["mode"], info["spend"]), ("start", True))
        self.assertTrue(info["new_run_needs_produce_select"])

    def test_sp_mode_and_reroll_come_from_the_profile(self):
        config, info = self.build(self.profile())
        option = config["tasks"][0]["option"]
        self.assertEqual(option["sp_mode"], "auto")
        self.assertNotIn("allow_reward_reroll", option)
        config, _ = self.build(self.profile(ProduceSP加点="不加点", Produce再抽选="Yes"))
        option = config["tasks"][0]["option"]
        self.assertEqual(option["sp_mode"], "skip")
        self.assertTrue(option["allow_reward_reroll"])
        config, _ = self.build(self.profile(ProduceSP加点="交给我"))
        self.assertEqual(config["tasks"][0]["option"]["sp_mode"], "handover")

    def test_check_memory_never_archives_for_a_start_request(self):
        config, _ = self.build(self.profile(Produce主属性="Vo"))   # 「自动」 now follows the P card's specialty (U02)
        data = config["tasks"][0]["option"]["produce_config"]
        with tempfile.TemporaryDirectory() as tmp:
            memory = Path(tmp) / "memory.json"
            memory.write_text(json.dumps({"strategy": "vo_score", "goal": "true_end"}), encoding="utf-8")
            self.assertIsNone(ui_wing.check_memory(data, "start", memory))
            self.assertTrue(memory.exists())
            memory.write_text(json.dumps({"strategy": "vo_score", "goal": "true_end", "produce": {
                "p_card": "1050080010", "source": "attach"}}), encoding="utf-8")
            archived = ui_wing.check_memory(data, "attach", memory)
            self.assertFalse(memory.exists())
            self.assertTrue(archived.exists())


def summary(status="failed", reasons=("wing:segment_budget_reached",), sent=5, ledger=None):
    clicks = [{"result": "sent"}] * sent + [{"result": "denied"}] * 3
    report = {"kind": "wing", "spend_ledger": ledger or {"key": None, "state": None, "unresolved_before": [],
                                                          "uncertain": False}}
    return {"tasks": [{"name": "Wing", "status": status, "fail_reasons": list(reasons), "live_clicks": clicks,
                       "reports": [report]}], "failed": int(status != "succeeded"),
            "succeeded": int(status == "succeeded")}


class SegmentRuleTests(unittest.TestCase):
    def test_only_sent_clicks_count_and_uncertain_spend_stops(self):
        task = summary(sent=0)["tasks"][0]
        self.assertEqual(ws.sent_clicks(task), 0)                       # 3 denied records are not progress
        self.assertEqual(ws.segment_verdict(task, spend_authorized=False)[0], "continue")
        bad = summary(reasons=("wing:clickUnconfirmed",), ledger={"key": "k", "state": "uncertain",
                                                                 "unresolved_before": [], "uncertain": True})
        self.assertEqual(ws.segment_verdict(bad["tasks"][0], spend_authorized=True), ("stop", "spend_uncertain"))
        no_report = summary()["tasks"][0]
        no_report["reports"] = []
        self.assertEqual(ws.segment_verdict(no_report, spend_authorized=True)[0], "stop")
        self.assertEqual(ws.segment_verdict(summary(reasons=("wing:act_refused:x",))["tasks"][0],
                                            spend_authorized=False)[0], "stop")
        # 2026-10-25 live: failure-snapshot near_miss notes do not turn a denied tap into a stop.
        denied = ("wing:click_not_sent_resumable:picker_tile_r3c5", "near_miss:SC.Nav.Present.Guard/SC.Present.Guard.Title",
                  "click_denied:wing-picker-tile-r3c5:frame_or_host_binding_failed")
        self.assertEqual(ws.segment_verdict(summary(reasons=denied)["tasks"][0], spend_authorized=False)[0], "continue")
        self.assertEqual(ws.segment_verdict(summary(reasons=("near_miss:SC.Nav.Present.Guard/x",))["tasks"][0],
                                            spend_authorized=False)[0], "stop")
        # 2026-10-27 live: the host refused a late APPEAL tap (never sent); the next segment re-observes the Live.
        late = ("wing:click_not_sent_resumable:live_appeal_timing",
                "click_refused:wing-live-appeal-timing:appeal_timing_no_future_target")
        self.assertEqual(ws.segment_verdict(summary(reasons=late)["tasks"][0], spend_authorized=False)[0], "continue")
        # 2026-10-29 user: small problems re-observe in a new segment; only hard causes stop the loop.
        other = ("wing:click_not_sent_resumable:live_appeal_timing", "click_refused:wing-live-appeal-timing:frame_stale")
        self.assertEqual(ws.segment_verdict(summary(reasons=other)["tasks"][0], spend_authorized=False)[0], "continue")
        for soft in ("wing:sp_panel_unreadable", "wing:home_label_unread", "wing:live_selected_card_unread",
                     "wing:state_unreadable", "wing:audition_table_mismatch"):
            self.assertEqual(ws.segment_verdict(summary(reasons=(soft,))["tasks"][0], spend_authorized=False)[0],
                             "continue", soft)
        for hard in ("wing:resume_config_mismatch", "wing:team_fingerprint_mismatch", "wing:sp_learn_handover",
                     "wing:sp_learn_unconfirmed", "wing:sp_replace_ok_layout_unmeasured", "wing:reward_sell_unconfirmed", "wing:produce_end_review", "wing:start_stamina_unread_or_unexpected",
                     "wing:no_produce_in_progress", "click_refused:x:spend_not_allowed", "wing:scorer_error:KeyError"):
            self.assertEqual(ws.segment_verdict(summary(reasons=(hard,))["tasks"][0], spend_authorized=False),
                             ("stop", hard))

    def test_later_segments_attach_without_spend(self):
        config = {"allow_spend": ["wing-produce-start"],
                  "tasks": [{"name": "Wing", "option": {"mode": "start", "unit_search": True}}]}
        later = ws.attach_config(config)
        self.assertEqual((later["allow_spend"], later["tasks"][0]["option"]), ([], {"mode": "attach"}))
        self.assertEqual(config["tasks"][0]["option"]["mode"], "start")   # the original is untouched
        self.assertGreater(ws.SEGMENT_PAUSE_S, ws.HOST_QUIET_S)


class UiLoopTests(unittest.TestCase):
    """tools/ui_engine.wing_main with a fake launcher: one event stream, attach after the first segment, pauses."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "dev_assets" / "private")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "profile.json").write_text(json.dumps({"schema_version": 1, "tasks": [
            {"name": "AutoProduce", "enabled": True, "option": {"ProduceP卡": P, "Produce可开新局": "Yes"}}]}),
            encoding="utf-8")
        self.args = argparse.Namespace(profile=self.dir / "profile.json", events=self.dir / "events.jsonl",
                                       stop_file=self.dir / "stop.flag", summary=self.dir / "summary.json",
                                       session=self.dir / "session.json", offline_frames=None, wing=True,
                                       allow_produce_spend=True)
        self.snapshot = mock.patch.object(ui_wing, "record_snapshot", return_value=None)
        self.snapshot.start()
        self.addCleanup(self.snapshot.stop)
        self.memory = mock.patch.object(ui_wing, "check_memory", return_value=None)
        self.memory.start()
        self.addCleanup(self.memory.stop)

    def fake(self, results):
        calls = []

        def run(config, *, control, summary_path, **kw):
            calls.append(json.loads(json.dumps(config)))
            control.emit("run.started")
            control.emit("task.started", task="Wing")
            result = results[len(calls) - 1]
            control.emit("task.finished", task="Wing", status=result["tasks"][0]["status"], reports_complete=True)
            control.emit("run.finished", status="failed")
            control.close()
            return result
        return run, calls

    def events(self):
        return [json.loads(line) for line in self.args.events.read_text(encoding="utf-8").splitlines()]

    def test_loop_continues_resumable_segments_in_attach_and_reports_one_stream(self):
        run, calls = self.fake([summary(sent=8), summary(sent=6), summary(status="succeeded", reasons=())])
        sleeps = []
        code = ui_engine.wing_main(self.args, run=run, sleep=sleeps.append, ledger=wl.SpendLedger(None))
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual((calls[0]["allow_spend"], calls[0]["tasks"][0]["option"]["mode"]),
                         (["wing-produce-start"], "start"))
        for later in calls[1:]:
            self.assertEqual((later["allow_spend"], later["tasks"][0]["option"]["mode"]), ([], "attach"))
        self.assertGreaterEqual(sum(sleeps), 2 * ui_engine.WING_PAUSE_S)
        names = [e["event"] for e in self.events()]
        self.assertEqual(names.count("run.started"), 1)
        self.assertEqual(names.count("run.finished"), 1)
        self.assertEqual(names.count("task.finished"), 1)      # segment ends are not task failures
        self.assertEqual(names[-1], "run.finished")
        self.assertEqual([e["sequence"] for e in self.events()], list(range(1, len(names) + 1)))
        self.assertEqual(json.loads(self.args.summary.read_text(encoding="utf-8"))["tasks"][0]["status"], "succeeded")

    def test_setup_segment_without_a_start_keeps_the_start_authority(self):
        # 2026-10-14 live: a denied unit-tab click ended segment 1 on UnitSelect before any start; an attach segment
        # 2 refused the setup screen (setup_requires_user). Only a proven-untouched start keeps the authority.
        setup = summary(reasons=("wing:click_not_sent_resumable:unit_tab_5",), sent=3)
        setup["tasks"][0]["reports"][0]["start_attempted"] = False
        started = summary(sent=6)
        started["tasks"][0]["reports"][0]["start_attempted"] = True
        run, calls = self.fake([setup, started, summary(status="succeeded", reasons=())])
        self.assertEqual(ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=wl.SpendLedger(None)), 0)
        modes = [(c["allow_spend"], c["tasks"][0]["option"]["mode"]) for c in calls]
        self.assertEqual(modes, [(["wing-produce-start"], "start"), (["wing-produce-start"], "start"), ([], "attach")])

    def test_uncertain_spend_stops_the_loop(self):
        bad = summary(reasons=("wing:clickUnconfirmed",), ledger={"key": "k", "state": "uncertain",
                                                                 "unresolved_before": [], "uncertain": True})
        run, calls = self.fake([bad, summary()])
        code = ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=wl.SpendLedger(None))
        self.assertEqual((code, len(calls)), (1, 1))
        self.assertEqual(json.loads(self.args.summary.read_text(encoding="utf-8"))["wing_segments"]["ended"],
                         "spend_uncertain")

    def test_no_progress_guard_and_cap_end_as_paused(self):
        run, calls = self.fake([summary(sent=0), summary(sent=0), summary()])
        self.assertEqual(ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=wl.SpendLedger(None)), 1)
        self.assertEqual(len(calls), 2)
        self.tearDown_reset()
        run, calls = self.fake([summary(sent=5)] * ui_engine.WING_MAX_SEGMENTS)
        self.assertEqual(ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=wl.SpendLedger(None)), 0)
        self.assertEqual(len(calls), ui_engine.WING_MAX_SEGMENTS)
        final = [e for e in self.events() if e["event"] == "task.finished"]
        self.assertEqual(final[-1]["status"], "paused")
        self.assertEqual(json.loads(self.args.summary.read_text(encoding="utf-8"))["tasks"][0]["status"], "paused")

    def test_a_small_problem_starts_a_new_segment_until_it_repeats(self):
        # 2026-10-29 user: 「maa不能遇到小问题就停止需求用户输入」 -- re-observe; the same cause three times ends the run
        soft = summary(reasons=("wing:sp_panel_unreadable",))
        run, calls = self.fake([soft, summary(), soft, soft, soft, summary()])
        self.assertEqual(ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=wl.SpendLedger(None)), 1)
        self.assertEqual(len(calls), 5)
        self.assertIn("repeated:wing:sp_panel_unreadable", self.args.summary.read_text(encoding="utf-8"))

    def test_a_produce_ended_at_home_reads_the_new_books_once(self):
        # Read the books right after a Produce ends; only when Maa itself reached home (獲得報酬 OK),
        # not on the handover, never a second time, and the W.I.N.G. result stays the run's result
        home = summary(status="succeeded", reasons=())
        home["tasks"][0]["reports"][0]["outcome"] = "produce_finished_home"
        scan = {"tasks": [{"name": "KnowhowScan", "status": "succeeded", "fail_reasons": [], "reports": [
            {"kind": "knowhow_scan", "outcome": "newest_read", "books": 2,
             "topup": {"mode": "only", "n": 2, "reason": "topup", "owned_now": 500},
             "inventory": {"topup_added": 2, "sync": {"state": "up_to_date"}}}]}], "failed": 0, "succeeded": 1}
        with mock.patch.object(ws, "KNOWHOW_AFTER", True):
            run, calls = self.fake([summary(sent=8), home, scan])
            sleeps = []
            self.assertEqual(ui_engine.wing_main(self.args, run=run, sleep=sleeps.append, ledger=wl.SpendLedger(None)), 0)
        self.assertEqual([c["tasks"][0]["name"] for c in calls], ["Wing", "Wing", "KnowhowScan"])
        self.assertEqual((calls[2]["tasks"][0]["option"], calls[2]["allow_spend"]), (ws.KNOWHOW_AFTER_OPTION, []))
        self.assertGreaterEqual(sum(sleeps), 2 * ui_engine.WING_PAUSE_S)        # its own run window after the pause
        out = json.loads(self.args.summary.read_text(encoding="utf-8"))
        self.assertEqual(out["tasks"][0]["name"], "Wing")
        self.assertEqual(out["knowhow_after"], {"status": "succeeded", "outcome": "newest_read", "books": 2, "added": 2,
                                                "owned_now": 500, "new": 2, "state": "up_to_date"})
        finished = [e for e in self.events() if e["event"] == "task.finished"]
        self.assertEqual([(e["task"], e["status"]) for e in finished], [("Wing", "succeeded")])
        self.assertTrue(any(e.get("kind") == "knowhow_after" for e in self.events()))
        for handover in ("produce_end_review", None):
            self.tearDown_reset()
            end = summary(status="succeeded", reasons=())
            end["tasks"][0]["reports"][0]["outcome"] = handover
            with mock.patch.object(ws, "KNOWHOW_AFTER", True):
                run, calls = self.fake([end, scan])
                ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=wl.SpendLedger(None))
            self.assertEqual(len(calls), 1, handover)
        self.tearDown_reset()
        self.tearDown_reset()
        profile = json.loads(self.args.profile.read_text(encoding="utf-8"))
        profile["tasks"][0]["option"].update({"Produce自动锁书": "Yes", "Produce自动卖书": "Yes"})
        self.args.profile.write_text(json.dumps(profile), encoding="utf-8")
        with mock.patch.object(ws, "KNOWHOW_AFTER", True):                    # the W.I.N.G. keep switches go along
            run, calls = self.fake([home, scan])
            ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=wl.SpendLedger(None))
        self.assertEqual(calls[1]["tasks"][0]["option"], {**ws.KNOWHOW_AFTER_OPTION, "keep_lock": True,
                                                          "keep_sell": True, "keep_strategy": "useless_only"})
        self.tearDown_reset()
        with mock.patch.object(ws, "KNOWHOW_AFTER", False):                   # the switch turns it off
            run, calls = self.fake([home, scan])
            ui_engine.wing_main(self.args, run=run, sleep=lambda s: None, ledger=wl.SpendLedger(None))
        self.assertEqual(len(calls), 1)
        self.assertTrue(ws.KNOWHOW_AFTER)                                      # on since the 2026-10-26 live test

    def test_the_wing_keep_switches_ride_on_the_new_book_read(self):
        # 自动锁书 / 自动卖书 are the step after W.I.N.G. (the post-Produce top-up)
        from agent.core import produce_config as pc

        self.assertIsNone(ws.knowhow_keep_settings({"tasks": [{"name": "Wing", "option": {}}]}))
        self.assertIsNone(ws.knowhow_keep_settings({"tasks": [{"name": "Wing", "option": {
            "produce_config": pc.default_config("weekly_count")}}]}))
        data = pc.default_config("true_end")
        config = {"tasks": [{"name": "Wing", "option": {"produce_config": data}}]}
        self.assertIsNone(ws.knowhow_keep_settings(config))                      # both off by default
        data["manual"].update(knowhow_lock=True, knowhow_sell_strategy="keep3")
        keep = ws.knowhow_keep_settings(config)
        self.assertEqual(keep, {"lock": True, "sell": False, "strategy": "keep3"})
        option = ws.knowhow_after_config(keep)["tasks"][0]["option"]
        self.assertEqual(option, {**ws.KNOWHOW_AFTER_OPTION, "keep_lock": True, "keep_sell": False,
                                  "keep_strategy": "keep3"})
        attach = run_tasks.knowhow_scan_options(option, ws.KNOWHOW_AFTER_TIMEOUT_S)["attach"]
        self.assertEqual((attach["topup"], attach["keep_lock"], attach["keep_strategy"]), ("only", True, "keep3"))
        self.assertEqual(ws.knowhow_after_config(None)["tasks"][0]["option"], ws.KNOWHOW_AFTER_OPTION)

    def test_knowhow_after_config_is_the_supervised_top_up(self):
        config = ws.knowhow_after_config()
        option = run_tasks.knowhow_scan_options(config["tasks"][0]["option"], config["task_timeout_s"])
        self.assertEqual((option["attach"]["topup"], option["attach"]["newest"]), ("only", 0))
        self.assertNotIn("max_clicks", config)   # the scan's own max_inputs bounds it; no click cap
        self.assertEqual(ws.knowhow_after_summary(None)["status"], None)

    def tearDown_reset(self):
        for name in ("events.jsonl", "summary.json", "run-config.json"):
            (self.dir / name).unlink(missing_ok=True)


class AppealMarginTests(unittest.TestCase):
    def test_alignment_delay_keeps_the_frame_young(self):
        self.assertLessEqual(wcu.DECISION_STALE_S + wcu.APPEAL_FRAME_MARGIN_S, 5.0)
        s = wcu.WingSession(input_enabled=True, clock=lambda: 2.0)
        s.runtime = nav.WingNavRuntime()
        slept = []
        with mock.patch("agent.core.wing_live_timing.align_delay", return_value=(0.9, None)), \
                mock.patch.object(wcu.time, "sleep", side_effect=slept.append):
            s._align_appeal(type("F", (), {"image": None})(), started=0.5)   # 1.5 s elapsed + 0.9 + 1.2 > 3.5
        self.assertEqual(slept, [])


if __name__ == "__main__":
    unittest.main()


class SameViewTests(unittest.TestCase):
    """2026-10-15 speed: the decision skips its whole-frame re-check only on the view SC.Wing.Is just confirmed."""

    def test_same_view_requires_screen_and_a_near_identical_frame(self):
        import numpy as np
        from agent.core import wing_custom as wc
        base = np.full((720, 1280, 3), 100, dtype=np.uint8)
        confirmed = ("Hub", wc._thumb(base))
        sparkle = base.copy()
        sparkle[100:140, 200:260] = 255                      # small animation
        popup = base.copy()
        popup[200:520, 300:980] = 20                         # a dialog over the page
        self.assertTrue(wc._same_view(confirmed, "Hub", sparkle))
        self.assertFalse(wc._same_view(confirmed, "Schedule", base))
        self.assertFalse(wc._same_view(confirmed, "Hub", popup))
        self.assertFalse(wc._same_view(None, "Hub", base))
