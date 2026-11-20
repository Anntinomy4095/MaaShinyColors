import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

import agent.core.training_flow as flow
from tools import build_training_profile as tool

PREFIX = flow.PREFIX


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def noise(seed, mode="RGB"):
    rng = np.random.default_rng(seed)
    pixels = np.kron(rng.integers(1, 255, (90, 160, 3), dtype=np.uint8), np.ones((8, 8, 1), dtype=np.uint8))
    image = Image.fromarray(pixels, "RGB")
    return image.convert(mode) if mode != "RGB" else image


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.private = self.root / "dev_assets/private"
        self.private.mkdir(parents=True)
        for target in (tool, flow):
            patcher = mock.patch.object(target, "ROOT", self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.base = self.private / "base"
        self.base.mkdir()
        self.make_base()

    def save_source(self, name, seed, mode="RGB"):
        path = self.private / name
        noise(seed, mode).save(path)
        return path

    def rel(self, path):
        return tool.rel(path)

    def make_base(self):
        """Independent minimal v1-shaped profile: Zero has a native positive plus NativeZero extras."""
        sources = {s: self.save_source(f"{s}-orig.png", i) for i, s in enumerate(flow.STAGES)}
        native = self.save_source("native-orig.png", 99)
        scenes, templates, samples, truth = [], [], [], []

        def sample(sid, image, stage, guard, kind, src, scene=None):
            samples.append({"id": sid, "image": image, "expected": {"scene": scene, "allowed": bool(scene), "reason": "positive_guard" if scene else "unknown_state"}})
            truth.append({"derived": image, "derived_sha256": sha(self.base / image), "stage": stage, "expected_guard": guard,
                          "kind": kind, "source": self.rel(src), "source_sha256": sha(src)})

        for stage in flow.STAGES:
            src = native if stage == "Zero" else sources[stage]
            image = Image.open(src)
            for anchor, roi in flow.ANCHORS[stage].items():
                x, y, w, h = roi
                name = f"{stage}-{anchor}.png"
                image.crop((x, y, x + w, y + h)).save(self.base / name)
                scenes.append({"name": f"{PREFIX}{stage}.{anchor}", "recognition": "TemplateMatch", "roi": roi, "template": name, "threshold": 0.95})
                templates.append({"template": name, "template_sha256": sha(self.base / name), "roi": roi, "source": self.rel(src), "source_sha256": sha(src)})
        for stage in flow.STAGES:
            src = native if stage == "Zero" else sources[stage]
            for anchor, roi in flow.ANCHORS[stage].items():
                image = tool.blacked(Image.open(src).convert("RGB"), roi)
                image.save(self.base / f"{stage}-missing-{anchor}.png")
                sample(f"{stage}-missing-{anchor}", f"{stage}-missing-{anchor}.png", stage, False, "synthetic_removed_anchor", src)
            if stage == "Zero":  # old rendering as the "Zero-source" negative, native as NativeZero-source
                Image.open(sources["Zero"]).save(self.base / "Zero-source.png")
                sample("Zero-source", "Zero-source.png", "Zero", False, "historical_other_rendering", sources["Zero"])
                Image.open(sources["After"]).save(self.base / "Zero-independent-level2.png")
                sample("Zero-independent-level2", "Zero-independent-level2.png", "Zero", False, "historical_other_rendering", sources["After"])
                Image.open(native).save(self.base / "NativeZero-source.png")
                sample("native-zero-source", "NativeZero-source.png", "Zero", True, "native_original", native)
            else:
                Image.open(sources[stage]).save(self.base / f"{stage}-source.png")
                sample(f"{stage}-source", f"{stage}-source.png", stage, True, "source_self_match", sources[stage], f"{PREFIX}{stage}.Title")
            for other in tool.REJECT_STAGES if hasattr(tool, "REJECT_STAGES") else ("One", "Confirm", "Result"):
                if other != stage:
                    Image.open(sources[other]).save(self.base / f"{stage}-reject-{other}.png")
                    sample(f"{stage}-reject-{other}", f"{stage}-reject-{other}.png", stage, False, "other_stage_original", sources[other])
        (self.base / "manifest.json").write_text(json.dumps({"version": 1, "canvas": [1280, 720], "scenes": scenes, "samples": samples}), encoding="utf-8")
        (self.base / "sources.json").write_text(json.dumps({"version": 1, "templates": templates, "samples": truth, "limitations": "base-limits"}), encoding="utf-8")

    def run_tool(self, out, *args):
        return tool.build(self.base, self.private / out, *args)

    def load(self, out):
        d = self.private / out
        return d, json.loads((d / "manifest.json").read_text(encoding="utf-8")), json.loads((d / "sources.json").read_text(encoding="utf-8"))


class BuildTrainingProfileTests(Fixture):
    def test_base_is_loadable(self):
        flow.load_guard(self.base / "manifest.json", self.base / "sources.json")

    def test_override_zero_copies_rest_and_passes_guard(self):
        new = self.save_source("new-zero.png", 500)
        self.run_tool("v2", [f"Zero={new}"], [])
        out, manifest, origin = self.load("v2")
        flow.load_guard(out / "manifest.json", out / "sources.json")
        image = Image.open(new)
        for anchor, (x, y, w, h) in flow.ANCHORS["Zero"].items():
            template = Image.open(out / f"Zero-{anchor}.png")
            self.assertEqual(template.size, (w, h))
            self.assertEqual(list(template.get_flattened_data()), list(image.crop((x, y, x + w, y + h)).get_flattened_data()))
            self.assertEqual(sha(out / f"Zero-missing-{anchor}.png") == sha(self.base / f"Zero-missing-{anchor}.png"), False)
        for name in ("One-Title.png", "Confirm-Start.png", "After-Plus.png", "Result-source.png", "Zero-independent-level2.png", "One-reject-Confirm.png"):
            self.assertEqual((out / name).read_bytes(), (self.base / name).read_bytes())
        self.assertEqual((out / "Zero-source.png").read_bytes(), new.read_bytes())
        ids = {s["id"] for s in manifest["samples"]}
        self.assertNotIn("native-zero-source", ids)
        self.assertFalse(any(s["image"].startswith("NativeZero") for s in manifest["samples"]))
        self.assertIn("NativeZero", origin["limitations"])
        self.assertIn("base-limits", origin["limitations"])
        zero = next(s for s in origin["samples"] if s["derived"] == "Zero-source.png")
        self.assertTrue(zero["expected_guard"])
        self.assertEqual(zero["source"], self.rel(new))
        self.assertEqual(zero["source"], zero["source"].replace("/", "\\"))
        # Non-overridden provenance is kept from the base.
        base_one = next(s for s in json.loads((self.base / "sources.json").read_text(encoding="utf-8"))["samples"] if s["derived"] == "One-source.png")
        self.assertEqual(next(s for s in origin["samples"] if s["derived"] == "One-source.png"), base_one)

    def test_missing_anchor_is_black_fill_only(self):
        new = self.save_source("new-one.png", 7)
        self.run_tool("v2", [f"One={new}"], [])
        out = self.private / "v2"
        base = np.asarray(Image.open(new).convert("RGB"))
        for anchor, (x, y, w, h) in flow.ANCHORS["One"].items():
            got = np.asarray(Image.open(out / f"One-missing-{anchor}.png"))
            self.assertTrue((got[y:y + h, x:x + w] == 0).all())
            expect = base.copy()
            expect[y:y + h, x:x + w] = 0
            self.assertTrue((got == expect).all())

    def test_rejects_regenerated_for_overridden_stage(self):
        new = self.save_source("new-confirm.png", 8)
        self.run_tool("v2", [f"Confirm={new}"], [])
        out, _, origin = self.load("v2")
        self.assertEqual((out / "One-reject-Confirm.png").read_bytes(), new.read_bytes())
        self.assertEqual((out / "Zero-reject-Confirm.png").read_bytes(), new.read_bytes())
        self.assertEqual((out / "Confirm-reject-One.png").read_bytes(), (self.base / "Confirm-reject-One.png").read_bytes())
        entry = next(s for s in origin["samples"] if s["derived"] == "Result-reject-Confirm.png")
        self.assertEqual(entry["source"], self.rel(new))
        self.assertEqual(entry["source_sha256"], sha(new))
        flow.load_guard(out / "manifest.json", out / "sources.json")

    def test_extra_reject_added(self):
        new, old = self.save_source("new-zero.png", 1), self.save_source("old-zero.png", 2)
        self.run_tool("v2", [f"Zero={new}"], [f"Zero={old}"])
        out, manifest, origin = self.load("v2")
        entry = next(s for s in origin["samples"] if s["derived"] == "Zero-variant-old-zero.png")
        self.assertFalse(entry["expected_guard"])
        self.assertEqual(entry["stage"], "Zero")
        self.assertIn("Zero-variant-old-zero", {s["id"] for s in manifest["samples"]})
        flow.load_guard(out / "manifest.json", out / "sources.json")

    def test_rgba_opaque_accepted_transparent_rejected(self):
        opaque = self.save_source("opaque.png", 3, "RGBA")
        self.run_tool("ok", [f"Result={opaque}"], [])
        flow.load_guard(self.private / "ok/manifest.json", self.private / "ok/sources.json")
        self.assertEqual(Image.open(self.private / "ok/Result-source.png").mode, "RGB")
        arr = np.asarray(noise(4, "RGBA")).copy()
        arr[0, 0, 3] = 128
        glassy = self.private / "glassy.png"
        Image.fromarray(arr, "RGBA").save(glassy)
        with self.assertRaises(ValueError):
            self.run_tool("bad", [f"Result={glassy}"], [])
        self.assertFalse((self.private / "bad").exists())

    def test_refusals(self):
        new = self.save_source("new.png", 5)
        self.run_tool("v2", [f"Zero={new}"], [])
        with self.assertRaises(ValueError):  # no overwrite
            self.run_tool("v2", [f"Zero={new}"], [])
        with self.assertRaises(ValueError):  # output outside private
            tool.build(self.base, self.root / "elsewhere", [f"Zero={new}"], [])
        outside = self.root / "outside.png"
        noise(6).save(outside)
        with self.assertRaises(ValueError):  # source outside private
            self.run_tool("v3", [f"Zero={outside}"], [])
        small = self.private / "small.png"
        Image.new("RGB", (640, 360)).save(small)
        with self.assertRaises(ValueError):  # wrong size
            self.run_tool("v4", [f"Zero={small}"], [])
        with self.assertRaises(ValueError):  # unknown stage / duplicate stage
            self.run_tool("v5", [f"Nope={new}"], [])
        with self.assertRaises(ValueError):
            self.run_tool("v6", [f"Zero={new}", f"Zero={new}"], [])
        for name in ("v3", "v4", "v5", "v6", "elsewhere"):
            self.assertFalse((self.private / name).exists())

    def test_cli_returns_error_code(self):
        self.assertEqual(tool.main(["--base", str(self.base), "--output", str(self.private / "x"), "--stage-source", "Zero=missing.png"]), 1)


if __name__ == "__main__":
    unittest.main()
