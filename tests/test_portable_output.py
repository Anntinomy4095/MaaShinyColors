import json
import tempfile
import unittest
from pathlib import Path
from tools.local_output import portable_output_allowed


class PortableOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "PACKAGE_MANIFEST.json").write_text(json.dumps({"name": "MaaShinyColors"}))
        (self.root / "project_policy.json").write_text("{}")

    def test_package_generates_local_imports_without_git(self):
        for name in ("ui/game-assets/inventory-scan.js", "dev_assets/private/scan/tile.png"):
            self.assertTrue(portable_output_allowed(self.root / name, self.root))

    def test_other_folders_and_repository_still_need_the_original_rule(self):
        self.assertFalse(portable_output_allowed(self.root / "ui/app.js", self.root))
        self.assertFalse(portable_output_allowed(self.root / "../outside.png", self.root))
        (self.root / ".git").mkdir()
        self.assertFalse(portable_output_allowed(self.root / "ui/game-assets/inventory-scan.js", self.root))

    def test_missing_package_marker_is_not_permission(self):
        (self.root / "PACKAGE_MANIFEST.json").unlink()
        self.assertFalse(portable_output_allowed(self.root / "ui/game-assets/inventory-scan.js", self.root))

    def test_icon_downloader_uses_the_same_rule(self):
        # 2026-11-22: ui_game_assets_fetch refused every write in a package (no .git), so a new install had no icons
        from unittest import mock
        from tools import ui_game_assets_fetch as fetch
        with mock.patch.object(fetch, "ROOT", self.root):
            self.assertTrue(fetch.is_gitignored(self.root / "dev_assets/private/game-assets-src/enza/a.png"))
            self.assertFalse(fetch.is_gitignored(self.root / "ui/app.js"))

    def test_parent_segments_cannot_escape_package(self):
        # resolve() containment is used on both path and designated local roots.
        self.assertFalse(portable_output_allowed(self.root / "ui/game-assets/../../../outside.png", self.root))
