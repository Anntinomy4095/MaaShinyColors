from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import unittest
from pathlib import Path

from agent.core import pipeline_actions as pa
from agent.core import webview_controller as wc
from tools import grade_protocol
from tools import repeatable_navigation_protocol as rp
from tools import navigation_protocol as nav

FIXTURE = Path(__file__).parent / "fixtures/replay/ready.png"
PNG = FIXTURE.read_bytes()
SHA = hashlib.sha256(PNG).hexdigest()
TOKEN = "a" * 64
PID = 4242
DAY = "2026-10-02"
POLICY_ON = {"live_input_enabled": True}
AUDIT_ROOT = wc.PRIVATE / "run_logs" / "_unittest_webview_controller"


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FakeImage:
    shape = (720, 1280, 3)

    def tobytes(self):
        return bytes(720 * 1280 * 3)


class FakeBridge:
    def __init__(self, png=PNG, **status):
        self.png = png
        self.session = {"pid": PID}
        self.status_value = {"ready": True, "browser_demo": False, "input_enabled": False, "width": 1280,
                             "height": 720, "composition_game": True, "minimized": False, "window_visible": True,
                             "webview_visible": True, "supervised_daily": True, "supervised_navigation": None,
                             "supervised_training": True, "supervised_scroll": True, "supervised_shop": True,
                             "supervised_grade": True, "repeatable_navigation": True, "pipeline_input": True,
                             "frame_sha256": hashlib.sha256(png).hexdigest(), "frame_age_ms": 10.0,
                             "source_capture_width": 1280, "source_capture_height": 720,
                             "capture_seq": 0, "frame_binding_seq": 0, "capture_pending": False, **status}

    def status(self):
        return dict(self.status_value)

    def screencap(self):
        for key in ("capture_seq", "frame_binding_seq"):
            if type(self.status_value.get(key)) is int:
                self.status_value[key] += 1 if key == "capture_seq" else 2
        return self.png


def daily_session():
    actions = (list(nav.DAILY_ACTION_POINTS) + list(nav.DAILY_TRAINING_ACTION_POINTS)
               + list(nav.DAILY_SCROLL_ACTION_POINTS) + list(nav.DAILY_SHOP_ACTION_POINTS))
    paths = {}
    for action in actions:
        if action in nav.DAILY_SHOP_ACTION_POINTS:
            paths[action] = str(nav.shop_budget_path(action, DAY))
        else:
            paths[action] = str(nav.DAILY_SCROLL_BUDGET_PATHS.get(action, nav.DAILY_BUDGET_PATHS.get(
                action, nav.ROOT / "dev_assets/private/daily_navigation" / f"{action}.json")))
    return {"version": 1, "host": "127.0.0.1", "port": 50001, "token": TOKEN, "pid": PID, "input_enabled": True,
            "browser_demo_only": False, "transport": nav.DAILY_TRANSPORT, "actions": actions, "budget_paths": paths}


def grade_session():
    return {"version": 1, "host": "127.0.0.1", "port": 50002, "token": TOKEN, "pid": PID, "input_enabled": True,
            "browser_demo_only": False, "transport": grade_protocol.TRANSPORT,
            "actions": list(grade_protocol.GRADE_ACTION_POINTS),
            "budget_paths": {a: str(grade_protocol.budget_path(a, DAY)) for a in grade_protocol.GRADE_ACTION_POINTS}}


def repeatable_session():
    return {"version": 1, "host": "127.0.0.1", "port": 50003, "token": TOKEN, "pid": PID, "input_enabled": True,
            "browser_demo_only": False, "transport": rp.TRANSPORT, "ledger_root": str(rp.LEDGER_ROOT),
            "game_day": DAY, "per_action_max": rp.PER_ACTION_MAX, "day_max": rp.DAY_MAX,
            "actions": rp.allowed_actions(True, True, True)}


SECRET = "07" * 32
TABLE = pa.default_table()
PIPELINE = frozenset(TABLE.families)


def pipeline_session(table=TABLE):
    return {"version": 2, "frame_binding_protocol": wc.PIPELINE_FRAME_BINDING_PROTOCOL,
            "host": "127.0.0.1", "port": 50004, "token": TOKEN, "pid": PID, "input_enabled": True,
            "browser_demo_only": False, "transport": wc.PIPELINE_TRANSPORT, "actions": table.ids,
            "consuming_actions": table.consuming_ids, "action_table_sha256": table.sha256, "spend_secret": SECRET,
            "audit_root": str(wc.PRIVATE / "run_logs"),
            "limits": {"min_interval_ms": 800, "interval_jitter_max_ms": 600, "per_action_per_minute": 12,
                       "run_max": 600, "run_spend_max": 8, "grant_max_ttl_ms": 5000, "session_max_ms": 3_600_000}}


# Offline table with one consuming rect, to prove pipeline mode refuses consuming actions whatever the table says.
SPEND_TABLE_RAW = json.dumps({
    "version": 1, "canvas": [1280, 720], "margin_percent": 20, "min_inner_px": 4, "families": ["popup"],
    "actions": [{"id": "popup-ok", "family": "popup", "consuming": False, "rect": [0, 0, 100, 100], "source": "t",
                 "nodes": ["SC.Test.PopupOK"]},
                {"id": "popup-buy", "family": "popup", "consuming": True, "rect": [200, 0, 100, 100], "source": "t",
                 "nodes": ["SC.Test.PopupBuy"]}]}).encode("utf-8")


class WebViewControllerTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(AUDIT_ROOT, ignore_errors=True)
        self.addCleanup(shutil.rmtree, AUDIT_ROOT, True)
        self.sent = []
        self.clock = Clock()
        self.audit = wc.AuditLog(AUDIT_ROOT, now=lambda: dt.datetime(2026, 10, 2, 3, 0, tzinfo=dt.timezone.utc))

    def send(self, session, request):
        self.sent.append((session["port"], request))
        if request["command"] == "repeatable-navigate":
            return {"ok": True, "attempted": True, "reason": "native_gesture_sent_postcondition_required",
                    "state": "pending", "action_count": 1, "day_count": 1}
        return {"ok": True, "attempted": True, "reason": "native_gesture_sent_postcondition_required"}

    def gate(self, families=(), repeatable=False, spend=None, bridge=None, enabled=True, pipeline=False, table=None,
             limits=None):
        config = wc.RunConfig(enabled, frozenset(families), repeatable) if enabled else wc.RunConfig()
        endpoints = {"daily": wc.Endpoint("daily", daily_session()), "grade": wc.Endpoint("grade", grade_session()),
                     "repeatable": wc.Endpoint("repeatable", repeatable_session())}
        if pipeline:
            endpoints = {"pipeline": wc.Endpoint("pipeline", pipeline_session(table or TABLE))}
        return wc.WebViewInputGate(bridge or FakeBridge(), config, endpoints, spend, self.audit, self.send,
                                   policy=POLICY_ON, clock=self.clock, decode=lambda blob: FakeImage(),
                                   action_table=table, limits=limits, frame_guard_enabled=pipeline)

    def log(self):
        path = AUDIT_ROOT / "2026-10-02.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def capture_and_click(self, gate, point, *, action=None):
        self.assertEqual(gate.screencap().shape, (720, 1280, 3))
        if gate.config.pipeline:
            self.permit(gate, point, action=action)
        return gate.click(*point)

    def permit(self, gate, point, action=None):
        """Synthetic completed parent recognition for transport-only unit tests."""
        hits = gate.table.at(point, gate.config.families)
        if action is not None:
            hits = [candidate for candidate in hits if candidate.id == action]
        else:  # v2 rects may overlap across families (wing-live judges over home-mission); the node picks one
            hits = [candidate for candidate in hits if candidate.enabled][:1]
        if len(hits) != 1 or not hits[0].nodes:
            return
        token = gate.frame_permits.issue(hits[0].nodes[0], hits[0].id, 1, 2, FakeImage(), gate._frame)
        if token is not None:
            self.assertTrue(gate.frame_permits.arm(token, 1, 2, hits[0].nodes[0]))

    def test_disabled_by_default(self):
        gate = wc.WebViewInputGate(FakeBridge(), audit=self.audit, send=self.send, clock=self.clock,
                                   decode=lambda blob: FakeImage())
        self.assertFalse(gate.config.input_enabled)
        self.assertFalse(self.capture_and_click(gate, (62, 429)))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.log()[-1]["reason"], "input_disabled")

    def timed_gate(self, **status):
        gate = self.gate(PIPELINE, pipeline=True,
                         bridge=FakeBridge(capture_started_ms=1_000_000, capture_completed_ms=1_000_040, **status))
        gate.endpoints["pipeline"].session["appeal_timing_protocol"] = wc.APPEAL_TIMING_PROTOCOL
        authority = wc.LiveSpendAuthority(gate)
        authority.configure_appeal_timing(True)
        return gate, authority

    def stage_timing(self, gate, authority):
        from agent.core.wing_live_timing import TimingBar
        return authority.stage_appeal_timing(FakeImage(),
            TimingBar((("Normal", 543, 716), ("Good", 717, 804), ("Perfect", 805, 890)), 650),
            period_s=.97, event_offset_ms=0)

    def test_timed_appeal_bound_to_host_capture_and_one_shot_permit(self):
        gate, authority = self.timed_gate()
        gate.screencap()
        why, plan = self.stage_timing(gate, authority)
        self.assertIsNone(why)
        self.permit(gate, (640, 380), action=wc.APPEAL_ACTION)
        self.assertTrue(gate.click(640, 380))
        request = self.sent[-1][1]
        self.assertEqual(request["action"], wc.APPEAL_ACTION)
        self.assertEqual(request["appeal_timing"], plan["request"])
        self.assertGreater(request["appeal_timing"]["target_down_ms"], 1_000_000)
        self.assertEqual(self.log()[-1]["appeal_timing"]["capture_half_interval_ms"], 20)
        self.assertIsNone(gate._appeal_timing)
        self.assertFalse(gate.click(640, 380))
        self.assertEqual(len(self.sent), 1)

    def test_timed_appeal_never_falls_back_after_recapture_or_missing_capability(self):
        gate, authority = self.timed_gate()
        gate.screencap()
        self.assertIsNone(self.stage_timing(gate, authority)[0])
        gate.screencap()  # even identical pixels have a new host frame sequence
        self.permit(gate, (640, 380), action=wc.APPEAL_ACTION)
        self.assertFalse(gate.click(640, 380))
        self.assertEqual(self.log()[-1]["reason"], "appeal_timing_frame_missing")
        gate.endpoints["pipeline"].session.pop("appeal_timing_protocol")
        self.assertEqual(self.stage_timing(gate, authority)[0], "appeal_timing_host_unavailable")
        self.assertEqual(self.sent, [])

    def test_timed_metadata_does_not_change_ordinary_click_or_survive_task_reset(self):
        gate, authority = self.timed_gate()
        gate.screencap()
        self.stage_timing(gate, authority)
        self.permit(gate, (62, 429), action="home-mission")
        self.assertTrue(gate.click(62, 429))
        self.assertNotIn("appeal_timing", self.sent[-1][1])
        gate.screencap()
        self.stage_timing(gate, authority)
        gate.begin_task("other")
        self.assertFalse(gate.appeal_timing_required)
        self.assertIsNone(gate._appeal_timing)

    def test_timed_plan_rejects_other_analysed_pixels_and_old_capture_telemetry(self):
        gate, authority = self.timed_gate()
        gate.screencap()
        from agent.core.wing_live_timing import TimingBar
        class WrongImage(FakeImage):
            def tobytes(self):
                return b"another frame"
        bar = TimingBar((("Perfect", 805, 890),), 650)
        self.assertEqual(authority.stage_appeal_timing(WrongImage(), bar, period_s=.97, event_offset_ms=0)[0],
                         "analysed_image_is_not_the_latest_frame")
        gate.bridge.status_value.pop("capture_started_ms")
        gate.screencap()
        self.assertEqual(self.stage_timing(gate, authority)[0], "appeal_timing_unread")
        self.assertEqual(self.sent, [])

    def test_grade_timed_appeal_needs_v2_host_and_its_own_node(self):
        from agent.core.wing_live_timing import TimingBar
        gate, authority = self.timed_gate()   # v1 host: W.I.N.G. bar only
        gate.screencap()
        bar = TimingBar((("Normal", 543, 716), ("Good", 717, 804), ("Perfect", 805, 890)), 650)
        stage = lambda action: authority.stage_appeal_timing(FakeImage(), bar, period_s=.965, event_offset_ms=150,
                                                             action=action)
        self.assertFalse(authority.appeal_timing_supported(wc.GRADE_APPEAL_ACTION))
        self.assertEqual(stage(wc.GRADE_APPEAL_ACTION)[0], "appeal_timing_host_unavailable")
        gate.endpoints["pipeline"].session["appeal_timing_protocol"] = wc.APPEAL_TIMING_PROTOCOL_V2
        self.assertTrue(authority.appeal_timing_supported(wc.GRADE_APPEAL_ACTION))
        self.assertTrue(authority.appeal_timing_supported(wc.APPEAL_ACTION))
        self.assertEqual(authority.capture_interval(FakeImage()), (1_000_000, 1_000_040))
        why, plan = stage(wc.GRADE_APPEAL_ACTION)
        self.assertIsNone(why)
        self.permit(gate, (420, 500), action=wc.GRADE_APPEAL_ACTION)
        self.assertTrue(gate.click(420, 500))
        request = self.sent[-1][1]
        self.assertEqual((request["action"], request["node_name"], request["appeal_timing"]),
                         (wc.GRADE_APPEAL_ACTION, wc.GRADE_APPEAL_NODE, plan["request"]))
        # A plan staged for the Grade bar never rides on the W.I.N.G. tap.
        gate.screencap()
        self.assertIsNone(stage(wc.GRADE_APPEAL_ACTION)[0])
        self.permit(gate, (640, 380), action=wc.APPEAL_ACTION)
        self.assertFalse(gate.click(640, 380))
        self.assertEqual(self.log()[-1]["reason"], "appeal_timing_frame_missing")
        self.assertEqual(len(self.sent), 1)
        # Untimed Grade tap once the runtime switched timing off for its fallback.
        gate.screencap()
        authority.configure_appeal_timing(False)
        self.permit(gate, (420, 500), action=wc.GRADE_APPEAL_ACTION)
        self.assertTrue(gate.click(420, 500))
        self.assertNotIn("appeal_timing", self.sent[-1][1])

    def test_session_accepts_only_the_v1_and_v2_timing_protocols(self):
        session = {**pipeline_session(), "appeal_timing_protocol": wc.APPEAL_TIMING_PROTOCOL}
        self.assertEqual(wc.validate_pipeline_session({**session, "appeal_timing_protocol": wc.APPEAL_TIMING_PROTOCOL_V2},
                                                      session["pid"])["appeal_timing_protocol"],
                         wc.APPEAL_TIMING_PROTOCOL_V2)
        with self.assertRaises(ValueError):
            wc.validate_pipeline_session({**session, "appeal_timing_protocol": "SC.AppealTiming.v9"}, session["pid"])
        self.assertEqual(wc.APPEAL_TIMING_TARGETS[wc.APPEAL_TIMING_PROTOCOL], {wc.APPEAL_ACTION: wc.APPEAL_NODE})

    def test_enable_requires_policy_and_family_needs_enable(self):
        with self.assertRaises(ValueError):
            wc.WebViewInputGate(FakeBridge(), wc.RunConfig(True, frozenset({"daily"})), policy={"live_input_enabled": False})
        with self.assertRaises(ValueError):
            wc.RunConfig(False, frozenset({"daily"}))
        with self.assertRaises(ValueError):
            wc.RunConfig.from_dict({"input_enabled": True, "families": ["arbitrary"]})
        self.assertEqual(wc.RunConfig.from_dict({}), wc.RunConfig())

    def test_unknown_and_ambiguous_coordinates_denied(self):
        gate = self.gate({"daily"})
        self.assertFalse(self.capture_and_click(gate, (300, 300)))
        self.assertEqual(self.log()[-1]["reason"], "unknown_point")
        self.assertFalse(self.capture_and_click(gate, (57, 662)))
        self.assertTrue(self.log()[-1]["reason"].startswith("ambiguous_point:"))
        gate.set_scope({"mission-back"})
        self.assertTrue(self.capture_and_click(gate, (57, 662)))
        self.assertEqual(self.sent[-1][1]["action"], "mission-back")
        with self.assertRaises(ValueError):
            gate.set_scope({"shop-exchange-confirm-1"})

    def test_mapping_per_family(self):
        cases = [({"daily"}, False, (62, 429), "home-mission", 50001, "daily-navigate"),
                 ({"training"}, False, (516, 299), "idol-training", 50001, "daily-navigate"),
                 ({"shop"}, False, (495, 110), "shop-money-tab", 50001, "daily-navigate"),
                 ({"grade"}, False, (1188, 541), "fes-grade-entry", 50002, "daily-navigate"),
                 ({"daily"}, True, (62, 429), "home-mission", 50003, "repeatable-navigate"),
                 ({"grade"}, True, (1167, 453), "grade-mode-grade", 50003, "repeatable-navigate"),
                 ({"training"}, True, (1085, 347), "training-bronze-plus", 50001, "daily-navigate")]
        for families, repeatable, point, action, port, command in cases:
            with self.subTest(action=action, repeatable=repeatable):
                self.sent.clear()
                self.assertTrue(self.capture_and_click(self.gate(families, repeatable), point))
                (sent_port, request), = self.sent
                self.assertEqual((sent_port, request["command"], request["action"], request["frame_sha256"]),
                                 (port, command, action, SHA))
                self.assertEqual(request["token"], TOKEN)
        gate = self.gate({"scroll"})
        gate.screencap()
        self.assertFalse(gate.scroll(0, -240))  # three scroll sets share a delta
        gate.set_scope({"match-skip-scroll-down", "match-skip-scroll-up"})
        self.assertTrue(gate.scroll(0, -240))
        self.assertEqual(self.sent[-1][1]["action"], "match-skip-scroll-down")
        gate.screencap()
        self.assertFalse(gate.scroll(0, -120))

    def test_spend_requires_spend_gate_grant(self):
        for families, point, action in (({"shop"}, (740, 626), "shop-exchange-confirm-2"),
                                        ({"training"}, (740, 510), "training-confirm-one-bronze"),
                                        ({"grade"}, (638, 360), "grade-difficulty-normal")):
            with self.subTest(action=action):
                self.sent.clear()
                denied = self.gate(families, repeatable=True)
                self.assertFalse(self.capture_and_click(denied, point))
                self.assertEqual(self.log()[-1]["reason"], "spend_not_authorized")
                with self.assertRaises(ValueError):
                    denied.spend_gate.authorize(action, SHA, "SC.Test")
                spend = wc.SpendGate({action}, clock=self.clock)
                gate = self.gate(families, repeatable=True, spend=spend)
                gate.screencap()
                spend.authorize(action, gate.latest_frame_sha256, "SC.Test.Approve")
                self.assertTrue(gate.click(*point))
                (port, request), = self.sent
                self.assertEqual(request["command"], "daily-navigate")  # never the repeatable ledger
                self.assertEqual((port, request["action"]), (50002 if families == {"grade"} else 50001, action))
                gate.screencap()
                self.assertFalse(gate.click(*point))  # grant is one-shot
                with self.assertRaises(ValueError):
                    spend.authorize(action, SHA, "SC.Test.Approve")  # per-run cap

    def test_spend_grant_bound_to_frame_and_ttl(self):
        spend = wc.SpendGate({"shop-exchange-confirm-1"}, clock=self.clock)
        gate = self.gate({"shop"}, spend=spend)
        gate.screencap()
        spend.authorize("shop-exchange-confirm-1", "b" * 64, "SC.Test")
        self.assertFalse(gate.click(740, 626))
        self.assertEqual(self.log()[-1]["reason"], "spend_grant_frame_mismatch")
        spend = wc.SpendGate({"shop-exchange-confirm-1"}, clock=self.clock)
        gate = self.gate({"shop"}, spend=spend)
        gate.screencap()
        spend.authorize("shop-exchange-confirm-1", SHA, "SC.Test")
        self.clock.now += 6
        self.assertFalse(gate.click(740, 626))
        self.assertIn(self.log()[-1]["reason"], ("spend_grant_expired", "frame_missing_or_stale"))
        self.assertEqual(self.sent, [])

    def test_stale_or_changed_frame_denied(self):
        gate = self.gate({"daily"})
        self.assertFalse(gate.click(62, 429))  # no screencap yet
        gate.screencap()
        self.clock.now += 5.5
        self.assertFalse(gate.click(62, 429))
        self.assertEqual(self.log()[-1]["reason"], "frame_missing_or_stale")
        gate = self.gate({"daily"}, bridge=FakeBridge(frame_sha256="c" * 64))
        self.assertFalse(self.capture_and_click(gate, (62, 429)))
        self.assertTrue(self.log()[-1]["reason"].startswith("frame_or_host_binding_failed"))
        gate = self.gate({"daily"})
        self.assertTrue(self.capture_and_click(gate, (62, 429)))
        self.assertFalse(gate.click(62, 429))  # a sent click drops the frame binding
        self.assertEqual(len(self.sent), 1)

    def test_screencap_refuses_minimized_or_wrong_size(self):
        gate = self.gate({"daily"}, bridge=FakeBridge(minimized=True))
        self.assertEqual(gate.screencap().shape, (0, 0, 3))
        self.assertIsNone(gate.latest_frame_sha256)
        bad = PNG[:16] + (1279).to_bytes(4, "big") + PNG[20:]
        gate = self.gate({"daily"}, bridge=FakeBridge(png=bad))
        self.assertEqual(gate.screencap().shape, (0, 0, 3))
        self.assertIn("1280x720", gate.capture_error)
        real = wc.WebViewInputGate(FakeBridge(), audit=self.audit, send=self.send, clock=self.clock)
        self.assertEqual(real.screencap().shape, (720, 1280, 3))
        self.assertEqual(real.latest_frame_sha256, SHA)

    def test_audit_log_written(self):
        gate = self.gate({"daily"})
        self.assertTrue(self.capture_and_click(gate, (62, 429)))
        capture, intent, result = self.log()
        self.assertEqual((capture["kind"], capture["result"], capture["capture_seq"]), ("capture", "captured", 1))
        self.assertEqual((intent["result"], result["result"]), ("intent", "sent"))
        self.assertEqual(intent["attempt"], result["attempt"])
        self.assertEqual((result["action"], result["frame_sha256"], result["endpoint"]), ("home-mission", SHA, "daily"))

    def test_frame_diagnostics_match_and_legacy_host_remain_compatible(self):
        gate = self.gate(PIPELINE, pipeline=True)
        self.assertTrue(self.capture_and_click(gate, (62, 429)))
        record = self.log()[-1]
        self.assertTrue(record["frame_binding_match"])
        self.assertEqual(record["host_at_click"]["frame_sha256"], SHA)
        self.assertEqual(record["host_at_click"]["binding_event"], "unknown")
        self.assertEqual(record["host_at_click"]["capture_seq"], 1)
        legacy = self.gate({"daily"}, bridge=FakeBridge(capture_seq=None, frame_binding_seq=None))
        self.assertTrue(self.capture_and_click(legacy, (62, 429)))

    def test_host_emergency_stop_reply_latches_the_run_stop(self):
        """紧急停止 for a command-line run: the host refuses with emergency_stop and the whole run stops."""
        from types import SimpleNamespace
        stops = []
        gate = self.gate(PIPELINE, pipeline=True)
        gate.run_control = SimpleNamespace(poll=lambda: bool(stops), request_stop=stops.append, input_event=lambda r: True)
        gate.send = lambda session, request: {"ok": False, "attempted": False, "reason": wc.HOST_EMERGENCY_STOP}
        self.assertFalse(self.capture_and_click(gate, (62, 429)))
        self.assertEqual(stops, [wc.HOST_EMERGENCY_STOP])
        self.assertEqual(self.log()[-1]["reason"], wc.HOST_EMERGENCY_STOP)
        self.assertFalse(self.capture_and_click(gate, (62, 429)))
        self.assertEqual(self.log()[-1]["reason"], "run_stopped")

    def test_external_capture_and_invalidation_diagnostics_never_send(self):
        for event, sha, capture_seq in (("capture_bridge", "c" * 64, 2),
                                        ("manual_mouse_move", None, 1),
                                        ("navigation_starting", None, 1),
                                        ("unknown", None, None)):
            with self.subTest(event=event):
                bridge = FakeBridge(capture_seq=0, frame_binding_seq=0)
                gate = self.gate(PIPELINE, pipeline=True, bridge=bridge)
                gate.screencap()
                self.permit(gate, (62, 429))
                bridge.status_value.update(frame_sha256=sha, capture_seq=capture_seq,
                                           frame_binding_seq=2, frame_binding_event=event, capture_source="capture_bridge")
                self.assertFalse(gate.click(62, 429))
                record = self.log()[-1]
                self.assertFalse(record["frame_binding_match"])
                self.assertEqual(record["frame_sha256"], SHA)
                self.assertEqual(record["host_at_click"]["frame_sha256"], sha)
                self.assertEqual(record["host_at_click"]["binding_event"], event)
                self.assertEqual(record["host_at_click"]["capture_seq"], capture_seq)
                self.assertEqual(self.sent, [])
                self.assertEqual(gate.run_clicks, 0)

    def test_pre_down_host_refusal_counts_as_not_sent(self):
        """2026-10-15 live: Review ← refused by the host guard after the hover moves (attempted=True, no Down)."""
        from types import SimpleNamespace
        gate = SimpleNamespace(history=[])
        authority = wc.LiveSpendAuthority.__new__(wc.LiveSpendAuthority)
        authority.gate = gate
        mark = authority.click_mark()
        gate.history.append({"kind": "click", "action": "wing-page-return", "result": "refused", "attempted": True,
                             "reason": "frame_changed_before_input_no_retry"})
        self.assertEqual(authority.clicks_since(mark), "not_sent")
        self.assertEqual(authority.last_outcome("wing-page-return"), "denied")
        gate.history.append({"kind": "click", "action": "wing-page-return", "result": "refused", "attempted": True,
                             "reason": "native_input_failed_no_retry: x"})            # may be after the Down
        self.assertEqual(authority.clicks_since(mark), "unknown")

    def test_capture_diagnostics_do_not_change_click_history_or_copy_secrets(self):
        bridge = FakeBridge(token=TOKEN, spend_secret=SECRET, frame_binding_event=SECRET, capture_source=TOKEN)
        gate = self.gate(PIPELINE, pipeline=True, bridge=bridge)
        authority = wc.LiveSpendAuthority(gate)
        mark = authority.click_mark()
        gate.screencap(source="maa_custom_controller")
        gate.screencap(source="maa_custom_controller")
        self.assertEqual(authority.click_mark(), mark)
        self.assertEqual(authority.clicks_since(mark), "not_sent")
        captures = self.log()
        self.assertEqual([r["capture_seq"] for r in captures], [1, 2])
        self.assertTrue(all(r["capture_source"] == "maa_custom_controller" for r in captures))
        self.assertTrue(all(r["host_before_capture"]["binding_event"] == "unknown" for r in captures))
        self.assertNotIn(TOKEN, json.dumps(captures))
        self.assertNotIn(SECRET, json.dumps(captures))

    def test_pipeline_maps_rect_points_to_one_action_and_sends_only_the_id(self):
        for action in TABLE.actions:
            if action.consuming or not action.enabled or action.drag_rect is not None:
                continue  # needs a grant (test_pipeline_consuming_action_needs_a_frame_bound_grant) / drag-only
            # 3 points per action; a fresh gate per action keeps 325+ actions under the 600-click run cap.
            gate = self.gate(PIPELINE, pipeline=True, limits=wc.ClickLimits(run_max=600))
            plus = action.id.startswith("match-skip-plus-")  # Plus also sends the observed point (validated in-rect)
            x, y, w, h = action.rect
            for point in ((x, y), (x + w - 1, y + h - 1), (x + w // 2, y + h // 2)):
                with self.subTest(action=action.id, point=point):
                    self.sent.clear()
                    self.assertTrue(self.capture_and_click(gate, point, action=action.id))
                    (port, request), = self.sent
                    self.assertEqual((port, request["command"], request["action"], request["frame_sha256"]),
                                     (50004, "pipeline-click", action.id, SHA))
                    self.assertEqual(set(request), {"token", "command", "action", "frame_sha256", "source_width",
                                                    "source_height", "nonce", "protocol_version", "capture_seq", "frame_binding_seq", "node_name"}
                                     | ({"target"} if plus else set()))
                    if plus:
                        self.assertEqual(request["target"], list(point))
                    self.assertEqual(self.log()[-1]["endpoint"], "pipeline")
        for point in ((0, 0), (0, 710), (140, 400), (1221, 30)):  # outside every rect, including business slots
            with self.subTest(point=point):
                self.assertFalse(self.capture_and_click(gate, point))
                self.assertEqual(self.log()[-1]["reason"], "pipeline_frame_guard_missing")
        gate.screencap()
        self.assertFalse(gate.scroll(0, -240))
        self.assertEqual(self.log()[-1]["reason"], "scroll_not_in_pipeline_vocabulary")

    def test_pipeline_family_and_scope_filter(self):
        gate = self.gate({"popup"}, pipeline=True)
        self.assertFalse(self.capture_and_click(gate, (57, 661)))  # nav-back, nav family disabled
        self.assertEqual(self.log()[-1]["reason"], "pipeline_frame_guard_missing")
        self.assertTrue(self.capture_and_click(gate, (640, 521)))
        gate.set_scope({"popup-help-close"})
        self.assertFalse(self.capture_and_click(gate, (640, 521)))
        with self.assertRaises(ValueError):
            gate.set_scope({"nav-back"})
        with self.assertRaises(ValueError):
            wc.RunConfig(True, frozenset({"popup", "daily"}))
        with self.assertRaises(ValueError):
            wc.RunConfig(True, frozenset({"popup"}), repeatable=True)

    def test_pipeline_refuses_consuming_actions_without_their_own_grant(self):
        table = pa.parse_table(SPEND_TABLE_RAW)
        spend = wc.SpendGate({"shop-exchange-confirm-1"}, clock=self.clock)
        gate = self.gate({"popup"}, spend=spend, pipeline=True, table=table)
        gate.screencap()
        spend.authorize("shop-exchange-confirm-1", gate.latest_frame_sha256, "SC.Test.Approve")
        self.permit(gate, (250, 50))
        self.assertFalse(gate.click(250, 50))  # a grant for another action does not cover popup-buy
        self.assertEqual(self.log()[-1]["reason"], "spend_not_authorized")
        self.assertEqual(self.log()[-1]["action"], "popup-buy")
        self.permit(gate, (50, 50))
        self.assertFalse(gate.click(740, 626))  # the legacy spend point is not a pipeline action
        self.assertEqual(self.log()[-1]["reason"], "unknown_point")
        self.assertEqual(self.sent, [])
        self.permit(gate, (50, 50))
        self.assertTrue(gate.click(50, 50))
        self.assertEqual(TABLE.consuming_ids, ["shop-exchange-confirm", "training-start", "grade-rehearsal-easy",
                                               "grade-rehearsal-normal", "grade-rehearsal-hard", "grade-tension-confirm",
                                               "match-skip-confirm", "match-play-live-start", "weekly-auto-skip",
                                               "weekly-auto-start", "weekly-auto-soda-start", "wing-reward-reroll-confirm",
                                               "wing-produce-start", "collabo-tension-decide"])   # W.I.N.G. start 2026-10-09; rehearsal/reroll consume

    def test_pipeline_consuming_action_needs_a_frame_bound_grant(self):
        import numpy as np
        pixels = np.zeros((720, 1280, 3), dtype=np.uint8)
        spend = wc.SpendGate({"shop-exchange-confirm"}, max_per_action=3, clock=self.clock)
        gate = wc.WebViewInputGate(FakeBridge(), wc.RunConfig(True, PIPELINE), {"pipeline": wc.Endpoint(
            "pipeline", pipeline_session())}, spend, self.audit, self.send, policy=POLICY_ON, clock=self.clock,
            decode=lambda blob: pixels, frame_guard_enabled=True)
        authority = wc.LiveSpendAuthority(gate)
        self.assertTrue(authority.can_spend("shop-exchange-confirm"))
        self.assertFalse(authority.can_spend("shop-exchange-confirm-1"))
        point = TABLE.centre("shop-exchange-confirm")
        gate.screencap()
        self.permit(gate, point, "shop-exchange-confirm")   # the gacha popup close overlaps this rect
        self.assertFalse(gate.click(*point))  # no grant
        self.assertEqual(self.log()[-1]["reason"], "spend_not_authorized")
        self.assertEqual(authority.last_outcome("shop-exchange-confirm"), "denied")
        gate.screencap()
        other = np.ones((720, 1280, 3), dtype=np.uint8)
        self.assertEqual(authority.authorize("shop-exchange-confirm", other, "SC.Shop.Check.Confirm"),
                         "analysed_image_is_not_the_latest_frame")
        self.assertIsNone(authority.authorize("shop-exchange-confirm", pixels, "SC.Shop.Check.Confirm"))
        self.permit(gate, point, "shop-exchange-confirm")
        self.assertTrue(gate.click(*point))
        (_, request), = self.sent
        self.assertEqual(request["action"], "shop-exchange-confirm")
        self.assertEqual(request["spend_grant"]["hmac"], wc.sign_spend_grant(
            SECRET, "shop-exchange-confirm", SHA, request["nonce"], request["spend_grant"]["expires_at"],
            request["capture_seq"], request["frame_binding_seq"]))
        self.assertEqual(authority.last_outcome("shop-exchange-confirm"), "sent")
        self.assertFalse(gate.click(*point))  # single use; also the frame binding is gone
        gate.screencap()
        self.permit(gate, point, "shop-exchange-confirm")
        self.assertFalse(gate.click(*point))
        self.assertEqual(self.log()[-1]["reason"], "spend_not_authorized")
        self.assertIsNone(authority.authorize("shop-exchange-confirm", pixels, "SC.Shop.Check.Confirm"))
        self.clock.now += 6  # grant and frame expire
        self.assertFalse(gate.click(*point))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(authority.authorize("shop-exchange-confirm", pixels, "SC.Shop.Check.Confirm"),
                         "frame_missing_or_stale")
        gate.screencap()
        self.assertIsNone(authority.authorize("shop-exchange-confirm", pixels, "SC.Shop.Check.Confirm"))
        with self.assertRaises(ValueError):  # three grants per action and run at most
            spend.authorize("shop-exchange-confirm", SHA, "SC.Shop.Check.Confirm", capture_seq=1, frame_binding_seq=2)
        no_spend = wc.LiveSpendAuthority(self.gate(PIPELINE, pipeline=True, spend=wc.SpendGate()))
        self.assertFalse(no_spend.can_spend("shop-exchange-confirm"))
        self.assertEqual(no_spend.authorize("shop-exchange-confirm", pixels, "SC.Shop.Check.Confirm"),
                         "spend_action_not_allowed")
        with self.assertRaises(ValueError):
            wc.SpendGate({"shop-exchange-confirm"}, max_per_action=4)

    def test_pipeline_grant_signing_still_matches_the_host_format(self):
        wall = [1759374000.0]
        spend = wc.SpendGate({"shop-exchange-confirm-1"}, clock=self.clock, wall_clock=lambda: wall[0])
        spend.authorize("shop-exchange-confirm-1", SHA, "SC.Test.Approve", capture_seq=17, frame_binding_seq=29)
        self.clock.now += 1.5
        grant = spend.take(wc.action_point("shop-exchange-confirm-1"))
        request = {"action": "shop-exchange-confirm-1", "frame_sha256": SHA, "nonce": "b" * 32,
                   "capture_seq": 17, "frame_binding_seq": 29}
        signed = spend.pipeline_grant(grant, pipeline_session(), request)
        self.assertEqual(signed["expires_at"], 1759374000000 + 3500)
        self.assertEqual(signed["hmac"], wc.sign_spend_grant(SECRET, "shop-exchange-confirm-1", SHA, "b" * 32,
                                                             signed["expires_at"], 17, 29))

    def test_pipeline_grant_vector_matches_host(self):
        # Same vector as browser_host/PipelineInputSelfTest.cs.
        self.assertEqual(wc.sign_spend_grant(SECRET, "shop-exchange-confirm-1", "a" * 64, "b" * 32, 1759374000000, 17, 29),
                         "d3fe989607ba19c4ce796c01f120fa2003b32f86b7a82e5e63ccf0b66288091a")
        with self.assertRaises(ValueError):
            wc.sign_spend_grant("zz" * 32, "shop-exchange-confirm-1", "a" * 64, "b" * 32, 1, 17, 29)

    def test_pipeline_request_requires_the_original_positive_int64_sequences(self):
        bridge = FakeBridge()
        bridge.screencap()
        def request(status, capture_seq=1, frame_binding_seq=2):
            return wc.pipeline_click_request(pipeline_session(), status, SHA, "home-mission",
                                             capture_seq=capture_seq, frame_binding_seq=frame_binding_seq,
                                             node_name="SC.MissionStatus.OpenMission")
        result = request(bridge.status())
        self.assertEqual((result["protocol_version"], result["capture_seq"], result["frame_binding_seq"]), (2, 1, 2))
        for key in ("capture_seq", "frame_binding_seq"):
            for value in (None, False, True, 0, -1, 1.0, "1", 2**63):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    request({**bridge.status(), key: value})
            invalid_original = {"capture_seq": 1, "frame_binding_seq": 2, key: True}
            with self.assertRaises(ValueError):
                request(bridge.status(), **invalid_original)
        for pending in (None, True, 0):
            with self.assertRaises(ValueError):
                request({**bridge.status(), "capture_pending": pending})
        bridge.screencap()  # Exactly the same bytes, but a new host binding.
        self.assertEqual(bridge.status()["frame_sha256"], SHA)
        with self.assertRaises(ValueError):
            request(bridge.status())
        with self.assertRaises(TypeError):
            wc.pipeline_click_request(pipeline_session(), bridge.status(), SHA, "home-mission")

    def test_match_plus_request_preserves_observed_point_and_rejects_other_targets(self):
        bridge = FakeBridge()
        bridge.screencap()
        def request(action, node, target):
            return wc.pipeline_click_request(pipeline_session(), bridge.status(), SHA, action,
                                             capture_seq=1, frame_binding_seq=2, node_name=node, target=target)
        result = request("match-skip-plus-5", "SC.Match.Skip.Plus5", (584, 514))
        self.assertEqual(result["target"], [584, 514])
        for action, node, point in (("match-skip-plus-5", "SC.Match.Skip.Plus5", (600, 514)),
                                    ("match-skip-plus-5", "SC.Match.Skip.Plus5", (584, 562)),
                                    ("home-mission", "SC.MissionStatus.OpenMission", (800, 680))):
            with self.subTest(action=action, point=point), self.assertRaises(ValueError):
                request(action, node, point)
        gate = self.gate({"match-fes"}, pipeline=True)
        gate.screencap()
        self.permit(gate, (584, 514), "match-skip-plus-5")
        self.assertTrue(gate.click(584, 514), self.log())
        self.assertEqual(self.sent[-1][1]["target"], [584, 514])

    def overlap_table(self, *, consuming=False):
        return pa.parse_table(json.dumps({"version": 2, "canvas": [1280, 720], "margin_percent": 20,
            "min_inner_px": 4, "families": ["popup"], "actions": [
                {"id": name, "family": "popup", "consuming": consuming and index == 1,
                 "rect": [0, 0, 100, 100], "source": "fixture", "nodes": ["SC.Test." + "AB"[index]]}
                for index, name in enumerate(("overlap-a", "shop-exchange-confirm" if consuming else "overlap-b"))]}).encode())

    def drag_table(self):
        return pa.parse_table(json.dumps({"version": 2, "canvas": [1280, 720], "margin_percent": 20,
            "min_inner_px": 4, "families": ["popup"], "actions": [
                {"id": "drag-a", "family": "popup", "consuming": False, "rect": [1000, 90, 270, 480], "source": "fixture",
                 "nodes": ["SC.Test.DragA"], "drag_rect": [1000, 175, 270, 70]},
                {"id": "nav-a", "family": "popup", "consuming": False, "rect": [0, 0, 100, 100], "source": "fixture",
                 "nodes": ["SC.Test.NavA"]}]}).encode())

    def test_drag_table_field_matches_the_host_rules(self):
        table = self.drag_table()
        self.assertEqual(table.get("drag-a").drag_rect, (1000, 175, 270, 70))
        self.assertIsNone(table.get("nav-a").drag_rect)
        base = {"id": "x", "family": "popup", "consuming": False, "rect": [0, 0, 10, 10], "source": "s", "nodes": ["SC.X"]}
        for extra in ({"drag_rect": [0, 0, 5, 5], "consuming": True}, {"drag_rect": [0, 0, 5, 5], "wheel": 120},
                      {"drag_rect": [1200, 0, 100, 5]}, {"drag_rect": [0, 0, 5]}):
            raw = {"version": 2, "canvas": [1280, 720], "margin_percent": 20, "min_inner_px": 4, "families": ["popup"],
                   "actions": [{**base, **extra}]}
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                pa.parse_table(json.dumps(raw).encode())
        v1 = {"version": 1, "canvas": [1280, 720], "margin_percent": 20, "min_inner_px": 4, "families": ["popup"],
              "actions": [{**base, "drag_rect": [0, 0, 5, 5]}]}
        with self.assertRaises(ValueError):
            pa.parse_table(json.dumps(v1).encode())

    def drag_session(self, table):
        return {**pipeline_session(table), "drag_protocol": wc.DRAG_PROTOCOL}

    def test_drag_request_binds_start_end_and_duration(self):
        table = self.drag_table()
        bridge = FakeBridge()
        bridge.screencap()
        def request(start, end, ms, session=None, **extra):
            return wc.pipeline_click_request(session or self.drag_session(table), bridge.status(), SHA, "drag-a",
                                             table=table, capture_seq=1, frame_binding_seq=2, node_name="SC.Test.DragA",
                                             target=start, drag_to=end, drag_ms=ms, **extra)
        result = request((1100, 530), (1100, 210), 400)
        self.assertEqual((result["target"], result["drag_to"], result["drag_ms"]), ([1100, 530], [1100, 210], 400))
        for start, end, ms in (((1100, 530), (1100, 300), 400), ((900, 530), (1100, 210), 400),
                               ((1100, 530), (1100, 210), 149), ((1100, 530), (1100, 210), 3001),
                               ((1100, 530), None, None), (None, (1100, 210), 400), ((1100, 530), (1100.5, 210), 400)):
            with self.subTest(start=start, end=end, ms=ms), self.assertRaises(ValueError):
                request(start, end, ms)
        with self.assertRaises(ValueError):   # an older host without the drag protocol
            request((1100, 530), (1100, 210), 400, session=pipeline_session(table))
        with self.assertRaises(ValueError):
            request((1100, 530), (1100, 210), 400, hold_ms=500)
        with self.assertRaises(ValueError):   # no drag on an action without drag_rect
            wc.pipeline_click_request(self.drag_session(table), bridge.status(), SHA, "nav-a", table=table,
                                      capture_seq=1, frame_binding_seq=2, node_name="SC.Test.NavA",
                                      target=(50, 50), drag_to=(60, 60), drag_ms=400)

    def drag_gate(self, table):
        gate = self.gate({"popup"}, pipeline=True, table=table)
        gate.endpoints["pipeline"] = wc.Endpoint("pipeline", self.drag_session(table))
        gate.screencap()
        return gate

    def test_maa_swipe_drags_only_a_drag_action_into_its_end_rect(self):
        table = self.drag_table()
        gate = self.drag_gate(table)
        self.permit(gate, (1100, 530), "drag-a")
        self.assertTrue(gate.swipe(1100, 530, 1100, 210, 400), self.log())
        request = self.sent[-1][1]
        self.assertEqual((request["action"], request["target"], request["drag_to"], request["drag_ms"]),
                         ("drag-a", [1100, 530], [1100, 210], 400))
        self.assertEqual(self.log()[-1]["result"], "sent")
        self.assertFalse(gate.swipe(1100, 530, 1100, 210, 400))   # the frame was consumed
        refusals = {}
        for args in ((1100, 530, 1100, 300, 400), (1100, 530, 1100, 210, 100), (50, 50, 60, 60, 400),
                     (1100, 530, 1100, 900, 400)):
            gate = self.drag_gate(table)
            self.permit(gate, args[:2])
            self.assertFalse(gate.swipe(*args))
            refusals[args] = self.log()[-1]["reason"]
        self.assertEqual(list(refusals.values()), ["swipe_end_out_of_bounds", "swipe_duration_out_of_bounds",
                                                   "swipe_action_not_draggable", "outside_canvas"])
        self.assertEqual(len(self.sent), 1)

    def test_a_drag_action_is_never_a_plain_click(self):
        table = self.drag_table()
        gate = self.drag_gate(table)
        self.permit(gate, (1100, 530), "drag-a")
        self.assertFalse(gate.click(1100, 530))
        self.assertEqual(self.sent, [])
        self.assertIn("拖动", self.log()[-1]["reason"])

    def test_swipe_outside_pipeline_mode_is_refused(self):
        gate = self.gate({"daily"})
        gate.screencap()
        self.assertFalse(gate.swipe(1100, 530, 1100, 210, 400))
        self.assertEqual(self.sent, [])

    def test_overlapping_point_sends_only_the_completed_recognition_action(self):
        table = self.overlap_table()
        for action in table.ids:
            gate = self.gate({"popup"}, pipeline=True, table=table)
            gate.screencap()
            self.permit(gate, (50, 50), action)
            self.assertTrue(gate.click(50, 50), self.log())
            request = self.sent[-1][1]
            self.assertEqual((request["action"], request["node_name"]), (action, table.get(action).nodes[0]))
            self.assertFalse(gate.click(50, 50))
        self.assertEqual([request["action"] for _, request in self.sent], table.ids)

    def test_invalid_parent_never_takes_a_grant_for_an_overlapping_action(self):
        table = self.overlap_table(consuming=True)
        spend = wc.SpendGate({"shop-exchange-confirm"}, clock=self.clock)
        gate = self.gate({"popup"}, pipeline=True, table=table, spend=spend)
        gate.screencap()
        spend.authorize("shop-exchange-confirm", gate.latest_frame_sha256, "SC.Test.Approve",
                        capture_seq=gate._frame.host_capture_seq, frame_binding_seq=gate._frame.host_binding_seq)
        self.assertFalse(gate.click(50, 50))
        self.assertEqual(self.log()[-1]["reason"], "pipeline_frame_guard_missing")
        self.permit(gate, (50, 50), "shop-exchange-confirm")
        self.assertTrue(gate.click(50, 50))
        self.assertEqual(self.sent[-1][1]["action"], "shop-exchange-confirm")

    def test_recognition_action_must_match_point_family_and_scope(self):
        table = self.overlap_table()
        for point, scope in (((150, 50), None), ((50, 50), {"overlap-b"})):
            gate = self.gate({"popup"}, pipeline=True, table=table)
            gate.screencap()
            self.permit(gate, (50, 50), "overlap-a")
            gate.set_scope(scope)
            self.assertFalse(gate.click(*point))
        gate = self.gate({"nav"}, pipeline=True, table=table)
        gate.screencap()
        token = gate.frame_permits.issue("SC.Test.A", "overlap-a", 1, 2, FakeImage(), gate._frame)
        self.assertTrue(gate.frame_permits.arm(token, 1, 2, "SC.Test.A"))
        self.assertFalse(gate.click(50, 50))
        self.assertEqual(self.sent, [])

    def test_v2_request_and_permit_reject_disabled_or_wrong_nodes(self):
        bridge = FakeBridge()
        bridge.screencap()
        for action, node in (("home-mission", None), ("home-mission", "SC.Startup.TouchToStart"),
                             ("present-list-scroll-down", "SC.Present.ScrollDown")):  # a disabled table action
            with self.subTest(action=action, node=node), self.assertRaises(ValueError):
                wc.pipeline_click_request(pipeline_session(), bridge.status(), SHA, action,
                                          capture_seq=1, frame_binding_seq=2, node_name=node)
        gate = self.gate({"present"}, pipeline=True)
        gate.screencap()
        self.assertNotIn("present-list-scroll-down", gate.enabled_actions())
        self.assertIsNone(gate.frame_permits.issue("SC.Present.ScrollDown", "present-list-scroll-down",
                                                 1, 2, FakeImage(), gate._frame))

    def test_pipeline_spend_grant_cannot_rebase_onto_an_identical_new_capture(self):
        spend = wc.SpendGate({"shop-exchange-confirm"}, clock=self.clock)
        spend.authorize("shop-exchange-confirm", SHA, "SC.Test.Approve", capture_seq=1, frame_binding_seq=2)
        grant = spend.take_action("shop-exchange-confirm")
        original = {"action": "shop-exchange-confirm", "frame_sha256": SHA, "nonce": "b" * 32,
                    "capture_seq": 1, "frame_binding_seq": 2}
        signed = spend.pipeline_grant(grant, pipeline_session(), original)
        for key in ("capture_seq", "frame_binding_seq"):
            with self.assertRaises(ValueError):
                spend.pipeline_grant(grant, pipeline_session(), {**original, key: original[key] + 1})
        self.assertEqual((signed["capture_seq"], signed["frame_binding_seq"]), (1, 2))
        self.assertIsNone(spend.take_action("shop-exchange-confirm"))
        for sequences in ((None, None), (True, 2), (1, 2**63)):
            with self.assertRaises(ValueError):
                wc.SpendGate({"shop-exchange-confirm"}).authorize("shop-exchange-confirm", SHA, "SC.Test.Approve",
                    capture_seq=sequences[0], frame_binding_seq=sequences[1])

    def test_pipeline_requires_host_flag(self):
        gate = self.gate(PIPELINE, bridge=FakeBridge(pipeline_input=False), pipeline=True)
        self.assertFalse(self.capture_and_click(gate, (62, 429)))
        self.assertTrue(self.log()[-1]["reason"].startswith("frame_or_host_binding_failed"))
        self.assertEqual(self.sent, [])

    def test_pipeline_requires_parent_permit_and_stays_disabled_by_default(self):
        gate = self.gate(PIPELINE, pipeline=True)
        gate.screencap()
        self.assertFalse(gate.click(62, 429))
        self.assertEqual(self.log()[-1]["reason"], "pipeline_frame_guard_missing")
        gate.configure_frame_guard(False)
        gate.screencap()
        self.assertFalse(gate.click(62, 429))
        self.assertEqual(self.log()[-1]["reason"], "pipeline_frame_guard_disabled")
        self.assertEqual(self.sent, [])

    def test_same_pixels_recapture_invalidates_the_parent_permit(self):
        gate = self.gate(PIPELINE, pipeline=True)
        gate.screencap()
        self.permit(gate, (62, 429))
        first = gate._frame
        gate.screencap()
        self.assertEqual(first.sha256, gate._frame.sha256)
        self.assertNotEqual(first.capture_seq, gate._frame.capture_seq)
        self.assertFalse(gate.click(62, 429))
        self.assertEqual(self.log()[-1]["reason"], "pipeline_frame_guard_missing")
        self.assertEqual(self.sent, [])

    def test_first_unknown_click_attempt_also_consumes_parent_permit(self):
        gate = self.gate(PIPELINE, pipeline=True)
        gate.screencap()
        self.permit(gate, (62, 429))
        self.assertFalse(gate.click(0, 0))
        self.assertFalse(gate.click(62, 429))
        self.assertEqual(self.log()[-1]["reason"], "pipeline_frame_guard_missing")
        self.assertEqual(self.sent, [])

    def test_external_same_pixel_host_capture_is_rejected_by_sequence(self):
        bridge = FakeBridge()
        gate = self.gate(PIPELINE, pipeline=True, bridge=bridge)
        gate.screencap()
        self.permit(gate, (62, 429))
        bridge.screencap()  # identical PNG; only the host sequence changes
        self.assertFalse(gate.click(62, 429))
        self.assertTrue(self.log()[-1]["reason"].startswith("frame_or_host_binding_failed:"))
        self.assertTrue(self.log()[-1]["frame_binding_match"])
        self.assertEqual(self.sent, [])

    def test_pipeline_session_validation(self):
        self.assertEqual(wc.validate_pipeline_session(pipeline_session(), PID)["port"], 50004)
        bad = []
        for key, value in (("transport", "webview2-composition-daily"), ("audit_root", str(wc.ROOT / "docs")),
                           ("actions", TABLE.ids[:-1]), ("actions", list(reversed(TABLE.ids))),
                           ("actions", sorted(wc.all_actions())), ("consuming_actions", ["shop-exchange-confirm-1"]),
                           ("action_table_sha256", "0" * 64), ("spend_secret", "x" * 64), ("host", "0.0.0.0"),
                           ("input_enabled", False), ("version", 1), ("version", True),
                           ("frame_binding_protocol", "SC.PipelineFrameBinding.v1")):
            data = pipeline_session()
            data[key] = value
            bad.append(data)
        for key, value in (("min_interval_ms", 100), ("interval_jitter_max_ms", -1), ("session_max_ms", 43_200_001),
                           ("run_max", -1)):
            data = pipeline_session()
            data["limits"] = dict(data["limits"], **{key: value})
            bad.append(data)
        data = pipeline_session()
        data["limits"].pop("session_max_ms")
        bad.append(data)
        data = pipeline_session()
        data.pop("spend_secret")
        bad.append(data)
        for data in bad:
            with self.assertRaises(ValueError):
                wc.validate_pipeline_session(data, PID)
        with self.assertRaises(ValueError):
            wc.validate_pipeline_session(pipeline_session(), PID + 1)

    def test_pipeline_session_run_begin_command_is_optional_and_exact(self):
        """2026-10-11 hosts name the launcher-only run-window handshake; older hosts omit it."""
        data = dict(pipeline_session(), run_begin_command="pipeline-run-begin")
        self.assertEqual(wc.validate_pipeline_session(data, PID)["run_begin_command"], wc.PIPELINE_RUN_BEGIN_COMMAND)
        for value in ("pipeline-click", "", None, 1):
            with self.assertRaises(ValueError):
                wc.validate_pipeline_session(dict(pipeline_session(), run_begin_command=value), PID)

    def test_agent_code_never_sends_the_run_begin_handshake(self):
        """Only the launcher (tools/run_tasks.begin_host_run) may open a new host run window; the Agent never does."""
        root = Path(__file__).resolve().parents[1] / "agent"
        hits = {}
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            count = text.count("pipeline-run-begin") + text.count("PIPELINE_RUN_BEGIN_COMMAND")
            if count:
                hits[path.relative_to(root).as_posix()] = count
        self.assertEqual(hits, {"core/webview_controller.py": 4})   # the constant and the session validator only

    def test_load_endpoints_by_family(self):
        root = AUDIT_ROOT / "sessions"
        root.mkdir(parents=True)
        base = root / "capture.json"
        Path(str(base) + ".daily-input.json").write_text(json.dumps(daily_session()), encoding="utf-8")
        legacy, pipeline = wc.RunConfig(True, frozenset({"daily"})), wc.RunConfig(True, PIPELINE)
        self.assertEqual(set(wc.load_endpoints(base, PID, legacy)), {"daily"})
        self.assertEqual(wc.load_endpoints(base, PID, pipeline), {})  # no pipeline session -> no endpoint
        Path(str(base) + wc.PIPELINE_SUFFIX).write_text(json.dumps(pipeline_session()), encoding="utf-8")
        self.assertEqual(set(wc.load_endpoints(base, PID, pipeline)), {"pipeline"})
        self.assertEqual(set(wc.load_endpoints(base, PID, legacy)), {"daily"})
        self.assertEqual(wc.load_endpoints(base, PID, wc.RunConfig()), {})

    def test_run_task_and_session_click_caps(self):
        gate = self.gate(PIPELINE, pipeline=True, limits=wc.ClickLimits(run_max=3, task_max=2, session_max_s=60))
        gate.begin_task("Startup")
        self.assertTrue(self.capture_and_click(gate, (640, 521), action="popup-reward-ok"))
        self.assertTrue(self.capture_and_click(gate, (640, 521), action="popup-reward-ok"))
        self.assertFalse(self.capture_and_click(gate, (640, 521), action="popup-reward-ok"))
        self.assertEqual(self.log()[-1]["reason"], "task_click_cap_reached")
        self.assertEqual(self.log()[-1]["task"], "Startup")
        gate.begin_task("MissionStatus", 1)
        self.assertTrue(self.capture_and_click(gate, (62, 429)))
        gate.begin_task("Other")
        self.assertFalse(self.capture_and_click(gate, (62, 429)))
        self.assertEqual(self.log()[-1]["reason"], "run_click_cap_reached")
        self.assertEqual((gate.run_clicks, len(self.sent)), (3, 3))
        with self.assertRaises(ValueError):
            gate.begin_task("Loose", 3)  # task caps can only tighten
        self.assertEqual([h["result"] for h in gate.history], ["sent", "sent", "denied", "sent", "denied"])
        timed = self.gate(PIPELINE, pipeline=True, limits=wc.ClickLimits(session_max_s=10))
        self.clock.now += 11
        self.assertFalse(self.capture_and_click(timed, (62, 429)))
        self.assertEqual(self.log()[-1]["reason"], "session_time_cap_reached")
        for bad in ({"run_max": -1}, {"task_max": -1}, {"session_max_s": 43201}, {"session_max_s": 0}):
            with self.assertRaises(ValueError):
                wc.ClickLimits(**bad)
        self.assertEqual(wc.ClickLimits(), wc.ClickLimits(None, None, 14400.0))   # no click caps

    def test_audit_root_must_be_private(self):
        with self.assertRaises(ValueError):
            wc.AuditLog(wc.ROOT / "docs")

    def test_spending_points_never_collide_with_navigation(self):
        spend_points = {wc.action_point(a) for a in wc.LEGACY_SPENDING_ACTIONS}
        others = {wc.action_point(a) for family in wc.CLICK_FAMILIES.values() for a in family
                  if a not in wc.SPENDING_ACTIONS}
        self.assertFalse(spend_points & others)
        self.assertEqual(set(wc.CLICK_FAMILIES["grade"]), set(grade_protocol.GRADE_ACTION_POINTS))

    def test_maa_controller_denies_swipe_and_text(self):
        try:
            controller = wc.create_webview_controller(self.gate({"daily"}))
        except (ImportError, OSError, RuntimeError) as exc:
            self.skipTest("MaaFramework unavailable: " + str(exc))
        self.assertEqual(controller.get_features(), 0)
        self.assertFalse(controller.swipe(0, 0, 100, 100, 200))
        self.assertFalse(controller.input_text("secret"))
        self.assertFalse(controller.click_key(13))
        self.assertFalse(controller.touch_down(0, 62, 429, 1))
        self.assertEqual(self.sent, [])
        reasons = [(r["kind"], r["result"]) for r in self.log()]
        self.assertIn(("swipe", "denied"), reasons)
        self.assertNotIn("secret", json.dumps(self.log()))


if __name__ == "__main__":
    unittest.main()
