"""Latest open target is independent of current Skip eligibility; replay never sends input."""
import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.core import match_latest as ml, match_task as mt
from tests.test_match_scan import page
from tests.maa_probe import NodeProbe, maa_ready

BASE = "dev_assets/private/sample-live-20261011/"
AREA_FRAMES = tuple(BASE + name + ".png" for name in (
    "45-match-main", "52-match-main-areas-2-5", "53-match-main-area6-67",
    "55-match-main-bottom-area7", "56-match-main-area7-ex-locked"))
STAGE_FRAME = BASE + "57-match-area7-stages.png"


def area(areas, stamp, terminal=None, rights=(19, 20)):
    return ml.AreaPage(str(stamp).encode().hex().ljust(64, 'a'), stamp, tuple(areas), terminal, rights)


class LatestNavigationTests(unittest.TestCase):
    def navigation(self):
        nav = ml.LatestNavigation()
        for i, ids in enumerate(((1, 2, 3), (2, 3, 4), (3, 4, 5), (4, 5, 6), (5, 6, 7), (6, 7)), 1):
            self.assertEqual(nav.area_step(area(ids, i)), "area_down")
        self.assertEqual(nav.area_step(area((7,), 7, 7)), "area_open")
        self.assertEqual(nav.stage_step(ml.StageBoundary('b' * 64, 8, 7, 20, True, (19, 20))), 'back')
        self.assertEqual(nav.returned(area((7,), 9, 7)), 'ready')
        return nav

    def test_latest20_uses_latest_skippable_stage15(self):
        """2026-10-14 user: 「最新」 = the latest stage of the complete, fresh Skip list (open A7-S20 is not skippable)."""
        nav = self.navigation()
        planner = ml.LatestSkipPlanner(mt.MatchConfig(stage='latest', count=1, count_mode='fixed', sample_only=False), nav)
        for index, (stages, thumb) in enumerate((((1, 2, 3), (193, 326)), ((2, 3, 4), (300, 433)),
                                                ((3, 4, 15), (426, 560))), 10):
            decision = planner.step(page(stages, thumb, index, cost=(0, 19)))
        self.assertEqual(decision.kind, 'plus')
        self.assertEqual(planner.target, (1, 15))
        self.assertEqual(planner.evidence()['latest_open_stage'], 'A7-S20')
        self.assertEqual(planner.evidence()['selected_from'], 'latest_skippable')
        self.assertIsNone(planner.evidence()['cycle_id'])
        run = mt.MatchRun(planner.config, None)
        run.planner = planner
        with patch('agent.core.task_common.TaskRun.summary', return_value={}):
            self.assertEqual(run.summary()['progress'], {
                'latest_open_stage': 'A7-S20', 'latest_skippable_stage': 'A1-S15',
                'selected_stage': 'A1-S15', 'skip_list_complete': True})

    def test_direct_skip_list_latest_long_presses_plus_for_all_rights(self):
        """2026-10-14 user: Skip opens straight from the main page; ＋ is long-pressed to the count (all rights)."""
        planner = ml.SkipListLatestPlanner(mt.MatchConfig(stage='latest', count=19, count_mode='fixed', sample_only=False))
        for index, (stages, thumb) in enumerate((((1, 2, 3), (193, 326)), ((2, 3, 4), (300, 433)),
                                                ((3, 4, 15), (426, 560))), 10):
            decision = planner.step(page(stages, thumb, index, cost=(0, 19)))
        self.assertEqual((decision.kind, planner.last, planner.target), ('plus', 'hold', (1, 15)))
        last = page((3, 4, 15), (426, 560), 20, cost=(19, 19))
        last = dataclasses.replace(last, rows=tuple(dataclasses.replace(r, count=19) if r.stage == (1, 15) else r
                                                    for r in last.rows))
        self.assertEqual(planner.step(last).kind, 'confirm')
        self.assertEqual(planner.pressed, 19)
        # live 2026-10-31: at all rights (8 / 8) the game disables ＋; the hold's counter is taken before that check
        held = ml.SkipListLatestPlanner(mt.MatchConfig(stage='latest', count=19, count_mode='fixed', sample_only=False))
        for index, (stages, thumb) in enumerate((((1, 2, 3), (193, 326)), ((2, 3, 4), (300, 433)),
                                                ((3, 4, 15), (426, 560))), 10):
            held.step(page(stages, thumb, index, cost=(0, 19)))
        maxed = dataclasses.replace(last, rows=tuple(dataclasses.replace(r, plus_enabled=False) if r.stage == (1, 15)
                                                     else r for r in last.rows))
        self.assertEqual((held.step(maxed).kind, held.pressed), ('confirm', 19))
        # a host without long press: the same ＋ is one ordinary tap
        other = ml.SkipListLatestPlanner(mt.MatchConfig(stage='latest', count=19, count_mode='fixed', sample_only=False))
        for index, (stages, thumb) in enumerate((((1, 2, 3), (193, 326)), ((2, 3, 4), (300, 433)),
                                                ((3, 4, 15), (426, 560))), 10):
            other.step(page(stages, thumb, index, cost=(0, 19)))
        other.hold_unavailable()
        self.assertEqual((other.last, other.pressed), ('plus', 1))

    def test_latest_scan_passes_an_unread_covered_row_but_never_presses_on_one(self):
        from tests.test_match_scan import blank
        planner = ml.SkipListLatestPlanner(mt.MatchConfig(stage='latest', count=1, count_mode='fixed', sample_only=False))
        self.assertEqual(planner.step(page((1, 2, 3, 4, 5, 6), (195, 326), 10, cost=(0, 20))).kind, 'scroll_down')
        self.assertEqual(planner.step(blank(page((5, 6, 7, 8), (257, 388), 11, cost=(0, 20)), 0)).kind, 'scroll_down')
        bottom = page((7, 8, 9), (427, 558), 12, cost=(0, 20))
        # the list ends on a page whose top row is unread: coverage is complete, but ＋ waits for a full read
        self.assertEqual(planner.step(blank(bottom, 0)).kind, 'wait')
        self.assertEqual(planner.scan.stages, [(1, s) for s in range(1, 10)])
        decision = planner.step(dataclasses.replace(bottom, observed_at=13))
        self.assertEqual((decision.kind, decision.slot, planner.target), ('plus', 3, (1, 9)))

    def test_incomplete_skip_list_never_retargets(self):
        nav = self.navigation()
        planner = ml.LatestSkipPlanner(mt.MatchConfig(stage='latest', count=1, count_mode='fixed', sample_only=False), nav)
        planner.step(page((1, 2, 3), (193, 326), 10, cost=(0, 19)))
        self.assertIsNone(planner.retarget)
        self.assertEqual(planner.target, (7, 20))

    def test_latest_current_skip_row_can_use_existing_precise_planner(self):
        nav = self.navigation()
        planner = ml.LatestSkipPlanner(mt.MatchConfig(stage='latest', count=1, count_mode='fixed', sample_only=False), nav)
        source = page((20,), (193, 560), 10, cost=(0, 19))
        source = dataclasses.replace(source, rows=(dataclasses.replace(source.rows[0], stage=(7, 20)),))
        self.assertEqual(planner.step(source).kind, 'plus')
        changed = dataclasses.replace(source, cost=(1, 19), rows=(dataclasses.replace(source.rows[0], count=1),))
        self.assertEqual(planner.step(changed).kind, 'confirm')
        self.assertEqual(planner.target, (7, 20))

    def test_partial_start_missing_overlap_changed_rights_or_return_stop(self):
        self.assertEqual(ml.LatestNavigation().area_step(area((4, 5, 6), 1)), 'area_up')
        for second in (area((4, 5, 6), 2), area((2, 3, 4), 2, rights=(18, 20)), area((2, 3, 4), 1)):
            nav = ml.LatestNavigation()
            nav.area_step(area((1, 2, 3), 1))
            self.assertEqual(nav.area_step(second), 'failed')
        nav = self.navigation()
        nav.phase = 'return'
        self.assertEqual(nav.returned(area((6, 7), 10)), 'failed')

    def test_back_may_reset_area_list_to_top_but_not_change_boundary_or_rights(self):
        # Live 2026-10-13: back from the A7 stage page showed areas 1-3 (rights unchanged), not the A7/EX page.
        for areas in ((1, 2, 3), (1, 2), (1,)):
            nav = self.navigation_to_back()
            self.assertEqual(nav.returned(area(areas, 9)), 'ready')
            self.assertEqual(nav.target, (7, 20))
        for page_ in (area((1, 2, 3), 9, rights=(18, 20)), area((2, 3, 4), 9), area((1, 2, 8), 9),
                      area((1, 2, 3), 9, terminal=3), area((7,), 9), area((1, 2, 3), 8)):
            with self.subTest(page=page_):
                nav = self.navigation_to_back()
                self.assertEqual(nav.returned(page_), 'failed')
                self.assertEqual(nav.phase, 'return')

    def navigation_to_back(self):
        nav = ml.LatestNavigation()
        for i, ids in enumerate(((1, 2, 3), (2, 3, 4), (3, 4, 5), (4, 5, 6), (5, 6, 7), (6, 7)), 1):
            nav.area_step(area(ids, i))
        nav.area_step(area((7,), 7, 7))
        self.assertEqual(nav.stage_step(ml.StageBoundary('b' * 64, 8, 7, 20, True, (19, 20))), 'back')
        return nav

    def test_mid_list_returns_to_top_before_collecting_open_progress(self):
        nav=ml.LatestNavigation()
        self.assertEqual(nav.area_step(area((6,7),1)), 'area_up')
        self.assertEqual(nav.area_step(area((4,5,6),2)), 'area_up')
        self.assertEqual(nav.area_step(area((1,2,3),3)), 'area_down')
        self.assertEqual(nav.areas, [1,2,3])
        self.assertIsNone(nav.target)
        stalled=ml.LatestNavigation()
        stalled.area_step(area((6,7),1))
        for index in range(mt.MAX_UNREADABLE_FRAMES+1):
            decision=stalled.area_step(area((6,7),2+index))
        self.assertEqual(decision, 'failed')
        self.assertIsNone(stalled.target)

    def test_no_effect_and_unknown_boundary_are_bounded(self):
        nav = ml.LatestNavigation()
        nav.area_step(area((1, 2, 3), 1))
        for i in range(mt.MAX_UNREADABLE_FRAMES + 1):
            decision = nav.area_step(area((1, 2, 3), 2 + i))
        self.assertEqual(decision, 'failed')
        self.assertIsNone(nav.target)
        nav = ml.LatestNavigation()
        nav.phase = 'stages'
        for _ in range(mt.MAX_UNREADABLE_FRAMES + 1):
            decision = nav.stage_step(None)
        self.assertEqual(decision, 'failed')


@unittest.skipUnless(maa_ready(*AREA_FRAMES, STAGE_FRAME), "需要10-11原生Match帧和OCR模型")
class LatestNativeTests(unittest.TestCase):
    def observe(self, frame, reader):
        from maa.custom_recognition import CustomRecognition
        from agent.core.task_custom import Vision
        result = []
        class Probe(CustomRecognition):
            def analyze(self, context, argv):
                result.append(reader(Vision(context), argv.image, 10.0))
                return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail='read_only')
        resource = NodeProbe.resource()
        resource.register_custom_recognition('SC.Test.LatestRead', Probe())
        NodeProbe().run(frame, {'SC.Test.Start': {'next': ['SC.Test.LatestRead']},
                               'SC.Test.LatestRead': {'recognition': 'Custom', 'custom_recognition': 'SC.Test.LatestRead'}})
        self.assertEqual(len(result), 1)
        return result[0]

    def test_terminal_native_area_and_stage_boundary(self):
        area_page = self.observe(AREA_FRAMES[-1], ml.read_area_page)
        self.assertIsNotNone(area_page)
        self.assertEqual((area_page.areas, area_page.terminal_locked, area_page.rights), ((7,), 7, (19, 20)))
        stage = self.observe(STAGE_FRAME, lambda v, i, t: ml.read_stage_boundary(v, i, t, 7))
        self.assertIsNotNone(stage)
        self.assertEqual((stage.area, stage.latest, stage.locked_after, stage.rights), (7, 20, True, (19, 20)))
        self.assertIsNone(self.observe(AREA_FRAMES[-1], lambda v, i, t: ml.read_stage_boundary(v, i, t, 7)))
        self.assertIsNone(self.observe(STAGE_FRAME, ml.read_area_page))

    def test_native_area_headers_keep_their_visible_order(self):
        self.assertEqual(self.observe(AREA_FRAMES[0], ml.read_area_page).areas, (1, 2, 3))
        self.assertEqual(self.observe(AREA_FRAMES[1], ml.read_area_page).areas, (2, 3, 4))

    def test_production_pipeline_targets_last_skip_row15_without_spend(self):
        self.replay_latest()

    def test_native_middle_start_returns_to_top_then_reads_same_latest(self):
        self.replay_latest(mid_start=True)

    def test_native_back_to_area_list_top_keeps_bound_latest(self):
        # Live 2026-10-13 real run: back reset the list to areas 1-3 and returned() failed latest_area_boundary_changed.
        self.replay_latest(back_frame=AREA_FRAMES[0])

    def replay_latest(self, mid_start=False, back_frame=None):
        from agent.core.pipeline_actions import default_table
        from agent.core.task_actions import ReportSink
        from tools import run_tasks as launcher
        # 2026-10-14 user: 「最新」 opens Skip straight from the Match main page (any area-list position); the target is
        # the last stage of the complete Skip list.
        start = AREA_FRAMES[2] if mid_start else back_frame or AREA_FRAMES[0]
        steps = [start, *(BASE + name + '.png' for name in ('46-match-skip-top', '49-match-skip-5-10-complete',
                    '50-match-skip-overlap-9-14', '51-match-skip-bottom-11-15')), start]
        images, advances = launcher.load_frames([(Path(f), 'click') for f in steps])
        table = default_table()
        table = dataclasses.replace(table, actions=tuple(dataclasses.replace(a, enabled=True)
                                    if a.id.startswith('match-latest-') else a for a in table.actions))
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "dev_assets/private") as temp, patch('agent.core.pipeline_actions.default_table', return_value=table):
            sink = ReportSink()
            scenario = launcher.ReplayScenario(images, advances, allow_input=True, table=table,
                                                allow_spend=['match-skip-confirm'])
            authority = launcher.ReplaySpendAuthority(scenario)
            runtimes = launcher.task_runtimes(sink, authority, {'match': {'log_root': Path(temp),
                                            'sample_root': Path(temp), 'clock': lambda: float(scenario.captures)}})
            tasker = launcher.build_tasker(launcher.create_replay_controller(scenario), sink,
                                           save_diagnostics=False, runtimes=runtimes)
            override = launcher.mode_override(launcher.OFFLINE_SIMULATED, allow_spend=['match-skip-confirm'])
            override.update({'SC.Match.Config.Stage': {'attach': {'stage': 'latest'}},
                             'SC.Match.Config.Count': {'attach': {'count': 1, 'count_mode': 'fixed'}},
                             'SC.Match.Config.Sample': {'attach': {'sample_only': False}},
                             'SC.Match.Skip.Cancel': {'enabled': True, 'next': ['SC.Test.End']},
                             'SC.Test.End': {'recognition': 'DirectHit', 'action': 'Custom',
                                             'custom_action': 'SC.Match.Finish'}})
            for name, node in override.items():
                if 'timeout' in node:
                    node['timeout'] = 5000
            job = tasker.post_task('SC.Match.Entry', override).wait()
            trace = [node.name for node in tasker.get_task_detail(job.job_id).nodes if node is not None]
            runtime = runtimes['match']
            # 2026-10-14 user: 「最新」 = the latest skippable stage of the complete list (A5-S15), not open A7-S20.
            # The replay ends after the first ＋ (the next frame is not the dialog), so nothing is confirmed.
            self.assertEqual(runtime.run.planner.target, (5, 15), (trace, scenario.refused))
            self.assertEqual(runtime.run.planner.pressed, 1)
            self.assertNotEqual(runtime.run.outcome, 'latest_not_skippable')
            self.assertEqual(scenario.spends, [])
            self.assertFalse(any(a.startswith('match-latest-') for a in scenario.actions), scenario.actions)
            self.assertEqual(sum(action.startswith('match-skip-plus') for action in scenario.actions), 1)
            self.assertNotIn('match-skip-confirm', scenario.actions)
            self.assertEqual(runtime.run.summary()['progress']['latest_skippable_stage'], 'A5-S15')
            self.assertEqual(runtime.run.summary()['progress']['selected_stage'], 'A5-S15')


HOME_FRAME = "dev_assets/private/live-20261012k/after-match1.png"


@unittest.skipUnless(maa_ready(HOME_FRAME), "需要10-13主页帧和OCR模型")
class LatestFromHomeTests(unittest.TestCase):
    def test_home_is_not_an_unreadable_area_page(self):
        # 2026-10-13 live: from home, Latest.Hold held on every frame (unreadable wait) and the task failed with 0 clicks.
        from tools import run_tasks as launcher
        images, advances = launcher.load_frames([(Path(HOME_FRAME), 'click')])
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "dev_assets/private") as temp:
            from agent.core.task_actions import ReportSink
            sink = ReportSink()
            scenario = launcher.ReplayScenario(images, advances, allow_input=True, allow_spend=[])
            authority = launcher.ReplaySpendAuthority(scenario)
            runtimes = launcher.task_runtimes(sink, authority, {'match': {'log_root': Path(temp),
                                            'sample_root': Path(temp), 'clock': lambda: float(scenario.captures)}})
            tasker = launcher.build_tasker(launcher.create_replay_controller(scenario), sink,
                                           save_diagnostics=False, runtimes=runtimes)
            override = launcher.mode_override(launcher.OFFLINE_SIMULATED, allow_spend=[])
            override.update({'SC.Match.Config.Stage': {'attach': {'stage': 'latest'}},
                             'SC.Match.Config.Count': {'attach': {'count': 1, 'count_mode': 'fixed'}},
                             'SC.Match.Config.Sample': {'attach': {'sample_only': False}}})
            for node in override.values():
                if 'timeout' in node:
                    node['timeout'] = 5000
            job = tasker.post_task('SC.Match.Entry', override).wait()
            trace = [node.name for node in tasker.get_task_detail(job.job_id).nodes if node is not None]
            self.assertIn('SC.Match.AtHome', trace)
            self.assertNotIn('SC.Match.Latest.Hold', trace)
            self.assertNotEqual(runtimes['match'].run.outcome, 'latest_progress_unreadable', trace)
            self.assertEqual(scenario.spends, [])


if __name__ == '__main__':
    unittest.main()
