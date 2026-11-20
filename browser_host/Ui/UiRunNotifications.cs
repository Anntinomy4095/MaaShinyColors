using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

internal sealed record UiRunNotification(string Key, string Title, string Message, ToolTipIcon Icon);

/// <summary>Native reminders from existing run events; results remain in the engine's persisted history.</summary>
internal sealed class UiRunNotifications
{
    private JsonObject _prefs = new();
    private string? _runId;
    private bool _attentionShown;
    private readonly HashSet<string> _shown = new(StringComparer.Ordinal);

    internal void SetProfile(JsonObject profile) => _prefs = (JsonObject?)profile["prefs"]?.DeepClone() ?? new();

    internal UiRunNotification? Handle(string name, JsonObject data)
    {
        if (name != "run.update" || data["run_id"]?.GetValue<string>() is not { } id) { return null; }
        // 设置 → 通知「系统通知」: off keeps only the in-page reminders.
        if (_prefs["notify"]?["system"] is JsonValue system && system.TryGetValue<bool>(out var systemOn) && !systemOn) { return null; }
        if (_runId != id)
        {
            _runId = id;
            _attentionShown = false;
            _shown.Clear();
        }
        var finished = (string?)data["state"] == "finished";
        var status = finished ? (string?)data["outcome"] : (string?)data["status"];
        var key = status switch
        {
            "failed" => "failed", "uncertain" => "uncertain", "takeover" => "takeover",
            "guard_refused" or "refused" => "guard_refused",
            "succeeded" or "stopped" when finished => "all_done",
            _ => null,
        };
        // Final reconciliation repeats task states. Do not repeat their reminder, or cover it with “all done”.
        if (key is null || (_attentionShown && (finished || key != "takeover")) || _shown.Contains(key)
            || _prefs["notify"]?[key] is JsonValue flag && flag.TryGetValue<bool>(out var on) && !on) { return null; }
        _shown.Add(key);
        if (key != "all_done") { _attentionShown = true; }
        var title = key switch
        {
            "failed" => "任务失败", "uncertain" => "结果需要核对", "takeover" => "需要你接手",
            "guard_refused" => "任务未能执行", _ => "运行结束",
        };
        var message = key switch
        {
            "failed" => "有任务失败，请打开运行记录查看原因。",
            "uncertain" => "本次结果不确定，请在游戏中核对。",
            "takeover" => "请还原窗口，接着操作游戏。运行记录已保留。",
            "guard_refused" => "画面与任务不符，未点击。详情见运行记录。",
            _ => status == "stopped" ? "本轮已停止，可打开运行记录查看结果。" : "本轮任务已结束，可打开运行记录查看结果。",
        };
        return new(key, "MaaShinyColors · " + title, message,
            key == "failed" ? ToolTipIcon.Error : key == "all_done" ? ToolTipIcon.Info : ToolTipIcon.Warning);
    }
}
