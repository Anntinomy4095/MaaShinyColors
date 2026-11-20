"""ノウハウブック library (agent/core/knowhow_books.py) and the ノウハウ page helpers (ui/components/knowhow-data.js).
Offline, synthetic data only; the private reference JPEG check is skipped when the file is absent."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from agent.core import knowhow_books as kb
from agent.core import produce_end as pe
from agent.core import wing_screen as ws

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
FIXTURE = UI / "fixtures" / "knowhow-demo.js"
REFERENCE = ROOT / "dev_assets" / "private" / "sidebrowser-knowhow-20261008" / "picker_sorted_by_eval_value.jpg"
NODE = shutil.which("node")


def fixture():
    text = FIXTURE.read_text(encoding="utf-8")
    return json.loads(text[text.index("export default ") + len("export default "):text.rindex(";")])


def book(title, stats, rows, ev=None, count=None, marked=None):
    return kb.KnowhowBookRecord.from_dict({"title": title, "stats": dict(zip(kb.STATS, stats)), "eval_value": ev, "marked": marked,
                                           "rows": [{"name": n, "level": lv} for n, lv in rows],
                                           "knowhow_count": len(rows) if count is None else count})


def run_node(body, data=None):
    script = ("const data = JSON.parse(process.argv[1]);\n"
              "const m = await import(new URL('ui/components/knowhow-data.js', 'file:///' + process.cwd().replace(/\\\\/g, '/') + '/'));\n"
              + body)
    result = subprocess.run([NODE, "--input-type=module", "-e", script, json.dumps(data, ensure_ascii=False)],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class ModelTests(unittest.TestCase):
    def test_names_and_wanted_sanitising(self):
        self.assertEqual(kb.normalize_name("メンタルUP＋ "), "メンタルUP+")
        self.assertEqual(kb.normalize_name("アピールUP（思い出高）"), "アピールUP(思い出高)")
        for bad in ("", "a/b", "C:x", 'a"b', "x" * 41, "a\x01b"):
            self.assertIsNone(kb.clean_name(bad), bad)
        items = kb.clean_wanted(["パーフェクトリィ", {"name": "パーフェクトリィ"}, {"name": "Vocal上限", "match": "prefix", "min_level": 9,
                                 "purpose": "spend", "position": "top"}, {"name": "../x"}, 5])
        self.assertEqual([i.to_dict() for i in items], [
            {"name": "パーフェクトリィ", "match": "exact", "min_level": 1, "purpose": "any"},
            {"name": "Vocal上限", "match": "prefix", "min_level": 1, "purpose": "any"}])
        self.assertEqual(len(kb.clean_wanted([{"name": f"n{i}"} for i in range(100)])), kb.MAX_WANTED)

    def test_match_kinds(self):
        self.assertTrue(kb.WantedItem("Vocal上限", "prefix").matches_name("Vocal上限+++"))
        self.assertFalse(kb.WantedItem("Vocal上限", "exact").matches_name("Vocal上限+"))
        self.assertTrue(kb.WantedItem("適正", "contains").matches_name("センター適正◎"))

    def test_fingerprint_identity_and_partial(self):
        a = book("【x】偶像A", (1, 2, 3, 4), [("A", 1), ("B", 2)])
        b = book("【x】偶像A", (1, 2, 3, 4), [("B", 2), ("A", 1)])
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertTrue(a.fingerprint.startswith("b-"))
        self.assertEqual(a.idol, "偶像A")
        self.assertNotEqual(a.fingerprint, book("【x】偶像A", (1, 2, 3, 4), [("A", 2), ("B", 2)]).fingerprint)
        self.assertTrue(kb.KnowhowBookRecord(rows=(kb.KnowhowRow("A", 1),)).fingerprint.startswith("p-"))
        self.assertTrue(a.complete)
        self.assertFalse(book("t", (1, 1, 1, 1), [("A", 1)], count=3).complete)

    def test_inventory_upsert_duplicate_and_roundtrip(self):
        inv = kb.KnowhowInventory()
        a = book("【x】偶像A", (1, 2, 3, 4), [("A", 1)], ev=100)
        fp = inv.upsert(a)
        inv.upsert(book("【x】偶像A", (1, 2, 3, 4), [("A", 1)], marked=True))
        self.assertEqual(inv.books[fp].count, 1)
        self.assertEqual((inv.books[fp].eval_value, inv.books[fp].marked), (100, True))
        inv.upsert(a, duplicate=True)
        self.assertEqual(inv.books[fp].count, 2)
        again = kb.KnowhowInventory.from_dict(json.loads(json.dumps(inv.to_dict(), ensure_ascii=False)))
        self.assertEqual(again.books[fp].count, 2)
        self.assertEqual(again.books[fp].rows, a.rows)

    def test_private_paths_only(self):
        with self.assertRaises(ValueError):
            kb.save_inventory(kb.KnowhowInventory(), ROOT / "docs" / "x.json")
        with self.assertRaises(ValueError):
            kb.export_ui_module(kb.KnowhowInventory(), ROOT / "ui" / "components" / "x.js")

    def test_produce_end_ingest_and_reconcile(self):
        # 獲得報酬 ノウハウブック tab geometry used by wing_screen.parse_reward_books (third demo #658).
        texts = [("ノウハウ", (420, 230, 80, 20)), ("パーフェクトリィ", (240, 290, 160, 20)), ("Lv.4", (560, 290, 40, 20)),
                 ("スタートダッシュ", (240, 340, 160, 20)), ("Lv.5", (560, 340, 40, 20)), ("Vocal+20", (240, 365, 80, 16))]
        reading = ws.read_end("Reward", texts + [("スタミナ", (1120, 20, 60, 20))])
        part = kb.book_from_produce_end(reading, frame="frame-1.png")
        self.assertEqual([(r.name, r.level) for r in part.rows], [("パーフェクトリィ", 4), ("スタートダッシュ", 5)])
        self.assertEqual(part.source, "produce_end")
        self.assertIn("title", part.unknowns)
        summary = pe.ProduceEndSummary(knowhow_books=(pe.KnowhowBook("パーフェクトリィ", 4), pe.KnowhowBook("スタートダッシュ", 5)))
        self.assertEqual(kb.book_from_produce_end(summary).rows, part.rows)
        self.assertIsNone(kb.book_from_produce_end({"knowhow_books": None}))
        inv = kb.KnowhowInventory()
        fp = inv.upsert(book("【x】偶像C", (1, 1, 1, 1), [("スタートダッシュ", 5), ("パーフェクトリィ", 4)]))
        inv.add_pending(part)
        payload = kb.ui_payload(inv)
        self.assertEqual([[r["name"] for r in b["rows"]] for b in payload["pending_books"]],
                         [["パーフェクトリィ", "スタートダッシュ"]])
        self.assertEqual(payload["scan"]["pending"], 1)
        self.assertEqual(inv.reconcile_pending(), [(part, fp)])
        self.assertEqual(inv.pending, [])
        self.assertEqual(kb.ui_payload(inv)["pending_books"], [])

    def test_newest_scan_resolves_a_garbled_reward_reading(self):
        # live 2026-11-15 say "Halo": the 獲得報酬 pane showed 3 of 21 rows, read the title as 「巡り行くく」 and cut a long
        # row name; the newest-books scan's copy must still take the pending reading away (it stayed in 新获得 forever).
        stats = {"vo": 70, "da": 50, "vi": 60, "me": 10}
        rows = [("DanceUP", 1), ("今がキラキラ!おもいッきりアイドル!O", 5), ("おはよう街角の朝ごはん(Vo)", 4), ("施設効率相談(say\"Halo\")", 4)]
        full = book("【きみと巡り行く】櫻木真乃", tuple(stats.values()), rows)
        other = book("【きみと巡り行く】櫻木真乃", (60, 50, 40, 10), rows)
        part = kb.book_from_reward({"title": "【きみと巡り行くく】櫻木真乃", "idol": "櫻木真乃", "stats": stats,
                                    "rows": [{"name": "DanceUP", "level": 1}, {"name": "おもいッきりア今る", "level": 5},
                                             {"name": "おはよう街角の朝ごはん(Vo)", "level": 4}], "rows_partial": False})
        inv = kb.KnowhowInventory()
        fp, fp_other = inv.upsert(full), inv.upsert(other)
        inv.add_pending(part)
        self.assertEqual(inv.reconcile_pending(), [])                       # a full-library pass stays exact
        self.assertEqual(inv.reconcile_pending(candidates=[fp_other]), [])  # stats differ
        self.assertEqual(inv.reconcile_pending(candidates=[fp, fp_other]), [(part, fp)])
        wrong = kb.book_from_reward({**part.to_dict(), "title": "【きみと巡り行く】櫻木真乃",
                                     "rows": [{"name": "DanceUP", "level": 2}]})
        inv.add_pending(wrong)
        self.assertEqual(inv.reconcile_pending(candidates=[fp]), [])        # the level must match

    def test_panel_shows_confidently_snapped_names_and_keeps_the_reading(self):
        inv = kb.KnowhowInventory()
        inv.upsert(kb.KnowhowBookRecord.from_dict({"title": "【x】偶像D", "stats": dict(zip(kb.STATS, (1, 1, 1, 1))), "knowhow_count": 2,
                                                  "rows": [{"name": "歌姬楽宴(Vo)", "level": 5, "effect": "Vocal+20"},
                                                           {"name": "謎のノウハウ", "level": 1}]}))
        rows = kb.ui_payload(inv)["books"][0]["rows"]
        self.assertEqual([(r["name"], r.get("raw")) for r in rows], [("歌姫楽宴(Vo)", "歌姬楽宴(Vo)"), ("謎のノウハウ", None)])
        self.assertEqual([r.name for r in next(iter(inv.books.values())).rows], ["歌姬楽宴(Vo)", "謎のノウハウ"])   # inventory unchanged

    def test_grid_coverage_record_is_only_complete_when_proven(self):
        inv = kb.KnowhowInventory(owned_total=3)
        self.assertEqual((kb.ui_payload(inv)["scan"]["grid_complete"], kb.ui_payload(inv)["scan"]["grid"]), (False, None))
        inv.grid = {"complete": True, "basis": "3 屏重叠", "values": [1020, 1000, 1000], "owned_total": 3,
                    "sort": kb.SORT_EVAL, "direction": "desc"}
        scan = kb.ui_payload(inv)["scan"]
        self.assertTrue(scan["grid_complete"])
        self.assertEqual(scan["grid"]["values"], [1020, 1000, 1000])
        again = kb.KnowhowInventory.from_dict(json.loads(json.dumps(inv.to_dict(), ensure_ascii=False)))
        self.assertTrue(again.grid["complete"])
        for bad in ({"values": [1020, 1000]}, {"values": [1000, 1020, 990]}, {"complete": "yes"}, {"owned_total": None}):
            self.assertFalse(kb.clean_grid({**inv.grid, **bad})["complete"], bad)
        self.assertIsNone(kb.clean_grid("x"))

    def test_catalog_includes_seen_and_private(self):
        seen = book("t", (1, 1, 1, 1), [("新しいノウハウ", 3), ("パーフェクトリィ", 1)])
        names = [e.name for e in kb.build_catalog([seen], private=(kb.CatalogEntry("私有名", "private", source="private"),))]
        self.assertIn("新しいノウハウ", names)
        self.assertIn("私有名", names)
        self.assertEqual(names.count("パーフェクトリィ"), 1)


class MatcherTests(unittest.TestCase):
    def setUp(self):
        self.payload = fixture()
        self.books = [kb.KnowhowBookRecord.from_dict(b) for b in self.payload["books"]]
        self.title = {b.fingerprint: b.title for b in self.books}

    def test_fixture_is_synthetic_and_fingerprints_stable(self):
        self.assertEqual(self.payload["fixture"], "synthetic")
        for model, raw in zip(self.books, self.payload["books"]):
            if model.complete:
                self.assertEqual(model.fingerprint, raw["fingerprint"])
            else:
                self.assertTrue(model.fingerprint.startswith("p-"))
        self.assertNotRegex(json.dumps(self.payload, ensure_ascii=False), r"dev_assets|\.png|\.jpg")

    def test_demo_analysis(self):
        r = kb.analyze(self.books, self.payload["demo_wanted"])
        t = self.title
        self.assertEqual(r["sell_candidates"], [])
        self.assertEqual([(t[o["book"]], t[o["by"]]) for o in r["outclassed"]], [("【示例2】偶像A", "【示例1】偶像A")])
        self.assertEqual(t[r["ranking"][0]], "【示例1】偶像A")
        holders = {w["item"]["name"]: [t[h["book"]] for h in w["holders"]] for w in r["wanted"]}
        self.assertEqual(holders["Vocal上限"], ["【示例1】偶像A"])
        self.assertEqual(holders["アピールUP(思い出低)"], [])
        self.assertTrue(r["advice_only"])
        # メンタルUP+ Lv3 is tied between 偶像B and the 283 book: both count as top, so neither is a sell candidate.
        self.assertEqual(len(r["best_holders"]["メンタルUP+"]["books"]), 2)

    def test_no_wanted_list_never_suggests_selling(self):
        r = kb.analyze(self.books, [])
        self.assertEqual(r["sell_candidates"], [])
        self.assertEqual(r["uncertain"], [])

    def test_guards_incomplete_single_idol_and_marked(self):
        wanted = [{"name": "X", "min_level": 5}]
        books = [book("【a】偶像A", (1, 1, 1, 1), [("Y", 1)], count=4),            # incomplete
                 book("【b】偶像B", (1, 1, 1, 1), [("Z", 1)]),                     # only book of 偶像B
                 book("【c】偶像C", (1, 1, 1, 1), [("Z", 1)], marked=True),
                 book("【d】偶像C", (1, 1, 1, 1), [("Y", 1)]),
                 book("【e】偶像C", (9, 9, 9, 9), [("Y", 3), ("Z", 2)])]
        r = kb.analyze(books, wanted)
        titles = {b.fingerprint: b.title for b in books}
        self.assertEqual(r["sell_candidates"], [])
        self.assertIn("【a】偶像A", [titles[u["book"]] for u in r["uncertain"]])
        self.assertEqual(len(r["review_facts"]), len(books))

    def test_purpose_filter_and_team_exclusion(self):
        r = kb.analyze(self.books, self.payload["demo_wanted"], purpose="step", exclude_idols=["偶像A"])
        self.assertEqual({w["item"]["purpose"] for w in r["wanted"]}, {"step", "any"})
        a1 = next(b for b in r["books"] if self.title[b["book"]] == "【示例1】偶像A")
        self.assertFalse(a1["usable"])

    def test_pluggable_score(self):
        r = kb.analyze(self.books, self.payload["demo_wanted"], score=lambda book, matched: len(matched))
        self.assertEqual(max(b["score"] for b in r["books"]), 2)

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_matcher_matches_python(self):
        payload = {**self.payload, "books": [b.to_dict() for b in self.books]}
        out = run_node("const books = m.cleanBooks(data.payload);\n"
                       "const res = ['all', 'halo', 'step'].map((p) => m.analyze(books, data.payload.demo_wanted, { purpose: p === 'all' ? null : p }));\n"
                       "console.log(JSON.stringify(res.map((r) => ({ ranking: r.ranking, sell: r.sell_candidates, outclassed: r.outclassed,"
                       " uncertain: r.uncertain, best: r.best_holders, holders: r.wanted.map((w) => w.holders.map((h) => [h.book, h.level])),"
                       " scores: r.books.map((b) => b.score) }))));", {"payload": payload})
        for purpose, js in zip((None, "halo", "step"), out):
            py = kb.analyze(self.books, payload["demo_wanted"], purpose=purpose)
            self.assertEqual(js["ranking"], py["ranking"], purpose)
            self.assertEqual(js["sell"], py["sell_candidates"], purpose)
            self.assertEqual(js["outclassed"], py["outclassed"], purpose)
            self.assertEqual(js["uncertain"], py["uncertain"], purpose)
            self.assertEqual(js["best"], py["best_holders"], purpose)
            self.assertEqual(js["holders"], [[[h["book"], h["level"]] for h in w["holders"]] for w in py["wanted"]], purpose)
            self.assertEqual(js["scores"], [b["score"] for b in py["books"]], purpose)

    @unittest.skipUnless(NODE, "node not installed")
    def test_threshold_ranking_team_and_physical_duplicates_mirror(self):
        books = [book("【low】偶像A", (1, 1, 1, 1), [("X", 1), ("Y", 1)], ev=10),
                 book("【high】偶像B", (1, 1, 1, 1), [("X", 6)], ev=1020),
                 book("unknown", (1, 1, 1, 1), [("X", 6)], ev=2000)]
        inv = kb.KnowhowInventory()
        for b in books:
            inv.upsert(b)
        inv.upsert(books[0], duplicate=True)
        books = list(inv.books.values())
        wanted = [{"name": "X"}, {"name": "Y"}]
        data = {"payload": {"books": [b.to_dict() for b in books]}, "wanted": wanted}
        js = run_node("const books = m.cleanBooks(data.payload); console.log(JSON.stringify(["
                      "m.analyze(books, data.wanted), m.analyze(books, data.wanted, {excludeIdols:['偶像A']}),"
                      "m.analyze(books, data.wanted, {excludeIdols:[]})]));", data)
        py = [kb.analyze(books, wanted), kb.analyze(books, wanted, exclude_idols=["偶像A"]),
              kb.analyze(books, wanted, exclude_idols=[])]
        self.assertEqual(js, py)
        self.assertEqual(py[0]["ranking"][0], books[0].fingerprint)
        self.assertEqual([b["score"] for b in py[0]["books"]], [2, 1, 1])
        self.assertTrue(py[0]["wanted"][1]["satisfied"])  # inventory presence remains usable in the existing panel
        self.assertIsNone(py[0]["wanted"][1]["team_satisfied"])
        self.assertTrue(py[1]["wanted"][1]["owned"])
        self.assertFalse(py[1]["wanted"][1]["satisfied"])
        self.assertFalse(py[1]["wanted"][1]["team_satisfied"])
        self.assertIsNone(py[1]["books"][2]["usable"])
        self.assertTrue(py[2]["wanted"][1]["team_satisfied"])
        self.assertEqual(py[0]["best_holders"]["Y"]["physical_count"], 2)
        self.assertFalse(py[0]["best_holders"]["Y"]["unique"])
        self.assertTrue(all(not r["sell_candidates"] for r in py))

    @unittest.skipUnless(NODE, "node not installed")
    def test_unknown_idol_never_claims_verified_team_usability(self):
        b = book("unknown", (1, 1, 1, 1), [("X", 5)])
        wanted = [{"name": "X"}]
        py = kb.analyze([b], wanted, exclude_idols=[])
        js = run_node("console.log(JSON.stringify(m.analyze(m.cleanBooks(data), [{name:'X'}], {excludeIdols:[]})));",
                      {"books": [b.to_dict()]})
        self.assertEqual(js, py)
        self.assertTrue(py["wanted"][0]["owned"])
        self.assertFalse(py["wanted"][0]["satisfied"])
        self.assertIsNone(py["books"][0]["usable"])


    def test_minimum_level_does_not_turn_discount_into_value(self):
        books = [book("【a】偶像A", (1, 1, 1, 1), [("X", 1)], ev=1),
                 book("【b】偶像B", (1, 1, 1, 1), [("X", 6)], ev=1020)]
        for purpose in ("halo", "step"):
            r = kb.analyze(books, [{"name": "X", "min_level": 1}], purpose=purpose)
            self.assertEqual([b["score"] for b in r["books"]], [1, 1])
        r = kb.analyze(books, [{"name": "X", "min_level": 5}])
        self.assertEqual([b["score"] for b in r["books"]], [0, 1])


    @unittest.skipUnless(NODE, "node not installed")
    def test_js_catalogue_and_sanitiser_match_python(self):
        samples = ["パーフェクトリィ", {"name": "Ａ Ｂ", "match": "prefix", "min_level": 3, "purpose": "halo", "position": "vocal"},
                   {"name": "AB", "match": "prefix", "purpose": "halo"}, {"name": "C:x"}, {"name": "D", "min_level": 0, "purpose": "x"}, 7]
        out = run_node("console.log(JSON.stringify({ catalog: m.GENERIC_CATALOG, wanted: m.cleanWanted(data) }));", samples)
        self.assertEqual(out["catalog"], [e.to_dict() for e in kb.GENERIC_CATALOG])
        self.assertEqual(out["wanted"], [w.to_dict() for w in kb.clean_wanted(samples)])


def T(text, x, y, w=60, h=20):
    return (text, (x, y, w, h))


class ParserTests(unittest.TestCase):
    def test_same_book_title_tolerates_one_idol_glyph_slip_but_not_another_idol(self):
        """2026-10-13 live: 「幽谷霧子」 read once as 「幽谷鼎子」 on the stylised title row."""
        self.assertTrue(kb.same_book_title("【總·總·屬·来】幽谷霧子", "【紳·縷·屈·来】幽谷鼎子"))
        self.assertFalse(kb.same_book_title("【x】幽谷霧子", "【x】大崎甘奈"))
        self.assertFalse(kb.same_book_title("【x】大崎甘奈", "【x】大崎甜花"))        # two glyphs differ
        self.assertTrue(kb.same_book_title("【x】月岡恋鐘", "【x】月岡鐘"))            # one dropped glyph of a 4-glyph name
        self.assertTrue(kb.same_book_title("【brokenshout】斑ル力", "【brokenshout】斑儿ル力"))  # 2026-10-24 seg26 (斑鳩ルカ)
        self.assertFalse(kb.same_book_title("【x】浅倉透", "【x】浅倉诱"))             # both 3 glyphs: no tolerance
        self.assertFalse(kb.same_book_title("【x】月岡恋鐘", "【x】月岡"))             # 2-glyph reading
        self.assertTrue(kb.same_book_title("【starringF]黛冬優子", "【starringF]黛冬偃優子"))   # 2026-10-23 seg10: one inserted glyph
        self.assertFalse(kb.same_book_title("【x】黛冬優子", "【x】黛冬偃偃優子"))
        self.assertTrue(kb._same_idol("1樋口円香", "樋口円香"))                     # 2026-10-23 seg20: stray 「1」
        self.assertFalse(kb._same_idol("大崎甘奈", "大崎甜花"))
        page = {"title": "【x】幽谷霧子", "idol": "幽谷霧子", "knowhow_count": 1, "stats": {}, "rows": [
            {"name": "A", "level": 5, "y": 100}]}
        slip = {**page, "title": "【y】幽谷鼎子", "idol": "幽谷鼎子"}
        self.assertNotIn("merge.book_conflict", kb.merge_detail_pages([page, slip])["unknowns"])
        other = {**page, "title": "【y】大崎甘奈", "idol": "大崎甘奈"}
        self.assertIn("merge.book_conflict", kb.merge_detail_pages([page, other])["unknowns"])

    def test_same_book_title_tolerates_a_kana_slip_only_for_the_same_idol(self):
        self.assertTrue(kb.same_book_title("[<5<5】三峰結華", "[<5<ら】三峰結華"))      # live 2026-10-12
        self.assertTrue(kb.same_book_title("【ひとつ、はばたく】櫻木真乃", "【ひとつ、はばたく】櫻木真乃"))
        self.assertTrue(kb.same_book_title("[<5<5】三峰結華", "【<ら<5】三峰結華"))
        self.assertTrue(kb.same_book_title("【<5<ら】三峰結華", "[<5<5]三峰結華"))
        self.assertFalse(kb.same_book_title("[<5<5】三峰結華", "[<5<5】月岡恋鐘"))
        self.assertFalse(kb.same_book_title(None, "【くらくら】三峰結華"))
        self.assertTrue(kb.same_book_title("Lv.5+x13/21", "Lv.5+x13/21") is True)          # plain equality
        self.assertRegex("Lv.5+↑x13/21", kb._LV5)                                           # live 2026-10-12 pill

    def picker_texts(self, sort="ノウハウ評価値順"):
        texts = [T("ノウハウブック選択", 40, 20, 200), T("選択数 0/3", 560, 120, 120), T(sort, 960, 115, 160)]
        for row in range(3):
            for col in range(5):
                x, y, w, h = kb.tile_box(row, col)
                texts.append(T(str(1000 - 10 * (row * 5 + col)), x + int(w * 0.62), y + int(h * 0.82), 40, 18))
                texts.append(T("甘奈", x + int(w * 0.38), y + int(h * 0.25), 30, 16))
        return texts

    def test_picker_tiles_in_eval_sort(self):
        out = kb.parse_picker_tiles(self.picker_texts())
        self.assertEqual(out["screen"], "picker")
        self.assertTrue(out["eval_sorted"])
        self.assertFalse(out["calibrated"])
        self.assertEqual(out["selected"], 0)
        self.assertEqual([t["value"] for t in out["tiles"]], [1000 - 10 * i for i in range(15)])
        self.assertEqual({t["name"] for t in out["tiles"]}, {"甘奈"})
        self.assertIsNone(out["tiles"][0]["marked"])  # no image -> unknown, never False

    def test_values_ignored_without_eval_sort_and_refused_in_sell_mode(self):
        self.assertEqual({t["value"] for t in kb.parse_picker_tiles(self.picker_texts("デフォルト"))["tiles"]}, {None})
        refused = kb.parse_picker_tiles(self.picker_texts() + [T("売却する", 1000, 650, 100)])
        self.assertEqual(refused["tiles"], [])
        self.assertEqual(refused["refused"], ["売却する"])
        self.assertIsNone(kb.classify_knowhow_screen([T("ホーム", 40, 20)])["screen"])

    def test_tile_overlay_is_strict(self):
        tile = kb.list_tile_box(0, 0)
        at = lambda text: [T(text, tile[0] + 40, tile[1] + 84, 60, 16)]
        toks = lambda texts: kb._tokens(texts)
        self.assertEqual(kb._tile_overlay(toks(at("2026/10/12")), tile, "date", None), "2026-10-12")
        self.assertIsNone(kb._tile_overlay(toks(at("2026/13/40")), tile, "date", None))          # not a date
        self.assertEqual(kb._tile_overlay(toks(at("x21")), tile, "count", 21), 21)
        self.assertIsNone(kb._tile_overlay(toks(at("x2")), tile, "count", 21))                   # disagrees with pill
        self.assertIsNone(kb._tile_overlay(toks(at("150") + at("140")), tile, "vo", None))       # two tokens
        self.assertIsNone(kb._tile_overlay(toks(at("150")), tile, None, None))                   # デフォルト / Lv順
        self.assertEqual(kb.parse_filter_popup([T("該当するノウハウブック", 830, 50, 190, 28), T("195", 1060, 48, 50, 30)]),
                         {"open": True, "count": 195})

    def test_list_title_accepts_ocr_dash_variants(self):
        # 2026-10-12 live (calibrate-20261012T022320Z/frame-0041): 「一覧」 read as 「ノウハウブック-」 + 「覧」
        for dash in ("一", "ー", "-", "－", "—"):
            texts = [T(f"ノウハウブック{dash}", 50, 19, 190, 32), T("覧", 245, 19, 32, 33)]
            self.assertEqual(kb.classify_knowhow_screen(texts)["screen"], "list", dash)

    def test_list_title_accepts_overlapping_ocr_strokes(self):
        # Live 2026-10-20 frame-0022: both boxes include the 一, first read as ー.
        texts = [T("ノウハウブックー", 52, 19, 197, 32), T("一覧", 237, 19, 40, 33)]
        self.assertEqual(kb.classify_knowhow_screen(texts)["screen"], "list")
        self.assertIsNone(kb.classify_knowhow_screen([T("ノウハウブック選択", 52, 19, 230, 33)])["mode"])

    def detail_page(self, rows, count=None, top=290):
        texts = [T("【ひとつ、はばたく】", 120, 100, 160), T("偶像A", 285, 100, 80),
                 T("50", 210, 145, 30), T("20", 370, 145, 30), T("10", 210, 177, 30), T("20", 370, 177, 30)]
        for i, (name, lv) in enumerate(rows):
            cy = top + i * 102
            texts += [T(name, 70, cy - 20, 200, 18), T(f"Lv.{lv}", 375, cy - 9, 40, 18), T("Vocal+20", 30, cy + 18, 80, 16)]
        if count is not None:
            texts.append(T(f"ノウハウ数 {count}/21", 40, 590, 160))
        return kb.parse_detail_panel(texts)

    def test_detail_panel_rows(self):
        page = self.detail_page([("パーフェクトリィ", 4), ("スタートダッシュ", 5), ("昂揚", 2)], count=3)
        self.assertEqual(page["title"], "【ひとつ、はばたく】偶像A")
        self.assertEqual(page["idol"], "偶像A")
        self.assertEqual(page["stats"], {"vo": 50, "da": 20, "vi": 10, "me": 20})
        self.assertEqual([(r["name"], r["level"], r["effect"]) for r in page["rows"]],
                         [("パーフェクトリィ", 4, "Vocal+20"), ("スタートダッシュ", 5, "Vocal+20"), ("昂揚", 2, "Vocal+20")])
        self.assertEqual(page["knowhow_count"], 3)

    def test_two_line_name_is_joined(self):
        texts = [T("今がキラキラ！おもいっきりア", 70, 300, 200, 18), T("イドル！(Vo)", 70, 318, 100, 18), T("Lv.3", 375, 308, 40, 18),
                 T("Vocal+20", 30, 340, 80, 16)]
        rows = kb.parse_detail_panel(texts)["rows"]
        self.assertEqual([(r["name"], r["level"]) for r in rows], [("今がキラキラ!おもいっきりアイドル!(Vo)", 3)])

    def test_merge_pages_overlap_and_completeness(self):
        p1 = {"title": "t", "stats": {"vo": 1}, "rows": [{"name": "A", "level": 1}, {"name": "B", "level": 2}, {"name": "C", "level": 3, "clipped": True}]}
        p2 = {"rows": [{"name": "B", "level": 2}, {"name": "C", "level": 3}, {"name": "D", "level": 4}], "knowhow_count": 4}
        merged = kb.merge_detail_pages([p1, p2])
        self.assertEqual([r["name"] for r in merged["rows"]], ["A", "B", "C", "D"])
        self.assertTrue(merged["complete"])
        self.assertFalse(kb.merge_detail_pages([p1])["complete"])
        rec = kb.book_from_detail(merged, eval_value=990, frames=["f1"])
        self.assertEqual(rec.eval_value, 990)
        self.assertIn("stats.da", rec.unknowns)

    def test_merge_conflicts_and_missing_overlap_cannot_be_complete(self):
        base = {"title": "【a】偶像A", "stats": dict(zip(kb.STATS, [1, 2, 3, 4])),
                "rows": [{"name": "A", "level": 1}, {"name": "B", "level": 2}], "knowhow_count": 3}
        cases = [
            {"rows": [{"name": "C", "level": 3}], "knowhow_count": 3},  # exact count, but gap
            {"title": "【b】偶像B", "rows": [{"name": "B", "level": 2}, {"name": "C", "level": 3}], "knowhow_count": 3},
            {"rows": [{"name": "B", "level": 2}, {"name": "C", "level": 3}], "knowhow_count": 4},
            {"stats": {"vo": 9}, "rows": [{"name": "B", "level": 2}, {"name": "C", "level": 3}], "knowhow_count": 3},
        ]
        for second in cases:
            with self.subTest(second=second):
                merged = kb.merge_detail_pages([base, second])
                rec = kb.book_from_detail(merged)
                self.assertFalse(merged["complete"])
                self.assertFalse(rec.complete)
                self.assertTrue(rec.fingerprint.startswith("p-"))
                self.assertTrue(any(u.startswith("merge.") for u in rec.unknowns))

    @unittest.skipUnless(NODE, "node not installed")
    def test_clipped_and_partial_details_stay_partial_after_roundtrip(self):
        full = {"title": "【a】偶像A", "stats": dict(zip(kb.STATS, [1, 2, 3, 4])),
                "rows": [{"name": "A", "level": 1}], "knowhow_count": 1}
        clipped = {**full, "rows": [{"name": "A", "level": 1, "clipped": True}], "complete": False}
        rec = kb.book_from_detail(clipped)
        self.assertFalse(rec.complete)
        self.assertIn("rows.clipped", rec.unknowns)
        self.assertTrue(rec.fingerprint.startswith("p-"))
        self.assertFalse(kb.KnowhowBookRecord.from_dict(rec.to_dict()).complete)
        partial = kb.KnowhowBookRecord.from_dict({**full, "knowhow_count": 2})
        self.assertFalse(partial.complete)
        self.assertTrue(partial.fingerprint.startswith("p-"))
        missing_level = kb.KnowhowBookRecord.from_dict({**full, "rows": [{"name": "A", "level": None}]})
        self.assertFalse(missing_level.complete)
        js = run_node("console.log(JSON.stringify(m.cleanBooks(data).map((b) => b.complete)));",
                      {"books": [rec.to_dict(), partial.to_dict(), missing_level.to_dict(),
                                 {**rec.to_dict(), "complete": True}]})
        self.assertEqual(js, [False] * 4)
        recovered = kb.book_from_detail(kb.merge_detail_pages([clipped, full]))
        self.assertFalse(recovered.complete)  # explicit incomplete evidence is never silently dropped


    def list_frame(self, pink=None):
        """Synthetic 一覧 frame: title tokens + an image with unchecked toggles; ``pink`` paints a mode toggle."""
        import numpy as np
        image = np.full((720, 1280, 3), 250, dtype=np.uint8)
        for x, y, w, h in kb.TOGGLE_INNER:
            image[y:y + h, x:x + w] = kb.TOGGLE_OFF_RGB
        if pink:
            x, y, w, h = getattr(kb, pink)
            image[y + 10:y + 20, x + 5:x + 30] = (240, 90, 170)
        texts = [T("ノウハウブックー", 52, 19, 190, 32), T("覧", 241, 17, 37, 37), T("ノウハウ評価値順", 972, 109, 150, 27),
                 T("▼降順", 1150, 105, 78, 33), T("514/600", 1153, 660, 93, 24)]
        return texts, image

    def test_list_mode_guard_is_pixel_based(self):
        texts, image = self.list_frame()
        c = kb.classify_knowhow_screen(texts, image)
        self.assertEqual((c["screen"], c["mode"], c["ok"], c["owned"], c["direction"]), ("list", "normal", True, 514, "desc"))
        self.assertTrue(c["eval_sorted"])
        self.assertIsNone(c["filter_active"])
        self.assertFalse(kb.classify_knowhow_screen(texts)["ok"])          # no image: 保護 mode cannot be excluded
        self.assertEqual(kb.parse_list_tiles(texts)["tiles"], [])
        for box, mode in (("LIST_PROTECT_BOX", "protect"), ("LIST_SALE_BOX", "sale")):
            texts, image = self.list_frame(box)
            c = kb.classify_knowhow_screen(texts, image)
            self.assertEqual((c["mode"], c["ok"]), (mode, False))
            self.assertEqual(kb.parse_list_tiles(texts, image)["tiles"], [])
        texts, image = self.list_frame()
        self.assertEqual(kb.classify_knowhow_screen(texts + [T("並び替え", 406, 125, 112, 33)], image)["popup"], "sort")
        self.assertEqual(kb.parse_list_tiles(texts + [T("並び替え", 406, 125, 112, 33)], image)["tiles"], [])
        self.assertIsNone(kb.classify_knowhow_screen(texts + [T("★ノウハウブックマーク", 170, 438, 251, 26)], image)["popup"])

    def test_list_tiles_on_synthetic_layout(self):
        texts, image = self.list_frame()
        for i, value in enumerate((1020, 1010, 1000)):
            x, y, w, h = kb.list_tile_box(0, i)
            texts += [T(str(value), x + 58, y + 83, 50, 24), T("Lv.5+x10/21", x - 6, y + 115, 120, 22)]
        x, y, _, _ = kb.list_tile_box(0, 0)
        image[y + 88:y + 104, x + 4:x + 20] = (250, 200, 40)             # lock
        out = kb.parse_list_tiles(texts, image)
        self.assertTrue(out["calibrated"])
        self.assertEqual([t["value"] for t in out["tiles"][:4]], [1020, 1010, 1000, None])
        self.assertEqual((out["tiles"][0]["lv5"], out["tiles"][0]["knowhow_count"]), (10, 21))
        self.assertEqual([t["protected"] for t in out["tiles"][:2]], [True, False])
        self.assertTrue(out["order_ok"])
        texts[2] = T("デフォルト", 992, 108, 104, 28)
        self.assertEqual({t["value"] for t in kb.parse_list_tiles(texts, image)["tiles"]}, {None})

    def test_merge_tolerates_ocr_slips_but_never_tier_or_ring_changes(self):
        a = {"title": "t", "stats": dict(zip(kb.STATS, [1, 2, 3, 4])), "knowhow_count": 3,
             "rows": [{"name": "A", "level": 1}, {"name": "エントリー目標達或(Vo)", "level": 2, "score": 0.9}]}
        b = {"knowhow_count": 3, "rows": [{"name": "エントリー目標達成(Vo)", "level": 2, "score": 0.95}, {"name": "C", "level": 3}]}
        merged = kb.merge_detail_pages([a, b])
        self.assertTrue(merged["complete"])
        self.assertEqual([r["name"] for r in merged["rows"]], ["A", "エントリー目標達成(Vo)", "C"])
        self.assertEqual(merged["rows"][1]["variants"], ["エントリー目標達或(Vo)"])
        for x, y in (("Vocal上限+", "Vocal上限++"), ("オールラウンダー◯", "オールラウンダー◎")):
            with self.subTest(pair=(x, y)):
                p1 = {"knowhow_count": 3, "rows": [{"name": "A", "level": 1}, {"name": x, "level": 5}]}
                p2 = {"knowhow_count": 3, "rows": [{"name": y, "level": 5}, {"name": "C", "level": 3}]}
                self.assertIn("merge.overlap_lost", kb.merge_detail_pages([p1, p2])["unknowns"])

    def test_ku_read_as_angle_bracket_is_repaired_in_scans_and_stored_books(self):
        page = kb.parse_detail_panel([T("[<5<5】三峰結華", 60, 108, 240, 30), T("疲れに<い(Hide&Attack)", 60, 268, 260, 24),
                                      T("Lv.1", 362, 270, 52, 26)])
        self.assertEqual(page["title"], "【くらくら】三峰結華")
        self.assertEqual([r["name"] for r in page["rows"]], ["疲れにくい(Hide&Attack)"])
        stats = dict(zip(kb.STATS, [10, 10, 40, 20]))
        old = {"title": "【<5<5】三峰結華", "stats": stats, "knowhow_count": 1, "rows": [{"name": "疲れに<い", "level": 1}]}
        twin = dict(old, title="[<5<5】三峰結華")
        stored = [dict(r, fingerprint=f"b-{i:016x}", count=1) for i, r in enumerate((old, twin))]
        inv = kb.KnowhowInventory.from_dict({"books": stored, "recent": [{"fp": "b-0000000000000001",
                                                                           "signature": ["2026-11-01", 0, 1]}]})
        (book,) = inv.books.values()
        self.assertEqual((book.title, book.rows[0].name, book.count), ("【くらくら】三峰結華", "疲れにくい", 2))
        self.assertEqual(inv.recent[0]["fp"], book.fingerprint)

    def test_detail_count_protect_and_wrapped_tail(self):
        texts = [T("ノウハウ数", 264, 577, 89, 23), T("21 / 21", 373, 575, 80, 28), T("保護解除", 152, 645, 97, 30),
                 T("基礎能力值UP（+3%）(W.I.N.G.", 60, 468, 300, 30), T("Lv.4", 362, 483, 52, 25),
                 T("とても長いノウハウの名前がここで折り返され", 60, 268, 300, 24), T("Lv.3", 362, 270, 52, 26)]
        page = kb.parse_detail_panel(texts)
        self.assertEqual((page["knowhow_count"], page["protected"]), (21, True))
        rows = {r["name"]: r for r in page["rows"]}
        self.assertEqual(rows["基礎能力値UP(+3%)(W.I.N.G.)"]["repairs"], ["closing_paren"])
        self.assertTrue(rows["とても長いノウハウの名前がここで折り返され"]["clipped"])  # unread tail is never guessed

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_normalize_matches_python(self):
        samples = ["オールラウンダー〇", "オールラウンダー○", "Dance適正 ◎", "ＨＯＰＰＩＮ ＪＡＭ（Ｖｏ）", "Landing Point大成功(SP)+ +"]
        out = run_node("console.log(JSON.stringify(data.map((s) => m.normalizeName(s))));", samples)
        self.assertEqual(out, [kb.normalize_name(s) for s in samples])
        self.assertEqual(out[:2], ["オールラウンダー◯"] * 2)

    @unittest.skipUnless(REFERENCE.is_file(), "private reference JPEG missing")
    def test_reference_jpeg_greyed_and_badges(self):
        """Rough geometry check on the 715x402 side-browser JPEG upscaled to 1280x720 (not a template, not calibration)."""
        import numpy as np
        from PIL import Image
        image = np.asarray(Image.open(REFERENCE).convert("RGB").resize((1280, 720)))
        greyed = [(r, c) for r in range(3) for c in range(5) if kb.tile_greyed(image, kb.tile_box(r, c))]
        marked = [(r, c) for r in range(3) for c in range(5) if kb.tile_badge(image, kb.tile_box(r, c))]
        self.assertEqual(greyed, [(2, 2)])
        self.assertEqual(marked, [(0, 4), (2, 4)])


class DetailRereadTests(unittest.TestCase):
    """2026-10-23 live chain: both detail_rows_missing stops came from ``refine_detail_tokens`` (full-frame tokens below)."""

    @staticmethod
    def canvas():
        import numpy as np
        return np.full((720, 1280, 3), 250, dtype=np.uint8)

    def test_effect_box_under_a_named_lv_line_is_not_reread(self):
        texts = [("ブェスュニット場成時、Danceに設定した際", (20, 424, 338, 49), 0.814),
                 ("の基礎ポジションポーナスを+10％", (25, 453, 265, 27), 0.942), ("↑", (37, 414, 23, 16), 0.889),
                 ("Dance適正", (53, 408, 136, 26), 0.938), ("Lv.4", (362, 412, 52, 26), 1.0)]
        crops = []
        out = kb.refine_detail_tokens(texts, self.canvas(), lambda c: crops.append(c.shape) or [("Dance通正", (0, 0, 9, 9), 0.9)])
        self.assertEqual(crops, [])
        self.assertEqual(sorted(t for t, _, _ in out if t.startswith("Dance")), ["Dance適正"])

    def test_second_line_fragment_does_not_block_an_icon_merged_name(self):
        """2026-10-23 live seg6: 「A.D.)」 below the icon-merged box is the name's own second line."""
        texts = [("アピールUP（思い出高&低）（G.R", (28, 411, 344, 49), 0.888), ("A.D.)", (60, 439, 70, 33), 0.852),
                 ("アピール値最大5％UP[思い出ゲージが多いほ", (28, 461, 328, 23), 0.985), ("Lv.4", (366, 434, 47, 25), 1.0)]
        crops = []
        kb.refine_detail_tokens(texts, self.canvas(), lambda c: crops.append(c.shape) or [])
        self.assertTrue(crops)

    def test_failed_band_is_retried_at_x3(self):
        """2026-10-23 live seg16: 「イドル！(Vi)」 read 「町イルイてV」 (0.57) at x2 and 「リイドルイ（Vi」 (0.76) at x3."""
        texts = [("今る！", (52, 401, 152, 55), 0.527), ("おもいッきりア", (212, 408, 140, 27), 0.999),
                 ("Lv.3", (361, 416, 55, 34), 1.0)]
        scales = []

        def ocr(crop):
            scales.append(crop.shape[0])
            if len(scales) == 1:
                return [("今がキラキラ！おもいッきりア", (0, 27, 595, 34), 0.99)]
            return [("町イルイてV", (1, 1, 235, 48), 0.57)] if len(scales) == 2 else [("リイドルイ（Vi", (0, 0, 300, 60), 0.76)]

        out = kb.refine_detail_tokens(texts, self.canvas(), ocr)
        self.assertEqual(len(scales), 3)
        self.assertEqual(scales[2] * 2, scales[1] * 3)                # same band, x3 instead of x2
        names = [t for t, _, _ in out if t not in ("Lv.3",)]
        self.assertIn("今がキラキラ!おもいッきりア", names)
        self.assertNotIn("今る!", names)

    def test_name_glued_to_its_level_is_split(self):
        """2026-10-23 live seg21: 「LandingPoint大成功(アビリティ)Lv.1」 was one token, so the row had no Lv and was dropped."""
        texts = [("LandingPoint大成功(アビリティ)Lv.1", (56, 416, 356, 31), 0.997),
                 ("フェスユニット編成時に全基礎能カヘのポー", (28, 450, 312, 18), 0.937),
                 ("LandingPoint(Vi)", (57, 518, 173, 31), 1.0), ("Lv.1", (362, 524, 50, 26), 1.0)]
        rows = kb.parse_detail_panel(texts)["rows"]
        self.assertEqual([(r["name"], r["level"], r["clipped"]) for r in rows],
                         [("LandingPoint大成功(アビリティ)", 1, False), ("LandingPoint(Vi)", 1, False)])
        self.assertEqual(kb._split_glued_levels([("VocalUPLv.1", (60, 300, 120, 30), 0.9)])[0][0], "VocalUPLv.1")  # not at Lv column

    def test_band_with_an_unreadable_token_keeps_the_original_tokens(self):
        texts = [("Vo", (32, 418, 26, 22), 0.909), ("今がキラキう!", (61, 408, 136, 28), 0.805),
                 ("ル(Vo)", (65, 416, 132, 47), 0.873), ("おもいッきりア", (212, 408, 140, 27), 0.999),
                 ("Vocal+20", (26, 450, 90, 22), 0.997), ("Lv.5", (361, 416, 55, 34), 1.0)]
        reads = iter([[("今", (29, 0, 52, 37), 0.91), ("がキラキラ", (56, 0, 212, 39), 0.88),
                       ("おもいッきりア", (333, 0, 262, 39), 0.995)],
                      [("ocal+20", (0, 31, 133, 24), 0.944), ("Fル.!", (77, 4, 96, 20), 0.421)]])
        out = kb.refine_detail_tokens(texts, self.canvas(), lambda _c: next(reads))
        self.assertIn("ル(Vo)", [t for t, _, _ in out])
        self.assertNotIn("ocal+20", [t for t, _, _ in out])


class ListTileCheckTests(unittest.TestCase):
    """2026-10-23 live: the pink 「使用中」 tag of a book in a running オートプロデュース is not the selection ✓."""
    PINK = (240, 80, 160)
    TILE = (534, 180)

    def canvas(self):
        import numpy as np
        return np.full((720, 1280, 3), 250, dtype=np.uint8)

    def paint(self, image, rel):
        x, y, w, h = kb._off(self.TILE, rel)
        image[max(0, y):y + h, max(0, x):x + w] = self.PINK

    def test_in_use_tag_without_frame_is_not_checked(self):
        image = self.canvas()
        self.paint(image, (70, 8, 46, 18))                      # 使用中 tag: inside the ✓ corner, no frame
        self.assertIs(kb.list_tile_pixels(image, self.TILE)["checked"], False)

    def test_check_with_selection_frame_is_checked(self):
        image = self.canvas()
        self.paint(image, (84, -2, 26, 30))                     # ✓ circle
        for roi in kb.LIST_SELECTED_FRAME_ROIS:                 # pink selection frame
            self.paint(image, roi)
        self.assertIs(kb.list_tile_pixels(image, self.TILE)["checked"], True)

    def test_pill_with_a_frame_glyph_still_reads(self):
        """2026-10-23 live: the selection frame's edge OCRs as 「(」 before the shown book's Lv.5 pill."""
        self.assertEqual(kb._LV5.fullmatch("(Lv.5+x12/21").groups(), ("12", "21"))
        self.assertEqual(kb._LV5.fullmatch("Lv.5↑x14/21)").groups(), ("14", "21"))
        self.assertIsNone(kb._LV5.fullmatch("x(Lv.5x1/2"))

    def test_unparsed_grid_pill_is_reread_from_a_crop(self):
        """2026-10-23 live seg4: 「Lv.5↑x11/21」 read as 「Lv.5+x11/2」 on every hold; the x2 crop reads it."""
        image = self.canvas()
        crops = []

        def ocr(crop):
            crops.append(crop.shape)
            return [("Lv.5x11/21", (11, 7, 245, 61), 0.99)]

        texts = [("Lv.5+x11/2", (948, 444, 106, 23), 0.94), ("Lv.5+x14/21", (1086, 446, 114, 22), 0.97),
                 ("Lv.3", (300, 400, 40, 20), 0.9)]
        out = kb.refine_list_pills(texts, image, ocr)
        self.assertEqual(out[0], ("Lv.5x11/21", (948, 444, 106, 23), 0.99))
        self.assertEqual([t for t, _, _ in out[1:]], ["Lv.5+x14/21", "Lv.3"])
        self.assertEqual(crops, [(70, 268, 3)])                  # only the broken pill, upscaled x2
        for reread in ([], [("Lv.5x11/2", (0, 0, 9, 9), 0.99)], [("Lv.5x11/21", (0, 0, 9, 9), 0.4)],
                       [("Lv.5x11/21", (0, 0, 9, 9), 0.99), ("Lv.5x11/20", (0, 0, 9, 9), 0.99)]):
            with self.subTest(reread=reread):                   # unusable re-read: the original token stays
                self.assertEqual(kb.refine_list_pills(texts[:1], image, lambda _c, r=reread: r)[0][0], "Lv.5+x11/2")

    def test_unread_detail_count_is_read_from_its_roi(self):
        """2026-10-23 live seg18: 「14 / 21」 read 「147 21」 (slash as 7) and stopped the book as detail_mismatch."""
        image = self.canvas()
        extra = kb.reread_detail_count(image, lambda _c: [("14 / 21", (0, 0, 120, 40), 0.9), ("LA", (0, 0, 9, 9), 0.64)])
        self.assertEqual([t for t, _, _ in extra], ["14/21"])
        self.assertTrue(kb._inside(extra[0][1], kb.DETAIL_COUNT_ROI))
        for reread in ([], [("147 21", (0, 0, 9, 9), 0.9)], [("14/21", (0, 0, 9, 9), 0.4)]):
            with self.subTest(reread=reread):
                self.assertEqual(kb.reread_detail_count(image, lambda _c, r=reread: r), [])

    def test_out_of_order_values_are_reread(self):
        """2026-10-23 live seg22: a fully visible 795 read 「195」 (same length) → grid_order_broken."""
        image = self.canvas()
        a = {"present": True, "value": 195, "box": [534, 107, 108, 108]}
        b = {"present": True, "value": 790, "box": [672, 107, 108, 108]}
        roi_a = kb._off(a["box"], kb.LIST_VALUE_ROI)
        texts = [("195", (roi_a[0] + 30, roi_a[1] + 8, 26, 14), 0.83), ("790", (744, 194, 32, 16), 1.0)]
        reads = iter([[("795", (0, 0, 90, 50), 1.0)], [("790", (0, 0, 90, 50), 1.0)]])
        out = kb.reread_out_of_order(texts, [a, b], image, lambda _c: next(reads))
        self.assertEqual(sorted(t for t, _, _ in out), ["790", "795"])
        self.assertIsNone(kb.reread_out_of_order(texts, [b, {**a, "value": 700}], image, lambda _c: []))   # in order
        same = iter([[("195", (0, 0, 9, 9), 1.0)], [("790", (0, 0, 9, 9), 1.0)]])
        self.assertIsNone(kb.reread_out_of_order(texts, [a, b], image, lambda _c: next(same)))   # re-read agrees: stop stands

    def test_missing_grid_value_is_read_from_its_roi(self):
        """2026-10-23 live seg9: tile 「羽那」 915 had no value token at all on every hold."""
        image = self.canvas()
        tiles = [{"present": True, "value": None, "box": [1086, 335, 108, 108]},
                 {"present": True, "value": 920, "box": [948, 335, 108, 108]}, {"present": False, "value": None}]
        crops = []
        extra = kb.reread_missing_values(tiles, image, lambda c: crops.append(c.shape) or [("915", (20, 10, 70, 30), 1.0)])
        self.assertEqual(crops, [(92, 176, 3)])
        self.assertEqual(len(extra), 1)
        text, box, _ = extra[0]
        self.assertEqual(text, "915")
        self.assertTrue(kb._inside(box, kb._off(tiles[0]["box"], kb.LIST_VALUE_ROI)))
        noisy = kb.reread_missing_values(tiles, image, lambda _c: [("775", (63, 23, 83, 51), 0.99), ("1", (69, 1, 50, 25), 0.57)])
        self.assertEqual([t for t, _, _ in noisy], ["775"])          # seg12: frame-edge noise beside the value
        for reread in ([], [("9l5", (0, 0, 9, 9), 1.0)], [("915", (0, 0, 9, 9), 0.4)],
                       [("915", (0, 0, 9, 9), 1.0), ("910", (0, 0, 9, 9), 1.0)]):
            with self.subTest(reread=reread):
                self.assertEqual(kb.reread_missing_values(tiles, image, lambda _c, r=reread: r), [])

    def test_short_grid_value_is_reread_from_a_crop(self):
        """2026-10-23 live seg5: the selected tile's 875 read as 「87」 → grid_order_broken."""
        image = self.canvas()
        texts = [("885", (600, 234, 41, 21), 1.0), ("87", (740, 551, 25, 18), 1.0), ("875", (876, 547, 42, 26), 1.0),
                 ("21", (300, 400, 20, 20), 1.0)]
        crops = []
        out = kb.refine_list_values(texts, image, lambda c: crops.append(c.shape) or [("875", (19, 12, 62, 40), 1.0)])
        self.assertEqual([t for t, _, _ in out], ["885", "875", "875", "21"])
        self.assertEqual(out[1][1], (740, 551, 25, 18))
        self.assertEqual(crops, [(60, 150, 3)])                  # only the short grid value, upscaled x2
        lead = kb.refine_list_values([("510", (600, 557, 42, 25), 1.0), ("10", (760, 563, 14, 12), 0.71)], image,
                                     lambda _c: [("510", (5, 1, 94, 46), 1.0), ("8", (0, 0, 9, 9), 0.9)])
        self.assertEqual(lead[1][0], "510")                     # seg17: lost leading digit, lock-icon noise ignored
        for reread in ([], [("87", (0, 0, 9, 9), 1.0)], [("875", (0, 0, 9, 9), 0.4)], [("Lv", (0, 0, 9, 9), 1.0)],
                       [("875", (0, 0, 9, 9), 1.0), ("870", (0, 0, 9, 9), 1.0)]):
            with self.subTest(reread=reread):
                self.assertEqual(kb.refine_list_values(texts, image, lambda _c, r=reread: r)[1][0], "87")
        same = [("1000", (600, 234, 44, 21), 1.0), ("995", (740, 234, 40, 21), 1.0)]
        self.assertEqual([t for t, _, _ in kb.refine_list_values(same, image, lambda _c: [("995", (0, 0, 9, 9), 1.0)])],
                         ["1000", "995"])

    @unittest.skipUnless((ROOT / "dev_assets/private/knowhow-scan/scan-20261023T054009Z/frame-0002.png").exists(),
                         "需要 2026-10-23 实机书库帧")
    def test_live_frame_only_the_shown_book_is_checked(self):
        import numpy as np
        from PIL import Image
        image = np.array(Image.open(ROOT / "dev_assets/private/knowhow-scan/scan-20261023T054009Z/frame-0002.png")
                         .convert("RGB"))
        tiles = [(int(534 + 138.4 * c), 180) for c in range(5)]
        self.assertEqual([c for c, t in enumerate(tiles) if kb.list_tile_pixels(image, t)["checked"]], [0])

class UiTests(unittest.TestCase):
    def test_panel_registered_and_display_only(self):
        app = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn('knowhow: "sc-knowhow-panel",', app)
        self.assertIn('import "./components/knowhow-panel.js";', app)
        self.assertIn('{ key: "knowhow", label: "ノウハウ" }', (UI / "components" / "header.js").read_text(encoding="utf-8"))
        panel = (UI / "components" / "knowhow-panel.js").read_text(encoding="utf-8")
        self.assertIn('customElements.define("sc-knowhow-panel", KnowhowPanel);', panel)
        # No sell advice: the old 「可考虑出售」 title is gone; 整理复核 facts carry REVIEW_NOTE instead.
        self.assertNotIn("SELL_TITLE", panel)
        self.assertNotIn("可考虑出售", panel)
        self.assertNotIn("sell_candidates", panel)
        self.assertIn("REVIEW_NOTE", panel)
        # 2026-10-12: the scan button asks the host for the supervised KnowhowScan through app actions,
        # gated like 「扫描持有卡」 (knowhow-scan.js); the panel itself sends no bridge request.
        self.assertIn('SCAN_STATUS = "需要先读取书库"', panel)
        self.assertIn("knowhowScanGate(state?.policy, state?.host)", panel)
        for forbidden in ("bridge.request", ".request(", "startRun", "setOption", "allow_spend", "run.start"):
            self.assertNotIn(forbidden, panel)
        # setTab = navigation only (编队未设置 → Produce page)
        self.assertEqual(set(re.findall(r"actions\??\.(\w+)", panel)), {"setPref", "setTab", "knowhowScanStart", "knowhowScanStop"})
        css = (UI / "styles" / "knowhow.css").read_text(encoding="utf-8")
        tokens = (UI / "styles" / "tokens.css").read_text(encoding="utf-8")
        for name in set(re.findall(r"var\((--[\w-]+)", css)):
            self.assertIn(name + ":", tokens, name)

    def test_wing_reward_books_reach_the_page(self):
        # 2026-10-12: the W.I.N.G. 獲得報酬 ingest also writes ui/game-assets/knowhow.js, and the host copies it after a
        # Wing run (the panel is served from the host's output ui folder).
        wing = (ROOT / "agent" / "core" / "wing_custom.py").read_text(encoding="utf-8")
        self.assertLess(wing.index("kb.save_inventory(inv)"), wing.index("kb.export_ui_module(inv)"))
        host = (ROOT / "browser_host" / "Ui" / "UiEngineSupervisor.cs").read_text(encoding="utf-8-sig")
        self.assertIn('run.Launch.Wing && PublishGameAsset(run, "knowhow.js"', host)
        self.assertIn('or "knowhow.js")) { return false; }', host)

    def test_protocol_declares_knowhow_wanted(self):
        protocol = json.loads((UI / "protocol.json").read_text(encoding="utf-8"))
        spec = protocol["definitions"]["profile"]["properties"]["prefs"]["properties"]["knowhow_wanted"]
        self.assertEqual(spec["maxItems"], kb.MAX_WANTED)
        props = spec["items"]["properties"]
        self.assertEqual(props["match"]["enum"], list(kb.MATCH_KINDS))
        self.assertEqual(props["purpose"]["enum"], list(kb.PURPOSES))
        self.assertEqual(props["position"]["enum"], list(kb.POSITIONS))
        self.assertEqual((props["min_level"]["minimum"], props["min_level"]["maximum"]), (1, 6))
        name = re.compile(props["name"]["pattern"])
        self.assertTrue(name.fullmatch("パーフェクトリィ"))
        for bad in ("a/b", "C:x", 'a"b', "", "x" * 41):
            self.assertIsNone(name.fullmatch(bad), bad)
        mirror = (UI / "protocol.js").read_text(encoding="utf-8")
        self.assertEqual(json.loads(mirror[mirror.index("export default ") + 15:mirror.rindex(";")]), protocol)

    def test_protocol_uses_only_host_validator_keywords(self):
        source = (ROOT / "browser_host" / "Ui" / "UiProtocol.cs").read_text(encoding="utf-8")
        block = source[source.index("KnownKeywords"):source.index("};", source.index("KnownKeywords"))]
        known = set(re.findall(r'"([$\w]+)"', block))
        used = set()

        def walk(schema):
            if not isinstance(schema, dict):
                return
            used.update(schema)
            for value in (schema.get("properties") or {}).values():
                walk(value)
            walk(schema.get("items"))
            walk(schema.get("additionalProperties"))
        protocol = json.loads((UI / "protocol.json").read_text(encoding="utf-8"))
        for value in protocol["definitions"].values():
            walk(value)
        for spec in protocol["commands"].values():
            walk(spec["params"])
        self.assertLessEqual(used, known, used - known)

    def test_ui_module_output_is_gitignored(self):
        self.assertTrue(str(kb.UI_MODULE_PATH.relative_to(ROOT)).replace("\\", "/").startswith("ui/game-assets/"))
        if not shutil.which("git"):
            self.skipTest("git not installed")
        for probe in ("ui/game-assets/knowhow.js", "dev_assets/private/knowhow/inventory.json"):
            self.assertEqual(subprocess.run(["git", "check-ignore", "-q", probe], cwd=ROOT).returncode, 0, probe)


if __name__ == "__main__":
    unittest.main()
