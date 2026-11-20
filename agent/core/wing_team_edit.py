"""ユニット編成 team edit (``RunOptions.team_edit``, start mode, default off): put the configured P / S1–S4 / guest cards
into the configured unit, one slot at a time, each verified on the formation page before the next.

Evidence: supervised recording ``captures-20261011-164549Z`` (user edited ユニット17, P 風野灯織).
Observed flow, native 1280x720:

- formation page: a tap on a slot's card icon (the ``unit_formation`` boxes of ``card_inventory``) opens the picker
  after a Now Loading (P #85 (466,291) -> #88/#91, S1 #98 (592,293) -> #108/#110, S2 (740,301) -> #169, S3 (918,325)
  -> #177, S4 (1030,300) -> #218, guest (1204,298) -> #204);
- picker: プロデュースアイドル編成 (P) / サポートアイドル編成 (S and guest; the guest is one of the user's own S cards),
  the same 7 x 138 px grid as the アイドル一覧 (``card_inventory`` ``idol_list``), list top = grid offset 184, scrollbar
  thumb top 191. One tap on a tile puts that card into the slot and returns to the formation page (no confirm
  dialog in the recording): P #95 (1179,399) -> #97 Now Loading -> #98 P = 1040020030; S r1c1 #172 (338,239) -> #175,
  S r1c2 (441,243) -> #166, S r2c6 (1036,398) -> #219.
- the game saves the formation on each pick (no save button; the mid-Produce ユニット確認 #239 shows the edited team).

2026-10-14 (UI 推荐编队): support slots are edited first and P last; the first サポート visit
surveys every wanted support card (seen on a tile Maa may tap) before the first swap; the picker is searched by a
read-only, bounded wheel scan (``picker_step``) and a card is tapped through one of three fixed rect phases that fits
the grid offset read on that frame. Sorting (RunOptions.picker_sort, default off, サポート picker only): before the scan
of a visit Maa may change the sort key once to the main attribute of the wanted cards (``agent/core/picker_sort.py``,
``sort_step`` / ``sort_popup_step`` / ``sort_verify``); never a filter or the direction. Targets beyond the four recorded
tiles stay disabled until ``assets/input/wing_picker_verified.json`` lists them (tools/wing_picker_enable.py). A
target that is not found or not tappable, a tile that is not unique, a slot whose current card cannot be read, a
slot edited before, or a picked slot that does not read as the target afterwards stops the run; nothing is retried.
Pure module (no Maa).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

SLOTS = ("P", "S1", "S2", "S3", "S4", "guest")
SLOT_INTENTS = {"P": "unit_slot_p", "S1": "unit_slot_s1", "S2": "unit_slot_s2", "S3": "unit_slot_s3",
                "S4": "unit_slot_s4", "guest": "unit_slot_guest"}
# Card icon boxes of the formation page (card_inventory.LAYOUTS["unit_formation"]) inset by 10 px.
SLOT_RECTS = {"P": (414, 260, 84, 84), "S1": (548, 260, 84, 84), "S2": (696, 260, 84, 84),
              "S3": (844, 260, 84, 84), "S4": (992, 260, 84, 84), "guest": (1142, 260, 84, 84)}
GRID_X0, GRID_DX, GRID_TILE, GRID_DY = 267, 138, 108, 154
GRID_TOP_OFFSET = 184            # first tile row y at the list top (#91/#110/#172/#218)
GRID_ROWS, GRID_COLS = 3, 7      # fully visible rows at the top (third row ends at y 600 < viewport 680)
THUMB_TOP_MAX = 196              # scrollbar thumb top at the list top (measured 191)
# Tap phases (2026-10-14, scroll scan): the action table holds fixed rects, but a scrolled grid sits at any offset in
# [170, 324) (card_inventory idol_list). Phase "" = the list-top rects (76 px inside the 108 px tile, the recorded
# taps); phases "b" / "c" = 40 px / 32 px rects for grids at offset 232 / 286. A tile is tapped through a phase only when the
# tile read on this frame contains that rect with a margin (|offset - phase| <= tolerance): together the three phases
# cover every offset 170..322 the reader returns. Phase key -> (nominal offset, rect size, full rows).
PHASES = {"": (GRID_TOP_OFFSET, 76, 3), "b": (232, 40, 3), "c": (286, 32, 2)}
PHASE_MARGIN = 2
# Tiles with a clean tap -> result pair in the recording (captures-20261011-164549Z). Further taps are enabled only
# through assets/input/wing_picker_verified.json (tools/wing_picker_enable.py after a supervised live check).
VERIFIED_TILES = {(2, 7), (1, 1), (1, 2), (2, 6)}
MAX_EDITS = 6
EDIT_ORDER = ("S1", "S2", "S3", "S4", "guest", "P")
# Read-only scroll scan of the picker (wheel inside the grid, same viewport as the アイドル一覧 scan).
SCROLL_INTENTS = {"down": "picker_scroll_down", "up": "picker_scroll_up"}
SCROLL_RECT = (270, 430, 130, 40)           # between tile rows 2/3 at the list top, like idol-list-scroll-*
SCROLL_WHEEL = {"picker_scroll_down": -60, "picker_scroll_up": 60}
MAX_PICKER_SCROLLS = 30          # wheel steps per picker visit (survey + find + rewind)
SETTLE_WAITS = 6                 # frames a scrolled list may take to stop moving
VERIFIED_FILE = Path(__file__).resolve().parents[2] / "assets" / "input" / "wing_picker_verified.json"


def tile_intent(row: int, col: int, phase: str = "") -> str:
    return f"picker_tile_{phase}_r{row}c{col}" if phase else f"picker_tile_r{row}c{col}"


def tile_rect(row: int, col: int, phase: str = "") -> tuple[int, int, int, int]:
    """Tap rect of grid row/col (1-based) for a grid at the phase's nominal offset (centred in the 108 px tile)."""
    offset, size, _ = PHASES[phase]
    inset = (GRID_TILE - size) // 2
    x = GRID_X0 + GRID_DX * (col - 1)
    y = offset + GRID_DY * (row - 1)
    return (x + inset, y + inset, size, size)


TILE_INTENTS = tuple(tile_intent(r, c, ph) for ph, (_, _, rows) in PHASES.items()
                     for r in range(1, rows + 1) for c in range(1, GRID_COLS + 1))
# 2026-10-14 sort (agent/core/picker_sort.py, RunOptions.picker_sort, default off): the sort key button of the サポート
# picker opens 並び替え; a key cell moves the pink mark; 決定 applies. Clean before/after pairs in the recording:
# open #112 (1045,141) -> #113 (also #118->#120, #123->#125, #141->#143); key r2c2 #120 (637,355) -> #121 (pink moved
# r2c1 -> r2c2); key r3c3 #143 (827,435) -> #145 (r3c2 -> r3c3); 決定 #122 (652,532) -> #123 header Dance順 (also #145->#147,
# #156->#158, #161->#162). The other seven key cells were only tapped in compound pairs (open + key between two frames)
# -> disabled until assets/input/wing_picker_verified.json lists them (tools/wing_picker_enable.py).
SORT_OPEN_INTENT = "picker_sort_open"
SORT_APPLY_INTENT = "picker_sort_apply"
# Auto 自动编队 (RunOptions.edit_only, 2026-10-30): 「Lv順」 ▼降順 instead of the attribute plan. Live: 413 S cards in
# デフォルト order put the wanted idol-20/25 cards past 30 wheel steps; under Lv順 the four Lv80 picks are in the first
# 45 tiles and the Lv75 guest near tile 55.
EDIT_ONLY_SORT_KEY = "Lv順"


def sort_key_intent(row: int, col: int) -> str:
    return f"picker_sort_key_r{row}c{col}"


SORT_KEY_INTENTS = tuple(sort_key_intent(r, c) for r in range(1, 4) for c in range(1, 4))
SORT_POPUP_INTENTS = SORT_KEY_INTENTS + (SORT_APPLY_INTENT,)
SORT_RECORDED = {SORT_OPEN_INTENT: "captures-20261011-164549Z #112 (1045,141) -> #113 並び替え (clean pair; also "
                                   "#118->#120, #123->#125, #141->#143)",
                 sort_key_intent(2, 2): "captures-20261011-164549Z #120 (637,355) -> #121 pink r2c1 -> r2c2 (clean pair)",
                 sort_key_intent(3, 3): "captures-20261011-164549Z #143 (827,435) -> #145 pink r3c2 -> r3c3 (clean pair)",
                 SORT_APPLY_INTENT: "captures-20261011-164549Z #122 (652,532) -> #123 header Dance順 (clean pair; also "
                                    "#145->#147, #156->#158, #161->#162)"}
PICKER_INTENTS = TILE_INTENTS + tuple(SCROLL_INTENTS.values()) + (SORT_OPEN_INTENT,)


def load_verified(path: Path | None = None) -> dict[str, str]:
    """{intent: evidence} of the picker targets verified for live taps: the recorded tiles plus the reviewed file
    (assets/input/wing_picker_verified.json). An unreadable file adds nothing (never widens on doubt)."""
    out = {tile_intent(r, c): "captures-20261011-164549Z (clean tap pair)" for r, c in VERIFIED_TILES}
    out.update(SORT_RECORDED)
    try:
        data = json.loads(Path(path or VERIFIED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for intent, evidence in (data.get("verified") or {}).items() if isinstance(data, dict) else ():
        if (intent in PICKER_INTENTS or intent in SORT_POPUP_INTENTS) and isinstance(evidence, str) and evidence.strip():
            out[intent] = evidence.strip()
    return out


VERIFIED_INTENTS = frozenset(load_verified())


def picker_kind(title_texts) -> str | None:
    """produce | support from the picker title OCR (「プロデュースアイドル編成」 / 「サポートアイドル編成」)."""
    text = "".join(str(t) for t, _ in title_texts or ())
    if "プロデュース" in text:
        return "produce"
    if "サポート" in text or "サボート" in text:
        return "support"
    return None


def slot_kind(slot: str) -> str:
    return "produce" if slot == "P" else "support"


def expected_slots(team_slots: Mapping[str, Any]) -> dict[str, str]:
    """{slot: card} wanted per slot. S cards keep the configured order (S1..S4)."""
    out: dict[str, str] = {}
    if team_slots.get("P"):
        out["P"] = str(team_slots["P"])
    for i, card in enumerate([c for c in team_slots.get("S") or () if c][:4]):
        out[f"S{i + 1}"] = str(card)
    if team_slots.get("guest"):
        out["guest"] = str(team_slots["guest"])
    return out


def _accepted(reading) -> str | None:
    if isinstance(reading, Mapping) and reading.get("status") == "accepted" and isinstance(reading.get("card_id"), str):
        return reading["card_id"]
    return None


def team_rule_problems(expected: Mapping[str, str], owned: Mapping[str, Any] | None = None) -> list[str]:
    """Produce team rules: no idol twice across P / S / guest, all five S-side cards distinct,
    P is a P card and S / guest are S cards, the guest is owned (when an owned list is given)."""
    from agent.core.card_inventory import idol_of
    problems = []
    if "P" not in expected:
        problems.append("team_p_missing")
    if any(slot not in expected for slot in ("S1", "S2", "S3", "S4")):
        problems.append("team_s_requires_four")
    idols: dict[str, str] = {}
    for slot, card in expected.items():
        if not (isinstance(card, str) and len(card) == 10 and card.isdigit()):
            problems.append(f"team_{slot}_invalid_id")
            continue
        if card[0] != ("1" if slot == "P" else "2"):
            problems.append(f"team_{slot}_wrong_kind")
        idol = idol_of(card)
        if idol in idols:
            problems.append(f"team_duplicate_idol:{idol}:{idols[idol]}/{slot}")
        idols[idol] = slot
    if owned is not None and expected.get("guest"):
        owned_s = {str(c) for c in owned.get("s") or ()}
        if owned_s and expected["guest"] not in owned_s:
            problems.append("team_guest_not_owned")
    return problems


OTHER_IDOL_MIN_SCORE = 0.70     # a rejected tile whose best candidate is another idol at this coarse score


def _likely_card(reading) -> str | None:
    """Best candidate of a rejected reading when it is strong (2026-10-14 live: an unrecognised S4 held the idol
    wanted in S3; ordering by it avoids the game's same-idol confirmation)."""
    if not isinstance(reading, Mapping) or reading.get("blank"):
        return None
    cands = [c for c in reading.get("candidates") or () if isinstance(c, (list, tuple)) and len(c) == 3]
    return cands[0][0] if cands and cands[0][1] >= OTHER_IDOL_MIN_SCORE and isinstance(cands[0][0], str) else None


def _other_idol(reading, card: str) -> bool:
    """A rejected slot reading that clearly shows another idol (best coarse candidate of a different idol, no
    candidate of the wanted idol among the top ones): safe to replace. Anything else stays ``unread``."""
    from agent.core.card_inventory import idol_of
    if not isinstance(reading, Mapping):
        return False
    cands = [c for c in reading.get("candidates") or () if isinstance(c, (list, tuple)) and len(c) == 3]
    if not cands or cands[0][1] < OTHER_IDOL_MIN_SCORE:
        return False
    return all(idol_of(c[0]) != idol_of(card) for c in cands[:3])


def slot_states(cards: Mapping[str, Any] | None, expected: Mapping[str, str], *, blank_wrong=()) -> dict[str, str]:
    """Per wanted slot: ``ok`` (holds the wanted card: accepted, targeted or team-edit match), ``wrong`` (an accepted different
    card) or ``unread``. S slots are positional here (the editor fills S1..S4 in the configured order); the final
    verification before 次へ (wing_unit_check.compare) treats S as a set."""
    from agent.core import wing_unit_check as wuc
    out = {}
    for slot, card in expected.items():
        reading = (cards or {}).get(slot)
        verdict = wuc.edit_slot_verdict(reading, card)
        if verdict == "match":
            out[slot] = "ok"
        elif slot in blank_wrong:
            out[slot] = "wrong"         # emptied by the game's same-idol confirmation: to be filled
        elif _accepted(reading) is not None or _other_idol(reading, card):
            out[slot] = "wrong"
        else:
            out[slot] = "unread"
    return out


def assign_s(cards: Mapping[str, Any] | None, expected: Mapping[str, str]) -> dict[str, str]:
    """S cards are a set (the game's S order need not follow the configuration): a wanted S card already in some S
    slot stays there; the other S slots receive the remaining wanted cards in configured order."""
    from agent.core import wing_unit_check as wuc
    s_slots = [s for s in ("S1", "S2", "S3", "S4") if s in expected]
    wanted = [expected[s] for s in s_slots]
    kept: dict[str, str] = {}
    for slot in s_slots:
        reading = (cards or {}).get(slot)
        for card in wanted:
            if card not in kept.values() and wuc.edit_slot_verdict(reading, card) == "match":
                kept[slot] = card
                break
    rest = [c for c in wanted if c not in kept.values()]
    out = dict(expected)
    for slot in s_slots:
        out[slot] = kept[slot] if slot in kept else rest.pop(0)
    return out


def next_slot(cards: Mapping[str, Any] | None, expected: Mapping[str, str], done: Mapping[str, Any], *,
              fill_blank: bool = False) -> dict:
    """The next slot to edit: {"action": "edit", "slot", "card"} | {"action": "done"} | {"action": "stop", "reason"}.

    Before the first edit an S card that already sits in another S slot would be moved by the game (the picker swaps
    or asks to confirm, not recorded) -> stop; Maa only fills slots whose wanted card is not elsewhere in the unit."""
    blank = [s for s in expected if isinstance((cards or {}).get(s), Mapping) and (cards or {})[s].get("blank")]
    if blank and not fill_blank:
        # Loading formation, an empty slot or a dimmed (conflicting) guest: re-read; the caller bounds the waits.
        return {"action": "wait", "reason": "unit_slot_blank", "slots": blank}
    if blank and any(s in done for s in blank):
        # An edited slot was emptied: the confirmed idol was not one of the slots still to fill. Never guess.
        return {"action": "stop", "reason": "team_edit_done_slot_vacated", "slots": [s for s in blank if s in done]}
    expected = assign_s(cards, expected)
    states = slot_states(cards, expected, blank_wrong=frozenset(blank) if fill_blank else frozenset())
    unread = [s for s, v in states.items() if v == "unread"]
    if unread:
        return {"action": "stop", "reason": "team_edit_slot_unread", "slots": unread}
    # 2026-10-14: support slots first, P last -- the first support picker surveys every wanted support card before
    # any swap (picker_step), so a missing support card stops with the unit untouched.
    wrong = [s for s in EDIT_ORDER if states.get(s) == "wrong"]
    if not wrong:
        return {"action": "done", "states": states}
    from agent.core.card_inventory import idol_of

    def clash(slot: str):
        """Another slot of the unit holds the wanted card's idol (the game would move her / ask: not sampled)."""
        for other, reading in (cards or {}).items():
            held = _accepted(reading) or _likely_card(reading)
            if other != slot and held is not None and idol_of(held) == idol_of(expected[slot]):
                return other
        return None

    # The first wrong slot in EDIT_ORDER whose wanted idol is not elsewhere in the unit (e.g. a new S card of the idol
    # that is the old P: replace the P first); when every wrong slot clashes, stop.
    slot = next((s for s in wrong if clash(s) is None), wrong[0])
    card = expected[slot]
    if slot in done:
        return {"action": "stop", "reason": "team_edit_slot_repeat", "slot": slot}
    if len(done) >= MAX_EDITS:
        return {"action": "stop", "reason": "team_edit_budget", "edits": len(done)}
    other = clash(slot)
    if other is not None:
        return {"action": "stop", "reason": "team_edit_idol_in_other_slot", "slot": slot, "other": other, "card": card}
    todo = {s: expected[s] for s in wrong if s not in done}
    return {"action": "edit", "slot": slot, "card": card, "states": states, "todo": todo}


def _hits(tiles, card: str) -> list[tuple[int, int]]:
    from agent.core import wing_unit_check as wuc
    hits = []
    for t in tiles or ():
        # Accepted as the card, or the targeted check of wing_unit_check (the wanted card is the tile's clear best).
        if wuc._slot_verdict(t, card) == "match":
            slot = str(t.get("slot") or "")
            try:
                row, col = (int(x) for x in slot[1:].split("c"))
            except ValueError:
                continue
            hits.append((row, col))
    return hits


def phase_for(offset: int, row: int) -> list[str]:
    """Phases whose fixed rect for ``row`` lies inside the tile of a grid read at ``offset`` (with PHASE_MARGIN)."""
    out = []
    for phase, (nominal, size, rows) in PHASES.items():
        if row <= rows and abs(offset - nominal) <= (GRID_TILE - size) // 2 - PHASE_MARGIN:
            out.append(phase)
    return out


def find_tile(tiles: list[Mapping[str, Any]] | None, offset: int | None, thumb_top: int | None, card: str,
              verified=None) -> dict:
    """Locate ``card`` among the picker tiles read on this frame (any scroll position): {"row", "col", "intent",
    "verified", "phase"} or {"reason"}. ``tiles`` are ``card_inventory.recognise(..., "idol_list")["tiles"]`` (slot
    ``r<row>c<col>``, rows counted from the first fully visible row at ``offset``). ``verified`` = the tap intents
    allowed live (default VERIFIED_INTENTS); a hit only on unverified rects -> ``team_edit_tile_not_enabled``.
    ``thumb_top`` is kept for callers of the list-top era; the scroll position comes from ``offset``."""
    verified = VERIFIED_INTENTS if verified is None else verified
    if offset is None:
        return {"reason": "picker_offset_unread"}
    hits = _hits(tiles, card)
    if not hits:
        return {"reason": "card_not_visible", "card": card}
    if len(hits) > 1:
        return {"reason": "team_edit_tile_ambiguous", "card": card, "tiles": hits}
    row, col = hits[0]
    phases = phase_for(offset, row)
    if not phases or not 1 <= col <= GRID_COLS:
        return {"reason": "team_edit_tile_misaligned", "card": card, "tile": [row, col], "offset": offset}
    for phase in phases:
        if tile_intent(row, col, phase) in verified:
            return {"row": row, "col": col, "phase": phase, "intent": tile_intent(row, col, phase), "verified": True}
    return {"reason": "team_edit_tile_not_enabled", "card": card, "tile": [row, col], "offset": offset,
            "intent": tile_intent(row, col, phases[0]), "verified": False}


# ---- picker scroll scan -------------------------------------------------------------------------------
def new_visit(slot: str, survey: Mapping[str, str] | None = None) -> dict:
    """State of one picker visit. ``survey`` = {slot: card} of every wanted card of this picker kind still to place
    (all must be seen on a tappable tile before the first tap of the run); empty = find only the pending card."""
    return {"slot": slot, "survey": dict(survey or {}), "surveyed": not survey, "seen": {}, "blocked": {}, "pos": 0,
            "scrolls": 0, "pending": None, "settle": 0, "prev": None, "last": None, "end": {}, "rewind": False}


def _signature(reading: Mapping[str, Any]) -> tuple:
    ids = tuple(sorted(str(t.get("card_id")) for t in reading.get("picker_tiles") or () if t.get("card_id")))
    return (reading.get("picker_offset"), reading.get("picker_thumb_top"), ids)


def _at_top(reading: Mapping[str, Any]) -> bool:
    top = reading.get("picker_thumb_top")
    offset = reading.get("picker_offset")
    return top is not None and top <= THUMB_TOP_MAX and offset is not None and abs(offset - GRID_TOP_OFFSET) <= 2


def picker_step(visit: dict, reading: Mapping[str, Any], card: str, verified=None) -> dict:
    """One decision on the picker frame ``reading`` (wing_screen.read_picker): {"action": "tap", "intent", ...} |
    {"action": "scroll", "intent", "dir"} | {"action": "wait", "reason"} | {"action": "stop", "reason", ...}.

    Read-only until the tap. A visit opened away from the list top first scrolls up to it. The survey (first サポート
    visit of the run) scrolls down until every wanted support card was seen on a tile Maa may tap; a card missing at
    the list end stops before any swap. Then the pending card is found again (up or down by the remembered scroll
    position) and tapped once. After each wheel step two frames must agree (inertia, <= SETTLE_WAITS), the step must
    keep at least one card of the previous frame visible (no row skipped), a step that moved nothing is the list end,
    and a visit has at most MAX_PICKER_SCROLLS steps. A wheel action that is not verified stops instead of scrolling."""
    verified = VERIFIED_INTENTS if verified is None else verified
    if reading.get("picker_scrollbar") == "loading" or reading.get("picker_offset") is None:
        visit["settle"] += 1
        if visit["settle"] > SETTLE_WAITS:
            return {"action": "stop", "reason": "team_edit_picker_unread"}
        return {"action": "wait", "reason": "picker_loading"}
    sig = _signature(reading)
    if visit["pending"] is not None:
        if visit["last"] != sig:
            visit["last"] = sig
            visit["settle"] += 1
            if visit["settle"] > SETTLE_WAITS:
                return {"action": "stop", "reason": "team_edit_scroll_unsettled"}
            return {"action": "wait", "reason": "picker_scrolling"}
        before = visit["prev"]
        moved = before is not None and before != sig
        if moved and before[2] and sig[2] and not set(before[2]) & set(sig[2]):
            return {"action": "stop", "reason": "team_edit_scroll_overlap_lost", "dir": visit["pending"]}
        if moved:
            visit["pos"] += 1 if visit["pending"] == "down" else -1
        visit["end"] = {visit["pending"]: not moved}
        visit["pending"] = None
    visit["settle"] = 0
    visit["prev"] = visit["last"] = sig
    tiles, offset = reading.get("picker_tiles"), reading.get("picker_offset")
    bottom = reading.get("picker_at_bottom") is True or visit["end"].get("down", False)
    top = _at_top(reading) or visit["end"].get("up", False)

    def scroll(direction: str, why: str) -> dict:
        intent = SCROLL_INTENTS[direction]
        if intent not in verified:
            return {"action": "stop", "reason": "team_edit_scroll_not_enabled", "dir": direction, "why": why}
        if visit["scrolls"] >= MAX_PICKER_SCROLLS:
            return {"action": "stop", "reason": "team_edit_scroll_budget", "scrolls": visit["scrolls"]}
        visit["scrolls"] += 1
        visit["pending"], visit["last"] = direction, None
        return {"action": "scroll", "intent": intent, "dir": direction, "why": why}

    if visit["rewind"] is False:
        visit["rewind"] = "done" if top else True       # opened scrolled: positions count from the list top
    if visit["rewind"] is True:
        if not top:
            return scroll("up", "rewind")
        visit["rewind"], visit["pos"], visit["end"] = "done", 0, {}
    if not visit["surveyed"]:
        for wanted in visit["survey"].values():
            found = find_tile(tiles, offset, None, wanted, verified)
            if found.get("verified") is True:
                visit["seen"].setdefault(wanted, visit["pos"])
                visit["blocked"].pop(wanted, None)
            elif found["reason"] == "team_edit_tile_ambiguous":
                return {"action": "stop", **found, "stage": "survey"}
            elif found["reason"] in ("team_edit_tile_not_enabled", "team_edit_tile_misaligned") \
                    and wanted not in visit["seen"]:
                visit["blocked"][wanted] = found["reason"]
        missing = [c for c in visit["survey"].values() if c not in visit["seen"]]
        if missing and not bottom:
            return scroll("down", "survey")
        if missing:
            cards = {c: visit["blocked"].get(c, "card_not_in_list") for c in missing}
            reason = "team_edit_card_not_in_list" if "card_not_in_list" in cards.values() else "team_edit_tile_not_enabled"
            return {"action": "stop", "reason": reason, "cards": cards, "stage": "survey"}
        visit["surveyed"] = True
    found = find_tile(tiles, offset, None, card, verified)
    if found.get("verified") is True:
        return {"action": "tap", **found}
    if found["reason"] in ("team_edit_tile_ambiguous", "picker_offset_unread"):
        return {"action": "stop", **found, "stage": "find"}
    if found["reason"] in ("team_edit_tile_not_enabled", "team_edit_tile_misaligned"):
        # visible but not tappable at this offset: one step down may land on a tappable phase (bounded)
        if not bottom:
            return scroll("down", found["reason"])
        return {"action": "stop", **found, "stage": "find"}
    seen = visit["seen"].get(card)
    if seen is not None and seen < visit["pos"] and not top:
        return scroll("up", "find")
    if not bottom:
        return scroll("down", "find")
    return {"action": "stop", "reason": "team_edit_card_not_in_list", "cards": {card: "card_not_in_list"},
            "stage": "find"}


# ---- picker sort (2026-10-14, agent/core/picker_sort.py) -----------------------------------------------------------
FALLBACK_SORT_KEY = "Lv順"       # verified cell r1c2 (H2c); used when the attribute key's cell is not
SORT_WAITS = 6                   # frames a popup / re-sorted list may take to show the requested state


def new_sort_state() -> dict:
    """Run-level sort state of the サポート picker (kept across visits: the game saves the sort)."""
    return {"phase": None, "want": None, "cell": None, "changes": 0, "visit_changes": {}, "original": None,
            "current": None, "waits": 0}


def sort_step(sort: dict, visit: dict, reading: Mapping[str, Any], card: str, remaining, attributes,
              verified=None, fixed_key: str | None = None) -> dict | None:
    """Before the scan of a visit: {"action": "sort_open", "intent", "key", "plan"} when a key change is planned and
    every target it needs is verified; else None (the scan proceeds on the current order; the plan is logged in
    ``visit["sort_plan"]``). Only on the first settled frame of a visit at its list top, once per visit and at most
    ``picker_sort.MAX_CHANGES_PER_RUN`` per run."""
    from agent.core import picker_sort as ps
    verified = VERIFIED_INTENTS if verified is None else verified
    if visit.get("sort_plan") is not None or visit["scrolls"] or visit["pending"] is not None:
        return None
    if reading.get("picker_offset") is None or reading.get("picker_scrollbar") == "loading":
        return None
    header = ps.header_from(reading.get("picker_header"))
    if header is None:
        visit["sort_plan"] = {"reason": "header_unread"}
        return None
    if sort["original"] is None and header.ok:
        sort["original"] = {"key": header.key, "direction": header.direction}
    sort["current"] = header.to_dict()
    plan = ps.fixed_plan(header, fixed_key) if fixed_key else ps.plan_sort(header, card, remaining, attributes)
    visit["sort_plan"] = plan.to_dict()
    if not plan.change:
        return None
    cell = ps.KEY_CELLS.get(plan.key)
    need = (SORT_OPEN_INTENT, sort_key_intent(*cell), SORT_APPLY_INTENT) if cell else ()
    missing = [i for i in need if i not in verified] if need else ["no_cell"]
    if missing and not fixed_key and plan.key in ps.KEY_ATTR:
        # H2d: an attribute key whose cell is not verified yet -> the verified 「Lv順」 (high-level cards,
        # the usual SSR targets, on the first page) instead of the current order.
        fallback = ps.fixed_plan(header, FALLBACK_SORT_KEY)
        visit["sort_plan"] = dict(fallback.to_dict(), fallback_from=plan.key)
        if not fallback.change:
            return None
        plan, cell = fallback, ps.KEY_CELLS[FALLBACK_SORT_KEY]
        need = (SORT_OPEN_INTENT, sort_key_intent(*cell), SORT_APPLY_INTENT)
        missing = [i for i in need if i not in verified]
    if missing:
        visit["sort_plan"]["skipped"] = "sort_target_not_enabled:" + ",".join(missing)
        return None
    if sort["visit_changes"].get(visit["slot"], 0) >= ps.MAX_CHANGES_PER_VISIT:
        visit["sort_plan"]["skipped"] = "sort_visit_budget"
        return None
    if sort["changes"] >= ps.MAX_CHANGES_PER_RUN:
        visit["sort_plan"]["skipped"] = "sort_run_budget"
        return None
    sort["changes"] += 1
    sort["visit_changes"][visit["slot"]] = sort["visit_changes"].get(visit["slot"], 0) + 1
    sort.update(phase="open_sent", want=plan.key, cell=cell, waits=0)
    return {"action": "sort_open", "intent": SORT_OPEN_INTENT, "key": plan.key, "plan": plan.to_dict()}


def sort_popup_step(sort: dict | None, popup, share) -> dict:
    """One decision on the 並び替え popup (``popup`` = picker_sort.parse_popup result, ``share(cell)`` = pink share of a
    cell on this frame): {"action": "tap", "intent"} | {"action": "wait", "reason"} | {"action": "stop", "reason"}.

    Only a popup this run opened is answered: tap the wanted key once (bound by the popup's own OCR to exactly its
    cell), then 決定 once when that cell shows selected. A popup that is not the known layout, a key that does not
    show selected, or a popup that stays after 決定 stops (決定 is the popup's only button: never pressed twice)."""
    from agent.core import picker_sort as ps
    if not sort or sort.get("phase") not in ("open_sent", "key_sent", "applied"):
        return {"action": "stop", "reason": "picker_sort_not_automated"}
    phase, want, cell = sort["phase"], sort["want"], sort["cell"]
    if phase == "applied":
        sort["waits"] += 1
        if sort["waits"] > SORT_WAITS:
            return {"action": "stop", "reason": "picker_sort_apply_unconfirmed", "key": want}
        return {"action": "wait", "reason": "picker_sort_closing"}
    bound = ps.popup_cell(popup, want) if popup is not None else None
    ready = popup is not None and popup.title and popup.apply is not None and ts_within_apply(popup.apply)
    if bound != cell or not ready:
        if phase == "open_sent" and (popup is None or not popup.title):
            sort["waits"] += 1
            if sort["waits"] <= SORT_WAITS:
                return {"action": "wait", "reason": "picker_sort_opening"}
        return {"action": "stop", "reason": "picker_sort_popup_unknown", "key": want,
                "popup": popup.to_dict() if popup is not None else None}
    selected = share(cell) >= ps.SELECTED_MIN
    if selected:
        sort.update(phase="applied", waits=0)
        return {"action": "tap", "intent": SORT_APPLY_INTENT, "key": want}
    if phase == "open_sent":
        sort.update(phase="key_sent", waits=0)
        return {"action": "tap", "intent": sort_key_intent(*cell), "key": want}
    sort["waits"] += 1
    if sort["waits"] > SORT_WAITS:
        return {"action": "stop", "reason": "picker_sort_key_unconfirmed", "key": want}
    return {"action": "wait", "reason": "picker_sort_key_pending"}


def ts_within_apply(box) -> bool:
    from agent.core import training_select as ts
    return ts._within(box, ts.SORT_APPLY_RECT)


def sort_verify(sort: dict | None, visit: dict, reading: Mapping[str, Any]) -> dict | None:
    """Back on the picker after 決定: the header must read the requested key (▼降順) on a settled list before the scan
    restarts from scratch on the new order (wait while loading, bounded); None = nothing to verify / verified."""
    if not sort or sort.get("phase") != "applied":
        return None
    from agent.core import picker_sort as ps
    header = ps.header_from(reading.get("picker_header"))
    key = getattr(header, "key", None)
    if key != sort["want"] or getattr(header, "direction", None) != "desc" or reading.get("picker_offset") is None:
        sort["waits"] += 1
        if sort["waits"] > SORT_WAITS:
            return {"action": "stop", "reason": "picker_sort_unverified", "key": sort["want"],
                    "read": header.to_dict() if hasattr(header, "to_dict") else None}
        return {"action": "wait", "reason": "picker_sort_applying"}
    sort.update(phase=None, waits=0, current=header.to_dict())
    fresh = new_visit(visit["slot"], visit["survey"])       # positions of the old order are void
    fresh["sort_plan"] = dict(visit.get("sort_plan") or {}, applied=key)
    fresh["sort_logged"] = visit.get("sort_logged", False)
    visit.clear()
    visit.update(fresh)
    return None
