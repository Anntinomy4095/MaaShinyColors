"""Portable template import uses the selected bundle, never its neighbouring private files."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from tools.import_local_resources import import_templates


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "package"
        self.source = Path(self.temp.name) / "old" / "dev_assets/private/resource_native"
        pipeline = self.root / "assets/resource/base/pipeline"
        pipeline.mkdir(parents=True)
        (pipeline / "one.json").write_text(json.dumps({"SC.Test": {"template": "native/home/title.png", "roi": [0, 0, 20, 20]}}))
        image = self.source / "image/native/home/title.png"
        image.parent.mkdir(parents=True)
        Image.new("RGB", (10, 10)).save(image)
        self.manifest = {"canvas": [1280, 720], "template_prefix": "native/", "templates": [
            {"name": "native/home/title.png", "sha256": hashlib.sha256(image.read_bytes()).hexdigest(), "size": [10, 10],
             "source": "private-account-capture.png"}]}
        self.write_manifest()

    def write_manifest(self):
        (self.source / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_project_import_copies_templates_only_and_removes_source_metadata(self):
        (self.source.parent / "account.json").write_text('{"private":true}')
        self.assertEqual(import_templates(self.source.parents[2], self.root), 1)
        target = self.root / "dev_assets/private/resource_native"
        self.assertTrue((target / "image/native/home/title.png").is_file())
        self.assertNotIn("private-account", (target / "manifest.json").read_text())
        self.assertFalse((target.parent / "account.json").exists())

    def test_bad_hash_changes_no_existing_template(self):
        self.manifest["templates"][0]["sha256"] = "0" * 64
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "校验失败"):
            import_templates(self.source, self.root)
        self.assertFalse((self.root / "dev_assets").exists())

    def test_path_escape_is_refused(self):
        self.manifest["templates"][0]["name"] = "native/../../../account.png"
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "名称无效"):
            import_templates(self.source, self.root)

    def test_missing_current_template_is_refused(self):
        self.manifest["templates"] = []
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "缺少当前版本模板"):
            import_templates(self.source, self.root)
