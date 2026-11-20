using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// --self-test-ui: offline checks for the isolated UI panel (U1). No network: the
/// only remote name used is https://blocked.invalid/ (reserved, never resolves) and every such request must be refused
/// by the host before it leaves the WebView. Never creates a game WebView, never touches the capture bridge or input.
/// Phases: R = router/profile/spend rules without a WebView; A = the real UI page (light and dark, screenshots);
/// B = an adversarial probe page served as index.html from a private staging folder, plus a wrong-origin page.
/// </summary>
internal static partial class UiSelfTest
{
    /// <summary>Records developer action-table calls only.</summary>
    private sealed class FakeDevTools : IUiDevTools
    {
        internal readonly List<string> Calls = new();
        internal string? Refusal;
        public JsonObject Status() => new() { ["action_table"] = new JsonObject() };
        public string? ActionTable(string op) { Calls.Add($"table:{op}"); return Refusal; }
    }

    [DllImport("user32.dll")] private static extern bool PrintWindow(IntPtr window, IntPtr hdc, uint flags);
    private const uint PrintWindowRenderFullContent = 2;
    private const string ExpectedCsp = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
    private static readonly string[] HandshakeCommands = { "ui.ready", "schema.get", "profile.get", "status.subscribe", "log.subscribe" };

    private sealed class Checks
    {
        private readonly List<JsonObject> _items = new();
        internal void Add(string phase, string name, bool pass, object? detail = null)
        {
            _items.Add(new JsonObject
            {
                ["phase"] = phase, ["check"] = name, ["pass"] = pass,
                ["detail"] = detail is null ? null : JsonNode.Parse(JsonSerializer.Serialize(detail)),
            });
        }
        internal int Passed => _items.Count(item => (bool)item["pass"]!);
        internal int Total => _items.Count;
        internal JsonArray ToJson() => new(_items.Select(item => (JsonNode)item.DeepClone()).ToArray());
        internal IEnumerable<string> Failures => _items.Where(item => !(bool)item["pass"]!).Select(item => $"{item["phase"]}/{item["check"]}");
    }

    internal static void Run()
    {
        var root = UiPaths.RepoRoot();
        var stamp = DateTime.Now.ToString("yyyyMMdd-HHmmss", System.Globalization.CultureInfo.InvariantCulture);
        var output = Path.Combine(root, "dev_assets", "private", "ui-selftest", stamp);
        Directory.CreateDirectory(output);
        var checks = new Checks();
        var report = new JsonObject { ["self_test"] = "ui", ["real_game_contact"] = false, ["network"] = "blocked.invalid only (refused)", ["output"] = output };
        var browserProcesses = new List<uint>();
        try
        {
            RouterPhase(checks, output);
            EnginePhase(checks, output);
            CardScanPhase(checks, output);
            KnowhowScanPhase(checks, output);
            StoragePhase(checks, output);
            SupportPhase(checks, output);
            AssetSyncPhase(checks, output);
            StaleSessionPhase(checks, output);
            var gameData = JsonNode.Parse(Task.Run(() => UiGameDataSelfTest.Run(output)).GetAwaiter().GetResult())!;
            foreach (var item in gameData["checks"]!.AsArray())
            { checks.Add("GD", item!["check"]!.GetValue<string>(), item["pass"]!.GetValue<bool>()); }
            var cardLibrary = JsonNode.Parse(Task.Run(() => UiCardLibrarySelfTest.Run(output)).GetAwaiter().GetResult())!;
            foreach (var item in cardLibrary["checks"]!.AsArray())
            { checks.Add("CL", item!["check"]!.GetValue<string>(), item["pass"]!.GetValue<bool>()); }
            var shots = new JsonArray();
            foreach (var theme in new[] { "light", "dark" }) { PagePhase(checks, output, theme, shots, browserProcesses); }
            report["screenshots"] = shots;
            ProbePhase(checks, output, report, browserProcesses);
        }
        catch (Exception exception)
        {
            checks.Add("run", "no_exception", false, exception.ToString());
        }
        foreach (var id in browserProcesses.Where(id => id != 0))
        {
            try { using var process = Process.GetProcessById((int)id); process.WaitForExit(15000); }
            catch (ArgumentException) { }
        }
        var passed = checks.Total > 0 && checks.Passed == checks.Total;
        report["passed"] = passed;
        report["checks_passed"] = checks.Passed;
        report["checks_total"] = checks.Total;
        report["failures"] = new JsonArray(checks.Failures.Select(item => (JsonNode)JsonValue.Create(item)!).ToArray());
        report["checks"] = checks.ToJson();
        var json = report.ToJsonString(new JsonSerializerOptions { TypeInfoResolver = new System.Text.Json.Serialization.Metadata.DefaultJsonTypeInfoResolver(), WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping });
        var path = Path.Combine(output, "selftest-ui.json");
        File.WriteAllText(path, json, new UTF8Encoding(false));
        Console.WriteLine(json);
        Console.WriteLine($"ui self-test: {checks.Passed}/{checks.Total} checks passed; report: {path}");
        Environment.ExitCode = passed ? 0 : 1;
    }

    // ---------------------------------------------------------------- phase R: router without a WebView
    private static void RouterPhase(Checks checks, string output)
    {
        const string P = "R";
        var uiFolder = UiPaths.OutputUiFolder();
        var protocol = UiProtocol.LoadEmbedded();
        var schema = UiTaskSchema.LoadFromUiFolder(uiFolder);
        checks.Add(P, "embedded_protocol_matches_output_copy",
            JsonNode.DeepEquals(JsonNode.Parse(File.ReadAllText(Path.Combine(uiFolder, "protocol.json"))), JsonNode.Parse(protocol.Root.GetRawText())));
        checks.Add(P, "csp_meta_exact", File.ReadAllText(Path.Combine(uiFolder, "index.html")).Contains(
            $"<meta http-equiv=\"Content-Security-Policy\" content=\"{ExpectedCsp}\">", StringComparison.Ordinal));
        var store = new UiProfileStore(Path.Combine(output, "profile-router.json"), schema);
        long now = 0;
        var router = new UiMessageRouter(protocol, schema, store, () => now);
        var events = new List<string>();
        router.EventReady += events.Add;
        const string Home = "https://maa-ui.test/index.html";
        var seq = 0;
        string Req(string cmd, string paramsJson, string extra = "") => $"{{\"v\":1,\"id\":\"r-{++seq}\",\"cmd\":\"{cmd}\",\"params\":{paramsJson}{extra}}}";
        bool Ok(string? reply) => reply is not null && (bool)JsonNode.Parse(reply)!["ok"]! == true;
        bool Refused(string? reply) => reply is not null && (bool)JsonNode.Parse(reply)!["ok"]! == false;
        string ErrorOf(string? reply) => reply is null ? "" : (string?)JsonNode.Parse(reply)!["error"] ?? "";
        void Tick() => now += 1000;

        checks.Add(P, "ready_ok", Ok(router.Handle(Home, Req("ui.ready", "{\"protocol_version\":1}"))));
        // policy.get: empty without a repo root; with one, a read-only summary of the real project_policy.json.
        var noPolicy = router.Handle(Home, Req("policy.get", "{}"));
        checks.Add(P, "policy_get_empty_without_root", Ok(noPolicy) && JsonNode.Parse(noPolicy!)!["result"]!["policy"]!.AsObject().Count == 0);
        Tick();
        var policyRouter = new UiMessageRouter(protocol, schema, store, () => now, policy: () => UiPolicySummary.Load(UiPaths.RepoRoot()));
        var policyReply = policyRouter.Handle(Home, Req("policy.get", "{}"));
        var summary = Ok(policyReply) ? JsonNode.Parse(policyReply!)!["result"]!["policy"] as JsonObject : null;
        checks.Add(P, "policy_get_summary", summary is not null && summary["premium_currency_allowed"] is JsonValue pc && pc.GetValue<bool>() == false
            && summary["paid_items_allowed"] is JsonValue pi && pi.GetValue<bool>() == false && summary.ContainsKey("risk_reference_resolvable"));
        // 扫描持有卡 readiness: read-only; since 2026-10-08 the catalog enables the five card-scan
        // actions and the entry for the one-shot supervised launcher entry, so the shipped tree reports ready.
        checks.Add(P, "policy_card_scan_ready_for_supervised_entry", summary is not null
            && summary["card_scan_ready"] is JsonValue csr && csr.GetValue<bool>() == true
            && summary["card_scan_reason"] is JsonValue csReason && csReason.GetValue<string>() == "ready");
        {
            var rect = new Rectangle(0, 0, 10, 10);
            PipelineAction Act(string id, string family, bool enabled) => new(id, family, false, rect, null, enabled, new[] { "SC.X" });
            var on = new[] { Act("a", "card-scan", true), Act("b", "card-scan", true), Act("home-idol", "training-bronze", true) };
            const string entryOn = "{\"SC.CardScan.Entry\":{\"recognition\":\"DirectHit\"}}";
            const string entryOff = "{\"SC.CardScan.Entry\":{\"enabled\":false}}";
            checks.Add(P, "card_scan_ready_when_all_enabled", UiPolicySummary.CardScanReadiness(on, entryOn) == (true, "ready")
                && UiPolicySummary.CardScanReadiness(on, "{\"SC.CardScan.Entry\":{\"enabled\":true}}").Ready);
            checks.Add(P, "card_scan_one_disabled_action_blocks", UiPolicySummary.CardScanReadiness(
                new[] { on[0], Act("b", "card-scan", false) }, entryOn) == (false, "card_scan_actions_disabled:b"));
            checks.Add(P, "card_scan_entry_disabled_blocks", UiPolicySummary.CardScanReadiness(on, entryOff) == (false, "card_scan_entry_disabled")
                && UiPolicySummary.CardScanReadiness(on, "{\"SC.CardScan.Entry\":{\"enabled\":\"yes\"}}").Reason == "card_scan_entry_disabled");
            checks.Add(P, "card_scan_missing_parts_block", UiPolicySummary.CardScanReadiness(new[] { on[2] }, entryOn).Reason == "card_scan_actions_missing"
                && UiPolicySummary.CardScanReadiness(on, null).Reason == "card_scan_pipeline_unreadable"
                && UiPolicySummary.CardScanReadiness(on, "{not json").Reason == "card_scan_pipeline_unreadable"
                && UiPolicySummary.CardScanReadiness(on, "{}").Reason == "card_scan_entry_missing");
        }
        checks.Add(P, "policy_get_rejects_params",Refused(policyRouter.Handle(Home, Req("policy.get", "{\"path\":\"x\"}"))));
        Tick();
        foreach (var source in new[] { null, "", "https://maa-ui.test/index.html?x=1", "https://maa-ui.test/probe.html", "https://evil.test/index.html",
                     "http://maa-ui.test/index.html", "file:///C:/ui/index.html", "https://maa-ui.test:444/index.html", "https://MAA-UI.test/index.html" })
        {
            checks.Add(P, "wrong_origin_dropped:" + (source ?? "null"), router.Handle(source, Req("ui.ready", "{\"protocol_version\":1}")) is null);
        }
        Tick();
        var refusals = new (string Name, string Json)[]
        {
            ("unknown_command", Req("fs.read", "{}")),
            ("live_command", Req("run.live", "{}")),
            ("envelope_extra_field", Req("ui.ready", "{\"protocol_version\":1}", ",\"token\":\"x\"")),
            ("envelope_bad_version", $"{{\"v\":2,\"id\":\"r-{++seq}\",\"cmd\":\"ui.ready\",\"params\":{{\"protocol_version\":1}}}}"),
            ("params_not_object", Req("ui.ready", "[]")),
            ("params_extra_key", Req("schema.get", "{\"path\":\"C:\\\\Windows\"}")),
            ("params_missing_required", Req("profile.save", "{}")),
            ("params_bad_enum", Req("run.stop", "{\"kind\":\"kill\"}")),
            ("params_bad_const", Req("ui.ready", "{\"protocol_version\":2}")),
            ("params_duplicate_key", Req("run.stop", "{\"kind\":\"cooperative\",\"kind\":\"emergency\"}")),
            ("profile_spend_field", Req("profile.save", "{\"profile\":{\"schema_version\":1,\"spend_flags\":[\"allow_grade_spend\"]}}")),
            ("profile_token_pref", Req("profile.save", "{\"profile\":{\"schema_version\":1,\"prefs\":{\"token\":\"secret\"}}}")),
            ("profile_input_enabled", Req("profile.save", "{\"profile\":{\"schema_version\":1,\"input_enabled\":true}}")),
            ("run_live_mode", Req("run.start", "{\"mode\":\"live\",\"config\":{\"tasks\":[]},\"spend_flags\":[]}")),
            ("run_unknown_flag", Req("run.start", "{\"mode\":\"read_only\",\"config\":{\"tasks\":[]},\"spend_flags\":[\"allow_jewel_spend\"]}")),
            ("run_frames_path", Req("run.start", "{\"mode\":\"offline_replay\",\"config\":{\"tasks\":[]},\"spend_flags\":[],\"frames_id\":\"..\\\\private\"}")),
            ("run_config_allow_spend", Req("run.start", "{\"mode\":\"read_only\",\"config\":{\"tasks\":[],\"allow_spend\":[\"x\"]},\"spend_flags\":[]}")),
        };
        foreach (var (name, json) in refusals) { checks.Add(P, "refused:" + name, Refused(router.Handle(Home, json))); }
        Tick();
        var drops = new (string Name, string? Json)[]
        {
            ("not_json", "{oops"),
            ("string_message", "\"hello\""),
            ("bad_id", "{\"v\":1,\"id\":\"../etc\",\"cmd\":\"ui.ready\",\"params\":{\"protocol_version\":1}}"),
            ("missing_id", "{\"v\":1,\"cmd\":\"ui.ready\",\"params\":{\"protocol_version\":1}}"),
            ("oversize", Req("schema.get", "{}", ",\"pad\":\"" + new string('x', protocol.MaxMessageBytes) + "\"")),
            ("null", null),
        };
        foreach (var (name, json) in drops) { checks.Add(P, "dropped:" + name, router.Handle(Home, json) is null); }
        Tick();
        var burst = Enumerable.Range(0, protocol.MaxMessagesPerSecond + 5).Select(_ => router.Handle(Home, Req("ui.ready", "{\"protocol_version\":1}"))).ToList();
        checks.Add(P, "rate_limit", burst.Take(protocol.MaxMessagesPerSecond).All(Ok)
            && burst.Skip(protocol.MaxMessagesPerSecond).All(reply => Refused(reply) && ErrorOf(reply).Contains("频繁", StringComparison.Ordinal)));
        Tick();
        checks.Add(P, "rate_limit_recovers", Ok(router.Handle(Home, Req("ui.ready", "{\"protocol_version\":1}"))));

        // Profile: sanitised, atomic, host-derived spend flags.
        var dirty = "{\"profile\":{\"schema_version\":1,\"preset\":\"C:\\\\evil\",\"tasks\":[" +
            "{\"name\":\"Evil\",\"enabled\":true}," +
            "{\"name\":\"Training\",\"enabled\":true,\"option\":{\"训练使用铜券\":\"Yes\",\"Grade难度\":\"Hard\"}}," +
            "{\"name\":\"GradeFes\",\"enabled\":true,\"option\":{\"Grade使用参加权\":\"No\",\"Grade难度\":\"C:\\\\Windows\\\\x\"}}," +
            "{\"name\":\"Training\",\"enabled\":false}]," +
            "\"prefs\":{\"theme\":\"dark\",\"developer\":true,\"hide_chibi_visitor\":true,\"reduce_chibi_motion\":true,\"notify\":{\"Bad-Key\":true,\"all_done\":false}}," +
            "\"daily_manual\":{\"../../etc\":{\"x\":true},\"2026-10-04\":{\"free_draw\":true,\"../x\":true}}}}";
        checks.Add(P, "profile_save_ok", Ok(router.Handle(Home, Req("profile.save", dirty))));
        var savedText = File.ReadAllText(store.Path);
        var saved = JsonNode.Parse(savedText)!.AsObject();
        var tasks = saved["tasks"]!.AsArray();
        checks.Add(P, "profile_unknown_task_dropped", tasks.All(item => (string?)item!["name"] != "Evil") && tasks.Count == 2);
        checks.Add(P, "profile_duplicate_task_dropped", tasks.Count(item => (string?)item!["name"] == "Training") == 1);
        checks.Add(P, "profile_foreign_option_dropped", tasks[0]!["option"]!.AsObject().Count == 1);
        checks.Add(P, "profile_invalid_case_dropped", tasks[1]!["option"]!["Grade难度"] is null);
        checks.Add(P, "profile_no_path_strings", !savedText.Contains('\\') && !savedText.Contains("evil", StringComparison.OrdinalIgnoreCase) && !savedText.Contains("..", StringComparison.Ordinal));
        checks.Add(P, "profile_unknown_preset_null", saved.ContainsKey("preset") && saved["preset"] is null);
        checks.Add(P, "profile_prefs_sanitised", (string?)saved["prefs"]!["theme"] == "dark" 
            && saved["prefs"]!["notify"]!.AsObject().Count == 1);
        checks.Add(P, "profile_saves_visitor_visibility", (bool?)saved["prefs"]!["hide_chibi_visitor"] == true
            && store.Sanitize(JsonNode.Parse("{\"schema_version\":1,\"prefs\":{\"hide_chibi_visitor\":false}}"))["prefs"]?["hide_chibi_visitor"]?.GetValue<bool>() == false);
        checks.Add(P, "profile_saves_visitor_motion", (bool?)saved["prefs"]!["reduce_chibi_motion"] == true);
        checks.Add(P, "profile_daily_sanitised", saved["daily_manual"]!.AsObject().Count == 1 && saved["daily_manual"]!["2026-10-04"]!.AsObject().Count == 1);
        checks.Add(P, "profile_atomic_no_temp_left", !Directory.EnumerateFiles(output, "profile-router.json.tmp-*").Any());
        var got = router.Handle(Home, Req("profile.get", "{}"));
        checks.Add(P, "profile_roundtrip", Ok(got) && JsonNode.DeepEquals(JsonNode.Parse(got!)!["result"]!["profile"], saved));

        // Developer action-table reload requires a game host and the developer setting.
        {
            var removedRecorder = router.Handle(Home, Req("dev.recorder", "{\"op\":\"start\"}"));
            checks.Add(P, "dev_recorder_command_removed", Refused(removedRecorder) && ErrorOf(removedRecorder).Contains("白名单", StringComparison.Ordinal));
            var fake = new FakeDevTools();
            var plainStore = new UiProfileStore(Path.Combine(output, "profile-devtools-off.json"), schema);
            var plain = new UiMessageRouter(protocol, schema, plainStore, () => now, hostStatus: () => new JsonObject { ["connected"] = true }, devTools: fake);
            Tick();
            var offReload = plain.Handle(Home, Req("dev.action_table", "{\"op\":\"reload\"}"));
            checks.Add(P, "dev_tools_need_developer_pref", Refused(offReload) && fake.Calls.Count == 0
                && ErrorOf(offReload).Contains("开发者选项", StringComparison.Ordinal));
            var devRouter = new UiMessageRouter(protocol, schema, store, () => now, hostStatus: () => new JsonObject { ["connected"] = true }, devTools: fake);
            var devEvents = new List<string>();
            devRouter.EventReady += devEvents.Add;
            Tick();
            checks.Add(P, "dev_status_subscribe_ok", Ok(devRouter.Handle(Home, Req("status.subscribe", "{\"enabled\":true}"))));
            checks.Add(P, "dev_status_has_action_table", devEvents.Any(e => e.Contains("\"action_table\"", StringComparison.Ordinal))
                && !devEvents.Any(e => e.Contains("\"recorder\"", StringComparison.Ordinal)));
            checks.Add(P, "dev_action_table_reload_accepted", Ok(devRouter.Handle(Home, Req("dev.action_table", "{\"op\":\"reload\"}"))) && fake.Calls.Last() == "table:reload");
            Tick();
            var beforeOff = fake.Calls.Count;
            checks.Add(P, "dev_action_table_needs_developer_pref", Refused(plain.Handle(Home, Req("dev.action_table", "{\"op\":\"reload\"}"))) && fake.Calls.Count == beforeOff);
            var bad = new[] {
                ("dev_action_table_unknown_op", Req("dev.action_table", "{\"op\":\"swap\"}")),
                ("dev_action_table_path_field", Req("dev.action_table", "{\"op\":\"reload\",\"path\":\"C:/x.json\"}")),
            };
            var calls = fake.Calls.Count;
            foreach (var (name, json) in bad) { checks.Add(P, name + "_refused", Refused(devRouter.Handle(Home, json))); }
            checks.Add(P, "dev_invalid_params_never_reach_host", fake.Calls.Count == calls);
        }

        // Host-derived spend flags ignore the page's spend_flags.
        var run = router.Handle(Home, Req("run.start",
            "{\"mode\":\"read_only\",\"config\":{\"tasks\":[{\"name\":\"Training\",\"enabled\":true},{\"name\":\"GradeFes\",\"enabled\":true}]},\"spend_flags\":[\"allow_grade_spend\",\"allow_match_spend\"]}"));
        checks.Add(P, "run_start_refused_in_preview", Refused(run) && ErrorOf(run).Contains("预览", StringComparison.Ordinal));
        checks.Add(P, "spend_flags_host_derived", router.LastRun is { } audit
            && audit.DerivedFlags.SequenceEqual(new[] { "allow_training_spend" })
            && audit.IgnoredPageFlags.SequenceEqual(new[] { "allow_grade_spend", "allow_match_spend" }),
            router.LastRun);
        router.Handle(Home, Req("run.start", "{\"mode\":\"read_only\",\"config\":{\"tasks\":[{\"name\":\"Training\",\"enabled\":false}]},\"spend_flags\":[]}"));
        checks.Add(P, "spend_flags_need_request_enabled", router.LastRun is { DerivedFlags.Count: 0 });
        // Daily "only this task" can run an unticked row, while its saved spend option remains authoritative.
        var originalProfile = store.Load();
        store.Save(JsonNode.Parse("{\"schema_version\":1,\"tasks\":[{\"name\":\"Training\",\"enabled\":false},"
            + "{\"name\":\"Shop\",\"enabled\":true}]}"));
        const string OneTraining = "\"config\":{\"tasks\":[{\"name\":\"Training\",\"enabled\":true}]},\"spend_flags\":[\"allow_training_spend\"]}";
        router.Handle(Home, Req("run.start", "{\"mode\":\"supervised_daily\",\"confirmed\":true," + OneTraining));
        checks.Add(P, "daily_unticked_row_uses_saved_spend_option", router.LastRun is { } dailyAudit
            && dailyAudit.DerivedFlags.SequenceEqual(new[] { "allow_training_spend" }) && dailyAudit.IgnoredPageFlags.Count == 0, router.LastRun);
        checks.Add(P, "daily_single_task_keeps_saved_plan", store.Load()["tasks"]![0]!["enabled"]!.GetValue<bool>() == false);
        router.Handle(Home, Req("run.start", "{\"mode\":\"read_only\"," + OneTraining));
        checks.Add(P, "non_daily_still_needs_saved_enabled", router.LastRun is { DerivedFlags.Count: 0 }
            && router.LastRun.IgnoredPageFlags.SequenceEqual(new[] { "allow_training_spend" }), router.LastRun);
        store.Save(JsonNode.Parse("{\"schema_version\":1,\"tasks\":[{\"name\":\"Training\",\"enabled\":false,"
            + "\"option\":{\"训练使用铜券\":\"No\"}}]}"));
        router.Handle(Home, Req("run.start", "{\"mode\":\"supervised_daily\",\"confirmed\":true," + OneTraining));
        checks.Add(P, "daily_saved_no_spend_still_refuses_page_flag", router.LastRun is { DerivedFlags.Count: 0 }
            && router.LastRun.IgnoredPageFlags.SequenceEqual(new[] { "allow_training_spend" }), router.LastRun);
        store.Save(originalProfile);
        var defaults = schema.DeriveSpendFlags(store.Sanitize(JsonNode.Parse(
            "{\"schema_version\":1,\"tasks\":[{\"name\":\"Training\",\"enabled\":true},{\"name\":\"GradeFes\",\"enabled\":true},{\"name\":\"MatchSkip\",\"enabled\":true}]}")));
        // MatchSkip defaults to using all remaining participation rights (daily scope), so it derives its flag.
        checks.Add(P, "spend_defaults_match_schema", defaults.SequenceEqual(new[] { "allow_match_spend", "allow_training_spend" }), defaults);
        var disabled = schema.DeriveSpendFlags(store.Sanitize(JsonNode.Parse(
            "{\"schema_version\":1,\"tasks\":[{\"name\":\"Training\"},{\"name\":\"GradeFes\",\"enabled\":false,\"option\":{\"Grade使用参加权\":\"Yes\"}}]}")));
        checks.Add(P, "spend_requires_explicit_enabled", disabled.Count == 0, disabled);
        // removed_tasks: known names only, unique, and a removed task is saved disabled (no run, no spend flag).
        var removedClean = store.Sanitize(JsonNode.Parse(
            "{\"schema_version\":1,\"removed_tasks\":[\"Training\",\"Evil\",\"Training\",\"../x\",7,\"Business\"]," +
            "\"tasks\":[{\"name\":\"Training\",\"enabled\":true},{\"name\":\"Shop\",\"enabled\":true}]}"));
        checks.Add(P, "profile_removed_tasks_sanitised", removedClean["removed_tasks"] is JsonArray removedList
            && removedList.Select(node => (string?)node).SequenceEqual(new[] { "Training", "Business" }), removedClean["removed_tasks"]);
        checks.Add(P, "profile_removed_task_saved_disabled", removedClean["tasks"]![0]!["enabled"]!.GetValue<bool>() == false
            && removedClean["tasks"]![1]!["enabled"]!.GetValue<bool>());
        var removedFlags = schema.DeriveSpendFlags(removedClean);
        checks.Add(P, "spend_flags_skip_removed_tasks", removedFlags.SequenceEqual(new[] { "allow_shop_spend" }), removedFlags);
        var legacy = store.Sanitize(JsonNode.Parse("{\"schema_version\":1,\"tasks\":[{\"name\":\"Training\",\"enabled\":true}]}"));
        checks.Add(P, "profile_legacy_without_removed_tasks", !legacy.ContainsKey("removed_tasks") && legacy["tasks"]![0]!["enabled"]!.GetValue<bool>());
        var stop = router.Handle(Home, Req("run.stop", "{\"kind\":\"emergency\"}"));
        checks.Add(P, "run_stop_not_accepted", Ok(stop) && stop!.Contains("\"accepted\":false", StringComparison.Ordinal));

        events.Clear();
        router.Handle(Home, Req("status.subscribe", "{\"enabled\":true}"));
        router.Handle(Home, Req("log.subscribe", "{\"enabled\":true}"));
        checks.Add(P, "synthetic_events_preview_only", events.Count >= 2 && events.All(item => item.Contains("\"v\":1", StringComparison.Ordinal))
            && events.Any(item => item.Contains("\"connected\":false", StringComparison.Ordinal) && item.Contains("\"pipeline_input_enabled\":false", StringComparison.Ordinal)));
    }

    // ---------------------------------------------------------------- phase A: the real UI page
    private static void PagePhase(Checks checks, string output, string theme, JsonArray shots, List<uint> browsers)
    {
        var P = "A-" + theme;
        var profile = Path.Combine(output, $"profile-{theme}.json");
        File.WriteAllText(profile, $"{{\"schema_version\":1,\"preset\":\"daily\",\"prefs\":{{\"theme\":\"{theme}\"}}}}\n");
        var options = new UiShellOptions(UiPaths.OutputUiFolder(), Path.Combine(output, "webview2-" + theme), profile, DevTools: false);
        RunForm(options, browsers, async form =>
        {
            checks.Add(P, "panel_initialized", form.InitializationError is null, form.InitializationError?.Message);
            if (form.InitializationError is not null) { return; }
            var handshake = await WaitFor(() => HandshakeCommands.All(cmd => form.Router.Decisions.Any(d => d.Command == cmd && d.Accepted)), 30000);
            checks.Add(P, "ready_handshake", handshake, form.Router.Decisions.Select(d => $"{d.Command}:{d.Accepted}").ToList());
            await Task.Delay(1500);
            checks.Add(P, "page_messages_all_accepted", form.Router.Decisions.All(d => d.Accepted && d.Source == UiPanelHost.HomeUri));
            checks.Add(P, "no_blocked_navigation", form.Panel.BlockedNavigations.Count == 0, form.Panel.BlockedNavigations);
            checks.Add(P, "no_blocked_request", form.Panel.BlockedRequests.Count == 0, form.Panel.BlockedRequests);
            // maa-ui.test files are served by the virtual-host mapping and do not raise WebResourceRequested; every request
            // that does reach the filter must be maa-ui.test, and the page must not have tried any other host.
            checks.Add(P, "requests_only_maa_ui_test", form.Panel.BlockedRequests.Count == 0
                && form.Panel.AllowedRequestHosts.All(host => string.Equals(host, UiProtocol.VirtualHost, StringComparison.OrdinalIgnoreCase)),
                new { form.Panel.AllowedRequests, hosts = form.Panel.AllowedRequestHosts });
            checks.Add(P, "documents_only_home", form.Panel.Documents.Count >= 1 && form.Panel.Documents.All(uri => uri == UiPanelHost.HomeUri), form.Panel.Documents);
            checks.Add(P, "status_subscribed", form.Router.StatusSubscribed && form.Router.LogSubscribed);
            // 2026-10-11 resizable preview: the window opens at 1280 + the profile's panel width (Windows may only shrink a
            // window that does not fit the screen, never below the minimum); the placeholder column stays 1280 wide and the
            // panel fills the rest.
            var view = form.Panel.View;
            bool ColumnFixed() => form.Canvas.Width == UiShellForm.GameWidth && form.Canvas.Left == 0
                && view.Parent!.Left == UiShellForm.GameWidth && view.Parent.Width == form.ClientSize.Width - UiShellForm.GameWidth
                && view.Width == view.Parent.Width && view.Height == form.ClientSize.Height;
            checks.Add(P, "window_size", form.FormBorderStyle == FormBorderStyle.Sizable && form.MaximizeBox
                && form.ClientSize.Width <= UiShellForm.GameWidth + form.PanelPixelWidth && form.ClientSize.Width >= form.MinimumClient.Width
                && form.ClientSize.Height >= form.MinimumClient.Height && ColumnFixed(),
                $"{form.ClientSize.Width}x{form.ClientSize.Height} (min {form.MinimumClient.Width}x{form.MinimumClient.Height}, " +
                $"panel {view.Width}x{view.Height}) @ {form.DeviceDpi} dpi");
            var panelPng = Path.Combine(output, $"panel-{theme}.png");
            var viewSize = view.Size;
            await form.Panel.SavePanelPngAsync(panelPng);
            var windowPng = Path.Combine(output, $"window-{theme}.png");
            var windowSaved = SaveWindowPng(form, windowPng);
            using (var image = Image.FromFile(panelPng))
            {
                // CapturePreviewAsync renders the page at its device-pixel size; allow the 96-dpi CSS size as well.
                bool Matches(int pixels, int control) => pixels == control || pixels == (int)Math.Round(control * 96.0 / form.DeviceDpi);
                checks.Add(P, "panel_screenshot", Matches(image.Width, viewSize.Width) && Matches(image.Height, viewSize.Height),
                    $"{image.Width}x{image.Height} for {viewSize.Width}x{viewSize.Height}");
            }
            // Resize and maximize: the column keeps its fixed width, only the panel grows; never below the minimum.
            var before = form.ClientSize;
            // Resize the outer window like a user drag. ClientSize can cache the requested size when Windows caps an
            // oversized window to the monitor; the docked controls still receive the actual native client rectangle.
            form.Size = new Size(form.Width + 160, form.Height + 80);
            await Task.Delay(300);
            checks.Add(P, "window_resize_grows_panel_only", ColumnFixed() && view.Parent!.Width == form.ClientSize.Width - UiShellForm.GameWidth
                && form.ClientSize.Width >= before.Width, $"{form.ClientSize} panel {view.Parent!.Width}");
            form.ClientSize = new Size(800, 400);
            await Task.Delay(300);
            checks.Add(P, "window_minimum_size", form.ClientSize.Width >= form.MinimumClient.Width && form.ClientSize.Height >= form.MinimumClient.Height
                && ColumnFixed(), $"{form.ClientSize}");
            form.WindowState = FormWindowState.Maximized;
            await Task.Delay(500);
            checks.Add(P, "window_maximize_keeps_column", form.WindowState == FormWindowState.Maximized && ColumnFixed(), $"{form.ClientSize}");
            form.WindowState = FormWindowState.Normal;
            form.ClientSize = before;
            await Task.Delay(300);
            checks.Add(P, "window_screenshot", windowSaved);
            shots.Add(JsonValue.Create(panelPng));
            if (windowSaved) { shots.Add(JsonValue.Create(windowPng)); }
        });
    }

    // ---------------------------------------------------------------- phase B: adversarial probe page
    private static void ProbePhase(Checks checks, string output, JsonObject report, List<uint> browsers)
    {
        const string P = "B";
        var site = Path.Combine(output, "probe-site");
        Directory.CreateDirectory(Path.Combine(site, "fixtures"));
        var uiFolder = UiPaths.OutputUiFolder();
        File.Copy(Path.Combine(uiFolder, "fixtures", "schema.json"), Path.Combine(site, "fixtures", "schema.json"), true);
        File.Copy(Path.Combine(uiFolder, "annotations.json"), Path.Combine(site, "annotations.json"), true);
        File.WriteAllText(Path.Combine(site, "index.html"), ProbeHtml("probe.js"), new UTF8Encoding(false));
        File.WriteAllText(Path.Combine(site, "probe.js"), ProbeScript, new UTF8Encoding(false));
        File.WriteAllText(Path.Combine(site, "wrong.html"), ProbeHtml("wrong.js"), new UTF8Encoding(false));
        File.WriteAllText(Path.Combine(site, "wrong.js"), WrongOriginScript, new UTF8Encoding(false));
        var profile = Path.Combine(output, "profile-probe.json");
        var options = new UiShellOptions(site, Path.Combine(output, "webview2-probe"), profile, DevTools: false);
        RunForm(options, browsers, async form =>
        {
            checks.Add(P, "panel_initialized", form.InitializationError is null, form.InitializationError?.Message);
            if (form.InitializationError is not null) { return; }
            JsonObject? results = null;
            form.Panel.MessageObserved += (source, json) =>
            {
                if (source == UiPanelHost.HomeUri && json.Contains("\"selftest.report\"", StringComparison.Ordinal))
                { results = JsonNode.Parse(json)?["params"]?["results"]?.AsObject(); }
            };
            var gotReport = await WaitFor(() => results is not null, 45000);
            checks.Add(P, "probe_report_received", gotReport);
            var wrong = await WaitFor(() => form.Router.Decisions.Count(d => d.Source.EndsWith("/wrong.html", StringComparison.Ordinal)) >= 2, 15000);
            checks.Add(P, "wrong_origin_messages_dropped", wrong && form.Router.Decisions.Where(d => d.Source.EndsWith("/wrong.html", StringComparison.Ordinal))
                .All(d => !d.Accepted && d.Error == "来源不符"));
            await Task.Delay(500);
            if (results is null) { return; }
            report["probe_results"] = results.DeepClone();
            bool Ok(string key) => results[key] is JsonObject reply && (bool?)reply["ok"] == true;
            bool Refused(string key) => results[key] is JsonObject reply && (bool?)reply["ok"] == false;
            bool Dropped(string key) => results.ContainsKey(key) && results[key] is null;
            checks.Add(P, "ready_ok", Ok("ready") && (string?)results["ready"]!["result"]!["host"] == UiMessageRouter.HostKind);
            foreach (var key in new[] { "unknown_cmd", "live_cmd", "extra_envelope", "bad_version", "extra_param", "missing_param", "profile_spend_field", "profile_token", "bad_enum" })
            { checks.Add(P, "refused:" + key, Refused(key)); }
            foreach (var key in new[] { "oversize", "bad_id", "not_object" }) { checks.Add(P, "dropped:" + key, Dropped(key)); }
            checks.Add(P, "profile_save_ok", Ok("save"));
            var stored = results["get"]?["result"]?["profile"]?.ToJsonString(UiMessageRouter.JsonOptions) ?? "";
            checks.Add(P, "profile_get_sanitised", Ok("get") && !stored.Contains("Evil", StringComparison.Ordinal) && !stored.Contains('\\')
                && !stored.Contains("..", StringComparison.Ordinal) && stored.Contains("\"Training\"", StringComparison.Ordinal), stored);
            var onDisk = File.Exists(profile) ? File.ReadAllText(profile) : "";
            checks.Add(P, "profile_file_matches", onDisk.Length > 0 && JsonNode.DeepEquals(JsonNode.Parse(onDisk), results["get"]?["result"]?["profile"]));
            checks.Add(P, "run_start_refused_in_preview", Refused("run_start"));
            checks.Add(P, "spend_flags_host_derived", form.Router.LastRun is { } audit && audit.DerivedFlags.SequenceEqual(new[] { "allow_training_spend" })
                && audit.IgnoredPageFlags.SequenceEqual(new[] { "allow_grade_spend" }), form.Router.LastRun);
            checks.Add(P, "run_stop_not_accepted", Ok("run_stop") && (bool?)results["run_stop"]!["result"]!["accepted"] == false);
            checks.Add(P, "status_event_received", results["events"]?.AsArray().Any(item => (string?)item == "status.update") == true);
            checks.Add(P, "rate_limited", (int?)results["burst_rate_limited"] > 0, new { limited = (int?)results["burst_rate_limited"], ok = (int?)results["burst_ok"] });
            checks.Add(P, "report_command_refused", form.Router.Decisions.Any(d => d.Command == "selftest.report" && !d.Accepted));
            checks.Add(P, "popup_blocked", form.Panel.NewWindowsBlocked >= 1 && (string?)results["popup"] != "window", new { form.Panel.NewWindowsBlocked, popup = (string?)results["popup"] });
            checks.Add(P, "external_image_blocked", (string?)results["img"] == "error" && form.Panel.BlockedRequests.Any(uri => uri.StartsWith("https://blocked.invalid/", StringComparison.Ordinal)),
                new { img = (string?)results["img"], blocked = form.Panel.BlockedRequests });
            checks.Add(P, "external_fetch_refused", (string?)results["fetch"] == "refused");
            var navigations = form.Panel.BlockedNavigations;
            checks.Add(P, "external_navigation_cancelled", navigations.Any(uri => uri.StartsWith("https://blocked.invalid/navigate", StringComparison.Ordinal)), navigations);
            // Chromium refuses file: from an https page before NavigationStarting; either way it must never load.
            checks.Add(P, "documents_only_maa_ui_test", form.Panel.Documents.Count >= 2 && form.Panel.Documents.All(UiPanelHost.IsUiUri)
                && form.Panel.Documents.Any(uri => uri.EndsWith("/wrong.html", StringComparison.Ordinal)), form.Panel.Documents);
            checks.Add(P, "allowed_requests_only_maa_ui_test", form.Panel.AllowedRequestHosts.All(host => string.Equals(host, UiProtocol.VirtualHost, StringComparison.OrdinalIgnoreCase)),
                form.Panel.AllowedRequestHosts);
            checks.Add(P, "blocked_requests_never_maa_ui_test", form.Panel.BlockedRequests.All(uri => !UiPanelHost.IsUiUri(uri)), form.Panel.BlockedRequests);
            checks.Add(P, "download_cancelled", form.Panel.DownloadsBlocked >= 1, form.Panel.DownloadsBlocked);
            checks.Add(P, "permission_denied", form.Panel.PermissionsDenied >= 1, form.Panel.PermissionsDenied);
        });
    }

    private static void RunForm(UiShellOptions options, List<uint> browsers, Func<UiShellForm, Task> body)
    {
        var form = new UiShellForm(options) { StartPosition = FormStartPosition.Manual, Location = new Point(0, 0) };
        Exception? failure = null;
        form.Shown += async (_, _) =>
        {
            try
            {
                await form.PanelInitialized.Task.WaitAsync(TimeSpan.FromSeconds(30));
                await body(form);
            }
            catch (Exception exception) { failure = exception; }
            finally
            {
                browsers.Add(form.Panel.BrowserProcessId);
                form.Close();
            }
        };
        Application.Run(form);
        form.Dispose();
        if (failure is not null) { throw new InvalidOperationException("界面自测阶段失败。", failure); }
    }

    private static async Task<bool> WaitFor(Func<bool> condition, int timeoutMs)
    {
        var watch = Stopwatch.StartNew();
        while (watch.ElapsedMilliseconds < timeoutMs)
        {
            if (condition()) { return true; }
            await Task.Delay(50);
        }
        return condition();
    }

    internal static bool SaveWindowPng(Form form, string path)
    {
        using var bitmap = new Bitmap(form.ClientSize.Width, form.ClientSize.Height, PixelFormat.Format32bppArgb);
        using var graphics = Graphics.FromImage(bitmap);
        var hdc = graphics.GetHdc();
        bool ok;
        try
        {
            // Client area only: render the full window then crop the client rectangle.
            ok = PrintWindow(form.Handle, hdc, PrintWindowRenderFullContent | 1);
        }
        finally { graphics.ReleaseHdc(hdc); }
        if (ok) { bitmap.Save(path, ImageFormat.Png); }
        return ok;
    }

    /// <summary>Whole window including any non-client frame (title bar, borders): shows what the user sees as the window edge.</summary>
    internal static bool SaveFramePng(Form form, string path)
    {
        using var bitmap = new Bitmap(form.Width, form.Height, PixelFormat.Format32bppArgb);
        using var graphics = Graphics.FromImage(bitmap);
        var hdc = graphics.GetHdc();
        bool ok;
        try { ok = PrintWindow(form.Handle, hdc, PrintWindowRenderFullContent); }
        finally { graphics.ReleaseHdc(hdc); }
        if (ok) { bitmap.Save(path, ImageFormat.Png); }
        return ok;
    }

    private static string ProbeHtml(string script) =>
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n" +
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'self'; script-src 'self'; img-src 'self' https://blocked.invalid; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'\">\n" +
        $"<title>UI self-test probe</title>\n<script type=\"module\" src=\"{script}\"></script>\n</head>\n<body><p>UI self-test probe (offline)</p></body>\n</html>\n";

    // An adversarial page at the whitelisted origin: every request below except ready/save/get/run/stop/status must be refused or dropped.
    private const string ProbeScript = """
        const results = {};
        const events = [];
        const waiting = new Map();
        const webview = window.chrome.webview;
        webview.addEventListener("message", (event) => {
          const m = event.data;
          if (m && typeof m.id === "string" && waiting.has(m.id)) { const done = waiting.get(m.id); waiting.delete(m.id); done(m); return; }
          if (m && typeof m.event === "string") events.push(m.event);
        });
        let seq = 0;
        const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
        const req = (cmd, params, extra = {}) => ({ v: 1, id: `p-${++seq}`, cmd, params, ...extra });
        function send(message, timeout = 1500) {
          const id = message && typeof message === "object" && typeof message.id === "string" ? message.id : null;
          return new Promise((resolve) => {
            if (id) waiting.set(id, resolve);
            webview.postMessage(message);
            setTimeout(() => { if (!id || waiting.has(id)) { if (id) waiting.delete(id); resolve(null); } }, timeout);
          });
        }
        async function run() {
          results.ready = await send(req("ui.ready", { protocol_version: 1 }));
          results.unknown_cmd = await send(req("fs.read", {}));
          results.live_cmd = await send(req("run.live", {}));
          results.extra_envelope = await send(req("ui.ready", { protocol_version: 1 }, { token: "x" }));
          results.bad_version = await send({ v: 2, id: `p-${++seq}`, cmd: "ui.ready", params: { protocol_version: 1 } });
          results.extra_param = await send(req("schema.get", { path: "C:\\Windows" }));
          results.missing_param = await send(req("profile.save", {}));
          results.profile_spend_field = await send(req("profile.save", { profile: { schema_version: 1, spend_flags: ["allow_grade_spend"] } }));
          results.profile_token = await send(req("profile.save", { profile: { schema_version: 1, prefs: { token: "secret" } } }));
          results.bad_enum = await send(req("run.stop", { kind: "kill" }));
          results.oversize = await send(req("schema.get", {}, { pad: "x".repeat(70000) }));
          results.bad_id = await send({ v: 1, id: "../etc", cmd: "ui.ready", params: { protocol_version: 1 } });
          results.not_object = await send("hello");
          results.save = await send(req("profile.save", { profile: {
            schema_version: 1, preset: "no-such-preset",
            tasks: [
              { name: "Evil", enabled: true },
              { name: "Training", enabled: true, option: { "训练使用铜券": "Yes" } },
              { name: "GradeFes", enabled: true, option: { "Grade使用参加权": "No", "Grade难度": "C:\\Windows\\x" } },
              { name: "Training", enabled: false },
            ],
            prefs: { theme: "light", developer: false, notify: { "Bad-Key": true, all_done: true } },
            daily_manual: { "../../etc": { x: true }, "2026-10-04": { free_draw: true, "../x": true } },
          } }));
          results.get = await send(req("profile.get", {}));
          results.run_start = await send(req("run.start", { mode: "read_only", config: { tasks: [{ name: "Training", enabled: true }, { name: "GradeFes", enabled: true }] }, spend_flags: ["allow_grade_spend"] }));
          results.run_stop = await send(req("run.stop", { kind: "cooperative" }));
          results.status = await send(req("status.subscribe", { enabled: true }));
          await sleep(1300);
          results.events = events.slice(0, 20);
          await sleep(1100);
          const burst = await Promise.all(Array.from({ length: 30 }, () => send(req("ui.ready", { protocol_version: 1 }))));
          results.burst_rate_limited = burst.filter((r) => r && r.ok === false && /频繁/.test(r.error || "")).length;
          results.burst_ok = burst.filter((r) => r && r.ok).length;
          await sleep(1200);
          let popup = "threw";
          try { popup = window.open("https://blocked.invalid/popup") === null ? "null" : "window"; } catch { /* blocked */ }
          results.popup = popup;
          results.img = await new Promise((resolve) => {
            const img = new Image();
            img.onload = () => resolve("loaded");
            img.onerror = () => resolve("error");
            img.src = "https://blocked.invalid/pixel.png";
            setTimeout(() => resolve("timeout"), 3000);
          });
          results.fetch = await fetch("https://blocked.invalid/data").then(() => "fetched", () => "refused");
          try { navigator.geolocation.getCurrentPosition(() => {}, () => {}); } catch { /* denied */ }
          const link = document.createElement("a");
          link.href = URL.createObjectURL(new Blob(["probe"], { type: "text/plain" }));
          link.download = "probe.txt";
          document.body.append(link);
          link.click();
          await sleep(1000);
          await send({ v: 1, id: "report", cmd: "selftest.report", params: { results } }, 300);
          location.href = "https://blocked.invalid/navigate";
          await sleep(600);
          location.href = "file:///C:/Windows/win.ini";
          await sleep(600);
          location.href = "/wrong.html";
        }
        run();
        """;

    // Served from https://maa-ui.test/wrong.html: same host, wrong page, so the router must drop it without replying.
    private const string WrongOriginScript = """
        const message = { v: 1, id: "w-1", cmd: "ui.ready", params: { protocol_version: 1 } };
        window.chrome.webview.postMessage(message);
        setTimeout(() => window.chrome.webview.postMessage({ ...message, id: "w-2" }), 300);
        """;
}
