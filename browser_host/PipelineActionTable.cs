using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace MaaShinyColors.BrowserHost;

/// <summary><paramref name="Wheel"/> (B5b): fixed wheel delta of a scroll action; null for a button click.</summary>
/// <summary><paramref name="DragRect"/> (SC.PipelineDrag.v1, 2026-11-18): a drag-only action; the drag starts at the
/// recognised point inside <paramref name="Rect"/> and must end inside this rect. Never consuming, never a wheel.</summary>
internal sealed record PipelineAction(string Id, string Family, bool Consuming, Rectangle Rect, int? Wheel = null,
    bool Enabled = true, IReadOnlyList<string>? Nodes = null, Rectangle? DragRect = null);

/// <summary>
/// The <c>pipeline-click</c> vocabulary, read and validated from <c>assets/input/pipeline_actions.json</c> (the single
/// source shared with the Python controller, <c>agent/core/pipeline_actions.py</c>) when the host starts, and again only
/// through <see cref="Reload"/> (设置 → 开发者「重新载入动作表」) while no run or scan is active. A table that
/// fails validation never replaces the current one, so a table change needs at most a reload, never a rebuild. Each action is one
/// button rect on the 1280x720 canvas. Version 2 permits overlap with unique recognition-node bindings and explicit
/// disabled catalog entries. The host chooses the click point itself, uniformly
/// inside the rect shrunk by <see cref="MarginPercent"/> per side (centre when the inner rect is too small), so
/// requests carry only an action id and never coordinates.
/// </summary>
internal sealed class PipelineActionTable
{
    internal static readonly string RelativePath = Path.Combine("assets", "input", "pipeline_actions.json");
    private static readonly Regex IdPattern = new("\\A[a-z0-9]+(?:-[a-z0-9]+)*\\z");
    private static readonly Regex NodePattern = new("\\ASC\\.[A-Za-z0-9_.]+\\z");
    private static readonly string[] TopKeys = { "actions", "canvas", "families", "margin_percent", "min_inner_px", "version" };
    private static readonly string[] ActionKeys = { "consuming", "family", "id", "nodes", "rect", "source" };
    private static readonly string[] WheelActionKeys = { "consuming", "family", "id", "nodes", "rect", "source", "wheel" };
    private static readonly int[] WheelDeltas = { -240, -120, -60, 60, 120, 240 };
    private static readonly object CurrentSync = new();
    private static PipelineActionTable? _current;

    internal IReadOnlyList<PipelineAction> Actions { get; }
    internal int MarginPercent { get; }
    internal int MinInnerPx { get; }
    internal string Sha256 { get; }
    internal int Version { get; }
    internal string[] Ids => Actions.Select(action => action.Id).ToArray();
    internal string[] ConsumingIds => Actions.Where(action => action.Consuming).Select(action => action.Id).ToArray();

    private PipelineActionTable(IReadOnlyList<PipelineAction> actions, int margin, int minInner, string sha, int version)
    { Actions = actions; MarginPercent = margin; MinInnerPx = minInner; Sha256 = sha; Version = version; }

    /// <summary>File path and UTC time this table was read from; null for a table parsed from bytes (self-tests).</summary>
    internal string? SourcePath { get; private init; }
    internal DateTimeOffset? LoadedAt { get; private init; }

    /// <summary>The host's current table: read from the repository file on first use, replaced only by <see cref="Reload"/>.</summary>
    internal static PipelineActionTable Default
    {
        get { lock (CurrentSync) { return _current ??= Load(DefaultPath(), SelectionLoopBudget.Root()); } }
    }

    internal static string DefaultPath() => Path.Combine(SelectionLoopBudget.Root(), RelativePath);

    /// <summary>
    /// Reads and fully validates a table file: <see cref="Parse"/> (schema, rects, consuming/enabled, node uniqueness)
    /// plus <see cref="RequireNodeBinding"/> against the Pipeline JSON under <paramref name="repoRoot"/>. Throws on any error.
    /// </summary>
    internal static PipelineActionTable Load(string path, string repoRoot)
    {
        var table = Parse(File.ReadAllBytes(path));
        RequireNodeBinding(table, repoRoot);
        return new(table.Actions, table.MarginPercent, table.MinInnerPx, table.Sha256, table.Version)
        { SourcePath = Path.GetFullPath(path), LoadedAt = DateTimeOffset.UtcNow };
    }

    /// <summary>
    /// Validates <paramref name="path"/> (default: the repository file) and, only when every check passes, makes it the
    /// current table. On any error the old table stays current and the exception propagates. Callers own the
    /// "nothing active" precondition and re-create every gate/session that captured the old table.
    /// </summary>
    internal static PipelineActionTable Reload(string? path = null, string? repoRoot = null)
    {
        var root = repoRoot ?? SelectionLoopBudget.Root();
        var table = Load(path ?? Path.Combine(root, RelativePath), root);
        Commit(table);
        return table;
    }

    /// <summary>Makes an already validated table (from <see cref="Load"/>) current.</summary>
    internal static void Commit(PipelineActionTable table)
    {
        if (table.SourcePath is null) { throw new InvalidOperationException("只能提交从文件载入并校验过的动作表。"); }
        lock (CurrentSync) { _current = table; }
    }

    /// <summary>Every bound node must be a top-level node of some Pipeline JSON under assets/resource (no dangling binding).</summary>
    internal static void RequireNodeBinding(PipelineActionTable table, string repoRoot)
    {
        var resource = Path.Combine(repoRoot, "assets", "resource");
        var known = new HashSet<string>(StringComparer.Ordinal);
        var options = new JsonDocumentOptions { CommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true };
        foreach (var file in Directory.EnumerateFiles(resource, "*.json", SearchOption.AllDirectories)
                     .Where(file => file.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar).Contains("pipeline")))
        {
            using var document = JsonDocument.Parse(File.ReadAllBytes(file), options);
            if (document.RootElement.ValueKind != JsonValueKind.Object) { continue; }
            foreach (var node in document.RootElement.EnumerateObject()) { known.Add(node.Name); }
        }
        var missing = table.Actions.SelectMany(action => (action.Nodes ?? Array.Empty<string>()).Where(node => !known.Contains(node))
            .Select(node => action.Id + "→" + node)).ToArray();
        if (missing.Length > 0) { throw new InvalidOperationException("动作绑定的识别节点不存在：" + string.Join("，", missing.Take(5))); }
    }

    internal bool IsAction(string? id) => id is not null && Actions.Any(action => action.Id == id);
    internal bool IsConsuming(string? id) => id is not null && Actions.Any(action => action.Id == id && action.Consuming);
    internal string? NodeRefusal(string id, string? node)
    {
        var action = Get(id);
        if (!action.Enabled) { return "pipeline_action_disabled"; }
        if ((Version == 2 || node is not null) && (node is null || action.Nodes?.Contains(node, StringComparer.Ordinal) != true))
        { return "pipeline_action_node_mismatch"; }
        return null;
    }
    internal int? WheelOf(string id) => Get(id).Wheel;
    internal Rectangle? DragOf(string id) => Get(id).DragRect;
    internal PipelineAction Get(string id) => Actions.FirstOrDefault(action => action.Id == id)
        ?? throw new InvalidOperationException("未知 Pipeline 动作；禁止任意坐标。");

    /// <summary>Inner rect after an integer ceil(margin% × size) per side; null when smaller than MinInnerPx.</summary>
    internal Rectangle? Inner(string id)
    {
        var rect = Get(id).Rect;
        var mx = (rect.Width * MarginPercent + 99) / 100;
        var my = (rect.Height * MarginPercent + 99) / 100;
        var width = rect.Width - 2 * mx; var height = rect.Height - 2 * my;
        return width < MinInnerPx || height < MinInnerPx ? null : new Rectangle(rect.X + mx, rect.Y + my, width, height);
    }

    internal Point Centre(string id) { var rect = Get(id).Rect; return new(rect.X + rect.Width / 2, rect.Y + rect.Height / 2); }

    /// <summary>Uniform integer point inside the inner rect; <paramref name="next"/>(n) returns 0 ≤ value &lt; n.</summary>
    internal Point Sample(string id, Func<int, int> next)
    {
        if (Inner(id) is not Rectangle inner) { return Centre(id); }
        var dx = next(inner.Width); var dy = next(inner.Height);
        if (dx < 0 || dx >= inner.Width || dy < 0 || dy >= inner.Height) { throw new InvalidOperationException("随机点越出内框。"); }
        return new(inner.X + dx, inner.Y + dy);
    }

    private static string[] Names(JsonElement element)
    {
        if (element.ValueKind != JsonValueKind.Object) { throw new InvalidOperationException("动作表对象无效。"); }
        var names = element.EnumerateObject().Select(item => item.Name).ToArray();
        if (names.Distinct(StringComparer.Ordinal).Count() != names.Length) { throw new InvalidOperationException("动作表含重复字段。"); }
        return names.Order(StringComparer.Ordinal).ToArray();
    }

    private static int Int(JsonElement element) =>
        element.ValueKind == JsonValueKind.Number && element.TryGetInt32(out var value) && element.GetRawText().All(char.IsAsciiDigit)
            ? value : throw new InvalidOperationException("动作表整数字段无效。");

    private static string Text(JsonElement element) =>
        element.ValueKind == JsonValueKind.String ? element.GetString()! : throw new InvalidOperationException("动作表文本字段无效。");

    private static bool Disjoint(Rectangle a, Rectangle b) => a.Right <= b.Left || b.Right <= a.Left || a.Bottom <= b.Top || b.Bottom <= a.Top;

    /// <summary>Same validation as Python <c>pipeline_actions.parse_table</c>.</summary>
    internal static PipelineActionTable Parse(byte[] raw)
    {
        using var document = JsonDocument.Parse(raw);
        var root = document.RootElement;
        if (!Names(root).SequenceEqual(TopKeys)) { throw new InvalidOperationException("动作表顶层字段无效。"); }
        var version = Int(root.GetProperty("version"));
        if (version is not (1 or 2)) { throw new InvalidOperationException("动作表版本必须为1或2。"); }
        var canvas = root.GetProperty("canvas");
        if (canvas.ValueKind != JsonValueKind.Array || canvas.GetArrayLength() != 2 || Int(canvas[0]) != 1280 || Int(canvas[1]) != 720)
        { throw new InvalidOperationException("动作表画布必须为1280x720。"); }
        var margin = Int(root.GetProperty("margin_percent"));
        var minInner = Int(root.GetProperty("min_inner_px"));
        if (margin is < 20 or > 45 || minInner is < 2 or > 64) { throw new InvalidOperationException("动作表边距参数无效。"); }
        var familyElement = root.GetProperty("families");
        if (familyElement.ValueKind != JsonValueKind.Array) { throw new InvalidOperationException("动作族列表无效。"); }
        var families = familyElement.EnumerateArray().Select(Text).ToArray();
        if (families.Length == 0 || families.Distinct(StringComparer.Ordinal).Count() != families.Length || !families.All(IdPattern.IsMatch))
        { throw new InvalidOperationException("动作族列表无效。"); }
        var items = root.GetProperty("actions");
        if (items.ValueKind != JsonValueKind.Array || items.GetArrayLength() == 0) { throw new InvalidOperationException("动作表为空。"); }
        var actions = new List<PipelineAction>(); var nodes = new HashSet<string>(StringComparer.Ordinal);
        foreach (var item in items.EnumerateArray())
        {
            var keys = Names(item);
            if (!keys.Where(key => key != "wheel" && (version != 2 || (key != "enabled" && key != "drag_rect"))).SequenceEqual(ActionKeys))
            { throw new InvalidOperationException("动作字段无效。"); }
            var id = Text(item.GetProperty("id"));
            var family = Text(item.GetProperty("family"));
            var consumingElement = item.GetProperty("consuming");
            if (!IdPattern.IsMatch(id) || actions.Any(action => action.Id == id) || !families.Contains(family, StringComparer.Ordinal) ||
                consumingElement.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
            { throw new InvalidOperationException("动作 id、族或消费标记无效：" + id); }
            var rectElement = item.GetProperty("rect");
            if (rectElement.ValueKind != JsonValueKind.Array || rectElement.GetArrayLength() != 4) { throw new InvalidOperationException("动作框无效：" + id); }
            var r = rectElement.EnumerateArray().Select(Int).ToArray();
            var rect = new Rectangle(r[0], r[1], r[2], r[3]);
            if (rect.Width < 1 || rect.Height < 1 || rect.X < 0 || rect.Y < 0 || rect.Right > 1280 || rect.Bottom > 720)
            { throw new InvalidOperationException("动作框越出画布：" + id); }
            if (string.IsNullOrEmpty(Text(item.GetProperty("source")))) { throw new InvalidOperationException("动作来源说明缺失：" + id); }
            var nodeElement = item.GetProperty("nodes");
            if (nodeElement.ValueKind != JsonValueKind.Array) { throw new InvalidOperationException("动作节点列表无效：" + id); }
            var actionNodes = nodeElement.EnumerateArray().Select(Text).ToArray();
            if (version == 2 && actionNodes.Length == 0) { throw new InvalidOperationException("版本2动作必须绑定节点：" + id); }
            foreach (var node in actionNodes)
            { if (!NodePattern.IsMatch(node) || !nodes.Add(node)) { throw new InvalidOperationException("动作节点无效或重复：" + node); } }
            if (version == 1 && actions.FirstOrDefault(other => !Disjoint(other.Rect, rect)) is PipelineAction overlap)
            { throw new InvalidOperationException($"动作框重叠：{overlap.Id} / {id}"); }
            var enabled = true;
            if (item.TryGetProperty("enabled", out var enabledElement))
            {
                if (enabledElement.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                { throw new InvalidOperationException("动作启用标记无效：" + id); }
                enabled = enabledElement.GetBoolean();
            }
            int? wheel = null;
            if (keys.Contains("wheel"))
            {
                var wheelElement = item.GetProperty("wheel");
                if (wheelElement.ValueKind != JsonValueKind.Number || !wheelElement.TryGetInt32(out var delta) || !WheelDeltas.Contains(delta) ||
                    consumingElement.GetBoolean())
                { throw new InvalidOperationException("滚轮动作增量无效或标为消费：" + id); }
                wheel = delta;
            }
            Rectangle? drag = null;
            if (keys.Contains("drag_rect"))
            {
                var dragElement = item.GetProperty("drag_rect");
                if (dragElement.ValueKind != JsonValueKind.Array || dragElement.GetArrayLength() != 4 || wheel is not null ||
                    consumingElement.GetBoolean())
                { throw new InvalidOperationException("拖动终点框无效、带滚轮或标为消费：" + id); }
                var d = dragElement.EnumerateArray().Select(Int).ToArray();
                var dragRect = new Rectangle(d[0], d[1], d[2], d[3]);
                if (dragRect.Width < 1 || dragRect.Height < 1 || dragRect.X < 0 || dragRect.Y < 0 || dragRect.Right > 1280 || dragRect.Bottom > 720)
                { throw new InvalidOperationException("拖动终点框越出画布：" + id); }
                drag = dragRect;
            }
            actions.Add(new(id, family, consumingElement.GetBoolean(), rect, wheel, enabled, actionNodes, drag));
        }
        return new(actions, margin, minInner, Convert.ToHexString(SHA256.HashData(raw)).ToLowerInvariant(), version);
    }
}
