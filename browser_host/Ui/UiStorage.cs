using System.Globalization;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// 设置 → 存储: disk usage of the local data under dev_assets/private by player category, per-category 清理 and
/// optional 自动清理 (default off; files older than N days, at host start and after a run ends). Only files the shipped app
/// writes are categorised (<see cref="Classify"/>); everything else is 其他 and never deleted. Protected data (settings,
/// WebView2 profiles / login, scan records, ledgers, memory, readings, live status, policy / risk / spend files) is excluded
/// by <see cref="IsProtected"/> before any category rule. Deletion resolves real paths inside the private root, never
/// enters or deletes reparse points (symlinks / junctions), deletes files then the emptied folders and reports freed bytes.
/// Scans and deletions run on a background thread; the UI thread only reads the cached snapshot. Refused while a run, a
/// scan or an external run is active (busy delegate). Never touches the game.
/// </summary>
internal sealed class UiStorage
{
    internal const string Samples = "samples";
    internal const string Logs = "logs";
    /// <summary>手牌识别: W.I.N.G. hand-card portraits (wing-cache/); derived data, never auto-pruned.</summary>
    internal const string Cache = "cache";
    internal static readonly string[] Categories = { Samples, Logs, Cache };
    internal static readonly int[] RetentionDays = { 7, 14, 30 };
    internal const int DefaultDays = 14;
    /// <summary>运行记录 newer than this are kept: today's / yesterday's game-day logs feed daily progress and session caps.</summary>
    internal static readonly TimeSpan RecentLogGuard = TimeSpan.FromHours(48);
    /// <summary>A cached size older than this is refreshed when the page asks (storage.status).</summary>
    internal static readonly TimeSpan StaleAfter = TimeSpan.FromMinutes(5);

    // First path segment (case-insensitive) of folders that are never deleted, whatever their content.
    private static readonly HashSet<string> ProtectedTop = new(StringComparer.OrdinalIgnoreCase)
    {
        "readings", "live-status", "mission-claim", "present", "daily_shop", "daily_grade", "daily_shop_native", "daily_grade_native",
        "daily_navigation", "daily_navigation_loop", "daily_navigation_repeatable", "readonly_scroll", "composition_navigation",
        "resource_native", "models", "game-assets-src", "true_end", "checkpoints",
    };

    private readonly string _root;
    private readonly Func<string?> _busy;
    private readonly Func<DateTime> _utcNow;
    private readonly object _sync = new();
    private Func<(bool Auto, int Days)> _readPrefs;
    private Func<bool, int, bool> _writePrefs;
    private JsonObject? _snapshot;          // last finished size scan (categories / other / total)
    private DateTime? _scannedAt;
    private string _state = "idle";         // idle / scanning / cleaning
    private JsonObject? _last;              // last clean / prune result
    private Task? _work;
    private bool _started;
    private bool _wasBusy;
    private bool _prunePending;

    internal UiStorage(string privateRoot, Func<string?> busy, Func<(bool Auto, int Days)> readPrefs, Func<bool, int, bool> writePrefs,
        Func<DateTime>? utcNow = null)
    {
        _root = Path.TrimEndingDirectorySeparator(Path.GetFullPath(privateRoot));
        _busy = busy;
        _readPrefs = readPrefs;
        _writePrefs = writePrefs;
        _utcNow = utcNow ?? (() => DateTime.UtcNow);
    }

    /// <summary>Extra host activity (a CLI run sharing the host); null = idle.</summary>
    internal Func<string?>? HostBusy { get; set; }
    internal string Root => _root;

    /// <summary>The game host owns the host prefs record (it rewrites the whole file): route storage prefs through it.</summary>
    internal void BindPrefs(Func<(bool Auto, int Days)> read, Func<bool, int, bool> write)
    {
        _readPrefs = read;
        _writePrefs = write;
    }

    internal (bool Auto, int Days) Prefs
    {
        get
        {
            var (auto, days) = _readPrefs();
            return (auto, RetentionDays.Contains(days) ? days : DefaultDays);
        }
    }

    internal bool Working { get { lock (_sync) { return _state != "idle"; } } }

    /// <summary>First reason nothing may be deleted now (null = idle): engine run, scans, or CLI run.</summary>
    internal string? Blocker() => _busy() ?? HostBusy?.Invoke();

    // ------------------------------------------------------------------ classification

    /// <summary>
    /// Category of a file path relative to the private root ('/' or '\' separators), or null (其他 / never deleted).
    /// KeepDepth = number of leading segments of the folder that is kept even when it becomes empty.
    /// </summary>
    internal static (string Category, int KeepDepth)? Classify(string relative)
    {
        var s = Segments(relative);
        if (s.Length == 0 || IsProtected(relative)) { return null; }
        var top = s[0].ToLowerInvariant();
        var name = s[^1];
        // 截图样本: task sample / diagnostic frames, scan frame folders (records stay), captured-frame sets, agent frames.
        if (top == "recordings" && s.Length >= 2) { return (Samples, 1); }
        if (top == "run_logs" && s.Length >= 3 && s[1].ToLowerInvariant() is "samples" or "diagnostics") { return (Samples, 2); }
        if (top is "card-scan" or "knowhow-scan" && s.Length >= 3) { return (Samples, 1); }
        if (top.StartsWith("captures-", StringComparison.Ordinal) && top.Length > 9 && char.IsAsciiDigit(top[9]) && s.Length >= 2) { return (Samples, 0); }
        if (top == "browser_captures" && s.Length >= 2) { return (Samples, 1); }
        // 运行记录: per-day task / host logs and run summaries, UI run folders, scan run folders, decision logs, agent requests.
        if (top == "run_logs" && s.Length == 2 && HasExtension(name, ".jsonl", ".json", ".log", ".txt")) { return (Logs, 1); }
        if (top == "ui" && s.Length >= 4 && s[1].Equals("runs", StringComparison.OrdinalIgnoreCase)) { return (Logs, 2); }
        if (top is "card-scan-ui" or "knowhow-scan-ui" && s.Length >= 3) { return (Logs, 1); }
        if (top == "wing-ui" && s.Length >= 3 && s[1].Equals("decision-log", StringComparison.OrdinalIgnoreCase)) { return (Logs, 2); }
        // 手牌识别: re-learned by one inspect tap per card when deleted.
        if (top == "wing-cache" && s.Length >= 2) { return (Cache, 1); }
        return null;
    }

    /// <summary>
    /// Never deleted, whatever a category rule says: settings / profiles / window prefs, WebView2 profiles (login), scan
    /// records (top-level JSON of card-scan / knowhow-scan, used for resume), W.I.N.G. memory / settings / spend ledger,
    /// readings, live status, claim / shop / grade / navigation ledgers, policy / risk / spend / ledger / session files.
    /// </summary>
    internal static bool IsProtected(string relative)
    {
        var s = Segments(relative);
        if (s.Length == 0) { return true; }
        var top = s[0];
        var name = s[^1].ToLowerInvariant();
        if (ProtectedTop.Contains(top)) { return true; }
        if (s.Any(part => part.Contains("webview2", StringComparison.OrdinalIgnoreCase) || part.EndsWith("-profile", StringComparison.OrdinalIgnoreCase)))
        { return true; }
        if (top.Equals("ui", StringComparison.OrdinalIgnoreCase) && (s.Length <= 2 || !s[1].Equals("runs", StringComparison.OrdinalIgnoreCase))) { return true; }
        if (top.Equals("wing-ui", StringComparison.OrdinalIgnoreCase) && s.Length <= 2) { return true; }
        if ((top.Equals("card-scan", StringComparison.OrdinalIgnoreCase) || top.Equals("knowhow-scan", StringComparison.OrdinalIgnoreCase)) && s.Length <= 2)
        { return true; }
        if (s.Length == 1) { return true; }   // files directly under dev_assets/private (e.g. wing-spend-ledger.json)
        foreach (var word in new[] { "host-window", "settings", "memory", "ledger", "spend", "policy", "risk", "session.json", "readings" })
        {
            if (name.Contains(word, StringComparison.Ordinal)) { return true; }
        }
        return false;
    }

    private static string[] Segments(string relative) =>
        relative.Split(new[] { '/', '\\' }, StringSplitOptions.RemoveEmptyEntries);

    private static bool HasExtension(string name, params string[] extensions) =>
        extensions.Any(ext => name.EndsWith(ext, StringComparison.OrdinalIgnoreCase));

    // ------------------------------------------------------------------ walking

    internal sealed record Entry(string FullPath, string Relative, long Length, DateTime WriteUtc, string? Category, int KeepDepth);

    /// <summary>Every regular file under the root; reparse points (symlinks / junctions) are neither entered nor listed.</summary>
    internal IEnumerable<Entry> Walk()
    {
        if (!Directory.Exists(_root) || IsReparse(_root)) { yield break; }
        var stack = new Stack<string>();
        stack.Push(_root);
        while (stack.Count > 0)
        {
            var dir = stack.Pop();
            IEnumerable<FileSystemInfo> children;
            try { children = new DirectoryInfo(dir).EnumerateFileSystemInfos().ToList(); }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { continue; }
            foreach (var child in children)
            {
                FileAttributes attributes;
                try { attributes = child.Attributes; }
                catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { continue; }
                if (attributes.HasFlag(FileAttributes.ReparsePoint)) { continue; }
                if (attributes.HasFlag(FileAttributes.Directory)) { stack.Push(child.FullName); continue; }
                var info = (FileInfo)child;
                var relative = Path.GetRelativePath(_root, info.FullName);
                var cls = Classify(relative);
                long length;
                DateTime write;
                try { length = info.Length; write = info.LastWriteTimeUtc; }
                catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { continue; }
                yield return new Entry(info.FullName, relative, length, write, cls?.Category, cls?.KeepDepth ?? 0);
            }
        }
    }

    private static bool IsReparse(string path)
    {
        try { return File.GetAttributes(path).HasFlag(FileAttributes.ReparsePoint); }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { return true; }
    }

    /// <summary>True when an entry may be deleted by 清理 (olderThanUtc = null) or by 自动清理 (files written before it).</summary>
    internal bool Eligible(Entry entry, string? category, DateTime? olderThanUtc)
    {
        if (entry.Category is null || (category is not null && entry.Category != category)) { return false; }
        if (entry.Category == Logs && _utcNow() - entry.WriteUtc < RecentLogGuard) { return false; }
        if (olderThanUtc is not null && entry.Category == Cache) { return false; }   // 自动清理 keeps the cache
        if (olderThanUtc is { } limit && entry.WriteUtc >= limit) { return false; }
        return true;
    }

    /// <summary>Synchronous size scan (background thread in the host; direct in the self-test).</summary>
    internal JsonObject ScanNow()
    {
        var totals = Categories.ToDictionary(key => key, _ => new long[4]);   // bytes, files, cleanable bytes, cleanable files
        long other = 0, otherFiles = 0;
        foreach (var entry in Walk())
        {
            if (entry.Category is null) { other += entry.Length; otherFiles++; continue; }
            var t = totals[entry.Category];
            t[0] += entry.Length; t[1]++;
            if (Eligible(entry, null, null)) { t[2] += entry.Length; t[3]++; }
        }
        var categories = new JsonArray();
        long total = other;
        foreach (var key in Categories)
        {
            var t = totals[key];
            total += t[0];
            categories.Add(new JsonObject { ["key"] = key, ["bytes"] = t[0], ["files"] = t[1], ["cleanable_bytes"] = t[2], ["cleanable_files"] = t[3] });
        }
        return new JsonObject { ["categories"] = categories, ["other_bytes"] = other, ["other_files"] = otherFiles, ["total_bytes"] = total };
    }

    internal sealed record CleanResult(long FreedBytes, int Files, int Failed);

    /// <summary>Deletes eligible files (one category, or every category for a prune), then their emptied folders.</summary>
    internal CleanResult DeleteNow(string? category, DateTime? olderThanUtc)
    {
        var candidates = Walk().Where(entry => Eligible(entry, category, olderThanUtc)).ToList();
        long freed = 0;
        int files = 0, failed = 0;
        var folders = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        var reparseCache = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
        var prefix = _root + Path.DirectorySeparatorChar;
        foreach (var entry in candidates)
        {
            var full = Path.GetFullPath(entry.FullPath);
            // Re-checked at delete time: inside the root, still a regular file, no reparse point on the way, still categorised.
            if (!full.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) || Classify(Path.GetRelativePath(_root, full)) is null
                || IsReparse(full) || AncestorReparse(Path.GetDirectoryName(full)!, reparseCache))
            { failed++; continue; }
            try
            {
                var info = new FileInfo(full);
                if (!info.Exists) { continue; }
                var length = info.Length;
                if (info.IsReadOnly) { info.IsReadOnly = false; }
                info.Delete();
                freed += length;
                files++;
                folders[Path.GetDirectoryName(full)!] = entry.KeepDepth;
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { failed++; }
        }
        foreach (var (folder, keepDepth) in folders.OrderByDescending(pair => pair.Key.Length)) { RemoveEmpty(folder, keepDepth); }
        return new CleanResult(freed, files, failed);
    }

    private bool AncestorReparse(string dir, Dictionary<string, bool> cache)
    {
        var current = dir;
        while (current.Length > _root.Length)
        {
            if (!cache.TryGetValue(current, out var reparse)) { cache[current] = reparse = IsReparse(current); }
            if (reparse) { return true; }
            current = Path.GetDirectoryName(current)!;
        }
        return false;
    }

    /// <summary>Removes the folder and its empty parents (non-recursive deletes) while deeper than keepDepth segments.</summary>
    private void RemoveEmpty(string folder, int keepDepth)
    {
        var current = folder;
        while (current.StartsWith(_root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
            && Segments(Path.GetRelativePath(_root, current)).Length > keepDepth)
        {
            try
            {
                if (IsReparse(current) || Directory.EnumerateFileSystemEntries(current).Any()) { return; }
                Directory.Delete(current, recursive: false);
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { return; }
            current = Path.GetDirectoryName(current)!;
        }
    }

    // ------------------------------------------------------------------ host commands (UI thread)

    /// <summary>storage.status: the cached snapshot; refresh (or a stale / missing one) starts a background size scan.</summary>
    internal JsonObject Status(bool refresh = false)
    {
        if (refresh || _scannedAt is null || _utcNow() - _scannedAt > StaleAfter) { StartScan(); }
        return Snapshot();
    }

    /// <summary>status.update storage: no paths, sizes only.</summary>
    internal JsonObject Snapshot()
    {
        var (auto, days) = Prefs;
        lock (_sync)
        {
            var result = _snapshot is null ? new JsonObject { ["categories"] = null, ["other_bytes"] = null, ["total_bytes"] = null } : (JsonObject)_snapshot.DeepClone();
            result["state"] = _state;
            result["scanned_at"] = _scannedAt?.ToString("O", CultureInfo.InvariantCulture);
            result["auto"] = new JsonObject { ["enabled"] = auto, ["days"] = days };
            result["blocker"] = Blocker();
            result["last"] = _last?.DeepClone();
            return result;
        }
    }

    /// <summary>storage.clean: refusal text or null (accepted; the result arrives in status.update storage.last).</summary>
    internal string? Clean(string category)
    {
        if (!Categories.Contains(category)) { return "没有这个分类。"; }
        if (Blocker() is string busy) { return BusyText(busy); }
        lock (_sync)
        {
            if (_state != "idle") { return _state == "cleaning" ? "正在清理，请稍候。" : "正在计算占用，请稍候。"; }
            _state = "cleaning";
        }
        Run(() => Finish("clean", category, DeleteNow(category, null)));
        return null;
    }

    /// <summary>storage.auto: saves the switch and retention days; refusal text or null.</summary>
    internal string? SetAuto(bool enabled, int days)
    {
        if (!RetentionDays.Contains(days)) { return "保留天数只能是 7、14 或 30 天。"; }
        if (!_writePrefs(enabled, days)) { return "设置保存失败。"; }
        if (enabled) { _prunePending = true; }
        return null;
    }

    /// <summary>UI thread, often: 自动清理 once at host start and once after each run / scan / recording ends.</summary>
    internal void Tick()
    {
        var busy = Blocker() is not null;
        if (!_started) { _started = true; _prunePending = true; }
        if (_wasBusy && !busy) { _prunePending = true; }
        _wasBusy = busy;
        if (!_prunePending || busy || Working) { return; }
        _prunePending = false;
        var (auto, days) = Prefs;
        if (!auto) { return; }
        lock (_sync) { _state = "cleaning"; }
        var limit = _utcNow() - TimeSpan.FromDays(days);
        Run(() => Finish("prune", null, DeleteNow(null, limit), days));
    }

    /// <summary>Self-test helper: waits for background work.</summary>
    internal bool Wait(TimeSpan timeout) => _work?.Wait(timeout) ?? true;

    private void StartScan()
    {
        lock (_sync)
        {
            if (_state != "idle") { return; }
            _state = "scanning";
        }
        Run(() => { });
    }

    /// <summary>Runs the work and then a size scan on a background thread; the state returns to idle afterwards.</summary>
    private void Run(Action work)
    {
        _work = Task.Run(() =>
        {
            try { work(); }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or InvalidOperationException)
            {
                lock (_sync) { _last = new JsonObject { ["op"] = "clean", ["ok"] = false, ["reason"] = exception.GetType().Name, ["at"] = Now() }; }
            }
            JsonObject? snapshot = null;
            try { snapshot = ScanNow(); }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { }
            lock (_sync)
            {
                if (snapshot is not null) { _snapshot = snapshot; _scannedAt = _utcNow(); }
                _state = "idle";
            }
        });
    }

    private void Finish(string op, string? category, CleanResult result, int? days = null)
    {
        lock (_sync)
        {
            _last = new JsonObject
            {
                ["op"] = op, ["category"] = category, ["ok"] = result.Failed == 0, ["freed_bytes"] = result.FreedBytes,
                ["files"] = result.Files, ["failed"] = result.Failed, ["days"] = days, ["at"] = Now(),
            };
        }
    }

    private string Now() => _utcNow().ToString("O", CultureInfo.InvariantCulture);

    internal static string BusyText(string code) => code switch
    {
        "engine_active" => "任务运行中，结束后再清理。",
        "card_scan_active" => "正在扫描持有卡，结束后再清理。",
        "knowhow_scan_active" => "正在扫描ノウハウ，结束后再清理。",
        "external_run" => "有任务正在操作游戏，结束后再清理。",
        _ => "现在不能清理（" + code + "）。",
    };
}
