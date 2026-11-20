using System.Drawing;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// U4: the isolated UI panel docked next to the game composition view. The game host
/// passes only two delegates: an in-process status snapshot (never a screenshot) and the path of its own capture-bridge
/// session (handed to the read-only engine child, never to the page). The panel WebView has its own environment and
/// profile folder and only loads https://maa-ui.test/; it never touches the game WebView, its input or its frames.
/// </summary>
internal sealed class UiGameDock : IDisposable
{
    internal const int PanelWidth = UiShellForm.DefaultPanelWidth;

    /// <summary>
    /// --self-test-ui-dock only: a private profile file and UI WebView2 folder under the self-test output, so the
    /// light/dark screenshots never read or write the user's profile.json or share a running host's UI browser.
    /// Null (the default) keeps the normal dev_assets/private/ui paths.
    /// </summary>
    internal static (string Profile, string UserData)? SelfTestPaths { get; set; }

    internal UiGameDock(Func<JsonObject> hostStatus, Func<string?> sessionPath, IUiDevTools? devTools = null,
        Action<UiRunNotification>? notify = null)
    {
        var root = UiPaths.RepoRoot();
        var data = Path.Combine(root, "dev_assets", "private");
        var uiFolder = UiPaths.OutputUiFolder();
        var paths = SelfTestPaths;
        Protocol = UiProtocol.LoadEmbedded();
        Schema = UiTaskSchema.LoadFromUiFolder(uiFolder);
        Profiles = new UiProfileStore(paths?.Profile ?? Path.Combine(data, "ui", "profile.json"), Schema);
        HostPrefsPath = paths is { } own ? Path.ChangeExtension(own.Profile, ".host-window.json") : Path.Combine(data, "ui", "host-window.json");
        Engine = new UiEngineSupervisor(root);
        var notifications = new UiRunNotifications();
        notifications.SetProfile(Profiles.Load());
        Engine.Output += (name, data) =>
        {
            if (notifications.Handle(name, data) is { } note) { notify?.Invoke(note); }
        };
        _root = root;
        _hostStatus = hostStatus;
        CardScan = new UiCardScanSupervisor(root, CardScanBlocker, sessionPath);
        KnowhowScan = new UiKnowhowScanSupervisor(root, KnowhowScanBlocker, sessionPath, hostStatus: hostStatus);
        Router = new UiMessageRouter(Protocol, Schema, Profiles, engine: Engine, hostStatus: hostStatus, sessionPath: sessionPath,
            replayRoot: Path.Combine(data, "ui", "replay"), policy: () => UiPolicySummary.Load(root), devTools: devTools, cardScan: CardScan,
            wingBlocker: WingBlocker, knowhowScan: KnowhowScan, teamPropose: new UiTeamPropose(root, () => Profiles.Path));
        CardLibrary = new UiCardLibrary(root);
        Router.CardLibrary = CardLibrary;
        // Modules rewritten outside the host flows (command-line scans, offline tools): copy before the panel loads, then poll.
        Router.AssetSync = new UiGameAssetSync(root);
        Router.AssetSync.SyncNow();
        // 设置 → 存储: sizes / 清理 / 自动清理 of dev_assets/private (the game form re-binds the prefs to its own record).
        var prefsPath = HostPrefsPath;
        Storage = new UiStorage(data, () => Engine.Active ? "engine_active" : CardScan.Active ? "card_scan_active"
                : KnowhowScan.Active ? "knowhow_scan_active" : null,
            () => { var prefs = UiHostPrefs.Load(prefsPath); return (prefs.StorageAuto, prefs.StorageDays); },
            (auto, days) => (UiHostPrefs.Load(prefsPath) with { StorageAuto = auto, StorageDays = days }).Save(prefsPath));
        Router.Storage = Storage;
        // 设置 → 关于 / 问题反馈 / 更新 / 通知测试 / 紧急停止热键; the game form wires notifications and the hotkey.
        Router.Support = Support = new UiSupport(root);
        var palette = Palette = UiShellForm.ShellPalette.For(UiShellForm.ThemeOf(Profiles.Load()));
        Panel = new UiPanelHost(uiFolder, paths?.UserData ?? Path.Combine(data, "ui-webview2-profile"), devTools: false, palette.Background);
        // a saved theme recolours the host chrome together with the page (UI thread: raised from WebMessageReceived).
        Router.ProfileSaved += saved =>
        {
            notifications.SetProfile(saved);
            ApplyPalette(saved);
        };
        Microsoft.Win32.SystemEvents.UserPreferenceChanged += OnUserPreferenceChanged;
    }

    private readonly string _root;
    private readonly Func<JsonObject> _hostStatus;
    private bool _disposed;
    private JsonObject? _policyCache;
    private long _policyAtMs;
    private int _lastClicks = -1;
    private long _lastClickChangeMs = long.MinValue / 2;
    private const long ExternalInputWindowMs = 20_000;

    /// <summary>
    /// First reason 「扫描持有卡」 cannot start now (null = ready): pipeline input off, card-scan actions/entry closed, host
    /// guard, an engine run, too little host input budget, or clicks that did not come from this panel's
    /// own scan within the last 20 s (a CLI supervised run sharing the host). Policy files are re-read at most every 5 s.
    /// </summary>
    private string? CardScanBlocker()
    {
        var now = Environment.TickCount64;
        if (_policyCache is null || now - _policyAtMs > 5_000) { _policyCache = UiPolicySummary.Load(_root); _policyAtMs = now; }
        var host = _hostStatus();
        var clicks = host["clicks_this_run"]?.GetValue<int>() ?? 0;
        if (_lastClicks >= 0 && clicks != _lastClicks && !ScanActive && !Engine.Active) { _lastClickChangeMs = now; }
        _lastClicks = clicks;
        if (KnowhowScan?.Active == true) { return "knowhow_scan_active"; }
        return UiCardScanGate.Blocker(host, _policyCache, Engine.Active,
            now - _lastClickChangeMs < ExternalInputWindowMs);
    }

    /// <summary>Either self-scrolling scan of this panel owns the host input (its clicks are not "external").</summary>
    private bool ScanActive => CardScan?.Active == true || KnowhowScan?.Active == true;

    /// <summary>First reason 「扫描ノウハウ」 cannot start now (null = ready): the card-scan conditions with the knowhow readiness.</summary>
    private string? KnowhowScanBlocker()
    {
        var now = Environment.TickCount64;
        if (_policyCache is null || now - _policyAtMs > 5_000) { _policyCache = UiPolicySummary.Load(_root); _policyAtMs = now; }
        var host = _hostStatus();
        var clicks = host["clicks_this_run"]?.GetValue<int>() ?? 0;
        if (_lastClicks >= 0 && clicks != _lastClicks && !ScanActive && !Engine.Active) { _lastClickChangeMs = now; }
        _lastClicks = clicks;
        return UiKnowhowScanGate.Blocker(host, _policyCache, Engine.Active, CardScan?.Active == true,
            now - _lastClickChangeMs < ExternalInputWindowMs);
    }

    /// <summary>
    /// First reason the supervised W.I.N.G. run (任务 page, run.start supervised_wing) cannot start now (null = ready): the
    /// card-scan conditions without the card-scan action readiness (<see cref="UiWingGate"/>).
    /// </summary>
    private string? WingBlocker()
    {
        var now = Environment.TickCount64;
        var host = _hostStatus();
        var clicks = host["clicks_this_run"]?.GetValue<int>() ?? 0;
        if (_lastClicks >= 0 && clicks != _lastClicks && !ScanActive && !Engine.Active) { _lastClickChangeMs = now; }
        _lastClicks = clicks;
        return UiWingGate.Blocker(host, ScanActive,
            now - _lastClickChangeMs < ExternalInputWindowMs);
    }

    internal UiCardScanSupervisor CardScan { get; }
    internal UiKnowhowScanSupervisor KnowhowScan { get; }
    internal UiCardLibrary CardLibrary { get; }
    internal UiStorage Storage { get; }
    internal UiSupport Support { get; }
    /// <summary>The host's own window settings file (<see cref="UiHostPrefs"/>); the self-test uses one under its output.</summary>
    internal string HostPrefsPath { get; }
    internal UiProtocol Protocol { get; }
    internal UiTaskSchema Schema { get; }
    internal UiProfileStore Profiles { get; }
    internal UiEngineSupervisor Engine { get; }
    internal UiMessageRouter Router { get; }
    internal UiPanelHost Panel { get; }
    internal UiShellForm.ShellPalette Palette { get; private set; }
    internal Color Background => Palette.Background;
    internal event Action<UiShellForm.ShellPalette>? PaletteChanged;
    internal Control View => Panel.View;

    internal Task InitializeAsync() => Panel.InitializeAsync(Router);

    private void ApplyPalette(JsonObject profile)
    {
        var palette = UiShellForm.ShellPalette.For(UiShellForm.ThemeOf(profile));
        if (Palette == palette) { return; }
        Palette = palette;
        Panel.SetBackground(palette.Background);
        PaletteChanged?.Invoke(palette);
    }

    private void OnUserPreferenceChanged(object sender, Microsoft.Win32.UserPreferenceChangedEventArgs args)
    {
        if (_disposed || !View.IsHandleCreated) { return; }
        // SystemEvents can arrive on its own thread. Keep native chrome changes on the window thread.
        try { View.BeginInvoke(() => { if (!_disposed) { ApplyPalette(Profiles.Load()); } }); }
        catch (InvalidOperationException) when (_disposed || View.IsDisposed) { }
    }

    public void Dispose()
    {
        _disposed = true;
        Microsoft.Win32.SystemEvents.UserPreferenceChanged -= OnUserPreferenceChanged;
        // Stop a running engine first (bounded wait, then kill; recorded as uncertain), then close the panel WebView.
        CardScan.Dispose();
        KnowhowScan.Dispose();
        CardLibrary.Dispose();
        Engine.Dispose();
        Panel.Dispose();
    }
}


/// <summary>
/// host chrome around the unchanged 1280x720 game viewport: the left part of the one full-width header (logo +
/// wordmark, and since 2026-10-11 the 刷新游戏 (after the wordmark) / 静音 (right end) icon buttons; the panel page continues the row with host
/// status and 紧急停止) and the thin footer under the game. Owner-drawn in the panel's tokens. It sits beside the viewport
/// (never over it), reads no frame and sends no input; the two icons only raise events that the host form handles
/// (静音 sets the game view's own mute flag; 刷新 goes through the host's confirmed manual reload).
/// </summary>
internal sealed class UiHostChrome : Control
{
    internal enum Part { Header, Footer }
    internal enum Tool { None, Mute, Refresh }

    private readonly Part _part;
    private UiShellForm.ShellPalette _palette;
    private string? _notice;

    internal UiHostChrome(Part part, UiShellForm.ShellPalette palette, string? label, string? trailing = null)
    {
        _part = part;
        _palette = palette;
        Label = label;
        Trailing = trailing;
        TabStop = false;
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint | ControlStyles.ResizeRedraw, true);
        SetStyle(ControlStyles.Selectable, false);
    }

    /// <summary>Header: quiet sub label after the wordmark (null for the game host: no 「游戏画面」 caption). Footer: left text.</summary>
    [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
    internal string? Label { get => _label; set { if (_label != value) { _label = value; Invalidate(); } } }
    private string? _label;
    private float _zoom = 1;
    /// <summary>display scale of the whole dock (1 = base size); multiplies the DPI scale of every drawn element.</summary>
    [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
    internal float Zoom { get => _zoom; set { if (Math.Abs(_zoom - value) > 1e-4f) { _zoom = value; Invalidate(); } } }
    internal string? Trailing { get; }
    [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
    internal UiShellForm.ShellPalette Palette { get => _palette; set { _palette = value; BackColor = value.Background; Invalidate(); } }
    /// <summary>Warning text shown in place of the header label (for example a panel start failure).</summary>
    [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
    internal string? Notice { get => _notice; set { _notice = value; Invalidate(); } }
    private int _rightInset;
    /// <summary>Header: px at the right end covered by the window buttons (panel on the left, 2026-10-16); icons and text stay left of it.</summary>
    [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
    internal int RightInset { get => _rightInset; set { if (_rightInset != value) { _rightInset = Math.Max(0, value); Invalidate(); } } }

    /// <summary>Header only: the user clicked the 刷新游戏 icon (reload the game page). Never raised by code.</summary>
    internal event Action? RefreshRequested;
    /// <summary>Header only: the user clicked the 静音 icon (toggle). Never raised by code.</summary>
    internal event Action? MuteRequested;
    private bool _refreshVisible, _muteVisible, _muted;
    [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
    internal bool RefreshVisible { get => _refreshVisible; set { _refreshVisible = value; Invalidate(); } }
    [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
    internal bool MuteVisible { get => _muteVisible; set { _muteVisible = value; Invalidate(); } }
    /// <summary>Shown state of the 静音 icon (the host sets it after applying the game view's mute flag).</summary>
    [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
    internal bool Muted { get => _muted; set { _muted = value; Invalidate(); } }
    internal const string RefreshTip = "刷新游戏";
    internal const string MuteTip = "静音";
    internal const string UnmuteTip = "取消静音";
    private Tool _hover, _pressed;
    private readonly ToolTip _tip = new() { InitialDelay = 400, ShowAlways = true };

    private float DpiScale => DeviceDpi / 96f * _zoom;

    /// <summary>
    /// Hit area of a visible header icon (Empty when hidden): 28 px rounded squares, vertically centred and never taller
    /// than the header minus 12 px. 2026-10-11 (host-20261011d): 刷新游戏 follows the wordmark on the left; 静音 sits at
    /// the right end of the strip with a 12 px margin.
    /// </summary>
    internal Rectangle ToolBounds(Tool tool)
    {
        if (_part != Part.Header || tool == Tool.None || !(tool == Tool.Refresh ? RefreshVisible : MuteVisible)) { return Rectangle.Empty; }
        var size = Math.Min((int)Math.Round(28 * DpiScale), Math.Max(8, Height - 12));
        var x = tool == Tool.Refresh
            ? (int)Math.Round(UiChrome.WordmarkEnd(DpiScale) + 4 * DpiScale)
            : Width - _rightInset - (int)Math.Round(12 * DpiScale) - size;
        return new Rectangle(x, (Height - size) / 2, size, size);
    }

    /// <summary>hit area of the 刷新游戏 icon (kept for the dock self-test).</summary>
    internal Rectangle RefreshBounds() => ToolBounds(Tool.Refresh);

    private Tool At(Point p)
    {
        foreach (var tool in new[] { Tool.Mute, Tool.Refresh })
        {
            if (ToolBounds(tool) is { IsEmpty: false } bounds && bounds.Contains(p)) { return tool; }
        }
        return Tool.None;
    }

    private const int WmNcHitTest = 0x0084;
    private const int HtTransparent = -1;

    /// <summary>
    /// frameless window: every area except a visible icon is transparent to hit-testing, so the form answers there
    /// (HTCAPTION over the header, resize edges at the window border, HTCLIENT elsewhere). Icons stay client areas.
    /// </summary>
    protected override void WndProc(ref Message m)
    {
        if (m.Msg == WmNcHitTest && CaptionDrag)
        {
            var point = PointToClient(new Point(unchecked((short)(long)m.LParam), unchecked((short)((long)m.LParam >> 16))));
            if (At(point) == Tool.None) { m.Result = (IntPtr)HtTransparent; return; }
        }
        base.WndProc(ref m);
    }

    /// <summary>Frameless host: let the form hit-test this strip (caption drag over the header, resize edges).</summary>
    [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
    internal bool CaptionDrag { get; set; }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        base.OnMouseMove(e);
        var hover = At(e.Location);
        if (hover == _hover) { return; }
        _hover = hover;
        if (hover == Tool.None) { _pressed = Tool.None; }
        Cursor = hover != Tool.None ? Cursors.Hand : Cursors.Default;
        _tip.SetToolTip(this, hover switch { Tool.Refresh => RefreshTip, Tool.Mute => Muted ? UnmuteTip : MuteTip, _ => null });
        Invalidate();
    }

    protected override void OnMouseLeave(EventArgs e)
    {
        base.OnMouseLeave(e);
        if (_hover != Tool.None || _pressed != Tool.None)
        {
            _hover = _pressed = Tool.None;
            Cursor = Cursors.Default;
            _tip.SetToolTip(this, null);
            Invalidate();
        }
    }

    protected override void OnMouseDown(MouseEventArgs e)
    {
        base.OnMouseDown(e);
        var tool = At(e.Location);
        if (e.Button == MouseButtons.Left && tool != Tool.None) { _pressed = tool; Invalidate(); }
    }

    protected override void OnMouseUp(MouseEventArgs e)
    {
        base.OnMouseUp(e);
        var pressed = _pressed;
        var click = pressed != Tool.None && e.Button == MouseButtons.Left && At(e.Location) == pressed;
        if (pressed != Tool.None) { _pressed = Tool.None; Invalidate(); }
        if (!click) { return; }
        // User-only: raised from a real left click released over the icon. RequestManualReload asks for confirmation.
        if (pressed == Tool.Refresh) { RefreshRequested?.Invoke(); }
        else { MuteRequested?.Invoke(); }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) { _tip.Dispose(); }
        base.Dispose(disposing);
    }

    /// <summary>Hover / pressed backdrop shared by the icons; returns the glyph colour.</summary>
    private Color DrawToolBackdrop(Graphics g, Tool tool, Rectangle r, float scale)
    {
        var active = _hover == tool || _pressed == tool;
        if (active)
        {
            using var fill = new SolidBrush(_pressed == tool ? _palette.Border : _palette.Surface);
            using var outline = new Pen(_palette.Border, 1);
            using var shape = UiWindowButtons.RoundRect(new RectangleF(r.X + 0.5f, r.Y + 0.5f, r.Width - 1, r.Height - 1), 6 * scale);
            g.FillPath(fill, shape);
            g.DrawPath(outline, shape);
        }
        return active ? _palette.Text : _palette.Text2;
    }

    /// <summary>Circular arrow (open at the upper right, arrowhead clockwise) in a 28 px rounded square.</summary>
    private void DrawRefresh(Graphics g, Rectangle r, float scale)
    {
        var state = g.Save();
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        var color = DrawToolBackdrop(g, Tool.Refresh, r, scale);
        float cx = r.X + r.Width / 2f, cy = r.Y + r.Height / 2f, radius = 6.5f * scale;
        const float Start = 0f, Sweep = 290f;
        using (var pen = new Pen(color, 1.6f * scale) { StartCap = System.Drawing.Drawing2D.LineCap.Round })
        { g.DrawArc(pen, cx - radius, cy - radius, radius * 2, radius * 2, Start, Sweep); }
        var theta = (Start + Sweep) * Math.PI / 180;
        var p = new PointF(cx + radius * (float)Math.Cos(theta), cy + radius * (float)Math.Sin(theta));
        var t = new PointF(-(float)Math.Sin(theta), (float)Math.Cos(theta));   // clockwise tangent
        var n = new PointF((float)Math.Cos(theta), (float)Math.Sin(theta));    // outward normal
        float a = 3.6f * scale, b = 1.2f * scale;
        using (var head = new SolidBrush(color))
        {
            g.FillPolygon(head, new[]
            {
                new PointF(p.X + t.X * a, p.Y + t.Y * a),
                new PointF(p.X + n.X * a - t.X * b, p.Y + n.Y * a - t.Y * b),
                new PointF(p.X - n.X * a - t.X * b, p.Y - n.Y * a - t.Y * b),
            });
        }
        g.Restore(state);
    }

    /// <summary>Speaker line icon (as the former panel header button): two waves, or a small × when muted.</summary>
    private void DrawMute(Graphics g, Rectangle r, float scale)
    {
        var state = g.Save();
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        var color = DrawToolBackdrop(g, Tool.Mute, r, scale);
        float cx = r.X + r.Width / 2f - 2.5f * scale, cy = r.Y + r.Height / 2f;
        PointF P(float x, float y) => new(cx + x * scale, cy + y * scale);
        using var pen = new Pen(color, 1.5f * scale)
        {
            LineJoin = System.Drawing.Drawing2D.LineJoin.Round,
            StartCap = System.Drawing.Drawing2D.LineCap.Round,
            EndCap = System.Drawing.Drawing2D.LineCap.Round,
        };
        g.DrawPolygon(pen, new[] { P(-5, -2.2f), P(-2, -2.2f), P(1.6f, -5.6f), P(1.6f, 5.6f), P(-2, 2.2f), P(-5, 2.2f) });
        if (Muted)
        {
            g.DrawLine(pen, P(4.4f, -2.6f), P(9.2f, 2.6f));
            g.DrawLine(pen, P(4.4f, 2.6f), P(9.2f, -2.6f));
        }
        else
        {
            g.DrawArc(pen, cx + (1.6f - 3.4f) * scale, cy - 3.4f * scale, 6.8f * scale, 6.8f * scale, -45, 90);
            g.DrawArc(pen, cx + (1.6f - 6.6f) * scale, cy - 6.6f * scale, 13.2f * scale, 13.2f * scale, -45, 90);
        }
        g.Restore(state);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        var scale = DpiScale;
        using var background = new SolidBrush(_palette.Background);
        using var border = new Pen(_palette.Border, 1);
        g.FillRectangle(background, ClientRectangle);
        if (_part == Part.Header)
        {
            // The sub label / notice starts after the 刷新游戏 icon that follows the wordmark.
            var reserve = RefreshVisible ? ToolBounds(Tool.Refresh).Width + 8 * scale : 0;
            UiChrome.DrawBrand(g, new Rectangle(0, 0, Math.Max(0, Width - _rightInset), Height), scale, _palette, Label, _notice, reserve);
            if (ToolBounds(Tool.Mute) is { IsEmpty: false } mute) { DrawMute(g, mute, scale); }
            if (ToolBounds(Tool.Refresh) is { IsEmpty: false } refresh) { DrawRefresh(g, refresh, scale); }
            g.DrawLine(border, 0, Height - 1, Width, Height - 1);
            return;
        }
        g.DrawLine(border, 0, 0, Width, 0);
        using var small = new Font("Microsoft YaHei UI", 12f * scale, FontStyle.Regular, GraphicsUnit.Pixel);
        const TextFormatFlags Flags = TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine | TextFormatFlags.NoPrefix | TextFormatFlags.EndEllipsis;
        var inner = new Rectangle((int)(16 * scale), 1, Width - (int)(32 * scale), Height - 1);
        TextRenderer.DrawText(g, Label ?? "", small, inner, _palette.Text2, Flags | TextFormatFlags.Left);
        if (!string.IsNullOrEmpty(Trailing)) { TextRenderer.DrawText(g, Trailing, small, inner, _palette.Text2, Flags | TextFormatFlags.Right); }
    }
}

/// <summary>
/// frameless host: 最小化 / 最大化(还原) / 关闭 drawn in the panel tokens at the far right of the one header row. It
/// sits over the right end of the panel page's header (the page pads that end, see .app-header.has-window-controls in
/// app.css), never over the game viewport. 最小化 sends WM_SYSCOMMAND SC_MINIMIZE (so the background keeper's tray
/// intercept still applies); 最大化 / 还原 send SC_MAXIMIZE / SC_RESTORE (2026-10-11: the window is resizable, the game view
/// itself stays 1280x720); 关闭 goes through Form.Close() and the normal FormClosing path. While the window is not
/// maximized the outer <see cref="ResizeBorder"/> px (top and right) are transparent to hit-testing so the form can
/// answer with resize edges. Reads no frame and sends no input to either WebView.
/// </summary>
internal sealed class UiWindowButtons : Control
{
    /// <summary>CSS px per button; app.css --window-controls-w is 3 × this.</summary>
    internal const int ButtonWidth = 46;
    internal const int Count = 3;
    internal enum Kind { None, Minimize, Maximize, Close }

    private UiShellForm.ShellPalette _palette;
    private Kind _hover, _pressed;
    private bool _maximized;
    private readonly ToolTip _tip = new() { InitialDelay = 400, ShowAlways = true };

    internal UiWindowButtons(UiShellForm.ShellPalette palette)
    {
        _palette = palette;
        TabStop = false;
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint | ControlStyles.ResizeRedraw, true);
        SetStyle(ControlStyles.Selectable, false);
        BackColor = palette.Background;
    }

    internal event Action? MinimizeRequested;
    internal event Action? MaximizeRequested;
    internal event Action? CloseRequested;
    [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
    internal UiShellForm.ShellPalette Palette { get => _palette; set { _palette = value; BackColor = value.Background; Invalidate(); } }
    /// <summary>Draws 还原 (two squares) instead of 最大化 (one square).</summary>
    [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
    internal bool Maximized { get => _maximized; set { if (_maximized != value) { _maximized = value; Invalidate(); } } }
    private float _zoom = 1;
    /// <summary>display scale of the dock (the host sizes the control; this scales the glyphs).</summary>
    [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
    internal float Zoom { get => _zoom; set { if (Math.Abs(_zoom - value) > 1e-4f) { _zoom = value; Invalidate(); } } }
    /// <summary>Px at the top and right edges that belong to the window's resize border (0 while maximized).</summary>
    [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
    internal int ResizeBorder { get; set; }

    internal static int ScaledWidth(int dpi) => Count * (int)Math.Round(ButtonWidth * dpi / 96.0);

    internal Rectangle BoundsOf(Kind kind)
    {
        var w = Width / Count;
        return kind switch
        {
            Kind.Minimize => new Rectangle(0, 0, w, Height),
            Kind.Maximize => new Rectangle(w, 0, w, Height),
            Kind.Close => new Rectangle(2 * w, 0, Width - 2 * w, Height),
            _ => Rectangle.Empty,
        };
    }

    private Kind At(Point p) => BoundsOf(Kind.Minimize).Contains(p) ? Kind.Minimize : BoundsOf(Kind.Maximize).Contains(p) ? Kind.Maximize
        : BoundsOf(Kind.Close).Contains(p) ? Kind.Close : Kind.None;

    private const int WmNcHitTest = 0x0084;
    private const int HtTransparent = -1;

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == WmNcHitTest && ResizeBorder > 0)
        {
            var point = PointToClient(new Point(unchecked((short)(long)m.LParam), unchecked((short)((long)m.LParam >> 16))));
            if (point.Y < ResizeBorder || point.X >= Width - ResizeBorder) { m.Result = (IntPtr)HtTransparent; return; }
        }
        base.WndProc(ref m);
    }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        base.OnMouseMove(e);
        var at = At(e.Location);
        if (at == _hover) { return; }
        _hover = at;
        _tip.SetToolTip(this, at switch { Kind.Minimize => "最小化", Kind.Maximize => Maximized ? "还原" : "最大化", Kind.Close => "关闭", _ => null });
        Invalidate();
    }

    protected override void OnMouseLeave(EventArgs e)
    {
        base.OnMouseLeave(e);
        _hover = _pressed = Kind.None;
        _tip.SetToolTip(this, null);
        Invalidate();
    }

    protected override void OnMouseDown(MouseEventArgs e)
    {
        base.OnMouseDown(e);
        if (e.Button == MouseButtons.Left) { _pressed = At(e.Location); Invalidate(); }
    }

    protected override void OnMouseUp(MouseEventArgs e)
    {
        base.OnMouseUp(e);
        var pressed = _pressed;
        _pressed = Kind.None;
        Invalidate();
        if (e.Button != MouseButtons.Left || pressed == Kind.None || At(e.Location) != pressed) { return; }
        switch (pressed)
        {
            case Kind.Minimize: MinimizeRequested?.Invoke(); break;
            case Kind.Maximize: MaximizeRequested?.Invoke(); break;
            default: CloseRequested?.Invoke(); break;
        }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) { _tip.Dispose(); }
        base.Dispose(disposing);
    }

    // Windows 11 caption red for 关闭 (hover / pressed); 最小化 / 最大化 use the panel's own border / canvas tones.
    private static readonly Color CloseHover = Color.FromArgb(0xC4, 0x2B, 0x1C);
    private static readonly Color ClosePressed = Color.FromArgb(0xC8, 0x4E, 0x42);

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        var scale = DeviceDpi / 96f * _zoom;
        using (var background = new SolidBrush(_palette.Background)) { g.FillRectangle(background, ClientRectangle); }
        foreach (var kind in new[] { Kind.Minimize, Kind.Maximize, Kind.Close })
        {
            var r = BoundsOf(kind);
            var active = _hover == kind || _pressed == kind;
            Color glyph = active ? _palette.Text : _palette.Text2;
            if (active)
            {
                var fill = kind == Kind.Close ? (_pressed == kind ? ClosePressed : CloseHover) : (_pressed == kind ? _palette.Canvas : _palette.Border);
                if (kind == Kind.Close) { glyph = Color.White; }
                using var brush = new SolidBrush(fill);
                // Leave the header's bottom hairline visible under the buttons.
                g.FillRectangle(brush, r.X, r.Y, r.Width, r.Height - 1);
            }
            var state = g.Save();
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            using var pen = new Pen(glyph, Math.Max(1f, 1.1f * scale));
            float cx = r.X + r.Width / 2f, cy = r.Y + r.Height / 2f, half = 5f * scale;
            if (kind == Kind.Minimize) { g.DrawLine(pen, cx - half, cy, cx + half, cy); }
            else if (kind == Kind.Maximize && Maximized)
            {
                // 还原: a front square plus the visible corner of a second one offset to the upper right.
                var offset = 2.2f * scale;
                var side = 2 * half - offset;
                g.DrawRectangle(pen, cx - half, cy - half + offset, side, side);
                g.DrawLines(pen, new[] { new PointF(cx - half + offset, cy - half + offset), new PointF(cx - half + offset, cy - half),
                    new PointF(cx + half, cy - half), new PointF(cx + half, cy + half - offset), new PointF(cx - half + side, cy + half - offset) });
            }
            else if (kind == Kind.Maximize) { g.DrawRectangle(pen, cx - half, cy - half, 2 * half, 2 * half); }
            else { g.DrawLine(pen, cx - half, cy - half, cx + half, cy + half); g.DrawLine(pen, cx - half, cy + half, cx + half, cy - half); }
            g.Restore(state);
        }
        using var border = new Pen(_palette.Border, 1);
        g.DrawLine(border, 0, Height - 1, Width, Height - 1);
    }

    internal static System.Drawing.Drawing2D.GraphicsPath RoundRect(RectangleF r, float radius)
    {
        var path = new System.Drawing.Drawing2D.GraphicsPath();
        var d = Math.Min(radius * 2, Math.Min(r.Width, r.Height));
        path.AddArc(r.X, r.Y, d, d, 180, 90);
        path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        path.CloseFigure();
        return path;
    }
}
