"""Incremental library updates: offline, isolated, no user profile or image writes."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import update_card_library as update
from tools import shinycolorsdb_import as imp
from agent.core import team_recommend


CARD = {"enzaId": "2040060210", "cardUuid": "66666666-7777-8888-9999-aaaaaaaaaaaa",
        "cardType": "S_SSR", "cardHash": "h1", "cardName": "Example", "panelSPOffset": 0,
        "idol": {"idolName": "Example"}}


class CardLibraryUpdateTest(unittest.TestCase):
    def setUp(self):
        imp.PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)   # absent in a fresh clone
        self.temp = tempfile.TemporaryDirectory(dir=imp.PRIVATE_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        update.write_json(self.root / "index.json", [CARD])
        self.index = imp.load_index([self.root / "index.json"])
        # refresh_icons also fetches the pinned item icons: no network here, and an existing manifest
        from tools import ui_game_assets_fetch as fetch
        from tools import ui_game_assets as assets
        self.items = []
        for target, name, value in ((fetch, "fetch_enza", lambda *_a, **_k: list(self.items)),
                                    (fetch, "fetch_tiles", lambda *_a, **_k: []),
                                    (assets, "OUT_DIR", self.root / "game-assets")):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.root / "game-assets").mkdir()
        (self.root / "game-assets" / "manifest.json").write_text("{}", encoding="utf-8")

    def client(self, card=CARD):
        responses = [(200, {}, b""), (200, {"Content-Type": "application/json"}, json.dumps(card).encode())]
        return imp.Importer(self.root, transport=lambda *_: responses.pop(0), sleep=lambda _: None,
                            clock=lambda: 0)

    def test_unchanged_does_not_fetch_convert_or_create_snapshot(self):
        report = update.update_plan(self.index, {CARD["enzaId"]: {"card_hash": "h1", "card_uuid": CARD["cardUuid"]}})
        client = mock.Mock()
        with mock.patch.object(update.converter, "convert_snapshot", side_effect=AssertionError("conversion")):
            result = update.apply_update(self.root, self.index, report, client)
        self.assertEqual(result["status"], "unchanged")
        self.assertFalse(result["rebuilt"])
        self.assertFalse(client.fetch.called)
        self.assertFalse(list(self.root.glob("snapshot-*")))

    def test_new_card_published_and_personal_files_unchanged(self):
        profile = self.root / "user-profile.json"
        profile.write_bytes(b'{"owned":{"2040060210":{"level":63,"limit_break":2}},"books":[1]}')
        before = profile.read_bytes()
        report = update.update_plan(self.index, {})
        result = update.apply_update(self.root, self.index, report, self.client(), wiki_cards=[])
        folder = self.root / result["snapshot"]
        self.assertTrue((folder / "merged-update.json").is_file())
        self.assertEqual(update.cached_cards(self.root)[CARD["enzaId"]]["card_hash"], "h1")
        self.assertEqual(profile.read_bytes(), before)
        self.assertEqual(result["updated_cards"], 1)
        self.assertEqual(update.update_plan(self.index, update.cached_cards(self.root))["pending"], [])

    def test_version_ignores_index_file_location_but_tracks_hash(self):
        old = update.update_plan(self.index, {})["source_version"]
        changed = {key: {**row, "index_file": "different", "card_hash": "new"} for key, row in self.index.items()}
        self.assertNotEqual(old, update.update_plan(changed, {})["source_version"])
        changed[CARD["enzaId"]]["card_hash"] = "h1"
        self.assertEqual(old, update.update_plan(changed, {})["source_version"])

    def test_hash_mismatch_never_activates_snapshot(self):
        report = update.update_plan(self.index, {})
        with self.assertRaises(imp.ImportRefused):
            update.apply_update(self.root, self.index, report, self.client({**CARD, "cardHash": "drift"}), wiki_cards=[])
        self.assertFalse(list(self.root.glob("snapshot-*")))
        self.assertEqual(update.cached_cards(self.root), {})

    def test_new_merged_card_outranks_old_merged_recommendation(self):
        old = self.root / "snapshot-old"
        old.mkdir()
        update.write_json(old / "manifest.json", {"generated_at": "2020-01-01", "entries": []})
        raw = update.converter.convert_card({**CARD, "cardName": "Old"})
        update.write_json(old / "merged-old.json", {"cards": [raw]})
        result = update.apply_update(self.root, self.index, update.update_plan(self.index, {}), self.client(), wiki_cards=[])
        library = team_recommend.load_library(self.root, legacy=(), id_map=mock.Mock())
        self.assertEqual(library["cards"][CARD["enzaId"]]["name"], "Example")
        self.assertEqual(library["sources"][CARD["enzaId"]], "merged:" + result["snapshot"])

    def test_one_batch_limit_leaves_remaining_cards(self):
        index = {**self.index, "2040060220": {**self.index[CARD["enzaId"]], "card_hash": "h2"}}
        report = update.update_plan(index, {})
        result = update.apply_update(self.root, index, report, self.client(), max_cards=1, wiki_cards=[])
        self.assertEqual(result["remaining_cards"], 1)
        self.assertEqual(update.update_plan(index, update.cached_cards(self.root))["pending"], ["2040060220"])

    def catalog_client(self, count, *, fail_after=None):
        cards = [{**CARD, "enzaId": str(2040060000 + i),
                  "cardUuid": f"66666666-7777-8888-9999-{i:012x}"} for i in range(count)]
        by_uuid = {card["cardUuid"]: card for card in cards}
        calls = []
        details = []
        def transport(url, _headers):
            calls.append(url)
            if url.endswith("/robots.txt"):
                return 200, {}, b""
            if url.endswith("/idolList"):
                payload = [{"idolId": 1}]
            elif "/idolInfo?" in url:
                payload = {"cardLists": cards}
            else:
                if fail_after is not None and len(details) >= fail_after:
                    raise OSError("offline fixture: detail unavailable")
                payload = by_uuid[url.split("=")[-1]]
                details.append(payload["enzaId"])
            return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode()
        client = imp.Importer(self.root, transport=transport, sleep=lambda _: None, clock=lambda: 0)
        return client, calls, details

    def test_all_initializes_empty_library_in_bounded_atomic_batches(self):
        client, calls, details = self.catalog_client(205)
        progress = []
        with mock.patch.object(update, "cached_wiki_cards", return_value=[]):
            result = update.run_update(self.root, client=client, all_cards=True, progress=progress.append)
        self.assertEqual(result["status"], "updated")
        self.assertTrue(result["complete"])
        self.assertTrue(result["cards_complete"])
        self.assertEqual(result["local_cards"], 205)
        self.assertEqual(result["updated_cards"], 205)
        self.assertEqual(result["remaining_cards"], 0)
        self.assertEqual(len(result["added"]), 205)
        self.assertEqual(result["changed"], [])
        self.assertEqual(result["pending"], [])
        self.assertEqual(result["completed_batches"], 3)
        sizes = [len(json.loads((self.root / path / "manifest.json").read_text())["entries"])
                 for path in result["snapshots"]]
        self.assertEqual(sizes, [100, 100, 5])
        self.assertEqual(len(update.cached_cards(self.root)), 205)
        self.assertEqual(len(set(details)), 205)
        self.assertEqual(sum(url.endswith("/idolList") for url in calls), 1)
        self.assertEqual(sum("/idolInfo?" in url for url in calls), 1)
        self.assertEqual([row["phase"] for row in progress], ["catalog", "cards", "cards", "cards", "cards"])
        self.assertEqual([row["updated_cards"] for row in progress], [0, 0, 100, 200, 205])
        self.assertIsNone(progress[0]["total_cards"])
        self.assertEqual([row["total_cards"] for row in progress[1:]], [205] * 4)
        self.assertTrue(all(row["event"] == "card_library_progress" for row in progress))

    def test_all_unchanged_library_does_not_fetch_details_or_rebuild(self):
        client, _, _ = self.catalog_client(1)
        with mock.patch.object(update, "cached_wiki_cards", return_value=[]):
            update.run_update(self.root, client=client, all_cards=True)
        client, _, details = self.catalog_client(1, fail_after=0)
        with mock.patch.object(update.converter, "convert_snapshot", side_effect=AssertionError("conversion")):
            result = update.run_update(self.root, client=client, all_cards=True)
        self.assertEqual(result["status"], "unchanged")
        self.assertTrue(result["complete"])
        self.assertEqual(result["local_cards"], 1)
        self.assertEqual(result["updated_cards"], 0)
        self.assertEqual(result["completed_batches"], 0)
        self.assertEqual(details, [])
        self.assertEqual(len(list(self.root.glob("snapshot-*"))), 1)

    def test_all_failure_keeps_enabled_batch_and_reports_remaining_not_success(self):
        client, _, details = self.catalog_client(205, fail_after=103)
        with mock.patch.object(update, "cached_wiki_cards", return_value=[]), \
             mock.patch.object(update, "refresh_icons") as icons:
            result = update.run_update(self.root, client=client, all_cards=True, with_icons=True)
        self.assertEqual(result["status"], "stopped")
        self.assertFalse(result["complete"])
        self.assertFalse(result["cards_complete"])
        self.assertEqual(result["local_cards"], 100)
        self.assertEqual(result["updated_cards"], 100)
        self.assertEqual(result["remaining_cards"], 105)
        self.assertEqual(len(result["pending"]), 105)
        self.assertEqual(result["completed_batches"], 1)
        self.assertEqual(len(update.cached_cards(self.root)), 100)
        self.assertEqual(len(details), 103)
        self.assertIn("detail unavailable", result["error"])
        icons.assert_called_once()           # 2026-11-23: images come first, before any card detail
        self.assertEqual(len(list(self.root.glob("pending-*"))), 1)

    def test_default_still_only_fetches_one_batch(self):
        client, _, details = self.catalog_client(3)
        with mock.patch.object(update, "cached_wiki_cards", return_value=[]):
            result = update.run_update(self.root, client=client, max_cards=2)
        self.assertEqual(result["status"], "updated")
        self.assertFalse(result["complete"])
        self.assertEqual(result["updated_cards"], 2)
        self.assertEqual(result["remaining_cards"], 1)
        self.assertEqual(len(details), 2)

    def test_cli_all_failure_prints_and_saves_partial_summary(self):
        client, _, _ = self.catalog_client(3, fail_after=1)
        with mock.patch.object(update, "DEFAULT_ROOT", self.root), \
             mock.patch.object(update.imp, "Importer", return_value=client), \
             mock.patch.object(update, "cached_wiki_cards", return_value=[]), \
             mock.patch.object(update, "print_json") as output:
            code = update.main(["--fetch", "--all", "--max-cards", "1"])
        self.assertEqual(code, 1)
        result = output.call_args.args[0]
        self.assertEqual(result["updated_cards"], 1)
        self.assertEqual(result["remaining_cards"], 2)
        self.assertFalse(result["complete"])
        self.assertEqual(json.loads((self.root / "last-update.json").read_text()), result)

    def test_cli_progress_is_json_lines_on_stderr_only(self):
        client, _, _ = self.catalog_client(2)
        stdout, stderr = io.BytesIO(), io.BytesIO()
        with mock.patch.object(update, "DEFAULT_ROOT", self.root), \
             mock.patch.object(update.imp, "Importer", return_value=client), \
             mock.patch.object(update, "cached_wiki_cards", return_value=[]), \
             mock.patch.object(update.sys, "stdout", mock.Mock(buffer=stdout)), \
             mock.patch.object(update.sys, "stderr", mock.Mock(buffer=stderr)):
            code = update.main(["--fetch", "--all", "--max-cards", "1", "--progress-json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["updated_cards"], 2)
        events = [json.loads(line) for line in stderr.getvalue().splitlines()]
        self.assertEqual([row["updated_cards"] for row in events], [0, 0, 1, 2])
        self.assertTrue(all(row["event"] == "card_library_progress" for row in events))

    def test_all_summary_preserves_added_and_changed_across_batches(self):
        client, _, _ = self.catalog_client(1)
        with mock.patch.object(update, "cached_wiki_cards", return_value=[]):
            update.run_update(self.root, client=client)
        client, _, details = self.catalog_client(3)
        # Change the catalogue and detail hash together, retaining exact identity.
        transport = client.transport
        def changed_transport(url, headers):
            status, response_headers, body = transport(url, headers)
            return status, response_headers, body.replace(b'"h1"', b'"h2"')
        client.transport = changed_transport
        with mock.patch.object(update, "cached_wiki_cards", return_value=[]):
            result = update.run_update(self.root, client=client, all_cards=True, max_cards=1)
        self.assertEqual(result["added"], ["2040060001", "2040060002"])
        self.assertEqual(result["changed"], ["2040060000"])
        self.assertEqual(result["updated_cards"], 3)
        self.assertEqual(len(set(details)), 3)
        self.assertTrue(result["complete"])

    def test_catalog_failure_still_returns_incomplete_machine_summary(self):
        with mock.patch.object(update, "fetch_catalog", side_effect=OSError("offline catalogue")):
            result = update.run_update(self.root, client=mock.Mock(), all_cards=True)
        self.assertEqual(result["status"], "stopped")
        self.assertFalse(result["complete"])
        self.assertEqual(result["updated_cards"], 0)
        self.assertIsNone(result["remaining_cards"])
        self.assertEqual(result["snapshots"], [])

    def test_batch_limit_cannot_exceed_importer_cap(self):
        with self.assertRaises(imp.ImportRefused):
            update.apply_update(self.root, self.index, update.update_plan(self.index, {}),
                                mock.Mock(), max_cards=imp.HARD_MAX_IDS + 1)
        self.assertFalse(list(self.root.glob("pending-*")))

    def test_icon_cache_writes_without_git_in_a_portable_install(self):
        from tools import ui_game_assets_fetch as fetch
        update.write_json(self.root / "PACKAGE_MANIFEST.json", {"name": "MaaShinyColors"})
        update.write_json(self.root / "project_policy.json", {})
        destination = self.root / "dev_assets/private/game-assets-src/icon.png"
        with mock.patch.object(fetch, "ROOT", self.root), mock.patch.object(fetch, "is_gitignored", return_value=False):
            fetch._write(destination, b"icon")
            with self.assertRaises(ValueError):
                fetch._write(self.root / "icon.png", b"outside cache")
        self.assertEqual(destination.read_bytes(), b"icon")

    def test_icon_path_reuses_fetcher_without_rebuild_when_unchanged(self):
        from tools import ui_game_assets_fetch as fetch
        from tools import ui_game_assets as assets
        path = self.root / "cards.json"
        update.write_json(path, {"cards": [CARD]})
        with mock.patch.object(fetch, "DB_CARDS_FILE", path), mock.patch.object(fetch, "Fetcher"), \
             mock.patch.object(fetch, "fetch_db", return_value={"downloaded": 0, "stopped": None, "failed": []}), \
             mock.patch.object(assets, "build") as build:
            self.assertFalse(update.refresh_icons()["rebuilt"])
            build.assert_not_called()

    def test_new_install_gets_item_icons_and_a_built_manifest(self):
        # 2026-11-22: a fresh package had no ui/game-assets at all; item icons arrive with the card icons and the
        # assets are built even when no card icon changed
        from tools import ui_game_assets_fetch as fetch
        from tools import ui_game_assets as assets
        path = self.root / "cards.json"
        update.write_json(path, {"cards": [CARD]})
        (self.root / "game-assets" / "manifest.json").unlink()
        self.items = [{"id": "a", "status": "downloaded"}, {"id": "b", "status": "sha256_changed"}]
        with mock.patch.object(fetch, "DB_CARDS_FILE", path), mock.patch.object(fetch, "Fetcher"),              mock.patch.object(fetch, "fetch_db", return_value={"downloaded": 0, "stopped": None, "failed": []}),              mock.patch.object(assets, "build") as build, mock.patch.object(update, "sync_host_assets", return_value=True):
            result = update.refresh_icons()
        build.assert_called_once()
        self.assertEqual((result["rebuilt"], result["items_downloaded"], result["items_failed"]), (True, 1, 1))

    def test_changed_and_removed_are_separate_no_deletion(self):
        previous = {CARD["enzaId"]: {"card_hash": "old", "card_uuid": CARD["cardUuid"]}, "1040130050": {}}
        report = update.update_plan(self.index, previous)
        self.assertEqual(report["changed"], [CARD["enzaId"]])
        self.assertEqual(report["absent_from_catalog"], ["1040130050"])
        self.assertEqual(report["added"], [])

    def test_missing_one_icon_still_rebuilds_the_other_available_cards(self):
        from tools import ui_game_assets_fetch as fetch
        from tools import ui_game_assets as assets
        path = self.root / "cards.json"
        update.write_json(path, {"cards": [CARD]})
        report = {"downloaded": 1, "stopped": None, "failed": [{"card_id": "old-card"}]}
        with mock.patch.object(fetch, "DB_CARDS_FILE", path), mock.patch.object(fetch, "Fetcher"), \
             mock.patch.object(fetch, "fetch_db", return_value=report), mock.patch.object(assets, "build") as build, \
             mock.patch.object(update, "sync_host_assets", return_value=True) as sync:
            result = update.refresh_icons()
        build.assert_called_once()
        sync.assert_called_once()
        self.assertTrue(result["rebuilt"])
        self.assertTrue(result["host_synced"])
        self.assertEqual(len(result["failed"]), 1)

    def test_complete_catalog_builds_available_icons_after_download_budget_stop(self):
        from tools import ui_game_assets_fetch as fetch
        from tools import ui_game_assets as assets
        path = self.root / "cards.json"
        update.write_json(path, {"cards": [CARD]})
        report = {"downloaded": 1, "stopped": "budget", "failed": [], "catalog_complete": True}
        with mock.patch.object(fetch, "DB_CARDS_FILE", path), mock.patch.object(fetch, "Fetcher"), \
             mock.patch.object(fetch, "fetch_db", return_value=report), mock.patch.object(assets, "build") as build, \
             mock.patch.object(update, "sync_host_assets", return_value=True):
            self.assertTrue(update.refresh_icons()["rebuilt"])
        build.assert_called_once()

    def test_icon_failure_keeps_completed_card_summary(self):
        client, _, _ = self.catalog_client(1)
        progress = []
        with mock.patch.object(update, "cached_wiki_cards", return_value=[]), \
             mock.patch.object(update, "refresh_icons", return_value={"failed": ["missing"]}):
            result = update.run_update(self.root, client=client, all_cards=True, with_icons=True,
                                       progress=progress.append)
        self.assertEqual(result["status"], "stopped")
        self.assertFalse(result["complete"])
        self.assertTrue(result["cards_complete"])
        self.assertEqual(result["local_cards"], 1)
        self.assertEqual(progress[0]["phase"], "icons")       # images first
        self.assertEqual(progress[-1]["phase"], "cards")
        self.assertEqual(progress[-1]["updated_cards"], 1)

    def test_portable_host_receives_generated_assets_without_personal_files(self):
        source = self.root / "ui" / "game-assets"
        source.mkdir(parents=True)
        (source / "manifest.js").write_text("export default {};", encoding="utf-8")
        (source / "owned-levels.js").write_text("old-source-levels", encoding="utf-8")
        with mock.patch.object(update, "ROOT", self.root):
            self.assertFalse(update.sync_host_assets(source))
            host = self.root / "host"
            (host / "ui").mkdir(parents=True)
            (host / "MaaShinyColors.BrowserHost.exe").write_bytes(b"fixture")
            (host / "ui" / "index.html").write_text("fixture", encoding="utf-8")
            target = host / "ui" / "game-assets"
            target.mkdir()
            (target / "owned-levels.js").write_text("current-levels", encoding="utf-8")
            personal = self.root / "profile.json"
            personal.write_bytes(b"keep")
            self.assertTrue(update.sync_host_assets(source))
            self.assertEqual((target / "manifest.js").read_bytes(), (source / "manifest.js").read_bytes())
            self.assertEqual((target / "owned-levels.js").read_text(encoding="utf-8"), "current-levels")
            self.assertEqual(personal.read_bytes(), b"keep")

    def test_catalog_uses_full_idol_lists_and_rejects_partial(self):
        client = mock.Mock()
        client.fetch_index.side_effect = [[{"idolId": 1}], {"cardLists": [CARD]}]
        self.assertEqual(set(update.fetch_catalog(client, self.root)), {CARD["enzaId"]})
        urls = [call.args[0] for call in client.fetch_index.call_args_list]
        self.assertEqual(urls, [imp.API_BASE + "/info/idolList", imp.API_BASE + "/info/idolInfo?idolId=1"])
        catalog = (self.root / "catalog.json").read_bytes()
        client.fetch_index.side_effect = [[{"idolId": 1}], {"cardLists": []}]
        with self.assertRaises(imp.ImportRefused):
            update.fetch_catalog(client, self.root)
        self.assertEqual((self.root / "catalog.json").read_bytes(), catalog)

    def test_index_etag_reuses_verified_cache(self):
        calls = []
        responses = [(200, {}, b""), (200, {"Content-Type": "application/json", "ETag": "one"}, b"[]"),
                     (304, {}, b"")]
        def transport(url, headers):
            calls.append((url, headers))
            return responses.pop(0)
        client = imp.Importer(self.root, transport=transport, sleep=lambda _: None, clock=lambda: 0)
        url = imp.API_BASE + "/info/idolList"
        self.assertEqual(client.fetch_index(url, self.root), [])
        self.assertEqual(client.fetch_index(url, self.root), [])
        self.assertEqual(calls[-1][1]["If-None-Match"], "one")

    def test_index_endpoint_queries_are_bounded(self):
        for query in ("", "?idolId=-1", "?idolId=1&extra=1", "?idolId=10000"):
            with self.assertRaises(imp.ImportRefused):
                imp.check_url(imp.API_BASE + "/info/idolInfo" + query)
        imp.check_url(imp.API_BASE + "/info/idolInfo?idolId=1")


if __name__ == "__main__":
    unittest.main()
