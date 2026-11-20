"""scripts/resolve_python.ps1 and scripts/check.ps1: the project uses its own .venv, but the check commands are not
pinned to the .venv path. Order: active virtualenv > project .venv > ``python`` on PATH.
Offline: fake interpreters are empty files in temporary folders; nothing is executed through them."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESOLVER = ROOT / "scripts" / "resolve_python.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


@unittest.skipUnless(POWERSHELL and os.name == "nt", "Windows PowerShell not available")
class ResolvePythonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "project"
        self.project.mkdir()
        self.empty_path = self.tmp / "emptybin"
        self.empty_path.mkdir()

    def resolve(self, *, virtual_env=None, path=None):
        env = {k: v for k, v in os.environ.items() if k.upper() not in ("VIRTUAL_ENV", "PATH")}
        env["PATH"] = str(path or self.empty_path)
        if virtual_env is not None:
            env["VIRTUAL_ENV"] = str(virtual_env)
        result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(RESOLVER),
                                 "-ResolveRoot", str(self.project), "-ResolvePrint"],
                                capture_output=True, text=True, encoding="utf-8", timeout=60, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        source, _, found = result.stdout.strip().partition("|")
        return source, found

    def test_active_virtualenv_wins(self):
        active = touch(self.tmp / "other-venv" / "Scripts" / "python.exe")
        touch(self.project / ".venv" / "Scripts" / "python.exe")
        self.assertEqual(self.resolve(virtual_env=active.parent.parent), ("active-venv", str(active)))

    def test_project_venv_when_nothing_is_activated(self):
        venv = touch(self.project / ".venv" / "Scripts" / "python.exe")
        self.assertEqual(self.resolve(), ("project-venv", str(venv)))
        # A stale VIRTUAL_ENV without an interpreter falls through to the project .venv.
        self.assertEqual(self.resolve(virtual_env=self.tmp / "gone"), ("project-venv", str(venv)))

    def test_path_python_last_and_missing_reported(self):
        on_path = touch(self.tmp / "bin" / "python.exe")
        self.assertEqual(self.resolve(path=on_path.parent), ("path", str(on_path)))
        self.assertEqual(self.resolve(), ("missing", ""))

    def test_dot_sourcing_does_not_clobber_the_callers_project_root(self):
        venv = touch(self.project / ".venv" / "Scripts" / "python.exe")
        script = (f"$projectRoot = '{self.project}'; . '{RESOLVER}'; "
                  "$r = Resolve-ProjectPython -ProjectRoot $projectRoot; Write-Output \"$projectRoot|$($r.Path)\"")
        env = {k: v for k, v in os.environ.items() if k.upper() != "VIRTUAL_ENV"}
        result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                                capture_output=True, text=True, encoding="utf-8", timeout=60, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"{self.project}|{venv}")


class CheckScriptContractTests(unittest.TestCase):
    def test_check_script_resolves_reports_and_fails_loudly(self):
        script = (ROOT / "scripts" / "check.ps1").read_text(encoding="utf-8")
        self.assertNotIn(".venv\\Scripts\\python.exe", script)  # never pinned to one interpreter path
        self.assertIn("Resolve-ProjectPython", script)
        self.assertIn('Write-Output "Python: $python', script)
        self.assertIn('-c "import maa"', script)              # maafw importability is checked ...
        self.assertIn("will be SKIPPED", script)               # ... and its absence is a loud warning
        self.assertIn("-m unittest discover -s tests -v", script)
        self.assertEqual(script.count("if ($LASTEXITCODE -ne 0) { throw"), 3)

    def test_other_scripts_use_the_resolver_and_docs_stay_generic(self):
        for name in ("check_browser_demo.ps1", "check_browser_input_probe.ps1", "check_observation.ps1",
                     "run_offline_click_experiment.ps1"):
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            with self.subTest(name):
                self.assertIn("Resolve-ProjectPython", text)
                self.assertNotIn(".venv\\Scripts\\python.exe", text)
                self.assertNotIn(".venv/Scripts/python.exe", text)
        guide = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("python -m unittest discover -s tests -v", guide)
        self.assertRegex(guide, r"virtual environment|虚拟环境")
        self.assertNotIn(".venv\\Scripts\\python.exe -m unittest", guide)


if __name__ == "__main__":
    unittest.main()
