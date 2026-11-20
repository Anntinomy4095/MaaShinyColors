"""Panel topology family, DB/Wiki field merge and per-role readiness (synthetic data only)."""
import copy
import unittest

from agent.core import card_data_merge as merge
from agent.core import shinycolorsdb_cards as conv
from agent.core.true_end.growth import panel_closure
from agent.core.true_end.models import CardData, Ownership, extract_card_features


def cap(slot, lb=0):
    return {"panelId": f"p{slot}", "panelSlot": slot, "panelIsGold": 0, "panelReleaseEvolution": lb,
            "panelReleaseByEvent": False, "panelReleaseDesc": None, "skillTitle": "Vocal上限UP",
            "skillDesc": "Vocal上限＋25", "skillType": "limit_break",
            "skillEffects": [{"effectType": "limit_break", "attribute": "vocal", "value": 25}], "extraEffect": None}


def raw_card(slots=conv.STANDARD_SLOTS, method="GeneralGasha", enza="2049990030"):
    return {"enzaId": enza, "cardUuid": "u", "cardName": "【合成】偶像", "cardType": "S_SSR", "getMethod": method,
            "panelSPOffset": 0, "cardHash": "h", "releaseDate": "2026-01-01", "idol": {"idolName": "偶像", "unitId": 1},
            "cardStatus": {"vocal": 300, "dance": 150, "visual": 150, "mental": 140},
            "cardPanels": [cap(s) for s in slots],
            "cardSupportSkills": [{"skillName": "ボーカルマスタリーSP", "skillDesc": "一緒にボーカルレッスンをするとSP＋3",
                                   "skillType": "tag_boost", "skillAttribute": "skill_point", "gainedAt": 1,
                                   "skillLevel": 3}],
            "cardSupportEvents": [{"eventName": "e", "eventAddVo": 20, "eventAddDa": 0, "eventAddVi": 0,
                                   "eventAddMe": 0, "eventAddSp": 10}]}


def reachable(edges, learned):
    """Nodes a player can open next under the in-game rule: row-1 root or an edge to a learned node."""
    adj = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    nodes = set(adj) | {11}
    return {n for n in nodes - set(learned) if n == 11 or adj.get(n, set()) & set(learned)}


class TopologyTest(unittest.TestCase):
    def test_triangle_edges(self):
        edges = conv.branch_edges()
        self.assertEqual(len(edges), 18)  # 6 sibling + 12 cross-row shared hex edges
        self.assertIn((21, 22), edges)
        self.assertIn((32, 33), edges)
        self.assertNotIn((21, 33), edges)
        self.assertEqual(sorted(b for a, b in edges if a == 22), [32, 33])

    def test_in_game_observations(self):
        """Learned -> open node sets read from the recordings (states only; card bound)."""
        edges = conv.branch_edges()
        observations = [
            ({11, 22}, {21, 32, 33}),                    # demo3-03 #461, P 1040040070
            ({11, 21, 31, 41}, {22, 32, 42}),            # demo2-02 #2628, 2040050080 (33/43/44 closed)
            ({11, 21, 31, 32, 41}, {22, 33, 42, 43}),    # demo2-02 #2639, same card: 33 opens via sibling 32
            ({11, 21, 31, 32, 41}, {22, 33, 42, 43}),    # demo2-02 #2629, 2040060080 (44 closed)
        ]
        for learned, opened in observations:
            self.assertEqual(reachable(edges, learned), opened, learned)
        # A rule without sibling edges would keep 33 closed in #2639: the observation rejects it.
        cross_only = [(a, b) for a, b in edges if b // 10 != a // 10]
        self.assertNotIn(33, reachable(cross_only, {11, 21, 31, 32, 41}))

    def test_families(self):
        self.assertEqual(conv.topology_family([cap(s) for s in conv.STANDARD_SLOTS])[0], "tri10")
        self.assertEqual(conv.topology_family([cap(s) for s in conv.SR_SLOTS])[0], "sr8")
        self.assertEqual(conv.topology_family([cap(s) for s in conv.ROW5_SLOTS]),
                         ("row5_13", "USER_SPECIFIED_2026-10-07"))
        dup = [cap(s) for s in conv.STANDARD_SLOTS] + [cap(44)]
        self.assertTrue(conv.topology_family(dup)[0].startswith("anomaly"))
        self.assertIsNone(conv.convert_card(raw_card(slots=(11, 21, 22, 31, 32, 33, 41, 42)))["panel_topology"])
        sr_edges = conv.branch_edges(conv.SR_SLOTS)
        self.assertEqual(sorted(a for a, b in sr_edges if b == 42), [31, 32])
        self.assertIn((42, 43), sr_edges)
        r5 = conv.family_edges(conv.ROW5_SLOTS)
        self.assertEqual(sorted(a for a, b in r5 if b == 52), [41, 42])

    def test_twilight_p_row5_user_rule(self):
        """Outer row-4 nodes open only their side's row-5 node; middle ones open two each."""
        edges = conv.family_edges(conv.ROW5_SLOTS)
        row5 = {a: sorted(b for x, b in edges if x == a and b // 10 == 5) for a in (41, 42, 43, 44)}
        self.assertEqual(row5, {41: [52], 42: [52, 53], 43: [53, 54], 44: [54]})
        self.assertFalse([(a, b) for a, b in edges if a // 10 == 5 and b // 10 == 5])  # no row-5 sibling edge
        self.assertEqual(reachable(edges, {11, 21, 31, 41}) & {52, 53, 54}, {52})
        self.assertEqual(reachable(edges, {11, 21, 32, 42}) & {52, 53, 54}, {52, 53})
        self.assertEqual(reachable(edges, {11, 22, 33, 44, 52}) & {52, 53, 54}, {54})
        raw = raw_card(slots=conv.ROW5_SLOTS)
        raw.update(cardType="P_SSR", enzaId="1049990031", getMethod="TwilightCollection", releaseDate="2024-01-01")
        card = conv.convert_card(raw)
        topo = card["panel_topology"]
        self.assertEqual((topo["family"], topo["confidence"]), ("row5_13", "USER_SPECIFIED_2026-10-07"))
        model = CardData.from_dict(card)
        feats = extract_card_features(model, Ownership(model.card_id, 4, 1))
        self.assertEqual([p.id for p in panel_closure(feats, "scdb-53")][-2:], ["scdb-42", "scdb-53"])

    def test_converter_topology_drives_growth_path(self):
        card = conv.convert_card(raw_card())
        topo = card["panel_topology"]
        self.assertEqual((topo["family"], topo["confidence"], topo["complete"]),
                         ("tri10", "IN_GAME_RULE_STANDARD_LAYOUT", True))
        model = CardData.from_dict(card)
        feats = extract_card_features(model, Ownership(model.card_id, 4, 80))
        path = panel_closure(feats, "scdb-44")
        self.assertEqual([p.id for p in path], ["scdb-11", "scdb-22", "scdb-33", "scdb-44"])  # cheapest witness
        self.assertEqual(conv.convert_card(raw_card(enza="2040060080"))["panel_topology"]["confidence"],
                         "IN_GAME_CARD_VERIFIED")
        # A dropped panel (unknown LB) must not leave edges to a missing node.
        bad = raw_card()
        bad["cardPanels"][3]["panelReleaseEvolution"] = 9
        self.assertIsNone(conv.convert_card(bad)["panel_topology"])


def wiki_card(**over):
    card = {"card_id": "【合成】偶像", "name": "【合成】偶像", "idol": "偶像", "kind": "S", "status": "PARTIAL",
            "source_url": "https://wikiwiki.jp/shinycolors/x",
            "levels": {"1": {"Vo": 120, "Da": 60, "Vi": 60, "Me": 56}, "80": {"Vo": 300, "Da": 150, "Vi": 150, "Me": 140}},
            "max_levels": {"0": 60, "1": 65, "2": 70, "3": 75, "4": 80},
            "owned_live_skills": [{"id": "ボーカルアピールⅣ", "components": {"Vo": 2.5}, "unlock_level": 1},
                                  {"id": "ボーカルアピールⅣ+", "components": {"Vo": 3.0}, "unlock_level": 80}],
            "support_skills": [{"id": "ボーカルマスタリーSP", "kind": "mastery_sp", "levels": {"1": 3}}],
            "panels": [{"id": f"w{i}", "kind": "cap", "sp_cost": c, "required_lb": 0}
                       for i, c in enumerate([20, 30, 30, 40, 40, 40, 50, 50, 50, 50])],
            "events": [{"id": "e", "stats": {"Vo": 20, "Da": 0, "Vi": 0, "Me": 0, "SP": 10}}],
            "provenance": {"levels": {"url": "https://wikiwiki.jp/shinycolors/x", "content_sha256": "abc"}}}
    card.update(over)
    return card


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.db = conv.convert_card(raw_card())

    def test_merge_fills_db_gaps_with_provenance_and_becomes_ready(self):
        self.assertFalse(self.db["readiness"]["ready_as_active"])
        merged = merge.merge_card(self.db, wiki_card())
        self.assertEqual(merged["merge"]["sources"]["levels.80"], "db+wiki agree")
        self.assertEqual(merged["merge"]["sources"]["owned_live_skills"], "wiki")
        self.assertEqual(merged["provenance"]["levels"]["wiki"]["content_sha256"], "abc")
        self.assertEqual(merged["merge"]["conflicts"], [])
        self.assertTrue(merged["readiness"]["ready_as_active"])
        self.assertTrue(merged["readiness"]["ready_as_guest"])
        model = CardData.from_dict(merged)
        self.assertTrue(extract_card_features(model, Ownership(model.card_id, 4, 80)).ready)
        # Lv65 has no stat row: a 1-LB card is not ready even though the Wiki gives its cap.
        low = merge.role_readiness(merged, limit_break=1)
        self.assertIn("levels.65", low["critical_gaps"]["active"])

    def test_conflicts_kept_unresolved_with_both_values(self):
        w = wiki_card(levels={"80": {"Vo": 301, "Da": 150, "Vi": 150, "Me": 140}},
                      support_skills=[{"id": "ボーカルマスタリーSP", "kind": "mastery_sp", "levels": {"1": 4}}])
        merged = merge.merge_card(self.db, w)
        self.assertIsNone(merged["levels"]["80"]["Vo"])  # no silent pick
        items = {u["field"]: u for u in merged["merge"]["unresolved"]}
        self.assertEqual((items["levels.80.Vo"]["db"], items["levels.80.Vo"]["wiki"]), (300.0, 301))
        self.assertEqual(items["levels.80.Vo"]["status"], "unresolved")
        self.assertEqual(items["levels.80.Vo"]["wiki_provenance"]["content_sha256"], "abc")
        self.assertIn("support_skills.ボーカルマスタリーSP", items)
        skill = merged["support_skills"][0]
        self.assertTrue(any("unresolved" in u for u in skill["unsupported"]))  # never applied while unresolved
        r = merged["readiness"]
        self.assertFalse(r["ready_as_active"])
        self.assertIn("levels.80 (unresolved)", r["unresolved_gaps"]["active"])
        self.assertIn("support_skills.ボーカルマスタリーSP (unresolved)", r["unresolved_gaps"]["guest"])
        self.assertEqual(r["unknown_gaps"]["active"], [])

    def test_in_game_reading_resolves_a_stat_conflict(self):
        from unittest import mock
        w = wiki_card(levels={"80": {"Vo": 301, "Da": 150, "Vi": 150, "Me": 140}})
        with mock.patch.dict(merge.IN_GAME_STATS_VERIFIED,
                             {self.db["card_id"]: {"levels": {80: {"Vo": 301}}, "evidence": "frame"}}):
            merged = merge.merge_card(self.db, w)
        self.assertEqual(merged["levels"]["80"]["Vo"], 301.0)
        self.assertEqual(merged["merge"]["unresolved"], [])
        self.assertEqual(merged["merge"]["conflicts"][0]["status"], "resolved_in_game")

    def test_sensitivity_same_outcome_is_non_blocking(self):
        w = wiki_card(levels={"80": {"Vo": 300, "Da": 150, "Vi": 151, "Me": 140}})
        merged = merge.merge_card(self.db, w)
        self.assertFalse(merged["readiness"]["ready_as_active"])
        # Decision ignores Vi (e.g. a Vo ranking); score = Vi so the lower (less favourable) side is used.
        outcome = lambda c: (("rank", 1), c["levels"]["80"]["Vi"])  # noqa: E731
        done = merge.assess_unresolved(merged, outcome)
        self.assertEqual(done["merge"]["sensitivity"]["verdict"], "non_blocking")
        self.assertEqual(done["merge"]["sensitivity"]["value_used"], "db")
        self.assertEqual(done["levels"]["80"]["Vi"], 150.0)
        self.assertEqual(done["merge"]["unresolved"][0]["status"], "non_blocking")
        self.assertEqual(done["merge"]["unresolved"][0]["wiki"], 151)  # the conflict record stays
        self.assertTrue(done["readiness"]["ready_as_active"])
        self.assertEqual(done["readiness"]["non_blocking_conflicts"], ["levels.80.Vi"])

    def test_sensitivity_changed_outcome_blocks(self):
        w = wiki_card(levels={"80": {"Vo": 330, "Da": 150, "Vi": 150, "Me": 140}})
        merged = merge.merge_card(self.db, w)
        outcome = lambda c: ((1 if c["levels"]["80"]["Vo"] > 310 else 2,), c["levels"]["80"]["Vo"])  # noqa: E731
        done = merge.assess_unresolved(merged, outcome)
        report = done["merge"]["sensitivity"]
        self.assertEqual(report["verdict"], "blocker")
        self.assertEqual((report["db"]["decision"], report["wiki"]["decision"]), ((2,), (1,)))
        self.assertIsNone(done["levels"]["80"]["Vo"])
        self.assertIn("levels.80 (unresolved)", done["readiness"]["unresolved_gaps"]["active"])

    def test_sensitivity_on_real_screening_outcome(self):
        a = merge.merge_card(self.db, wiki_card(levels={"80": {"Vo": 300, "Da": 150, "Vi": 151, "Me": 140}}))
        b = merge.merge_card(conv.convert_card(dict(raw_card(enza="2049990040"), cardName="【別】偶像2",
                                                    idol={"idolName": "偶像2", "unitId": 1})),
                             wiki_card(name="【別】偶像2", card_id="【別】偶像2", idol="偶像2"))
        done = merge.assess_unresolved(a, merge.screening_outcome([a, b]))
        self.assertEqual(done["merge"]["sensitivity"]["verdict"], "non_blocking")  # Vi does not move a VO rank

    def test_contested_sp_never_resolved_by_wiki(self):
        raw = raw_card(method="TwilightCollection")
        raw["releaseDate"] = None  # outside the version rule: stays contested
        db = conv.convert_card(raw)
        w = wiki_card(panels=[{"id": f"w{i}", "kind": "cap", "sp_cost": c, "required_lb": 0}
                              for i, c in enumerate([10, 20, 20, 30, 30, 30, 40, 40, 40, 40])])
        merged = merge.merge_card(db, w)
        self.assertTrue(any(u.endswith(".sp_cost") for u in merged["unknown_fields"]))
        self.assertTrue(merged["provenance"]["panels"]["wiki_evidence"]["wiki_10_lower"])
        self.assertFalse(merged["readiness"]["ready_as_active"])
        self.assertIn("panels.scdb-11.sp_cost", merged["readiness"]["critical_gaps"]["guest"])

    def test_guest_does_not_need_owned_live(self):
        w = wiki_card(owned_live_skills=[])
        r = merge.merge_card(self.db, w)["readiness"]
        self.assertFalse(r["ready_as_active"])
        self.assertTrue(r["ready_as_guest"])
        self.assertEqual(r["critical_gaps"]["active"], ["owned_live_skills"])

    def test_readiness_marks_assumed_ownership_and_level_cap(self):
        merged = merge.merge_card(self.db, wiki_card())
        self.assertEqual(merged["readiness"]["assumed"]["ownership"], "ASSUMED_LB4_CAP")
        owned = merge.role_readiness(merged, limit_break=2, level=70)
        self.assertEqual(owned["assumed"]["ownership"], "OWNED")
        self.assertIn("levels.70", owned["critical_gaps"]["guest"])  # never the Lv80 row
        over = merge.role_readiness(merged, limit_break=2, level=80)
        self.assertIn("level 80 exceeds the LB2 cap 70", over["critical_gaps"]["guest"])

    def test_locked_panels_do_not_block_low_lb(self):
        raw = raw_card(method="TwilightCollection")
        raw["releaseDate"] = None
        card = conv.convert_card(raw)
        for p in card["panels"]:
            p["required_lb"] = 0 if p["id"] == "scdb-11" else 2
        card["unknown_fields"] = ["panels.scdb-44.sp_cost"]
        r = merge.role_readiness(merge.merge_card(card, wiki_card()), limit_break=1)
        self.assertNotIn("panels.scdb-44.sp_cost", r["critical_gaps"]["guest"])

    def test_match_by_exact_name_only(self):
        self.assertEqual(list(merge.match_by_name([self.db], [wiki_card()])), [self.db["card_id"]])
        self.assertEqual(merge.match_by_name([self.db], [wiki_card(), wiki_card(source_url="b")]), {})
        self.assertEqual(merge.match_by_name([self.db], [wiki_card(name="【別】偶像", card_id="【別】偶像")]), {})
        payload = merge.merge_database({"cards": [self.db]}, [wiki_card()])
        self.assertEqual(payload["matched"], [self.db["card_id"]])
        self.assertEqual(merge.readiness_counts(payload["cards"])["ready_as_active"], 1)

    def test_fetched_page_binds_by_enza_id_only_when_name_and_kind_agree(self):
        good = dict(wiki_card(), fetch_enza_id=self.db["card_id"])
        matches, notes = merge.bind_wiki_cards([self.db], [good, dict(good)])  # same URL twice: read once
        self.assertEqual(list(matches), [self.db["card_id"]])
        self.assertEqual(notes, [])
        wrong = dict(wiki_card(name="【別】偶像", card_id="【別】偶像"), fetch_enza_id=self.db["card_id"])
        matches, notes = merge.bind_wiki_cards([self.db], [wrong])
        self.assertEqual(matches, {})
        self.assertEqual(notes[0]["reason"], "fetched page does not match the DB card; not merged")
        db_heart = dict(self.db, name="【合成～♡】偶像")
        alias = dict(wiki_card(name="【合成～】偶像", card_id="【合成～】偶像"), fetch_enza_id=self.db["card_id"])
        matches, notes = merge.bind_wiki_cards([db_heart], [alias])
        self.assertEqual(list(matches), [self.db["card_id"]])
        self.assertIn("decorative symbols", notes[0]["reason"])
        self.assertEqual(merge.match_by_name([db_heart], [wiki_card(name="【合成～】偶像")]), {})  # name match stays exact
        p_page = dict(wiki_card(kind="P"), fetch_enza_id=self.db["card_id"])
        self.assertEqual(merge.bind_wiki_cards([self.db], [p_page])[0], {})

    def test_p_te_eligible_from_wiki_only_when_stated(self):
        raw = raw_card()
        raw.update(enzaId="1049990030", cardType="P_SSR")
        p = conv.convert_card(raw)
        page = wiki_card(kind="P", te_eligible=True, levels={}, max_levels={}, owned_live_skills=[],
                         support_skills=[], events=[], panels=[])
        merged = merge.merge_card(p, page)
        self.assertIs(merged["te_eligible"], True)
        self.assertEqual(merged["provenance"]["te_eligible"]["source"], "wiki")
        self.assertNotIn("te_eligible", merged["unknown_fields"])
        self.assertTrue(merged["readiness"]["ready_as_p"])
        silent = merge.merge_card(p, dict(page, te_eligible=None))
        self.assertIsNone(silent["te_eligible"])  # absence of the text is unknown, never False
        self.assertEqual(silent["readiness"]["critical_gaps"]["p"], ["te_eligible"])

    def test_te_condition_reader_accepts_both_spellings_and_only_the_standard_condition(self):
        page = ("<td>Sweet shopping</td><th>{}</th><td>条件</td><td>{}</td><td>&nbsp;</td>"
                "<h3>プロデュースイベント</h3>")
        std = "アイドルランクA達成 かつ W.I.N.G.優勝"
        for spelling in ("TrueEnd", "True End"):
            self.assertEqual(merge.te_condition_from_page(page.format(spelling, std)), std)
        self.assertIsNone(merge.te_condition_from_page("<p>TrueEnd研修</p>"))
        row = {"kind": "P", "te_eligible": None, "source_url": "u", "provenance": {}}
        merge._apply_te_condition(row, page.format("TrueEnd", std))
        self.assertIs(row["te_eligible"], True)
        fans = {"kind": "P", "te_eligible": True, "provenance": {}}
        merge._apply_te_condition(fans, page.format("True End", "ファン数50万人達成 かつ W.I.N.G.優勝"))
        self.assertIsNone(fans["te_eligible"])  # not the modelled True End: unknown
        self.assertEqual(fans["te_condition"], "ファン数50万人達成 かつ W.I.N.G.優勝")

    def test_apply_owned_levels_derives_the_owned_row_and_marks_source(self):
        page = wiki_card(levels={"1": {"Vo": 120, "Da": 60, "Vi": 60, "Me": 56},
                                 "60": {"Vo": 240, "Da": 120, "Vi": 120, "Me": 112},
                                 "80": {"Vo": 300, "Da": 150, "Vi": 150, "Me": 140}})
        merged = merge.merge_card(self.db, page)
        self.assertFalse(merge.role_readiness(merged, limit_break=1, level=63)["ready_as_guest"])
        out = merge.apply_owned_levels([merged], {self.db["card_id"]: {"limit_break": 1, "level": 63}})[0]
        self.assertEqual(out["levels"]["63"], {"Vo": 249.0, "Da": 124.0, "Vi": 124.0, "Me": 116.0})
        self.assertEqual(out["merge"]["derived_levels"]["63"]["status"], "derived")
        self.assertEqual(out["merge"]["owned_level"]["source"], merge.OWNED_LEVEL_SOURCE)
        self.assertEqual(out["readiness"]["assumed"]["ownership"], "OWNED")
        self.assertTrue(out["readiness"]["ready_as_active"])
        untouched = merge.apply_owned_levels([merged], {})[0]
        self.assertNotIn("owned_level", untouched["merge"])

    def test_load_owned_details_reads_the_js_module(self):
        import json
        import tempfile
        from pathlib import Path
        doc = {"details": {"2049990030": {"limit_break": 4, "level": 80}, "1049990030": {"limit_break": 2}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "owned-levels.js"
            path.write_text("// x\nexport default " + json.dumps(doc) + ";\n", encoding="utf-8")
            self.assertEqual(merge.load_owned_details(path), doc["details"])

    def test_wiki_cache_log_skips_404_robots_and_challenge_lines(self):
        import json
        import tempfile
        from pathlib import Path
        lines = [{"enza_id": None, "url": "https://wikiwiki.jp/robots.txt", "status": 200},
                 {"enza_id": "2049990030", "url": "https://wikiwiki.jp/shinycolors/x", "status": 404},
                 {"enza_id": "2049990031", "url": "https://wikiwiki.jp/shinycolors/y", "status": None, "error": "t"},
                 {"enza_id": "2049990032", "url": "https://wikiwiki.jp/shinycolors/z", "status": 200,
                  "file": "z.html", "challenge": True}]
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "fetch-log.jsonl"
            log.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
            errors = []
            self.assertEqual(merge._load_wiki_cache(log, errors), [])
            self.assertEqual(errors, [])

    def test_p_readiness_ignores_unconsumed_fields(self):
        raw = raw_card()
        raw.update(enzaId="1049990030", cardType="P_SSR")
        card = conv.convert_card(raw)
        self.assertEqual(card["readiness"]["critical_gaps"]["p"], ["te_eligible"])
        card = copy.deepcopy(card)
        card["te_eligible"] = True
        self.assertTrue(merge.role_readiness(card)["ready_as_p"])


if __name__ == "__main__":
    unittest.main()
