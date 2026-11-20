"""Bounds and safety of the ShinyColorsDB importer; no network is used."""
import json
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

from tools import shinycolorsdb_import as imp

UUID_P = "11111111-2222-3333-4444-555555555555"
UUID_S = "66666666-7777-8888-9999-aaaaaaaaaaaa"
INDEX = [
    {"enzaId": "1040130050", "cardUuid": UUID_P, "cardType": "P_SSR", "cardHash": "h1", "cardName": "P card"},
    {"enzaId": "2040060210", "cardUuid": UUID_S, "cardType": "S_SSR", "cardHash": "h2", "cardName": "S card"},
]


def card_body(enza, uuid, ctype, card_hash="h"):
    return json.dumps({"enzaId": enza, "cardUuid": uuid, "cardType": ctype, "cardHash": card_hash}).encode()


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClock:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


JSON = {"Content-Type": "application/json; charset=utf-8"}


class ParseIdsTest(unittest.TestCase):
    def test_requires_explicit_ids(self):
        with self.assertRaises(imp.ImportRefused):
            imp.parse_ids([""])

    def test_rejects_non_enza_ids(self):
        for bad in ("123", "3040010010", "abcdefghij", "2040010010x", "2140010010", "1840010010"):
            with self.assertRaises(imp.ImportRefused):
                imp.parse_ids([bad])

    def test_accepts_special_idolroad_and_pcup_ids(self):
        self.assertEqual(imp.parse_ids(["1940010010,2940162510"]), ["1940010010", "2940162510"])

    def test_deduplicates_and_splits(self):
        self.assertEqual(imp.parse_ids(["2040060210, 1040130050", "2040060210"]), ["2040060210", "1040130050"])

    def test_more_than_twenty_requires_allow_many(self):
        ids = [f"20400{i:05d}" for i in range(21)]
        with self.assertRaises(imp.ImportRefused):
            imp.parse_ids([",".join(ids)])
        self.assertEqual(len(imp.parse_ids([",".join(ids)], allow_many=True)), 21)
        self.assertEqual(len(imp.parse_ids([",".join(ids[:20])])), 20)

    def test_hard_cap_even_with_allow_many(self):
        ids = [f"20400{i:05d}" for i in range(imp.HARD_MAX_IDS + 1)]
        with self.assertRaises(imp.ImportRefused):
            imp.parse_ids([",".join(ids)], allow_many=True)

    def test_cli_refuses_many_without_flag_before_any_request(self):
        ids = ",".join(f"20400{i:05d}" for i in range(21))
        with mock.patch.object(imp, "urllib_transport", side_effect=AssertionError("network")):
            self.assertEqual(imp.main(["--ids", ids, "--fetch"]), 2)


class IndexAndPlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "index.json"
        self.path.write_text(json.dumps({"produce": INDEX[:1], "support": INDEX[1:]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_mapping_and_missing_ids_are_not_guessed(self):
        index = imp.load_index([self.path])
        jobs, missing = imp.plan(["1040130050", "2040060211"], index)
        self.assertEqual([j["enza_id"] for j in jobs], ["1040130050"])
        self.assertEqual(missing, ["2040060211"])
        self.assertTrue(jobs[0]["url"].endswith("/info/pCardInfo?cardId=" + UUID_P))

    def test_conflicting_uuid_refused(self):
        other = Path(self.tmp.name) / "other.json"
        other.write_text(json.dumps([{**INDEX[0], "cardUuid": UUID_S}]), encoding="utf-8")
        with self.assertRaises(imp.ImportRefused):
            imp.load_index([self.path, other])

    def test_url_allowlist_and_private_output(self):
        imp.check_url("https://api.shinycolors.moe/info/sCardInfo?cardId=" + UUID_S)
        for bad in ("http://api.shinycolors.moe/info/sCardInfo", "https://api.shinycolors.moe/info/getAllTable",
                    "https://evil.example/info/sCardInfo", "https://cf-static.shinycolors.moe/images/x.png"):
            with self.assertRaises(imp.ImportRefused):
                imp.check_url(bad)
        with self.assertRaises(imp.ImportRefused):
            imp.resolve_out_dir(Path(self.tmp.name), "20261006")
        self.assertTrue(str(imp.resolve_out_dir(None, "20261006")).endswith("snapshot-20261006"))


class ImporterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "snapshot-20261006"
        index_path = Path(self.tmp.name) / "index.json"
        index_path.write_text(json.dumps(INDEX), encoding="utf-8")
        self.jobs, _ = imp.plan(["1040130050", "2040060210"], imp.load_index([index_path]))

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, responses, etags=None):
        clock = FakeClock()
        transport = FakeTransport(responses)
        importer = imp.Importer(self.out, transport=transport, sleep=clock.sleep, clock=clock.clock, etags=etags)
        return importer, transport, clock

    def test_serial_fetch_with_gaps_robots_and_manifest(self):
        importer, transport, clock = self.make([
            (200, {"Content-Type": "text/plain"}, b"User-agent: *\nDisallow:\n"),
            (200, {**JSON, "ETag": 'W/"a"'}, card_body("1040130050", UUID_P, "P_SSR", "h1")),
            (200, {**JSON, "ETag": 'W/"b"'}, card_body("2040060210", UUID_S, "S_SSR", "h2")),
        ])
        manifest = importer.fetch(self.jobs)
        self.assertEqual(len(transport.calls), 3)
        self.assertTrue(transport.calls[0][0].endswith("/robots.txt"))
        self.assertEqual(len(clock.slept), 2)
        self.assertTrue(all(imp.MIN_INTERVAL <= s <= imp.MAX_INTERVAL for s in clock.slept))
        for _, headers in transport.calls:
            self.assertNotIn("Cookie", headers)
            self.assertIn("MaaShinyColorsResearch", headers["User-Agent"])
        saved = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["requests"], 3)
        entry = manifest["entries"][0]
        body = (self.out / entry["file"]).read_bytes()
        self.assertEqual(entry["sha256"], sha256(body).hexdigest())
        self.assertEqual((entry["card_hash"], entry["etag"], entry["card_hash_changed"]), ("h1", 'W/"a"', False))
        self.assertIn("retrieved_at", entry)

    def test_http_error_stops_without_retry(self):
        err = HTTPError("u", 429, "Too Many", {}, None)
        importer, transport, _ = self.make([(200, {}, b""), err, (200, JSON, b"{}")])
        with self.assertRaises(HTTPError):
            importer.fetch(self.jobs)
        self.assertEqual(len(transport.calls), 2)

    def test_non_200_status_stops(self):
        importer, transport, _ = self.make([(200, {}, b""), (403, JSON, b"")])
        with self.assertRaises(imp.ImportRefused):
            importer.fetch(self.jobs)
        self.assertEqual(len(transport.calls), 2)

    def test_robots_disallow_blocks_card_requests(self):
        importer, transport, _ = self.make([(200, {}, b"User-agent: *\nDisallow: /info/\n")])
        with self.assertRaises(imp.ImportRefused):
            importer.fetch(self.jobs)
        self.assertEqual(len(transport.calls), 1)

    def test_identity_mismatch_and_size_cap(self):
        importer, _, _ = self.make([(200, {}, b""), (200, JSON, card_body("1040130051", UUID_P, "P_SSR"))])
        with self.assertRaises(imp.ImportRefused):
            importer.fetch(self.jobs)
        importer, _, _ = self.make([(200, {}, b""), (200, JSON, b"x" * (imp.MAX_BODY_BYTES + 1))])
        with self.assertRaises(imp.ImportRefused):
            importer.fetch(self.jobs)

    def test_etag_if_none_match_and_304_reuse(self):
        old = Path(self.tmp.name) / "old.json"
        body = card_body("1040130050", UUID_P, "P_SSR", "h1")
        old.write_bytes(body)
        etags = {self.jobs[0]["url"]: {"etag": 'W/"a"', "path": old, "sha256": sha256(body).hexdigest()}}
        importer, transport, _ = self.make([(200, {}, b""), (304, {"ETag": 'W/"a"'}, b"")], etags)
        manifest = importer.fetch(self.jobs[:1])
        self.assertEqual(transport.calls[1][1].get("If-None-Match"), 'W/"a"')
        self.assertEqual(manifest["entries"][0]["http_status"], 304)
        self.assertEqual(manifest["entries"][0]["sha256"], sha256(body).hexdigest())

    def test_batches_merge_into_one_manifest(self):
        importer, _, _ = self.make([(200, {}, b""), (200, {**JSON, "ETag": "a"}, card_body("1040130050", UUID_P, "P_SSR"))])
        importer.fetch(self.jobs[:1])
        importer, _, _ = self.make([(200, {}, b""), (200, {**JSON, "ETag": "b"}, card_body("2040060210", UUID_S, "S_SSR"))])
        manifest = importer.fetch(self.jobs[1:])
        self.assertEqual([e["enza_id"] for e in manifest["entries"]], ["1040130050", "2040060210"])
        self.assertEqual(manifest["requests"], 4)
        # the same card fetched again replaces its entry instead of duplicating it
        importer, _, _ = self.make([(200, {}, b""), (200, {**JSON, "ETag": "c"}, card_body("2040060210", UUID_S, "S_SSR"))])
        manifest = importer.fetch(self.jobs[1:])
        self.assertEqual(sorted(e["enza_id"] for e in manifest["entries"]), ["1040130050", "2040060210"])
        self.assertEqual(manifest["requests"], 6)

    def test_stop_mid_batch_keeps_manifest_of_saved_cards(self):
        importer, _, _ = self.make([(200, {}, b""), (200, JSON, card_body("1040130050", UUID_P, "P_SSR")),
                                    (429, JSON, b"")])
        with self.assertRaises(imp.ImportRefused):
            importer.fetch(self.jobs)
        saved = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([e["enza_id"] for e in saved["entries"]], ["1040130050"])

    def test_drifted_existing_entry_refuses_merge(self):
        importer, _, _ = self.make([(200, {}, b""), (200, JSON, card_body("1040130050", UUID_P, "P_SSR"))])
        manifest = importer.fetch(self.jobs[:1])
        (self.out / manifest["entries"][0]["file"]).write_bytes(b"tampered")
        importer, transport, _ = self.make([(200, {}, b"")])
        with self.assertRaises(imp.ImportRefused):
            importer.fetch(self.jobs[1:])
        self.assertEqual(transport.calls, [])

    def test_previous_etags_ignores_drifted_files(self):
        snap = Path(self.tmp.name) / "snapshot-20261001"
        snap.mkdir()
        (snap / "a.json").write_bytes(b"{}")
        (snap / "manifest.json").write_text(json.dumps({"entries": [
            {"url": "u1", "etag": "e1", "file": "a.json", "sha256": sha256(b"{}").hexdigest()},
            {"url": "u2", "etag": "e2", "file": "a.json", "sha256": "0" * 64}]}), encoding="utf-8")
        known = imp.previous_etags(Path(self.tmp.name))
        self.assertEqual(set(known), {"u1"})


if __name__ == "__main__":
    unittest.main()
