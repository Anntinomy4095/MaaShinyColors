using System.Drawing;
using System.Text.Json.Nodes;
using Microsoft.Win32;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>Paths for the offline UI preview. Only --ui-devtools may follow --ui-preview; nothing else is accepted.</summary>
internal sealed record UiShellOptions(string UiFolder, string UserDataFolder, string ProfilePath, bool DevTools)
{
    internal const string PreviewSwitch = "--ui-preview";
    internal const string DevToolsSwitch = "--ui-devtools";

    internal static UiShellOptions ForPreview(IReadOnlyList<string> rest)
    {
        var devTools = false;
        foreach (var argument in rest)
        {
            if (argument == DevToolsSwitch && !devTools) { devTools = true; continue; }
            // In particular --composition-game / --enable-pipeline-input can never be combined with the UI preview.
            throw new ArgumentException("界面预览只接受 " + DevToolsSwitch + "，不能与其他参数组合：" + argument);
        }
        var root = UiPaths.RepoRoot();
        var data = Path.Combine(root, "dev_assets", "private");
        return new UiShellOptions(UiPaths.OutputUiFolder(), Path.Combine(data, "ui-webview2-profile"),
            Path.Combine(data, "ui", "profile.json"), devTools);
    }
}

internal static class UiPaths
{
    /// <summary>The ui folder copied next to the executable (csproj Content).</summary>
    internal static string OutputUiFolder() => Path.Combine(AppContext.BaseDirectory, "ui");

    /// <summary>Repository root: the working directory or an ancestor of the executable holding project_policy.json and assets/interface.json.</summary>
    internal static string RepoRoot()
    {
        foreach (var start in new[] { Directory.GetCurrentDirectory(), AppContext.BaseDirectory })
        {
            for (var directory = new DirectoryInfo(start); directory is not null; directory = directory.Parent)
            {
                if (File.Exists(Path.Combine(directory.FullName, "project_policy.json")) && File.Exists(Path.Combine(directory.FullName, "assets", "interface.json")))
                { return directory.FullName; }
            }
        }
        throw new DirectoryNotFoundException("找不到项目根目录（需要 project_policy.json 与 assets/interface.json）。");
    }
}

/// <summary>
/// U1 offline UI preview window: a header strip and a neutral 1280x720 placeholder where the game will sit (no game
/// WebView is created and no remote URL is loaded), plus the isolated UI panel WebView on the right. No live input and
/// no capture bridge; the engine supervisor can only run an offline replay of private frames (U2).
/// 2026-10-11: sizable and maximizable like the game host. The placeholder column keeps the game's fixed 1280x720 (the
/// game is never scaled); the panel takes the remaining width. The 面板宽度
/// setting is gone; the window opens at 1280 + 400 px.
/// </summary>
internal sealed class UiShellForm : Form
{
    internal const int GameWidth = 1280;
    internal const int GameHeight = 720;
    internal const int HeaderHeight = 44;
    internal const int StatusHeight = 24;
    internal const int DefaultPanelWidth = 400;

    private readonly UiShellOptions _options;
    private readonly ShellCanvas _canvas;
    private readonly UiPanelHost _panel;
    private readonly int _panelWidth = DefaultPanelWidth;

    internal UiShellForm(UiShellOptions options)
    {
        _options = options;
        Text = "MaaShinyColors 界面预览";
        AutoScaleMode = AutoScaleMode.None;
        FormBorderStyle = FormBorderStyle.Sizable;
        MaximizeBox = true;
        StartPosition = FormStartPosition.CenterScreen;
        Protocol = UiProtocol.LoadEmbedded();
        Schema = UiTaskSchema.LoadFromUiFolder(options.UiFolder);
        Profiles = new UiProfileStore(options.ProfilePath, Schema);
        // U2: offline replay of private frames only (no game host here, so read-only runs are refused).
        var data = Path.Combine(UiPaths.RepoRoot(), "dev_assets", "private");
        Engine = new UiEngineSupervisor(UiPaths.RepoRoot());
        Router = new UiMessageRouter(Protocol, Schema, Profiles, engine: Engine, replayRoot: Path.Combine(data, "ui", "replay"), policy: () => UiPolicySummary.Load(UiPaths.RepoRoot()),
            teamPropose: new UiTeamPropose(UiPaths.RepoRoot(), () => Profiles.Path));
        var profile = Profiles.Load();
        var palette = ShellPalette.For(ThemeOf(profile));
        _canvas = new ShellCanvas(palette) { Dock = DockStyle.Left, Width = GameWidth };
        _panel = new UiPanelHost(options.UiFolder, options.UserDataFolder, options.DevTools, palette.Background);
        var panelHolder = new Panel { Dock = DockStyle.Fill, BackColor = palette.Background };
        panelHolder.Controls.Add(_panel.View);
        Controls.Add(panelHolder);
        Controls.Add(_canvas);
        BackColor = palette.Background;
        ApplyLayout(resizeWindow: true);
        Router.ProfileSaved += saved =>
        {
            _canvas.Palette = ShellPalette.For(ThemeOf(saved));
            BackColor = panelHolder.BackColor = _canvas.Palette.Background;
            ApplyLayout(resizeWindow: false);
        };
        DpiChanged += (_, _) => ApplyLayout(resizeWindow: WindowState == FormWindowState.Normal);
        Shown += async (_, _) =>
        {
            try { await _panel.InitializeAsync(Router); }
            catch (Exception exception)
            {
                InitializationError = exception;
                _canvas.Notice = "界面面板无法启动：" + exception.Message;
            }
            finally { PanelInitialized.TrySetResult(); }
        };
        FormClosed += (_, _) => { Engine.Dispose(); _panel.Dispose(); };
    }

    internal UiProtocol Protocol { get; }
    internal UiTaskSchema Schema { get; }
    internal UiProfileStore Profiles { get; }
    internal UiMessageRouter Router { get; }
    internal UiEngineSupervisor Engine { get; }
    internal UiPanelHost Panel => _panel;
    internal Exception? InitializationError { get; private set; }
    internal TaskCompletionSource PanelInitialized { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    internal int PanelPixelWidth => Px(_panelWidth);
    /// <summary>Client height of the game column: header + 1280x720 placeholder + status bar.</summary>
    internal int ColumnPixelHeight => Px(HeaderHeight) + GameHeight + Px(StatusHeight);
    /// <summary>Smallest client size: the fixed 1280 column plus the narrowest panel (360).</summary>
    internal Size MinimumClient => new(GameWidth + Px(360), ColumnPixelHeight);
    internal Control Canvas => _canvas;

    /// <summary>Enforces the minimum client size on programmatic and user resizes (MinimumSize alone was not applied).</summary>
    protected override void SetClientSizeCore(int x, int y)
    {
        if (_canvas is not null) { x = Math.Max(x, MinimumClient.Width); y = Math.Max(y, MinimumClient.Height); }
        base.SetClientSizeCore(x, y);
    }

    protected override void SetBoundsCore(int x, int y, int width, int height, BoundsSpecified specified)
    {
        if (_canvas is not null && IsHandleCreated)
        {
            var minimum = SizeFromClientSize(MinimumClient);
            if ((specified & BoundsSpecified.Width) != 0) { width = Math.Max(width, minimum.Width); }
            if ((specified & BoundsSpecified.Height) != 0) { height = Math.Max(height, minimum.Height); }
        }
        base.SetBoundsCore(x, y, width, height, specified);
    }

    private int Px(int value) => (int)Math.Round(value * DeviceDpi / 96.0);

    private void ApplyLayout(bool resizeWindow)
    {
        // The game placeholder keeps the game's physical 1280x720; header, status bar and panel follow display scaling.
        _canvas.DpiScale = DeviceDpi / 96f;
        _canvas.Width = GameWidth;
        MinimumSize = SizeFromClientSize(MinimumClient);
        if (resizeWindow) { ClientSize = new Size(GameWidth + Px(_panelWidth), ColumnPixelHeight); }
        _canvas.Invalidate();
    }


    internal static bool ThemeOf(JsonObject profile)
    {
        var theme = (string?)profile["prefs"]?["theme"];
        if (theme == "dark") { return true; }
        if (theme == "light") { return false; }
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
            return key?.GetValue("AppsUseLightTheme") is int light && light == 0;
        }
        catch (System.Security.SecurityException) { return false; }
    }

    internal sealed record ShellPalette(Color Background, Color Canvas, Color Border, Color Text, Color Text2, Color Warning,
        Color Surface, Color UmbrellaFill, Color UmbrellaLine, Color UmbrellaRib, Color UmbrellaHandle, Color UmbrellaShine)
    {
        private static Color C(string hex) => ColorTranslator.FromHtml(hex);

        // Same values as ui/styles/tokens.css (--sc-bg, --sc-canvas, --sc-border, --sc-text, --sc-text-2, --sc-warning,
        // --sc-surface, --sc-umbrella-fill/-line/-rib/-handle/-shine); tests/test_ui_host_guards.py keeps them in sync.
        internal static ShellPalette For(bool dark) => dark
            ? new(C("#17161B"), C("#1C1B20"), C("#34323A"), C("#ECEAE6"), C("#A7A3AD"), C("#D1A158"),
                C("#201F25"), C("#F4F6F8"), C("#C9CED4"), C("#9AA1A9"), C("#E6E8EB"), C("#FFFFFF"))
            : new(C("#FAF8F4"), C("#F3F1EC"), C("#E6E2DA"), C("#24232B"), C("#6E6B76"), C("#B07A2A"),
                C("#FFFFFF"), C("#FFFFFF"), C("#7D838B"), C("#A3A9B1"), C("#5B6470"), C("#D5DDE4"));
    }

    /// <summary>Owner-drawn header, placeholder frame and status bar for the left column.</summary>
    private sealed class ShellCanvas : Control
    {
        private ShellPalette _palette;
        private string? _notice;

        internal ShellCanvas(ShellPalette palette)
        {
            _palette = palette;
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint | ControlStyles.ResizeRedraw, true);
        }

        [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
        internal float DpiScale { get; set; } = 1f;
        [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
        internal ShellPalette Palette { get => _palette; set { _palette = value; Invalidate(); } }
        [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
        internal string? Notice { get => _notice; set { _notice = value; Invalidate(); } }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
            int S(float value) => (int)Math.Round(value * DpiScale);
            var header = S(HeaderHeight);
            var status = S(StatusHeight);
            var game = new Rectangle(0, header, Width, GameHeight);
            using var background = new SolidBrush(_palette.Background);
            using var canvas = new SolidBrush(_palette.Canvas);
            using var border = new Pen(_palette.Border, 1);
            // Extra window height below the status bar stays plain background (the placeholder never stretches).
            g.FillRectangle(background, ClientRectangle);
            g.FillRectangle(canvas, game);
            // One shared header: the panel page continues the same background and hairline on the right, and its
            // side panel draws the vertical divider below the header, so no full-height seam is painted here.
            g.DrawLine(border, 0, header - 1, Width, header - 1);
            g.DrawLine(border, 0, header + GameHeight, Width, header + GameHeight);

            using var body = new Font("Microsoft YaHei UI", 12f * DpiScale, FontStyle.Regular, GraphicsUnit.Pixel);
            const TextFormatFlags Left = TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine | TextFormatFlags.NoPrefix;
            UiChrome.DrawBrand(g, new Rectangle(0, 0, Width, header), DpiScale, _palette, "界面预览", null);

            // Placeholder frame, same proportions as the browser preview (.game-area / .game-frame); only the size label
            // remains (2026-10-11: no 「游戏画面」 caption).
            var frame = Rectangle.Inflate(game, -S(16), -S(16));
            g.DrawRectangle(border, frame.X, frame.Y, frame.Width - 1, frame.Height - 1);
            var caption = new Rectangle(frame.X + S(16), frame.Bottom - S(16) - S(20), frame.Width - S(32), S(20));
            TextRenderer.DrawText(g, "1280 × 720", body, caption, _palette.Text2, Left | TextFormatFlags.NoPadding);

            var statusRect = new Rectangle(S(16), header + GameHeight + 1, Width - S(32), status - 1);
            TextRenderer.DrawText(g, _notice ?? "预览模式：未加载游戏，仅离线回放", body, statusRect, _notice is null ? _palette.Text2 : _palette.Warning, Left | TextFormatFlags.EndEllipsis);
        }
    }
}

/// <summary>
/// Shared host chrome painting: the app logo and wordmark on the left of the one
/// full-width header, in the same tokens as the panel page. The logo is ui/icon.png, the picture the panel header shows;
/// it never reads a game frame.
/// </summary>
internal static class UiChrome
{
    internal const string BrandName = "MaaShinyColors";
    private static readonly object FontGate = new();
    private static System.Drawing.Text.PrivateFontCollection? _privateFonts;
    private static FontFamily? _heading;

    /// <summary>Heading family like --font-heading: M PLUS Rounded 1c (bundled ui/fonts or installed), else Microsoft YaHei UI.</summary>
    internal static FontFamily HeadingFamily()
    {
        lock (FontGate)
        {
            if (_heading is not null) { return _heading; }
            try
            {
                var file = Path.Combine(UiPaths.OutputUiFolder(), "fonts", "MPLUSRounded1c-Bold.ttf");
                if (File.Exists(file))
                {
                    _privateFonts = new System.Drawing.Text.PrivateFontCollection();
                    _privateFonts.AddFontFile(file);
                    _heading = _privateFonts.Families.FirstOrDefault();
                }
            }
            catch (Exception exception) when (exception is IOException or ArgumentException or UnauthorizedAccessException) { _heading = null; }
            _heading ??= FontFamily.Families.FirstOrDefault(f => f.Name == "M PLUS Rounded 1c")
                ?? FontFamily.Families.FirstOrDefault(f => f.Name == "Microsoft YaHei UI")
                ?? FontFamily.GenericSansSerif;
            return _heading;
        }
    }

    /// <summary>
    /// Draws logo + wordmark (+ a quiet sub label, or a warning notice instead) vertically centred in the header.
    /// Returns the x just after the last drawn text (where a small header tool such as 刷新 may follow).
    /// </summary>
    internal static float DrawBrand(Graphics g, Rectangle header, float scale, UiShellForm.ShellPalette palette, string? sub, string? notice,
        float reserve = 0)
    {
        float S(float value) => value * scale;
        var state = g.Save();
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAliasGridFit;
        var icon = S(24);
        var x = header.X + S(16);
        var midY = header.Y + header.Height / 2f;
        DrawLogo(g, new RectangleF(x, midY - icon / 2f, icon, icon));
        x += icon + S(8);
        using var name = new Font(HeadingFamily(), S(16), FontStyle.Bold, GraphicsUnit.Pixel);
        using var small = new Font("Microsoft YaHei UI", S(12), FontStyle.Regular, GraphicsUnit.Pixel);
        using var format = new StringFormat(StringFormat.GenericTypographic) { LineAlignment = StringAlignment.Center, FormatFlags = StringFormatFlags.NoWrap };
        using var text = new SolidBrush(palette.Text);
        var nameWidth = g.MeasureString(BrandName, name, PointF.Empty, format).Width;
        g.DrawString(BrandName, name, text, new RectangleF(x, header.Y, nameWidth + S(4), header.Height), format);
        x += nameWidth + S(12) + reserve;
        var end = x - S(12) + S(4);
        var label = notice ?? sub;
        if (!string.IsNullOrEmpty(label))
        {
            using var border = new Pen(palette.Border, Math.Max(1f, scale));
            g.DrawLine(border, x, midY - S(6), x, midY + S(6));
            x += S(12);
            using var brush = new SolidBrush(notice is null ? palette.Text2 : palette.Warning);
            format.Trimming = StringTrimming.EllipsisCharacter;
            var room = Math.Max(0, header.Right - x - S(16));
            g.DrawString(label, small, brush, new RectangleF(x, header.Y, room, header.Height), format);
            end = x + Math.Min(room, g.MeasureString(label, small, PointF.Empty, format).Width);
        }
        g.Restore(state);
        return end;
    }

    private static readonly Bitmap MeasureSurface = new(1, 1);

    /// <summary>
    /// X just after the wordmark as <see cref="DrawBrand"/> draws it in a header starting at x 0 (same font, format and
    /// text hint), so a header tool can sit beside it before the first paint (hit-testing).
    /// </summary>
    internal static float WordmarkEnd(float scale)
    {
        lock (MeasureSurface)
        {
            using var g = Graphics.FromImage(MeasureSurface);
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAliasGridFit;
            using var name = new Font(HeadingFamily(), 16 * scale, FontStyle.Bold, GraphicsUnit.Pixel);
            using var format = new StringFormat(StringFormat.GenericTypographic) { LineAlignment = StringAlignment.Center, FormatFlags = StringFormatFlags.NoWrap };
            return (16 + 24 + 8 + 4) * scale + g.MeasureString(BrandName, name, PointF.Empty, format).Width;
        }
    }

    private static readonly object LogoGate = new();
    private static Image? _logo;

    /// <summary>ui/icon.png (tools/make_app_icon.py), read once into memory so the file stays unlocked; the window icon if missing.</summary>
    private static Image Logo()
    {
        lock (LogoGate)
        {
            if (_logo is not null) { return _logo; }
            try
            {
                var file = Path.Combine(UiPaths.OutputUiFolder(), "icon.png");
                if (File.Exists(file))
                {
                    using var stream = new MemoryStream(File.ReadAllBytes(file));
                    using var loaded = Image.FromStream(stream);
                    _logo = new Bitmap(loaded);
                }
            }
            catch (Exception exception) when (exception is IOException or ArgumentException or UnauthorizedAccessException) { _logo = null; }
            return _logo ??= AppIcon.Value.ToBitmap();
        }
    }

    internal static void DrawLogo(Graphics g, RectangleF box)
    {
        var state = g.Save();
        g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
        g.PixelOffsetMode = System.Drawing.Drawing2D.PixelOffsetMode.HighQuality;
        g.DrawImage(Logo(), box);
        g.Restore(state);
    }
}
