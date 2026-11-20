using System.Text.Json;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// Reader for the run's live status file (<c>dev_assets/private/live-status/current.json</c>, written atomically and at
/// most twice per second by <c>agent/core/live_status.py</c>) that becomes <c>status.update.run_live</c> for the panel's
/// 「运行中」 view. Display only: no input, no paths or screenshots reach the page.
/// Wired in <c>UiMessageRouter.EmitStatus</c> (game host only, 2026-10-10).
/// </summary>
internal sealed class UiLiveStatusReader
{
    internal const int MaxBytes = 48 * 1024;
    private readonly string _path;
    private readonly Func<DateTime> _utcNow;
    private DateTime _cachedWrite = DateTime.MinValue;
    private long _cachedLength = -1;
    private JsonObject? _cached;

    /// <param name="repoRoot">Repository root (the folder that holds <c>dev_assets</c>).</param>
    internal UiLiveStatusReader(string repoRoot, Func<DateTime>? utcNow = null)
    {
        _path = Path.Combine(repoRoot, "dev_assets", "private", "live-status", "current.json");
        _utcNow = utcNow ?? (() => DateTime.UtcNow);
    }

    /// <summary>A copy of the last snapshot plus <c>age_s</c> (seconds since the file was written), or null when there is
    /// no file, it is too large, unreadable, or not a version-1 snapshot. Re-parses only when the file changed.</summary>
    internal JsonObject? Read()
    {
        try
        {
            var info = new FileInfo(_path);
            if (!info.Exists || info.Length <= 0 || info.Length > MaxBytes
                || (info.Attributes & FileAttributes.ReparsePoint) != 0) { return null; }
            if (info.LastWriteTimeUtc != _cachedWrite || info.Length != _cachedLength)
            {
                _cached = Parse(ReadShared(_path));
                _cachedWrite = info.LastWriteTimeUtc;
                _cachedLength = info.Length;
            }
            if (_cached is null) { return null; }
            var copy = (JsonObject)_cached.DeepClone();
            copy["age_s"] = Math.Max(0, (int)Math.Round((_utcNow() - info.LastWriteTimeUtc).TotalSeconds));
            return copy;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            return null;   // the writer replaced the file mid-read: the next tick reads the new one
        }
    }

    private static string ReadShared(string path)
    {
        // FileShare.Delete lets the writer's os.replace succeed while we read.
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd();
    }

    /// <summary>Validates the shape the page expects; unknown top-level keys are dropped.</summary>
    internal static JsonObject? Parse(string text)
    {
        if (text.Length > MaxBytes || JsonNode.Parse(text) is not JsonObject root) { return null; }
        if (root["version"] is not JsonValue version || !version.TryGetValue<int>(out var v) || v != 1) { return null; }
        if (root["run_id"] is not JsonValue runId || !runId.TryGetValue<string>(out var id) || id.Length is 0 or > 64) { return null; }
        var allowed = new[] { "version", "run_id", "state", "outcome", "outcome_text", "started_at", "updated_at", "elapsed_s",
            "tasks", "current", "step", "progress", "clicks", "last_frame_at", "wing" };
        var clean = new JsonObject();
        foreach (var key in allowed)
        {
            if (root[key] is JsonNode node) { clean[key] = node.DeepClone(); }
        }
        return LooksLikePath(clean.ToJsonString()) ? null : clean;
    }

    /// <summary>Defence in depth: the writer never puts paths in the snapshot; refuse one that contains a drive path.</summary>
    private static bool LooksLikePath(string json) =>
        json.Contains(":\\\\", StringComparison.Ordinal) || json.Contains("dev_assets", StringComparison.Ordinal);
}
