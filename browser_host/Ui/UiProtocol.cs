using System.Globalization;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// The UI panel command whitelist (ui/protocol.json, embedded as SC.UiProtocol.json) plus a small validator for the
/// JSON-Schema subset the protocol uses (type, const, enum, required, properties, additionalProperties, items,
/// maxItems, uniqueItems, pattern, maxLength, minimum, maximum, exclusiveMinimum, local $ref). Anything the
/// validator does not understand fails closed.
/// </summary>
internal sealed class UiProtocol
{
    internal const string ResourceName = "SC.UiProtocol.json";
    internal const string VirtualHost = "maa-ui.test";
    internal const string Origin = "https://maa-ui.test/";
    private static readonly HashSet<string> KnownKeywords = new(StringComparer.Ordinal)
    {
        "type", "const", "enum", "required", "properties", "additionalProperties", "items", "minItems", "maxItems", "uniqueItems",
        "pattern", "maxLength", "minimum", "maximum", "exclusiveMinimum", "$ref", "description",
    };

    private readonly JsonDocument _document;

    private UiProtocol(JsonDocument document)
    {
        _document = document;
        var root = document.RootElement;
        Version = root.GetProperty("protocol_version").GetInt32();
        var source = root.GetProperty("source");
        SourceUri = string.Create(CultureInfo.InvariantCulture,
            $"{source.GetProperty("scheme").GetString()}://{source.GetProperty("host").GetString()}{source.GetProperty("path").GetString()}");
        var limits = root.GetProperty("limits");
        MaxMessageBytes = limits.GetProperty("max_message_bytes").GetInt32();
        MaxMessagesPerSecond = limits.GetProperty("max_messages_per_second").GetInt32();
        Commands = root.GetProperty("commands").EnumerateObject().Select(item => item.Name).ToHashSet(StringComparer.Ordinal);
        Events = root.GetProperty("events").EnumerateObject().Select(item => item.Name).ToHashSet(StringComparer.Ordinal);
        if (Version != 1 || SourceUri != "https://maa-ui.test/index.html" || MaxMessageBytes <= 0 || MaxMessagesPerSecond <= 0)
        { throw new InvalidDataException("界面协议 v1 的来源或限额无效。"); }
    }

    internal int Version { get; }
    internal string SourceUri { get; }
    internal int MaxMessageBytes { get; }
    internal int MaxMessagesPerSecond { get; }
    internal IReadOnlySet<string> Commands { get; }
    internal IReadOnlySet<string> Events { get; }
    internal JsonElement Root => _document.RootElement;

    internal static UiProtocol LoadEmbedded()
    {
        using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(ResourceName)
            ?? throw new InvalidDataException("缺少嵌入的界面协议 " + ResourceName);
        return new UiProtocol(JsonDocument.Parse(stream, new JsonDocumentOptions { MaxDepth = 64 }));
    }

    internal JsonElement CommandParams(string command) => Root.GetProperty("commands").GetProperty(command).GetProperty("params");

    internal JsonElement EnvelopeRequest => Root.GetProperty("envelope").GetProperty("request");

    /// <summary>Validates <paramref name="value"/> against <paramref name="schema"/>; returns the first error or null.</summary>
    internal string? Validate(JsonElement value, JsonElement schema, string path = "$")
    {
        var errors = new List<string>();
        Check(value, schema, path, errors, 0);
        return errors.Count == 0 ? null : errors[0];
    }

    private void Check(JsonElement value, JsonElement schema, string path, List<string> errors, int depth)
    {
        if (errors.Count > 0) { return; }
        if (depth > 32) { errors.Add(path + ": 嵌套过深"); return; }
        if (schema.ValueKind != JsonValueKind.Object) { errors.Add(path + ": 协议 Schema 无效"); return; }
        foreach (var keyword in schema.EnumerateObject())
        {
            if (!KnownKeywords.Contains(keyword.Name)) { errors.Add(path + ": 未支持的 Schema 关键字 " + keyword.Name); return; }
        }
        if (schema.TryGetProperty("$ref", out var reference))
        {
            Check(value, Resolve(reference.GetString()), path, errors, depth + 1);
            return;
        }
        if (value.ValueKind == JsonValueKind.Object && HasDuplicateKeys(value)) { errors.Add(path + ": 重复字段"); return; }
        if (schema.TryGetProperty("type", out var type) && !TypeMatches(value, type)) { errors.Add(path + ": 类型不符"); return; }
        if (schema.TryGetProperty("const", out var constant) && !JsonEquals(value, constant)) { errors.Add(path + ": 值不符"); return; }
        if (schema.TryGetProperty("enum", out var choices) && !choices.EnumerateArray().Any(choice => JsonEquals(value, choice)))
        { errors.Add(path + ": 不在允许值内"); return; }
        switch (value.ValueKind)
        {
            case JsonValueKind.String:
                var text = value.GetString()!;
                if (schema.TryGetProperty("maxLength", out var maxLength) && text.Length > maxLength.GetInt32()) { errors.Add(path + ": 过长"); return; }
                if (schema.TryGetProperty("pattern", out var pattern)
                    && !Regex.IsMatch(text, pattern.GetString()!, RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(100)))
                { errors.Add(path + ": 格式不符"); return; }
                break;
            case JsonValueKind.Number:
                var number = value.GetDouble();
                if (schema.TryGetProperty("minimum", out var minimum) && number < minimum.GetDouble()) { errors.Add(path + ": 过小"); return; }
                if (schema.TryGetProperty("maximum", out var maximum) && number > maximum.GetDouble()) { errors.Add(path + ": 过大"); return; }
                if (schema.TryGetProperty("exclusiveMinimum", out var exclusive) && number <= exclusive.GetDouble()) { errors.Add(path + ": 过小"); return; }
                break;
            case JsonValueKind.Array:
                var items = value.EnumerateArray().ToList();
                if (schema.TryGetProperty("minItems", out var minItems) && items.Count < minItems.GetInt32()) { errors.Add(path + ": 项目过少"); return; }
                if (schema.TryGetProperty("maxItems", out var maxItems) && items.Count > maxItems.GetInt32()) { errors.Add(path + ": 项目过多"); return; }
                if (schema.TryGetProperty("uniqueItems", out var unique) && unique.GetBoolean()
                    && items.Select(item => item.GetRawText()).Distinct(StringComparer.Ordinal).Count() != items.Count)
                { errors.Add(path + ": 项目重复"); return; }
                if (schema.TryGetProperty("items", out var itemSchema))
                {
                    for (var index = 0; index < items.Count; index++) { Check(items[index], itemSchema, $"{path}[{index}]", errors, depth + 1); }
                }
                break;
            case JsonValueKind.Object:
                if (schema.TryGetProperty("required", out var required))
                {
                    foreach (var name in required.EnumerateArray())
                    {
                        if (!value.TryGetProperty(name.GetString()!, out _)) { errors.Add($"{path}: 缺少 {name.GetString()}"); return; }
                    }
                }
                var properties = schema.TryGetProperty("properties", out var declared) ? declared : default;
                var hasAdditional = schema.TryGetProperty("additionalProperties", out var additional);
                foreach (var property in value.EnumerateObject())
                {
                    var child = $"{path}.{property.Name}";
                    if (properties.ValueKind == JsonValueKind.Object && properties.TryGetProperty(property.Name, out var propertySchema))
                    { Check(property.Value, propertySchema, child, errors, depth + 1); }
                    else if (hasAdditional && additional.ValueKind == JsonValueKind.False)
                    { errors.Add(child + ": 不在协议内"); }
                    else if (hasAdditional && additional.ValueKind == JsonValueKind.Object)
                    { Check(property.Value, additional, child, errors, depth + 1); }
                    if (errors.Count > 0) { return; }
                }
                break;
        }
    }

    private JsonElement Resolve(string? reference)
    {
        if (reference is null || !reference.StartsWith("#/", StringComparison.Ordinal)) { throw new InvalidDataException("只支持本地 $ref"); }
        var node = Root;
        foreach (var part in reference[2..].Split('/')) { node = node.GetProperty(part); }
        return node;
    }

    private static bool TypeMatches(JsonElement value, JsonElement type)
    {
        if (type.ValueKind == JsonValueKind.Array) { return type.EnumerateArray().Any(item => TypeMatches(value, item)); }
        return type.GetString() switch
        {
            "object" => value.ValueKind == JsonValueKind.Object,
            "array" => value.ValueKind == JsonValueKind.Array,
            "string" => value.ValueKind == JsonValueKind.String,
            "boolean" => value.ValueKind is JsonValueKind.True or JsonValueKind.False,
            "null" => value.ValueKind == JsonValueKind.Null,
            "number" => value.ValueKind == JsonValueKind.Number,
            "integer" => value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out _),
            _ => false,
        };
    }

    internal static bool HasDuplicateKeys(JsonElement value)
    {
        var seen = new HashSet<string>(StringComparer.Ordinal);
        return value.EnumerateObject().Any(property => !seen.Add(property.Name));
    }

    internal static bool JsonEquals(JsonElement left, JsonElement right)
    {
        if (left.ValueKind != right.ValueKind)
        {
            return left.ValueKind == JsonValueKind.Number && right.ValueKind == JsonValueKind.Number && left.GetDouble() == right.GetDouble();
        }
        return left.ValueKind switch
        {
            JsonValueKind.Number => left.GetDouble() == right.GetDouble(),
            JsonValueKind.String => left.GetString() == right.GetString(),
            JsonValueKind.True or JsonValueKind.False or JsonValueKind.Null => true,
            _ => left.GetRawText() == right.GetRawText(),
        };
    }

    internal static int Utf8Length(string text) => Encoding.UTF8.GetByteCount(text);
}
