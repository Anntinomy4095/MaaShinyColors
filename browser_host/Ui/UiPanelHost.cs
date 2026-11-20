using System.Drawing;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// The isolated UI panel WebView2: its own CoreWebView2Environment and user-data folder,
/// no Chromium switches, only https://maa-ui.test/ (virtual host mapped to the local ui folder, cross-origin access
/// denied). Every other navigation, frame navigation, new window, download, permission and network request is refused
/// and recorded. The host never injects script, never calls DevTools Protocol methods and never touches cookies; the
/// page talks to the host only through the whitelisted UiMessageRouter. This class never touches a game WebView.
/// </summary>
internal sealed class UiPanelHost : IDisposable
{
    private readonly WebView2 _view;
    private readonly string _uiFolder;
    private readonly string _userDataFolder;
    private readonly bool _devTools;
    // 250 ms: tails the engine run events; status.update is posted every fourth tick (once per second).
    private readonly System.Windows.Forms.Timer _statusTimer = new() { Interval = 250 };
    private int _ticks;
    private readonly object _gate = new();
    private readonly List<string> _blockedNavigations = new();
    private readonly List<string> _blockedRequests = new();
    private readonly HashSet<string> _allowedRequestHosts = new(StringComparer.OrdinalIgnoreCase);
    private readonly List<string> _documents = new();
    private UiMessageRouter? _router;
    private CoreWebView2? _core;

    internal UiPanelHost(string uiFolder, string userDataFolder, bool devTools, Color background)
    {
        _uiFolder = Path.GetFullPath(uiFolder);
        _userDataFolder = Path.GetFullPath(userDataFolder);
        _devTools = devTools;
        _view = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = background, AllowExternalDrop = false };
        _statusTimer.Tick += (_, _) =>
        {
            _router?.Tick();
            if (++_ticks % 4 == 0) { _router?.EmitStatus(); }
        };
    }

    internal static string HomeUri => UiProtocol.Origin + "index.html";

    internal Control View => _view;
    internal int AllowedRequests { get; private set; }
    internal int NewWindowsBlocked { get; private set; }
    internal int DownloadsBlocked { get; private set; }
    internal int PermissionsDenied { get; private set; }
    internal int ProcessFailures { get; private set; }
    internal string? LastError { get; private set; }

    internal IReadOnlyList<string> BlockedNavigations { get { lock (_gate) { return _blockedNavigations.ToList(); } } }
    internal IReadOnlyList<string> BlockedRequests { get { lock (_gate) { return _blockedRequests.ToList(); } } }
    internal IReadOnlyList<string> AllowedRequestHosts { get { lock (_gate) { return _allowedRequestHosts.ToList(); } } }
    /// <summary>Every top-level document that finished navigating (must all be https://maa-ui.test/...).</summary>
    internal IReadOnlyList<string> Documents { get { lock (_gate) { return _documents.ToList(); } } }

    /// <summary>Raised for every received message before routing (self-test observation only; never answered).</summary>
    internal event Action<string, string>? MessageObserved;

    internal event Action<bool>? NavigationFinished;

    internal static bool IsUiUri(string? uri) =>
        Uri.TryCreate(uri, UriKind.Absolute, out var parsed)
        && parsed.Scheme == Uri.UriSchemeHttps
        && string.Equals(parsed.Host, UiProtocol.VirtualHost, StringComparison.OrdinalIgnoreCase)
        && parsed.IsDefaultPort
        && string.IsNullOrEmpty(parsed.UserInfo);

    internal async Task InitializeAsync(UiMessageRouter router)
    {
        _router = router;
        if (!File.Exists(Path.Combine(_uiFolder, "index.html"))) { throw new FileNotFoundException("界面文件缺失：" + Path.Combine(_uiFolder, "index.html")); }
        Directory.CreateDirectory(_userDataFolder);
        // A separate environment and profile from any game WebView; no AdditionalBrowserArguments (no keepalive switches).
        var environment = await CoreWebView2Environment.CreateAsync(null, _userDataFolder, new CoreWebView2EnvironmentOptions
        {
            AreBrowserExtensionsEnabled = false,
            AllowSingleSignOnUsingOSPrimaryAccount = false,
        });
        await _view.EnsureCoreWebView2Async(environment);
        var core = _view.CoreWebView2;
        _core = core;
        var settings = core.Settings;
        settings.AreHostObjectsAllowed = false;
        settings.AreDefaultContextMenusEnabled = false;
        settings.IsPasswordAutosaveEnabled = false;
        settings.IsGeneralAutofillEnabled = false;
        settings.IsStatusBarEnabled = false;
        settings.AreDefaultScriptDialogsEnabled = false;
        settings.IsZoomControlEnabled = false;
        settings.IsSwipeNavigationEnabled = false;
        settings.IsPinchZoomEnabled = false;
        settings.IsWebMessageEnabled = true;
        settings.AreDevToolsEnabled = _devTools;
        settings.AreBrowserAcceleratorKeysEnabled = _devTools;
        core.NavigationStarting += (_, args) =>
        {
            if (IsUiUri(args.Uri)) { return; }
            args.Cancel = true;
            Remember(_blockedNavigations, args.Uri);
        };
        core.FrameNavigationStarting += (_, args) =>
        {
            if (IsUiUri(args.Uri)) { return; }
            args.Cancel = true;
            Remember(_blockedNavigations, "frame:" + args.Uri);
        };
        core.NewWindowRequested += (_, args) =>
        {
            args.Handled = true;
            NewWindowsBlocked++;
            Remember(_blockedNavigations, "new-window:" + args.Uri);
        };
        core.DownloadStarting += (_, args) =>
        {
            args.Cancel = true;
            args.Handled = true;
            DownloadsBlocked++;
        };
        core.PermissionRequested += (_, args) =>
        {
            args.State = CoreWebView2PermissionState.Deny;
            args.Handled = true;
            PermissionsDenied++;
        };
        core.ProcessFailed += (_, args) => { ProcessFailures++; LastError = "WebView2 进程失败：" + args.ProcessFailedKind; };
        core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
        core.WebResourceRequested += (_, args) =>
        {
            var uri = args.Request.Uri;
            // Virtual-host (maa-ui.test) files are served inside WebView2 and normally never reach this handler;
            // anything that does reach it and is not maa-ui.test is refused here, before it leaves the WebView.
            if (IsUiUri(uri))
            {
                AllowedRequests++;
                lock (_gate) { _allowedRequestHosts.Add(new Uri(uri).Host); }
                return;
            }
            args.Response = environment.CreateWebResourceResponse(null, 403, "Blocked", "Content-Type: text/plain");
            Remember(_blockedRequests, uri);
        };
        core.WebMessageReceived += OnMessage;
        core.NavigationCompleted += (_, args) =>
        {
            Remember(_documents, core.Source);
            NavigationFinished?.Invoke(args.IsSuccess);
        };
        router.EventReady += PostEvent;
        core.SetVirtualHostNameToFolderMapping(UiProtocol.VirtualHost, _uiFolder, CoreWebView2HostResourceAccessKind.Deny);
        _statusTimer.Start();
        core.Navigate(HomeUri);
    }

    internal void Reload() => _core?.Navigate(HomeUri);

    /// <summary>Theme change: the colour shown before the page paints, matching the host chrome.</summary>
    internal void SetBackground(Color background) => _view.DefaultBackgroundColor = background;

    /// <summary>
    /// Page zoom of the docked panel. The dock keeps it at 1, so the text follows only the system
    /// DPI and 外观 → 字号 while the game column scales with the window. Display only; never touches the game view.
    /// </summary>
    internal void SetZoom(double zoom, bool force = false)
    {
        if (force || Math.Abs(_view.ZoomFactor - zoom) > 1e-4) { _view.ZoomFactor = zoom; }
    }

    internal double Zoom => _view.ZoomFactor;

    /// <summary>Saves a PNG of the UI panel itself (the local UI page; never a game frame, never the capture bridge).</summary>
    internal async Task SavePanelPngAsync(string path)
    {
        if (_core is null) { throw new InvalidOperationException("界面面板尚未就绪。"); }
        var core = _core;
        byte[] png;
        try { png = await CaptureAsync().WaitAsync(TimeSpan.FromSeconds(15)); }
        catch (TimeoutException exception)
        {
            throw new TimeoutException($"WebView2 截图超时（{Path.GetFileName(path)}，运行时 {core.Environment.BrowserVersionString}，可见={_view.Visible}，尺寸={_view.Size}）。", exception);
        }
        if (png.Length == 0) { throw new InvalidDataException("WebView2 截图为空。"); }
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        await File.WriteAllBytesAsync(path, png);

        async Task<byte[]> CaptureAsync()
        {
            using var stream = new MemoryStream();
            await core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream);
            return stream.ToArray();
        }
    }

    internal uint BrowserProcessId => _core?.BrowserProcessId ?? 0;

    private void OnMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        string? json;
        try { json = args.WebMessageAsJson; }
        catch (ArgumentException) { json = null; }
        MessageObserved?.Invoke(args.Source, json ?? "");
        var reply = _router?.Handle(args.Source, json);
        if (reply is not null && _core is not null && string.Equals(_core.Source, args.Source, StringComparison.Ordinal))
        { _core.PostWebMessageAsJson(reply); }
    }

    private void PostEvent(string json)
    {
        // Events go only to the exact whitelisted page.
        if (_core is not null && _router is not null && string.Equals(_core.Source, HomeUri, StringComparison.Ordinal))
        { _core.PostWebMessageAsJson(json); }
    }

    private void Remember(List<string> list, string? uri)
    {
        var text = uri ?? "(null)";
        lock (_gate)
        {
            if (list.Count < 200) { list.Add(text.Length > 200 ? text[..200] : text); }
        }
    }

    public void Dispose()
    {
        _statusTimer.Stop();
        _statusTimer.Dispose();
        if (_router is not null) { _router.EventReady -= PostEvent; }
        _view.Dispose();
    }
}
