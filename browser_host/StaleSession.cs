using System.Diagnostics;
using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

/// <summary>
/// A bridge session file (and its <c>session.json.*</c> siblings: pipeline input, navigation, …) left behind by a host that
/// was ended without cleanup. The bridges create their files with <c>CreateNew</c> so one host never replaces another
/// host's endpoint; a file whose pid no longer runs a BrowserHost has no owner and only blocked the next start
/// (2026-10-24: 「Composition 测试宿主失败」 after a forced close). A live host's files, or an unreadable file, stay.
/// </summary>
internal static class StaleSession
{
    /// <summary>Deletes the session and its siblings when their host is gone; true when anything was removed.</summary>
    internal static bool Clear(string sessionFile, Func<int, DateTime, bool>? hostAlive = null)
    {
        if (!File.Exists(sessionFile)) { return false; }
        int pid;
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(sessionFile));
            if (!doc.RootElement.TryGetProperty("pid", out var value) || !value.TryGetInt32(out pid)) { return false; }
        }
        catch (Exception exception) when (exception is IOException or JsonException or UnauthorizedAccessException) { return false; }
        if (pid == Environment.ProcessId || (hostAlive ?? HostAlive)(pid, File.GetLastWriteTimeUtc(sessionFile))) { return false; }
        var full = Path.GetFullPath(sessionFile);
        var removed = false;
        foreach (var file in Directory.GetFiles(Path.GetDirectoryName(full)!, Path.GetFileName(full) + ".*").Append(full))
        {
            try { File.Delete(file); removed = true; }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { }
        }
        return removed;
    }

    /// <summary>The pid runs a MaaShinyColors host that started before the file was written (a reused pid does not count).</summary>
    private static bool HostAlive(int pid, DateTime writtenUtc)
    {
        try
        {
            using var process = Process.GetProcessById(pid);
            return !process.HasExited && process.ProcessName.StartsWith("MaaShinyColors", StringComparison.OrdinalIgnoreCase)
                && process.StartTime.ToUniversalTime() <= writtenUtc.AddSeconds(5);
        }
        catch (ArgumentException) { return false; }
        catch (InvalidOperationException) { return false; }
        catch (System.ComponentModel.Win32Exception) { return true; }   // no access to that process: treat as alive
    }
}
