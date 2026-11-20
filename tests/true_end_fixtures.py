"""Invented mechanics for regressions, never a real card recommendation."""
from agent.core.true_end.models import CardData, Ownership, extract_card_features


def card(card_id, *, kind="S", idol=None, attr="Vo", mastery=10, sp=8, stamina=6,
         cap=200, multiplier=3.5, rarity="SR", extra=()):
    source = {"source_url": "synthetic_fixture", "fetched_at": "2026-10-05T00:00:00Z",
              "parser_version": "test", "status": "SYNTHETIC"}
    raw = {"card_id": card_id, "name": card_id, "idol": idol or card_id, "kind": kind,
           "status": "OK", "source_url": "synthetic_fixture", "rarity": rarity,
           "te_eligible": True if kind == "P" else None, "max_levels": {str(i): 80 for i in range(5)},
           "levels": {"80": {"Vo": 260, "Da": 240, "Vi": 220, "Me": 250}},
           "owned_live_skills": [{"id": "own", "components": {attr: multiplier}, "unlock_level": 1}],
           "support_skills": ([] if kind == "P" else [
               {"id": "growth", "kind": "mastery_stat", "attribute": attr, "room": attr, "levels": {"1": mastery}},
               {"id": "sp", "kind": "mastery_sp", "attribute": "SP", "room": attr, "levels": {"1": sp}},
               {"id": "stamina", "kind": "mastery_stamina", "room": attr, "levels": {"1": stamina}},
               *extra]),
           "panels": [{"id": "root", "kind": "stat", "sp_cost": 20, "required_lb": 0,
                       "prerequisites": [], "prerequisites_known": True, "effects": {attr: 10}},
                      {"id": "live", "kind": "live", "sp_cost": 30, "required_lb": 0,
                       "prerequisites": ["root"], "prerequisites_known": True,
                       "effects": {"id": card_id + "-live", "components": {attr: multiplier}}},
                      {"id": "cap", "kind": "cap", "sp_cost": 40, "required_lb": 3,
                       "prerequisites": ["root"], "prerequisites_known": True, "effects": {attr: cap}}],
           "events": [{"id": "e1", "gains": {attr: 20, "SP": 15}}], "provenance": {"card": source}}
    return CardData.from_dict(raw)


def feature(card_id, **kwargs):
    data = card(card_id, **kwargs)
    return extract_card_features(data, Ownership(card_id, 4, 80))


def growth_input(**overrides):
    value = {"starting_stats": {"Vo": 50, "Da": 50, "Vi": 50, "Me": 100},
             "starting_sp": 0, "starting_stamina": 100,
             "starting_live_skills": [
                 {"id": "basic-vo", "components": {"Vo": 1}},
                 {"id": "basic-da", "components": {"Da": 1}},
                 {"id": "basic-vi", "components": {"Vi": 1}},
                 {"id": "initial-p", "components": {"Vo": 2}}],
             "facility_levels": [1, 2, 3, 4], "placement_probability": 1 / 6,
             "perfect_probability": .1, "event_completion_probability": .75,
             "first_place_probability": .75}
    return value | overrides
