using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>Developer action-table management.</summary>
internal interface IUiDevTools
{
    /// <summary>Action-table status for the developer settings page.</summary>
    JsonObject Status();
    /// <summary>op: reload (「重新载入动作表」). Returns null on success, otherwise a refusal code; a refusal keeps the old table.</summary>
    string? ActionTable(string op);
}

internal sealed class HostDevTools : IUiDevTools
{
    private readonly Func<string?>? _reloadTable;
    private JsonObject? _lastReload;

    internal HostDevTools(Func<string?>? reloadTable = null)
    { _reloadTable = reloadTable; }

    /// <summary>Current action table summary for 设置 → 开发者 (no path beyond the fixed repository-relative name).</summary>
    internal static JsonObject TableStatus(PipelineActionTable table, JsonObject? lastReload) => new()
    {
        ["sha256"] = table.Sha256, ["source"] = "assets/input/pipeline_actions.json", ["actions"] = table.Actions.Count,
        ["enabled"] = table.Actions.Count(action => action.Enabled), ["consuming"] = table.ConsumingIds.Length,
        ["loaded_at"] = table.LoadedAt?.ToString("O", System.Globalization.CultureInfo.InvariantCulture),
        ["last_reload"] = lastReload?.DeepClone(),
    };

    public string? ActionTable(string op)
    {
        if (op != "reload") { return "unknown_op"; }
        if (_reloadTable is null) { return "action_table_reload_unavailable"; }
        var before = PipelineActionTable.Default.Sha256;
        var refusal = _reloadTable();
        _lastReload = new JsonObject
        {
            ["ok"] = refusal is null, ["reason"] = refusal, ["at"] = DateTimeOffset.UtcNow.ToString("O", System.Globalization.CultureInfo.InvariantCulture),
            ["previous_sha256"] = before, ["sha256"] = PipelineActionTable.Default.Sha256,
        };
        return refusal;
    }

    public JsonObject Status()
    {
        JsonObject? table = null;
        try { table = TableStatus(PipelineActionTable.Default, _lastReload); }
        catch (Exception exception) when (exception is InvalidOperationException or IOException or System.Text.Json.JsonException) { }
        return new JsonObject { ["action_table"] = table };
    }
}
