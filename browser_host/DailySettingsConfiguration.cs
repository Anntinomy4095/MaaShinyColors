using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

/// <summary>Planning preferences only; no runtime authorization or host/session data.</summary>
internal sealed class DailySettingsConfiguration
{
    public bool ShopEnabled { get; set; }
    public bool IncludeThird { get; set; }
    public bool TrainingEnabled { get; set; }
    public bool MatchEnabled { get; set; }
    public bool GradeEnabled { get; set; }
    public string CardId { get; set; } = "";
    public string StageId { get; set; } = "";
    public string DifficultyId { get; set; } = "";
    public bool LatestStage { get; set; } = true;
    public int MatchCount { get; set; } = 20;
    public int Coins { get; set; }
    public int BronzeTickets { get; set; }
    public int FesRights { get; set; }
    public int ShopBudget { get; set; }
    public int CoinReserve { get; set; }
    public int TrainingBudget { get; set; }
    public int MatchBudget { get; set; }
    public int GradeBudget { get; set; }
    public List<DailyShopPreference> Offers { get; } = [];
    /// <summary>Rank mode: buy offer rank r whatever today's 本日限定 item is (name OCR must match card and dialog).</summary>
    public const string AnyDailyDiscount = "any_daily_discount";

    public string ToJson()
    {
        if (new[] { Coins, BronzeTickets, FesRights, ShopBudget, CoinReserve, TrainingBudget, MatchBudget, GradeBudget }.Any(value => value < 0)
            || MatchCount < 1) { throw new ArgumentException("预算须为非负整数，Match 次数须为正整数。"); }
        var card = OptionalIdentity(CardId);
        var stage = LatestStage ? null : OptionalIdentity(StageId);
        var difficulty = OptionalIdentity(DifficultyId);
        if (TrainingEnabled && card is null) { throw new ArgumentException("启用训练偏好须明确填写 S 卡身份。"); }
        if (MatchEnabled && !LatestStage && stage is null) { throw new ArgumentException("指定 Match 关卡须填写 Area/Stage 身份。"); }
        if (GradeEnabled && difficulty is null) { throw new ArgumentException("启用 Grade 偏好须填写具体难度。"); }
        if (Offers.Any(offer => offer.Rank is < 1 or > 3 || (offer.Rank == 3 && !IncludeThird)
            || string.IsNullOrWhiteSpace(offer.ItemId) || offer.ItemId != offer.ItemId.Trim()
            || offer.Quantity < 1 || offer.MaxUnitPrice < 1
            || offer.ItemType is not ("basic_training_ticket" or "fes_entry_ticket" or "ordinary_item"))
            || Offers.Select(offer => offer.Rank).Distinct().Count() != Offers.Count
            || Offers.Any(offer => offer.ItemId == AnyDailyDiscount && (offer.Quantity != 1 || offer.ItemType != "ordinary_item"))
            || Offers.Where(offer => offer.ItemId != AnyDailyDiscount).Select(offer => offer.ItemId).Distinct().Count()
                != Offers.Count(offer => offer.ItemId != AnyDailyDiscount))
        { throw new ArgumentException("折扣商品须绑定不同身份/排序、明确类型、正整数数量及最高单价；第三项须单独开启。"); }
        if (ShopEnabled && (!Offers.Any(offer => offer.Rank == 1) || !Offers.Any(offer => offer.Rank == 2)))
        { throw new ArgumentException("启用商店偏好须填写前两项折扣商品。"); }
        return JsonSerializer.Serialize(new
        {
            version = 1, premium_currency_allowed = false,
            budgets = new { coins = Coins, basic_training_tickets = BronzeTickets, fes_participation_rights = FesRights },
            shop = new { enabled = ShopEnabled, purchase_scope = "daily_discount_only", include_third = IncludeThird,
                coin_budget = ShopBudget, coin_reserve = CoinReserve,
                items = Offers.Select(offer => new { offer_rank = offer.Rank, item_id = offer.ItemId,
                    item_type = offer.ItemType, quantity = offer.Quantity, max_unit_price = offer.MaxUnitPrice, currency = "money" }) },
            training = new { enabled = TrainingEnabled, card_id = card, candidate_card_ids = Array.Empty<string>(),
                ticket_type = "basic_training_ticket", ticket_quantity = 1, ticket_budget = TrainingBudget },
            match_fes = new { enabled = MatchEnabled, mode = "skip", stage_selection = LatestStage ? "latest" : "explicit",
                stage_id = stage, count = MatchCount, participation_budget = MatchBudget },
            grade_fes = new { enabled = GradeEnabled, difficulty_id = difficulty, count = 1, participation_budget = GradeBudget },
        }, new JsonSerializerOptions { WriteIndented = true });
    }

    public void SaveNewCopy(string path)
    {
        var json = ToJson();
        using var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        using var writer = new StreamWriter(stream, new System.Text.UTF8Encoding(false));
        writer.Write(json);
    }

    private static string? OptionalIdentity(string value)
    {
        var result = value.Trim();
        if (result.Length == 0) { return null; }
        if (result.ToLowerInvariant() is "auto" or "any" or "random")
        { throw new ArgumentException("身份或难度须为明确值，不能使用 auto/any/random。"); }
        return result;
    }
}

internal sealed record DailyShopPreference(int Rank, string ItemId, string ItemType, int Quantity, int MaxUnitPrice);
