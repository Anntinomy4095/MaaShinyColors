from __future__ import annotations

import unittest

from agent.core.runtime_state import RuntimeMode


class RuntimeModeTests(unittest.TestCase):
    def test_observe_mode_cannot_enable_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "observe"):
            RuntimeMode(mode="observe", allow_input=True).validate()

    def test_input_requires_confirmation(self) -> None:
        with self.assertRaisesRegex(ValueError, "confirmation"):
            RuntimeMode(
                mode="confirm_each_action",
                allow_input=True,
                require_confirmation=False,
            ).validate()

    def test_confirm_each_action_is_valid(self) -> None:
        mode = RuntimeMode(
            mode="confirm_each_action",
            allow_input=True,
            require_confirmation=True,
        ).validate()
        self.assertTrue(mode.allow_input)


if __name__ == "__main__":
    unittest.main()
