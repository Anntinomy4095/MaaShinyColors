from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from agent.core.true_end.models import Ownership
from agent.core.true_end.records import RunRecord, append_run_record

ROOT = Path(__file__).resolve().parents[1]


def record():
    stats = {"Vo": 500, "Da": 100, "Vi": 100, "Me": 300, "SP": 300, "fans": 500000}
    return {"run_id": "observed-fixture", "observed_at": "2026-10-05T00:00:00Z",
        "p_card": asdict(Ownership("p", 0, 1)), "active": [asdict(Ownership("s" + str(i), 4, 80)) for i in range(4)],
        "guest": asdict(Ownership("g", 4, 80)), "strategy": "VO_SAFE", "difficulty": "Middle",
        "season_stats": [stats] * 4, "final_stats": stats, "skill_plan": ["learned1"],
        "auditions": [{"audition_id": "final", "trend": ["Vo", "Da", "Vi"], "turns": 6, "success": True}],
        "data_snapshot_sha256": "a" * 64, "success": True}


class RecordsTests(unittest.TestCase):
    def test_observations_validate_and_append_once_privately(self):
        r = RunRecord.from_dict(record())
        private = ROOT / "dev_assets/private"
        private.mkdir(exist_ok=True, parents=True)
        with tempfile.TemporaryDirectory(dir=private, prefix="te-record-test-") as directory:
            path = Path(directory) / "runs.jsonl"
            append_run_record(r, path)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                append_run_record(r, path)
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)
        with self.assertRaisesRegex(ValueError, "private"):
            append_run_record(r, ROOT / "tests/should-not-write.jsonl")

    def test_uncertain_or_missing_observations_not_success(self):
        for change in ({"success": "uncertain"}, {"season_stats": []}, {"observed_at": "2026-10-05T00:00:00"},
                       {"data_snapshot_sha256": ""}, {"final_stats": {"Vo": float("nan")}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                RunRecord.from_dict(record() | change)


if __name__ == "__main__":
    unittest.main()
