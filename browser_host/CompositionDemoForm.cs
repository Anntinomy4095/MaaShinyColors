using System.Drawing;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Diagnostics;
using Microsoft.Web.WebView2.Core;

namespace MaaShinyColors.BrowserHost;

/// <summary>Visual host with separate owned-demo and supervised-navigation boundaries.</summary>
internal sealed class CompositionDemoForm : Form
{
    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")]
    private static extern bool PostMessage(IntPtr window, int message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern uint RegisterWindowMessage(string name);
    private static readonly uint ShowExistingMessage = RegisterWindowMessage("MaaShinyColors.ShowExisting.v1");
    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool RegisterHotKey(IntPtr window, int id, uint modifiers, uint key);
    [DllImport("user32.dll")]
    private static extern bool UnregisterHotKey(IntPtr window, int id);
    // 设置 → 紧急停止热键: Ctrl+Alt+Shift+S only stops a running task; it never starts anything.
    private const int WmHotkey = 0x0312, StopHotkeyId = 0x5C01;
    private const uint ModAlt = 0x1, ModControl = 0x2, ModShift = 0x4, ModNoRepeat = 0x4000;
    private bool _stopHotkeyRegistered;
    protected override bool ShowWithoutActivation => true;
    private readonly BrowserHostOptions _options;
    private CoreWebView2CompositionController _webView = null!;
    private CoreWebView2Environment? _browserEnvironment;
    private readonly HashSet<LoginBrowserForm> _loginWindows = new();
    private ulong _acceptedNavigationId;
    private readonly Panel _viewport;
    private DirectCompositionTarget? _visual;
    private CaptureBridge? _captureBridge;
    private LocalDemoInputBridge? _inputBridge;
    private SupervisedNavigationBridge? _navigationBridge;
    private SupervisedDailyBridge? _dailyBridge;
    private SupervisedDailyBridge? _gradeBridge;
    private NavigationBudget? _navigationBudget;
    private SelectionLoopBridge? _selectionLoopBridge;
    private SelectionLoopBudget? _selectionLoopBudget;
    private RepeatableNavigationBridge? _repeatableBridge;
    private RepeatableNavigationBudget? _repeatableBudget;
    private PipelineInputBridge? _pipelineBridge;
    private PipelineInputGate? _pipelineGate;
    private readonly System.Windows.Forms.Timer _activityTimer = new() { Interval = 40 };
    private long _lastExternalPipelineTimestamp;
    private bool _ready;
    private bool _used;
    private string _pageResult = "starting";
    private int? _sourceWidth;
    private int? _sourceHeight;
    private string? _lastFrameSha256;
    private string? _lastScrollFrameSha256;
    private long _lastFrameTimestamp;
    // CapturePreview API interval observations, never the image's actual game-render timestamp.
    private double? _captureStartedMs;
    private double? _captureCompletedMs;
    private double? CaptureEstimatedMs => (_captureStartedMs + _captureCompletedMs) / 2;
    private long _captureSequence;
    private long _frameBindingSequence;
    private int _capturesInFlight;
    private readonly int _uiThreadId = Environment.CurrentManagedThreadId;
    private string _captureSource = "unknown";
    private string _frameBindingEvent = "unknown";
    // Tracks manual clicks so long-running scans can stop if the user takes over.
    private int _manualClickSeq;
    private readonly FrameChangeTracker _frameChanges = new();
    private BackgroundKeeper? _keeper;
    private Ui.UiGameDock? _uiDock;
    private Ui.UiGameData? _gameData;
    private string? _bridgeSessionPath;
    // Legacy/demo-only text strip; the docked layout paints the shared header and footer instead.
    private readonly Label? _header;
    private readonly Ui.UiHostChrome? _chromeHeader;
    private readonly Ui.UiHostChrome? _chromeFooter;
    // frameless docked window (poi-like). The shared header is the title bar; 最小化 / 关闭 are host-painted.
    private readonly Ui.UiWindowButtons? _windowButtons;
    private bool Frameless => _options?.DockedUiPanel == true;
    // 2026-10-11: resizable frameless window. The game WebView stays 1280x720 (render, capture and
    // program input; HostGuard requires it) while the displayed picture scales with the window through the composition
    // root visual's transform; header, footer, window buttons and the panel zoom scale uniformly with it.
    private readonly Panel? _panelHolder;
    // 面板位置「独立窗口」: the window that holds _panelHolder while detached (null while docked).
    private Ui.UiPanelWindow? _panelWindow;
    private Ui.UiHostPrefs _prefs = Ui.UiHostPrefs.Default;
    // Size the committed display transform maps the canvas onto (Empty until the visual exists or after a failed commit).
    private Size _displaySize;
    // False after the visual transform failed once: the dock then keeps the game at 1:1 (the unscaled layout).
    private bool _displayScaling = true;
    private DockLayout _layout;
    private bool _desktopLocked;
    private string? _browserArguments;
    private bool _keepaliveArguments;
    private readonly TaskCompletionSource _heartbeatSeen = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private string DemoUri => "https://sc-local.test/" + _options.DemoPage;
    internal bool IsHeartbeatPage => _options.CompositionDemo && _options.DemoPage == HeartbeatPage;
    internal const string HeartbeatPage = "heartbeat.html";
    internal BackgroundKeeper? Keeper => _keeper;
    internal Heartbeat? LatestHeartbeat { get; private set; }
    internal Task HeartbeatSeen => _heartbeatSeen.Task;

    public CompositionDemoForm(BrowserHostOptions options)
    {
        if (!(options.CompositionDemo || options.CompositionGame) || options.OfflineDemo)
        { throw new ArgumentException("Composition 宿主类型无效。"); }
        _options = options;
        Text = options.WindowTitle;
        Icon = AppIcon.Value;                 // taskbar / Alt-Tab (app.ico)
        AutoScaleMode = AutoScaleMode.None;
        // U4: the new UI panel sits to the right of the unchanged 1280x720 viewport (scaled with the display DPI).
        var panelWidth = (int)Math.Round(Ui.UiGameDock.PanelWidth * DeviceDpi / 96.0);
        ClientSize = new Size(options.DockedUiPanel ? 1280 + panelWidth : options.CompositionGame ? 1640 : 1280, 788);
        // the docked layout has no system title bar or frame (the header is the caption); legacy layouts keep theirs.
        FormBorderStyle = options.DockedUiPanel ? FormBorderStyle.None : FormBorderStyle.FixedSingle;
        // 2026-10-11: the docked window can be resized and maximized; the whole dock scales (see ApplyDockLayout).
        MaximizeBox = options.DockedUiPanel;
        StartPosition = FormStartPosition.CenterScreen;
        _viewport = new Panel { Location = new Point(0, 44), Size = new Size(1280, 720), BackColor = Color.Black };
        Controls.Add(_viewport);
        if (options.DockedUiPanel)
        {
            // Isolated local UI WebView (own environment/profile, https://maa-ui.test/ only). It reads in-process status
            // and the bridge session path through delegates; it never captures, injects or sends input to the game.
            _uiDock = new Ui.UiGameDock(UiStatus, () => _bridgeSessionPath, new Ui.HostDevTools(() => OnUiSync(ReloadActionTable)),
                notify: note => _keeper?.ShowNotification(note.Title, note.Message, note.Icon));
            var holder = _panelHolder = new Panel { Location = new Point(1280, 0), Size = new Size(panelWidth, 788), BackColor = _uiDock.Background };
            holder.Controls.Add(_uiDock.View);
            Controls.Add(holder);
            _prefs = Ui.UiHostPrefs.Load(_uiDock.HostPrefsPath);
            // 设置 → 存储: this form owns the host prefs record (it rewrites the whole file), so 自动清理 goes through it.
            _uiDock.Storage.BindPrefs(() => (_prefs.StorageAuto, _prefs.StorageDays), (auto, days) =>
            {
                _prefs = _prefs with { StorageAuto = auto, StorageDays = days };
                return _prefs.Save(_uiDock.HostPrefsPath);
            });
            _uiDock.Storage.HostBusy = () => ExternalPipelineRecent ? "external_run" : null;
            // saved restored window size, reduced to the primary work area, never below the minimum.
            var work = Screen.PrimaryScreen?.WorkingArea.Size ?? new Size(int.MaxValue / 4, int.MaxValue / 4);
            // No saved size: the game at 1:1 with a 400 px panel, both at the window DPI (1680x788 at 100 %, 1880x822 at 150 %).
            var first = _prefs == Ui.UiHostPrefs.Default ? GameDisplay.ClientFor(1, Px(Ui.UiHostPrefs.Default.Width - 1280), ChromeScale)
                : new Size(_prefs.Width, _prefs.Height);
            ClientSize = new Size(Math.Max(MinimumClient.Width, Math.Min(first.Width, work.Width)),
                Math.Max(MinimumClient.Height, Math.Min(first.Height, work.Height)));
            MinimumSize = MinimumClient;   // frameless: window size == client size
            // one window. The left half of the shared header and the thin footer are painted in the panel's tokens
            // beside (never over) the game viewport (base 0,44 1280x720, scaled for display); the page draws status
            // and 紧急停止 on the right.
            var palette = _uiDock.Palette;
            BackColor = palette.Background;
            // 2026-10-11: no 「游戏画面」 caption on the game host; the demo page keeps its 「自有测试页」 label.
            _chromeHeader = new Ui.UiHostChrome(Ui.UiHostChrome.Part.Header, palette, options.CompositionGame ? null : "自有测试页")
                { Bounds = new Rectangle(0, 0, 1280, 44) };
            _chromeFooter = new Ui.UiHostChrome(Ui.UiHostChrome.Part.Footer, palette, "画布 1280 × 720", FooterStatus(options))
                { Bounds = new Rectangle(0, 764, 1280, 24) };
            Controls.Add(_chromeHeader);
            Controls.Add(_chromeFooter);
            _chromeHeader.CaptionDrag = true;
            _chromeFooter.CaptionDrag = true;   // never a caption there: lets the form answer resize edges along the bottom
            // 最小化 / 最大化 / 关闭 at the far right of the header row, over the panel page's padded right end (never the game).
            var buttonsWidth = Ui.UiWindowButtons.ScaledWidth(DeviceDpi);
            _windowButtons = new Ui.UiWindowButtons(palette) { Bounds = new Rectangle(ClientSize.Width - buttonsWidth, 0, buttonsWidth, 44) };
            // Posted (not sent) so it runs after the button's mouse-up/capture handling; same path as the system menu's 最小化.
            _windowButtons.MinimizeRequested += () => PostMessage(Handle, WmSysCommand, (IntPtr)ScMinimize, IntPtr.Zero);
            _windowButtons.MaximizeRequested += () => PostMessage(Handle, WmSysCommand,
                (IntPtr)(WindowState == FormWindowState.Maximized ? ScRestore : ScMaximize), IntPtr.Zero);
            _windowButtons.CloseRequested += Close;
            Controls.Add(_windowButtons);
            _windowButtons.BringToFront();
            _uiDock.Router.WindowLayout = SetPanelSide;
            _uiDock.Router.PanelSide = () => PanelSideName;
            _uiDock.Router.ExternalStop = StopExternalRun;
            // a saved 独立窗口 starts detached (shown after the game window, before the panel page initializes).
            if (_prefs.PanelDetached) { DetachPanel(show: false); }
            if (options.CompositionGame)
            {
                // Host icons: 刷新 after the wordmark (confirmed manual reload), 静音 at the right end (game view audio only).
                _chromeHeader.MuteVisible = true;
                _chromeHeader.Muted = _prefs.Muted;
                _chromeHeader.MuteRequested += () => SetGameMuted(!_prefs.Muted);
                _uiDock.Router.GameAudio = SetGameMuted;
                _gameData = new Ui.UiGameData(() => _webView?.CoreWebView2 is not null,
                    () => _uiDock.Engine.Active || _uiDock.CardScan.Active || _uiDock.KnowhowScan.Active
                        ? "正在运行任务或扫描，请先停止后清理。" : null,
                    kinds => _webView.CoreWebView2.Profile.ClearBrowsingDataAsync(kinds),
                    () => { InvalidateFrame("game_data_clear"); _webView.CoreWebView2.Reload(); });
                _uiDock.Router.GameData = _gameData;
                _gameData.Changed += () => _uiDock.Router.EmitStatus();
                _activityTimer.Tick += (_, _) => KeepAwakeTick(EngineActive || ExternalPipelineRecent);
                _activityTimer.Start();
                _uiDock.Router.BackgroundRun = (op, enabled) => op == "hide" ? (HideToBackground() is { } mode && mode != BackgroundKeeper.None ? true : null) : SetBackgroundRun(enabled);
                _chromeHeader.RefreshVisible = true;
                _chromeHeader.RefreshRequested += RequestManualReload;
            }
            _uiDock.PaletteChanged += changed =>
            {
                BackColor = holder.BackColor = changed.Background;
                _chromeHeader.Palette = _chromeFooter.Palette = changed;
                _windowButtons.Palette = changed;
                ApplyFrameAttributes(changed);
                _panelWindow?.ApplyPalette(changed.Background);
            };
            Resize += (_, _) => ApplyDockLayout();
            ResizeEnd += (_, _) => SaveWindowPrefs();
            FormClosing += (_, closing) => { SaveWindowPrefs(); if (!closing.Cancel) { CloseDetachedPanel(); } };
            ApplyDockLayout();
        }
        else if (options.CompositionGame)
        {
            // Legacy (--legacy-settings-panel): native preferences outside the recognition canvas; they cannot authorize input.
            Controls.Add(new DailySettingsPanel(() =>
                $"网页：{(_ready ? "已加载" : "未就绪")}；窗口：{WindowStatusText()}\n" +
                $"后台导航实验：{(_options.EnableSupervisedDaily ? "开启" : "关闭")}\n" +
                $"训练实验：{(_options.EnableSupervisedTraining ? "开启" : "关闭")}；滚动实验：{(_options.EnableSupervisedScroll ? "开启" : "关闭")}\n" +
                $"商店实验：{(_options.EnableSupervisedShop ? "开启" : "关闭")}；Grade 实验：{(_options.EnableSupervisedGrade ? "开启" : "关闭")}\n" +
                $"Pipeline 输入实验：{(_options.EnablePipelineInput ? "开启" : "关闭")}；本次点击：{_pipelineGate?.RunCount ?? 0}\n" +
                $"监督探索网格：{(_options.EnableProduceExplore ? "开启" : "关闭")}")
                { Location = new Point(1280, 44), Size = new Size(360, 720) });
        }
        if (!options.DockedUiPanel)
        {
            _header = new Label { Text = options.CompositionGame
                ? "MaaShinyColors · 游戏画布 1280×720 · 后台输入实验需单独启用"
                : "自有 Composition 测试页 · 游戏输入关闭", Dock = DockStyle.Top, Height = 44 };
            Controls.Add(_header);
        }
        // Docked game host: keep clear of the 静音 icon at the right end of the 1280 px header strip.
        var headerRight = options.DockedUiPanel ? 1280 - (options.CompositionGame ? Px(80) : 0) : ClientSize.Width;
        if (KeepaliveWanted || (_options.CompositionGame && _uiDock is not null))
        {
            // 后台运行: minimize hides the window (DWM cloak) instead of minimizing it, so rendering, capture and input
            // continue. The docked game host uses the setting in 设置 → 窗口.
            var keeper = _keeper = new BackgroundKeeper(this, tray: true);
            keeper.Enabled = _options.BackgroundKeepalive || _prefs.BackgroundRun;
            Resize += (_, _) => keeper.OnResize();
        }
        if (_uiDock is not null)
        {
            var support = _uiDock.Support;
            support.Notify = (title, message, icon) => _keeper?.ShowNotification(title, message, icon);
            support.Hotkey = () => new System.Text.Json.Nodes.JsonObject
                { ["enabled"] = _prefs.StopHotkey, ["registered"] = _stopHotkeyRegistered, ["supported"] = true };
            support.SetHotkey = enabled =>
            {
                _prefs = _prefs with { StopHotkey = enabled };
                _prefs.Save(_uiDock.HostPrefsPath);
                ApplyStopHotkey();
                return support.Hotkey!();
            };
        }
        Microsoft.Win32.SystemEvents.SessionSwitch += OnSessionSwitch;
        // Forward only real, user-generated viewport events for manual browser use.
        // The supervised endpoint never calls MoveFocus or the desktop mouse APIs.
        _viewport.TabStop = true;
        _viewport.MouseMove += (_, mouse) => ForwardManualMouse(CoreWebView2MouseEventKind.Move, mouse, false);
        _viewport.MouseDown += (_, mouse) => ForwardManualMouse(CoreWebView2MouseEventKind.LeftButtonDown, mouse, true);
        _viewport.MouseUp += (_, mouse) => ForwardManualMouse(CoreWebView2MouseEventKind.LeftButtonUp, mouse, false);
        _viewport.MouseLeave += (_, _) => { if (_ready) { _webView.SendMouseInput(CoreWebView2MouseEventKind.Leave, CoreWebView2MouseEventVirtualKeys.None, 0, Point.Empty); } };
        Shown += Initialize;
        FormClosed += (_, _) => { _activityTimer.Stop(); Microsoft.Win32.SystemEvents.SessionSwitch -= OnSessionSwitch; _uiDock?.Dispose(); _keeper?.Dispose(); _pipelineBridge?.Dispose(); _repeatableBridge?.Dispose(); _selectionLoopBridge?.Dispose(); _dailyBridge?.Dispose(); _gradeBridge?.Dispose(); _navigationBridge?.Dispose(); _inputBridge?.Dispose(); _captureBridge?.Dispose(); if (_webView is not null) { _webView.Close(); } _visual?.Dispose(); };
    }

    private async void Initialize(object? sender, EventArgs args)
    {
        try
        {
            Directory.CreateDirectory(_options.UserDataFolder);
            // One user-data folder must always see the same argument set; keep a separate profile per mode.
            _keepaliveArguments = (_options.BackgroundKeepalive || (_options.CompositionGame && _uiDock is not null))
                && !_options.OmitBackgroundArguments;
            _browserArguments = HostGuard.GameArguments(_keepaliveArguments);
            var environment = _browserEnvironment = await CoreWebView2Environment.CreateAsync(null, _options.UserDataFolder,
                new CoreWebView2EnvironmentOptions { AdditionalBrowserArguments = _browserArguments, Language = HostGuard.GameLanguage });
            _webView = await environment.CreateCoreWebView2CompositionControllerAsync(_viewport.Handle);
            // 2026-10-16: at 125%/150% Windows scaling the page otherwise renders at devicePixelRatio 1.5 (CSS viewport
            // 853x480), so text/edges differ from the 100% templates and Common.GoHome etc. miss. Pin the page to the
            // canonical 1280x720 CSS canvas at DPR 1 on every monitor; Bounds and SendMouseInput stay raw 1280x720 pixels.
            _webView.ShouldDetectMonitorScaleChanges = false;
            _webView.RasterizationScale = 1.0;
            _visual = new DirectCompositionTarget(_viewport.Handle);
            _webView.RootVisualTarget = _visual.Visual;
            _webView.Bounds = new Rectangle(0, 0, 1280, 720);
            // Stays true for the whole session; TrySuspendAsync is never called.
            _webView.IsVisible = true;
            _visual.Commit();
            // the docked host applies its display scale to the root visual now; other hosts never scale.
            if (_panelHolder is not null) { ApplyDockLayout(); } else { _displaySize = HostGuard.Canvas; }
            var core = _webView.CoreWebView2;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.IsPasswordAutosaveEnabled = false;
            core.Settings.IsGeneralAutofillEnabled = false;
            core.Settings.IsZoomControlEnabled = false;
            // 静音 (host preference, game host only): the game view's own audio flag; no input, no page script.
            if (_options.CompositionGame && _uiDock is not null) { core.IsMuted = _prefs.Muted; }
            core.NavigationStarting += (_, navigation) =>
            {
                navigation.Cancel = _options.CompositionDemo
                    ? navigation.Uri != DemoUri
                    : !BrowserNavigationPolicy.CanBrowse(navigation.Uri);
                if (navigation.Cancel) { return; }
                _acceptedNavigationId = navigation.NavigationId;
                _ready = false;
                InvalidateFrame("navigation_starting");
                _pageResult = "loading";
            };
            core.NewWindowRequested += OpenLoginWindow;
            core.NavigationCompleted += (_, navigation) =>
            {
                if (navigation.NavigationId != _acceptedNavigationId) { return; }
                _ready = navigation.IsSuccess;
                _pageResult = navigation.IsSuccess ? "loaded" : navigation.WebErrorStatus.ToString();
            };
            if (IsHeartbeatPage) { core.WebMessageReceived += OnHeartbeat; }
            _webView.ZoomFactor = 1;
            if (_options.CompositionDemo)
            { core.SetVirtualHostNameToFolderMapping("sc-local.test", Path.Combine(AppContext.BaseDirectory, "demo"), CoreWebView2HostResourceAccessKind.Deny); }
            var path = _options.BridgeSession ?? Path.Combine(_options.UserDataFolder, $"bridge-{Environment.ProcessId}.json");
            _captureBridge = new CaptureBridge(path, () => OnUi(() => CaptureImage("capture_bridge")), () => OnUi(() => Task.FromResult<object>(new
            {
                ready = HostGuardReason is null,
                host_guard_reason = HostGuardReason,
                page_ready = _ready, page_result = _pageResult,
                minimized = Snapshot().Minimized,
                background_keepalive = KeepaliveWanted,
                browser_arguments = _browserArguments,
                hide_mode = _keeper?.HideMode ?? BackgroundKeeper.None,
                minimize_intercept = _keeper?.InterceptMinimize ?? false,
                notification_state = _keeper?.LastNotificationState ?? "unavailable",
                notification_error = _keeper?.LastNotificationError,
                controller_visible = _webView.IsVisible,
                desktop_locked = _desktopLocked,
                frame_changing = _frameChanges.Changing(),
                frame_change_age_ms = _frameChanges.ChangeAgeMs,
                window_visible = Visible, webview_visible = _viewport.Visible,
                browser_demo = _options.BrowserDemo, composition_demo = _options.CompositionDemo,
                composition_game = _options.CompositionGame,
                input_enabled = false, local_input_enabled = _options.EnableLocalInput,
                supervised_navigation = _options.SupervisedNavigation,
                supervised_daily = _options.EnableSupervisedDaily,
                supervised_training = _options.EnableSupervisedTraining,
                supervised_scroll = _options.EnableSupervisedScroll,
                training_selection_loop = _options.EnableTrainingSelectionLoop,
                repeatable_navigation = _options.EnableRepeatableNavigation,
                supervised_shop = _options.EnableSupervisedShop,
                supervised_grade = _options.EnableSupervisedGrade,
                pipeline_input = _options.EnablePipelineInput,
                produce_explore = _options.EnableProduceExplore && _pipelineGate?.Explore is not null,
                explore_grid_version = _pipelineGate?.Explore is null ? null : ProduceExploreGrid.Version,
                explore_grid_sha256 = _pipelineGate?.Explore?.Sha256,
                frame_sha256 = _lastFrameSha256,
                capture_seq = _captureSequence,
                capture_started_ms = _captureStartedMs, capture_completed_ms = _captureCompletedMs,
                capture_estimated_ms = CaptureEstimatedMs,
                capture_pending = _capturesInFlight != 0,
                capture_source = _captureSource,
                frame_binding_seq = _frameBindingSequence,
                frame_binding_event = _frameBindingEvent,
                manual_click_seq = _manualClickSeq,
                frame_age_ms = _lastFrameSha256 is null ? (double?)null : Stopwatch.GetElapsedTime(_lastFrameTimestamp).TotalMilliseconds,
                source_capture_width = _sourceWidth, source_capture_height = _sourceHeight,
                width = 1280, height = 720,
            })));
            _bridgeSessionPath = path;
            if (_options.EnableLocalInput)
            { _inputBridge = new LocalDemoInputBridge(path + ".local-input.json", () => OnUi(ClickDemo)); }
            if (_options.SupervisedNavigation is not null)
            {
                _navigationBudget = new NavigationBudget(_options.SupervisedNavigation);
                _navigationBridge = new SupervisedNavigationBridge(path + ".navigation-input.json",
                    _options.SupervisedNavigation, _navigationBudget.Path,
                    (sha, width, height) => OnUi(() => NavigateOnce(sha, width, height)));
            }
            if (_options.EnableSupervisedDaily)
            { _dailyBridge = new SupervisedDailyBridge(path + ".daily-input.json", (request, cancellation) => OnUi(() => NavigateDailyOnce(request, cancellation)), _options.EnableSupervisedTraining, _options.EnableSupervisedScroll, _options.EnableSupervisedShop); }
            if (_options.EnableSupervisedGrade)
            { _gradeBridge = SupervisedDailyBridge.Grade(path + ".grade-input.json", (request, cancellation) => OnUi(() => NavigateDailyOnce(request, cancellation))); }
            if (_options.EnableRepeatableNavigation)
            {
                _repeatableBudget = new RepeatableNavigationBudget();
                _repeatableBridge = new RepeatableNavigationBridge(path + ".repeatable-navigation.json", _repeatableBudget,
                    _options.EnableSupervisedTraining, (request, cancellation) => OnUi(() => RepeatableNavigation(request, cancellation)), _options.EnableSupervisedShop, _options.EnableSupervisedGrade);
            }
            if (_options.EnablePipelineInput)
            {
                _pipelineGate = new PipelineInputGate(_options.EnableProduceExplore ? ProduceExploreGrid.Default : null);
                _pipelineBridge = new PipelineInputBridge(path + PipelineInputPolicy.SessionSuffix, _pipelineGate,
                    (request, cancellation) => OnUi(() => PipelineClick(request, cancellation)));
            }
            if (_options.EnableTrainingSelectionLoop)
            {
                _selectionLoopBudget = new SelectionLoopBudget(_options.TrainingSelectionResolution!, _options.TrainingSelectionResolutionSha256!);
                _selectionLoopBridge = new SelectionLoopBridge(path + ".selection-loop-input.json", _selectionLoopBudget,
                    (request, cancellation) => OnUi(() => SelectionLoop(request, cancellation)));
            }
            core.Navigate(_options.CompositionDemo ? DemoUri : _options.StartUri.AbsoluteUri);
            ShowWindow(Handle, 4);
            // the detached panel window (no-activate too) before the panel page initializes inside it.
            _panelWindow?.Show();
            // Saved 最大化 (host prefs): after the no-activate show, which would otherwise restore it.
            if (Frameless && _prefs.Maximized) { WindowState = FormWindowState.Maximized; }
        }
        catch (Exception exception)
        {
            MessageBox.Show(this, "MaaShinyColors 启动失败：" + exception.Message);
            Close();
            return;
        }
        if (_uiDock is not null)
        {
            // A panel failure never closes or changes the game view; it is shown in the header instead.
            try
            {
                await _uiDock.InitializeAsync();
                // the panel page never zooms with the window (text follows the DPI and 外观 → 字号 only).
                _uiDock.Panel.SetZoom(1, force: true);
                UiPanelReady.TrySetResult(true);
            }
            catch (Exception exception)
            {
                if (_chromeHeader is not null) { _chromeHeader.Notice = "界面面板无法启动：" + exception.Message; }
                else if (_header is not null) { _header.Text = "界面面板无法启动：" + exception.Message; }
                UiPanelReady.TrySetResult(false);
            }
        }
    }

    internal Ui.UiGameDock? UiDock => _uiDock;
    internal Ui.UiHostChrome? ChromeHeader => _chromeHeader;
    internal Ui.UiHostChrome? ChromeFooter => _chromeFooter;

    /// <summary>Footer text: the host's own option flags only (what this process was started with), no live claim.</summary>
    private static string FooterStatus(BrowserHostOptions options)
    {
        var input = !options.CompositionGame ? "游戏输入关闭"
            : options.EnablePipelineInput ? "程序输入：已启用（需监督）" : "程序输入：关闭";
        return options.BackgroundKeepalive ? input + " · 后台保活" : input;
    }

    private int Px(int value) => (int)Math.Round(value * DeviceDpi / 96.0);

    /// <summary>Resize border inside the frameless window (physical px; 0 while maximized).</summary>
    internal int ResizeBorder => !Frameless || WindowState == FormWindowState.Maximized ? 0 : Math.Max(4, Px(5));

    /// <summary>Narrowest panel in physical px at scale 1 (GameDisplay.MinPanelCss at the window DPI).</summary>
    private int MinPanelPx => Px(GameDisplay.MinPanelCss);

    /// <summary>2026-10-15: DPI factor of the header / footer strips (their text and icons are DPI-scaled; the canvas is not).</summary>
    internal double ChromeScale => DeviceDpi / 96.0;

    /// <summary>Smallest client size: the game at <see cref="GameDisplay.MinScale"/> with a minimum-width panel.</summary>
    internal Size MinimumClient => GameDisplay.ClientFor(_displayScaling ? GameDisplay.MinScale : 1, PanelDetached ? 0 : MinPanelPx, ChromeScale);
    internal Ui.UiHostPrefs WindowPrefs => _prefs;
    /// <summary>Current display scale of the dock (1 = base size, game shown 1:1).</summary>
    internal double DisplayScale => _layout.Scale;
    /// <summary>Size the committed visual transform shows the 1280x720 canvas at (Empty = not committed).</summary>
    internal Size DisplaySize => _displaySize;
    internal bool DisplayScalingAvailable => _displayScaling;

    /// <summary>
    /// Enforces the docked minimum on every resize path (programmatic Size/ClientSize, user drag, snap): a frameless
    /// WS_POPUP window gets no WM_GETMINMAXINFO clamp from SetWindowPos, and Form.MinimumSize alone was not applied.
    /// </summary>
    /// <summary>Frameless: the window has no non-client area (WM_NCCALCSIZE), so window size == client size.</summary>
    protected override Size SizeFromClientSize(Size clientSize) => Frameless ? clientSize : base.SizeFromClientSize(clientSize);

    protected override void SetClientSizeCore(int x, int y)
    {
        if (Frameless && _uiDock is not null) { x = Math.Max(x, MinimumClient.Width); y = Math.Max(y, MinimumClient.Height); }
        // Frameless (no non-client area): set the window size directly, so WinForms reads the real client rect back
        // instead of caching a client size computed for a WS_THICKFRAME frame.
        if (Frameless) { Size = new Size(x, y); return; }
        base.SetClientSizeCore(x, y);
    }

    protected override void SetBoundsCore(int x, int y, int width, int height, BoundsSpecified specified)
    {
        if (Frameless && _uiDock is not null)
        {
            var minimum = MinimumClient;   // frameless: window size == client size
            if ((specified & BoundsSpecified.Width) != 0) { width = Math.Max(width, minimum.Width); }
            if ((specified & BoundsSpecified.Height) != 0) { height = Math.Max(height, minimum.Height); }
        }
        base.SetBoundsCore(x, y, width, height, specified);
    }

    /// <summary>
    /// docked layout for any window size: the game column (header strip, displayed game, footer) scales to the
    /// window height (held down only so the panel keeps its minimum width); the panel takes the remaining width and
    /// keeps zoom 1 (a wider panel reflows instead); the window buttons follow the right edge. The game WebView keeps Bounds 0,0 1280x720:
    /// only the viewport HWND and the composition root visual's transform change (see <see cref="SetGameDisplay"/>), so
    /// captures and program input stay in canvas pixels.
    /// </summary>
    private void ApplyDockLayout()
    {
        if (_panelHolder is null || _windowButtons is null || _uiDock is null || _chromeHeader is null || _chromeFooter is null
            || WindowState is not (FormWindowState.Normal or FormWindowState.Maximized)) { return; }
        var border = ResizeBorder;
        var layout = GameDisplay.Compute(ClientSize, border, MinPanelPx, Ui.UiWindowButtons.ScaledWidth(DeviceDpi), _displayScaling, ChromeScale, PanelLeft, PanelDetached);
        if (!SetGameDisplay(layout.Game))
        {
            // The transform could not be committed: fall back to the unscaled dock (game 1:1, panel takes the rest).
            layout = GameDisplay.Compute(ClientSize, border, MinPanelPx, Ui.UiWindowButtons.ScaledWidth(DeviceDpi), scaling: false, ChromeScale, PanelLeft, PanelDetached);
            SetGameDisplay(layout.Game);
        }
        _layout = layout;
        var zoom = (float)layout.Scale;
        _chromeHeader.Bounds = layout.Header;
        _chromeHeader.Zoom = zoom;
        // Panel on the left or detached: the window buttons sit over the game header's right end, which keeps it free.
        _chromeHeader.RightInset = PanelLeft || PanelDetached ? layout.Buttons.Width : 0;
        _chromeFooter.Bounds = layout.Footer;
        _chromeFooter.Zoom = zoom;
        _chromeFooter.Label = layout.Scale == 1 ? "画布 1280 × 720" : $"画布 1280 × 720 · 显示 {layout.Scale * 100:0}%";
        if (_panelWindow is null) { _panelHolder.Bounds = layout.Panel; }
        _uiDock.Panel.SetZoom(1);
        _windowButtons.Bounds = layout.Buttons;
        _windowButtons.Zoom = (float)layout.ButtonsScale;
        _windowButtons.Maximized = WindowState == FormWindowState.Maximized;
        _windowButtons.ResizeBorder = border;
        _windowButtons.BringToFront();
        Invalidate();
    }

    /// <summary>
    /// Shows the game at <paramref name="display"/> (client px): commits the root-visual scale for that size, then sizes
    /// the viewport HWND to it, so the guard's ViewportSize == DisplaySize check holds. Before the visual exists only the
    /// viewport moves (the guard refuses until the transform is committed). Returns false when the commit failed; the
    /// display size is then unknown (Empty) unless the caller falls back to 1:1.
    /// </summary>
    private bool SetGameDisplay(Rectangle display)
    {
        RequireUiThread();
        if (_visual is not null && display.Size != _displaySize)
        {
            try { _visual.SetScale(display.Width / 1280f, display.Height / 720f); _displaySize = display.Size; }
            catch (Exception exception) when (exception is COMException or InvalidCastException)
            {
                _displayScaling = false;
                _displaySize = Size.Empty;
                if (_chromeHeader is not null) { _chromeHeader.Notice = "游戏画面缩放不可用，保持 1:1 显示：" + exception.Message; }
                if (display.Size != HostGuard.Canvas) { return false; }
                // Even 1:1 failed to commit: leave the display size unknown, the guard keeps refusing.
                _viewport.Bounds = display;
                return true;
            }
        }
        _viewport.Bounds = display;
        return true;
    }

    /// <summary>Saves the restored window size and maximized state; best effort, never touches the game view.</summary>
    private void SaveWindowPrefs()
    {
        if (_uiDock is null || WindowState is not (FormWindowState.Normal or FormWindowState.Maximized)) { return; }
        var maximized = WindowState == FormWindowState.Maximized;
        var client = maximized ? RestoreBounds.Size : ClientSize;   // frameless: window size == client size
        var next = _prefs with
        {
            Width = Math.Clamp(client.Width, Ui.UiHostPrefs.MinWidth, Ui.UiHostPrefs.MaxWidth),
            Height = Math.Clamp(client.Height, Ui.UiHostPrefs.MinHeight, Ui.UiHostPrefs.MaxHeight),
            Maximized = maximized,
        };
        if (next == _prefs) { return; }
        _prefs = next;
        _prefs.Save(_uiDock.HostPrefsPath);
    }

    /// <summary>
    /// Whether minimizing hides the window in the tray.
    /// </summary>
    private bool KeepaliveWanted => _options.BackgroundKeepalive || (_options.CompositionGame && _uiDock is not null && _prefs.BackgroundRun);

    /// <summary>Whether this process's browser arguments include the keepalive switches.</summary>
    internal bool BackgroundArgumentsActive => _keepaliveArguments;

    /// <summary>
    /// 设置 → 窗口 → 后台运行 (window.background op set): saves the setting in the host's prefs and applies minimize
    /// interception now. --background-keepalive keeps it on. Returns the effective state, or null without a keeper.
    /// </summary>
    internal bool? SetBackgroundRun(bool enabled)
    {
        RequireUiThread();
        if (_keeper is null) { return null; }
        if (_uiDock is not null && _prefs.BackgroundRun != enabled)
        {
            _prefs = _prefs with { BackgroundRun = enabled };
            _prefs.Save(_uiDock.HostPrefsPath);
        }
        _keeper.Enabled = _options.BackgroundKeepalive || enabled;
        return _keeper.Enabled;
    }

    /// <summary>设置 → 外观 → 面板位置 (window.layout): which side of the game the panel sits on; display only.</summary>
    internal bool PanelLeft => _uiDock is not null && _prefs.PanelLeft && !_prefs.PanelDetached;
    /// <summary>面板位置「独立窗口」: the panel is in its own window and this window shows only the game column.</summary>
    internal bool PanelDetached => _uiDock is not null && _prefs.PanelDetached;
    internal string PanelSideName => PanelDetached ? "detached" : PanelLeft ? "left" : "right";
    internal Ui.UiPanelWindow? PanelWindow => _panelWindow;

    /// <summary>Compatibility wrapper (self-tests): right / left.</summary>
    internal bool? SetPanelLeft(bool left) => SetPanelSide(left ? "left" : "right") is string side ? side == "left" : null;

    /// <summary>
    /// Saves the panel position (right / left / detached) in the host prefs and re-lays out now: detaching moves the panel
    /// holder into its own window and shrinks this one to the game column; docking moves it back and gives the panel its
    /// default width again. Returns the applied side, or null without a dock. Display only: never input or a frame.
    /// </summary>
    internal string? SetPanelSide(string side)
    {
        RequireUiThread();
        if (_uiDock is null || _panelHolder is null || side is not ("left" or "right" or "detached")) { return null; }
        var detached = side == "detached";
        var left = side == "left";
        if (_prefs.PanelDetached != detached || (!detached && _prefs.PanelLeft != left))
        {
            var wasDetached = _prefs.PanelDetached;
            // While detached PanelLeft keeps the side it was docked on: closing the panel window docks it back there.
            _prefs = _prefs with { PanelLeft = detached ? _prefs.PanelLeft : left, PanelDetached = detached };
            MinimumSize = MinimumClient;   // the game-only window may be narrower than the docked minimum
            if (detached && !wasDetached) { DetachPanel(show: true); }
            else if (!detached && wasDetached) { DockPanel(); }
            _prefs.Save(_uiDock.HostPrefsPath);
            ApplyDockLayout();
            // The page drops its window-button gap unless the panel is docked on the right (status.update.window_layout).
            if (_uiDock.Router.StatusSubscribed) { _uiDock.Router.EmitStatus(); }
        }
        return PanelSideName;
    }

    /// <summary>
    /// moves the panel holder (the UI WebView keeps its HWND and controller) into its own window, opened where the
    /// docked panel was, and shrinks this window to the game column at the current scale.
    /// </summary>
    private void DetachPanel(bool show)
    {
        if (_panelHolder is null || _uiDock is null || _panelWindow is not null) { return; }
        var near = _panelHolder.IsHandleCreated && _panelHolder.Width > 0
            ? _panelHolder.RectangleToScreen(_panelHolder.ClientRectangle) : Rectangle.Empty;
        var window = _panelWindow = new Ui.UiPanelWindow(Icon, _uiDock.Background, _prefs.PanelWindow, near, DeviceDpi);
        window.DockRequested += () => SetPanelSide(_prefs.PanelLeft ? "left" : "right");
        window.ResizeEnd += (_, _) => SavePanelWindow();
        _ = window.Handle;   // the holder moves straight into a live parent (its HWND and the UI WebView are kept)
        Controls.Remove(_panelHolder);
        _panelHolder.Dock = DockStyle.Fill;
        window.Controls.Add(_panelHolder);
        if (IsHandleCreated && WindowState == FormWindowState.Normal)
        {
            ClientSize = GameDisplay.ClientFor(_layout.Scale > 0 ? _layout.Scale : 1, 0, ChromeScale);
        }
        if (show) { window.Show(); }
    }

    /// <summary>moves the panel back into this window (on the side it was docked on) and widens it by the default panel width.</summary>
    private void DockPanel()
    {
        if (_panelWindow is not { } window || _panelHolder is null) { return; }
        SavePanelWindow();
        PullBackPanel(window);
        if (WindowState == FormWindowState.Normal)
        {
            var work = Screen.FromHandle(Handle).WorkingArea;
            var wanted = GameDisplay.ClientFor(_layout.Scale > 0 ? _layout.Scale : 1, Px(Ui.UiHostPrefs.Default.Width - 1280), ChromeScale);
            ClientSize = new Size(Math.Min(wanted.Width, work.Width), Math.Min(wanted.Height, work.Height));
        }
    }

    private void PullBackPanel(Ui.UiPanelWindow window)
    {
        _panelWindow = null;
        window.Controls.Remove(_panelHolder);
        _panelHolder!.Dock = DockStyle.None;
        Controls.Add(_panelHolder);
        _windowButtons?.BringToFront();
        window.AllowClose = true;
        window.Close();
        window.Dispose();
    }

    /// <summary>App exit: the panel returns into this window before it closes (the setting stays 「独立窗口」).</summary>
    private void CloseDetachedPanel()
    {
        if (_panelWindow is not { } window) { return; }
        SavePanelWindow();
        PullBackPanel(window);
    }

    private void SavePanelWindow()
    {
        if (_panelWindow is not { } window || _uiDock is null || window.WindowState == FormWindowState.Minimized) { return; }
        var bounds = window.SavedBounds;
        if (bounds == _prefs.PanelWindow) { return; }
        _prefs = _prefs with { PanelWindow = bounds };
        _prefs.Save(_uiDock.HostPrefsPath);
    }

    /// <summary>设置 → 窗口 → 现在隐藏 (window.background op hide): hide now, same path as the minimize intercept.</summary>
    internal string? HideToBackground()
    {
        RequireUiThread();
        return _keeper is { Enabled: true } keeper ? keeper.Hide() : null;
    }

    // While Maa runs, ask Windows not to idle-lock or blank the display (a lock stops capture and ends a long scan).
    // The request lapses when the run ends or the host exits. A manual Win+L still locks.
    [System.Runtime.InteropServices.DllImport("kernel32.dll")]
    private static extern uint SetThreadExecutionState(uint flags);
    private const uint EsContinuous = 0x80000000, EsSystemRequired = 0x1, EsDisplayRequired = 0x2;
    private bool _keepAwake;

    private void KeepAwakeTick(bool running)
    {
        if (running == _keepAwake) { return; }
        _keepAwake = running;
        SetThreadExecutionState(running ? EsContinuous | EsSystemRequired | EsDisplayRequired : EsContinuous);
    }

    /// <summary>status.update window_background: setting, whether the arguments are in effect, and the hide state.</summary>
    private System.Text.Json.Nodes.JsonObject WindowBackgroundStatus() => new()
    {
        ["supported"] = _keeper is not null,
        ["enabled"] = _keeper?.Enabled ?? false,
        ["forced"] = _options.BackgroundKeepalive,
        ["args_active"] = _keepaliveArguments,
        ["hide_mode"] = _keeper?.HideMode ?? BackgroundKeeper.None,
    };

    /// <summary>
    /// 静音 (header icon, or the panel's game.audio.set): sets the game view's own mute flag and saves it in the host's
    /// prefs. Audio only: no input, no frame, no page script. Returns the applied state, or null on a host without a
    /// game view (the saved preference is applied when the game view is created).
    /// </summary>
    private bool? SetGameMuted(bool muted)
    {
        RequireUiThread();
        if (!_options.CompositionGame || _uiDock is null) { return null; }
        _prefs = _prefs with { Muted = muted };
        _prefs.Save(_uiDock.HostPrefsPath);
        if (_chromeHeader is not null) { _chromeHeader.Muted = muted; }
        if (_webView?.CoreWebView2 is not { } core) { return muted; }
        core.IsMuted = muted;
        return core.IsMuted;
    }

    internal TaskCompletionSource<bool> UiPanelReady { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    internal Panel GameViewport => _viewport;
    internal Rectangle GameBounds => _webView is null ? Rectangle.Empty : _webView.Bounds;
    internal double GameRasterScale => _webView is null ? 0 : _webView.RasterizationScale;
    internal long CaptureSequence => _captureSequence;

    /// <summary>Recognition crops installed (agent/core/resource_bundles.py loads either bundle); without both every page
    /// check fails, so the panel says so before a run (UX review #4, 2026-10-24).</summary>
    private static bool RecognitionReady()
    {
        var root = SelectionLoopBudget.Root();
        return File.Exists(Path.Combine(root, "assets", "resource", "recognition", "manifest.json"))
            || File.Exists(Path.Combine(root, "dev_assets", "private", "resource_native", "manifest.json"));
    }

    /// <summary>In-process status for the UI panel: host fields only, never a capture (capture_seq must not move because of the UI).</summary>
    private System.Text.Json.Nodes.JsonObject UiStatus()
    {
        var status = new System.Text.Json.Nodes.JsonObject
        {
            ["connected"] = _ready,
            ["hide_mode"] = _keeper?.HideMode ?? BackgroundKeeper.None,
            ["window_background"] = WindowBackgroundStatus(),
            ["game_data"] = _gameData?.Status(_prefs.Muted),
            ["notification_state"] = _keeper?.LastNotificationState ?? "unavailable",
            ["notification_error"] = _keeper?.LastNotificationError,
            ["capture_seq"] = _captureSequence,
            ["manual_click_seq"] = _manualClickSeq,
            ["desktop_locked"] = _desktopLocked,
            ["host_guard_reason"] = HostGuardReason,
            ["pipeline_input_enabled"] = _options.EnablePipelineInput,
            ["recognition_ready"] = RecognitionReady(),
            ["clicks_this_run"] = _pipelineGate?.RunCount ?? 0,
            // A Maa run started outside the panel is clicking (紧急停止 stays available for it).
            ["external_run"] = ExternalPipelineRecent && !EngineActive && !_externalStopped,
            // Per-run windows: a launcher's pipeline-run-begin may open a fresh window now (no gesture in the last 30 s).
            ["run_window"] = _pipelineGate?.RunWindow ?? 0,
            ["run_begin_ready"] = _pipelineGate is not null && _pipelineGate.RunBeginRefusal() is null,
        };
        status["frame_age_ms"] = _lastFrameSha256 is null ? null : (long)Stopwatch.GetElapsedTime(_lastFrameTimestamp).TotalMilliseconds;
        return status;
    }

    private void ForwardManualMouse(CoreWebView2MouseEventKind kind, MouseEventArgs mouse, bool focus)
    {
        if (_webView?.CoreWebView2 is null || !_options.CompositionGame ||
            (kind != CoreWebView2MouseEventKind.Move && mouse.Button != MouseButtons.Left)) { return; }
        // the viewport shows the canvas scaled; without a committed display transform the mapping is unknown.
        if (_displaySize.IsEmpty) { return; }
        if (focus) { _webView.MoveFocus(CoreWebView2MoveFocusReason.Programmatic); }
        var flags = mouse.Button.HasFlag(MouseButtons.Left) && kind != CoreWebView2MouseEventKind.LeftButtonUp
            ? CoreWebView2MouseEventVirtualKeys.LeftButton : CoreWebView2MouseEventVirtualKeys.None;
        // Map the user's display point back through the inverse display transform to canvas pixels.
        var canvas = GameDisplay.ToCanvas(new Point(mouse.X, mouse.Y), _displaySize);
        _webView.SendMouseInput(kind, flags, 0, canvas);
        if (kind == CoreWebView2MouseEventKind.LeftButtonDown)
        {
            _manualClickSeq++;
        }
        InvalidateFrame(kind == CoreWebView2MouseEventKind.Move ? "manual_mouse_move" :
            kind == CoreWebView2MouseEventKind.LeftButtonDown ? "manual_mouse_down" : "manual_mouse_up");
    }

    private async void OpenLoginWindow(object? sender, CoreWebView2NewWindowRequestedEventArgs popup)
    {
        popup.Handled = true;
        if (!_options.CompositionGame || _browserEnvironment is null || !BrowserNavigationPolicy.CanBrowse(popup.Uri))
        { return; }
        using var deferred = popup.GetDeferral();
        var window = new LoginBrowserForm();
        _loginWindows.Add(window);
        InvalidateFrame("login_window_open");
        window.FormClosed += (_, _) =>
        {
            _loginWindows.Remove(window);
            InvalidateFrame("login_window_closed");
        };
        try
        {
            window.Show(this);
            popup.NewWindow = await window.InitializeAsync(_browserEnvironment, OpenLoginWindow);
        }
        catch (Exception exception)
        {
            window.Close();
            MessageBox.Show(this, "登录窗口打开失败：" + exception.Message, "登录", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        finally { deferred.Complete(); }
    }

    private Task<T> OnUi<T>(Func<Task<T>> action)
    {
        var completion = new TaskCompletionSource<T>(TaskCreationOptions.RunContinuationsAsynchronously);
        BeginInvoke(async () =>
        {
            try { completion.SetResult(await action()); }
            catch (Exception exception) { completion.SetException(exception); }
        });
        return completion.Task;
    }

    private async Task<byte[]> CaptureImage(string captureSource)
    {
        RequireUiThread();
        if (HostGuardReason is string reason)
        { throw new InvalidOperationException("游戏窗口不可截图：" + reason); }
        // A capture in progress is not an input permit. Do not rebind a capture if navigation/manual input
        // or another capture invalidated its starting state while CapturePreviewAsync was awaiting.
        InvalidateFrame("capture_starting");
        var startingSequence = _frameBindingSequence;
        _capturesInFlight++;
        try
        {
            using var stream = new MemoryStream();
            var captureStarted = HostMonotonic.NowMs();
            await _webView.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream);
            var captureCompleted = HostMonotonic.NowMs();
            RequireUiThread();
            if (HostGuardReason is not null || _frameBindingSequence != startingSequence)
            { throw new InvalidOperationException("截图期间游戏窗口状态或帧绑定发生变化。"); }
            stream.Position = 0;
            using var source = Image.FromStream(stream);
            _sourceWidth = source.Width; _sourceHeight = source.Height;
            if (Math.Abs(source.Width - 1280) > 2 || Math.Abs(source.Height - 720) > 2)
            { throw new InvalidOperationException($"Composition 画布尺寸异常：{source.Width}×{source.Height}"); }
            if (source.Width == 1280 && source.Height == 720) { return BindFrame(stream.ToArray(), captureSource, captureStarted, captureCompleted); }
            using var normalized = new Bitmap(1280, 720);
            using (var graphics = Graphics.FromImage(normalized)) { graphics.DrawImage(source, new Rectangle(0, 0, 1280, 720)); }
            using var output = new MemoryStream();
            normalized.Save(output, System.Drawing.Imaging.ImageFormat.Png);
            return BindFrame(output.ToArray(), captureSource, captureStarted, captureCompleted);
        }
        catch { InvalidateFrame("capture_failed"); throw; }
        finally { _capturesInFlight--; }
    }

    /// <summary>2026-10-14 Perfect calibration (user-approved plan B): after a sent timed APPEAL, capture a short burst
    /// of raw diagnostic frames with host timestamps. Our own judgement only flashes briefly after the tap, which the
    /// Maa capture cadence (~1 frame/s) misses. Diagnostic only: no frame binding, no input permit, never throws.</summary>
    private async Task<NavigationResult> AfterTimedAppeal(Task<NavigationResult> click, AppealTimingObservation? observation)
    {
        var result = await click;
        if (result.ok) { _ = AppealBurst(observation); }
        return result;
    }

    private const int AppealBurstFrames = 12;
    private const double AppealBurstWindowMs = 1500;

    private async Task AppealBurst(AppealTimingObservation? observation)
    {
        try
        {
            var dir = Path.Combine(SelectionLoopBudget.Root(), "dev_assets", "private", "wing_timing_burst",
                DateTime.UtcNow.ToString("yyyyMMdd'T'HHmmssfff'Z'"));
            Directory.CreateDirectory(dir);
            var frames = new System.Text.Json.Nodes.JsonArray();
            var end = HostMonotonic.NowMs() + AppealBurstWindowMs;
            for (var i = 0; i < AppealBurstFrames && HostMonotonic.NowMs() < end; i++)
            {
                if (HostGuardReason is not null || _webView.CoreWebView2 is null) { break; }
                using var stream = new MemoryStream();
                var started = HostMonotonic.NowMs();
                await _webView.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream);
                var completed = HostMonotonic.NowMs();
                var name = i.ToString("00") + ".png";
                await File.WriteAllBytesAsync(Path.Combine(dir, name), stream.ToArray());
                frames.Add(new System.Text.Json.Nodes.JsonObject { ["file"] = name, ["started_ms"] = started, ["completed_ms"] = completed });
            }
            var meta = new System.Text.Json.Nodes.JsonObject
            {
                ["requested_down_ms"] = observation?.requested_down_ms, ["selected_down_ms"] = observation?.selected_down_ms,
                ["actual_down_ms"] = observation?.actual_down_ms, ["actual_up_ms"] = observation?.actual_up_ms,
                ["period_ms"] = observation?.period_ms, ["frames"] = frames,
            };
            await File.WriteAllTextAsync(Path.Combine(dir, "burst.json"), meta.ToJsonString());
        }
        catch { /* diagnostics only */ }
    }

    private byte[] BindFrame(byte[] bytes, string source, double captureStarted, double captureCompleted)
    {
        RequireUiThread();
        _lastFrameSha256 = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        _frameChanges.Observe(_lastFrameSha256);
        _lastFrameTimestamp = Stopwatch.GetTimestamp();
        _captureStartedMs = captureStarted; _captureCompletedMs = captureCompleted;
        _captureSequence = checked(_captureSequence + 1);
        _frameBindingSequence = checked(_frameBindingSequence + 1);
        _captureSource = source;
        _frameBindingEvent = source;
        return bytes;
    }

    /// <summary>
    /// User-initiated reload of the game page (header 「刷新」), for a stuck loading screen. Refused while a Maa run is
    /// active; asks for confirmation because a reload can interrupt an in-game action. Never called by automation.
    /// </summary>
    private void RequestManualReload()
    {
        if (_webView?.CoreWebView2 is null) { return; }
        if (_uiDock?.Engine.Active == true || _uiDock?.CardScan.Active == true || _uiDock?.KnowhowScan.Active == true
            || _gameData?.Busy == true)
        {
            MessageBox.Show(this, _gameData?.Busy == true ? "正在清理游戏数据，请稍候。" : "正在运行任务或扫描，请先停止后刷新。", "刷新游戏", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        var answer = MessageBox.Show(this, "重新载入游戏页面？" + Environment.NewLine + "正在进行的操作（如兑换、Live）可能被中断，刷新后请确认游戏内结果。",
            "刷新游戏", MessageBoxButtons.OKCancel, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2);
        if (answer != DialogResult.OK) { return; }
        InvalidateFrame("manual_reload");
        _webView.CoreWebView2.Reload();
    }

    private void InvalidateFrame(string reason)
    {
        RequireUiThread();
        _lastFrameSha256 = null;
        _frameBindingSequence = checked(_frameBindingSequence + 1);
        _frameBindingEvent = reason;
    }

    private void RequireUiThread()
    {
        if (Environment.CurrentManagedThreadId != _uiThreadId)
        { throw new InvalidOperationException("帧绑定与输入必须在宿主 UI 线程执行。"); }
    }

    private async Task<NavigationResult> NavigateOnce(string sha, int sourceWidth, int sourceHeight)
    {
        var navigation = _options.SupervisedNavigation;
        if (!_options.CompositionGame || navigation is null || _navigationBudget is null || HostGuardReason is not null ||
            !Uri.TryCreate(_webView.CoreWebView2.Source, UriKind.Absolute, out var source) ||
            source.Scheme != "https" || source.Host != "shinycolors.enza.fun")
        { return new(false, false, "host_state_guard_failed"); }
        if (_lastFrameSha256 is null || sha.Length != 64 || sha != _lastFrameSha256 ||
            sourceWidth != _sourceWidth || sourceHeight != _sourceHeight ||
            Stopwatch.GetElapsedTime(_lastFrameTimestamp).TotalMilliseconds > 5000)
        { return new(false, false, "frame_binding_guard_failed"); }
        if (!_navigationBudget.Consume(navigation, sha)) { return new(false, false, "one_shot_budget_exhausted"); }
        InvalidateFrame("navigation_gesture");
        var fixedPoint = navigation switch
        {
            "announcement-back" => new Point(57, 662),
            "mission-entry" => new Point(62, 429),
            "touch-to-start" => new Point(640, 630),
            _ => throw new InvalidOperationException("未知固定导航；禁止任意坐标。")
        };
        var point = new Point((int)Math.Round(fixedPoint.X * sourceWidth / 1280.0), (int)Math.Round(fixedPoint.Y * sourceHeight / 720.0));
        try
        {
            await SendNativeGesture(point);
            return new(true, true, "native_gesture_sent_postcondition_required");
        }
        catch (Exception exception) { return new(false, true, "native_input_failed_no_retry: " + exception.Message); }
    }

    private Task SendNativeGesture(Point point) => NativeGesture.Send(
        (kind, keys) => _webView.SendMouseInput(kind, keys, 0, point));

    private async Task<SelectionLoopResponse> SelectionLoop(SelectionLoopRequest request, CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        if (!_options.EnableTrainingSelectionLoop || _selectionLoopBudget is null) { return new(false, false, "selection_loop_disabled", "blocked", 1); }
        var snapshot = _selectionLoopBudget.Inspect();
        if (request.Command == "selection-loop-status") { return new(true, false, "readonly_ledger_status", snapshot.State, snapshot.Attempts); }
        if (!_options.CompositionGame || HostGuardReason is not null ||
            !DailyNavigationPolicy.IsGameUri(_webView.CoreWebView2.Source))
        { return new(false, false, "host_state_guard_failed", snapshot.State, snapshot.Attempts); }
        var frame = new DailyNavigationRequest(SelectionLoopBudget.Action, request.FrameSha256!, request.SourceWidth, request.SourceHeight, request.Nonce!);
        bool Bound() => DailyNavigationPolicy.FrameMatches(frame, _lastFrameSha256, _sourceWidth, _sourceHeight,
            Stopwatch.GetElapsedTime(_lastFrameTimestamp).TotalMilliseconds);
        if (!Bound()) { return new(false, false, "frame_binding_guard_failed", snapshot.State, snapshot.Attempts); }
        if (request.Command == "selection-loop-confirm")
        {
            var confirmed = _selectionLoopBudget.Confirm(request.Nonce!, request.FrameSha256!, request.ProofPath!, request.ProofSha256!);
            snapshot = _selectionLoopBudget.Inspect();
            return new(confirmed, false, confirmed ? "selection_postcondition_confirmed" : "pending_identity_mismatch", snapshot.State, snapshot.Attempts);
        }
        cancellation.ThrowIfCancellationRequested();
        if (!_selectionLoopBudget.Begin(request.Nonce!, request.FrameSha256!))
        { return new(false, false, "pending_replay_or_budget_exhausted", snapshot.State, snapshot.Attempts); }
        snapshot = _selectionLoopBudget.Inspect();
        // Recheck after disk I/O; an intervening manual gesture may invalidate the frame.
        if (!Bound()) { return new(false, false, "frame_changed_after_pending_no_retry", snapshot.State, snapshot.Attempts); }
        InvalidateFrame("selection_gesture");
        var point = new Point((int)Math.Round(461.0*request.SourceWidth/1280), (int)Math.Round(236.0*request.SourceHeight/720));
        try
        {
            await SendNativeGesture(point); _selectionLoopBudget.MarkNativeSent(request.Nonce!);
            return new(true, true, "native_selection_sent_postcondition_required", "pending", snapshot.Attempts);
        }
        catch (Exception exception) { return new(false, true, "native_selection_unknown_no_retry: " + exception.Message, "pending", snapshot.Attempts); }
    }

    private async Task<RepeatableNavigationResponse> RepeatableNavigation(RepeatableNavigationRequest request, CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        if (!_options.EnableRepeatableNavigation || _repeatableBudget is null || !RepeatableNavigationBudget.IsRepeatable(request.Action))
        { return new(false, false, "repeatable_navigation_disabled", "blocked", 0, 0); }
        RepeatableNavigationResponse Respond(bool ok, bool attempted, string reason)
        {
            var snapshot = _repeatableBudget.Inspect(request.Action);
            return new(ok, attempted, reason, snapshot.State, snapshot.ActionCount, snapshot.DayCount);
        }
        if (request.Command == "repeatable-status") { return Respond(true, false, "readonly_ledger_status"); }
        var frame = new DailyNavigationRequest(request.Action, request.FrameSha256!, request.SourceWidth, request.SourceHeight, request.Nonce!);
        if (request.Command == "repeatable-confirm")
        {
            if (!_options.EnableSupervisedDaily || !_options.CompositionGame || _options.SupervisedNavigation is not null ||
                HostGuardReason is not null ||
                !DailyNavigationPolicy.IsGameUri(_webView.CoreWebView2.Source))
            { return Respond(false, false, "host_state_guard_failed"); }
            if (!DailyNavigationPolicy.FrameMatches(frame, _lastFrameSha256, _sourceWidth, _sourceHeight,
                Stopwatch.GetElapsedTime(_lastFrameTimestamp).TotalMilliseconds))
            { return Respond(false, false, "frame_binding_guard_failed"); }
            var outcome = _repeatableBudget.ConfirmOutcome(request.Action, request.Nonce!, request.FrameSha256!, request.ProofPath!, request.ProofSha256!);
            return Respond(outcome is not null, false, outcome switch
            {
                "confirmed" => "repeatable_postcondition_confirmed",
                "interrupted" => "repeatable_interruption_recorded",
                _ => "pending_identity_mismatch",
            });
        }
        var result = await NavigateDailyOnce(frame, cancellation, _repeatableBudget);
        return Respond(result.ok, result.attempted, result.reason);
    }

    /// <summary>The opt-in pipeline transport: same host/frame guards, in-memory limits only, no ledger.</summary>
    private Task<NavigationResult> PipelineClick(PipelineClickRequest request, CancellationToken cancellation)
    {
        RequireUiThread();
        if (!_options.EnablePipelineInput || _pipelineGate is null) { return Task.FromResult(new NavigationResult(false, false, "pipeline_input_disabled")); }
        // 紧急停止 for an outside run holds until that run has been quiet for 60 s or the panel starts its own run.
        if (_externalStopped && (EngineActive || !ExternalPipelineRecent)) { _externalStopped = false; }
        // A Maa run outside the UI is using the pipeline endpoint: the recorder pauses.
        _lastExternalPipelineTimestamp = Stopwatch.GetTimestamp();
        if (_externalStopped) { return Task.FromResult(new NavigationResult(false, false, "emergency_stop")); }
        return RunPipelineGate(_pipelineGate, request, cancellation);
    }

    private string? PipelineHostGuard() => !_options.CompositionGame || _options.SupervisedNavigation is not null ||
        HostGuardReason is not null || !DailyNavigationPolicy.IsGameUri(_webView.CoreWebView2.Source)
        ? "host_state_guard_failed" : null;

    /// <summary>One request through one gate: the existing host guard, frame binding, admission, audit and gesture.</summary>
    private Task<NavigationResult> RunPipelineGate(PipelineInputGate gate, PipelineClickRequest request, CancellationToken cancellation)
    {
        RequireUiThread();
        bool FrameBound(PipelineFrameRequest frame)
        {
            RequireUiThread();
            return PipelineInputPolicy.FrameMatches(frame, _lastFrameSha256, _sourceWidth, _sourceHeight,
                Stopwatch.GetElapsedTime(_lastFrameTimestamp).TotalMilliseconds, _captureSequence, _frameBindingSequence,
                _capturesInFlight != 0);
        }
        // The gate chose the canonical point inside the action rect; only scale it to the capture source size.
        Task<NavigationResult> Gesture(PipelineClickRequest admitted, Point canonical, Func<string?> guard)
        {
            var frame = admitted.Frame;
            var point = new Point((int)Math.Round(canonical.X * frame.SourceWidth / 1280.0), (int)Math.Round(canonical.Y * frame.SourceHeight / 720.0));
            void ConsumeFrame() { RequireUiThread(); InvalidateFrame("pipeline_gesture"); }
            // Exploration cells are plain clicks; only fixed table actions may carry a wheel delta.
            if (!gate.IsExplore(admitted.Action) && gate.Table.WheelOf(admitted.Action) is int delta)
            {
                // Wheel action (B5b): pointer move + one fixed wheel delta inside the rect; no button.
                return Task.FromResult(PipelineGesture.Wheel(guard, ConsumeFrame, delta, point,
                    (kind, keys, data, target) => _webView.SendMouseInput(kind, keys, data, target)));
            }
            if (!gate.IsExplore(admitted.Action) && admitted.DragTo is Point dragTo && admitted.DragMs is int dragMs)
            {
                // SC.PipelineDrag.v1: start at the admitted (recognised) point, end inside the action's drag_rect.
                var end = new Point((int)Math.Round(dragTo.X * frame.SourceWidth / 1280.0), (int)Math.Round(dragTo.Y * frame.SourceHeight / 720.0));
                return PipelineGesture.Drag(guard, ConsumeFrame, point, end, dragMs,
                    (kind, keys, target) => _webView.SendMouseInput(kind, keys, 0, target));
            }
            var click = PipelineGesture.Click(guard, ConsumeFrame, (kind, keys) => _webView.SendMouseInput(kind, keys, 0, point),
                timing: admitted.AppealTiming, observation: admitted.TimingObservation, timedDown: gate.RecordTimedDown,
                cancellation: cancellation, holdMs: admitted.HoldMs);
            if (admitted.AppealTiming is null) { return click; }
            return AfterTimedAppeal(click, admitted.TimingObservation);
        }
        return gate.Execute(request, PipelineHostGuard, FrameBound, Gesture, cancellation);
    }

    /// <summary>
    /// 设置 → 开发者「重新载入动作表」: re-reads and validates assets/input/pipeline_actions.json (the same checks as at
    /// startup) and swaps it in only when nothing can be using the old one — no UI run, no card scan, no pipeline request in
    /// the last 60 s. The pipeline endpoint is re-created (new token, port and spend secret; its session
    /// file carries the new action_table_sha256), so a stale client is refused; frame permits are invalidated. On any error
    /// the old table, gate and session stay unchanged.
    /// </summary>
    private string? ReloadActionTable()
    {
        RequireUiThread();
        if (EngineActive) { return "engine_active"; }
        if (_uiDock?.CardScan.Active == true) { return "card_scan_active"; }
        if (_uiDock?.KnowhowScan.Active == true) { return "knowhow_scan_active"; }
        if (ExternalPipelineRecent) { return "supervised_run_recent"; }
        PipelineActionTable table;
        try
        {
            table = PipelineActionTable.Load(PipelineActionTable.DefaultPath(), SelectionLoopBudget.Root());
            if (_options.EnableProduceExplore) { ProduceExploreGrid.RequireDisjointIds(table); }
        }
        catch (Exception exception) when (exception is InvalidOperationException or IOException or UnauthorizedAccessException or
            System.Text.Json.JsonException or KeyNotFoundException)
        { return "validation_failed: " + exception.Message; }
        var oldGate = _pipelineGate; var oldBridge = _pipelineBridge;
        if (_options.EnablePipelineInput && _bridgeSessionPath is string path)
        {
            var gate = new PipelineInputGate(table, _options.EnableProduceExplore ? ProduceExploreGrid.Default : null);
            oldBridge?.Dispose(); // deletes the old session file first: the new one is created with FileMode.CreateNew
            try
            {
                _pipelineBridge = new PipelineInputBridge(path + PipelineInputPolicy.SessionSuffix, gate,
                    (request, cancellation) => OnUi(() => PipelineClick(request, cancellation)));
                _pipelineGate = gate;
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or System.Net.Sockets.SocketException)
            {
                // Restore the previous endpoint over the previous table; the table stays unchanged.
                _pipelineBridge = oldGate is null ? null : new PipelineInputBridge(path + PipelineInputPolicy.SessionSuffix, oldGate,
                    (request, cancellation) => OnUi(() => PipelineClick(request, cancellation)));
                return "session_rewrite_failed: " + exception.Message;
            }
        }
        PipelineActionTable.Commit(table);
        InvalidateFrame("action_table_reload");
        return null;
    }

    private T OnUiSync<T>(Func<T> work) => InvokeRequired ? (T)Invoke(work) : work();

    private bool EngineActive => _uiDock?.Engine.Active == true;
    private bool _externalStopped;

    private bool StopExternalRun() => OnUiSync(() =>
    {
        if (EngineActive || !ExternalPipelineRecent) { return false; }
        _externalStopped = true;
        return true;
    });
    private bool ExternalPipelineRecent => _lastExternalPipelineTimestamp != 0 &&
        Stopwatch.GetElapsedTime(_lastExternalPipelineTimestamp).TotalSeconds < 60;

    private async Task<NavigationResult> NavigateDailyOnce(DailyNavigationRequest request, CancellationToken cancellation,
        RepeatableNavigationBudget? repeatable = null)
    {
        // Only the separate repeatable endpoint passes a ledger; the daily endpoint keeps one-shot budgets.
        if (repeatable is not null && (!_options.EnableRepeatableNavigation || !RepeatableNavigationBudget.IsRepeatable(request.Action)))
        { return new(false, false, "repeatable_action_not_whitelisted"); }
        cancellation.ThrowIfCancellationRequested();
        if (DailyNavigationPolicy.IsTraining(request.Action) && !_options.EnableSupervisedTraining)
        { return new(false, false, "training_not_explicitly_enabled"); }
        if (DailyNavigationPolicy.IsGrade(request.Action) && !_options.EnableSupervisedGrade)
        { return new(false, false, "grade_not_explicitly_enabled"); }
        // Difficulty taps may consume a フェス参加権: never on the repeatable ledger.
        if (repeatable is not null && DailyNavigationPolicy.IsGradeConsuming(request.Action))
        { return new(false, false, "consuming_action_never_repeatable"); }
        if (DailyNavigationPolicy.IsShop(request.Action) && !_options.EnableSupervisedShop)
        { return new(false, false, "shop_not_explicitly_enabled"); }
        // Consuming exchange confirms never use the repeatable ledger.
        if (repeatable is not null && DailyNavigationPolicy.IsShopConsuming(request.Action))
        { return new(false, false, "consuming_action_never_repeatable"); }
        var scroll = DailyNavigationPolicy.IsScroll(request.Action);
        if (scroll && !_options.EnableSupervisedScroll)
        { return new(false, false, "scroll_not_explicitly_enabled"); }
        if (!_options.EnableSupervisedDaily || !_options.CompositionGame || _options.SupervisedNavigation is not null ||
            HostGuardReason is not null ||
            !DailyNavigationPolicy.IsGameUri(_webView.CoreWebView2.Source))
        { return new(false, false, "host_state_guard_failed"); }
        if (!DailyNavigationPolicy.FrameMatches(request, _lastFrameSha256, _sourceWidth, _sourceHeight,
            Stopwatch.GetElapsedTime(_lastFrameTimestamp).TotalMilliseconds))
        { return new(false, false, "frame_binding_guard_failed"); }
        if (scroll && request.FrameSha256 == _lastScrollFrameSha256)
        { return new(false, false, "scroll_frame_unchanged_no_retry"); }
        // Resolve the original announcement ledger, never issue it a fresh daily budget.
        if (repeatable is not null)
        {
            try
            {
                cancellation.ThrowIfCancellationRequested();
                var refusal = repeatable.Begin(request.Action, request.Nonce, request.FrameSha256);
                if (refusal is not null) { return new(false, false, refusal); }
            }
            catch (Exception exception) { return new(false, false, "budget_guard_failed_no_reset: " + exception.Message); }
            // Recheck after disk I/O; an intervening manual gesture may invalidate the frame. Pending stays.
            if (!DailyNavigationPolicy.FrameMatches(request, _lastFrameSha256, _sourceWidth, _sourceHeight,
                Stopwatch.GetElapsedTime(_lastFrameTimestamp).TotalMilliseconds))
            { return new(false, false, "frame_changed_after_pending_no_retry"); }
            InvalidateFrame("repeatable_gesture");
            var fixedRepeatable = DailyNavigationPolicy.FixedPoint(request.Action);
            var repeatablePoint = new Point((int)Math.Round(fixedRepeatable.X * request.SourceWidth / 1280.0),
                (int)Math.Round(fixedRepeatable.Y * request.SourceHeight / 720.0));
            try
            {
                await SendNativeGesture(repeatablePoint);
                repeatable.MarkNativeSent(request.Nonce);
                return new(true, true, "native_gesture_sent_postcondition_required");
            }
            catch (Exception exception) { return new(false, true, "native_input_failed_no_retry: " + exception.Message); }
        }
        try
        {
            cancellation.ThrowIfCancellationRequested();
            var accepted = scroll
                ? new ReadOnlyScrollBudget(request.Action).Consume(request.Action, request.FrameSha256, request.Nonce)
                : new NavigationBudget(request.Action).Consume(request.Action, request.FrameSha256, request.Nonce);
            if (!accepted)
            { return new(false, false, scroll ? "scroll_budget_or_replay_guard_failed" : "one_shot_budget_exhausted"); }
        }
        catch (Exception exception) { return new(false, false, "budget_guard_failed_no_reset: " + exception.Message); }
        InvalidateFrame("daily_gesture");
        if (scroll) { _lastScrollFrameSha256 = request.FrameSha256; }
        var fixedPoint = DailyNavigationPolicy.FixedPoint(request.Action);
        var point = new Point((int)Math.Round(fixedPoint.X * request.SourceWidth / 1280.0),
            (int)Math.Round(fixedPoint.Y * request.SourceHeight / 720.0));
        try
        {
            if (scroll)
            {
                NativeScroll.Send(request.Action, point, (kind, keys, data, target) => _webView.SendMouseInput(kind, keys, data, target));
                return new(true, true, "native_wheel_sent_postcondition_required");
            }
            await SendNativeGesture(point);
            return new(true, true, "native_gesture_sent_postcondition_required");
        }
        catch (Exception exception) { return new(false, true, "native_input_failed_no_retry: " + exception.Message); }
    }

    /// <summary>The only place window state is sampled for guards (see HostGuard).</summary>
    private HostSnapshot Snapshot() => new(
        _ready, Visible, WindowState == FormWindowState.Minimized, _viewport.Visible, _viewport.Size,
        _webView is null ? Rectangle.Empty : _webView.Bounds, _webView is not null && _webView.IsVisible, _desktopLocked, _displaySize);

    private bool LoginActive => _options.CompositionGame && (_loginWindows.Count != 0 ||
        !DailyNavigationPolicy.IsGameUri(_webView?.CoreWebView2?.Source ?? ""));

    internal string? HostGuardReason => _gameData?.Busy == true ? "game_data_busy" : LoginActive ? "login_required" : HostGuard.Renderable(Snapshot());

    private string WindowStatusText() => HostGuardReason switch
    {
        null when (_keeper?.HideMode ?? BackgroundKeeper.None) != BackgroundKeeper.None => $"后台隐藏（{_keeper!.HideMode}），仍在渲染",
        null => "可见",
        var reason => "不可用：" + reason,
    };

    private const int WmSysCommand = 0x0112, ScMinimize = 0xF020, WmNcHitTest = 0x0084, HtClient = 1, HtCaption = 2;
    private const int WsMinimizeBox = 0x00020000, WsMaximizeBox = 0x00010000, WsSysMenu = 0x00080000, CsDropShadow = 0x00020000;
    // the system only runs the sizing loop for HTLEFT ... HTBOTTOMRIGHT on windows with WS_THICKFRAME; WM_NCCALCSIZE
    // then removes the frame again, so the client area is still the whole window.
    private const int WsThickFrame = 0x00040000, WmNcCalcSize = 0x0083;
    private const int ScMaximize = 0xF030, ScRestore = 0xF120, WmGetMinMaxInfo = 0x0024, WmSizing = 0x0214;
    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect { public int Left, Top, Right, Bottom; }
    private const int HtLeft = 10, HtRight = 11, HtTop = 12, HtTopLeft = 13, HtTopRight = 14, HtBottom = 15, HtBottomLeft = 16, HtBottomRight = 17;

    [StructLayout(LayoutKind.Sequential)]
    private struct NativePoint { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)]
    private struct MinMaxInfo { public NativePoint Reserved, MaxSize, MaxPosition, MinTrackSize, MaxTrackSize; }

    /// <summary>
    /// frameless window: keep the system menu, minimize and maximize boxes (taskbar click, Win+Down/Up,
    /// Alt+Space, caption double-click) and a drop shadow, without any caption or system frame. Resizing uses the form's
    /// own hit-test edges (see WndProc); the game view itself always stays 1280x720.
    /// </summary>
    protected override CreateParams CreateParams
    {
        get
        {
            var parameters = base.CreateParams;
            if (Frameless)
            {
                parameters.Style |= WsMinimizeBox | WsMaximizeBox | WsSysMenu | WsThickFrame;
                parameters.ClassStyle |= CsDropShadow;
            }
            return parameters;
        }
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        if (_uiDock is not null) { ApplyFrameAttributes(_uiDock.Palette); }
        ApplyStopHotkey();
    }

    protected override void OnHandleDestroyed(EventArgs e)
    {
        if (_stopHotkeyRegistered) { UnregisterHotKey(Handle, StopHotkeyId); _stopHotkeyRegistered = false; }
        base.OnHandleDestroyed(e);
    }

    /// <summary>Registers / releases the global stop hotkey to match the saved setting (docked UI host only).</summary>
    private void ApplyStopHotkey()
    {
        if (_uiDock is null || !IsHandleCreated) { return; }
        if (_stopHotkeyRegistered) { UnregisterHotKey(Handle, StopHotkeyId); _stopHotkeyRegistered = false; }
        if (_prefs.StopHotkey)
        {
            _stopHotkeyRegistered = RegisterHotKey(Handle, StopHotkeyId, ModControl | ModAlt | ModShift | ModNoRepeat, (uint)Keys.S);
        }
    }

    /// <summary>Same as the panel's 紧急停止: stops the active task or scan. Never starts or resumes anything.</summary>
    private void OnStopHotkey()
    {
        var accepted = _uiDock?.Router.RequestStop("emergency") ?? false;
        _keeper?.ShowNotification("MaaShinyColors · 紧急停止",
            accepted ? "已发送停止请求。" : "当前没有运行中的任务。", accepted ? ToolTipIcon.Warning : ToolTipIcon.Info);
    }

    /// <summary>Windows 11 rounded corners and a hairline border in the panel's border token; ignored where unsupported.</summary>
    private void ApplyFrameAttributes(Ui.UiShellForm.ShellPalette palette)
    {
        if (!Frameless || !IsHandleCreated) { return; }
        try
        {
            int round = 2;   // DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND
            DwmSetWindowAttribute(Handle, 33, ref round, sizeof(int));
            int border = palette.Border.R | (palette.Border.G << 8) | (palette.Border.B << 16);   // DWMWA_BORDER_COLOR = 34
            DwmSetWindowAttribute(Handle, 34, ref border, sizeof(int));
        }
        catch (Exception exception) when (exception is DllNotFoundException or EntryPointNotFoundException) { }
    }

    /// <summary>Client point that drags the frameless window: the host-painted header strip left of the panel.</summary>
    internal bool IsCaptionPoint(Point client) => Frameless && _chromeHeader is { } header && header.Bounds.Contains(client);

    /// <summary>
    /// Resize edge for a client point (HTLEFT ... HTBOTTOMRIGHT), or null. Only within <see cref="ResizeBorder"/> of the
    /// window edge and never while maximized. Reached where the form itself (the right / bottom border strips, the area
    /// below the footer) or a pass-through host control (header, footer, window buttons' outer edge) is under the cursor;
    /// the game viewport and the panel WebView answer their own hit tests, so they never become resize handles.
    /// </summary>
    internal int? ResizeHit(Point client)
    {
        var border = ResizeBorder;
        if (border <= 0) { return null; }
        var size = ClientSize;
        bool left = client.X < border, right = client.X >= size.Width - border;
        bool top = client.Y < border, bottom = client.Y >= size.Height - border;
        if (!(left || right || top || bottom)) { return null; }
        // Corners are a little longer than the border along each edge (easier to grab).
        var corner = Math.Max(border, Px(12));
        if (top || bottom) { left |= client.X < corner; right |= client.X >= size.Width - corner; }
        if (left || right) { top |= client.Y < corner; bottom |= client.Y >= size.Height - corner; }
        return (top, bottom, left, right) switch
        {
            (true, _, true, _) => HtTopLeft,
            (true, _, _, true) => HtTopRight,
            (_, true, true, _) => HtBottomLeft,
            (_, true, _, true) => HtBottomRight,
            (true, _, _, _) => HtTop,
            (_, true, _, _) => HtBottom,
            (_, _, true, _) => HtLeft,
            (_, _, _, true) => HtRight,
            _ => null,
        };
    }

    protected override void WndProc(ref Message message)
    {
        if (message.Msg == WmHotkey && message.WParam == (IntPtr)StopHotkeyId)
        {
            OnStopHotkey();
            message.Result = IntPtr.Zero;
            return;
        }
        if ((uint)message.Msg == ShowExistingMessage)
        {
            if (_keeper is not null) { _keeper.Show(activate: true); }
            else
            {
                if (WindowState == FormWindowState.Minimized) { WindowState = FormWindowState.Normal; }
                Show();
                Activate();
            }
            message.Result = IntPtr.Zero;
            return;
        }
        if (_keeper?.TryInterceptMinimize(ref message) == true) { return; }
        // frameless + WS_THICKFRAME: no non-client area (client rect == window rect, maximized included; the
        // maximized position and size come from WM_GETMINMAXINFO = the monitor work area).
        // wParam FALSE (lParam = RECT) included: leaving the window rect unchanged keeps client == window there too.
        if (message.Msg == WmNcCalcSize && Frameless) { message.Result = IntPtr.Zero; return; }
        if (Frameless && SuppressClassicFrame(ref message)) { return; }
        // a height drag widens the window when the panel would get too narrow (WMSZ_* edge in wParam, RECT in lParam).
        if (message.Msg == WmSizing && Frameless && _uiDock is not null && _displayScaling && IsHandleCreated)
        {
            var rect = Marshal.PtrToStructure<NativeRect>(message.LParam);
            var work = Screen.FromHandle(Handle).WorkingArea;
            var dragged = Rectangle.FromLTRB(rect.Left, rect.Top, rect.Right, rect.Bottom);
            var sized = PanelDetached   // the game-only window keeps the game column's shape
                ? GameDisplay.DragSizingDetached(dragged, (int)message.WParam, MinimumClient, ChromeScale)
                : GameDisplay.DragSizing(dragged, (int)message.WParam, MinPanelPx, work.Width, MinimumClient, ChromeScale);
            rect = new NativeRect { Left = sized.Left, Top = sized.Top, Right = sized.Right, Bottom = sized.Bottom };
            Marshal.StructureToPtr(rect, message.LParam, false);
            message.Result = (IntPtr)1;
            return;
        }
        base.WndProc(ref message);
        // Frameless: maximize to the monitor's work area (not over the taskbar) and never below the minimum size.
        if (message.Msg == WmGetMinMaxInfo && Frameless && IsHandleCreated)
        {
            var info = Marshal.PtrToStructure<MinMaxInfo>(message.LParam);
            var screen = Screen.FromHandle(Handle);
            var work = screen.WorkingArea; var monitor = screen.Bounds;
            info.MaxPosition = new NativePoint { X = work.Left - monitor.Left, Y = work.Top - monitor.Top };
            info.MaxSize = new NativePoint { X = work.Width, Y = work.Height };
            var minimum = MinimumClient;
            info.MinTrackSize = new NativePoint { X = minimum.Width, Y = minimum.Height };
            Marshal.StructureToPtr(info, message.LParam, false);
            message.Result = IntPtr.Zero;
            return;
        }
        // The header / footer controls answer HTTRANSPARENT over empty areas, so the hit test reaches the form here.
        if (message.Msg == WmNcHitTest && Frameless && (int)message.Result == HtClient)
        {
            var screen = new Point(unchecked((short)(long)message.LParam), unchecked((short)((long)message.LParam >> 16)));
            var client = PointToClient(screen);
            if (ResizeHit(client) is int edge) { message.Result = (IntPtr)edge; }
            else if (IsCaptionPoint(client)) { message.Result = (IntPtr)HtCaption; }
        }
    }

    /// <summary>
    /// 2026-10-11 (host-20261011d): after a system minimize / restore (taskbar click, SC_RESTORE) DefWindowProc paints the
    /// classic caption (title, min / max / close) and frame straight into the window DC over the host header whenever it
    /// handles WM_NCACTIVATE, WM_NCPAINT, WM_SETTEXT / WM_SETICON or the themed caption messages: the window has no
    /// non-client area (WM_NCCALCSIZE), so DWM draws no frame and the system falls back to painting one itself. The
    /// activation state still changes; only that painting is skipped. Returns true when the message was handled.
    /// </summary>
    private bool SuppressClassicFrame(ref Message message)
    {
        switch (message.Msg)
        {
            case WmNcActivate:
                // lParam -1: DefWindowProc updates the activation state without repainting the non-client area.
                message.LParam = (IntPtr)(-1);
                base.WndProc(ref message);
                return true;
            case WmNcPaint:
            case WmNcUahDrawCaption:
            case WmNcUahDrawFrame:
                message.Result = IntPtr.Zero;
                return true;
            case WmSetText:
            case WmSetIcon:
                if (!IsHandleCreated || !Visible) { return false; }
                // Hide the window's visible bit for the default handler only (no repaint, no show/hide event), so the text
                // or icon is stored without the caption being drawn.
                var style = GetWindowLongPtr(message.HWnd, GwlStyle);
                SetWindowLongPtr(message.HWnd, GwlStyle, (IntPtr)((long)style & ~WsVisible));
                try { base.WndProc(ref message); }
                finally { SetWindowLongPtr(message.HWnd, GwlStyle, style); }
                return true;
            default:
                return false;
        }
    }

    private const int WmNcActivate = 0x0086, WmNcPaint = 0x0085, WmSetText = 0x000C, WmSetIcon = 0x0080;
    private const int WmNcUahDrawCaption = 0x00AE, WmNcUahDrawFrame = 0x00AF, GwlStyle = -16;
    private const long WsVisible = 0x10000000;
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")]
    private static extern IntPtr GetWindowLongPtr(IntPtr window, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")]
    private static extern IntPtr SetWindowLongPtr(IntPtr window, int index, IntPtr value);

    internal Ui.UiWindowButtons? WindowButtons => _windowButtons;

    private void OnSessionSwitch(object? sender, Microsoft.Win32.SessionSwitchEventArgs args)
    {
        if (Environment.CurrentManagedThreadId != _uiThreadId)
        {
            if (IsDisposed || !IsHandleCreated) { return; }
            try { BeginInvoke(() => OnSessionSwitch(sender, args)); }
            catch (InvalidOperationException) when (IsDisposed || !IsHandleCreated) { }
            return;
        }
        // A locked session counts as occluded for Chromium; refuse until unlock, then require a fresh frame.
        if (args.Reason is Microsoft.Win32.SessionSwitchReason.SessionLock or Microsoft.Win32.SessionSwitchReason.ConsoleDisconnect
            or Microsoft.Win32.SessionSwitchReason.RemoteDisconnect)
        { _desktopLocked = true; InvalidateFrame("desktop_locked"); }
        else if (args.Reason is Microsoft.Win32.SessionSwitchReason.SessionUnlock or Microsoft.Win32.SessionSwitchReason.ConsoleConnect
            or Microsoft.Win32.SessionSwitchReason.RemoteConnect)
        { _desktopLocked = false; InvalidateFrame("desktop_unlocked"); }
    }

    internal sealed record Heartbeat(long Raf, long Timer, long Clicks, bool Hidden, string Visibility, long VisibilityChanges, long Messages);

    private long _heartbeatMessages;
    private void OnHeartbeat(object? sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        // Only the self-owned heartbeat page registers this handler.
        if (!IsHeartbeatPage || args.Source != DemoUri) { return; }
        try
        {
            using var json = System.Text.Json.JsonDocument.Parse(args.WebMessageAsJson);
            var root = json.RootElement;
            LatestHeartbeat = new Heartbeat(root.GetProperty("raf").GetInt64(), root.GetProperty("timer").GetInt64(),
                root.GetProperty("clicks").GetInt64(), root.GetProperty("hidden").GetBoolean(),
                root.GetProperty("vis").GetString() ?? "", root.GetProperty("visChanges").GetInt64(), ++_heartbeatMessages);
            _heartbeatSeen.TrySetResult();
        }
        catch (Exception) { }
    }

    /// <summary>Self-test probe: raw CapturePreviewAsync without the guard, heartbeat page only.</summary>
    internal async Task<(bool ok, string? sha, string? error, string? size)> ProbeCaptureAsync(TimeSpan timeout)
    {
        if (!IsHeartbeatPage) { throw new InvalidOperationException("仅心跳自测页可绕过守卫取帧。"); }
        try
        {
            using var stream = new MemoryStream();
            var capture = _webView.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream);
            if (await Task.WhenAny(capture, Task.Delay(timeout)) != capture) { return (false, null, "timeout", null); }
            await capture;
            var sha = Convert.ToHexString(SHA256.HashData(stream.ToArray())).ToLowerInvariant();
            _frameChanges.Observe(sha);
            stream.Position = 0;
            using var image = Image.FromStream(stream);
            return (true, sha, null, $"{image.Width}x{image.Height}");
        }
        catch (Exception exception) { return (false, null, exception.GetType().Name + ": " + exception.Message, null); }
    }

    /// <summary>
    /// --self-test-ui-dock only: one capture through the real guarded path (CaptureImage) on the self-owned demo page,
    /// to prove the display scale never changes the captured 1280x720 content. Refused on the game host.
    /// </summary>
    internal Task<byte[]> SelfTestCaptureAsync()
    {
        if (!_options.CompositionDemo || _options.CompositionGame) { throw new InvalidOperationException("仅自有测试页可做自测截图。"); }
        return CaptureImage("self_test_display_scale");
    }

    internal double? FrameChangeAgeMs => _frameChanges.ChangeAgeMs;
    internal uint BrowserProcessId => _webView?.CoreWebView2?.BrowserProcessId ?? 0;

    /// <summary>Self-test click into the heartbeat page via SendMouseInput; the system cursor is untouched.</summary>
    internal Task ProbeClickAsync(Point point)
    {
        if (!IsHeartbeatPage) { throw new InvalidOperationException("仅心跳自测页可发送测试点击。"); }
        return SendNativeGesture(point);
    }

    private async Task<bool> ClickDemo()
    {
        if (!_options.EnableLocalInput || _used || HostGuardReason is not null ||
            _webView.CoreWebView2.Source != "https://sc-local.test/index.html") { return false; }
        // Independent fresh-pixel guard at the host boundary, never DOM inspection.
        using var stream = new MemoryStream(await CaptureImage("local_demo_guard"));
        using var image = new Bitmap(stream);
        var ready = 0; var complete = 0;
        for (var y = 380; y < 540; y++) for (var x = 430; x < 850; x++)
        { var color = image.GetPixel(x, y); if (color.R >= 10 && color.R <= 35 && color.G >= 145 && color.G <= 180 && color.B >= 55 && color.B <= 95) { ready++; } }
        for (var y = 250; y < 380; y++) for (var x = 320; x < 960; x++)
        { var color = image.GetPixel(x, y); if (color.R >= 25 && color.R <= 70 && color.G >= 85 && color.G <= 115 && color.B >= 215 && color.B <= 255) { complete++; } }
        if (ready < 30000 || complete >= 45000) { return false; }
        _used = true;
        var point = new Point((int)Math.Round(640.0 * _sourceWidth!.Value / 1280), (int)Math.Round(460.0 * _sourceHeight!.Value / 720));
        await SendNativeGesture(point);
        return true;
    }
}

