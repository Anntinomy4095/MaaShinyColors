using System.Text.Json;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// Read-only, flattened view of project_policy.json for the 安全 tab (policy.get). The page can only display it:
/// nothing here grants input or spending, and the page can never write the policy file.
/// </summary>
internal static class UiPolicySummary
{
    private const long MaxPolicyBytes = 64 * 1024;

    internal static JsonObject Load(string repoRoot)
    {
        var path = Path.Combine(repoRoot, "project_policy.json");
        var info = new FileInfo(path);
        if (!info.Exists || info.Length > MaxPolicyBytes) { return new JsonObject { ["error"] = "policy_unreadable" }; }
        using var doc = JsonDocument.Parse(File.ReadAllText(path));
        return FromPolicy(doc.RootElement, repoRoot);
    }

    internal static JsonObject FromPolicy(JsonElement policy, string repoRoot)
    {
        var resources = policy.TryGetProperty("resource_usage", out var r) && r.ValueKind == JsonValueKind.Object ? r : default;
        var reference = Str(policy, "risk_acceptance_reference");
        var (cardScanReady, cardScanReason) = CardScanReadiness(PipelineActionTable.Default.Actions, ReadCardScanPipeline(repoRoot));
        var (knowhowReady, knowhowReason) = KnowhowScanReadiness(PipelineActionTable.Default.Actions, ReadPipeline(repoRoot, "knowhow_scan.json"));
        return new JsonObject
        {
            ["card_scan_ready"] = cardScanReady,
            ["card_scan_reason"] = cardScanReason,
            ["knowhow_scan_ready"] = knowhowReady,
            ["knowhow_scan_reason"] = knowhowReason,
            ["live_input_enabled"] = Bool(policy, "live_input_enabled"),
            ["risk_acceptance_reference"] = reference,
            ["risk_reference_resolvable"] = reference is null ? false : ReferenceResolvable(repoRoot, reference),
            ["premium_currency_allowed"] = Bool(resources, "premium_currency_allowed"),
            ["paid_items_allowed"] = Bool(resources, "paid_items_allowed"),
            ["other_inventory_allowed"] = Bool(resources, "other_inventory_allowed"),
            ["stamina_items_enabled"] = Nested(resources, "stamina_items", "enabled"),
            ["fes_tickets_enabled"] = Nested(resources, "fes_tickets", "enabled"),
            ["allowed_live_input_nodes"] = policy.TryGetProperty("allowed_live_input_nodes", out var nodes) && nodes.ValueKind == JsonValueKind.Array
                ? nodes.GetArrayLength() : null,
        };
    }

    private const long MaxPipelineBytes = 256 * 1024;
    internal const string CardScanFamily = "card-scan";
    internal const string CardScanEntryNode = "SC.CardScan.Entry";
    internal const string KnowhowScanFamily = "knowhow-scan";
    internal const string KnowhowScanEntryNode = "SC.KnowhowScan.Entry";

    /// <summary>Text of assets/resource/base/pipeline/card_scan.json, or null when missing / oversized / unreadable.</summary>
    internal static string? ReadCardScanPipeline(string repoRoot) => ReadPipeline(repoRoot, "card_scan.json");

    /// <summary>Text of one assets/resource/base/pipeline file, or null when missing / oversized / unreadable.</summary>
    internal static string? ReadPipeline(string repoRoot, string fileName)
    {
        try
        {
            var info = new FileInfo(Path.Combine(repoRoot, "assets", "resource", "base", "pipeline", fileName));
            return info.Exists && info.Length <= MaxPipelineBytes ? File.ReadAllText(info.FullName) : null;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { return null; }
    }

    /// <summary>
    /// Read-only readiness of the self-scrolling owned-card scan for the 资源 tab button. Ready only
    /// when the host's own (embedded) action table has at least one <c>card-scan</c> action and every one of them is
    /// enabled, AND the scan entry node in card_scan.json is enabled. Display only: it grants no input, the host still
    /// refuses each disabled action at click time, and nothing here can change either file.
    /// </summary>
    internal static (bool Ready, string Reason) CardScanReadiness(IEnumerable<PipelineAction> actions, string? pipelineJson) =>
        ScanReadiness(actions, pipelineJson, CardScanFamily, CardScanEntryNode, "card_scan");

    /// <summary>
    /// Same rule for 「扫描ノウハウ」: every <c>knowhow-scan</c> action enabled and the
    /// SC.KnowhowScan.Entry node of knowhow_scan.json enabled (the bound file produced by tools/knowhow_scan_build.py).
    /// </summary>
    internal static (bool Ready, string Reason) KnowhowScanReadiness(IEnumerable<PipelineAction> actions, string? pipelineJson) =>
        ScanReadiness(actions, pipelineJson, KnowhowScanFamily, KnowhowScanEntryNode, "knowhow_scan");

    private static (bool Ready, string Reason) ScanReadiness(IEnumerable<PipelineAction> actions, string? pipelineJson, string familyName,
        string entryNode, string code)
    {
        var family = actions.Where(action => action.Family == familyName).ToList();
        if (family.Count == 0) { return (false, code + "_actions_missing"); }
        var disabled = family.Where(action => !action.Enabled).Select(action => action.Id).ToList();
        if (disabled.Count > 0) { return (false, code + "_actions_disabled:" + string.Join(",", disabled)); }
        if (pipelineJson is null) { return (false, code + "_pipeline_unreadable"); }
        try
        {
            using var doc = JsonDocument.Parse(pipelineJson, new JsonDocumentOptions
            { CommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true });
            if (doc.RootElement.ValueKind != JsonValueKind.Object || !doc.RootElement.TryGetProperty(entryNode, out var entry)
                || entry.ValueKind != JsonValueKind.Object)
            { return (false, code + "_entry_missing"); }
            // Maa's default is enabled; only an explicit boolean false (or a malformed flag) keeps the entry closed.
            if (entry.TryGetProperty("enabled", out var flag) && flag.ValueKind != JsonValueKind.True)
            { return (false, code + "_entry_disabled"); }
        }
        catch (JsonException) { return (false, code + "_pipeline_unreadable"); }
        return (true, "ready");
    }

    /// <summary>"docs/SAFETY.md#adr-011..." resolves when the file is inside the repo and has a heading starting with the anchor's ADR id.</summary>
    internal static bool ReferenceResolvable(string repoRoot, string reference)
    {
        var parts = reference.Split('#', 2);
        var full = Path.GetFullPath(Path.Combine(repoRoot, parts[0]));
        var root = Path.GetFullPath(repoRoot).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!full.StartsWith(root, StringComparison.OrdinalIgnoreCase) || !File.Exists(full)) { return false; }
        if (parts.Length == 1) { return true; }
        var id = parts[1].Split(new[] { '用', '-' }, 3);
        var adr = id.Length >= 2 && id[0].Equals("adr", StringComparison.OrdinalIgnoreCase) ? $"ADR-{id[1]}" : parts[1];
        foreach (var line in File.ReadLines(full))
        {
            if (line.StartsWith('#') && line.Contains(adr, StringComparison.OrdinalIgnoreCase)) { return true; }
        }
        return false;
    }

    private static bool? Bool(JsonElement obj, string key) =>
        obj.ValueKind == JsonValueKind.Object && obj.TryGetProperty(key, out var v) && v.ValueKind is JsonValueKind.True or JsonValueKind.False ? v.GetBoolean() : null;

    private static bool? Nested(JsonElement obj, string key, string inner) =>
        obj.ValueKind == JsonValueKind.Object && obj.TryGetProperty(key, out var v) ? Bool(v, inner) : null;

    private static string? Str(JsonElement obj, string key) =>
        obj.ValueKind == JsonValueKind.Object && obj.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
}
