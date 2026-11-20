"""Offline field-level audit regressions; no actual cards or account evidence."""
from dataclasses import asdict, replace
import unittest

from agent.core.true_end.audit import compare_card_sources, review_card
from agent.core.true_end.models import CardData

EN = "https://shinycolors.wiki/wiki/S-SSR1_Test"
JP = "https://wikiwiki.jp/shinycolors/【合成】架空"
HASH = "a" * 64


def card():
    return CardData.from_dict(dict(card_id="【合成】架空", name="【合成】架空", idol="架空", kind="S",
        status="PARTIAL", source_url=EN, levels={80: {"Vo": 100, "Da": 200, "Vi": 100, "Me": None}},
        max_levels={4: 80}, te_eligible=None,
        owned_live_skills=[dict(id="live-en", components={"Da": 3}, unlock_level=60)],
        support_skills=[dict(id="support-en", kind="mastery_sp", attribute="SP", room="Da", levels={1: 3, 60: 10}, unsupported=["description conflict"])],
        panels=[dict(id="en-root", kind="cap", sp_cost=20, required_lb=0, effects={"Da": 50},
                     prerequisites=[], prerequisites_known=False),
                dict(id="en-leaf", kind="cap", sp_cost=50, required_lb=3, effects={"Da": 150},
                     prerequisites=[], prerequisites_known=False)],
        events=[], unknown_fields=["levels.80.Me", "levels", "max_levels", "panels.en-root.prerequisites", "memory_appeal"],
        unsupported=["other unsupported"],
        provenance={"card": dict(url=EN, content_sha256=HASH),
                    "panels.en-root.sp_cost": dict(url=EN, content_sha256=HASH, cells=[dict(text="20SP", row=0, column=0)])}))


def change(field="panels.0.sp_cost", value=10, expected_previous=20, **extra):
    return dict(field=field, value=value, expected_previous=expected_previous, source_url=JP,
                locator="スキルパネル 第一段", reviewed_at="2026-10-06", reason="逐节点核对日文原值") | extra


class CardAuditTests(unittest.TestCase):
    def test_review_preserves_original_sp_and_english_provenance(self):
        original = card()
        reviewed = review_card(original, [change(expected_source_sha256=HASH)])
        self.assertEqual(original.panels[0].sp_cost, 20)
        self.assertEqual(reviewed.panels[0].sp_cost, 10)
        self.assertFalse(reviewed.panels[0].prerequisites_known)
        evidence = reviewed.provenance["panels.0.sp_cost"]
        self.assertEqual((evidence["previous_value"], evidence["reviewed_value"]), (20, 10))
        self.assertEqual(evidence["previous_source"]["cells"][0]["text"], "20SP")
        self.assertEqual(evidence["original_source"]["url"], EN)
        self.assertEqual((evidence["source_url"], evidence["reviewed_at"]), (JP, "2026-10-06"))
        self.assertEqual(reviewed.status, "PARTIAL")

    def test_wrong_previous_or_hash_refused_atomically(self):
        original = card()
        for changes in ([change(expected_previous=19)], [change(expected_source_sha256="b" * 64)],
                        [change(), change("panels.1.sp_cost", 40, 49)]):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                review_card(original, changes)
        self.assertEqual(original.panels[0].sp_cost, 20)
        self.assertNotIn("panels.0.sp_cost", original.provenance)

    def test_only_resolved_exact_unknown_leaf_is_removed(self):
        reviewed = review_card(card(), [change("levels.80.Me", 150, None),
                                       change("max_levels.1", 65, None)])
        self.assertNotIn("levels.80.Me", reviewed.unknown_fields)
        self.assertIn("levels", reviewed.unknown_fields)
        self.assertIn("max_levels", reviewed.unknown_fields)
        self.assertIn("memory_appeal", reviewed.unknown_fields)
        self.assertIn("panels.en-root.prerequisites", reviewed.unknown_fields)
        self.assertEqual(reviewed.unsupported, card().unsupported)
        self.assertEqual(reviewed.support_skills[0].unsupported, ("description conflict",))

    def test_whole_mapping_review_does_not_clear_group_missing(self):
        reviewed = review_card(card(), [change("max_levels", {0: 60, 1: 65, 4: 80}, {"4": 80})])
        self.assertEqual(reviewed.max_levels, {0: 60, 1: 65, 4: 80})
        self.assertIn("max_levels", reviewed.unknown_fields)

    def test_identity_status_topology_and_list_append_are_forbidden(self):
        for field in ("card_id", "name", "idol", "kind", "status", "source_url", "unknown_fields",
                      "unsupported", "panels", "panels.0.id", "panels.0.prerequisites",
                      "panels.0.prerequisites_known", "panels.2.sp_cost", "panels.-1.sp_cost",
                      "panels.01.sp_cost", "max_levels.04", "panel_topology", "levels.__class__", "panels.0.effects.id"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                review_card(card(), [change(field, "anything", None)])

    def test_duplicate_ids_invalid_mechanics_and_bad_evidence_refused(self):
        duplicate = replace(card(), panels=(card().panels[0], card().panels[0]))
        with self.assertRaises(ValueError):
            review_card(duplicate, [])
        duplicate = replace(card(), support_skills=(card().support_skills[0],) * 2)
        with self.assertRaises(ValueError):
            compare_card_sources(duplicate, card())
        for entry in (change(value=-1), change(value=True), change("levels.80.Da", -1, 200),
                      change(source_url="https://u:p@example.com/x"), change(reviewed_at="yesterday"), change(reason="")):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                review_card(card(), [entry])
        with self.assertRaises(ValueError):
            review_card(card(), [change(), change()])
        with self.assertRaises(ValueError):
            review_card(card(), [change("max_levels", {"04": 80}, {"4": 80})])
        with self.assertRaises(ValueError):
            review_card(card(), [change("max_levels", {4: 80}, {4: 80}), change("max_levels.0", 60, None)])

    def test_repeated_review_preserves_prior_review_history(self):
        first = review_card(card(), [change()])
        second = review_card(first, [change(value=15, expected_previous=10)])
        self.assertEqual(second.provenance["panels.0.sp_cost"]["previous_source"]["previous_value"], 20)
        self.assertEqual(second.provenance["panels.0.sp_cost"]["previous_value"], 10)

    def test_panels_remain_unmatched_without_explicit_map(self):
        original = card()
        report = compare_card_sources(original, original)
        self.assertEqual(report["unmatched"]["panels"]["primary"], ["en-leaf", "en-root"])
        self.assertFalse(any(item["field"].startswith("panels.") for item in report["comparisons"]))

    def test_explicit_cross_language_maps_compare_real_values(self):
        raw = asdict(card())
        raw["source_url"] = JP
        raw["panels"][0].update(id="jp-root", sp_cost=10)
        raw["panels"][1].update(id="jp-leaf", sp_cost=40)
        raw["owned_live_skills"][0].update(id="live-jp", components={"Da": 3.1})
        raw["support_skills"][0].update(id="support-jp", levels={1: 3, 60: 20})
        reference = CardData.from_dict(raw)
        report = compare_card_sources(card(), reference, panel_map={"en-root": "jp-root", "en-leaf": "jp-leaf"},
                                      live_map={"live-en": "live-jp"}, support_map={"support-en": "support-jp"})
        rows = {r["field"]: r for r in report["comparisons"]}
        self.assertEqual(rows["panels.0.sp_cost"]["status"], "conflict")
        self.assertEqual((rows["panels.0.sp_cost"]["primary_value"], rows["panels.0.sp_cost"]["reference_value"]), (20, 10))
        self.assertEqual(rows["panels.0.effects.Da"]["status"], "same")
        self.assertEqual(rows["owned_live_skills.0.components.Da"]["status"], "conflict")
        self.assertEqual(rows["support_skills.0.levels.60"]["status"], "conflict")
        self.assertEqual(report["unmatched"]["panels"], {"primary": [], "reference": []})

    def test_identity_kind_alias_and_map_collisions_rejected(self):
        for reference in (replace(card(), card_id="another"), replace(card(), kind="P"), replace(card(), idol="English Idol")):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                compare_card_sources(card(), reference)
        reference = replace(card(), idol="English Idol")
        evidence = dict(primary="架空", reference="English Idol", source_url=JP, locator="個卡偶像表記")
        report = compare_card_sources(card(), reference, idol_alias=evidence)
        self.assertEqual(report["idol_alias"], evidence)
        for mapping in ({"en-root": "en-root", "en-leaf": "en-root"}, {"missing": "en-root"}, {"en-root": "missing"}):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                compare_card_sources(card(), card(), panel_map=mapping)

    def test_exact_levels_and_known_missing_are_distinguished(self):
        reference = replace(card(), levels={60: {"Da": 180}, 80: {"Da": 200, "Me": 150}}, max_levels={0: 60, 4: 80}, te_eligible=True)
        rows = {r["field"]: r for r in compare_card_sources(card(), reference)["comparisons"]}
        self.assertEqual(rows["levels.60"]["status"], "missing_primary")
        self.assertEqual(rows["levels.80.Me"]["status"], "unknown_primary")
        self.assertEqual(rows["max_levels.0"]["status"], "missing_primary")
        self.assertEqual(rows["te_eligible"]["status"], "unknown_primary")


if __name__ == "__main__":
    unittest.main()
