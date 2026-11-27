using System.Diagnostics;

namespace MaaShinyColors.BrowserHost;

/// <summary>
/// 设置 → 更新 → 「重启并更新」 (2026-11-27): starts the package's own launcher with the fixed argument --update; it waits
/// for this host to close and applies the update staged under dev_assets/private/update/staged. Nothing else is started.
/// </summary>
internal static class UpdateLauncher
{
    internal const string Argument = "--update";

    /// <summary>True when <c>&lt;root&gt;\MaaShinyColors.exe --update</c> started.</summary>
    internal static bool Start(string root)
    {
        var launcher = Path.Combine(root, "MaaShinyColors.exe");
        if (!File.Exists(launcher)) { return false; }
        try
        {
            using var process = Process.Start(new ProcessStartInfo(launcher, Argument) { WorkingDirectory = root, UseShellExecute = false });
            return process is not null;
        }
        catch (System.ComponentModel.Win32Exception) { return false; }
    }
}
