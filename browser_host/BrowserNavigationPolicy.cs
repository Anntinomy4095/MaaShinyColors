namespace MaaShinyColors.BrowserHost;

/// <summary>Manual browsing includes HTTPS login pages; game automation has its own game-only policy.</summary>
internal static class BrowserNavigationPolicy
{
    internal static bool CanBrowse(string raw) => Uri.TryCreate(raw, UriKind.Absolute, out var uri) &&
        (uri.Scheme == Uri.UriSchemeHttps || uri.AbsoluteUri == "about:blank");
}
