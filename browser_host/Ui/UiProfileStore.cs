using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// Read-only view of the task schema exported by tools/ui_schema.py (U1 preview: the generated
/// ui/fixtures/schema.json copied next to the executable) plus ui/annotations.json. Used to serve schema.get,
/// to sanitise saved profiles and to re-derive spend flags on the host side.
/// </summary>
internal sealed class UiTaskSchema
{
    private readonly Dictionary<string, TaskInfo> _tasks = new(StringComparer.Ordinal);
    private readonly Dictionary<string, OptionInfo> _options = new(StringComparer.Ordinal);

    internal sealed record TaskInfo(string Name, IReadOnlyList<string> Options, string? SpendKey, string? SpendWhen, string? SpendFlag);
    internal sealed record OptionInfo(string Key, string Type, IReadOnlySet<string> Cases, string? DefaultCase, Regex? Verify = null, bool SingleInput = false)
    {
        internal const int MaxInputLength = 256;

        /// <summary>A single-input option value (e.g. a Produce card id): bounded string that fully matches the PI verify regex.</summary>
        internal bool AcceptsInput(string value) =>
            Type == "input" && SingleInput && value.Length <= MaxInputLength
            && (Verify is null || Verify.IsMatch(value));
    }

    internal UiTaskSchema(string schemaJson, string annotationsJson)
    {
        Schema = JsonNode.Parse(schemaJson)?.AsObject() ?? throw new InvalidDataException("任务 Schema 为空。");
        Annotations = JsonNode.Parse(annotationsJson)?.AsObject() ?? throw new InvalidDataException("界面注释为空。");
        if ((int?)Schema["schema_version"] != 1) { throw new InvalidDataException("任务 Schema 版本不是 1。"); }
        SpendFlags = (Schema["run_config"]?["spend_flags"]?.AsObject() ?? new JsonObject())
            .Select(item => item.Key).ToHashSet(StringComparer.Ordinal);
        foreach (var (key, node) in Schema["options"]!.AsObject())
        {
            var cases = (node!["cases"]?.AsArray() ?? new JsonArray()).Select(item => (string)item!["name"]!).ToHashSet(StringComparer.Ordinal);
            var type = (string)node["type"]!;
            Regex? verify = null;
            var single = false;
            if (type == "input" && node["inputs"] is JsonArray inputs && inputs.Count == 1)
            {
                single = true;
                // PI verify regex, anchored as Python re.fullmatch; ECMAScript keeps \d ASCII-only; bounded match time.
                if ((string?)inputs[0]?["verify"] is { Length: > 0 } pattern)
                { verify = new Regex("^(?:" + pattern + ")$", RegexOptions.ECMAScript | RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(100)); }
            }
            _options[key] = new OptionInfo(key, type, cases, (string?)node["default_case"], verify, single);
        }
        foreach (var node in Schema["tasks"]!.AsArray())
        {
            var name = (string)node!["name"]!;
            var options = node["options"]!.AsArray().Select(item => (string)item!).ToList();
            var spend = node["ui"]?["spend_option"];
            _tasks[name] = new TaskInfo(name, options, (string?)spend?["key"], (string?)spend?["when"], (string?)node["ui"]?["spend_flag"]);
        }
        Presets = Schema["presets"]!.AsArray().Select(item => (string)item!["name"]!).ToHashSet(StringComparer.Ordinal);
    }

    internal JsonObject Schema { get; }
    internal JsonObject Annotations { get; }
    internal IReadOnlySet<string> SpendFlags { get; }
    internal IReadOnlySet<string> Presets { get; }
    internal IReadOnlyDictionary<string, TaskInfo> Tasks => _tasks;
    internal IReadOnlyDictionary<string, OptionInfo> Options => _options;

    internal static UiTaskSchema LoadFromUiFolder(string uiFolder) => new(
        File.ReadAllText(Path.Combine(uiFolder, "fixtures", "schema.json"), Encoding.UTF8),
        File.ReadAllText(Path.Combine(uiFolder, "annotations.json"), Encoding.UTF8));

    /// <summary>
    /// Host-side mirror of tools/ui_schema._profile_spends: an enabled task whose explicit spend option (schema default
    /// when unset) holds the consenting case. Stricter than the page: a task must also be enabled in the run request
    /// when <paramref name="requestEnabled"/> is given. Page-supplied spend flags are never an input.
    /// </summary>
    /// <param name="savedEnabledRequired">false only for a task that runs from its own tab (W.I.N.G. on 「Produce」, 2026-10-13):
    /// its saved row is unticked so the daily queue never runs it, while its saved spend option still decides.</param>
    internal IReadOnlyList<string> DeriveSpendFlags(JsonObject sanitizedProfile, IReadOnlySet<string>? requestEnabled = null, bool savedEnabledRequired = true)
    {
        var flags = new SortedSet<string>(StringComparer.Ordinal);
        foreach (var node in sanitizedProfile["tasks"]?.AsArray() ?? new JsonArray())
        {
            var name = (string?)node?["name"];
            if (name is null || !_tasks.TryGetValue(name, out var task)) { continue; }
            if (savedEnabledRequired && node!["enabled"]?.GetValue<bool>() != true) { continue; }
            if (requestEnabled is not null && !requestEnabled.Contains(name)) { continue; }
            if (task.SpendKey is null || task.SpendFlag is null || !SpendFlags.Contains(task.SpendFlag)) { continue; }
            if (!_options.TryGetValue(task.SpendKey, out var option)) { continue; }
            var value = (string?)node!["option"]?[task.SpendKey] ?? option.DefaultCase;
            if (value == task.SpendWhen) { flags.Add(task.SpendFlag); }
        }
        return flags.ToList();
    }
}

/// <summary>
/// dev_assets/private/ui/profile.json: task order / enabled flags / option cases, UI prefs and daily manual checks.
/// Input already passed the protocol schema (unknown fields rejected); this class additionally keeps only known tasks,
/// options and cases, so no free-form string (token, path, spend authorisation) can be stored. Writes are atomic.
/// </summary>
internal sealed partial class UiProfileStore
{
    internal const int MaxDailyDays = 31;
    internal const int MaxDailyItems = 32;
    internal const int MaxNotifyKeys = 16;
    internal const int MaxOwnedCards = 2000;
    internal const int MaxOwnedDetails = 1000;   // per-card Lv/凸 entries kept (profile.save must also fit the 64 KiB message cap)
    internal const int MaxCardLevel = 100;       // same bound as ui/protocol.json prefs.owned_cards.details
    internal const int MaxRemovedTasks = 64;     // same bound as ui/protocol.json profile.removed_tasks
    internal const int MaxKnowhowWanted = 64;    // same bound as ui/protocol.json prefs.knowhow_wanted
    private readonly UiTaskSchema _schema;

    internal UiProfileStore(string path, UiTaskSchema schema)
    {
        Path = System.IO.Path.GetFullPath(path);
        _schema = schema;
    }

    internal string Path { get; }

    [GeneratedRegex("^[0-9]{4}-[0-9]{2}-[0-9]{2}$")] private static partial Regex DayKey();
    [GeneratedRegex("^[A-Za-z0-9_.-]{1,64}$")] private static partial Regex ItemKey();
    [GeneratedRegex("^[a-z][a-z0-9_]{0,31}$")] private static partial Regex NotifyKey();
    [GeneratedRegex("^[^\\u0000-\\u001f/\\\\:<>\"'`]{1,40}$")] private static partial Regex KnowhowName();
    [GeneratedRegex("^[^\\u0000-\\u001f/\\\\:<>\"'`]{1,60}$")] private static partial Regex KnowhowTitle();

    internal JsonObject Load()
    {
        try
        {
            if (!File.Exists(Path)) { return Empty(); }
            var info = new FileInfo(Path);
            if (info.Length > 256 * 1024) { return Empty(); }
            return Sanitize(JsonNode.Parse(File.ReadAllText(Path, Encoding.UTF8)));
        }
        catch (Exception exception) when (exception is JsonException or IOException or InvalidOperationException or UnauthorizedAccessException)
        {
            return Empty();
        }
    }

    internal JsonObject Save(JsonNode? profile)
    {
        var clean = Sanitize(profile);
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
        var temp = Path + ".tmp-" + Environment.ProcessId.ToString(System.Globalization.CultureInfo.InvariantCulture);
        File.WriteAllText(temp, clean.ToJsonString(new JsonSerializerOptions(UiMessageRouter.JsonOptions) { WriteIndented = true }) + "\n", new UTF8Encoding(false));
        File.Move(temp, Path, overwrite: true);
        return clean;
    }

    internal static JsonObject Empty() => new() { ["schema_version"] = 1 };

    /// <summary>Owned-card modelling snapshot only: ids and integer Lv/LB. No other preference or authorisation.</summary>
    internal static JsonObject SanitizeOwnedCards(JsonObject owned)
    {
        // 持有卡牌 (modelling data only): wiki card ids, P = 1xxxxxxxxx, S = 2xxxxxxxxx; unique, sorted, capped.
        var cards = new JsonObject();
        foreach (var (kind, lead) in new[] { ("p", '1'), ("s", '2') })
        {
            var ids = (owned[kind] as JsonArray ?? new JsonArray())
                .Select(node => node is JsonValue v && v.TryGetValue<string>(out var id) ? id : null)
                .Where(id => id is { Length: 10 } && id[0] == lead && id.All(char.IsAsciiDigit))
                .Distinct(StringComparer.Ordinal).OrderBy(id => id, StringComparer.Ordinal).Take(MaxOwnedCards)
                .Select(id => (JsonNode)JsonValue.Create(id)!).ToArray();
            cards[kind] = new JsonArray(ids);
        }
        // Optional per-card Lv / 凸 (recommendation and Wing scoring): keys must be in the kept p/s lists,
        // level 1..100, limit_break 0..4, integers only; a missing field means unknown, empty entries are dropped.
        if (owned["details"] is JsonObject details)
        {
            var known = new HashSet<string>(StringComparer.Ordinal);
            foreach (var kind in new[] { "p", "s" })
            {
                foreach (var node in cards[kind]!.AsArray()) { known.Add((string)node!); }
            }
            var kept = new SortedDictionary<string, JsonObject>(StringComparer.Ordinal);
            foreach (var (id, value) in details)
            {
                if (kept.Count >= MaxOwnedDetails || !known.Contains(id) || value is not JsonObject entry) { continue; }
                var item = new JsonObject();
                if (TryInt(entry["level"]) is { } level && level is >= 1 and <= MaxCardLevel) { item["level"] = level; }
                if (TryInt(entry["limit_break"]) is { } lb && lb is >= 0 and <= 4) { item["limit_break"] = lb; }
                if (item.Count > 0) { kept[id] = item; }
            }
            if (kept.Count > 0)
            {
                var detailNode = new JsonObject();
                foreach (var (id, item) in kept) { detailNode[id] = item; }
                cards["details"] = detailNode;
            }
        }
        return cards;
    }

    private static JsonArray? SanitizeStepBooks(JsonArray books, int count = 3)
    {
        if (books.Count != count) { return null; }
        var list = new JsonArray();
        foreach (var node in books)
        {
            if (node is not JsonObject book || book["title"] is not JsonValue titleValue || !titleValue.TryGetValue<string>(out var rawTitle)) { return null; }
            var title = string.Concat(rawTitle.Normalize(NormalizationForm.FormKC).Where(c => !char.IsWhiteSpace(c)));
            if (!KnowhowTitle().IsMatch(title)) { return null; }
            var entry = new JsonObject { ["title"] = title };
            foreach (var key in new[] { "vo", "da", "vi", "me", "eval" })
            {
                if (TryInt(book[key]) is not { } value || value is < 0 or > 2000) { return null; }
                entry[key] = value;
            }
            list.Add(entry);
        }
        return list;
    }

    internal JsonObject Sanitize(JsonNode? input)
    {
        var output = Empty();
        if (input is not JsonObject source || (int?)TryInt(source["schema_version"]) != 1) { return output; }
        if (source["preset"] is JsonValue presetValue && presetValue.TryGetValue<string>(out var preset) && _schema.Presets.Contains(preset))
        { output["preset"] = preset; }
        else if (source.ContainsKey("preset")) { output["preset"] = null; }

        // removed_tasks: tasks outside the user's 方案. Known schema task names only, unique, capped; their saved
        // entries are forced to enabled=false so they never run or consent to a spend (DeriveSpendFlags reads enabled).
        var removed = new HashSet<string>(StringComparer.Ordinal);
        if (source["removed_tasks"] is JsonArray removedNodes)
        {
            var list = new JsonArray();
            foreach (var node in removedNodes)
            {
                if (removed.Count >= MaxRemovedTasks) { break; }
                if (node is JsonValue value && value.TryGetValue<string>(out var name) && _schema.Tasks.ContainsKey(name) && removed.Add(name))
                { list.Add(name); }
            }
            output["removed_tasks"] = list;
        }

        if (source["tasks"] is JsonArray tasks)
        {
            var seen = new HashSet<string>(StringComparer.Ordinal);
            var clean = new JsonArray();
            foreach (var node in tasks)
            {
                if (node is not JsonObject item || item["name"] is not JsonValue nameValue || !nameValue.TryGetValue<string>(out var name)) { continue; }
                if (!_schema.Tasks.TryGetValue(name, out var task) || !seen.Add(name)) { continue; }
                var entry = new JsonObject { ["name"] = name, ["enabled"] = !removed.Contains(name) && item["enabled"] is JsonValue enabled && enabled.TryGetValue<bool>(out var on) && on };
                if (item["option"] is JsonObject options)
                {
                    var option = new JsonObject();
                    foreach (var (key, value) in options)
                    {
                        if (!task.Options.Contains(key) || !_schema.Options.TryGetValue(key, out var spec)) { continue; }
                        if (value is not JsonValue caseValue || !caseValue.TryGetValue<string>(out var chosen)) { continue; }
                        if (spec.Type is "select" or "switch" ? spec.Cases.Contains(chosen) : spec.AcceptsInput(chosen)) { option[key] = chosen; }
                    }
                    if (option.Count > 0) { entry["option"] = option; }
                }
                clean.Add(entry);
            }
            output["tasks"] = clean;
        }

        if (source["prefs"] is JsonObject prefs)
        {
            var clean = new JsonObject();
            if (prefs["theme"] is JsonValue theme && theme.TryGetValue<string>(out var themeName) && themeName is "system" or "light" or "dark")
            { clean["theme"] = themeName; }
            if (prefs["developer"] is JsonValue developer && developer.TryGetValue<bool>(out var dev)) { clean["developer"] = dev; }
            // 设置 → 更新: which GitHub releases count as an update (display preference only).
            if (prefs["update_channel"] is JsonValue channel && channel.TryGetValue<string>(out var channelName) && channelName is "stable" or "beta")
            { clean["update_channel"] = channelName; }
            // 设置 → 外观: display preferences only.
            if (prefs["accent"] is JsonValue accent && accent.TryGetValue<string>(out var accentName) && accentName is "pink" or "sky" or "violet" or "teal")
            { clean["accent"] = accentName; }
            if (prefs["font_size"] is JsonValue font && font.TryGetValue<string>(out var fontName) && fontName is "small" or "normal" or "large" or "xlarge")
            { clean["font_size"] = fontName; }
            if (prefs["density"] is JsonValue density && density.TryGetValue<string>(out var densityName) && densityName is "normal" or "compact")
            { clean["density"] = densityName; }
            if (prefs["reduce_motion"] is JsonValue motion && motion.TryGetValue<bool>(out var reduce)) { clean["reduce_motion"] = reduce; }
            if (prefs["hide_chibi_visitor"] is JsonValue visitor && visitor.TryGetValue<bool>(out var hideVisitor)) { clean["hide_chibi_visitor"] = hideVisitor; }
            if (prefs["reduce_chibi_motion"] is JsonValue visitorMotion && visitorMotion.TryGetValue<bool>(out var reduceVisitor)) { clean["reduce_chibi_motion"] = reduceVisitor; }
            if (prefs["owned_cards"] is JsonObject owned)
            {
                clean["owned_cards"] = SanitizeOwnedCards(owned);
            }
            if (prefs["knowhow_wanted"] is JsonArray wanted)
            {
                // ノウハウ page wanted list (display/match preference only): bounded names without control, path or quote
                // characters; known enum values only; unique by (name, match, purpose); capped. Never an authorisation.
                var list = new JsonArray();
                var keys = new HashSet<string>(StringComparer.Ordinal);
                foreach (var node in wanted)
                {
                    if (list.Count >= MaxKnowhowWanted) { break; }
                    if (node is not JsonObject item || item["name"] is not JsonValue nameValue || !nameValue.TryGetValue<string>(out var rawName)) { continue; }
                    var name = rawName.Normalize(NormalizationForm.FormKC);
                    name = string.Concat(name.Where(c => !char.IsWhiteSpace(c)));
                    if (!KnowhowName().IsMatch(name)) { continue; }
                    var match = item["match"] is JsonValue m && m.TryGetValue<string>(out var mv) && mv is "exact" or "prefix" or "contains" ? mv : "exact";
                    var purpose = item["purpose"] is JsonValue p && p.TryGetValue<string>(out var pv) && pv is "halo" or "step" or "any" ? pv : "any";
                    if (!keys.Add(name + "|" + match + "|" + purpose)) { continue; }
                    var entry = new JsonObject { ["name"] = name, ["match"] = match, ["min_level"] = TryInt(item["min_level"]) is { } lv && lv is >= 1 and <= 6 ? lv : 1, ["purpose"] = purpose };
                    if (item["position"] is JsonValue pos && pos.TryGetValue<string>(out var position) && position is "center" or "leader" or "vocal" or "dance" or "visual")
                    { entry["position"] = position; }
                    list.Add(entry);
                }
                clean["knowhow_wanted"] = list;
            }
            if (prefs["auto_step_books"] is JsonArray books && SanitizeStepBooks(books) is { } stepBooks)
            {
                // ノウハウ page 「设为 Auto 带书」: exactly three books (title + Vo/Da/Vi/Me + 評価値) the Auto picker looks for.
                clean["auto_step_books"] = stepBooks;
            }
            if (prefs["halo_book"] is JsonArray halo && SanitizeStepBooks(halo, 1) is { } haloBook)
            {
                // ノウハウ page 「设为 Halo 带书」: one book (same shape) the say "Halo" picker looks for.
                clean["halo_book"] = haloBook;
            }
            if (prefs["notify"] is JsonObject notify)
            {
                var flags = new JsonObject();
                foreach (var (key, value) in notify)
                {
                    if (flags.Count >= MaxNotifyKeys || !NotifyKey().IsMatch(key)) { continue; }
                    if (value is JsonValue flag && flag.TryGetValue<bool>(out var enabled)) { flags[key] = enabled; }
                }
                clean["notify"] = flags;
            }
            output["prefs"] = clean;
        }

        if (source["daily_manual"] is JsonObject daily)
        {
            var clean = new JsonObject();
            foreach (var (day, node) in daily.Where(item => DayKey().IsMatch(item.Key)).OrderByDescending(item => item.Key, StringComparer.Ordinal).Take(MaxDailyDays))
            {
                if (node is not JsonObject items) { continue; }
                var dayItems = new JsonObject();
                foreach (var (key, value) in items)
                {
                    if (dayItems.Count >= MaxDailyItems || !ItemKey().IsMatch(key)) { continue; }
                    if (value is JsonValue flag && flag.TryGetValue<bool>(out var done)) { dayItems[key] = done; }
                }
                clean[day] = dayItems;
            }
            output["daily_manual"] = clean;
        }
        return output;
    }

    private static int? TryInt(JsonNode? node)
    {
        if (node is not JsonValue value) { return null; }
        if (value.TryGetValue<int>(out var integer)) { return integer; }
        if (value.TryGetValue<JsonElement>(out var element) && element.ValueKind == JsonValueKind.Number && element.TryGetInt32(out var parsed)) { return parsed; }
        return null;
    }
}
