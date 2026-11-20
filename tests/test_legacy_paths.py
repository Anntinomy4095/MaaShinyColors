from pathlib import Path
import tempfile
import unittest

from agent.core import legacy_paths


class LegacyPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.private = Path(self.temporary.name) / "dev_assets/private"
        self.private.mkdir(parents=True)

    def test_former_root_rebases_case_insensitively_into_private(self):
        for value in (r"D:\Machine Learning\maa\dev_assets\private\x\frame-001.png",
                      r"d:\machine learning\MAA\dev_assets\private\x\frame-001.png"):
            with self.subTest(value=value):
                self.assertEqual(legacy_paths.legacy_private_path(value, self.private),
                                 (self.private / "x/frame-001.png").resolve())

    def test_current_paths_pass_through_unchanged(self):
        current = str(self.private / "a.png")
        self.assertEqual(legacy_paths.rebase_legacy(current), current)
        self.assertEqual(legacy_paths.legacy_private_path(current, self.private), Path(current).resolve())

    def test_escape_other_roots_and_non_strings_rejected(self):
        for value in (r"D:\Machine Learning\maa\README.md",
                      r"D:\Machine Learning\maa\dev_assets\private\..\..\x.png",
                      r"E:\Machine Learning\maa\dev_assets\private\a.png",
                      r"D:\Machine Learning\maaX\dev_assets\private\a.png", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                legacy_paths.legacy_private_path(value, self.private)


if __name__ == "__main__":
    unittest.main()
