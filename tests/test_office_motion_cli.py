"""命令行作者工作流；只读写临时动作文件，不加载游戏资源。"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "office_motion.mjs"


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class OfficeMotionCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="office-motion-cli-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.motion = self.directory / "head motion.json"

    def cli(self, *args, ok=True):
        result = subprocess.run(
            [shutil.which("node"), str(CLI), *map(str, args)], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("错误：", result.stderr)
        return result

    def new(self, *args):
        self.cli("new", "--out", self.motion, "--character", "mano", "--base", "__setup__",
                 "--duration", "3", "--name", "抬头", *args)
        return self.read()

    def read(self):
        return json.loads(self.motion.read_text(encoding="utf-8"))

    def module_value(self, path):
        result = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e",
             "const {default: value}=await import(process.argv[1]); console.log(JSON.stringify(value));", path.as_uri()],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_new_only_seeds_explicit_bones(self):
        self.assertEqual(self.new()["tracks"], {})
        data = self.new("--seed", "head,body", "--start", "2", "--end", "2")
        self.assertEqual((data["version"], data["name"], data["sourceEnd"]), (1, "抬头", 2))
        self.assertEqual(set(data["tracks"]), {"head", "body"})
        for keys in data["tracks"].values():
            self.assertEqual(len(keys), 1)
            self.assertEqual(keys[0]["time"], 0)
            self.assertEqual(keys[0]["rotation"], 0)

    def test_standing_model_survives_cli_edit_and_export(self):
        self.assertEqual(self.new("--model", "stand")["modelKind"], "stand")
        self.cli("key", self.motion, "--bone", "face_control", "--at", "1", "--rotation", "4")
        self.assertEqual(json.loads(self.cli("inspect", self.motion).stdout)["modelKind"], "stand")
        self.cli("source", self.motion, "--base", "wait", "--start", "0", "--end", "3",
                 "--expression", "face_smile", "--gaze", "eye_left")
        self.assertEqual(self.read()["expressionAnimation"], "face_smile")
        self.assertEqual(self.read()["gazeAnimation"], "eye_left")
        output = self.directory / "standing.mjs"
        self.cli("module", self.motion, "--out", output)
        self.assertEqual(self.module_value(output)["modelKind"], "stand")

    def test_world_position_pin_survives_edit_inspect_and_module_roundtrip(self):
        self.assertEqual(self.new("--pin", "Pchan,head")["worldPositionBones"], ["Pchan", "head"])
        self.cli("key", self.motion, "--bone", "head", "--at", "1", "--rotation", "10")
        self.assertEqual(json.loads(self.cli("inspect", self.motion).stdout)["worldPositionBones"], ["Pchan", "head"])
        output = self.directory / "pinned.mjs"
        self.cli("module", self.motion, "--out", output)
        self.assertEqual(self.module_value(output)["worldPositionBones"], ["Pchan", "head"])
        self.cli("new", "--out", self.motion, "--pin", "Pchan,", ok=False)
        self.assertEqual(self.read()["worldPositionBones"], ["Pchan", "head"])

    def test_partial_key_keeps_sampled_components_and_does_not_add_endpoints(self):
        self.new()
        self.cli("key", self.motion, "--bone", "head", "--at", "0", "--x", "2", "--y", "4", "--rotation", "10", "--curve", "linear")
        self.cli("key", self.motion, "--bone", "head", "--at", "2", "--x", "10", "--y", "12", "--rotation", "30")
        self.cli("key", self.motion, "--bone", "head", "--at", "1", "--rotation", "90", "--curve", "hold")
        keys = self.read()["tracks"]["head"]
        self.assertEqual([key["time"] for key in keys], [0, 1, 2])
        self.assertEqual((keys[1]["x"], keys[1]["y"], keys[1]["rotation"], keys[1]["curve"]), (6, 8, 90, "hold"))

    def test_move_and_duration_preserve_source_range(self):
        self.new("--start", "2", "--end", "8")
        self.cli("key", self.motion, "--bone", "head", "--at", "1", "--rotation", "15")
        self.cli("move", self.motion, "--bone", "head", "--from", "1", "--at", "2")
        self.cli("duration", self.motion, "--seconds", "6")
        data = self.read()
        self.assertEqual((data["startTime"], data["sourceEnd"], data["duration"]), (2, 8, 6))
        self.assertEqual(data["tracks"]["head"][0]["time"], 4)
        self.assertEqual(data["tracks"]["head"][0]["rotation"], 15)

    def test_source_inspect_and_module_export(self):
        self.new("--seed", "head")
        self.cli("source", self.motion, "--base", "wait", "--start", "1.5", "--end", "4", "--loop", "false")
        metadata = json.loads(self.cli("inspect", self.motion).stdout)
        self.assertEqual((metadata["baseAnimation"], metadata["startTime"], metadata["sourceEnd"], metadata["sourceLoop"]), ("wait", 1.5, 4, False))
        self.assertEqual(metadata["keyTimes"], {"head": [0]})
        self.assertNotIn("tracks", metadata)
        output = self.directory / "motion.mjs"
        self.cli("module", self.motion, "--out", output)
        self.assertEqual(self.module_value(output), self.read())

    def test_scene_resolves_relative_motion_paths_and_preserves_labels(self):
        self.new()
        other = self.directory / "second.json"
        self.cli("new", "--out", other, "--duration", "2")
        manifest = self.directory / "scene.json"
        manifest.write_text(json.dumps({"version": 1, "name": "短场景", "segments": [
            {"motion": self.motion.name, "label": "抬头"}, {"motion": other.name},
        ]}, ensure_ascii=False), encoding="utf-8")
        output = self.directory / "scene.mjs"
        self.cli("scene", manifest, "--out", output)
        data = self.module_value(output)
        self.assertEqual((data["version"], data["name"]), (1, "短场景"))
        self.assertEqual(data["segments"][0], {"motion": self.read(), "label": "抬头"})
        self.assertEqual(data["segments"][1]["motion"]["duration"], 2)
        self.assertNotIn("label", data["segments"][1])

    def test_duet_scene_embeds_actor_timing_and_original_motion(self):
        self.new("--model", "stand")
        manifest = self.directory / "duet.json"
        manifest.write_text(json.dumps({"version": 2, "name": "招呼", "duration": 3, "actors": [
            {"id": "mano", "x": .69, "view": "upper", "segments": [
                {"at": 0, "motion": self.motion.name},
                {"at": 1.2, "motion": self.motion.name, "mix": .35},
            ]},
        ]}, ensure_ascii=False), encoding="utf-8")
        output = self.directory / "duet.mjs"
        self.cli("scene", manifest, "--out", output)
        data = self.module_value(output)
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["actors"][0]["x"], .69)
        self.assertEqual(data["actors"][0]["segments"][1]["at"], 1.2)
        self.assertEqual(data["actors"][0]["segments"][1]["mix"], .35)
        self.assertEqual(data["actors"][0]["segments"][1]["motion"], self.read())

    def test_invalid_edit_preserves_file_and_invalid_scene_preserves_output(self):
        self.new()
        before = self.motion.read_bytes()
        for args in [
            ("key", "--bone", "head", "--at", "NaN"),
            ("key", "--bone", "head", "--at", "4"),
            ("key", "--bone", "head", "--at", "1", "--curve", "wrong"),
            ("move", "--bone", "head", "--from", "1", "--at", "2"),
            ("duration", "--seconds", "0"),
            ("source", "--base", "wait", "--start", "3", "--end", "2"),
            ("source", "--base", "wait", "--start", "0", "--end", "2", "--loop", "yes"),
        ]:
            self.cli(args[0], self.motion, *args[1:], ok=False)
            self.assertEqual(self.motion.read_bytes(), before)
        bad = self.directory / "bad.json"
        bad.write_text('{"version":1,"name":"bad","segments":[{"motion":"missing.json"}]}', encoding="utf-8")
        output = self.directory / "existing.mjs"
        output.write_text("keep", encoding="utf-8")
        self.cli("scene", bad, "--out", output, ok=False)
        self.assertEqual(output.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
