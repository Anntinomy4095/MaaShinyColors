"""Offline source dispatch and cached English CLI regressions."""
from contextlib import redirect_stderr
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.core.true_end.wiki import CardPageParser, parse_card_page
from tools import true_end_wiki
from test_true_end_english_wiki import fixture
from test_true_end_wiki import small_card

JP = "https://wikiwiki.jp/shinycolors/【test】Idol"
EN = "https://shinycolors.wiki/wiki/S-SSR1_Test"
P_EN = "https://shinycolors.wiki/wiki/P-SSR1_Test"


class WikiDispatchTests(unittest.TestCase):
    def test_english_and_japanese_dispatch(self):
        jp = parse_card_page(small_card(), source_url=JP)
        self.assertEqual(jp, CardPageParser().parse(small_card(), source_url=JP))
        en = parse_card_page(fixture(), source_url=EN)
        self.assertEqual(en.kind, "S")
        self.assertEqual(en.provenance["card"]["parser_version"], "true-end-english-card-v1")
        p = parse_card_page('<ul><li>JP Card Name: 【test】Idol</li></ul>', source_url=P_EN)
        self.assertEqual(p.kind, "P")

    def test_japanese_p_inference_and_explicit_unknown_kind(self):
        p = parse_card_page('<h2>思い出アピール</h2>', source_url=JP)
        self.assertEqual(p.kind, "P")
        with self.assertRaises(ValueError):
            parse_card_page('<p>unknown card</p>', source_url=JP)
        self.assertEqual(parse_card_page('<p>unknown card</p>', source_url=JP, kind="P").kind, "P")

    def test_host_url_and_kind_binding(self):
        for url, html, kind in (("https://enza.fun/game", fixture(), None),
                                (EN + "?action=edit", fixture(), None),
                                (EN, fixture(), "P"), (JP, small_card(), "P")):
            with self.subTest(url=url, kind=kind), self.assertRaises(ValueError):
                parse_card_page(html, source_url=url, kind=kind)

    def test_cli_cached_english_p_preserves_byte_sha_and_infers_kind(self):
        # CRLF must not be translated before the provenance digest is computed.
        raw = b'<ul>\r\n<li>JP Card Name: test</li>\r\n</ul>\r\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source, output = path / "cached.html", path / "cards.json"
            source.write_bytes(raw)
            argv = ["true_end_wiki.py", "--url", P_EN, "--html", str(source), "--output", str(output)]
            with patch("sys.argv", argv), patch.object(true_end_wiki.SnapshotManager, "fetch") as fetch:
                self.assertEqual(true_end_wiki.main(), 0)
                fetch.assert_not_called()
            card, = json.loads(output.read_text(encoding="utf-8"))["cards"]
            self.assertEqual(card["kind"], "P")
            self.assertEqual(card["provenance"]["card"]["content_sha256"], sha256(raw).hexdigest())

    def test_english_fetch_is_explicitly_unsupported_without_network(self):
        argv = ["true_end_wiki.py", "--url", EN, "--fetch", "--output", "unused.json"]
        stderr = io.StringIO()
        with patch("sys.argv", argv), patch.object(true_end_wiki.SnapshotManager, "fetch") as fetch, redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as error:
                true_end_wiki.main()
            self.assertEqual(error.exception.code, 2)
            fetch.assert_not_called()
        self.assertIn("抓取尚未支持", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
