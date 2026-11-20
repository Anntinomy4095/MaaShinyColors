"""Produce end summary / recommendation / notifications / navigation gate (agent/core/produce_end.py) and the UI
helpers in ui/components/produce-end.js. Offline; no game, no host, no input."""
from __future__ import annotations

import itertools
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from agent.core import produce_end as pe

ROOT = Path(__file__).resolve().parents[1]


def full_flow(**reward):
    obs = pe.demo_observations()
    obs.insert(2, ("FesBirthConfirm", {"sp_lost": 248}))
    obs.insert(5, ("Reward", reward or {"ex_skills": [{"name": "A", "tier": "虹", "target": "produce"},
                                                      {"name": "B", "tier": "金"}, {"name": "C", "tier": "silver"}],
                                         "knowhow_books": [{"level": 4}]}))
    return obs


class ParserTests(unittest.TestCase):
    def test_rank(self):
        self.assertEqual(pe.parse_rank("総合評価 B+ RANK"), "B+")
        self.assertEqual(pe.parse_rank("Ｂ＋"), "B+")
        self.assertEqual(pe.parse_rank("SS"), "SS")
        for bad in ("", None, "G", "B++", "RANK", "8+"):
            self.assertIsNone(pe.parse_rank(bad), bad)

    def test_ex_tier(self):
        cases = {"虹": "rainbow", "金": "gold", "銀": "silver", "銅": "bronze", "gold": "gold", "rainbow": "rainbow",
                 "レインボー": "rainbow", "": None, None: None, "紫": None, "UR": None}
        for raw, tier in cases.items():
            self.assertEqual(pe.parse_ex_tier(raw), tier, raw)

    def test_numbers(self):
        self.assertEqual(pe.parse_int("529,253"), 529253)
        self.assertEqual(pe.parse_int("+10,000人"), 10000)
        self.assertIsNone(pe.parse_int("abc"))
        self.assertIsNone(pe.parse_int(True))
        self.assertEqual(pe.parse_pair("6/35"), (6, 35))
        self.assertIsNone(pe.parse_pair("6/"))

    def test_ex_skill_name_never_sets_tier(self):
        # A bare name (even one containing 金) has an unknown tier: the tier is its own reading.
        self.assertIsNone(pe.ExSkill.from_reading("金色のステージ").tier)


class SummaryTests(unittest.TestCase):
    def test_demo_flow_summary(self):
        s = pe.build_summary(pe.demo_observations())
        self.assertEqual(s.rank, "B+")
        self.assertEqual(s.stats, {"vo": 1492, "da": 400, "vi": 339, "me": 656})
        self.assertEqual(s.final_fans, 529253)
        self.assertEqual(s.sp_lost, 248)          # FesBirthConfirm not captured: SP shown at birth is lost
        self.assertTrue(s.true_end)
        self.assertEqual(s.communication_level, (6, 35))
        self.assertIn("ex_skills", s.unknowns)
        self.assertIn("knowhow_books", s.unknowns)
        self.assertIsNone(s.best_ex_tier)

    def test_unknowns_stay_unknown(self):
        s = pe.build_summary([("TotalEval", {"rank": "???"}), ("Reward", {"ex_skills": [{"name": "X"}]})])
        self.assertIsNone(s.rank)
        self.assertEqual(s.stats, {})
        self.assertIsNone(s.true_end)               # flow not finished: neither seen nor ruled out
        for key in ("rank", "final_fans", "stats", "sp_lost", "knowhow_books", "trust_level", "ex_tier"):
            self.assertIn(key, s.unknowns)
        # 2026-10-10: a missing TE commu is not an unread field (the game skips it for a P card that already reached
        # True End); it is an info notification instead.
        self.assertNotIn("true_end", s.unknowns)
        note = next(n for n in pe.notifications(s) if n["key"] == "true_end_unseen")
        self.assertEqual(note["tone"], "info")
        self.assertEqual(s.ex_skills[0].tier, None)

    def test_finished_without_te_is_false(self):
        s = pe.build_summary([("TotalEval", {"rank": "C"}), ("MissionPopup", {"mission_text": "ログインしよう"})])
        self.assertIs(s.true_end, False)

    def test_garbage_readings_do_not_raise(self):
        s = pe.build_summary([("FesBirth", {"stats": "x", "live_skills": [1, None, " ok "]}),
                              ("Reward", {"ex_skills": [None, 3, "名前"], "knowhow_books": [{"level": 99}, "本"]}),
                              ("Unknown", {"rank": "S"}), ("ClearReward", {"communication_level": [1]})])
        self.assertEqual(s.live_skills, ("ok",))
        self.assertEqual([e.name for e in s.ex_skills], ["名前"])
        self.assertEqual([b.level for b in s.knowhow_books], [None, None])
        self.assertIsNone(s.communication_level)
        self.assertNotIn("Unknown", s.screens_seen)

    def test_conflicting_reads_are_reported(self):
        s = pe.build_summary([("TotalEval", {"rank": "B"}), ("TotalEval", {"rank": "A"})])
        self.assertEqual(s.rank, "B")
        self.assertIn("conflict:rank", s.unknowns)

    def test_full_flow_payload_round_trips_json(self):
        c = pe.EndCollector(wing_won=True)
        for screen, r in full_flow():
            pe.observe_end_screen(c, screen, r)
        payload = pe.summary_payload(c, stop_reason="produce_end_review")
        doc = json.loads(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(doc["summary"]["best_ex_tier"], "rainbow")
        self.assertEqual([e["tier"] for e in doc["summary"]["ex_skills"]], ["rainbow", "gold", "silver"])
        self.assertEqual(doc["summary"]["knowhow_books"], [{"name": None, "level": 4, "count": 1}])
        self.assertTrue(pe.payload_is_safe(doc))


class RecommendationTests(unittest.TestCase):
    def summary(self, rank=None, tier=None, te=None):
        ex = (pe.ExSkill("x", tier),) if tier else ()
        return pe.ProduceEndSummary(rank=rank, ex_skills=ex, true_end=te)

    def test_default_rules_keep(self):
        self.assertEqual(pe.recommend(self.summary("D"))["suggestion"], "keep")
        self.assertEqual(pe.recommend(self.summary("D"))["reasons"], ["no_release_rule"])
        self.assertIn("ex_tier_rainbow", pe.recommend(self.summary("D", "rainbow"))["reasons"])
        self.assertIn("ex_tier_gold", pe.recommend(self.summary(None, "gold"))["reasons"])
        self.assertIn("rank_B+", pe.recommend(self.summary("B+"))["reasons"])
        self.assertIn("true_end", pe.recommend(self.summary("F", te=True))["reasons"])

    def test_release_rule_is_opt_in_and_overridden_by_keep_rules(self):
        rules = {"release_below_rank": "C", "keep_if_true_end": False}
        self.assertEqual(pe.recommend(self.summary("D"), rules)["suggestion"], "release_candidate")
        self.assertEqual(pe.recommend(self.summary("C"), rules)["suggestion"], "keep")
        self.assertEqual(pe.recommend(self.summary(None), rules)["suggestion"], "undecided")
        self.assertEqual(pe.recommend(self.summary("D", "rainbow"), rules)["suggestion"], "keep")
        self.assertEqual(pe.recommend(self.summary("D", "silver"), rules)["suggestion"], "release_candidate")
        self.assertEqual(pe.recommend(self.summary("D", te=True), {"release_below_rank": "C"})["suggestion"], "keep")
        no_tier_rule = {"release_below_rank": "C", "keep_if_ex_tier_at_least": None, "keep_if_true_end": False}
        self.assertEqual(pe.recommend(self.summary("D", "rainbow"), no_tier_rule)["suggestion"], "release_candidate")

    def test_rules_validation(self):
        for bad in ({"nope": 1}, {"keep_if_ex_tier_at_least": "platinum"}, {"release_below_rank": "Z"},
                    {"keep_if_true_end": "yes"}):
            with self.assertRaises(ValueError):
                pe.EndRules.from_mapping(bad)

    def test_never_auto_delete(self):
        ranks = (None,) + pe.RANKS
        tiers = (None,) + pe.EX_TIERS
        rule_sets = [None, {"release_below_rank": "S"}, {"release_below_rank": "SS+", "keep_if_ex_tier_at_least": None,
                                                         "keep_if_rank_at_least": None, "keep_if_true_end": False}]
        for rank, tier, te, rules in itertools.product(ranks, tiers, (None, True, False), rule_sets):
            rec = pe.recommend(self.summary(rank, tier, te), rules)
            self.assertEqual(rec["default_choice"], "keep")
            self.assertIs(rec["requires_user_confirmation"], True)
            self.assertIsNone(rec["auto_action"])
            self.assertIsNone(rec["knowhow_book"]["auto_action"])
            self.assertNotIn("release", rec["choices"])
            self.assertNotIn("delete", rec["choices"])

    def test_payload_is_safe_rejects_tampering(self):
        c = pe.EndCollector()
        payload = pe.summary_payload(c)
        self.assertTrue(pe.payload_is_safe(payload))
        for mutate in (lambda p: p.update(auto_delete=True),
                       lambda p: p["recommendation"].update(auto_action="release"),
                       lambda p: p["recommendation"].update(default_choice="later_in_game"),
                       lambda p: p["recommendation"]["knowhow_book"].update(auto_action="sell")):
            bad = json.loads(json.dumps(payload))
            mutate(bad)
            self.assertFalse(pe.payload_is_safe(bad))


class NotificationTests(unittest.TestCase):
    def notes(self, prefs=None, **reward):
        s = pe.build_summary(full_flow(**reward))
        return pe.notifications(s, prefs)

    def test_triggers(self):
        keys = [n["key"] for n in self.notes()]
        for key in ("ex_rainbow", "ex_gold", "knowhow_book", "true_end", "sp_lost"):
            self.assertIn(key, keys)
        rainbow = next(n for n in self.notes() if n["key"] == "ex_rainbow")
        self.assertEqual(rainbow["message"], "获得虹色 EX 技能：A")
        self.assertNotIn("ex_tier_unread", keys)

    def test_prefs_turn_off_ex_reminders_only(self):
        prefs = {"notify": {"produce_end_ex_rainbow": False, "produce_end_ex_gold": False}}
        keys = [n["key"] for n in self.notes(prefs, ex_skills=[{"name": "A", "tier": "rainbow"}, {"name": "B"}])]
        self.assertNotIn("ex_rainbow", keys)
        self.assertIn("ex_tier_unread", keys)       # safety-ish notes are not switchable
        self.assertIn("unknowns", keys)              # knowhow_books unread in this flow

    def test_silver_and_bronze_do_not_remind(self):
        keys = [n["key"] for n in self.notes(ex_skills=[{"tier": "銀"}, {"tier": "銅"}], knowhow_books=[])]
        self.assertFalse({"ex_rainbow", "ex_gold"} & set(keys))

    def test_release_candidate_note(self):
        s = pe.ProduceEndSummary(rank="D", true_end=False)
        rec = pe.recommend(s, {"release_below_rank": "C"})
        msgs = [n["message"] for n in pe.notifications(s, None, rec)]
        self.assertTrue(any("Maa 不会执行" in m and "默认保留" in m for m in msgs))

    def test_notify_keys_fit_host_sanitizer(self):
        # UiProfileStore: notify keys ^[a-z][a-z0-9_]{0,31}$, at most 16 in total (settings already uses 6).
        for key in pe.NOTIFY_PREFS.values():
            self.assertRegex(key, r"^[a-z][a-z0-9_]{0,31}$")
        self.assertLessEqual(6 + len(pe.NOTIFY_PREFS), 16)


class ThirdDemoTests(unittest.TestCase):
    """Third demo: 諦める -> 評価, the 獲得報酬 page notice, a level-up animation and a scrolled Ex list."""

    def test_evaluation_is_not_a_win(self):
        c = pe.EndCollector()
        c.observe("Evaluation", {"final_fans": 408})
        self.assertIsNone(c.summary().wing_won)          # 社長からの評価 also follows an abandoned Produce

    def test_reward_page_notice_is_not_a_dialog(self):
        texts = ["獲得報酬", "スタミナが無い場合や、保議・売却を行う場合", "再抽選できません", "保護", "売却",
                 "ノウハウブックを選択してください", "再抽選1/1", "OK"]
        self.assertEqual(pe.end_screen_gate("Reward", texts)["action"], "handover")
        dialog = texts + ["以下のノウハウブックを売却します", "キャンセル"]
        self.assertEqual(pe.end_screen_gate("Reward", dialog)["action"], "hard_stop")

    def test_animated_and_scrolled_fields(self):
        c = pe.EndCollector()
        c.observe("ClearReward", {"trust_level": 9, "communication_level": "3/35"})
        c.observe("ClearReward", {"trust_level": 9, "communication_level": "4/35"})   # LEVEL UP animation
        c.observe("Reward", {"ex_skills": [{"name": "Visual+10", "idol": "x", "target": "support"},
                                           {"name": "メンタル上限+5", "idol": "y", "tier": "bronze"}]})
        c.observe("Reward", {"ex_skills": [{"name": "Visual+10", "idol": "x", "target": "support", "tier": "銅"},
                                           {"name": "Vocal初期値+10", "idol": "y", "tier": None}]})
        summary = c.summary()
        self.assertEqual(summary.communication_level, (4, 35))
        self.assertNotIn("conflict:communication_level", summary.unknowns)
        self.assertEqual([(e.name, e.tier) for e in summary.ex_skills],
                         [("Visual+10", "bronze"), ("メンタル上限+5", "bronze"), ("Vocal初期値+10", None)])
        self.assertIn("ex_tier", summary.unknowns)


class GateTests(unittest.TestCase):
    def test_wing_nav_end_flow_names_match(self):
        from agent.core import wing_nav
        self.assertTrue(set(pe.END_SCREENS) - {"TrueEndCommu", "MissionPopup"} <= set(wing_nav.END_FLOW))
        for screen in pe.END_SCREENS:
            self.assertIn(screen, wing_nav.RULES)
        self.assertEqual(set(pe.READING_FIELDS), set(pe.END_SCREENS))

    def test_non_destructive_screens_continue(self):
        for screen in ("Evaluation", "FesBirth", "FesBirthConfirm", "TotalEval", "ClearReward", "FesDetail"):
            self.assertEqual(pe.end_screen_gate(screen, ["OK", "次へ"], dialog=screen == "FesBirthConfirm")["action"],
                             "continue", screen)
        self.assertEqual(pe.end_screen_gate("MissionPopup", ["OK", "ミッションへ"])["action"], "finish")

    def test_reward_hands_over_by_default(self):
        texts = [("獲得報酬", (40, 10, 140, 50)), ("保護", (723, 112, 122, 58)), ("売却", (851, 112, 115, 58)),
                 ("再抽選", (909, 624, 166, 77)), ("OK", (1094, 624, 167, 77))]
        self.assertEqual(pe.end_screen_gate("Reward", texts), {"action": "handover", "reason": "produce_end_review"})
        self.assertEqual(pe.end_screen_gate("Reward", texts, config=pe.EndGateConfig(pause_at_reward=False))["action"],
                         "continue")

    def test_destructive_dialogs_hard_stop(self):
        for screen, text in (("Reward", "このノウハウブックを売却しますか？"), ("Reward", "再抽選しますか？体力30"),
                             ("FesDetail", "フェスアイドルを移籍しますか"), ("ClearReward", "所持上限に達しました"),
                             ("FesBirthConfirm", "ジュエルを消費して枠拡張"), ("FesDetail", "削除")):
            gate = pe.end_screen_gate(screen, [text], dialog=True)
            self.assertEqual(gate["action"], "hard_stop", text)
            self.assertEqual(gate["reason"], "destructive_dialog")
        # Even without a detected modal, those words outside the Reward page buttons stop the flow.
        self.assertEqual(pe.end_screen_gate("FesDetail", ["売却"])["action"], "hard_stop")
        self.assertEqual(pe.end_screen_gate("Reward", ["売却しますか"])["action"], "hard_stop")

    def test_unknown_dialog_and_screen_stop(self):
        self.assertEqual(pe.end_screen_gate("ClearReward", ["OK"], dialog=True)["reason"], "unknown_end_dialog")
        self.assertEqual(pe.end_screen_gate("Hub", [])["reason"], "not_an_end_screen")
        self.assertEqual(pe.end_screen_gate(None, [])["action"], "hard_stop")

    def test_sp_loss_threshold_is_opt_in(self):
        cfg = pe.EndGateConfig(max_sp_lost=0)
        self.assertEqual(pe.end_screen_gate("FesBirthConfirm", [], dialog=True, readings={"sp_lost": 248},
                                            config=cfg)["reason"], "sp_would_be_lost")
        self.assertEqual(pe.end_screen_gate("FesBirthConfirm", [], dialog=True, config=cfg)["reason"], "sp_lost_unread")
        self.assertEqual(pe.end_screen_gate("FesBirthConfirm", [], dialog=True, readings={"sp_lost": 0},
                                            config=cfg)["action"], "continue")

    def test_module_has_no_input_path(self):
        source = (ROOT / "agent/core/produce_end.py").read_text(encoding="utf-8")
        for forbidden in ("post_click", "controller", "SendInput", "context.run", "import maa", "click("):
            self.assertNotIn(forbidden, source)
        nav = (ROOT / "agent/core/wing_nav.py").read_text(encoding="utf-8")
        self.assertNotRegex(nav, r"(売却|移籍|削除)_?(ok|confirm)")


class UiStaticTests(unittest.TestCase):
    def test_dialog_has_no_destructive_button(self):
        js = (ROOT / "ui/components/produce-end.js").read_text(encoding="utf-8")
        self.assertNotIn("bridge.request", js)
        self.assertNotIn("postMessage", js)
        labels = re.findall(r'h\("button"[^)]*\)[^,]*,\s*([^)]+)\)', js)
        self.assertTrue(labels)
        for label in labels:
            for word in ("删除", "移籍", "売却", "解放", "delete", "release"):
                self.assertNotIn(word, label)
        self.assertIn('keep: "保留"', js)
        self.assertIn('later_in_game: "稍后在游戏内处理"', js)

    def test_protocol_unchanged_notify_is_free_form(self):
        proto = json.loads((ROOT / "ui/protocol.json").read_text(encoding="utf-8"))
        notify = proto["definitions"]["profile"]["properties"]["prefs"]["properties"]["notify"]
        self.assertEqual(notify["additionalProperties"], {"type": "boolean"})


@unittest.skipUnless(shutil.which("node"), "node not installed")
class UiHelperTests(unittest.TestCase):
    def test_clean_payload_and_notifications(self):
        c = pe.EndCollector(wing_won=True)
        for screen, r in full_flow(ex_skills=[{"name": "A", "tier": "rainbow"}, {"name": "B", "tier": "gold"}, {"name": "C"}],
                                     knowhow_books=[{"level": 4}]):
            c.observe(screen, r)
        payload = pe.summary_payload(c)
        tampered = json.loads(json.dumps(payload))
        tampered["auto_delete"] = True
        tampered["recommendation"].update(default_choice="release", auto_action="release", suggestion="delete_now")
        script = (
            "import './tests/fixtures/ui/fake_dom.mjs';"
            "import { cleanEndPayload, visibleNotifications, headline, summaryView, recordIntent } from './ui/components/produce-end.js';"
            "const [payload, tampered] = JSON.parse(process.argv[1]);"
            "const clean = cleanEndPayload(payload), bad = cleanEndPayload(tampered);"
            "const mem = {}; const store = { load: (k) => mem[k] || null, save: (k, v) => { mem[k] = v; } };"
            "const intent = recordIntent('run1', 'release_later', store); const cleared = recordIntent('run1', null, store);"
            "console.log(JSON.stringify({ clean, bad, head: headline(clean),"
            " off: visibleNotifications(clean, { produce_end_ex_rainbow: false }).map((n) => n.key),"
            " on: visibleNotifications(clean, {}).map((n) => n.key),"
            " view: summaryView(clean).textContent, none: cleanEndPayload({ version: 2, summary: {} }), intent, cleared }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps([payload, tampered], ensure_ascii=False)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        clean, bad = out["clean"], out["bad"]
        self.assertEqual(clean["rank"], "B+")
        self.assertEqual([e["tier"] for e in clean["ex"]], ["rainbow", "gold", None])
        for view in (clean, bad):
            self.assertEqual((view["defaultChoice"], view["autoAction"], view["requiresConfirmation"]), ("keep", None, True))
        self.assertEqual(bad["suggestion"], "keep")          # unknown suggestion falls back to keep
        self.assertEqual(out["head"], "总合评价 B+ · 虹 EX ×1 · 金 EX ×1 · True End")
        self.assertIn("ex_rainbow", out["on"])
        self.assertNotIn("ex_rainbow", out["off"])
        self.assertIn("ex_tier_unread", out["off"])
        for text in ("B+", "已看到", "虹", "金", "未读", "Lv.4"):
            self.assertIn(text, out["view"])
        self.assertIsNone(out["none"])
        self.assertEqual(out["intent"], {"intent": "release_later", "executed": False})
        self.assertIsNone(out["cleared"])


if __name__ == "__main__":
    unittest.main()
