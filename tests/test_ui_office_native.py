"""本机通常服SD/立绘资源准备合同；使用合成资源，不访问网络。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import ui_office_native as native


class OfficeNativePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="office-native-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private = self.root / "private"
        self.output = self.root / "output"
        for name, value in (("ROOT", self.root), ("PRIVATE", self.private), ("OUTPUT", self.output)):
            context = patch.object(native, name, value)
            context.start()
            self.addCleanup(context.stop)
        self.cache = self.private / "mano"
        self.cache.mkdir(parents=True)
        directory = [{"dressName": "通常服", "assets": {"idols": [
            {"type": "cb_costume", "path": "spine/idols/cb_costume/ignored/"},
            {"type": "cb", "path": "spine/idols/cb/first/"},
            {"type": "stand", "path": "spine/idols/stand/first/"},
            {"type": "stand_costume", "path": "spine/idols/stand_costume/ignored/"},
        ]}}, {"dressName": "次の服", "assets": {"idols": [
            {"type": "stand", "path": "spine/idols/stand/second/"},
        ]}}]
        (self.cache / "dresslist.json").write_text(json.dumps(directory), encoding="utf-8")
        self.skeleton = {"skeleton": {"spine": "3.6.53", "width": 300, "height": 900},
                         "bones": [{"name": "root"}, {"name": "head", "parent": "root"}],
                         "skins": {"default": {}}, "animations": {"wait": {}, "yes": {}}}

    def cache_model(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "data.json").write_text(json.dumps(self.skeleton), encoding="utf-8")
        (directory / "data.atlas").write_text("\ndata.png\nsize: 1,1\n", encoding="utf-8")
        (directory / "data.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")

    def test_legacy_cb_cache_and_stand_are_separate_and_index_is_compatible(self):
        self.cache_model(self.cache)
        self.cache_model(self.cache / "stand")
        original = (self.cache / "data.json").read_bytes()
        with patch.object(native.urllib.request, "urlopen", side_effect=AssertionError("offline")):
            cb = native.prepare_character("mano", False)
            cb_module = (self.output / "mano.js").read_bytes()
            stand = native.prepare_character("mano", False, "stand")
            catalog = native.write_index()
        self.assertEqual((self.cache / "data.json").read_bytes(), original)
        self.assertEqual((self.output / "mano.js").read_bytes(), cb_module)
        self.assertEqual(cb["module"], "./mano.js")
        self.assertEqual(stand["module"], "./stand/mano.js")
        self.assertEqual(stand["bones"], self.skeleton["bones"])
        self.assertEqual(stand["spineVersion"], "3.6.53")
        self.assertEqual(catalog["mano"]["module"], cb["module"])
        self.assertEqual(catalog["mano"]["models"], {"cb": cb, "stand": stand})
        module_text = (self.output / "stand/mano.js").read_text(encoding="utf-8")
        module = json.loads(module_text.removeprefix("export default ").removesuffix(";\n"))
        self.assertEqual(module["skeleton"], self.skeleton)
        self.assertEqual(module["pages"], [{"name": "data.png", "src": "./stand/mano/data.png"}])
        self.assertTrue((self.output / "stand/mano/data.png").is_file())

    def test_missing_stand_sources_never_fetch_without_explicit_flag(self):
        self.cache_model(self.cache)
        with patch.object(native.urllib.request, "urlopen") as request:
            with self.assertRaises(FileNotFoundError):
                native.prepare_character("mano", False, "stand")
        request.assert_not_called()
        self.assertFalse((self.output / "stand/mano.js").exists())

    def test_first_ssr_uses_casual_clothes_even_when_viewer_omits_the_variant(self):
        directory = [
            {"enzaId": "1940010010", "dressType": "P_SSR", "dressName": "アイドルロード"},
            {"enzaId": "1040010020", "dressType": "P_SSR", "dressName": "次の服",
             "assets": {"idols": [{"type": "cb", "path": "spine/idols/cb/1040010020/"}]}},
            {"enzaId": "1040010010", "dressType": "P_SSR", "dressName": "首张卡",
             "assets": {"idols": [{"type": "cb_costume", "path": "spine/idols/cb_costume/1040010010/"}]}},
        ]
        (self.cache / "dresslist.json").write_text(json.dumps(directory), encoding="utf-8")
        self.cache_model(self.cache / "first-ssr-casual")
        with patch.object(native.urllib.request, "urlopen", side_effect=AssertionError("offline")):
            model = native.prepare_character("mano", False, "first-ssr-casual")
        self.assertEqual(model["cardId"], "1040010010")
        self.assertEqual(model["module"], "./first-ssr-casual/mano.js")
        report = json.loads((self.cache / "first-ssr-casual/report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["modelPath"], "spine/idols/cb/1040010010/")
        self.assertEqual(native.write_index()["mano"]["models"], {"first-ssr-casual": model})

    def test_casual_alternate_cache_is_bound_to_its_card(self):
        kind = "casual-extra-1"
        directory = [
            {"enzaId": card, "dressType": "P_SSR", "dressName": card}
            for card in ("1040010020", "1040010040")
        ]
        (self.cache / "dresslist.json").write_text(json.dumps(directory), encoding="utf-8")
        self.cache_model(self.cache / kind / "1040010020")
        original = native.prepare_character("mano", False, kind)
        self.assertEqual(original["cardId"], "1040010020")
        with patch.object(native, "CASUAL_OVERRIDES", {"mano": {2: 4}}):
            self.assertNotIn("mano", native.write_index())
            with self.assertRaises(FileNotFoundError):
                native.prepare_character("mano", False, kind)
            self.cache_model(self.cache / kind / "1040010040")
            replacement = native.prepare_character("mano", False, kind)
            self.assertEqual(replacement["cardId"], "1040010040")
            self.assertEqual(native.write_index()["mano"]["models"], {kind: replacement})

    def test_idol_road_uses_the_road_cards_default_costume(self):
        directory = [
            {"enzaId": "1040010010", "dressType": "P_SSR"},
            {"enzaId": "1940010010", "dressType": "P_SSR", "assets": {"idols": [
                {"type": "cb", "path": "spine/idols/cb/1940010010/"},
                {"type": "cb_costume", "path": "spine/idols/cb_costume/1940010010/"},
            ]}},
        ]
        (self.cache / "dresslist.json").write_text(json.dumps(directory), encoding="utf-8")
        self.cache_model(self.cache / "idol-road")
        model = native.prepare_character("mano", False, "idol-road")
        report = json.loads((self.cache / "idol-road/report.json").read_text(encoding="utf-8"))
        self.assertEqual(model["cardId"], "1940010010")
        self.assertEqual(report["modelPath"], "spine/idols/cb_costume/1940010010/")
        self.assertEqual(native.write_index()["mano"]["models"], {"idol-road": model})

    def test_fetch_only_first_normal_stand_not_costume_or_second_outfit(self):
        expected = {"data.json": json.dumps(self.skeleton).encode(),
                    "data.atlas": b"\ndata.png\nsize: 1,1\n", "data.png": b"\x89PNG\r\n\x1a\nfixture"}
        urls = []
        def download(request, timeout):
            from io import BytesIO
            url = request.full_url
            urls.append(url)
            return BytesIO(expected[url.rsplit("/", 1)[-1]])
        with patch.object(native.urllib.request, "urlopen", side_effect=download):
            native.prepare_character("mano", True, "stand")
        self.assertEqual(set(urls), {native.ASSETS + "spine/idols/stand/first/" + filename for filename in expected})
        self.assertTrue((self.cache / "stand/report.json").is_file())
        self.assertFalse((self.cache / "data.json").exists())
        entry = native.write_index()["mano"]
        self.assertNotIn("module", entry)
        self.assertEqual(set(entry["models"]), {"stand"})


if __name__ == "__main__":
    unittest.main()
