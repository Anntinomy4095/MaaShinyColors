using Microsoft.Web.WebView2.Core;

namespace MaaShinyColors.BrowserHost;

internal static class NativeGesture
{
    internal static async Task Send(Action<CoreWebView2MouseEventKind, CoreWebView2MouseEventVirtualKeys> send)
    {
        // Canvas scenes (the Fes live) sample the pointer once per rendered frame: hover first for
        // a couple of frames and hold the press long enough to be seen (observed live 2026-10-03).
        send(CoreWebView2MouseEventKind.Move, CoreWebView2MouseEventVirtualKeys.None);
        await Task.Delay(50);
        send(CoreWebView2MouseEventKind.Move, CoreWebView2MouseEventVirtualKeys.None);
        await Task.Delay(30);
        Exception? primary = null;
        try
        {
            send(CoreWebView2MouseEventKind.LeftButtonDown, CoreWebView2MouseEventVirtualKeys.LeftButton);
            await Task.Delay(120);
        }
        catch (Exception exception) { primary = exception; throw; }
        finally
        {
            try { send(CoreWebView2MouseEventKind.LeftButtonUp, CoreWebView2MouseEventVirtualKeys.None); }
            catch (Exception release) when (primary is not null)
            { throw new AggregateException("按下与释放均失败，不重试。", primary, release); }
        }
    }
}
