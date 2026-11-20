"""自动编队 adapter: True End recommender first, wing_team fallback with reasons; exact-only card id map.

Uses the invented True End fixtures (tests/true_end_fixtures.py); no private data, game, input or network."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agent.core import card_id_map as cim  # noqa: E402
from agent.core import produce_config as pc  # noqa: E402
from agent.core import team_recommend as tr  # noqa: E402
from true_end_fixtures import card, growth_input  # noqa: E402

P_WIKI = "1040010010"
S_WIKI = [f"20500{i}0010" for i in range(2, 9)]   # seven S cards, idols 002..008
SMALL_SEARCH = {"candidate_per_role": 3, "beam_width": 12, "projection_budget": 300, "evaluate_top": 1,
                "screening_trials": 1, "confirmation_trials": 1}
SCENARIO = {"name": "synthetic-assumption", "player_judgments": {"Perfect": 1}, "npc_judgments": {"Good": 1},
            "npc_late_multiplier": 1.25, "reaction_mode": "uniform", "fourth_turn_multiplier": 2,
            "hand_recycle": "discard_shuffle", "ta_tie": "player_loses", "overkill_credit": False,
            "assumptions": ["Synthetic judgment/reaction assumptions for tests, not calibrated"]}


def te_id(wiki_id):
    return f"te-{wiki_id}"


def fixture_cards(count=7):
    """Raw CardData dicts keyed by True End id (asdict of the fixture cards)."""
    out = {te_id(P_WIKI): asdict(card(te_id(P_WIKI), kind="P", idol="idol-001"))}
    for i, wiki in enumerate(S_WIKI[:count]):
        attr = ("Vo", "Vo", "Vo", "Da", "Vi", "Vo", "Da")[i]
        out[te_id(wiki)] = asdict(card(te_id(wiki), idol=f"idol-{wiki[3:6]}", attr=attr, mastery=10 + i))
    return out


def fixture_map(wiki_ids=(P_WIKI, *S_WIKI)):
    return cim.CardIdMap({"version": 1, "mapped": {w: {"true_end_id": te_id(w), "kind": "P" if w[0] == "1" else "S",
                                                       "match": "exact_title_idol_kind"} for w in wiki_ids}})


def owned(details=True, s_count=7, skip=()):
    data = {"p": [P_WIKI], "s": S_WIKI[:s_count]}
    if details:
        data["details"] = {w: {"level": 80, "limit_break": 4} for w in data["p"] + data["s"] if w not in skip}
    return data


def settings(**extra):
    return {"growth": growth_input(), "search": SMALL_SEARCH, **extra}


def recommend(own, **kwargs):
    kwargs.setdefault("cards", fixture_cards())
    kwargs.setdefault("id_map", fixture_map())
    kwargs.setdefault("settings", settings())
    return tr.recommend(own, "Vo", **kwargs)


class TrueEndEngineTests(unittest.TestCase):
    def test_true_end_proposal_maps_back_to_wiki_ids(self):
        result = recommend(owned())
        self.assertEqual((result["engine"], result["fallback_reason"], result["status"]), ("true_end", None, "needs_confirmation"))
        self.assertEqual(result["p_card"], P_WIKI)
        self.assertEqual(len(result["starters"]), 4)
        team = [result["p_card"], *result["starters"], result["guest"]]
        self.assertTrue(set(team[1:]) <= set(S_WIKI))
        self.assertEqual(pc.duplicate_idols(team), {})
        summary = result["summary"]
        self.assertEqual(summary["rating_label"], "ESTIMATED")
        self.assertIsNone(summary["true_end_probability"])
        self.assertIsInstance(summary["ProxyTERating"], float)
        self.assertEqual(summary["evaluation"], "NO_BATTLE")
        self.assertEqual(summary["battle"]["rows"], [])
        self.assertTrue(set(summary["growth"]["effective_stats"]) >= {"Vo", "Da", "Vi", "Me"})
        self.assertTrue(any("全部 8 张" in a for a in summary["assumptions"]))
        self.assertEqual(len(summary["data_snapshot_sha256"]), 64)
        self.assertEqual(set(result["coverage"].values()), {"usable"})
        choices = result["candidates"]
        self.assertTrue(1 <= len(choices) <= 3)
        self.assertEqual(choices[0]["starters"], result["starters"])
        self.assertEqual(choices[0]["guest"], result["guest"])
        self.assertEqual(len({c["id"] for c in choices}), len(choices))
        for choice in choices:
            self.assertEqual(choice["p_card"], P_WIKI)
            self.assertTrue(set(choice["starters"] + [choice["guest"]]) <= set(S_WIKI))
            self.assertEqual(choice["evaluation"], "NO_BATTLE")
            self.assertIn("unknowns", choice)

    def test_selected_p_card_is_fixed(self):
        # U05: the recommendation searches only the selected P card's teams
        self.assertEqual(recommend(owned(), p_card=P_WIKI)["p_card"], P_WIKI)
        other = recommend(owned(), p_card="1040990010", fallback=lambda cards, primary: {"status": "rejected"})
        self.assertEqual(other["fallback_reason"], "selected_p_unavailable")
        plan = pc.auto_team_plan({"mode": "auto", "primary_stat": "Vo", "p_card": "1040990010"}, owned(),
                                 recommender={"fallback": lambda cards, primary: {
                                     "status": "ok", "p_card": P_WIKI, "starters": S_WIKI[:4], "guest": S_WIKI[4]}})
        self.assertEqual((plan["reason"], plan["proposal"]), ("auto_team_rejected", None))

    def test_selected_p_outside_candidate_cut_and_unknown_te(self):
        # 2026-10-13 live: the selected P was dropped by the newest-six cut, then by an unknown TE flag.
        newer = "1040990020"
        cards = fixture_cards()
        cards[te_id(newer)] = asdict(card(te_id(newer), kind="P", idol="idol-099"))
        cards[te_id(P_WIKI)]["te_eligible"] = None
        own = owned()
        own["p"] = [P_WIKI, newer]
        own["details"][newer] = {"level": 80, "limit_break": 4}
        with mock.patch.object(tr, "MAX_P_CANDIDATES", 1):
            result = recommend(own, cards=cards, id_map=fixture_map((P_WIKI, newer, *S_WIKI)), p_card=P_WIKI)
        self.assertEqual(result["p_card"], P_WIKI)
        self.assertTrue(any("True End 资格未知" in a for a in result["summary"]["assumptions"]))

    def test_pick_p_compares_owned_p_cards_with_the_s_card_attribute(self):
        # 推荐编队: Maa picks the P card; unknown TE counts as TE. 主属性 「自动」 (2026-10-31, research R23):
        # the owned S cards decide it for every P card, not each P card's specialty (fixture: four Vo S cards).
        other = "1040990020"
        cards = fixture_cards()
        cards[te_id(other)] = asdict(card(te_id(other), kind="P", idol="idol-099"))
        cards[te_id(other)]["te_eligible"] = None
        own = owned()
        own["p"] = [P_WIKI, other]
        own["details"][other] = {"level": 80, "limit_break": 4}
        spec = {P_WIKI: "Vo", other: "Da"}
        result = recommend(own, cards=cards, id_map=fixture_map((P_WIKI, other, *S_WIKI)), pick_p=True,
                           specialty=spec.get, p_card="1040123450")      # a fixed P is ignored in pick mode
        result_auto = tr.recommend(own, "auto", cards=cards, id_map=fixture_map((P_WIKI, other, *S_WIKI)),
                                   settings=settings(), pick_p=True, specialty=spec.get)
        for got in (result, result_auto):
            self.assertEqual(got["engine"], "true_end")
            self.assertIn(got["p_card"], (P_WIKI, other))
            cands = got["summary"]["p_candidates"]
            self.assertEqual({c["p_card"] for c in cands}, {P_WIKI, other})
            self.assertEqual(cands[0]["p_card"], got["p_card"])         # the best ProxyTERating wins
            self.assertTrue(got["summary"]["assumptions"][0].startswith("P 卡由 Maa 选"))
        self.assertEqual(result["primary"], "Vo")                          # fixed 主属性
        self.assertEqual({c["primary"] for c in result_auto["summary"]["p_candidates"]}, {"Vo"})
        self.assertEqual(result_auto["primary"], "Vo")
        self.assertIn("按持有 S 卡定为 Vo", result_auto["summary"]["assumptions"][0])
        # the same through auto_team_plan / the CLI shape (p_card "auto")
        plan = pc.auto_team_plan({"mode": "auto", "primary_stat": "auto", "p_card": "auto"}, own,
                                 recommender={"cards": cards, "id_map": fixture_map((P_WIKI, other, *S_WIKI)),
                                              "settings": settings(), "specialty": spec.get})
        self.assertEqual(plan["reason"], "team_needs_confirmation")
        self.assertEqual(plan["primary"], "Vo")
        self.assertEqual(plan["proposal"]["primary_stat"], plan["primary"])

    def test_s_cards_decide_the_auto_attribute(self):
        from agent.core.true_end.models import CardData, Ownership
        cards = {k: CardData.from_dict(v) for k, v in fixture_cards().items()}
        inventory = {k: Ownership(k, 4, 80) for k in cards}
        self.assertEqual(tr.support_attributes(cards, inventory)[0], "Vo")
        vi = {k: CardData.from_dict(asdict(card(k, idol=f"vi-{n}", attr="Vi", mastery=30))) for n, k in enumerate("abcde")}
        self.assertEqual(tr.support_attributes(vi, {k: Ownership(k, 4, 80) for k in vi}), ["Vi"])
        self.assertEqual(tr.support_attributes({}, {}), [])
        self.assertEqual(tr.strategy_stat({"strategy": {"strategy": "DA_SAFE"}}), "Da")
        self.assertIsNone(tr.strategy_stat({"strategy": {"strategy": "VO_DA_SAFE"}}))

    def test_pick_p_fallback_keeps_a_team_built_for_its_p_specialty(self):
        calls = []

        def scorer(cards, primary):
            calls.append(primary)
            return {"status": "ok", "p_card": P_WIKI, "starters": S_WIKI[:4], "guest": S_WIKI[4]}
        got = tr.recommend(owned(), "auto", cards={}, id_map=cim.CardIdMap(), settings=settings(), fallback=scorer,
                           pick_p=True, specialty={P_WIKI: "Da"}.get)
        self.assertEqual((got["engine"], got["primary"]), ("wing_team", "Da"))
        self.assertEqual(calls, ["Vo", "Da"])

    def test_battle_scenario_results_are_summarised(self):
        result = recommend(owned(), settings=settings(battle_scenarios=[SCENARIO]))
        self.assertEqual(result["engine"], "true_end")
        battle = result["summary"]["battle"]
        self.assertEqual(battle["status"], "CONDITIONAL_SIMULATION")
        self.assertEqual([r["audition"] for r in battle["rows"]], ["s3_odotte", "legend", "semifinal", "final"])
        for row in battle["rows"]:
            self.assertTrue(0 <= row["worst_lower_bound"] <= row["worst_probability"] + 1e-9 <= 1 + 1e-9)
        self.assertEqual(result["summary"]["evaluation"], "INDEPENDENT_CONFIRMATION")
        self.assertIsNone(result["summary"]["true_end_probability"])

    def test_training_hint_names_an_under_levelled_card_that_would_raise_the_team(self):
        # 2026-10-25 user: 练级提示. S_WIKI[5] (Vo, the strongest mastery) is owned at Lv40, where the library has no stat
        # row, so it is not searched; at its 4凸 cap (Lv80) it would replace a weaker card.
        own = owned()
        own["details"][S_WIKI[5]] = {"level": 40, "limit_break": 4}
        result = recommend(own, p_card=P_WIKI)
        self.assertEqual(result["engine"], "true_end")
        self.assertNotIn(S_WIKI[5], (*result["starters"], result["guest"]))
        hint = result["training_hints"][0]
        self.assertEqual((hint["card_id"], hint["level"], hint["target_level"], hint["limit_break"]), (S_WIKI[5], 40, 80, 4))
        self.assertIn(hint["replaces"], (*result["starters"], result["guest"]))
        self.assertGreater(hint["gain"], 0)
        self.assertEqual([h["card_id"] for h in result["training_hints"]], [S_WIKI[5]])   # Lv80 cards are never hints
        plan = pc.auto_team_plan({"mode": "auto", "primary_stat": "Vo", "p_card": P_WIKI}, own,
                                 recommender={"cards": fixture_cards(), "id_map": fixture_map(), "settings": settings()})
        self.assertEqual(plan["training_hints"], result["training_hints"])

    def test_unknown_level_cards_are_excluded_and_disclosed(self):
        result = recommend(owned(skip=(S_WIKI[6],)))
        self.assertEqual(result["engine"], "true_end")
        self.assertEqual(result["coverage"][S_WIKI[6]], "level_unknown")
        self.assertNotIn(S_WIKI[6], [*result["starters"], result["guest"]])
        self.assertTrue(any("1 张因未映射" in a for a in result["summary"]["assumptions"]))
        partial = recommend({**owned(), "details": {**owned()["details"], S_WIKI[5]: {"level": 80}}})
        self.assertEqual(partial["coverage"][S_WIKI[5]], "limit_break_unknown")

    def test_auto_team_plan_uses_the_adapter_and_stays_confirm_before_use(self):
        rec = {"cards": fixture_cards(), "id_map": fixture_map(), "settings": settings()}
        plan = pc.auto_team_plan({"mode": "auto", "primary_stat": "Vo"}, owned(), recommender=rec)
        self.assertTrue(plan["stop"])
        self.assertEqual((plan["reason"], plan["engine"], plan["fallback_reason"]), ("team_needs_confirmation", "true_end", None))
        self.assertEqual(plan["proposal"]["mode"], "manual")
        self.assertIn("id", plan["candidates"][0])
        self.assertEqual(plan["candidates"][0]["starters"], plan["proposal"]["s_cards"])
        pc.validate_team(plan["proposal"])
        self.assertIsNone(plan["recommender"]["true_end_probability"])
        self.assertEqual(plan["confidence"], None)


class FallbackTests(unittest.TestCase):
    def fake(self):
        seen = {}

        def scorer(cards, primary):
            seen["cards"], seen["primary"] = cards, primary
            return {"status": "rejected", "p_card": None, "starters": [], "guest": None, "confidence": 0.0,
                    "reasons": ["wing_rejected"]}
        return scorer, seen

    def assert_fallback(self, result, reason):
        self.assertEqual((result["engine"], result["fallback_reason"]), ("wing_team", reason))
        self.assertEqual(result["reasons"][0], f"fallback:{reason}")
        self.assertIsNone(result["summary"])

    def test_each_missing_data_reason(self):
        scorer, _ = self.fake()
        self.assert_fallback(recommend(owned(), cards={}, fallback=scorer), "card_db_empty")
        self.assert_fallback(recommend(owned(), id_map=cim.CardIdMap(), fallback=scorer), "id_map_missing")
        unmapped = recommend(owned(), id_map=fixture_map(S_WIKI), fallback=scorer)
        self.assert_fallback(unmapped, "p_card_unavailable")
        self.assertEqual(unmapped["coverage"][P_WIKI], "unmapped:not_in_map")
        self.assertEqual(unmapped["needs_parsing"][0]["id"], P_WIKI)
        no_db = fixture_cards()
        del no_db[te_id(P_WIKI)]
        missing = recommend(owned(), cards=no_db, fallback=scorer)
        self.assert_fallback(missing, "p_card_unavailable")
        self.assertEqual(missing["coverage"][P_WIKI], "not_parsed")
        self.assertEqual(missing["needs_parsing"][0]["candidate_title"], te_id(P_WIKI))
        self.assert_fallback(recommend(owned(skip=(P_WIKI,)), fallback=scorer), "p_card_unavailable")
        self.assert_fallback(recommend(owned(details=False), fallback=scorer), "p_card_unavailable")
        self.assert_fallback(recommend(owned(s_count=4), fallback=scorer), "too_few_s_cards")
        bad = recommend(owned(), settings={"search": {"difficulty": "Master"}}, fallback=scorer)
        self.assert_fallback(bad, "true_end_settings_invalid")

    def test_blocked_true_end_result_falls_back(self):
        scorer, _ = self.fake()
        cards = fixture_cards()
        cards[te_id(P_WIKI)]["te_eligible"] = None   # P card TE eligibility unknown -> recommender BLOCKED_DATA
        result = recommend(owned(), cards=cards, fallback=scorer)
        self.assert_fallback(result, "true_end_blocked")
        self.assertIn("te_eligible", result["true_end_rejected"][P_WIKI])
        self.assertEqual(result["coverage"][P_WIKI], "not_ready")

    def test_fallback_gets_user_levels_as_features(self):
        scorer, seen = self.fake()
        own = owned()
        own["details"][S_WIKI[0]] = {"level": 55}
        recommend(own, cards={}, fallback=scorer, features={S_WIKI[1]: {"level": 70, "uncap": 2}})
        by_id = {c["id"]: c for c in seen["cards"]}
        self.assertEqual((by_id[S_WIKI[0]]["level"], "uncap" in by_id[S_WIKI[0]]), (55, False))
        self.assertEqual((by_id[S_WIKI[1]]["level"], by_id[S_WIKI[1]]["uncap"]), (70, 2))   # explicit features win
        self.assertEqual((by_id[P_WIKI]["level"], by_id[P_WIKI]["uncap"]), (80, 4))
        self.assertEqual(seen["primary"], "Vo")

    def test_invalid_details_are_ignored_with_a_note(self):
        scorer, seen = self.fake()
        own = owned()
        own["details"][S_WIKI[0]] = {"level": 0}
        result = recommend(own, fallback=scorer)
        self.assert_fallback(result, "p_card_unavailable")
        self.assertTrue(any("Lv/凸 数据无效" in n for n in result["notes"]))
        self.assertTrue(all("level" not in c for c in seen["cards"]))

    def test_default_wing_team_fallback_still_rejects_ids_only(self):
        plan = pc.auto_team_plan({"mode": "auto"}, {"p": ["1040170010"], "s": ["2050170010"]},
                                 recommender={"cards": {}, "id_map": cim.CardIdMap(), "settings": {}})
        self.assertEqual((plan["reason"], plan["engine"], plan["fallback_reason"]), ("auto_team_rejected", "wing_team", "card_db_empty"))


class DetailsValidationTests(unittest.TestCase):
    def test_clean_details(self):
        base = {"p": [P_WIKI], "s": [S_WIKI[0]]}
        self.assertEqual(tr.clean_details({**base, "details": {P_WIKI: {"level": 1, "limit_break": 0}, S_WIKI[0]: {},
                                                               "2059990010": {"level": 5}}}),
                         {P_WIKI: {"level": 1, "limit_break": 0}})
        for bad in ({"level": 0}, {"level": 101}, {"level": True}, {"level": 5.0}, {"limit_break": 5},
                    {"limit_break": -1}, {"level": "80"}, {"level": 80, "skill": 1}, 3):
            with self.assertRaises(ValueError, msg=bad):
                tr.clean_details({**base, "details": {S_WIKI[0]: bad}})
        with self.assertRaises(ValueError):
            tr.clean_details({**base, "details": []})
        self.assertEqual(tr.clean_details(None), {})

    def test_protocol_declares_details_tightly(self):
        protocol = json.loads((ROOT / "ui" / "protocol.json").read_text(encoding="utf-8"))
        owned_spec = protocol["definitions"]["profile"]["properties"]["prefs"]["properties"]["owned_cards"]
        self.assertEqual(set(owned_spec["properties"]), {"p", "s", "details"})
        details = owned_spec["properties"]["details"]
        entry = details["additionalProperties"]
        self.assertEqual(details["type"], "object")
        self.assertIs(entry["additionalProperties"], False)
        self.assertEqual(entry["properties"]["level"], {"type": "integer", "minimum": 1, "maximum": tr.MAX_LEVEL})
        self.assertEqual(entry["properties"]["limit_break"], {"type": "integer", "minimum": 0, "maximum": 4})
        cs = (ROOT / "browser_host" / "Ui" / "UiProfileStore.cs").read_text(encoding="utf-8")
        self.assertIn(f"MaxCardLevel = {tr.MAX_LEVEL};", cs)
        self.assertIn('owned["details"] is JsonObject details', cs)
        selftest = (ROOT / "browser_host" / "Ui" / "UiEngineSelfTest.cs").read_text(encoding="utf-8")
        for name in ("owned_details_saved", "owned_details_refused:", "owned_details_sanitised_on_load", "owned_details_empty_dropped"):
            self.assertIn(name, selftest)

    def test_propose_tool_reads_details_and_reports_engine(self):
        from tools import auto_team_propose as tool
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            path.write_text(json.dumps({"schema_version": 1, "prefs": {"owned_cards": owned()}}), encoding="utf-8")
            loaded = tool.load_owned(path)
        self.assertEqual(loaded["details"][P_WIKI], {"level": 80, "limit_break": 4})
        result = tool.propose(loaded, {}, "Vo", recommender={"cards": fixture_cards(), "id_map": fixture_map(), "settings": settings()})
        self.assertEqual((result["engine"], result["reason"], result["fallback_reason"]), ("true_end", "team_needs_confirmation", None))
        self.assertEqual(result["coverage_counts"], {"usable": 8})
        self.assertIsNone(result["recommender"]["true_end_probability"])
        self.assertIn("id", result["candidates"][0])
        self.assertEqual(result["candidates"][0]["starters"], result["proposal"]["s_cards"])
        # the counts of the owned list the proposal was computed from (the UI head shows these, not the profile's)
        self.assertEqual(result["owned_counts"], {"p": 1, "s": 7, "details": 8})
        self.assertEqual(result["owned_source"], "profile")
        json.dumps(result, allow_nan=False)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ProposalHeadTests(unittest.TestCase):
    def test_head_counts_come_from_the_proposal_and_flag_a_different_source(self):
        data = {"version": 1, "status": "needs_confirmation", "engine": "true_end", "owned_counts": {"p": 131, "s": 146},
                "owned_source": "snapshot manifest snapshot-20261006-owned", "assume": {"recognised_levels": True}}
        script = (
            "import './tests/fixtures/ui/fake_dom.mjs';"
            "import { cleanProposal } from './ui/components/team-proposal.js';"
            "import { proposalHead } from './ui/components/team-field.js';"
            "const c = cleanProposal(JSON.parse(process.argv[1]));"
            "const empty = { p: [], s: [] }, same = { p: Array(131).fill('1'), s: Array(146).fill('2') };"
            "console.log(JSON.stringify({ counts: c.ownedCounts, assume: c.assume, differ: proposalHead(c, empty),"
            " same: proposalHead(c, same), old: proposalHead(cleanProposal({ version: 1 }), empty) }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(data, ensure_ascii=False)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["counts"], {"p": 131, "s": 146})
        self.assertEqual(out["assume"], {"recognised_levels": True})
        # 2026-10-07 role-play: the head said P 0 / S 0 (empty profile) beside a 277-card proposal
        self.assertIn("P 131 / S 146", out["differ"])
        self.assertIn("snapshot-20261006-owned", out["differ"])
        self.assertIn("与当前「持有卡牌」P 0 / S 0 不同", out["differ"])
        self.assertIn("「持有卡牌」中评分提出一队（P 131 / S 146）", out["same"])
        self.assertIn("P 0 / S 0", out["old"])   # an old file without counts falls back to the profile


def enza_cards(count=7, p_te=True):
    """Fixture cards keyed directly by their 10-digit ID (library form, no id map needed)."""
    out = {P_WIKI: {**asdict(card(P_WIKI, kind="P", idol="idol-001")), "te_eligible": True if p_te else None}}
    for i, wiki in enumerate(S_WIKI[:count]):
        out[wiki] = asdict(card(wiki, idol=f"idol-{wiki[3:6]}", attr=("Vo", "Da", "Vi")[i % 3], mastery=10 + i))
    return out


def write_snapshot(root, name, generated_at, *, converted=None, merged=None):
    folder = Path(root) / name
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"version": 1, "generated_at": generated_at, "entries": [
        {"enza_id": c["card_id"]} for c in (converted or [])]}), encoding="utf-8")
    if converted is not None:
        (folder / "converted-cards.json").write_text(json.dumps({"cards": converted}, default=list), encoding="utf-8")
    if merged is not None:
        (folder / "merged-candidates.json").write_text(json.dumps({"cards": merged}, default=list), encoding="utf-8")
    return folder


def no_team(cards, primary):
    return {"status": "rejected", "starters": []}


class LibraryTests(unittest.TestCase):
    """Whole local library (schedule item 1): every snapshot, every owned ID, per-role gaps; no fixed sample list."""

    def test_library_union_precedence_and_unconverted_snapshot(self):
        cards = enza_cards()
        old_s0 = {**cards[S_WIKI[0]], "name": "old-db"}
        merged_s0 = {**cards[S_WIKI[0]], "name": "merged"}
        new_s1 = {**cards[S_WIKI[1]], "name": "new-db"}
        old_s1 = {**cards[S_WIKI[1]], "name": "old-db"}
        legacy = {**cards[S_WIKI[2]], "card_id": "【legacy】idol", "name": "【legacy】idol"}
        with tempfile.TemporaryDirectory() as tmp:
            write_snapshot(tmp, "snapshot-20261006-owned", "2026-10-06T11:00:00+00:00",
                           converted=[old_s0, old_s1, cards[P_WIKI]], merged=[merged_s0])
            write_snapshot(tmp, "snapshot-20261007-all", "2026-10-07T05:00:00+00:00", converted=[new_s1])
            write_snapshot(tmp, "snapshot-20261008-all", "2026-10-08T01:00:00+00:00")   # fetch still running
            db = Path(tmp) / "legacy.json"
            db.write_text(json.dumps({"cards": [legacy]}, default=list), encoding="utf-8")
            id_map = cim.CardIdMap({"version": 1, "mapped": {S_WIKI[2]: {"true_end_id": "【legacy】idol", "kind": "S",
                                                                         "match": "exact_title_idol_kind"}}})
            lib = tr.load_library(Path(tmp), legacy=(db,), id_map=id_map)
        self.assertEqual([s["name"] for s in lib["snapshots"]],
                         ["snapshot-20261008-all", "snapshot-20261007-all", "snapshot-20261006-owned"])
        self.assertEqual(lib["cards"][S_WIKI[0]]["name"], "merged")           # merged DB+Wiki beats plain DB
        self.assertEqual(lib["cards"][S_WIKI[1]]["name"], "new-db")           # newest manifest wins
        self.assertEqual(lib["cards"][S_WIKI[2]]["card_id"], S_WIKI[2])       # legacy re-keyed to its 10-digit ID
        self.assertEqual(lib["sources"][S_WIKI[2]], "wiki_reviewed")
        self.assertEqual(lib["sources"][S_WIKI[1]], "db:snapshot-20261007-all")
        self.assertTrue(any("snapshot-20261008-all" in n and "converted-cards.json" in n for n in lib["notes"]))
        self.assertEqual(lib["counts"], {"S": 3, "P": 1})

    def test_owned_ids_found_in_library_without_id_map_and_gaps_listed(self):
        cards = enza_cards()
        cards[S_WIKI[6]] = {**cards[S_WIKI[6]], "owned_live_skills": []}
        own = {**owned(), "s": [*S_WIKI, "2059990010"]}
        result = tr.recommend(own, "Vo", cards=cards, id_map=cim.CardIdMap(), settings=settings())
        self.assertEqual(result["engine"], "true_end")
        self.assertEqual(result["coverage"]["2059990010"], "not_parsed")
        rows = {r["id"]: r for r in result["card_gaps"]}
        self.assertEqual(len(rows), 9)
        self.assertEqual(rows["2059990010"]["source"], "missing")
        self.assertEqual(rows[S_WIKI[0]]["ownership"], "OWNED")
        self.assertEqual(rows[S_WIKI[0]]["ready"], {"active": True, "guest": True})
        gap = rows[S_WIKI[6]]
        self.assertIn("owned_live_skills", gap["unknown"]["active"])
        self.assertNotIn("owned_live_skills", gap["unknown"]["guest"])
        self.assertEqual(gap["unresolved"], {"active": [], "guest": []})
        self.assertEqual(rows[P_WIKI]["ready"], {"p": True})
        self.assertEqual(result["gap_counts"]["missing"], 1)
        json.dumps(result, allow_nan=False)

    def test_unresolved_conflict_is_listed_separately_from_unknown(self):
        cards = enza_cards()
        conflict = {"field": "levels.80.Vo", "db": 260, "wiki": 250, "critical": True, "status": "unresolved"}
        cards[S_WIKI[5]] = {**cards[S_WIKI[5]], "merge": {"unresolved": [conflict]},
                            "levels": {"80": {"Vo": None, "Da": 240, "Vi": 220, "Me": 250}}}
        result = tr.recommend(owned(), "Vo", cards=cards, id_map=cim.CardIdMap(), settings=settings(), fallback=no_team)
        row = next(r for r in result["card_gaps"] if r["id"] == S_WIKI[5])
        self.assertEqual(row["unresolved"]["active"], ["levels.80 (unresolved)"])
        self.assertEqual(row["unknown"]["active"], [])
        self.assertEqual(result["gap_counts"]["with_unresolved"], 1)

    def test_unknown_levels_and_p_te_need_explicit_assumptions(self):
        cards = enza_cards(p_te=False)
        ids = {"p": [P_WIKI], "s": list(S_WIKI)}
        plain = tr.recommend(ids, "Vo", cards=cards, id_map=cim.CardIdMap(), settings=settings(), fallback=no_team)
        self.assertEqual((plain["engine"], plain["fallback_reason"]), ("wing_team", "p_card_unavailable"))
        self.assertEqual(set(plain["coverage"].values()), {"level_unknown"})
        lb4 = tr.recommend(ids, "Vo", cards=cards, id_map=cim.CardIdMap(), settings=settings(), assume={"lb4_cap": True},
                           fallback=no_team)
        self.assertEqual(lb4["fallback_reason"], "true_end_blocked")     # P TE eligibility still unknown
        self.assertIn("te_eligible", lb4["true_end_rejected"][P_WIKI])
        both = tr.recommend(ids, "Vo", cards=cards, id_map=cim.CardIdMap(), settings=settings(),
                            assume={"lb4_cap": True, "p_te_eligible": True})
        self.assertEqual((both["engine"], both["p_card"]), ("true_end", P_WIKI))
        self.assertEqual(set(both["coverage"].values()), {"usable_assumed"})
        self.assertEqual(both["assume"], {"lb4_cap": True, "p_te_eligible": True})
        self.assertEqual(both["summary"]["assumed"], {"lb4_cap": 8, "p_te_eligible": 1})
        self.assertTrue(any("保留已填写值" in a for a in both["summary"]["assumptions"]))
        rows = {r["id"]: r for r in both["card_gaps"]}
        self.assertEqual(rows[P_WIKI]["assumed"], ["lb4_cap", "p_te_eligible"])
        self.assertEqual(rows[P_WIKI]["unknown"]["p"], ["te_eligible"])   # the data gap itself stays visible
        self.assertEqual(rows[S_WIKI[0]]["ownership"], "ASSUMED_LB4_CAP")
        self.assertEqual((rows[S_WIKI[0]]["level"], rows[S_WIKI[0]]["limit_break"]), (80, 4))
        self.assertIsNone(cards[P_WIKI]["te_eligible"])                     # the library itself is not modified

    def test_recognised_levels_need_the_switch_and_derive_the_owned_row(self):
        from unittest import mock
        cards = enza_cards()
        cards[S_WIKI[0]] = {**cards[S_WIKI[0]], "levels": {"1": {"Vo": 104, "Da": 96, "Vi": 88, "Me": 100},
                                                           "80": {"Vo": 260, "Da": 240, "Vi": 220, "Me": 250}}}
        doc = {"details": {P_WIKI: {"limit_break": 4}, S_WIKI[0]: {"limit_break": 4, "level": 79},
                           **{w: {"limit_break": 4, "level": 80} for w in S_WIKI[1:]}, "2059990010": {"limit_break": 1}}}
        ids = {"p": [P_WIKI], "s": list(S_WIKI)}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "owned-levels.js"
            path.write_text("export default " + json.dumps(doc) + ";", encoding="utf-8")
            with mock.patch.object(tr, "RECOGNISED_LEVELS", path):
                plain = tr.recommend(ids, "Vo", cards=cards, id_map=cim.CardIdMap(), settings=settings(), fallback=no_team)
                self.assertEqual(set(plain["coverage"].values()), {"level_unknown"})   # off by default
                own, _ = tr.with_recognised_levels({**ids, "details": {S_WIKI[1]: {"level": 70, "limit_break": 2}}})
                self.assertEqual(own["details"][S_WIKI[1]], {"level": 70, "limit_break": 2})   # the user's value wins
                self.assertIn("2059990010", own["s"])
                self.assertNotIn(S_WIKI[1], own["recognised"])
                both = tr.recommend(ids, "Vo", cards=cards, id_map=cim.CardIdMap(), settings=settings(),
                                    assume={"recognised_levels": True, "lb4_cap": True})
        self.assertEqual(both["engine"], "true_end")
        rows = {r["id"]: r for r in both["card_gaps"]}
        self.assertEqual(rows[S_WIKI[0]]["ownership"], "OWNED")
        self.assertIn("recognised_levels", rows[S_WIKI[0]]["assumed"])
        self.assertIn("derived_level:derived", rows[S_WIKI[0]]["assumed"])
        self.assertEqual(rows[P_WIKI]["ownership"], "OWNED")    # a P card has stars only: a known LB is enough
        self.assertEqual(rows[P_WIKI]["assumed"], ["recognised_levels"])
        self.assertEqual(rows[S_WIKI[0]]["ready"], {"active": True, "guest": True})
        self.assertEqual(both["summary"]["assumed"]["derived_levels"], 1)
        self.assertTrue(any("RECOGNISED_NOT_USER_CONFIRMED" in a for a in both["summary"]["assumptions"]))
        self.assertNotIn("79", cards[S_WIKI[0]]["levels"])   # the library itself is not modified

    def test_owned_from_snapshot_and_cli_flags(self):
        from tools import auto_team_propose as tool
        cards = enza_cards()
        with tempfile.TemporaryDirectory() as tmp:
            folder = write_snapshot(tmp, "snapshot-x-owned", "2026-10-07T00:00:00+00:00", converted=list(cards.values()))
            ids = tr.owned_from_snapshot(folder)
        self.assertEqual(ids, {"p": [P_WIKI], "s": sorted(S_WIKI)})
        result = tool.propose({**ids, "source": "snapshot manifest x"}, {}, "Vo", recommender={
            "cards": cards, "id_map": cim.CardIdMap(), "settings": settings(), "assume": {"lb4_cap": True}})
        self.assertEqual((result["engine"], result["reason"]), ("true_end", "team_needs_confirmation"))
        self.assertEqual(result["coverage_counts"], {"usable_assumed": 8})
        self.assertEqual(result["assume"], {"lb4_cap": True})
        self.assertEqual(len(result["card_gaps"]), 8)
        self.assertEqual(result["notes"][0], "持有卡来源：snapshot manifest x")
        source = (ROOT / "tools" / "auto_team_propose.py").read_text(encoding="utf-8")
        for flag in ("--owned-snapshot", "--assume-lb4-cap", "--assume-p-te"):
            self.assertIn(flag, source)


class CardIdMapTests(unittest.TestCase):
    def lists(self):
        support = [
            {"card_id": "2040010010", "title": "走るっ！！！！", "idol": "大崎甘奈"},
            {"card_id": "2040010020", "title": "同じ名前", "idol": "黛冬優子"},
            {"card_id": "2040010030", "title": "同じ名前", "idol": "黛冬優子"},
            {"card_id": "2040010040", "title": "ハーフ!", "idol": "小宮果穂"},
            {"card_id": "2040010050", "title": "甜der Dream Show", "idol": "大崎 甜花"},
            {"card_id": "2040010060", "title": "種別", "idol": "三峰結華"},
            {"card_id": "1040010070", "title": "P卡", "idol": "浅倉透"},
            {"card_id": "bad", "title": "x", "idol": "y"},
        ]
        return [("S", support[:6] + support[7:]), ("P", [support[6]])]

    def parsed(self):
        return [
            {"card_id": "【走るっ！！！！】大崎甘奈", "kind": "S", "idol": "大崎甘奈"},
            {"card_id": "【同じ名前】黛冬優子", "kind": "S", "idol": "黛冬優子"},
            {"card_id": "【ハーフ！】小宮果穂", "kind": "S", "idol": "小宮果穂"},     # full-width ！ vs list "!"
            {"card_id": "【甜der Dream Show】大崎 甜花", "kind": "S", "idol": "大崎甜花"},
            {"card_id": "【種別】三峰結華", "kind": "P", "idol": "三峰結華"},
            {"card_id": "【P卡】浅倉透", "kind": "P", "idol": "浅倉透"},
        ]

    def test_exact_only(self):
        doc = cim.build_map(self.lists(), self.parsed())
        self.assertEqual(set(doc["mapped"]), {"2040010010", "1040010070"})
        self.assertEqual(doc["mapped"]["2040010010"]["true_end_id"], "【走るっ！！！！】大崎甘奈")
        reasons = {k: v["reason"] for k, v in doc["unmapped"].items()}
        self.assertEqual(reasons, {"2040010020": "ambiguous_list_title", "2040010030": "ambiguous_list_title",
                                   "2040010040": "not_parsed", "2040010050": "idol_mismatch", "2040010060": "kind_mismatch"})
        self.assertEqual(doc["unmapped"]["2040010040"]["candidate_title"], "【ハーフ!】小宮果穂")
        mapping = cim.CardIdMap(doc)
        self.assertEqual(mapping.true_end_id("2040010010"), "【走るっ！！！！】大崎甘奈")
        self.assertIsNone(mapping.true_end_id("2040010040"))
        self.assertEqual(mapping.wiki_id("【P卡】浅倉透"), "1040010070")
        self.assertEqual(mapping.unmapped_reason("2040010020"), "ambiguous_list_title")
        self.assertEqual(mapping.unmapped_reason("2999999999"), "not_in_map")

    def test_ambiguous_parsed_cards_stay_unmapped(self):
        parsed = self.parsed() + [{"card_id": "【走るっ！！！！】大崎甘奈", "kind": "S", "idol": "別人"}]
        doc = cim.build_map(self.lists(), parsed)
        self.assertEqual(doc["unmapped"]["2040010010"]["reason"], "ambiguous_parsed_card")

    def test_loader_refuses_non_exact_entries(self):
        good = {"true_end_id": "x", "kind": "S", "match": "exact_title_idol_kind"}
        for bad in ({"2040010010": {**good, "match": "fuzzy"}}, {"2040010010": {**good, "kind": "P"}},
                    {"2040010010": good, "2040010020": good}, {"abc": good}):
            with self.assertRaises(ValueError):
                cim.CardIdMap({"version": 1, "mapped": bad})
        with self.assertRaises(ValueError):
            cim.CardIdMap({"version": 2})
        self.assertEqual(len(cim.CardIdMap.load(Path(tempfile.gettempdir()) / "missing-card-id-map.json")), 0)

    def test_build_tool_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "support.json").write_text(json.dumps({"kind": "support", "cards": self.lists()[0][1]}, ensure_ascii=False), encoding="utf-8")
            (tmp / "db.json").write_text(json.dumps({"cards": self.parsed()}, ensure_ascii=False), encoding="utf-8")
            out = tmp / "map.json"
            cmd = [sys.executable, "tools/card_id_map_build.py", "--list", str(tmp / "support.json"), "--database",
                   str(tmp / "db.json"), "--output", str(out)]
            env = {"PYTHONIOENCODING": "utf-8"}
            run = lambda extra=(): subprocess.run(cmd + list(extra), cwd=ROOT, capture_output=True, text=True,
                                                   encoding="utf-8", env={**__import__("os").environ, **env}, timeout=60)
            self.assertEqual(run().returncode, 0)
            self.assertEqual(run(["--check"]).returncode, 0)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(set(doc["mapped"]), {"2040010010"})
            self.assertEqual([s["kind"] for s in doc["sources"]], ["wiki_list", "true_end_database"])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ProposalUiTests(unittest.TestCase):
    def test_proposal_engine_display_and_details_helpers(self):
        summary = {"ProxyTERating": 61.234, "rating_label": "ESTIMATED", "true_end_probability": 0.9, "strategy": "VO_SAFE",
                   "evaluation": "INDEPENDENT_CONFIRMATION",
                   "growth": {"effective_stats": {"Vo": 812.4, "Da": 300, "Vi": 290, "Me": 260}, "sp": 410, "route_feasible": True},
                   "battle": {"status": "CONDITIONAL_SIMULATION", "policy": "VO_SAFE",
                              "rows": [{"audition": "final", "worst_probability": .875, "worst_lower_bound": .5}, {"audition": "x"}]},
                   "assumptions": ["仅在 5 张可计算的持有卡中搜索", 7]}
        data = {"version": 1, "status": "needs_confirmation", "reason": "team_needs_confirmation", "engine": "true_end",
                "fallback_reason": "ignored", "recommender": summary, "coverage_counts": {"usable": 6, "not_parsed": 2, "Bad": 1},
                "needs_parsing": [{"id": "2050990010", "candidate_title": "【x】y", "reason": "not_parsed"}, {"id": "evil"}],
                "proposal": {"p_card": "1040010010", "s_cards": ["2050020010", "2050030010", "2050040010", "2050050010"], "guest": "2050060010"},
                "training_hints": [
                    {"card_id": "2050070010", "level": 40, "target_level": 80, "limit_break": 4, "slot": "S3",
                     "replaces": "2050040010", "gain": 1.25},
                    {"card_id": "2050070010", "level": 40, "target_level": 80, "limit_break": 4, "slot": "S2",
                     "replaces": "2050030010", "gain": 1},
                    {"card_id": "2050080010", "level": 80, "target_level": 80, "limit_break": 4, "slot": "S1",
                     "replaces": "2050020010", "gain": 1},
                    {"card_id": "2050090010", "level": 1, "target_level": 60, "limit_break": 0, "slot": "S5",
                     "replaces": "2050020010", "gain": 1},
                    {"card_id": "2050090010", "level": 1, "target_level": 60, "limit_break": 0, "slot": "guest",
                     "replaces": "2050060010", "gain": 0.4}]}
        fallback = {"version": 1, "status": "rejected", "reason": "auto_team_rejected", "engine": "wing_team",
                    "fallback_reason": "p_card_unavailable", "recommender": summary, "proposal": None}
        script = (
            "import './tests/fixtures/ui/fake_dom.mjs';"
            "import { cleanProposal, engineLabel, fallbackLabel } from './ui/components/team-proposal.js';"
            "import { engineInfo } from './ui/components/team-field.js';"
            "import { parseLevel, parseLimitBreak, applyDetailEdits, detailsSummary } from './ui/components/team-details.js';"
            "import { normalizeOwned, saveOwnedCards } from './ui/components/team-store.js';"
            "const [data, fallback] = JSON.parse(process.argv[1]);"
            "const good = cleanProposal(data), back = cleanProposal(fallback);"
            "const owned = { p: ['1040010010'], s: ['2050020010', '2050030010'], details: { '2050020010': { level: 60, limit_break: 2 } } };"
            "const edited = applyDetailEdits(owned, { '1040010010': { level: 80, limit_break: 4 }, '2050020010': { level: null },"
            "  '2059999999': { level: 5 } });"
            "let sent = null; const actions = { setPref: (k, v) => { sent = [k, v]; } };"
            "saveOwnedCards({ bridgeKind: 'mock', prefs: { owned_cards: owned } }, actions, { p: ['1040010010'], s: ['2050020010'] });"
            "console.log(JSON.stringify({ good, back,"
            " shown: engineInfo(good).textContent, backShown: engineInfo(back).textContent, none: engineInfo(cleanProposal(null)),"
            " labels: [engineLabel('true_end'), engineLabel('nope'), fallbackLabel('too_few_s_cards'), fallbackLabel('')],"
            " levels: ['', ' 80 ', '0', '101', '7.5', 'x'].map((v) => parseLevel(v) ?? String(parseLevel(v))),"
            " lbs: ['', '0', '4', '5', '-1'].map((v) => parseLimitBreak(v) ?? String(parseLimitBreak(v))),"
            " edited, summary: detailsSummary({ ...owned, details: edited }),"
            " normalized: normalizeOwned({ p: ['1040010010'], s: [], details: { '1040010010': { level: 80, limit_break: 9 }, '2050020010': { level: 3 } } }),"
            " sent }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps([data, fallback], ensure_ascii=False)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        good, back = out["good"], out["back"]
        self.assertEqual((good["engine"], good["fallbackReason"]), ("true_end", ""))
        self.assertIsNone(good["recommender"]["trueEndProbability"])   # never taken from the file
        self.assertEqual(good["recommender"]["battle"]["rows"], [{"audition": "final", "worst": .875, "lower": .5}])
        self.assertEqual(good["recommender"]["assumptions"], ["仅在 5 张可计算的持有卡中搜索"])
        self.assertEqual(good["coverage"], {"usable": 6, "not_parsed": 2})
        self.assertEqual([n["id"] for n in good["needsParsing"]], ["2050990010"])
        self.assertIsNotNone(good["proposal"])
        # 练级提示: one per card; a hint must raise the level, name a real slot and gain something.
        self.assertEqual(good["trainingHints"], [
            {"cardId": "2050070010", "level": 40, "targetLevel": 80, "limitBreak": 4, "slot": "S3", "replaces": "2050040010", "gain": 1.25},
            {"cardId": "2050090010", "level": 1, "targetLevel": 60, "limitBreak": 0, "slot": "guest", "replaces": "2050060010", "gain": 0.4}])
        self.assertEqual(back["trainingHints"], [])
        self.assertEqual((back["engine"], back["fallbackReason"], back["recommender"]), ("wing_team", "p_card_unavailable", None))
        for text in ("True End 推荐器", "ProxyTERating 61.2（ESTIMATED）", "True End 概率：未建立", "Vo 812", "决赛 最差 87.5% / 下界 50%",
                     "仅在 5 张可计算的持有卡中搜索", "1 张持有卡还没有已解析"):
            self.assertIn(text, out["shown"])
        self.assertIn("旧评分（wing_team）", out["backShown"])
        self.assertIn("回退原因：没有可计算的 P 卡", out["backShown"])
        self.assertNotIn("ProxyTERating", out["backShown"])
        self.assertIsNone(out["none"])
        self.assertEqual(out["labels"], ["True End 推荐器", "", "可计算的 S 卡不足 5 名偶像", ""])
        self.assertEqual(out["levels"], ["null", 80, "undefined", "undefined", "undefined", "undefined"])  # null = unknown
        self.assertEqual(out["lbs"], ["null", 0, 4, "undefined", "undefined"])
        self.assertEqual(out["edited"], {"1040010010": {"level": 80, "limit_break": 4}, "2050020010": {"limit_break": 2}})
        self.assertEqual(out["summary"], {"known": 1, "total": 3})
        self.assertEqual(out["normalized"], {"p": ["1040010010"], "s": [], "details": {"1040010010": {"level": 80}}})
        self.assertEqual(out["sent"], ["owned_cards", {"p": ["1040010010"], "s": ["2050020010"],
                                                       "details": {"2050020010": {"level": 60, "limit_break": 2}}}])


class ProgressTests(unittest.TestCase):
    """推荐进度条: one step per compared P card plus 练级提示, sent to the host as stdout lines and
    shown under 推荐编队 while the recommender runs."""

    def test_steps_run_from_zero_to_total(self):
        steps = []
        result = recommend(owned(), progress=lambda done, total: steps.append((done, total)))
        self.assertEqual(result["engine"], "true_end")
        self.assertEqual(steps, [(0, 2), (1, 2), (2, 2)])   # one owned P card + 练级提示

    def test_tool_line_matches_the_host_parser(self):
        import contextlib
        import io
        import re
        from tools import auto_team_propose as tool
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tool.print_progress(3, 9)
        line = out.getvalue().strip()
        self.assertEqual(json.loads(line), {"progress": [3, 9]})
        host = (ROOT / "browser_host" / "Ui" / "UiTeamPropose.cs").read_text(encoding="utf-8")
        self.assertIn('"--progress"', host)
        pattern = re.search(r'GeneratedRegex\(@"(\^\\\{""progress"".*?)"\)\]', host).group(1).replace('""', '"')
        self.assertRegex(line, pattern)

    @unittest.skipUnless(shutil.which("node"), "node is required")
    def test_panel_shows_steps_only_while_running(self):
        script = (
            "import './tests/fixtures/ui/fake_dom.mjs';"
            "import { proposeProgress } from './ui/components/team-field.js';"
            "const show = (tp) => { const el = proposeProgress(tp, '推荐中…');"
            " return [el.textContent, el.dataset.proposeProgress, el.getAttribute('role')]; };"
            "console.log(JSON.stringify([show({ state: 'running', progress: { done: 3, total: 9 } }),"
            " show({ state: 'running', progress: null }), show({ state: 'done', progress: { done: 9, total: 9 } }),"
            " show({ state: 'running', progress: { done: 12, total: 9 } }), show(null)]));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [["推荐中…3/9", "3/9", "status"], ["推荐中…", "", "status"],
                                                     ["推荐中…", "", "status"], ["推荐中…9/9", "9/9", "status"],
                                                     ["推荐中…", "", "status"]])



class TieTests(unittest.TestCase):
    """基本持平: 92.3 against 92.0 is within the estimate; a team less than 1 point below the best is
    marked so the player can pick either."""

    @unittest.skipUnless(shutil.which("node"), "node is required")
    def test_close_ratings_are_marked_as_a_tie(self):
        script = (
            "import { tiedWithBest, TIE_MARGIN } from './ui/components/team-proposal.js';"
            "const all = [{ id: 'm', rating: 92.32 }, { id: 'a', rating: 91.97 }, { id: 'b', rating: 90.94 }, { id: 'c', rating: null }];"
            "console.log(JSON.stringify({ margin: TIE_MARGIN, marks: all.map((c) => tiedWithBest(all, c)), none: tiedWithBest([], all[1]) }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"margin": 1.0, "marks": [False, True, False, False], "none": False})


if __name__ == "__main__":
    unittest.main()
