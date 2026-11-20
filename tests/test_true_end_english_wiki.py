"""Offline English-page fixtures; optional cached public pages never initiate requests."""
from hashlib import sha256
import json
from pathlib import Path
import unittest

from agent.core.true_end.english_wiki import EnglishCardPageParser, EnglishMechanicsParser
from agent.core.true_end.growth import panel_closure
from agent.core.true_end.models import Ownership, extract_card_features

URL = "https://shinycolors.wiki/wiki/S-SSR1_Test"
CACHE = Path(__file__).resolve().parents[1] / "dev_assets/private/true_end/english-wiki-20261006"


def fixture():
    return '''<div class="mw-parser-output"><ul><li>JP Card Name: 【合成】架空</li><li>Card Rarity: SSR</li></ul>
    <h2>Skill Panel<span>[edit | edit source]</span></h2><table><tbody>
    <tr><td rowspan="2">20SP</td><td colspan="2" id="root">Dance Limit UP</td></tr>
    <tr><td colspan="2"><table><tr><td><img src="https://image.shinycolors.wiki/Skill_Limit_Da.png"></td><td>Dance Limit +50</td></tr></table></td></tr>
    <tr><td rowspan="2">50SP</td><td colspan="2" id="leaf">Dance &amp; Mental Limit UP (3☆)</td></tr>
    <tr><td colspan="2"><table><tr><td>Dance &amp; Mental Limit +150</td></tr></table></td></tr>
    </tbody></table>
    <h2>Support Skills</h2><table><tr><th>Appeal Name</th><th>Description</th><th>Card LV</th></tr>
    <tr><td>Dance Appeal</td><td>Dance 3× Appeal</td><td>1-59</td></tr>
    <tr><td>Dance Appeal+</td><td>Dance 3.1× Appeal / Dance 5% UP [3 turns]</td><td>60</td></tr></table>
    <table><tr><th>Mastery</th><th>Description</th></tr>
    <tr><td>Dance Mastery SP</td><td>additional [LV×1] SP gain when training together in Dance room</td></tr>
    <tr><td>Dance Mastery Sta</td><td>recover [LV×3] stamina when training together in Dance room</td></tr></table>
    <table><tr><th rowspan="2">Support Skill</th><th colspan="3">Card LV / Skill LV</th></tr>
    <tr><th>1</th><th>60</th><th>80</th></tr>
    <tr><td>Dance Mastery SP</td><td>4</td><td>10</td><td>20</td></tr>
    <tr><td>Dance Mastery Sta</td><td></td><td>3</td><td>7</td></tr></table>
    <h2>Support Events</h2><table><tr><th>Event Name</th><th>Vocal</th><th>Dance</th><th>Visual</th><th>Mental</th><th>SP</th></tr>
    <tr><td>event</td><td>+5</td><td>+20</td><td></td><td>+10</td><td>+15</td></tr></table>
    <h2>Stats</h2><table><tr><th>Card LV</th><th>Vocal</th><th>Dance</th><th>Visual</th><th>Mental</th></tr>
    <tr><td>60 (0☆)</td><td>125</td><td>250</td><td>125</td><td>115</td></tr>
    <tr><td>80 (4☆)</td><td>163</td><td>325</td><td>163</td><td>150</td></tr></table></div>
    <script>JP Card Name: 【bad】Wrong; True End</script>'''


class EnglishWikiTests(unittest.TestCase):
    def test_nested_panels_sp_values_and_provenance(self):
        html = fixture()
        card = EnglishCardPageParser().parse(html, source_url=URL, card_id="bound-id")
        self.assertEqual((card.card_id, card.name, card.idol, card.kind), ("bound-id", "【合成】架空", "架空", "S"))
        self.assertEqual([(p.id, p.sp_cost, p.required_lb, p.effects) for p in card.panels],
                         [("root", 20, 0, {"Da": 50}), ("leaf", 50, 3, {"Da": 150, "Me": 150})])
        self.assertTrue(all(not p.prerequisites_known for p in card.panels))
        self.assertEqual(card.provenance["card"]["content_sha256"], sha256(html.encode()).hexdigest())
        source = card.provenance["panels.leaf"]["cells"]
        self.assertEqual(source[1]["dom_id"], "leaf")
        self.assertEqual(source[2]["colspan"], 2)
        self.assertIn("Skill_Limit_Da.png", card.provenance["panels.root"]["raw_effect"])
        features = extract_card_features(card, Ownership("bound-id", 4, 80))
        self.assertIsNone(panel_closure(features, "leaf"))

    def test_exact_sparse_levels_live_upgrade_and_event(self):
        card = EnglishCardPageParser().parse(fixture(), source_url=URL)
        self.assertEqual(card.levels[80], {"Vo": 163, "Da": 325, "Vi": 163, "Me": 150})
        self.assertNotIn(65, card.levels)
        self.assertEqual(card.max_levels, {0: 60, 4: 80})
        self.assertEqual(card.owned_live_skills[1].unlock_level, 60)
        self.assertEqual(card.owned_live_skills[1].buff_amount, .05)
        self.assertEqual(card.support_skills[0].levels, {1: 4, 60: 10, 80: 20})
        self.assertEqual(card.support_skills[1].kind, "mastery_stamina")
        self.assertEqual(card.events[0]["gains"], {"Vo": 5, "Da": 20, "Vi": 0, "Me": 10, "SP": 15})

    def test_missing_stats_and_level_caps_remain_unknown(self):
        html = fixture().replace("60 (0☆)", "60").replace("80 (4☆)", "80").replace("<td>325</td>", "<td>?</td>")
        card = EnglishCardPageParser().parse(html, source_url=URL)
        self.assertIsNone(card.levels[80]["Da"])
        self.assertIn("levels.80.Da", card.unknown_fields)
        # Since 2026-10-18 the verified SSR/SR/UR S-card cap rule fills the missing page caps, with its evidence;
        # the stat row itself is never derived.
        self.assertEqual(card.max_levels, {0: 60, 1: 65, 2: 70, 3: 75, 4: 80})
        self.assertEqual(card.provenance["max_levels"]["source"], "verified_rarity_rule")
        self.assertNotIn("max_levels", card.unknown_fields)
        # R has only an inferred rule: its missing caps stay unknown.
        low = EnglishCardPageParser().parse(html.replace("Card Rarity: SSR", "Card Rarity: R"), source_url=URL)
        self.assertEqual(low.max_levels, {})
        self.assertIn("max_levels", low.unknown_fields)

    def test_description_conflict_does_not_grant_sp(self):
        html = fixture().replace("[LV×1] SP gain", "[LV×1] Mental gain")
        card = EnglishCardPageParser().parse(html, source_url=URL)
        self.assertTrue(card.support_skills[0].unsupported)
        self.assertIn("Mental gain", card.support_skills[0].unsupported[0])

    def test_live_link_and_reactive_buff_are_not_unconditional(self):
        link = EnglishMechanicsParser.live("Vocal 3× Appeal / Link (w/ Madoka): Vocal 1.5× Appeal", "link")
        self.assertEqual(link["components"], {"Vo": 3})
        self.assertTrue(link["unsupported"])
        reactive = EnglishMechanicsParser.live("Dance 5x Appeal / Dance 30% UP [4 turns] per successful damage", "reactive")
        self.assertNotIn("buff_amount", reactive)
        self.assertTrue(reactive["unsupported"])

    def test_passive_conditions_require_complete_semantics(self):
        kind, effects, issues = EnglishMechanicsParser.panel("Dance 100% UP", "If: Turn count ≤ 3 Chance: 20% Maximum 1×", "p")
        self.assertEqual((kind, effects["end_turn"], effects["probability"], effects["max_activations"], issues),
                         ("passive", 3, .2, 1, []))
        for text in ("If: Chance: 20% Maximum 1×", "If: w/ Kaho Chance: 20% Maximum 1×", "Chance: 20%"):
            kind, effects, issues = EnglishMechanicsParser.panel("Dance 100% UP", text, "p")
            self.assertEqual(kind, "passive")
            self.assertTrue(effects["condition"])
            self.assertTrue(issues)

    def test_image_condition_and_event_lock_keep_raw_evidence(self):
        html = fixture().replace('Dance Limit UP</td>', 'Dance 180% UP (E)</td>', 1)
        html = html.replace('Dance Limit +50</td>', 'If: <img title="Dance UP" src="https://image.shinycolors.wiki/Live_Status_DaUp.png"><br>Chance: 20%<br>Maximum 2×</td>', 1)
        card = EnglishCardPageParser().parse(html, source_url=URL)
        panel = card.panels[0]
        self.assertEqual(panel.kind, "passive")
        self.assertIn("[image:Dance UP]", panel.effects["condition"])
        self.assertTrue(panel.unsupported)
        self.assertEqual(panel.event, "E")
        self.assertIn("panels.root.event_identity", card.unknown_fields)
        images = card.provenance["panels.root"]["cells"][2]["images"]
        self.assertTrue(any(i["src"].endswith("Live_Status_DaUp.png") for i in images))

    def test_only_explicit_dom_prerequisites_unlock_closure(self):
        html = fixture().replace("Dance Limit +50</td>", 'Dance Limit +50<div class="prerequisites">Prerequisites: None</div></td>')
        html = html.replace("Dance &amp; Mental Limit +150</td>", 'Dance &amp; Mental Limit +150<div class="prerequisites">Prerequisites: <a href="#root">root</a></div></td>')
        card = EnglishCardPageParser().parse(html, source_url=URL)
        features = extract_card_features(card, Ownership(card.card_id, 4, 80))
        closure = panel_closure(features, "leaf")
        self.assertEqual([p.id for p in closure], ["root", "leaf"])
        self.assertEqual(sum(p.sp_cost for p in closure), 70)
        broken = EnglishCardPageParser().parse(html.replace('href="#root"', 'href="#absent"'), source_url=URL)
        self.assertFalse(broken.panels[1].prerequisites_known)

    def test_p_choice_rows_and_card_specific_true_end_evidence(self):
        html = '''<ul><li>JP Card Name: 【合成】架空</li></ul><h2>Unique Produce Events</h2>
        <table><tr><th>Event Name</th><th>Choice</th><th>Vocal</th><th>Dance</th><th>Visual</th><th>Mental</th><th>SP</th></tr>
        <tr><td rowspan="2">event</td><td>choice-a</td><td>+20</td><td></td><td></td><td rowspan="2">+5</td><td rowspan="2">+10</td></tr>
        <tr><td>choice-b</td><td></td><td>+20</td><td></td></tr>
        <tr><td>ending</td><td>---</td><td colspan="5">True End</td></tr></table>'''
        card = EnglishCardPageParser().parse(html, source_url="https://shinycolors.wiki/wiki/P-SSR1_Test")
        self.assertTrue(card.te_eligible)
        self.assertEqual(len(card.events), 1)
        self.assertEqual([o["gains"]["SP"] for o in card.events[0]["options"]], [10, 10])
        self.assertEqual([o["gains"]["Me"] for o in card.events[0]["options"]], [5, 5])
        unknown = EnglishCardPageParser().parse(html.replace("True End", "Unknown ending"), source_url="https://shinycolors.wiki/wiki/P-SSR1_Test")
        self.assertIsNone(unknown.te_eligible)

    def test_public_card_url_and_kind_are_bound(self):
        for url in ("https://enza.fun/wiki/S-x", "https://shinycolors.wiki/wiki/File:x", URL + "?action=edit"):
            with self.assertRaises(ValueError):
                EnglishCardPageParser().parse(fixture(), source_url=url)
        with self.assertRaises(ValueError):
            EnglishCardPageParser().parse(fixture(), source_url=URL, kind="P")

    @unittest.skipUnless((CACHE / "manifest.json").is_file(), "可选英文公开缓存不在本机")
    def test_five_cached_public_pages_are_auditable_partial_records(self):
        for item in json.loads((CACHE / "manifest.json").read_text(encoding="utf-8-sig")):
            with self.subTest(title=item["title"]):
                raw = (CACHE / (item["title"] + ".html")).read_bytes()
                self.assertEqual(sha256(raw).hexdigest(), item["sha256"])
                card = EnglishCardPageParser().parse(raw.decode("utf-8"), source_url=item["url"], fetched_at=item["fetched_at"])
                self.assertEqual(card.status, "PARTIAL")
                self.assertEqual(len(card.panels), 10)
                self.assertEqual({p.sp_cost for p in card.panels}, {20, 30, 40, 50})
                self.assertTrue(all(not p.prerequisites_known for p in card.panels))
                self.assertEqual(card.provenance["card"]["content_sha256"], item["sha256"])
                if card.kind == "S":
                    self.assertEqual(set(card.levels), {1, 60, 80})
                    self.assertEqual(len(card.owned_live_skills), 6)
                    self.assertEqual(len(card.events), 3)
                else:
                    self.assertTrue(card.te_eligible)
                    self.assertGreaterEqual(len(card.events), 4)
                if item["title"] in ("S-SSR20_Tenka", "S-SSR21_Sakuya"):
                    self.assertTrue(next(s for s in card.support_skills if s.id == "Dance Mastery SP").unsupported)
                    locator = "Live_Status_Regen.png" if item["title"] == "S-SSR20_Tenka" else "Live_Status_DaUp.png"
                    self.assertTrue(any(locator in p["raw_effect"] for p in card.provenance.values() if "raw_effect" in p))
                    self.assertTrue(any(locator in p.effects.get("condition", "") and p.unsupported for p in card.panels))


if __name__ == "__main__":
    unittest.main()
