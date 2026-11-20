from __future__ import annotations

import contextlib
import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from tools.manage_capture_session import import_session, main, validate_dataset


def png(color=0, width=1280, height=720):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    raw = (b"\0" + bytes([color]) * width * 3) * height
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class CaptureSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.private = self.base / "private"
        self.root = self.private / "sessions"
        self.source = self.base / "source"
        self.source.mkdir()
        self.source.joinpath("account-window-name.png").write_bytes(png())
        self.patch = patch("tools.manage_capture_session.PRIVATE_ROOT", self.private)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def load(self, session="visit-1"):
        return json.loads((self.root / session / "session.json").read_text(encoding="utf-8"))

    def save(self, data, session="visit-1"):
        (self.root / session / "session.json").write_text(json.dumps(data), encoding="utf-8")

    def capture(self, session="visit-1", split="train"):
        return import_session(self.source, self.root, session, split, "SC.Home")

    def ready(self):
        data = self.load()
        data["samples"][0].update(label="ready", expected={"scene": "SC.Home", "allowed": True, "reason": "positive_guard"})
        self.save(data)

    def test_import_is_private_pending_anonymous_and_nonrecursive(self):
        nested = self.source / "nested"
        nested.mkdir()
        (nested / "bad.png").write_bytes(b"not PNG")
        data = self.capture()
        self.assertEqual(len(data["samples"]), 1)
        self.assertEqual(data["samples"][0]["label"], "pending")
        self.assertNotIn("expected", data["samples"][0])
        self.assertNotIn("account-window", json.dumps(data))
        self.assertNotIn(str(self.source), json.dumps(data))
        report = validate_dataset(self.root)
        self.assertFalse(report["passed"])
        self.assertEqual(report["pending"], 1)
        self.ready()
        checked = validate_dataset(self.root)
        self.assertTrue(checked["passed"])
        self.assertFalse(checked["evaluation_ready"])
        self.assertEqual(checked["split_samples"], {"train": 1, "holdout": 0})

    def test_evaluation_ready_requires_distinct_labeled_splits(self):
        self.capture()
        self.ready()
        self.source.joinpath("account-window-name.png").write_bytes(png(1))
        data = self.capture("visit-2", "holdout")
        data["samples"][0].update(label="ready", expected={"scene": None, "allowed": False, "reason": "unknown_state"})
        self.save(data, "visit-2")
        checked = validate_dataset(self.root)
        self.assertTrue(checked["evaluation_ready"])
        self.assertEqual(checked["split_samples"], {"train": 1, "holdout": 1})

    def test_overwrite_same_session_cannot_change_split(self):
        self.capture()
        before = (self.root / "visit-1" / "session.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "覆盖"):
            self.capture(split="holdout")
        self.assertEqual((self.root / "visit-1" / "session.json").read_bytes(), before)

    def test_duplicate_hash_rejected_in_same_or_other_split(self):
        self.capture()
        for split in ("train", "holdout"):
            with self.subTest(split=split), self.assertRaisesRegex(ValueError, "重复PNG"):
                self.capture("visit-2", split)
        self.assertFalse((self.root / "visit-2").exists())

    def test_duplicate_within_source_creates_no_destination(self):
        (self.source / "duplicate.png").write_bytes(png())
        with self.assertRaisesRegex(ValueError, "重复PNG"):
            self.capture()
        self.assertFalse(self.root.exists())

    def test_invalid_late_image_creates_no_destination(self):
        for bad in (png(width=640), png()[:-5], png()[:24], png()[:-12] + b"bad checksum"):
            with self.subTest(length=len(bad)):
                (self.source / "z-bad.png").write_bytes(bad)
                with self.assertRaises(ValueError):
                    self.capture()
                self.assertFalse(self.root.exists())

    def test_destination_escape_and_bad_ids_rejected(self):
        for destination, identifier, scene in ((self.base, "visit", "SC.Home"), (self.root, "../escape", "SC.Home"), (self.root, "visit", "../scene"), (self.root, "CON", "SC.Home")):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                import_session(self.source, destination, identifier, "train", scene)

    def test_tampered_hash_wrong_dimensions_and_missing_split_fail(self):
        self.capture()
        self.ready()
        data = self.load()
        data["samples"][0]["sha256"] = "0" * 64
        self.save(data)
        self.assertIn("hash", " ".join(validate_dataset(self.root)["errors"]))
        (self.root / "visit-1" / "sample-0001.png").write_bytes(png(width=640))
        self.assertIn("1280x720", " ".join(validate_dataset(self.root)["errors"]))
        del data["split"]
        self.save(data)
        self.assertFalse(validate_dataset(self.root)["passed"])

    def test_validator_detects_manually_copied_cross_split_leak(self):
        self.capture()
        self.ready()
        data = self.load()
        data.update(id="visit-2", split="holdout")
        second = self.root / "visit-2"
        second.mkdir()
        (second / "sample-0001.png").write_bytes(png())
        self.save(data, "visit-2")
        report = validate_dataset(self.root)
        self.assertFalse(report["passed"])
        self.assertIn("train/holdout泄漏", " ".join(report["errors"]))

    def test_label_semantics_and_path_escape_fail(self):
        self.capture()
        for expected in ({"scene": None, "allowed": True, "reason": "positive_guard"}, {"scene": "SC.Home", "allowed": False, "reason": "unknown_state"}, {"scene": None, "allowed": False, "reason": "invalid_dimensions"}, {"scene": "home", "allowed": True, "reason": "positive_guard"}):
            data = self.load()
            data["samples"][0].update(label="ready", expected=expected)
            self.save(data)
            self.assertFalse(validate_dataset(self.root)["passed"])
        data["samples"][0]["file"] = "../account-window-name.png"
        self.save(data)
        self.assertIn("文件名", " ".join(validate_dataset(self.root)["errors"]))

    def test_negative_label_and_cli_report(self):
        self.capture()
        data = self.load()
        data["samples"][0].update(label="ready", expected={"scene": None, "allowed": False, "reason": "ambiguous_state"})
        self.save(data)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["check", "--root", str(self.root)]), 0)
        self.assertTrue(json.loads((self.root / "validation-report.json").read_text(encoding="utf-8"))["passed"])
        self.assertFalse(validate_dataset(self.private / "empty")["passed"])

    def test_png_crc_corruption_fails(self):
        image = bytearray(png())
        image[29] ^= 1
        self.source.joinpath("account-window-name.png").write_bytes(image)
        with self.assertRaisesRegex(ValueError, "校验"):
            self.capture()

    def test_unregistered_png_or_session_cannot_be_ignored(self):
        self.capture()
        self.ready()
        (self.root / "visit-1" / "extra.png").write_bytes(png(1))
        self.assertIn("未登记", " ".join(validate_dataset(self.root)["errors"]))
        (self.root / "visit-1" / "extra.png").unlink()
        (self.root / "visit-2").mkdir()
        self.assertIn("缺少session.json", " ".join(validate_dataset(self.root)["errors"]))


if __name__ == "__main__":
    unittest.main()
