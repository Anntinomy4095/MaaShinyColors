using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>Host-side surface of the 「扫描ノウハウ」 button (knowhow.scan.start / stop / status).</summary>
internal interface IUiKnowhowScan
{
    bool Active { get; }
    /// <summary>The <c>knowhow_scan</c> object merged into status.update.</summary>
    JsonObject Status();
    /// <summary><paramref name="mode"/> is calibrate, scan or newest (<paramref name="count"/> 1..10: the new books the
    /// page expects, 0 otherwise). Null when started, otherwise a short refusal code.</summary>
    string? Start(string mode, int count = 0);
    bool RequestStop();
    void Tick();
    event Action<string, JsonObject>? Output;
}

/// <summary>
/// The page's 「扫描ノウハウ」: launches exactly the CLI's supervised KnowhowScan (<c>.venv</c> python tools/run_tasks.py
/// --live --supervised-task, one task, <c>allow_spend: []</c>) as one child process with a private generated config, events,
/// summary and stop file — the same mechanics as <see cref="UiCardScanSupervisor"/>. The page chooses only the mode
/// (calibrate: wheel-step measurement, no sort change; scan: 評価値 grid + details, fresh on each user start, or only the new
/// books when 所持数 exceeds the recorded library by 1..10 (run_tasks topup "prefer"); newest: a top-up only, the agent
/// reads 所持数 and opens at most 10 books in 獲得順 (newest "auto"), for 自动卖书's library check, 2026-10-25); the
/// options and click budget are fixed here and re-validated by run_tasks. The host adds no click of its own. Success is read
/// from the run summary, never from the exit code; nothing is retried. Not thread safe: UI thread only.
/// </summary>
internal sealed partial class UiKnowhowScanSupervisor : IUiKnowhowScan, IDisposable
{
    internal const string RunScript = "tools/run_tasks.py";
    internal const string TaskName = "KnowhowScan";
    /// <summary>Calibration option (tools/run_tasks.knowhow_scan_options): 3 + 2×(1+10) + 120 + 2 + 3 = 150 inputs.</summary>
    internal const string CalibrateOption = """{"mode": "calibrate", "calibration": {"books": 2, "detail_wheels": 10, "grid_wheels": 4, "grid_seek_bottom": true, "max_grid_wheels": 120, "grid_up_wheels": 2, "max_frames": 400, "max_holds": 3}}""";
    /// <summary>Scan the whole library, resuming between host run windows: 590 scan inputs + 5 navigation.</summary>
    internal const string ScanOption = """{"mode": "scan", "details": true, "max_books": 600, "max_inputs": 590, "max_frames": 8000, "max_holds": 5, "max_grid_scrolls": 300, "max_detail_scrolls": 30, "resume_record": null}""";
    /// <summary>Top-up: the agent decides the count from 所持数 (at most NewestMax).</summary>
    internal const int NewestMax = 10;
    internal static readonly string[] Modes = { "calibrate", "scan", "newest" };
    internal const int SessionMinutes = 50;
    internal const int TaskTimeoutS = 2700;
    private const string ImportModule = "knowhow.js";

    private readonly string _repoRoot;
    private readonly string _runsRoot;
    private readonly string _framesRoot;
    private readonly string _outputGameAssets;
    private readonly Func<string?> _blocker;
    private readonly Func<string?> _sessionPath;
    private readonly Func<JsonObject>? _hostStatus;
    private readonly Func<CardScanLaunch, ProcessStartInfo> _command;
    private readonly Func<long> _clockMs;
    private readonly CardScanLimits _limits;
    private Run? _run;
    private int _finishedSeq;
    private JsonObject? _last;
    private bool _scanAll;
    private long? _continueAtMs;
    private string? _scanId;
    private string? _resumeActivity;
    private int _newestCount;

    [GeneratedRegex("^[A-Za-z0-9_:.,-]{1,80}$")] private static partial Regex ReasonCode();

    internal UiKnowhowScanSupervisor(string repoRoot, Func<string?> blocker, Func<string?> sessionPath,
        Func<CardScanLaunch, ProcessStartInfo>? command = null, Func<long>? clockMs = null, CardScanLimits? limits = null,
        string? runsRoot = null, string? framesRoot = null, string? outputGameAssets = null, Func<JsonObject>? hostStatus = null)
    {
        _repoRoot = Path.GetFullPath(repoRoot);
        var privateRoot = Path.GetFullPath(Path.Combine(_repoRoot, "dev_assets", "private")) + Path.DirectorySeparatorChar;
        _runsRoot = Path.GetFullPath(runsRoot ?? Path.Combine(_repoRoot, "dev_assets", "private", "knowhow-scan-ui"));
        _framesRoot = Path.GetFullPath(framesRoot ?? Path.Combine(_repoRoot, "dev_assets", "private", "knowhow-scan"));
        if (!_runsRoot.StartsWith(privateRoot, StringComparison.OrdinalIgnoreCase)) { throw new ArgumentException("扫描目录必须位于 dev_assets/private 内。"); }
        _outputGameAssets = Path.GetFullPath(outputGameAssets ?? Path.Combine(UiPaths.OutputUiFolder(), "game-assets"));
        _blocker = blocker;
        _sessionPath = sessionPath;
        _hostStatus = hostStatus;
        _command = command ?? DefaultCommand;
        _clockMs = clockMs ?? (() => Stopwatch.GetTimestamp() * 1000 / Stopwatch.Frequency);
        _limits = limits ?? new CardScanLimits();
    }

    public event Action<string, JsonObject>? Output;

    public bool Active => _run is not null || _continueAtMs is not null;
    internal string RunsRoot => _runsRoot;

    internal static bool ValidMode(string? mode) => mode is "calibrate" or "scan" or "newest";

    internal static bool ValidCount(string mode, int count) => mode == "newest" ? count is >= 1 and <= NewestMax : count == 0;

    /// <summary>The generated run config: the CLI's supervised KnowhowScan with the fixed option of <paramref name="mode"/>.</summary>
    internal static JsonObject BuildConfig(string mode, string? scanId = null, bool resume = false, int count = 0)
    {
        if (!ValidMode(mode) || !ValidCount(mode, count)) { throw new ArgumentException("mode"); }
        var option = mode == "newest"
            ? new JsonObject { ["mode"] = "scan", ["details"] = true, ["newest"] = "auto" }
            : JsonNode.Parse(mode == "calibrate" ? CalibrateOption : ScanOption)!;
        if (mode == "scan" && scanId is not null)
        {
            option["scan_id"] = scanId;
            option["resume_record"] = resume ? "latest" : null;
        }
        return new JsonObject
        {
            ["preset"] = null,
            ["tasks"] = new JsonArray(new JsonObject
            {
                ["name"] = TaskName,
                ["option"] = option,
            }),
            ["session_max_minutes"] = SessionMinutes,
            ["task_timeout_s"] = TaskTimeoutS,
            ["allow_spend"] = new JsonArray(),
        };
    }

    internal static IReadOnlyList<string> RunArguments(string repoRoot, CardScanLaunch launch) => UiCardScanSupervisor.RunArguments(repoRoot, launch);

    internal ProcessStartInfo DefaultCommand(CardScanLaunch launch)
    {
        var python = Path.Combine(_repoRoot, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python)) { throw new InvalidOperationException("python_env_missing"); }
        var info = UiEngineSupervisor.NewStartInfo(_repoRoot, python);
        foreach (var argument in RunArguments(_repoRoot, launch)) { info.ArgumentList.Add(argument); }
        return info;
    }

    public string? Start(string mode, int count = 0)
    {
        if (!ValidMode(mode) || !ValidCount(mode, count)) { return "invalid_mode"; }
        _newestCount = mode == "newest" ? count : 0;
        var refusal = Start(mode, continuation: false);
        if (refusal is not null && !Active) { _scanId = _resumeActivity = null; }
        return refusal;
    }

    private static string? InputActivity(JsonObject? status)
    {
        if (status?["manual_click_seq"] is not { } manual || status["run_window"] is not { } window
            || status["clicks_this_run"] is not { } clicks) { return null; }
        return $"{manual}:{window}:{clicks}";
    }

    private string? Start(string mode, bool continuation)
    {
        if (!ValidMode(mode)) { return "invalid_mode"; }
        if (Active) { return "already_running"; }
        if (_blocker() is string blocker) { return blocker; }
        var session = _sessionPath();
        if (string.IsNullOrEmpty(session) || !File.Exists(session)) { return "session_unavailable"; }
        var host = _hostStatus?.Invoke();
        var activity = InputActivity(host);
        var resume = mode == "scan" && continuation && _scanId is not null && _resumeActivity is not null
            && activity == _resumeActivity;
        _scanId = mode == "scan" ? (resume ? _scanId : Guid.NewGuid().ToString("N")) : null;
        _resumeActivity = null;
        var stamp = DateTime.UtcNow.ToString("yyyyMMdd'T'HHmmss'Z'", CultureInfo.InvariantCulture);
        Directory.CreateDirectory(_runsRoot);
        string? folder = null;
        for (var attempt = 1; attempt <= 20 && folder is null; attempt++)
        {
            var candidate = Path.Combine(_runsRoot, attempt == 1 ? stamp : $"{stamp}-{attempt}");
            if (Directory.Exists(candidate)) { continue; }
            try { Directory.CreateDirectory(candidate); folder = candidate; }
            catch (IOException) { }
        }
        if (folder is null) { return "folder_unavailable"; }
        var launch = new CardScanLaunch("knowhow-scan-ui-" + Path.GetFileName(folder), folder, Path.Combine(folder, "config.json"),
            Path.Combine(folder, "events.jsonl"), Path.Combine(folder, "stop.flag"), Path.Combine(folder, "summary.json"), session);
        using (var stream = new FileStream(launch.ConfigPath, FileMode.CreateNew, FileAccess.Write))
        { stream.Write(Encoding.UTF8.GetBytes(BuildConfig(mode, _scanId, resume, mode == "newest" ? _newestCount : 0).ToJsonString(UiMessageRouter.JsonOptions))); }

        ProcessStartInfo info;
        try { info = _command(launch); }
        catch (InvalidOperationException exception) { return ReasonOf(exception.Message, "launch_failed"); }
        var process = new Process { StartInfo = info, EnableRaisingEvents = false };
        var run = new Run(launch, mode, _clockMs(), DateTime.UtcNow)
        {
            Count = mode == "newest" ? _newestCount : 0,
            DeadlineMs = (long)(SessionMinutes * 60_000L) + _limits.DeadlineMarginMs,
            ManualSequence = host?["manual_click_seq"]?.ToJsonString(),
        };
        process.OutputDataReceived += (_, line) => { if (line.Data is not null && run.Stdout.Count < 2_000) { run.Stdout.Enqueue(line.Data); } };
        process.ErrorDataReceived += (_, line) => { if (line.Data is not null && run.Stderr.Count < 2_000) { run.Stderr.Enqueue(line.Data); } };
        try { if (!process.Start()) { process.Dispose(); return "launch_failed"; } }
        catch (System.ComponentModel.Win32Exception) { process.Dispose(); return "launch_failed"; }
        try { process.StandardInput.Close(); } catch (IOException) { }
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        run.Process = process;
        _run = run;
        _scanAll = mode == "scan";
        Log("info", mode switch
        {
            "scan" => resume ? "正在接续本轮书库扫描。" : "书库扫描已开始，将重新读取全库，自动分段完成。",
            "newest" => $"补读新书已开始：按獲得順读最新 {run.Count} 本，只读；请不要触碰游戏画面。",
            _ => "ノウハウ校准已开始：只滚轮测步长、打开 ≤2 本书，不改排序；请不要触碰游戏画面。",
        });
        return null;
    }

    public bool RequestStop() => RequestStop("user_stop");

    private bool RequestStop(string reason)
    {
        _scanAll = false;
        _scanId = _resumeActivity = null;
        if (_continueAtMs is not null)
        {
            _continueAtMs = null;
            if (_last is not null) { _last["outcome"] = "stopped"; _last["reason"] = reason; }
            _finishedSeq++;
            Log("info", "扫描全库已停止，已读结果保留。");
            return true;
        }
        var run = _run;
        if (run is null || run.Finalized) { return false; }
        if (run.StopRequestedMs is null)
        {
            run.StopRequestedMs = _clockMs();
            run.StopReason = reason;
            try { using var _ = new FileStream(run.Launch.StopPath, FileMode.OpenOrCreate, FileAccess.Write, FileShare.ReadWrite); }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
            { Log("error", "无法写入停止文件；宽限期后将终止扫描进程，结果不确定。"); }
            Log("warn", reason switch
            {
                "user_stop" => "正在停止扫描ノウハウ：回到首页后结束。",
                "manual_game_input" => "游戏被手动操作，扫描已暂停；已读结果保留。",
                _ => "扫描ノウハウ超出预定时长，已请求停止。",
            });
        }
        return true;
    }

    public void Tick()
    {
        if (_continueAtMs is long next && _clockMs() >= next)
        {
            _continueAtMs = null;
            var refusal = Start("scan", continuation: true);
            if (refusal is not null)
            {
                _scanAll = false;
                _scanId = _resumeActivity = null;
                if (_last is not null) { _last["outcome"] = "stopped"; _last["reason"] = refusal; }
                Log("warn", "扫描全库已暂停：" + refusal);
            }
        }
        var run = _run;
        if (run is null) { return; }
        if (run.Mode is "scan" or "newest" && run.StopRequestedMs is null && run.ManualSequence is not null
            && _hostStatus?.Invoke()["manual_click_seq"]?.ToJsonString() != run.ManualSequence)
        { RequestStop("manual_game_input"); }
        _ = _blocker();
        while (run.Stdout.TryDequeue(out _)) { }
        while (run.Stderr.TryDequeue(out _)) { }
        TailEvents(run);
        if (run.Ticks++ % 4 == 0) { CountFrames(run); }
        var now = _clockMs();
        if (run.Process!.HasExited)
        {
            run.Process.WaitForExit(_limits.ExitDrainMs);
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
            Log("error", "扫描ノウハウ进程未在宽限期内停止，已终止；结果不确定，请核对游戏画面（排序可能需手动恢复）。");
        }
    }

    public JsonObject Status()
    {
        var run = _run;
        var status = new JsonObject
        {
            ["state"] = _continueAtMs is not null ? "running" : run is null ? (_last is null ? "idle" : "finished") : run.StopRequestedMs is null ? "running" : "stopping",
            ["blocker"] = Active ? null : _blocker(),
            ["finished_seq"] = _finishedSeq,
            ["modes"] = new JsonArray(Modes.Select(m => (JsonNode)JsonValue.Create(m)!).ToArray()),
        };
        if (run is not null)
        {
            status["mode"] = run.Mode;
            if (run.Mode == "newest") { status["count"] = run.Count; }
            status["entered"] = run.Entered;
            status["scrolls"] = run.Scrolls;
            status["books"] = run.Books;
            status["frames"] = run.Frames;
            status["elapsed_s"] = (int)Math.Max(0, (_clockMs() - run.StartMs) / 1000);
        }
        else if (_last is not null)
        {
            foreach (var (key, value) in _last) { status[key] = value?.DeepClone(); }
        }
        if (_scanAll) { status["mode"] = "scan"; }
        return status;
    }

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
            if (end < 0) { if (read == buffer.Length) { run.EventsOffset += read; } return; }
            run.EventsOffset += end + 1;
            foreach (var line in Encoding.UTF8.GetString(buffer, 0, end).Split('\n'))
            {
                if (line.Length == 0 || line.Length > _limits.MaxLineBytes) { continue; }
                ApplyEvent(run, line);
            }
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { }
    }

    /// <summary>Progress from succeeded SC.KnowhowScan.* click nodes: list entered, wheel steps (grid and detail), books opened.</summary>
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
            if (name == "SC.KnowhowScan.IdolList.OpenKnowhow") { run.Entered = true; }
            else if (name is "SC.KnowhowScan.Grid.ScrollDown" or "SC.KnowhowScan.Grid.ScrollUp" or "SC.KnowhowScan.Detail.ScrollDown") { run.Scrolls++; }
            else if (name.StartsWith("SC.KnowhowScan.Grid.OpenTile.", StringComparison.Ordinal)
                || name.StartsWith("SC.KnowhowScan.Grid.OpenSlot.", StringComparison.Ordinal)) { run.Books++; }
        }
        catch (JsonException) { }
    }

    /// <summary>Frames saved for this run: the newest calibrate-&lt;UTC&gt; / scan-&lt;UTC&gt; folder created after the launch.</summary>
    private void CountFrames(Run run)
    {
        try
        {
            if (!Directory.Exists(_framesRoot)) { return; }
            DirectoryInfo? newest = null;
            // the newest-books read is a KnowhowScan scan: its frames go to scan-<UTC>
            foreach (var dir in new DirectoryInfo(_framesRoot).EnumerateDirectories((run.Mode == "newest" ? "scan" : run.Mode) + "-*"))
            {
                if (dir.CreationTimeUtc >= run.StartUtc.AddSeconds(-5) && (newest is null || dir.CreationTimeUtc > newest.CreationTimeUtc)) { newest = dir; }
            }
            if (newest is not null) { run.Frames = newest.EnumerateFiles("frame-*.png").Count(); run.FramesFolder = newest.Name; }
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { }
    }

    private void Finalize(Run run)
    {
        run.Finalized = true;
        CountFrames(run);
        var result = ReadResult(run, run.Process!.ExitCode);
        run.Process.Dispose();
        _run = null;
        _finishedSeq++;
        result["finished_seq"] = _finishedSeq;
        result["elapsed_s"] = (int)Math.Max(0, (_clockMs() - run.StartMs) / 1000);
        _last = result;
        var outcome = (string)result["outcome"]!;
        if (_scanAll && outcome == "partial" && (string?)result["reason"] is "book_budget_exhausted" or "input_budget_exhausted")
        {
            // Capture after the old process exited and its final navigation completed, before the next run-begin.
            _resumeActivity = InputActivity(_hostStatus?.Invoke());
            if (_resumeActivity is not null)
            {
                _continueAtMs = _clockMs() + 31_000;
                Log("info", $"已读取 {result["books_read"]} 本，继续扫描全库。");
                return;
            }
        }
        _scanAll = false;
        _scanId = _resumeActivity = null;
        Log(outcome is "calibrated" or "scanned" or "newest_read" or "library_up_to_date" ? "info" : "warn", outcome switch
        {
            "calibrated" => "ノウハウ校准完成：步长与帧已保存（私有目录 " + (string?)result["frames_folder"] + "）。",
            "scanned" => "扫描ノウハウ完成，书库已更新。",
            "newest_read" => "新书已读入书库。",
            "library_up_to_date" => "书库已是最新，没有新书。",
            "needs_full_scan" => $"所持数与书库对不上（{result["reason"]}）：请读取书库。",
            "partial" => $"扫描ノウハウ只完成一部分（{result["reason"]}）；已读部分标为部分扫描。",
            _ => $"扫描ノウハウ未完成（{outcome}：{result["reason"]}）。",
        });
    }

    /// <summary>Outcome strictly from the run summary's knowhow_scan report.</summary>
    private JsonObject ReadResult(Run run, int exitCode)
    {
        var result = new JsonObject { ["outcome"] = "failed", ["mode"] = run.Mode, ["reason"] = $"engine_exit_{exitCode}", ["imported"] = false,
            ["frames_folder"] = run.FramesFolder is null ? null : ReasonOf(run.FramesFolder, null) };
        if (run.Killed) { result["outcome"] = "uncertain"; result["reason"] = "killed_after_stop"; return result; }
        if (run.StopReason == "manual_game_input")
        { result["outcome"] = "stopped"; result["reason"] = run.StopReason; return result; }
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
                    if (!item.TryGetProperty("kind", out var k) || k.GetString() != "knowhow_scan" || !item.TryGetProperty("outcome", out var o)) { continue; }
                    var text = o.GetString() ?? "";
                    if (text.StartsWith("stopped:", StringComparison.Ordinal)) { stopped = text["stopped:".Length..]; }
                    else if (report.ValueKind == JsonValueKind.Undefined && text != "config_invalid") { report = item.Clone(); }
                    else if (text == "config_invalid") { stopped = "config_invalid"; }
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
            CopyNumber(report, result, "grid_step_rows", "grid_step_rows");
            if (report.TryGetProperty("grid_step_px", out var gs) && gs.ValueKind == JsonValueKind.Object) { CopyNumber(gs, result, "median", "grid_step_px"); }
            if (report.TryGetProperty("detail_step_px", out var ds) && ds.ValueKind == JsonValueKind.Object) { CopyNumber(ds, result, "median", "detail_step_px"); }
            CopyNumber(report, result, "detail_min_row_overlap", "detail_min_row_overlap");
            CopyNumber(report, result, "books", "books_read");
            CopyNumber(report, result, "grid_count", "grid_count");
            if (report.TryGetProperty("scan_ready", out var ready) && ready.ValueKind is JsonValueKind.True or JsonValueKind.False) { result["scan_ready"] = ready.GetBoolean(); }
            if (report.TryGetProperty("grid_complete", out var gc) && gc.ValueKind is JsonValueKind.True or JsonValueKind.False) { result["grid_complete"] = gc.GetBoolean(); }
            if (report.TryGetProperty("sort_restore_needed", out var restore) && restore.ValueKind is JsonValueKind.True or JsonValueKind.False)
            { result["sort_restore_needed"] = restore.GetBoolean(); }
            if (report.TryGetProperty("grid_bottom", out var gb) && gb.ValueKind == JsonValueKind.Object
                && gb.TryGetProperty("reached", out var reached) && reached.ValueKind is JsonValueKind.True or JsonValueKind.False)
            { result["grid_bottom_reached"] = reached.GetBoolean(); }
            if (report.TryGetProperty("review", out var review) && review.ValueKind == JsonValueKind.Object)
            {
                CopyNumber(review, result, "needs_review", "needs_review");
                CopyNumber(review, result, "auto_accepted", "auto_accepted");
            }
            if (outcome is "calibrated" or "scanned" || (outcome is "newest_read" or "library_up_to_date" && run.Mode == "newest"))
            {
                result["outcome"] = outcome;
                result["reason"] = null;
            }
            else if (run.Mode == "newest" && outcome.StartsWith("needs_full_scan:", StringComparison.Ordinal))
            {
                result["outcome"] = "needs_full_scan";
                result["reason"] = ReasonOf(outcome["needs_full_scan:".Length..], "needs_full_scan");
            }
            else if (outcome.StartsWith("partial:", StringComparison.Ordinal))
            {
                result["outcome"] = run.StopReason is not null || stopped.Length > 0 ? "stopped" : "partial";
                result["reason"] = run.StopReason ?? ReasonOf(outcome["partial:".Length..], "partial");
            }
            else { result["reason"] = ReasonOf(stopped.Length > 0 ? stopped : outcome, "failed"); }
            // A scan (complete or partial) rewrites ui/game-assets/knowhow.js with flagged coverage; copy it for the panel.
            if (run.Mode is "scan" or "newest" && outcome.Length > 0) { result["imported"] = PublishImport(run); }
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException or InvalidOperationException)
        {
            result["outcome"] = "failed";
            result["reason"] = "summary_unreadable";
        }
        return result;
    }

    private static void CopyNumber(JsonElement from, JsonObject to, string key, string name)
    {
        if (from.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number))
        { to[name] = Math.Round(number, 3); }
    }

    private bool PublishImport(Run run)
    {
        try
        {
            var from = Path.GetFullPath(Path.Combine(_repoRoot, "ui", "game-assets", ImportModule));
            var info = new FileInfo(from);
            if (!info.Exists || info.Length > 8 * 1024 * 1024 || info.LastWriteTimeUtc < run.StartUtc.AddSeconds(-5)) { return false; }
            Directory.CreateDirectory(_outputGameAssets);
            var to = Path.Combine(_outputGameAssets, ImportModule);
            if (!string.Equals(from, Path.GetFullPath(to), StringComparison.OrdinalIgnoreCase)) { File.Copy(from, to, overwrite: true); }
            return true;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { return false; }
    }

    private static string? ReasonOf(string? text, string? fallback) => text is not null && ReasonCode().IsMatch(text) ? text : fallback;

    private void Log(string level, string text) =>
        Output?.Invoke("log.append", new JsonObject { ["level"] = level, ["kind"] = "knowhow_scan", ["text"] = text, ["task"] = TaskName });

    public void Dispose()
    {
        _scanAll = false;
        _scanId = _resumeActivity = null;
        _continueAtMs = null;
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
        internal Run(CardScanLaunch launch, string mode, long startMs, DateTime startUtc) { Launch = launch; Mode = mode; StartMs = startMs; StartUtc = startUtc; }
        internal CardScanLaunch Launch { get; }
        internal string Mode { get; }
        internal int Count { get; init; }
        internal long StartMs { get; }
        internal DateTime StartUtc { get; }
        internal long DeadlineMs { get; init; }
        internal Process? Process { get; set; }
        internal System.Collections.Concurrent.ConcurrentQueue<string> Stdout { get; } = new();
        internal System.Collections.Concurrent.ConcurrentQueue<string> Stderr { get; } = new();
        internal long EventsOffset { get; set; }
        internal int Ticks { get; set; }
        internal bool Entered { get; set; }
        internal int Scrolls { get; set; }
        internal int Books { get; set; }
        internal int Frames { get; set; }
        internal string? FramesFolder { get; set; }
        internal long? StopRequestedMs { get; set; }
        internal string? StopReason { get; set; }
        internal bool Killed { get; set; }
        internal bool Finalized { get; set; }
        internal string? ManualSequence { get; init; }
    }
}

/// <summary>
/// Readiness gate for 「扫描ノウハウ」: the card-scan conditions (<see cref="UiCardScanGate"/>) with the knowhow readiness
/// signal, plus no card scan running. The first failing condition wins; null = nothing blocks a start.
/// </summary>
internal static class UiKnowhowScanGate
{
    internal static string? Blocker(JsonObject host, JsonObject policy, bool engineActive, bool cardScanActive,
        bool recentExternalInput)
    {
        if (host["pipeline_input_enabled"]?.GetValue<bool>() != true) { return "pipeline_input_disabled"; }
        if (policy["knowhow_scan_ready"] is not JsonValue ready || !ready.TryGetValue<bool>(out var ok) || !ok)
        { return policy["knowhow_scan_reason"] is JsonValue reason && reason.TryGetValue<string>(out var text) ? Sanitize(text) : "knowhow_scan_not_ready"; }
        if (host["connected"]?.GetValue<bool>() != true) { return "host_not_connected"; }
        if (host["host_guard_reason"] is not null) { return "host_guard_active"; }
        if (engineActive) { return "engine_active"; }
        if (cardScanActive) { return "card_scan_active"; }
        // 2026-10-28: no click caps, no input-budget check.
        if (recentExternalInput) { return "other_input_activity"; }
        return null;
    }

    private static string Sanitize(string text) => text.Length is > 0 and <= 120 && text.All(c => char.IsAsciiLetterOrDigit(c) || c is '_' or ':' or ',' or '-') ? text : "knowhow_scan_not_ready";
}
