"""Auto 词条记录 (agent/core/knowhow_auto_log.py, tools/ui_engine.note_auto_start); synthetic summaries and books."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.core import knowhow_auto_log as kal
from tools import ui_engine


def summary(reason="started", scenario="LP"):
    report = {"kind": "weeklyauto", "reason": reason, "config": {"runs": 1, "expect_scenario": scenario, "unit": 20}}
    return {"tasks": [{"name": "Startup", "reports": []}, {"name": "WeeklyProduce", "reports": [report]}]}


TEAM = {"p_card": "1050180010", "idols": ["黛冬優子", "八宮めぐる"]}


class KnowhowAutoLogTests(unittest.TestCase):
    def test_start_entry_only_for_a_started_auto(self):
        row = kal.start_entry(summary(), target={"targets": ["思い出(ノウハウ)++"]}, team=TEAM, at="t1")
        self.assertEqual(row, {"kind": "start", "at": "t1", "scenario": "LP", "runs": 1, "unit": 20,
                               "p_card": "1050180010", "p_idol": "黛冬優子", "targets": ["思い出(ノウハウ)++"]})
        self.assertIsNone(kal.start_entry(summary("input_disabled:start"), at="t1"))
        self.assertIsNone(kal.start_entry({}, at="t1"))
        self.assertIsNone(kal.start_entry(summary(), at="t1")["p_idol"])        # 指定ユニット, P not read
        read = summary()
        read["tasks"][1]["reports"][0]["p_card"] = "1050180010"                 # read on the Auto unit page
        # the card -> idol lookup reads the local card library; stubbed so the test needs no private data
        with mock.patch("agent.core.wing_nav.card_idol_name", lambda card: {"1050180010": "黛冬優子"}.get(card)):
            self.assertEqual(kal.start_entry(read, at="t1")["p_idol"], "黛冬優子")

    def test_read_matches_wanted_and_the_p_idol(self):
        start = kal.start_entry(summary(), team=TEAM, at="t1")
        books = [{"title": "【ハウ・アー・ユー】黛冬優子", "rows": [{"name": "思い出(ノウハウ)++", "level": 2},
                                                                {"name": "スロースターター", "level": 5}]},
                 {"title": "【x】櫻木真乃", "rows": [{"name": "メンタリスト1", "level": 1}]}]
        wanted = [{"name": "思い出(ノウハウ)++"}, {"name": "メンタリスト", "match": "prefix"}, {"name": "一致団結"}]
        row = kal.read_entry(start, books, wanted=wanted, at="t2")
        self.assertEqual(row["start"], "t1")
        self.assertEqual([b["p_match"] for b in row["books"]], [True, False])
        self.assertEqual(row["books"][0]["rows"], [["思い出(ノウハウ)++", 2], ["スロースターター", 5]])
        self.assertEqual(row["wanted_found"], ["思い出(ノウハウ)++", "メンタリスト"])

    def test_log_pending_and_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            first = kal.start_entry(summary(), at="t1")
            second = kal.start_entry(summary(scenario="GRAD"), at="t2")
            kal.append(first, path)
            kal.append(second, path)
            self.assertEqual([r["at"] for r in kal.pending(kal.load(path))], ["t1", "t2"])
            kal.append(kal.read_entry(first, [{"title": "【a】黛冬優子", "rows": [{"name": "思い出(ノウハウ)++", "level": 1}]}],
                                      wanted=["思い出(ノウハウ)++"], at="t3"), path)
            with path.open("a", encoding="utf-8") as handle:
                handle.write("not json\n")
            rows = kal.load(path)
            self.assertEqual([r["at"] for r in kal.pending(rows)], ["t2"])
            self.assertEqual(kal.stats(rows), {"LP": {"runs": 1, "books": 1, "entries": {"思い出(ノウハウ)++": 1},
                                                      "wanted": {"思い出(ノウハウ)++": 1}}})

    def test_daily_run_logs_a_started_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, log = Path(tmp) / "summary.json", Path(tmp) / "log.jsonl"
            path.write_text(json.dumps(summary(), ensure_ascii=False), encoding="utf-8")
            row = ui_engine.note_auto_start(path, {"targets": ["思い出(ノウハウ)++"]}, TEAM, log_path=log)
            self.assertEqual((row["scenario"], row["p_idol"]), ("LP", "黛冬優子"))
            self.assertEqual(kal.load(log), [row])
            path.write_text(json.dumps(summary("input_disabled:start")), encoding="utf-8")
            self.assertIsNone(ui_engine.note_auto_start(path, None, None, log_path=log))
            self.assertIsNone(ui_engine.note_auto_start(Path(tmp) / "missing.json", None, None, log_path=log))
            self.assertEqual(len(kal.load(log)), 1)


if __name__ == "__main__":
    unittest.main()
