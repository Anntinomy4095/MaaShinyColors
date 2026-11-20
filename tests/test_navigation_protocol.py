import unittest

from tools import navigation_protocol as protocol


class NavigationProtocolTests(unittest.TestCase):
    def setUp(self):
        self.session = {"version": 1, "host": "127.0.0.1", "port": 1234, "token": "a" * 64,
                        "pid": 123, "input_enabled": True, "browser_demo_only": False,
                        "transport": protocol.TRANSPORT, "navigation": "announcement-back",
                        "budget_path": str(protocol.BUDGET_PATH)}
        self.status = {"ready": True, "browser_demo": False, "input_enabled": False,
                       "composition_game": True, "width": 1280, "height": 720,
                       "window_visible": True, "webview_visible": True, "minimized": False,
                       "source_capture_width": 1282, "source_capture_height": 721,
                       "frame_sha256": "b" * 64, "frame_age_ms": 25,
                       "supervised_navigation": "announcement-back"}

    def test_session_requires_same_pid_fixed_navigation_local_auth_and_shared_budget(self):
        self.assertEqual(protocol.validate_navigation_session(self.session, 123), self.session)
        for update in ({"pid": 124}, {"pid": True}, {"port": True}, {"host": "localhost"},
                       {"navigation": "mission-entry"}, {"input_enabled": False},
                       {"browser_demo_only": True}, {"token": "short"},
                       {"budget_path": str(protocol.BUDGET_PATH.with_name("new-budget.json"))},
                       {"coordinate": [57, 662]}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                protocol.validate_navigation_session({**self.session, **update}, 123)

    def test_startup_has_separate_persistent_budget_and_exact_navigation(self):
        startup = {**self.session, "navigation": "touch-to-start", "budget_path": str(protocol.STARTUP_BUDGET_PATH)}
        self.assertEqual(protocol.validate_navigation_session(startup, 123, "touch-to-start"), startup)
        self.assertEqual(protocol.NAVIGATION_POINTS["touch-to-start"], (640, 630))
        for session, navigation in ((startup, "announcement-back"),
                                    ({**startup, "budget_path": str(protocol.BUDGET_PATH)}, "touch-to-start"),
                                    ({**self.session, "budget_path": str(protocol.STARTUP_BUDGET_PATH)}, "announcement-back")):
            with self.assertRaises(ValueError):
                protocol.validate_navigation_session(session, 123, navigation)
        status = {**self.status, "supervised_navigation": "touch-to-start"}
        self.assertEqual(protocol.navigation_request(startup, status, "b" * 64)["navigation"], "touch-to-start")

    def test_request_has_no_arbitrary_coordinates_and_binds_guarded_png_and_dimensions(self):
        request = protocol.navigation_request(self.session, self.status, "b" * 64)
        self.assertEqual(set(request), {"token", "command", "navigation", "frame_sha256", "source_width", "source_height"})
        self.assertEqual(request["command"], "navigate")
        self.assertEqual(request["source_width"], 1282)
        for update in ({"frame_sha256": "c" * 64}, {"frame_age_ms": 5000.1},
                       {"frame_age_ms": float("nan")}, {"frame_age_ms": True},
                       {"source_capture_width": 1920}, {"source_capture_height": True},
                       {"composition_game": False}, {"browser_demo": True},
                       {"minimized": True}, {"window_visible": False}, {"input_enabled": True},
                       {"supervised_navigation": None}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                protocol.navigation_request(self.session, {**self.status, **update}, "b" * 64)

    def test_transport_success_never_claims_business_postcondition(self):
        response = {"ok": True, "attempted": True, "reason": "native_gesture_sent_postcondition_required"}
        self.assertEqual(protocol.validate_navigation_response(response), response)
        for update in ({"attempted": False}, {"ok": 1}, {"reason": None}, {"passed": True}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                protocol.validate_navigation_response({**response, **update})


class DailyNavigationProtocolTests(unittest.TestCase):
    def test_training_requires_separate_exact_scope_and_runtime_toggle(self):
        base = dict(self.session)
        expanded = {**base, 'actions':list(protocol.ALL_DAILY_ACTION_POINTS),
            'budget_paths': {a:str(protocol.DAILY_BUDGET_PATHS.get(a,protocol.ROOT/'dev_assets/private/daily_navigation'/f'{a}.json')) for a in protocol.ALL_DAILY_ACTION_POINTS}}
        self.assertEqual(protocol.validate_daily_session(expanded,base['pid']),expanded)
        with self.assertRaises(ValueError):
            protocol.daily_navigation_request(base,self.status,'b'*64,'training-confirm-one-bronze')
        with self.assertRaises(ValueError):
            protocol.daily_navigation_request(expanded,self.status,'b'*64,'training-confirm-one-bronze')
        status={**self.status,'supervised_training':True}
        self.assertEqual(protocol.daily_navigation_request(expanded,status,'b'*64,'training-confirm-one-bronze')['action'],'training-confirm-one-bronze')
    def test_training_portal_returns_are_fixed_and_behind_training_toggle(self):
        expanded = {**self.session, 'actions':list(protocol.ALL_DAILY_ACTION_POINTS),
            'budget_paths': {a:str(protocol.DAILY_BUDGET_PATHS.get(a,protocol.ROOT/'dev_assets/private/daily_navigation'/f'{a}.json')) for a in protocol.ALL_DAILY_ACTION_POINTS}}
        for action in ('training-list-back','idol-back'):
            with self.subTest(action=action):
                self.assertEqual(protocol.DAILY_TRAINING_ACTION_POINTS[action],(57,662))
                self.assertNotIn(action,protocol.DAILY_ACTION_POINTS)
                self.assertNotIn(action,protocol.DAILY_BUDGET_PATHS)
                with self.assertRaises(ValueError):
                    protocol.daily_navigation_request(self.session,{**self.status,'supervised_training':True},'b'*64,action)
                with self.assertRaises(ValueError):
                    protocol.daily_navigation_request(expanded,self.status,'b'*64,action)
                request=protocol.daily_navigation_request(expanded,{**self.status,'supervised_training':True},'b'*64,action)
                self.assertEqual(request['action'],action)
                self.assertNotIn('coordinates',request)
    def setUp(self):
        self.session = {"version": 1, "host": "127.0.0.1", "port": 1234, "token": "a" * 64,
                        "pid": 123, "input_enabled": True, "browser_demo_only": False,
                        "transport": protocol.DAILY_TRANSPORT, "actions": list(protocol.DAILY_ACTION_POINTS),
                        "budget_paths": {key: str(path) for key, path in protocol.DAILY_BUDGET_PATHS.items()}}
        self.status = {"ready": True, "browser_demo": False, "input_enabled": False,
                       "composition_game": True, "width": 1280, "height": 720,
                       "window_visible": True, "webview_visible": True, "minimized": False,
                       "source_capture_width": 1280, "source_capture_height": 720,
                       "frame_sha256": "b" * 64, "frame_age_ms": 25,
                       "supervised_daily": True, "supervised_navigation": None}

    def test_session_cannot_extend_actions_or_create_new_announcement_budget(self):
        self.assertEqual(protocol.validate_daily_session(self.session, 123), self.session)
        self.assertEqual(protocol.DAILY_BUDGET_PATHS["announcement-back"], protocol.BUDGET_PATH)
        self.assertEqual(len(set(protocol.DAILY_BUDGET_PATHS.values())), 18)
        for update in ({"pid": 124}, {"pid": True}, {"port": True}, {"host": "localhost"},
                       {"input_enabled": False}, {"browser_demo_only": True}, {"token": "short"},
                       {"transport": protocol.TRANSPORT}, {"actions": ["home-mission"]},
                       {"actions": self.session["actions"] + ["training-confirm"]},
                       {"budget_paths": {**self.session["budget_paths"], "announcement-back":
                                         str(protocol.BUDGET_PATH.with_name("fresh.json"))}},
                       {"budget_paths": {"home-mission": str(protocol.DAILY_BUDGET_PATHS["home-mission"])}},
                       {"coordinate": [57, 662]}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                protocol.validate_daily_session({**self.session, **update}, 123)

    def test_mission_complete_is_base_action_with_fixed_point_and_independent_budget(self):
        action = "mission-complete-to-mission"
        self.assertEqual(protocol.DAILY_ACTION_POINTS[action], (744, 580))
        self.assertNotIn(action, protocol.DAILY_TRAINING_ACTION_POINTS)
        expected_path = protocol.ROOT / "dev_assets/private/daily_navigation/mission-complete-to-mission.json"
        self.assertEqual(protocol.DAILY_BUDGET_PATHS[action], expected_path)
        status = {**self.status, "supervised_training": False}
        request = protocol.daily_navigation_request(self.session, status, "b" * 64, action, "c" * 32)
        self.assertEqual(request["action"], action)
        for actions in ([item for item in self.session["actions"] if item != action],
                        list(reversed(self.session["actions"])),
                        self.session["actions"] + [action]):
            with self.subTest(actions=actions), self.assertRaises(ValueError):
                protocol.validate_daily_session({**self.session, "actions": actions}, 123)
        for path in (protocol.DAILY_BUDGET_PATHS["home-mission"], protocol.BUDGET_PATH,
                     expected_path.with_name("mission-complete-new.json")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                protocol.validate_daily_session({**self.session, "budget_paths": {
                    **self.session["budget_paths"], action: str(path)}}, 123)

    def test_nonspend_navigation_has_exact_base_scope_and_separate_budgets(self):
        points = {"mission-help-close": (640, 603), "home-fes": (1190, 650),
                  "fes-match-entry": (1045, 543), "match-help-close": (640, 603),
                  "match-skip-open": (1190, 548), "match-skip-cancel": (462, 620),
                  "match-back": (57, 662), "training-back": (57, 662), "pickup-close": (1111, 68),
                  "story-skip-open": (1221, 56), "training-help-close": (640, 603),
                  "game-start": (640, 630)}
        self.assertEqual(self.session["actions"][-12:], list(points))
        status = {**self.status, "supervised_training": False}
        for action, point in points.items():
            with self.subTest(action=action):
                self.assertEqual(protocol.DAILY_ACTION_POINTS[action], point)
                self.assertNotIn(action, protocol.DAILY_TRAINING_ACTION_POINTS)
                self.assertEqual(protocol.DAILY_BUDGET_PATHS[action],
                                 protocol.ROOT / "dev_assets/private/daily_navigation" / (action + ".json"))
                self.assertEqual(protocol.daily_navigation_request(self.session, status, "b" * 64, action)["action"], action)
                old_scope = [item for item in self.session["actions"] if item != action]
                with self.assertRaises(ValueError):
                    protocol.validate_daily_session({**self.session, "actions": old_scope}, 123)
                with self.assertRaises(ValueError):
                    protocol.validate_daily_session({**self.session, "budget_paths": {
                        **self.session["budget_paths"], action: str(protocol.DAILY_BUDGET_PATHS["mission-back"])}}, 123)

    def test_each_fixed_action_binds_new_frame_and_nonce_without_coordinates(self):
        for action in protocol.DAILY_ACTION_POINTS:
            request = protocol.daily_navigation_request(self.session, self.status, "b" * 64, action, "c" * 32)
            self.assertEqual(set(request), {"token", "command", "action", "frame_sha256", "source_width", "source_height", "nonce"})
            self.assertEqual((request["command"], request["action"], request["nonce"]), ("daily-navigate", action, "c" * 32))
        first = protocol.daily_navigation_request(self.session, self.status, "b" * 64, "home-mission")
        second = protocol.daily_navigation_request(self.session, self.status, "b" * 64, "home-mission")
        self.assertNotEqual(first["nonce"], second["nonce"])
        for nonce in (True, "short", "A" * 32, "c" * 32 + "\n"):
            with self.assertRaises(ValueError):
                protocol.daily_navigation_request(self.session, self.status, "b" * 64, "home-mission", nonce)
        for action in ("training-confirm", "touch-to-start", "mission-entry", "match-skip-confirm", "story-skip-confirm", "pickup-details",
                       "match-skip-scroll-down", "match-skip-plus", "", None):
            with self.assertRaises(ValueError):
                protocol.daily_navigation_request(self.session, self.status, "b" * 64, action)

    def test_daily_endpoint_rejects_stale_hidden_mixed_or_wrong_geometry_state(self):
        for update in ({"frame_sha256": "c" * 64}, {"frame_age_ms": 5000.1},
                       {"frame_age_ms": -1}, {"frame_age_ms": float("nan")}, {"frame_age_ms": True},
                       {"source_capture_width": 1920}, {"source_capture_height": True},
                       {"composition_game": False}, {"browser_demo": True}, {"ready": False},
                       {"minimized": True}, {"window_visible": False}, {"webview_visible": False},
                       {"input_enabled": True}, {"supervised_daily": False},
                       {"supervised_navigation": "touch-to-start"}):
            for action in protocol.DAILY_ACTION_POINTS:
                with self.subTest(update=update, action=action), self.assertRaises(ValueError):
                    protocol.daily_navigation_request(self.session, {**self.status, **update}, "b" * 64, action)

    def scroll_session(self, training=False):
        points = (protocol.ALL_DAILY_ACTION_POINTS if training else protocol.DAILY_ACTION_POINTS) | protocol.DAILY_SCROLL_ACTION_POINTS
        return {**self.session, "actions": list(points), "budget_paths": {
            action: str(protocol.DAILY_SCROLL_BUDGET_PATHS.get(action, protocol.DAILY_BUDGET_PATHS.get(
                action, protocol.ROOT / "dev_assets/private/daily_navigation" / (action + ".json"))))
            for action in points}}

    def test_scroll_and_training_have_four_exact_independent_session_scopes(self):
        sessions = (self.session,
                    {**self.session, "actions": list(protocol.ALL_DAILY_ACTION_POINTS), "budget_paths": {
                        action: str(protocol.DAILY_BUDGET_PATHS.get(action, protocol.ROOT / "dev_assets/private/daily_navigation" / (action + ".json")))
                        for action in protocol.ALL_DAILY_ACTION_POINTS}},
                    self.scroll_session(), self.scroll_session(training=True))
        self.assertEqual([len(session["actions"]) for session in sessions], [18, 27, 24, 33])
        for session in sessions:
            with self.subTest(actions=session["actions"]):
                self.assertEqual(protocol.validate_daily_session(session, 123), session)
                for actions in (session["actions"] + [session["actions"][-1]], list(reversed(session["actions"])),
                                session["actions"][:-1]):
                    with self.assertRaises(ValueError):
                        protocol.validate_daily_session({**session, "actions": actions}, 123)
        combined = self.scroll_session(training=True)
        wrong_order = list(protocol.DAILY_ACTION_POINTS | protocol.DAILY_SCROLL_ACTION_POINTS | protocol.DAILY_TRAINING_ACTION_POINTS)
        with self.assertRaises(ValueError):
            protocol.validate_daily_session({**combined, "actions": wrong_order}, 123)
        for extra in ({"coordinates": [1000, 430]}, {"delta": -240}, {"scroll_enabled": True}):
            with self.assertRaises(ValueError):
                protocol.validate_daily_session({**combined, **extra}, 123)

    def test_scroll_is_fixed_transport_only_and_requires_its_runtime_toggle(self):
        points = {"mission-scroll-down": (1000, 430), "mission-scroll-up": (1000, 430),
                  "match-area-scroll-down": (260, 460), "match-area-scroll-up": (260, 460),
                  "match-skip-scroll-down": (1030, 410), "match-skip-scroll-up": (1030, 410)}
        self.assertEqual(protocol.DAILY_SCROLL_ACTION_POINTS, points)
        self.assertEqual(protocol.DAILY_SCROLL_MAX_ATTEMPTS, 12)
        self.assertTrue(set(points).isdisjoint(protocol.ALL_DAILY_ACTION_POINTS))
        self.assertEqual(protocol.ALL_DAILY_TRANSPORT_ACTION_POINTS, protocol.ALL_DAILY_ACTION_POINTS | points)
        session = self.scroll_session()
        status = {**self.status, "supervised_scroll": True, "supervised_training": False}
        for action in points:
            with self.subTest(action=action):
                self.assertEqual(protocol.DAILY_SCROLL_DELTAS[action], -240 if action.endswith("-down") else 240)
                for missing_status in (self.status, {**status, "supervised_scroll": False}, {**status, "supervised_scroll": 1}):
                    with self.assertRaises(ValueError):
                        protocol.daily_navigation_request(session, missing_status, "b" * 64, action)
                with self.assertRaises(ValueError):
                    protocol.daily_navigation_request(self.session, status, "b" * 64, action)
                request = protocol.daily_navigation_request(session, status, "b" * 64, action, "c" * 32)
                self.assertEqual(set(request), {"token", "command", "action", "frame_sha256", "source_width", "source_height", "nonce"})
                self.assertEqual(request["action"], action)
        with self.assertRaises(ValueError):
            protocol.daily_navigation_request(self.scroll_session(training=True), status, "b" * 64, "training-confirm-one-bronze")
        training_only_status = {**self.status, "supervised_training": True, "supervised_scroll": False}
        with self.assertRaises(ValueError):
            protocol.daily_navigation_request(self.scroll_session(training=True), training_only_status, "b" * 64, "mission-scroll-down")

    def test_scroll_budget_paths_are_separate_fixed_ledgers(self):
        session = self.scroll_session(training=True)
        self.assertEqual(len(set(session["budget_paths"].values())), len(session["actions"]))
        for action in protocol.DAILY_SCROLL_ACTION_POINTS:
            expected_path = protocol.ROOT / "dev_assets/private/readonly_scroll" / (action + ".json")
            self.assertEqual(protocol.DAILY_SCROLL_BUDGET_PATHS[action], expected_path)
            other_action = next(item for item in protocol.DAILY_SCROLL_ACTION_POINTS if item != action)
            for path in (protocol.DAILY_BUDGET_PATHS["home-mission"], protocol.BUDGET_PATH,
                         protocol.DAILY_SCROLL_BUDGET_PATHS[other_action], expected_path.with_name("fresh.json")):
                with self.subTest(action=action, path=path), self.assertRaises(ValueError):
                    protocol.validate_daily_session({**session, "budget_paths": {
                        **session["budget_paths"], action: str(path)}}, 123)

    def test_scroll_requires_the_same_fresh_visible_geometry_and_nonce_contract(self):
        session = self.scroll_session()
        status = {**self.status, "supervised_scroll": True}
        for action in protocol.DAILY_SCROLL_ACTION_POINTS:
            for update in ({"frame_sha256": "c" * 64}, {"frame_age_ms": 5000.1},
                           {"frame_age_ms": -1}, {"frame_age_ms": float("nan")}, {"frame_age_ms": True},
                           {"source_capture_width": 1920}, {"source_capture_height": True},
                           {"composition_game": False}, {"browser_demo": True}, {"ready": False},
                           {"minimized": True}, {"window_visible": False}, {"webview_visible": False},
                           {"input_enabled": True}, {"supervised_daily": False},
                           {"supervised_navigation": "touch-to-start"}):
                with self.subTest(action=action, update=update), self.assertRaises(ValueError):
                    protocol.daily_navigation_request(session, {**status, **update}, "b" * 64, action)
            for nonce in (True, "short", "A" * 32, "c" * 32 + "\n"):
                with self.subTest(action=action, nonce=nonce), self.assertRaises(ValueError):
                    protocol.daily_navigation_request(session, status, "b" * 64, action, nonce)


if __name__ == "__main__":
    unittest.main()
