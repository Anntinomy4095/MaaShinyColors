"""Fixed bronze icon checks and explicitly synthetic login/receipt/CLEAR replay.

The bronze reference came from a Fes row. Neither it nor any generated frame here proves a
native daily-login bronze reward, receipt or live claim. Every replay controller is offline.
"""
import hashlib
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from agent.core import mission_claim_actions as actions, mission_claim_reader as reader
from tests import test_mission_claim_sender as sender
from tests.maa_probe import maa_ready, ocr_items, rgb
from tests.test_mission_claim_reader import NOW, TIMER, item, login_items


def list_image(template):
    image = np.full((720, 1280, 3), 245, dtype=np.uint8)
    image[92:124, 30:160] = (253, 106, 194)
    image[246:326, 1042:1210] = (253, 106, 194)
    image[250:316, 283:363] = template
    return image


def receipt_image(template):
    image = np.full((720, 1280, 3), 240, dtype=np.uint8)
    image[480:560, 557:723] = (253, 106, 194)
    image[334:400, 408:488] = template
    return np.ascontiguousarray(image[:, :, ::-1])


def receipt_items(quantity=1):
    return [item("報酬受け取り", (410, 150, 220, 30)),
            item("以下の報酬を受け取りました", (475, 239, 330, 30)),
            item(f"トレーニングチケット x{quantity}", (514, 350, 340, 28)),
            item("OK", (610, 510, 60, 30))]


def synthetic_bronze_receipt(path, quantity=1):
    """Synthetic popup using reviewed Fes bronze pixels, never native login evidence."""
    image = Image.open(sender.ROOT / sender.WRONG).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.rectangle((390, 305, 885, 440), fill=(240, 235, 242))
    image.paste(Image.fromarray(actions.load_ticket_template()), (408, 334))
    draw.text((514, 350), f"トレーニングチケット x{quantity}", fill=(97, 90, 101),
              font=ImageFont.truetype(str(sender.FONT), 23))
    image.save(path)
    return path


def synthetic_bronze_row(source, output):
    """Replace an existing login icon for a synthetic row/CLEAR wiring fixture only."""
    if str(source) not in (str(sender.ROOT / sender.INITIAL), str(sender.ROOT / sender.CLAIMED)):
        return source
    image = rgb(source)
    rows = reader.ms.parse_rows(ocr_items(source, reader.ROIS["list"]))
    row, = [r for r in rows if r["title"].replace(" ", "") == reader.LOGIN_TITLE]
    ty = row["title_y"]
    silver = actions.load_silver_template().astype(np.float32)
    region = image[ty - 24:ty + 85, 273:378].astype(np.float32)
    energy = float(np.square(silver).sum())
    matches = []
    for y in range(44):
        for x in range(26):
            patch = region[y:y + 66, x:x + 80]
            denominator = (float(np.square(patch).sum()) * energy) ** .5
            if denominator:
                matches.append((float(np.square(patch - silver).sum()) / denominator, y, x))
    error, y, x = min(matches)
    assert error <= reader.TEMPLATE_MAX_ERROR
    image[ty - 24 + y:ty - 24 + y + 66, 273 + x:273 + x + 80] = actions.load_ticket_template()
    Image.fromarray(image).save(output)
    return output


@unittest.skipUnless(actions.TEMPLATE_PATH.exists() and actions.SILVER_TEMPLATE_PATH.exists(),
                     "需要独立审查的铜券和银券私有图案")
class BronzeReferenceTests(unittest.TestCase):
    def setUp(self):
        self.bronze = actions.load_ticket_template()
        self.silver = actions.load_silver_template()

    def reading(self, template, **changes):
        args = dict(image_rgb=list_image(template), list_results=login_items(), timer_results=TIMER,
                    observed_at=NOW, ticket_template=self.bronze, page_guard=True, overlay=False)
        args.update(changes)
        return reader.read_login(**args)

    def test_default_runtime_and_verified_templates_bind_distinct_kinds(self):
        rt = actions.MissionRuntime()
        self.assertEqual((rt.expected_reward, rt.ticket_template_path, rt.journal_path),
                         ("bronze_ticket", actions.TEMPLATE_PATH, actions.JOURNAL_PATH))
        self.assertFalse(rt.input_enabled)
        self.assertEqual(hashlib.sha256(self.bronze.tobytes()).hexdigest(),
                         "3f25f2fc16440dc58f241ac521fa14177bea7e023ff3594099331b33e331c98b")
        self.assertTrue(reader.template_verified(self.bronze, "bronze_ticket"))
        self.assertTrue(reader.template_verified(self.silver, "silver_ticket"))
        self.assertFalse(reader.template_verified(self.silver, "bronze_ticket"))
        self.assertFalse(reader.template_verified(self.bronze, "silver_ticket"))
        for reward, path in (("bronze_ticket", actions.SILVER_TEMPLATE_PATH),
                             ("silver_ticket", actions.TEMPLATE_PATH)):
            with self.subTest(reward=reward), self.assertRaisesRegex(ValueError, "template_unverified"):
                actions.load_ticket_template(reward, path)
        with self.assertRaisesRegex(ValueError, "reward_unsupported"):
            actions.MissionRuntime(expected_reward="jewels")
        with self.assertRaisesRegex(ValueError, "template_mismatch"):
            actions.MissionRuntime(silver_template_path=actions.SILVER_TEMPLATE_PATH)

    def test_synthetic_bronze_row_requires_exact_kind_and_one(self):
        self.assertEqual(self.reading(self.bronze).reward, ("bronze_ticket", 1))
        for changes in ({"list_results": login_items(quantity="x2")},
                        {"ticket_template": self.silver}, {"expected_reward": "silver_ticket"},
                        {"ticket_template": None, "silver_template": self.silver}):
            with self.subTest(changes=list(changes)):
                self.assertEqual(self.reading(self.bronze, **changes).state, "unknown")
        self.assertEqual(self.reading(self.silver).state, "unknown")
        silver = self.reading(self.silver, ticket_template=self.silver, expected_reward="silver_ticket")
        self.assertEqual(silver.reward, ("silver_ticket", 1))

    def test_synthetic_receipt_cannot_relabel_silver_or_accept_two(self):
        self.assertTrue(actions.receipt_matches(receipt_image(self.bronze), receipt_items(), self.bronze))
        for image, items, template in ((receipt_image(self.silver), receipt_items(), self.bronze),
                                       (receipt_image(self.silver), receipt_items(), self.silver),
                                       (receipt_image(self.bronze), receipt_items(2), self.bronze)):
            with self.subTest(kind=hashlib.sha256(template.tobytes()).hexdigest(), items=items):
                self.assertFalse(actions.receipt_matches(image, items, template))
        self.assertTrue(actions.receipt_matches(receipt_image(self.silver), receipt_items(), self.silver,
                                               expected_reward="silver_ticket"))

    @unittest.skipUnless((sender.ROOT / sender.CURRENT).exists(), "需要铜券来源的原生Fes行")
    def test_native_fes_bronze_pattern_is_distinct_from_native_login_silver(self):
        image = rgb(sender.CURRENT)
        self.assertTrue(reader.ticket_matches(image, 389, self.bronze))
        self.assertFalse(reader.ticket_matches(image, 250, self.bronze))
        self.assertTrue(reader.ticket_matches(image, 250, self.silver))
        self.assertFalse(reader.ticket_matches(image, 389, self.silver))


@unittest.skipUnless(maa_ready(sender.HOME, sender.INITIAL, sender.CLAIMED, sender.WRONG)
                     and actions.TEMPLATE_PATH.exists() and actions.SILVER_TEMPLATE_PATH.exists()
                     and sender.FONT.exists(), "需要Maa、原生布局、私有固定模板与日文字体")
class SyntheticBronzeFlowTests(unittest.TestCase):
    assert_actions = sender.MissionSenderTests.assert_actions

    def replay(self, frames, **changes):
        args = dict(expected_reward="bronze_ticket", frame_transform=synthetic_bronze_row,
                    receipt_factory=synthetic_bronze_receipt)
        args.update(changes)
        return sender.MissionSenderTests.replay(self, frames, **args)

    def test_explicitly_synthetic_bronze_row_receipt_clear_complete_offline_flow(self):
        summary, events = self.replay([sender.INITIAL, "synthetic", sender.CLAIMED, sender.HOME])
        self.assertEqual(summary["tasks"][0]["status"], "succeeded", summary)
        self.assert_actions(summary, ["mission-login-claim", "mission-result-close", "nav-back"])
        self.assertEqual(events, ["pending", "claimed"])

    def test_native_silver_login_is_not_a_default_bronze_candidate(self):
        summary, events = self.replay([sender.INITIAL], frame_transform=None)
        self.assertEqual(summary["tasks"][0]["status"], "failed", summary)
        self.assert_actions(summary, [])
        self.assertEqual(events, [])
        # 2026-10-12: the stop names the observed kind and saves its frame as evidence.
        report = [r for r in summary["tasks"][0]["reports"] if r.get("kind") == "missions_claim"][-1]
        self.assertIn("login_reward_kind_mismatch:silver_ticket", report["reason"])
        self.assertEqual(report["reading"]["observed_reward"], "silver_ticket")
        self.assertTrue(report["samples"], report)  # saved under the replay's temporary private directory
        self.assertIn("missions-read-", Path(report["samples"][0]).name)

    def test_silver_receipt_after_synthetic_bronze_row_stops_without_retry(self):
        summary, events = self.replay([sender.INITIAL, "synthetic"],
                                      receipt_factory=sender.synthetic_receipt)
        self.assertEqual(summary["tasks"][0]["status"], "failed", summary)
        self.assert_actions(summary, ["mission-login-claim"])
        self.assertEqual(events, ["pending", "uncertain"])

    def test_bronze_two_receipt_after_one_row_stops_without_retry(self):
        summary, events = self.replay([sender.INITIAL, "synthetic"],
            receipt_factory=lambda path: synthetic_bronze_receipt(path, quantity=2))
        self.assertEqual(summary["tasks"][0]["status"], "failed", summary)
        self.assert_actions(summary, ["mission-login-claim"])
        self.assertEqual(events, ["pending", "uncertain"])


if __name__ == "__main__":
    unittest.main()
