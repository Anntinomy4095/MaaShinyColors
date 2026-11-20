"""W.I.N.G. Schedule room preview check: preview reader, optimistic room bound and the bounded
non-consuming room taps in wing_nav. Synthetic readings only, zero input."""
from __future__ import annotations

import copy
import dataclasses
import unittest

from agent.core import produce_config as pc
from agent.core import wing_nav as nav
from agent.core import wing_score as sc
from agent.core import wing_screen as wsn
from agent.core import wing_state as ws

ROOMS = [
    {"id": "lesson_vo", "kind": "lesson", "attribute": "Vo", "label_ok": True, "level": 2, "supports": 0,
     "excellent": False},
    {"id": "lesson_da", "kind": "lesson", "attribute": "Da", "label_ok": True, "level": 2, "supports": 2,
     "excellent": True},
    {"id": "lesson_vi", "kind": "lesson", "attribute": "Vi", "label_ok": True, "level": 2, "supports": 0,
     "excellent": False},
    {"id": "work_radio", "kind": "work", "attribute": None, "label_ok": True, "level": 1, "supports": 0},
    {"id": "work_talk", "kind": "work", "attribute": None, "label_ok": True, "level": 1, "supports": 2},
    {"id": "work_magazine", "kind": "work", "attribute": None, "label_ok": True, "level": 1, "supports": 1},
]
WEEK = {"season": 1, "weeks_left": 8, "stats": {"Vo": 120, "Da": 60, "Vi": 60, "Me": 80},
        "stat_caps": {"Vo": 500, "Da": 500, "Vi": 500, "Me": 500}, "fans": 500, "sp": 20, "stamina_pct": 90,
        "trouble_rate": 0}


def frame(selected=None, preview=None, **over):
    """Schedule readings as wing_screen.read_screen returns them (preview attached to the selected room)."""
    rooms = copy.deepcopy(ROOMS)
    for room in rooms:
        room["selected"] = room["id"] == selected
        if room["id"] == selected and preview:
            room["preview"] = dict(preview["preview"])
            for key in ("sp_gain", "fan_gain"):
                if preview.get(key) is not None:
                    room[key] = preview[key]
    return {**WEEK, "rooms": rooms, "selected_room": selected, "selected_preview": preview, **over}


def runtime() -> nav.WingNavRuntime:
    rt = nav.WingNavRuntime(nav.RunMemory.from_produce_config(
        pc.ProduceConfig.from_dict(pc.default_config("true_end"))))
    rt.begin()
    return rt


def low(stats, sp=4):
    return {"preview": stats, "sp_gain": sp, "fan_gain": None}


class PreviewReaderTests(unittest.TestCase):
    def test_columns_from_native_row(self):
        # demo1 #326 (Da lesson selected): +28 Da, +20 Me, +58 SP, +32 fans; status cells below are ignored
        texts = [("+28", (556, 341, 54, 29)), ("E/125", (560, 390, 86, 24)), ("+20", (797, 341, 55, 29)),
                 ("+58", (918, 341, 55, 29)), ("+32", (1064, 341, 54, 29))]
        self.assertEqual(wsn.parse_preview(texts), {"preview": {"Da": 28, "Me": 20}, "sp_gain": 58, "fan_gain": 32})
        # narrow numbers are centred in their column (#259 「+5」 SP, #270 「+7」 Da)
        self.assertEqual(wsn.parse_preview([("+7", (564, 338, 36, 28)), ("+5", (926, 344, 38, 26))]),
                         {"preview": {"Da": 7}, "sp_gain": 5, "fan_gain": None})
        self.assertEqual(wsn.parse_preview([("+1,248", (1057, 341, 68, 29))])["fan_gain"], 1248)

    def test_ambiguous_rows_are_not_guessed(self):
        self.assertIsNone(wsn.parse_preview([]))
        self.assertIsNone(wsn.parse_preview([("+12", (500, 341, 50, 29))]))          # between Vo and Da
        self.assertIsNone(wsn.parse_preview([("+12", (434, 341, 55, 29)), ("+13", (440, 341, 50, 29))]))
        self.assertIsNone(wsn.parse_preview([("+???", (434, 341, 55, 29))]))


class UpperBoundTests(unittest.TestCase):
    def test_upper_covers_supports_and_keeps_read_previews(self):
        p = sc.ScoreParams()
        empty = ws.ScheduleOption("lesson_vo", "lesson", "Vo", level=3, supports=0, excellent_present=False)
        full = dataclasses.replace(empty, supports=2, excellent_present=True)
        lower = sc.lesson_gain(sc.LESSON_BASE[3][0], week_index=10)
        e_gain, e_sp = sc.room_upper_gains(empty, 10, p)
        f_gain, f_sp = sc.room_upper_gains(full, 10, p)
        self.assertGreaterEqual(e_gain["Vo"], lower)
        self.assertGreater(f_gain["Vo"], e_gain["Vo"] * 1.5)
        self.assertEqual((e_sp, f_sp), (5, 5 + 2 * p.preview_upper_sp_per_support))
        unread = dataclasses.replace(empty, supports=None, excellent_present=None)
        self.assertEqual(sc.room_upper_gains(unread, 10, p)[1], 5 + 3 * p.preview_upper_sp_per_support)
        self.assertIsNone(sc.room_upper_gains(dataclasses.replace(full, preview={"Vo": 40}), 10, p))
        self.assertIsNone(sc.room_upper_gains(dataclasses.replace(full, level=None), 10, p))
        talk = ws.ScheduleOption("work_talk", "work", None, "トーク", level=1, supports=2)
        gains, sp = sc.room_upper_gains(talk, 10, p)
        self.assertTrue(gains["Da"] >= 4 and gains["Me"] >= 4 and sp == 15 + 40)

    def test_bounds_are_opt_in_and_name_rooms_worth_checking(self):
        state, _ = nav.build_week_state(frame(), nav.RunMemory())
        plain = sc.score_week(state, "Vo")
        self.assertNotIn("room_bounds", plain.context)
        bounded = sc.score_week(state, "Vo", preview_bounds=True)
        self.assertEqual(plain.choice.id, bounded.choice.id)
        bounds = bounded.context["room_bounds"]
        self.assertEqual(set(bounds), {r["id"] for r in ROOMS})
        self.assertTrue(all(b["source"] == "estimate" and b["upper"] >= b["estimate"] for b in bounds.values()))
        # no audition list here: the talk room (supports) is the estimate's choice, never its own check;
        # the 2-support Da lesson could win once its SP mastery shows, the empty Vi lesson never does
        self.assertEqual(bounded.choice.id, "work_talk")
        self.assertNotIn("work_talk", bounded.context["preview_check"])
        self.assertIn("lesson_da", bounded.context["preview_check"])
        self.assertNotIn("lesson_vi", bounded.context["preview_check"])
        supports = {r["id"]: r["supports"] for r in ROOMS}
        order = bounded.context["preview_check"]
        self.assertEqual([supports[i] > 0 for i in order], sorted((supports[i] > 0 for i in order), reverse=True))
        # primary capped (live 2026-10-10, Vo 536 = cap): the Vo lesson's optimistic gain overflows -> never checked
        capped, _ = nav.build_week_state(frame(stats={**WEEK["stats"], "Vo": 500}), nav.RunMemory())
        capped_check = sc.score_week(capped, "Vo", preview_bounds=True).context["preview_check"]
        self.assertNotIn("lesson_vo", capped_check)
        # a read preview is certain: no optimistic world for it
        read, _ = nav.build_week_state(frame("work_talk", low({"Da": 4, "Me": 4})), nav.RunMemory())
        again = sc.score_week(read, "Vo", preview_bounds=True)
        self.assertEqual(again.context["room_bounds"]["work_talk"]["source"], "preview")
        self.assertNotIn("work_talk", again.context["preview_check"])


class NavPreviewCheckTests(unittest.TestCase):
    def test_taps_uncertain_rooms_once_each_and_caps_per_week(self):
        rt = runtime()
        first = rt.decide("Schedule", frame())
        # 2026-10-15: room fans cover this week's deficit (passive_cover 2.4), so the audition no longer leads and
        # the first uncertain room checked is lesson_da (was work_talk behind the audition)
        self.assertEqual((first.action, first.reason, first.detail["room"]), ("click", "preview_check", "lesson_da"))
        self.assertEqual(first.intent, "schedule_room_2")
        taps = [first.detail["room"]]
        selected = first.detail["room"]
        for _ in range(6):
            d = rt.decide("Schedule", frame(selected, low({"Da": 4, "Me": 4} if selected.startswith("work")
                                                           else {selected[-2:].capitalize(): 20})))
            if d.reason != "preview_check":
                break
            taps.append(d.detail["room"])
            selected = d.detail["room"]
        self.assertLessEqual(len(taps), nav.PREVIEW_CHECK_MAX)
        self.assertEqual(len(taps), len(set(taps)))
        self.assertNotEqual(d.reason, "preview_check")
        mem = rt.memory.room_previews
        self.assertEqual(mem["key"], [1, 8])
        self.assertTrue(set(taps) <= set(mem["previews"]))
        # remembered previews are reused for rooms that are no longer selected
        bounds = rt.memory.last_week_decision["plan"]["context"]["room_bounds"]
        self.assertTrue(all(bounds[i]["source"] == "preview" for i in taps))
        # same week: no further taps; new week: memory resets
        self.assertNotEqual(rt.decide("Schedule", frame(selected)).reason, "preview_check")
        rt.memory.pending = None
        nxt = rt.decide("Schedule", frame(weeks_left=7))
        self.assertEqual(rt.memory.room_previews["key"], [1, 7])
        self.assertEqual(nxt.reason, "preview_check")
        self.assertEqual(rt.memory.room_previews["checked"], [nxt.detail["room"]])

    def test_a_strong_real_preview_is_chosen_then_confirmed(self):
        rt = runtime()
        self.assertEqual(rt.decide("Schedule", frame()).reason, "preview_check")
        strong = {"preview": {"Da": 8, "Me": 8}, "sp_gain": 80, "fan_gain": 150}
        d = rt.decide("Schedule", frame("work_talk", strong))
        self.assertEqual(rt.memory.last_week_decision["option_id"], "work_talk")
        self.assertEqual((d.intent, d.reason), ("schedule_decide", "confirm_room"))

    def test_selected_room_without_preview_and_changed_sp(self):
        rt = runtime()
        d = rt.decide("Schedule", frame("work_talk", None))          # selected, preview row not read yet: no tap
        self.assertNotEqual(d.detail.get("room"), "work_talk")
        self.assertNotIn("work_talk", rt.memory.room_previews["checked"])
        rt.decide("Schedule", frame("lesson_da", low({"Da": 20})))
        self.assertIn("lesson_da", rt.memory.room_previews["previews"])
        rt.decide("Schedule", frame(None, None, sp=5))                 # SP spent: cached gains dropped
        self.assertEqual(rt.memory.room_previews["previews"], {})

    def test_not_snapshotted(self):
        self.assertNotIn("room_previews", nav.RunMemory.SNAPSHOT_KEYS)


if __name__ == "__main__":
    unittest.main()
