import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from agent.core import daily_navigation as nav
from tools import build_navigation_profile as tool

try:
    from tools.replay_screenshots import MaaRecognizer
    MaaRecognizer().version  # noqa: B018 - fail early when the pinned runtime is missing
    HAVE_MAA = True
except Exception:  # pragma: no cover - environment without MaaFramework
    HAVE_MAA = False

NATIVE = {n for n in nav.ROIS if n.endswith("Native")}
NEW_ACTIONS = ("training-list-back", "idol-back")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def noise(seed):
    rng = np.random.default_rng(seed)
    pixels = np.kron(rng.integers(1, 255, (90, 160, 3), dtype=np.uint8), np.ones((8, 8, 1), dtype=np.uint8))
    return Image.fromarray(pixels)


def paste(target, source, roi):
    x, y, w, h = roi
    image = target.copy()
    image.paste(source.crop((x, y, x + w, y + h)), (x, y))
    return image


@unittest.skipUnless(HAVE_MAA, "需要MaaFramework v5.12.3")
class BuildNavigationProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recognizer = MaaRecognizer()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.private = self.root / "dev_assets/private"
        self.private.mkdir(parents=True)
        patcher = mock.patch.object(tool, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.base = self.private / "base"
        self.base.mkdir()
        self.screens = {g: noise(10 + i) for i, g in enumerate(nav.GROUPS)}
        self.make_base()

    def save(self, name, image):
        path = self.private / name
        image.save(path)
        return path

    def make_base(self):
        """Every group has its own noise screen; native variants and Announcement topics come from other images."""
        scenes, templates, samples, truth = [], [], [], []
        alternate = noise(99)
        origin = self.save("orig.png", noise(1))
        for group, names in nav.GROUPS.items():
            for name in names:
                source = alternate if name in NATIVE or name.endswith(".TopicsSelected") else self.screens[group]
                x, y, w, h = nav.ROIS[name]
                file = f"t-{name}.png"
                source.crop((x, y, x + w, y + h)).save(self.base / file)
                scenes.append({"name": name, "recognition": "TemplateMatch", "roi": nav.ROIS[name], "template": file, "threshold": 0.95})
                templates.append({"template": file, "sha256": sha(self.base / file)})

        def sample(file, image, actions):
            image.save(self.base / file)
            samples.append({"id": Path(file).stem, "image": file, "expected": {"scene": None, "allowed": False, "reason": "ambiguous_state"}})
            truth.append({"image": file, "sha256": sha(self.base / file), "original": "D:\\elsewhere\\orig.png",
                          "original_sha256": sha(origin), "actions": actions})

        for group in nav.GROUPS:
            # Simulates an older base whose vocabulary predates the portal return actions.
            sample(f"base-{group}.png", self.screens[group], [a for a in tool.group_actions(group) if a not in NEW_ACTIONS])
        self.entry_variant = noise(500)
        flip = paste(self.screens["IdolPortal"], self.entry_variant, nav.ROIS["SC.Research.DailyIdol.TrainingEntry"])
        sample("base-idol-other-entry.png", flip, [])
        sample("base-idol-missing-title.png", tool.blacked(self.screens["IdolPortal"], nav.ROIS["SC.Research.DailyIdol.Title"]), [])
        (self.base / "m.json").write_text(json.dumps({"version": 1, "canvas": [1280, 720], "scenes": scenes, "samples": samples}), encoding="utf-8")
        (self.base / "s.json").write_text(json.dumps({"templates": templates, "samples": truth}), encoding="utf-8")

    def run_tool(self, out, groups, negatives=(), native=None, base=None, names=("m.json", "s.json")):
        return tool.build(base or self.base, *names, self.private / out, groups, negatives, "user-note", native,
                          recognizer=self.recognizer)

    def load(self, out):
        d = self.private / out
        manifest = json.loads((d / tool.DEFAULT_MANIFEST).read_text(encoding="utf-8"))
        sources = json.loads((d / tool.DEFAULT_SOURCES).read_text(encoding="utf-8"))
        nav.load_contract(d / tool.DEFAULT_MANIFEST, d / tool.DEFAULT_SOURCES)
        return d, manifest, sources, {s["image"]: s for s in sources["samples"]}

    def test_base_is_loadable(self):
        nav.load_contract(self.base / "m.json", self.base / "s.json")

    def test_idol_portal_override_recrops_both_variants_and_drops_changed_truths(self):
        new = self.save("new-idol.png", paste(self.screens["IdolPortal"], self.entry_variant, nav.ROIS["SC.Research.DailyIdol.TrainingEntry"]))
        report = self.run_tool("v2", [f"IdolPortal={new}"], native=["IdolPortal"])
        out, manifest, sources, truth = self.load("v2")
        image = Image.open(new).convert("RGB")
        for name in nav.GROUPS["IdolPortal"]:
            x, y, w, h = nav.ROIS[name]
            scene = next(s for s in manifest["scenes"] if s["name"] == name)
            self.assertEqual(scene["template"], f"navprofile-IdolPortal-{name.rsplit('.', 1)[1]}.png")
            self.assertEqual(list(Image.open(out / scene["template"]).get_flattened_data()), list(image.crop((x, y, x + w, y + h)).get_flattened_data()))
        self.assertEqual((out / "navprofile-IdolPortal-TrainingEntry.png").read_bytes(),
                         (out / "navprofile-IdolPortal-TrainingEntryNative.png").read_bytes())
        self.assertEqual(truth["navprofile-IdolPortal-positive.png"]["actions"], ["idol-training", "idol-back"])
        self.assertEqual(truth["navprofile-IdolPortal-positive.png"]["original"], tool.rel(new))
        self.assertEqual((out / "navprofile-IdolPortal-positive.png").read_bytes(), new.read_bytes())
        missing = sorted(k for k in truth if k.startswith("navprofile-IdolPortal-missing-"))
        self.assertEqual(missing, ["navprofile-IdolPortal-missing-Return.png", "navprofile-IdolPortal-missing-Title.png",
                                   "navprofile-IdolPortal-missing-TrainingEntry.png"])
        for name in missing:
            self.assertEqual(truth[name]["actions"], [])
            self.assertTrue(truth[name]["synthetic"])
        # Old positive no longer matches; the old negative turned positive: both dropped and listed.
        for name in ("base-IdolPortal.png", "base-idol-other-entry.png"):
            self.assertNotIn(name, truth)
            self.assertIn(name, sources["limitations"])
        self.assertIn("base-idol-missing-title.png", truth)
        # Unrelated templates and samples stay byte-for-byte; truth only extended by new sibling actions.
        self.assertEqual((out / "t-SC.Research.DailyMission.Title.png").read_bytes(), (self.base / "t-SC.Research.DailyMission.Title.png").read_bytes())
        self.assertEqual((out / "base-Home.png").read_bytes(), (self.base / "base-Home.png").read_bytes())
        self.assertEqual(truth["base-Home.png"]["actions"], ["home-mission", "home-idol", "home-fes"])
        self.assertEqual(truth["base-TrainingList.png"]["actions"], ["training-select-asahi", "training-list-back"])
        self.assertTrue(any("base-TrainingList.png" in e for e in report["extended"]))
        self.assertEqual(sources["native_groups"], ["IdolPortal"])
        self.assertIn("user-note", sources["limitations"])
        self.assertEqual(sources["base"]["manifest_sha256"], sha(self.base / "m.json"))
        for entry in sources["templates"]:
            self.assertEqual(sha(out / entry["template"]), entry["sha256"])

    def test_negative_sample_and_rebuild_supersedes_and_carries_native(self):
        new = self.save("new-list.png", self.screens["TrainingList"])
        other = self.save("other.png", noise(777))
        self.run_tool("v2", [f"TrainingList={new}"], [f"TrainingList={other}"], native=["TrainingList"])
        _, _, sources, truth = self.load("v2")
        self.assertEqual(truth["navprofile-TrainingList-negative-other.png"]["actions"], [])
        self.assertEqual(truth["navprofile-TrainingList-positive.png"]["actions"], ["training-select-asahi", "training-list-back"])
        self.assertEqual(len([k for k in truth if "-TrainingList-missing-" in k]), 3)
        # Same rendering as base: the base positive keeps its (extended) truth.
        self.assertEqual(truth["base-TrainingList.png"]["actions"], ["training-select-asahi", "training-list-back"])
        again = self.save("again.png", self.screens["TrainingList"])
        report = tool.build(self.private / "v2", tool.DEFAULT_MANIFEST, tool.DEFAULT_SOURCES, self.private / "v3",
                            [f"TrainingList={again}", f"Home={self.save('home.png', self.screens['Home'])}"],
                            recognizer=self.recognizer)
        _, _, sources3, truth3 = self.load("v3")
        self.assertIn("navprofile-TrainingList-positive.png", report["superseded"])
        self.assertEqual(truth3["navprofile-TrainingList-positive.png"]["original"], tool.rel(again))
        self.assertEqual(sources3["native_groups"], [])  # re-calibrated without a native claim
        self.assertEqual(truth3["navprofile-Home-positive.png"]["actions"], ["home-mission", "home-idol", "home-fes"])

    def test_native_claim_is_carried_for_untouched_groups(self):
        self.run_tool("v2", [f"IdolPortal={self.save('i.png', self.screens['IdolPortal'])}"], native=["IdolPortal"])
        tool.build(self.private / "v2", tool.DEFAULT_MANIFEST, tool.DEFAULT_SOURCES, self.private / "v3",
                   [f"MatchMain={self.save('m.png', self.screens['MatchMain'])}"], native_groups=["MatchMain"], recognizer=self.recognizer)
        self.assertEqual(self.load("v3")[2]["native_groups"], ["IdolPortal", "MatchMain"])

    def test_truth_mismatch_fails_loudly_and_cleans_up(self):
        # A Home screen cropped into TrainingList templates also completes Home, so no unique action exists.
        home = self.save("home-as-list.png", self.screens["Home"])
        with self.assertRaisesRegex(ValueError, "预期"):
            self.run_tool("bad", [f"TrainingList={home}"])
        self.assertFalse((self.private / "bad").exists())
        positive = self.save("pos.png", self.screens["Pickup"])
        with self.assertRaisesRegex(ValueError, "预期"):  # a "negative" that is actually actionable
            self.run_tool("bad2", [f"Pickup={positive}"], [f"Pickup={positive}"])
        self.assertFalse((self.private / "bad2").exists())

    def test_refusals(self):
        new = self.save("new.png", self.screens["Story"])
        self.run_tool("v2", [f"Story={new}"])
        cases = [
            ("v2", [f"Story={new}"], {}),  # no overwrite
            ("v3", [f"Nope={new}"], {}),
            ("v4", [f"Story={new}", f"Story={new}"], {}),
            ("v5", [f"Story={new}"], {"native": ["Home"]}),
            ("v6", [], {}),
        ]
        for out, groups, extra in cases:
            with self.subTest(out=out), self.assertRaises(ValueError):
                self.run_tool(out, groups, **extra)
        outside = self.root / "outside.png"
        noise(3).save(outside)
        small = self.save("small.png", Image.new("RGB", (640, 360), (1, 2, 3)))
        rgba = np.asarray(self.screens["Story"].convert("RGBA")).copy()
        rgba[0, 0, 3] = 10
        glassy = self.save("glassy.png", Image.fromarray(rgba))
        for out, raw in (("v7", outside), ("v8", small), ("v9", glassy)):
            with self.subTest(out=out), self.assertRaises(ValueError):
                self.run_tool(out, [f"Story={raw}"])
        with self.assertRaises(ValueError):
            tool.build(self.base, "m.json", "s.json", self.root / "elsewhere", [f"Story={new}"], recognizer=self.recognizer)
        with self.assertRaises(ValueError):
            self.run_tool("v10", [f"Story={new}"], names=("..\\m.json", "s.json"))
        for name in ("v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10"):
            self.assertFalse((self.private / name).exists())
        self.assertFalse((self.root / "elsewhere").exists())

    def test_base_predating_new_group_requires_and_adds_its_scenes(self):
        old = self.private / "old"
        old.mkdir()
        manifest = json.loads((self.base / "m.json").read_text(encoding="utf-8"))
        sources = json.loads((self.base / "s.json").read_text(encoding="utf-8"))
        start = set(nav.GROUPS["StartScreen"])
        keep = [s for s in manifest["samples"] if s["image"] != "base-StartScreen.png"]
        manifest = dict(manifest, scenes=[s for s in manifest["scenes"] if s["name"] not in start], samples=keep)
        sources = dict(sources, templates=[t for t in sources["templates"] if t["template"] not in {f"t-{n}.png" for n in start}],
                       samples=[t for t in sources["samples"] if t["image"] != "base-StartScreen.png"])
        for scene in manifest["scenes"]:
            (old / scene["template"]).write_bytes((self.base / scene["template"]).read_bytes())
        for sample in keep:
            (old / sample["image"]).write_bytes((self.base / sample["image"]).read_bytes())
        (old / "m.json").write_text(json.dumps(manifest), encoding="utf-8")
        (old / "s.json").write_text(json.dumps(sources), encoding="utf-8")
        with self.assertRaises(ValueError):
            nav.load_contract(old / "m.json", old / "s.json")
        home = self.save("h.png", self.screens["Home"])
        with self.assertRaisesRegex(ValueError, "基线缺少锚点"):
            self.run_tool("v2", [f"Home={home}"], base=old)
        self.assertFalse((self.private / "v2").exists())
        frame = self.save("start.png", self.screens["StartScreen"])
        report = self.run_tool("v3", [f"StartScreen={frame}"], base=old)
        _, out_manifest, _, truth = self.load("v3")
        self.assertEqual(report["added_scenes"], list(nav.GROUPS["StartScreen"]))
        self.assertEqual({s["name"] for s in out_manifest["scenes"]}, set(nav.ROIS))
        self.assertEqual(truth["navprofile-StartScreen-positive.png"]["actions"], ["game-start"])
        self.assertEqual(len([k for k in truth if "-StartScreen-missing-" in k]), 3)
        self.assertEqual(truth["base-Home.png"]["actions"], ["home-mission", "home-idol", "home-fes"])

    def test_cli(self):
        self.assertEqual(tool.parse_native("Home, IdolPortal"), ["Home", "IdolPortal"])
        for raw in ("", "Home,Home", "Nope"):
            with self.assertRaises(ValueError):
                tool.parse_native(raw)
        with mock.patch.object(tool, "build", side_effect=ValueError("x")) as build:
            self.assertEqual(tool.main(["--base", str(self.base), "--base-manifest", "m.json", "--base-sources", "s.json",
                                        "--output-dir", str(self.private / "x"), "--group-source", "Home=a.png",
                                        "--require-native-groups", "Home"]), 1)
            self.assertEqual(build.call_args.args[7], ["Home"])


if __name__ == "__main__":
    unittest.main()
