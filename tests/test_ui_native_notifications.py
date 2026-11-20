"""ANN-48: run actual C# notification selection without a game, WebView or desktop notification."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("dotnet"), "dotnet not installed")
class NativeNotificationTests(unittest.TestCase):
    def test_run_notifications(self):
        private = ROOT / "dev_assets/private/ui-notification-tests"
        private.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as folder:
            project = Path(folder)
            source = ROOT / "browser_host/Ui/UiRunNotifications.cs"
            (project / "test.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<OutputType>Exe</OutputType><TargetFramework>net10.0-windows</TargetFramework>'
                '<UseWindowsForms>true</UseWindowsForms><ImplicitUsings>enable</ImplicitUsings>'
                '<Nullable>enable</Nullable><NuGetAudit>false</NuGetAudit></PropertyGroup><ItemGroup>'
                f'<Compile Include="{source}" Link="UiRunNotifications.cs" />'
                '</ItemGroup></Project>', encoding="utf-8")
            (project / "Program.cs").write_text(r'''
using System.Text.Json.Nodes;
using MaaShinyColors.BrowserHost.Ui;

var notes = new UiRunNotifications();
JsonObject Row(string run, string status, bool finished = false) => new() {
    ["run_id"] = run, ["state"] = finished ? "finished" : "running",
    [finished ? "outcome" : "status"] = status,
};
void Check(bool ok, string message) { if (!ok) throw new Exception(message); }
Check(notes.Handle("log.append", Row("a", "failed")) is null, "logs are not reminders");
Check(notes.Handle("run.update", Row("a", "running")) is null, "no progress reminder");
Check(notes.Handle("run.update", Row("a", "failed"))?.Key == "failed", "failure");
Check(notes.Handle("run.update", Row("a", "failed")) is null, "reconciled failure deduplicated");
Check(notes.Handle("run.update", Row("a", "uncertain")) is null, "no repeated attention while reconciling failure");
Check(notes.Handle("run.update", Row("a", "failed", true)) is null, "no duplicate end failure");
Check(notes.Handle("run.update", Row("b", "succeeded", true))?.Key == "all_done", "completion");
Check(notes.Handle("run.update", Row("b", "succeeded", true)) is null, "completion once");
Check(notes.Handle("run.update", Row("c", "takeover"))?.Key == "takeover", "manual takeover");
Check(notes.Handle("run.update", Row("c", "failed", true)) is null, "do not cover takeover with failure");
Check(notes.Handle("run.update", Row("d", "uncertain", true))?.Key == "uncertain", "abnormal exit");
Check(notes.Handle("run.update", Row("e", "refused", true))?.Key == "guard_refused", "refused before start");
notes.SetProfile(JsonNode.Parse("{\"prefs\":{\"notify\":{\"failed\":false,\"all_done\":true}}}")!.AsObject());
Check(notes.Handle("run.update", Row("f", "failed")) is null, "failure setting off");
Check(notes.Handle("run.update", Row("f", "failed", true)) is null, "all_done must not bypass disabled failure");
Check(notes.Handle("run.update", Row("g", "succeeded", true))?.Key == "all_done", "completion remains enabled");
notes.SetProfile(JsonNode.Parse("{\"prefs\":{\"notify\":{\"takeover\":false,\"all_done\":false,\"uncertain\":false}}}")!.AsObject());
Check(notes.Handle("run.update", Row("h", "takeover")) is null, "takeover off");
Check(notes.Handle("run.update", Row("i", "succeeded", true)) is null, "completion off");
Check(notes.Handle("run.update", Row("j", "uncertain", true)) is null, "uncertain off");
notes.SetProfile(new());
Check(notes.Handle("run.update", Row("k", "stopped", true))?.Key == "all_done", "settings update applies");
Console.WriteLine("native notification checks passed");
''', encoding="utf-8")
            result = subprocess.run(["dotnet", "run", "--project", str(project / "test.csproj"),
                                     "--verbosity", "quiet"], cwd=ROOT, capture_output=True,
                                    text=True, encoding="utf-8", timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("native notification checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
