using System.Runtime.InteropServices;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// 设置 → 外观 → 面板位置「独立窗口」(2026-10-25, user: the game window and the control UI can be separated). The docked
/// panel holder (the UI WebView) moves into this ordinary resizable window; the game window then shows only the game
/// column. Display only: the game WebView, its 1280x720 canvas, captures and program input are unchanged, and the panel
/// never touches the game view. Closing this window docks the panel back (it never closes the app); the game window's
/// close still closes everything.
/// </summary>
internal sealed class UiPanelWindow : Form
{
    internal const int MinClientCss = 360;
    internal const int MinHeightCss = 480;

    internal UiPanelWindow(Icon? icon, Color background, Rectangle saved, Rectangle near, int dpi)
    {
        Text = "MaaShinyColors 控制面板";
        if (icon is not null) { Icon = icon; }
        AutoScaleMode = AutoScaleMode.None;
        FormBorderStyle = FormBorderStyle.Sizable;
        MaximizeBox = true;
        StartPosition = FormStartPosition.Manual;
        BackColor = background;
        var minimum = SizeFromClientSize(new Size(Px(MinClientCss, dpi), Px(MinHeightCss, dpi)));
        MinimumSize = minimum;
        Bounds = Place(saved, near, minimum);
    }

    /// <summary>Like the game window: showing it never takes focus from the window the user is in.</summary>
    protected override bool ShowWithoutActivation => true;

    /// <summary>Set by the game window before it closes this one (app exit or docking back).</summary>
    [System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
    internal bool AllowClose { get; set; }

    /// <summary>The user closed this window: the game window docks the panel back.</summary>
    internal event Action? DockRequested;

    /// <summary>Restored bounds to save (screen px).</summary>
    internal Rectangle SavedBounds => WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (!AllowClose && e.CloseReason == CloseReason.UserClosing)
        {
            e.Cancel = true;
            BeginInvoke(() => DockRequested?.Invoke());   // after the close message unwinds
            return;
        }
        base.OnFormClosing(e);
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        ApplyPalette(BackColor);
    }

    /// <summary>Background and a dark title bar for a dark palette (DWMWA_USE_IMMERSIVE_DARK_MODE = 20; best effort).</summary>
    internal void ApplyPalette(Color background)
    {
        BackColor = background;
        if (!IsHandleCreated) { return; }
        try
        {
            int dark = background.GetBrightness() < 0.5f ? 1 : 0;
            DwmSetWindowAttribute(Handle, 20, ref dark, sizeof(int));
        }
        catch (Exception exception) when (exception is DllNotFoundException or EntryPointNotFoundException) { }
    }

    /// <summary>
    /// Saved bounds when at least 120x120 px of them lie on a screen's work area; otherwise <paramref name="near"/> (where
    /// the docked panel was) kept on its screen; never below <paramref name="minimum"/>.
    /// </summary>
    internal static Rectangle Place(Rectangle saved, Rectangle near, Size minimum)
    {
        static bool Visible(Rectangle r) => Screen.AllScreens.Any(s => Rectangle.Intersect(s.WorkingArea, r) is { Width: >= 120, Height: >= 120 });
        if (!saved.IsEmpty && Visible(saved))
        {
            return saved with { Width = Math.Max(saved.Width, minimum.Width), Height = Math.Max(saved.Height, minimum.Height) };
        }
        var work = Screen.FromRectangle(near.IsEmpty ? new Rectangle(0, 0, 1, 1) : near).WorkingArea;
        var size = new Size(Math.Min(Math.Max(near.Width, minimum.Width), work.Width), Math.Min(Math.Max(near.Height, minimum.Height), work.Height));
        var x = Math.Clamp(near.IsEmpty ? work.Left + (work.Width - size.Width) / 2 : near.Left, work.Left, work.Right - size.Width);
        var y = Math.Clamp(near.IsEmpty ? work.Top + (work.Height - size.Height) / 2 : near.Top, work.Top, work.Bottom - size.Height);
        return new Rectangle(x, y, size.Width, size.Height);
    }

    private static int Px(int css, int dpi) => (int)Math.Round(css * dpi / 96.0);

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);
}
