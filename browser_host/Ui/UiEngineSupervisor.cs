using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>Bounded timeouts for one supervised engine run.</summary>
internal sealed record UiEngineLimits(
    int StartupTimeoutMs = 120_000,
    int CooperativeGraceMs = 90_000,
    int EmergencyGraceMs = 10_000,
    int ExitDrainMs = 3_000,
    int KillWaitMs = 5_000,
    long MaxEventBytes = 32L * 1024 * 1024,
    int MaxEventLines = 100_000,
    int MaxLineBytes = 16_384,
    int MaxSummaryBytes = 16 * 1024 * 1024,
    int MaxForwardedOutputLines = 200,
    int MaxNodeLogs = 2_000,
    double DefaultTaskTimeoutS = 2400,
    int RunMarginMs = 300_000,
    long MaxRunMs = 8L * 3600 * 1000);

/// <summary>Everything one run needs on disk; all paths live in a fresh private run folder and never reach the page.</summary>
internal sealed record UiEngineLaunch(string RunId, string RunFolder, string ProfilePath, string EventsPath, string StopPath,
    string SummaryPath, string? SessionPath, string? FramesPath, bool Wing = false, bool AllowProduceSpend = false, bool Daily = false);

/// <summary>
/// 2026-10-11 readiness gate for the supervised W.I.N.G. run from the 任务 page (run.start mode supervised_wing), the
/// same host conditions as 「扫描持有卡」 minus the card-scan action readiness: pipeline input on, game host connected,
/// no host guard, no card scan and no clicks from another program in the last seconds (2026-10-28: no
/// click caps, no input-budget check). Null = nothing blocks a start; the launcher still enforces the policy and action
/// table.
/// </summary>
internal static class UiWingGate
{

    internal static string? Blocker(JsonObject host, bool cardScanActive, bool recentExternalInput)
    {
        if (host["pipeline_input_enabled"]?.GetValue<bool>() != true) { return "pipeline_input_disabled"; }
        if (host["connected"]?.GetValue<bool>() != true) { return "host_not_connected"; }
        if (host["host_guard_reason"] is not null) { return "host_guard_active"; }
        if (cardScanActive) { return "card_scan_active"; }
        if (recentExternalInput) { return "other_input_activity"; }
        return null;
    }
}

/// <summary>
/// U2 engine supervisor: starts tools/ui_engine.py (profile -> ui_schema.profile_to_run_config -> run_tasks, read-only)
/// as a child process with a private --events JSONL and --stop-file, tails the events into UI events, and reports each
/// task's result only from the events and the run summary (both must agree for 成功). 停止 creates the stop file; a
/// child that does not stop within a bounded grace is killed and the run is shown as uncertain. Nothing is ever retried
/// and no spend or live-input flag is ever passed, except the supervised W.I.N.G. run (2026-10-11, mode supervised_wing:
/// --wing, and --allow-produce-spend only when the router derived and the page confirmed it). Ticked on the UI thread.
/// </summary>
internal sealed partial class UiEngineSupervisor : IDisposable
{
    internal const string EngineScript = "tools/ui_engine.py";
    internal const int MaxHistory = 20;
    private const int MaxRunReadings = 200;
    private static readonly HashSet<string> TaskStatuses = new(StringComparer.Ordinal)
    { "pending", "running", "succeeded", "skipped", "failed", "uncertain", "guard_refused", "takeover" };

    private readonly string _repoRoot;
    private readonly string _runsRoot;
    private readonly Func<UiEngineLaunch, ProcessStartInfo> _command;
    private readonly Func<long> _clockMs;
    private readonly UiEngineLimits _limits;
    private readonly List<JsonObject> _history = new();
    private Run? _run;

    [GeneratedRegex("^[A-Za-z][A-Za-z0-9_.-]{0,159}$")] private static partial Regex Identifier();
    [GeneratedRegex("^[0-9a-f]{32}$")] private static partial Regex PythonRunId();
    [GeneratedRegex(@"(?:[A-Za-z]:[\\/]|\\\\)[^\s""'<>|]*")] private static partial Regex PathLike();
    [GeneratedRegex("^ui-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")] private static partial Regex UiRunId();

    private readonly string _outputGameAssets;

    internal UiEngineSupervisor(string repoRoot, Func<UiEngineLaunch, ProcessStartInfo>? command = null,
        Func<long>? clockMs = null, UiEngineLimits? limits = null, string? runsRoot = null, string? outputGameAssets = null)
    {
        _outputGameAssets = Path.GetFullPath(outputGameAssets ?? Path.Combine(UiPaths.OutputUiFolder(), "game-assets"));
        _repoRoot = Path.GetFullPath(repoRoot);
        _runsRoot = Path.GetFullPath(runsRoot ?? Path.Combine(_repoRoot, "dev_assets", "private", "ui", "runs"));
        var privateRoot = Path.GetFullPath(Path.Combine(_repoRoot, "dev_assets", "private")) + Path.DirectorySeparatorChar;
        if (!_runsRoot.StartsWith(privateRoot, StringComparison.OrdinalIgnoreCase))
        { throw new ArgumentException("运行目录必须位于 dev_assets/private 内。"); }
        _command = command ?? DefaultCommand;
        _clockMs = clockMs ?? (() => Stopwatch.GetTimestamp() * 1000 / Stopwatch.Frequency);
        _limits = limits ?? new UiEngineLimits();
        LoadHistory();
    }

    /// <summary>(event name, data) for the page; names are protocol events (run.update / log.append / readings.update).</summary>
    internal event Action<string, JsonObject>? Output;

    internal bool Active => _run is not null;
    internal string? ActiveRunId => _run?.Launch.RunId;
    internal IReadOnlyList<JsonObject> History => _history;
    internal string RunsRoot => _runsRoot;

    /// <summary>Default child: the project's own venv Python running tools/ui_engine.py. Never --simulate-input; --wing only for supervised_wing.</summary>
    internal ProcessStartInfo DefaultCommand(UiEngineLaunch launch)
    {
        var python = Path.Combine(_repoRoot, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python)) { throw new InvalidOperationException("找不到项目 Python 环境（.venv）。"); }
        var info = NewStartInfo(_repoRoot, python);
        foreach (var argument in new[] { "-X", "utf8", Path.Combine(_repoRoot, EngineScript) }) { info.ArgumentList.Add(argument); }
        AddEngineArguments(info, launch);
        return info;
    }

    internal static ProcessStartInfo NewStartInfo(string repoRoot, string executable)
    {
        var info = new ProcessStartInfo(executable)
        {
            WorkingDirectory = repoRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = new UTF8Encoding(false),
            StandardErrorEncoding = new UTF8Encoding(false),
        };
        info.Environment["PYTHONIOENCODING"] = "utf-8";
        // UTF-8 mode: default text I/O stays UTF-8 on a Chinese or Japanese Windows code page (936/932), not only on 1252.
        info.Environment["PYTHONUTF8"] = "1";
        info.Environment["PYTHONUNBUFFERED"] = "1";
        return info;
    }

    internal static void AddEngineArguments(ProcessStartInfo info, UiEngineLaunch launch)
    {
        foreach (var argument in new[] { "--profile", launch.ProfilePath, "--events", launch.EventsPath, "--stop-file", launch.StopPath, "--summary", launch.SummaryPath })
        { info.ArgumentList.Add(argument); }
        if (launch.SessionPath is not null) { info.ArgumentList.Add("--session"); info.ArgumentList.Add(launch.SessionPath); }
        else { info.ArgumentList.Add("--offline-frames"); info.ArgumentList.Add(launch.FramesPath!); }
        // Supervised W.I.N.G. (run.start supervised_wing): ui_engine builds the CLI's Wing config and runs it live; the
        // spend flag only when the router derived it from the saved profile and the page confirmed it for this run.
        if (launch.Wing && launch.SessionPath is not null)
        {
            info.ArgumentList.Add("--wing");
            if (launch.AllowProduceSpend) { info.ArgumentList.Add("--allow-produce-spend"); }
        }
        // ANN-17 supervised daily (run.start supervised_daily): ui_engine runs the ticked tasks live as one supervised
        // sequence; the spend actions come only from the ticked tasks' own spend options in the run profile.
        else if (launch.Daily && launch.SessionPath is not null) { info.ArgumentList.Add("--daily"); }
    }

    internal const string WingMode = "supervised_wing";
    internal const string DailyMode = "supervised_daily";
    internal const string WingTask = "AutoProduce";
    /// <summary>run_tasks names the supervised entry "Wing"; the page knows the row as AutoProduce.</summary>
    private const string WingEngineTask = "Wing";
    // 2026-10-11 review P1-4: tools/ui_engine.py loops W.I.N.G. segments (each <=60 min, 35 s apart, total <=210 min).
    private const double WingTaskTimeoutS = 4 * 3600;
    // 2026-10-26 W.I.N.G. 连跑 (「Produce次数」 1–5, sanitised against the schema): each further run adds 2 h, the whole run
    // stays under MaxRunMs (tools/ui_engine.py WING_EXTRA_RUN_MINUTES / WING_LOOP_MAX_MINUTES end the loop before that).
    private const double WingExtraRunS = 2 * 3600;
    // 2026-11-19 CollaboFes waits on home for 満員 / Pass recovery: tools/run_tasks.py COLLABO_TASK_TIMEOUT_S.
    private const string CollaboTask = "CollaboFes";
    private const double CollaboTaskTimeoutS = 4 * 3600;
    internal const string WingRunsOption = "Produce次数";

    /// <summary>Planned W.I.N.G. runs of the (sanitised) task list: the AutoProduce row's 「Produce次数」, 1 when absent.</summary>
    internal static int WingRuns(JsonArray tasks)
    {
        var row = tasks.FirstOrDefault(item => (string?)item?["name"] == WingTask);
        var raw = row?["option"]?[WingRunsOption]?.GetValue<string>();
        return int.TryParse(raw, NumberStyles.None, CultureInfo.InvariantCulture, out var runs) && runs is >= 1 and <= 5 ? runs : 1;
    }

    /// <summary>
    /// Starts one run. <paramref name="tasks"/> is already sanitised against the schema (names / enabled / option cases).
    /// Exactly one of <paramref name="sessionPath"/> (read-only against the host) and <paramref name="framesPath"/> (offline).
    /// </summary>
    internal string Start(string mode, JsonArray tasks, double? taskTimeoutS, string? sessionPath, string? framesPath,
        bool allowProduceSpend = false, JsonObject? ownedCards = null)
    {
        if (_run is not null) { throw new InvalidOperationException("已有运行进行中。"); }
        if (mode is not ("read_only" or "offline_replay" or WingMode or DailyMode)) { throw new InvalidOperationException("不支持的运行方式。"); }
        if ((sessionPath is null) == (framesPath is null)) { throw new InvalidOperationException("运行来源无效。"); }
        var enabled = tasks.Where(item => item?["enabled"]?.GetValue<bool>() == true).Select(item => (string)item!["name"]!).ToList();
        if (enabled.Count == 0) { throw new InvalidOperationException("没有启用的任务。"); }
        var wing = mode == WingMode;
        if (wing && (sessionPath is null || enabled.Count != 1 || enabled[0] != WingTask))
        { throw new InvalidOperationException("W.I.N.G. 需要单独运行，且只能在游戏窗口中运行。"); }
        if (allowProduceSpend && !wing) { throw new InvalidOperationException("体力授权只用于 W.I.N.G.。"); }
        var daily = mode == DailyMode;
        if (daily && (sessionPath is null || enabled.Contains(WingTask)))
        { throw new InvalidOperationException("实机日常只能在游戏窗口中运行，且不含 W.I.N.G.。"); }
        if (wing) { taskTimeoutS = WingTaskTimeoutS + (WingRuns(tasks) - 1) * WingExtraRunS; }

        var stamp = DateTime.Now.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture);
        var runId = $"ui-{stamp}-{Convert.ToHexString(System.Security.Cryptography.RandomNumberGenerator.GetBytes(3)).ToLowerInvariant()}";
        var folder = Path.Combine(_runsRoot, runId);
        Directory.CreateDirectory(_runsRoot);
        Directory.CreateDirectory(folder);
        var launch = new UiEngineLaunch(runId, folder, Path.Combine(folder, "profile.json"), Path.Combine(folder, "events.jsonl"),
            Path.Combine(folder, "stop.flag"), Path.Combine(folder, "summary.json"), sessionPath, framesPath, wing, wing && allowProduceSpend, daily);
        var profile = new JsonObject { ["schema_version"] = 1, ["tasks"] = tasks.DeepClone() };
        // Wing modelling and the daily Auto 自动编队 use only the saved, sanitised ownership snapshot; other engine runs
        // still carry tasks only.
        if ((wing || daily) && ownedCards is not null) { profile["owned_cards"] = UiProfileStore.SanitizeOwnedCards(ownedCards); }
        if (taskTimeoutS is { } timeout) { profile["task_timeout_s"] = timeout; }
        using (var stream = new FileStream(launch.ProfilePath, FileMode.CreateNew, FileAccess.Write))
        { stream.Write(Encoding.UTF8.GetBytes(profile.ToJsonString(UiMessageRouter.JsonOptions))); }

        var info = _command(launch);
        var process = new Process { StartInfo = info, EnableRaisingEvents = false };
        var run = new Run(launch, mode, enabled, _clockMs(), DateTimeOffset.Now)
        {
            Aliases = wing ? new Dictionary<string, string>(StringComparer.Ordinal) { [WingEngineTask] = WingTask } : new Dictionary<string, string>(StringComparer.Ordinal),
            StartUtc = DateTime.UtcNow,
            DeadlineMs = Math.Min(_limits.MaxRunMs, (long)((enabled.Count * (taskTimeoutS ?? _limits.DefaultTaskTimeoutS)
                + (enabled.Contains(CollaboTask) ? Math.Max(0, CollaboTaskTimeoutS - (taskTimeoutS ?? _limits.DefaultTaskTimeoutS)) : 0)) * 1000)
                + _limits.RunMarginMs),
        };
        process.OutputDataReceived += (_, line) => { if (line.Data is not null && run.Stdout.Count < 10_000) { run.Stdout.Enqueue(line.Data); } };
        process.ErrorDataReceived += (_, line) => { if (line.Data is not null && run.Stderr.Count < 10_000) { run.Stderr.Enqueue(line.Data); } };
        try { if (!process.Start()) { throw new InvalidOperationException("引擎进程无法启动。"); } }
        catch (System.ComponentModel.Win32Exception exception) { process.Dispose(); throw new InvalidOperationException("引擎进程无法启动：" + exception.NativeErrorCode); }
        try { process.StandardInput.Close(); } catch (IOException) { }
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        run.Process = process;
        _run = run;
        Emit("run.update", new JsonObject { ["run_id"] = runId, ["state"] = "starting", ["task"] = null });
        Log(wing || daily ? "warn" : "info", null, "run", wing
            ? $"开始：W.I.N.G.（实机，会点击游戏；{(launch.AllowProduceSpend ? "没有进行中的一局时会开新一局，消耗体力 30" : "只接着跑进行中的一局，不开新一局")}）。请不要触碰游戏画面。"
            : daily
            ? $"开始：{enabled.Count} 个日常任务（实机，按方案顺序点击游戏；只有勾选且打开消耗选项的任务会消耗）。请不要触碰游戏画面。"
            : $"开始：{enabled.Count} 个任务（{(mode == "offline_replay" ? "离线回放" : "识别")}，不点击、不消耗）");
        return runId;
    }

    /// <summary>停止: creates the run's stop file (latched by run_tasks). Emergency shortens the kill grace and marks the run uncertain.</summary>
    internal bool RequestStop(string kind)
    {
        var run = _run;
        if (run is null || run.Finalized) { return false; }
        var emergency = kind == "emergency";
        if (emergency) { run.Emergency = true; }
        if (run.StopRequestedMs is null || emergency)
        {
            run.StopRequestedMs ??= _clockMs();
            run.GraceMs = Math.Min(run.GraceMs ?? long.MaxValue, emergency ? _limits.EmergencyGraceMs : _limits.CooperativeGraceMs);
            if (!TouchStopFile(run))
            {
                Log("error", null, "stop", "无法写入停止文件；将在宽限期后终止引擎，结果不确定");
            }
            Emit("run.update", new JsonObject { ["run_id"] = run.Launch.RunId, ["state"] = "stopping", ["task"] = null, ["reason"] = emergency ? "emergency" : "cooperative" });
            Log(emergency ? "error" : "warn", null, "stop", emergency
                ? "紧急停止：已请求整轮停止；若引擎未及时退出将被终止，本次结果需人工核对"
                : "已请求停止：当前任务按中断结算，后续任务不再开始");
        }
        return true;
    }

    private static bool TouchStopFile(Run run)
    {
        try
        {
            using var _ = new FileStream(run.Launch.StopPath, FileMode.OpenOrCreate, FileAccess.Write, FileShare.ReadWrite);
            run.StopFileWritten = true;
            return true;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { return false; }
    }

    /// <summary>Called periodically on the UI thread: tail events, forward output, enforce timeouts, finalize.</summary>
    internal void Tick()
    {
        var run = _run;
        if (run is null) { return; }
        var now = _clockMs();
        ReadEvents(run);
        DrainOutput(run);
        var process = run.Process!;
        bool exited;
        try { exited = process.HasExited; }
        catch (InvalidOperationException) { exited = true; }
        if (exited)
        {
            if (run.ExitedMs is null)
            {
                run.ExitedMs = now;
                try { process.WaitForExit(); run.ExitCode = process.ExitCode; } catch (InvalidOperationException) { run.ExitCode = null; }
            }
            // Give the event tail a short, bounded window to see the last complete lines, then settle.
            if (run.SawRunFinished || now - run.ExitedMs >= _limits.ExitDrainMs)
            {
                ReadEvents(run);
                DrainOutput(run);
                Finalize(run);
            }
            return;
        }
        if (!run.SawRunStarted && run.StopRequestedMs is null && now - run.StartedMs > _limits.StartupTimeoutMs)
        {
            run.TimeoutReason = "引擎启动超时";
            Log("error", null, "timeout", "引擎在限定时间内没有开始运行，正在停止");
            RequestStop("cooperative");
        }
        else if (run.StopRequestedMs is null && now - run.StartedMs > run.DeadlineMs)
        {
            run.TimeoutReason = "整轮运行超时";
            Log("error", null, "timeout", "整轮运行超过时限，正在停止");
            RequestStop("cooperative");
        }
        if (run.StopRequestedMs is { } stopAt && !run.Killed && now - stopAt > run.GraceMs)
        {
            run.Killed = true;
            Log("error", null, "kill", "引擎未在宽限期内退出，已强制终止；本次结果不确定，需人工核对，不会自动重试");
            try { process.Kill(entireProcessTree: true); process.WaitForExit(_limits.KillWaitMs); }
            catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception or NotSupportedException)
            { Log("error", null, "kill", "强制终止失败：" + exception.GetType().Name); }
        }
    }

    // ------------------------------------------------------------------ events
    private void ReadEvents(Run run)
    {
        if (run.EventsBroken is not null || !File.Exists(run.Launch.EventsPath)) { return; }
        try
        {
            using var stream = new FileStream(run.Launch.EventsPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            if (stream.Length < run.EventOffset) { BreakEvents(run, "事件文件被截断"); return; }
            stream.Seek(run.EventOffset, SeekOrigin.Begin);
            var buffer = new byte[64 * 1024];
            int read;
            var budget = 4 * 1024 * 1024;  // per tick, keeps the UI thread responsive
            while (budget > 0 && (read = stream.Read(buffer, 0, Math.Min(buffer.Length, budget))) > 0)
            {
                budget -= read;
                run.EventOffset += read;
                run.EventBytes += read;
                if (run.EventBytes > _limits.MaxEventBytes) { BreakEvents(run, "事件流超过容量上限"); return; }
                for (var index = 0; index < read; index++)
                {
                    var b = buffer[index];
                    if (b != (byte)'\n')
                    {
                        if (run.Partial.Count >= _limits.MaxLineBytes) { BreakEvents(run, "事件行过长"); return; }
                        run.Partial.Add(b);
                        continue;
                    }
                    var line = Encoding.UTF8.GetString(run.Partial.ToArray());
                    run.Partial.Clear();
                    if (++run.EventLines > _limits.MaxEventLines) { BreakEvents(run, "事件行数超过上限"); return; }
                    HandleEventLine(run, line);
                    if (run.EventsBroken is not null) { return; }
                }
            }
        }
        catch (IOException) { /* writer holds the file; retry next tick */ }
        catch (UnauthorizedAccessException) { BreakEvents(run, "事件文件不可读"); }
    }

    private void BreakEvents(Run run, string reason)
    {
        if (run.EventsBroken is not null) { return; }
        run.EventsBroken = reason;
        Log("error", null, "events", $"运行事件无效（{reason}），本次结果按不确定处理");
        if (!run.Finalized && run.Process is { } process)
        {
            bool alive;
            try { alive = !process.HasExited; } catch (InvalidOperationException) { alive = false; }
            if (alive) { RequestStop("cooperative"); }
        }
    }

    internal void HandleEventLine(Run run, string line)
    {
        JsonObject row;
        try { row = JsonNode.Parse(line, documentOptions: new JsonDocumentOptions { MaxDepth = 16 }) as JsonObject ?? throw new JsonException(); }
        catch (JsonException) { BreakEvents(run, "事件不是 JSON 对象"); return; }
        if (Int(row["version"]) != 1) { BreakEvents(run, "事件版本不符"); return; }
        var pythonRunId = Str(row["run_id"]);
        if (pythonRunId is null || !PythonRunId().IsMatch(pythonRunId)) { BreakEvents(run, "事件缺少运行 ID"); return; }
        if (run.PythonRunId is null) { run.PythonRunId = pythonRunId; }
        else if (run.PythonRunId != pythonRunId) { BreakEvents(run, "事件运行 ID 不一致"); return; }
        if (Int(row["sequence"]) is not { } sequence || sequence != run.LastSequence + 1) { BreakEvents(run, "事件序号不连续"); return; }
        run.LastSequence = sequence;
        var name = Str(row["event"]);
        var task = TaskOf(run, Str(row["task"]));
        var id = run.Launch.RunId;
        switch (name)
        {
            case "run.started":
                run.SawRunStarted = true;
                Emit("run.update", new JsonObject { ["run_id"] = id, ["state"] = "running", ["task"] = null });
                break;
            case "task.started":
                if (task is null) { break; }
                run.TasksStarted.Add(task);
                Emit("run.update", new JsonObject { ["run_id"] = id, ["state"] = "running", ["task"] = task, ["status"] = "running", ["reason"] = null });
                Log("info", task, "task_start", "开始");
                break;
            case "task.finished":
            {
                if (task is null) { break; }
                var raw = Str(row["status"]) ?? "unknown";
                var (status, reason) = MapTaskStatus(raw);
                if (row["reports_complete"] is JsonValue complete && complete.TryGetValue<bool>(out var ok) && !ok)
                { (status, reason) = ("uncertain", "报告不完整，需人工核对"); }
                if (raw == "skipped") { reason = Str(row["reason"]) == "run_stopped" ? "已停止，未开始" : "未开始"; }
                reason = UiTaskResult.Reason(task, status, reason, run.Readings);
                run.EventStatus[task] = (status, raw);
                // Live display only; the final result is reconciled with the summary when the run ends.
                Emit("run.update", new JsonObject { ["run_id"] = id, ["state"] = "running", ["task"] = task, ["status"] = status, ["reason"] = reason });
                Log(status switch { "failed" => "error", "uncertain" => "warn", _ => "info" }, task, "task_end", $"结束：{StatusText(status)}{(reason is null ? "" : "（" + reason + "）")}");
                break;
            }
            case "readings":
            {
                var kind = Str(row["kind"]);
                if (row["readings"] is not JsonObject readings || kind is null || !Identifier().IsMatch(kind)) { break; }
                var copy = BoundedCopy(readings, 4096);
                if (copy is null) { break; }
                var data = new JsonObject
                {
                    ["run_id"] = id, ["task"] = task, ["kind"] = kind, ["readings"] = copy,
                    ["mode"] = run.Mode,
                    ["ts"] = DateTimeOffset.Now.ToString("yyyy-MM-ddTHH:mm:ss.fffzzz", CultureInfo.InvariantCulture),
                };
                if (run.Readings.Count < MaxRunReadings) { run.Readings.Add((JsonObject)data.DeepClone()); }
                Emit("readings.update", data);
                Log("debug", task, "readings", "读数：" + kind);
                break;
            }
            case "node":
            {
                var node = Str(row["node"]);
                var state = Str(row["status"]);
                if (node is null || !Identifier().IsMatch(node) || state is not ("succeeded" or "failed")) { break; }
                if (run.NodeLogs++ >= _limits.MaxNodeLogs) { break; }
                Log(state == "failed" ? "warn" : "debug", task, "node", $"{node} {(state == "failed" ? "未通过" : "通过")}");
                break;
            }
            case "click.request":
            case "click.result":
            {
                var action = Str(row["action"]);
                var result = Str(row["result"]);
                var safeAction = action is not null && Identifier().IsMatch(action) ? action : "?";
                var safeResult = result is not null && Identifier().IsMatch(result) ? result : "?";
                Log(safeResult is "sent" or "intent" ? "info" : "warn", task, "click", $"点击 {safeAction}：{safeResult}");
                break;
            }
            case "refusal":
                Log("warn", task, "refusal", "任务报告拒绝或失败");
                break;
            case "run.stop_requested":
                run.EngineStopSeen = true;
                Emit("run.update", new JsonObject { ["run_id"] = id, ["state"] = "stopping", ["task"] = null, ["reason"] = run.Emergency ? "emergency" : "cooperative" });
                Log("warn", null, "stop", "引擎已锁存停止：" + (Str(row["reason"]) is { } why && Identifier().IsMatch(why) ? why : "unknown"));
                break;
            case "run.finished":
                run.SawRunFinished = true;
                run.EngineFinishStatus = Str(row["status"]);
                break;
        }
    }

    /// <summary>The page's task name for an engine task name (identifier, known to the run; Wing -> AutoProduce).</summary>
    private static string? TaskOf(Run run, string? raw)
    {
        if (raw is null || !Identifier().IsMatch(raw)) { return null; }
        var name = run.Aliases.TryGetValue(raw, out var alias) ? alias : raw;
        return run.Tasks.Contains(name) ? name : null;
    }

    /// <summary>
    /// A failed/timed-out task whose failure snapshot recognised no page but one page guard missed by a single template
    /// anchor (run_tasks <c>layout_hint</c>, agent/core/guard_diagnosis.py): the game layout probably changed. A task's own
    /// outcome still wins (UiTaskResult.Reason).
    /// </summary>
    internal static string? LayoutHint(JsonObject task)
    {
        if (Str(task["status"]) is not ("failed" or "timeout" or "error") || task["layout_hint"] is not JsonObject hint
            || Str(hint["page"]) is not { } page || !Regex.IsMatch(page, @"\A[A-Za-z][A-Za-z0-9.]{0,60}\z")) { return null; }
        return $"画面与已知布局不一致（{page}），可能是游戏更新；截图已存入运行日志";
    }

    internal static (string Status, string? Reason) MapTaskStatus(string raw) => raw switch
    {
        "succeeded" => ("succeeded", null),
        "failed" => ("failed", null),
        "takeover" => ("takeover", "需要人工接管"),
        "guard_refused" => ("guard_refused", "当前画面不符合任务"),
        "uncertain" => ("uncertain", "结果不确定，需人工核对"),
        "error" => ("failed", "任务异常"),
        "skipped" => ("skipped", null),
        "timeout" => ("uncertain", "任务超时，结果不确定"),
        "interrupted" => ("uncertain", "已中断，结果不确定"),
        // W.I.N.G. segment loop ended at its time/segment cap on a resumable boundary: not a failure.
        "paused" => ("succeeded", "本段已完成，W.I.N.G. 尚未结束：再次开始会接着跑"),
        _ => ("uncertain", "未知结果"),
    };

    private static string StatusText(string status) => status switch
    {
        "succeeded" => "成功", "failed" => "失败", "skipped" => "跳过", "uncertain" => "不确定",
        "takeover" => "需要你操作", "guard_refused" => "未执行", _ => status,
    };

    // ------------------------------------------------------------------ output
    private void DrainOutput(Run run)
    {
        while (run.Stdout.TryDequeue(out var line))
        {
            var trimmed = line.Trim();
            if (trimmed.StartsWith('{') && trimmed.EndsWith('}')) { run.LastStdoutJson = trimmed.Length > 64 * 1024 ? null : trimmed; }
        }
        while (run.Stderr.TryDequeue(out var line))
        {
            if (string.IsNullOrWhiteSpace(line)) { continue; }
            if (line.Contains("\"spend_deferred\"", StringComparison.Ordinal))
            {
                Log("info", null, "spend", "消耗开关已记录；界面运行（识别 / 离线回放）不点击、不消耗");
                continue;
            }
            if (run.ForwardedOutput++ >= _limits.MaxForwardedOutputLines) { continue; }
            Log("debug", null, "stderr", Redact(line));
        }
    }

    internal static string Redact(string text)
    {
        var clean = new string(text.Where(c => !char.IsControl(c) || c == ' ').ToArray());
        clean = PathLike().Replace(clean, "〈路径〉");
        return clean.Length > 500 ? clean[..500] + "…" : clean;
    }

    // ------------------------------------------------------------------ finalize
    private void Finalize(Run run)
    {
        if (run.Finalized) { return; }
        run.Finalized = true;
        var summary = ReadSummary(run, out var summaryProblem);
        string? refusal = null;
        if (run.LastStdoutJson is not null)
        {
            try
            {
                if (JsonNode.Parse(run.LastStdoutJson) is JsonObject final && final["passed"] is JsonValue passed
                    && passed.TryGetValue<bool>(out var p) && !p && Str(final["error"]) is { } error)
                { refusal = Redact(error); }
            }
            catch (JsonException) { }
        }
        // Refused before run_tasks started (no events at all, exit 2): nothing ran.
        var refusedBeforeStart = run.ExitCode == 2 && run.LastSequence == 0 && !File.Exists(run.Launch.EventsPath) && !run.Killed;
        var clean = run.EventsBroken is null && run.SawRunFinished && !run.Killed && run.ExitCode is 0 or 1 or 2;
        var tasks = new JsonArray();
        var outcome = new Dictionary<string, (string Status, string? Reason)>(StringComparer.Ordinal);
        foreach (var task in run.Tasks)
        {
            (string Status, string? Reason) final;
            var hasEvent = run.EventStatus.TryGetValue(task, out var fromEvent);
            (string Status, string? Reason)? fromSummary = null;
            if (summary is not null && summary.TryGetValue(task, out var entry)) { fromSummary = entry; }
            if (refusedBeforeStart) { final = ("skipped", "未运行：" + (refusal ?? "配置被拒绝")); }
            else if (hasEvent && fromSummary is { } s)
            {
                final = fromEvent.Status == s.Status ? (s.Status, s.Reason) : ("uncertain", "事件与运行摘要不一致，需人工核对");
            }
            else if (hasEvent)
            {
                // A success needs both the event and the summary.
                final = fromEvent.Status is "failed" or "skipped" && clean ? (fromEvent.Status, null) : ("uncertain", "缺少运行摘要，需人工核对");
            }
            else if (fromSummary is { } onlySummary)
            {
                final = onlySummary.Status is "failed" or "skipped" ? onlySummary : ("uncertain", "运行事件不完整，需人工核对");
            }
            // 2026-10-15: a refusal after run.started (e.g. host action table out of date) names its cause.
            else if (!run.TasksStarted.Contains(task) && clean) { final = ("skipped", refusal is not null ? "未运行：" + refusal : run.EngineStopSeen ? "已停止，未开始" : "未开始"); }
            else { final = ("uncertain", run.Killed ? "引擎被终止，结果不确定" : "引擎异常结束，结果不确定"); }
            if (run.Killed && final.Status == "succeeded" && fromSummary is null) { final = ("uncertain", "引擎被终止，结果不确定"); }
            outcome[task] = final;
            tasks.Add(new JsonObject { ["name"] = task, ["status"] = final.Status, ["reason"] = final.Reason });
            Emit("run.update", new JsonObject { ["run_id"] = run.Launch.RunId, ["state"] = "running", ["task"] = task, ["status"] = final.Status, ["reason"] = final.Reason });
        }
        var statuses = outcome.Values.Select(item => item.Status).ToList();
        var result = refusedBeforeStart ? "refused"
            : statuses.Contains("uncertain") || run.Killed || run.Emergency || !clean ? "uncertain"
            : statuses.Contains("takeover") || run.StopRequestedMs is not null || run.EngineStopSeen ? "stopped"
            : statuses.All(item => item is "succeeded") ? "succeeded" : "failed";
        var reason = result switch
        {
            "refused" => "未运行：" + (refusal ?? "配置被拒绝"),
            "uncertain" => run.TimeoutReason ?? (run.Killed ? "运行进程被终止" : run.EventsBroken ?? (run.SawRunFinished ? "有不确定的结果" : "运行进程异常结束")) + "，请在游戏中核对",
            "stopped" => statuses.Contains("takeover") ? "需要你在游戏里接手，后续任务未运行" : refusal is not null && run.TasksStarted.Count == 0 ? "未运行：" + refusal : "已停止",
            "succeeded" => "全部成功",
            _ => "有任务失败",
        };
        if (summaryProblem is not null && !refusedBeforeStart) { Log("warn", null, "summary", summaryProblem); }
        Log(result switch { "succeeded" => "info", "stopped" => "warn", _ => "error" }, null, "run",
            $"运行结束：{reason}（退出码 {(run.ExitCode?.ToString(CultureInfo.InvariantCulture) ?? "未知")}）");
        var record = new JsonObject
        {
            ["id"] = run.Launch.RunId,
            ["started_at"] = run.StartedAt.ToString("yyyy-MM-ddTHH:mm:ss.fffzzz", CultureInfo.InvariantCulture),
            ["finished_at"] = DateTimeOffset.Now.ToString("yyyy-MM-ddTHH:mm:ss.fffzzz", CultureInfo.InvariantCulture),
            ["mode"] = run.Mode,
            ["outcome"] = result,
            ["reason"] = reason,
            ["exit_code"] = run.ExitCode,
            ["killed"] = run.Killed,
            ["events_complete"] = run.EventsBroken is null && run.SawRunFinished,
            ["tasks"] = tasks,
            ["readings"] = new JsonArray(run.Readings.Select(item => (JsonNode)item.DeepClone()).ToArray()),
        };
        WriteRecord(run, record);
        if (run.Launch.Wing && PublishGameAsset(run, "team-snapshot.js", 64 * 1024)) { Log("info", WingTask, "snapshot", "已更新 W.I.N.G. 编队快照（上次读取）"); }
        if (run.Launch.Wing && PublishGameAsset(run, "produce-end-last.js", 256 * 1024)) { Log("info", WingTask, "end", "已更新 Produce 结束摘要"); }
        // 2026-10-12: 獲得報酬 books of the W.I.N.G. run (agent/core/knowhow_books.py export) -> the ノウハウ page.
        if (run.Launch.Wing && PublishGameAsset(run, "knowhow.js", 4 * 1024 * 1024)) { Log("info", WingTask, "knowhow", "已更新ノウハウ记录"); }
        // ANN-17: passive readings written by this run (agent/core/reading_store.py) -> the 资源 page.
        if (run.Launch.SessionPath is not null && PublishGameAsset(run, "passive-readings.js", 8 * 1024 * 1024)) { Log("info", null, "readings", "已更新资源读数"); }
        _history.Insert(0, record);
        while (_history.Count > MaxHistory) { _history.RemoveAt(_history.Count - 1); }
        Emit("run.update", new JsonObject { ["run_id"] = run.Launch.RunId, ["state"] = "finished", ["task"] = null, ["reason"] = reason, ["outcome"] = result });
        try { run.Process?.Dispose(); } catch (InvalidOperationException) { }
        _run = null;
    }

    private Dictionary<string, (string Status, string? Reason)>? ReadSummary(Run run, out string? problem)
    {
        problem = null;
        try
        {
            var info = new FileInfo(run.Launch.SummaryPath);
            if (!info.Exists) { problem = "没有运行摘要"; return null; }
            if (info.Length > _limits.MaxSummaryBytes) { problem = "运行摘要过大，未读取"; return null; }
            if (JsonNode.Parse(File.ReadAllText(info.FullName, Encoding.UTF8), documentOptions: new JsonDocumentOptions { MaxDepth = 256 }) is not JsonObject summary
                || summary["tasks"] is not JsonArray tasks)
            { problem = "运行摘要格式无效"; return null; }
            var result = new Dictionary<string, (string, string?)>(StringComparer.Ordinal);
            foreach (var node in tasks)
            {
                if (node is not JsonObject task || TaskOf(run, Str(task["name"])) is not { } name || Str(task["status"]) is not { } raw) { continue; }
                result[name] = SummaryRow(name, task, raw, run.Readings);
            }
            return result;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException)
        {
            problem = "运行摘要无法读取";
            return null;
        }
    }

    /// <summary>
    /// One run-summary task row's (status, reason). The reason is the first of these that applies: the layout hint, the
    /// task's own filtered outcome, a failed task's Pipeline stop code (<see cref="UiTaskResult.StopReason"/>, only while no
    /// reason is set yet); then the W.I.N.G. stop, which overrides as before.
    /// </summary>
    internal static (string Status, string? Reason) SummaryRow(string name, JsonObject task, string raw, IEnumerable<JsonObject> readings)
    {
        var mapped = MapTaskStatus(raw);
        if (task["reports_incomplete"] is JsonValue incomplete && incomplete.TryGetValue<bool>(out var yes) && yes)
        { mapped = ("uncertain", "报告不完整，需人工核对"); }
        if (raw == "skipped") { mapped = ("skipped", "未开始"); }
        else if (LayoutHint(task) is { } hint) { mapped = (mapped.Status, hint); }
        mapped = (mapped.Status, UiTaskResult.Reason(name, mapped.Status, mapped.Reason, readings));
        // A Pipeline stop (SC.FailTask reason such as maintenance / gacha_announcement_unhandled) names a failed task's cause.
        if (mapped.Status == "failed" && mapped.Reason is null && UiTaskResult.StopReason(task) is { } stop)
        { mapped = (mapped.Status, Redact(stop)); }
        // W.I.N.G. stops: the wing report's outcome code (+ wing_nav's Chinese detail text) instead of no reason.
        if (name == WingTask && mapped.Status is "failed" or "takeover" or "uncertain" && UiTaskResult.WingReason(task) is { } wing)
        { mapped = (mapped.Status, Redact(wing)); }
        return mapped;
    }

    /// <summary>
    /// A run writes fixed-name display modules into the repo's ui/game-assets (team-snapshot.js, produce-end-last.js and
    /// knowhow.js from a Wing run, passive-readings.js from any host run); the panel is served from the host's output ui folder, so
    /// each module is copied there when this run wrote it. <paramref name="name"/> is one of those constant names.
    /// </summary>
    private bool PublishGameAsset(Run run, string name, long maxBytes)
    {
        if (name is not ("team-snapshot.js" or "produce-end-last.js" or "passive-readings.js" or "knowhow.js")) { return false; }
        try
        {
            var from = Path.Combine(_repoRoot, "ui", "game-assets", name);
            var info = new FileInfo(from);
            if (!info.Exists || info.Length > maxBytes || info.LastWriteTimeUtc < run.StartUtc.AddSeconds(-5)) { return false; }
            Directory.CreateDirectory(_outputGameAssets);
            var to = Path.Combine(_outputGameAssets, name);
            if (!string.Equals(Path.GetFullPath(from), Path.GetFullPath(to), StringComparison.OrdinalIgnoreCase)) { File.Copy(from, to, overwrite: true); }
            return true;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { return false; }
    }

    private static void WriteRecord(Run run, JsonObject record)
    {
        try
        {
            var path = Path.Combine(run.Launch.RunFolder, "result.json");
            var temp = path + ".tmp";
            File.WriteAllText(temp, record.ToJsonString(new JsonSerializerOptions(UiMessageRouter.JsonOptions) { WriteIndented = true }), new UTF8Encoding(false));
            File.Move(temp, path, overwrite: true);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { }
    }

    /// <summary>Recent run records (path-free) for the daily / history / resources views.</summary>
    private void LoadHistory()
    {
        if (!Directory.Exists(_runsRoot)) { return; }
        foreach (var folder in Directory.EnumerateDirectories(_runsRoot).Select(Path.GetFileName).Where(name => name is not null && UiRunId().IsMatch(name))
                     .OrderByDescending(name => name, StringComparer.Ordinal).Take(MaxHistory * 2))
        {
            if (_history.Count >= MaxHistory) { break; }
            try
            {
                var path = Path.Combine(_runsRoot, folder!, "result.json");
                var info = new FileInfo(path);
                if (!info.Exists || info.Length > 512 * 1024 || info.Attributes.HasFlag(FileAttributes.ReparsePoint)) { continue; }
                if (SanitizeRecord(JsonNode.Parse(File.ReadAllText(path, Encoding.UTF8)), folder!) is { } record) { _history.Add(record); }
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException) { }
        }
    }

    /// <summary>Re-validates a stored record so only fixed fields, known statuses and identifiers reach the page.</summary>
    internal static JsonObject? SanitizeRecord(JsonNode? node, string folder)
    {
        if (node is not JsonObject source || Str(source["id"]) != folder || source["tasks"] is not JsonArray tasks) { return null; }
        var clean = new JsonArray();
        foreach (var item in tasks.Take(32))
        {
            if (item is not JsonObject task || Str(task["name"]) is not { } name || !Identifier().IsMatch(name)) { continue; }
            var status = Str(task["status"]) is { } s && TaskStatuses.Contains(s) ? s : "uncertain";
            clean.Add(new JsonObject { ["name"] = name, ["status"] = status, ["reason"] = Str(task["reason"]) is { } r ? Redact(r) : null });
        }
        var readings = new JsonArray();
        if (source["readings"] is JsonArray stored)
        {
            foreach (var item in stored.Take(MaxRunReadings))
            {
                if (item is not JsonObject reading || reading["readings"] is not JsonObject values || BoundedCopy(values, 4096) is not { } copy) { continue; }
                var kind = Str(reading["kind"]);
                var task = Str(reading["task"]);
                if (kind is null || !Identifier().IsMatch(kind)) { continue; }
                readings.Add(new JsonObject
                {
                    ["run_id"] = folder, ["task"] = task is not null && Identifier().IsMatch(task) ? task : null, ["kind"] = kind,
                    ["readings"] = copy, ["ts"] = Str(reading["ts"]) is { } ts && DateTimeOffset.TryParse(ts, CultureInfo.InvariantCulture, DateTimeStyles.None, out _) ? ts : null,
                });
            }
        }
        var startedAt = Str(source["started_at"]);
        if (startedAt is null || !DateTimeOffset.TryParse(startedAt, CultureInfo.InvariantCulture, DateTimeStyles.None, out _)) { return null; }
        var mode = Str(source["mode"]) is "read_only" or "offline_replay" or WingMode or DailyMode ? Str(source["mode"]) : "read_only";
        var outcome = Str(source["outcome"]) is "succeeded" or "failed" or "stopped" or "uncertain" or "refused" ? Str(source["outcome"]) : "uncertain";
        return new JsonObject
        {
            ["id"] = folder, ["started_at"] = startedAt, ["mode"] = mode, ["outcome"] = outcome,
            ["reason"] = Str(source["reason"]) is { } reason ? Redact(reason) : null, ["tasks"] = clean, ["readings"] = readings,
        };
    }

    /// <summary>Copies a readings object keeping only bools, small non-negative numbers, short identifiers and nested arrays/objects of those.</summary>
    internal static JsonObject? BoundedCopy(JsonObject source, int maxBytes)
    {
        static JsonNode? Copy(JsonNode? node, int depth)
        {
            if (depth > 4 || node is null) { return null; }
            switch (node)
            {
                case JsonObject obj:
                    var outObj = new JsonObject();
                    foreach (var (key, value) in obj.Take(64))
                    {
                        if (!Identifier().IsMatch(key)) { continue; }
                        if (Copy(value, depth + 1) is { } copied) { outObj[key] = copied; }
                    }
                    return outObj;
                case JsonArray array:
                    var outArray = new JsonArray();
                    foreach (var item in array.Take(100)) { if (Copy(item, depth + 1) is { } copied) { outArray.Add(copied); } }
                    return outArray;
                case JsonValue value:
                    if (value.TryGetValue<bool>(out var flag)) { return JsonValue.Create(flag); }
                    if (value.TryGetValue<JsonElement>(out var element) && element.ValueKind == JsonValueKind.Number && element.TryGetInt64(out var number) && number is >= 0 and <= 1_000_000_000)
                    { return JsonValue.Create(number); }
                    if (value.TryGetValue<long>(out var direct) && direct is >= 0 and <= 1_000_000_000) { return JsonValue.Create(direct); }
                    if (value.TryGetValue<int>(out var small) && small is >= 0) { return JsonValue.Create(small); }
                    if (value.TryGetValue<string>(out var text) && Identifier().IsMatch(text) && text.Length <= 64) { return JsonValue.Create(text); }
                    return null;
            }
            return null;
        }
        var copy = Copy(source, 0) as JsonObject;
        return copy is not null && Encoding.UTF8.GetByteCount(copy.ToJsonString()) <= maxBytes ? copy : null;
    }

    private void Log(string level, string? task, string kind, string text) =>
        Emit("log.append", new JsonObject { ["level"] = level, ["task"] = task, ["kind"] = kind, ["text"] = text });

    private void Emit(string name, JsonObject data) => Output?.Invoke(name, data);

    private static string? Str(JsonNode? node) => node is JsonValue value && value.TryGetValue<string>(out var text) ? text : null;

    private static long? Int(JsonNode? node)
    {
        if (node is not JsonValue value) { return null; }
        if (value.TryGetValue<long>(out var number)) { return number; }
        if (value.TryGetValue<int>(out var small)) { return small; }
        if (value.TryGetValue<JsonElement>(out var element) && element.ValueKind == JsonValueKind.Number && element.TryGetInt64(out var parsed)) { return parsed; }
        return null;
    }

    /// <summary>Host shutdown: request a stop, wait a bounded time, then kill. The run is recorded as uncertain if killed.</summary>
    public void Dispose()
    {
        var run = _run;
        if (run is null || run.Process is null) { return; }
        RequestStop("cooperative");
        try
        {
            if (!run.Process.WaitForExit(15_000))
            {
                run.Killed = true;
                run.Process.Kill(entireProcessTree: true);
                run.Process.WaitForExit(_limits.KillWaitMs);
            }
        }
        catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception) { }
        run.ExitedMs ??= _clockMs();
        try { run.ExitCode = run.Process.ExitCode; } catch (InvalidOperationException) { }
        ReadEvents(run);
        Finalize(run);
    }

    internal sealed class Run
    {
        internal Run(UiEngineLaunch launch, string mode, List<string> tasks, long startedMs, DateTimeOffset startedAt)
        {
            Launch = launch; Mode = mode; Tasks = tasks; StartedMs = startedMs; StartedAt = startedAt;
        }
        internal UiEngineLaunch Launch { get; }
        internal string Mode { get; }
        internal List<string> Tasks { get; }
        internal long StartedMs { get; }
        internal DateTimeOffset StartedAt { get; }
        internal long DeadlineMs { get; init; }
        internal Dictionary<string, string> Aliases { get; init; } = new(StringComparer.Ordinal);
        internal DateTime StartUtc { get; init; }
        internal Process? Process { get; set; }
        internal ConcurrentQueue<string> Stdout { get; } = new();
        internal ConcurrentQueue<string> Stderr { get; } = new();
        internal string? LastStdoutJson { get; set; }
        internal int ForwardedOutput { get; set; }
        internal long EventOffset { get; set; }
        internal long EventBytes { get; set; }
        internal int EventLines { get; set; }
        internal List<byte> Partial { get; } = new();
        internal long LastSequence { get; set; }
        internal string? PythonRunId { get; set; }
        internal string? EventsBroken { get; set; }
        internal bool SawRunStarted { get; set; }
        internal bool SawRunFinished { get; set; }
        internal string? EngineFinishStatus { get; set; }
        internal bool EngineStopSeen { get; set; }
        internal HashSet<string> TasksStarted { get; } = new(StringComparer.Ordinal);
        internal Dictionary<string, (string Status, string Raw)> EventStatus { get; } = new(StringComparer.Ordinal);
        internal List<JsonObject> Readings { get; } = new();
        internal int NodeLogs { get; set; }
        internal long? StopRequestedMs { get; set; }
        internal long? GraceMs { get; set; }
        internal bool StopFileWritten { get; set; }
        internal bool Emergency { get; set; }
        internal bool Killed { get; set; }
        internal string? TimeoutReason { get; set; }
        internal long? ExitedMs { get; set; }
        internal int? ExitCode { get; set; }
        internal bool Finalized { get; set; }
    }
}
