from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.core.foreground_guard import CANVAS, interpret_foreground, validate_contracts
from tools.replay_foreground import load_configuration, main, replay

FIXTURES = Path(__file__).parent / "fixtures/replay_template"
SCENES = ["SC.Replay.A", "SC.Replay.B", "SC.Home.Ready", "SC.Other.Title", "SC.Other.Close"]


def rules():
    return {"version": 1, "foreground": [{"name": "SC.Popup.Help", "anchors": SCENES[:2]}],
            "underlying": [SCENES[2]], "expected": {"sample": {"state": "known_popup", "popup": "SC.Popup.Help"}}}


class ForegroundGuardTests(unittest.TestCase):
    def observe(self, hits, contracts=None, dimensions=CANVAS):
        return interpret_foreground([{"scene": s, "hit": s in hits} for s in SCENES], dimensions, contracts or rules())

    def test_popup_blocks_underlying_ready_and_never_authorizes_close(self):
        result = self.observe(SCENES[:3])
        self.assertEqual(result["state"], "known_popup")
        self.assertEqual(result["popup"], "SC.Popup.Help")
        self.assertEqual(result["underlying_hits"], ["SC.Home.Ready"])
        for field in ("underlying_allowed", "input_enabled", "input_authorized", "close_authorized"):
            self.assertIs(result[field], False)

    def test_partial_close_or_title_is_not_complete_popup(self):
        for anchor in SCENES[:2]:
            result = self.observe([anchor, SCENES[2]])
            self.assertEqual(result["state"], "partial")
            self.assertIsNone(result["popup"])
            self.assertFalse(result["underlying_allowed"])

    def test_idle_story_foreground_supersedes_visible_home_ready(self):
        contracts = {"foreground": [{"name": "SC.Foreground.IdleStory",
                                     "anchors": ["SC.Story.Skip", "SC.Story.Dialogue"]}],
                     "underlying": ["SC.Home.Ready"]}
        result = interpret_foreground([{"scene": name, "hit": True}
                                       for name in ["SC.Story.Skip", "SC.Story.Dialogue", "SC.Home.Ready"]],
                                      CANVAS, contracts)
        self.assertEqual(result["state"], "known_popup")
        self.assertEqual(result["popup"], "SC.Foreground.IdleStory")
        self.assertEqual(result["underlying_hits"], ["SC.Home.Ready"])
        self.assertFalse(result["underlying_allowed"])
        self.assertFalse(result["close_authorized"])

    def test_multiple_or_partially_conflicting_types_stop(self):
        contracts = rules()
        contracts["foreground"].append({"name": "SC.Popup.Other", "anchors": SCENES[3:]})
        for hits in (SCENES, SCENES[:4], [SCENES[0], SCENES[3]]):
            self.assertEqual(self.observe(hits, contracts)["state"], "conflict")

    def test_no_known_popup_does_not_prove_ready_is_unobstructed(self):
        for hits in ([], [SCENES[2]]):
            result = self.observe(hits)
            self.assertEqual(result["state"], "no_known_popup")
            self.assertFalse(result["underlying_allowed"])

    def test_wrong_dimensions_override_visible_anchors(self):
        result = self.observe(SCENES, dimensions=(640, 360))
        self.assertEqual(result["state"], "invalid_dimensions")
        self.assertEqual(result["underlying_hits"], [])

    def test_missing_duplicate_or_non_boolean_recognition_rejected(self):
        for recognitions in ([], [{"scene": SCENES[0], "hit": 1}],
                             [{"scene": SCENES[0], "hit": True}] * 2):
            with self.assertRaises(ValueError):
                interpret_foreground(recognitions, CANVAS, rules())

    def test_contracts_reject_actions_unknown_refs_and_non_independent_anchors(self):
        validate_contracts(rules(), SCENES, ["sample"])
        variants = []
        for key, value in (("action", "Click"), ("next", ["SC.Click"]), ("version", True)):
            item = rules()
            item[key] = value
            variants.append(item)
        for anchors in ([SCENES[0]], [SCENES[0]] * 2, [SCENES[0], "SC.Missing"]):
            item = rules()
            item["foreground"][0]["anchors"] = anchors
            variants.append(item)
        item = rules()
        item["foreground"][0]["action"] = "Click"
        variants.append(item)
        item = rules()
        item["foreground"][0]["name"] = "Popup.Help"
        variants.append(item)
        item = rules()
        item["expected"] = {}
        variants.append(item)
        item = rules()
        item["expected"]["sample"]["popup"] = None
        variants.append(item)
        for item in variants:
            with self.subTest(item=item), self.assertRaises(ValueError):
                validate_contracts(item, SCENES, ["sample"])


class FakeRecognizer:
    version = "fake"
    input_attempts = 0

    def __init__(self):
        self.images = []

    def recognize(self, scene, image, base):
        self.images.append(image)
        hit = image.stem in ({"positive_a", "ambiguous"} if scene["name"].endswith(".A") else {"positive_b", "ambiguous"})
        return {"scene": scene["name"], "hit": hit}


class ForegroundReplayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.manifest = FIXTURES / "manifest.json"
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        expected = {}
        for sample in data["samples"]:
            state = {"positive_a": "partial", "positive_b": "partial", "ambiguous": "known_popup",
                     "invalid_size": "invalid_dimensions"}.get(Path(sample["image"]).stem, "no_known_popup")
            expected[sample["id"]] = {"state": state, "popup": "SC.Popup.Help" if state == "known_popup" else None}
        self.contracts = self.base / "contracts.json"
        self.contracts.write_text(json.dumps({"version": 1, "foreground": [{"name": "SC.Popup.Help", "anchors": [s["name"] for s in data["scenes"]]}], "expected": expected}), encoding="utf-8")
        decoder = patch("tools.replay_foreground.read_png", side_effect=lambda path: path)
        self.decoder = decoder.start()
        self.addCleanup(decoder.stop)

    def test_same_decoded_frame_all_anchors_and_hashes_and_actual_zero_inputs(self):
        recognizer = FakeRecognizer()
        report = replay(self.manifest, self.contracts, recognizer)
        self.assertTrue(report["passed"], report["samples"])
        valid_samples = [s for s in report["samples"] if s["dimensions"] == list(CANVAS)]
        self.assertEqual(self.decoder.call_count, len(valid_samples))
        for index in range(0, len(recognizer.images), 2):
            self.assertIs(recognizer.images[index], recognizer.images[index + 1])
        self.assertEqual(len(report["configuration_sha256"]), 64)
        self.assertTrue(all(len(s["image_sha256"]) == 64 for s in report["samples"]))
        self.assertEqual(report["input_attempts"], 0)
        self.assertFalse(report["input_authorized"])

    def test_nonzero_input_counter_fails_overall_even_when_expectations_pass(self):
        recognizer = FakeRecognizer()
        recognizer.input_attempts = 1
        report = replay(self.manifest, self.contracts, recognizer)
        self.assertTrue(all(s["passed"] for s in report["samples"]))
        self.assertFalse(report["passed"])

    def test_frame_changed_during_recognition_rejected(self):
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        import shutil
        for path in FIXTURES.glob("*.png"):
            shutil.copyfile(path, self.base / path.name)
        manifest = self.base / "manifest.json"
        manifest.write_text(json.dumps(data), encoding="utf-8")

        class MutatingRecognizer(FakeRecognizer):
            def recognize(self, scene, image, base):
                result = super().recognize(scene, image, base)
                image.write_bytes(b"changed")
                return result

        with self.assertRaisesRegex(ValueError, "图片发生变化"):
            replay(manifest, self.contracts, MutatingRecognizer())

    def test_contract_or_template_identity_changed_during_recognition_rejected(self):
        original = load_configuration(self.manifest, self.contracts)
        changed = (*original[:2], "0" * 64, original[3])
        with patch("tools.replay_foreground.load_configuration", side_effect=[original, changed]):
            with self.assertRaisesRegex(ValueError, "配置或模板发生变化"):
                replay(self.manifest, self.contracts, FakeRecognizer())

    def test_cli_rejects_invalid_configuration_before_maa_and_records_no_inputs(self):
        self.contracts.write_text('{"action":"Click"}', encoding="utf-8")
        report = self.base / "report.json"
        with patch("tools.replay_foreground.MaaRecognizer") as recognizer:
            self.assertEqual(main(["--manifest", str(self.manifest), "--contracts", str(self.contracts), "--report", str(report)]), 2)
            recognizer.assert_not_called()
        result = json.loads(report.read_text(encoding="utf-8"))
        self.assertFalse(result["passed"])
        self.assertEqual(result["input_attempts"], 0)

    def test_cli_refuses_existing_report_and_sources_before_reading_or_constructing_maa(self):
        report = self.base / "existing.json"
        report.write_bytes(b"existing evidence")
        for output in (self.manifest, self.contracts, report):
            before = output.read_bytes()
            with self.subTest(output=output), patch("tools.replay_foreground.load_configuration") as loader, \
                    patch("tools.replay_foreground.MaaRecognizer") as recognizer:
                self.assertEqual(main(["--manifest", str(self.manifest), "--contracts", str(self.contracts),
                                       "--report", str(output)]), 2)
                loader.assert_not_called()
                recognizer.assert_not_called()
            self.assertEqual(output.read_bytes(), before)

    def test_cli_refuses_non_json_report_without_creating_error_file(self):
        output = self.base / "report.png"
        with patch("tools.replay_foreground.load_configuration") as loader, \
                patch("tools.replay_foreground.MaaRecognizer") as recognizer:
            self.assertEqual(main(["--manifest", str(self.manifest), "--contracts", str(self.contracts),
                                   "--report", str(output)]), 2)
            loader.assert_not_called()
            recognizer.assert_not_called()
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
