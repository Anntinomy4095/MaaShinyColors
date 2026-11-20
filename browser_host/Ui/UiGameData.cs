using Microsoft.Web.WebView2.Core;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>Clear only the game WebView profile through the official API; never inspect stored contents.</summary>
internal sealed class UiGameData(Func<bool> ready, Func<string?> blocker,
    Func<CoreWebView2BrowsingDataKinds, Task> clear, Action reload)
{
    internal bool Busy { get; private set; }
    internal Task Pending { get; private set; } = Task.CompletedTask;
    private JsonObject? _last;
    internal event Action? Changed;

    internal JsonObject Status(bool muted) => new()
    {
        ["muted"] = muted, ["clear_supported"] = ready(), ["busy"] = Busy,
        ["last"] = _last?.DeepClone(),
    };

    internal string? Start(string kind, bool confirmed)
    {
        if (kind is not ("cache" or "login")) { return "未知的清理类型。"; }
        if (kind == "login" && !confirmed) { return "请先确认清除登录信息。"; }
        if (!ready()) { return "游戏画面尚未就绪。"; }
        if (Busy) { return "正在清理游戏数据，请稍候。"; }
        if (blocker() is { } reason) { return reason; }
        Busy = true;
        _last = null;
        Pending = ClearAsync(kind);
        return null;
    }

    private async Task ClearAsync(string kind)
    {
        // Keep acceptance separate from completion, including when a test delegate completes synchronously.
        await Task.Yield();
        var ok = false;
        string? reason = null;
        try
        {
            // SDK: DiskCache does not include cookies/DOM storage; AllSite includes both.
            await clear(kind == "cache" ? CoreWebView2BrowsingDataKinds.DiskCache : CoreWebView2BrowsingDataKinds.AllSite);
            try { reload(); ok = true; }
            catch (Exception) { reason = "数据已清理，但游戏刷新失败，请手动刷新。"; }
        }
        catch (Exception) { reason = "游戏数据清理失败，请稍后重试。"; }
        finally
        {
            Busy = false;
            _last = new JsonObject
            {
                ["op"] = kind, ["ok"] = ok, ["reason"] = reason,
                ["at"] = DateTimeOffset.UtcNow.ToString("O"),
            };
        }
        Changed?.Invoke();
    }
}
