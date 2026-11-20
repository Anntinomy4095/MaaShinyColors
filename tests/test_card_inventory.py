"""Card inventory recognition (agent/core/card_inventory.py, tools/card_inventory_scan.py). Offline: synthetic frames
always; the user's private captures when present (skipped otherwise). No device, no input, no network."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent.core import card_inventory as ci
from tools import card_inventory_scan as scan_tool

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "dev_assets" / "private" / "recordings" / "wing-demo-20261005-0058-01"
TRAINING = ROOT / "dev_assets" / "private" / "nav-v15-training-list-back-readonly-frames" / "frame-001.png"
ASSETS = ROOT / "ui" / "game-assets" / "manifest.json"


def list_order(card_id):
    """Default in-game order seen in the captures: rarity descending, then idol number, then card serial."""
    return (-int(card_id[1:3]), card_id[3:])


def synthetic_refs(count=24, seed=3):
    import numpy as np
    from PIL import Image, ImageFilter

    rng = np.random.default_rng(seed)
    refs = {}
    for i in range(count):
        noise = (rng.random((12, 12, 3)) * 255).astype("uint8")
        image = Image.fromarray(noise, "RGB").resize((96, 96), Image.BICUBIC).filter(ImageFilter.GaussianBlur(1))
        card_id = f"204{i + 10:03d}0010"  # distinct idol numbers (digits 4-6)
        refs[card_id] = image
    return refs


def frame_with(refs, layout="idol_list", offset=196, blank=()):
    from PIL import Image, ImageDraw

    frame = Image.new("RGB", ci.CANVAS, (238, 232, 240))
    spec = ci.LAYOUTS[layout]
    placed = {}
    ids = list(refs)
    rows = [y for y in range(offset, spec["viewport"][1], spec["dy"]) if y + spec["tile"] <= spec["viewport"][1]]
    k = 0
    for r, y in enumerate(rows):
        for c in range(spec["cols"]):
            x = spec["x0"] + spec["dx"] * c
            slot = f"r{r + 1}c{c + 1}"
            ImageDraw.Draw(frame).rectangle((x, y, x + spec["tile"] - 1, y + spec["tile"] - 1), fill=(200, 120, 180))
            if slot in blank:
                continue
            frame.paste(refs[ids[k]].resize((spec["tile"], spec["tile"])), (x, y))  # the wiki icon is the in-game tile art
            placed[slot] = ids[k]
            k += 1
    return frame, placed


class SyntheticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.refs = synthetic_refs()
        cls.index = ci.CardIndex(list(cls.refs), list(cls.refs.values()))

    def test_grid_offset_search_and_matches(self):
        frame, placed = frame_with(self.refs, offset=214, blank=("r2c4",))
        result = ci.recognise(frame, self.index, "idol_list")
        self.assertEqual(result["offset"], 214)
        got = {t["slot"]: t["card_id"] for t in result["tiles"] if t["status"] == "accepted"}
        self.assertEqual(got, placed)
        blank = next(t for t in result["tiles"] if t["slot"] == "r2c4")
        self.assertEqual(blank["status"], "rejected")  # an empty tile is refused, never guessed
        self.assertIsNone(blank["card_id"])

    def test_rarity_frame_check(self):
        """Third demo: a same-idol variant missing from the references matched an R card inside a rainbow (SSR) frame;
        the frame colour must agree with the matched rarity, otherwise the tile is rejected (never guessed)."""
        import colorsys
        from PIL import ImageDraw
        frame, placed = frame_with(self.refs, offset=214)
        slot, card = next(iter(placed.items()))
        spec = ci.LAYOUTS["idol_list"]
        x, y, size = spec["x0"], 214, spec["tile"]
        draw = ImageDraw.Draw(frame)
        for i in range(size):                    # rainbow border: the hue changes along the right and top edges
            rgb = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(i / size, 0.6, 1.0))
            for px in ((x + size - 3, y + i), (x + size - 4, y + i), (x + i, y + 2)):
                draw.point(px, fill=rgb)
        self.assertEqual(ci.frame_tier(frame, (x, y, spec["tile"], spec["tile"])), "rainbow")
        index = ci.CardIndex(list(self.refs), list(self.refs.values()),
                             meta={cid: {"rarity": "R"} for cid in self.refs})
        result = ci.recognise(frame, index, "idol_list")
        tile = next(t for t in result["tiles"] if t["slot"] == slot)
        self.assertEqual((tile["status"], tile["reason"], tile["card_id"]), ("rejected", "rarity_mismatch", None))
        # no rarity metadata (synthetic index) -> the check does not apply
        plain = ci.recognise(frame, self.index, "idol_list")
        self.assertEqual(next(t for t in plain["tiles"] if t["slot"] == slot)["card_id"], card)

    def test_identical_art_variants_are_split_by_the_frame(self):
        """ShinyColorsDB set: the same art exists as R【白いツバサ】 and as an SR 93… re-release. Only the rarity frame
        separates them, so the variant choice is restricted to the observed frame tier (rainbow -> no variant fits)."""
        from PIL import ImageDraw

        frame, placed = frame_with(self.refs, offset=214)
        slot, card = next(iter(placed.items()))
        twin = "293" + card[3:]                  # same idol (digits 4-6), same art, SR
        ids = [*self.refs, twin]
        images = [*self.refs.values(), self.refs[card]]
        meta = {cid: {"rarity": "SSR"} for cid in self.refs} | {card: {"rarity": "R"}, twin: {"rarity": "SR"}}
        index = ci.CardIndex(ids, images, meta=meta)
        spec = ci.LAYOUTS["idol_list"]
        x, y, size = spec["x0"], 214, spec["tile"]
        expected = {"silver": ("accepted", card), "gold": ("accepted", twin), "rainbow": ("rejected", None)}
        colours = {"silver": (200, 200, 206), "gold": (232, 182, 40)}
        for tier, (status, want) in expected.items():
            painted = frame.copy()
            draw = ImageDraw.Draw(painted)
            if tier == "rainbow":
                import colorsys
                for i in range(size):
                    rgb = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(i / size, 0.6, 1.0))
                    draw.line((x + size - 6, y + i, x + size - 1, y + i), fill=rgb)
                    draw.line((x + i, y, x + i, y + 4), fill=rgb)
            else:
                draw.rectangle((x + size - 6, y, x + size - 1, y + size - 1), fill=colours[tier])
                draw.rectangle((x, y, x + size - 1, y + 4), fill=colours[tier])
            self.assertEqual(ci.frame_tier(painted, (x, y, size, size)), tier)
            tile = next(t for t in ci.recognise(painted, index, "idol_list")["tiles"] if t["slot"] == slot)
            self.assertEqual((tile["status"], tile["card_id"]), (status, want), tier)
            if status == "rejected":
                self.assertEqual(tile["reason"], "rarity_mismatch")
        # Without a known frame (unit formation boxes) identical twins stay ambiguous -> rejected, never guessed.
        match = index.match(frame.crop((x, y, x + size, y + size)), slot, (x, y, size, size))
        self.assertEqual((match.status, match.reason), ("rejected", "ambiguous_variant"))

    def test_wrong_canvas_and_merge(self):
        from PIL import Image

        with self.assertRaises(ValueError):
            ci.recognise(Image.new("RGB", (1920, 1080)), self.index)
        merged = ci.merge_scans([{"accepted": ["2040100010", "1040010010"]}, {"accepted": ["2040100010"]}])
        self.assertEqual(merged, {"p": ["1040010010"], "s": ["2040100010"], "frames": 2})

    def test_thresholds_are_conservative(self):
        th = ci.DEFAULT_THRESHOLDS
        self.assertGreaterEqual(th["min_score"], 0.8)
        self.assertGreaterEqual(th["min_margin"], 0.05)
        self.assertGreaterEqual(th["min_edge"], 0.3)
        self.assertEqual(ci.idol_of("2050170010"), "017")
        self.assertIsNone(ci.idol_of("abc"))

    def test_module_has_no_input_or_network(self):
        for path in (ROOT / "agent" / "core" / "card_inventory.py", ROOT / "tools" / "card_inventory_scan.py"):
            text = path.read_text(encoding="utf-8")
            for word in ("urllib", "requests", "post_click", "Click", "BrowserBridge", "socket"):
                self.assertNotIn(word, text, (path.name, word))


class DraftPipelineTests(unittest.TestCase):
    def test_draft_is_disabled_read_only_and_unwired(self):
        draft = ROOT / "assets" / "resource" / "base" / "pipeline" / "card_inventory_draft.json"
        nodes = json.loads(draft.read_text(encoding="utf-8"))
        self.assertTrue(nodes)
        for name, node in nodes.items():
            self.assertTrue(name.startswith("SC.CardInventory.Anchor."), name)
            self.assertIs(node["enabled"], False, name)
            self.assertEqual(node["recognition"], "OCR", name)
            for key in ("action", "next", "target"):
                self.assertNotIn(key, node, name)
        self.assertNotIn("SC.CardInventory.", (ROOT / "assets" / "interface.json").read_text(encoding="utf-8"))
        for path in (ROOT / "assets").rglob("*.json"):
            if path != draft:
                self.assertNotIn("SC.CardInventory.", path.read_text(encoding="utf-8"), path.name)


@unittest.skipUnless(ASSETS.is_file() and DEMO.is_dir(), "private captures / local wiki icons not present")
class PrivateCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = ci.CardIndex.from_assets()

    def open(self, n):
        from PIL import Image

        return Image.open(DEMO / f"frame-{n:06d}.png")

    def test_formation_frame(self):
        result = ci.recognise(self.open(163), self.index, "unit_formation")
        got = {t["slot"]: t["card_id"] for t in result["tiles"] if t["status"] == "accepted"}
        self.assertEqual(got.get("P"), "1040130050")  # 有栖川夏葉【cheer+】 (WING demo analysis)
        self.assertEqual(got.get("S1"), "2040050080")  # 田中摩美々 (purple twin tails)
        for slot, card in got.items():
            self.assertIsNotNone(card, slot)
        idols = [ci.idol_of(c) for c in got.values()]
        self.assertEqual(len(idols), len(set(idols)))  # the game's own unit never repeats an idol

    def test_idol_list_frames_are_sorted_and_consistent(self):
        seen = {}
        for n in (130, 143, 157, 159):
            result = ci.recognise(self.open(n), self.index, "idol_list")
            accepted = [t for t in result["tiles"] if t["status"] == "accepted"]
            self.assertGreaterEqual(len(accepted), 15, n)
            ids = [t["card_id"] for t in accepted]
            self.assertEqual(ids, sorted(ids, key=list_order), n)  # sorted list: a wrong match would break the order
            self.assertTrue(all(i.startswith("1") for i in ids), n)  # プロデュースアイドル編成 lists P cards
            for t in accepted:
                seen.setdefault(t["slot"] + str(n), t["card_id"])
        r130 = ci.recognise(self.open(130), self.index, "idol_list")
        r157 = ci.recognise(self.open(157), self.index, "idol_list")
        a = [t["card_id"] for t in r130["tiles"][7:] if t["status"] == "accepted"]
        b = [t["card_id"] for t in r157["tiles"][:14] if t["status"] == "accepted"]
        self.assertTrue(set(a) & set(b))  # the scrolled rows agree across frames

    def test_non_list_frames_accept_nothing(self):
        for n in range(250, 960, 45):
            path = DEMO / f"frame-{n:06d}.png"
            if not path.is_file():
                continue
            for layout in ci.LAYOUTS:
                self.assertEqual(ci.recognise(self.open(n), self.index, layout)["accepted"], [], (n, layout))

    @unittest.skipUnless(TRAINING.is_file(), "training list capture not present")
    def test_training_list_frame(self):
        from PIL import Image

        result = ci.recognise(Image.open(TRAINING), self.index, "idol_list")
        ids = [t["card_id"] for t in result["tiles"] if t["status"] == "accepted"]
        self.assertGreaterEqual(len(ids), 10)
        self.assertTrue(all(i.startswith("2") for i in ids))
        self.assertEqual(ids, sorted(ids, key=list_order))

    def test_scan_tool_dry_run_and_write_guard(self):
        data = scan_tool.scan([DEMO / "frame-000157.png"], "idol_list", self.index)
        self.assertEqual(data["version"], 1)
        self.assertGreaterEqual(len(data["cards"]), 15)
        with self.assertRaises(ValueError):
            scan_tool.write_module(data, ROOT / "ui" / "elsewhere.js")
        if shutil.which("git"):
            self.assertEqual(subprocess.run(["git", "check-ignore", "-q", "ui/game-assets/inventory-scan.js"], cwd=ROOT).returncode, 0)


DEMO2 = ROOT / "dev_assets" / "private" / "recordings" / "wing-demo2-20261006-0106-02"


@unittest.skipUnless(ASSETS.is_file() and (DEMO2 / "frame-000438.png").is_file(), "second W.I.N.G. demo not present")
class SecondDemoInventory(unittest.TestCase):
    """2026-10-06 demo: the P / support pickers (scrolled by the user) and the unit page after the P-card change."""

    @classmethod
    def setUpClass(cls):
        cls.index = ci.CardIndex.from_assets()

    def open(self, n):
        from PIL import Image

        return Image.open(DEMO2 / f"frame-{n:06d}.png")

    def test_unit_after_p_change(self):
        result = ci.recognise(self.open(438), self.index, "unit_formation")
        got = {t["slot"]: t["card_id"] for t in result["tiles"] if t["status"] == "accepted"}
        # Cross-checked with the サポートアイドル詳細 pages the user opened (#340 咲耶, #382 結華, #392 摩美々) and the
        # P card of the final evaluation (#2730, 風野灯織【ひとりじゃない夜のこと】).
        self.assertEqual(got, {"P": "1040020080", "S1": "2040050080", "S2": "2040060080", "S4": "2040040090",
                               "guest": "2040070080"})

    def test_support_picker_frames(self):
        for n in (266, 290, 306, 315, 320):
            ids = ci.recognise(self.open(n), self.index, "idol_list")["accepted"]
            self.assertGreaterEqual(len(ids), 11, n)
            self.assertTrue(all(i.startswith("2") for i in ids), n)   # サポートアイドル編成 lists S cards only
            self.assertEqual(ids, sorted(ids, key=list_order), n)     # default order (デフォルト(W.I.N.G.用))


DEMO3 = ROOT / "dev_assets" / "private" / "recordings" / "wing-demo3-20261006-1643-02"


@unittest.skipUnless(ASSETS.is_file() and (DEMO3 / "frame-000048.png").is_file(), "third W.I.N.G. demo not present")
class ShinyColorsDbReferenceTests(unittest.TestCase):
    """2026-10-06: ShinyColorsDB icons preferred per card id (wiki fallback). Tiles checked by eye against the icons."""

    @classmethod
    def setUpClass(cls):
        cls.index = ci.CardIndex.from_assets()

    def tiles(self, folder, n):
        from PIL import Image

        result = ci.recognise(Image.open(folder / f"frame-{n:06d}.png"), self.index, "idol_list")
        return {t["slot"]: t["card_id"] for t in result["tiles"] if t["status"] == "accepted"}

    def test_r_white_wings_inside_silver_frames(self):
        # The DB set also has SR 93… re-releases with identical art; the silver frame picks the R card.
        got = self.tiles(DEMO3, 48)
        self.assertEqual((got.get("r2c7"), got.get("r3c1")), ("1020210010", "1020230010"))

    def test_cards_only_in_the_db_set(self):
        if "1940250010" not in self.index.ids:
            self.skipTest("ShinyColorsDB thumbnails not built")
        self.assertEqual(self.tiles(DEMO3, 51).get("r1c7"), "1940250010")   # 緋田美琴 SSR【アイドルロード】
        self.assertEqual(self.tiles(DEMO3, 8).get("r1c3"), "1940080010")    # 幽谷霧子 SSR【アイドルロード】
        if (DEMO2 / "frame-000218.png").is_file():
            self.assertEqual(self.tiles(DEMO2, 218).get("r1c2"), "1940010010")  # 櫻木真乃 SSR【アイドルロード】


if __name__ == "__main__":
    unittest.main()
