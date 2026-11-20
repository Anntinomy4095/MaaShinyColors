using System.Diagnostics;
using System.Text;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// Phase C of --self-test-ui: the 「扫描持有卡」 host path without a game. A fake scan object checks the
/// router/protocol side (whitelist, confirmed:true, refusals, status, mutual exclusion); the real supervisor runs a trivial
/// PowerShell child in a throw-away repo folder to check the generated config, the fixed argument list (never spend / replay
/// flags), progress from the events file, outcomes read from the summary, stop, kill and the import-file copy. Zero game input.
/// </summary>
internal static partial class UiSelfTest
{
    private sealed class FakeCardScan : IUiCardScan
    {
        internal readonly List<string> Calls = new();
        internal string? Refusal;
        public bool Active { get; set; }
        public event Action<string, JsonObject>? Output;
        public JsonObject Status() => new() { ["state"] = Active ? "running" : "idle", ["blocker"] = null, ["finished_seq"] = 0 };
        public string? Start() { Calls.Add("start"); if (Refusal is null) { Active = true; } return Refusal; }
        public bool RequestStop() { Calls.Add("stop"); var was = Active; Active = false; return was; }
        public void Tick() { Calls.Add("tick"); _ = Output; }
    }

    private static void CardScanPhase(Checks checks, string output)
    {
        const string P = "C";
        var protocol = UiProtocol.LoadEmbedded();
        var schema = UiTaskSchema.LoadFromUiFolder(UiPaths.OutputUiFolder());
        long now = 0;
        var seq = 0;
        const string Home = "https://maa-ui.test/index.html";
        string Req(string cmd, string paramsJson) => $"{{\"v\":1,\"id\":\"c-{++seq}\",\"cmd\":\"{cmd}\",\"params\":{paramsJson}}}";
        bool Ok(string? reply) => reply is not null && (bool)JsonNode.Parse(reply)!["ok"]! == true;
        bool Refused(string? reply) => reply is not null && (bool)JsonNode.Parse(reply)!["ok"]! == false;
        string ErrorOf(string? reply) => reply is null ? "" : (string?)JsonNode.Parse(reply)!["error"] ?? "";

        // ---- protocol whitelist -------------------------------------------------------------------------------
        checks.Add(P, "scan_commands_whitelisted", new[] { "cards.scan.start", "cards.scan.stop", "cards.scan.status" }.All(protocol.Commands.Contains));

        // ---- router with a fake scan -------------------------------------------------------------------------
        var store = new UiProfileStore(Path.Combine(output, "profile-cardscan.json"), schema);
        var fake = new FakeCardScan();
        var router = new UiMessageRouter(protocol, schema, store, () => now, hostStatus: () => new JsonObject { ["connected"] = true }, cardScan: fake);
        var events = new List<string>();
        router.EventReady += events.Add;
        void Tick() => now += 1000;
        foreach (var (name, json) in new (string, string)[]
        {
            ("scan_start_needs_confirmed", Req("cards.scan.start", "{}")),
            ("scan_start_confirmed_false", Req("cards.scan.start", "{\"confirmed\":false}")),
            ("scan_start_confirmed_string", Req("cards.scan.start", "{\"confirmed\":\"true\"}")),
            ("scan_start_rejects_path", Req("cards.scan.start", "{\"confirmed\":true,\"config\":\"C:/x.json\"}")),
            ("scan_start_rejects_budget", Req("cards.scan.start", "{\"confirmed\":true,\"max_scrolls_per_tab\":500}")),
            ("scan_stop_rejects_fields", Req("cards.scan.stop", "{\"force\":true}")),
        })
        {
            Tick();
            checks.Add(P, name + "_refused", Refused(router.Handle(Home, json)));
        }
        checks.Add(P, "scan_invalid_params_never_reach_scan", fake.Calls.Count == 0);
        Tick();
        checks.Add(P, "scan_start_accepted", Ok(router.Handle(Home, Req("cards.scan.start", "{\"confirmed\":true}"))) && fake.Calls.SequenceEqual(new[] { "start" }));
        Tick();
        var second = router.Handle(Home, Req("run.start", "{\"mode\":\"read_only\",\"config\":{\"tasks\":[{\"name\":\"Training\",\"enabled\":false}]},\"spend_flags\":[]}"));
        checks.Add(P, "scan_blocks_run_start", Refused(second));
        Tick();
        checks.Add(P, "scan_status_readonly", Ok(router.Handle(Home, Req("cards.scan.status", "{}"))));
        Tick();
        checks.Add(P, "scan_stop_accepted", Ok(router.Handle(Home, Req("cards.scan.stop", "{}"))) && fake.Calls.Last() == "stop");
        fake.Refusal = "engine_active";
        Tick();
        var refused = router.Handle(Home, Req("cards.scan.start", "{\"confirmed\":true}"));
        checks.Add(P, "scan_refusal_plain_text", Refused(refused) && ErrorOf(refused).Contains("任务正在运行", StringComparison.Ordinal));
        Tick();
        var gameRouter = new UiMessageRouter(protocol, schema, store, () => now, hostStatus: () => new JsonObject { ["connected"] = true, ["pipeline_input_enabled"] = true }, cardScan: fake);
        var gameEvents = new List<string>();
        gameRouter.EventReady += gameEvents.Add;
        Tick();
        gameRouter.Handle(Home, Req("status.subscribe", "{\"enabled\":true}"));
        checks.Add(P, "scan_status_update_carries_card_scan", gameEvents.Any(e => e.Contains("\"card_scan\"", StringComparison.Ordinal) && !e.Contains("\"click_cap\"", StringComparison.Ordinal)));
        Tick();
        var noScan = new UiMessageRouter(protocol, schema, store, () => now, hostStatus: () => new JsonObject { ["connected"] = true });
        var none = noScan.Handle(Home, Req("cards.scan.start", "{\"confirmed\":true}"));
        checks.Add(P, "scan_refused_without_host_scan", Refused(none) && ErrorOf(none).Contains("扫描持有卡", StringComparison.Ordinal));
        Tick();
        var preview = new UiMessageRouter(protocol, schema, store, () => now);
        checks.Add(P, "scan_refused_in_preview", Refused(preview.Handle(Home, Req("cards.scan.start", "{\"confirmed\":true}"))));

        // ---- generated config / fixed command line ---------------------------------------------------------------
        var config = UiCardScanSupervisor.BuildConfig();
        var task = config["tasks"]![0]!;
        var option = task["option"]!;
        checks.Add(P, "scan_config_both_tabs_full_budget",
            (string?)task["name"] == "CardScan" && config["tasks"]!.AsArray().Count == 1 && config["preset"] is null
            && option["tabs"]!.AsArray().Select(n => (string?)n).SequenceEqual(new[] { "support", "produce" })
            && (int)option["max_scrolls_per_tab"]! == 80 && (int)option["max_frames"]! == 400 && (int)option["max_holds"]! == 10);
        checks.Add(P, "scan_config_no_caps_and_no_spend",
            config["max_clicks"] is null && config["task_max_clicks"] is null
            && (int)config["session_max_minutes"]! == 50 && config["allow_spend"]!.AsArray().Count == 0);
        var launch = new CardScanLaunch("id", "F", "F\\config.json", "F\\events.jsonl", "F\\stop.flag", "F\\summary.json", "S\\session.json");
        var args = UiCardScanSupervisor.RunArguments("R", launch);
        checks.Add(P, "scan_command_is_supervised_live_task_only",
            args.Contains("--live") && args.Contains("--supervised-task") && args.Contains("--config") && args.Contains("--stop-file")
            && args.Contains("--summary") && args.Contains("--events") && args.Contains("--session")
            && !args.Any(a => a.StartsWith("--allow", StringComparison.Ordinal) || a == "--simulate-input" || a == "--offline-frames" || a == "--supervised-startup"
                || a == "--preset" || a == "--tasks"));

        // ---- readiness gate ------------------------------------------------------------------------------------------
        JsonObject Host(bool pipeline = true, bool connected = true, string? guard = null, int used = 0, int cap = 600) => new()
        { ["pipeline_input_enabled"] = pipeline, ["connected"] = connected, ["host_guard_reason"] = guard, ["clicks_this_run"] = used, ["click_cap"] = cap };
        JsonObject Policy(bool ready, string reason = "ready") => new() { ["card_scan_ready"] = ready, ["card_scan_reason"] = reason };
        checks.Add(P, "gate_ready", UiCardScanGate.Blocker(Host(), Policy(true), false, false) is null);
        checks.Add(P, "gate_needs_pipeline_input", UiCardScanGate.Blocker(Host(pipeline: false), Policy(true), false, false) == "pipeline_input_disabled");
        checks.Add(P, "gate_needs_ready_signal", UiCardScanGate.Blocker(Host(), Policy(false, "card_scan_actions_disabled:a,b"), false, false) == "card_scan_actions_disabled:a,b");
        checks.Add(P, "gate_hostile_reason_sanitised", UiCardScanGate.Blocker(Host(), Policy(false, "<img src=x>"), false, false) == "card_scan_not_ready");
        checks.Add(P, "gate_missing_policy", UiCardScanGate.Blocker(Host(), new JsonObject(), false, false) == "card_scan_not_ready");
        checks.Add(P, "gate_not_connected", UiCardScanGate.Blocker(Host(connected: false), Policy(true), false, false) == "host_not_connected");
        checks.Add(P, "gate_host_guard", UiCardScanGate.Blocker(Host(guard: "desktop_locked"), Policy(true), false, false) == "host_guard_active");
        checks.Add(P, "gate_engine_active", UiCardScanGate.Blocker(Host(), Policy(true), true, false) == "engine_active");
        // 2026-10-28: the clicks already used in the host's window never block a start (no input-budget check).
        var busy = Host(used: 500); busy["run_begin_ready"] = false;
        checks.Add(P, "gate_no_input_budget_check", UiCardScanGate.Blocker(Host(used: 500), Policy(true), false, false) is null
            && UiCardScanGate.Blocker(busy, Policy(true), false, false) is null);
        checks.Add(P, "gate_external_input", UiCardScanGate.Blocker(Host(), Policy(true), false, true) == "other_input_activity");

        // ---- real supervisor with a trivial child (zero game input) ----------------------------------------------------
        var fakeRepo = Path.Combine(output, "cardscan-repo");
        var runs = Path.Combine(fakeRepo, "dev_assets", "private", "card-scan-ui");
        var framesRoot = Path.Combine(fakeRepo, "dev_assets", "private", "card-scan");
        var sourceAssets = Path.Combine(fakeRepo, "ui", "game-assets");
        var outAssets = Path.Combine(output, "cardscan-output-assets");
        Directory.CreateDirectory(sourceAssets);
        Directory.CreateDirectory(framesRoot);
        var session = Path.Combine(output, "cardscan-session.json");
        File.WriteAllText(session, "{}");
        string? blocker = null;
        string? sessionPath = session;
        var scenario = "scanned";
        CardScanLaunch? seen = null;
        var logs = new List<string>();

        ProcessStartInfo Child(CardScanLaunch launch)
        {
            seen = launch;
            string powershell = "exit 0";
            switch (scenario)
            {
                case "scanned":
                    File.WriteAllText(Path.Combine(sourceAssets, "owned-levels.js"), "export default {};\n");
                    File.WriteAllText(Path.Combine(sourceAssets, "inventory-scan.js"), "export default {};\n");
                    File.WriteAllText(launch.SummaryPath, Summary("scanned", written: true, cards: 233));
                    break;
                case "scanned_no_files":
                    File.WriteAllText(launch.SummaryPath, Summary("scanned", written: false, cards: 233));
                    break;
                case "partial":
                    File.WriteAllText(launch.SummaryPath, Summary("partial:scroll_budget_exhausted", written: false, cards: 212));
                    break;
                case "failed":
                    powershell = "exit 3";
                    break;
                case "events":
                    File.WriteAllLines(launch.EventsPath, new[]
                    {
                        "{\"event\":\"node\",\"status\":\"succeeded\",\"node\":\"SC.CardScan.List.TapSupport\"}",
                        "{\"event\":\"node\",\"status\":\"succeeded\",\"node\":\"SC.CardScan.List.ScrollDown\"}",
                        "{\"event\":\"node\",\"status\":\"starting\",\"node\":\"SC.CardScan.List.ScrollDown\"}",
                        "{\"event\":\"node\",\"status\":\"succeeded\",\"node\":\"SC.CardScan.List.ScrollDown\"}",
                        "{\"event\":\"node\",\"status\":\"succeeded\",\"node\":\"SC.CardScan.List.TapProduce\"}",
                        "not json",
                    });
                    var frames = Path.Combine(framesRoot, "card-scan-20261009T000000Z");
                    Directory.CreateDirectory(frames);
                    foreach (var n in new[] { "001", "002", "003" }) { File.WriteAllBytes(Path.Combine(frames, $"frame-{n}.png"), new byte[] { 1 }); }
                    powershell = $"while (-not (Test-Path -LiteralPath '{launch.StopPath}')) {{ Start-Sleep -Milliseconds 100 }}; exit 0";
                    break;
                case "stop":
                    powershell = $"while (-not (Test-Path -LiteralPath '{launch.StopPath}')) {{ Start-Sleep -Milliseconds 100 }}; exit 0";
                    break;
                case "ignore_stop":
                    powershell = "Start-Sleep -Seconds 60";
                    break;
            }
            var info = UiEngineSupervisor.NewStartInfo(fakeRepo, "powershell.exe");
            foreach (var argument in new[] { "-NoProfile", "-NonInteractive", "-Command", powershell }) { info.ArgumentList.Add(argument); }
            return info;
        }

        string Summary(string outcome, bool written, int cards) => new JsonObject
        {
            ["tasks"] = new JsonArray(new JsonObject
            {
                ["name"] = "CardScan",
                ["reports"] = new JsonArray(new JsonObject
                {
                    ["kind"] = "card_scan", ["outcome"] = outcome, ["cards"] = cards,
                    ["written"] = written ? new JsonArray("ui/game-assets/owned-levels.js", "ui/game-assets/inventory-scan.js") : new JsonArray(),
                    ["tabs"] = new JsonArray(
                        new JsonObject { ["tab"] = "support", ["complete"] = outcome == "scanned", ["scrolls"] = 30, ["slots_accepted"] = 100, ["end_reason"] = null },
                        new JsonObject { ["tab"] = "produce", ["complete"] = outcome == "scanned", ["scrolls"] = 0, ["slots_accepted"] = 0, ["end_reason"] = null }),
                }),
            }),
        }.ToJsonString();

        UiCardScanSupervisor NewSupervisor(CardScanLimits? limits = null)
        {
            var supervisor = new UiCardScanSupervisor(fakeRepo, () => blocker, () => sessionPath, Child, limits: limits,
                runsRoot: runs, framesRoot: framesRoot, outputGameAssets: outAssets);
            supervisor.Output += (_, data) => logs.Add((string)data["text"]!);
            return supervisor;
        }

        bool Pump(UiCardScanSupervisor supervisor, Func<bool> done, int timeoutMs = 20_000)
        {
            var watch = Stopwatch.StartNew();
            while (watch.ElapsedMilliseconds < timeoutMs)
            {
                supervisor.Tick();
                if (done()) { return true; }
                Thread.Sleep(50);
            }
            return false;
        }

        bool RunToEnd(UiCardScanSupervisor supervisor) => Pump(supervisor, () => !supervisor.Active);

        {
            var supervisor = NewSupervisor();
            blocker = "engine_active";
            checks.Add(P, "scan_start_refused_on_blocker", supervisor.Start() == "engine_active" && !supervisor.Active && seen is null);
            blocker = null;
            sessionPath = null;
            checks.Add(P, "scan_start_refused_without_session", supervisor.Start() == "session_unavailable" && !supervisor.Active);
            sessionPath = session;

            scenario = "scanned";
            var started = supervisor.Start();
            checks.Add(P, "scan_start_launches_once", started is null && supervisor.Active && supervisor.Start() == "already_running");
            checks.Add(P, "scan_run_folder_private_and_config_written", seen is not null
                && Path.GetFullPath(seen.Folder).StartsWith(Path.GetFullPath(runs), StringComparison.OrdinalIgnoreCase)
                && JsonNode.DeepEquals(JsonNode.Parse(File.ReadAllText(seen.ConfigPath)), JsonNode.Parse(UiCardScanSupervisor.BuildConfig().ToJsonString())));
            var finished = RunToEnd(supervisor);
            var status = supervisor.Status();
            checks.Add(P, "scan_complete_imported", finished && (string?)status["state"] == "finished" && (string?)status["outcome"] == "scanned"
                && status["imported"]!.GetValue<bool>() && (int)status["cards"]! == 233 && (int)status["finished_seq"]! == 1
                && File.Exists(Path.Combine(outAssets, "owned-levels.js")) && File.Exists(Path.Combine(outAssets, "inventory-scan.js")), status);
            checks.Add(P, "scan_status_has_no_paths", !status.ToJsonString().Contains(":\\", StringComparison.Ordinal) && !status.ToJsonString().Contains("/dev_assets", StringComparison.Ordinal));
            checks.Add(P, "scan_logs_plain_chinese", logs.Count >= 2 && logs.Any(l => l.Contains("只读", StringComparison.Ordinal)));

            scenario = "scanned_no_files";
            Directory.Delete(outAssets, true);
            supervisor.Start();
            RunToEnd(supervisor);
            status = supervisor.Status();
            checks.Add(P, "scan_complete_without_files_not_imported", (string?)status["outcome"] == "scanned" && !status["imported"]!.GetValue<bool>()
                && (string?)status["reason"] == "import_files_missing" && !File.Exists(Path.Combine(outAssets, "owned-levels.js")), status);

            scenario = "partial";
            supervisor.Start();
            RunToEnd(supervisor);
            status = supervisor.Status();
            checks.Add(P, "scan_partial_reason_and_no_import", (string?)status["outcome"] == "partial" && (string?)status["reason"] == "scroll_budget_exhausted"
                && !status["imported"]!.GetValue<bool>() && status["tabs"]!.AsArray().Count == 2 && (int)status["finished_seq"]! == 3, status);

            scenario = "failed";
            supervisor.Start();
            RunToEnd(supervisor);
            status = supervisor.Status();
            checks.Add(P, "scan_exit_without_summary_is_failed", (string?)status["outcome"] == "failed" && (string?)status["reason"] == "engine_exit_3" && !status["imported"]!.GetValue<bool>(), status);

            scenario = "events";
            supervisor.Start();
            var progressed = Pump(supervisor, () => (int)supervisor.Status()["frames"]! == 3 && (string?)supervisor.Status()["tab"] == "produce");
            status = supervisor.Status();
            checks.Add(P, "scan_progress_from_events_and_frames", progressed && (int)status["scrolls"]! == 2 && (string?)status["state"] == "running", status);
            checks.Add(P, "scan_stop_accepted_once_running", supervisor.RequestStop() && File.Exists(seen!.StopPath));
            var stopping = (string?)supervisor.Status()["state"];
            RunToEnd(supervisor);
            status = supervisor.Status();
            checks.Add(P, "scan_stop_ends_as_stopped", stopping == "stopping" && (string?)status["outcome"] == "stopped" && (string?)status["reason"] == "user_stop"
                && !status["imported"]!.GetValue<bool>(), status);
            checks.Add(P, "scan_stop_without_run_not_accepted", !supervisor.RequestStop());
            supervisor.Dispose();
        }
        {
            // A child that ignores 停止 is killed after the grace and shown as uncertain; Dispose never leaves it running.
            var supervisor = NewSupervisor(new CardScanLimits(CooperativeGraceMs: 500, EmergencyWaitMs: 2_000));
            scenario = "ignore_stop";
            var ok = supervisor.Start() is null;
            Thread.Sleep(300);
            supervisor.RequestStop();
            var ended = RunToEnd(supervisor);
            var status = supervisor.Status();
            checks.Add(P, "scan_kill_after_grace_is_uncertain", ok && ended && (string?)status["outcome"] == "uncertain" && (string?)status["reason"] == "killed_after_stop"
                && !status["imported"]!.GetValue<bool>(), status);
            supervisor.Dispose();
        }
        {
            var supervisor = NewSupervisor(new CardScanLimits(EmergencyWaitMs: 2_000));
            scenario = "ignore_stop";
            supervisor.Start();
            var process = seen;
            supervisor.Dispose();
            checks.Add(P, "scan_dispose_ends_child", !supervisor.Active && process is not null);
        }
    }
}
