using System.Diagnostics;

namespace MaaShinyColors.BrowserHost;

/// <summary>
/// 设置 → 问题反馈 / 关于 / 更新: opens Explorer at a host-chosen dev_assets/private folder or file, or the
/// default browser at a https://github.com/ page of interface.json's repository. Callers pass fixed host paths / URLs only
/// (the page names an operation, never a target); nothing here touches the game, capture or input.
/// </summary>
internal static class ShellOpen
{
    internal static void Folder(string path) =>
        Process.Start(new ProcessStartInfo("explorer.exe", "\"" + Path.GetFullPath(path) + "\"") { UseShellExecute = false });

    internal static void SelectFile(string path) =>
        Process.Start(new ProcessStartInfo("explorer.exe", "/select,\"" + Path.GetFullPath(path) + "\"") { UseShellExecute = false });

    internal static void GitHubPage(string url)
    {
        if (!url.StartsWith("https://github.com/", StringComparison.Ordinal)) { throw new ArgumentException("not a GitHub page", nameof(url)); }
        Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
    }
}
