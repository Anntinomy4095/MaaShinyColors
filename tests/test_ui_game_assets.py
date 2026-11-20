"""UI U0.3: local-only game-asset pipeline guards (tools/ui_game_assets_fetch.py, tools/ui_game_assets.py,
ui/components/game-assets.js, card-picker.js). Offline: no test touches the network."""
import json
import re
import shutil
import subprocess
import unittest
import urllib.request
from pathlib import Path

from tools import ui_game_assets as build
from tools import ui_game_assets_fetch as fetch

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
ENZA = {
    "https://shinycolors.enza.fun/assets/7f697481939646e7371fd37596e0055b265b229c4f94dffac38bf51b87fce6a4?v=49",
    "https://shinycolors.enza.fun/assets/50989a84739aa52800afb56fb9cac1330207c874390b256ca2acde5e4bd74c75?v=46",
    "https://shinycolors.enza.fun/assets/7d84382d6ef3a1e5580d739ea856f79a5c24b0bfb596224b655ce89fb0c255e1?v=7",
    "https://shinycolors.enza.fun/assets/33c9066ff372eee1ca7d09078701994b87fa93f7516540b5e53456fe222bfc5d?v=8",
    "https://shinycolors.enza.fun/assets/7221d3d1d9e0bbb01a3e8419c5b98964887bb0d43ef2d8a930787b95278273c9?v=1",
    "https://shinycolors.enza.fun/assets/357163b7627d22e35d95707232d0c192ed1c39dbae3865ea9d4165aa52372c74?v=8",
    "https://shinycolors.enza.fun/assets/f6ec49880ff401feaa5f1304968651a30027eea0ba37f7818d11d54cf6aee8e6?v=8",
    "https://shinycolors.enza.fun/assets/4941788029c750a003577f19d591e67d1d17bb56d8b8ce29cdcf91695b4d3cb8?v=3",
    "https://shinycolors.enza.fun/assets/838bc741063799df09eef6f032f97704eb6b8932ec45ea53de3690da5b54a721?v=9",
    "https://shinycolors.enza.fun/assets/8f5a4652a6d1d7a160fa53c7c06f245463066358eaf592a4be18e5a7a634f24a?v=85",
}
SAMPLE_LIST = """<table class="wikitable"><caption>S-UR
</caption><tbody><tr><th>Icon</th></tr>
<tr style="background-color:#d8bfd8">
<td><a href="/wiki/File:CardIcon-2050170010.png"><img src="https://image.shinycolors.wiki/2/22/CardIcon-2050170010.png" width="96" /></a></td>
<td><a href="/wiki/Asahi_Serizawa">Asahi<br />Serizawa</a></td><td><a href="/wiki/S-UR1_Asahi">Kuttsuku, Kuttsukanai</a></td>
<td>【くっつく、くっつかない】<br />芹沢あさひ</td><td>Permanent Gacha<br />2026/04/17</td></tr>
<tr><td><img src="https://evil.example/2/22/CardIcon-2050999990.png" /></td><td>x</td><td>y</td><td>【a】<br />b</td></tr>
</tbody></table>
<table><caption>S-SSR
</caption><tbody><tr>
<td><img src="https://image.shinycolors.wiki/a/a8/CardIcon-2040010010.png" /></td>
<td>Mano<br />Sakuragi</td><td>Title &amp; Co</td><td>【ほわっと&amp;スマイル】<br />櫻木真乃</td><td>Event</td></tr></tbody></table>"""


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)


class FetchAllowlistTests(unittest.TestCase):
    def test_enza_sources_are_exactly_the_user_approved_urls(self):
        self.assertEqual(set(fetch.ENZA_URLS), ENZA)
        self.assertEqual(len(fetch.ENZA_SOURCES), 10)
        for source in fetch.ENZA_SOURCES:
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(source["purpose"])
            self.assertNotIn("/", source["path"])  # stored directly under game-assets-src/
        self.assertEqual(fetch.WIKI_HOSTS, {"shinycolors.wiki", "image.shinycolors.wiki"})

    def test_item_tiles_are_pinned_user_copied_urls_covering_ui_items(self):
        from agent.core import item_inventory as ii

        keys = [t["key"] for t in fetch.ENZA_TILES]
        self.assertEqual(len(keys), len(set(keys)))
        for tile in fetch.ENZA_TILES:
            self.assertRegex(tile["url"], r"^https://shinycolors\.enza\.fun/assets/[0-9a-f]{64}\?v=\d{1,4}$")
            self.assertEqual(tile["url"].split("/assets/")[1][:12], tile["key"])
            self.assertRegex(tile["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(tile["purpose"])
            self.assertTrue(fetch.is_allowed_url(tile["url"]))
            self.assertEqual(fetch.tile_file(tile["key"]).parent, fetch.SRC_DIR / "enza-tiles")
        self.assertFalse(fetch.ENZA_TILE_URLS & fetch.ENZA_URLS)
        # Every scanned Produce item id and the two account items get a clean tile instead of a crop / drawn icon.
        self.assertTrue(set(ii.PRODUCE_ITEMS) <= set(build.TILE_ITEMS))
        self.assertTrue({"fes_ticket", "stamina_drink", "handmade_gift"} <= set(build.TILE_ITEMS))
        self.assertFalse(set(build.TILE_ITEMS) & set(build.ATLAS_ITEMS))
        for key in build.TILE_ITEMS:
            self.assertRegex(key, r"^[a-z][a-z0-9_]{0,31}$")  # the UI loader drops other keys

    def test_allowlist_accepts_only_pinned_and_wiki_paths(self):
        for url in ENZA | {
            "https://shinycolors.wiki/wiki/Card_List/Support",
            "https://shinycolors.wiki/wiki/Card_List/Produce",
            "https://image.shinycolors.wiki/8/83/CardIcon-1050080010.png",
            "https://shinycolors.wiki/robots.txt",
            "https://image.shinycolors.wiki/robots.txt",
            "https://image.shinycolors.wiki/2/22/CardIcon-2050170010.png",
        }:
            self.assertTrue(fetch.is_allowed_url(url), url)
        for url in (
            "https://shinycolors.enza.fun/assets/7f697481939646e7371fd37596e0055b265b229c4f94dffac38bf51b87fce6a4",  # no ?v
            "https://shinycolors.enza.fun/assets/0000000000000000000000000000000000000000000000000000000000000000?v=1",
            "https://shinycolors.enza.fun/",
            "http://shinycolors.wiki/wiki/Card_List/Support",
            "https://shinycolors.wiki/wiki/Card_List/Produce?action=raw",
            "https://shinycolors.wiki/wiki/Card_List/Support?action=raw",
            "https://shinycolors.wiki/wiki/Card_List/Event",
            "https://shinycolors.wiki/wiki/Card_List/ProduceX",
            "https://image.shinycolors.wiki/8/83/CardIcon-1050080010_FES.png",
            "https://shinycolors.wiki/w/api.php",
            "https://shinycolors.wiki/wiki/Special:Export",
            "https://shinycolors.wiki/2/22/CardIcon-2050170010.png",  # images only from the image host
            "https://image.shinycolors.wiki/thumb/b/b2/Idea_Da.png/29px-Idea_Da.png",
            "https://image.shinycolors.wiki/2/22/CardIcon-2050170010.png#x",
            "https://image.shinycolors.wiki:8443/2/22/CardIcon-2050170010.png",
            "https://user@image.shinycolors.wiki/2/22/CardIcon-2050170010.png",
            "https://image.shinycolors.wiki.evil.example/2/22/CardIcon-2050170010.png",
            "https://shinycolors.moe/",
            "file:///C:/x.png",
        ):
            self.assertFalse(fetch.is_allowed_url(url), url)

    def test_shinycolorsdb_allowlist_is_exact(self):
        self.assertEqual(fetch.DB_HOSTS, {"api.shinycolors.moe", "cf-static.shinycolors.moe"})
        for url in (
            "https://api.shinycolors.moe/info/idolList",
            "https://api.shinycolors.moe/info/idolInfo?idolId=1",
            "https://api.shinycolors.moe/info/idolInfo?idolId=801",
            "https://api.shinycolors.moe/robots.txt",
            "https://cf-static.shinycolors.moe/robots.txt",
            "https://cf-static.shinycolors.moe/images/content/idols/icon/1040010130.png",
            "https://cf-static.shinycolors.moe/images/content/support_idols/icon/2040010260.png",
        ):
            self.assertTrue(fetch.is_allowed_url(url), url)
        for url in (
            # card detail / skill / other API endpoints are never requested (thumbnails only)
            "https://api.shinycolors.moe/info/sCardInfo?cardId=11d66fd2-33c9-40b8-8f68-f99ed2d449e9",
            "https://api.shinycolors.moe/info/pCardInfo?cardId=d0035340-5593-46e2-8413-4e6886a61f86",
            "https://api.shinycolors.moe/info/getAllTable",
            "https://api.shinycolors.moe/info/latestSInfo",
            "https://api.shinycolors.moe/info/supportSkillList",
            "https://api.shinycolors.moe/info/idolList?x=1",
            "https://api.shinycolors.moe/info/idolInfo",
            "https://api.shinycolors.moe/info/idolInfo?idolId=1&x=2",
            "https://api.shinycolors.moe/info/idolInfo?idolId=abc",
            "https://api.shinycolors.moe/info/idolInfo?idolId=1#f",
            "http://api.shinycolors.moe/info/idolList",
            "https://api.shinycolors.moe:8443/info/idolList",
            "https://user@api.shinycolors.moe/info/idolList",
            "https://api.shinycolors.moe/images/content/idols/icon/1040010130.png",  # icons only from the CDN
            # CDN: only P icons under idols/, S icons under support_idols/, exact 10-digit ids
            "https://cf-static.shinycolors.moe/images/content/idols/icon/2040010260.png",
            "https://cf-static.shinycolors.moe/images/content/support_idols/icon/1040010130.png",
            "https://cf-static.shinycolors.moe/images/content/idols/icon/104001013.png",
            "https://cf-static.shinycolors.moe/images/content/idols/icon/10400101300.png",
            "https://cf-static.shinycolors.moe/images/content/idols/icon/1040010130.jpg",
            "https://cf-static.shinycolors.moe/images/content/idols/icon/1040010130.png?v=1",
            "https://cf-static.shinycolors.moe/images/content/idols/card/1040010130.jpg",
            "https://cf-static.shinycolors.moe/images/content/idols/fes_card/1040010130.png",
            "https://cf-static.shinycolors.moe/movies/idols/card/1040010130.mp4",
            "https://cf-static.shinycolors.moe/images/content/others/skillIcon/90003.png",
            "https://cf-static.shinycolors.moe/images/content/idols/icon/../card/1040010130.png",
            "https://cf-static.shinycolors.moe.evil.example/images/content/idols/icon/1040010130.png",
            "https://shinycolors.moe/images/content/idols/icon/1040010130.png",
            "https://static.shinycolors.moe/images/content/idols/icon/1040010130.png",
        ):
            self.assertFalse(fetch.is_allowed_url(url), url)

    def test_db_icon_url_and_card_parsing_use_exact_ids(self):
        self.assertEqual(fetch.db_icon_url("1040010130"),
                         "https://cf-static.shinycolors.moe/images/content/idols/icon/1040010130.png")
        self.assertEqual(fetch.db_icon_url("2040010260"),
                         "https://cf-static.shinycolors.moe/images/content/support_idols/icon/2040010260.png")
        for bad in ("304001013", "3040010130", "104001013a", "", None, 1040010130):
            self.assertIsNone(fetch.db_icon_url(bad), bad)
        self.assertEqual(fetch.db_idol_url(7), "https://api.shinycolors.moe/info/idolInfo?idolId=7")
        good = {"enzaId": "2040010260", "cardType": "S_SSR", "cardName": "【We can go now!】櫻木真乃", "idolId": 1,
                "cardHash": "eed2", "releaseDate": "2026-07-26", "cardUuid": "11d66fd2", "cardStatus": {"vocal": 1}}
        card = fetch.parse_db_card(good)
        self.assertEqual((card["card_id"], card["kind"], card["rarity"], card["title"], card["idol"]),
                         ("2040010260", "S", "SSR", "We can go now!", "櫻木真乃"))
        self.assertTrue(fetch.is_allowed_url(card["icon_url"]))
        self.assertNotIn("cardStatus", json.dumps(card))  # thumbnails only: no stat/skill fields are kept
        self.assertNotIn("cardUuid", json.dumps(card))
        for bad in ({**good, "cardType": "P_SSR"},          # prefix disagrees with the type
                    {**good, "enzaId": "204001026"},        # not 10 digits
                    {**good, "enzaId": 2040010260},         # not a string
                    {**good, "cardType": "S_XR"}, "x", None):
            self.assertIsNone(fetch.parse_db_card(bad), bad)

    def test_image_format_magic(self):
        self.assertEqual(fetch.image_format(b"\x89PNG\r\n\x1a\n" + b"0" * 8), "png")
        self.assertEqual(fetch.image_format(b"RIFF\x00\x00\x00\x00WEBPVP8L"), "webp")
        for bad in (b"<html>", b"RIFF\x00\x00\x00\x00AVI LIST", b""):
            self.assertIsNone(fetch.image_format(bad))

    def test_rate_limit_stops_instead_of_retrying(self):
        import io
        import urllib.error

        fetcher = fetch.Fetcher()
        fetcher._last = {}
        calls = []

        def opener(request, timeout=None):
            calls.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {"Retry-After": "60"}, io.BytesIO(b""))

        fetcher.opener.open = opener
        fetch.time.sleep, sleep = (lambda s: None), fetch.time.sleep
        try:
            with self.assertRaises(fetch.RateLimited):
                fetcher._raw("https://cf-static.shinycolors.moe/images/content/idols/icon/1040010130.png")
        finally:
            fetch.time.sleep = sleep
        self.assertEqual(len(calls), 1)  # one attempt, no retry loop

    def test_only_the_icon_cdn_is_paced_faster(self):
        # 2026-11-23: card icons (static CDN) at >= 0.2 s between starts with 4 workers; API/wiki stay >= 1 s
        self.assertGreaterEqual(fetch.CDN_INTERVAL, 0.2)
        self.assertLessEqual(fetch.ICON_WORKERS, 4)
        fetcher = fetch.Fetcher()
        sleeps = []
        fetch.time.sleep, sleep = sleeps.append, fetch.time.sleep
        try:
            for _ in range(3):
                fetcher._wait("https://cf-static.shinycolors.moe/x.png")
            fetcher._wait("https://api.shinycolors.moe/info/idolList")
            fetcher._wait("https://api.shinycolors.moe/info/idolList")
        finally:
            fetch.time.sleep = sleep
        self.assertTrue(all(s <= fetch.CDN_INTERVAL * 2 + 0.05 for s in sleeps[:2]))
        self.assertGreaterEqual(sleeps[-1], fetch.MIN_INTERVAL - 0.05)

    def test_fetcher_sends_no_cookies_and_identifies_itself(self):
        fetcher = fetch.Fetcher()
        handlers = [type(h) for h in fetcher.opener.handlers]
        self.assertNotIn(urllib.request.HTTPCookieProcessor, handlers)
        self.assertIn(fetch._AllowlistRedirect, handlers)
        self.assertEqual(dict(fetcher.opener.addheaders)["User-Agent"], fetch.USER_AGENT)
        # robots.txt groups match by substring: our agent must not hit a blocked group such as "Fetch" or "wget".
        for blocked in ("fetch", "wget", "httrack", "webcopier", "libwww", "larbin", "fast"):
            self.assertNotIn(blocked, fetch.USER_AGENT.lower())
        self.assertGreaterEqual(fetch.MIN_INTERVAL, 1.0)
        self.assertLessEqual(fetch.MAX_TOTAL_BYTES, 50 * 1024 * 1024)
        with self.assertRaises(ValueError):
            fetcher._raw("https://shinycolors.enza.fun/assets/other?v=1")  # refused before any network use
        self.assertEqual(fetcher.requests, 0)

    def test_fetch_and_build_modules_do_not_touch_cookies_dom_or_traffic(self):
        for path in (ROOT / "tools" / "ui_game_assets_fetch.py", ROOT / "tools" / "ui_game_assets.py"):
            text = path.read_text(encoding="utf-8")
            for word in ("CookieJar", "HTTPCookieProcessor(", "Cookie:", "selenium", "playwright", "DevTools", "mitm"):
                self.assertIsNone(re.search(rf"\b{re.escape(word)}", text), (path.name, word))
        self.assertNotIn("urllib", (ROOT / "tools" / "ui_game_assets.py").read_text(encoding="utf-8"))  # build is offline

    def test_parse_support_list(self):
        cards = fetch.parse_support_list(SAMPLE_LIST)
        self.assertEqual([c["card_id"] for c in cards], ["2050170010", "2040010010"])  # foreign host row dropped
        asahi, mano = cards
        self.assertEqual((asahi["rarity"], asahi["idol"], asahi["title"], asahi["idol_en"]),
                         ("UR", "芹沢あさひ", "くっつく、くっつかない", "Asahi Serizawa"))
        self.assertEqual((mano["rarity"], mano["title"], mano["title_en"]), ("SSR", "ほわっと&スマイル", "Title & Co"))
        for card in cards:
            self.assertTrue(fetch.is_allowed_url(card["icon_url"]))


class LocalOnlyStorageTests(unittest.TestCase):
    def test_sources_and_outputs_are_gitignored(self):
        self.assertTrue(str(fetch.SRC_DIR.resolve()).startswith(str((ROOT / "dev_assets" / "private").resolve())))
        self.assertEqual(build.OUT_DIR.resolve(), (UI / "game-assets").resolve())
        if not shutil.which("git"):
            self.skipTest("git not installed")
        for probe in ("dev_assets/private/game-assets-src/enza-1.webp",
                      "dev_assets/private/game-assets-src/wiki-support/icons/CardIcon-2050170010.png",
                      "dev_assets/private/game-assets-src/wiki-support/cards.json",
                      "dev_assets/private/game-assets-src/shinycolorsdb/icons/1040010130.png",
                      "dev_assets/private/game-assets-src/shinycolorsdb/icons/1020010010.webp",
                      "dev_assets/private/game-assets-src/shinycolorsdb/cards.json",
                      "dev_assets/private/game-assets-src/shinycolorsdb/idols/idolInfo-1.json",
                      "ui/game-assets/manifest.js", "ui/game-assets/items/money@2x.png", "ui/game-assets/cards/2050170010.png"):
            self.assertEqual(git("check-ignore", "-q", probe).returncode, 0, probe)

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_no_game_asset_is_tracked(self):
        tracked = git("ls-files", "--cached", "-z", "--", "ui", "dev_assets").stdout.split("\0")
        for name in filter(None, tracked):
            self.assertFalse(name.startswith(("ui/game-assets/", "dev_assets/private/")), name)
            if name.startswith("ui/") and name != "ui/icon.png":   # the app icon (tools/make_app_icon.py)
                self.assertNotIn(Path(name).suffix.lower(), {".png", ".jpg", ".jpeg", ".webp", ".gif"}, name)

    def test_build_refuses_other_output_dirs(self):
        with self.assertRaises(ValueError):
            build.build(ROOT / "ui" / "elsewhere")

    def test_badge_icons_fit_their_atlases_and_cover_ranks_and_attributes(self):
        self.assertEqual(set(build.ATLAS_SIZES), {s["id"] for s in fetch.ENZA_SOURCES})
        for key, spec in build.ATLAS_ICONS.items():
            self.assertRegex(key, r"^(rank|attr|ui)_[a-z_]{1,16}$")  # the UI loader drops other keys
            self.assertIn(spec["group"], ("rank", "attr", "ui"))
            self.assertEqual(spec["group"], key.split("_")[0], key)
            self.assertIn(spec.get("mode", "crop"), ("crop", "tile", "glyph"), key)
            x, y, w, h = spec["rect"]
            aw, ah = build.ATLAS_SIZES[spec["atlas"]]
            self.assertTrue(0 <= x and 0 <= y and x + w <= aw and y + h <= ah, key)
        self.assertEqual({k for k in build.ATLAS_ICONS if k.startswith("rank_")},
                         {f"rank_{r}" for r in ("ss", "s_plus", "s", "a", "b", "c", "d", "e", "f")})
        self.assertEqual({k for k in build.ATLAS_ICONS if k.startswith("attr_")}, {"attr_vo", "attr_da", "attr_vi", "attr_me"})

    def test_item_icons_no_longer_fall_back_to_screenshot_crops(self):
        self.assertEqual(build.CAPTURE_ITEMS, {})  # 2026-10-08 「尽量不用截图」: atlases or the UI line icon only

    def test_lift_glyph_keeps_dark_shape_and_drops_light_face_and_sparkles(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (40, 40), (236, 234, 246, 255))  # light button face
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 12, 29, 31), fill=(90, 80, 120, 255))  # dark glyph
        draw.point((3, 3), fill=(80, 80, 80, 255))  # 1-px speck (dropped: under min_size)
        glyph = build.lift_glyph(image)
        self.assertEqual(glyph.size, (20, 20))
        self.assertEqual(glyph.getpixel((5, 5))[3], 255)
        with self.assertRaises(ValueError):
            build.lift_glyph(Image.new("RGBA", (20, 20), (240, 240, 240, 255)))

    def test_lift_glyph_drop_edge_removes_button_outline_and_invert_lifts_light_glyphs(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (40, 40), (236, 234, 246, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 39, 39), outline=(70, 60, 90, 255), width=2)  # button outline touching the rect edge
        draw.rectangle((12, 12, 27, 27), fill=(150, 150, 190, 255))  # lighter gradient glyph (lum ~157)
        self.assertEqual(build.lift_glyph(image).size, (40, 40))  # default: outline kept, light glyph lost
        glyph = build.lift_glyph(image, lo=185, hi=220, min_size=10, drop_edge=True)
        self.assertEqual(glyph.size, (16, 16))
        dark = Image.new("RGBA", (30, 30), (120, 40, 140, 255))
        ImageDraw.Draw(dark).rectangle((8, 8, 21, 21), fill=(250, 250, 250, 255))
        self.assertEqual(build.lift_glyph(dark, lo=100, hi=120, invert=True, min_size=10).size, (14, 14))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_ui_items_use_glyph_plates_and_produce_names_are_marked(self):
        from agent.core import item_inventory as ii

        script = ("import './tests/fixtures/ui/fake_dom.mjs';"
                  "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
                  "const g = await import('./ui/components/game-assets.js');"
                  "const { produceName } = await import('./ui/components/resource-panel.js');"
                  "const t = await import('./ui/components/task-icons.js');"
                  "g.useGameAssets({ icons: { ui_mission: { src: 'game-assets/icons/ui_mission.png', mode: 'glyph' },"
                  " ui_fes_p: { src: 'game-assets/icons/ui_fes_p.png', mode: 'tile' } } });"
                  "const cls = (e) => e?.getAttribute('class') || '';"
                  "const plate = g.itemIcon(g.itemSpec(null, 'mission'), { size: 24, label: '' });"
                  "const none = g.itemIcon(g.itemSpec(null, 'shop'), { size: 24, label: '' });"
                  "const task = t.taskIcon(null, 'MissionsClaim', { size: 24 }).childNodes[0];"
                  "const fes = t.taskIcon(null, 'GradeFes', { size: 24 }).childNodes[0];"
                  "console.log(JSON.stringify({ plate: cls(plate), inner: cls(plate.childNodes[0]), none: cls(none),"
                  " task: [cls(task), cls(task.childNodes[0])], fes: [cls(fes), fes.childNodes[0].tagName],"
                  " names: [produceName('p_secret_memo', {}), produceName('p_tart_blue_2', {}),"
                  " produceName('p_tart_blue_2', { name: 'X' }), produceName('p_new', { look: '红盒' })],"
                  " sure: Object.entries(g.P_ITEM_NAMES).filter(([, v]) => v.sure).map(([k]) => k).sort() }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertIn("glyph-plate", out["plate"])
        self.assertIn("tone-sky", out["plate"])
        self.assertEqual(out["inner"], "glyph-mask")
        # One treatment for task icons: always the tinted plate; glyph inside, or the coloured badge where no glyph exists.
        self.assertIn("glyph-plate", out["task"][0])
        self.assertEqual(out["task"][1], "glyph-mask")
        # 2026-10-09: no coloured item tiles inside badges; Grade has no game glyph -> filled project glyph on the badge.
        self.assertIn("line-plate", out["fes"][0])
        self.assertNotIn("badge-plate", out["fes"][0])
        self.assertEqual(out["fes"][1].lower(), "svg")
        self.assertIn("item-line", out["none"])  # no glyph in the manifest: self-drawn line plate
        # 2026-10-08d: every pinned Produce item name was read from アイテム一覧, so none is marked 推测.
        self.assertEqual(out["names"], ["秘密のメモ帳", "ヒーリングフルーツタルト(2個入り)", "X", "未确认名称（红盒）"])
        self.assertEqual(out["sure"], sorted(ii.PRODUCE_ITEMS))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_task_and_tab_icons_cover_tasks_and_fall_back_to_line_icons(self):
        schema = json.loads((UI / "fixtures" / "schema.json").read_text(encoding="utf-8"))
        script = ("import { GLYPHS as ICONS } from './ui/components/glyphs.js';"
                  "import { TASK_ICONS, TAB_ICONS, taskIconSource } from './ui/components/task-icons.js';"
                  "import { useGameAssets, ITEM_DEFAULTS, readingItemKey } from './ui/components/game-assets.js';"
                  "const names = JSON.parse(process.argv[1]);"
                  "const before = names.map(taskIconSource);"
                  "const a = useGameAssets({ icons: { ui_home: { src: 'game-assets/icons/ui_home.png', mode: 'glyph' },"
                  " ui_fes_p: { src: 'game-assets/icons/ui_fes_p.png', mode: 'tile' }, ui_evil: { src: 'https://x.example/a.png' } } });"
                  "console.log(JSON.stringify({ missing: names.filter((n) => !TASK_ICONS[n]),"
                  " tabsMissing: JSON.parse(process.argv[2]).filter((k) => !TAB_ICONS[k]),"
                  " badLines: [...Object.values(TASK_ICONS), ...Object.values(TAB_ICONS), ...Object.values(ITEM_DEFAULTS)]"
                  "  .map((s) => s.line || s.icon).filter((n) => !ICONS[n]),"
                  " before, startup: taskIconSource('Startup'), grade: taskIconSource('GradeFes'), icons: Object.keys(a.icons).sort(),"
                  " glyph: a.icons.ui_home.glyph, tile: a.icons.ui_fes_p.glyph,"
                  " readings: [{ key: 'missions' }, { key: 'stamina' }, { key: 'x', item: 'money' }, { key: 'x' }].map(readingItemKey) }));")
        names = [t["name"] for t in schema["tasks"]]
        header = (UI / "components" / "header.js").read_text(encoding="utf-8")
        tabs = re.findall(r'\{ key: "(\w+)", label:', header)
        self.assertIn("settings", tabs)
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(names), json.dumps(tabs)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["missing"], [])
        self.assertEqual(out["tabsMissing"], [])
        self.assertEqual(out["badLines"], [])
        self.assertEqual(set(out["before"]), {"line"})  # no manifest: every task still has a (line) icon
        self.assertEqual((out["startup"], out["grade"]), ("game", "line"))  # Grade: filled crown glyph (no game glyph)
        self.assertEqual(out["icons"], ["ui_fes_p", "ui_home"])
        self.assertEqual((out["glyph"], out["tile"]), (True, False))
        self.assertEqual(out["readings"], ["mission", "stamina", "money", None])
        source = (UI / "components" / "task-icons.js").read_text(encoding="utf-8")
        for key in set(re.findall(r'ui: "(ui_[a-z_]+)"', source)):
            self.assertIn(key, build.ATLAS_ICONS)  # every referenced game icon has a pinned atlas rectangle

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_badge_manifest_validation_and_rank_mapping(self):
        manifest = {"icons": {
            "rank_s": {"src": "game-assets/icons/rank_s.png", "src2x": "game-assets/icons/rank_s@2x.png"},
            "attr_vo": {"src": "game-assets/icons/attr_vo.png"},
            "rank_x": {"src": "game-assets/items/money.png"},  # icons must live under game-assets/icons/
            "evil": {"src": "game-assets/icons/evil.png"},  # key outside rank_/attr_
            "attr_da": {"src": "https://evil.example/x.png"},
        }}
        script = ("import { useGameAssets, rankIconKey, attrIconKey } from './ui/components/game-assets.js';"
                  "import { primaryAttr } from './ui/components/team-picker.js';"
                  "const a = useGameAssets(JSON.parse(process.argv[1]));"
                  "console.log(JSON.stringify({ icons: Object.keys(a.icons).sort(), s2x: a.icons.rank_s?.src2x,"
                  " ranks: ['SS+', 'SS', 'S+', 'S', 'A+', 'B', 'F', 'G', '', null].map(rankIconKey),"
                  " attrs: ['Vo', 'dance', 'Visual', 'Me', 'Balanced', ''].map(attrIconKey),"
                  " primary: ['Dance (Vocal)', 'Vocal & Visual', 'Balanced', ''].map(primaryAttr) }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(manifest)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["icons"], ["attr_vo", "rank_s"])
        self.assertEqual(out["s2x"], "game-assets/icons/rank_s@2x.png")
        self.assertEqual(out["ranks"], ["rank_ss", "rank_ss", "rank_s_plus", "rank_s", "rank_a", "rank_b", "rank_f", None, None, None])
        self.assertEqual(out["attrs"], ["attr_vo", "attr_da", "attr_vi", "attr_me", None, None])
        self.assertEqual(out["primary"], ["Dance", "Vocal", "", ""])

    def test_atlas_rects_fit_their_atlases_and_missing_items_are_listed(self):
        for key, spec in build.ATLAS_ITEMS.items():
            x, y, w, h = spec["rect"]
            aw, ah = build.ATLAS_SIZES[spec["atlas"]]
            self.assertTrue(0 <= x and 0 <= y and x + w <= aw and y + h <= ah, key)
            self.assertIn(spec["mode"], ("tile", "crop"))
        self.assertTrue(build.ATLAS_ITEMS["jewel"].get("indicator_only"))
        # 2026-10-08b: home-screen buttons are glyphs (no plate / label crops), not items.
        for key in ("gasha", "shop", "mission", "business", "present", "notice"):
            self.assertNotIn(key, build.ATLAS_ITEMS)
            self.assertEqual(build.ATLAS_ICONS[f"ui_{key}"]["mode"], "glyph")
        self.assertEqual(build.MISSING_ITEMS, {})
        self.assertIn("handmade_gift", build.TILE_ITEMS)
        for key, spec in build.ATLAS_ICONS.items():
            x, y, w, h = spec["rect"]
            aw, ah = build.ATLAS_SIZES[spec["atlas"]]
            self.assertTrue(0 <= x and 0 <= y and x + w <= aw and y + h <= ah, key)
            self.assertLessEqual(set(spec.get("lift", {})), {"lo", "hi", "min_size", "drop_edge", "invert"}, key)
        self.assertLessEqual({"silver_ticket", "skip_ticket", "bronze_ticket", "lv50_ticket"}, set(build.ATLAS_ITEMS))
        self.assertTrue(set(build.MISSING_ITEMS).isdisjoint(build.ATLAS_ITEMS))

    def test_build_cards_prefers_db_thumbnail_and_falls_back_to_wiki(self):
        import tempfile
        from unittest import mock

        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "src"
            (src / "wiki-support" / "icons").mkdir(parents=True)
            (src / "shinycolorsdb" / "icons").mkdir(parents=True)
            (tmp / "out" / "cards").mkdir(parents=True)
            red, blue = (200, 30, 30, 255), (30, 30, 200, 255)
            # A: in both (DB wins); B: wiki only (fallback); C: DB only (WebP, new card); D: wiki id with bad prefix.
            Image.new("RGBA", (96, 96), red).save(src / "shinycolorsdb" / "icons" / "2040010010.png")
            Image.new("RGBA", (96, 96), red).save(src / "shinycolorsdb" / "icons" / "2040020270.webp", "WEBP")
            for cid in ("2040010010", "2030020010"):
                Image.new("RGBA", (96, 96), blue).save(src / "wiki-support" / "icons" / f"CardIcon-{cid}.png")
            wiki = {"source": "w", "cards": [
                {"card_id": "2040010010", "rarity": "SSR", "idol": "櫻木真乃", "title": "A", "idol_en": "Mano Sakuragi",
                 "specialty": "Vocal", "idea": "Vocal"},
                {"card_id": "2030020010", "rarity": "SR", "idol": "風野灯織", "title": "B"},
                {"card_id": "1040010010", "rarity": "SSR", "idol": "x", "title": "P in S list"}]}
            db = {"fetched_at": "t", "cards": [
                {"card_id": "2040010010", "kind": "S", "rarity": "SSR", "idol": "櫻木真乃", "title": "A"},
                {"card_id": "2040020270", "kind": "S", "rarity": "SSR", "idol": "風野灯織", "title": "C"},
                {"card_id": "2040030010", "kind": "P", "rarity": "SSR", "idol": "x", "title": "kind mismatch"}]}
            (src / "wiki.json").write_text(json.dumps(wiki, ensure_ascii=False), encoding="utf-8")
            (src / "db.json").write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(build, "SRC_DIR", src), mock.patch.object(build, "WIKI_CARDS_FILE", src / "wiki.json"), \
                    mock.patch.object(build, "DB_CARDS_FILE", src / "db.json"), \
                    mock.patch.object(fetch, "DB_ICON_DIR", src / "shinycolorsdb" / "icons"), \
                    mock.patch.object(build, "rel", lambda p: Path(p).name):
                for prefer, want in (("db", {"2040010010": "shinycolorsdb", "2030020010": "wiki", "2040020270": "shinycolorsdb"}),
                                     ("wiki", {"2040010010": "wiki", "2030020010": "wiki", "2040020270": "shinycolorsdb"})):
                    manifest = {"cards": [], "skipped": []}
                    self.assertEqual(build.build_cards(tmp / "out", manifest, "S", prefer=prefer), 3)
                    got = {c["id"]: c["thumb_source"] for c in manifest["cards"]}
                    self.assertEqual(got, want, prefer)
                    pixel = Image.open(tmp / "out" / "cards" / "2040010010.png").convert("RGBA").getpixel((48, 48))
                    self.assertEqual(pixel, red if prefer == "db" else blue)
                    for card in manifest["cards"]:
                        self.assertRegex(card["src"], r"^game-assets/cards/\d{10}\.png$")
                    mano = next(c for c in manifest["cards"] if c["id"] == "2040010010")
                    self.assertEqual((mano["idol_en"], mano["specialty"]), ("Mano Sakuragi", "Vocal"))  # wiki metadata kept
                    source = manifest["card_source"]
                    self.assertEqual((source["prefer"], source["wiki_listed"], source["db_listed"]), (prefer, 2, 2))
                    self.assertEqual(sum(source["thumbs"].values()), 3)
        with self.assertRaises(ValueError):
            build.build(prefer="other")

    def test_strip_tile_removes_white_frame_only(self):
        from PIL import Image, ImageDraw

        tile = Image.new("RGBA", (40, 40), (255, 255, 255, 255))
        ImageDraw.Draw(tile).rectangle((2, 2, 37, 37), outline=(225, 220, 226, 255))
        ImageDraw.Draw(tile).ellipse((10, 10, 29, 29), fill=(240, 190, 20, 255), outline=(120, 70, 10, 255))
        out = build.strip_tile(tile)
        self.assertLessEqual(max(out.size), 22)
        self.assertEqual(out.getpixel((out.width // 2, out.height // 2))[3], 255)
        self.assertEqual(out.getpixel((0, 0))[3], 0)


class LoaderAndPickerTests(unittest.TestCase):
    def test_loader_only_accepts_same_origin_relative_paths(self):
        loader = (UI / "components" / "game-assets.js").read_text(encoding="utf-8")
        self.assertIn('import("../game-assets/manifest.js"', loader)
        self.assertIsNone(re.search(r"https?:|//[a-z]", loader.replace("// ", "")))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_manifest_validation_and_card_art_lookup_by_id(self):
        manifest = {
            "items": {
                "money": {"src": "game-assets/items/money.png", "src2x": "game-assets/items/money@2x.png"},
                "evil": {"src": "https://evil.example/x.png"},
                "up": {"src": "../secret.png"},
                "abs": {"src": "/game-assets/items/x.png"},
                "data": {"src": "data:image/png;base64,AAAA"},
                "Bad-Key": {"src": "game-assets/items/x.png"},
            },
            "cards": [
                {"id": "2050170010", "option": "【くっつく、くっつかない】芹沢あさひ", "calibrated": True, "idol": "芹沢あさひ",
                 "title": "くっつく、くっつかない", "rarity": "UR", "src": "game-assets/cards/2050170010.png"},
                {"id": "2040010010", "idol": "櫻木真乃", "title": "ほわっと", "rarity": "SSR", "src": "game-assets/cards/2040010010.png"},
                {"id": "x", "src": "game-assets/cards/../../x.png"},
                {"id": "y", "src": "https://image.shinycolors.wiki/2/22/CardIcon-2050170010.png"},
            ],
        }
        script = ("import { useGameAssets, cardArt, itemKeyFor } from './ui/components/game-assets.js';"
                  "const assets = useGameAssets(JSON.parse(process.argv[1]));"
                  "const byId = [cardArt('2040010010')?.idol ?? null, cardArt('2050170010')?.idol ?? null, cardArt('') ?? null, cardArt('2999999999') ?? null];"
                  "const empty = useGameAssets(null);"
                  "console.log(JSON.stringify({ items: Object.keys(assets.items), money2x: assets.items.money?.src2x,"
                  " cards: assets.cards.map((c) => c.id), art: assets.cards.length,"
                  " byId,"
                  " empty: empty.cards.length,"
                  " keys: ['商店跳过券', '商店金训练券', '商店其他·シルバートレーニングチケット@283', '训练使用铜券', 'Match次数'].map(itemKeyFor) }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(manifest, ensure_ascii=False)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["items"], ["money"])
        self.assertEqual(out["money2x"], "game-assets/items/money@2x.png")
        self.assertEqual(out["cards"], ["2050170010", "2040010010"])
        self.assertEqual(out["byId"], ["櫻木真乃", "芹沢あさひ", None, None])   # any S card id, not only the calibrated option
        self.assertEqual(out["empty"], 0)
        self.assertEqual(out["keys"], ["skip_ticket", "gold_ticket", "silver_ticket", "bronze_ticket", None])

    def test_training_card_option_is_a_confirmed_s_card_picker_with_a_default(self):
        schema = json.loads((UI / "fixtures" / "schema.json").read_text(encoding="utf-8"))
        option = schema["options"]["训练卡"]
        self.assertEqual((option["type"], option["default_case"]), ("input", ""))
        self.assertEqual(option["inputs"][0]["verify"], r"^(2\d{9})?$")
        annotations = json.loads((UI / "annotations.json").read_text(encoding="utf-8"))
        self.assertEqual(annotations["options"]["训练卡"]["control"], "card_picker")
        picker = (UI / "components" / "team-picker.js").read_text(encoding="utf-8")
        for needle in ("openTrainingCardPicker", "ownedCards(state)", 'kind: "S"', "TRAINING_DEFAULT", "trainingCardButton"):
            self.assertIn(needle, picker)
        field = (UI / "components" / "option-field.js").read_text(encoding="utf-8")
        self.assertIn("openTrainingCardPicker(state, { value, onSelect: onChange, goResources: goScan })", field)


if __name__ == "__main__":
    unittest.main()
