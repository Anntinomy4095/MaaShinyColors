"""say "Halo" decisions (agent/core/halo_policy.py): 掘书线 (dig, the only route since 2026-11-07), pure functions."""
import unittest

from agent.core import halo_policy as hp

NONE = dict.fromkeys(hp.ADVICE_KEYS, 0)


def hub(**kw):
    read = {"season": 2, "weeks_left": 6, "stamina_pct": 60, "hub_button": "produce", "advice": dict(NONE)}
    read.update(kw)
    return read


class HubTests(unittest.TestCase):
    def test_week_start_growth_then_team_info_then_produce(self):
        self.assertEqual(hp.hub_choice(hub(), hp.HaloMemory()), "team_info")      # 成長 not automated yet
        mem = hp.HaloMemory(growth=True)
        self.assertEqual(hp.hub_choice(hub(), mem), "growth")
        mem.growth_weeks.add((2, 6))
        self.assertEqual(hp.hub_choice(hub(), mem), "team_info")
        mem.team_info_seasons.add(2)
        self.assertEqual(hp.hub_choice(hub(), mem), "team_info")      # S2 on: the recruits not disbanded yet
        mem.disbanded = True
        self.assertEqual(hp.hub_choice(hub(), mem), "produce")
        self.assertEqual(hp.hub_choice(hub(stamina_pct=8), mem), "rest")

    def test_season_one_has_no_growth(self):
        self.assertEqual(hp.hub_choice(hub(season=1, weeks_left=4), hp.HaloMemory()), "produce")

    def test_dig_gives_up_when_goals_met_or_at_final_live(self):
        mem = hp.HaloMemory(facility_done=True, facility_plus_done=True, completion=100)
        self.assertEqual(hp.hub_choice(hub(), mem), "give_up")
        mem = hp.HaloMemory()
        self.assertEqual(hp.hub_choice(hub(hub_button="first_live"), mem), "live")
        self.assertEqual(hp.hub_choice(hub(hub_button="final_live"), mem), "give_up")
        with self.assertRaises(ValueError):
            hp.HaloMemory(route="fes")                                   # 强fes线 removed

    def test_route_validated(self):
        with self.assertRaises(ValueError):
            hp.HaloMemory(route="x")


class WeekTests(unittest.TestCase):
    ROOMS = [{"id": "lesson_vo", "advice": ["vo", "vo", "vo"]}, {"id": "lesson_da", "advice": ["me", "rest"]},
             {"id": "work_magazine", "advice": ["vi"]}]

    def test_lesson_prefers_wanted_advice(self):
        self.assertEqual(hp.lesson_choice({"rooms": self.ROOMS, "advice": dict(NONE)}, hp.HaloMemory()), "lesson_da")
        self.assertIsNone(hp.lesson_choice({"rooms": None}, hp.HaloMemory()))

    def test_unread_icon_never_wins(self):
        rooms = [{"id": "lesson_vo", "advice": ["?", "me"]}, {"id": "lesson_da", "advice": []}]
        self.assertEqual(hp.lesson_choice({"rooms": rooms, "advice": dict(NONE)}, hp.HaloMemory()), "lesson_da")

    def test_practice_dig(self):
        cards = [{"index": 0, "name": "みんなで休憩", "rate": 100, "note": None},
                 {"index": 1, "name": "歌唱力強化練習", "rate": 100, "note": None},
                 {"index": 2, "name": "施設効率相談", "rate": 100, "note": "best"}]
        mem = hp.HaloMemory()
        self.assertEqual(hp.practice_choice(cards, {"stamina_pct": 50}, mem), 2)
        mem.facility_done = mem.facility_plus_done = True
        self.assertEqual(hp.practice_choice(cards, {"stamina_pct": 50}, mem), 1)
        self.assertEqual(hp.practice_choice(cards, {"stamina_pct": 20}, mem), 0)
        short = [dict(c, note="short") for c in cards]
        self.assertIsNone(hp.practice_choice(short, {"stamina_pct": 50}, hp.HaloMemory()))
        # 施設効率相談 still pending: its advice is not spent on another practice; ビジュアル効率相談 is not it
        pending = [cards[1], dict(cards[2], note="short"), {"index": 3, "name": "ビジュアル効率相談", "rate": 100}]
        self.assertIsNone(hp.practice_choice(pending, {"stamina_pct": 50}, hp.HaloMemory()))

    def test_audition_most_fans_at_100(self):
        cards = [{"index": 0, "rate": 100, "fans": 2000}, {"index": 1, "rate": 9, "fans": 90000},
                 {"index": 2, "rate": 100, "fans": 20000}, {"index": 3, "rate": None, "fans": 145000}]
        self.assertEqual(hp.audition_choice(cards, hp.HaloMemory()), 2)
        locked = cards + [{"index": 4, "rate": 100, "fans": 145000, "condition": True}]
        self.assertEqual(hp.audition_choice(locked, hp.HaloMemory()), 2)
        self.assertIsNone(hp.audition_choice(cards[1:2], hp.HaloMemory()))

    def test_audition_dig_keeps_under_the_fans_cap(self):
        cards = [{"index": 0, "rate": 100, "fans": 30000}, {"index": 1, "rate": 100, "fans": 60000}]
        mem = hp.HaloMemory(goals=hp.Goals(fans_cap=100000))
        self.assertEqual(hp.audition_choice(cards, mem), 1)
        hp.note_audition(cards[1], mem)
        self.assertEqual(mem.fans_est, 60000)
        self.assertEqual(hp.audition_choice(cards, mem), 0)              # 60000 + 60000 would pass 10万
        hp.note_audition(cards[0], mem)
        self.assertIsNone(hp.audition_choice(cards, mem))
        # live 2026-11-15: other actions add fans, so auditions stop FANS_CAP_MARGIN under the cap
        mem = hp.HaloMemory(goals=hp.Goals(fans_cap=100000))
        mem.fans_seen = 94840
        self.assertIsNone(hp.audition_choice([{"index": 0, "rate": 100, "fans": 5000}], mem))
        mem.fans_seen = 85000
        self.assertEqual(hp.audition_choice([{"index": 0, "rate": 100, "fans": 5000}], mem), 0)

    def test_audition_cap_uses_the_fan_total_on_screen(self):
        # H2d live 2026-11-06: the 目標ライブ fans are not auditions; the schedule page showed 114,204 in S3.
        cards = [{"index": 0, "rate": 100, "fans": 30000}, {"index": 1, "rate": 100, "fans": 65000}]
        mem = hp.HaloMemory(goals=hp.Goals(fans_cap=100000))
        mem.fans_seen = 50000
        self.assertEqual(hp.audition_choice(cards, mem), 0)              # 50000 + 65000 would pass 10万
        mem.fans_seen = 114204                                           # already past: ファンの声援 is earned
        self.assertEqual(hp.audition_choice(cards, mem), 1)

    def test_week_order(self):
        self.assertEqual(hp.week_order({"season": 1}, hp.HaloMemory()), ("lesson",))
        self.assertEqual(hp.week_order({"season": 3}, hp.HaloMemory()), ("practice", "audition", "lesson"))
        # 2026-11-16: advice first once 親愛度 reaches 50 while 施設効率相談 / + are pending (live S2: 55 at week 4)
        mem = hp.HaloMemory()
        self.assertEqual(hp.week_order({"season": 2, "affection_value": 42}, mem), ("practice", "audition", "lesson"))
        self.assertEqual(hp.week_order({"season": 2, "affection_value": 55}, mem), ("practice", "lesson", "audition"))
        self.assertEqual(hp.week_order({"season": 3, "affection": {"level": 5, "next": None}}, mem)[1], "lesson")
        mem.facility_done = mem.facility_plus_done = True
        self.assertEqual(hp.week_order({"season": 2, "affection_value": 55}, mem), ("practice", "audition", "lesson"))
        none = hp.HaloMemory(goals=hp.Goals(facility=False, facility_plus=False))
        self.assertEqual(hp.week_order({"season": 2, "affection_value": 90}, none)[1], "audition")


class GoalsTests(unittest.TestCase):
    """H2c batch 5: the dig route's goals from the user's wanted list (prefs.knowhow_wanted)."""
    def test_goals_from_wanted(self):
        from agent.core.knowhow_books import WantedItem as W
        base = [W("施設効率相談+", "prefix", purpose="halo"), W("施設効率相談", "prefix", purpose="halo"),
                W("パッシブスキル延長", "prefix", purpose="halo"), W("スタートダッシュ", purpose="step")]
        self.assertEqual(hp.goals_from_wanted(base).as_dict(),
                         {"facility": True, "facility_plus": True, "completion": 51, "fans_cap": 100000})
        goals = hp.goals_from_wanted([W("昂揚", "prefix"), W("ファンの声援", "prefix")])
        self.assertEqual(goals.as_dict(), {"facility": False, "facility_plus": False, "completion": 100, "fans_cap": None})
        self.assertEqual(hp.goals_from_wanted([W("パッシブスキル延長+", purpose="halo")]).completion, 100)
        self.assertIsNone(hp.goals_from_wanted([W("スタートダッシュ", purpose="step")]))
        self.assertIsNone(hp.goals_from_wanted([]))
        self.assertFalse(hp.goals_from_wanted([W("Vocal適正◎")]).any())

    def test_goals_round_trip_and_refusals(self):
        goals = hp.Goals(facility=False, completion=51, fans_cap=100000)
        self.assertEqual(hp.Goals.from_dict(goals.as_dict()), goals)
        for bad in ({"x": 1}, {"facility": 1}, {"completion": 0}, {"completion": "100"}, {"fans_cap": -1}, []):
            with self.assertRaises(ValueError, msg=bad):
                hp.Goals.from_dict(bad)


class LiveGrowthRecruitTests(unittest.TestCase):
    def test_live(self):
        self.assertEqual(hp.live_choice([79, 79, 78], "first_live", hp.HaloMemory()), 0)
        self.assertEqual(hp.live_choice([62, 97, 98], "final_live", hp.HaloMemory()), 2)
        self.assertIsNone(hp.live_choice([None, None, None], "final_live", hp.HaloMemory()))

    def test_growth_affection_until_target(self):
        mem = hp.HaloMemory()
        self.assertEqual(hp.growth_plan({"affection_value": 24}, mem), {"affection": 1, "stats": True})
        self.assertEqual(hp.growth_plan({"affection_value": 50}, mem)["affection"], 1)    # + wanted: on toward 80
        self.assertEqual(hp.growth_plan({"affection_value": 80}, mem)["affection"], 0)
        only = hp.HaloMemory(goals=hp.Goals(facility_plus=False))
        self.assertEqual(hp.growth_plan({"affection_value": 50}, only)["affection"], 0)
        mem.facility_done = mem.facility_plus_done = True
        self.assertEqual(hp.growth_plan({"affection_value": 50}, mem)["affection"], 0)

    def test_recruit_by_missing_kinds(self):
        cands = [{"index": 0, "advice": ["vo", "da", "vi", "best"]}, {"index": 1, "advice": ["me", "rest", "work", "limit"]}]
        self.assertEqual(hp.recruit_choice(cands, {"advice": dict(NONE)}, hp.HaloMemory()), 1)
        self.assertIsNone(hp.recruit_choice([{"index": 0, "advice": []}], {"advice": dict(NONE)}, hp.HaloMemory()))


class FactsTests(unittest.TestCase):
    def test_camp_week_keys_save_and_load(self):
        # live 2026-11-15: the 合宿 week reads season "camp"; saving mixed keys crashed the checkpoint
        mem = hp.HaloMemory()
        mem.growth_weeks |= {(2, 8), ("camp", 1), (3, 8)}
        mem.team_info_seasons |= {2, "camp"}
        data = hp.save_facts(mem)
        self.assertTrue(hp.valid_facts(data))
        back = hp.HaloMemory()
        hp.load_facts(back, data)
        self.assertEqual((back.growth_weeks, back.team_info_seasons), (mem.growth_weeks, mem.team_info_seasons))
        self.assertFalse(hp.valid_facts({**data, "growth_weeks": [["x", 1]]}))


if __name__ == "__main__":
    unittest.main()
