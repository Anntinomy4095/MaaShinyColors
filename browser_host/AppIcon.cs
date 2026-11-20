using System.Reflection;

namespace MaaShinyColors.BrowserHost;

/// <summary>Host icon (app.ico, built by tools/make_app_icon.py from app_icon.png): window/taskbar and tray share it.
/// Falls back to the system application icon if the embedded resource is missing.</summary>
internal static class AppIcon
{
    private static Icon? _icon;

    internal static Icon Value => _icon ??= Load();

    private static Icon Load()
    {
        try
        {
            using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("SC.AppIcon.ico");
            return stream is null ? SystemIcons.Application : new Icon(stream);
        }
        catch (ArgumentException) { return SystemIcons.Application; }
    }
}
