"""UI overhaul U0/U0.1/U0.2: static safety guards for the panel front-end under ui/ (no network, no dynamic code, no HTML
injection, strict CSP, whitelisted protocol, complete design tokens with WCAG AA contrast, no generic decoration)."""
import json
import re
import shutil
import struct
import subprocess
import unittest
from pathlib import Path

from tools import run_tasks

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
APP_ICON = "icon.png"  # the one image in ui/: the app icon (tools/make_app_icon.py)
GAME_ASSETS = UI / "game-assets"  # local crops from tools/ui_game_assets.py; gitignored, never shipped or committed
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".ico"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; "
       "connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'")
WHITELIST = {"ui.ready", "schema.get", "profile.get", "profile.save", "run.start", "run.stop",
             "status.subscribe", "log.subscribe",
             "policy.get",  # read-only display of project_policy.json for the 安全 tab (never a grant)
             # 设置 → 开发者「重新载入动作表」: host re-validates pipeline_actions.json and swaps it only when idle.
             "dev.action_table",
             # 设置 → 关于数据 / 静音游戏: host clears WebView2 cache or login data / mutes; needs a newer host.
             "game.data.clear", "game.audio.set",
             # 设置 → 关于 / 问题反馈 / 更新 / 通知测试 / 紧急停止快捷键: the host owns every path, URL and file list.
             "app.support", "window.layout",
             # 设置 → 窗口: 后台运行 switch (host setting, default off since 2026-10-13) and 现在隐藏; window handling only.
             "window.background",
             # 设置 → 存储: sizes of dev_assets/private by category, 清理 (confirmed:true) and 自动清理 (host setting).
             "storage.status", "storage.clean", "storage.auto",
             # 资源 → 扫描持有卡 (host-launched supervised CardScan): start needs confirmed:true.
             "cards.scan.start", "cards.scan.stop", "cards.scan.status",
             # 资源 → 更新卡资料 (2026-10-18~): host runs the fixed incremental public-data tool; no game connection, no owned-card writes.
             "cards.library.update", "cards.library.stop",
             # ノウハウ → 扫描ノウハウ (host-launched supervised KnowhowScan): confirmed:true + mode enum only.
             "knowhow.scan.start", "knowhow.scan.stop", "knowhow.scan.status",
             # 任务 → W.I.N.G. 编队「按当前 P 重新生成」 (U05, 2026-10-12): offline recommender over local files only.
             "team.propose"}
SVG_NAMESPACE = "http://www.w3.org/2000/svg"  # XML namespace identifier, never fetched
TOKENS = {
    # 2026-10-09: Shiny Colors x minimal. --sc-bg / --sc-canvas / --sc-border / --sc-text(-2) /
    # --sc-warning / --sc-surface stay equal to the host chrome palette (test_ui_host_guards); the panel body uses --sc-paper.
    "--sc-bg": ("#FAF8F4", "#17161B"),
    "--sc-paper": ("#F5F1F6", "#19161D"),
    "--sc-surface": ("#FFFFFF", "#201F25"),
    "--sc-surface-2": ("#F3EEF4", "#2C2632"),
    "--sc-border": ("#E6E2DA", "#34323A"),
    "--sc-control": ("#8E7E96", "#8A7C92"),
    "--sc-text": ("#24232B", "#ECEAE6"),
    "--sc-text-2": ("#6E6B76", "#A7A3AD"),
    "--sc-primary": ("#E0438A", "#F27BB0"),
    "--sc-primary-strong": ("#C42876", "#F27BB0"),
    "--sc-primary-text": ("#B42A68", "#F59CC4"),
    "--sc-primary-tint": ("#FDEAF3", "#3B2132"),
    "--sc-on-primary": ("#FFFFFF", "#1B1220"),
    "--sc-accent": ("#D96A24", "#F0975A"),
    "--sc-accent-text": ("#A34C17", "#F4AE7C"),
    "--sc-success": ("#2F8E6B", "#5DB38C"),
    "--sc-warning": ("#B07A2A", "#D1A158"),
    "--sc-danger": ("#C4403C", "#E5807A"),
    "--sc-danger-strong": ("#B23733", "#E5807A"),
    "--sc-on-danger": ("#FFFFFF", "#1B1220"),
    "--sc-uncertain": ("#7A62C0", "#A796DE"),
    "--sc-muted": ("#8A8496", "#827B90"),
    "--sc-canvas": ("#F3F1EC", "#1C1B20"),
    "--sc-ribbon": ("#6A5D6E", "#CFC3D3"),
    "--sc-on-ribbon": ("#FFFFFF", "#1B1722"),
}
SHARED_TOKENS = ["--r-s", "--r-m", "--shadow-pop", "--font-heading", "--font-ja"]
BACKGROUNDS = ("--sc-bg", "--sc-paper", "--sc-surface")
# WCAG 2.1 AA: text pairs need >= 4.5:1; non-text UI parts (focus ring, control borders, status dots) >= 3:1.
TEXT_PAIRS = [(fg, bg) for fg in ("--sc-text", "--sc-text-2") for bg in ("--sc-bg", "--sc-paper", "--sc-surface", "--sc-surface-2", "--sc-canvas")] + [
    ("--sc-on-ribbon", "--sc-ribbon"),  # ribbons, fact labels, the reset pill
    ("--sc-primary-text", "--sc-paper"), ("--sc-accent-text", "--sc-paper"),
    ("--sc-text", "--sc-primary-tint"),
    ("--sc-primary-text", "--sc-surface"), ("--sc-primary-text", "--sc-bg"), ("--sc-primary-text", "--sc-primary-tint"),
    ("--sc-accent-text", "--sc-surface"), ("--sc-accent-text", "--sc-bg"),
    ("--sc-danger", "--sc-surface"), ("--sc-danger", "--sc-bg"),  # stop buttons use danger as text
    ("--sc-on-primary", "--sc-primary-strong"),
    ("--sc-on-danger", "--sc-danger-strong"),
    ("--sc-on-danger", "--sc-accent-text"),  # tab attention badge
]
NON_TEXT_PAIRS = [(fg, bg) for fg in ("--sc-primary", "--sc-control", "--sc-success", "--sc-warning", "--sc-danger",
                                      "--sc-uncertain", "--sc-muted", "--sc-accent") for bg in BACKGROUNDS] + [
    (fg, bg) for fg in ("--rar-ur", "--rar-ssr", "--rar-sr", "--rar-r") for bg in BACKGROUNDS]  # 资源 tab card frames


def relative_luminance(hex_color):
    channels = [int(hex_color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a, b):
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ui_files(*suffixes):
    return sorted(p for p in UI.rglob("*") if p.is_file() and p.suffix in suffixes and GAME_ASSETS not in p.parents)


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)


def css_block(text, selector):
    start = text.index(selector + " {")
    depth, i = 0, text.index("{", start)
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i + 1:j]
    raise AssertionError(selector)


def declarations(block):
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", block)}


class UiStaticGuardTests(unittest.TestCase):
    def test_expected_files_exist(self):
        for rel in ("index.html", "app.js", "protocol.json", "protocol.js", "annotations.json", "styles/tokens.css",
                    "styles/app.css", "fixtures/demo-state.json", "fixtures/schema.json", "fixtures/mock-bundle.js",
                    "icon.png", "components/logo.js"):
            self.assertTrue((UI / rel).is_file(), rel)
        self.assertTrue(list((UI / "components").glob("*.js")))

    def test_no_external_urls(self):
        for path in ui_files(".html", ".js", ".css", ".json", ".svg", ".md"):
            text = path.read_text(encoding="utf-8").replace(SVG_NAMESPACE, "")
            self.assertIsNone(re.search(r"https?://", text), path)
            self.assertNotRegex(text, r"(?<![\w.])//[a-z0-9-]+\.[a-z]{2,}/", path)
        for path in ui_files(".css"):
            for url in re.findall(r"url\(\s*['\"]?([^'\")]+)", path.read_text(encoding="utf-8")):
                self.assertTrue(url.startswith(("../", "./")) and ":" not in url, (path, url))

    def test_no_dynamic_code_or_html_injection(self):
        forbidden = [
            (r"\beval\s*\(", "eval"),
            (r"\bnew\s+Function\b", "new Function"),
            (r"\bFunction\s*\(", "Function()"),
            (r"setTimeout\s*\(\s*['\"`]", "string setTimeout"),
            (r"setInterval\s*\(\s*['\"`]", "string setInterval"),
            (r"\.innerHTML\b", "innerHTML"),
            (r"\.outerHTML\s*=", "outerHTML assignment"),
            (r"insertAdjacentHTML", "insertAdjacentHTML"),
            (r"document\.write", "document.write"),
            (r"createContextualFragment", "createContextualFragment"),
            (r"DOMParser", "DOMParser"),
            (r"\bfetch\s*\(", "fetch"),
            (r"XMLHttpRequest|WebSocket|EventSource|sendBeacon", "network API"),
            (r"setAttribute\(\s*['\"]style['\"]", "inline style attribute"),
            (r"setAttribute\(\s*['\"]on\w+", "inline handler attribute"),
            (r"\.cookie\b", "cookie access"),
        ]
        for path in ui_files(".js"):
            text = path.read_text(encoding="utf-8")
            for pattern, name in forbidden:
                self.assertIsNone(re.search(pattern, text), f"{path.relative_to(ROOT)}: {name}")

    def test_index_has_exact_csp_and_only_local_assets(self):
        html = (UI / "index.html").read_text(encoding="utf-8")
        csp = re.findall(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', html)
        self.assertEqual(csp, [CSP])
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)[^>]*>")  # no inline script
        self.assertNotIn("<style", html)
        self.assertNotRegex(html, r"\sstyle=")
        self.assertNotRegex(html, r"\son\w+=")
        for ref in re.findall(r'(?:src|href)="([^"]+)"', html):
            self.assertTrue((UI / ref.split("?", 1)[0]).is_file(), ref)

    def test_no_inline_style_or_handlers_in_any_html(self):
        for path in ui_files(".html"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("<style", text, path)
            self.assertNotRegex(text, r"\son\w+=", path)

    def test_protocol_commands_are_the_whitelist_with_schemas(self):
        protocol = json.loads((UI / "protocol.json").read_text(encoding="utf-8"))
        self.assertEqual(protocol["protocol_version"], 1)
        self.assertEqual(protocol["source"], {"scheme": "https", "host": "maa-ui.test", "path": "/index.html"})
        self.assertEqual(set(protocol["commands"]), WHITELIST)
        for name, spec in protocol["commands"].items():
            for part in ("params", "result"):
                self.assertEqual(spec[part].get("type"), "object", f"{name}.{part}")
            self.assertIs(spec["params"].get("additionalProperties"), False, name)
            self.assertTrue(spec.get("description"), name)
        start = protocol["commands"]["run.start"]["params"]["properties"]
        # 2026-10-11: supervised_wing = the CLI's supervised Wing segment, only with the page's confirmed:true; the
        # produce spend flag is forwarded only when the host re-derives it from the saved profile (tools/ui_wing.py).
        self.assertEqual(start["mode"]["enum"], ["read_only", "offline_replay", "supervised_wing", "supervised_daily"])
        self.assertEqual(start["confirmed"]["type"], "boolean")
        self.assertEqual(set(start["spend_flags"]["items"]["enum"]), set(run_tasks.SPEND_FLAGS))
        self.assertNotIn("spend_consent", start)
        prefs = protocol["definitions"]["profile"]["properties"]["prefs"]["properties"]
        # 2026-10-15/16: update_channel (设置 → 更新) and the 外观 display prefs; the host sanitiser keeps exactly these enums.
        # 2026-10-31: auto_step_books (ノウハウ page 「设为 Auto 带书」), three books the Auto picker looks for.
        # 2026-11-15: halo_book (「设为 Halo 带书」), one book for the say "Halo" picker.
        self.assertEqual(set(prefs), {"theme", "notify", "developer", "owned_cards", "knowhow_wanted", "update_channel",
                                      "accent", "font_size", "density", "reduce_motion", "auto_step_books",
                                      "halo_book", "hide_chibi_visitor", "reduce_chibi_motion"})
        store_cs = (ROOT / "browser_host" / "Ui" / "UiProfileStore.cs").read_text(encoding="utf-8")
        for key in ("update_channel", "accent", "font_size", "density"):
            values = " or ".join(f'"{v}"' for v in prefs[key]["enum"])
            self.assertIn(f'is {values})', store_cs, key)
        self.assertIn('prefs["reduce_motion"]', store_cs)
        owned = prefs["owned_cards"]
        self.assertIs(owned["additionalProperties"], False)
        self.assertEqual(owned["properties"]["p"]["items"]["pattern"], "^1[0-9]{9}$")
        self.assertEqual(owned["properties"]["s"]["items"]["pattern"], "^2[0-9]{9}$")
        self.assertTrue(all(owned["properties"][k]["maxItems"] == 2000 and owned["properties"][k]["uniqueItems"] for k in "ps"))
        self.assertEqual(prefs["developer"]["type"], "boolean")
        self.assertNotIn("allow_spend", protocol["definitions"]["run_config"]["properties"])
        self.assertNotIn("input_enabled", protocol["definitions"]["run_config"]["properties"])
        self.assertLessEqual(set(protocol["definitions"]["run_config"]["properties"]), run_tasks.RUN_CONFIG_KEYS)
        profile = protocol["definitions"]["profile"]["properties"]
        for word in ("spend", "live", "input", "token", "cookie", "session"):
            self.assertFalse([k for k in profile if word in k], word)
        statuses = protocol["definitions"]["task_status"]["enum"]
        self.assertEqual(set(statuses), {"pending", "running", "succeeded", "skipped", "failed", "uncertain",
                                         "guard_refused", "takeover"})

    def test_page_only_sends_whitelisted_commands(self):
        used = set()
        for path in ui_files(".js"):
            used |= set(re.findall(r"\.request\(\s*\"([\w.]+)\"", path.read_text(encoding="utf-8")))
        self.assertTrue(used)
        self.assertLessEqual(used, WHITELIST)

    def test_panel_scroll_padding_is_symmetric_with_the_scrollbar_gutter(self):
        # host-20261011d: the reserved scrollbar gutter made the right side ~2x the left. The gutter is now a fixed
        # 8 px CSS scrollbar (zooms with the page) and the right padding gives that width back.
        css = (ROOT / "ui" / "styles" / "app.css").read_text(encoding="utf-8")
        rule = css[css.index(".panel-scroll {"):]
        rule = rule[: rule.index("}")]
        self.assertIn("--scrollbar-w: 8px;", rule)
        self.assertIn("scrollbar-gutter: stable;", rule)
        self.assertIn("padding: var(--sp-2) calc(var(--sp-4) - var(--scrollbar-w)) var(--sp-4) var(--sp-4);", rule)
        self.assertIn(".panel-scroll::-webkit-scrollbar { width: 8px; }", css)

    def test_tokens_define_plan_values_for_both_themes(self):
        css = (UI / "styles" / "tokens.css").read_text(encoding="utf-8")
        light = declarations(css_block(css, ":root"))
        media = css[css.index("@media (prefers-color-scheme: dark)"):]
        dark_system = declarations(css_block(media, ':root:not([data-theme="light"])'))
        dark_forced = declarations(css_block(css, ':root[data-theme="dark"]'))
        for token, (light_value, dark_value) in TOKENS.items():
            self.assertEqual(light.get(token, "").upper(), light_value.upper(), token)
            self.assertEqual(dark_system.get(token, "").upper(), dark_value.upper(), token)
            self.assertEqual(dark_forced.get(token, "").upper(), dark_value.upper(), token)
        for token in SHARED_TOKENS:
            self.assertIn(token, light)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("prefers-reduced-motion", (UI / "styles" / "app.css").read_text(encoding="utf-8"))

    def theme_tokens(self):
        css = (UI / "styles" / "tokens.css").read_text(encoding="utf-8")
        light = declarations(css_block(css, ":root"))
        dark = declarations(css_block(css, ':root[data-theme="dark"]'))
        return light, {**light, **dark}

    def test_token_colours_meet_wcag_aa_in_both_themes(self):
        for theme, tokens in zip(("light", "dark"), self.theme_tokens()):
            for pairs, minimum in ((TEXT_PAIRS, 4.5), (NON_TEXT_PAIRS, 3.0)):
                for fg, bg in pairs:
                    ratio = contrast(tokens[fg], tokens[bg])
                    self.assertGreaterEqual(ratio, minimum, f"{theme}: {fg} on {bg} = {ratio:.2f}")

    def test_decoration_comes_only_from_tokens(self):
        """2026-10-09: the Shiny Colors look (gradients, rounded panels, soft shadows, the
        one sparkle ornament) is allowed, but only through tokens.css, so it stays restrained and themeable: no literal
        gradients / large radii / ad-hoc shadows in component CSS, no glass, glow or looping animation, at most a few
        sparkle ornaments."""
        tokens_css = (UI / "styles" / "tokens.css").read_text(encoding="utf-8")
        components = [p for p in ui_files(".css") if p.name != "tokens.css"]
        css = "\n".join(p.read_text(encoding="utf-8") for p in components)
        # Character movement in the requested office toy is gameplay, not panel decoration.
        panel_css = "\n".join(p.read_text(encoding="utf-8") for p in components if p.name != "office-eggs.css")
        for pattern, name in [
            (r"gradient\(", "literal gradient outside tokens.css"),
            (r"backdrop-filter|blur\(", "glass / blur"),
            (r"drop-shadow", "glow"),
            (r"\binfinite\b", "looping (breathing) animation"),
            (r"border-radius:\s*(?:[7-9]|\d\d+)px", "literal large radius (use --r-l / --r-pill)"),
            (r"text-shadow", "text glow"),
        ]:
            self.assertIsNone(re.search(pattern, panel_css if name == "looping (breathing) animation" else css), name)
        for value in re.findall(r"box-shadow:\s*([^;]+);", css):
            self.assertTrue(value.startswith(("var(--shadow-", "inset", "none")), value)
        for value in re.findall(r"background(?:-image)?:\s*([^;]+);", css):
            self.assertNotIn("url(", value)  # no game art / pictures as backgrounds
        self.assertIn("--grad-primary", tokens_css)
        self.assertLessEqual(css.count('content: "\\2726"'), 3)  # ✦: fold titles, ribbons are JS text, the home hero
        self.assertNotIn("header-grad", css)

    def test_accent_presets_meet_wcag_aa(self):
        """设置 → 外观 主题色: every accent keeps the AA pairs and readable primary buttons in both themes, and
        the light / system-dark / forced-dark blocks agree."""
        css = (UI / "styles" / "tokens.css").read_text(encoding="utf-8")
        base_light, base_dark = self.theme_tokens()
        accents = re.findall(r':root\[data-accent="(\w+)"\] \{', css)
        self.assertEqual(accents, ["sky", "violet", "teal"])
        for accent in accents:
            light = {**base_light, **declarations(css_block(css, f':root[data-accent="{accent}"]'))}
            forced = declarations(css_block(css, f':root[data-theme="dark"][data-accent="{accent}"]'))
            system = declarations(css_block(css, f':root[data-accent="{accent}"]:not([data-theme="light"])'))
            self.assertEqual(forced, system, accent)
            self.assertEqual(set(forced), set(declarations(css_block(css, f':root[data-accent="{accent}"]'))), accent)
            dark = {**base_dark, **forced}
            for theme, tokens in (("light", light), ("dark", dark)):
                for pairs, minimum in ((TEXT_PAIRS, 4.5), (NON_TEXT_PAIRS, 3.0)):
                    for fg, bg in pairs:
                        ratio = contrast(tokens[fg], tokens[bg])
                        self.assertGreaterEqual(ratio, minimum, f"{accent}/{theme}: {fg} on {bg} = {ratio:.2f}")
                for stop in re.findall(r"#[0-9A-Fa-f]{6}", tokens["--grad-primary"]):
                    self.assertGreaterEqual(contrast(stop, tokens["--sc-on-primary"]), 4.5, f"{accent}/{theme}: {stop}")
        app = (UI / "components" / "appearance-settings.js").read_text(encoding="utf-8")
        for accent in accents:
            self.assertIn(f'"{accent}"', app)
            self.assertIn(f"--swatch-{accent}:", css)

    def test_primary_gradient_keeps_button_text_readable(self):
        for theme, tokens in zip(("light", "dark"), self.theme_tokens()):
            stops = re.findall(r"#[0-9A-Fa-f]{6}", tokens["--grad-primary"])
            self.assertEqual(len(stops), 2, theme)
            for stop in stops:
                self.assertGreaterEqual(contrast(stop, tokens["--sc-on-primary"]), 4.5, f"{theme}: {stop}")

    def test_icons_are_one_thin_control_set_and_one_filled_glyph_set(self):
        # Thin line icons are only the small controls (chevron, check, close, search, …); every pictogram (task, tab,
        # item fallback) is a filled glyph (glyphs.js) on the glossy badge, 2026-10-09.
        dom = (UI / "components" / "dom.js").read_text(encoding="utf-8")
        self.assertIn('svg.setAttribute("stroke-width", "1.5")', dom)
        self.assertIn('svg.setAttribute("fill-rule", "evenodd")', dom)
        icons = (UI / "components" / "icons.js").read_text(encoding="utf-8")
        self.assertNotIn("fill", icons)
        glyphs = (UI / "components" / "glyphs.js").read_text(encoding="utf-8")
        self.assertNotIn("stroke", glyphs.split("export const GLYPHS")[1])
        names = set(re.findall(r"^  (\w+): ", icons, re.M))
        self.assertLessEqual(names, set(re.findall(r"^  (\w+): \[", glyphs, re.M)))  # every icon name has a filled twin
        sizes = set()
        for path in ui_files(".js"):
            sizes |= set(re.findall(r"icon\(\s*\"\w+\",\s*\{\s*size:\s*(\d+)", path.read_text(encoding="utf-8")))
        self.assertLessEqual(sizes, {"16", "20"})

    def test_app_logo_is_the_built_icon(self):
        # ui/icon.png is tools/make_app_icon.py's 256 px copy of browser_host/app_icon.png, the window icon's picture.
        data = (UI / APP_ICON).read_bytes()
        self.assertEqual(data[:8], bytes.fromhex("89504e470d0a1a0a"))
        self.assertEqual(struct.unpack(">II", data[16:24]), (256, 256))
        self.assertLess(len(data), 200_000)
        logo = (UI / "components" / "logo.js").read_text(encoding="utf-8")
        self.assertIn('src: "icon.png"', logo)
        for page in ("index.html",):
            html = (UI / page).read_text(encoding="utf-8")
            self.assertIn('<link rel="icon" href="icon.png" type="image/png">', html)

    def test_app_css_uses_only_defined_tokens(self):
        tokens = set(declarations((UI / "styles" / "tokens.css").read_text(encoding="utf-8")))
        app = (UI / "styles" / "app.css").read_text(encoding="utf-8")
        local = set(re.findall(r"(--[\w-]+)\s*:", app))
        for name in set(re.findall(r"var\((--[\w-]+)", app)):
            self.assertIn(name, tokens | local, name)

    def test_fixtures_are_synthetic(self):
        demo = json.loads((UI / "fixtures" / "demo-state.json").read_text(encoding="utf-8"))
        self.assertEqual(demo["fixture"], "synthetic")
        text = json.dumps(demo, ensure_ascii=False)
        for word in ("dev_assets", ".png", ".jpg", "token", "cookie", "C:\\\\", "Users"):
            self.assertNotIn(word, text)
        self.assertEqual(demo["daily"]["rollover"], {"timezone": "Asia/Tokyo", "hour": 5, "verified": True})

    def test_no_binary_or_game_assets_in_ui(self):
        allowed = {".html", ".js", ".css", ".json", ".svg", ".md", ".ttf", ".woff2", ".txt"}  # fonts: OFL only (ui/fonts/README.md)
        for path in UI.rglob("*"):
            if path.is_file() and GAME_ASSETS not in path.parents and path != UI / APP_ICON:
                self.assertIn(path.suffix, allowed, path)

    def test_game_assets_folder_is_gitignored(self):
        lines = [line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
        self.assertIn("ui/game-assets/", lines)
        if not shutil.which("git"):
            self.skipTest("git not installed")
        for probe in ("ui/game-assets/manifest.js", "ui/game-assets/cards/card-0000000000.png", "ui/game-assets/items/money.png"):
            self.assertEqual(git("check-ignore", "-q", probe).returncode, 0, probe)

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_no_committable_file_under_ui_is_a_game_derived_image(self):
        listed = git("ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "ui")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        files = [f for f in listed.stdout.split("\0") if f]
        self.assertTrue(files)
        for name in files:
            self.assertFalse(name.startswith("ui/game-assets/"), name)
            if name != f"ui/{APP_ICON}":   # the app icon, chosen by the user
                self.assertNotIn(Path(name).suffix.lower(), IMAGE_SUFFIXES, name)
        for name in files:
            if name.endswith(".svg"):
                self.assertNotRegex((ROOT / name).read_text(encoding="utf-8"), r"<image\b|data:image/", name)

    def test_game_asset_loader_is_same_origin_and_optional(self):
        loader = (UI / "components" / "game-assets.js").read_text(encoding="utf-8")
        # same-origin relative module; the revision query (force reload after 更新卡资料) stays on that path
        self.assertIn('import("../game-assets/manifest.js"', loader)
        self.assertNotRegex(loader, r"import\(\s*[\"'`](?:https?:)?//")
        self.assertIn("catch", loader)
        self.assertIn("^game-assets\\/(items|cards|icons)\\/", loader)  # only local relative image paths are accepted
        icons = (UI / "components" / "icons.js").read_text(encoding="utf-8")
        annotations = json.loads((UI / "annotations.json").read_text(encoding="utf-8"))
        for key, item in annotations["items"].items():
            self.assertRegex(icons, rf"\b{item['icon']}:", key)  # line-icon fallback exists

    def test_main_ui_has_no_spend_dialog_or_mode_switch(self):
        self.assertFalse((UI / "components" / "spend-dialog.js").exists())
        js = "\n".join(p.read_text(encoding="utf-8") for p in ui_files(".js") if "fixtures" not in p.parts)
        for word in ("askSpendConsent", "spend_consent", "mode-seg", "只读模式", "每次运行前"):
            self.assertNotIn(word, js, word)



class CardScanButtonTests(unittest.TestCase):
    """「扫描持有卡」: disabled 「待监督校准」 unless the host's read-only policy.get reports card_scan_ready;
    the readiness is computed from the embedded catalog + card_scan.json entry; the click goes through a confirmation (card-scan.js)."""

    def run_node(self, body, data):
        script = ("import './tests/fixtures/ui/fake_dom.mjs';"
                  "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
                  "const m = await import('./ui/components/resource-panel.js');"
                  "const data = JSON.parse(process.argv[1]);" + body)
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(data, ensure_ascii=False)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_button_is_gated_by_host_readiness(self):
        out = self.run_node(
            "const res = {};"
            "for (const [name, policy] of Object.entries(data)) {"
            "  let clicks = 0;"
            "  const state = { bridgeKind: 'mock', prefs: { owned_cards: { p: [], s: [] } }, annotations: {}, runReadings: [], policy };"
            "  const view = m.holdingsView(state, { itemScan: m.cleanItemScan(null), cardScan: { cards: [] } }, { onCardScan: () => { clicks += 1; } });"
            "  const find = (node) => node?.dataset?.focusKey === 'holdings-card-scan' ? node"
            "    : (node.childNodes || []).reduce((hit, c) => hit || find(c), null);"
            "  const btn = find(view);"
            "  for (const fn of btn.listeners.click || []) fn();"
            "  res[name] = { text: btn.textContent, disabled: btn.getAttribute('disabled') !== null,"
            "    title: btn.getAttribute('title') || '', clicks, state: btn.dataset.cardScan, gate: m.cardScanGate(policy) };"
            "}"
            "console.log(JSON.stringify(res));",
            {"missing": None, "closed": {"card_scan_ready": False,
                                         "card_scan_reason": "card_scan_actions_disabled:idol-portal-list,idol-list-tab-produce"},
             "string_true": {"card_scan_ready": "true", "card_scan_reason": "ready"},
             "ready": {"card_scan_ready": True, "card_scan_reason": "ready"},
             "hostile": {"card_scan_ready": False, "card_scan_reason": "<img src=x onerror=alert(1)>"}})
        for name in ("missing", "closed", "string_true", "hostile"):
            with self.subTest(name=name):
                self.assertEqual(out[name]["text"], "扫描持有卡")
                self.assertTrue(out[name]["disabled"])
                self.assertFalse(out[name]["gate"]["ready"])
        self.assertIn("idol-portal-list", out["closed"]["gate"]["detail"])  # developer view only
        self.assertEqual(out["closed"]["title"], "此功能暂不可用：需要更新程序")
        self.assertEqual(out["hostile"]["gate"]["reason"], "")              # unexpected text never reaches the page
        self.assertEqual(out["ready"]["text"], "扫描持有卡")
        self.assertFalse(out["ready"]["disabled"])
        self.assertTrue(out["ready"]["gate"]["ready"])
        self.assertEqual(out["ready"]["clicks"], 1)                         # host-ready click goes to the panel handler only
        self.assertEqual([out[n]["clicks"] for n in ("missing", "closed", "string_true", "hostile")], [0, 0, 0, 0])
        self.assertEqual((out["ready"]["state"], out["closed"]["state"]), ("ready", "pending"))

    def test_page_starts_the_scan_only_through_the_confirmed_host_command(self):
        panel = (UI / "components" / "resource-panel.js").read_text(encoding="utf-8")
        scan = (UI / "components" / "card-scan.js").read_text(encoding="utf-8")
        app = (UI / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("bridge", panel)
        self.assertNotIn("run.start", panel + scan)
        self.assertNotIn("SC.CardScan", panel + scan + app)          # no Pipeline entry name, path or budget is sent from the page
        self.assertIn("onCardScan: () => openCardScanConfirm({ onConfirm: () => actions.cardScanStart?.(),", panel)
        self.assertIn('bridge.request("cards.scan.start", { confirmed: true })', app)
        self.assertIn('bridge.request("cards.scan.stop", {})', app)
        self.assertEqual(app.count("cards.scan.start"), 1)
        self.assertIn("导入 Lv/凸", panel)
        self.assertEqual(panel.count("saveOwnedCards(state, actions"), 1)   # only the reviewed import dialog saves
        protocol = json.loads((UI / "protocol.json").read_text(encoding="utf-8"))
        start = protocol["commands"]["cards.scan.start"]["params"]
        self.assertEqual(start["required"], ["confirmed"])
        self.assertEqual(start["properties"], {"confirmed": {"const": True}})
        self.assertEqual(protocol["commands"]["cards.scan.stop"]["params"]["properties"], {})
        self.assertIn("card_scan", protocol["events"]["status.update"]["data"]["properties"])
        knowhow = protocol["commands"]["knowhow.scan.start"]["params"]
        self.assertEqual(knowhow["required"], ["confirmed", "mode"])
        # 2026-10-25: 补读新书 = mode newest with count 1..10 (one screen of 獲得順 tiles); still no path or budget
        self.assertEqual(knowhow["properties"], {"confirmed": {"const": True}, "mode": {"enum": ["calibrate", "scan", "newest"]},
                                                 "count": {"type": "integer", "minimum": 1, "maximum": 10}})
        self.assertFalse(knowhow["additionalProperties"])
        self.assertIn("knowhow_scan", protocol["events"]["status.update"]["data"]["properties"])

    def test_host_readiness_reads_catalog_family_and_entry_node(self):
        source = (ROOT / "browser_host" / "Ui" / "UiPolicySummary.cs").read_text(encoding="utf-8")
        for needle in ('CardScanFamily = "card-scan"', 'CardScanEntryNode = "SC.CardScan.Entry"', "PipelineActionTable.Default.Actions",
                       '["card_scan_ready"]', '["card_scan_reason"]', '"_actions_disabled:"', '"_entry_disabled"',
                       '"card_scan")', 'KnowhowScanFamily = "knowhow-scan"', '["knowhow_scan_ready"]'):
            self.assertIn(needle, source)
        self.assertNotIn("File.Write", source)
        actions = json.loads((ROOT / "assets/input/pipeline_actions.json").read_text(encoding="utf-8"))["actions"]
        family = [a for a in actions if a["family"] == "card-scan"]
        entry = json.loads((ROOT / "assets/resource/base/pipeline/card_scan.json").read_text(encoding="utf-8"))["SC.CardScan.Entry"]
        self.assertEqual(len(family), 5)
        # 2026-10-08: the five card-scan actions and the entry are enabled for the one-shot supervised
        # CardScan launcher entry, so a rebuilt host reports ready; the button asks the host (cards.scan.start).
        self.assertTrue(all(a.get("enabled", True) for a in family) and entry.get("enabled", True) is True)

if __name__ == "__main__":
    unittest.main()
