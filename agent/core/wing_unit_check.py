"""ユニット編成 team fingerprint: which cards sit in the P / S1–S4 / guest slots, compared with the configured team.

「maa要自己选择到这，编队也得自己选择」 — Maa reaches the configured unit itself and proves it holds the
configured team before 次へ, instead of relying on the user's ``team_confirmed``. No input, no device, no network.

Card identity reuses ``agent.core.card_inventory`` (local ShinyColorsDB icons, ``ui/game-assets``; private): the
``unit_formation`` layout boxes (P (404,250), S1–S4 x 538/686/834/982, guest 1132; 104 px) were measured on the
W.I.N.G. demo's ユニット編成 frames and give accepted readings for most slots (third demo #233: 5 of 6; the S3 card
with the round badge and the guest are sometimes ``verify_failed``).

A rejected slot is re-checked **only against the expected card** (targeted check): the expected card must be the
tile's overall best coarse match, coarse >= ``TARGET_MIN_SCORE``, ``TARGET_MIN_MARGIN`` above every other idol, its
finer colour score >= ``TARGET_MIN_VERIFY`` and ``TARGET_MIN_VARIANT_GAP`` above the idol's other variants. That never turns a different card into a match; it only resolves tiles
the open-set matcher could not name. S slots are compared as a set (the in-game S order need not follow the
configuration). Verdicts: ``match`` (every expected slot matched), ``mismatch`` (some slot holds a different card —
the run stops, the team is never edited), ``uncertain`` (anything else; the run stops unless the user confirmed).
"""
from __future__ import annotations

from typing import Mapping

TARGET_MIN_SCORE = 0.78
TARGET_MIN_VERIFY = 0.50        # S3/guest tiles with the round badge read 0.53–0.55 (third demo #233, second #211)
TARGET_MIN_MARGIN = 0.06        # coarse gap to the best card of another idol (card_inventory min_margin)
TARGET_MIN_VARIANT_GAP = 0.05   # fine gap to the next variant of the same idol
# Team edit only (wing_team_edit, start mode): pale card art under the slot overlays (stars, Lv, badges) can stay below
# TARGET_MIN_SCORE although the wanted card is the tile's best match; the finer colour score then decides (live
# 2026-10-25 unit20 S4 2040080010 霧・霧・奇・譚: coarse 0.763, fine 0.650 vs <= 0.575 for every other candidate).
EDIT_MIN_SCORE = 0.74
EDIT_MIN_VERIFY = 0.60
EDIT_MIN_FINE_GAP = 0.06        # fine gap to every other candidate, any idol
CANDIDATES = 6
BLANK_STD_MAX = 25.0
BLANK_GREY_SPREAD = 12.0
SLOTS = ("P", "S1", "S2", "S3", "S4", "guest")
_INDEX = None


def card_index():
    """Lazily loaded reference index (about 3 s the first time; private assets). None when not available."""
    global _INDEX
    if _INDEX is None:
        from agent.core import card_inventory as ci
        try:
            _INDEX = ci.CardIndex.from_assets()
        except (OSError, ValueError):
            _INDEX = False
    return _INDEX or None


def read_unit_cards(image, index=None, layout: str = "unit_formation") -> dict | None:
    """{slot: {"card_id", "status", "reason", "score", "candidates": [[id, coarse, fine], ...]}} of a 1280x720 RGB
    frame (numpy array or PIL image); None without a reference index or a frame. ``layout``: a card_inventory boxes
    layout (``unit_formation`` = ユニット編成 / ユニット確認; ``resume_dialog`` = the P portrait of プロデュース再開)."""
    if image is None:
        return None
    index = index if index is not None else card_index()
    if index is None:
        return None
    import numpy as np
    from PIL import Image
    from agent.core import card_inventory as ci

    frame = image if isinstance(image, Image.Image) else Image.fromarray(np.asarray(image)[:, :, :3].astype("uint8"))
    try:
        result = ci.recognise(frame, index, layout)
    except ValueError:
        return None
    frame = frame.convert("RGB")
    out = {}
    for tile in result["tiles"]:
        x, y, w, h = tile["box"]
        crop = frame.crop((x, y, x + w, y + h))
        row = index.coarse_scores([crop])[0]
        fine = ci._normalise(ci._vector(crop, ci.FINE_CROP, ci.FINE)[None])[0]
        order = [int(i) for i in np.argsort(row)[::-1][:CANDIDATES]]
        # Flat tile (no card art): an empty slot or the formation still loading (recording 2026-10-11 #85: every
        # channel std <= 17 while loading; card tiles are far above). Never read as a card.
        pixels = np.asarray(crop).reshape(-1, 3).astype(np.int16)
        # Or the grey silhouette of an empty slot (live 2026-10-14 unit20 S4: colour spread p95 = 2; cards >= 105).
        grey = float(np.percentile(pixels.max(axis=1) - pixels.min(axis=1), 95)) <= BLANK_GREY_SPREAD
        blank = bool(pixels.std(axis=0).max() < BLANK_STD_MAX) or grey
        out[tile["slot"]] = {"card_id": tile["card_id"], "status": tile["status"], "reason": tile["reason"],
                             "score": tile["score"], "blank": blank,
                             "candidates": [[index.ids[i], round(float(row[i]), 4), round(float(fine @ index.fine[i]), 4)]
                                            for i in order]}
    return out


def _idol(card_id):
    from agent.core.card_inventory import idol_of
    return idol_of(card_id)


def _slot_verdict(reading: Mapping | None, expected: str) -> str:
    if not isinstance(reading, Mapping):
        return "uncertain"
    if reading.get("status") == "accepted":
        return "match" if reading.get("card_id") == expected else "mismatch"
    cands = [c for c in reading.get("candidates") or () if isinstance(c, (list, tuple)) and len(c) == 3]
    if not cands or cands[0][0] != expected:
        return "uncertain"
    _, coarse, fine = cands[0]
    others = [c[1] for c in cands[1:] if _idol(c[0]) != _idol(expected)]
    variants = [c[2] for c in cands[1:] if _idol(c[0]) == _idol(expected)]
    if (coarse >= TARGET_MIN_SCORE and fine >= TARGET_MIN_VERIFY
            and (not others or coarse - max(others) >= TARGET_MIN_MARGIN)
            and (not variants or fine - max(variants) >= TARGET_MIN_VARIANT_GAP)):
        return "match"
    return "uncertain"


def edit_slot_verdict(reading: Mapping | None, expected: str) -> str:
    """``_slot_verdict`` plus the team-edit fallback: the expected card is the best coarse match (>= EDIT_MIN_SCORE)
    and leads every other candidate on the finer colour score by EDIT_MIN_FINE_GAP. Used only while Maa edits the
    configured unit; resume checks and the picker search stay strict."""
    verdict = _slot_verdict(reading, expected)
    if verdict != "uncertain" or not isinstance(reading, Mapping) or reading.get("blank"):
        return verdict
    cands = [c for c in reading.get("candidates") or () if isinstance(c, (list, tuple)) and len(c) == 3]
    if not cands or cands[0][0] != expected:
        return "uncertain"
    _, coarse, fine = cands[0]
    rest = [c[2] for c in cands[1:]]
    if coarse >= EDIT_MIN_SCORE and fine >= EDIT_MIN_VERIFY and (not rest or fine - max(rest) >= EDIT_MIN_FINE_GAP):
        return "match"
    return "uncertain"


def slot_verdict(reading: Mapping | None, expected: str | None) -> str:
    """Public strict per-slot check (accepted exact id, or the targeted check against ``expected``): match / mismatch /
    uncertain. A blank tile or a missing expectation is uncertain."""
    if not expected or not isinstance(reading, Mapping) or reading.get("blank"):
        return "uncertain"
    return _slot_verdict(reading, expected)


def compare(cards: Mapping | None, expected: Mapping[str, str | None], *, editing: bool = False) -> dict:
    """Verdict of the read unit against ``expected`` ({"P": id, "S": [ids], "guest": id|None}). ``editing``: the
    run edits this unit itself (team edit), so rejected slots use ``edit_slot_verdict``."""
    if not isinstance(cards, Mapping):
        return {"verdict": "uncertain", "reason": "unit_cards_unread", "slots": {}}
    check = edit_slot_verdict if editing else _slot_verdict
    slots = {}
    p = expected.get("P")
    if p:
        slots["P"] = check(cards.get("P"), p)
    s_ids = [c for c in expected.get("S") or () if c]
    used: set = set()
    for slot in ("S1", "S2", "S3", "S4")[:len(s_ids)] if s_ids else ():
        reading = cards.get(slot)
        verdict = "uncertain"
        if isinstance(reading, Mapping) and reading.get("status") == "accepted":
            card = reading.get("card_id")
            verdict = "match" if card in s_ids and card not in used else "mismatch"
            if verdict == "match":
                used.add(card)
        else:
            for card in s_ids:
                if card not in used and check(reading, card) == "match":
                    verdict = "match"
                    used.add(card)
                    break
        slots[slot] = verdict
    guest = expected.get("guest")
    if guest:
        slots["guest"] = check(cards.get("guest"), guest)
    if not slots:
        return {"verdict": "uncertain", "reason": "team_not_configured", "slots": slots}
    values = set(slots.values())
    verdict = "mismatch" if "mismatch" in values else "match" if values == {"match"} else "uncertain"
    return {"verdict": verdict, "reason": verdict, "slots": slots}
