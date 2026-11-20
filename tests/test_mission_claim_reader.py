import datetime as dt
from dataclasses import replace
import hashlib
import unittest
from unittest.mock import patch

import numpy as np

from agent.core import mission_claim_reader as reader
from agent.core import mission_claim as claim
from agent.core.frame_permit import FrameCapture


NOW = dt.datetime(2026, 10, 7, 23, 14, 20, tzinfo=dt.timezone.utc)
TIMER = [{"text": "あと20時間45分", "box": [27, 126, 156, 24]}]


def item(text, box):
    return {"text": text, "box": list(box), "score": 1.0}


def login_items(ty=250, *, state="claimable", quantity="x1"):
    progress = "0/1" if state == "incomplete" else "1/1"
    button = {"claimable": "受け取る", "claimed": "CLEAR!!", "incomplete": "詳細"}.get(state)
    result = [item(reader.LOGIN_TITLE, (389, ty, 248, 26)), item(progress, (606, ty + 60, 35, 27)),
              item(quantity, (353, ty + 68, 23, 19))]
    if button:
        result.append(item(button, (1064, ty + 17, 124, 33)))
    return result


class LoginReaderTests(unittest.TestCase):
    def setUp(self):
        self.template = np.random.default_rng(8).integers(0, 256, (66, 80, 3), dtype=np.uint8)
        # Synthetic pixels exercise pure row contracts; this override never grants runtime input.
        binding = patch.dict(reader.TEMPLATE_DIGESTS,
                             bronze_ticket=hashlib.sha256(self.template.tobytes()).hexdigest())
        binding.start()
        self.addCleanup(binding.stop)

    def image(self, ty=250, *, pink=True, tab="daily", reward=True):
        image = np.full((720, 1280, 3), 245, dtype=np.uint8)
        if tab:
            y = 92 if tab == "daily" else 177
            image[y:y + 32, 30:160] = (253, 106, 194)
        if pink:
            image[ty - 4:ty + 76, 1042:1210] = (253, 106, 194)
        if reward:
            image[ty:ty + 66, 283:363] = self.template
        return image

    def read(self, *, ty=250, state="claimable", **kwargs):
        args = dict(image_rgb=self.image(ty, pink=state == "claimable"), list_results=login_items(ty, state=state),
                    timer_results=TIMER, observed_at=NOW, ticket_template=self.template,
                    page_guard=True, overlay=False)
        args.update(kwargs)
        if "frame" not in kwargs:
            digest = hashlib.sha256(np.ascontiguousarray(args["image_rgb"][:, :, ::-1]).tobytes()).hexdigest()
            args["frame"] = FrameCapture("a" * 64, 10.0, (720, 1280, 3), digest, 1, 2, 3)
        return reader.read_login(**args)

    def test_complete_login_binds_stable_identity_reward_and_original_frame(self):
        reading = self.read()
        self.assertEqual((reading.state, reading.cycle_id, reading.reward, reading.target),
                         ("claimable", "daily:2026-10-08", ("bronze_ticket", 1), (1042, 246, 168, 80)))
        self.assertFalse(reading.to_dict()["input_authorized"])
        frame = FrameCapture("a" * 64, 10.0, (720, 1280, 3), reading.pixel_digest, 1, 2, 3)
        observation = reading.observation(frame)
        decision = claim.inspect_claim(observation, "daily.login", reading.cycle_id, frame=frame, now=10.0)
        self.assertEqual(decision.state, "candidate")
        self.assertFalse(decision.input_authorized)
        self.assertIsNone(reading.observation(replace(frame, pixel_digest="b" * 64)))
        self.assertIsNone(reading.observation(replace(frame, captured_at=11.0, capture_seq=2,
                                                    host_capture_seq=3, host_binding_seq=4)))
        self.assertIsNone(self.read(frame=None).observation(frame))
        # This reader never creates missing host sequences; the existing claim contract refuses them.
        unbound = replace(frame, host_capture_seq=None)
        unbound_reading = self.read(frame=unbound)
        decision = claim.inspect_claim(unbound_reading.observation(unbound), "daily.login", reading.cycle_id,
                                       frame=unbound, now=10.0)
        self.assertEqual(decision.reason, "claim_frame_invalid")

    def test_claimed_after_reorder_is_same_mission_without_click_target(self):
        reading = self.read(ty=213, state="claimed")
        self.assertEqual((reading.state, reading.reward, reading.target), ("claimed", reader.REWARD, None))
        frame = FrameCapture("a" * 64, 10.0, (720, 1280, 3), reading.pixel_digest, 1, 2, 3)
        decision = claim.inspect_claim(reading.observation(frame), "daily.login", reading.cycle_id,
                                       frame=frame, now=10.0)
        self.assertEqual(decision.state, "already_claimed")

    def test_bottom_claimed_requires_complete_key_fields(self):
        self.assertEqual(self.read(ty=493, state="claimed").state, "claimed")
        for field in ("1/1", "x1", "CLEAR!!", reader.LOGIN_TITLE):
            with self.subTest(field=field):
                items = login_items(493, state="claimed")
                items = [dict(i, box=[i["box"][0], 580, i["box"][2], 30])
                         if i["text"] == field else i for i in items]
                reading = self.read(ty=493, state="claimed", list_results=items)
                self.assertEqual(reading.state, "unknown")
                self.assertIsNone(reading.target)

    def test_row_deadline_overrides_stale_sidebar_only_for_login_row(self):
        now = dt.datetime(2026, 10, 7, 13, 36, 50, tzinfo=dt.timezone.utc)
        stale = [item("あと6時間27分", (27, 126, 156, 24))]
        deadline = item("あと6時間23分", (875, 313, 131, 21))
        reading = self.read(list_results=login_items() + [deadline], timer_results=stale, observed_at=now)
        self.assertEqual((reading.state, reading.cycle_id), ("claimable", "daily:2026-10-07"))
        for deadlines in ([dict(deadline, box=[875, 453, 131, 21])],
                          [dict(deadline, box=[875, 313, 170, 21])],
                          [dict(deadline, text="あと6時間27分")],
                          [deadline, deadline], [dict(deadline, text="あと不明")]):
            with self.subTest(deadlines=deadlines):
                self.assertEqual(self.read(list_results=login_items() + deadlines,
                                          timer_results=stale, observed_at=now).state, "unknown")
        # A known but contradictory row deadline cannot fall back to a fresh sidebar.
        self.assertEqual(self.read(list_results=login_items() + [deadline]).state, "unknown")
        self.assertEqual(self.read(list_results=login_items() + [deadline], timer_results=[],
                                   observed_at=now).state, "claimable")

    def test_row_deadline_retains_reset_margin(self):
        now = dt.datetime(2026, 10, 7, 19, 56, tzinfo=dt.timezone.utc)
        reading = self.read(list_results=login_items() + [item("あと4分", (875, 313, 131, 21))],
                            timer_results=[], observed_at=now)
        self.assertEqual(reading.reasons, ("daily_reset_too_close",))

    def test_clipped_row_and_unread_clear_are_unknown(self):
        for ty, state in ((525, "claimed"), (110, "claimable"), (250, "completed")):
            with self.subTest(ty=ty, state=state):
                reading = self.read(ty=ty, state=state)
                self.assertEqual(reading.state, "unknown")
                self.assertIsNone(reading.target)
                self.assertIsNone(reading.observation(None))

    def test_title_is_exact_unique_identity_and_not_reward_or_row_order(self):
        for results in ([], login_items() * 2,
                        [dict(i, text="マニーショップで買い物をしよう") if i["text"] == reader.LOGIN_TITLE else i
                         for i in login_items()],
                        [dict(i, text="ゲームにログインしよう！") if i["text"] == reader.LOGIN_TITLE else i
                         for i in login_items()]):
            with self.subTest(results=results):
                self.assertEqual(self.read(list_results=results).state, "unknown")
        shuffled = list(reversed(login_items()))
        self.assertEqual(self.read(list_results=shuffled).state, "claimable")

    def test_wrong_reward_missing_quantity_and_duplicate_quantity_stop(self):
        for args in ({"ticket_template": None}, {"image_rgb": self.image(reward=False)},
                     {"list_results": login_items(quantity="x2")},
                     {"list_results": login_items() + [item("x1", (353, 318, 23, 19))]}):
            with self.subTest(args=list(args)):
                reading = self.read(**args)
                self.assertIn("login_reward_unverified", reading.reasons)
                self.assertIsNone(reading.target)
        self.assertEqual(self.read(list_results=login_items(quantity="xl")).reward, reader.REWARD)

    def test_daily_tab_overlay_page_and_button_are_required(self):
        for args in ({"image_rgb": self.image(tab="weekly")}, {"image_rgb": self.image(tab=None)},
                     {"overlay": True}, {"page_guard": False}, {"image_rgb": self.image(pink=False)},
                     {"list_results": login_items() + [item("受け取る", (1064, 267, 124, 33))]}):
            with self.subTest(args=list(args)):
                self.assertEqual(self.read(**args).state, "unknown")

    def test_wrong_progress_cannot_become_login_claim(self):
        for text in ("1/2", "2/1", "0/1"):
            items = [dict(i, text=text) if i["text"] == "1/1" else i for i in login_items()]
            self.assertEqual(self.read(list_results=items).state, "unknown")
        self.assertEqual(self.read(state="incomplete").state, "incomplete")

    def test_two_mission_titles_cannot_share_the_same_button(self):
        reading = self.read(list_results=login_items() + [item("マニーショップで買い物をしよう", (389, 260, 320, 26))])
        self.assertIn("login_row_ambiguous", reading.reasons)
        self.assertIsNone(reading.target)

    def test_raw_claimable_row_cannot_bypass_unknown_cycle(self):
        reading = self.read(timer_results=[])
        self.assertEqual((reading.state, reading.row_state), ("unknown", "claimable"))
        frame = FrameCapture("a" * 64, 10.0, (720, 1280, 3), reading.pixel_digest, 1, 2, 3)
        self.assertIsNone(reading.observation(frame))


class DailyCycleTests(unittest.TestCase):
    def test_jst_midnight_does_not_change_cycle_but_five_does(self):
        for utc, timer, expected in (("2026-10-07T14:59:00+00:00", "あと5時間1分", "daily:2026-10-07"),
                                     ("2026-10-07T15:01:00+00:00", "あと4時間59分", "daily:2026-10-07"),
                                     ("2026-10-07T20:06:00+00:00", "あと23時間54分", "daily:2026-10-08")):
            with self.subTest(utc=utc):
                self.assertEqual(reader.check_daily_cycle([item(timer, (0, 0, 1, 1))], dt.datetime.fromisoformat(utc)),
                                 (expected, None))

    def test_clock_alone_wrong_timer_duplicate_and_near_reset_are_untrusted(self):
        for items, now in (([], NOW), (TIMER * 2, NOW), ([item("あと1時間1分", (0, 0, 1, 1))], NOW),
                           (TIMER, NOW.replace(tzinfo=None)),
                           ([item("あと4分", (0, 0, 1, 1))], dt.datetime(2026, 10, 7, 19, 56, tzinfo=dt.timezone.utc)),
                           ([item("あと99日99時間", (0, 0, 1, 1))], NOW)):
            with self.subTest(items=items, now=now):
                self.assertIsNone(reader.check_daily_cycle(items, now)[0])


if __name__ == "__main__":
    unittest.main()
