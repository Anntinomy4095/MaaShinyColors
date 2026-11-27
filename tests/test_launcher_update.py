"""Launcher applies a staged update (tools/packaging/launcher/Launcher.cs --apply-update, 2026-11-27).

Compiles the real launcher with the Windows .NET Framework csc into a throw-away folder that looks like an installed
package, stages an update the way the host's 下载更新 does, and checks replace / remove / restore and player data.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import package

INTERFACE = '{\n  "name": "MaaShinyColors",\n  "version": "%s"\n}\n'


@unittest.skipUnless(package.CSC.is_file(), ".NET Framework csc not available")
class LauncherUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = Path(tempfile.mkdtemp())
        subprocess.run([str(package.CSC), "/nologo", "/target:winexe", "/optimize+", "/r:System.Windows.Forms.dll",
                        f"/out:{cls.build / 'MaaShinyColors.exe'}", str(package.LAUNCHER_SOURCE)], check=True,
                       capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.build, True)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        shutil.copy2(self.build / "MaaShinyColors.exe", self.root / "MaaShinyColors.exe")
        self.write("assets/interface.json", INTERFACE % "0.1.1")
        self.write("agent/a.py", "old a")
        self.write("agent/gone.py", "old")
        self.write("dev_assets/private/settings.json", "mine")
        self.staged = self.root / "dev_assets/private/update/staged"

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def stage(self, files: dict[str, str], removed=(), base="0.1.1", to="0.1.2", end=True, tamper=None):
        self.staged.mkdir(parents=True, exist_ok=True)
        lines = [f"to\t{to}", f"from\t{base}"]
        for relative, text in files.items():
            path = self.staged / "files" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            lines.append(f"file\t{relative}\t{hashlib.sha256(path.read_bytes()).hexdigest()}")
        lines += [f"remove\t{r}" for r in removed] + (["end"] if end else [])
        (self.staged / "ready.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if tamper:
            (self.staged / "files" / tamper).write_text("tampered", encoding="utf-8")

    def apply(self):
        done = subprocess.run([str(self.root / "MaaShinyColors.exe"), "--apply-update"], cwd=self.root, timeout=120)
        result = json.loads((self.root / "dev_assets/private/update/last-apply.json").read_text(encoding="utf-8"))
        return done.returncode, result

    def read(self, relative):
        return (self.root / relative).read_text(encoding="utf-8")

    def test_applies_files_removals_and_clears_staging(self):
        self.stage({"agent/a.py": "new a", "ui/new.js": "n", "assets/interface.json": INTERFACE % "0.1.2"},
                   removed=["agent/gone.py"])
        code, result = self.apply()
        self.assertEqual((code, result["ok"], result["version"], result["files"]), (0, True, "0.1.2", 4))
        self.assertEqual((self.read("agent/a.py"), self.read("ui/new.js")), ("new a", "n"))
        self.assertFalse((self.root / "agent/gone.py").exists())
        self.assertIn("0.1.2", self.read("assets/interface.json"))
        self.assertEqual(self.read("dev_assets/private/settings.json"), "mine")
        self.assertFalse(self.staged.exists())

    def test_launcher_replaces_itself(self):
        new_exe = (self.build / "MaaShinyColors.exe").read_bytes() + b"\0"   # any different bytes
        self.staged.mkdir(parents=True)
        (self.staged / "files").mkdir()
        (self.staged / "files" / "MaaShinyColors.exe").write_bytes(new_exe)
        (self.staged / "ready.txt").write_text(
            f"to\t0.1.2\nfrom\t0.1.1\nfile\tMaaShinyColors.exe\t{hashlib.sha256(new_exe).hexdigest()}\nend\n", encoding="utf-8")
        code, result = self.apply()
        self.assertEqual((code, result["ok"]), (0, True))
        self.assertEqual((self.root / "MaaShinyColors.exe").read_bytes(), new_exe)
        self.assertTrue((self.root / "MaaShinyColors.exe.old").exists())    # removed by the next launch

    def test_refusals_change_nothing(self):
        cases = {
            "wrong_base": dict(files={"agent/a.py": "x"}, base="0.1.0"),
            "incomplete": dict(files={"agent/a.py": "x"}, end=False),
            "tampered": dict(files={"agent/a.py": "x"}, tamper="agent/a.py"),
            "player_data": dict(files={"dev_assets/private/settings.json": "x"}),
            "game_assets": dict(files={"ui/game-assets/c.png": "x"}),
            "escape": dict(files={}, removed=["../outside.txt"]),
        }
        for name, case in cases.items():
            with self.subTest(name):
                self.stage(**case)
                code, result = self.apply()
                self.assertEqual((code, result["ok"]), (8, False))
                self.assertEqual(self.read("agent/a.py"), "old a")
                self.assertEqual(self.read("dev_assets/private/settings.json"), "mine")
                self.assertFalse(self.staged.exists())

    def test_failure_midway_restores_everything(self):
        self.stage({"agent/a.py": "new a", "agent/locked.py": "new"}, removed=["agent/gone.py"])
        self.write("agent/locked.py", "old locked")
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.restype = ctypes.c_void_p
        # GENERIC_READ, no sharing, OPEN_EXISTING: the launcher cannot overwrite it (retries ~10 s, then restores).
        handle = kernel.CreateFileW(str(self.root / "agent/locked.py"), 0x80000000, 0, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            code, result = self.apply()
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
        self.assertEqual((code, result["ok"]), (8, False))
        self.assertEqual(self.read("agent/a.py"), "old a")
        self.assertEqual(self.read("agent/gone.py"), "old")
        self.assertEqual(self.read("agent/locked.py"), "old locked")
        self.assertFalse((self.root / "agent/locked.py.old").exists())

    def test_nothing_staged_is_a_no_op(self):
        done = subprocess.run([str(self.root / "MaaShinyColors.exe"), "--apply-update"], cwd=self.root, timeout=60)
        self.assertEqual(done.returncode, 0)
        self.assertFalse((self.root / "dev_assets/private/update/last-apply.json").exists())


if __name__ == "__main__":
    unittest.main()
