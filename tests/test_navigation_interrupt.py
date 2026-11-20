import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from agent.core import navigation_interrupt as interrupt
from agent.core.daily_navigation import GROUPS, POST_GROUPS
from tools import repeatable_navigation_protocol as protocol
from tools import run_daily_navigation as nav_runner
from tools import run_daily_shop as shop_runner
from tools.navigation_protocol import ALL_DAILY_ACTION_POINTS, DAILY_SHOP_ACTION_POINTS

ROOT = Path(__file__).resolve().parents[1]
PRE, POST = "a" * 64, "b" * 64
CLEAR = {"state": "no_known_popup", "popup": None, "complete_contracts": [], "partial_contracts": []}


def hit(scene, score=.97):
    return {"scene": scene, "hit": True, "score": score, "raw_detail": {"filtered": [{"box": [0, 0, 1, 1]}]}}


def miss(scene, score=.3):
    return {"scene": scene, "hit": False, "score": score, "raw_detail": {"filtered": []}}


def frame(*complete, present=()):
    """Navigation recognitions: groups in ``complete`` fully match, ``present`` only at presence score."""
    hits = []
    for group, names in GROUPS.items():
        for name in names:
            if group in complete:
                hits.append(hit(name))
            elif group in present:
                hits.append(miss(name, .85))
            else:
                hits.append(miss(name))
    return hits


def point(action):
    return (ALL_DAILY_ACTION_POINTS | DAILY_SHOP_ACTION_POINTS)[action]


class InterruptedDecisionTests(unittest.TestCase):
    def decide(self, action="home-shop", hits=None, foreground=CLEAR, pre=PRE, post=POST, pids=(7, 7), expected=False, at=None):
        hits = frame("Home", "MissionCompleted") if hits is None else hits
        return interrupt.decide_interrupted(action, point(action) if at is None else at, hits, foreground, pre, post,
                                            pids[0], pids[1], expected)

    def test_positive_mission_completed_over_home_for_each_home_action(self):
        for action in ("home-shop", "home-mission", "home-fes", "home-idol"):
            with self.subTest(action=action):
                result = self.decide(action)
                self.assertTrue(result["interrupted"], result["checks"])
                self.assertEqual(result["interruption"], {
                    "popup": "MissionCompleted", "pre_group": "Home", "underlying_group": "Home",
                    "click_point": list(point(action)), "panel_rects": [[346, 69, 588, 575]], "panel_margin": 8,
                    "popup_complete": True, "underlying_present": True, "post_differs": True, "expected_post_complete": False})

    def test_pickup_positive_except_on_its_checkbox(self):
        self.assertTrue(self.decide("home-fes", frame("Home", "Pickup"))["interrupted"])
        refused = self.decide("home-idol", frame("Home", "Pickup"))
        self.assertFalse(refused["interrupted"])
        self.assertEqual([k for k, v in refused["checks"].items() if not v], ["click_outside_panel"])

    def test_home_presence_beneath_is_enough(self):
        self.assertTrue(self.decide(hits=frame("MissionCompleted", present=("Home",)))["interrupted"])

    def failing(self, result):
        self.assertFalse(result["interrupted"])
        self.assertNotIn("interruption", result)
        return [k for k, v in result["checks"].items() if not v]

    def test_each_condition_is_required(self):
        cases = {
            "same_pid": dict(pids=(7, 8)),
            "post_differs": dict(post=PRE),
            "expected_post_not_complete": dict(expected=True),
            "click_outside_panel": dict(at=(640, 360)),
            "pre_group_present_beneath": dict(hits=frame("MissionCompleted")),
            "foreground_allowed": dict(foreground={**CLEAR, "state": "known_popup"}),
        }
        for check, change in cases.items():
            with self.subTest(check=check):
                self.assertIn(check, self.failing(self.decide(**change)))
        # Non-Home actions are never interruptible, even with a matching popup frame.
        self.assertIn("action_interruptible", self.failing(self.decide("mission-back", at=(57, 662))))
        self.assertIn("action_interruptible", self.failing(self.decide("shop-back", at=(57, 662))))
        self.assertIn("same_pid", self.failing(self.decide(pids=(None, None))))

    def test_popup_must_be_single_complete_and_whitelisted(self):
        incomplete = [r if r["scene"] != GROUPS["MissionCompleted"][0] else miss(r["scene"]) for r in frame("Home", "MissionCompleted")]
        self.assertIn("single_whitelisted_popup_complete", self.failing(self.decide(hits=incomplete)))
        self.assertIn("single_whitelisted_popup_complete", self.failing(self.decide(hits=frame("Home", "MissionCompleted", "Pickup"))))
        # MissionHelp, FesMenu and other modals are not on the whitelist.
        for group in ("MissionHelp", "FesMenu", "MatchSkip", "Announcement"):
            with self.subTest(group=group):
                self.assertIn("single_whitelisted_popup_complete", self.failing(self.decide(hits=frame("Home", group))))
        # Reward is whitelisted but its sample is drawn over Story, not Home: underlying page must be present.
        self.assertIn("pre_group_present_beneath", self.failing(self.decide(hits=frame("Reward", present=("Story",)))))
        self.assertTrue(self.decide(hits=frame("Reward", "Home"))["interrupted"])

    def test_foreign_page_refuses(self):
        self.assertIn("no_foreign_group", self.failing(self.decide(hits=frame("Home", "MissionCompleted", present=("FesMenu",)))))
        self.assertIn("no_foreign_group", self.failing(self.decide(hits=frame("Home", "MissionCompleted", present=("Loading",)))))

    def test_panel_geometry(self):
        self.assertEqual(set(interrupt.PANEL_RECTS), set(interrupt.INTERRUPT_POPUPS))
        self.assertTrue(interrupt.outside_panels((62, 282), "MissionCompleted"))
        for inside in ((346, 69), (933, 643), (340, 300), (938, 300), (640, 649)):
            with self.subTest(point=inside):
                self.assertFalse(interrupt.outside_panels(inside, "MissionCompleted"))
        self.assertTrue(interrupt.outside_panels((640, 652), "MissionCompleted"))
        self.assertFalse(interrupt.outside_panels((62, 282), "Story"))
        self.assertFalse(interrupt.outside_panels(None, "MissionCompleted"))
        # Every interruptible action's fixed point clears MissionCompleted and Reward.
        for action in interrupt.INTERRUPTIBLE_ACTIONS:
            for popup in ("MissionCompleted", "Reward"):
                self.assertTrue(interrupt.outside_panels(point(action), popup), (action, popup))
            # None of these actions expects the popup itself or Home as its post page.
            self.assertTrue(action == "home-shop" or not set(POST_GROUPS[action]) & {"Home", *interrupt.INTERRUPT_POPUPS})


class StableGuardTests(unittest.TestCase):
    def test_interval_and_identity(self):
        self.assertEqual(interrupt.STABLE_GUARD_INTERVAL_S, 1.0)
        sig = interrupt.recognition_signature(frame("Home"), interrupt.home_signature_names())
        good = {"passed": True, "signature": sig}
        self.assertTrue(interrupt.stable_pair(good, dict(good), 1.0))
        self.assertFalse(interrupt.stable_pair(good, dict(good), .99))
        self.assertFalse(interrupt.stable_pair(good, {**good, "passed": False}, 2))
        self.assertFalse(interrupt.stable_pair({"passed": True, "signature": None}, {"passed": True, "signature": None}, 2))
        self.assertFalse(interrupt.stable_pair(good, dict(good), float("nan")))
        # A near-match overlay fails that frame's own guard (0.80 exclusion), so the pair is refused.
        popup = interrupt.recognition_signature(frame("Home", present=("MissionCompleted",)), interrupt.home_signature_names())
        self.assertFalse(interrupt.stable_pair(good, {"passed": False, "signature": popup}, 2))
        matched = interrupt.recognition_signature(frame("Home", "MissionCompleted"), interrupt.home_signature_names())
        self.assertFalse(interrupt.stable_pair(good, {"passed": True, "signature": matched}, 2))

    def test_signature_ignores_score_bands_but_not_matches(self):
        names = interrupt.home_signature_names()
        a = frame("Home")
        b = [dict(r, score=r["score"] + .001) for r in a]
        self.assertEqual(interrupt.recognition_signature(a, names), interrupt.recognition_signature(b, names))
        c = [dict(r, score=.81) if r["scene"] == GROUPS["Pickup"][0] else r for r in a]
        self.assertEqual(interrupt.recognition_signature(a, names), interrupt.recognition_signature(c, names))
        d = [dict(r, hit=True, score=.99) if r["scene"] == GROUPS["Pickup"][0] else r for r in a]
        self.assertNotEqual(interrupt.recognition_signature(a, names), interrupt.recognition_signature(d, names))

    def test_navigation_guard_signature_includes_foreground(self):
        hits = frame("Home")
        self.assertNotEqual(nav_runner.guard_signature(hits, CLEAR), nav_runner.guard_signature(hits, {**CLEAR, "state": "partial"}))

    def test_shop_home_overlay_exclusion(self):
        self.assertTrue(interrupt.home_overlays_clear(frame("Home")))
        self.assertFalse(interrupt.home_overlays_clear(frame("Home", "MissionCompleted")))
        self.assertFalse(interrupt.home_overlays_clear(frame("Home", present=("Pickup",))))
        self.assertFalse(interrupt.home_overlays_clear([]))


class InterruptedProofTests(unittest.TestCase):
    def setUp(self):
        self.post = {"image": str(protocol.PRIVATE / "x/frame-024.png"), "sha256": POST}
        result = interrupt.decide_interrupted("home-shop", (62, 282), frame("Home", "MissionCompleted"), CLEAR, PRE, POST, 7, 7, False)
        self.interruption = result["interruption"]

    def proof(self, action="home-shop", interruption=None):
        return protocol.interrupted_proof(action, "d" * 32, 7, PRE, self.post, "1" * 64, "2" * 64, [], CLEAR, "3" * 64,
                                          self.interruption if interruption is None else interruption)

    def test_shape_matches_host_whitelist(self):
        proof = self.proof()
        self.assertEqual(proof["kind"], "SC.RepeatableNavigation.Interrupted")
        self.assertIs(proof["passed"], False)
        self.assertIs(proof["resource_consumption"], False)
        self.assertEqual(set(proof) - {"interruption"}, set(protocol.postcondition_proof(
            "home-shop", "d" * 32, 7, PRE, self.post, "1" * 64, "2" * 64, [], CLEAR, "3" * 64)))
        self.assertEqual(set(proof["interruption"]), protocol.INTERRUPTION_FIELDS)
        self.assertEqual(Path(proof["producer_path"]), protocol.SHOP_PRODUCER.resolve())
        self.assertEqual(Path(self.proof("home-mission", {**self.interruption, "click_point": [62, 429]})["producer_path"]),
                         protocol.PRODUCER.resolve())

    def test_refusals(self):
        for change in ({"popup": "MissionHelp"}, {"pre_group": "Mission"}, {"underlying_group": "Story"},
                       {"click_point": [640, 360]}, {"panel_rects": [[0, 0, 1, 1]]}, {"panel_margin": 0},
                       {"expected_post_complete": True}, {"popup_complete": False}, {"post_differs": False},
                       {"extra": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.proof(interruption={**self.interruption, **change})
        with self.assertRaises(ValueError):
            self.proof("mission-back")
        with self.assertRaises(ValueError):  # Post frame must differ from pre frame.
            protocol.interrupted_proof("home-shop", "d" * 32, 7, POST, self.post, "1" * 64, "2" * 64, [], CLEAR, "3" * 64,
                                       self.interruption)

    def test_strict_postcondition_unchanged_and_interrupted_state_allows_next_attempt(self):
        proof = protocol.postcondition_proof("home-shop", "d" * 32, 7, PRE, self.post, "1" * 64, "2" * 64, [], CLEAR, "3" * 64)
        self.assertEqual((proof["kind"], proof["passed"]), ("SC.RepeatableNavigation.Postcondition", True))
        self.assertNotIn("interruption", proof)
        base = {"ok": True, "attempted": False, "reason": "readonly_ledger_status", "state": "interrupted",
                "action_count": 1, "day_count": 8}
        self.assertTrue(protocol.status_allows_attempt(base))
        self.assertFalse(protocol.status_allows_attempt({**base, "state": "pending"}))
        self.assertFalse(protocol.status_allows_attempt({**base, "action_count": 3, "day_count": 9}))


class RunnerConfirmationTests(unittest.TestCase):
    def test_navigation_confirmation_requires_interrupted_state(self):
        with tempfile.TemporaryDirectory(dir=protocol.PRIVATE) as directory:
            base = Path(directory)
            image = base / "frame-002.png"
            image.write_bytes(b"post")
            entry = {"image": str(image), "sha256": hashlib.sha256(b"post").hexdigest()}
            args = types.SimpleNamespace(report=base / "report.json", action="home-mission")
            bridge = types.SimpleNamespace(session={"pid": 7}, status=lambda: {})
            producer = hashlib.sha256(Path(nav_runner.__file__).read_bytes()).hexdigest()
            interruption = interrupt.decide_interrupted("home-mission", (62, 429), frame("Home", "MissionCompleted"), CLEAR,
                                                        "c" * 64, entry["sha256"], 7, 7, False)["interruption"]
            report = {"nonce": "d" * 32, "pre_frame_sha256": "c" * 64, "response": {"ok": True, "attempted": True, "state": "pending"}}
            reply = {"ok": True, "attempted": False, "reason": "repeatable_interruption_recorded", "state": "interrupted",
                     "action_count": 1, "day_count": 1}
            with patch.object(nav_runner.repeatable_protocol, "confirm_request", side_effect=lambda *a: {}):
                nav_runner.confirm_repeatable(report, args, {}, bridge, entry, [], CLEAR, "1" * 64, "2" * 64, producer,
                                              lambda *_: reply, interruption)
                written = json.loads((base / "report-postcondition.json").read_text(encoding="utf-8"))
                self.assertEqual((written["kind"], written["passed"]), ("SC.RepeatableNavigation.Interrupted", False))
                (base / "report-postcondition.json").unlink()
                # A host that answers "confirmed" (or an old host without the kind) leaves the run unregistered.
                for answer in ({**reply, "state": "confirmed"}, {**reply, "ok": False, "state": "pending"}):
                    with self.subTest(answer=answer["state"]), self.assertRaises(ValueError):
                        nav_runner.confirm_repeatable(report, args, {}, bridge, entry, [], CLEAR, "1" * 64, "2" * 64, producer,
                                                      lambda *_: answer, interruption)
                    (base / "report-postcondition.json").unlink()
                with self.assertRaises(ValueError):  # Strict postcondition path still demands "confirmed".
                    nav_runner.confirm_repeatable(report, args, {}, bridge, entry, [], CLEAR, "1" * 64, "2" * 64, producer,
                                                  lambda *_: reply)

    def test_navigation_interruption_check_uses_expected_post_groups(self):
        hits = frame("Home", "MissionCompleted")
        self.assertTrue(nav_runner.interruption_check("home-mission", hits, CLEAR, PRE, POST, 7, 7)["interrupted"])
        reached = frame("Home", "MissionCompleted", "Mission")
        self.assertFalse(nav_runner.interruption_check("home-mission", reached, CLEAR, PRE, POST, 7, 7)["interrupted"])


class ShopResolveTests(unittest.TestCase):
    def setUp(self):
        self.prior = {"runner": "SC.DailyShop", "action": "home-shop", "input_enabled": True, "input_attempts": 1,
                      "passed": False, "reason": "postcondition_unverified_no_retry", "initial_repeatable_status": {},
                      "session_pid": 3604, "nonce": "5" * 32, "pre_frame_sha256": "2" * 64,
                      "request_binding": {"action": "home-shop", "nonce": "5" * 32, "frame_sha256": "2" * 64},
                      "response": {"ok": True, "attempted": True, "state": "pending", "reason": "x", "action_count": 1, "day_count": 8}}

    def test_accepts_sent_unverified_repeatable_report(self):
        result = shop_runner.validate_pending_report(self.prior, "home-shop", 3604)
        self.assertEqual((result["nonce"], result["pre_frame_sha256"]), ("5" * 32, "2" * 64))

    def test_refuses_other_reports(self):
        for key, value in (("action", "shop-offer-open-1"), ("session_pid", 1), ("input_attempts", 0), ("passed", True),
                           ("reason", "stage_postcondition_verified"), ("runner", "SC.Other"), ("nonce", "x"),
                           ("request_binding", {"nonce": "6" * 32, "frame_sha256": "2" * 64}),
                           ("response", {"ok": True, "attempted": True, "state": "confirmed"})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                shop_runner.validate_pending_report({**self.prior, key: value}, "home-shop", 3604)
        no_repeatable = dict(self.prior)
        del no_repeatable["initial_repeatable_status"]
        with self.assertRaises(ValueError):
            shop_runner.validate_pending_report(no_repeatable, "home-shop", 3604)
        with self.assertRaises(ValueError):
            shop_runner.validate_pending_report({**self.prior, "action": "shop-exchange-confirm-1"}, "shop-exchange-confirm-1", 3604)

    def test_incident_report_is_accepted_when_present(self):
        path = ROOT / "dev_assets/private/shop-20261002-10-home.json"
        if not path.is_file():
            self.skipTest("私有事故报告不存在")
        prior = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(shop_runner.validate_pending_report(prior, "home-shop", prior["session_pid"])["nonce"], prior["nonce"])

    def test_resolve_cli_refusals(self):
        import contextlib
        import io
        base = ["--foreground-manifest", "x.json", "--foreground-contracts", "y.json", "--session", "s.json",
                "--report", str(ROOT / "dev_assets/private/shop-test-never-written.json")]
        pending = str(ROOT / "dev_assets/private/x.json")
        for extra in (["--stage", "Home", "--action", "home-shop", "--resolve-pending-report", pending],  # no --repeatable
                      ["--stage", "Home", "--action", "home-shop", "--repeatable", "--execute", "--resolve-pending-report", pending],
                      ["--stage", "List", "--action", "home-shop", "--repeatable", "--resolve-pending-report", pending],
                      ["--stage", "List", "--action", "shop-offer-open", "--offer-rank", "1", "--repeatable", "--resolve-pending-report", pending],
                      ["--stage", "Home", "--action", "home-shop", "--repeatable", "--resolve-pending-report", "README.md"],
                      ["--stage", "Home", "--action", "home-shop", "--repeatable"]):  # repeatable without execute or resolve
            with self.subTest(extra=extra), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                shop_runner.main(base + extra)
        self.assertFalse((ROOT / "dev_assets/private/shop-test-never-written.json").exists())


try:
    from tools.replay_screenshots import MaaRecognizer  # noqa: F401
    HAVE_MAA = True
except Exception:  # pragma: no cover
    HAVE_MAA = False

NAV_PROFILE = ROOT / "dev_assets/private/in_app_browser/20261001-daily-research/nav-native-v27"
FOREGROUND = ROOT / "dev_assets/private/in_app_browser/20260930-session01/replay"
INCIDENT = ROOT / "dev_assets/private/shop-20261002-10-home-frames"


@unittest.skipUnless(HAVE_MAA and NAV_PROFILE.is_dir() and FOREGROUND.is_dir() and INCIDENT.is_dir(), "需要Maa与私有原生样本")
class NativeIncidentReplayTests(unittest.TestCase):
    """Offline replay of the 2026-10-02 incident frames; no host, no input."""

    @classmethod
    def setUpClass(cls):
        from agent.core.daily_navigation import load_contract
        from agent.core.foreground_guard import interpret_foreground
        from tools.replay_foreground import load_configuration
        from tools.replay_screenshots import MaaRecognizer, read_png
        data, _, _ = load_contract(NAV_PROFILE / "daily-navigation-manifest.json", NAV_PROFILE / "daily-navigation-sources.json")
        fore, rules, _, _ = load_configuration(FOREGROUND / "manifest.json", FOREGROUND / "contracts.json")
        reco = MaaRecognizer()

        def classify(path):
            image = read_png(path)
            hits = [reco.recognize(s, image, NAV_PROFILE) for s in data["scenes"]]
            state = interpret_foreground([reco.recognize(s, image, FOREGROUND) for s in fore["scenes"]], (1280, 720), rules)
            return hits, state, hashlib.sha256(path.read_bytes()).hexdigest()
        cls.classify = staticmethod(classify)
        cls.reco = reco

    def test_incident_pre_frame_refused_and_post_frame_interrupted(self):
        pre_hits, _, pre_sha = self.classify(INCIDENT / "frame-004.png")
        self.assertFalse(interrupt.home_overlays_clear(pre_hits))  # The new shop Home exclusion refuses the pre frame.
        hits, state, sha = self.classify(INCIDENT / "frame-024.png")
        result = interrupt.decide_interrupted("home-shop", (62, 282), hits, state, pre_sha, sha, 3604, 3604, False)
        self.assertTrue(result["interrupted"], result["checks"])
        self.assertEqual(result["popup"], "MissionCompleted")
        self.assertEqual(self.reco.input_attempts, 0)


if __name__ == "__main__":
    unittest.main()
