using System.Diagnostics;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// Keeps the panel's local display modules current. Python writers (scans, runs, offline tools) rewrite the repo's
/// <c>ui/game-assets/*.js</c>, but the panel is served from the host's output ui folder; the host flows copy their own
/// file when they finish, while a scan or run started elsewhere (2026-10-24: command-line ノウハウ scan segments) left the
/// panel on the old copy. Every few seconds this copies each whitelisted module whose repo copy differs in write time or size
/// (the repo is the source; the copy keeps the write time) and is at least <see cref="SettledMs"/> old (a file being
/// written is left for the next check), and counts a version per module; the page re-reads
/// a module when its version in <c>status.update.game_assets</c> moves. Fixed names only, size-limited, no other paths.
/// </summary>
internal sealed class UiGameAssetSync
{
    internal const int IntervalMs = 3000;
    internal const int SettledMs = 2000;

    internal static readonly IReadOnlyDictionary<string, long> Modules = new Dictionary<string, long>(StringComparer.Ordinal)
    {
        ["knowhow.js"] = 8L * 1024 * 1024,
        ["owned-levels.js"] = 8L * 1024 * 1024,
        ["inventory-scan.js"] = 8L * 1024 * 1024,
        ["item-scan.js"] = 8L * 1024 * 1024,
        ["passive-readings.js"] = 8L * 1024 * 1024,
        ["team-proposal.js"] = 8L * 1024 * 1024,
        ["team-snapshot.js"] = 64L * 1024,
        ["produce-end-last.js"] = 256L * 1024,
    };

    private readonly string _source;
    private readonly string _target;
    private readonly Func<DateTime> _utcNow;
    private readonly Dictionary<string, int> _versions = new(StringComparer.Ordinal);
    private long _lastCheck;
    private int _seq;

    internal UiGameAssetSync(string repoRoot, string? outputGameAssets = null, Func<DateTime>? utcNow = null)
    {
        _source = Path.GetFullPath(Path.Combine(repoRoot, "ui", "game-assets"));
        _target = Path.GetFullPath(outputGameAssets ?? Path.Combine(UiPaths.OutputUiFolder(), "game-assets"));
        _utcNow = utcNow ?? (() => DateTime.UtcNow);
    }

    /// <summary>Panel timer: at most one check per <see cref="IntervalMs"/>.</summary>
    internal void Tick()
    {
        var now = Stopwatch.GetTimestamp();
        if (_lastCheck != 0 && Stopwatch.GetElapsedTime(_lastCheck, now).TotalMilliseconds < IntervalMs) { return; }
        _lastCheck = now;
        SyncNow();
    }

    /// <summary>Copies every whitelisted module that differs from the repo; returns the names copied.</summary>
    internal IReadOnlyList<string> SyncNow()
    {
        var copied = new List<string>();
        if (string.Equals(_source, _target, StringComparison.OrdinalIgnoreCase)) { return copied; }
        foreach (var (name, maxBytes) in Modules)
        {
            try
            {
                var from = new FileInfo(Path.Combine(_source, name));
                if (!from.Exists || from.Length > maxBytes) { continue; }
                if ((_utcNow() - from.LastWriteTimeUtc).TotalMilliseconds < SettledMs) { continue; }
                var to = new FileInfo(Path.Combine(_target, name));
                if (to.Exists && to.LastWriteTimeUtc == from.LastWriteTimeUtc && to.Length == from.Length) { continue; }   // mirror of the repo
                Directory.CreateDirectory(_target);
                File.Copy(from.FullName, to.FullName, overwrite: true);   // keeps the source write time
                _versions[name] = _versions.GetValueOrDefault(name) + 1;
                copied.Add(name);
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { }
        }
        if (copied.Count > 0) { _seq += 1; }
        return copied;
    }

    /// <summary>status.update game_assets: copy count and per-module versions (names only, no paths).</summary>
    internal JsonObject Status()
    {
        var modules = new JsonObject();
        foreach (var (name, version) in _versions) { modules[name] = version; }
        return new JsonObject { ["seq"] = _seq, ["modules"] = modules };
    }
}
