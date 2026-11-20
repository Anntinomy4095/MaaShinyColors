"""ANN-47: different card and both native ticket layouts, through real Maa offline replay."""
import datetime as dt
import json
import shutil
import unittest
from pathlib import Path

from agent.core import card_inventory as ci, training_select as ts, training_task as tt
from tests import maa_probe
from tests import test_daily_b5_tasks as b5
from tools import run_tasks as launcher

ROOT = Path(__file__).resolve().parents[1]
LIVE = 'dev_assets/private/sample-live-20261011/'
LIST = LIVE + '20-training-lv-ascending.png'
ZERO = LIVE + '22-training-detail-settled.png'
ONE = LIVE + '23-training-bronze-one-selected.png'
CONFIRM = LIVE + '24-training-after-start.png'
RESULT = LIVE + '26-training-result-settled.png'
AFTER = LIVE + '27-training-inventory-after.png'
TENKA = '2010150010'


@unittest.skipUnless(maa_probe.maa_ready(LIST, ZERO, ONE, CONFIRM, RESULT, AFTER), '需要训练原生截图、Maa与私有模板包')
class NativeGenericTrainingTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'dev_assets/private/run_logs' / ('_unittest_ann47_' + self.id().rsplit('.', 1)[-1])
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)

    def replay(self, card=None):
        task = {'name': 'Training'}
        if card:
            task['option'] = {'训练卡': card}
        frames = [LIST, ZERO, ONE, CONFIRM, RESULT, AFTER, LIST, b5.IDOL, b5.HOME]
        summary = launcher.run({'preset': None, 'tasks': [task], 'task_timeout_s': 60, 'allow_spend': ['training-start']},
            offline_steps=[(ROOT / f, 'click') for f in frames], simulate_input=True,
            policy={'live_input_enabled': False}, summary_path=False, save_diagnostics=False,
            task_options={'training': {'log_root': self.root, 'now': lambda: dt.datetime(2026, 10, 12, tzinfo=dt.timezone.utc)}})
        result, = summary['tasks']
        report, = [r for r in result['reports'] if r['kind'] == 'training']
        self.assertEqual((result['status'], report['outcome']), ('succeeded', 'trained'), report)
        self.assertEqual(result['simulated_actions'], ['training-list-slot-r1c1', 'training-bronze-plus-top',
            'training-submit', 'training-start', 'training-result-close', 'nav-back', 'nav-back', 'nav-back'])
        self.assertEqual(result['simulated_spends'], [['training-start', 'sent']])
        self.assertEqual(report['notes'], ['inventory 22723->22722', 'level 1->2'])
        events = [json.loads(line) for line in (self.root / 'training-2026-10-12.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([e['event'] for e in events], ['intent', 'result', 'verified'])
        self.assertEqual((events[0]['card_id'], events[0]['ticket_layout'], events[0]['quantity']), (TENKA, 'top', 1))

    def test_default_n_card_complete_native_chain(self):
        self.replay()

    def test_selected_non_asahi_card_complete_native_chain(self):
        self.replay(TENKA)

    def test_two_sampled_material_layouts_and_dialogs(self):
        probe = maa_probe.NodeProbe()
        for frame, yes, no in [(ZERO, 'Detail.Top.Guard', 'Detail.Lv50.Guard'),
                              (ONE, 'One.Top.Guard', 'One.Lv50.Guard'),
                              (b5.ZERO, 'Detail.Lv50.Guard', 'Detail.Top.Guard'),
                              (b5.ONE, 'One.Lv50.Guard', 'One.Top.Guard')]:
            self.assertTrue(probe.hits(frame, 'SC.Training.' + yes), (frame, yes))
            self.assertFalse(probe.hits(frame, 'SC.Training.' + no), (frame, no))
        self.assertTrue(probe.hits(CONFIRM, 'SC.Training.Confirm.Guard'))
        self.assertTrue(probe.hits(RESULT, 'SC.Training.ResultAny.Guard'))
        self.assertFalse(probe.hits(b5.EXTRA_MATERIAL, 'SC.Training.Confirm.Guard'))

    def test_low_score_latin_card_name_still_anchors_the_detail_page(self):
        # 2026-10-12 live: 「【HoP PoP らびっつ】三峰結華」 reads exactly but only at 0.9497.
        frame = 'dev_assets/private/b5-replay/training-list/detail-low-score-name.png'
        if not (ROOT / frame).exists():
            self.skipTest('私有截图缺失')
        probe = maa_probe.NodeProbe()
        for node in ('Anchor.Detail.CardName', 'Detail.Guard', 'Detail.Top.Guard'):
            self.assertTrue(probe.hits(frame, 'SC.Training.' + node), node)
        one = 'dev_assets/private/b5-replay/training-list/one-lv66-live.png'
        if (ROOT / one).exists():   # the One preview of the same card (Bronze 1, EXP 50) anchors too
            self.assertTrue(probe.hits(one, 'SC.Training.One.Top.Guard'))
        self.assertEqual(maa_probe.ocr_items(frame, (159, 96, 451, 31))[0]['text'], '【HoP PoP らびっつ】三峰結華')
        self.assertEqual(tuple(tt.NUMERIC_FIELDS['level'][0]), (252, 138, 38, 25))   # two-digit Lv.66 fits, the ▶▶ preview arrow stays outside

    def test_native_default_takes_the_first_bright_tile(self):
        from PIL import Image
        view = ts.locate_default(Image.open(ROOT / LIST).convert('RGB'), ci.CardIndex.from_assets(kind='S'))
        self.assertEqual((view.status, view.target.db_card, view.target.slot), ('found', TENKA, 'r1c1'))
        self.assertEqual(view.reasons, ('bright:r1c1:2010150010',))

    def test_default_unidentified_tile_binds_its_name_on_the_native_detail_page(self):
        # 2026-10-12: a bright first tile the DB cannot name is opened; the real detail page (OCR name + S badge) binds it.
        import dataclasses
        from unittest import mock
        real = ts.locate_default

        def unidentified(*args, **kwargs):
            view = real(*args, **kwargs)
            if view.status != 'found':
                return view
            target = dataclasses.replace(view.target, db_card=None, db_idol=None)
            return dataclasses.replace(view, targets=(target,))

        with mock.patch.object(ts, 'locate_default', unidentified):
            self.replay()


if __name__ == '__main__':
    unittest.main()


PLAIN_CONFIRM = 'dev_assets/private/training-confirm-plain-20261031/confirm-no-campaign.png'


@unittest.skipUnless(maa_probe.maa_ready(PLAIN_CONFIRM, b5.CONFIRM), '需要训练确认原生截图、Maa与私有模板包')
class ConfirmWithoutCampaignBubbleTests(unittest.TestCase):
    def test_start_anchor_with_and_without_the_bubble(self):
        """Live 2026-10-31: after the 超成功 campaign the 「超成功発生中！」 bubble left the トレーニング開始 button."""
        probe = maa_probe.NodeProbe()
        for frame in (PLAIN_CONFIRM, b5.CONFIRM):
            self.assertTrue(probe.hits(frame, 'SC.Training.Anchor.Confirm.Start'), frame)
        self.assertTrue(probe.hits(PLAIN_CONFIRM, 'SC.Training.Confirm.Guard'))
