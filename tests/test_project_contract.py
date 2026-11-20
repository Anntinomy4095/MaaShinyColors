from __future__ import annotations

import unittest

from tools.check_project import inspect_project


class ProjectContractTests(unittest.TestCase):
    def test_project_contract(self) -> None:
        result = inspect_project()
        self.assertEqual(result.errors, [], "\n".join(result.errors))
        self.assertGreaterEqual(result.node_count, 10)


if __name__ == "__main__":
    unittest.main()
