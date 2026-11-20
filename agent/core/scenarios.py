from __future__ import annotations

from agent.core.models import Scenario


DEFAULT_SCENARIO = {
    "state": {
        "turn": 8,
        "turns_to_exam": 3,
        "stamina": 42,
        "max_stamina": 100,
        "mental": 55,
        "stats": {
            "vocal": 260,
            "dance": 310,
            "visual": 220
        },
        "targets": {
            "vocal": 500,
            "dance": 500,
            "visual": 500
        }
    },
    "candidates": [
        {
            "id": "vocal_lesson",
            "label": "Vocal lesson",
            "category": "lesson",
            "gains": {
                "vocal": 42
            },
            "stamina_cost": 22,
            "risk": 0.12
        },
        {
            "id": "visual_lesson",
            "label": "Visual lesson",
            "category": "lesson",
            "gains": {
                "visual": 38
            },
            "stamina_cost": 18,
            "risk": 0.08
        },
        {
            "id": "rest",
            "label": "Rest",
            "category": "recovery",
            "stamina_recovery": 36,
            "risk": 0
        }
    ]
}


def default_scenario() -> Scenario:
    return Scenario.from_dict(DEFAULT_SCENARIO)
