using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

internal static class DailySettingsSelfTest
{
    public static void Run()
    {
        var defaults = new DailySettingsConfiguration();
        using var document = JsonDocument.Parse(defaults.ToJson());
        var root = document.RootElement;
        foreach (var task in new[] { "shop", "training", "match_fes", "grade_fes" })
        { Require(!root.GetProperty(task).GetProperty("enabled").GetBoolean(), "default_task_enabled"); }
        Require(root.GetProperty("budgets").EnumerateObject().All(property => property.Value.GetInt32() == 0), "default_budget_nonzero");
        Require(root.GetProperty("training").GetProperty("ticket_quantity").GetInt32() == 1, "bronze_quantity_changed");
        Require(root.GetProperty("match_fes").GetProperty("count").GetInt32() == 20, "match_default_changed");
        Require(!root.GetProperty("premium_currency_allowed").GetBoolean(), "currency_enabled");
        Reject(new() { TrainingEnabled = true });
        Reject(new() { MatchEnabled = true, LatestStage = false });
        Reject(new() { GradeEnabled = true });
        Reject(new() { ShopEnabled = true });
        Reject(new() { Coins = -1 });
        Reject(new() { MatchCount = 0 });
        Reject(new() { StageId = "random", LatestStage = false });
        var third = new DailySettingsConfiguration();
        third.Offers.Add(new(3, "offer-three", "ordinary_item", 1, 100));
        Reject(third);
        var duplicate = new DailySettingsConfiguration();
        duplicate.Offers.Add(new(1, "duplicate", "ordinary_item", 1, 100));
        duplicate.Offers.Add(new(2, "duplicate", "ordinary_item", 1, 100));
        Reject(duplicate);
        var configured = new DailySettingsConfiguration
        {
            ShopEnabled = true, IncludeThird = true, TrainingEnabled = true, CardId = "明确 S 卡",
            MatchEnabled = true, LatestStage = false, StageId = "Area4/Stage12", MatchCount = 20,
            GradeEnabled = true, DifficultyId = "Normal", Coins = 1000, BronzeTickets = 1, FesRights = 21,
            ShopBudget = 300, CoinReserve = 700, TrainingBudget = 1, MatchBudget = 20, GradeBudget = 1,
        };
        for (var rank = 1; rank <= 3; rank++)
        { configured.Offers.Add(new(rank, DailySettingsConfiguration.AnyDailyDiscount, "ordinary_item", 1, 100)); }
        foreach (var bad in new DailyShopPreference[] { new(1, DailySettingsConfiguration.AnyDailyDiscount, "ordinary_item", 2, 10),
            new(1, DailySettingsConfiguration.AnyDailyDiscount, "basic_training_ticket", 1, 10), new(1, DailySettingsConfiguration.AnyDailyDiscount, "ordinary_item", 1, 0) })
        { var rejected = new DailySettingsConfiguration(); rejected.Offers.Add(bad); Reject(rejected); }
        var configuredJson = configured.ToJson();
        var path = Path.Combine(Path.GetTempPath(), $"sc-daily-preferences-selftest-{Guid.NewGuid():N}.json");
        try
        {
            defaults.SaveNewCopy(path);
            var before = File.ReadAllBytes(path);
            try { configured.SaveNewCopy(path); throw new InvalidOperationException("existing_file_overwritten"); }
            catch (IOException) { }
            Require(before.SequenceEqual(File.ReadAllBytes(path)), "existing_file_modified");
        }
        finally { if (File.Exists(path)) { File.Delete(path); } }
        using (var shopDocument = JsonDocument.Parse(configuredJson))
        {
            var items = shopDocument.RootElement.GetProperty("shop").GetProperty("items").EnumerateArray().ToArray();
            Require(items.Length == 3 && items.All(item => item.GetProperty("item_id").GetString() == DailySettingsConfiguration.AnyDailyDiscount
                && item.GetProperty("currency").GetString() == "money" && item.GetProperty("quantity").GetInt32() == 1), "rank_mode_export_changed");
        }
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            result = "passed", defaults = JsonSerializer.Deserialize<JsonElement>(defaults.ToJson()),
            configured = JsonSerializer.Deserialize<JsonElement>(configuredJson),
        }));
    }

    private static void Reject(DailySettingsConfiguration configuration)
    {
        try { _ = configuration.ToJson(); }
        catch (ArgumentException) { return; }
        throw new InvalidOperationException("invalid_preferences_accepted");
    }

    private static void Require(bool condition, string reason)
    { if (!condition) { throw new InvalidOperationException(reason); } }
}
