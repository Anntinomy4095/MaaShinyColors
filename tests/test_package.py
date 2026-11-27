"""Offline contracts for the portable package builder (tools/package.py). No build, no network."""
from __future__ import annotations

import re
import json
import shutil
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from pathlib import Path
from unittest import mock
from PIL import Image

from tools import package

ROOT = Path(__file__).resolve().parent.parent


def fake_recognition(out, source):
    pipeline = out / "assets/resource/base/pipeline/test.json"
    pipeline.parent.mkdir(parents=True, exist_ok=True)
    pipeline.write_text(json.dumps({"SC.Test": {"template": "native/test/anchor.png", "roi": [0, 0, 100, 100]}}), encoding="utf-8")
    path = source / "image/native/test/anchor.png"
    path.parent.mkdir(parents=True)
    Image.new("RGB", (30, 10), (120, 90, 80)).save(path)
    manifest = {"version": 1, "canvas": [1280, 720], "template_prefix": "native/",
                "private_source": "excluded", "templates": [{"name": "native/test/anchor.png",
                "size": [30, 10], "sha256": package.sha256_file(path), "source": "excluded"}]}
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pipeline.relative_to(out).as_posix()


class SelectionTests(unittest.TestCase):
    def test_private_and_game_derived_paths_are_never_selected(self):
        files = [
            "agent/main.py", "tools/ui_engine.py", "ui/app.js", "ui/icon.png", "assets/interface.json", "NOTES.md",
            "tools/package.py", "deps/tools/schema.json", "tests/fixtures/ui/fake_dom.mjs",
            "project_policy.json", "tests/fixtures/ui/fake_engine.py", "docs/SAFETY.md",
            "dev_assets/private/webview2-profile/Default/Cookies", "ui/game-assets/cards/1.png",
            "dev_assets/private/game-assets-src/enza-1.webp", "tools/__pycache__/x.pyc", "docs/notes.md",
            "tests/test_shop_task.py", "node_modules/a/index.js", "browser_host/Program.cs",
            ".vscode/settings.json", ".github/workflows/check.yml", "docs/research/notes.md",
            "ui/NOTES.md",
        ]
        selected = package.select_package_files(files)
        self.assertEqual(selected, ["agent/main.py", "tools/ui_engine.py", "ui/icon.png", "assets/interface.json",
                                    "project_policy.json", "tests/fixtures/ui/fake_engine.py",
                                    "docs/SAFETY.md"])

    def test_host_sources_exclude_game_assets(self):
        files = ["browser_host/Program.cs", "ui/index.html", "ui/game-assets/manifest.js", "assets/input/pipeline_actions.json",
                 "browser_host/bin/Debug/x.dll", "agent/main.py", "ui/fixtures/schema.json", "ui/fixtures/demo-state.json"]
        self.assertEqual(package.select_host_sources(files), ["browser_host/Program.cs", "ui/index.html",
                                                              "assets/input/pipeline_actions.json", "ui/fixtures/schema.json"])

    def test_publish_uses_pinned_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, calls = Path(tmp) / "out", []

            def run(command, cwd=None, env=None, timeout=1800):
                calls.append(command)
                if "publish" in command:
                    (out / "host").mkdir(parents=True, exist_ok=True)
                    (out / "host" / package.HOST_EXE).write_bytes(b"MZ")
                return "10.0.403"

            with mock.patch.object(package, "run", run), mock.patch.object(package, "checked_output", lambda p: p):
                info = package.build_host([], out, Path(tmp) / "cache")
            publish = next(c for c in calls if "publish" in c)
            self.assertEqual(info["edition"], "release")
            self.assertIn(f"-p:RuntimeFrameworkVersion={info['dotnet_runtime']}", publish)

    def test_forbidden_markers(self):
        for path in ("a/dev_assets/b", "host/ui/game-assets/x.js", "x/EBWebView/y", "a/b.webp", "r/events.jsonl",
                     "host/x.pdb", "a/.git/config", "NOTES.md", "docs/notes.md", "agent/.cache/x",
                     "ui/NOTES.local.md", "agent/.local/skills/x.md"):
            self.assertIsNotNone(package.is_forbidden(path), path)
        self.assertIsNone(package.is_forbidden("tools/capture_browser_window.py"))
        self.assertIsNone(package.is_forbidden("agent/core/wing_nav.py"))

    def test_output_folder_is_confined(self):
        with self.assertRaises(SystemExit):
            package.checked_output(ROOT)
        with self.assertRaises(SystemExit):
            package.checked_output(ROOT / "dist")
        self.assertEqual(package.checked_output(ROOT / "dist" / "MaaShinyColors"), (ROOT / "dist" / "MaaShinyColors").resolve())


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.source = Path(tempfile.mkdtemp())
        self.files = ["assets/interface.json", "project_policy.json", "tools/ui_engine.py", "ui/icon.png"]
        for item in self.files + ["MaaShinyColors.exe", "PACKAGE_MANIFEST.json", "host/MaaShinyColors.BrowserHost.exe",
                                  "host/ui/index.html", ".venv/Scripts/python.exe"]:
            (self.temp / item).parent.mkdir(parents=True, exist_ok=True)
            (self.temp / item).write_text("x", encoding="utf-8")
        self.files.extend(["ui/index.html", fake_recognition(self.temp, self.source)])
        package.build_recognition(self.temp, self.source)

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)
        shutil.rmtree(self.source, ignore_errors=True)

    def test_clean_package_passes(self):
        self.assertEqual(package.audit(self.temp, self.files), [])

    def test_private_data_fails_build_audit_but_runtime_data_is_tolerated_in_smoke(self):
        (self.temp / "dev_assets" / "private").mkdir(parents=True)
        (self.temp / "dev_assets" / "private" / "profile.json").write_text("{}", encoding="utf-8")
        self.assertTrue(package.audit(self.temp, self.files))
        self.assertEqual(package.audit(self.temp, self.files, runtime=True), [])

    def test_game_assets_in_host_ui_fail_even_at_runtime(self):
        (self.temp / "host" / "ui" / "game-assets").mkdir(parents=True)
        (self.temp / "host" / "ui" / "game-assets" / "manifest.js").write_text("x", encoding="utf-8")
        self.assertTrue(package.audit(self.temp, self.files, runtime=True))

    def test_unlisted_file_and_missing_launcher_fail(self):
        (self.temp / "notes.txt").write_text("x", encoding="utf-8")
        (self.temp / "MaaShinyColors.exe").unlink()
        problems = package.audit(self.temp, self.files)
        self.assertTrue(any(item.startswith("notes.txt") for item in problems))
        self.assertTrue(any(item.startswith("MaaShinyColors.exe") for item in problems))

    def test_recognition_export_strips_source_metadata_and_only_copies_required_templates(self):
        bundle = self.temp / package.RECOGNITION_TARGET
        clean = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(clean), {"version", "canvas", "template_prefix", "templates"})
        self.assertEqual(set(clean["templates"][0]), {"name", "sha256", "size"})
        self.assertEqual(len(list(bundle.rglob("*.png"))), 1)

    def test_missing_or_modified_recognition_fails_even_at_runtime(self):
        image = self.temp / package.RECOGNITION_TARGET / "image/native/test/anchor.png"
        image.write_bytes(b"corrupted")
        self.assertTrue(any("recognition" in p for p in package.audit(self.temp, self.files, runtime=True)))
        image.unlink()
        self.assertTrue(any("recognition" in p for p in package.audit(self.temp, self.files)))

    def test_build_refuses_missing_required_source(self):
        (self.source / "image/native/test/anchor.png").unlink()
        with self.assertRaises(ValueError):
            package.build_recognition(self.temp, self.source)

    def test_import_existing_portable_zip_reads_only_required_recognition_files(self):
        archive = self.source / "old-portable.zip"
        bundle = self.temp / package.RECOGNITION_TARGET
        with zipfile.ZipFile(archive, "w") as handle:
            for path in bundle.rglob("*"):
                if path.is_file():
                    handle.write(path, "MaaShinyColors/" + path.relative_to(self.temp).as_posix())
            handle.writestr("MaaShinyColors/dev_assets/private/profile.json", "private")
            handle.writestr("../../escaped.txt", "not extracted")
            handle.writestr("MaaShinyColors/assets/resource/recognition/image/native/unused.png", "not required")
        shutil.rmtree(bundle)
        package.build_recognition(self.temp, archive)
        self.assertEqual(package.audit(self.temp, self.files), [])
        self.assertFalse((self.temp / "dev_assets").exists())
        self.assertFalse((bundle / "image/native/unused.png").exists())

    def test_resource_export_is_clean_and_can_be_reimported(self):
        archive = self.source / "recognition-resources.zip"
        args = SimpleNamespace(out=archive, recognition_source=self.source)
        with mock.patch.object(package, "ROOT", self.temp), \
                mock.patch.object(package, "checked_output", return_value=archive):
            self.assertEqual(package.export_recognition(args), 0)
        with zipfile.ZipFile(archive) as handle:
            self.assertEqual(set(handle.namelist()), {
                "assets/resource/recognition/manifest.json",
                "assets/resource/recognition/image/native/test/anchor.png",
            })
            manifest = json.loads(handle.read("assets/resource/recognition/manifest.json"))
            self.assertNotIn("private_source", manifest)
            self.assertNotIn("source", manifest["templates"][0])
        shutil.rmtree(self.temp / package.RECOGNITION_TARGET)
        package.build_recognition(self.temp, archive)
        self.assertEqual(package.audit(self.temp, self.files), [])

    def test_resource_zip_with_missing_or_modified_required_crop_fails(self):
        bundle = self.temp / package.RECOGNITION_TARGET
        archive = self.source / "incomplete.zip"
        for image in (None, b"modified"):
            with self.subTest(image=image), zipfile.ZipFile(archive, "w") as handle:
                handle.write(bundle / "manifest.json", f"{package.RECOGNITION_TARGET}/manifest.json")
                if image is not None:
                    handle.writestr(f"{package.RECOGNITION_TARGET}/image/native/test/anchor.png", image)
            with self.assertRaises(ValueError):
                package.build_recognition(self.temp, archive)

    def test_resource_zip_requires_one_recognition_manifest(self):
        archive = self.source / "ambiguous.zip"
        for prefixes in ([], ["one/", "two/"]):
            with self.subTest(prefixes=prefixes), zipfile.ZipFile(archive, "w") as handle:
                for prefix in prefixes:
                    handle.writestr(prefix + f"{package.RECOGNITION_TARGET}/manifest.json", "{}")
            with self.assertRaisesRegex(ValueError, "exactly one"):
                package.build_recognition(self.temp, archive)

    def test_archive_retains_recognition_but_excludes_runtime_private_data(self):
        import zipfile
        (self.temp / "PACKAGE_MANIFEST.json").write_text(json.dumps({"source": {"commit": "test"}}), encoding="utf-8")
        private = self.temp / "dev_assets/private/profile.json"
        private.parent.mkdir(parents=True)
        private.write_text("{}", encoding="utf-8")
        with mock.patch.object(package, "checked_output", return_value=self.temp), \
                mock.patch.object(package, "repository_files", return_value=self.files):
            archive = package.write_archive(self.temp)
        try:
            with zipfile.ZipFile(archive) as z:
                names = z.namelist()
                self.assertTrue(any(n.endswith("recognition/image/native/test/anchor.png") for n in names))
                self.assertFalse(any("dev_assets/" in n for n in names))
                self.assertFalse(any(n.endswith(".cmd") for n in names))
        finally:
            archive.unlink()


class RuntimeRecognitionTests(unittest.TestCase):
    def test_load_order_keeps_bundled_recognition_without_private_override(self):
        from agent.core import resource_bundles as rb
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundled, private = root / "recognition", root / "private"
            for path in (bundled, private):
                path.mkdir()
                (path / "manifest.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(rb, "PACKAGED_RECOGNITION_BUNDLE", bundled), \
                    mock.patch.object(rb, "PRIVATE_NATIVE_BUNDLE", private):
                self.assertEqual(rb.bundle_paths(), [rb.BASE_BUNDLE, bundled, private])
                self.assertEqual(rb.bundle_paths(include_private=False), [rb.BASE_BUNDLE, bundled])


class PinTests(unittest.TestCase):
    def test_runtime_lock_is_fully_hashed_and_matches_requirements(self):
        lines = [line for line in package.RUNTIME_LOCK.read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.startswith("#")]
        pins = {}
        for line in lines:
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([0-9.]+) --hash=sha256:([0-9a-f]{64})", line)
            self.assertIsNotNone(match, line)
            pins[match.group(1).lower()] = match.group(2)
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
            if "==" in line and not line.startswith("#"):
                name, version = line.strip().split("==")
                self.assertEqual(pins.get(name.lower()), version, name)

    def test_embed_and_ocr_hashes_are_pinned(self):
        self.assertRegex(package.EMBED_SHA256, r"^[0-9a-f]{64}$")
        self.assertTrue(package.EMBED_URL.startswith("https://www.python.org/ftp/python/"))
        self.assertEqual(set(package.OCR_FILES), {"det.onnx", "rec.onnx", "keys.txt"})

    def test_dist_is_gitignored(self):
        self.assertIn("dist/", (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())


class LauncherSourceTests(unittest.TestCase):
    """Normal EXE launch enables tasks; offline tools keep their separate fixed arguments."""

    def setUp(self):
        self.source = package.LAUNCHER_SOURCE.read_text(encoding="utf-8")

    def test_only_safe_host_arguments(self):
        code = "\n".join(line for line in self.source.splitlines() if not line.lstrip().startswith("//"))
        self.assertIn('bool supervised = args.Length == 0 || (args.Length == 1 && args[0] == "--supervised");', code)
        self.assertIn('string hostArgs = supervised ?', code)
        self.assertIn('"--composition-game --enable-pipeline-input --enable-produce-explore"', code)
        self.assertNotIn("--background-keepalive", code)
        self.assertIn('assets\\\\resource\\\\recognition\\\\manifest.json', code)
        self.assertIn('resource_native\\\\manifest.json', code)
        self.assertIn('HostAlreadyRunning(host, true)', code)
        self.assertIn('MaaShinyColors.ShowExisting.v1', code)
        self.assertIn('OfflineModes = { "--self-test-ui", "--self-test-ui-dock", "--ui-preview" }', code)

    def test_unknown_arguments_are_refused(self):
        self.assertIn("if (!(args.Length == 0 || check || offline || supervised || import || update || applyOnly))", self.source)


if __name__ == "__main__":
    unittest.main()


def _full_zip(path: Path, version: str, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as handle:
        handle.writestr("MaaShinyColors/assets/interface.json", json.dumps({"version": version}))
        for name, data in files.items():
            handle.writestr(f"MaaShinyColors/{name}", data)
    return path


class ReleaseArtifactTests(unittest.TestCase):
    """2026-11-27: full zip + update zip for GitHub, full zip with the private resources for the user's own PCs."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.old = _full_zip(self.tmp / "old.zip", "0.1.0", {"agent/a.py": b"a", "agent/gone.py": b"g", "host/x.dll": b"x"})
        self.new = _full_zip(self.tmp / "new.zip", "0.1.1", {"agent/a.py": b"a2", "host/x.dll": b"x", "ui/new.js": b"n"})

    def test_update_zip_carries_only_changed_files_and_removals(self):
        archive = package.make_patch(self.old, self.new, self.tmp / "out")
        self.assertEqual(archive.name, "MaaShinyColors-update-0.1.0-to-0.1.1.zip")
        with zipfile.ZipFile(archive) as handle:
            names = sorted(handle.namelist())
            patch = json.loads(handle.read("MaaShinyColors/UPDATE_PATCH.json"))
            self.assertEqual(handle.read("MaaShinyColors/agent/a.py"), b"a2")
        self.assertEqual(names, ["MaaShinyColors/UPDATE_PATCH.json", "MaaShinyColors/agent/a.py",
                                 "MaaShinyColors/assets/interface.json", "MaaShinyColors/ui/new.js"])
        self.assertEqual((patch["from"], patch["to"], patch["removed"]), ("0.1.0", "0.1.1", ["agent/gone.py"]))
        self.assertEqual(set(patch["files"]), {"agent/a.py", "assets/interface.json", "ui/new.js"})
        with self.assertRaises(ValueError):
            package.make_patch(self.new, self.old, self.tmp / "out")       # never backwards

    def test_player_data_never_enters_an_update(self):
        bad = _full_zip(self.tmp / "bad.zip", "0.1.2", {"dev_assets/private/x.json": b"1"})
        with self.assertRaises(ValueError):
            package.make_patch(self.new, bad, self.tmp / "out")

    def test_with_resources_adds_only_player_data(self):
        pack = self.tmp / "pack.zip"
        with zipfile.ZipFile(pack, "w") as handle:
            handle.writestr("MaaShinyColors\\ui\\game-assets\\cards\\1.png", b"p")   # Compress-Archive style name
            handle.writestr("MaaShinyColors/dev_assets/private/shinycolorsdb/s.json", b"s")
        out = package.with_resources(self.new, pack, self.tmp / "with.zip")
        with zipfile.ZipFile(out) as handle:
            self.assertIn("MaaShinyColors/ui/game-assets/cards/1.png", handle.namelist())
            self.assertIn("MaaShinyColors/agent/a.py", handle.namelist())
        with zipfile.ZipFile(pack, "a") as handle:
            handle.writestr("MaaShinyColors/agent/a.py", b"evil")
        with self.assertRaises(ValueError):
            package.with_resources(self.new, pack, self.tmp / "with2.zip")
        recovered = package.resources_from(out, self.new, self.tmp / "recovered.zip")
        with zipfile.ZipFile(recovered) as handle:
            self.assertEqual(sorted(handle.namelist()), ["MaaShinyColors/dev_assets/private/shinycolorsdb/s.json",
                                                         "MaaShinyColors/ui/game-assets/cards/1.png"])

    def test_release_writes_three_artifacts_and_keeps_the_full_zip(self):
        pack = self.tmp / "pack.zip"
        with zipfile.ZipFile(pack, "w") as handle:
            handle.writestr("MaaShinyColors/ui/game-assets/cards/1.png", b"p")
        releases = self.tmp / "releases"
        (releases / "0.1.0").mkdir(parents=True)
        shutil.copy2(self.old, releases / "0.1.0" / "MaaShinyColors.zip")
        out = package.ROOT / "dist" / "release-test-tmp"
        self.addCleanup(shutil.rmtree, out, True)
        with mock.patch.object(package, "RELEASES", releases):
            package.release(SimpleNamespace(zip=self.new, previous=None, resources=pack, out=out))
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["MaaShinyColors-update-0.1.0-to-0.1.1.zip", "MaaShinyColors-with-resources.zip",
                          "MaaShinyColors.zip", "SHA256SUMS.txt"])
        sums = (out / "SHA256SUMS.txt").read_text(encoding="utf-8")
        self.assertNotIn("with-resources", sums)                         # never listed for upload
        self.assertTrue((releases / "0.1.1" / "MaaShinyColors.zip").is_file())

    def test_version_order(self):
        self.assertLess(package.version_key("0.1.9"), package.version_key("0.1.10"))
        self.assertLess(package.version_key("v0.2.0-beta.1"), package.version_key("0.2.0"))
        self.assertEqual(package.interface_version('{\n  // JSONC\n  "title": "x",\n  "version": "0.1.2",\n}\n'), "0.1.2")
