"""Training target = any confirmed S card, or the default. Offline: fake DB index / fake locate, no device.

* Config: the card id option (``^2\\d{9}$``), the legacy names, and the empty default (first trainable of the sort).
* Identity: ``spec_for`` / ``check_tile`` of an uncalibrated card (DB matcher + frame tier only), look-alike stop.
* Default mode: the first bright (not dimmed) tile in reading order; an unclassifiable tile stops; the detail page
  binds an unidentified tile's name and S badge before any spend.
* Runtime: generic card identity is enabled; a verified max-level page can continue the bounded search.
"""
import datetime as dt
import shutil
import unittest
from pathlib import Path
from unittest import mock

from agent.core import card_inventory as ci
from agent.core import pipeline_actions as pa
from agent.core import training_select as ts
from agent.core import training_task as tt

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "dev_assets" / "private"
TABLE = pa.default_table()
ASAHI = tt.CARD_IDS[tt.CARD_ID]
MANO = "2040010010"        # a made-up SSR of idol 001 (only the id shape matters)
MANO2 = "2040010020"       # another card of the same idol (idol = id[3:6])
FIRST_BOX = (267, 184, 108, 108)
ORDER = ts.ListOrder("default", "desc")


def blank():
    from PIL import Image
    return Image.new("RGB", ci.CANVAS)


class FakeIndex:
    def __init__(self):
        self.meta = {
            ASAHI: {"idol": "芹沢あさひ", "title": "くっつく、くっつかない", "rarity": "UR"},
            MANO: {"idol": "櫻木真乃", "title": "ほわっと", "rarity": "SSR"},
            MANO2: {"idol": "櫻木真乃", "title": "ひだまりの中で", "rarity": "SR"},
            "2040030010": {"idol": "櫻木真乃", "title": "名前なし", "rarity": ""},
        }


class ConfigTests(unittest.TestCase):
    def test_card_id_option_any_s_card(self):
        config = tt.TrainingConfig.from_params({"card_id": MANO})
        self.assertEqual((config.card_id, config.selected, config.default_mode), (MANO, True, False))
        self.assertEqual(config.card, "")      # the name is bound from the DB index by the list step

    def test_empty_is_the_default_mode_not_a_skip(self):
        for params in ({}, {"card_id": ""}, {"card": "", "card_id": ""}):
            config = tt.TrainingConfig.from_params(params)
            self.assertEqual((config.card_id, config.selected, config.default_mode), ("", False, True))

    def test_legacy_name_and_id_agree(self):
        by_name = tt.TrainingConfig.from_params({"card": tt.CARD_ID})
        by_id = tt.TrainingConfig.from_params({"card_id": ASAHI})
        self.assertEqual((by_name.card_id, by_id.card), (ASAHI, tt.CARD_ID))
        with self.assertRaises(Exception):
            tt.TrainingConfig.from_params({"card": tt.CARD_ID, "card_id": MANO})

    def test_malformed_ids_are_refused(self):
        for bad in ("1050170010", "20501700", "20501700101", "2050170O10", " 2050170010", 2050170010, None):
            with self.assertRaises(Exception, msg=repr(bad)):
                tt.TrainingConfig.from_params({"card_id": bad})
        with self.assertRaises(Exception):
            tt.TrainingConfig.from_params({"card": "【別】カード"})

    def test_identity_uses_the_bound_name_and_never_an_empty_one(self):
        config = tt.TrainingConfig.from_params({"card_id": MANO})
        self.assertEqual(tt.identity_reasons(config, "【ほわっと】櫻木真乃"), ["card_name_unbound"])
        self.assertEqual(tt.identity_reasons(config, "【ほわっと】櫻木真乃", "【ほわっと】櫻木真乃"), [])
        self.assertEqual(tt.identity_reasons(config, "【ひだまりの中で】櫻木真乃", "【ほわっと】櫻木真乃"), ["identity_mismatch"])
        self.assertEqual(tt.identity_reasons(config, None, "【ほわっと】櫻木真乃"), ["card_name_unreadable"])
        # Live 2026-10-31: OCR reads the katakana middle dot as U+00B7.
        self.assertEqual(tt.identity_reasons(config, "【煌·煌·星·娘】幽谷霧子", "【煌・煌・星・娘】幽谷霧子"), [])
        self.assertEqual(tt.identity_reasons(config, "【煌·煌·星·娘】幽谷霧子", "【霧・霧・奇・譚】幽谷霧子"),
                         ["identity_mismatch"])


class SpecTests(unittest.TestCase):
    def test_spec_for_calibrated_and_uncalibrated(self):
        index = FakeIndex()
        self.assertIs(ts.spec_for(ASAHI, index.meta[ASAHI]), ts.TARGETS[tt.CARD_ID])
        spec = ts.spec_for(MANO, index.meta[MANO])
        self.assertEqual((spec.calibrated, spec.tier, spec.idol, spec.name), (False, "rainbow", "001", "【ほわっと】櫻木真乃"))
        self.assertIsNone(ts.load_reference(spec))          # no native template needed
        for card_id, meta in ((MANO, None), (MANO, {"idol": "x", "title": "y", "rarity": "??"}),
                              ("2040030010", index.meta["2040030010"]), ("1040010010", index.meta[MANO]), (None, None)):
            self.assertIsNone(ts.spec_for(card_id, meta), card_id)

    def tile(self, card_id, status="accepted"):
        return {"slot": "r1c1", "box": list(FIRST_BOX), "status": status, "card_id": card_id if status == "accepted" else None}

    def check(self, spec, tile, idol, tier):
        with mock.patch.object(ci, "frame_tier", return_value=tier):
            return ts.check_tile(None, None, tile, spec, None, idol)

    def test_uncalibrated_target_needs_the_db_accept_and_the_frame(self):
        spec = ts.spec_for(MANO, FakeIndex().meta[MANO])
        self.assertEqual(self.check(spec, self.tile(MANO), "001", "rainbow").verdict, "target")
        self.assertEqual(self.check(spec, self.tile(MANO), "001", "gold").verdict, "other")    # frame of another rarity
        self.assertEqual(self.check(spec, self.tile(MANO2), "001", "rainbow").verdict, "other")  # a different card, accepted
        self.assertEqual(self.check(spec, self.tile(None, "rejected"), "001", "rainbow").verdict, "lookalike")
        self.assertEqual(self.check(spec, self.tile(None, "rejected"), "002", "rainbow").verdict, "other")

    def test_unplaced_tile_of_the_target_idol_stops_the_search(self):
        spec = ts.spec_for(MANO, FakeIndex().meta[MANO])
        result = {"offset": 184, "tiles": [self.tile(None, "rejected")], "accepted": [], "rejected": 1}
        with mock.patch.object(ci, "recognise", return_value=result), mock.patch.object(ci, "frame_tier", return_value="rainbow"),                 mock.patch.object(ts, "_coarse_idols", return_value=["001"]):
            view = ts.locate(blank(), spec, FakeIndex(), None)
        self.assertEqual((view.status, len(view.lookalikes)), ("ambiguous", 1))
        # 2026-11-21 live: the closest variant is clearly another card of the idol -> not the target, search goes on
        other = {**self.tile(None, "rejected"), "variant": MANO2, "variant_margin": 0.2}
        self.assertEqual(self.check(spec, other, "001", "rainbow").verdict, "other")
        close = {**self.tile(None, "rejected"), "variant": MANO2, "variant_margin": 0.01}
        self.assertEqual(self.check(spec, close, "001", "rainbow").verdict, "lookalike")
        same = {**self.tile(None, "rejected"), "variant": MANO, "variant_margin": 0.2}
        self.assertEqual(self.check(spec, same, "001", "rainbow").verdict, "lookalike")


class DefaultLocateTests(unittest.TestCase):
    """2026-10-12: default mode follows the game's dimming (any sort key), never the tile number or a DB identity."""

    def locate(self, tiles, offset=184, states=None, tier="rainbow"):
        result = {"offset": offset, "tiles": tiles, "accepted": [], "rejected": 0}
        states = states or {}

        def tile_state(hsv, box):
            return states.get(tuple(box[:2]), "bright"), {}

        with mock.patch.object(ci, "recognise", return_value=result), mock.patch.object(ci, "frame_tier", return_value=tier),                 mock.patch.object(ts, "tile_state", tile_state):
            return ts.locate_default(blank(), FakeIndex())

    def tile(self, slot, card_id, status="accepted"):
        row, col = int(slot[1]), int(slot[3])
        box = [267 + 138 * (col - 1), 184 + 154 * (row - 1), 108, 108]
        return {"slot": slot, "box": box, "status": status, "card_id": card_id if status == "accepted" else None,
                "reason": "ok" if status == "accepted" else "low_score"}

    def test_first_bright_tile_at_the_top_is_bound(self):
        view = self.locate([self.tile("r1c1", MANO), self.tile("r1c2", ASAHI)])
        self.assertEqual((view.status, view.target.db_card, view.target.slot), ("found", MANO, "r1c1"))

    def test_top_position_tolerance(self):
        self.assertEqual(self.locate([self.tile("r1c1", MANO)], offset=184 + ts.FIRST_ROW_TOLERANCE).status, "found")
        view = self.locate([self.tile("r1c1", MANO)], offset=184 + ts.FIRST_ROW_TOLERANCE + 2)
        self.assertEqual(view.status, "ambiguous")
        self.assertTrue(view.reasons[0].startswith("list_not_at_top"))

    def test_dimmed_tiles_are_skipped_in_reading_order(self):
        tiles = [self.tile("r2c1", MANO2), self.tile("r1c2", None, "rejected"), self.tile("r1c1", None, "rejected")]
        view = self.locate(tiles, states={(267, 184): "dimmed", (405, 184): "dimmed"})
        self.assertEqual((view.status, view.target.slot, view.target.db_card), ("found", "r2c1", MANO2))
        self.assertEqual(view.reasons[:2], ("dimmed:r1c1", "dimmed:r1c2"))

    def test_unidentified_bright_tile_is_the_target_without_a_card_id(self):
        view = self.locate([self.tile("r1c1", None, "rejected"), self.tile("r1c2", ASAHI)])
        self.assertEqual((view.status, view.target.slot, view.target.db_card), ("found", "r1c1", None))
        self.assertIn("bright:r1c1:unidentified", view.reasons)

    def test_unclassifiable_tile_stops_and_is_never_skipped(self):
        tiles = [self.tile("r1c1", MANO), self.tile("r1c2", ASAHI), self.tile("r1c3", MANO2)]
        view = self.locate(tiles, states={(267, 184): "dimmed", (405, 184): None})
        self.assertEqual((view.status, view.targets), ("ambiguous", ()))
        self.assertEqual(view.lookalikes[0].slot, "r1c2")
        self.assertTrue(view.reasons[-1].startswith("tile_unclassified:r1c2"))
        self.assertEqual(ts.ListSearch(4).observe(ORDER, view, True).reason, "target_ambiguous")

    def test_a_p_card_id_is_never_bound(self):
        view = self.locate([self.tile("r1c1", "1040010010")])
        self.assertEqual((view.status, view.target.db_card), ("found", None))

    def test_empty_list(self):
        self.assertEqual(self.locate([self.tile("r1c2", MANO)]).status, "absent")
        self.assertEqual(self.locate([]).status, "absent")

    def test_a_page_of_dimmed_cards_allows_the_bounded_search_to_continue(self):
        view = self.locate([self.tile("r1c1", MANO), self.tile("r1c2", MANO2)],
                           states={(267, 184): "dimmed", (405, 184): "dimmed"})
        self.assertEqual((view.status, view.reasons), ("absent", ("dimmed:r1c1", "dimmed:r1c2")))
        decision = ts.ListSearch(2).observe(ORDER, view, can_scroll=True)
        self.assertEqual((decision.kind, decision.direction), ("scroll", "down"))

    def test_tile_state_thresholds(self):
        import numpy as np

        def tile(badge_v, white, art_v, share=0.25):
            hsv = np.zeros((720, 1280, 3), dtype=np.uint8)
            hsv[..., 2] = 90
            x, y = 267, 184
            bx, by, bw, bh = ts.BADGE_BOX
            region = hsv[y + by:y + by + bh, x + bx:x + bx + bw]
            flat = region.reshape(-1, 3)
            n = len(flat)
            nb, nw = int(n * share), int(n * white)
            flat[:nb] = (145, 200, badge_v)
            flat[nb:nb + nw] = (0, 10, 250)
            region[:] = flat.reshape(region.shape)
            hsv[y + 30:y + 60, x + 60:x + 100, 2] = art_v    # bright art pixels outside the badge
            return ts.tile_state(hsv, (x, y))[0]

        self.assertEqual(tile(238, 0.18, 255), "bright")
        self.assertEqual(tile(142, 0.0, 153), "dimmed")
        self.assertIsNone(tile(200, 0.18, 255))                 # badge between the bands
        self.assertIsNone(tile(142, 0.0, 255))                  # features disagree
        self.assertIsNone(tile(238, 0.18, 255, share=0.05))     # no S badge
        self.assertIsNone(tile(238, 0.18, 255, share=0.0))


# ======================================================================== runtime (fake screen)
try:
    from agent.core import training_custom
    HAVE_RUNTIME = True
except Exception:  # pragma: no cover - environment without the maa package
    HAVE_RUNTIME = False


def frame(marker):
    import numpy as np

    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[0, 0, 0] = marker
    return image


def found(card_id, slot="r1c1", box=FIRST_BOX):
    target = ts.TileCheck(slot, box, 1.0, "rainbow", ci.idol_of(card_id), card_id, "target")
    return ts.ListView("found", 184, (target,), (), 21, "d0")


@unittest.skipUnless(HAVE_RUNTIME, "需要 maa Python 包")
# one fake frame per step: the live settle check is covered by test_training_select.SettleTests
@mock.patch.object(tt, "MAX_SETTLE_HOLDS", 0)
class RuntimeTests(unittest.TestCase):
    NAMES = {10: "【ほわっと】櫻木真乃", 11: "【くっつく、くっつかない】芹沢あさひ"}

    def setUp(self):
        self.root = PRIVATE / "run_logs" / ("_unittest_tany_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        test = self
        self.view = None
        self.calls = []
        self.order_text = ("デフォルト", "▼降順")

        class Vision:
            def __init__(self, context):
                pass

            def text(self, image, roi, threshold=None):
                if list(roi) == tt.CARD_NAME_BOX:
                    return test.NAMES.get(int(image[0, 0, 0]))
                sort, direction = test.order_text
                return {tuple(ts.SORT_BOX): sort, tuple(ts.DIRECTION_BOX): direction, tuple(ts.FILTER_BOX): "絞り込み"}[tuple(roi)]

            def number(self, image, crop, kind, pattern, cutoff=128, unanimous=False):
                return "2"

        def fake_locate(pil, spec, index, reference):
            self.calls.append(("explicit", spec.card_id))
            return self.view

        def fake_default(pil, index, ocr=None, **kwargs):
            self.calls.append(("default", None))
            return self.view

        for name, fake in (("locate", fake_locate), ("locate_default", fake_default)):
            patcher = mock.patch.object(ts, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        now = dt.datetime(2026, 10, 10, 3, 0, tzinfo=dt.timezone.utc)
        self.table = self.enabled_grid()
        self.runtime = training_custom.TrainingRuntime(log_root=self.root, now=lambda: now, vision=Vision, card_index=FakeIndex(),
                                                       reference_loader=lambda spec: "ref", table=self.table)

    def start(self, **params):
        self.runtime.run = tt.TrainingRun(tt.TrainingConfig.from_params(params), self.runtime.log)
        self.runtime.started(self.runtime.run)
        return self.runtime.run

    def open(self, marker=1):
        action = "training-list-slot-r1c1"
        return self.runtime.list_step(None, frame(marker), {"want": "open", "action": action, "node": ts.SELECT_NODES[action]})

    def test_card_library_name_of_the_same_id_passes_the_name_check(self):
        # 2026-11-22 live: manifest 【当冬の隙間】白瀬咲耶 vs game/library 【冬の隙間】白瀬咲耶 -> identity_mismatch
        run = self.start(card_id=MANO)
        run.target_id, run.target_name = MANO, "【当ほわっと】櫻木真乃"
        self.runtime._library = {}
        self.assertEqual(self.runtime.identity(None, frame(10)), ["identity_mismatch"])
        run.identity_frame = None
        self.runtime._library = {MANO: {"name": "【ほわっと】櫻木真乃"}}
        self.assertEqual(self.runtime.identity(None, frame(10)), [])
        run.identity_frame = None
        self.runtime._library = {MANO: {"name": "【くっつく、くっつかない】芹沢あさひ"}}
        self.assertEqual(self.runtime.identity(None, frame(10)), ["identity_mismatch"])

    def test_crossed_card_names_get_no_library_alias(self):
        run = self.start(card_id=MANO)
        run.target_id, run.target_name = MANO, "【当ほわっと】櫻木真乃"
        self.runtime._library = {MANO: {"name": "【ほわっと】櫻木真乃"}}
        self.runtime._swapped = {MANO}
        self.assertEqual(self.runtime.identity(None, frame(10)), ["identity_mismatch"])

    def test_swapped_names_and_dash_variants(self):
        manifest = {"a": "【雪】咲耶", "b": "【紅】咲耶", "c": "【当冬】咲耶", "d": "【──過去】咲耶"}
        library = {"a": "【紅】咲耶", "b": "【雪】咲耶", "c": "【冬】咲耶", "d": "【――過去】咲耶"}
        self.assertEqual(tt.swapped_names(manifest, library), {"a", "b"})
        self.assertEqual(tt.name_key("【──過去】"), tt.name_key("【――過去】"))

    def test_level_band_drops_same_idol_tiles_of_another_level(self):
        # 2026-11-21 live: 三峰結華 tiles the art matcher could not place; the target UR is at Lv66, the others Lv80
        self.runtime._open_uncalibrated = True
        self.runtime._levels = {MANO: 66}
        run = self.start(card_id=MANO)
        spec = ts.spec_for(MANO, FakeIndex().meta[MANO])

        def look(slot, x):
            return ts.TileCheck(slot, (x, 184, 108, 108), 0.0, spec.tier, spec.idol, None, "lookalike", ("db_not_accepted",))

        view = ts.ListView("ambiguous", 184, (), (look("r1c1", 267), look("r1c2", 405)), 21, "d0")
        levels = {267: 80, 405: 66}
        self.runtime.tile_level = lambda context, image, box: levels[box[0]]
        out = self.runtime._level_filter(None, frame(1), spec, view)
        self.assertEqual((out.status, out.target.slot), ("found", "r1c2"))
        levels[405] = None                    # unread level: never opened
        self.assertEqual(self.runtime._level_filter(None, frame(1), spec, view).status, "ambiguous")
        levels.update({267: 80, 405: 75})     # every same-idol tile at another level: search goes on
        self.assertEqual(self.runtime._level_filter(None, frame(1), spec, view).status, "absent")
        self.runtime._levels = {}             # target level unknown: unchanged
        self.assertIs(self.runtime._level_filter(None, frame(1), spec, view), view)
        self.assertFalse(run.finished)

    # -- arbitrary confirmed card
    def test_an_explicitly_disabled_generic_identity_mode_does_not_tap(self):
        self.runtime._open_uncalibrated = False
        run = self.start(card_id=MANO)
        self.assertFalse(run.finished)                     # an explicit card is no longer a skip
        self.view = found(MANO)
        self.assertFalse(self.open())
        self.assertEqual((run.outcome, self.calls), ("target_uncalibrated", [("explicit", MANO)]))
        self.assertIn(MANO, run.notes)
        self.assertEqual(run.target_id, MANO)

    def test_uncalibrated_card_opens_once_the_reviewed_switch_is_on(self):
        self.runtime._open_uncalibrated = True
        run = self.start(card_id=MANO)
        self.view = found(MANO)
        self.assertTrue(self.open())
        self.assertEqual((run.target_id, run.target_name, run.target_mode), (MANO, "【ほわっと】櫻木真乃", "explicit"))
        # the detail page must show exactly that name: the bound name, not an empty configured one
        self.assertFalse(self.runtime.check_identity(None, frame(10)))
        self.assertTrue(self.runtime.check_identity(None, frame(11)))
        self.assertEqual(run.outcome, "identity_mismatch")

    def test_card_without_db_meta_stops_before_any_tap(self):
        run = self.start(card_id="2099990010")
        self.view = found("2099990010")
        self.assertFalse(self.open())
        self.assertEqual((run.outcome, self.calls), ("identity_reference_missing", []))

    def test_calibrated_card_by_id_opens_as_before(self):
        run = self.start(card_id=ASAHI)
        self.view = found(ASAHI)
        self.assertTrue(self.open())
        self.assertEqual((run.target_id, run.target_name), (ASAHI, tt.CARD_ID))
        self.assertFalse(self.runtime.check_identity(None, frame(11)))

    # -- default mode
    def test_default_mode_binds_the_first_tile_and_never_scrolls(self):
        self.runtime._open_uncalibrated = True
        run = self.start()
        self.assertTrue(run.config.default_mode)
        self.view = found(MANO)
        self.assertTrue(self.open())
        self.assertEqual(self.calls, [("default", None)])
        self.assertEqual((run.list_search.max_steps, run.target_mode, run.target_id), (tt.DEFAULT_SCROLL_STEPS, "default", MANO))
        self.assertEqual(run.decisions[-1]["mode"], "default")
        self.assertFalse(self.runtime.check_identity(None, frame(10)))

    def test_default_mode_with_generic_identity_disabled_stops_untapped(self):
        self.runtime._open_uncalibrated = False
        run = self.start()
        self.view = found(MANO)
        self.assertFalse(self.open())
        self.assertEqual(run.outcome, "target_uncalibrated")

    def test_default_mode_calibrated_first_card_opens(self):
        run = self.start()
        self.view = found(ASAHI)
        self.assertTrue(self.open())
        self.assertEqual((run.target_name, run.target_mode), (tt.CARD_ID, "default"))

    def test_default_mode_needs_the_reviewed_r1c1_action(self):
        import dataclasses
        table = pa.default_table()
        self.runtime._table = dataclasses.replace(table, actions=tuple(
            dataclasses.replace(action, enabled=False) if action.id == "training-list-slot-r1c1" else action
            for action in table.actions))
        run = self.start()
        self.view = found(ASAHI)
        self.assertFalse(self.open())
        self.assertEqual(run.outcome, "target_slot_unbound")

    def enabled_grid(self):
        import dataclasses
        return dataclasses.replace(TABLE, actions=tuple(dataclasses.replace(a, enabled=True) if a.id.startswith("training-list-")
                                                        else a for a in TABLE.actions))

    def test_default_mode_turns_lv_desc_to_asc_before_scrolling(self):
        # Live 2026-10-31 (ui-20261031-043350): the list opened as Lv順▼降順, 21 grey maxed cards on the first screen;
        # the default search scrolled to the list end and stopped with target_slot_unbound. Now: one toggle to ▲昇順.
        run = self.start()
        self.order_text = ("Lv順", "▼降順")
        self.view = ts.ListView("absent", 184, (), (), 21, "d0", ("dimmed:r1c1",))
        self.assertTrue(self.runtime.list_step(None, frame(1), {"want": "sort", "node": ts.SORT_NODE}))
        self.assertEqual((run.sort_toggles, run.sort_expected), (1, "asc"))
        self.assertEqual(run.decisions[-1]["sort_plan"]["why"], "default_lv_desc_all_dimmed")
        self.order_text = ("Lv順", "▲昇順")
        self.view = found(MANO)
        self.runtime._open_uncalibrated = True
        self.assertTrue(self.open(2))
        self.assertEqual((run.target_mode, run.target_id, run.finished), ("default", MANO, False))

    def test_toggle_decided_but_never_sent_is_decided_again(self):
        # 2026-11-21 live: the toggle was decided, its click node never ran, the next frame still read ▼降順 -> the run
        # stopped with sort_toggle_unverified. Not sent = decide again.
        run = self.start()
        self.order_text = ("Lv順", "▼降順")
        self.view = ts.ListView("absent", 184, (), (), 21, "d0", ("dimmed:r1c1",))
        scroll = {"want": "scroll", "direction": "down", "node": ts.SCROLL_NODES["down"]}
        self.assertFalse(self.runtime.list_step(None, frame(1), scroll))      # decided "sort" on frame 1, not this node
        self.assertEqual(run.sort_expected, "asc")
        self.assertTrue(self.runtime.list_step(None, frame(2), {"want": "sort", "node": ts.SORT_NODE}))
        self.assertFalse(run.finished)
        self.assertEqual(run.sort_toggles, 1)

    def test_plan_yes_without_the_click_is_not_sent(self):
        # 2026-11-21 live (external-run-20261121j): Plan.Sort answered yes but SortToggle never hit (no click); the next
        # frame read the old direction and the run stopped. Only SC.Training.List.Sorted (after the click) marks it sent.
        run = self.start()
        self.order_text = ("Lv順", "▼降順")
        self.view = ts.ListView("absent", 184, (), (), 21, "d0", ("dimmed:r1c1",))
        self.assertTrue(self.runtime.list_step(None, frame(1), {"want": "sort", "node": ts.SORT_NODE}))
        self.runtime.list_step(None, frame(2), {"want": "sort", "node": ts.SORT_NODE})
        self.assertFalse(run.finished)
        self.assertEqual((run.sort_toggles, run.sort_expected), (1, "asc"))

    def test_default_mode_toggle_without_effect_stops_and_other_orders_are_kept(self):
        run = self.start()
        self.order_text = ("Lv順", "▼降順")
        self.view = ts.ListView("absent", 184, (), (), 21, "d0", ("dimmed:r1c1",))
        self.assertTrue(self.runtime.list_step(None, frame(1), {"want": "sort", "node": ts.SORT_NODE}))
        self.assertTrue(self.runtime.list_step(None, frame(1), {"want": "sort_sent"}))   # SC.Training.List.Sorted
        self.assertFalse(self.runtime.list_step(None, frame(2), {"want": "sort", "node": ts.SORT_NODE}))
        self.assertEqual(run.outcome, "sort_toggle_unverified")
        for order_text in (("Vocal順", "▼降順"), ("Lv順", "▲昇順"), ("加入順", "▼降順")):
            run = self.start()
            self.order_text = order_text
            self.assertFalse(self.runtime.list_step(None, frame(3), {"want": "sort", "node": ts.SORT_NODE}), order_text)
            self.assertEqual(getattr(run, "sort_toggles", 0), 0, order_text)

    def test_default_mode_ambiguity_stops_and_never_scrolls(self):
        run = self.start()
        look = ts.TileCheck("r1c1", FIRST_BOX, 0.0, None, None, None, "lookalike", ("first_tile_unidentified:low_score",))
        self.view = ts.ListView("ambiguous", 184, (), (look,), 21, "d0", look.reasons)
        self.assertFalse(self.open())
        self.assertEqual(run.outcome, "target_ambiguous")
        self.assertFalse(self.runtime.list_step(None, frame(1), {"want": "scroll", "direction": "down",
                                                                 "node": ts.SCROLL_NODES["down"]}))

    def test_default_card_without_exp_ends_before_any_spend(self):
        self.runtime._open_uncalibrated = True
        run = self.start()
        self.view = found(MANO)
        self.assertTrue(self.open())
        self.runtime.read = lambda context, image, stage: ({}, {"inventory": 5, "level": 80, "remaining_exp": 0})
        self.runtime.can_spend = lambda: True
        self.assertFalse(self.runtime.plan(None, frame(10), {"step": "plus"}))
        self.assertEqual(run.outcome, "default_card_not_trainable")
        self.assertIn("no_remaining_exp", run.notes)
        self.assertIn("default_card_not_trainable", tt.FAILURE_OUTCOMES)

    # -- default mode, first bright tile without DB identity: the detail page binds the name
    def open_unidentified(self):
        run = self.start()
        self.view = found(None)
        self.assertTrue(self.open())
        self.assertEqual((run.target_mode, run.target_id, run.target_name, run.default_bind_pending),
                         ("default", None, None, True))
        self.assertEqual(run.decisions[-1]["identity"], "detail_page")
        return run

    def test_default_unidentified_tile_binds_the_detail_name_then_checks_it(self):
        run = self.open_unidentified()
        with mock.patch.object(ts, "detail_s_badge", return_value=True):
            self.assertFalse(self.runtime.check_identity(None, frame(10)))
        self.assertEqual((run.target_name, run.target_id, run.default_bind_pending), ("【ほわっと】櫻木真乃", MANO, False))
        self.assertFalse(run.finished)
        self.assertFalse(self.runtime.check_identity(None, frame(10)))      # same card: continue to the Zero page
        self.assertTrue(self.runtime.check_identity(None, frame(11)))       # another card later: stop, no spend
        self.assertEqual(run.outcome, "identity_mismatch")

    def test_default_detail_name_unknown_to_the_db_binds_without_an_id(self):
        run = self.open_unidentified()
        self.NAMES = {**self.NAMES, 13: "【新しいカード】櫻木真乃"}
        with mock.patch.object(ts, "detail_s_badge", return_value=True):
            self.assertFalse(self.runtime.check_identity(None, frame(13)))
        self.assertEqual((run.target_name, run.target_id), ("【新しいカード】櫻木真乃", None))

    def test_default_detail_without_the_s_badge_stops_before_any_spend(self):
        run = self.open_unidentified()
        with mock.patch.object(ts, "detail_s_badge", return_value=False):
            self.assertTrue(self.runtime.check_identity(None, frame(10)))
        self.assertEqual((run.outcome, run.target_name), ("default_card_not_trainable", None))
        self.assertIn("detail_not_s_card", run.notes)

    def test_default_detail_name_unreadable_or_malformed_holds_then_stops(self):
        run = self.open_unidentified()
        self.NAMES = {**self.NAMES, 12: "櫻木真乃"}
        with mock.patch.object(ts, "detail_s_badge", return_value=True):
            for marker in (12, 99, 12):
                self.assertTrue(self.runtime.check_identity(None, frame(marker)))
                self.assertFalse(run.finished)
            self.assertTrue(self.runtime.check_identity(None, frame(99)))
        self.assertEqual((run.outcome, run.target_name), ("ocr_unreadable", None))

    def test_default_detail_without_a_list_tap_is_never_bound(self):
        run = self.start()
        with mock.patch.object(ts, "detail_s_badge", return_value=True):
            self.assertFalse(self.runtime.check_identity(None, frame(10)))   # BackOne returns to the list
        self.assertEqual((run.target_name, run.finished), (None, False))

    def test_default_plus_binds_the_name_first_and_keeps_the_spend_guards(self):
        run = self.open_unidentified()
        self.runtime.read = lambda context, image, stage: ({}, {"inventory": 5, "level": 3, "remaining_exp": 40})
        self.runtime.can_spend = lambda: True
        with mock.patch.object(ts, "detail_s_badge", return_value=False):
            self.assertFalse(self.runtime.plan(None, frame(10), {"step": "plus"}))
        self.assertEqual((run.baseline, run.outcome), (None, "default_card_not_trainable"))

    def test_default_plus_after_binding_reads_the_zero_page(self):
        run = self.open_unidentified()
        self.runtime.read = lambda context, image, stage: ({}, {"inventory": 0, "level": 3, "remaining_exp": 40})
        self.runtime.can_spend = lambda: True
        with mock.patch.object(ts, "detail_s_badge", return_value=True):
            self.assertFalse(self.runtime.plan(None, frame(10), {"step": "plus"}))
        self.assertEqual((run.target_name, run.outcome), ("【ほわっと】櫻木真乃", "insufficient_tickets"))

    def test_default_unidentified_tile_with_generic_identity_disabled_stops_untapped(self):
        self.runtime._open_uncalibrated = False
        run = self.start()
        self.view = found(None)
        self.assertFalse(self.open())
        self.assertEqual(run.outcome, "target_uncalibrated")

    def test_explicit_card_without_exp_keeps_the_old_outcome(self):
        run = self.start(card_id=ASAHI)
        self.view = found(ASAHI)
        self.assertTrue(self.open())
        self.runtime.read = lambda context, image, stage: ({}, {"inventory": 5, "level": 80, "remaining_exp": 0})
        self.assertFalse(self.runtime.plan(None, frame(11), {"step": "plus"}))
        self.assertEqual(run.outcome, "baseline_mismatch")

    def test_intent_log_names_the_bound_card(self):
        self.runtime._open_uncalibrated = True
        run = self.start()
        self.view = found(MANO)
        self.assertTrue(self.open())
        run.phase, run.baseline = "submitted", {"inventory": 10, "level": 2, "remaining_exp": 40}
        self.runtime.read = lambda context, image, stage: ({}, {"level": 2})
        self.runtime.can_spend = lambda: True
        self.runtime.authorize = lambda image, approver: None
        self.assertTrue(self.runtime.check_confirm(None, frame(10)))
        self.assertEqual((run.intent["card"], run.intent["card_id"]), ("【ほわっと】櫻木真乃", MANO))


if __name__ == "__main__":
    unittest.main()
