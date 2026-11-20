"""ShinyColorsDB -> True End CardData converter: mapping and preserved unknowns."""
import copy
import json
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from agent.core import shinycolorsdb_cards as conv
from agent.core.true_end.models import CardData, Ownership, extract_card_features

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "dev_assets" / "private" / "shinycolorsdb" / "snapshot-20261006"


def panel(slot, stype, title, desc, effects, lb=0, by_event=False, release=None, extra=None):
    return {"panelId": f"20400000000{slot:03d}", "panelSlot": slot, "panelIsGold": 0,
            "panelReleaseEvolution": lb, "panelReleaseByEvent": by_event, "panelReleaseDesc": release,
            "skillTitle": title, "skillDesc": desc, "skillType": stype, "skillEffects": effects,
            "extraEffect": extra}


def synthetic_s(card_type="S_SSR", method="GeneralGasha", offset=0):
    """Synthetic record in the DB shape (not copied game data)."""
    return {
        "enzaId": "2049990010", "cardUuid": "00000000-0000-0000-0000-000000000001", "cardName": "【テスト】偶像",
        "cardType": card_type, "getMethod": method, "panelSPOffset": offset, "cardHash": "abc",
        "releaseDate": "2026-01-01", "lastModified": "2026-01-01", "ideaMark": "dance",
        "idol": {"idolName": "偶像", "unitId": 2},
        "cardStatus": {"vocal": 100, "dance": 200, "visual": 100, "mental": 90},
        "cardPanels": [
            panel(11, "limit_break", "Dance上限UP", "Dance上限＋50",
                  [{"effectType": "limit_break", "attribute": "dance", "value": 50}]),
            panel(21, "active", "テスト", "Dance3.5倍アピール/Dance20％UP[3ターン]",
                  [{"effectType": "damage", "attribute": "dance", "value": 350, "turn": 1},
                   {"effectType": "appeal_up", "attribute": "dance", "value": 20, "turn": 3}], lb=1),
            panel(22, "passive", "Dance100％UP", "[条件:3ターン以前]\n[確率:20％]\n[最大:1回]", []),
            panel(31, "passive", "Dance180％UP/注目度30％UP", "[条件:DanceUPが付与されている場合]\n[確率:20％]\n[最大:2回]", [], lb=2),
            panel(32, "passive", "Dance30％UP", "[条件:2ターン以降]\n[確率:20％]\n[最大:2回]", [],
                  by_event=True, release="特定のサポートイベントで解放"),
            panel(33, "limit_break", "Dance上限UP", "Dance上限＋75",  # text disagrees with JSON
                  [{"effectType": "limit_break", "attribute": "dance", "value": 50}], lb=3),
            panel(41, "quick", "クイック", "Vocal100％UP[3ターン][コスト:3]", []),
            panel(42, "active", "テスト＋", "Excellent3倍アピール[興味無視]",
                  [{"effectType": "direct_damage", "attribute": "excellent", "value": 300}], lb=4,
                  extra={"extraEffectType": "link", "extraEffectDesc": "Excellent3倍アピール"}),
        ],
        "cardSupportSkills": [
            {"skillName": "アイドルの絆", "skillDesc": "プロデュース開始時に絆＋10", "skillType": "friendship",
             "skillAttribute": "none", "gainedAt": 5, "skillLevel": 2},
            {"skillName": "アイドルの絆", "skillDesc": "プロデュース開始時に絆＋25", "skillType": "friendship",
             "skillAttribute": "none", "gainedAt": 40, "skillLevel": 5},
            {"skillName": "ダンスマスタリーSP", "skillDesc": "一緒にダンスレッスンをするとSP＋4", "skillType": "tag_boost",
             "skillAttribute": "skill_point", "gainedAt": 1, "skillLevel": 4},
            {"skillName": "ダンスマスタリー体力", "skillDesc": "一緒にダンスレッスンをすると体力10回復",
             "skillType": "tag_stamina", "skillAttribute": "none", "gainedAt": 70, "skillLevel": 3},
            {"skillName": "体力サポート", "skillDesc": "9％の確率で体力消費量－50％", "skillType": "stamina_support",
             "skillAttribute": "none", "gainedAt": 45, "skillLevel": 3},
        ],
        "cardSupportEvents": [{"eventName": "イベント1", "eventAddVo": 0, "eventAddDa": 20, "eventAddVi": 0,
                               "eventAddMe": 10, "eventAddSp": 15}],
        "cardProficiencies": [{"proficiency": "feel_stable", "value": 4}],
        "supportFightSkills": {"ftComment": "発動タームでの最終ポイント値＋(18,21,25)％"},
    }


def synthetic_p():
    raw = synthetic_s()
    raw.update(enzaId="1049990010", cardType="P_SSR", cardStatus={"vocal": 50, "dance": 50, "visual": 50, "mental": 50},
               cardMemoryAppeals=[{"memoryId": "5", "memoryTitle": "m", "memoryDesc": "思い出アピール[Lv1]",
                                   "memoryEffects": [{"effectType": "memory_appeal_damage", "attribute": "vocal", "value": 80}]}],
               cardProduceAptitude=[{"aptClass": "environmentAptitudes", "aptType": "rain", "aptStepName": "SS"}],
               cardIdolEvents=[{"eventId": 1, "eventCategory": "アイドルイベント", "eventTitle": "t"}],
               memoryChargeSkills=[])
    for key in ("cardSupportSkills", "cardSupportEvents", "cardProficiencies", "supportFightSkills"):
        raw.pop(key)
    return raw


class ConvertSyntheticTest(unittest.TestCase):
    def setUp(self):
        self.card = conv.convert_card(synthetic_s())
        self.model = CardData.from_dict(self.card)
        self.panels = {p.id: p for p in self.model.panels}

    def test_identity_and_loadable(self):
        self.assertEqual((self.model.card_id, self.model.kind, self.model.status), ("2049990010", "S", "PARTIAL"))
        self.assertEqual(self.model.unit, "アンティーカ")
        self.assertEqual(self.model.rarity, "S_SSR")

    def test_status_is_max_level_only_and_unknowns_kept(self):
        self.assertEqual(dict(self.model.levels), {80: {"Vo": 100.0, "Da": 200.0, "Vi": 100.0, "Me": 90.0}})
        # Not listed statically: the feature extractor derives these gaps for the owned level itself.
        for key in ("levels.per_level", "max_levels", "owned_live_skills"):
            self.assertNotIn(key, self.model.unknown_fields)
        self.assertEqual(self.model.owned_live_skills, ())
        feats = extract_card_features(self.model, Ownership("2049990010", 4, 80))
        self.assertFalse(feats.ready)  # still blocked, never treated as zero
        self.assertNotIn("max_levels", feats.unknowns)
        self.assertEqual(self.card["max_levels"], {})  # The API did not supply a cap table.
        self.assertEqual(self.model.provenance["max_levels"]["source"], "verified_rarity_rule")
        self.assertIn("owned_live_skills", feats.unknowns)
        gaps = self.card["readiness"]["critical_gaps"]
        self.assertNotIn("max_levels", gaps["active"])
        self.assertIn("owned_live_skills", gaps["active"])
        self.assertNotIn("owned_live_skills", gaps["guest"])
        ur = CardData.from_dict(conv.convert_card(synthetic_s(card_type="S_UR")))
        self.assertEqual(list(ur.levels), [90])  # Wiki Lv90 row == cardStatus for the owned UR
        sr = CardData.from_dict(conv.convert_card(synthetic_s(card_type="S_R")))
        self.assertEqual(dict(sr.levels), {})

    def test_sp_cost_from_row_and_never_zero(self):
        self.assertEqual({k: p.sp_cost for k, p in self.panels.items()},
                         {"scdb-11": 20, "scdb-21": 30, "scdb-22": 30, "scdb-31": 40, "scdb-32": 40,
                          "scdb-33": 40, "scdb-41": 50, "scdb-42": 50})
        self.assertTrue(all(not p.prerequisites_known and p.prerequisites == () for p in self.model.panels))
        self.assertIsNone(self.model.panel_topology)
        bad = synthetic_s()
        bad["cardPanels"][0]["panelSlot"] = 7  # outside the known grid
        card = conv.convert_card(bad)
        self.assertNotIn("scdb-7", [p["id"] for p in card["panels"]])
        self.assertIn("panels.scdb-7", card["unknown_fields"])
        neg = conv.convert_card(synthetic_s(offset=-20))  # 20 - 20 = 0 must not become a free panel
        self.assertNotIn("scdb-11", [p["id"] for p in neg["panels"]])

    def test_contested_sp_flags(self):
        # Outside the verified rule: a non-zero DB offset on an ordinary card, or a Twilight card without a date.
        undated = synthetic_s(method="TwilightCollection")
        undated["releaseDate"] = None
        for raw in (synthetic_s(offset=-10), undated):
            card = conv.convert_card(raw)
            self.assertIn("panels.scdb-11.sp_cost", card["unknown_fields"])
        self.assertFalse(any(k.endswith(".sp_cost") for k in self.model.unknown_fields))

    def test_version_rule_for_twilight_and_ur(self):
        """P-Twilight 2025+ and S-Twilight 2026+ cost 10 less; earlier Twilight normal; UR -10."""
        def costs(card_type, method, date, offset=0):
            raw = synthetic_s(card_type=card_type, method=method, offset=offset)
            raw.update(releaseDate=date, enzaId=("1" if card_type.startswith("P") else "2") + "049990099")
            card = conv.convert_card(raw)
            self.assertFalse(any(k.endswith(".sp_cost") for k in card["unknown_fields"]), (card_type, date))
            self.assertIn("USER_RULE_2026-10-07", card["provenance"]["panels"]["sp_cost"])
            return sorted({p["sp_cost"] for p in card["panels"]})
        self.assertEqual(costs("S_SSR", "TwilightCollection", "2026-01-01"), [10, 20, 30, 40])
        self.assertEqual(costs("S_SSR", "TwilightCollection", "2025-12-31"), [20, 30, 40, 50])
        self.assertEqual(costs("P_SSR", "TwilightCollection", "2025-01-01"), [10, 20, 30, 40])
        self.assertEqual(costs("P_SSR", "TwilightCollection", "2024-12-31"), [20, 30, 40, 50])
        self.assertEqual(costs("S_UR", "GeneralGasha", "2025-04-18", offset=-10), [10, 20, 30, 40])
        self.assertEqual(costs("S_UR", "GeneralGasha", "2025-04-18", offset=0), [10, 20, 30, 40])

    def test_rule_agrees_with_every_in_game_reading(self):
        """The four Twilight/UR cards read in game must get the same offset from the version rule alone."""
        verified = {"2040060210": ("S_SSR", "TwilightCollection", "2026-08-19", 0),
                    "2040040090": ("S_SSR", "TwilightCollection", "2023-02-19", 0),
                    "2040070100": ("S_SSR", "TwilightCollection", "2023-08-21", 0),
                    "2050070010": ("S_UR", "GeneralGasha", "2025-04-18", -10)}
        for enza, (ctype, method, date, db) in verified.items():
            raw = {"enzaId": "2049990098", "cardType": ctype, "getMethod": method, "releaseDate": date,
                   "panelSPOffset": db}
            rule = conv.sp_offset(raw)
            reading = conv.sp_offset(dict(raw, enzaId=enza))
            self.assertEqual(rule[0], reading[0], enza)
            if enza in conv.SP_OFFSET_VERIFIED:  # 2040040090's reading is row-bound (SP_INGAME_VERIFIED)
                self.assertIn("IN_GAME_VERIFIED", reading[2], enza)

    def test_ingame_reading_clears_twilight_only_when_rows_match(self):
        raw = synthetic_s(method="TwilightCollection")
        raw["releaseDate"] = None  # outside the version rule: only the reading can clear it
        raw["cardPanels"] = [p for p in raw["cardPanels"] if p["skillType"] in ("limit_break", "active", "passive")]
        rows = {1: ("cap",), 2: ("live", "passive"), 3: ("passive", "passive", "cap"), 4: ("live",)}
        from unittest import mock
        with mock.patch.dict(conv.SP_INGAME_VERIFIED, {"2049990010": {"rows": rows, "evidence": "test frame"}}):
            card = conv.convert_card(raw)
            self.assertFalse(any(k.endswith(".sp_cost") for k in card["unknown_fields"]))
            self.assertIn("IN_GAME_VERIFIED test frame", card["provenance"]["panels"]["sp_cost"])
            wrong = {**rows, 2: ("cap", "passive")}  # binding to the wrong card must not clear the flag
            conv.SP_INGAME_VERIFIED["2049990010"] = {"rows": wrong, "evidence": "test frame"}
            card = conv.convert_card(raw)
            self.assertIn("panels.scdb-11.sp_cost", card["unknown_fields"])
            self.assertNotIn("IN_GAME_VERIFIED", card["provenance"]["panels"]["sp_cost"])
            offset = conv.convert_card(dict(raw, panelSPOffset=-10))  # a reading of the 0-offset rule never covers -10
            self.assertNotIn("IN_GAME_VERIFIED", offset["provenance"]["panels"]["sp_cost"])

    def test_user_verified_offset_applies_to_that_card_only(self):
        raw = synthetic_s(method="TwilightCollection")
        from unittest import mock
        record = {"offset": -10, "db_offset": 0, "evidence": "IN_GAME_VERIFIED: test"}
        with mock.patch.dict(conv.SP_OFFSET_VERIFIED, {"2049990010": record}, clear=True):
            card = conv.convert_card(raw)
            self.assertEqual(sorted({p["sp_cost"] for p in card["panels"]}), [10, 20, 30, 40])
            self.assertFalse(any(k.endswith(".sp_cost") for k in card["unknown_fields"]))
            self.assertIn("IN_GAME_VERIFIED", card["provenance"]["panels"]["sp_cost"])
            drift = conv.convert_card(dict(raw, panelSPOffset=-10))  # DB changed since the reading: stay contested
            self.assertIn("panels.scdb-11.sp_cost", drift["unknown_fields"])
            other = conv.convert_card(dict(raw, enzaId="2049990020"))  # other card: the version rule, not the reading
            self.assertNotIn("IN_GAME_VERIFIED", other["provenance"]["panels"]["sp_cost"])
            self.assertIn("USER_RULE_2026-10-07", other["provenance"]["panels"]["sp_cost"])
        self.assertEqual(conv.SP_OFFSET_VERIFIED["2040060210"]["offset"], -10)
        self.assertNotIn("2040040090", conv.SP_OFFSET_VERIFIED)  # 2023 Twilight reading: ordinary rule
        self.assertEqual(conv.SP_OFFSET_VERIFIED["2050070010"]["offset"], -10)  # UR
        self.assertEqual(conv.SP_OFFSET_VERIFIED["2040070100"]["offset"], 0)    # 2023 Twilight

    def test_cap_live_passive_mapping(self):
        self.assertEqual((self.panels["scdb-11"].kind, dict(self.panels["scdb-11"].effects)), ("cap", {"Da": 50.0}))
        live = self.panels["scdb-21"]
        self.assertEqual((live.kind, live.required_lb), ("live", 1))
        self.assertEqual(live.effects["components"], {"Da": 3.5})
        self.assertEqual((live.effects["buff_attribute"], live.effects["buff_amount"], live.effects["buff_turns"]),
                         ("Da", 0.2, 3))
        simple = self.panels["scdb-22"]
        self.assertEqual(dict(simple.effects), {"id": "scdb-22", "attribute": "Da", "buff": 1.0, "probability": 0.2,
                                                "max_activations": 1, "end_turn": 3})
        self.assertEqual(simple.unsupported, ())
        complex_ = self.panels["scdb-31"]
        self.assertEqual(complex_.kind, "passive")
        self.assertTrue(complex_.unsupported)
        self.assertEqual(self.panels["scdb-32"].event, conv.EVENT_UNSPECIFIED)

    def test_uninterpretable_panels_stay_unknown(self):
        self.assertEqual(self.panels["scdb-33"].kind, "unknown")  # text/JSON disagreement
        self.assertEqual(self.panels["scdb-41"].kind, "unknown")  # quick skill

    def test_excellent_appeal_panel_needs_json_confirmation(self):
        excellent = self.panels["scdb-42"]  # "Excellent3倍アピール[興味無視]", JSON attribute "excellent" value 300
        self.assertEqual(excellent.kind, "live")
        self.assertEqual((dict(excellent.effects["components"]), excellent.effects["excellent"]), ({}, 3.0))
        self.assertTrue(any("興味無視" in u for u in excellent.effects["unsupported"]))  # interest-ignore unmodeled
        self.assertTrue(any("extraEffect link" in u for u in excellent.effects["unsupported"]))
        raw = synthetic_s()
        raw["cardPanels"][-1]["skillEffects"][0]["value"] = 250  # JSON disagrees with the text
        card = CardData.from_dict(conv.convert_card(raw))
        self.assertEqual({p.id: p.kind for p in card.panels}["scdb-42"], "unknown")

    def test_live_buff_must_match_json(self):
        raw = synthetic_s()
        raw["cardPanels"][1]["skillEffects"][1]["value"] = 30
        card = CardData.from_dict(conv.convert_card(raw))
        self.assertEqual({p.id: p.kind for p in card.panels}["scdb-21"], "unknown")

    def test_support_skills_and_events(self):
        skills = {s.id: s for s in self.model.support_skills}
        self.assertEqual(dict(skills["アイドルの絆"].levels), {5: 2.0, 40: 5.0})
        self.assertEqual((skills["アイドルの絆"].kind, skills["アイドルの絆"].unsupported), ("bond", ()))
        self.assertEqual((skills["ダンスマスタリーSP"].kind, skills["ダンスマスタリーSP"].attribute,
                          skills["ダンスマスタリーSP"].room), ("mastery_sp", "SP", "Da"))
        # 10 != 3 x 3: description disagreement is preserved, not silently trusted
        self.assertTrue(skills["ダンスマスタリー体力"].unsupported)
        self.assertTrue(skills["体力サポート"].unsupported)
        raw = synthetic_s()
        raw["cardSupportSkills"].append({"skillName": "パーフェクトマスタリー", "skillDesc": "スキル発動時に…パーフェクトが発生",
                                         "skillType": "perfect_mastery", "skillAttribute": "none",
                                         "gainedAt": 1, "skillLevel": 1})
        named = {s["id"]: s for s in conv.convert_card(raw)["support_skills"]}["パーフェクトマスタリー"]
        self.assertEqual(named["kind"], "unmodeled_perfect_mastery")
        self.assertTrue(named["unsupported"])
        # 2026-10-25: ユニットマスタリー体力 is unit stamina (Lv×3 per member), not an attribute-less stat mastery.
        raw = synthetic_s()
        raw["cardSupportSkills"] += [{"skillName": "ユニットマスタリー体力", "skillType": "unit_mastery",
                                      "skillDesc": f"行動した場所に自分以外のユニットメンバーがいると1人につき体力{3 * lv}回復",
                                      "gainedAt": at, "skillLevel": lv} for at, lv in ((1, 1), (50, 3))]
        unit = {s["id"]: s for s in conv.convert_card(raw)["support_skills"]}["ユニットマスタリー体力"]
        self.assertEqual((unit["kind"], unit["attribute"], unit["room"], unit["unsupported"]),
                         ("unit_mastery_stamina", None, None, []))
        self.assertEqual(self.model.events[0]["stats"], {"Vo": 0, "Da": 20, "Vi": 0, "Me": 10, "SP": 15})
        self.assertEqual(self.card["scdb"]["fight_skill"], "発動タームでの最終ポイント値+(18,21,25)%")

    def test_p_card_unknowns_and_extras(self):
        card = conv.convert_card(synthetic_p())
        model = CardData.from_dict(card)
        self.assertEqual((model.kind, dict(model.levels), model.support_skills, model.events), ("P", {}, (), ()))
        readiness = card["readiness"]
        for key in ("initial_stats", "produce_events", "memory_appeal"):  # not consumed by True End
            self.assertNotIn(key, model.unknown_fields)
            self.assertIn(key, readiness["non_critical_gaps"])
        self.assertIn("te_eligible", readiness["critical_gaps"]["p"])
        self.assertFalse(readiness["ready_as_p"])
        self.assertIsNone(model.te_eligible)
        self.assertEqual(card["scdb"]["aptitude"], {"environmentAptitudes.rain": "SS"})
        self.assertEqual(card["scdb"]["memory_appeals"][0]["effects"][0]["value"], 80)

    def test_memory_boost_variant_does_not_collide(self):
        raw = synthetic_p()
        dup = copy.deepcopy(raw["cardPanels"][1])
        dup.update(skillTitle="[MB]テスト", panelReleaseDesc="特訓4回目、メモリーブースト2/5で解放")
        raw["cardPanels"].append(dup)
        model = CardData.from_dict(conv.convert_card(raw))
        mb = {p.id: p for p in model.panels}["scdb-21-mb"]
        self.assertEqual(mb.kind, "unknown")

    def test_rejects_non_card(self):
        with self.assertRaises(ValueError):
            conv.convert_card({"enzaId": "x"})


class SnapshotTest(unittest.TestCase):
    def test_checksum_drift_refused_and_output_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = json.dumps(synthetic_s(), ensure_ascii=False).encode()
            (Path(tmp) / "s.json").write_bytes(body)
            manifest = {"entries": [{"file": "s.json", "sha256": sha256(body).hexdigest(), "url": "u",
                                     "retrieved_at": "t"}]}
            (Path(tmp) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            payload = conv.convert_snapshot(Path(tmp))
            self.assertEqual(payload["parser_version"], conv.PARSER_VERSION)
            self.assertEqual(payload["cards"][0]["provenance"]["panels"]["content_sha256"], sha256(body).hexdigest())
            (Path(tmp) / "s.json").write_bytes(body + b" ")
            with self.assertRaises(ValueError):
                conv.convert_snapshot(Path(tmp))


@unittest.skipUnless((SNAPSHOT / "manifest.json").is_file(), "private pilot snapshot not present")
class SavedPilotSnapshotTest(unittest.TestCase):
    """Replays the user's pilot snapshot (private, gitignored)."""

    @classmethod
    def setUpClass(cls):
        cls.payload = conv.convert_snapshot(SNAPSHOT)
        cls.cards = {c["card_id"]: CardData.from_dict(c) for c in cls.payload["cards"]}

    def test_all_cards_load(self):
        self.assertGreaterEqual(len(self.cards), 5)
        self.assertTrue(all(c.status == "PARTIAL" for c in self.cards.values()))

    def test_no_panel_is_free_or_silently_complete(self):
        for card in self.cards.values():
            self.assertTrue(all(p.sp_cost > 0 for p in card.panels))
            r = next(c for c in self.payload["cards"] if c["card_id"] == card.card_id)["readiness"]
            self.assertFalse(r["ready_as_active"] or r["ready_as_p"])  # Missing Live / TE eligibility still blocks those roles.
            if r["ready_as_guest"]:
                self.assertEqual(r["level_cap_rule"]["source"], "verified_rarity_rule")

    def test_wiki_cross_checked_values(self):
        kaho = self.cards.get("2040090090")
        if kaho is None:
            self.skipTest("Kaho not in snapshot")
        self.assertEqual(dict(kaho.levels[80]), {"Vo": 225.0, "Da": 172.0, "Vi": 172.0, "Me": 181.0})
        self.assertEqual(sorted(p.sp_cost for p in kaho.panels), [20, 30, 30, 40, 40, 40, 50, 50, 50, 50])
        bond = {s.id: s for s in kaho.support_skills}["アイドルの絆"]
        self.assertEqual(dict(bond.levels), {5: 2.0, 20: 3.0, 40: 4.0, 60: 6.0, 80: 10.0})


OWNED = ROOT / "dev_assets" / "private" / "shinycolorsdb" / "snapshot-20261006-owned"


@unittest.skipUnless((OWNED / "manifest.json").is_file(), "private owned-card snapshot not present")
class SavedOwnedSnapshotTest(unittest.TestCase):
    """Binds the in-game skill-panel readings to the user's owned-card snapshot (private, gitignored)."""

    @classmethod
    def setUpClass(cls):
        cls.payload = conv.convert_snapshot(OWNED)
        cls.cards = {c["card_id"]: c for c in cls.payload["cards"]}

    def test_every_ingame_reading_matches_its_db_rows(self):
        present = [cid for cid in conv.SP_INGAME_VERIFIED if cid in self.cards]
        self.assertTrue(present)
        for cid in present:
            card = self.cards[cid]
            self.assertIn("IN_GAME_VERIFIED", card["provenance"]["panels"]["sp_cost"], cid)
            self.assertFalse([k for k in card["unknown_fields"] if k.endswith(".sp_cost")], cid)
            row_costs = {p["sp_cost"] for p in card["panels"]}
            self.assertEqual(row_costs, {20, 30, 40, 50}, cid)

    def test_twilight_and_ur_follow_the_version_rule(self):
        for card in self.cards.values():
            if card["scdb"]["get_method"] != "TwilightCollection" and not card["rarity"].endswith("_UR"):
                continue
            self.assertFalse([k for k in card["unknown_fields"] if k.endswith(".sp_cost")], card["card_id"])
            low = card["rarity"].endswith("_UR") or card["implementation_date"] >= conv.TWILIGHT_LOW_SP_FROM[card["kind"]]
            first = min(p["sp_cost"] for p in card["panels"])
            self.assertEqual(first, 10 if low else 20, card["card_id"])

    def test_user_verified_sakuya_costs_ten_less(self):
        card = self.cards.get("2040060210")
        if card is None:
            self.skipTest("card not in snapshot")
        self.assertEqual(sorted({p["sp_cost"] for p in card["panels"]}), [10, 20, 30, 40])
        self.assertEqual(card["scdb"]["panel_sp_offset"], 0)  # the DB field itself does not explain it

    def test_topology_families_cover_all_but_anomalies(self):
        families = {}
        for card in self.cards.values():
            families.setdefault(card["scdb"]["topology_family"], []).append(card)
            topo = card["panel_topology"]
            if topo is not None:
                model = CardData.from_dict(card)  # complete topology must connect every panel to the root
                self.assertEqual(model.panel_topology["roots"], ("scdb-11",))
        self.assertTrue({"tri10"} <= set(families))
        for family, cards in families.items():
            emitted = {c["panel_topology"] is not None for c in cards}
            self.assertEqual(emitted, {not family.startswith("anomaly")}, family)
        self.assertLessEqual(sum(len(v) for k, v in families.items() if k.startswith("anomaly")), 5)

    def test_all_cards_load_partial(self):
        for card in self.cards.values():
            model = CardData.from_dict(card)
            self.assertEqual(model.status, "PARTIAL")
            self.assertTrue(all(p.sp_cost > 0 for p in model.panels))


if __name__ == "__main__":
    unittest.main()
