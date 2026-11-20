"""Synthetic account contracts; no game, network or private profile access."""
from copy import deepcopy
import unittest

from agent.core.card_id_map import CardIdMap, build_map
from agent.core.true_end.account import build_account_input, build_reachability_report
from agent.core.true_end.models import CardData, STATS


def fixture():
    rows, cards = [], {}
    ids = ["1000000001", *[f"200000000{i}" for i in range(1, 6)]]
    skill_defs = [{"id": f"skill-{i}", "components": {a: 1 + (i == 3)}}
                  for i, a in enumerate(("Vo", "Da", "Vi", "Vo"))]
    for i, ui_id in enumerate(ids):
        title, idol, kind = f"synthetic-{i}", f"idol-{i}", "P" if i == 0 else "S"
        key = f"【{title}】{idol}"
        raw = {"card_id": key, "name": key, "idol": idol, "kind": kind,
               "status": "OK", "source_url": "synthetic://account-tests",
               "te_eligible": True if kind == "P" else None,
               "levels": {"50": dict.fromkeys(STATS, 100)}, "max_levels": {"0": 60, "4": 80},
               "owned_live_skills": [{"id": "own-" + str(i), "components": {"Vo": 2}}],
               "panels": [{"id": f"panel-{j}", "kind": "live", "sp_cost": 10,
                           "prerequisites_known": True, "effects": s} for j, s in enumerate(skill_defs)] if i == 0 else []}
        cards[key] = CardData.from_dict(raw)
        rows.append({"card_id": ui_id, "title": title, "idol": idol})
    mapping = CardIdMap(build_map([("P", rows[:1]), ("S", rows[1:])],
                                  [{"card_id": c.card_id, "kind": c.kind, "idol": c.idol} for c in cards.values()]))
    profile = {"prefs": {"owned_cards": {"p": ids[:1], "s": ids[1:],
        "details": {k: {"limit_break": 0, **({"level": 50} if i else {})} for i, k in enumerate(ids)}}}}
    settings = {"p_card": ids[0], "primary_attribute": "Vo",
                "growth": {"starting_stats": dict.fromkeys(STATS, 17),
                           "starting_caps": dict.fromkeys(STATS, 500), "starting_sp": 3,
                           "starting_live_skills": [{**s, "source_card": ids[0], "panel_id": f"panel-{j}"}
                                                   for j, s in enumerate(skill_defs)]},
                "learned_panels": [{"source_card": ids[0], "panel_id": f"panel-{j}"} for j in range(4)]}
    return profile, settings, mapping, cards


class AccountTests(unittest.TestCase):
    def bind(self, values=None):
        return build_account_input(*(values or fixture()))

    def test_explicit_actual_state_and_p_level_sentinel(self):
        bound = self.bind()
        self.assertEqual(bound["status"], "READY_ACCOUNT_INPUT")
        payload = bound["input"]
        self.assertEqual(payload["growth"]["starting_stats"]["Vo"], 17)
        self.assertEqual(payload["growth"]["starting_sp"], 3)
        p = next(v for v in payload["inventory"] if v["card_id"] == payload["p_card"])
        self.assertEqual(p["level"], 1)
        self.assertEqual(p["limit_break"], 0)
        self.assertTrue(all(v["level"] == 50 for v in payload["inventory"] if v != p))

    def test_no_ownership_inferred_from_database(self):
        p, s, m, c = fixture()
        result = self.bind(({}, s, m, c))
        self.assertEqual(result["status"], "NEEDS_ACCOUNT_DATA")
        self.assertIsNone(result["input"])
        self.assertTrue(any(v["field"] == "prefs.owned_cards" for v in result["missing"]))

    def test_required_opening_inputs_never_default(self):
        for field in ("starting_stats", "starting_sp", "starting_caps", "starting_live_skills"):
            p, s, m, c = fixture()
            del s["growth"][field]
            result = self.bind((p, s, m, c))
            self.assertIsNone(result["input"])
            self.assertIn("growth." + field, [v["field"] for v in result["missing"]])

    def test_bad_types_bool_and_missing_actual_lb(self):
        for key, value in (("level", True), ("limit_break", 4.0), ("limit_break", None)):
            p, s, m, c = fixture()
            p["prefs"]["owned_cards"]["details"]["2000000001"][key] = value
            result = self.bind((p, s, m, c))
            self.assertEqual(result["status"], "NEEDS_ACCOUNT_DATA")
            self.assertEqual(result["excluded_cards"][0]["id"], "2000000001")

    def test_actual_level_above_break_limit_rejected(self):
        p, s, m, c = fixture()
        p["prefs"]["owned_cards"]["details"]["2000000001"]["level"] = 80
        result = self.bind((p, s, m, c))
        self.assertTrue(any("limit-break" in str(v) for v in result["excluded_cards"]))

    def test_unmapped_and_unknown_excluded(self):
        p, s, m, c = fixture()
        self.assertEqual(self.bind((p, s, CardIdMap(), c))["status"], "NEEDS_ACCOUNT_DATA")
        key = m.true_end_id("2000000001")
        raw = deepcopy(c[key].__dict__)
        raw["unknown_fields"] = ("panels",)
        c[key] = CardData(**raw)
        result = self.bind((p, s, m, c))
        self.assertIn("panels", result["excluded_cards"][0]["missing"])

    def test_duplicate_baseline_idol_rejected(self):
        p, s, m, c = fixture()
        s["baseline"] = {"active": ["2000000001"] * 4, "guest": "2000000005"}
        result = self.bind((p, s, m, c))
        self.assertTrue(any(v["code"] == "invalid_team" for v in result["missing"]))

    def test_skill_requires_source_and_learned_record(self):
        p, s, m, c = fixture()
        s["learned_panels"] = []
        result = self.bind((p, s, m, c))
        self.assertEqual(result["status"], "NEEDS_ACCOUNT_DATA")
        self.assertIn("显式已学习", str(result["missing"]))

    def test_unknown_topology_lists_need_panel_paths(self):
        p, s, m, c = fixture()
        key = m.true_end_id("1000000001")
        from dataclasses import replace
        c[key] = replace(c[key], panels=tuple(replace(v, prerequisites_known=False) for v in c[key].panels))
        result = self.bind((p, s, m, c))
        self.assertTrue(result["need_panel_paths"])
        self.assertIsNone(result["input"])

    def test_digest_changes_for_actual_level_mapping_and_equipment(self):
        original = self.bind()["binding_sha256"]
        p, s, m, c = fixture()
        p["prefs"]["owned_cards"]["details"]["2000000001"]["limit_break"] = 4
        self.assertNotEqual(original, self.bind((p, s, m, c))["binding_sha256"])
        p, s, m, c = fixture()
        s["growth"]["starting_live_skills"].reverse()
        self.assertNotEqual(original, self.bind((p, s, m, c))["binding_sha256"])

    def test_reachability_never_guaranteed_or_te_probability(self):
        report = build_reachability_report({"recommendation": {"projection": {
            "effective_stats": {"Vo": 800}, "caps": {"Vo": 900}, "sp": 500,
            "sp_required": 400, "fans_by_season": [1000, 10000, 100000, 500000],
            "route_feasible": True, "skill_plan": {"cost": 300}}}})
        self.assertEqual(report["status"], "EXPECTED_GROWTH_ONLY")
        self.assertFalse(report["guaranteed"])
        self.assertIsNone(report["true_end_probability"])
        self.assertEqual(report["sp_path_cost"], 400)


if __name__ == "__main__":
    unittest.main()
