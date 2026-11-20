r"""Synthetic preview data for the W.I.N.G. 本周评分卡 (ui/components/wing-score-card.js): offline only.

Runs ``wing_score.score_week`` on two hand-made S2W5 weeks (no capture, no game, no input) and turns each result into
``wing_score.score_card``. ``ui/fixtures/demo-state.json`` (``produce.score_cards``) must equal ``demo_cards()``
(tests/test_wing_score_card.py). Print with:

    .venv\Scripts\python.exe tools\wing_score_card_demo.py
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.core import wing_score as wsc  # noqa: E402
from agent.core import wing_state as ws  # noqa: E402

# S2W5, 12,000 fans, rank D, Vo 220: an empty Vo room, a 3-support Vo room with SP +40, a Vi room whose preview was
# not read, the three works, rest and the four S2 auditions (synthetic, the shape of tests/test_wing_score.rooms()).
BASE_STATE = {'version': 1,
 'phase': 'week',
 'season': 2,
 'week': 5,
 'stats': {'Vo': 220, 'Da': 60, 'Vi': 60, 'Me': 200},
 'fans': 12000,
 'stamina': 47,
 'max_stamina': 100,
 'condition': '普通',
 'sp': 120,
 'memory_level': 1,
 'trend': {'Vo': 2, 'Da': 1, 'Vi': 3},
 'trouble_rate': None,
 'stat_caps': None,
 'rank': 'D',
 'promise': None,
 'options': [{'id': 'lesson_vo',
              'kind': 'lesson',
              'attribute': 'Vo',
              'label': 'Vocal',
              'available': True,
              'preview': {'Vo': 30},
              'stamina_cost': 15,
              'supports': 0,
              'stamina_recovery': False,
              'fan_gain': None},
             {'id': 'lesson_da',
              'kind': 'lesson',
              'attribute': 'Da',
              'label': 'Dance',
              'available': True,
              'preview': {'Da': 30},
              'stamina_cost': 15,
              'supports': 0,
              'stamina_recovery': False,
              'fan_gain': None},
             {'id': 'work_radio',
              'kind': 'work',
              'attribute': None,
              'label': 'ラジオ',
              'available': True,
              'preview': {'Me': 20, 'Vo': 5},
              'stamina_cost': 20,
              'supports': 0,
              'stamina_recovery': False,
              'fan_gain': None},
             {'id': 'work_talk',
              'kind': 'work',
              'attribute': None,
              'label': 'トーク',
              'available': True,
              'preview': {'Me': 5, 'Da': 5},
              'stamina_cost': 20,
              'supports': 0,
              'stamina_recovery': False,
              'fan_gain': None,
              'sp_gain': 20},
             {'id': 'work_magazine',
              'kind': 'work',
              'attribute': None,
              'label': '雑誌撮影',
              'available': True,
              'preview': {'Vi': 5, 'Me': 5},
              'stamina_cost': 5,
              'supports': 0,
              'stamina_recovery': False,
              'fan_gain': 900},
             {'id': 'rest',
              'kind': 'rest',
              'attribute': None,
              'label': '休息',
              'available': True,
              'preview': {},
              'stamina_cost': 0,
              'supports': None,
              'stamina_recovery': False,
              'fan_gain': None},
             {'id': 'lesson_vo_3sup',
              'kind': 'lesson',
              'attribute': 'Vo',
              'label': 'Vocal',
              'available': True,
              'preview': {'Vo': 30},
              'stamina_cost': 15,
              'supports': 3,
              'stamina_recovery': False,
              'fan_gain': None,
              'sp_gain': 40},
             {'id': 'lesson_vi',
              'kind': 'lesson',
              'attribute': 'Vi',
              'label': 'Visual',
              'available': True,
              'preview': {},
              'stamina_cost': 15,
              'supports': 2,
              'stamina_recovery': False,
              'fan_gain': None}],
 'auditions': [{'id': 's2_3k',
                'name': 's2_3k',
                'fan_reward': 3000,
                'min_fans': None,
                'available': True,
                'difficulty': 6,
                'recommended': {'Vo': 150},
                'required_rank': None},
               {'id': 's2_5k',
                'name': 's2_5k',
                'fan_reward': 5000,
                'min_fans': None,
                'available': True,
                'difficulty': 7,
                'recommended': {'Vo': 170},
                'required_rank': None},
               {'id': 's2_10k',
                'name': 's2_10k',
                'fan_reward': 10000,
                'min_fans': None,
                'available': True,
                'difficulty': 8,
                'recommended': {'Vo': 190},
                'required_rank': None},
               {'id': 's2_15k',
                'name': 's2_15k',
                'fan_reward': 15000,
                'min_fans': None,
                'available': True,
                'difficulty': 10,
                'recommended': {'Vo': 230},
                'required_rank': 'D'}],
 'wins': [],
 'confidence': 0.99,
 'recognized': True,
 'evidence': ['synthetic']}
WEEKS = (("体力 50（トラブル 8%）", 50), ("体力 41（トラブル 25%，超过阈值）", 41))


def demo_cards() -> list:
    out = []
    for title, stamina in WEEKS:
        data = copy.deepcopy(BASE_STATE)
        data["stamina"] = stamina
        result = wsc.score_week(ws.WingState.from_dict(data), "Vo")
        out.append({"title": f"S2 第 5 周 · {title}", "source": "合成样例：离线 score_week，非实机",
                    "card": wsc.score_card(result.to_dict())})
    return out


if __name__ == "__main__":
    sys.stdout.buffer.write((json.dumps(demo_cards(), ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
