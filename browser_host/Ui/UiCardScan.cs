using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>Host-side surface of the 「扫描持有卡」 button (cards.scan.start / stop / status).</summary>
internal interface IUiCardScan
{
    bool Active { get; }
    /// <summary>The <c>card_scan</c> object merged into status.update (state, progress, blocker, last result).</summary>
    JsonObject Status();
    /// <summary>Null when started, otherwise a short refusal code (nothing was launched).</summary>
    string? Start();
    bool RequestStop();
    /// <summary>Called periodically on the UI thread: tail events, count frames, enforce limits, finalize.</summary>
    void Tick();
    /// <summary>(event name, data) for the page; only protocol events (log.append).</summary>
    event Action<string, JsonObject>? Output;
}

/// <summary>Everything one scan run needs on disk; paths live in a fresh private folder and never reach the page.</summary>
internal sealed record CardScanLaunch(string RunId, string Folder, string ConfigPath, string EventsPath, string StopPath,
    string SummaryPath, string SessionPath);

/// <summary>Bounded limits of the UI-launched scan (the CLI's supervised full-library budget).</summary>
internal sealed record CardScanLimits(
    int CooperativeGraceMs = 90_000,
    int KillWaitMs = 5_000,
    int EmergencyWaitMs = 10_000,
    int ExitDrainMs = 3_000,
    int DeadlineMarginMs = 5 * 60_000,
    long MaxEventBytes = 32L * 1024 * 1024,
    int MaxLineBytes = 16_384,
    int MaxSummaryBytes = 16 * 1024 * 1024);

/// <summary>
/// The page's 「扫描持有卡」: launches exactly the CLI's supervised CardScan (<c>.venv</c> python tools/run_tasks.py --live
/// --supervised-task, one task, <c>allow_spend: []</c>) as one child process with a private generated config, events,
/// summary and stop file. The page can only start (after its own confirmation), stop and watch; it never supplies a path,
/// option or budget. The host adds no click of its own: the child talks to the host's existing pipeline-input bridge, which
/// still admits only enabled action-table actions on fresh frames. Success is read from the run summary (a complete scan
/// that wrote both import modules), never from the exit code. A child that ignores 停止 is killed and shown as uncertain;
/// nothing is retried. Not thread safe: Start/Tick/RequestStop all run on the UI thread.
/// </summary>
internal sealed partial class UiCardScanSupervisor : IUiCardScan, IDisposable
{
    internal const string RunScript = "tools/run_tasks.py";
    internal const int MaxScrollsPerTab = 80;
    internal const int MaxFrames = 400;
    internal const int MaxHolds = 10;
    internal const int SessionMinutes = 50;
    internal const int TaskTimeoutS = 2700;
    private static readonly string[] ImportModules = { "owned-levels.js", "inventory-scan.js" };

    private readonly string _repoRoot;
    private readonly string _runsRoot;
    private readonly string _framesRoot;
    private readonly string _outputGameAssets;
    private readonly Func<string?> _blocker;
    private readonly Func<string?> _sessionPath;
    private readonly Func<CardScanLaunch, ProcessStartInfo> _command;
    private readonly Func<long> _clockMs;
    private readonly CardScanLimits _limits;
    private Run? _run;
    private int _finishedSeq;
    private JsonObject? _last;

    [GeneratedRegex("^[A-Za-z0-9_:.-]{1,80}$")] private static partial Regex ReasonCode();

    internal UiCardScanSupervisor(string repoRoot, Func<string?> blocker, Func<string?> sessionPath,
        Func<CardScanLaunch, ProcessStartInfo>? command = null, Func<long>? clockMs = null, CardScanLimits? limits = null,
        string? runsRoot = null, string? framesRoot = null, string? outputGameAssets = null)
    {
        _repoRoot = Path.GetFullPath(repoRoot);
        var privateRoot = Path.GetFullPath(Path.Combine(_repoRoot, "dev_assets", "private")) + Path.DirectorySeparatorChar;
        _runsRoot = Path.GetFullPath(runsRoot ?? Path.Combine(_repoRoot, "dev_assets", "private", "card-scan-ui"));
        _framesRoot = Path.GetFullPath(framesRoot ?? Path.Combine(_repoRoot, "dev_assets", "private", "card-scan"));
        if (!_runsRoot.StartsWith(privateRoot, StringComparison.OrdinalIgnoreCase)) { throw new ArgumentException("扫描目录必须位于 dev_assets/private 内。"); }
        _outputGameAssets = Path.GetFullPath(outputGameAssets ?? Path.Combine(UiPaths.OutputUiFolder(), "game-assets"));
        _blocker = blocker;
        _sessionPath = sessionPath;
        _command = command ?? DefaultCommand;
        _clockMs = clockMs ?? (() => Stopwatch.GetTimestamp() * 1000 / Stopwatch.Frequency);
        _limits = limits ?? new CardScanLimits();
    }

    public event Action<string, JsonObject>? Output;

    public bool Active => _run is not null;
    internal string RunsRoot => _runsRoot;

    /// <summary>The generated run config: exactly the CLI's supervised full-library CardScan (both tabs, no spend, no preset).</summary>
    internal static JsonObject BuildConfig() => new()
    {
        ["preset"] = null,
        ["tasks"] = new JsonArray(new JsonObject
        {
            ["name"] = "CardScan",
            ["option"] = new JsonObject
            {
                ["tabs"] = new JsonArray("support", "produce"),
                ["max_scrolls_per_tab"] = MaxScrollsPerTab,
                ["max_frames"] = MaxFrames,
                ["max_holds"] = MaxHolds,
            },
        }),
        ["session_max_minutes"] = SessionMinutes,
        ["task_timeout_s"] = TaskTimeoutS,
        ["allow_spend"] = new JsonArray(),
    };

    /// <summary>The fixed argument list after the interpreter: run_tasks.py --live --supervised-task with the private files.</summary>
    internal static IReadOnlyList<string> RunArguments(string repoRoot, CardScanLaunch launch) => new[]
    {
        "-X", "utf8", Path.Combine(repoRoot, RunScript), "--live", "--supervised-task",
        "--config", launch.ConfigPath, "--session", launch.SessionPath,
        "--summary", launch.SummaryPath, "--events", launch.EventsPath, "--stop-file", launch.StopPath,
    };

    internal ProcessStartInfo DefaultCommand(CardScanLaunch launch)
    {
        var python = Path.Combine(_repoRoot, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python)) { throw new InvalidOperationException("python_env_missing"); }
        var info = UiEngineSupervisor.NewStartInfo(_repoRoot, python);
        foreach (var argument in RunArguments(_repoRoot, launch)) { info.ArgumentList.Add(argument); }
        return info;
    }

    public string? Start()
    {
        if (_run is not null) { return "already_running"; }
        if (_blocker() is string blocker) { return blocker; }
        var session = _sessionPath();
        if (string.IsNullOrEmpty(session) || !File.Exists(session)) { return "session_unavailable"; }
        var stamp = DateTime.UtcNow.ToString("yyyyMMdd'T'HHmmss'Z'", CultureInfo.InvariantCulture);
        Directory.CreateDirectory(_runsRoot);
        // One folder per run, never reused: a second start within the same second gets a numeric suffix.
        string? folder = null;
        for (var attempt = 1; attempt <= 20 && folder is null; attempt++)
        {
            var candidate = Path.Combine(_runsRoot, attempt == 1 ? stamp : $"{stamp}-{attempt}");
            if (Directory.Exists(candidate)) { continue; }
            try { Directory.CreateDirectory(candidate); folder = candidate; }
            catch (IOException) { }
        }
        if (folder is null) { return "folder_unavailable"; }
        var launch = new CardScanLaunch("card-scan-ui-" + Path.GetFileName(folder), folder, Path.Combine(folder, "config.json"), Path.Combine(folder, "events.jsonl"),
            Path.Combine(folder, "stop.flag"), Path.Combine(folder, "summary.json"), session);
        using (var stream = new FileStream(launch.ConfigPath, FileMode.CreateNew, FileAccess.Write))
        { stream.Write(Encoding.UTF8.GetBytes(BuildConfig().ToJsonString(UiMessageRouter.JsonOptions))); }

        ProcessStartInfo info;
        try { info = _command(launch); }
        catch (InvalidOperationException exception) { return ReasonOf(exception.Message, "launch_failed"); }
        var process = new Process { StartInfo = info, EnableRaisingEvents = false };
        var run = new Run(launch, _clockMs(), DateTime.UtcNow)
        { DeadlineMs = (long)(SessionMinutes * 60_000L) + _limits.DeadlineMarginMs };
        process.OutputDataReceived += (_, line) => { if (line.Data is not null && run.Stdout.Count < 2_000) { run.Stdout.Enqueue(line.Data); } };
        process.ErrorDataReceived += (_, line) => { if (line.Data is not null && run.Stderr.Count < 2_000) { run.Stderr.Enqueue(line.Data); } };
        try { if (!process.Start()) { process.Dispose(); return "launch_failed"; } }
        catch (System.ComponentModel.Win32Exception) { process.Dispose(); return "launch_failed"; }
        try { process.StandardInput.Close(); } catch (IOException) { }
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        run.Process = process;
        _run = run;
        Log("info", "扫描持有卡已开始：只读、约 10–20 分钟，请不要触碰游戏画面。");
        return null;
    }

    public bool RequestStop() => RequestStop("user_stop");

    private bool RequestStop(string reason)
    {
        var run = _run;
        if (run is null || run.Finalized) { return false; }
        if (run.StopRequestedMs is null)
        {
            run.StopRequestedMs = _clockMs();
            run.StopReason = reason;
            try
            {
                using var _ = new FileStream(run.Launch.StopPath, FileMode.OpenOrCreate, FileAccess.Write, FileShare.ReadWrite);
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
            {
                Log("error", "无法写入停止文件；宽限期后将终止扫描进程，结果不确定。");
            }
            Log("warn", reason == "user_stop" ? "正在停止扫描：回到首页后结束。" : "扫描超出预定时长，已请求停止。");
        }
        return true;
    }

    public void Tick()
    {
        var run = _run;
        if (run is null) { return; }
        _ = _blocker();   // keeps the host's click-activity sampling current while this run owns the input
        DrainOutput(run);
        TailEvents(run);
        if (run.Ticks++ % 4 == 0) { CountFrames(run); }
        var now = _clockMs();
        if (run.Process!.HasExited)
        {
            run.Process.WaitForExit(_limits.ExitDrainMs);
            DrainOutput(run);
            TailEvents(run);
            Finalize(run);
            return;
        }
        if (run.StopRequestedMs is null && now - run.StartMs > run.DeadlineMs) { RequestStop("deadline"); }
        if (run.StopRequestedMs is { } stopped && !run.Killed && now - stopped > _limits.CooperativeGraceMs)
        {
            run.Killed = true;
            try { run.Process.Kill(entireProcessTree: true); }
            catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception) { }
            Log("error", "扫描进程未在宽限期内停止，已终止；结果不确定，请核对游戏画面。");
        }
    }

    public JsonObject Status()
    {
        var run = _run;
        var status = new JsonObject
        {
            ["state"] = run is null ? (_last is null ? "idle" : "finished") : run.StopRequestedMs is null ? "running" : "stopping",
            ["blocker"] = run is null ? _blocker() : null,
            ["finished_seq"] = _finishedSeq,
        };
        if (run is not null)
        {
            status["tab"] = run.Tab;
            status["scrolls"] = run.Scrolls;
            status["frames"] = run.Frames;
            status["elapsed_s"] = (int)Math.Max(0, (_clockMs() - run.StartMs) / 1000);
        }
        else if (_last is not null)
        {
            foreach (var (key, value) in _last) { status[key] = value?.DeepClone(); }
        }
        return status;
    }

    private void DrainOutput(Run run)
    {
        while (run.Stdout.TryDequeue(out _)) { }
        while (run.Stderr.TryDequeue(out _)) { }
    }

    /// <summary>Reads appended complete lines of the private events file: the selected tab and the wheel steps.</summary>
    private void TailEvents(Run run)
    {
        try
        {
            var info = new FileInfo(run.Launch.EventsPath);
            if (!info.Exists || info.Length <= run.EventsOffset || info.Length > _limits.MaxEventBytes) { return; }
            using var stream = new FileStream(info.FullName, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            stream.Seek(run.EventsOffset, SeekOrigin.Begin);
            var buffer = new byte[Math.Min(info.Length - run.EventsOffset, 256 * 1024)];
            var read = stream.Read(buffer, 0, buffer.Length);
            var end = -1;
            for (var index = read - 1; index >= 0; index--) { if (buffer[index] == (byte)'\n') { end = index; break; } }
            if (end < 0)
            {
                // No complete line yet; a buffer-sized run without a newline is garbage and is skipped, never re-read forever.
                if (read == buffer.Length) { run.EventsOffset += read; }
                return;
            }
            run.EventsOffset += end + 1;
            foreach (var line in Encoding.UTF8.GetString(buffer, 0, end).Split('\n'))
            {
                if (line.Length == 0 || line.Length > _limits.MaxLineBytes) { continue; }
                ApplyEvent(run, line);
            }
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { }
    }

    internal static void ApplyEvent(Run run, string line)
    {
        try
        {
            using var doc = JsonDocument.Parse(line);
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("event", out var kind) || kind.GetString() != "node"
                || !root.TryGetProperty("status", out var status) || status.GetString() != "succeeded"
                || !root.TryGetProperty("node", out var node) || node.GetString() is not string name) { return; }
            switch (name)
            {
                case "SC.CardScan.List.TapSupport": run.Tab = "support"; break;
                case "SC.CardScan.List.TapProduce": run.Tab = "produce"; break;
                case "SC.CardScan.List.ScrollDown":
                case "SC.CardScan.List.ScrollUp": run.Scrolls++; break;
            }
        }
        catch (JsonException) { }
    }

    /// <summary>Frames the scanner saved for this run: the newest card-scan-&lt;UTC&gt; folder created after the launch.</summary>
    private void CountFrames(Run run)
    {
        try
        {
            if (!Directory.Exists(_framesRoot)) { return; }
            DirectoryInfo? newest = null;
            foreach (var dir in new DirectoryInfo(_framesRoot).EnumerateDirectories("card-scan-*"))
            {
                if (dir.CreationTimeUtc >= run.StartUtc.AddSeconds(-5) && (newest is null || dir.CreationTimeUtc > newest.CreationTimeUtc)) { newest = dir; }
            }
            if (newest is not null) { run.Frames = newest.EnumerateFiles("frame-*.png").Count(); }
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { }
    }

    private void Finalize(Run run)
    {
        run.Finalized = true;
        var exitCode = run.Process!.ExitCode;
        var result = ReadResult(run, exitCode);
        run.Process.Dispose();
        _run = null;
        _finishedSeq++;
        result["finished_seq"] = _finishedSeq;
        result["elapsed_s"] = (int)Math.Max(0, (_clockMs() - run.StartMs) / 1000);
        _last = result;
        var outcome = (string)result["outcome"]!;
        var imported = result["imported"]!.GetValue<bool>();
        Log(outcome == "scanned" ? "info" : "warn", outcome == "scanned"
            ? (imported ? "扫描完成，结果已写入本机导入文件。" : "扫描完成，但没有写出导入文件。")
            : $"扫描未完整完成（{outcome}）；部分结果不会导入。");
    }

    /// <summary>Outcome strictly from the run summary: scanned only for a complete scan that wrote both import modules.</summary>
    private JsonObject ReadResult(Run run, int exitCode)
    {
        var result = new JsonObject { ["outcome"] = "failed", ["reason"] = $"engine_exit_{exitCode}", ["imported"] = false, ["cards"] = null, ["tabs"] = new JsonArray() };
        if (run.Killed) { result["outcome"] = "uncertain"; result["reason"] = "killed_after_stop"; return result; }
        JsonElement report = default;
        var stopped = "";
        try
        {
            var info = new FileInfo(run.Launch.SummaryPath);
            if (!info.Exists || info.Length > _limits.MaxSummaryBytes)
            {
                if (run.StopReason is not null) { result["outcome"] = "stopped"; result["reason"] = run.StopReason; }
                return result;
            }
            using var doc = JsonDocument.Parse(File.ReadAllText(info.FullName));
            if (doc.RootElement.TryGetProperty("tasks", out var tasks) && tasks.ValueKind == JsonValueKind.Array && tasks.GetArrayLength() > 0
                && tasks[0].TryGetProperty("reports", out var reports) && reports.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in reports.EnumerateArray())
                {
                    if (!item.TryGetProperty("kind", out var k) || k.GetString() != "card_scan" || !item.TryGetProperty("outcome", out var o)) { continue; }
                    var text = o.GetString() ?? "";
                    if (text.StartsWith("stopped:", StringComparison.Ordinal)) { stopped = text["stopped:".Length..]; }
                    else if (report.ValueKind == JsonValueKind.Undefined) { report = item.Clone(); }
                }
                if (report.ValueKind == JsonValueKind.Undefined && tasks[0].TryGetProperty("fail_reasons", out var fails)
                    && fails.ValueKind == JsonValueKind.Array && fails.GetArrayLength() > 0)
                { result["reason"] = ReasonOf(fails[0].GetString(), (string)result["reason"]!); }
            }
            if (report.ValueKind == JsonValueKind.Undefined)
            {
                if (stopped.Length > 0) { result["outcome"] = "stopped"; result["reason"] = ReasonOf(stopped, "stopped"); }
                else if (run.StopReason is not null) { result["outcome"] = "stopped"; result["reason"] = run.StopReason; }
                return result;
            }
            var outcome = report.GetProperty("outcome").GetString() ?? "";
            if (report.TryGetProperty("cards", out var cards) && cards.TryGetInt32(out var count)) { result["cards"] = count; }
            if (report.TryGetProperty("tabs", out var tabs) && tabs.ValueKind == JsonValueKind.Array)
            {
                var list = new JsonArray();
                foreach (var tab in tabs.EnumerateArray().Take(4))
                {
                    list.Add(new JsonObject
                    {
                        ["tab"] = ReasonOf(tab.TryGetProperty("tab", out var t) ? t.GetString() : null, "?"),
                        ["complete"] = tab.TryGetProperty("complete", out var c) && c.ValueKind == JsonValueKind.True,
                        ["scrolls"] = tab.TryGetProperty("scrolls", out var s) && s.TryGetInt32(out var n) ? n : 0,
                        ["accepted"] = tab.TryGetProperty("slots_accepted", out var a) && a.TryGetInt32(out var m) ? m : 0,
                        ["end_reason"] = tab.TryGetProperty("end_reason", out var e) && e.ValueKind == JsonValueKind.String ? ReasonOf(e.GetString(), "?") : null,
                    });
                }
                result["tabs"] = list;
            }
            var written = report.TryGetProperty("written", out var w) && w.ValueKind == JsonValueKind.Array
                ? w.EnumerateArray().Select(x => x.GetString() ?? "").ToList() : new List<string>();
            if (outcome == "scanned")
            {
                var complete = ImportModules.All(name => written.Contains("ui/game-assets/" + name));
                var published = complete && PublishImports(run);
                result["outcome"] = "scanned";
                result["imported"] = published;
                result["reason"] = !complete ? "import_files_missing" : published ? null : "import_copy_failed";
            }
            else if (outcome.StartsWith("partial:", StringComparison.Ordinal))
            {
                result["outcome"] = run.StopReason is not null || stopped.Length > 0 ? "stopped" : "partial";
                result["reason"] = run.StopReason ?? ReasonOf(outcome["partial:".Length..], "partial");
            }
            else { result["reason"] = ReasonOf(stopped.Length > 0 ? stopped : outcome, "failed"); }
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException or InvalidOperationException)
        {
            result["outcome"] = "failed";
            result["reason"] = "summary_unreadable";
        }
        return result;
    }

    /// <summary>
    /// The scan writes the repo's ui/game-assets; the panel is served from the host's output ui folder, so the two module files
    /// (fixed names, written by this run) are copied there. Nothing else is copied and nothing is saved into the profile.
    /// </summary>
    private bool PublishImports(Run run)
    {
        try
        {
            var source = Path.GetFullPath(Path.Combine(_repoRoot, "ui", "game-assets"));
            Directory.CreateDirectory(_outputGameAssets);
            foreach (var name in ImportModules)
            {
                var from = Path.Combine(source, name);
                var info = new FileInfo(from);
                if (!info.Exists || info.Length > 8 * 1024 * 1024 || info.LastWriteTimeUtc < run.StartUtc.AddSeconds(-5)) { return false; }
                var to = Path.Combine(_outputGameAssets, name);
                if (!string.Equals(Path.GetFullPath(from), Path.GetFullPath(to), StringComparison.OrdinalIgnoreCase)) { File.Copy(from, to, overwrite: true); }
            }
            return true;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { return false; }
    }

    private static string ReasonOf(string? text, string fallback) => text is not null && ReasonCode().IsMatch(text) ? text : fallback;

    private void Log(string level, string text) =>
        Output?.Invoke("log.append", new JsonObject { ["level"] = level, ["kind"] = "card_scan", ["text"] = text, ["task"] = "CardScan" });

    public void Dispose()
    {
        var run = _run;
        if (run?.Process is { } process)
        {
            try
            {
                RequestStop("user_stop");
                if (!process.WaitForExit(_limits.EmergencyWaitMs)) { process.Kill(entireProcessTree: true); process.WaitForExit(_limits.KillWaitMs); }
            }
            catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception) { }
            process.Dispose();
        }
        _run = null;
    }

    internal sealed class Run
    {
        internal Run(CardScanLaunch launch, long startMs, DateTime startUtc) { Launch = launch; StartMs = startMs; StartUtc = startUtc; }
        internal CardScanLaunch Launch { get; }
        internal long StartMs { get; }
        internal DateTime StartUtc { get; }
        internal long DeadlineMs { get; init; }
        internal Process? Process { get; set; }
        internal System.Collections.Concurrent.ConcurrentQueue<string> Stdout { get; } = new();
        internal System.Collections.Concurrent.ConcurrentQueue<string> Stderr { get; } = new();
        internal long EventsOffset { get; set; }
        internal int Ticks { get; set; }
        internal string? Tab { get; set; }
        internal int Scrolls { get; set; }
        internal int Frames { get; set; }
        internal long? StopRequestedMs { get; set; }
        internal string? StopReason { get; set; }
        internal bool Killed { get; set; }
        internal bool Finalized { get; set; }
    }
}

/// <summary>
/// Pure readiness gate for 「扫描持有卡」, shared by the page's button state (status.update card_scan.blocker) and the host's
/// own refusal in cards.scan.start. The first failing condition wins; null = nothing blocks a start.
/// </summary>
internal static class UiCardScanGate
{
    /// <param name="host">UiStatus() fields (pipeline_input_enabled, connected, host_guard_reason, click counters).</param>
    /// <param name="policy">UiPolicySummary (card_scan_ready / card_scan_reason).</param>
    /// <param name="engineActive">A UI read-only / replay run is active.</param>
    /// <param name="recentExternalInput">Clicks reached the host within the last seconds without this host launching them.</param>
    internal static string? Blocker(JsonObject host, JsonObject policy, bool engineActive, bool recentExternalInput)
    {
        if (host["pipeline_input_enabled"]?.GetValue<bool>() != true) { return "pipeline_input_disabled"; }
        if (policy["card_scan_ready"] is not JsonValue ready || !ready.TryGetValue<bool>(out var ok) || !ok)
        { return policy["card_scan_reason"] is JsonValue reason && reason.TryGetValue<string>(out var text) ? Sanitize(text) : "card_scan_not_ready"; }
        if (host["connected"]?.GetValue<bool>() != true) { return "host_not_connected"; }
        if (host["host_guard_reason"] is not null) { return "host_guard_active"; }
        if (engineActive) { return "engine_active"; }
        // 2026-10-28: no click caps, no input-budget check.
        if (recentExternalInput) { return "other_input_activity"; }
        return null;
    }

    private static string Sanitize(string text) => text.Length is > 0 and <= 120 && text.All(c => char.IsAsciiLetterOrDigit(c) || c is '_' or ':' or ',' or '-') ? text : "card_scan_not_ready";
}
