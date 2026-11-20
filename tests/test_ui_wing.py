"""W.I.N.G. from the 任务 page: UI profile -> ProduceConfig.from_task_options -> supervised Wing run config
(tools/ui_wing.py), the per-run produce spend consent, the private 编队快照 and the redesigned team field rules. Offline;
no host, no game, no input."""
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from agent.core import wing_ledger as wl
from tools import run_tasks, ui_engine, ui_schema, ui_wing

ROOT = Path(__file__).resolve().parent.parent
P = "1040170010"
SNAP = {"version": 1, "unit": 1, "p_card": P, "s_cards": ["2010010010", "2020020010", "2030030010", "2060060010"],
        "guest": "2070070010", "read_at": "2026-10-10T20:00:00+11:00"}


def profile(**option):
    return {"schema_version": 1, "preset": None, "tasks": [
        {"name": "Shop", "enabled": False},
        {"name": "AutoProduce", "enabled": True, "option": {"ProduceP卡": P, **option}}]}


class _UnreadableLedger(wl.SpendLedger):
    def pending(self):
        raise wl.LedgerError("unreadable")


def unreadable_ledger():
    """An unreadable プロデュース開始 ledger (wing_ledger): the run falls back to a no-spend attach. An earlier uncertain
    start alone no longer does (the Agent rechecks it on Home)."""
    return _UnreadableLedger(None)


class WingRunConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = ui_schema.export_schema()
        cls.interface = run_tasks.load_interface()

    def build(self, prof, **kw):
        kw.setdefault("snapshot", {})
        kw.setdefault("allow_produce_spend", True)     # 2026-10-13: the host always passes it for a 「Produce」 开始
        kw.setdefault("ledger", wl.SpendLedger(None))
        return ui_wing.wing_run_config(self.schema, prof, interface=self.interface, **kw)

    def test_default_resumes_after_verification_or_starts_one_produce(self):
        config, info = self.build(profile())
        self.assertEqual(config["preset"], None)
        self.assertTrue(config["input_enabled"])
        self.assertEqual(config["allow_spend"], ["wing-produce-start"])
        self.assertEqual((config["tasks"][0]["option"]["mode"], info["mode"], info["spend"]), ("start", "start", True))
        # an in-progress Produce is continued only after the P idol / team check (wing_nav.RunOptions.verify_resume)
        self.assertIs(config["tasks"][0]["option"]["verify_resume"], True)
        run_tasks.supervised_task_config(config, live=True, offline=False, simulate_input=False)
        # an unreadable ledger: attach only, still verified, no spend
        config, info = self.build(profile(), ledger=unreadable_ledger())
        self.assertEqual(config["allow_spend"], [])
        self.assertNotIn("max_clicks", config)   # no click caps
        self.assertNotIn("task_max_clicks", config)
        option = config["tasks"][0]["option"]
        self.assertEqual(config["tasks"][0]["name"], "Wing")
        self.assertEqual((option["mode"], option["from_home"], option["sp_mode"]), ("attach", True, "auto"))
        self.assertNotIn("unit_search", option)
        self.assertEqual(option["memory_file"], "dev_assets/private/wing-ui/memory.json")
        team = option["produce_config"]["team"]
        self.assertEqual((team["p_card"], team["s_cards"], team["guest"]), (P, [None] * 4, None))
        self.assertEqual(info["mode"], "attach")
        self.assertIs(option["verify_resume"], True)
        # exactly the CLI's supervised validation
        run_tasks.supervised_task_config(config, live=True, offline=False, simulate_input=False)

    def test_halo_scenario(self):
        # H2c batch 5: 剧本 say "Halo" adds the book and the goals from the wanted list (掘书线 only).
        from agent.core.knowhow_books import WantedItem as W
        halo = {"Produce剧本": 'say "Halo"'}
        wanted = [W("施設効率相談+", "prefix", purpose="halo"), W("施設効率相談", "prefix", purpose="halo")]
        config, info = self.build(profile(**halo, Produce次数="3", Halo带书="プリセット2"), wanted=wanted)
        option = config["tasks"][0]["option"]
        self.assertEqual((option["halo_route"], option["halo_book"]), ("dig", "preset2"))
        self.assertEqual(option["halo_goals"], {"facility": True, "facility_plus": True, "completion": None,
                                                "fans_cap": 100000})
        self.assertEqual((info["scenario"], info["runs"], option["produce_config"]["caps"]["max_runs"]), ("halo", 1, 1))
        run_tasks.supervised_task_config(config, live=True, offline=False, simulate_input=False)
        config, _ = self.build(profile(**halo, Halo路线="强fes线"), wanted=[])   # a profile saved before 2026-11-07
        option = config["tasks"][0]["option"]
        self.assertEqual((option["halo_route"], option["halo_book"]), ("dig", "auto"))
        self.assertNotIn("halo_goals", option)                       # no Halo items: the default goals
        self.assertNotIn("halo_goals", config["tasks"][0]["option"])
        with self.assertRaises(ValueError):
            self.build(profile(**halo), wanted=[W("Vocal適正◎", purpose="halo")])   # nothing for the dig route
        config, info = self.build(profile())
        self.assertNotIn("halo_route", config["tasks"][0]["option"])
        self.assertEqual(info["scenario"], "wing")

    def test_halo_target_book(self):
        # 2026-11-15: 带书「ノウハウ页指定」 takes the one book the ノウハウ page saved (prefs.halo_book).
        halo = {"Produce剧本": 'say "Halo"', "Halo带书": "ノウハウ页指定"}
        book = {"title": "【夜明け】 テスト", "vo": 120, "da": 80, "vi": 60, "me": 40, "eval": 650}
        from unittest import mock
        from agent.core import weekly_auto
        with mock.patch.object(weekly_auto, "load_halo_book", return_value=None), self.assertRaises(ValueError):
            self.build(profile(**halo), wanted=[])
        plan = weekly_auto.halo_book_of({"prefs": {"halo_book": [book]}})
        self.assertEqual(plan, {**book, "title": "【夜明け】テスト"})
        self.assertIsNone(weekly_auto.halo_book_of({"prefs": {"halo_book": [book, book]}}))
        with mock.patch.object(weekly_auto, "load_halo_book", return_value=plan):
            config, _ = self.build(profile(**halo), wanted=[])
        option = config["tasks"][0]["option"]
        self.assertEqual((option["halo_book"], option["halo_book_plan"]), ("target", plan))
        run_tasks.supervised_task_config(config, live=True, offline=False, simulate_input=False)

    def test_settings_reach_the_produce_config(self):
        config, _ = self.build(profile(Produce主属性="Da", Produce难度="Master", Produce报酬后OK="Yes", Produce编队槽位="unit3"))
        data = config["tasks"][0]["option"]["produce_config"]
        self.assertEqual((data["difficulty"], data["allow_master"], data["unit_preset"]), ("master", True, "unit3"))
        self.assertEqual(data["manual"]["strategy"], "da_score")
        self.assertIs(data["manual"]["reward_ok"], True)

    def test_new_produce_needs_the_per_run_flag_and_old_no_is_migrated(self):
        with self.assertRaisesRegex(ValueError, "授权"):
            self.build(profile(), allow_produce_spend=False)
        # 2026-10-13: the retired 开局 choice -- a saved "No" is dropped (only the case Yes remains), never a refusal
        self.assertEqual([c["name"] for c in self.schema["options"]["Produce可开新局"]["cases"]], ["Yes"])
        old = profile(Produce可开新局="No")
        self.assertNotIn("Produce可开新局", ui_schema.migrate_legacy_profile(self.schema, old)["tasks"][1]["option"])
        config, info = self.build(old)
        self.assertEqual((config["tasks"][0]["option"]["mode"], info["spend"]), ("start", True))
        config, info = self.build(profile(Produce可开新局="Yes", Produce找编队="Yes"), allow_produce_spend=True)
        self.assertEqual(config["allow_spend"], ["wing-produce-start"])
        self.assertEqual(config["tasks"][0]["option"]["mode"], "start")
        self.assertTrue(config["tasks"][0]["option"]["unit_search"])
        self.assertTrue(info["spend"])
        # the ordinary profile -> run config derivation names the same consent (still deferred for read-only runs)
        self.assertEqual(ui_schema.spend_flags_for_profile(self.schema, profile(Produce可开新局="Yes")), ["allow_produce_spend"])
        self.assertEqual(ui_schema.spend_flags_for_profile(self.schema, profile()), ["allow_produce_spend"])
        self.assertEqual(ui_schema.spend_flags_for_profile(self.schema, ui_schema.migrate_legacy_profile(
            self.schema, profile(Produce可开新局="No"))), ["allow_produce_spend"])
        # 2026-10-12: the default (key absent) allows a new Produce; an old saved 「Produce开新一局」=No is dropped
        default = profile()
        default["tasks"][1]["option"].pop("Produce可开新局", None)
        default["tasks"][1]["option"]["Produce开新一局"] = "No"
        migrated = ui_schema.migrate_legacy_profile(self.schema, default)
        self.assertEqual(ui_schema.spend_flags_for_profile(self.schema, migrated), ["allow_produce_spend"])

    def test_refusals(self):
        bad = profile()
        bad["tasks"][1]["option"]["ProduceP卡"] = ""
        with self.assertRaisesRegex(ValueError, "P 偶像"):
            self.build(bad)
        both = profile()
        both["tasks"][0]["enabled"] = True
        with self.assertRaisesRegex(ValueError, "单独"):
            self.build(both)
        # 用推荐 without the adopted recommendation (S/Guest keys empty) cannot start
        with self.assertRaisesRegex(ValueError, "推荐编队未生成"):
            self.build(profile(Produce编队方式="自动编队"))

    def test_snapshot_only_for_the_same_p_and_unit(self):
        config, info = self.build(profile(), snapshot=SNAP)
        team = config["tasks"][0]["option"]["produce_config"]["team"]
        self.assertEqual((team["s_cards"], team["guest"], info["snapshot"]), (SNAP["s_cards"], SNAP["guest"], True))
        _, other_unit = self.build(profile(Produce编队槽位="unit2"), snapshot=SNAP)
        self.assertFalse(other_unit["snapshot"])
        _, searching = self.build(profile(Produce编队槽位="unit2", Produce可开新局="Yes", Produce找编队="Yes"),
                                  snapshot=SNAP, allow_produce_spend=True)
        self.assertTrue(searching["snapshot"])
        _, other_p = self.build(profile(ProduceP卡="1050080010"), snapshot=SNAP)
        self.assertFalse(other_p["snapshot"])

    def test_recommend_edits_the_chosen_unit_and_ignores_the_snapshot(self):
        # 推荐编队 (2026-10-14, user): Maa picked P + 4 S + Guest; a new Produce overwrites the chosen unit (team_edit)
        rec_p = "1050080010"
        cards = dict(ProduceP卡=rec_p, ProduceS卡1="2010010020", ProduceS卡2="2020020020", ProduceS卡3="2030030020",
                     ProduceS卡4="2060060020", Produce客串="2070070020")
        rec = profile(Produce编队方式="自动编队", Produce编队槽位="unit5", Produce找编队="Yes", **cards)
        with patch("tools.ui_wing.spend_pending", return_value=[]):
            config, info = self.build(rec, snapshot={**SNAP, "p_card": rec_p, "unit": 5})
        option = config["tasks"][0]["option"]
        team = option["produce_config"]["team"]
        self.assertEqual(team["mode"], "manual")
        self.assertEqual((team["p_card"], team["s_cards"], team["guest"]),
                         (rec_p, [cards[f"ProduceS卡{i}"] for i in range(1, 5)], cards["Produce客串"]))
        self.assertTrue(option["team_edit"])                       # the chosen unit is overwritten
        self.assertIs(option["picker_sort"], True)                 # サポート list sorted by attribute (skipped if disabled)
        self.assertNotIn("unit_search", option)                    # 自动查找编队 does not apply to 推荐编队
        self.assertEqual(option["produce_config"]["unit_preset"], "unit5")
        self.assertEqual((info["team_source"], info["snapshot"], info["team_edit"], info["overwrite_unit"]),
                         ("recommend", False, True, "unit5"))
        run_tasks.supervised_task_config(config, live=True, offline=False, simulate_input=False)
        # attach (unreadable ledger, no new Produce): no edit; the resume check verifies against this team
        config, info = self.build(rec, snapshot=SNAP, ledger=unreadable_ledger())
        option = config["tasks"][0]["option"]
        self.assertNotIn("team_edit", option)
        self.assertNotIn("picker_sort", option)
        self.assertNotIn("unit_search", option)
        self.assertIs(option["verify_resume"], True)
        self.assertEqual(option["produce_config"]["team"]["s_cards"], [cards[f"ProduceS卡{i}"] for i in range(1, 5)])
        self.assertEqual((info["team_source"], info["overwrite_unit"]), ("recommend", None))
        # later segments are attach runs: the start-only edit is dropped (tools/wing_segments.attach_config)
        from tools import wing_segments
        with patch("tools.ui_wing.spend_pending", return_value=[]):
            config, _ = self.build(rec, snapshot=SNAP)
        self.assertNotIn("team_edit", wing_segments.attach_config(config)["tasks"][0]["option"])
        self.assertNotIn("picker_sort", wing_segments.attach_config(config)["tasks"][0]["option"])
        # incomplete adoption (no P / missing S) cannot start
        with self.assertRaisesRegex(ValueError, "推荐编队未生成"):
            self.build(profile(Produce编队方式="自动编队", ProduceP卡="", ProduceS卡1="2010010020"))
        with self.assertRaisesRegex(ValueError, "推荐编队未生成"):
            self.build(profile(Produce编队方式="自动编队", ProduceS卡1="2010010020"))

    def test_minigame_before_start_option(self):
        """「开局前大富豪」 关/自动/每次: 每次 -> RunOptions.minigame=before_start on a new Produce; 自动 only when
        the adopted 推荐编队 scores below MINIGAME_AUTO_MIN_RATING (and the home entry is verified); never on attach."""
        rec_p = "1050080010"
        cards = dict(ProduceP卡=rec_p, ProduceS卡1="2010010020", ProduceS卡2="2020020020", ProduceS卡3="2030030020",
                     ProduceS卡4="2060060020", Produce客串="2070070020")
        team = {"p_card": rec_p, "s_cards": [cards[f"ProduceS卡{i}"] for i in range(1, 5)], "guest": cards["Produce客串"]}
        low = {"proposal": team, "recommender": {"ProxyTERating": ui_wing.MINIGAME_AUTO_MIN_RATING - 5,
                                                 "growth": {"route_feasible": True}}}
        high = {"proposal": team, "recommender": {"ProxyTERating": ui_wing.MINIGAME_AUTO_MIN_RATING + 5,
                                                  "growth": {"route_feasible": True}}}
        other = {**low, "proposal": {**team, "guest": "2080080020"}}
        rec = lambda game: profile(Produce编队方式="自动编队", Produce大富豪=game, **cards)

        def run(prof, **kw):
            with patch("tools.ui_wing.spend_pending", return_value=[]):
                config, info = self.build(prof, **kw)
            run_tasks.supervised_task_config(config, live=True, offline=False, simulate_input=False)
            return config["tasks"][0]["option"], info

        option, info = run(profile(Produce大富豪="每次"))
        self.assertEqual((option.get("minigame"), option["from_home"], info["minigame"]), ("before_start", True, "before_start"))
        option, info = run(rec("关"), proposal=low)
        self.assertNotIn("minigame", option)
        self.assertEqual(info["minigame_reason"], "off")
        with patch("tools.ui_wing.minigame_entry_verified", return_value=True):
            option, info = run(rec("自动"), proposal=low)
            self.assertEqual((option.get("minigame"), info["minigame_reason"]), ("before_start", "low_rating"))
            for prop, why in ((high, "rating_ok"), (other, "no_rating"), ({}, "no_rating")):
                option, info = run(rec("自动"), proposal=prop)
                self.assertNotIn("minigame", option)
                self.assertEqual(info["minigame_reason"], why)
            option, info = run(rec("自动"), proposal={**high, "recommender": {**high["recommender"], "growth": {"route_feasible": False}}})
            self.assertEqual(option.get("minigame"), "before_start")
            # 指定ユニット has no recommendation score: 自动 does not play
            option, info = run(profile(Produce大富豪="自动"), proposal=low)
            self.assertEqual((option.get("minigame"), info["minigame_reason"]), (None, "no_rating"))
        # the home entry was live-verified 2026-10-15; while unverified 自动 does not play
        self.assertTrue(ui_wing.minigame_entry_verified())
        with patch("tools.ui_wing.minigame_entry_verified", return_value=False):
            option, info = run(rec("自动"), proposal=low)
        self.assertEqual((option.get("minigame"), info["minigame_reason"]), (None, "entry_unverified"))
        # attach (unreadable ledger): never; later segments drop it (wing_segments.attach_config)
        config, info = self.build(profile(Produce大富豪="每次"), ledger=unreadable_ledger())
        self.assertNotIn("minigame", config["tasks"][0]["option"])
        self.assertEqual(info["minigame_reason"], "attach")
        from tools import wing_segments
        with patch("tools.ui_wing.spend_pending", return_value=[]):
            config, _ = self.build(profile(Produce大富豪="每次"))
        self.assertNotIn("minigame", wing_segments.attach_config(config)["tasks"][0]["option"])
        # the UI mirrors the threshold and the entry state (ui/components/team-proposal.js)
        source = (ROOT / "ui/components/team-proposal.js").read_text(encoding="utf-8")
        self.assertIn(f"MINIGAME_AUTO_MIN_RATING = {ui_wing.MINIGAME_AUTO_MIN_RATING:g};", source)
        self.assertIn(f"MINIGAME_ENTRY_VERIFIED = {str(ui_wing.minigame_entry_verified()).lower()};", source)

    def test_proposal_module_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "team-proposal.js"
            path.write_text('// header\nexport default {"version": 1, "recommender": {"ProxyTERating": 70.5}};\n', encoding="utf-8")
            self.assertEqual(ui_wing.load_proposal(path)["recommender"]["ProxyTERating"], 70.5)
            path.write_text("export default nope;", encoding="utf-8")
            self.assertIsNone(ui_wing.load_proposal(path))
            self.assertIsNone(ui_wing.load_proposal(Path(tmp) / "missing.js"))

    def test_snapshot_check_off_checks_the_p_card_only(self):
        config, info = self.build(profile(Produce核对上次编队="No"), snapshot=SNAP)
        team = config["tasks"][0]["option"]["produce_config"]["team"]
        self.assertEqual((team["p_card"], team["s_cards"], team["guest"]), (P, [None] * 4, None))
        self.assertEqual((info["snapshot"], info["snapshot_check"], info["team_source"]), (False, False, "unit"))
        # the UI-only switch never reaches the run options
        self.assertNotIn("Produce核对上次编队", json.dumps(config, ensure_ascii=False))
        self.assertTrue(self.schema["options"]["Produce核对上次编队"]["ui_only"])

    def test_legacy_registered_cards_still_verify_without_a_snapshot(self):
        config, _ = self.build(profile(ProduceS卡1="2010010010", Produce客串="2070070010"))
        team = config["tasks"][0]["option"]["produce_config"]["team"]
        self.assertEqual((team["s_cards"][0], team["guest"]), ("2010010010", "2070070010"))

    def test_team_edit_keeps_current_cards_instead_of_the_previous_unit_snapshot(self):
        chosen = ["2010010020", "2020020020", "2030030020", "2060060020"]
        guest = "2070070020"
        selected = profile(Produce可开新局="Yes", Produce编队槽位="unit1", ProduceS卡1=chosen[0],
                           ProduceS卡2=chosen[1], ProduceS卡3=chosen[2], ProduceS卡4=chosen[3], Produce客串=guest)
        with patch("tools.ui_wing.spend_pending", return_value=[]):
            config, info = self.build(selected, snapshot=SNAP, allow_produce_spend=True, team_edit=True)
        option = config["tasks"][0]["option"]
        team = option["produce_config"]["team"]
        self.assertEqual((team["p_card"], team["s_cards"], team["guest"]), (P, chosen, guest))
        self.assertTrue(option["team_edit"])
        self.assertFalse(info["snapshot"])


class SnapshotTests(unittest.TestCase):
    def event(self, **cards):
        base = {"P": P, "S1": "2010010010", "S2": "2020020010", "S3": "2030030010", "S4": "2060060010", "guest": "2070070010"}
        return {"event": "unit_cards", "unit": 7, "cards": {**base, **cards}, "verdict": "match"}

    def test_snapshot_needs_six_cards_the_same_p_and_a_match(self):
        snap = ui_wing.snapshot_from_events([{"event": "x"}, self.event()], P)
        self.assertEqual((snap["unit"], snap["s_cards"], snap["guest"]), (7, SNAP["s_cards"], SNAP["guest"]))
        self.assertIsNone(ui_wing.snapshot_from_events([self.event(S3=None)], P))
        self.assertIsNone(ui_wing.snapshot_from_events([self.event()], "1050080010"))
        self.assertIsNone(ui_wing.snapshot_from_events([{**self.event(), "verdict": "mismatch"}], P))
        self.assertIsNone(ui_wing.clean_snapshot({**SNAP, "guest": SNAP["s_cards"][0]}))

    def test_record_writes_private_json_and_display_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "decision-log").mkdir()
            (tmp / "decision-log" / "a.jsonl").write_text(json.dumps(self.event()) + "\n", encoding="utf-8")
            snap = ui_wing.record_snapshot(0, P, trace_dir=tmp / "decision-log", path=tmp / "snap.json", module=tmp / "snap.js")
            self.assertEqual(ui_wing.load_snapshot(tmp / "snap.json")["unit"], 7)
            self.assertIn("export default", (tmp / "snap.js").read_text(encoding="utf-8"))
            self.assertEqual(snap["p_card"], P)

    def test_memory_is_kept_for_start_and_other_settings_are_refused(self):
        schema = ui_schema.export_schema()
        config, _ = ui_wing.wing_run_config(schema, profile(), snapshot={}, allow_produce_spend=True, ledger=wl.SpendLedger(None))
        data = config["tasks"][0]["option"]["produce_config"]
        with tempfile.TemporaryDirectory() as tmp:
            memory = Path(tmp) / "memory.json"
            # A record with progress (wins) for another strategy is refused; an empty unbound one is archived (below).
            memory.write_text(json.dumps({"strategy": "vi_score", "goal": "true_end", "wins": ["s1"]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "主属性"):
                ui_wing.check_memory(data, "attach", memory)
            # 2026-10-11 review P1-1: a start request never archives (the Agent does it after a confirmed start).
            with self.assertRaisesRegex(ValueError, "主属性"):
                ui_wing.check_memory(data, "start", memory)
            self.assertTrue(memory.exists())

    def test_empty_unbound_memory_is_archived_not_refused(self):
        # 2026-10-14 live: a 10-13 record with produce null and no wins refused every Produce-tab run.
        schema = ui_schema.export_schema()
        config, _ = ui_wing.wing_run_config(schema, profile(), snapshot={}, allow_produce_spend=True, ledger=wl.SpendLedger(None))
        data = config["tasks"][0]["option"]["produce_config"]
        with tempfile.TemporaryDirectory() as tmp:
            memory = Path(tmp) / "memory.json"
            memory.write_text(json.dumps({"strategy": "vi_score", "goal": "true_end", "wins": [], "promise": None,
                                          "wing_won": False, "live_cards": {}, "produce": None}), encoding="utf-8")
            archived = ui_wing.check_memory(data, "start", memory)
            self.assertFalse(memory.exists())
            self.assertIn("unbound", archived.name)

    def test_snapshot_files_are_private_or_ignored(self):
        self.assertTrue(str(ui_wing.SNAPSHOT_FILE).replace("\\", "/").find("dev_assets/private/") >= 0)
        ignored = subprocess.run(["git", "check-ignore", "-q", "ui/game-assets/team-snapshot.js"], cwd=ROOT).returncode
        self.assertEqual(ignored, 0)


class EngineTests(unittest.TestCase):
    def test_wing_needs_a_session_and_refuses_before_anything_runs(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "dev_assets" / "private") as tmp:
            tmp = Path(tmp)
            (tmp / "profile.json").write_text(json.dumps(profile()), encoding="utf-8")
            args = ["--profile", str(tmp / "profile.json"), "--events", str(tmp / "e.jsonl"), "--stop-file", str(tmp / "s.flag"),
                    "--summary", str(tmp / "m.json"), "--offline-frames", str(tmp), "--wing"]
            self.assertEqual(ui_engine.main(args), 2)
            self.assertFalse((tmp / "run-config.json").exists())
            self.assertEqual(ui_engine.main(args[:-1] + ["--allow-produce-spend"]), 2)

    def test_protocol_and_host_carry_the_confirmation(self):
        protocol = json.loads((ROOT / "ui" / "protocol.json").read_text(encoding="utf-8"))
        start = protocol["commands"]["run.start"]["params"]["properties"]
        self.assertIn("supervised_wing", start["mode"]["enum"])
        self.assertIn("allow_produce_spend", start["spend_flags"]["items"]["enum"])
        router = (ROOT / "browser_host" / "Ui" / "UiMessageRouter.cs").read_text(encoding="utf-8")
        self.assertIn("pageSpend && derivedSpend", router)
        self.assertIn("W.I.N.G. 需要开始前确认", router)
        app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("request.confirmed = true;", app)
        # 2026-10-13 (user): the saved 开新局 switch is the consent; no page dialog, the host check is unchanged
        self.assertNotIn("openWingRunConfirm", app)


class OptionShapeTests(unittest.TestCase):
    def test_binary_wing_settings_are_switches(self):
        interface = run_tasks.load_interface()
        for key in ("Produce终局清空SP", "Produce报酬读取", "Produce保护Lv5书", "Produce报酬后OK", "Produce再抽选", "Produce找编队"):
            self.assertEqual(interface.options[key]["type"], "switch", key)
            self.assertEqual([c["name"] for c in interface.options[key]["cases"]], ["Yes", "No"], key)
        migrated = ui_schema.migrate_legacy_profile(ui_schema.export_schema(), {"schema_version": 1, "tasks": [
            {"name": "AutoProduce", "option": {"Produce报酬后OK": "按 OK", "Produce报酬读取": "关"}}]})
        self.assertEqual(migrated["tasks"][0]["option"], {"Produce报酬后OK": "Yes", "Produce报酬读取": "No"})


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TeamRulesJsTests(unittest.TestCase):
    def test_balanced_p_card_needs_a_primary_attribute(self):
        """2026-10-24 role-play: produce_config rejects 主属性「自动」 for a 均衡 P card, so 开始 must stay off until Vo/Da/Vi."""
        script = (
            "import './tests/fixtures/ui/fake_dom.mjs';"
            "import { teamCheck } from './ui/components/team-rules.js';"
            "import { useGameAssets } from './ui/components/game-assets.js';"
            "import { readFileSync } from 'node:fs';"
            "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
            "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
            "useGameAssets({ p_cards: [{ id: '1010010010', src: 'game-assets/cards/a.png', idol: 'A', rarity: 'SSR', specialty: 'Balanced' },"
            "  { id: '1010010020', src: 'game-assets/cards/b.png', idol: 'B', rarity: 'SSR', specialty: 'Dance' }] });"
            "const owned = { p: ['1010010010', '1010010020'], s: [] };"
            "const errors = (option) => teamCheck({ schema, annotations, bridgeKind: 'mock', prefs: { owned_cards: owned },"
            "  profile: { tasks: [{ name: 'AutoProduce', enabled: true, option }] } }, 'AutoProduce', { option }).errors;"
            "console.log(JSON.stringify({ auto: errors({ ProduceP卡: '1010010010' }), picked: errors({ ProduceP卡: '1010010010', Produce主属性: 'Da' }),"
            "  dance: errors({ ProduceP卡: '1010010020' }) }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertIn("P 卡无单一特长：主属性需选 Vo / Da / Vi", out["auto"])
        self.assertNotIn("P 卡无单一特长：主属性需选 Vo / Da / Vi", out["picked"])
        self.assertNotIn("P 卡无单一特长：主属性需选 Vo / Da / Vi", out["dance"])

    def test_checklist_snapshot_and_unit_page_two(self):  # noqa: C901 (one node run)
        script = (
            "import './tests/fixtures/ui/fake_dom.mjs';"
            "import { teamCheck } from './ui/components/team-rules.js';"
            "import { setTeamSnapshot } from './ui/components/team-snapshot.js';"
            "import { wingOnly, taskErrors } from './ui/components/display.js';"
            "import { primaryLine } from './ui/components/wing-run.js';"
            "import { readFileSync } from 'node:fs';"
            "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
            "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
            "const snap = JSON.parse(process.argv[1]);"
            "const owned = { p: [snap.p_card], s: [...snap.s_cards, snap.guest] };"
            "const state = (option, extra = {}) => ({ schema, annotations, bridgeKind: 'mock', prefs: { owned_cards: owned }, ...extra,"
            "  profile: { tasks: [{ name: 'AutoProduce', enabled: true, option }, { name: 'Shop', enabled: extra.shop === true, option: {} }] } });"
            "const check = (option, extra) => teamCheck(state(option, extra), 'AutoProduce', { option });"
            "const none = check({});"
            "setTeamSnapshot(snap);"
            "const withSnap = check({ ProduceP卡: snap.p_card });"
            "const page2 = check({ ProduceP卡: snap.p_card, Produce编队槽位: 'unit12' });"
            "const notOwned = check({ ProduceP卡: '1050080010' });"
            "console.log(JSON.stringify({ none, withSnap: { errors: withSnap.errors, source: withSnap.team.source, checks: withSnap.checks },"
            "  page2: { notes: page2.notes, errors: page2.errors }, notOwned: notOwned.errors,"
            "  only: wingOnly(state({ ProduceP卡: snap.p_card })), both: wingOnly(state({}, { shop: true })),"
            "  errors: taskErrors(state({ ProduceP卡: snap.p_card }, { shop: true }), { name: 'AutoProduce' }, { enabled: true, option: { ProduceP卡: snap.p_card } }),"
            "  lines: [primaryLine('自动', ''), primaryLine('Vo', '')] }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(SNAP)], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertIn("未选择 P 偶像", out["none"]["errors"])
        self.assertEqual(out["withSnap"]["errors"], [])
        self.assertEqual(out["withSnap"]["source"], "snapshot")
        self.assertTrue(all(c["ok"] for c in out["withSnap"]["checks"]))
        # 2026-10-13: both unit pages are verified; ユニット12 is an ordinary choice
        self.assertEqual(out["page2"], {"notes": [], "errors": []})
        self.assertIn("P 卡不在持有卡牌中", out["notOwned"])
        self.assertEqual((out["only"], out["both"]), (True, False))
        # 2026-10-13: W.I.N.G. starts only from 「Produce」 (wingRunState), so ticked daily tasks are no longer an error
        self.assertFalse(any(e.startswith("W.I.N.G. 要单独开始") for e in out["errors"]))
        # 2026-10-13: the start dialog is gone; the 主属性 hint names the attribute chosen for this Produce
        self.assertEqual(out["lines"], ["选择 P 偶像后，跟随其特长决定。", "本次：Vo"])

    def test_team_sources(self):
        """Scenarios: 指定ユニット (+ 自动查找编队) / 推荐编队 (the player picks the P, Maa
        recommends S + Guest for it and overwrites the chosen unit), stale snapshot, no recommendation, owned list."""
        rec = {"version": 1, "status": "needs_confirmation", "primary": "Vo", "requested_p_card": SNAP["p_card"], "requested_primary": "auto",
               "proposal": {"p_card": SNAP["p_card"], "s_cards": ["2010010020", "2020020020", "2030030020", "2060060020"],
                            "guest": "2070070020"}}
        script = (
            "import './tests/fixtures/ui/fake_dom.mjs';"
            "import { teamCheck } from './ui/components/team-rules.js';"
            "import { setTeamSnapshot } from './ui/components/team-snapshot.js';"
            "import { setTeamProposal } from './ui/components/team-proposal.js';"
            "import { modeHint } from './ui/components/team-field.js';"
            "import { readFileSync } from 'node:fs';"
            "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
            "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
            "const [snap, rec] = [JSON.parse(process.argv[1]), JSON.parse(process.argv[2])];"
            "const pp = rec.proposal;"
            "const owned = { p: [snap.p_card], s: [...snap.s_cards, snap.guest, ...pp.s_cards, pp.guest] };"
            "const state = (option, own = owned) => ({ schema, annotations, bridgeKind: 'mock', prefs: { owned_cards: own },"
            "  profile: { tasks: [{ name: 'AutoProduce', enabled: true, option }] } });"
            "const check = (option, own) => { const r = teamCheck(state(option, own), 'AutoProduce', { option });"
            "  return { mode: r.mode, errors: r.errors, notes: r.notes, source: r.team?.source, ok: r.checks.map((c) => c.ok), texts: r.checks.map((c) => c.text) }; };"
            "const P = snap.p_card;"
            "const adopted = { ProduceP卡: pp.p_card, ProduceS卡1: pp.s_cards[0], ProduceS卡2: pp.s_cards[1], ProduceS卡3: pp.s_cards[2], ProduceS卡4: pp.s_cards[3], Produce客串: pp.guest };"
            "setTeamSnapshot(snap);"
            "const out = {"
            "  unit: check({ ProduceP卡: P }), search: check({ ProduceP卡: P, Produce找编队: 'Yes', Produce编队槽位: 'unit3' }),"
            "  otherUnit: check({ ProduceP卡: P, Produce编队槽位: 'unit3' }),"
            "  snapOff: check({ ProduceP卡: P, Produce核对上次编队: 'No' }),"
            "  recNoP: check({ Produce编队方式: '自动编队' }),"
            "  recNotLoaded: check({ Produce编队方式: '自动编队', ProduceP卡: P }),"
            "  recAdoptedNoFile: check({ Produce编队方式: '自动编队', Produce编队槽位: 'unit4', ...adopted }),"
            "  emptyOwned: check({ ProduceP卡: P }, { p: [], s: [] }),"
            "  snapGuestNotOwned: check({ ProduceP卡: P }, { p: [P], s: snap.s_cards }),"
            "};"
            "setTeamProposal(rec);"
            "out.recPending = check({ Produce编队方式: '自动编队', ProduceP卡: P });"
            "out.recAdopted = check({ Produce编队方式: '自动编队', ...adopted });"
            "out.recOld = check({ Produce编队方式: '自动编队', ...adopted, ProduceS卡1: snap.s_cards[0] });"
            "out.recNotOwned = check({ Produce编队方式: '自动编队', ...adopted }, { p: [P], s: snap.s_cards });"
            "setTeamProposal({ ...rec, requested_p_card: 'auto' });"
            "out.recAutoOld = check({ Produce编队方式: '自动编队', ...adopted });"
            "setTeamProposal({ version: 1, status: 'rejected', requested_p_card: P, proposal: null });"
            "out.recNone = check({ Produce编队方式: '自动编队', ProduceP卡: P });"
            "out.hints = [modeHint('unit', { unit: 7, snapshot: true }), modeHint('unit', { unit: 7 }), modeHint('search', {}), modeHint('recommend', { unit: 3 })];"
            "console.log(JSON.stringify(out));")
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(SNAP), json.dumps(rec)], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        # (a) 指定ユニット with the snapshot of that unit: P + snapshot, all checks green
        self.assertEqual((out["unit"]["mode"], out["unit"]["source"], out["unit"]["errors"]), ("unit", "snapshot", []))
        self.assertTrue(all(out["unit"]["ok"]))
        # (b) 自动查找编队: the snapshot of the same P is used whatever unit is selected
        self.assertEqual((out["search"]["mode"], out["search"]["source"]), ("search", "snapshot"))
        # snapshot of another unit / check off -> P only
        self.assertIsNone(out["otherUnit"]["source"])
        self.assertEqual((out["snapOff"]["source"], out["snapOff"]["errors"]), (None, []))
        # (c) 推荐编队: the player picks the P (2026-10-14 user); needs the adopted recommendation for that P; the chosen
        # unit will be overwritten
        self.assertEqual(out["recNoP"]["errors"], ["未选择 P 偶像"])
        self.assertEqual(out["recNotLoaded"]["errors"], ["推荐编队未生成"])
        self.assertEqual((out["recAdoptedNoFile"]["mode"], out["recAdoptedNoFile"]["source"], out["recAdoptedNoFile"]["errors"]),
                         ("recommend", "recommend", []))
        # the overwrite is said by the 「将覆盖ユニットN」 tag and the mode hint only (2026-10-14 polish), not a note/checklist
        self.assertFalse(any("覆盖" in n for n in out["recAdoptedNoFile"]["notes"]))
        self.assertIn("ユニット4", out["recAdoptedNoFile"]["texts"])
        self.assertEqual(out["recPending"]["errors"], ["推荐更新中"])
        self.assertEqual(out["recAdopted"]["errors"], [])
        self.assertTrue(all(out["recAdopted"]["ok"]))
        self.assertEqual(out["recOld"]["errors"], ["推荐更新中"])
        self.assertIn("Guest 不在持有卡牌中", out["recNotOwned"]["errors"])
        self.assertEqual(out["recAutoOld"]["errors"], ["推荐编队未生成"])   # an old 「Maa picks P」 proposal is not for this P
        # (m) no recommendation
        self.assertEqual(out["recNone"]["errors"], ["暂无推荐：改用指定ユニット"])
        # (h) empty owned list; a snapshot Guest missing from 持有卡牌 is only a note (it was read from the game)
        self.assertIn("持有卡牌为空：需在「资源」页扫描", out["emptyOwned"]["errors"])
        self.assertEqual(out["snapGuestNotOwned"]["errors"], [])
        self.assertTrue(any("资源" in n for n in out["snapGuestNotOwned"]["notes"]))
        self.assertEqual(out["hints"], ["开始后核对ユニット7 的 P 和 S/Guest，不符就停止。", "开始后核对ユニット7 的 P 偶像，不符就停止。",
                                        "开始后在ユニット1–20 里找含此 P 偶像的编队，找不到就停止。",
                                        "开新局时把ユニット3 改成这套推荐编队（覆盖原编队），核对一致后开始。"])

    def test_recommend_alternatives_and_outdated_key(self):
        """推荐编队 UX: at most two real alternatives (stable id, distinct
        team), 「使用这队」 adopts at once and a re-render keeps it (no jump back to the first team); 重新推荐 keeps the
        chosen id; owned changes with the same counts (other cards, Lv/凸) mark the proposal outdated."""
        main = {"p_card": SNAP["p_card"], "s_cards": ["2010010020", "2020020020", "2030030020", "2060060020"], "guest": "2070070020"}
        alt_sp = {**main, "s_cards": main["s_cards"][:3] + ["2080080020"]}
        alt_aud = {**main, "guest": "2090090020"}
        rec = {"version": 1, "status": "needs_confirmation", "primary": "Vo", "requested_p_card": SNAP["p_card"], "requested_primary": "auto",
               "engine": "true_end", "generated_at": "2026-10-14T10:00:00+1100", "candidate_id": "main-1",
               "feature": "主属性技能齐全", "tradeoff": "回体较少", "owned_counts": {"p": 1, "s": 8},
               "style": {"id": "mono", "label": "单属性", "detail": "技能和支援集中在 Vo"},
               "recommender": {"ProxyTERating": 80.0, "growth": {"route_feasible": True}, "battle": {"rows": []}},
               "proposal": main,
               "alternatives": [
                   {"id": "sp", "label": "SP与回体", "feature": "回体多", "tradeoff": "攻击札较弱", "reasons": ["S4 回体"], "rating": 70.0, "proposal": alt_sp,
                    "style": {"id": "bogus", "label": "坏流派"}},                               # unknown 流派: not shown
                   {"id": "dup", "label": "重复", "proposal": main},                          # same team as main: dropped
                   {"id": "aud", "label": "试镜成长", "rating": 79.0, "proposal": alt_aud,
                    "style": {"id": "audition", "label": "オデマス", "detail": "オーディションマスタリー 2 张"}},
                   {"id": "x", "label": "第三套", "proposal": {**main, "guest": "2100100020"}}],   # more than two: dropped
               "training_hints": [{"card_id": "2100100020", "level": 40, "target_level": 80, "limit_break": 4, "slot": "S3",
                                   "replaces": "2030030020", "gain": 1.24}]}
        script = (
            "import './tests/fixtures/ui/fake_dom.mjs';"
            "import { teamField } from './ui/components/team-field.js';"
            "import * as TP from './ui/components/team-proposal.js';"
            "import { readFileSync } from 'node:fs';"
            "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
            "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
            "const rec = JSON.parse(process.argv[1]);"
            "const store = {}; globalThis.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); } };"
            "const task = schema.tasks.find((t) => t.name === 'AutoProduce');"
            "const keys = task.options.filter((k) => annotations.options[k]?.group === 'Produce编队');"
            "const pp = rec.proposal;"
            "const ids = [pp.p_card, ...pp.s_cards, pp.guest, '2080080020', '2090090020', '2100100020'];"
            "const owned = { p: [pp.p_card], s: ids.slice(1) };"
            "const all = (node, out = []) => { for (const c of node.childNodes || []) { if (c.tagName) { out.push(c); all(c, out); } } return out; };"
            "const vals = (t) => ({ Produce编队方式: '自动编队', ProduceP卡: t.p_card, ProduceS卡1: t.s_cards[0], ProduceS卡2: t.s_cards[1],"
            "  ProduceS卡3: t.s_cards[2], ProduceS卡4: t.s_cards[3], Produce客串: t.guest });"
            "const render = async (option) => {"
            "  const changes = [];"
            "  const state = { schema, annotations, bridgeKind: 'mock', prefs: { owned_cards: owned }, profile: { tasks: [{ name: 'AutoProduce', enabled: true, option }] } };"
            "  const field = teamField({ state, group: 'Produce编队', keys, values: option, disabled: false, onChange: (c) => changes.push(c), focusKey: 'k', actions: {} });"
            "  await Promise.resolve(); await Promise.resolve();"
            "  const more = all(field).find((e) => e.tagName === 'BUTTON' && e.textContent.startsWith('其他推荐') && e.attributes['aria-expanded'] === 'false');"
            "  if (more) more.listeners.click[0]();"
            "  const els = all(field);"
            "  const use = els.filter((e) => e.tagName === 'BUTTON' && e.textContent === '使用这队');"
            "  return { text: field.textContent, changes, use, alts: els.filter((e) => e.dataset?.candidate).map((e) => e.dataset.candidate) };"
            "};"
            "TP.setTeamProposal(rec);"
            "const clean = TP.teamProposal();"
            "const first = await render(vals(pp));"
            "const firstChanges = [...first.changes];"
            "first.use[0].listeners.click[0]();"
            "const afterUse = { changes: first.changes.slice(firstChanges.length), selected: TP.selectedCandidateId() };"
            "const onAlt = await render(vals(rec.alternatives[0].proposal));"
            "const oldSp = rec.alternatives[0].proposal;"
            "TP.setTeamProposal({ ...rec, generated_at: 'later', alternatives: [rec.alternatives[2],"
            "  { ...rec.alternatives[0], proposal: { ...oldSp, s_cards: [...oldSp.s_cards.slice(0, 3), '2100100020'] } }] });"
            "const reRec = await render(vals(oldSp));"
            "const fp = TP.ownedFingerprint(owned);"
            "const swapped = { ...owned, s: [...owned.s.slice(0, -1), '2110110020'] };"
            "const leveled = { ...owned, details: { [pp.guest]: { level: 50, limit_break: 3 } } };"
            "const basis = { generatedAt: clean.generatedAt, fp };"
            "console.log(JSON.stringify({ altIds: clean.alternatives.map((a) => a.id), candidates: TP.proposalCandidates(clean).map((c) => c.id),"
            "  first: { text: first.text, changes: firstChanges, alts: first.alts }, afterUse, onAlt: { text: onAlt.text, changes: onAlt.changes, alts: onAlt.alts },"
            "  reRec: { changes: reRec.changes },"
            "  outdated: [TP.proposalOutdated(clean, owned, basis), TP.proposalOutdated(clean, swapped, basis), TP.proposalOutdated(clean, leveled, basis),"
            "    TP.proposalOutdated(clean, swapped, null)],"
            "  own: TP.proposalOutdated({ ...clean, ownedFingerprint: fp }, owned), ownChanged: TP.proposalOutdated({ ...clean, ownedFingerprint: fp }, leveled),"
            "  game: [TP.minigameAuto(clean, rec.alternatives[0].proposal, true), TP.minigameAuto(clean, pp, true)] }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(rec)], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["altIds"], ["sp", "aud"])
        self.assertEqual(out["candidates"], ["main-1", "sp", "aud"])
        self.assertEqual(out["first"]["changes"], [])                     # main already adopted: nothing written
        self.assertEqual(out["first"]["alts"], ["sp", "aud"])
        for word in ("综合推荐", "特点主属性技能齐全", "取舍回体较少", "其他推荐（2）", "SP与回体", "试镜成长", "单属性", "オデマス"):
            self.assertIn(word, out["first"]["text"])
        self.assertNotIn("坏流派", out["first"]["text"])                    # 流派: known ids only
        # 训练后可加入推荐 (2026-10-25, was 练级): under the adopted main team only: target level, gain and the slot
        for word in ("训练后可加入推荐", "Lv40→80", "+1.2", "换 S3"):
            self.assertIn(word, out["first"]["text"])
        self.assertNotIn("Lv40→80", out["onAlt"]["text"])
        sp = rec["alternatives"][0]["proposal"]
        self.assertEqual(out["afterUse"]["changes"], [{"ProduceP卡": sp["p_card"], "ProduceS卡1": sp["s_cards"][0], "ProduceS卡2": sp["s_cards"][1],
                                                        "ProduceS卡3": sp["s_cards"][2], "ProduceS卡4": sp["s_cards"][3], "Produce客串": sp["guest"]}])
        self.assertEqual(out["afterUse"]["selected"], "sp")
        # the adopted alternative stays on top; the main recommendation is listed under 其他推荐
        self.assertEqual(out["onAlt"]["changes"], [])
        self.assertIn("SP与回体 · 主属性", out["onAlt"]["text"])
        self.assertEqual(out["onAlt"]["alts"], ["main-1", "aud"])
        # 重新推荐 with the same ids: the chosen alternative is adopted again (stable id, not array order)
        self.assertEqual(out["reRec"]["changes"][0]["ProduceS卡4"], "2100100020")  # new cards of the same 「sp」 route
        self.assertEqual(out["outdated"], [False, True, True, False])     # counts alone missed the swap
        self.assertEqual((out["own"], out["ownChanged"]), (False, True))
        self.assertEqual(out["game"], [{"play": True, "rating": 70}, {"play": False, "rating": 80}])
        # the run side reads the same alternative's score
        team = {"p_card": sp["p_card"], "s_cards": sp["s_cards"], "guest": sp["guest"]}
        with patch("tools.ui_wing.minigame_entry_verified", return_value=True):
            self.assertEqual(ui_wing.minigame_choice("auto", team, rec, True), (True, "low_rating"))
            self.assertEqual(ui_wing.minigame_choice("auto", dict(main), rec, True), (False, "rating_ok"))

    def test_team_field_renders_each_source(self):
        """The 编队 field: two source chips; 指定ユニット = P picker + ユニット pills (2026-10-24, was a select) + 「自动查找编队」 switch and no
        recommendation; 推荐编队 = the player's target P (更换 allowed), 「将覆盖ユニットN」, the six recommended cards, adoption of
        S/Guest for that P."""
        rec = {"version": 1, "status": "needs_confirmation", "primary": "Vo", "requested_p_card": SNAP["p_card"], "requested_primary": "auto",
               "recommender": {"p_candidates": [{"p_card": SNAP["p_card"], "primary": "Vo", "rating": 70.5}]},
               "proposal": {"p_card": SNAP["p_card"], "s_cards": ["2010010020", "2020020020", "2030030020", "2060060020"],
                            "guest": "2070070020"}}
        script = (
            "import './tests/fixtures/ui/fake_dom.mjs';"
            "import { teamField } from './ui/components/team-field.js';"
            "import { setTeamSnapshot } from './ui/components/team-snapshot.js';"
            "import { setTeamProposal } from './ui/components/team-proposal.js';"
            "import { readFileSync } from 'node:fs';"
            "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
            "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
            "const [snap, rec] = [JSON.parse(process.argv[1]), JSON.parse(process.argv[2])];"
            "const task = schema.tasks.find((t) => t.name === 'AutoProduce');"
            "const keys = task.options.filter((k) => annotations.options[k]?.group === 'Produce编队');"
            "const owned = { p: [snap.p_card], s: [...snap.s_cards, snap.guest, ...rec.proposal.s_cards, rec.proposal.guest] };"
            "const all = (node, out = []) => { for (const c of node.childNodes || []) { if (c.tagName) { out.push(c); all(c, out); } } return out; };"
            "const render = async (option) => {"
            "  const changes = [];"
            "  const state = { schema, annotations, bridgeKind: 'mock', prefs: { owned_cards: owned }, profile: { tasks: [{ name: 'AutoProduce', enabled: true, option }] } };"
            "  const field = teamField({ state, group: 'Produce编队', keys, values: option, disabled: false, onChange: (c) => changes.push(c), focusKey: 'k', actions: {} });"
            "  const text = field.textContent;"
            "  const els = all(field);"
            "  const seg = els.filter((e) => e.attributes.role === 'radio' && /segment/.test(e.attributes.class || '')).map((e) => [e.textContent, e.attributes['aria-checked'], 'disabled' in e.attributes, e.attributes.title || '']);"
            "  const units = els.filter((e) => e.attributes.role === 'radio' && /chip/.test(e.attributes.class || '')).map((e) => [e.textContent, e.attributes['aria-checked']]);"
            "  const sw = els.filter((e) => e.attributes.role === 'switch').map((e) => e.attributes['aria-checked']);"
            "  const tips = els.map((e) => e.attributes.title || '').filter(Boolean).join(' | ');"
            "  const selects = els.filter((e) => e.tagName === 'SELECT').length;"
            "  const buttons = els.filter((e) => e.tagName === 'BUTTON').map((e) => e.textContent);"
            "  await Promise.resolve();"
            "  const pick = (label) => { const b = els.find((e) => e.attributes.role === 'radio' && e.textContent === label); b.listeners.click[0](); };"
            "  const flip = () => { els.find((e) => e.attributes.role === 'switch').listeners.click[0](); };"
            "  return { text, tips, seg, units, sw, selects, buttons, changes, pick, flip };"
            "};"
            "const P = snap.p_card;"
            "setTeamSnapshot(snap);"
            "setTeamProposal(rec);"
            "const unit = await render({ ProduceP卡: P });"
            "unit.flip();"
            "unit.pick('推荐编队');"
            "const search = await render({ ProduceP卡: P, Produce找编队: 'Yes' });"
            "const recommend = await render({ ProduceP卡: P, Produce编队方式: '自动编队', Produce编队槽位: 'unit6' });"
            "recommend.pick('指定ユニット');"
            "const snapOff = await render({ ProduceP卡: P, Produce核对上次编队: 'No' });"
            "setTeamProposal({ version: 1, status: 'rejected', requested_p_card: P, proposal: null });"
            "const none = await render({ ProduceP卡: P });"
            "setTeamProposal({ version: 1, status: 'rejected', reason: 'auto_team_rejected', engine: 'wing_team', fallback_reason: 'p_card_unavailable',"
            "  reasons: ['fallback:p_card_unavailable'], requested_p_card: P, proposal: null });"
            "const pMissing = await render({ Produce编队方式: '自动编队', ProduceP卡: P });"
            "const noP = await render({ Produce编队方式: '自动编队' });"
            "setTeamSnapshot(null);"
            "const noSnap = await render({ ProduceP卡: P });"
            "const strip = (r) => ({ text: r.text, tips: r.tips, seg: r.seg, units: r.units, sw: r.sw, selects: r.selects, buttons: r.buttons, changes: r.changes });"
            "console.log(JSON.stringify({ unit: strip(unit), search: strip(search), recommend: strip(recommend), snapOff: strip(snapOff), none: strip(none), pMissing: strip(pMissing), noP: strip(noP), noSnap: strip(noSnap) }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(SNAP), json.dumps(rec)], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        adopt = {"ProduceP卡": SNAP["p_card"], "ProduceS卡1": "2010010020", "ProduceS卡2": "2020020020", "ProduceS卡3": "2030030020",
                 "ProduceS卡4": "2060060020", "Produce客串": "2070070020"}
        clear = {k: "" for k in adopt if k != "ProduceP卡"}
        unit = out["unit"]
        self.assertEqual([s[:3] for s in unit["seg"]], [["指定ユニット", "true", False], ["推荐编队", "false", False]])
        self.assertEqual((unit["selects"], unit["sw"]), (0, ["false"]))
        self.assertEqual(len(unit["units"]), 20)
        self.assertEqual([u for u in unit["units"] if u[1] == "true"], [["1", "true"]])   # default ユニット1
        self.assertIn("自动查找编队", unit["text"])
        self.assertIn("更换 P 偶像", unit["buttons"])
        self.assertIn("改为只核对 P", unit["buttons"])               # 2026-10-26: reads as a button, not a status
        self.assertIn("将核对", unit["text"])
        self.assertIn("P + S1–S4 + Guest · ユニット1 · 10-10 读取", unit["text"])   # what 开始 compares, with the snapshot's unit and read date
        self.assertIn("开始后核对ユニット1 的 P 和 S/Guest，不符就停止。", unit["tips"])  # 2026-10-24: a tooltip
        self.assertNotIn("推荐理由", unit["text"])                          # no recommendation in 指定ユニット
        self.assertNotIn("重新推荐", unit["buttons"])
        self.assertEqual(unit["changes"], [{"Produce找编队": "Yes"}, {"Produce编队方式": "自动编队", **adopt}])
        search = out["search"]
        self.assertEqual((search["selects"], search["sw"]), (0, ["true"]))
        self.assertEqual(search["seg"][0][1], "true")                        # still the 指定ユニット chip
        self.assertIn("找这套游戏内编队", search["tips"])
        rec_out = out["recommend"]
        self.assertEqual(rec_out["seg"][1][1], "true")
        self.assertEqual(rec_out["sw"], [])                                  # 自动查找编队 only under 指定ユニット
        self.assertNotIn("将核对", rec_out["text"])
        self.assertIn("将覆盖ユニット6", rec_out["text"])
        self.assertIn("估算", rec_out["text"])                               # 2026-10-26: needs_confirmation is said on the tag, not only in its tooltip
        self.assertIn("P 偶像", rec_out["text"])
        self.assertIn("更换 P 偶像", rec_out["buttons"])                     # 2026-10-14 user: the player picks the P
        self.assertNotIn("比较了", rec_out["text"])
        self.assertIn("开新局时把ユニット6 改成这套推荐编队", rec_out["tips"])
        # first the adoption of P/S/Guest (microtask), then leaving 推荐编队 clears S/Guest again
        self.assertEqual(rec_out["changes"], [adopt, {"Produce编队方式": "指定编队", **clear}])
        self.assertIn("本次只核对 P · ユニット1 · 10-10 读取", out["snapOff"]["text"])
        self.assertIn("恢复核对 S/Guest", out["snapOff"]["buttons"])
        self.assertIn("开始后核对ユニット1 的 P 偶像，不符就停止。", out["snapOff"]["tips"])
        self.assertEqual(out["none"]["seg"][1][2:], [True, "暂无推荐"])     # (m) no recommendation
        self.assertNotIn("暂无推荐", out["none"]["text"])
        self.assertNotIn("将核对", out["noSnap"]["text"])  # 2026-10-24: no placeholder row before the first read
        self.assertIn("持有 P 卡缺少 Lv/凸 或卡牌资料", out["pMissing"]["text"])
        self.assertIn("前往资源扫描", out["pMissing"]["buttons"])
        self.assertIn("选择 P 偶像后推荐 S 卡与 Guest", out["noP"]["text"])
        self.assertIn("选择 P 偶像", out["noP"]["buttons"])
        self.assertEqual(out["none"]["changes"], [])

if __name__ == "__main__":
    unittest.main()
