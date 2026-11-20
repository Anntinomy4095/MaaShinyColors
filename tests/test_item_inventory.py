"""「识别持有」道具读取 (agent/core/item_inventory.py, tools/item_inventory_scan.py, ui resource-panel.js). Offline only."""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import item_inventory as ii  # noqa: E402

LAYOUT = ii.LAYOUTS["produce_item_select"]
ITEM_ANCHORS = [("プロデュースアイテム準備", 1.0, (50, 20, 300, 30)), ("アイテム選択", 1.0, (990, 22, 128, 28)),
                ("ON", 1.0, (673, 135, 49, 31))]
HOME_ANCHORS = [("プロデュース", 1.0, (997, 640, 120, 32)), ("フェス", 1.0, (1150, 639, 75, 33)), ("営業", 1.0, (777, 667, 53, 30))]
D1 = ROOT / "dev_assets/private/recordings/wing-demo-20261005-0058-01"
D2 = ROOT / "dev_assets/private/recordings/wing-demo2-20261006-0106-02"
TEMPLATES = ROOT / "dev_assets/private/item_inventory/templates"


def synthetic_tile(seed: int):
    """A 108x112 tile with a seeded drawing (no game art): grey frame, coloured shapes, dark count band left blank."""
    from PIL import Image, ImageDraw

    rng = random.Random(seed)
    tile = Image.new("RGB", LAYOUT["tile"], (250, 250, 252))
    draw = ImageDraw.Draw(tile)
    draw.rectangle((0, 0, 107, 111), outline=(200, 200, 205), width=2)
    for _ in range(7):
        x0, y0 = rng.randint(8, 70), rng.randint(20, 60)
        x1, y1 = x0 + rng.randint(12, 34), y0 + rng.randint(10, 24)
        colour = tuple(rng.randint(0, 255) for _ in range(3))
        (draw.ellipse if rng.random() < .5 else draw.rectangle)((x0, y0, x1, y1), fill=colour, outline=(20, 20, 20))
    return tile


def draw_band(image, x, y):
    from PIL import ImageDraw

    ImageDraw.Draw(image).rectangle((x + 14, y + LAYOUT["band_offset"], x + 104, y + LAYOUT["band_offset"] + LAYOUT["band_h"] - 1),
                                     fill=(70, 70, 75))


def synthetic_frame(cells):
    """cells: {(row, col): seed or None for an empty slot}. Returns (frame, texts) with x<count> bands."""
    from PIL import Image

    frame = Image.new("RGB", ii.CANVAS, (255, 255, 255))
    texts = list(ITEM_ANCHORS)
    tops = [214, 332, 450]
    for (r, c), seed in cells.items():
        x, y = round(LAYOUT["x0"] + LAYOUT["dx"] * c), tops[r]
        if seed is None:
            continue
        frame.paste(synthetic_tile(seed), (x, y))
        draw_band(frame, x, y)
    return frame, texts


def count_text(r, c, value, score=0.99):
    x, y = round(LAYOUT["x0"] + LAYOUT["dx"] * c), [214, 332, 450][r]
    return (f"x{value}", score, (x + 50, y + 88, 52, 20))


def synthetic_index(seeds=(1, 2, 3, 4)):
    return ii.ItemIndex({f"item_{s}": synthetic_tile(s) for s in seeds})


class ParserTests(unittest.TestCase):
    def test_strict_parsers(self):
        self.assertEqual(ii.parse_count("x1874"), 1874)
        self.assertEqual(ii.parse_count("× 25"), 25)
        for bad in ("1874", "x18a4", "x", "x1234567", "", None):
            self.assertIsNone(ii.parse_count(bad))
        self.assertEqual(ii.parse_money("17,693,906"), 17693906)
        self.assertIsNone(ii.parse_money("17,69,906"))
        self.assertEqual(ii.parse_pair("300/300"), (300, 300))
        self.assertIsNone(ii.parse_pair("300/30b"))
        self.assertEqual(ii.parse_fes("20/20+0"), (20, 20, 0))
        self.assertEqual(ii.parse_fes("4/20"), (4, 20, None))
        self.assertIsNone(ii.parse_fes("21/20"))


class SyntheticGridTests(unittest.TestCase):
    def test_accepts_known_tiles_with_counts(self):
        frame, texts = synthetic_frame({(0, 0): 1, (0, 1): 2, (1, 3): 3, (2, 4): 4, (1, 0): None})
        texts += [count_text(0, 0, 12), count_text(0, 1, 7), count_text(1, 3, 1874), count_text(2, 4, 3)]
        result = ii.recognise(frame, synthetic_index(), "produce_item_select", texts, frame_name="synthetic.png")
        self.assertTrue(result["screen_ok"])
        self.assertEqual(result["rows"], [214, 332, 450])
        self.assertTrue(result["favorites_only"])
        self.assertEqual({k: v["count"] for k, v in result["items"].items()},
                         {"item_1": 12, "item_2": 7, "item_3": 1874, "item_4": 3})
        self.assertTrue(all(v["frame"] == "synthetic.png" and v["confidence"] >= 0.8 for v in result["items"].values()))

    def test_unknown_tile_and_bad_counts_are_refused(self):
        frame, texts = synthetic_frame({(0, 0): 1, (0, 1): 99, (0, 2): 2, (0, 3): 3, (0, 4): 4})
        texts += [count_text(0, 0, 5, score=0.30), count_text(0, 1, 9), count_text(0, 3, 1), count_text(0, 3, 2),
                  ("x1O", 0.95, (round(LAYOUT["x0"] + LAYOUT["dx"] * 4) + 50, 302, 40, 20))]
        result = ii.recognise(frame, synthetic_index(), "produce_item_select", texts)
        reasons = {t["slot"]: t["reason"] for t in result["tiles"] if t["slot"].startswith("r1")}
        self.assertEqual(reasons["r1c1"], "count_low_ocr")       # count OCR below min_ocr
        self.assertIn(reasons["r1c2"], {"low_score", "verify_failed", "ambiguous_item", "structure_mismatch"})  # unknown icon
        self.assertEqual(reasons["r1c3"], "count_unread")        # no count text
        self.assertEqual(reasons["r1c4"], "count_ambiguous")
        self.assertEqual(reasons["r1c5"], "count_unread")        # 'x1O' fails the strict regex
        self.assertEqual(result["items"], {})

    def test_low_confidence_is_refused_even_when_checks_pass(self):
        frame, texts = synthetic_frame({(0, 0): 1})
        texts += [count_text(0, 0, 5)]
        result = ii.recognise(frame, synthetic_index(), "produce_item_select", texts, thresholds={"min_confidence": 1.01})
        self.assertEqual(result["items"], {})
        self.assertEqual(result["tiles"][0]["reason"], "low_confidence")

    def test_whole_frame_refusals(self):
        frame, texts = synthetic_frame({(0, 0): 1})
        index = synthetic_index()
        self.assertEqual(ii.recognise(frame, index, "produce_item_select", texts + [("Now Loading", .9, (829, 653, 308, 60))])["refused"],
                         "loading_overlay")
        self.assertEqual(ii.recognise(frame, index, "produce_item_select", texts + [("使用確認", .9, (400, 90, 120, 30))])["refused"],
                         "dialog_overlay")
        self.assertEqual(ii.recognise(frame, index, "produce_item_select", texts[1:])["refused"], "screen_guard_failed")
        with self.assertRaises(ValueError):
            ii.recognise(frame.resize((640, 360)), index, "produce_item_select", texts)
        with self.assertRaises(ValueError):
            ii.recognise(frame, index, "item_list", texts)   # アイテム一覧 has no sample -> no layout

    def test_same_item_twice_in_one_frame_is_refused(self):
        frame, texts = synthetic_frame({(0, 0): 1, (0, 1): 1})
        texts += [count_text(0, 0, 5), count_text(0, 1, 6)]
        result = ii.recognise(frame, synthetic_index(), "produce_item_select", texts)
        self.assertEqual(result["items"], {})
        self.assertEqual(result["duplicated"], ["item_1"])

    def test_atlas_style_reference_round_trip(self):
        from PIL import Image

        art = synthetic_tile(7).resize((148, 148))
        ref = ii.atlas_tile_as_grid_tile(art)
        self.assertEqual(ref.size, (108, 112))
        index = ii.ItemIndex({"skip_ticket": ref, **{f"item_{s}": synthetic_tile(s) for s in (1, 2)}})
        frame = Image.new("RGB", ii.CANVAS, (255, 255, 255))
        x = round(LAYOUT["x0"])
        frame.paste(ref, (x, 214))
        draw_band(frame, x, 214)
        result = ii.recognise(frame, index, "produce_item_select", ITEM_ANCHORS + [count_text(0, 0, 610)])
        self.assertEqual(result["items"]["skip_ticket"]["count"], 610)

    def test_merge_keeps_latest_value(self):
        merged = ii.merge_reads([{"items": {"money": {"count": 1, "confidence": 1.0, "frame": "a"}}},
                                 {"items": {"money": {"count": 2, "confidence": 1.0, "frame": "b"}}}], ["t1", "t2"])
        self.assertEqual(merged["money"], {"count": 2, "confidence": 1.0, "frame": "b", "observed_at": "t2", "frames": 2})


class HomeCounterTests(unittest.TestCase):
    def frame(self):
        from PIL import Image
        return Image.new("RGB", ii.CANVAS)

    def test_reads_counters_and_refuses_malformed(self):
        texts = HOME_ANCHORS + [("17,693,906", 1.0, (354, 17, 104, 22)), ("51,167", 1.0, (529, 15, 69, 26)),
                                ("300/30b", 0.72, (1005, 665, 103, 33)), ("20/20+0", 0.99, (1138, 671, 95, 22)),
                                ("プロデューサー", 1.0, (50, 9, 78, 39))]
        result = ii.recognise(self.frame(), None, "home_counters", texts, frame_name="home.png")
        self.assertEqual(result["items"]["money"]["count"], 17693906)
        self.assertTrue(result["items"]["jewel"]["indicator_only"])
        self.assertEqual(result["items"]["fes"], {"count": 20, "confidence": 0.99, "frame": "home.png", "max": 20, "plus": 0})
        self.assertNotIn("stamina", result["items"])            # 300/30b: format mismatch, never guessed
        self.assertEqual(result["counters"]["stamina"]["reason"], "format_mismatch")
        self.assertNotIn("プロデューサー", json.dumps(result, ensure_ascii=False))   # player name is never read

    def test_low_ocr_and_dialog_are_refused(self):
        texts = HOME_ANCHORS + [("17,693,906", 0.4, (354, 17, 104, 22))]
        result = ii.recognise(self.frame(), None, "home_counters", texts)
        self.assertEqual(result["counters"]["money"]["reason"], "low_ocr")
        self.assertEqual(result["items"], {})
        self.assertEqual(ii.recognise(self.frame(), None, "home_counters", texts + [("OK", .99, (500, 560, 60, 40))])["refused"],
                         "dialog_overlay")


def _dump_texts(analysis: str, seq: int):
    path = ROOT / "dev_assets/private" / analysis / "ocr.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["seq"] == seq:
            return row["texts"]
    return None


@unittest.skipUnless(TEMPLATES.is_dir() and (D2 / "frame-000448.png").is_file()
                     and (ROOT / "dev_assets/private/wing-demo2-analysis/ocr.jsonl").is_file()
                     and (ROOT / "dev_assets/private/item_inventory/truth.json").is_file(), "private captures not present")
class RealFrameTests(unittest.TestCase):
    """The user's own captures (private). Templates come from demo #233/#239; demo2 is a later, separate session."""

    @classmethod
    def setUpClass(cls):
        from tools import item_inventory_scan as scan
        cls.scan = scan
        cls.index = scan.load_index()
        cls.truth = json.loads((ROOT / "dev_assets/private/item_inventory/truth.json").read_text(encoding="utf-8"))

    def read(self, folder, analysis, seq):
        path = folder / f"frame-{seq:06d}.png"
        return path, ii.recognise(self.scan.load_image(path), self.index, "produce_item_select", _dump_texts(analysis, seq))

    def test_item_grid_accuracy(self):
        correct = accepted = 0
        for folder, analysis, seq in ((D2, "wing-demo2-analysis", 448), (D2, "wing-demo2-analysis", 447),
                                      (D1, "wing-demo-analysis", 233)):
            path, result = self.read(folder, analysis, seq)
            truth = self.truth[path.relative_to(ROOT).as_posix()]
            for tile in result["tiles"]:
                if tile["status"] == "accepted":
                    accepted += 1
                    self.assertEqual([tile["item_id"], tile["count"]], truth[tile["slot"]], f"{path.name} {tile['slot']}")
                    correct += 1
        self.assertEqual((accepted, correct), (45, 45))

    def test_dialog_and_transition_frames_are_not_misread(self):
        _, dialog = self.read(D2, "wing-demo2-analysis", 451)
        self.assertEqual(dialog["refused"], "dialog_overlay")
        path, fading = self.read(D2, "wing-demo2-analysis", 442)
        truth = self.truth[path.relative_to(ROOT).as_posix()]
        for tile in fading["tiles"]:
            if tile["status"] == "accepted":
                self.assertEqual([tile["item_id"], tile["count"]], truth[tile["slot"]])

    def test_random_crops_are_rejected(self):
        th = ii.DEFAULT_THRESHOLDS
        rng = random.Random(3)
        frames = [D2 / "frame-000001.png", D2 / "frame-002458.png", D1 / "frame-000100.png", D1 / "frame-000130.png"]
        passed = 0
        for path in frames:
            image = self.scan.load_image(path)
            for _ in range(30):
                x, y = rng.randint(0, 1172), rng.randint(0, 608)
                best, score, margin, verify, edge, _ = self.index.match(image.crop((x, y, x + 108, y + 112)))
                passed += score >= th["min_score"] and verify >= th["min_verify"] and margin >= th["min_margin"] and edge >= th["min_edge"]
        self.assertEqual(passed, 0)


class DraftPipelineTests(unittest.TestCase):
    def test_draft_is_disabled_recognition_only_and_matches_module(self):
        draft = json.loads((ROOT / "assets/resource/base/pipeline/item_inventory_draft.json").read_text(encoding="utf-8"))
        for name, node in draft.items():
            self.assertTrue(name.startswith("SC.ItemInventory."))
            self.assertIs(node["enabled"], False)
            self.assertNotIn("action", node)
            self.assertNotIn("next", node)
        anchors = {tuple(n["roi"]): n["expected"][0] for k, n in draft.items() if ".Anchor." in k}
        for layout in ii.LAYOUTS.values():
            for roi, pattern in layout["anchors"].values():
                self.assertEqual(anchors[tuple(roi)], pattern)
        for path in (ROOT / "assets/tasks").glob("*.json"):
            self.assertNotIn("SC.ItemInventory", path.read_text(encoding="utf-8"))


class ScanToolTests(unittest.TestCase):
    def test_output_paths_are_guarded_and_merge_keeps_newest(self):
        from tools import item_inventory_scan as scan

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                scan.write_module({"version": 1}, Path(tmp) / "item-scan.js")
        old = {"frames": [{"file": "a"}], "items": [{"id": "money", "count": 1, "observed_at": "2026-10-05T00:00:00+11:00"}],
               "cards": []}
        new = {"frames": [{"file": "b"}], "items": [{"id": "money", "count": 2, "observed_at": "2026-10-06T00:00:00+11:00"}],
               "cards": [{"id": "2040010010", "observed_at": "2026-10-06T00:00:00+11:00"}]}
        merged = scan.merge_module(old, new)
        self.assertEqual(merged["items"][0]["count"], 2)
        self.assertEqual(len(merged["frames"]), 2)
        self.assertEqual(scan.merge_module(new, old)["items"][0]["count"], 2)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ResourcePanelUiTests(unittest.TestCase):
    def run_node(self, body, data):
        script = ("import './tests/fixtures/ui/fake_dom.mjs';"
                  "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
                  "const m = await import('./ui/components/resource-panel.js');"
                  "const data = JSON.parse(process.argv[1]);" + body)
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(data, ensure_ascii=False)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_missing_values_show_unobserved_never_zero(self):
        scan = {"version": 1, "generated_at": "2026-10-06T01:14:00+11:00", "items": [
            {"id": "money", "count": 17693906, "confidence": 1, "frame": "dev_assets/private/x/frame-000001.png", "observed_at": "2026-10-06T01:08:41+11:00"},
            {"id": "p_secret_memo", "kind": "produce_item", "name": "秘密のメモ帳", "count": 7619, "confidence": 0.99,
             "frame": "f.png", "observed_at": "2026-10-06T01:14:44+11:00", "icon": "game-assets/item-scan/p_secret_memo.png"},
            {"id": "BAD id", "count": 3}, {"id": "skip_ticket", "count": -1}, {"id": "gold_ticket", "count": "5"}],
            "cards": [{"id": "2040010010", "frame": "f.png", "observed_at": "2026-10-06T01:00:00+11:00", "score": 0.9}, {"id": "x"}]}
        runs = [{"kind": "match", "run_id": "r1", "ts": "2026-10-06T02:00:00+11:00",
                 "readings": {"observations": [{"rights_after": [6, 20]}], "items": {"bronze_ticket": {"count": 41}}}}]
        out = self.run_node(
            "const state = { bridgeKind: 'mock', prefs: { owned_cards: { p: [], s: [] } }, annotations: {}, runReadings: data.runs };"
            "const scan = m.cleanItemScan(data.scan);"
            "const rows = m.holdingRows(state, scan);"
            "const view = m.holdingsView(state, { itemScan: scan, cardScan: { cards: [{ id: '2040010010', score: 0.9, frames: 1 }], generatedAt: '' } }, {});"
            "const empty = m.holdingsView(state, { itemScan: m.cleanItemScan(null), cardScan: { cards: [] } }, {});"
            "const money = m.holdingTile(rows.find((r) => r.id === 'money'));"
            "console.log(JSON.stringify({ rows: rows.map((r) => [r.id, r.count, r.max, r.source]), text: view.textContent,"
            " emptyText: empty.textContent, ids: scan.items.map((i) => i.id), cards: Object.keys(scan.cards),"
            " moneyTitle: money.getAttribute('title'), moneyText: money.textContent }));",
            {"scan": scan, "runs": runs})
        rows = {r[0]: r for r in out["rows"]}
        self.assertEqual(out["ids"], ["money", "p_secret_memo"])
        self.assertEqual(out["cards"], ["2040010010"])
        self.assertEqual(rows["money"][1], 17693906)
        self.assertEqual(rows["fes"][1:3], [6, 20])                # newer engine reading
        self.assertEqual(rows["bronze_ticket"][1], 41)             # readings.items hook
        self.assertIsNone(rows["skip_ticket"][1])                  # invalid scan entry -> unobserved, not 0
        self.assertIsNone(rows["gold_ticket"][1])
        self.assertIn("尚未读取", out["text"])
        self.assertIn("秘密のメモ帳", out["text"])
        self.assertIn("frame-000001.png", out["moneyTitle"])        # source frame + time on the tile (and its detail line)
        self.assertIn("17,693,906", out["moneyTitle"])            # exact value in the title, compact on the tile
        self.assertIn("1769万", out["moneyText"])
        self.assertNotIn("—0", out["text"])
        self.assertNotIn("null", out["text"])
        # Engine readings fill fes and bronze_ticket. Unobserved: マニー / 体力 / ジュエル in the summary strip, 8 of the 9
        # other account tiles (铜训练券 observed) — 11 × 尚未读取, never 0. The empty card list now reads
        # 「暂无卡牌」「扫描持有卡或导入识别结果」 (copy pass), so it no longer repeats 尚未读取.
        self.assertEqual(out["emptyText"].count("尚未读取"), 11)
        self.assertIn("暂无卡牌", out["emptyText"])

    def test_produce_items_get_wiki_names_and_crops_are_marked(self):
        scan = {"version": 1, "items": [
            {"id": "p_tart_blue_2", "kind": "produce_item", "count": 662, "look": "蓝条纹盒 2 个草莓塔",
             "icon": "game-assets/item-scan/p_tart_blue_2.png"},
            {"id": "p_secret_memo", "kind": "produce_item", "name": "秘密のメモ帳", "count": 7620}]}
        out = self.run_node(
            "const state = { prefs: { owned_cards: { p: [], s: [] } }, annotations: {}, runReadings: [] };"
            "const rows = m.holdingRows(state, m.cleanItemScan(data.scan));"
            "const tart = rows.find((r) => r.id === 'p_tart_blue_2');"
            "const li = m.holdingTile(tart);"
            "const img = li.childNodes[0].childNodes[0].childNodes[0];"   # tile > icon slot > res-icon well > img
            "console.log(JSON.stringify({ label: tart.label, cls: img.getAttribute('class'), src: img.dataset.iconSource,"
            " memo: rows.find((r) => r.id === 'p_secret_memo').label }));", {"scan": scan})
        self.assertEqual(out["label"], "ヒーリングフルーツタルト(2個入り)")
        self.assertEqual(out["memo"], "秘密のメモ帳")
        self.assertIn("is-crop", out["cls"])  # last-resort screenshot crop, visibly marked
        self.assertEqual(out["src"], "crop")

    def test_import_needs_confirmation(self):
        out = self.run_node(
            "let sent = null; const actions = { setPref: (k, v) => { sent = [k, v]; } };"
            "const state = { bridgeKind: 'mock', prefs: { owned_cards: { p: [], s: [] } }, annotations: {}, runReadings: [] };"
            "let opened = 0;"
            "const view = m.holdingsView(state, { itemScan: m.cleanItemScan(null), cardScan: { cards: [{ id: '2040010010', score: 0.9, frames: 1 }] } },"
            " { onImport: () => { opened += 1; } });"
            "let dialog = null; const fakeOpen = (args) => { dialog = args; return 'opened'; };"
            "const result = m.importOwnedCards(state, actions, () => {}, fakeOpen);"
            "const before = sent;"   # dialog open, nothing confirmed yet
            "dialog.onApply({ p: [], s: ['2040010010'] });"   # what the dialog's 确定 (onCommit) does
            "console.log(JSON.stringify({ before, sent, result, owned: dialog.owned, text: view.textContent }));", {})
        self.assertIsNone(out["before"])
        self.assertEqual(out["result"], "opened")
        self.assertEqual(out["owned"], {"p": [], "s": []})
        self.assertEqual(out["sent"][0], "owned_cards")
        self.assertEqual(out["sent"][1]["s"], ["2040010010"])
        self.assertIn("导入持有卡牌", out["text"])
        # The only path from the dialog to saving is team-import's onCommit (the 确定 button of openGridPicker).
        source = (ROOT / "ui/components/team-import.js").read_text(encoding="utf-8")
        self.assertIn("onCommit: (chosen) => onApply(", source)
        panel = (ROOT / "ui/components/resource-panel.js").read_text(encoding="utf-8")
        self.assertEqual(panel.count("saveOwnedCards(state, actions"), 1)
        self.assertIn("onApply: (merged) =>", panel)


if __name__ == "__main__":
    unittest.main()
