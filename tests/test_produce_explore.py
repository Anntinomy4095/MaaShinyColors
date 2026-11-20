"""Default-off supervised exploration: grid contract, paid guard and explore_step with fakes.

Nothing here starts a host, opens a socket or sends input; every bridge, OCR and send is a fake.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from agent.core import pipeline_actions
from agent.core import produce_explore as pe
from tools import explore_step
from tests.test_webview_controller import pipeline_session

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "browser_host"
PINNED_GRID_SHA = "6585249ad26de73ecca876b2897cbf566e5b493a3bbdbeae238461c72711d2ab"
NOW = dt.datetime(2026, 10, 4, 3, 0, tzinfo=dt.timezone.utc)  # JST 12:00 -> game day 2026-10-04


def _read(name):
    return (HOST / name).read_text(encoding="utf-8")


class GridContractTests(unittest.TestCase):
    def test_grid_is_in_bounds_disjoint_and_tiles_the_canvas(self):
        ids = pe.all_ids()
        self.assertEqual(len(ids), 576)
        self.assertEqual(len(set(ids)), 576)
        rects = [pe.rect(i) for i in ids]
        self.assertTrue(all(x >= 0 and y >= 0 and x + w <= 1280 and y + h <= 720 and (w, h) == (40, 40) for x, y, w, h in rects))
        self.assertEqual(sum(w * h for _, _, w, h in rects), 1280 * 720)
        self.assertEqual(len({(x, y) for x, y, _, _ in rects}), 576)  # equal-size grid cells at distinct origins never overlap
        self.assertEqual(pe.rect("explore-r07c12"), (480, 280, 40, 40))
        self.assertEqual(pe.inner_rect("explore-r07c12"), (488, 288, 24, 24))
        self.assertEqual(pe.rect("explore-r17c31"), (1240, 680, 40, 40))
        self.assertEqual(pe.action_for_cell("r07c12"), "explore-r07c12")

    def test_bad_ids_are_refused(self):
        for bad in ("explore-r18c00", "explore-r00c32", "explore-r7c12", "explore-R07C12", "explore-r07c12 ", "r07c12",
                    "explore-r-1c00", "explore-r07c12x", "explore-r０7c12", "", None, 7):
            self.assertFalse(pe.is_cell(bad), bad)
        for bad in ("7c12", "r7c12", "r18c00", "r00c32", "R07C12", "explore-r07c12", "r07c12 "):
            with self.assertRaises(ValueError):
                pe.action_for_cell(bad)

    def test_grid_sha_is_pinned_on_both_sides(self):
        self.assertEqual(pe.GRID_SHA256, hashlib.sha256(pe.DESCRIPTOR.encode()).hexdigest())
        self.assertEqual(pe.GRID_SHA256, PINNED_GRID_SHA)
        self.assertIn(f'ExpectedGridSha256 = "{PINNED_GRID_SHA}"', _read("ProduceExploreSelfTest.cs"))
        grid = _read("ProduceExploreGrid.cs")
        for constant in ('Version = "produce-explore-grid.v1"', 'Family = "produce-explore"', "Cell = 40", "Rows = 18",
                         "Cols = 32", "MarginPercent = 20"):
            self.assertIn(constant, grid)

    def test_grid_is_separate_from_the_pipeline_table(self):
        table = pipeline_actions.default_table()
        self.assertFalse(any(i.startswith(pe.ID_PREFIX) for i in table.ids))
        self.assertNotIn(pe.FAMILY, table.families)
        raw = (ROOT / "assets/input/pipeline_actions.json").read_text(encoding="utf-8")
        self.assertNotIn("explore", raw)
        self.assertEqual(table.sha256, hashlib.sha256((ROOT / "assets/input/pipeline_actions.json").read_bytes()).hexdigest())


class GuardTests(unittest.TestCase):
    def test_guard_words_cover_the_paid_vocabulary(self):
        for word in ("ジュエル", "有償", "課金", "購入", "ショップ", "交換"):
            self.assertIn(word, pe.GUARD_WORDS)

    def test_guard_matches(self):
        self.assertEqual(pe.guard_matches(["スタミナ", "300/300", "プロデュース開始"]), [])
        hits = pe.guard_matches(["ジュエルを使用してスタミナを回復しますか?"])
        self.assertEqual([h["word"] for h in hits], ["ジュエル"])
        self.assertTrue(pe.guard_matches(["ｼﾞｭｴﾙ"]))           # half-width katakana (NFKC)
        self.assertTrue(pe.guard_matches(["有 償 ジュエル"]))      # OCR spacing
        self.assertTrue(pe.guard_matches(["交换上限/日"]))         # OCR reads 交換 as simplified 交换
        self.assertEqual(pe.guard_matches([None, ""]), [])
        # Collabo Fes live skill-slot hint: the fixed phrase passes, the rest of the text is still checked
        self.assertEqual(pe.guard_matches(["スライドで交換 あと3回"]), [])
        self.assertTrue(pe.guard_matches(["スライドで交換 ジュエル"]))
        self.assertTrue(pe.guard_matches(["アイテム交換"]))


def _status(**overrides):
    status = {"ready": True, "browser_demo": False, "input_enabled": False, "width": 1280, "height": 720,
              "composition_game": True, "minimized": False, "window_visible": True, "webview_visible": True,
              "pipeline_input": True, "supervised_navigation": None, "produce_explore": True,
              "explore_grid_version": pe.VERSION, "explore_grid_sha256": pe.GRID_SHA256, "frame_sha256": None,
              "frame_age_ms": 20.0, "source_capture_width": 1280, "source_capture_height": 720,
              "capture_seq": 0, "frame_binding_seq": 0, "capture_pending": False}
    status.update(overrides)
    return status


class FakeBridge:
    """Each screencap returns the next PNG blob and binds the host's frame SHA to it (like the real host)."""

    def __init__(self, frames, status=None, rebind=None):
        self.session = {"pid": 4242}
        self.frames = list(frames)
        self.base = status or {}
        self.frame_sha = None
        self.rebind = rebind
        self.captures = 0

    def status(self):
        sha = self.rebind if (self.rebind and self.captures) else self.frame_sha
        return _status(**{**self.base, "frame_sha256": sha, "capture_seq": self.captures, "frame_binding_seq": self.captures * 2})

    def screencap(self):
        blob = self.frames[min(self.captures, len(self.frames) - 1)]
        self.captures += 1
        self.frame_sha = hashlib.sha256(blob).hexdigest()
        return blob


class FakeOcr:
    def __init__(self, texts):
        self.texts_value = texts
        self.calls = 0

    def texts(self, path):
        self.calls += 1
        self.assertion = Path(path).is_file()
        return [{"text": t, "score": 0.9, "box": [0, 0, 1, 1]} for t in self.texts_value]


BEFORE, AFTER = b"\x89PNG-before-frame", b"\x89PNG-after-frame"
SESSION = {**pipeline_session(), "token": "ab" * 32}


class ExploreStepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sent = []
        self.slept = []
        self.ocr_loads = 0

    def tearDown(self):
        self.tmp.cleanup()

    def deps(self, bridge, ocr=None, send=None, ocr_error=None):
        def ocr_factory(model_dir):
            self.ocr_loads += 1
            if ocr_error:
                raise ocr_error
            return ocr or FakeOcr([])

        def fake_send(session, request):
            self.sent.append(request)
            return send(request) if send else {"ok": True, "attempted": True, "reason": "native_gesture_sent_postcondition_required"}

        return explore_step.Deps(sample_root=self.root, now=lambda: NOW, sleep=self.slept.append,
                                 bridge_factory=lambda path: bridge, ocr_factory=ocr_factory, send=fake_send,
                                 read_pipeline_session=lambda path, pid: dict(SESSION),
                                 live_status=self.root / "live-status.json")

    def call(self, argv, deps):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = explore_step.main(argv, deps)
        lines = out.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 1)  # one JSON summary line
        return code, json.loads(lines[0])

    def click_args(self, sha, cell="r07c12"):
        return ["--session", str(self.root / "s.json"), "--cell", cell, "--expect-frame", sha, "--note", "test tap"]

    def log_records(self):
        path = self.root / "2026-10-04" / explore_step.LOG_NAME
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_capture_only_sends_nothing(self):
        bridge = FakeBridge([BEFORE])
        code, summary = self.call(["--session", str(self.root / "s.json"), "--capture-only"], self.deps(bridge))
        self.assertEqual(code, 0)
        self.assertEqual(summary["result"], "captured")
        self.assertEqual(summary["frame_sha256"], hashlib.sha256(BEFORE).hexdigest())
        self.assertEqual(Path(summary["frame"]).read_bytes(), BEFORE)
        self.assertEqual((self.sent, self.slept, self.ocr_loads, summary["input_requests"]), ([], [], 0, 0))
        self.assertEqual(self.log_records()[-1]["result"], "captured")

    def test_an_active_run_refuses_even_a_capture(self):
        status = self.root / "live-status.json"
        fresh = {"run_id": "r1", "state": "running", "updated_at": (NOW - dt.timedelta(seconds=20)).isoformat()}
        status.write_text(json.dumps(fresh), encoding="utf-8")
        for argv in (["--session", str(self.root / "s.json"), "--capture-only"],
                     self.click_args(hashlib.sha256(BEFORE).hexdigest())):
            bridge = FakeBridge([BEFORE])
            code, summary = self.call(argv, self.deps(bridge))
            self.assertEqual((code, summary["result"], summary["reason"]), (2, "refused", "run_in_progress: r1"))
            self.assertEqual((self.sent, bridge.captures), ([], 0))
        for stale in ({**fresh, "state": "finished"},
                      {**fresh, "updated_at": (NOW - dt.timedelta(minutes=5)).isoformat()}):
            status.write_text(json.dumps(stale), encoding="utf-8")
            code, summary = self.call(["--session", str(self.root / "s.json"), "--capture-only"],
                                      self.deps(FakeBridge([BEFORE])))
            self.assertEqual(summary["result"], "captured")

    def test_sha_mismatch_is_refused(self):
        bridge = FakeBridge([BEFORE])
        code, summary = self.call(self.click_args("0" * 64), self.deps(bridge))
        self.assertEqual(code, 2)
        self.assertEqual(summary["reason"], "frame_sha_mismatch")
        self.assertEqual(self.sent, [])
        self.assertTrue(Path(summary["before"]).is_file())  # the fresh frame is still kept as a sample

    def test_guard_words_are_refused_and_logged(self):
        bridge = FakeBridge([BEFORE])
        ocr = FakeOcr(["スタミナが足りません", "ジュエルを使用して回復しますか?"])
        code, summary = self.call(self.click_args(hashlib.sha256(BEFORE).hexdigest()), self.deps(bridge, ocr=ocr))
        self.assertEqual(code, 2)
        self.assertEqual(summary["reason"], "paid_guard_matched")
        self.assertEqual(self.sent, [])
        record = self.log_records()[-1]
        self.assertFalse(record["ocr_guard"]["passed"])
        self.assertEqual([m["word"] for m in record["ocr_guard"]["matches"]], ["ジュエル"])

    def test_far_short_labels_are_exempt_only_on_request(self):
        """2026-10-31: Home / Match carry 「ジュエル」「ショップ」「ガシャ」 nav labels far from the tapped button."""
        class BoxOcr(FakeOcr):
            def texts(self, path):
                return [{"text": t, "score": 0.9, "box": b} for t, b in self.texts_value]
        far = [("ショップ", [29, 268, 61, 27]), ("ジュエル", [33, 242, 56, 14]), ("フェス", [1150, 640, 80, 30])]
        sha = hashlib.sha256(BEFORE).hexdigest()
        args = self.click_args(sha, cell="r16c29")   # フェス, bottom right
        code, summary = self.call(args, self.deps(FakeBridge([BEFORE, AFTER]), ocr=BoxOcr(far)))
        self.assertEqual((code, summary["reason"]), (2, "paid_guard_matched"))   # off by default
        code, summary = self.call(args + ["--allow-far-labels"], self.deps(FakeBridge([BEFORE, AFTER]), ocr=BoxOcr(far)))
        self.assertEqual((code, summary["result"]), (0, "sent"))
        self.assertEqual(self.log_records()[-1]["ocr_guard"]["far_labels_exempt"], ["ショップ", "ジュエル"])
        for texts in ([("ジュエルを使用して回復しますか?", [300, 300, 600, 30])],   # a sentence: never exempt
                      [("購入", [1120, 600, 60, 30])]):                         # a label next to the tap
            self.sent.clear()
            code, summary = self.call(args + ["--allow-far-labels"], self.deps(FakeBridge([BEFORE]), ocr=BoxOcr(texts)))
            self.assertEqual((code, summary["reason"], self.sent), (2, "paid_guard_matched", []))

    def test_missing_ocr_model_fails_closed(self):
        bridge = FakeBridge([BEFORE])
        code, summary = self.call(self.click_args(hashlib.sha256(BEFORE).hexdigest()),
                                  self.deps(bridge, ocr_error=ValueError("必须提供OCR模型目录")))
        self.assertEqual(code, 2)
        self.assertTrue(summary["reason"].startswith("ocr_unavailable"))
        self.assertEqual((self.sent, bridge.captures), ([], 0))

    def test_real_ocr_loader_refuses_a_missing_model_directory(self):
        with self.assertRaises(Exception):
            explore_step.FrameOcr(self.root / "no-such-model")

    def test_host_without_explore_flag_is_refused(self):
        bridge = FakeBridge([BEFORE], status={"produce_explore": False})
        code, summary = self.call(self.click_args(hashlib.sha256(BEFORE).hexdigest()), self.deps(bridge))
        self.assertEqual(code, 2)
        self.assertTrue(summary["reason"].startswith("host_not_ready_for_explore"))
        self.assertEqual(self.sent, [])

    def test_grid_sha_mismatch_is_refused(self):
        bridge = FakeBridge([BEFORE], status={"explore_grid_sha256": "f" * 64})
        code, _ = self.call(self.click_args(hashlib.sha256(BEFORE).hexdigest()), self.deps(bridge))
        self.assertEqual((code, self.sent), (2, []))

    def test_host_frame_rebound_by_another_client_is_refused(self):
        bridge = FakeBridge([BEFORE], rebind="e" * 64)
        code, summary = self.call(self.click_args(hashlib.sha256(BEFORE).hexdigest()), self.deps(bridge))
        self.assertEqual(code, 2)
        self.assertTrue(summary["reason"].startswith("capture_failed"))
        self.assertEqual(self.sent, [])

    def test_happy_path_sends_exactly_one_click_and_saves_samples(self):
        bridge = FakeBridge([BEFORE, AFTER])
        sha = hashlib.sha256(BEFORE).hexdigest()
        code, summary = self.call(self.click_args(sha), self.deps(bridge, ocr=FakeOcr(["プロデュース", "スタミナ"])))
        self.assertEqual(code, 0)
        self.assertEqual(summary["result"], "sent")
        self.assertEqual(len(self.sent), 1)
        request = self.sent[0]
        self.assertEqual(set(request), {"token", "command", "action", "frame_sha256", "source_width", "source_height", "nonce",
                                       "protocol_version", "capture_seq", "frame_binding_seq"})
        self.assertEqual((request["protocol_version"], request["capture_seq"], request["frame_binding_seq"]), (2, 1, 2))
        self.assertEqual((request["command"], request["action"], request["frame_sha256"]), ("pipeline-click", "explore-r07c12", sha))
        self.assertRegex(request["nonce"], r"\A[0-9a-f]{32}\Z")
        self.assertEqual(self.slept, [explore_step.AFTER_DELAY_S])
        self.assertEqual(Path(summary["before"]).read_bytes(), BEFORE)
        self.assertEqual(Path(summary["after"]).read_bytes(), AFTER)
        self.assertEqual(summary["after_sha256"], hashlib.sha256(AFTER).hexdigest())
        self.assertTrue(Path(summary["before"]).resolve().is_relative_to(self.root.resolve() / "2026-10-04"))
        intent, final = self.log_records()[-2:]
        self.assertEqual(intent["result"], "intent")
        self.assertEqual(final["result"], "sent")
        self.assertEqual((final["cell"], final["rect"], final["note"]), ("r07c12", [480, 280, 40, 40], "test tap"))
        self.assertTrue(final["ocr_guard"]["passed"])
        self.assertEqual(final["host_response"]["reason"], "native_gesture_sent_postcondition_required")
        self.assertEqual(final["input_requests"], 1)

    def test_identical_external_capture_during_ocr_does_not_rebind_the_request(self):
        bridge = FakeBridge([BEFORE])
        class RecapturingOcr(FakeOcr):
            def texts(self, path):
                bridge.screencap()
                return super().texts(path)
        code, summary = self.call(self.click_args(hashlib.sha256(BEFORE).hexdigest()),
                                  self.deps(bridge, ocr=RecapturingOcr(["プロデュース"])))
        self.assertEqual(code, 2)
        self.assertTrue(summary["reason"].startswith("frame_binding_refused"))
        self.assertEqual(self.sent, [])
        self.assertEqual(bridge.captures, 2)

    def test_send_failure_is_not_retried(self):
        bridge = FakeBridge([BEFORE, AFTER])

        def boom(request):
            raise OSError("connection reset")

        code, summary = self.call(self.click_args(hashlib.sha256(BEFORE).hexdigest()), self.deps(bridge, send=boom))
        self.assertEqual(code, 3)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("no_retry", summary["host_response"]["reason"])

    def test_host_refusal_is_reported_without_retry(self):
        bridge = FakeBridge([BEFORE, AFTER])
        code, _ = self.call(self.click_args(hashlib.sha256(BEFORE).hexdigest()),
                            self.deps(bridge, send=lambda r: {"ok": False, "attempted": False, "reason": "rate_limited_min_interval"}))
        self.assertEqual((code, len(self.sent)), (2, 1))

    def test_cli_requires_frame_note_and_valid_cell(self):
        bridge = FakeBridge([BEFORE])
        sha = hashlib.sha256(BEFORE).hexdigest()
        for argv in (["--session", "s", "--cell", "r07c12", "--note", "x"],
                     ["--session", "s", "--cell", "r07c12", "--expect-frame", sha],
                     ["--session", "s", "--cell", "r07c12", "--expect-frame", sha.upper(), "--note", "x"],
                     ["--session", "s", "--cell", "r18c00", "--expect-frame", sha, "--note", "x"],
                     ["--session", "s", "--capture-only", "--expect-frame", sha],
                     ["--session", "s", "--cell", "r07c12", "--capture-only"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                explore_step.main(argv, self.deps(bridge))
        self.assertEqual((self.sent, bridge.captures), ([], 0))


class BoxOcr(FakeOcr):
    """OCR items with real boxes; ``texts_value`` is one list of (text, box) or, for polling, a list of such lists."""

    def texts(self, path):
        self.calls += 1
        value = self.texts_value
        if value and isinstance(value[0], list):
            value = value[min(self.calls - 1, len(value) - 1)]
        return [{"text": t, "score": 0.9, "box": b} for t, b in value]


HALO_SCHEDULE = [("スケジュール選択", [20, 10, 200, 40]), ("ビジュアルレッスン", [620, 520, 140, 30]),
                 ("決定", [1150, 645, 60, 35]), ("決定", [330, 525, 60, 30])]


class AgentModeTests(ExploreStepTests):
    """--tap-text / --look / --wait-* / --label / --brief command options."""

    def session(self):
        return ["--session", str(self.root / "s.json")]

    def test_tap_text_clicks_the_cell_under_the_one_match(self):
        bridge = FakeBridge([BEFORE, AFTER])
        ocr = BoxOcr([("スケジュール選択", [20, 10, 200, 40]), ("ビジュアルレッスン", [620, 520, 140, 30])])
        code, summary = self.call(self.session() + ["--tap-text", "ビジュアル", "--note", "Vi lesson", "--label", "HaloSchedule",
                                                    "--look-after"], self.deps(bridge, ocr=ocr))
        self.assertEqual((code, summary["result"], summary["cell"]), (0, "sent", "r13c17"))
        self.assertEqual(self.sent[0]["action"], "explore-r13c17")
        self.assertEqual(summary["after_texts"], ["スケジュール選択@r00c03", "ビジュアルレッスン@r13c17"])
        record = self.log_records()[-1]
        self.assertEqual((record["label"], record["tap_text"], record["expect_text"]["matches"]),
                         ("HaloSchedule", "ビジュアル", ["ビジュアルレッスン"]))
        self.assertEqual(record["ocr"][1], {"text": "ビジュアルレッスン", "box": [620, 520, 140, 30]})

    def test_tap_text_refuses_when_absent_or_ambiguous_and_near_picks_one(self):
        for text, reason in (("休む", "tap_text_not_found"), ("決定", "tap_text_ambiguous")):
            code, summary = self.call(self.session() + ["--tap-text", text, "--note", "x"],
                                      self.deps(FakeBridge([BEFORE]), ocr=BoxOcr(HALO_SCHEDULE)))
            self.assertEqual((code, summary["reason"], self.sent), (2, reason, []))
        self.assertEqual(summary["tap_candidates"], ["決定@r16c29", "決定@r13c09"])
        code, summary = self.call(self.session() + ["--tap-text", "決定", "--near", "r16c28", "--note", "x"],
                                  self.deps(FakeBridge([BEFORE, AFTER]), ocr=BoxOcr(HALO_SCHEDULE)))
        self.assertEqual((code, summary["cell"], len(self.sent)), (0, "r16c29", 1))

    def test_tap_text_keeps_the_paid_guard(self):
        ocr = BoxOcr([("購入する", [600, 400, 120, 40]), ("キャンセル", [400, 400, 120, 40])])
        code, summary = self.call(self.session() + ["--tap-text", "キャンセル", "--note", "x"],
                                  self.deps(FakeBridge([BEFORE]), ocr=ocr))
        self.assertEqual((code, summary["reason"], self.sent), (2, "paid_guard_matched", []))

    def test_look_reads_the_frame_without_input(self):
        code, summary = self.call(self.session() + ["--look", "--label", "HaloHub"],
                                  self.deps(FakeBridge([BEFORE]), ocr=BoxOcr(HALO_SCHEDULE[:2])))
        self.assertEqual((code, summary["result"], summary["texts"]),
                         (0, "captured", ["スケジュール選択@r00c03", "ビジュアルレッスン@r13c17"]))
        self.assertEqual((self.sent, summary["input_requests"]), ([], 0))
        self.assertEqual(self.log_records()[-1]["label"], "HaloHub")

    def test_wait_polls_until_the_text_is_gone_and_is_bounded(self):
        commu = [("早送り×4", [1080, 650, 70, 20])]
        hub = [("プロデュース", [40, 620, 200, 40])]
        ocr = BoxOcr([commu, commu, hub])
        code, summary = self.call(self.session() + ["--wait-gone", "早送り", "--timeout", "30", "--interval", "3"],
                                  self.deps(FakeBridge([BEFORE]), ocr=ocr))
        self.assertEqual((code, summary["result"], summary["polls"], summary["texts"]), (0, "waited", 3, ["プロデュース@r16c03"]))
        self.assertEqual((self.slept, self.sent), ([3.0, 3.0], []))
        self.slept.clear()
        code, summary = self.call(self.session() + ["--wait-for", "^OK$", "--timeout", "6", "--interval", "3"],
                                  self.deps(FakeBridge([BEFORE]), ocr=BoxOcr([commu])))
        self.assertEqual((code, summary["result"], summary["polls"], self.slept), (2, "wait_timeout", 3, [3.0, 3.0]))

    def test_brief_prints_result_then_ocr(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            explore_step.main(self.session() + ["--look", "--brief"], self.deps(FakeBridge([BEFORE]), ocr=BoxOcr(HALO_SCHEDULE[:2])))
        first, second = out.getvalue().strip().splitlines()
        self.assertTrue(first.startswith("captured "))
        self.assertEqual(second, "スケジュール選択@r00c03 | ビジュアルレッスン@r13c17")

    def test_cli_rules_for_the_agent_modes(self):
        sha = hashlib.sha256(BEFORE).hexdigest()
        for argv in (["--tap-text", "決定"],                                        # no note
                     ["--tap-text", " ", "--note", "x"],
                     ["--tap-text", "決定", "--note", "x", "--expect-frame", sha],
                     ["--tap-text", "決定", "--note", "x", "--near", "r18c00"],
                     ["--cell", "r07c12", "--expect-frame", sha, "--note", "x", "--near", "r07c12"],
                     ["--look", "--note", "x"],
                     ["--look", "--look-after"],
                     ["--wait-gone", "("],
                     ["--wait-gone", "x", "--timeout", "600"],
                     ["--wait-gone", "x", "--interval", "0.5"],
                     ["--look", "--tap-text", "決定"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                explore_step.main(self.session() + argv, self.deps(FakeBridge([BEFORE])))
        self.assertEqual(self.sent, [])


class HostContractTests(unittest.TestCase):
    def test_flag_is_default_off_and_bound_to_pipeline_input_on_the_game_host(self):
        options = _read("BrowserHostOptions.cs")
        self.assertIn("bool EnableProduceExplore = false", options)
        self.assertIn('case "--enable-produce-explore":', options)
        self.assertRegex(options, r"enableProduceExplore && \(!enablePipelineInput \|\| !compositionGame \|\| supervisedNavigation is not null \|\|\s*"
                                  r"!DailyNavigationPolicy\.IsGameUri\(startUri\.AbsoluteUri\)\)")

    def test_explore_ids_are_accepted_only_through_the_optional_grid(self):
        policy = _read("PipelineInput.cs")
        self.assertIn("table.IsAction(action) || explore?.IsCell(action) == true", policy)
        self.assertIn("internal bool IsExplore(string? id) => Explore?.IsCell(id) == true;", policy)
        self.assertIn("internal bool IsConsuming(string? id) => Table.IsConsuming(id);", policy)  # cells never consume
        self.assertIn("PipelineInputPolicy.Parse(document.RootElement, _token, _table, _explore)", _read("PipelineInputBridge.cs"))
        form = _read("CompositionDemoForm.cs")
        self.assertIn("new PipelineInputGate(_options.EnableProduceExplore ? ProduceExploreGrid.Default : null)", form)
        # The gesture runs through RunPipelineGate(gate, ...).
        self.assertIn("return RunPipelineGate(_pipelineGate, request, cancellation);", form)
        self.assertIn("!gate.IsExplore(admitted.Action) && gate.Table.WheelOf(admitted.Action)", form)
        self.assertIn("explore_grid_sha256 = _pipelineGate?.Explore?.Sha256", form)
        # Never raw coordinates: the grid exposes ids, the host samples the point.
        grid = _read("ProduceExploreGrid.cs")
        self.assertIn("\\\\Aexplore-r([0-9]{2})c([0-9]{2})\\\\z", grid)
        self.assertNotRegex(policy, r'"(x|y|point)"')

    def test_pipeline_session_file_is_unchanged(self):
        # The Python validator pins the session keys exactly; the grid is reported only in host status.
        bridge = _read("PipelineInputBridge.cs")
        self.assertNotIn("explore", bridge[bridge.index("JsonSerializer.Serialize(file"):bridge.index("catch { _listener.Stop()")])

    def test_self_tests_cover_the_grid(self):
        self.assertIn("await ProduceExploreSelfTest.Run(root, Check);", _read("NavigationSelfTest.cs"))
        self.assertIn('"--self-test-produce-explore"', _read("Program.cs"))


if __name__ == "__main__":
    unittest.main()
