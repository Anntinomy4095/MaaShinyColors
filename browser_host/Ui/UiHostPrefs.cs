using System.Text.Json;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// The docked game host's own window settings: restored window width and height (physical px; the game column
/// scales to the height and the panel takes the remaining width, so there is no separate panel-width setting),
/// maximized, 静音 (game WebView IsMuted) and 后台运行 (minimize hides to tray and keeps running; default off),
/// Stored in dev_assets/private/ui/host-window.json, separate from the page's profile.json (the page cannot write it).
/// 设置 → 存储: 自动清理 (storage_auto, default off: a missing key is off)
/// and its retention days (storage_days: 7 / 14 / 30, default 14; see <see cref="UiStorage"/>). 面板位置 (panel_side, 2026-10-16:
/// right by default, left mirrors the dock; 2026-10-25 "detached" puts the panel in its own window, whose restored bounds
/// are panel_window {x, y, width, height}; panel_dock = the side it docks back to). Display preferences only: nothing here
/// can authorize input or spending.
/// Unknown or out-of-range values fall back to the defaults (an old file's panel_width is ignored); writes are atomic.
/// </summary>
internal sealed record UiHostPrefs(int Width, int Height, bool Maximized, bool Muted, bool BackgroundRun = false,
    bool StorageAuto = false, int StorageDays = UiStorage.DefaultDays,
    bool StopHotkey = true, bool PanelLeft = false, bool PanelDetached = false, Rectangle PanelWindow = default)
{
    internal const int MinWidth = 600;
    internal const int MaxWidth = 8000;
    /// <summary>788 x GameDisplay.MinScale (0.6), rounded down.</summary>
    internal const int MinHeight = 472;
    internal const int MaxHeight = 4000;
    internal static readonly UiHostPrefs Default = new(1680, 788, false, false);

    internal static UiHostPrefs Load(string path)
    {
        try
        {
            if (!File.Exists(path) || new FileInfo(path).Length > 4096) { return Default; }
            if (JsonNode.Parse(File.ReadAllText(path)) is not JsonObject root) { return Default; }
            static int? Int(JsonNode? node) => node is JsonValue value && value.TryGetValue<int>(out var number) ? number : null;
            static bool Bool(JsonNode? node) => node is JsonValue value && value.TryGetValue<bool>(out var flag) && flag;
            var width = Int(root["width"]);
            var height = Int(root["height"]);
            return new(width is >= MinWidth and <= MaxWidth ? width.Value : Default.Width,
                height is >= MinHeight and <= MaxHeight ? height.Value : Default.Height,
                Bool(root["maximized"]), Bool(root["muted"]),
                root["background_run"] is JsonValue run && run.TryGetValue<bool>(out var background) ? background : Default.BackgroundRun,
                Bool(root["storage_auto"]),
                Int(root["storage_days"]) is int days && UiStorage.RetentionDays.Contains(days) ? days : UiStorage.DefaultDays,
                // 紧急停止热键: on unless explicitly turned off.
                root["stop_hotkey"] is JsonValue hotkey && hotkey.TryGetValue<bool>(out var stopHotkey) ? stopHotkey : Default.StopHotkey,
                // 面板位置: "left" puts the panel on the game's left, "detached" in its own window;
                // anything else is the default right side.
                SideName(root) == "left" || (SideName(root) == "detached" && root["panel_dock"] is JsonValue dock && dock.TryGetValue<string>(out var dockSide) && dockSide == "left"),
                SideName(root) == "detached", PanelBounds(root["panel_window"]));
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException) { return Default; }
    }

    private static string? SideName(JsonObject root) => root["panel_side"] is JsonValue side && side.TryGetValue<string>(out var name) ? name : null;

    /// <summary>Saved bounds of the detached panel window (screen px), or empty when missing or out of range.</summary>
    private static Rectangle PanelBounds(JsonNode? node)
    {
        if (node is not JsonObject box) { return Rectangle.Empty; }
        static int? Int(JsonNode? value) => value is JsonValue v && v.TryGetValue<int>(out var number) ? number : null;
        return (Int(box["x"]), Int(box["y"]), Int(box["width"]), Int(box["height"])) is (int x and >= -20000 and <= 20000, int y and >= -20000 and <= 20000,
            int width and >= 200 and <= MaxWidth, int height and >= 200 and <= MaxHeight)
            ? new Rectangle(x, y, width, height) : Rectangle.Empty;
    }

    /// <summary>Best effort: a failed write keeps the previous file and never affects the game view.</summary>
    internal bool Save(string path)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            var temp = path + ".tmp";
            File.WriteAllText(temp, new JsonObject
            {
                ["width"] = Math.Clamp(Width, MinWidth, MaxWidth),
                ["height"] = Math.Clamp(Height, MinHeight, MaxHeight),
                ["maximized"] = Maximized,
                ["muted"] = Muted,
                ["background_run"] = BackgroundRun,
                ["storage_auto"] = StorageAuto,
                ["storage_days"] = UiStorage.RetentionDays.Contains(StorageDays) ? StorageDays : UiStorage.DefaultDays,
                ["stop_hotkey"] = StopHotkey,
                ["panel_side"] = PanelDetached ? "detached" : PanelLeft ? "left" : "right",
                ["panel_dock"] = PanelLeft ? "left" : "right",   // the side 独立窗口 docks back to
                ["panel_window"] = PanelWindow.IsEmpty ? null : new JsonObject
                    { ["x"] = PanelWindow.X, ["y"] = PanelWindow.Y, ["width"] = PanelWindow.Width, ["height"] = PanelWindow.Height },
            }.ToJsonString() + "\n");
            File.Move(temp, path, overwrite: true);
            return true;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { return false; }
    }
}
