from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.check_project import inspect_project


class ProjectContractGuardTests(unittest.TestCase):
    def _project(self, nodes: dict[str, object]) -> Path:
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        (directory / "assets" / "resource" / "base" / "pipeline").mkdir(
            parents=True
        )
        (directory / "assets" / "interface.json").write_text(
            json.dumps(
                {
                    "interface_version": 2,
                    "agent": {"child_args": ["../agent/main.py"]},
                    "resource": [],
                    "task": [{"entry": "SC.Root"}],
                }
            ),
            encoding="utf-8",
        )
        (directory / "project_policy.json").write_text(
            json.dumps(
                {
                    "pipeline_prefix": "SC.",
                    "live_input_enabled": False,
                    "forbidden_actions": [],
                }
            ),
            encoding="utf-8",
        )
        (directory / "agent").mkdir()
        (directory / "agent" / "main.py").write_text("", encoding="utf-8")
        (directory / "assets" / "resource" / "base" / "pipeline" / "x.json").write_text(
            json.dumps(nodes), encoding="utf-8"
        )
        return directory

    def test_inspect_project_uses_supplied_root(self) -> None:
        root = self._project({"SC.Root": {"action": "DoNothing"}})
        result = inspect_project(root)
        self.assertEqual(result.errors, [], "\n".join(result.errors))
        self.assertEqual(result.file_count, 1)

    def test_only_explicitly_disabled_catalog_may_reserve_a_missing_node(self):
        root = self._project({"SC.Root": {"action": "DoNothing"}})
        directory = root / "assets" / "input"
        directory.mkdir()
        path = directory / "pipeline_actions.json"
        data = {"version": 2, "canvas": [1280, 720], "margin_percent": 20, "min_inner_px": 4,
                "families": ["test"], "actions": [{"id": "reserved", "family": "test", "consuming": False,
                    "enabled": False, "rect": [0, 0, 100, 100], "source": "fixture", "nodes": ["SC.Future.Click"]}]}
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(inspect_project(root).errors, [])
        data["actions"][0]["enabled"] = True
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertTrue(any("missing or non-Click" in e for e in inspect_project(root).errors))

    def test_disabled_catalog_cannot_bind_an_enabled_pipeline_node(self):
        from tools.check_project import _action_table_errors
        root = self._project({"SC.Root": {"action": "DoNothing"}})
        directory = root / "assets" / "input"
        directory.mkdir()
        data = {"version": 2, "canvas": [1280, 720], "margin_percent": 20, "min_inner_px": 4,
                "families": ["test"], "actions": [{"id": "reserved", "family": "test", "consuming": False,
                    "enabled": False, "rect": [0, 0, 100, 100], "source": "fixture", "nodes": ["SC.Future.Click"]}]}
        (directory / "pipeline_actions.json").write_text(json.dumps(data), encoding="utf-8")
        pipeline = root / "assets/resource/base/pipeline/x.json"
        node = {"action": "Click", "target": [0, 0, 100, 100], "enabled": True}
        self.assertTrue(any("has an enabled node" in e for e in _action_table_errors(root, {"SC.Future.Click": (pipeline, node)})))
        node["enabled"] = False
        self.assertEqual(_action_table_errors(root, {"SC.Future.Click": (pipeline, node)}), [])

    def test_currency_and_inventory_spending_cannot_be_enabled_in_policy(self):
        root = self._project({"SC.Root": {"action": "DoNothing"}})
        path = root / "project_policy.json"
        policy = json.loads(path.read_text(encoding="utf-8"))
        usage = {
            "premium_currency_allowed": False, "paid_items_allowed": False,
            "other_inventory_allowed": False,
            "stamina_items": {"enabled": False, "max_per_run": 0},
            "fes_tickets": {"enabled": False, "max_per_run": 0},
        }
        policy["resource_usage"] = usage
        for key in ("premium_currency_allowed", "paid_items_allowed", "other_inventory_allowed"):
            usage[key] = True
            path.write_text(json.dumps(policy), encoding="utf-8")
            self.assertTrue(any(key in error for error in inspect_project(root).errors))
            usage[key] = False

    def test_optional_items_require_explicit_bounded_opt_in(self):
        root = self._project({"SC.Root": {"action": "DoNothing"}})
        path = root / "project_policy.json"
        policy = json.loads(path.read_text(encoding="utf-8"))
        policy["resource_usage"] = {
            "premium_currency_allowed": False, "paid_items_allowed": False, "other_inventory_allowed": False,
            "stamina_items": {"enabled": False, "max_per_run": 0},
            "fes_tickets": {"enabled": False, "max_per_run": 0},
        }
        for setting in ({"enabled": True, "max_per_run": 0}, {"enabled": False, "max_per_run": 1},
                        {"enabled": True, "max_per_run": True}, {"enabled": 1, "max_per_run": 1}):
            policy["resource_usage"]["stamina_items"] = setting
            path.write_text(json.dumps(policy), encoding="utf-8")
            self.assertTrue(any("stamina_items" in error for error in inspect_project(root).errors))
        policy["resource_usage"]["stamina_items"] = {"enabled": True, "max_per_run": 1}
        path.write_text(json.dumps(policy), encoding="utf-8")
        self.assertEqual(inspect_project(root).errors, [])

    def test_missing_pipeline_reference_is_rejected(self) -> None:
        root = self._project(
            {"SC.Root": {"next": "SC.Missing", "action": "DoNothing"}}
        )
        result = inspect_project(root)
        self.assertTrue(any("missing next node" in error for error in result.errors))

    def test_special_jump_and_anchor_references_are_accepted(self) -> None:
        root = self._project(
            {
                "SC.Root": {
                    "next": ["[JumpBack]SC.Done", "[Anchor]Last"],
                    "on_error": {"name": "SC.Recover"},
                },
                "SC.Done": {"action": "DoNothing"},
                "SC.Recover": {"action": "StopTask"},
            }
        )
        result = inspect_project(root)
        self.assertEqual(result.errors, [], "\n".join(result.errors))

    def test_disabled_input_still_requires_guards_and_recovery(self) -> None:
        root = self._project(
            {
                "SC.Root": {
                    "enabled": False,
                    "action": "Click",
                    "recognition": "DirectHit",
                }
            }
        )
        result = inspect_project(root)
        self.assertTrue(
            any("positive screen-state guard" in error for error in result.errors)
        )
        self.assertTrue(
            any("post-action success guard" in error for error in result.errors)
        )
        self.assertTrue(
            any("error recovery path" in error for error in result.errors)
        )

    def test_v2_input_and_recognition_objects_are_checked(self) -> None:
        root = self._project(
            {
                "SC.Root": {
                    "enabled": False,
                    "action": {"type": "Click", "param": {}},
                    "recognition": {
                        "type": "ColorMatch",
                        "param": {"inverse": True},
                    },
                    "next": "SC.Done",
                    "on_error": "SC.Recover",
                },
                "SC.Done": {"recognition": "ColorMatch"},
                "SC.Recover": {"action": "StopTask"},
            }
        )
        result = inspect_project(root)
        self.assertTrue(
            any("positive screen-state guard" in error for error in result.errors)
        )

    def test_unknown_recognition_and_mixed_success_path_are_rejected(self) -> None:
        root = self._project(
            {
                "SC.Root": {
                    "enabled": False,
                    "action": {"type": "Click", "param": {}},
                    "recognition": "FutureRecognition",
                    "next": ["SC.Done", "SC.Unknown"],
                    "on_error": "SC.Recover",
                },
                "SC.Done": {"recognition": "ColorMatch"},
                "SC.Unknown": {"recognition": "DirectHit"},
                "SC.Recover": {"action": "StopTask"},
            }
        )
        result = inspect_project(root)
        self.assertTrue(
            any("positive screen-state guard" in error for error in result.errors)
        )
        self.assertTrue(
            any("no positive success guard" in error for error in result.errors)
        )

    def test_anchor_success_reference_is_not_static_success_evidence(self) -> None:
        root = self._project(
            {
                "SC.Root": {
                    "enabled": False,
                    "action": "Click",
                    "recognition": "ColorMatch",
                    "next": "[Anchor]LastSeen",
                    "on_error": "SC.Recover",
                },
                "SC.Recover": {"action": "StopTask"},
            }
        )
        result = inspect_project(root)
        self.assertTrue(
            any("post-action success guard" in error for error in result.errors)
        )


if __name__ == "__main__":
    unittest.main()
