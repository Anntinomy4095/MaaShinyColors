from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.check_project import POLICY_PATH, load_jsonc


class PolicyGateTests(unittest.TestCase):
    def test_live_input_rests_only_on_recorded_user_risk_decision(self) -> None:
        # ADR-011 (user enabled 2026-10-02): user risk decision, never claimed as provider permission.
        policy = load_jsonc(POLICY_PATH)
        self.assertTrue(policy["live_input_requires_service_authorization"])
        self.assertIsNone(policy["service_authorization_reference"])
        if policy["live_input_enabled"]:
            self.assertTrue(str(policy["risk_acceptance_reference"]).startswith("docs/SAFETY.md#adr-011"))
        usage = policy["resource_usage"]
        self.assertFalse(usage["premium_currency_allowed"] or usage["paid_items_allowed"])

    def test_offline_click_is_exactly_allowlisted_and_disabled(self) -> None:
        policy = load_jsonc(POLICY_PATH)
        root = POLICY_PATH.parent
        pipeline = json.loads(
            (
                root
                / "assets"
                / "resource"
                / "offline"
                / "pipeline"
                / "offline_click.json"
            ).read_text(encoding="utf-8")
        )
        node_name = "SC.Offline.Click.ReadyGuard"

        self.assertEqual(
            policy["offline_harness"]["allowed_input_nodes"],
            [node_name],
        )
        self.assertFalse(pipeline[node_name]["enabled"])
        self.assertEqual(pipeline[node_name]["action"], "Click")

    def test_jsonc_loader_preserves_urls_inside_strings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.jsonc"
            path.write_text(
                '{"url":"https://enza.fun/menu/terms"} // comment',
                encoding="utf-8",
            )
            self.assertEqual(
                load_jsonc(path),
                {"url": "https://enza.fun/menu/terms"},
            )


if __name__ == "__main__":
    unittest.main()
