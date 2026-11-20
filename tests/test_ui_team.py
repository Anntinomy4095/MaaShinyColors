"""Produce 编队 settings: PI v2 task options, single-input card options through ui_schema / run_tasks,
produce_config team validation, the wiki P-card list parser and the UI team helpers. Offline; no network, no game."""
import copy
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from agent.core import produce_config as pc
from tools import run_tasks, ui_schema
from tools import ui_game_assets_fetch as fetch
from tools.check_project import load_jsonc

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
CARD_KEYS = ["ProduceP卡", "ProduceS卡1", "ProduceS卡2", "ProduceS卡3", "ProduceS卡4", "Produce客串"]
P_SAMPLE = """<table class="wikitable"><caption>P-UR
</caption><tbody><tr><th>Icon</th><th>Fes</th><th>Character</th><th>Title</th><th>JP Title</th><th>How to Acquire</th>
<th>Specialty</th></tr>
<tr><td><img src="https://image.shinycolors.wiki/8/83/CardIcon-1050080010.png" /></td>
<td><img src="https://image.shinycolors.wiki/1/11/CardIcon-1050080010_FES.png" /></td>
<td><a>Kiriko<br />Yukoku</a></td><td>Chi・Chi</td><td>【知・知・識・識】<br />幽谷霧子</td><td>Permanent Gacha<br />2026/04/17</td>
<td>Vocal</td></tr>
<tr><td><img src="https://evil.example/8/83/CardIcon-1050999990.png" /></td><td></td><td>x</td><td>y</td><td>【a】<br />b</td></tr>
</tbody></table>
<table><caption>S-SSR
</caption><tbody><tr><td><img src="https://image.shinycolors.wiki/a/a8/CardIcon-2040010010.png" /></td>
<td>Mano</td><td>T</td><td>【x】<br />櫻木真乃</td><td>Event</td></tr></tbody></table>"""


class TaskOptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.interface = run_tasks.load_interface()
        cls.schema = ui_schema.export_schema()

    def test_task_declares_the_team_options_and_stays_a_no_click_stub(self):
        task = self.interface.tasks["AutoProduce"]
        self.assertEqual(task["entry"], "SC.Produce.Entry")
        self.assertFalse(task["default_check"])
        self.assertLessEqual(set(CARD_KEYS) | {"Produce编队方式", "Produce编队槽位", "Produce主属性", "Produce难度",
                                               "Produce目标", "Produce次数"}, set(task["option"]))
        self.assertNotIn("AutoProduce", run_tasks.LIVE_TASK_CLICK_CAPS)
        pipeline = load_jsonc(ROOT / "assets" / "resource" / "base" / "pipeline" / "placeholders.json")
        self.assertEqual(pipeline["SC.Produce.Entry"]["custom_action"], "SC.FailTask")
        modes = [c["name"] for c in self.interface.options["Produce编队方式"]["cases"]]
        self.assertEqual(modes, ["指定编队", "自动编队"])  # never the game's recommended team
        self.assertEqual(self.interface.options["Produce编队槽位"]["default_case"], "unit1")

    def test_carrier_nodes_exist_for_every_override(self):
        pipeline = load_jsonc(ROOT / "assets" / "resource" / "base" / "pipeline" / "produce.json")
        task = self.interface.tasks["AutoProduce"]
        for key in task["option"]:
            option = self.interface.options[key]
            overrides = [option.get("pipeline_override", {})] + [c.get("pipeline_override", {}) for c in option.get("cases", [])]
            for override in overrides:
                for node in override:
                    if node.startswith("SC.Produce.Config."):
                        self.assertIn(node, pipeline, key)
                        self.assertEqual(pipeline[node]["recognition"], "DirectHit")
                        self.assertNotIn("action", pipeline[node])  # attach carriers only: no click

    def test_defaults_resolve_to_a_valid_produce_config(self):
        plan, = run_tasks.plan_tasks(self.interface, {"tasks": [{"name": "AutoProduce"}]})
        attach = {k: v for node, fields in plan.pipeline_override.items() if node.startswith("SC.Produce.Config.")
                  for k, v in fields["attach"].items()}
        config = pc.ProduceConfig.from_task_options(attach)
        self.assertEqual((config.goal, config.data["unit_preset"], config.team["mode"]), ("true_end", "unit1", "manual"))
        self.assertFalse(config.data["enabled"])
        self.assertEqual(config.team["s_cards"], [None] * 4)

    def test_card_input_values_are_substituted_and_verified(self):
        option = {"ProduceP卡": "1050080010", "ProduceS卡1": "2050170010", "Produce客串": "", "Produce目标": "True End",
                  "Produce主属性": "Da"}
        plan, = run_tasks.plan_tasks(self.interface, {"tasks": [{"name": "AutoProduce", "option": option}]})
        override = plan.pipeline_override
        self.assertEqual(override["SC.Produce.Config.Card.P"]["attach"], {"p_card": "1050080010"})
        self.assertEqual(override["SC.Produce.Config.Card.S1"]["attach"], {"s1": "2050170010"})
        self.assertEqual(override["SC.Produce.Config.Card.Guest"]["attach"], {"guest": ""})
        attach = {k: v for node, fields in override.items() if node.startswith("SC.Produce.Config.") for k, v in fields["attach"].items()}
        config = pc.ProduceConfig.from_task_options(attach)
        self.assertEqual((config.manual["strategy"], config.team["primary_stat"]), ("da_score", "Da"))
        for bad in ({"ProduceP卡": "2050170010"}, {"ProduceS卡1": "1050080010"}, {"ProduceS卡2": "20501700"},
                    {"ProduceS卡3": "2050170010 "}, {"ProduceP卡": {"card": "x"}}):
            with self.subTest(bad), self.assertRaises(ValueError):
                run_tasks.plan_tasks(self.interface, {"tasks": [{"name": "AutoProduce", "option": bad}]})

    def test_input_substitution_keeps_types(self):
        option = {"type": "input", "inputs": [{"name": "n", "pipeline_type": "int", "verify": "^\\d+$"}, {"name": "s"}],
                  "pipeline_override": {"X": {"attach": {"n": "{n}", "text": "a{s}b", "keep": "{other}"}}}}
        interface = run_tasks.Interface({}, {"K": option}, {})
        self.assertEqual(run_tasks.option_override(interface, "K", {"n": "7", "s": "z"}),
                         {"X": {"attach": {"n": 7, "text": "azb", "keep": "{other}"}}})
        with self.assertRaises(ValueError):
            run_tasks.option_override(interface, "K", "7")  # several inputs need an object

    def test_stale_case_name_runs_with_the_default(self):
        # 2026-10-28 live: a profile saved with 「开启（一括，每日 1 次）」 refused the whole run after the case was renamed.
        interface = run_tasks.load_interface()
        on = run_tasks.option_override(interface, "プレゼント领取", "开启")
        self.assertEqual(run_tasks.option_override(interface, "プレゼント领取", "开启（一括，每日 1 次）"), on)
        self.assertEqual(on["SC.Present.Config"]["attach"], {"enabled": True})
        plan, = run_tasks.plan_tasks(interface, {"tasks": [{"name": "PresentClaim",
                                                            "option": {"プレゼント领取": "开启（一括，含未知物品）"}}]})
        self.assertEqual(plan.pipeline_override["SC.Present.Config"]["attach"], {"enabled": True})

    def test_ui_profile_round_trip_carries_card_strings(self):
        profile = {"schema_version": 1, "preset": None, "tasks": [
            {"name": "AutoProduce", "enabled": True, "option": {"ProduceP卡": "1050080010", "ProduceS卡4": "", "Produce编队方式": "自动编队"}}]}
        config = ui_schema.profile_to_run_config(self.schema, profile)
        self.assertEqual(config["tasks"][0]["option"]["ProduceP卡"], "1050080010")
        # 2026-10-12: a new W.I.N.G. is allowed by default (the host's per-run confirmation is the consent)
        self.assertEqual(config["allow_spend"], ["wing-produce-start"])
        self.assertEqual(ui_schema.spend_flags_for_profile(self.schema, profile), ["allow_produce_spend"])
        run_tasks.plan_tasks(self.interface, config)
        bad = copy.deepcopy(profile)
        bad["tasks"][0]["option"]["ProduceP卡"] = "../../x"
        with self.assertRaises(ValueError):
            ui_schema.profile_to_run_config(self.schema, bad)


class ProduceTeamConfigTests(unittest.TestCase):
    def team(self, **changes):
        data = pc.default_config("true_end")
        data["team"] = {**copy.deepcopy(pc.DEFAULT_TEAM), **changes}
        return data

    def test_team_is_optional_and_defaults_to_manual(self):
        config = pc.ProduceConfig.from_dict(pc.default_config())
        self.assertEqual(config.team, pc.DEFAULT_TEAM)
        self.assertEqual(config.data["unit_preset"], "unit1")

    def test_team_validation(self):
        ok = pc.ProduceConfig.from_dict(self.team(p_card="1050080010", s_cards=["2050170010", None, None, None], guest="2040010010"))
        self.assertEqual(ok.team["guest"], "2040010010")
        for changes in ({"mode": "game_recommended"}, {"p_card": "2050170010"}, {"s_cards": ["1050080010", None, None, None]},
                        {"s_cards": [None] * 5}, {"s_cards": ["2050170010", "2050170010", None, None]},
                        {"guest": "2050170010", "s_cards": ["2050170010", None, None, None]}, {"primary_stat": "Me"},
                        {"primary_stat": "Da"},  # Da conflicts with the default vo_score strategy
                        {"p_card": "1040170010", "s_cards": ["2050170010", None, None, None]},  # same idol: P vs S
                        {"s_cards": ["2050170010", None, None, None], "guest": "2040170020"},   # same idol: S vs guest
                        {"s_cards": ["2050170010", "2040170020", None, None]}):                  # same idol: S vs S
            with self.subTest(changes), self.assertRaises(ValueError):
                pc.ProduceConfig.from_dict(self.team(**changes))
        data = self.team()
        data["team"]["extra"] = 1
        with self.assertRaises(ValueError):
            pc.ProduceConfig.from_dict(data)

    def test_warnings_and_auto_hook(self):
        same_idol = {"mode": "manual", "p_card": "1040170010", "s_cards": ["2050170010", None, None, None], "guest": None}
        self.assertEqual(pc.team_warnings(same_idol), ["same_idol_twice", "s_cards_not_registered"])
        self.assertEqual(pc.duplicate_idols(["1040170010", "2050170010", None, "", "2040010010"]), {"017": ["1040170010", "2050170010"]})
        self.assertEqual(pc.duplicate_idols(pc.team_cards({"p_card": None, "s_cards": [None] * 4, "guest": None})), {})
        self.assertEqual(pc.owned_missing({"p_card": "1040170010", "s_cards": [None] * 4, "guest": "2040010010"},
                                          {"p": ["1040170010"], "s": []}), ["2040010010"])  # the guest must be owned too
        self.assertEqual(pc.UNIT_PRESETS[0], "unit1")
        self.assertEqual(len(pc.UNIT_PRESETS), 20)
        data = pc.default_config()
        data["unit_preset"] = "unit21"
        with self.assertRaises(ValueError):
            pc.ProduceConfig.from_dict(data)
        self.assertEqual(pc.idol_of("2050170010"), "017")
        self.assertEqual(pc.auto_team_plan({"mode": "manual"})["stop"], False)
        self.assertEqual(pc.auto_team_plan({"mode": "auto"})["reason"], "owned_cards_unknown")
        self.assertEqual(pc.auto_team_plan({"mode": "auto"}, {"p": ["1040170010"], "s": []})["reason"], "owned_cards_unknown")
        # wing_team.select_team with ids only: levels / TE flags unknown -> no eligible P card, nothing proposed
        result = pc.auto_team_plan({"mode": "auto"}, {"p": ["1040170010"], "s": ["2050170010"]})
        self.assertTrue(result["stop"])  # auto mode never starts a run by itself
        self.assertEqual((result["reason"], result["status"], result["proposal"]), ("auto_team_rejected", "rejected", None))

    def test_task_options_follow_the_existing_rules(self):
        with self.assertRaises(ValueError):
            pc.ProduceConfig.from_task_options({"goal": "weekly_count", "difficulty": "master"})
        with self.assertRaises(ValueError):
            pc.ProduceConfig.from_task_options({"goal": "true_end", "difficulty": "beginner"})
        with self.assertRaises(ValueError):
            pc.ProduceConfig.from_task_options({"unknown": 1})
        master = pc.ProduceConfig.from_task_options({"goal": "true_end", "difficulty": "master", "max_runs": 2})
        self.assertTrue(master.data["allow_master"])
        self.assertEqual((master.caps["max_runs"], master.caps["max_stamina"]), (2, 60))
        weekly = pc.ProduceConfig.from_task_options({"goal": "weekly_count", "max_runs": 1})
        self.assertEqual(weekly.caps["max_skip_tickets"], 1)
        self.assertFalse(weekly.data["premium_currency_allowed"] or weekly.data["recovery_soda_allowed"])


class ProduceListTests(unittest.TestCase):
    def test_parse_produce_list_uses_header_columns(self):
        cards = fetch.parse_produce_list(P_SAMPLE)
        self.assertEqual([c["card_id"] for c in cards], ["1050080010"])  # foreign host and S table ignored
        kiriko = cards[0]
        self.assertEqual((kiriko["rarity"], kiriko["idol"], kiriko["title"], kiriko["idol_en"], kiriko["specialty"]),
                         ("UR", "幽谷霧子", "知・知・識・識", "Kiriko Yukoku", "Vocal"))
        self.assertTrue(kiriko["icon_url"].endswith("/CardIcon-1050080010.png"))  # not the _FES column
        self.assertEqual([c["card_id"] for c in fetch.parse_support_list(P_SAMPLE)], ["2040010010"])

    def test_lists_are_separate_and_gitignored(self):
        self.assertEqual(set(fetch.WIKI_LISTS), {"support", "produce"})
        self.assertEqual(fetch.WIKI_LISTS["produce"][0], "https://shinycolors.wiki/wiki/Card_List/Produce")
        self.assertTrue(str(fetch.WIKI_P_DIR.resolve()).startswith(str(fetch.SRC_DIR.resolve())))
        if shutil.which("git"):
            for probe in ("dev_assets/private/game-assets-src/wiki-produce/icons/CardIcon-1050080010.png",
                          "dev_assets/private/game-assets-src/wiki-produce/cards.json"):
                self.assertEqual(subprocess.run(["git", "check-ignore", "-q", probe], cwd=ROOT).returncode, 0, probe)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TeamUiNodeTests(unittest.TestCase):
    def test_team_helpers(self):
        manifest = {"cards": [{"id": "2050170010", "idol": "芹沢あさひ", "rarity": "UR", "specialty": "Dance (Vocal)",
                               "tags": ["回体"], "src": "game-assets/cards/2050170010.png"}],
                    "p_cards": [{"id": "1040170010", "idol": "芹沢あさひ", "rarity": "SSR", "specialty": "Vocal<script>",
                                 "src": "game-assets/cards/1040170010.png"},
                                {"id": "2050170010", "src": "game-assets/cards/2050170010.png"}]}
        script = (
            "import { useGameAssets, anyCardArt, idolNumber } from './ui/components/game-assets.js';"
            "import { normalizeOwned } from './ui/components/team-store.js';"
            "import { specialtyShort, cardFilters, cardsOf } from './ui/components/team-picker.js';"
            "import { fieldCheck } from './ui/components/team-field.js';"
            "import { cleanScan, mergeOwned } from './ui/components/team-import.js';"
            "import { sanitizeProfile, profileToRunConfig, inputValueOk } from './ui/components/store.js';"
            "import { optionRows, visibleOptions, caseLabel, taskVisible, startBlockers, wingBlockers } from './ui/components/display.js';"
            "import { readFileSync } from 'node:fs';"
            "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
            "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
            "const a = useGameAssets(JSON.parse(process.argv[1]));"
            "const stored = { schema_version: 1, preset: null, tasks: [{ name: 'AutoProduce', enabled: true, option: {"
            " ProduceP卡: '1040170010', ProduceS卡1: '2050170010', ProduceS卡2: 'evil', Produce客串: '1040170010' } }] };"
            "const profile = sanitizeProfile(schema, stored, null);"
            "const entry = profile.tasks.find((t) => t.name === 'AutoProduce');"
            "const state = { schema, annotations, profile, bridgeKind: 'mock', prefs: { owned_cards: { p: ['1040170010'], s: ['2050170010'] } } };"
            "const task = schema.tasks.find((t) => t.name === 'AutoProduce');"
            "const rows = optionRows(state, task, entry);"
            "const note = annotations.option_groups['Produce编队'];"
            "const slots = rows.find((r) => r.group).keys.map((key) => ({ key }));"
            "const values = Object.fromEntries(slots.map((s) => [s.key, entry.option[s.key]]));"
            "const auto = { ...state, profile: { ...profile, tasks: [{ ...entry, option: { ...entry.option, Produce编队方式: '自动编队' } }] } };"
            "const autoRows = optionRows(auto, task, auto.profile.tasks[0]).map((r) => r.key || r.group);"
            "console.log(JSON.stringify({"
            " p: a.pCards.map((c) => [c.id, c.specialty]), s: a.cards.map((c) => [c.id, c.specialty, c.tags]),"
            " art: anyCardArt('1040170010')?.idol, idol: idolNumber('2050170010'),"
            " owned: normalizeOwned({ p: ['1040170010', '2050170010', '1040170010'], s: ['2050170010', 'x', 5] }),"
            " short: ['Dance (Vocal)', 'Vocal & Visual', 'Balanced'].map(specialtyShort),"
            " filters: cardFilters(cardsOf('S')).map((f) => f.options.length),"
            " option: entry.option, run: profileToRunConfig(schema, profile).tasks.find((t) => t.name === 'AutoProduce').option,"
            " rows: rows.map((r) => r.key || r.group), autoRows, slots: slots.map((s) => s.key),"
            " notes: fieldCheck(state, note, values), autoNotes: fieldCheck(auto, note, { ...values, Produce编队方式: '自动编队' }),"
            " blockers: wingBlockers(state).map((b) => b.name), daily: startBlockers(state).map((b) => b.name),"
            " notOwned: fieldCheck({ ...state, prefs: {} }, note, values).errors.length,"
            " scan: cleanScan({ version: 1, frames: [{}], cards: [{ id: '2050170010', score: 2, verify: 0.8, frames: 1 }, { id: 'x' }, { id: '2050170010' }, { id: '1040170010', score: 0.9, verify: 0.9, frames: 2, confidence: 0.95 }, { id: '2040170010', confidence: 'high' }] }),"
            " merged: mergeOwned({ p: ['1040170010'], s: [] }, ['2050170010', '1040170010']),"
            " label: caseLabel(state, 'ProduceP卡', '1040170010'), empty: caseLabel(state, 'ProduceS卡4', ''),"
            " visible: [taskVisible(state, 'AutoProduce'), taskVisible(state, 'MissionsClaim')],"
            " ok: [inputValueOk(schema.options['ProduceP卡'], '1040170010'), inputValueOk(schema.options['ProduceP卡'], '2040170010')] }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(manifest, ensure_ascii=False)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["p"], [["1040170010", ""]])  # bad specialty dropped; S id in the P list dropped
        self.assertEqual(out["s"], [["2050170010", "Dance (Vocal)", ["回体"]]])
        self.assertEqual((out["art"], out["idol"]), ("芹沢あさひ", "017"))
        self.assertEqual(out["owned"], {"p": ["1040170010"], "s": ["2050170010"]})
        self.assertEqual(out["short"], ["Da(Vo)", "Vo&Vi", "均衡"])
        self.assertEqual(out["filters"], [2, 2, 5])
        self.assertEqual(out["option"]["ProduceP卡"], "1040170010")
        self.assertEqual(out["option"]["ProduceS卡2"], "")  # invalid id dropped back to the default
        self.assertEqual(out["option"]["Produce客串"], "")  # a P id cannot sit in an S slot
        self.assertEqual(out["run"]["ProduceS卡1"], "2050170010")
        # 2026-10-11 redesign: one 编队 field (mode, ユニット, 自动找, P and the developer-registered S/Guest keys)
        self.assertEqual(out["slots"], ["Produce编队方式", "Produce编队槽位", "Produce找编队"] + CARD_KEYS + ["Produce核对上次编队"])
        # 2026-10-13 (user): 可开新局 is fixed to Yes and hidden; 目标 leads the list
        self.assertEqual(out["rows"][:4], ["Produce目标", "Produce难度", "Produce主属性", "Produce编队"])
        self.assertEqual(out["autoRows"][:4], ["Produce目标", "Produce难度", "Produce主属性", "Produce编队"])
        self.assertNotIn("Produce可开新局", out["rows"])
        self.assertTrue(out["notes"]["errors"][0].startswith("同一偶像出现两次：芹沢あさひ"), out["notes"])
        # both cards are owned; the fixture P card's specialty is dropped (bad value), so 主属性「自动」 cannot decide
        self.assertEqual(out["notes"]["errors"][1:], ["P 卡无单一特长：主属性需选 Vo / Da / Vi"])
        self.assertEqual([c["ok"] for c in out["notes"]["checks"]], [True, False, True, True])
        self.assertEqual(out["notOwned"], 4)  # + P / S 「不在持有卡牌中」 when 持有卡牌 is empty (and the specialty error)
        self.assertEqual(out["blockers"], ["AutoProduce"])  # 「Produce」 开始 disabled while the W.I.N.G. settings have errors
        self.assertEqual(out["daily"], [])  # 2026-10-13: W.I.N.G. no longer blocks the daily 开始
        # confidence (2026-10-08b): missing or non-numeric -> None (treated as low by the resources page)
        self.assertEqual(out["scan"]["cards"], [
            {"id": "2050170010", "score": 1, "verify": 0.8, "frames": 1, "confidence": None},
            {"id": "1040170010", "score": 0.9, "verify": 0.9, "frames": 2, "confidence": 0.95},
            {"id": "2040170010", "score": 0, "verify": 0, "frames": 0, "confidence": None}])
        self.assertEqual(out["merged"], {"p": ["1040170010"], "s": ["2050170010"]})
        self.assertTrue(out["autoNotes"]["auto"])
        # 2026-10-13: 自动编队 = 用推荐; it cannot start until the recommendation is adopted into the S/Guest keys
        self.assertIn("推荐编队未生成", out["autoNotes"]["errors"])
        self.assertEqual(out["autoNotes"]["mode"], "recommend")
        self.assertEqual((out["label"], out["empty"]), ("芹沢あさひ", "未登记"))
        self.assertEqual(out["ok"], [True, False])
        self.assertTrue(out["visible"][0])  # W.I.N.G. is a normal visible task (MissionsClaim visibility belongs to the daily-loop work)


if __name__ == "__main__":
    unittest.main()
