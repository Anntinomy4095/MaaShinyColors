import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from agent.core.true_end.models import CardData, Ownership, PARSER_VERSION, extract_card_features
from agent.core.true_end.wiki import CardDatabase, CardHTML, CardPageParser, MechanicsParser, SnapshotManager, discover_card_urls, public_url

ROOT = Path(__file__).resolve().parents[1]
URL = "https://wikiwiki.jp/shinycolors/%E3%80%90test%E3%80%91Idol"


def p_card(condition, title="True End"):
    return ('<table><tr><th>アイドル</th><td>Idol</td></tr></table>'
            f'<h2>カードイベント</h2><table><tr><th colspan="2">{title}</th></tr>'
            f'<tr><th>条件</th><td>{condition}</td></tr></table>')


def small_card():
    return '''<table><tr><th>アイドル</th><td>Idol</td></tr></table>
    <h2>ステータス</h2><table><tr><th>Lv</th><th>Vo</th><th>Da</th><th>Vi</th><th>メンタル</th></tr>
    <tr><th>60（☆0）</th><td>100</td><td>110</td><td>120</td><td>130</td></tr>
    <tr><th>65（☆1）</th><td></td><td></td><td></td><td></td></tr></table>
    <h2>ライブスキル</h2><table><tr><th>スキル名</th><th>効果</th><th>取得Lv</th></tr>
    <tr><td>Base</td><td>Vocal3倍アピール</td><td>初期</td></tr>
    <tr><td>Upgraded</td><td>Vocal3.1倍アピール/Vocal5%UP[3ターン]</td><td>60</td></tr></table>
    <h2>サポートスキル</h2><table><tr><th rowspan="2">スキル名</th><th rowspan="2">スキル効果</th><th colspan="3">取得Lv/スキルLv</th></tr>
    <tr><th>1</th><th>60</th><th>最大</th></tr><tr><td>ボーカルマスタリーSP</td><td>SP+Lv</td><td>3</td><td>8</td><td>8</td></tr></table>
    <h2>サポートイベント</h2><table><tr><th>イベント名</th><th>Vo</th><th>Da</th><th>Vi</th><th>メンタル</th><th>SP</th></tr>
    <tr><td>Event</td><td>+10</td><td></td><td></td><td>+5</td><td>+10</td></tr></table>
    <h2>スキルパネル</h2><table><tr><th>SP</th><th colspan="2">解放</th></tr>
    <tr><th rowspan="2">30</th><th colspan="2">Skill(☆1)</th></tr><tr><td colspan="2">Vocal3.5倍アピール</td></tr></table>'''


class WikiTests(unittest.TestCase):
    def test_discovery_retains_cards_and_sr_partition_only(self):
        html = '''<a href="/shinycolors/%E3%80%90test%E3%80%91Idol">Card</a>
        <a href="/shinycolors/サポートアイドル一覧/分冊ファイル01">SR</a>
        <a href="https://enza.fun/【test】Idol">Game</a>
        <a href="/shinycolors/【test】Idol?cmd=edit">Edit</a>
        <a href="/shinycolors/foo.png">Image</a>'''
        links = discover_card_urls(html, "https://wikiwiki.jp/shinycolors/サポートアイドル一覧")
        self.assertEqual(len(links), 2)
        self.assertEqual({v["link_kind"] for v in links}, {"card", "index_partition"})

    def test_mocked_robots_and_checksum_cache(self):
        private = ROOT / "dev_assets/private"
        private.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as d:
            manager = SnapshotManager(Path(d))
            with patch.object(manager, "_read", side_effect=[b"User-agent: *\nAllow: /shinycolors/\nCrawl-delay: 2", b"<html>public</html>"]) as read:
                metadata = manager.fetch(URL, allow_network=True)
                self.assertEqual(read.call_count, 2)
                self.assertEqual(manager.min_interval, 2)
            text, cached = manager.read_cached(URL)
            self.assertEqual(text, "<html>public</html>")
            self.assertEqual(metadata, cached)
            (Path(d) / metadata["file"]).write_text("tampered", encoding="utf-8")
            with self.assertRaises(ValueError):
                manager.read_cached(URL)

    def test_robots_denies_page_before_fetch(self):
        private = ROOT / "dev_assets/private"
        private.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as d:
            manager = SnapshotManager(Path(d))
            with patch.object(manager, "_read", return_value=b"User-agent: *\nDisallow: /") as read:
                with self.assertRaises(ValueError):
                    manager.fetch(URL, allow_network=True)
                self.assertEqual(read.call_count, 1)

    def test_snapshot_keeps_exact_response_bytes_and_line_endings(self):
        raw = '<html>\n日本語\r\nLast-modified: today</html>\n'.encode("utf-8")
        private = ROOT / "dev_assets/private"
        private.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as d:
            manager = SnapshotManager(Path(d))
            with patch.object(manager, "_read", side_effect=[b"User-agent: *\nAllow: /", raw]):
                metadata = manager.fetch(URL, allow_network=True)
            stored = (Path(d) / metadata["file"]).read_bytes()
            self.assertEqual(stored, raw)
            self.assertEqual(metadata["content_sha256"], sha256(stored).hexdigest())
            self.assertEqual(manager.read_cached(URL)[0].encode("utf-8"), raw)

    def test_legacy_cache_is_verified_without_silent_checksum_repair(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            key = sha256(URL.encode()).hexdigest()
            raw = b"<html>\r\nlegacy</html>"
            metadata = dict(url=URL, file=key + ".html", content_sha256=sha256(raw).hexdigest(),
                            parser_version="true-end-card-v1")
            (cache / metadata["file"]).write_bytes(raw)
            (cache / (key + ".json")).write_text(json.dumps(metadata), encoding="utf-8")
            self.assertEqual(SnapshotManager(cache).read_cached(URL), (raw.decode(), metadata))

    def test_span_origin_and_panel_unique(self):
        parsed = CardPageParser().parse(small_card(), source_url=URL)
        self.assertEqual(len(parsed.panels), 1)
        panel = parsed.panels[0]
        self.assertEqual((panel.sp_cost, panel.required_lb), (30, 1))
        self.assertFalse(panel.prerequisites_known)

    def test_multiple_panel_tables_keep_distinct_identity_and_source_layout(self):
        first = small_card()
        panel_html = first[first.index('<h2>スキルパネル</h2>'):]
        card = CardPageParser().parse(first + panel_html, source_url=URL)
        self.assertEqual([p.id for p in card.panels], ["panel-table-1-1-1", "panel-table-2-1-1"])
        self.assertEqual([p.source_layout["table"] for p in card.panels], [1, 2])
        self.assertEqual(card.panels[0].source_layout, dict(table=1, row=1, column=1, rowspan=1, colspan=2))
        self.assertTrue(all(not p.prerequisites and not p.prerequisites_known for p in card.panels))
        self.assertIsNone(card.panel_topology)
        self.assertIn("panel_variant_selection", card.unknown_fields)
        self.assertEqual(CardData.from_dict(asdict(card)), card)

    def test_variant_layout_is_not_collapsed_into_another_table(self):
        variant = ('<h2>スキルパネル</h2><table><tr><th>SP</th><th colspan="2">解放</th></tr>'
                   '<tr><th rowspan="2">40</th><th>Left</th><th>Right</th></tr>'
                   '<tr><td>Vocal上限+25</td><td>Dance上限+50</td></tr></table>')
        card = CardPageParser().parse(small_card() + variant, source_url=URL)
        self.assertEqual([(p.id, p.sp_cost, p.source_layout["colspan"]) for p in card.panels],
                         [("panel-table-1-1-1", 30, 2), ("panel-table-2-1-1", 40, 1),
                          ("panel-table-2-1-2", 40, 1)])
        self.assertEqual(card.panels[-1].effects, {"Da": 50})
        self.assertFalse(any(p.prerequisites_known for p in card.panels))

    def test_missing_stats_are_error_not_zero(self):
        card = CardPageParser().parse(small_card(), source_url=URL)
        self.assertEqual(card.status, "PARSE_ERROR")
        self.assertIsNone(card.levels[65]["Vo"])
        self.assertEqual(card.max_levels[1], 65)

    def test_sparse_levels_max_column_and_owned_upgrade(self):
        card = CardPageParser().parse(small_card(), source_url=URL)
        self.assertEqual(card.support_skills[0].levels, {1: 3.0, 60: 8.0})
        self.assertEqual(card.owned_live_skills[-1].unlock_level, 60)
        self.assertEqual(card.owned_live_skills[-1].buff_amount, .05)
        self.assertEqual(card.owned_live_skills[-1].components, {"Vo": 3.1})

    def test_unknown_mechanic_never_disappears(self):
        skill = MechanicsParser.live("Vocal4倍アピール/[興味無視]/change", "x")
        self.assertTrue(skill["unsupported"])
        self.assertIsNone(MechanicsParser.live("全観客に謎の効果", "x"))
        self.assertEqual(MechanicsParser.support("ベストアドバイスマスタリー＋＋")[0], "unknown")
        linked = MechanicsParser.live("Vocal3倍アピール/(Link)Vocal1.5倍アピール", "x")
        self.assertTrue(any("ordered" in item for item in linked["unsupported"]))

    def test_excellent_appeal_parsed_as_live(self):
        skill = MechanicsParser.live("Excellent3倍アピール", "エクセレントアピールⅤ")
        self.assertEqual((skill["components"], skill["excellent"], skill["unsupported"]), ({}, 3.0, []))
        ignored = MechanicsParser.live("Excellent3倍アピール [興味無視]", "x")
        self.assertTrue(ignored["unsupported"])  # interest-ignore stays visible
        mixed = MechanicsParser.live("Excellent3倍アピール/Vocal2倍アピール", "x")
        self.assertTrue(any("ordered" in item for item in mixed["unsupported"]))

    def test_p_page_not_support_zero(self):
        card = CardPageParser().parse('<table><tr><th>アイドル</th><td>Idol</td></tr></table><h2>思い出アピール</h2>', source_url=URL, kind="P")
        self.assertEqual(card.status, "PARTIAL")
        self.assertIn("memory_appeal", card.unknown_fields)
        self.assertNotIn("support_skills", card.unknown_fields)
        self.assertIsNone(card.te_eligible)
        eligible = CardPageParser().parse(p_card('アイドルランクA達成 かつ W.I.N.G.優勝'), source_url=URL, kind="P")
        self.assertTrue(eligible.te_eligible)

    def test_true_end_spelling_and_condition_are_bound_to_the_table(self):
        for title in ("True End", "TrueEnd"):
            with self.subTest(title=title):
                standard = CardPageParser().parse(p_card('アイドルランクA達成 かつ W.I.N.G.優勝', title), source_url=URL, kind="P")
                self.assertTrue(standard.te_eligible)
                self.assertEqual(standard.te_condition, 'アイドルランクA達成 かつ W.I.N.G.優勝')
                self.assertEqual(standard.provenance["te_condition"]["parser_version"], PARSER_VERSION)
                fans = CardPageParser().parse(p_card('ファン数50万人達成 かつ W.I.N.G.優勝', title), source_url=URL, kind="P")
                self.assertIsNone(fans.te_eligible)
                self.assertEqual(fans.te_condition, 'ファン数50万人達成 かつ W.I.N.G.優勝')
                self.assertIn("te_eligible", fans.unknown_fields)
                self.assertEqual(CardData.from_dict(asdict(fans)), fans)

    def test_generic_incomplete_alternative_or_conflicting_conditions_stay_unknown(self):
        samples = [
            p_card('アイドルランクA達成') + '<p>W.I.N.G.優勝</p>',
            p_card('アイドルランクA達成 または W.I.N.G.優勝'),
            p_card('アイドルランクA達成 かつ W.I.N.G.優勝 追加条件'),
            p_card('未知条件') + '<p>アイドルランクA達成 かつ W.I.N.G.優勝</p>',
            '<p>True End 条件 アイドルランクA達成 かつ W.I.N.G.優勝</p>',
            p_card('アイドルランクA達成 かつ W.I.N.G.優勝') + p_card('ファン数50万人達成 かつ W.I.N.G.優勝')]
        for html in samples:
            with self.subTest(html=html):
                parsed = CardPageParser().parse(html, source_url=URL, kind="P")
                self.assertIsNone(parsed.te_eligible)

    def test_host_and_network_optin(self):
        for url in ("https://enza.fun/game", "https://wikiwiki.jp/shinycolors/x?cmd=edit", "https://u:p@wikiwiki.jp/shinycolors/x", "http://wikiwiki.jp/shinycolors/x", "https://wikiwiki.jp/other/x"):
            with self.assertRaises(ValueError):
                public_url(url)
        with tempfile.TemporaryDirectory() as d:
            manager = SnapshotManager(Path(d))
            with patch.object(manager, "_read") as read:
                with self.assertRaises(ValueError):
                    manager.fetch(URL)
                read.assert_not_called()

    def test_stopped_crawler_does_not_retry(self):
        with tempfile.TemporaryDirectory() as d:
            manager = SnapshotManager(Path(d))
            manager.stopped = True
            with patch.object(manager.opener, "open") as opened:
                with self.assertRaises(RuntimeError):
                    manager._read(URL)
                opened.assert_not_called()

    def test_http_429_stops_without_automatic_retry(self):
        with tempfile.TemporaryDirectory() as d:
            manager = SnapshotManager(Path(d))
            with patch.object(manager.opener, "open", side_effect=HTTPError(URL, 429, "limited", {}, None)) as opened, patch("agent.core.true_end.wiki.time.sleep"):
                with self.assertRaises(HTTPError):
                    manager._read(URL)
                self.assertTrue(manager.stopped)
                with self.assertRaises(RuntimeError):
                    manager._read(URL)
                self.assertEqual(opened.call_count, 1)

    def test_override_requires_provenance_and_keeps_unknowns(self):
        db = CardDatabase()
        card = CardPageParser().parse(small_card(), source_url=URL)
        db.add(card)
        with self.assertRaises(ValueError):
            db.override(card.card_id, "levels", {}, source_url=URL, reason="", reviewed_at="2026-10-05")
        updated = db.override(card.card_id, "levels", {60: dict(Vo=100, Da=110, Vi=120, Me=130)}, source_url=URL, reason="reviewed game frame", reviewed_at="2026-10-05")
        self.assertEqual(updated.provenance["levels"]["status"], "MANUAL_OVERRIDE")
        self.assertIn("previous_value", updated.provenance["levels"])

    def test_private_real_html_when_present(self):
        cache = ROOT / "dev_assets/private/true_end/wiki_cache"
        manifest = cache / "manifest.json"
        if not manifest.exists():
            self.skipTest("private audit samples unavailable")
        entries = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(len(entries), 5)
        for item in entries:
            with self.subTest(name=item["name"]):
                html = (cache / Path(item["file"]).name).read_text(encoding="utf-8")
                card = CardPageParser().parse(html, source_url=item["url"], rarity="SR" if "2018" in item["name"] else None)
                self.assertNotEqual(card.idol, "UNKNOWN")
                self.assertEqual(card.status, "PARTIAL", card.unknown_fields)
                self.assertEqual(len(card.panels), 8 if card.max_levels[4] == 70 else 10)
                self.assertTrue(card.support_skills)
                self.assertTrue(card.owned_live_skills)
                lv = card.max_levels[4]
                features = extract_card_features(card, Ownership(card.card_id, 4, lv))
                self.assertFalse(features.unknowns, features.unknowns)


if __name__ == "__main__":
    unittest.main()
