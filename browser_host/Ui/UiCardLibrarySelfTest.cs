using System.Diagnostics;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>Offline updater fixtures only; never invokes the real card updater or a game session.</summary>
internal static class UiCardLibrarySelfTest
{
    internal static string Run(string output)
    {
        var checks = new JsonArray();
        void Check(string name, bool pass)
        {
            checks.Add(new JsonObject { ["check"] = name, ["pass"] = pass });
            if (!pass) { throw new InvalidOperationException("card-library self-test failed: " + name); }
        }
        var root = Path.Combine(output, "card-library-" + Guid.NewGuid().ToString("N"));
        var assets = Path.Combine(root, "ui", "game-assets");
        var destination = Path.Combine(root, "host", "ui", "game-assets");
        Directory.CreateDirectory(assets);
        Directory.CreateDirectory(destination);
        var reportPath = Path.Combine(root, "fixture-report.json");
        var releasePath = Path.Combine(root, "release.fixture");
        var childPidPath = Path.Combine(root, "child.pid");
        var historicalPath = Path.Combine(root, "dev_assets", "private", "shinycolorsdb", "last-update.json");
        foreach (var name in new[] { "manifest.json", "manifest.js", "cards/a.json", "items/a.json", "icons/a.webp" })
        {
            var path = Path.Combine(assets, name.Replace('/', Path.DirectorySeparatorChar));
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(path, "public-" + name);
        }
        foreach (var name in new[] { "owned-levels.js", "book-library.js", "team-proposal.js", "inventory-scan.js" })
        {
            File.WriteAllText(Path.Combine(assets, name), "should-not-publish");
            File.WriteAllText(Path.Combine(destination, name), "personal-kept");
        }
        string Quote(string value) => "'" + value.Replace("'", "''") + "'";
        string scenario = "success";
        ProcessStartInfo Child()
        {
            var commands = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);";
            if (scenario is "wait" or "tree")
            {
                if (scenario == "tree")
                {
                    commands += "$p=Start-Process -FilePath powershell.exe -ArgumentList '-NoProfile -NonInteractive -Command Start-Sleep -Seconds 60' -WindowStyle Hidden -PassThru;"
                        + "[IO.File]::WriteAllText(" + Quote(childPidPath) + ",$p.Id.ToString());";
                }
                commands += "while(-not [IO.File]::Exists(" + Quote(releasePath) + ")){Start-Sleep -Milliseconds 30};";
            }
            if (scenario == "batches")
            {
                foreach (var count in new[] { 100, 200, 240 })
                {
                    var progress = new JsonObject { ["event"] = "card_library_progress", ["phase"] = "cards", ["updated_cards"] = count, ["total_cards"] = 240 };
                    commands += "[Console]::Error.WriteLine(" + Quote(progress.ToJsonString()) + ");";
                    if (count == 100)
                    { commands += "while(-not [IO.File]::Exists(" + Quote(releasePath) + ")){Start-Sleep -Milliseconds 30};"; }
                }
            }
            // Both pipes exceed their OS buffer, proving concurrent drain rather than waiting before reading.
            if (scenario == "verbose") { commands += "1..1000|%{[Console]::Error.WriteLine(('log-'+('x'*100)))};"; }
            commands += scenario == "bad" ? "[Console]::Out.Write('not-json');" : "[Console]::Out.Write([IO.File]::ReadAllText(" + Quote(reportPath) + "));";
            commands += scenario is "nonzero" or "partial-exit" ? "exit 3" : "exit 0";
            var info = UiEngineSupervisor.NewStartInfo(root, "powershell.exe");
            info.RedirectStandardInput = false;
            foreach (var arg in new[] { "-NoProfile", "-NonInteractive", "-Command", commands }) { info.ArgumentList.Add(arg); }
            return info;
        }
        JsonObject Report(string status = "updated", bool complete = true, int updated = 4) => new()
        {
            ["status"] = status, ["checked_at"] = "2026-10-18T00:00:00Z", ["complete"] = complete,
            ["cards_complete"] = complete, ["previous_cards"] = 1467, ["local_cards"] = 1471,
            ["catalog_cards"] = 1471, ["updated_cards"] = updated, ["remaining_cards"] = complete ? 0 : 20,
            ["added"] = new JsonArray("a", "b", "c"), ["changed"] = new JsonArray("d"),
            ["icons"] = new JsonObject { ["failed"] = new JsonArray(), ["stopped"] = false },
        };
        void WriteReport(JsonObject report) => File.WriteAllText(reportPath, report.ToJsonString());
        bool Until(Func<bool> predicate, int timeout = 15_000)
        {
            var watch = Stopwatch.StartNew();
            while (watch.ElapsedMilliseconds < timeout)
            {
                if (predicate()) { return true; }
                Thread.Sleep(20);
            }
            return predicate();
        }
        JsonObject Wait(UiCardLibrary service)
        {
            Check("fixture_completes_" + scenario, Until(() => (string?)service.Status()["state"] != "running"));
            return service.Status();
        }
        bool Alive(int pid)
        {
            try { using var process = Process.GetProcessById(pid); return !process.HasExited; }
            catch (ArgumentException) { return false; }
        }
        int? ChildPid()   // the fixture child may still hold the file open while writing it
        {
            try { return int.TryParse(File.ReadAllText(childPidPath), out var pid) ? pid : null; }
            catch (IOException) { return null; }
        }

        using (var service = new UiCardLibrary(root, Child, destination))
        {
            Check("missing_report_idle_supported", (bool?)service.Status()["supported"] == true
                && (string?)service.Status()["state"] == "idle" && service.Status()["last"] is null);
            WriteReport(Report());
            Check("start_without_game_connection", service.Start() is null);
            var status = Wait(service);
            Check("success_summary", (string?)status["state"] == "done" && (int?)status["finished_seq"] == 1
                && (int?)status["last"]?["local_cards"] == 1471 && (int?)status["last"]?["added_cards"] == 3
                && (int?)status["last"]?["changed_cards"] == 1);
            Check("public_manifest_and_folders_synced", File.ReadAllText(Path.Combine(destination, "manifest.js")) == "public-manifest.js"
                && File.ReadAllText(Path.Combine(destination, "cards", "a.json")) == "public-cards/a.json"
                && File.Exists(Path.Combine(destination, "items", "a.json")) && File.Exists(Path.Combine(destination, "icons", "a.webp")));
            Check("personal_modules_preserved", new[] { "owned-levels.js", "book-library.js", "team-proposal.js", "inventory-scan.js" }
                .All(name => File.ReadAllText(Path.Combine(destination, name)) == "personal-kept"));

            WriteReport(Report("unchanged", updated: 0));
            service.Start(); status = Wait(service);
            Check("unchanged_is_done_and_sequence_advances", (string?)status["state"] == "done" && (int?)status["finished_seq"] == 2
                && (string?)status["last"]?["status"] == "unchanged");

            var iconsFailed = Report("stopped");
            iconsFailed["complete"] = false;
            iconsFailed["icons"]!["failed"] = new JsonArray("missing-icon");
            WriteReport(iconsFailed); scenario = "partial-exit";
            service.Start(); status = Wait(service);
            Check("icon_failure_nonzero_with_complete_cards_is_partial", (string?)status["state"] == "partial"
                && (bool?)status["last"]?["cards_complete"] == true && (int?)status["last"]?["icons_failed"] == 1);
            iconsFailed["icons"]!["failed"] = new JsonArray();
            iconsFailed["icons"]!["stopped"] = true;
            WriteReport(iconsFailed);
            service.Start(); status = Wait(service);
            Check("icon_stop_with_complete_cards_is_partial", (string?)status["state"] == "partial" && (bool?)status["last"]?["icons_stopped"] == true);
            iconsFailed["icons"]!["stopped"] = "download_budget_exhausted";
            WriteReport(iconsFailed);
            service.Start(); status = Wait(service);
            Check("icon_string_budget_stop_is_partial", (string?)status["state"] == "partial" && (bool?)status["last"]?["icons_stopped"] == true
                && (bool?)status["last"]?["cards_complete"] == true);

            WriteReport(Report("stopped", complete: false, updated: 100));
            service.Start(); status = Wait(service);
            Check("enabled_card_batches_retained_as_partial", (string?)status["state"] == "partial"
                && (int?)status["last"]?["updated_cards"] == 100 && (int?)status["last"]?["remaining_cards"] == 20);

            var lastBeforeProgress = service.Status()["last"]?.ToJsonString();
            scenario = "batches"; WriteReport(Report(updated: 240));
            service.Start();
            Check("first_install_progress_before_final_report", Until(() => (int?)service.Status()["updated_cards"] == 100)
                && (string?)service.Status()["phase"] == "cards" && (int?)service.Status()["total_cards"] == 240
                && service.Status()["last"]?.ToJsonString() == lastBeforeProgress);
            Check("duplicate_start_rejected", service.Start() == "already_running");
            File.WriteAllText(releasePath, "go"); status = Wait(service); File.Delete(releasePath);
            Check("first_install_multiple_batches_complete", (string?)status["state"] == "done" && (int?)status["last"]?["updated_cards"] == 240);

            scenario = "verbose";
            var verbose = Report(); verbose["fixture_padding"] = new string('x', 100_000); WriteReport(verbose);
            service.Start(); status = Wait(service);
            Check("stdout_and_stderr_drained_without_blocking", (string?)status["state"] == "done");

            var lastBeforeFailure = service.Status()["last"]?.ToJsonString();
            scenario = "nonzero"; WriteReport(Report()); service.Start(); status = Wait(service);
            Check("nonzero_does_not_claim_success_and_preserves_local_summary", (string?)status["state"] == "failed"
                && (string?)status["error"] == "exit_3" && status["last"]?.ToJsonString() == lastBeforeFailure);
            scenario = "bad"; service.Start(); status = Wait(service);
            Check("invalid_json_preserves_old_summary_without_claiming_success", (string?)status["state"] == "failed"
                && (string?)status["error"] == "invalid_report" && status["last"]?.ToJsonString() == lastBeforeFailure);
            scenario = "wait"; service.Start();
            Check("stop_accepted_once", service.RequestStop() && !service.RequestStop()); status = Wait(service);
            Check("stop_finishes_without_retries", (string?)status["state"] == "stopped" && status["last"]?.ToJsonString() == lastBeforeFailure);

            // 2026-10-31: reading the library refreshes it when the last check is older than AutoInterval.
            var now = DateTimeOffset.UtcNow;
            // 2026-11-22 (new install): without local card icons a recent check is never "fresh"
            var icons = Path.Combine(root, "ui", "game-assets", "manifest.json");
            var iconsBefore = File.Exists(icons) ? File.ReadAllText(icons) : "{}";
            File.Delete(icons);
            Check("missing_icons_are_never_fresh", service.AssetsMissing && service.StartIfStale(now) is null);
            File.WriteAllText(releasePath, "go"); Wait(service); File.Delete(releasePath);
            Directory.CreateDirectory(Path.GetDirectoryName(icons)!); File.WriteAllText(icons, iconsBefore);
            Check("auto_refresh_skips_a_fresh_check", !service.AssetsMissing && service.StartIfStale(now) == "fresh");
            var later = now + UiCardLibrary.AutoInterval + TimeSpan.FromMinutes(1);
            scenario = "wait";
            Check("auto_refresh_starts_when_stale", service.StartIfStale(later) is null && (bool?)service.Status()["auto"] == true
                && service.StartIfStale(later) == "already_running");
            Check("run_stops_only_the_auto_refresh", service.StopAuto()); status = Wait(service);
            Check("stopped_auto_refresh_retries_on_next_read", (string?)status["state"] == "stopped" && service.StartIfStale(later) is null);
            File.WriteAllText(releasePath, "go"); status = Wait(service); File.Delete(releasePath);
            Check("tried_auto_refresh_waits_an_interval", service.StartIfStale(later + TimeSpan.FromMinutes(1)) == "fresh");
            service.Start();
            Check("manual_update_is_not_auto_and_not_stopped", (bool?)service.Status()["auto"] == false && !service.StopAuto());
            File.WriteAllText(releasePath, "go"); Wait(service); File.Delete(releasePath);
        }

        scenario = "tree"; WriteReport(Report());
        var disposed = new UiCardLibrary(root, Child, destination);
        try
        {
            disposed.Start();
            Check("disposal_fixture_child_started", Until(() => ChildPid() is not null));
            var childPid = ChildPid() ?? -1;
            disposed.Dispose();
            Check("dispose_kills_descendant_and_prevents_restart", Until(() => !Alive(childPid))
                && (string?)disposed.Status()["state"] == "stopped" && disposed.Start() == "disposed");
        }
        finally { disposed.Dispose(); }

        Directory.CreateDirectory(Path.GetDirectoryName(historicalPath)!);
        var historical = Report("updated", updated: 4); historical.Remove("cards_complete"); historical.Remove("local_cards");
        historical["added"] = new JsonArray("a", "b", "c", "d"); historical["changed"] = new JsonArray("existing-card");
        historical["pending"] = new JsonArray("a", "b", "c", "d"); // Original plan retained by the old successful updater.
        File.WriteAllText(historicalPath, historical.ToJsonString());
        using (var service = new UiCardLibrary(root, Child, destination))
        {
            Check("legacy_report_summary_loaded_with_new_cards_only", (bool?)service.Status()["last"]?["cards_complete"] == true
                && (int?)service.Status()["last"]?["local_cards"] == 1471 && (int?)service.Status()["finished_seq"] == 0);
            scenario = "bad"; var previous = service.Status()["last"]?.ToJsonString(); service.Start(); var status = Wait(service);
            Check("failed_update_preserves_historical_local_library_summary", (string?)status["state"] == "failed"
                && status["last"]?.ToJsonString() == previous);
        }
        historical["status"] = "stopped"; historical["complete"] = false; historical["cards_complete"] = false;
        historical["remaining_cards"] = 3; historical["updated_cards"] = 2;
        historical["pending"] = new JsonArray("c", "d", "existing-card");
        File.WriteAllText(historicalPath, historical.ToJsonString());
        using (var service = new UiCardLibrary(root, Child, destination))
        {
            Check("legacy_partial_report_counts_only_enabled_added_cards", (int?)service.Status()["last"]?["local_cards"] == 1469
                && (bool?)service.Status()["last"]?["cards_complete"] == false);
        }
        File.WriteAllText(historicalPath, "corrupt");
        using (var service = new UiCardLibrary(root, Child, destination))
        { Check("invalid_historical_report_ignored", service.Status()["last"] is null); }

        var fakePython = Path.Combine(root, ".venv", "Scripts", "python.exe");
        Directory.CreateDirectory(Path.GetDirectoryName(fakePython)!); File.WriteAllText(fakePython, "never executed");
        using (var service = new UiCardLibrary(root, outputGameAssets: destination))
        {
            var command = service.DefaultCommand();
            Check("fixed_background_command", command.FileName == fakePython && command.WorkingDirectory == root
                && command.CreateNoWindow && !command.UseShellExecute && command.RedirectStandardOutput && command.RedirectStandardError
                && command.ArgumentList.SequenceEqual(new[] { "-X", "utf8", "tools/update_card_library.py", "--fetch", "--all", "--with-icons", "--progress-json" }));
            Check("first_install_timeout_ninety_minutes", UiCardLibrary.TimeoutMs == 90 * 60 * 1000);
        }
        scenario = "success"; WriteReport(Report());
        var blockedTarget = Path.Combine(root, "blocked-assets"); File.WriteAllText(blockedTarget, "file");
        using (var service = new UiCardLibrary(root, Child, blockedTarget))
        {
            service.Start(); var status = Wait(service);
            Check("publish_failure_reports_partial_actual_data", (string?)status["state"] == "partial"
                && (string?)status["error"] == "publish_failed" && (int?)status["last"]?["local_cards"] == 1471);
        }
        // 2026-11-23: icons already in the install folder (private resource pack) reach the served copy at start
        var served = Path.Combine(root, "served-at-start");
        using (var service = new UiCardLibrary(root, Child, served))
        {
            Check("start_publishes_existing_icons_once", File.Exists(Path.Combine(served, "manifest.json"))
                && File.Exists(Path.Combine(served, "cards", "a.json")) && !File.Exists(Path.Combine(served, "owned-levels.js")));
        }
        return new JsonObject { ["passed"] = true, ["real_game_contact"] = false, ["network_contact"] = false, ["checks"] = checks }.ToJsonString();
    }
}
