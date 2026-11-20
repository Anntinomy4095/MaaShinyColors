"""Read-only Match fields on native frames; partial progress never becomes skip authority."""
import dataclasses
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from agent.core import match_reader as reader, match_task as mt, task_common as tc
from tests.maa_probe import NodeProbe, maa_ready

MAIN = "dev_assets/private/nav-v21-match-help-close-execute-frames/frame-023.png"
DIALOG = "dev_assets/private/match-skip-native-20261002-frames/frame-005.png"
HELP = "dev_assets/private/manual-capture-kept-20261002/match-help-1of17-20261001.png"
IMAGE = np.zeros((720, 1280, 3), np.uint8)


class MatchMainReaderTests(unittest.TestCase):
    def vision(self, rights="20/20", page=True, ranking="10/30 13:59まで", bad_row=None):
        def text(image, box):
            if box == reader.DEADLINE_BOX:
                return ranking
            row = (box[1] - (reader.AREA_HEADER[1] if box[0] == reader.AREA_HEADER[0]
                             else reader.AREA_PERCENT[1])) // reader.AREA_ROW_DY
            if row == bad_row:
                return None
            return f"エリア{row + 1}Normal" if box[0] == reader.AREA_HEADER[0] else "達成率100%"
        return SimpleNamespace(hit=lambda *a: page, number=lambda *a: rights, text=text)

    def test_ranking_deadline_has_no_year_and_keeps_raw_text(self):
        for text in ("10/30 13:59 まで", "１０／３０ １３：５９まで", "10/3013:59まで"):
            value = reader.parse_ranking_deadline(text)
            self.assertEqual((value.month, value.day, value.hour, value.minute), (10, 30, 13, 59))
            self.assertEqual(value.raw, text)
            self.assertNotIn("year", dataclasses.asdict(value))
        self.assertEqual(reader.parse_ranking_deadline("2/29 00:00まで").day, 29)

    def test_ambiguous_or_impossible_deadline_is_not_guessed(self):
        for text in (None, "2/3012:00まで", "2/30 12:00まで", "13/1 12:00まで", "10/30 24:00まで",
                     "10/30 13:60まで", "11/112:00まで", "2026/10/30 13:59まで", "あと18:28"):
            with self.subTest(text=text):
                self.assertIsNone(reader.parse_ranking_deadline(text))
        self.assertEqual(reader.parse_ranking_deadline("11/1 12:00まで").day, 1)

    def test_visible_100_percent_areas_do_not_prove_latest_or_full_progress(self):
        value = reader.read_main(self.vision(), IMAGE, 10.0)
        self.assertEqual(value.rights, (20, 20))
        self.assertEqual([(a.area_id, a.difficulty, a.achievement) for a in value.areas],
                         [(1, "Normal", 100), (2, "Normal", 100), (3, "Normal", 100)])
        report = value.to_dict()
        self.assertFalse(report["stage_list_complete"])
        self.assertIsNone(report["latest_stage"])
        self.assertFalse(report["input_authorized"])
        self.assertEqual((value.frame_digest, value.observed_at), (tc.image_digest(IMAGE), 10.0))
        json.dumps(report, allow_nan=False)

    def test_unknown_rows_and_ranking_do_not_erase_good_resource_reading(self):
        value = reader.read_main(self.vision(ranking=None, bad_row=1), IMAGE, 10.0)
        self.assertEqual(value.rights, (20, 20))
        self.assertIsNone(value.ranking_deadline)
        self.assertEqual((value.areas[1].area_id, value.areas[1].achievement), (None, None))

    def test_wrong_page_or_size_returns_no_observation(self):
        self.assertIsNone(reader.read_main(self.vision(page=False), IMAGE, 10.0))
        self.assertIsNone(reader.read_main(self.vision(), IMAGE[:700], 10.0))

    def test_unknown_capacity_and_invalid_reading_are_not_resource_evidence(self):
        for rights in ("20/99", "21/20", "unreadable"):
            self.assertIsNone(reader.read_main(self.vision(rights=rights), IMAGE, 10.0).rights)
        for stamp in (True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                reader.read_main(self.vision(), IMAGE, stamp)

    def test_runtime_records_fields_but_still_requires_full_progress(self):
        from agent.core.match_custom import MatchRuntime

        runtime = MatchRuntime(vision=lambda context: self.vision(), clock=lambda: 10.0,
                               authority=Mock(can_spend=lambda action: True))
        runtime.log = Mock()
        runtime.run = mt.MatchRun(mt.MatchConfig(count=1, sample_only=False), runtime.log)
        self.assertFalse(runtime._open_skip(None, IMAGE))
        self.assertEqual(runtime.run.outcome, "progress_evidence_missing")
        self.assertIsNone(runtime.run.intent)
        self.assertIsNone(runtime.progress_reader)
        observation = next(d["main_observation"] for d in runtime.run.decisions if "main_observation" in d)
        self.assertEqual(observation["ranking_deadline"]["day"], 30)
        self.assertFalse(observation["input_authorized"])


@unittest.skipUnless(maa_ready(MAIN, DIALOG, HELP), "需要 Maa、OCR 模型及 Match 原生帧")
class MatchMainNativeTests(unittest.TestCase):
    def observe(self, frame):
        from maa.custom_recognition import CustomRecognition
        from agent.core.task_custom import Vision

        values = []

        class Probe(CustomRecognition):
            def analyze(self, context, argv):
                values.append(reader.read_main(Vision(context), argv.image, 100.0))
                return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="read_only")

        resource = NodeProbe.resource()
        resource.register_custom_recognition("SC.Test.MatchMainRead", Probe())
        override = {"SC.Test.Start": {"next": ["SC.Test.MatchMainRead"]},
                    "SC.Test.MatchMainRead": {"recognition": "Custom",
                                               "custom_recognition": "SC.Test.MatchMainRead"}}
        NodeProbe().run(frame, override)
        self.assertEqual(len(values), 1)
        return values[0]

    def test_native_main_reads_deadline_rights_and_visible_areas(self):
        value = self.observe(MAIN)
        self.assertIsNotNone(value)
        self.assertEqual(value.rights, (1, 20))
        self.assertEqual((value.ranking_deadline.month, value.ranking_deadline.day,
                          value.ranking_deadline.hour, value.ranking_deadline.minute), (10, 30, 13, 59))
        self.assertEqual([a.area_id for a in value.areas], [1, 2, 3])
        self.assertFalse(value.to_dict()["stage_list_complete"])

    def test_native_skip_and_help_cannot_be_a_main_source(self):
        self.assertIsNone(self.observe(DIALOG))
        self.assertIsNone(self.observe(HELP))


if __name__ == "__main__":
    unittest.main()
