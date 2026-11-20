using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>Public card-data updater. It has no game/input dependency and accepts no page-supplied command or paths.</summary>
internal sealed class UiCardLibrary : IDisposable
{
    internal const int TimeoutMs = 90 * 60 * 1000;
    /// <summary>2026-10-31 (user): reading the card library (panel start, 推荐编队, 扫描持有卡) refreshes it first, at most
    /// once per interval; a failed or offline attempt also waits. The manual 「更新卡资料」 stays as it is.</summary>
    internal static readonly TimeSpan AutoInterval = TimeSpan.FromHours(12);
    private const int MaxOutputChars = 16 * 1024 * 1024;
    private readonly string _repoRoot;
    private readonly string _outputGameAssets;
    private readonly Func<ProcessStartInfo>? _command;
    private readonly object _gate = new();
    private Process? _process;
    private Task? _pending;
    private bool _disposed;
    private bool _stopRequested;
    private string _state = "idle";
    private string? _phase;
    private string? _error;
    private int _updatedCards;
    private int _totalCards;
    private int _finishedSeq;
    private JsonObject? _last;
    private DateTimeOffset? _lastChecked;
    private DateTimeOffset? _lastAuto;
    private bool _auto;

    internal UiCardLibrary(string repoRoot, Func<ProcessStartInfo>? command = null, string? outputGameAssets = null)
    {
        _repoRoot = Path.GetFullPath(repoRoot);
        _outputGameAssets = Path.GetFullPath(outputGameAssets ?? Path.Combine(UiPaths.OutputUiFolder(), "game-assets"));
        _command = command;
        try
        {
            var path = Path.Combine(_repoRoot, "dev_assets", "private", "shinycolorsdb", "last-update.json");
            if (File.Exists(path) && JsonNode.Parse(File.ReadAllText(path)) is JsonObject report && ValidReport(report))
            {
                _last = Summary(report);
                _lastChecked = DateTimeOffset.TryParse(Text(report["checked_at"]), CultureInfo.InvariantCulture,
                    DateTimeStyles.AssumeUniversal, out var at) ? at : new DateTimeOffset(File.GetLastWriteTimeUtc(path));
            }
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or System.Text.Json.JsonException) { }
        // 2026-11-23: icons unpacked into the install folder (the private resource pack) or left by an earlier update
        // reach the served UI copy once, at start, instead of waiting for the next card-library update.
        if (!AssetsMissing && !File.Exists(Path.Combine(_outputGameAssets, "manifest.json"))) { PublishAssets(); }
    }

    internal ProcessStartInfo DefaultCommand()
    {
        var python = Path.Combine(_repoRoot, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python)) { throw new InvalidOperationException("python_env_missing"); }
        var info = UiEngineSupervisor.NewStartInfo(_repoRoot, python);
        info.RedirectStandardInput = false;
        foreach (var argument in new[] { "-X", "utf8", "tools/update_card_library.py", "--fetch", "--all", "--with-icons", "--progress-json" })
        { info.ArgumentList.Add(argument); }
        return info;
    }

    /// <summary>Null means accepted. Starts only the fixed local updater; completion is observed in Status.</summary>
    internal string? Start()
    {
        lock (_gate)
        {
            if (_disposed) { return "disposed"; }
            if (_process is not null) { return "already_running"; }
            _phase = "icons"; _updatedCards = 0; _totalCards = 0; _error = null; _stopRequested = false; _auto = false;
            ProcessStartInfo info;
            try { info = (_command ?? DefaultCommand)(); }
            catch (Exception exception) when (exception is InvalidOperationException or IOException or UnauthorizedAccessException)
            { Finish("failed", "python_env_missing"); return "python_env_missing"; }
            var process = new Process { StartInfo = info };
            try
            {
                if (!process.Start()) { throw new InvalidOperationException("not started"); }
            }
            catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception or IOException)
            {
                process.Dispose(); Finish("failed", "start_failed"); return "start_failed";
            }
            _process = process; _state = "running";
            _pending = Task.Run(() => Supervise(process));
            return null;
        }
    }

    internal bool Running { get { lock (_gate) { return _process is not null; } } }

    /// <summary>2026-11-22 (new install): the local card icons that the owned-card scan and the 资源 page need. A fresh
    /// folder, or one whose dev_assets were copied from another PC (last-update.json says "checked"), has none.</summary>
    internal bool AssetsMissing => !File.Exists(Path.Combine(_repoRoot, "ui", "game-assets", "manifest.json"));

    /// <summary>2026-11-23: the updater fetches images first; once it moves on to the card details the icons are
    /// published and an owned-card scan may start beside the (slow, network-only) details download.</summary>
    internal bool IconsReady
    {
        get
        {
            if (AssetsMissing) { return false; }
            lock (_gate) { return _process is null || _phase is "catalog" or "cards"; }
        }
    }

    /// <summary>Null = an automatic refresh started; "fresh" = checked or tried within <see cref="AutoInterval"/>;
    /// otherwise <see cref="Start"/>'s refusal (e.g. "already_running").</summary>
    internal string? StartIfStale(DateTimeOffset now)
    {
        lock (_gate)
        {
            if (_disposed) { return "disposed"; }
            if (_process is not null) { return "already_running"; }
            // missing icons are never "fresh": a stopped or copied-over check must not leave the scan without them
            if (!AssetsMissing && (Recent(_lastChecked, now) || Recent(_lastAuto, now))) { return "fresh"; }
            _lastAuto = now;
        }
        var refusal = Start();
        if (refusal is null) { lock (_gate) { _auto = true; } }
        return refusal;
    }

    /// <summary>Stops only an automatic refresh (a run or scan is starting) and lets the next read try again.</summary>
    internal bool StopAuto()
    {
        lock (_gate)
        {
            if (!_auto || _process is null) { return false; }
            _lastAuto = null;
        }
        return RequestStop();
    }

    private static bool Recent(DateTimeOffset? at, DateTimeOffset now) => at is DateTimeOffset t && t <= now && now - t < AutoInterval;

    internal bool RequestStop()
    {
        lock (_gate)
        {
            if (_process is null || _stopRequested) { return false; }
            _stopRequested = true;
            Kill(_process);
            return true;
        }
    }

    private static void Kill(Process process)
    {
        try { if (!process.HasExited) { process.Kill(entireProcessTree: true); } }
        catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception) { }
    }

    private async Task Supervise(Process process)
    {
        var stdout = DrainOutput(process.StandardOutput);
        var stderr = DrainProgress(process.StandardError);
        string? failure = null;
        string output = "";
        try
        {
            try { await process.WaitForExitAsync().WaitAsync(TimeSpan.FromMilliseconds(TimeoutMs)); }
            catch (TimeoutException) { failure = "timeout"; Kill(process); }
            if (!process.HasExited) { await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5)); }
            await Task.WhenAll(stdout, stderr).WaitAsync(TimeSpan.FromSeconds(5));
            output = await stdout;
            if (failure is null && process.ExitCode != 0) { failure = "exit_" + process.ExitCode; }
        }
        catch (Exception exception) when (exception is IOException or InvalidOperationException or TimeoutException)
        { failure ??= "process_failed"; Kill(process); }

        lock (_gate)
        {
            JsonObject? report = null;
            try { report = JsonNode.Parse(output) as JsonObject; }
            catch (System.Text.Json.JsonException) { }
            if (report is not null && !ValidReport(report)) { report = null; }
            if (report is not null && !_stopRequested) { _lastChecked = DateTimeOffset.UtcNow; }
            if (_stopRequested)
            {
                if (report is not null) { _last = Summary(report); }
                Finish("stopped", null);
            }
            else if (failure is not null && !(failure.StartsWith("exit_", StringComparison.Ordinal) && report is not null
                && Text(report["status"]) == "stopped" && (CardsComplete(report) || Number(report["updated_cards"]) > 0)))
            { Finish("failed", failure); }
            else if (report is null) { Finish("failed", "invalid_report"); }
            else
            {
                _last = Summary(report);
                _updatedCards = Number(report["updated_cards"]);
                _totalCards = Number(report["catalog_cards"]);
                var stopped = Text(report["status"]) == "stopped";
                var partial = stopped || !CardsComplete(report) || Boolean(report["complete"]) == false
                    || Number(report["remaining_cards"]) > 0 || Count(report["icons"]?["failed"]) > 0
                    || IsStopped(report["icons"]?["stopped"]);
                var published = PublishAssets();
                var partialResult = partial || !published;
                // Older updaters use status=stopped even when only optional icons failed.
                var usableResult = CardsComplete(report) || Number(report["updated_cards"]) > 0;
                Finish(stopped && !usableResult ? "stopped" : partialResult ? "partial" : "done", published ? null : "publish_failed");
            }
            process.Dispose();
        }
    }

    private static async Task<string> DrainOutput(StreamReader reader)
    {
        var output = new StringBuilder();
        var buffer = new char[4096];
        int count;
        var overflow = false;
        while ((count = await reader.ReadAsync(buffer.AsMemory())) != 0)
        {
            if (output.Length + count <= MaxOutputChars) { output.Append(buffer, 0, count); }
            else { overflow = true; }
        }
        return overflow ? "" : output.ToString();
    }

    private async Task DrainProgress(StreamReader reader)
    {
        string? line;
        while ((line = await reader.ReadLineAsync()) is not null)
        {
            JsonObject? progress;
            try { progress = JsonNode.Parse(line) as JsonObject; }
            catch (System.Text.Json.JsonException) { continue; }
            if (progress is null || Text(progress["event"]) != "card_library_progress") { continue; }
            var phase = Text(progress["phase"]);
            if (phase is not ("catalog" or "cards" or "icons")) { continue; }
            lock (_gate)
            {
                _phase = phase;
                if (progress["updated_cards"] is not null) { _updatedCards = Number(progress["updated_cards"]); }
                if (progress["total_cards"] is not null) { _totalCards = Number(progress["total_cards"]); }
            }
        }
    }

    /// <summary>Publish only public generated data. Never overwrite owned/book/team/personal modules.</summary>
    private bool PublishAssets()
    {
        try
        {
            var source = Path.GetFullPath(Path.Combine(_repoRoot, "ui", "game-assets"));
            foreach (var name in new[] { "manifest.json", "manifest.js" })
            { if (!File.Exists(Path.Combine(source, name))) { return false; } }
            if (string.Equals(source.TrimEnd(Path.DirectorySeparatorChar), _outputGameAssets.TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase))
            { return true; }
            Directory.CreateDirectory(_outputGameAssets);
            foreach (var name in new[] { "manifest.json", "manifest.js" })
            { File.Copy(Path.Combine(source, name), Path.Combine(_outputGameAssets, name), overwrite: true); }
            foreach (var name in new[] { "cards", "items", "icons" })
            {
                var folder = Path.Combine(source, name);
                if (Directory.Exists(folder)) { CopyPublicFolder(folder, Path.Combine(_outputGameAssets, name)); }
            }
            return true;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { return false; }
    }

    private static void CopyPublicFolder(string source, string target)
    {
        if ((File.GetAttributes(source) & FileAttributes.ReparsePoint) != 0) { throw new IOException("linked_asset_folder"); }
        Directory.CreateDirectory(target);
        foreach (var path in Directory.EnumerateFiles(source))
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) { throw new IOException("linked_asset_file"); }
            File.Copy(path, Path.Combine(target, Path.GetFileName(path)), overwrite: true);
        }
        foreach (var path in Directory.EnumerateDirectories(source))
        { CopyPublicFolder(path, Path.Combine(target, Path.GetFileName(path))); }
    }

    private void Finish(string state, string? error)
    {
        _process = null; _state = state; _error = error; _phase = null; _finishedSeq++;
    }

    private static string? Text(JsonNode? value) => value is JsonValue scalar && scalar.TryGetValue<string>(out var text) ? text : null;
    private static bool? Boolean(JsonNode? value) => value is JsonValue scalar && scalar.TryGetValue<bool>(out var flag) ? flag : null;
    private static bool IsStopped(JsonNode? value) => Boolean(value) == true || !string.IsNullOrWhiteSpace(Text(value));
    private static int Number(JsonNode? value) => value is JsonValue scalar && scalar.TryGetValue<int>(out var count) ? Math.Max(0, count) : 0;
    private static int Count(JsonNode? value) => value is JsonArray array ? array.Count : 0;
    private static bool ValidReport(JsonObject report)
    {
        if (Text(report["status"]) is not ("updated" or "unchanged" or "stopped")) { return false; }
        if (report["icons"] is not null && report["icons"] is not JsonObject) { return false; }
        if (report["icons"]?["failed"] is not null && report["icons"]?["failed"] is not JsonArray) { return false; }
        foreach (var field in new[] { "added", "changed" })
        { if (report[field] is not null && report[field] is not JsonArray) { return false; } }
        foreach (var field in new[] { "complete", "cards_complete" })
        { if (report[field] is not null && Boolean(report[field]) is null) { return false; } }
        return true;
    }
    private static bool CardsComplete(JsonObject report) => Boolean(report["cards_complete"])
        ?? (Text(report["status"]) is "updated" or "unchanged" && Number(report["remaining_cards"]) == 0);

    private static int LocalCards(JsonObject report)
    {
        if (report["local_cards"] is not null) { return Number(report["local_cards"]); }
        var added = report["added"] as JsonArray;
        var enabledAdded = 0;
        if (added is not null)
        {
            // Older successful reports retained the original pending plan even after remaining_cards reached zero.
            if (CardsComplete(report) && Number(report["remaining_cards"]) == 0) { enabledAdded = added.Count; }
            else if (report["pending"] is JsonArray pending)
            {
                var pendingIds = pending.Select(Text).Where(id => id is not null).ToHashSet(StringComparer.Ordinal);
                enabledAdded = added.Count(id => Text(id) is string cardId && !pendingIds.Contains(cardId));
            }
            else
            {
                // The updater enables added ids before changed ids. Never count changed cards as new cards.
                var enabled = report["updated_cards"] is not null ? Number(report["updated_cards"])
                    : Math.Max(0, added.Count + Count(report["changed"]) - Number(report["remaining_cards"]));
                enabledAdded = Math.Min(added.Count, enabled);
            }
        }
        return Number(report["previous_cards"]) + enabledAdded;
    }

    private static JsonObject Summary(JsonObject report) => new()
    {
        ["checked_at"] = Text(report["checked_at"]), ["status"] = Text(report["status"]),
        ["cards_complete"] = CardsComplete(report), ["local_cards"] = LocalCards(report),
        ["catalog_cards"] = Number(report["catalog_cards"]), ["added_cards"] = Count(report["added"]),
        ["changed_cards"] = Count(report["changed"]), ["updated_cards"] = Number(report["updated_cards"]),
        ["remaining_cards"] = Number(report["remaining_cards"]), ["icons_failed"] = Count(report["icons"]?["failed"]),
        ["icons_stopped"] = IsStopped(report["icons"]?["stopped"]),
    };

    internal JsonObject Status()
    {
        lock (_gate)
        {
            return new JsonObject
            {
                ["supported"] = true, ["state"] = _state, ["phase"] = _phase, ["auto"] = _auto,
                ["updated_cards"] = _updatedCards, ["total_cards"] = _totalCards, ["finished_seq"] = _finishedSeq,
                ["error"] = _error, ["last"] = _last?.DeepClone(),
            };
        }
    }

    public void Dispose()
    {
        Task? pending;
        lock (_gate)
        {
            if (_disposed) { return; }
            _disposed = true;
            if (_process is not null) { _stopRequested = true; Kill(_process); }
            pending = _pending;
        }
        try { pending?.Wait(TimeSpan.FromSeconds(6)); }
        catch (AggregateException) { }
    }
}
