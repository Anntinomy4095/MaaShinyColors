using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace MaaShinyColors.BrowserHost;

internal sealed record DailyNavigationRequest(string Action, string FrameSha256, int SourceWidth, int SourceHeight, string Nonce);

/// <summary>Closed transport vocabulary; screen recognition remains a Maa guard.</summary>
internal static class DailyNavigationPolicy
{
    internal static readonly string[] Actions = { "reward-ack", "login-board-skip", "announcement-back", "home-mission", "mission-complete-to-mission", "mission-back",
        "mission-help-close", "home-fes", "fes-match-entry", "match-help-close", "match-skip-open", "match-skip-cancel", "match-back", "training-back", "pickup-close", "story-skip-open", "training-help-close", "game-start" };
    internal static readonly string[] TrainingActions = { "home-idol", "idol-training", "training-select-asahi", "training-bronze-plus", "training-open-confirm", "training-confirm-one-bronze", "training-result-close",
        "training-list-back", "idol-back" };
    internal static readonly string[] ScrollActions = { "mission-scroll-down", "mission-scroll-up", "match-area-scroll-down", "match-area-scroll-up", "match-skip-scroll-down", "match-skip-scroll-up" };
    // Daily shop (マニー tab, 本日限定 offers 1–3). Only the three per-offer exchange confirms consume マニー;
    // each has its own per-game-day one-shot native budget. Quantity/limit controls (−/+/>>) are never targets.
    internal static readonly string[] ShopActions = { "home-shop", "shop-money-tab", "shop-back", "shop-offer-open-1", "shop-offer-open-2",
        "shop-offer-open-3", "shop-exchange-cancel", "shop-exchange-confirm-1", "shop-exchange-confirm-2", "shop-exchange-confirm-3",
        "shop-result-close" };
    // Daily Grade Fes (separate --enable-supervised-grade endpoint). Only the three difficulty circles may consume
    // one フェス参加権 (bulk 「まとめて消費」 is never a target); each has a per-game-day one-shot native budget.
    internal static readonly string[] GradeActions = { "fes-grade-entry", "fes-help-close", "grade-join-open", "grade-mode-grade",
        "grade-back", "grade-difficulty-easy", "grade-difficulty-normal", "grade-difficulty-hard" };
    internal static bool IsGrade(string action) => GradeActions.Contains(action, StringComparer.Ordinal);
    internal static bool IsGradeConsuming(string action) => IsGrade(action) && action.StartsWith("grade-difficulty-", StringComparison.Ordinal);
    internal static bool IsTraining(string action) => TrainingActions.Contains(action, StringComparer.Ordinal);
    internal static bool IsScroll(string action) => ScrollActions.Contains(action, StringComparer.Ordinal);
    internal static bool IsShop(string action) => ShopActions.Contains(action, StringComparer.Ordinal);
    internal static bool IsShopConsuming(string action) => IsShop(action) && action.StartsWith("shop-exchange-confirm-", StringComparison.Ordinal);
    internal static bool IsAction(string action) => Actions.Contains(action, StringComparer.Ordinal) || IsTraining(action) || IsScroll(action) || IsShop(action) || IsGrade(action);
    internal static string[] AllowedActions(bool training, bool scroll, bool shop = false) => Actions
        .Concat(training ? TrainingActions : Array.Empty<string>())
        .Concat(scroll ? ScrollActions : Array.Empty<string>())
        .Concat(shop ? ShopActions : Array.Empty<string>()).ToArray();
    internal static int FixedWheelDelta(string action) => IsScroll(action)
        ? action.EndsWith("-down", StringComparison.Ordinal) ? -240 : 240
        : throw new InvalidOperationException("未知只读滚动；禁止任意滚动幅度。");
    internal static Point FixedPoint(string action) => action switch
    {
        "reward-ack" => new Point(640, 520),
        "login-board-skip" or "story-skip-open" => new Point(1221, 56),
        "announcement-back" or "mission-back" or "match-back" or "training-back" or "training-list-back" or "idol-back" => new Point(57, 662),
        "home-mission" => new Point(62, 429),
        "mission-complete-to-mission" => new Point(744, 580),
        "mission-help-close" or "match-help-close" or "training-help-close" => new Point(640, 603),
        "home-fes" => new Point(1190, 650),
        "fes-match-entry" => new Point(1045, 543),
        "match-skip-open" => new Point(1190, 548),
        "match-skip-cancel" => new Point(462, 620),
        "pickup-close" => new Point(1111, 68),
        // Same fixed TOUCH TO START point as the separate one-shot touch-to-start mode (native 2026-10-02).
        "game-start" => new Point(640, 630),
        "mission-scroll-down" or "mission-scroll-up" => new Point(1000, 430),
        "match-area-scroll-down" or "match-area-scroll-up" => new Point(260, 460),
        "match-skip-scroll-down" or "match-skip-scroll-up" => new Point(1030, 410),
        "home-idol" => new Point(565, 665),
        "idol-training" => new Point(516, 299),
        "training-select-asahi" => new Point(461, 236),
        "training-bronze-plus" => new Point(1085, 347),
        "training-open-confirm" => new Point(1173, 663),
        "training-confirm-one-bronze" => new Point(740, 510),
        "training-result-close" => new Point(640, 486),
        // Measured on native 1280x720 frames 2026-10-02 (manual-capture-kept-20261002/shop-grade).
        "home-shop" => new Point(62, 282),
        "shop-money-tab" => new Point(495, 110),
        "shop-back" => new Point(57, 662),
        "shop-offer-open-1" => new Point(166, 607),
        "shop-offer-open-2" => new Point(436, 607),
        "shop-offer-open-3" => new Point(706, 607),
        "shop-exchange-cancel" => new Point(540, 626),
        // 交換結果 dialog 閉じる (dev_assets/private/shop-20261002-04-confirm1-frames/frame-024.png).
        "shop-result-close" => new Point(640, 497),
        "shop-exchange-confirm-1" or "shop-exchange-confirm-2" or "shop-exchange-confirm-3" => new Point(740, 626),
        // Grade Fes, measured on native frames manual-capture-kept-20261002/shop-grade 026/033/053/064/066.
        "fes-grade-entry" => new Point(1188, 541),
        "fes-help-close" => new Point(640, 600),
        "grade-join-open" => new Point(1183, 620),
        "grade-mode-grade" => new Point(1167, 453),
        "grade-back" => new Point(57, 662),
        "grade-difficulty-easy" => new Point(332, 360),
        "grade-difficulty-normal" => new Point(638, 360),
        "grade-difficulty-hard" => new Point(942, 360),
        _ => throw new InvalidOperationException("未知日常动作；禁止任意坐标。")
    };
    internal static bool IsGameUri(string raw) => Uri.TryCreate(raw, UriKind.Absolute, out var uri) &&
        uri.Scheme == "https" && uri.Host == "shinycolors.enza.fun" && uri.IsDefaultPort && string.IsNullOrEmpty(uri.UserInfo);

    internal static DailyNavigationRequest Parse(JsonElement root, string token)
    {
        var names = root.EnumerateObject().Select(item => item.Name).Order().ToArray();
        if (!names.SequenceEqual(new[] { "action", "command", "frame_sha256", "nonce", "source_height", "source_width", "token" }) ||
            root.GetProperty("command").GetString() != "daily-navigate" ||
            !CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(root.GetProperty("token").GetString() ?? ""), Encoding.UTF8.GetBytes(token)))
        { throw new InvalidOperationException("日常请求字段或认证无效。"); }
        var action = root.GetProperty("action").GetString() ?? "";
        var sha = root.GetProperty("frame_sha256").GetString() ?? "";
        var nonce = root.GetProperty("nonce").GetString() ?? "";
        var width = root.GetProperty("source_width").GetInt32();
        var height = root.GetProperty("source_height").GetInt32();
        if (!IsAction(action) || !Regex.IsMatch(sha, "\\A[0-9a-f]{64}\\z") ||
            !Regex.IsMatch(nonce, "\\A[0-9a-f]{32}\\z") || Math.Abs((long)width - 1280) > 2 || Math.Abs((long)height - 720) > 2)
        { throw new InvalidOperationException("日常固定动作、帧、单次编号或源尺寸无效。"); }
        return new(action, sha, width, height, nonce);
    }
    internal static bool FrameMatches(DailyNavigationRequest request, string? sha, int? width, int? height, double ageMs) =>
        sha is not null && request.FrameSha256 == sha && request.SourceWidth == width && request.SourceHeight == height &&
        double.IsFinite(ageMs) && ageMs >= 0 && ageMs <= 5000;
}
