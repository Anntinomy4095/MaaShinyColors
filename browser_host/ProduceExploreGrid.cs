using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace MaaShinyColors.BrowserHost;

/// <summary>
/// Default-off supervised exploration vocabulary (<c>--enable-produce-explore</c>): a
/// deterministic 18x32 grid of 40x40 px cells on the 1280x720 canvas, ids <c>explore-rRRcCC</c>, family
/// <c>produce-explore</c>, never consuming. It is generated, not loaded, and kept apart from
/// <see cref="PipelineActionTable"/> so that table's bytes, SHA and disjointness check are unchanged. Requests still
/// carry only an id; the host picks the point inside the cell shrunk by <see cref="MarginPercent"/> per side.
/// The grid SHA is the SHA-256 of <see cref="Descriptor"/> (mirrored by Python <c>agent/core/produce_explore.py</c>).
/// </summary>
internal sealed class ProduceExploreGrid
{
    internal const string Family = "produce-explore";
    internal const string Version = "produce-explore-grid.v1";
    internal const string IdPrefix = "explore-";
    internal const int Cell = 40;
    internal const int Rows = 18;
    internal const int Cols = 32;
    internal const int MarginPercent = 20;
    private static readonly Regex IdPattern = new("\\Aexplore-r([0-9]{2})c([0-9]{2})\\z");
    internal static readonly ProduceExploreGrid Default = new();

    internal static string Descriptor =>
        $"SC.ProduceExploreGrid.v1\ncanvas=1280x720\ncell={Cell}\nrows={Rows}\ncols={Cols}\nmargin_percent={MarginPercent}\n" +
        "consuming=false\nfamily=produce-explore\nid=explore-rRRcCC\n";

    internal string Sha256 { get; } = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(Descriptor))).ToLowerInvariant();

    internal static string IdOf(int row, int col) =>
        row is >= 0 and < Rows && col is >= 0 and < Cols ? $"{IdPrefix}r{row:00}c{col:00}" : throw new InvalidOperationException("探索单元越界。");

    internal IEnumerable<string> Ids => Enumerable.Range(0, Rows).SelectMany(r => Enumerable.Range(0, Cols).Select(c => IdOf(r, c)));

    /// <summary>Strict id parse: two-digit row/col inside the grid, nothing else (no coordinates, no other spelling).</summary>
    internal static bool TryParse(string? id, out int row, out int col)
    {
        row = col = -1;
        if (id is null || IdPattern.Match(id) is not { Success: true } match) { return false; }
        row = int.Parse(match.Groups[1].Value, System.Globalization.CultureInfo.InvariantCulture);
        col = int.Parse(match.Groups[2].Value, System.Globalization.CultureInfo.InvariantCulture);
        return row < Rows && col < Cols;
    }

    internal bool IsCell(string? id) => TryParse(id, out _, out _);

    internal Rectangle Rect(string id) =>
        TryParse(id, out var row, out var col) ? new Rectangle(col * Cell, row * Cell, Cell, Cell)
            : throw new InvalidOperationException("未知探索单元；禁止任意坐标。");

    /// <summary>Cell shrunk by ceil(20% of 40) = 8 px per side: a 24x24 inner rect.</summary>
    internal Rectangle Inner(string id)
    {
        var rect = Rect(id);
        var m = (Cell * MarginPercent + 99) / 100;
        return new Rectangle(rect.X + m, rect.Y + m, rect.Width - 2 * m, rect.Height - 2 * m);
    }

    /// <summary>Uniform integer point inside the inner rect; <paramref name="next"/>(n) returns 0 ≤ value &lt; n.</summary>
    internal Point Sample(string id, Func<int, int> next)
    {
        var inner = Inner(id);
        var dx = next(inner.Width); var dy = next(inner.Height);
        if (dx < 0 || dx >= inner.Width || dy < 0 || dy >= inner.Height) { throw new InvalidOperationException("随机点越出探索内框。"); }
        return new(inner.X + dx, inner.Y + dy);
    }

    /// <summary>The grid must never share an id with the fixed pipeline table (checked at gate construction).</summary>
    internal static void RequireDisjointIds(PipelineActionTable table)
    {
        if (table.Ids.Any(id => id.StartsWith(IdPrefix, StringComparison.Ordinal)) ||
            table.Actions.Any(action => action.Family == Family))
        { throw new InvalidOperationException("Pipeline 动作表不得包含探索网格 id 或族。"); }
    }
}
