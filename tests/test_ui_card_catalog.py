"""Offline coverage for complete card directories with partially cached thumbnails."""
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from tools import ui_game_assets as build
from tools import ui_game_assets_fetch as fetch


ROOT = Path(__file__).resolve().parent.parent


class CardCatalogTests(unittest.TestCase):
    def test_builder_retains_named_p_and_s_cards_without_images(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp, ExitStack() as patches:
            root = Path(tmp)
            db = root / "cards.json"
            db.write_text(json.dumps({"cards": [
                {"card_id": "2040010010", "kind": "S", "rarity": "SSR", "idol": "真乃", "title": "S title"},
                {"card_id": "1040010010", "kind": "P", "rarity": "SSR", "idol": "真乃", "title": "P title"},
                {"card_id": "bad", "kind": "S", "rarity": "SSR"},
            ]}), encoding="utf-8")
            for name, value in (("SRC_DIR", root), ("DB_CARDS_FILE", db),
                                ("WIKI_CARDS_FILE", root / "no-s.json"),
                                ("WIKI_P_CARDS_FILE", root / "no-p.json")):
                patches.enter_context(mock.patch.object(build, name, value))
            patches.enter_context(mock.patch.object(build, "db_icon_file", return_value=None))
            patches.enter_context(mock.patch.object(build, "rel", lambda p: Path(p).name))
            manifest = {"skipped": []}
            for kind, field in (("S", "cards"), ("P", "p_cards")):
                self.assertEqual(build.build_cards(root, manifest, kind), 1)
                card, = manifest[field]
                self.assertEqual((card["idol"], card["title"]), ("真乃", f"{kind} title"))
                self.assertIsNone(card["src"])
                self.assertIsNone(card["thumb_source"])
                self.assertTrue(card["thumbnail_missing"])
            self.assertEqual(manifest["card_source"]["missing_thumbnails"], 1)
            self.assertFalse((root / "cards").exists())

    def fetch_catalog(self, info, *, max_bytes=0, idols=None, image_error=None):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp, ExitStack() as patches:
            root = Path(tmp)
            for name, value in (("SRC_DIR", root), ("DB_DIR", root), ("DB_IDOLS_DIR", root / "idols"),
                                ("DB_ICON_DIR", root / "icons"), ("DB_CARDS_FILE", root / "cards.json")):
                patches.enter_context(mock.patch.object(fetch, name, value))
            patches.enter_context(mock.patch.object(fetch, "is_gitignored", return_value=True))

            class OfflineFetcher:
                total = 0

                def get(self, url):
                    if url == fetch.DB_IDOL_LIST_URL:
                        data = [{"idolId": 1}] if idols is None else idols
                    elif url == fetch.db_idol_url(1):
                        if isinstance(info, Exception):
                            raise info
                        data = info
                    else:
                        if image_error is not None:
                            raise image_error
                        raise AssertionError(f"Unexpected image fetch: {url}")
                    encoded = json.dumps(data).encode("utf-8")
                    self.total += len(encoded)
                    return encoded

            report = fetch.fetch_db(OfflineFetcher(), max_bytes=max_bytes)
            catalog = json.loads((root / "cards.json").read_text(encoding="utf-8")) if (root / "cards.json").exists() else None
            return report, catalog

    def test_thumbnail_budget_does_not_invalidate_completed_catalog(self):
        report, catalog = self.fetch_catalog({"cardLists": [
            {"enzaId": "2040010010", "cardType": "S_SSR", "cardName": "【S title】真乃"},
            {"enzaId": "1040010010", "cardType": "P_SSR", "cardName": "【P title】真乃"},
        ]})
        self.assertTrue(report["catalog_complete"])
        self.assertIsNotNone(report["stopped"])
        self.assertEqual(report["downloaded"], 0)
        self.assertEqual(len(catalog["cards"]), 2)

    def test_thumbnail_failure_preserves_catalog_completion(self):
        for error in (OSError("image unavailable"), fetch.RateLimited("rate limited")):
            with self.subTest(error=error):
                report, catalog = self.fetch_catalog({"cardLists": [
                    {"enzaId": "2040010010", "cardType": "S_SSR", "cardName": "card"},
                ]}, max_bytes=1000000, image_error=error)
                self.assertTrue(report["catalog_complete"])
                self.assertEqual(len(catalog["cards"]), 1)
                self.assertTrue(report["failed"] or report["stopped"])

    def test_failed_or_malformed_directory_is_incomplete(self):
        for info in (OSError("offline failure"), {}, {"cardLists": None}):
            with self.subTest(info=info):
                report, _ = self.fetch_catalog(info)
                self.assertFalse(report["catalog_complete"])
                self.assertIsNotNone(report["stopped"])
        for idols in ({}, [], [{"idolId": True}], [{"idolId": 1}, {"idolId": "unknown"}]):
            with self.subTest(idols=idols):
                report, _ = self.fetch_catalog({"cardLists": []}, idols=idols)
                self.assertFalse(report["catalog_complete"])
        with mock.patch.object(fetch, "DB_MAX_IDOLS", 1):
            report, _ = self.fetch_catalog({"cardLists": []}, idols=[{"idolId": 1}, {"idolId": 2}])
            self.assertFalse(report["catalog_complete"])

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_ui_keeps_missing_thumbnail_cards_in_pickable_directory(self):
        script = """
import { useGameAssets, cardArt, anyCardArt } from './ui/components/game-assets.js';
import { cardsOf, cardFilters } from './ui/components/team-picker.js';
const s = {id:'2040010010', idol:'真乃', title:'S title', rarity:'SSR', src:null, thumbnail_missing:true};
const p = {...s, id:'1040010010', title:'P title'};
const a = useGameAssets({cards:[s, {...s, id:'bad'}, {...s, src:'https://bad/x.png'}, {...s, thumbnail_missing:false}], p_cards:[p]});
console.log(JSON.stringify({s:cardsOf('S'), p:cardsOf('P'), sArt:cardArt(s.id), pArt:anyCardArt(p.id), filters:cardFilters(a.cards)[1].options}));
"""
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(len(out["s"]), 1)
        self.assertEqual(len(out["p"]), 1)
        self.assertEqual(out["sArt"]["title"], "S title")
        self.assertEqual(out["pArt"]["title"], "P title")
        self.assertIsNone(out["sArt"]["src"])
        self.assertTrue(out["sArt"]["thumbnailMissing"])
        self.assertEqual(out["filters"][1], ["001", "真乃"])


if __name__ == "__main__":
    unittest.main()
