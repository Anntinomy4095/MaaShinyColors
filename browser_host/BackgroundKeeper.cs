using System.Drawing;
using System.Runtime.InteropServices;

namespace MaaShinyColors.BrowserHost;

/// <summary>
/// Hide-to-tray for --background-keepalive. The window is never really minimized: it is DWM
/// cloaked (or moved off-screen as a fallback) so Win32 stays WS_VISIBLE and the 1280x720
/// viewport and WebView2 IsVisible never change. Never activates the window unless the user
/// asked through the tray, and never touches the system cursor.
/// </summary>
internal sealed class BackgroundKeeper : IDisposable
{
    public const string None = "none";
    public const string Cloaked = "cloaked";
    public const string Offscreen = "offscreen";
    /// <summary>Fallback order, chosen from the measured --self-test-background results (see README).</summary>
    public static readonly string[] DefaultOrder = { Cloaked, Offscreen };

    private const int DwmwaCloak = 13;
    private const int DwmwaCloaked = 14;
    private const int WmSysCommand = 0x0112;
    private const int ScMinimize = 0xF020;
    private const int ScRestore = 0xF120;
    private const int SwShowNoActivate = 4;

    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);
    [DllImport("dwmapi.dll")] private static extern int DwmGetWindowAttribute(IntPtr window, int attribute, out int value, int size);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] private static extern IntPtr GetWindowLongPtr(IntPtr window, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] private static extern IntPtr SetWindowLongPtr(IntPtr window, int index, IntPtr value);
    private const int GwlExStyle = -20;
    private const long WsExNoActivate = 0x08000000;

    [ComImport, Guid("56FDF342-FD6D-11d0-958A-006097C9A090"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface ITaskbarList
    {
        void HrInit();
        void AddTab(IntPtr window);
        void DeleteTab(IntPtr window);
        void ActivateTab(IntPtr window);
        void SetActiveAlt(IntPtr window);
    }
    [ComImport, Guid("56FDF344-FD6D-11d0-958A-006097C9A090")]
    private class TaskbarList { }

    private readonly Form _form;
    private readonly NotifyIcon? _tray;
    private Point _savedLocation;
    private ITaskbarList? _taskbar;

    public BackgroundKeeper(Form form, bool tray)
    {
        _form = form;
        if (!tray) { return; }
        var menu = new ContextMenuStrip();
        menu.Items.Add("显示", null, (_, _) => Show(activate: true));
        menu.Items.Add("隐藏", null, (_, _) => { if (Enabled) Hide(notify: false); });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("退出", null, (_, _) =>
        {
            if (MessageBox.Show("退出 MaaShinyColors？正在进行的任务会停止。", "MaaShinyColors",
                MessageBoxButtons.OKCancel, MessageBoxIcon.Question) == DialogResult.OK) { _form.Close(); }
        });
        _tray = new NotifyIcon
        {
            Icon = AppIcon.Value, Text = "MaaShinyColors",
            ContextMenuStrip = menu, Visible = true,
        };
        // A left click brings the window back (or to the front); the context menu 隐藏 hides it again.
        _tray.MouseClick += (_, mouse) => { if (mouse.Button == MouseButtons.Left) { Show(activate: true); } };
        _tray.BalloonTipShown += (_, _) => LastNotificationState = "shown";
        _tray.BalloonTipClicked += (_, _) => { LastNotificationState = "clicked"; Show(activate: true); };
        _tray.BalloonTipClosed += (_, _) => { LastNotificationState = "closed"; if (!Enabled) { _tray.Visible = false; } };
    }

    /// <summary>A shell request is not proof of a visible notification; only BalloonTipShown records "shown".</summary>
    internal string LastNotificationState { get; private set; } = "none";
    internal string? LastNotificationError { get; private set; }

    /// <summary>Uses the existing tray, even when minimize interception is off. Windows controls notification sound.</summary>
    internal void ShowNotification(string title, string message, ToolTipIcon icon)
    {
        LastNotificationError = null;
        if (_tray is null || _form.IsDisposed) { LastNotificationState = "unavailable"; return; }
        try
        {
            _tray.Visible = true;
            LastNotificationState = "requested";
            _tray.ShowBalloonTip(10_000, title, message, icon);
        }
        catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            LastNotificationState = "failed";
            LastNotificationError = exception.GetType().Name;
            // Notification availability must not interrupt the task or its persisted result.
        }
    }

    /// <summary>"none" | "cloaked" | "offscreen".</summary>
    public string HideMode { get; private set; } = None;
    /// <summary>The host UI toggle: minimize becomes hide-to-tray while true.</summary>
    public bool InterceptMinimize { get; set; } = true;

    /// <summary>
    /// The 后台运行 setting: minimize interception plus the tray icon. Turning it off while hidden brings the window back
    /// (without activating it), so a disabled setting never leaves an invisible window behind.
    /// </summary>
    public bool Enabled
    {
        get => InterceptMinimize;
        set
        {
            InterceptMinimize = value;
            if (_tray is not null) { _tray.Visible = value; }
            if (!value && HideMode != None) { Show(activate: false); }
        }
    }
    public string? LastError { get; private set; }
    public event Action? Changed;

    /// <summary>Call first in WndProc; true means the message was consumed.</summary>
    public bool TryInterceptMinimize(ref Message message)
    {
        // A restore command while hidden (taskbar / Alt+Tab path) un-hides first and then lets the system handle it.
        if (message.Msg == WmSysCommand && ((long)message.WParam & 0xFFF0) == ScRestore && HideMode != None) { Show(activate: true); return false; }
        if (!InterceptMinimize || message.Msg != WmSysCommand || ((long)message.WParam & 0xFFF0) != ScMinimize) { return false; }
        Hide();
        message.Result = IntPtr.Zero;
        return true;
    }

    /// <summary>Fallback for minimizes that bypass WM_SYSCOMMAND (e.g. Win+D / Win+M).</summary>
    public void OnResize()
    {
        if (InterceptMinimize && IsIconic(_form.Handle)) { _form.BeginInvoke(() => { if (Enabled && IsIconic(_form.Handle)) Hide(); }); }
    }

    /// <summary>
    /// Hide using the first mode that works; returns the mode now in effect. A minimize or 「现在隐藏」 also shows a tray
    /// notification (otherwise the window just disappears); the tray menu's own 隐藏 and the self-test's
    /// forced modes do not.
    /// </summary>
    public string Hide(string? only = null, bool notify = true)
    {
        if (HideMode != None) { return HideMode; }
        var handle = _form.Handle;
        var iconic = IsIconic(handle);
        _savedLocation = iconic ? _form.RestoreBounds.Location : _form.Location;
        foreach (var mode in only is null ? DefaultOrder : new[] { only })
        {
            if (mode == Cloaked && TryCloak(handle, true))
            {
                // Cloak first, then un-minimize: the restore is never drawn.
                if (iconic) { ShowWindow(handle, SwShowNoActivate); }
                HideMode = Cloaked;
                break;
            }
            if (mode == Offscreen)
            {
                if (iconic) { ShowWindow(handle, SwShowNoActivate); }
                var screen = SystemInformation.VirtualScreen;
                _form.Location = new Point(screen.Right + 400, screen.Top);
                HideMode = Offscreen;
                break;
            }
        }
        if (HideMode != None) { Taskbar(false); NoActivate(handle, true); }
        if (HideMode != None && notify && only is null && _tray is not null)
        {
            ShowNotification("MaaShinyColors 已隐藏到托盘", "任务继续运行，点托盘图标或本通知还原。", ToolTipIcon.None);
        }
        Changed?.Invoke();
        return HideMode;
    }

    public void Show(bool activate)
    {
        var handle = _form.Handle;
        if (HideMode == Cloaked) { TryCloak(handle, false); }
        if (HideMode == Offscreen) { _form.Location = _savedLocation; }
        if (HideMode != None) { Taskbar(true); NoActivate(handle, false); }
        HideMode = None;
        if (IsIconic(handle)) { ShowWindow(handle, SwShowNoActivate); }
        // A user's tray click or repeated launcher invocation may bring the window forward.
        if (activate) { _form.Activate(); }
        Changed?.Invoke();
    }

    /// <summary>A hidden window must never become the foreground (and take the user's keyboard).</summary>
    private static void NoActivate(IntPtr handle, bool on)
    {
        var style = (long)GetWindowLongPtr(handle, GwlExStyle);
        SetWindowLongPtr(handle, GwlExStyle, (IntPtr)(on ? style | WsExNoActivate : style & ~WsExNoActivate));
    }

    private bool TryCloak(IntPtr handle, bool cloak)
    {
        try
        {
            var value = cloak ? 1 : 0;
            var hr = DwmSetWindowAttribute(handle, DwmwaCloak, ref value, sizeof(int));
            if (hr != 0) { LastError = $"DWMWA_CLOAK hr=0x{hr:X8}"; return false; }
            if (!cloak) { return true; }
            // Verify DWM reports the app cloak; otherwise fall back.
            hr = DwmGetWindowAttribute(handle, DwmwaCloaked, out var state, sizeof(int));
            if (hr != 0 || (state & 1) == 0) { LastError = $"DWMWA_CLOAKED hr=0x{hr:X8} state={state}"; TryUncloak(handle); return false; }
            return true;
        }
        catch (Exception exception) { LastError = exception.Message; return false; }
    }

    private static void TryUncloak(IntPtr handle)
    {
        var value = 0;
        _ = DwmSetWindowAttribute(handle, DwmwaCloak, ref value, sizeof(int));
    }

    private void Taskbar(bool show)
    {
        // ShowInTaskbar would recreate the HWND and break the composition target, so use ITaskbarList.
        try
        {
            if (_taskbar is null) { _taskbar = (ITaskbarList)new TaskbarList(); _taskbar.HrInit(); }
            if (show) { _taskbar.AddTab(_form.Handle); } else { _taskbar.DeleteTab(_form.Handle); }
        }
        catch (Exception exception) { LastError = "taskbar: " + exception.Message; }
    }

    public void Dispose()
    {
        if (_tray is not null) { _tray.Visible = false; _tray.Dispose(); }
        if (_taskbar is not null) { Marshal.ReleaseComObject(_taskbar); _taskbar = null; }
    }
}
