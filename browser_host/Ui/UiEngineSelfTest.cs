using System.Diagnostics;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// --self-test-ui phase E: the U2 engine supervisor against a fake child (tests/fixtures/ui/fake_engine.py, which writes
/// run_control-format events and never imports Maa, connects to a host or clicks). Covers success, failure, cooperative
/// stop, crash, a hung child that is killed after the grace, the whole-run deadline, refusal before start, event/summary
/// disagreement and malformed events, plus the router's run.start refusals and the default command's arguments.
/// </summary>
internal static partial class UiSelfTest
{
    /// <summary>Phase S: profile sanitising of single-input options (Produce card ids) and prefs.owned_cards.</summary>
    private static void ProfileInputPhase(Checks checks, string output)
    {
        const string P = "S";
        var schema = UiTaskSchema.LoadFromUiFolder(UiPaths.OutputUiFolder());
        // A card-id input (exactly one of P 1xxxxxxxxx / S 2xxxxxxxxx); other single inputs such as Grade参加ユニット
        // (3–6 digit score) or 营业队伍 (free team name) have different rules and are not what this phase checks.
        var input = schema.Options.Values.FirstOrDefault(o => o.Type == "input" && o.SingleInput && o.Verify is not null
            && o.Verify.IsMatch("1000000001") != o.Verify.IsMatch("2000000001"));
        var owner = input is null ? null : schema.Tasks.Values.FirstOrDefault(t => t.Options.Contains(input.Key));
        checks.Add(P, "schema_has_single_input_option", input is not null && owner is not null, input?.Key);
        var store = new UiProfileStore(Path.Combine(output, "profile-inputs.json"), schema);
        var router = new UiMessageRouter(UiProtocol.LoadEmbedded(), schema, store);
        const string Home = "https://maa-ui.test/index.html";
        var seq = 0;
        string Save(string profile) => router.Handle(Home, $"{{\"v\":1,\"id\":\"s-{++seq}\",\"cmd\":\"profile.save\",\"params\":{{\"profile\":{profile}}}}}")!;
        bool Ok(string reply) => (bool)JsonNode.Parse(reply)!["ok"]!;
        if (input is not null && owner is not null)
        {
            var lead = input.Verify?.IsMatch("1000000001") == true ? "1" : "2";
            var good = lead + "000000123";
            var wrongKind = (lead == "1" ? "2" : "1") + "000000123";
            var saved = Save($"{{\"schema_version\":1,\"tasks\":[{{\"name\":\"{owner.Name}\",\"enabled\":true,\"option\":{{\"{input.Key}\":\"{good}\"}}}}]}}");
            var kept = (string?)store.Load()["tasks"]?[0]?["option"]?[input.Key];
            checks.Add(P, "input_value_survives_restart", Ok(saved) && kept == good, kept);
            checks.Add(P, "input_empty_allowed", input.AcceptsInput(""));
            foreach (var bad in new[] { wrongKind, good + "0", "C:\\x", "１000000123", new string('1', 300) })
            {
                Save($"{{\"schema_version\":1,\"tasks\":[{{\"name\":\"{owner.Name}\",\"enabled\":true,\"option\":{{\"{input.Key}\":\"{bad.Replace("\\", "\\\\")}\"}}}}]}}");
                checks.Add(P, "input_rejected:" + (bad.Length > 20 ? "long" : bad), store.Load()["tasks"]?[0]?["option"]?[input.Key] is null);
            }
        }
        var owned = Save("{\"schema_version\":1,\"prefs\":{\"owned_cards\":{\"p\":[\"1000000002\",\"1000000001\"],\"s\":[\"2000000001\"]}}}");
        var prefs = store.Load()["prefs"]?["owned_cards"];
        checks.Add(P, "owned_cards_saved_sorted", Ok(owned) && prefs?["p"]?.AsArray().Select(n => (string?)n).SequenceEqual(new[] { "1000000001", "1000000002" }) == true
            && prefs?["s"]?.AsArray().Count == 1, prefs?.ToJsonString());
        checks.Add(P, "owned_cards_wrong_kind_refused", !Ok(Save("{\"schema_version\":1,\"prefs\":{\"owned_cards\":{\"p\":[\"2000000001\"]}}}")));
        checks.Add(P, "owned_cards_extra_key_refused", !Ok(Save("{\"schema_version\":1,\"prefs\":{\"owned_cards\":{\"x\":[]}}}")));
        checks.Add(P, "owned_cards_duplicates_refused", !Ok(Save("{\"schema_version\":1,\"prefs\":{\"owned_cards\":{\"s\":[\"2000000001\",\"2000000001\"]}}}")));
        var direct = store.Sanitize(JsonNode.Parse("{\"schema_version\":1,\"prefs\":{\"owned_cards\":{\"p\":[\"1000000001\",\"2000000001\",\"1x\",5],\"s\":[\"2000000003\",\"2000000003\"]}}}"));
        checks.Add(P, "owned_cards_sanitised_on_load", direct["prefs"]?["owned_cards"]?["p"]?.AsArray().Count == 1 && direct["prefs"]?["owned_cards"]?["s"]?.AsArray().Count == 1,
            direct.ToJsonString());
        // prefs.owned_cards.details: per-card Lv/凸 for owned ids only; protocol refuses bad shapes, Sanitize drops the rest.
        var withDetails = Save("{\"schema_version\":1,\"prefs\":{\"owned_cards\":{\"p\":[\"1000000001\"],\"s\":[\"2000000001\"],"
            + "\"details\":{\"1000000001\":{\"level\":80,\"limit_break\":4},\"2000000001\":{\"level\":60}}}}}");
        var savedDetails = store.Load()["prefs"]?["owned_cards"]?["details"];
        checks.Add(P, "owned_details_saved", Ok(withDetails) && (int?)savedDetails?["1000000001"]?["level"] == 80
            && (int?)savedDetails?["1000000001"]?["limit_break"] == 4 && savedDetails?["2000000001"]?["limit_break"] is null, savedDetails?.ToJsonString());
        foreach (var (name, bad) in new[] {
            ("level_zero", "{\"level\":0}"), ("level_high", "{\"level\":101}"), ("lb_high", "{\"limit_break\":5}"),
            ("lb_negative", "{\"limit_break\":-1}"), ("level_fraction", "{\"level\":1.5}"), ("level_string", "{\"level\":\"80\"}"),
            ("extra_key", "{\"level\":80,\"skill\":1}") })
        {
            checks.Add(P, "owned_details_refused:" + name, !Ok(Save("{\"schema_version\":1,\"prefs\":{\"owned_cards\":{\"s\":[\"2000000001\"],\"details\":{\"2000000001\":" + bad + "}}}}")));
        }
        var strayDetails = store.Sanitize(JsonNode.Parse("{\"schema_version\":1,\"prefs\":{\"owned_cards\":{\"s\":[\"2000000001\"],\"details\":{"
            + "\"2000000001\":{\"level\":70,\"limit_break\":9},\"2000000099\":{\"level\":50},\"x\":{\"level\":1},\"2000000002\":5}}}}"));
        var stray = strayDetails["prefs"]?["owned_cards"]?["details"]?.AsObject();
        checks.Add(P, "owned_details_sanitised_on_load", stray is { Count: 1 } && (int?)stray["2000000001"]?["level"] == 70 && stray["2000000001"]?["limit_break"] is null,
            strayDetails.ToJsonString());
        var emptyDetails = store.Sanitize(JsonNode.Parse("{\"schema_version\":1,\"prefs\":{\"owned_cards\":{\"s\":[\"2000000001\"],\"details\":{\"2000000001\":{}}}}}"));
        checks.Add(P, "owned_details_empty_dropped", emptyDetails["prefs"]?["owned_cards"]?["details"] is null, emptyDetails.ToJsonString());
        // prefs.knowhow_wanted (ノウハウ page): protocol refuses bad shapes; Sanitize normalises, dedupes and drops the rest.
        var wantedSave = Save("{\"schema_version\":1,\"prefs\":{\"knowhow_wanted\":[{\"name\":\"パーフェクトリィ\",\"min_level\":4,\"purpose\":\"halo\"},"
            + "{\"name\":\"Vocal上限\",\"match\":\"prefix\",\"min_level\":5,\"purpose\":\"step\",\"position\":\"vocal\"}]}}");
        var savedWanted = store.Load()["prefs"]?["knowhow_wanted"]?.AsArray();
        checks.Add(P, "knowhow_wanted_saved", Ok(wantedSave) && savedWanted is { Count: 2 } && (int?)savedWanted[0]?["min_level"] == 4
            && (string?)savedWanted[0]?["match"] == "exact" && (string?)savedWanted[1]?["position"] == "vocal", savedWanted?.ToJsonString());
        foreach (var (name, bad) in new[] {
            ("path", "{\"name\":\"..\\/x\"}"), ("quote", "{\"name\":\"a\\\"b\"}"), ("long", "{\"name\":\"" + new string('a', 41) + "\"}"),
            ("level_high", "{\"name\":\"a\",\"min_level\":7}"), ("purpose", "{\"name\":\"a\",\"purpose\":\"spend\"}"),
            ("extra_key", "{\"name\":\"a\",\"allow\":true}"), ("no_name", "{\"min_level\":3}") })
        {
            checks.Add(P, "knowhow_wanted_refused:" + name, !Ok(Save("{\"schema_version\":1,\"prefs\":{\"knowhow_wanted\":[" + bad + "]}}")));
        }
        var strayWanted = store.Sanitize(JsonNode.Parse("{\"schema_version\":1,\"prefs\":{\"knowhow_wanted\":[{\"name\":\"Ａ Ｂ\"},{\"name\":\"AB\"},"
            + "{\"name\":\"C:\\\\x\"},{\"name\":\"D\",\"min_level\":9,\"position\":\"top\"},5]}}"));
        var stray2 = strayWanted["prefs"]?["knowhow_wanted"]?.AsArray();
        checks.Add(P, "knowhow_wanted_sanitised_on_load", stray2 is { Count: 2 } && (string?)stray2[0]?["name"] == "AB" && (int?)stray2[1]?["min_level"] == 1
            && stray2[1]?["position"] is null, strayWanted.ToJsonString());
        // prefs.auto_step_books (ノウハウ page 「设为 Auto 带书」): exactly three books; anything else is refused or dropped.
        static string StepBook(string title, int eval) => $"{{\"title\":\"{title}\",\"vo\":60,\"da\":20,\"vi\":20,\"me\":30,\"eval\":{eval}}}";
        var trio = string.Join(",", StepBook("【オイサラバエル】樋口円香", 1020), StepBook("【ポシェットの中には】福丸小糸", 1010), StepBook("【ポシェットの中には】福丸小糸", 1000));
        // A fresh router: this phase already used the per-second message budget of the first one.
        var booksRouter = new UiMessageRouter(UiProtocol.LoadEmbedded(), schema, store);
        string SaveBooks(string books) => booksRouter.Handle(Home,
            $"{{\"v\":1,\"id\":\"b-{++seq}\",\"cmd\":\"profile.save\",\"params\":{{\"profile\":{{\"schema_version\":1,\"prefs\":{{\"auto_step_books\":[{books}]}}}}}}}}")!;
        var booksSave = SaveBooks(trio);
        var savedBooks = store.Load()["prefs"]?["auto_step_books"]?.AsArray();
        checks.Add(P, "auto_step_books_saved", Ok(booksSave) && savedBooks is { Count: 3 } && (int?)savedBooks[2]?["eval"] == 1000
            && (string?)savedBooks[0]?["title"] == "【オイサラバエル】樋口円香", booksSave + " " + savedBooks?.ToJsonString());
        foreach (var (name, bad) in new[] {
            ("two", StepBook("a", 1) + "," + StepBook("b", 2)), ("path", trio.Replace("樋口円香", "..\\\\/x")),
            ("eval_high", trio.Replace("1020", "2001")), ("extra_key", trio.Replace("\"eval\":1000", "\"eval\":1000,\"allow\":true")) })
        {
            var reply = SaveBooks(bad);
            checks.Add(P, "auto_step_books_refused:" + name, !Ok(reply) && !reply.Contains("请求过于频繁", StringComparison.Ordinal), reply);
        }
        var strayBooks = store.Sanitize(JsonNode.Parse("{\"schema_version\":1,\"prefs\":{\"auto_step_books\":[" + trio.Replace("\"vo\":60", "\"vo\":-1") + "]}}"));
        checks.Add(P, "auto_step_books_dropped_on_load", strayBooks["prefs"]?["auto_step_books"] is null, strayBooks.ToJsonString());
        // prefs.halo_book (ノウハウ page 「设为 Halo 带书」): exactly one book of the same shape.
        string SaveHalo(string books) => booksRouter.Handle(Home,
            $"{{\"v\":1,\"id\":\"b-{++seq}\",\"cmd\":\"profile.save\",\"params\":{{\"profile\":{{\"schema_version\":1,\"prefs\":{{\"halo_book\":[{books}]}}}}}}}}")!;
        var haloSave = SaveHalo(StepBook("【オイサラバエル】樋口円香", 1020));
        var savedHalo = store.Load()["prefs"]?["halo_book"]?.AsArray();
        checks.Add(P, "halo_book_saved", Ok(haloSave) && savedHalo is { Count: 1 } && (int?)savedHalo[0]?["eval"] == 1020, haloSave + " " + savedHalo?.ToJsonString());
        var haloTwo = SaveHalo(StepBook("a", 1) + "," + StepBook("b", 2));
        checks.Add(P, "halo_book_refused:two", !Ok(haloTwo) && !haloTwo.Contains("请求过于频繁", StringComparison.Ordinal), haloTwo);
        var strayHalo = store.Sanitize(JsonNode.Parse("{\"schema_version\":1,\"prefs\":{\"halo_book\":[" + StepBook("x", 2001) + "]}}"));
        checks.Add(P, "halo_book_dropped_on_load", strayHalo["prefs"]?["halo_book"] is null, strayHalo.ToJsonString());
    }

    private static void EnginePhase(Checks checks, string output)
    {
        const string P = "E";
        ProfileInputPhase(checks, output);
        var root = UiPaths.RepoRoot();
        var python = Path.Combine(root, ".venv", "Scripts", "python.exe");
        var fake = Path.Combine(root, "tests", "fixtures", "ui", "fake_engine.py");
        checks.Add(P, "fake_engine_available", File.Exists(python) && File.Exists(fake));
        if (!File.Exists(python) || !File.Exists(fake)) { return; }
        var runs = Path.Combine(output, "engine-runs");
        const string TaskJson = "[{\"name\":\"Startup\",\"enabled\":true},{\"name\":\"Shop\",\"enabled\":true},{\"name\":\"MatchSkip\",\"enabled\":true},{\"name\":\"Training\",\"enabled\":false}]";
        var fast = new UiEngineLimits(StartupTimeoutMs: 20_000, CooperativeGraceMs: 20_000, EmergencyGraceMs: 2_000, ExitDrainMs: 1_000);

        (UiEngineSupervisor Supervisor, List<(string Name, JsonObject Data)> Events, bool Finished) Run(string scenario, UiEngineLimits limits,
            Action<UiEngineSupervisor, List<(string Name, JsonObject Data)>>? onTick = null, int timeoutMs = 60_000)
        {
            var supervisor = new UiEngineSupervisor(root, launch =>
            {
                var info = UiEngineSupervisor.NewStartInfo(root, python);
                foreach (var argument in new[] { "-X", "utf8", fake, "--scenario", scenario }) { info.ArgumentList.Add(argument); }
                UiEngineSupervisor.AddEngineArguments(info, launch);
                return info;
            }, limits: limits, runsRoot: Path.Combine(runs, scenario));
            var events = new List<(string, JsonObject)>();
            supervisor.Output += (name, data) => events.Add((name, (JsonObject)data.DeepClone()));
            supervisor.Start("offline_replay", JsonNode.Parse(TaskJson)!.AsArray(), null, null, Path.Combine(runs, "frames"));
            var watch = Stopwatch.StartNew();
            while (supervisor.Active && watch.ElapsedMilliseconds < timeoutMs)
            {
                supervisor.Tick();
                onTick?.Invoke(supervisor, events);
                Thread.Sleep(50);
            }
            return (supervisor, events, !supervisor.Active);
        }

        static JsonObject? Finished(List<(string Name, JsonObject Data)> events) =>
            events.LastOrDefault(item => item.Name == "run.update" && (string?)item.Data["state"] == "finished").Data;
        static Dictionary<string, string?> FinalStatuses(List<(string Name, JsonObject Data)> events)
        {
            // The finalize pass emits one run.update per task right before "finished"; the last one per task wins.
            var result = new Dictionary<string, string?>(StringComparer.Ordinal);
            foreach (var (name, data) in events)
            {
                if (name == "run.update" && (string?)data["task"] is { } task && data["status"] is not null) { result[task] = (string?)data["status"]; }
            }
            return result;
        }
        static Dictionary<string, string?> FinalReasons(List<(string Name, JsonObject Data)> events)
        {
            var result = new Dictionary<string, string?>(StringComparer.Ordinal);
            foreach (var (name, data) in events)
            {
                if (name == "run.update" && (string?)data["task"] is { } task && data["status"] is not null) { result[task] = (string?)data["reason"]; }
            }
            return result;
        }
        static bool LogsPathFree(List<(string Name, JsonObject Data)> events) =>
            events.Where(item => item.Name == "log.append").All(item => !((string?)item.Data["text"] ?? "").Contains(":\\", StringComparison.Ordinal));
        static bool StartedOnStartup(List<(string Name, JsonObject Data)> events) =>
            events.Any(item => item.Name == "run.update" && (string?)item.Data["task"] == "Startup" && (string?)item.Data["status"] == "running");

        // ok: every task succeeded in both the events and the summary.
        var ok = Run("ok", fast);
        var okStatus = FinalStatuses(ok.Events);
        checks.Add(P, "ok_finished", ok.Finished && (string?)Finished(ok.Events)?["outcome"] == "succeeded", Finished(ok.Events));
        checks.Add(P, "ok_task_statuses", okStatus.Count == 3 && okStatus.Values.All(item => item == "succeeded") && !okStatus.ContainsKey("Training"), okStatus);
        checks.Add(P, "ok_readings_forwarded", ok.Events.Count(item => item.Name == "readings.update") == 2
            && ok.Events.Any(item => item.Name == "readings.update" && (string?)item.Data["kind"] == "shop" && item.Data["readings"]?["observations"]?[0]?["money_after"]?.GetValue<long>() == 113456));
        checks.Add(P, "ok_logs_redact_paths", LogsPathFree(ok.Events));
        checks.Add(P, "ok_history_recorded", ok.Supervisor.History.Count == 1 && (string?)ok.Supervisor.History[0]["outcome"] == "succeeded"
            && Directory.EnumerateFiles(Path.Combine(runs, "ok"), "result.json", SearchOption.AllDirectories).Count() == 1);
        checks.Add(P, "ok_history_reloads", new UiEngineSupervisor(root, runsRoot: Path.Combine(runs, "ok")).History.Count == 1);
        checks.Add(P, "ok_profile_private_and_enabled_only", Directory.EnumerateFiles(Path.Combine(runs, "ok"), "profile.json", SearchOption.AllDirectories)
            .Select(File.ReadAllText).Single() is var profileText && profileText.Contains("\"Training\"", StringComparison.Ordinal) && !profileText.Contains("allow", StringComparison.Ordinal));

        var failOne = Run("fail_one", fast);
        var failStatus = FinalStatuses(failOne.Events);
        checks.Add(P, "fail_one_reported", (string?)Finished(failOne.Events)?["outcome"] == "failed"
            && failStatus.GetValueOrDefault("Shop") == "failed" && failStatus.GetValueOrDefault("Startup") == "succeeded", failStatus);
        // End to end through the summary reader: the failed row carries the first plain stop code of fail_reasons, the others none.
        var failReasons = FinalReasons(failOne.Events);
        checks.Add(P, "fail_one_stop_reason_forwarded", failReasons.GetValueOrDefault("Shop") == "maintenance"
            && failReasons.GetValueOrDefault("Startup") is null && failReasons.GetValueOrDefault("MatchSkip") is null
            && failOne.Supervisor.History[0]["tasks"] is JsonArray recorded
            && recorded.Any(item => (string?)item?["name"] == "Shop" && (string?)item?["reason"] == "maintenance"), failReasons);

        // Cooperative stop: the stop file is written, the child latches it; interrupted -> uncertain, the rest skipped.
        var stopSent = false;
        var stop = Run("stop", fast, (supervisor, events) =>
        {
            if (!stopSent && StartedOnStartup(events)) { stopSent = supervisor.RequestStop("cooperative"); }
        });
        var stopStatus = FinalStatuses(stop.Events);
        checks.Add(P, "stop_file_written", stopSent && Directory.EnumerateFiles(Path.Combine(runs, "stop"), "stop.flag", SearchOption.AllDirectories).Count() == 1);
        checks.Add(P, "stop_interrupted_is_uncertain", stopStatus.GetValueOrDefault("Startup") == "uncertain"
            && stopStatus.GetValueOrDefault("Shop") == "skipped" && stopStatus.GetValueOrDefault("MatchSkip") == "skipped", stopStatus);
        checks.Add(P, "stop_outcome_uncertain_not_success", (string?)Finished(stop.Events)?["outcome"] == "uncertain", Finished(stop.Events));
        checks.Add(P, "stop_emits_stopping", stop.Events.Any(item => item.Name == "run.update" && (string?)item.Data["state"] == "stopping"));

        var crash = Run("crash", fast);
        var crashStatus = FinalStatuses(crash.Events);
        checks.Add(P, "crash_is_uncertain", (string?)Finished(crash.Events)?["outcome"] == "uncertain"
            && crashStatus.Count == 3 && crashStatus.Values.All(item => item == "uncertain"), crashStatus);

        // A child that ignores the stop file is killed after the (short) grace; never retried.
        var hangSent = false;
        var hang = Run("hang", fast with { CooperativeGraceMs = 1_500 }, (supervisor, events) =>
        {
            if (!hangSent && StartedOnStartup(events)) { hangSent = supervisor.RequestStop("cooperative"); }
        }, timeoutMs: 30_000);
        checks.Add(P, "hang_killed_after_grace", hang.Finished && hangSent && (bool?)hang.Supervisor.History.FirstOrDefault()?["killed"] == true
            && (string?)Finished(hang.Events)?["outcome"] == "uncertain"
            && hang.Events.Any(item => item.Name == "log.append" && ((string?)item.Data["text"] ?? "").Contains("强制终止", StringComparison.Ordinal)), Finished(hang.Events));
        checks.Add(P, "hang_all_uncertain", FinalStatuses(hang.Events).Values.All(item => item == "uncertain"), FinalStatuses(hang.Events));

        // Whole-run deadline: no stop from the user; the supervisor stops, then kills, then reports uncertain.
        var deadline = Run("hang", fast with { DefaultTaskTimeoutS = 0.3, RunMarginMs = 500, CooperativeGraceMs = 1_000 }, timeoutMs: 30_000);
        checks.Add(P, "deadline_enforced", deadline.Finished && (string?)Finished(deadline.Events)?["outcome"] == "uncertain"
            && ((string?)Finished(deadline.Events)?["reason"] ?? "").Contains("超时", StringComparison.Ordinal), Finished(deadline.Events));
        // 2026-10-26 W.I.N.G. 连跑: the run count (1–5) of the AutoProduce row extends the W.I.N.G. deadline; anything else is 1.
        static JsonArray Rows(string runs) => JsonNode.Parse("[{\"name\":\"AutoProduce\",\"enabled\":true,\"option\":{" + runs + "}}]")!.AsArray();
        checks.Add(P, "wing_runs_option", UiEngineSupervisor.WingRuns(Rows("\"Produce次数\":\"3\"")) == 3
            && UiEngineSupervisor.WingRuns(Rows("\"Produce次数\":\"9\"")) == 1 && UiEngineSupervisor.WingRuns(Rows("")) == 1
            && UiEngineSupervisor.WingRuns(new JsonArray()) == 1);
        // 2026-10-26 推荐进度条: only the recommender's exact progress line is read; anything else is ignored.
        checks.Add(P, "team_propose_progress_line", UiTeamPropose.ParseProgress("{\"progress\": [3, 9]}") is [3, 9]
            && UiTeamPropose.ParseProgress("{\"progress\": [9, 3]}") is null && UiTeamPropose.ParseProgress("{\"progress\": [0, 0]}") is null
            && UiTeamPropose.ParseProgress("{\"engine\": \"true_end\"}") is null && UiTeamPropose.ParseProgress(null) is null);

        var refuse = Run("refuse", fast);
        var refuseFinal = Finished(refuse.Events);
        checks.Add(P, "refuse_nothing_ran", (string?)refuseFinal?["outcome"] == "refused"
            && FinalStatuses(refuse.Events).Values.All(item => item == "skipped"), refuseFinal);
        checks.Add(P, "refuse_reason_redacted", ((string?)refuseFinal?["reason"] ?? "").Contains("〈路径〉", StringComparison.Ordinal)
            && !((string?)refuseFinal?["reason"] ?? "").Contains(":\\", StringComparison.Ordinal), refuseFinal);

        var mismatch = Run("mismatch", fast);
        checks.Add(P, "mismatch_is_uncertain", FinalStatuses(mismatch.Events).GetValueOrDefault("Startup") == "uncertain"
            && (string?)Finished(mismatch.Events)?["outcome"] == "uncertain", FinalStatuses(mismatch.Events));

        var garbage = Run("garbage", fast);
        checks.Add(P, "garbage_events_uncertain", (string?)Finished(garbage.Events)?["outcome"] == "uncertain"
            && FinalStatuses(garbage.Events).Values.All(item => item != "succeeded"), FinalStatuses(garbage.Events));

        // Guards on Start and on the default command line.
        var guard = new UiEngineSupervisor(root, runsRoot: Path.Combine(runs, "guard"));
        checks.Add(P, "start_refuses_live_mode", Throws(() => guard.Start("live", JsonNode.Parse(TaskJson)!.AsArray(), null, "s", null)));
        checks.Add(P, "start_refuses_no_enabled", Throws(() => guard.Start("offline_replay", JsonNode.Parse("[{\"name\":\"Startup\",\"enabled\":false}]")!.AsArray(), null, null, "f")));
        checks.Add(P, "start_refuses_two_sources", Throws(() => guard.Start("offline_replay", JsonNode.Parse(TaskJson)!.AsArray(), null, "s", "f")));
        checks.Add(P, "runs_root_must_be_private", Throws(() => new UiEngineSupervisor(root, runsRoot: Path.Combine(root, "ui"))));
        var launch = new UiEngineLaunch("ui-x", runs, "p.json", "e.jsonl", "s.flag", "m.json", "session.json", null);
        var arguments = File.Exists(python) ? guard.DefaultCommand(launch).ArgumentList.ToList() : new List<string>();
        checks.Add(P, "default_command_read_only", arguments.Any(item => item.EndsWith("ui_engine.py", StringComparison.Ordinal)) && arguments.Contains("--session")
            && !arguments.Any(item => item is "--live" or "--simulate-input" || item.StartsWith("--allow", StringComparison.Ordinal)), arguments);
        // 2026-10-11 supervised W.I.N.G.: --wing only for that mode, the spend flag only when the launch carries it.
        var wingLaunch = launch with { Wing = true };
        var wingArgs = File.Exists(python) ? guard.DefaultCommand(wingLaunch).ArgumentList.ToList() : new List<string>();
        var wingSpendArgs = File.Exists(python) ? guard.DefaultCommand(wingLaunch with { AllowProduceSpend = true }).ArgumentList.ToList() : new List<string>();
        checks.Add(P, "wing_command_arguments", wingArgs.Contains("--wing") && !wingArgs.Contains("--allow-produce-spend")
            && wingSpendArgs.Contains("--wing") && wingSpendArgs.Contains("--allow-produce-spend")
            && !wingSpendArgs.Any(item => item is "--live" or "--simulate-input"), wingSpendArgs);
        const string WingTasks = "[{\"name\":\"AutoProduce\",\"enabled\":true}]";
        checks.Add(P, "wing_start_needs_session", Throws(() => guard.Start(UiEngineSupervisor.WingMode, JsonNode.Parse(WingTasks)!.AsArray(), null, null, "f")));
        checks.Add(P, "wing_start_only_autoproduce", Throws(() => guard.Start(UiEngineSupervisor.WingMode,
            JsonNode.Parse("[{\"name\":\"AutoProduce\",\"enabled\":true},{\"name\":\"Startup\",\"enabled\":true}]")!.AsArray(), null, "s", null)));
        // ANN-17 supervised daily: --daily only for that mode, never together with --wing; needs a session, no W.I.N.G. row.
        var dailyArgs = File.Exists(python) ? guard.DefaultCommand(launch with { Daily = true }).ArgumentList.ToList() : new List<string>();
        checks.Add(P, "daily_command_arguments", (!File.Exists(python) || dailyArgs.Contains("--daily")) && !dailyArgs.Contains("--wing")
            && !arguments.Contains("--daily") && !wingArgs.Contains("--daily")
            && !dailyArgs.Any(item => item is "--live" or "--simulate-input" || item.StartsWith("--allow", StringComparison.Ordinal)), dailyArgs);
        checks.Add(P, "daily_start_needs_session", Throws(() => guard.Start(UiEngineSupervisor.DailyMode, JsonNode.Parse(TaskJson)!.AsArray(), null, null, "f")));
        checks.Add(P, "daily_start_without_wing", Throws(() => guard.Start(UiEngineSupervisor.DailyMode,
            JsonNode.Parse("[{\"name\":\"AutoProduce\",\"enabled\":true},{\"name\":\"Startup\",\"enabled\":true}]")!.AsArray(), null, "s", null)));
        checks.Add(P, "spend_flag_only_for_wing", Throws(() => guard.Start("read_only", JsonNode.Parse(TaskJson)!.AsArray(), null, "s", null, allowProduceSpend: true)));
        var gateHost = new JsonObject { ["pipeline_input_enabled"] = true, ["connected"] = true, ["host_guard_reason"] = null, ["click_cap"] = 1500, ["clicks_this_run"] = 0 };
        checks.Add(P, "wing_gate_ready", UiWingGate.Blocker(gateHost, false, false) is null);
        checks.Add(P, "wing_gate_blocks", UiWingGate.Blocker(gateHost, true, false) == "card_scan_active"
            && UiWingGate.Blocker(gateHost, false, true) == "other_input_activity"
            && UiWingGate.Blocker(new JsonObject { ["pipeline_input_enabled"] = false }, false, false) == "pipeline_input_disabled"
            && UiWingGate.Blocker(new JsonObject { ["pipeline_input_enabled"] = true, ["connected"] = true, ["click_cap"] = 1500, ["clicks_this_run"] = 1450 }, false, false) is null);
        checks.Add(P, "redact_paths", UiEngineSupervisor.Redact("x C:\\Users\\public\\b.json y \\\\server\\share z") == "x 〈路径〉 y 〈路径〉 z");
        checks.Add(P, "status_mapping", UiEngineSupervisor.MapTaskStatus("interrupted").Status == "uncertain" && UiEngineSupervisor.MapTaskStatus("timeout").Status == "uncertain"
            && UiEngineSupervisor.MapTaskStatus("error").Status == "failed" && UiEngineSupervisor.MapTaskStatus("whatever").Status == "uncertain");
        JsonObject hinted(string status, string page) => new() { ["status"] = status, ["layout_hint"] = new JsonObject { ["page"] = page } };
        checks.Add(P, "layout_hint_reason", UiEngineSupervisor.LayoutHint(hinted("timeout", "Shop.Premium"))!.Contains("（Shop.Premium）")
            && UiEngineSupervisor.LayoutHint(hinted("succeeded", "Shop.Premium")) is null
            && UiEngineSupervisor.LayoutHint(hinted("failed", "x<script>")) is null
            && UiEngineSupervisor.LayoutHint(new JsonObject { ["status"] = "failed" }) is null);
        JsonObject wingTask(string outcome, string? text) => new()
        {
            ["reports"] = new JsonArray(new JsonObject { ["kind"] = "resources" },
                new JsonObject { ["kind"] = "wing", ["outcome"] = outcome, ["stop_text"] = text })
        };
        checks.Add(P, "wing_stop_reason", UiTaskResult.WingReason(wingTask("produce_settings_mismatch", " 体力回復演出：应为 ON ")) == "produce_settings_mismatch：体力回復演出：应为 ON"
            && UiTaskResult.WingReason(wingTask("click_unavailable:home_minigame_menu", "x")) == "click_unavailable:home_minigame_menu"
            && UiTaskResult.WingReason(wingTask("screen_unstable", null)) == "screen_unstable"
            && UiTaskResult.WingReason(wingTask("running", "x")) is null
            && UiTaskResult.WingReason(wingTask("Bad Code", "x")) is null
            && UiTaskResult.WingReason(new JsonObject()) is null);
        // 2026-10-27: a failed ordinary task row shows its Pipeline stop code (the SC.FailTask reason in fail_reasons).
        JsonObject withReasons(string status, params string[] reasons) => new()
        {
            ["status"] = status,
            ["fail_reasons"] = new JsonArray(reasons.Select(item => (JsonNode)JsonValue.Create(item)!).ToArray())
        };
        var noReadings = Array.Empty<JsonObject>();
        (string Status, string? Reason) rowOf(string name, string raw, JsonObject task, params JsonObject[] readings) => UiEngineSupervisor.SummaryRow(name, task, raw, readings);
        var forwarded = rowOf("Startup", "failed", withReasons("failed", "near_miss:x", "maintenance"), noReadings);
        checks.Add(P, "stop_reason_forwarded", rowOf("Startup", "failed", withReasons("failed", "gacha_announcement_unhandled"), noReadings) == ("failed", "gacha_announcement_unhandled")
            && rowOf("Startup", "failed", withReasons("failed", "near_miss:SC.Shop.Premium.Guard/SC.Shop.Anchor@0.91", "maintenance", "fes_menu_open"), noReadings) == ("failed", "maintenance")
            && forwarded == ("failed", "maintenance"), forwarded);
        checks.Add(P, "stop_reason_plain_codes_only", new[]
            {
                "near_miss:SC.Shop.Premium.Guard/x@0.91", "click_refused:act:why", "weekly_tab_not_open:navigation_clicks_disabled", "unspecified",
                "Bad Code", "x<script>", "../x", "C:\\Users\\public\\b.json", "maintenance\n", "Maintenance", "9lives", "停止", "", new string('a', 65),
            }.All(item => rowOf("Startup", "failed", withReasons("failed", item), noReadings) == ("failed", null))
            && rowOf("Startup", "failed", withReasons("failed", new string('a', 64)), noReadings).Reason == new string('a', 64)
            && rowOf("Startup", "failed", new JsonObject { ["status"] = "failed" }, noReadings) == ("failed", null)
            && rowOf("Startup", "failed", new JsonObject { ["status"] = "failed", ["fail_reasons"] = "maintenance" }, noReadings) == ("failed", null)
            && rowOf("Startup", "failed", new JsonObject { ["status"] = "failed", ["fail_reasons"] = new JsonArray(7, null, true) }, noReadings) == ("failed", null));
        checks.Add(P, "stop_reason_only_failed", rowOf("Startup", "succeeded", withReasons("succeeded", "maintenance"), noReadings) == ("succeeded", null)
            && rowOf("Startup", "skipped", withReasons("skipped", "maintenance"), noReadings) == ("skipped", "未开始")
            && rowOf("Startup", "takeover", withReasons("takeover", "maintenance"), noReadings) == ("takeover", "需要人工接管")
            && rowOf("Startup", "timeout", withReasons("timeout", "maintenance"), noReadings) == ("uncertain", "任务超时，结果不确定")
            && rowOf("Startup", "uncertain", withReasons("uncertain", "maintenance"), noReadings) == ("uncertain", "结果不确定，需人工核对")
            && rowOf("Startup", "error", withReasons("error", "maintenance"), noReadings) == ("failed", "任务异常"));
        var hintedStop = withReasons("failed", "maintenance");
        hintedStop["layout_hint"] = new JsonObject { ["page"] = "Shop.Premium" };
        var shopOutcome = new JsonObject { ["task"] = "Shop", ["kind"] = "shop", ["readings"] = new JsonObject { ["outcome"] = "card_not_purchasable" } };
        var wingStop = withReasons("failed", "maintenance");
        wingStop["reports"] = new JsonArray(new JsonObject { ["kind"] = "wing", ["outcome"] = "screen_unstable", ["stop_text"] = null });
        checks.Add(P, "stop_reason_specific_wins", rowOf("Shop", "failed", hintedStop, noReadings).Reason!.Contains("（Shop.Premium）")
            && rowOf("Shop", "failed", withReasons("failed", "maintenance"), shopOutcome).Reason == "card_not_purchasable"
            && rowOf(UiEngineSupervisor.WingTask, "failed", wingStop, noReadings).Reason == "screen_unstable"
            && rowOf(UiEngineSupervisor.WingTask, "failed", withReasons("failed", "maintenance"), noReadings).Reason == "maintenance");

        // Router with an engine but no game host: read-only refused, offline replay needs a known frames id, bad options refused.
        var schema = UiTaskSchema.LoadFromUiFolder(UiPaths.OutputUiFolder());
        var store = new UiProfileStore(Path.Combine(output, "profile-engine.json"), schema);
        var replayRoot = Path.Combine(output, "replay");
        Directory.CreateDirectory(Path.Combine(replayRoot, "demo"));
        var routerEngine = new UiEngineSupervisor(root, launch2 =>
        {
            var info = UiEngineSupervisor.NewStartInfo(root, python);
            foreach (var argument in new[] { "-X", "utf8", fake, "--scenario", "ok" }) { info.ArgumentList.Add(argument); }
            UiEngineSupervisor.AddEngineArguments(info, launch2);
            return info;
        }, limits: fast, runsRoot: Path.Combine(runs, "router"));
        var router = new UiMessageRouter(UiProtocol.LoadEmbedded(), schema, store, engine: routerEngine, replayRoot: replayRoot);
        const string Home = "https://maa-ui.test/index.html";
        var seq = 0;
        string Req(string cmd, string paramsJson) => $"{{\"v\":1,\"id\":\"e-{++seq}\",\"cmd\":\"{cmd}\",\"params\":{paramsJson}}}";
        static JsonObject Reply(string? reply) => JsonNode.Parse(reply!)!.AsObject();
        var readOnly = Reply(router.Handle(Home, Req("run.start", "{\"mode\":\"read_only\",\"config\":{\"tasks\":[{\"name\":\"Startup\",\"enabled\":true}]},\"spend_flags\":[]}")));
        checks.Add(P, "router_read_only_needs_game_host", (bool)readOnly["ok"]! == false && ((string?)readOnly["error"] ?? "").Contains("游戏窗口", StringComparison.Ordinal), readOnly);
        var wingNoHost = Reply(router.Handle(Home, Req("run.start", "{\"mode\":\"supervised_wing\",\"confirmed\":true,\"config\":{\"tasks\":[{\"name\":\"AutoProduce\",\"enabled\":true}]},\"spend_flags\":[\"allow_produce_spend\"]}")));
        checks.Add(P, "router_wing_needs_game_host", (bool)wingNoHost["ok"]! == false && ((string?)wingNoHost["error"] ?? "").Contains("游戏窗口", StringComparison.Ordinal), wingNoHost);
        var badFrames = Reply(router.Handle(Home, Req("run.start", "{\"mode\":\"offline_replay\",\"frames_id\":\"missing\",\"config\":{\"tasks\":[{\"name\":\"Startup\",\"enabled\":true}]},\"spend_flags\":[]}")));
        checks.Add(P, "router_unknown_frames_refused", (bool)badFrames["ok"]! == false, badFrames);
        var badCase = Reply(router.Handle(Home, Req("run.start", "{\"mode\":\"offline_replay\",\"frames_id\":\"demo\",\"config\":{\"tasks\":[{\"name\":\"GradeFes\",\"enabled\":true,\"option\":{\"Grade难度\":\"C:\\\\x\"}}]},\"spend_flags\":[]}")));
        checks.Add(P, "router_unknown_case_refused", (bool)badCase["ok"]! == false && ((string?)badCase["error"] ?? "").Contains("未知", StringComparison.Ordinal), badCase);
        var idleStop = Reply(router.Handle(Home, Req("run.stop", "{\"kind\":\"cooperative\"}")));
        checks.Add(P, "router_stop_idle_not_accepted", (bool?)idleStop["result"]?["accepted"] == false, idleStop);
        var started = Reply(router.Handle(Home, Req("run.start", "{\"mode\":\"offline_replay\",\"frames_id\":\"demo\",\"config\":{\"tasks\":[{\"name\":\"Startup\",\"enabled\":true}]},\"spend_flags\":[\"allow_grade_spend\"]}")));
        checks.Add(P, "router_offline_replay_started", (bool)started["ok"]! && ((string?)started["result"]?["run_id"] ?? "").StartsWith("ui-", StringComparison.Ordinal), started);
        checks.Add(P, "router_page_spend_flag_ignored", router.LastRun is { } audit && audit.IgnoredPageFlags.SequenceEqual(new[] { "allow_grade_spend" }), router.LastRun);
        var second = Reply(router.Handle(Home, Req("run.start", "{\"mode\":\"offline_replay\",\"frames_id\":\"demo\",\"config\":{\"tasks\":[{\"name\":\"Startup\",\"enabled\":true}]},\"spend_flags\":[]}")));
        checks.Add(P, "router_single_run", (bool)second["ok"]! == false, second);
        var watch = Stopwatch.StartNew();
        while (routerEngine.Active && watch.ElapsedMilliseconds < 30_000) { router.Tick(); Thread.Sleep(50); }
        checks.Add(P, "router_run_finished", !routerEngine.Active && routerEngine.History.Count == 1 && (string?)routerEngine.History[0]["outcome"] == "succeeded");

        // Supervised W.I.N.G. on a (fake) game host: every refusal happens before the engine starts.
        var wingEngine = new UiEngineSupervisor(root, runsRoot: Path.Combine(runs, "wing"));
        string? wingBlock = "pipeline_input_disabled";
        string? wingSession = null;
        var wingRouter = new UiMessageRouter(UiProtocol.LoadEmbedded(), schema, store, engine: wingEngine,
            hostStatus: () => new JsonObject { ["connected"] = true }, sessionPath: () => wingSession, wingBlocker: () => wingBlock);
        string Wing(bool confirmed, string tasks, string flags) => (string?)Reply(wingRouter.Handle(Home, Req("run.start",
            $"{{\"mode\":\"supervised_wing\",\"confirmed\":{(confirmed ? "true" : "false")},\"config\":{{\"tasks\":{tasks}}},\"spend_flags\":{flags}}}")))["error"] ?? "";
        checks.Add(P, "wing_needs_confirmation", Wing(false, WingTasks, "[]").Contains("确认", StringComparison.Ordinal));
        checks.Add(P, "wing_alone", Wing(true, "[{\"name\":\"AutoProduce\",\"enabled\":true},{\"name\":\"Startup\",\"enabled\":true}]", "[]").Contains("单独", StringComparison.Ordinal));
        checks.Add(P, "wing_blocker_refuses", Wing(true, WingTasks, "[]").Contains("无法开始", StringComparison.Ordinal));
        wingBlock = null;
        checks.Add(P, "wing_needs_session", Wing(true, WingTasks, "[]").Contains("游戏画面", StringComparison.Ordinal));
        wingSession = Path.Combine(output, "no-session.json");
        checks.Add(P, "wing_page_spend_needs_profile", Wing(true, WingTasks, "[\"allow_produce_spend\"]").Contains("未在已保存", StringComparison.Ordinal)
            && !wingEngine.Active);
        // 2026-10-13: the saved W.I.N.G. row is unticked (it runs from 「Produce」); its spend option still derives the flag, and a
        // saved "No" of the retired 开局 choice is dropped by the sanitizer (Produce可开新局 has only the case Yes).
        var wingOnly = new HashSet<string>(StringComparer.Ordinal) { UiEngineSupervisor.WingTask };
        var unticked = store.Sanitize(JsonNode.Parse("{\"schema_version\":1,\"tasks\":[{\"name\":\"AutoProduce\",\"enabled\":false,\"option\":{\"Produce可开新局\":\"No\"}}]}"));
        checks.Add(P, "wing_spend_from_unticked_row", schema.DeriveSpendFlags(unticked, wingOnly, savedEnabledRequired: false).Contains(UiMessageRouter.WingSpendFlag)
            && !schema.DeriveSpendFlags(unticked, wingOnly).Contains(UiMessageRouter.WingSpendFlag)
            && unticked["tasks"]?[0]?["option"]?["Produce可开新局"] is null, unticked);

        // ANN-17 supervised daily on the same (fake) game host: refusals before the engine starts.
        string Daily(bool confirmed, string tasks, string flags) => (string?)Reply(wingRouter.Handle(Home, Req("run.start",
            $"{{\"mode\":\"supervised_daily\",\"confirmed\":{(confirmed ? "true" : "false")},\"config\":{{\"tasks\":{tasks}}},\"spend_flags\":{flags}}}")))["error"] ?? "";
        const string DailyTasks = "[{\"name\":\"Startup\",\"enabled\":true},{\"name\":\"Shop\",\"enabled\":true}]";
        checks.Add(P, "daily_needs_confirmation", Daily(false, DailyTasks, "[]").Contains("确认", StringComparison.Ordinal));
        checks.Add(P, "daily_without_wing", Daily(true, "[{\"name\":\"AutoProduce\",\"enabled\":true},{\"name\":\"Shop\",\"enabled\":true}]", "[]").Contains("单独", StringComparison.Ordinal));
        wingBlock = "other_input_activity";
        checks.Add(P, "daily_blocker_refuses", Daily(true, DailyTasks, "[]").Contains("无法开始", StringComparison.Ordinal));
        wingBlock = null;
        wingSession = null;
        checks.Add(P, "daily_needs_session", Daily(true, DailyTasks, "[]").Contains("游戏画面", StringComparison.Ordinal) && !wingEngine.Active);
    }

    private static bool Throws(Action action)
    {
        try { action(); return false; }
        catch (Exception exception) when (exception is InvalidOperationException or ArgumentException) { return true; }
    }
}
