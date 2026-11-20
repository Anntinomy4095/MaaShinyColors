using System.Diagnostics;
using System.Drawing;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// Phase K of --self-test-ui: the 「扫描ノウハウ」 host path without a game. A fake scan checks
/// the router/protocol side (confirmed:true + mode enum only, refusals, status, mutual exclusion with the card scan); the real
/// supervisor runs a trivial PowerShell child in a throw-away repo folder to check the generated config per mode, progress
/// from the events file, outcomes read from the summary and the knowhow.js copy. Zero game input.
/// </summary>
internal static partial class UiSelfTest
{
    private sealed class FakeKnowhowScan : IUiKnowhowScan
    {
        internal readonly List<string> Calls = new();
        public bool Active { get; set; }
        public event Action<string, JsonObject>? Output;
        public JsonObject Status() => new() { ["state"] = Active ? "running" : "idle", ["blocker"] = null, ["finished_seq"] = 0 };
        public string? Start(string mode, int count = 0) { Calls.Add("start:" + mode + (count > 0 ? ":" + count : "")); Active = true; return null; }
        public bool RequestStop() { Calls.Add("stop"); var was = Active; Active = false; return was; }
        public void Tick() { _ = Output; }
    }

    private static void KnowhowScanPhase(Checks checks, string output)
    {
        const string P = "K";
        var protocol = UiProtocol.LoadEmbedded();
        var schema = UiTaskSchema.LoadFromUiFolder(UiPaths.OutputUiFolder());
        long now = 0;
        var seq = 0;
        const string Home = "https://maa-ui.test/index.html";
        string Req(string cmd, string paramsJson) => $"{{\"v\":1,\"id\":\"k-{++seq}\",\"cmd\":\"{cmd}\",\"params\":{paramsJson}}}";
        bool Ok(string? reply) => reply is not null && (bool)JsonNode.Parse(reply)!["ok"]! == true;
        bool Refused(string? reply) => reply is not null && (bool)JsonNode.Parse(reply)!["ok"]! == false;

        checks.Add(P, "knowhow_commands_whitelisted", new[] { "knowhow.scan.start", "knowhow.scan.stop", "knowhow.scan.status" }.All(protocol.Commands.Contains));
        var store = new UiProfileStore(Path.Combine(output, "profile-knowhowscan.json"), schema);
        var fake = new FakeKnowhowScan();
        var card = new FakeCardScan();
        var router = new UiMessageRouter(protocol, schema, store, () => now, hostStatus: () => new JsonObject { ["connected"] = true },
            cardScan: card, knowhowScan: fake);
        foreach (var (name, json) in new (string, string)[]
        {
            ("start_needs_confirmed", Req("knowhow.scan.start", "{\"mode\":\"calibrate\"}")),
            ("start_needs_mode", Req("knowhow.scan.start", "{\"confirmed\":true}")),
            ("start_rejects_unknown_mode", Req("knowhow.scan.start", "{\"confirmed\":true,\"mode\":\"sell\"}")),
            ("start_rejects_budget", Req("knowhow.scan.start", "{\"confirmed\":true,\"mode\":\"scan\",\"max_books\":600}")),
            ("start_rejects_path", Req("knowhow.scan.start", "{\"confirmed\":true,\"mode\":\"scan\",\"resume_record\":\"C:/x.json\"}")),
            ("start_newest_needs_count", Req("knowhow.scan.start", "{\"confirmed\":true,\"mode\":\"newest\"}")),
            ("start_newest_rejects_11", Req("knowhow.scan.start", "{\"confirmed\":true,\"mode\":\"newest\",\"count\":11}")),
            ("start_scan_rejects_count", Req("knowhow.scan.start", "{\"confirmed\":true,\"mode\":\"scan\",\"count\":3}")),
        })
        {
            now += 1000;
            checks.Add(P, name + "_refused", Refused(router.Handle(Home, json)));
        }
        checks.Add(P, "invalid_params_never_reach_scan", fake.Calls.Count == 0);
        card.Active = true;
        now += 1000;
        checks.Add(P, "card_scan_blocks_knowhow_scan", Refused(router.Handle(Home, Req("knowhow.scan.start", "{\"confirmed\":true,\"mode\":\"calibrate\"}"))) && fake.Calls.Count == 0);
        card.Active = false;
        now += 1000;
        checks.Add(P, "start_calibrate_accepted", Ok(router.Handle(Home, Req("knowhow.scan.start", "{\"confirmed\":true,\"mode\":\"calibrate\"}")))
            && fake.Calls.SequenceEqual(new[] { "start:calibrate" }));
        now += 1000;
        checks.Add(P, "knowhow_scan_blocks_run_start", Refused(router.Handle(Home,
            Req("run.start", "{\"mode\":\"read_only\",\"config\":{\"tasks\":[{\"name\":\"Training\",\"enabled\":false}]},\"spend_flags\":[]}"))));
        now += 1000;
        checks.Add(P, "status_readonly", Ok(router.Handle(Home, Req("knowhow.scan.status", "{}"))));
        fake.Active = false;
        now += 1000;
        checks.Add(P, "start_newest_accepted", Ok(router.Handle(Home, Req("knowhow.scan.start", "{\"confirmed\":true,\"mode\":\"newest\",\"count\":2}")))
            && fake.Calls.Last() == "start:newest:2");
        now += 1000;
        checks.Add(P, "stop_accepted", Ok(router.Handle(Home, Req("knowhow.scan.stop", "{}"))) && fake.Calls.Last() == "stop");
        fake.Active = true;
        card.Active = true;
        now += 1000;
        var globalStop = router.Handle(Home, Req("run.stop", "{\"kind\":\"emergency\"}"));
        checks.Add(P, "global_stop_reaches_both_scans", Ok(globalStop)
            && (bool?)JsonNode.Parse(globalStop!)!["result"]!["accepted"] == true
            && !fake.Active && !card.Active && fake.Calls.Last() == "stop" && card.Calls.Last() == "stop");
        now += 1000;
        var idleStop = router.Handle(Home, Req("run.stop", "{\"kind\":\"emergency\"}"));
        checks.Add(P, "global_stop_idle_not_accepted", Ok(idleStop)
            && (bool?)JsonNode.Parse(idleStop!)!["result"]!["accepted"] == false);
        fake.Active = true;
        checks.Add(P, "hotkey_entry_stops_scan_without_engine", router.RequestStop("emergency") && !fake.Active);
        now += 1000;
        var preview = new UiMessageRouter(protocol, schema, store, () => now);
        checks.Add(P, "refused_in_preview", Refused(preview.Handle(Home, Req("knowhow.scan.start", "{\"confirmed\":true,\"mode\":\"scan\"}"))));

        // ---- generated config ------------------------------------------------------------------------------------------
        var calib = UiKnowhowScanSupervisor.BuildConfig("calibrate");
        var scan = UiKnowhowScanSupervisor.BuildConfig("scan");
        checks.Add(P, "config_calibrate", (string?)calib["tasks"]![0]!["name"] == "KnowhowScan" && calib["preset"] is null
            && (string?)calib["tasks"]![0]!["option"]!["mode"] == "calibrate" && calib["max_clicks"] is null
            && calib["task_max_clicks"] is null && calib["allow_spend"]!.AsArray().Count == 0);
        checks.Add(P, "config_scan", (string?)scan["tasks"]![0]!["option"]!["mode"] == "scan" && scan["max_clicks"] is null
            && scan["tasks"]![0]!["option"]!["resume_record"] is null
            && scan["allow_spend"]!.AsArray().Count == 0);
        var continued = UiKnowhowScanSupervisor.BuildConfig("scan", "current-scan", resume: true);
        checks.Add(P, "config_continuation_has_current_scan_id", (string?)continued["tasks"]![0]!["option"]!["scan_id"] == "current-scan"
            && (string?)continued["tasks"]![0]!["option"]!["resume_record"] == "latest");
        var newest = UiKnowhowScanSupervisor.BuildConfig("newest", count: 3);
        checks.Add(P, "config_newest", (string?)newest["tasks"]![0]!["option"]!["mode"] == "scan"
            && (string?)newest["tasks"]![0]!["option"]!["newest"] == "auto" && (bool)newest["tasks"]![0]!["option"]!["details"]!
            && newest["max_clicks"] is null && newest["allow_spend"]!.AsArray().Count == 0);
        bool threw;
        try { UiKnowhowScanSupervisor.BuildConfig("sell"); threw = false; } catch (ArgumentException) { threw = true; }
        checks.Add(P, "config_rejects_unknown_mode", threw);
        try { UiKnowhowScanSupervisor.BuildConfig("newest", count: 0); threw = false; } catch (ArgumentException) { threw = true; }
        checks.Add(P, "config_newest_needs_count", threw);

        // ---- readiness and gate ----------------------------------------------------------------------------------------
        var rect = new Rectangle(1, 1, 10, 10);
        var on = new[] { new PipelineAction("knowhow-a", "knowhow-scan", false, rect, null, true, new[] { "SC.KnowhowScan.X" }) };
        var off = new[] { new PipelineAction("knowhow-a", "knowhow-scan", false, rect, null, false, new[] { "SC.KnowhowScan.X" }) };
        const string Bound = "{\"SC.KnowhowScan.Entry\":{\"recognition\":\"DirectHit\"}}";
        const string Unbound = "{\"SC.KnowhowScan.Entry\":{\"enabled\":false}}";
        checks.Add(P, "readiness",
            UiPolicySummary.KnowhowScanReadiness(on, Bound) == (true, "ready")
            && UiPolicySummary.KnowhowScanReadiness(off, Bound).Reason == "knowhow_scan_actions_disabled:knowhow-a"
            && UiPolicySummary.KnowhowScanReadiness(Array.Empty<PipelineAction>(), Bound).Reason == "knowhow_scan_actions_missing"
            && UiPolicySummary.KnowhowScanReadiness(on, Unbound).Reason == "knowhow_scan_entry_disabled");
        JsonObject Host(bool pipeline = true, int used = 0) => new()
        { ["pipeline_input_enabled"] = pipeline, ["connected"] = true, ["host_guard_reason"] = null, ["clicks_this_run"] = used, ["click_cap"] = 600 };
        JsonObject Policy(bool ready, string reason = "ready") => new() { ["knowhow_scan_ready"] = ready, ["knowhow_scan_reason"] = reason };
        checks.Add(P, "gate",
            UiKnowhowScanGate.Blocker(Host(), Policy(true), false, false, false) is null
            && UiKnowhowScanGate.Blocker(Host(pipeline: false), Policy(true), false, false, false) == "pipeline_input_disabled"
            && UiKnowhowScanGate.Blocker(Host(), Policy(false, "knowhow_scan_actions_missing"), false, false, false) == "knowhow_scan_actions_missing"
            && UiKnowhowScanGate.Blocker(Host(), Policy(false, "<b>"), false, false, false) == "knowhow_scan_not_ready"
            && UiKnowhowScanGate.Blocker(Host(), Policy(true), true, false, false) == "engine_active"
            && UiKnowhowScanGate.Blocker(Host(), Policy(true), false, true, false) == "card_scan_active"
            && UiKnowhowScanGate.Blocker(Host(used: 410), Policy(true), false, false, false) is null
            && UiKnowhowScanGate.Blocker(Host(), Policy(true), false, false, true) == "other_input_activity");

        // ---- real supervisor with a trivial child (zero game input) ----------------------------------------------------
        var fakeRepo = Path.Combine(output, "knowhowscan-repo");
        var runs = Path.Combine(fakeRepo, "dev_assets", "private", "knowhow-scan-ui");
        var framesRoot = Path.Combine(fakeRepo, "dev_assets", "private", "knowhow-scan");
        var sourceAssets = Path.Combine(fakeRepo, "ui", "game-assets");
        var outAssets = Path.Combine(output, "knowhowscan-output-assets");
        Directory.CreateDirectory(sourceAssets);
        Directory.CreateDirectory(framesRoot);
        var session = Path.Combine(output, "knowhowscan-session.json");
        File.WriteAllText(session, "{}");
        var scenario = "calibrated";
        JsonNode? seenConfig = null;

        ProcessStartInfo Child(CardScanLaunch launch)
        {
            seenConfig = JsonNode.Parse(File.ReadAllText(launch.ConfigPath));
            File.WriteAllLines(launch.EventsPath, new[]
            {
                "{\"event\":\"node\",\"status\":\"succeeded\",\"node\":\"SC.KnowhowScan.IdolList.OpenKnowhow\"}",
                "{\"event\":\"node\",\"status\":\"succeeded\",\"node\":\"SC.KnowhowScan.Grid.OpenTile.R0C0\"}",
                "{\"event\":\"node\",\"status\":\"succeeded\",\"node\":\"SC.KnowhowScan.Detail.ScrollDown\"}",
                "{\"event\":\"node\",\"status\":\"succeeded\",\"node\":\"SC.KnowhowScan.Grid.ScrollDown\"}",
            });
            var frames = Path.Combine(framesRoot, (scenario == "calibrated" ? "calibrate" : "scan") + "-20261012T000000Z");
            Directory.CreateDirectory(frames);
            foreach (var n in new[] { "0001", "0002" }) { File.WriteAllBytes(Path.Combine(frames, $"frame-{n}.png"), new byte[] { 1 }); }
            var report = scenario == "calibrated"
                ? new JsonObject { ["kind"] = "knowhow_scan", ["outcome"] = "calibrated", ["mode"] = "calibrate", ["scan_ready"] = true,
                    ["grid_step_px"] = new JsonObject { ["median"] = 158 }, ["grid_step_rows"] = 1.003,
                    ["detail_step_px"] = new JsonObject { ["median"] = 201 }, ["detail_min_row_overlap"] = 1,
                    ["grid_bottom"] = new JsonObject { ["reached"] = true }, ["frames_dir"] = "C:/secret/path" }
                : new JsonObject { ["kind"] = "knowhow_scan", ["outcome"] = "partial:book_budget_exhausted", ["mode"] = "scan", ["books"] = 15,
                    ["grid_count"] = 120, ["grid_complete"] = false, ["sort_restore_needed"] = true,
                    ["review"] = new JsonObject { ["needs_review"] = 2, ["auto_accepted"] = 13 } };
            if (scenario == "scan") { File.WriteAllText(Path.Combine(sourceAssets, "knowhow.js"), "export default {};\n"); }
            File.WriteAllText(launch.SummaryPath, new JsonObject { ["tasks"] = new JsonArray(new JsonObject { ["name"] = "KnowhowScan", ["reports"] = new JsonArray(report) }) }.ToJsonString());
            var info = UiEngineSupervisor.NewStartInfo(fakeRepo, "powershell.exe");
            foreach (var argument in new[] { "-NoProfile", "-NonInteractive", "-Command", "exit 0" }) { info.ArgumentList.Add(argument); }
            return info;
        }

        JsonObject RunOnce(string mode)
        {
            var supervisor = new UiKnowhowScanSupervisor(fakeRepo, () => null, () => session, Child, runsRoot: runs, framesRoot: framesRoot, outputGameAssets: outAssets);
            if (supervisor.Start(mode) is not null) { return new JsonObject { ["state"] = "refused" }; }
            var watch = Stopwatch.StartNew();
            while (supervisor.Active && watch.ElapsedMilliseconds < 20_000) { supervisor.Tick(); Thread.Sleep(50); }
            return supervisor.Status();
        }

        var calibrated = RunOnce("calibrate");
        checks.Add(P, "supervisor_calibrate_outcome", (string?)calibrated["outcome"] == "calibrated" && (bool?)calibrated["scan_ready"] == true
            && (double?)calibrated["grid_step_px"] == 158 && (double?)calibrated["detail_step_px"] == 201
            && (string?)calibrated["frames_folder"] == "calibrate-20261012T000000Z" && (bool?)calibrated["imported"] == false
            && !calibrated.ToJsonString().Contains("secret", StringComparison.Ordinal)
            && (string?)seenConfig?["tasks"]?[0]?["option"]?["mode"] == "calibrate");
        scenario = "scan";
        var scanned = RunOnce("scan");
        checks.Add(P, "supervisor_scan_partial_imports_flagged_module", (string?)scanned["outcome"] == "partial"
            && (string?)scanned["reason"] == "book_budget_exhausted" && (bool?)scanned["imported"] == true
            && (bool?)scanned["sort_restore_needed"] == true
            && (double?)scanned["needs_review"] == 2 && File.Exists(Path.Combine(outAssets, "knowhow.js")));
        var run = new UiKnowhowScanSupervisor.Run(new CardScanLaunch("i", "f", "c", "e", "s", "m", "x"), "scan", 0, DateTime.UtcNow);
        foreach (var line in new[]
        {
            "{\"event\":\"node\",\"status\":\"succeeded\",\"node\":\"SC.KnowhowScan.Grid.OpenTile.R1C2\"}",
            "{\"event\":\"node\",\"status\":\"succeeded\",\"node\":\"SC.KnowhowScan.Grid.OpenSlot.Y1C0\"}",
            "{\"event\":\"node\",\"status\":\"starting\",\"node\":\"SC.KnowhowScan.Grid.ScrollDown\"}",
            "{\"event\":\"node\",\"status\":\"succeeded\",\"node\":\"SC.KnowhowScan.Grid.ScrollUp\"}",
            "not json",
        }) { UiKnowhowScanSupervisor.ApplyEvent(run, line); }
        checks.Add(P, "events_progress", run.Books == 2 && run.Scrolls == 1 && !run.Entered);

        // A new user scan is fresh. Only a non-interrupted automatic segment can reuse its private record.
        foreach (var changed in new[] { "none", "manual_click_seq", "run_window", "clicks_this_run" })
        {
            long clock = 0;
            var host = new JsonObject { ["manual_click_seq"] = 0L, ["run_window"] = 1L, ["clicks_this_run"] = 580L };
            var configs = new List<JsonNode>();
            ProcessStartInfo SegmentChild(CardScanLaunch launch)
            {
                var command = Child(launch);
                configs.Add(JsonNode.Parse(File.ReadAllText(launch.ConfigPath))!);
                return command;
            }
            using var supervisor = new UiKnowhowScanSupervisor(fakeRepo, () => null, () => session, SegmentChild,
                clockMs: () => clock, runsRoot: Path.Combine(runs, "continuation-" + changed), framesRoot: framesRoot,
                outputGameAssets: outAssets, hostStatus: () => host);
            var started = supervisor.Start("scan") is null;
            var watch = Stopwatch.StartNew();
            while ((int?)supervisor.Status()["finished_seq"] == 0 && watch.ElapsedMilliseconds < 10_000)
            { supervisor.Tick(); Thread.Sleep(20); }
            var firstId = (string?)configs.FirstOrDefault()?["tasks"]?[0]?["option"]?["scan_id"];
            checks.Add(P, "fresh_scan_ignores_old_record_" + changed, started && !string.IsNullOrEmpty(firstId)
                && configs[0]["tasks"]![0]!["option"]!["resume_record"] is null && supervisor.Active);
            if (changed != "none") { host[changed] = 999L; }
            clock = 31_001;
            supervisor.Tick();
            var option = configs.LastOrDefault()?["tasks"]?[0]?["option"];
            checks.Add(P, "continuation_activity_" + changed, configs.Count == 2 && (changed == "none"
                ? (string?)option?["scan_id"] == firstId && (string?)option?["resume_record"] == "latest"
                : (string?)option?["scan_id"] != firstId && option?["resume_record"] is null));
            supervisor.RequestStop();
        }

        // Even a complete summary cannot override manual game input observed before Finalize/PublishImport.
        var manualHost = new JsonObject { ["manual_click_seq"] = 0L, ["run_window"] = 1L, ["clicks_this_run"] = 580L };
        ProcessStartInfo CompleteChild(CardScanLaunch launch)
        {
            var command = Child(launch);
            File.WriteAllText(launch.SummaryPath, """{"tasks":[{"name":"KnowhowScan","reports":[{"kind":"knowhow_scan","mode":"scan","outcome":"scanned"}]}]}""");
            return command;
        }
        using (var supervisor = new UiKnowhowScanSupervisor(fakeRepo, () => null, () => session, CompleteChild,
            runsRoot: Path.Combine(runs, "manual-before-finalize"), framesRoot: framesRoot, outputGameAssets: outAssets, hostStatus: () => manualHost))
        {
            supervisor.Start("scan");
            manualHost["manual_click_seq"] = 1L;
            var watch = Stopwatch.StartNew();
            while (supervisor.Active && watch.ElapsedMilliseconds < 10_000) { supervisor.Tick(); Thread.Sleep(20); }
            var status = supervisor.Status();
            checks.Add(P, "manual_input_blocks_complete_import", (string?)status["outcome"] == "stopped"
                && (string?)status["reason"] == "manual_game_input" && (bool?)status["imported"] == false && !supervisor.Active);
        }
    }
}
