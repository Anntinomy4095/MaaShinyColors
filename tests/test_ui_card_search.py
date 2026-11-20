"""Card picker search (2026-10-26, user): Simplified idol names, aliases and space-separated words
(ui/components/name-fold.js, card-picker.js searchEntries, team-picker.js cardEntry). Offline; node only, with a few
synthetic cards (idol names + made-up titles) — no card list or private data."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_node(script):
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


# id, idol (as the game spells it), idol_en, rarity, specialty
CARDS = [
    ("2040010010", "櫻木真乃", "Mano Sakuragi", "SSR", "Vocal"),
    ("2040010020", "櫻木真乃", "Mano Sakuragi", "SR", "Dance"),
    ("2040020010", "風野灯織", "Hiori Kazano", "SR", "Visual"),
    ("2040030010", "八宮めぐる", "Meguru Hachimiya", "UR", "Dance (Vocal)"),
    ("2040050010", "田中摩美々", "Mamimi Tanaka", "SR", "Visual"),
    ("2040060010", "白瀬咲耶", "Sakuya Shirase", "SR", "Vocal"),
    ("2040080010", "幽谷霧子", "Kiriko Yukoku", "R", "Visual"),
    ("2040090010", "小宮果穂", "Kaho Komiya", "SR", "Vocal"),
    ("2040130010", "有栖川夏葉", "Natsuha Arisugawa", "SR", "Vocal"),
    ("2040140010", "大崎甘奈", "Amana Osaki", "SR", "Dance"),
    ("2040170010", "芹沢あさひ", "Asahi Serizawa", "SR", "Dance"),
    ("2040210010", "樋口円香", "Madoka Higuchi", "SR", "Vocal"),
    ("2040230010", "市川雛菜", "Hinana Ichikawa", "SR", "Dance"),
    ("2040260010", "斑鳩ルカ", "Luca Ikaruga", "SR", "Vocal"),
]
SCRIPT = """
import { cardEntry } from './ui/components/team-picker.js';
import { searchEntries } from './ui/components/card-picker.js';
import { FOLD_GROUPS } from './ui/components/name-fold.js';
const cards = %s.map(([id, idol, idol_en, rarity, specialty]) => ({ id, idol, idolEn: idol_en, title: 'T' + id.slice(-5), titleEn: '', rarity, specialty }));
const entries = cards.map((card) => cardEntry(card));
const hits = (query) => searchEntries(entries, query).map((e) => e.id);
const queries = %s;
console.log(JSON.stringify({ hits: Object.fromEntries(queries.map((q) => [q, hits(q)])), groups: FOLD_GROUPS }));
"""


def search(queries):
    return run_node(SCRIPT % (json.dumps(CARDS, ensure_ascii=False), json.dumps(queries, ensure_ascii=False)))["hits"]


def ids(*numbers):
    return sorted(c[0] for c in CARDS if c[0][3:6] in numbers)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class SimplifiedNameTests(unittest.TestCase):
    def test_simplified_and_shinjitai_names_hit_the_game_spelling(self):
        hits = search(["樱木真乃", "桜木", "风野灯织", "白濑咲耶", "摩美美", "摩美々", "雾子", "圆香", "雏菜", "夏叶", "果穗", "甘奈"])
        self.assertEqual(sorted(hits["樱木真乃"]), ids("001"))
        self.assertEqual(sorted(hits["桜木"]), ids("001"))
        self.assertEqual(hits["风野灯织"], ids("002"))
        self.assertEqual(hits["白濑咲耶"], ids("006"))
        self.assertEqual(hits["摩美美"], ids("005"))     # 々 repeats the character before it
        self.assertEqual(hits["摩美々"], ids("005"))
        self.assertEqual(hits["雾子"], ids("008"))
        self.assertEqual(hits["圆香"], ids("021"))
        self.assertEqual(hits["雏菜"], ids("023"))
        self.assertEqual(hits["夏叶"], ids("013"))
        self.assertEqual(hits["果穗"], ids("009"))
        self.assertEqual(hits["甘奈"], ids("014"))

    def test_the_game_spelling_still_hits_and_romaji_stays_searchable(self):
        hits = search(["櫻木", "霧子", "Sakuragi", "kiriko yukoku", "Yukoku Kiriko"])
        self.assertEqual(sorted(hits["櫻木"]), ids("001"))
        self.assertEqual(hits["霧子"], ids("008"))
        self.assertEqual(sorted(hits["Sakuragi"]), ids("001"))
        self.assertEqual(hits["kiriko yukoku"], ids("008"))
        self.assertEqual(hits["Yukoku Kiriko"], ids("008"))   # word order does not matter

    def test_width_case_and_kana(self):
        hits = search(["ＳＲ　樱木", "ルカ", "るか", "ﾙｶ", "MEGURU"])
        self.assertEqual(hits["ＳＲ　樱木"], ["2040010020"])
        self.assertEqual(hits["ルカ"], ids("026"))
        self.assertEqual(hits["るか"], ids("026"))
        self.assertEqual(hits["ﾙｶ"], ids("026"))            # half-width katakana
        self.assertEqual(hits["MEGURU"], ids("003"))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class WordSearchTests(unittest.TestCase):
    def test_every_word_must_match(self):
        hits = search(["真乃 SR", "SR 真乃", "真乃  ssr", "真乃 UR", "真乃 灯织", "真乃 dance", "樱木 真乃"])
        self.assertEqual(hits["真乃 SR"], ["2040010020"])       # SR is a whole word: not SSR
        self.assertEqual(hits["SR 真乃"], ["2040010020"])
        self.assertEqual(hits["真乃  ssr"], ["2040010010"])
        self.assertEqual(hits["真乃 UR"], [])
        self.assertEqual(hits["真乃 灯织"], [])                 # two different idols: AND finds nothing
        self.assertEqual(hits["真乃 dance"], ["2040010020"])
        self.assertEqual(sorted(hits["樱木 真乃"]), ids("001"))

    def test_unrelated_words_and_empty_query(self):
        hits = search(["不相关的词", "真乃 xyz", "", "   "])
        self.assertEqual(hits["不相关的词"], [])
        self.assertEqual(hits["真乃 xyz"], [])
        self.assertEqual(hits[""], [c[0] for c in CARDS])         # nothing typed: every entry stays
        self.assertEqual(hits["   "], [c[0] for c in CARDS])

    def test_whole_word_rarity_only(self):
        hits = search(["SR", "R", "UR", "s"])
        self.assertEqual(hits["SR"], [c[0] for c in CARDS if c[3] == "SR"])
        self.assertEqual(hits["R"], [c[0] for c in CARDS if c[3] == "R"])
        self.assertEqual(hits["UR"], ["2040030010"])
        self.assertIn("2040010010", hits["s"])                    # other short words stay plain substrings


@unittest.skipUnless(shutil.which("node"), "node not installed")
class AliasTests(unittest.TestCase):
    def test_unit_names_and_chinese_given_names(self):
        hits = search(["放课后", "放课后气候少女", "六出花", "安蒂卡", "illumination stars", "八宫巡", "巡", "斑鸠露卡", "露卡",
                       "ストレイライト", "芹泽朝日", "朝日"])
        self.assertEqual(hits["放课后"], ids("009", "013"))        # 放課後クライマックスガールズ
        self.assertEqual(hits["放课后气候少女"], ids("009", "013"))
        self.assertEqual(hits["六出花"], ids("014"))
        self.assertEqual(hits["安蒂卡"], ids("005", "006", "008"))   # アンティーカ
        self.assertEqual(sorted(hits["illumination stars"]), ids("001", "002", "003"))
        self.assertEqual(hits["八宫巡"], ids("003"))
        self.assertEqual(hits["巡"], ids("003"))
        self.assertEqual(hits["斑鸠露卡"], ids("026"))
        self.assertEqual(hits["露卡"], ids("026"))
        self.assertEqual(hits["ストレイライト"], ids("017"))        # ストレイライト, matched through the unit alias
        self.assertEqual(hits["芹泽朝日"], ids("017"))
        self.assertEqual(hits["朝日"], ids("017"))

    def test_kana_readings_and_attribute_words(self):
        hits = search(["さくらぎ まの", "まみみ", "舞蹈", "声乐 sr", "视觉 雾子", "ボーカル 放课后"])
        self.assertEqual(sorted(hits["さくらぎ まの"]), ids("001"))
        self.assertEqual(hits["まみみ"], ids("005"))
        self.assertEqual(hits["舞蹈"], [c[0] for c in CARDS if "Dance" in c[4]])
        self.assertEqual(hits["声乐 sr"], [c[0] for c in CARDS if "Vocal" in c[4] and c[3] == "SR"])
        self.assertEqual(hits["视觉 雾子"], ids("008"))
        self.assertEqual(hits["ボーカル 放课后"], ids("009", "013"))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class FoldTableTests(unittest.TestCase):
    def test_groups_are_consistent(self):
        groups = run_node(SCRIPT % ("[]", "[]"))["groups"]
        targets = set()
        variants = {}
        for group in groups:
            chars = list(group)
            self.assertGreaterEqual(len(chars), 2, group)
            targets.add(chars[0])
            for ch in chars[1:]:
                self.assertNotIn(ch, variants, f"{ch} folds twice")
                variants[ch] = chars[0]
        self.assertEqual(len(targets), len(groups))              # one group per game character
        self.assertFalse(targets & set(variants), "a variant must not also be a game character")
        self.assertGreaterEqual(len(groups), 150)

    def test_fold_is_idempotent_and_wiring_is_in_place(self):
        out = run_node("""
import { fold } from './ui/components/name-fold.js';
const samples = ['樱木真乃 SR', '田中摩美々', 'ＭＥＭちょ', 'ﾙｶ', '放课后气候少女', '々', ' 々a'];
console.log(JSON.stringify({ once: samples.map(fold), twice: samples.map((s) => fold(fold(s))) }));""")
        self.assertEqual(out["once"], out["twice"])
        self.assertEqual(out["once"][0], "櫻木真乃 sr")
        self.assertEqual(out["once"][1], "田中摩美美")
        self.assertEqual(out["once"][3], "るか")
        self.assertEqual(out["once"][5], "々")                      # nothing before it: left as is
        picker = (ROOT / "ui" / "components" / "card-picker.js").read_text(encoding="utf-8")
        self.assertIn("searchEntries([...entries, ...(showAll ? extra : [])], filter)", picker)
        self.assertNotIn(".includes(text)", picker)


if __name__ == "__main__":
    unittest.main()
