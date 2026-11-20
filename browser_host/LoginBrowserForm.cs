using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace MaaShinyColors.BrowserHost;

/// <summary>Owned browser window sharing the game's environment, so OAuth opener/close semantics stay intact.</summary>
internal sealed class LoginBrowserForm : Form
{
    private readonly WebView2 _browser = new() { Dock = DockStyle.Fill, DefaultBackgroundColor = Color.White };

    internal LoginBrowserForm()
    {
        Text = "登录 · MaaShinyColors";
        StartPosition = FormStartPosition.CenterParent;
        ClientSize = new Size(900, 720);
        MinimumSize = new Size(560, 500);
        Controls.Add(_browser);
    }

    internal async Task<CoreWebView2> InitializeAsync(CoreWebView2Environment environment,
        EventHandler<CoreWebView2NewWindowRequestedEventArgs> openWindow)
    {
        await _browser.EnsureCoreWebView2Async(environment);
        var core = _browser.CoreWebView2;
        core.Settings.AreDevToolsEnabled = false;
        core.Settings.AreHostObjectsAllowed = false;
        core.Settings.IsPasswordAutosaveEnabled = false;
        core.Settings.IsGeneralAutofillEnabled = false;
        core.NavigationStarting += (_, navigation) => navigation.Cancel = !BrowserNavigationPolicy.CanBrowse(navigation.Uri);
        core.NavigationCompleted += (_, _) =>
        {
            if (Uri.TryCreate(core.Source, UriKind.Absolute, out var uri) && uri.Scheme == Uri.UriSchemeHttps)
            { Text = "登录 · " + uri.Host + " · MaaShinyColors"; }
        };
        core.NewWindowRequested += openWindow;
        core.WindowCloseRequested += (_, _) => Close();
        return core;
    }
}
