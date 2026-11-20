"""W.I.N.G. 振り返り skill panel: canonical layout, node slots, info-bar / dialog parsing (pure, no input).

Evidence (native 1280x720 recordings):

* The panel is one honeycomb holding every card's petal around the avatar cluster. Opening 振り返り from the Hub
  shows a **canonical view** (middle zoom, panel centre on the screen centre): the 「SP: N」 labels of two different
  teams (second demo #671/#1516/#1786/#2742, third demo #491/#624) lie on the same lattice within ±2 px. Node taps
  never pan the view (37–45 labels unchanged across taps); only the zoom buttons or a drag do, and a drag is never
  sent. Maa therefore acts only on the canonical view: a frame whose labels are off the lattice is re-entered once
  (back to the Hub and 振り返り again), then stops.
* Label lattice (label centres): x = 639 + 81.9·i, y = 344 + 47.25·k with i + k odd. The hexagon body sits ~35 px
  above its label. Tap rects (44x30, the host samples inside the inner 60 %) are fixed table actions, one per
  observed slot that is not under the left status box, the header, the zoom buttons or the info bar (the info bar,
  x 568–1264 / y 544–706, appears after the first tap and hides the nodes under it).
* Petal owner: the P card's petal is the top-right one (third demo: the P card's Live skill was learned at slot
  (1,-2); both teams show the P idol's avatar at the cluster's top-right). Other petals are ``unknown`` (their S-slot
  order is not observed), which ``wing_sp.plan_sp`` ranks as main S Live skills / ordinary caps.
* Info bar of the selected node (x 575–1260, y 550–700): name (y ~580), description (y 610–690), 必要 SP and the
  button state (習得する / 習得済み / SP不足 / 未解放 / 削除). The description gives the node kind and value:
  「Vocal上限+25」 (cap), 「Vi3倍アピール…」 (Live skill), 「Vocal&Visual35%UP」 with [確率:30%] (passive).
* スキル習得確認: 「SPを20消費してスキルを習得しますか?」 and 「86 ▶ 66」 (OCR may glue them: 「8666」).
* 忘れるスキル選択 (4 rows: name + description, 選択 at x 790–952) and スキル入れ替え確認 (忘れるスキル / 新たに習得するスキル).

Nothing here is a game formula; unparseable text is ``None`` and the caller stops or skips the node.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Iterable, Mapping, Sequence

Texts = Sequence[tuple]

# ---- canonical lattice ------------------------------------------------------------------------------------------
LATTICE_X0, LATTICE_DX = 639.0, 81.9
LATTICE_Y0, LATTICE_DY = 344.0, 47.25
CENTER = (639.0, 391.25)               # label-space centre of the six inner nodes (avatar cluster above it)
LABEL_WIDTH_RANGE = (44, 60)           # middle zoom (labels ~51 px); max zoom ~73, min zoom ~38–41
LATTICE_TOLERANCE = 7                  # px between a read label centre and its lattice point
CANONICAL_MIN_LABELS = 12
CANONICAL_MIN_SHARE = 0.85
HEX_LIFT = 35                          # label centre -> hexagon centre (middle zoom)
TAP_SIZE = (44, 30)
INFO_BAR = (568, 544, 696, 162)
# Slots with a node label in the canonical frames of both teams and a tap rect clear of the UI (see module doc).
SLOTS: tuple[tuple[int, int], ...] = (
    (-1, -6), (3, -6),
    (-2, -5), (2, -5), (4, -5),
    (-3, -4), (-1, -4), (1, -4), (3, -4),
    (-2, -3), (2, -3),
    (-1, -2), (1, -2), (5, -2),
    (4, -1),
    (-5, 0), (-3, 0), (3, 0), (5, 0),
    (-4, 1), (-2, 1), (2, 1), (4, 1),
    (-5, 2), (-3, 2), (3, 2), (5, 2),
    (-4, 3), (4, 3),
    (-5, 4), (-1, 4), (1, 4), (5, 4),
    (-2, 5),
    (-3, 6),
    (-4, 7), (-2, 7),
)
INNER_RING = ((-1, -2), (1, -2), (-2, 1), (2, 1), (-1, 4), (1, 4))
INNER_RING_COSTS = (0, 10, 20)
# Label slots under the seven avatars: never labelled in the canonical view, labelled in any view panned by lattice steps.
CENTRE_SLOTS = ((0, -1), (-1, 0), (1, 0), (0, 1), (-1, 2), (1, 2))
PETALS = ("top_right", "right", "bottom_right", "bottom_left", "left", "top_left")
P_PETAL = "top_right"
PETAL_MARGIN_DEG = 6.0


def slot_name(slot: tuple[int, int]) -> str:
    """Stable intent suffix of a slot: column 0..14 / row 0..16 (``skill_node_c06_r05`` = lattice (1,-2))."""
    i, k = slot
    return f"c{i + 7:02d}_r{k + 8:02d}"


def slot_intent(slot: tuple[int, int]) -> str:
    return "skill_node_" + slot_name(slot)


def slot_label(slot: tuple[int, int]) -> tuple[float, float]:
    i, k = slot
    return LATTICE_X0 + LATTICE_DX * i, LATTICE_Y0 + LATTICE_DY * k


def slot_rect(slot: tuple[int, int]) -> tuple[int, int, int, int]:
    lx, ly = slot_label(slot)
    w, h = TAP_SIZE
    return round(lx - w / 2), round(ly - HEX_LIFT - h / 2), w, h


SLOT_INTENTS = {slot_intent(s): s for s in SLOTS}


def petal_of(slot_or_point) -> str | None:
    """Petal (by angle around the cluster) of a slot or a label point; None near a petal boundary."""
    x, y = slot_label(slot_or_point) if isinstance(slot_or_point[0], int) else slot_or_point
    angle = math.degrees(math.atan2(y - CENTER[1], x - CENTER[0]))      # screen y grows downwards
    # petal centres: top_right -60, right 0, bottom_right 60, bottom_left 120, left 180, top_left -120
    rel = (angle + 90.0) % 360.0                                        # top_right spans rel 0..60
    index, offset = int(rel // 60.0), rel % 60.0
    if offset < PETAL_MARGIN_DEG or offset > 60.0 - PETAL_MARGIN_DEG:
        return None
    return PETALS[index]


def slot_source(slot: tuple[int, int], p_petal: str = P_PETAL) -> str:
    """``wing_sp`` node source: ``p_card`` for the P card's petal, else ``unknown``."""
    return "p_card" if petal_of(slot) == p_petal else "unknown"


# ---- panel topology (2026-10-10) ---------------------------------------------------
# The honeycomb is a flat-top hex grid in "doubled-height" coordinates: lattice (i, k) with i + k odd, neighbours
# (0, ±2) and (±1, ±1). The avatar cluster is centred on (0, 1) (seven avatars = hex distance 0–1); the six inner
# nodes are at hex distance 2. Evidence (19 canonical native frames of two teams, second demo #671–#2752 and third demo
# #386–#624, scratch script over the recorded OCR + pixels): every read 「SP: N」 label with N > 0 satisfies
# N = 10 × hex distance (482/482); every ``open`` node is in the inner ring or next to a learned node (156/165, the 9
# others have an OCR-unread neighbour); no ``unreachable`` node sits next to a learned node or in the inner ring
# (0 cases). A petal is a triangular wedge (1/2/3/4 nodes at distance 2/3/4/5), so a "line" is any chain outward.
# Assumptions (documented, not observed): learning needs one learned neighbour in the same petal (the wiki rule
# 「既に開いたノードに隣接するノードのみ解放」); petals do not connect to each other; a node
# that stays ``unreachable`` next to a learned node is blocked (padlock ☆ / event), never a path step.
PANEL_ORIGIN = (0, 1)
NEIGHBOUR_STEPS = ((0, -2), (0, 2), (1, -1), (1, 1), (-1, -1), (-1, 1))
RING_COST = 10                         # SP per hex distance from the avatar cluster (20/30/40/50)


def hex_distance(slot: Sequence[int], origin: Sequence[int] = PANEL_ORIGIN) -> int:
    di, dk = abs(slot[0] - origin[0]), abs(slot[1] - origin[1])
    return di + max(0, (dk - di) // 2)


def neighbours(slot: Sequence[int]) -> list[tuple[int, int]]:
    return [(slot[0] + a, slot[1] + b) for a, b in NEIGHBOUR_STEPS]


def ring_cost(slot: Sequence[int]) -> int:
    return RING_COST * hex_distance(slot)


def panel_paths(slots: Mapping[str, Mapping], blocked: Iterable[str] = ()) -> dict:
    """Prerequisite chains on the panel: {intent: {"requires": (intents, inner -> outer, unlearned), "cost": SP of the
    chain including the node, "state", "petal", "blocked": bool}} for every slot that can be reached; slots that cannot
    are absent. ``slots`` is ``read_panel()["slots"]`` (possibly merged with earlier frames of the session).

    A chain starts at an ``open`` node (or an inner-ring / learned-neighbour node whose colour is not unreachable/event)
    and runs through unlearned nodes of the same petal. ``blocked`` intents and ``unreachable`` / ``event`` (deep gold)
    nodes next to the cluster or a learned node (padlock) are never path steps nor targets."""
    by_slot = {tuple(sl["slot"]): intent for intent, sl in slots.items() if sl.get("slot") is not None}
    learned = {s for s, intent in by_slot.items() if slots[intent].get("state") == "learned"
               or slots[intent].get("cost") == 0}
    blocked = set(blocked)

    def frontier(slot) -> bool:
        return hex_distance(slot) == 2 or any(n in learned for n in neighbours(slot))

    def usable(slot) -> bool:
        intent = by_slot[slot]
        state = slots[intent].get("state")
        if intent in blocked or slot in learned or slots[intent].get("petal") is None or state == "unknown":
            return False                                # unknown: colour not read, never routed through
        # 2026-10-14 live: the deep-gold 「event」 colour is a gold (high-multiplier / 50 SP) node that is not reachable
        # yet -- c11_r03 「黒百合前で待ち合わせ+」 and c05_r13 turned 「open」 right after a neighbour was learned. It
        # plans like ``unreachable``; next to the cluster or a learned node it stays a padlock.
        return not (state in ("unreachable", "event") and frontier(slot))

    def cost_of(slot) -> int:
        cost = slots[by_slot[slot]].get("cost")
        return cost if isinstance(cost, int) and cost > 0 else ring_cost(slot)

    import heapq
    best: dict = {}
    heap = []
    for slot in by_slot:
        if usable(slot) and (slots[by_slot[slot]].get("state") == "open" or frontier(slot)):
            heapq.heappush(heap, (cost_of(slot), hex_distance(slot), by_slot[slot], slot, ()))
    while heap:
        cost, _, intent, slot, chain = heapq.heappop(heap)
        if intent in best:
            continue
        best[intent] = {"requires": chain, "cost": cost, "state": slots[intent].get("state"),
                        "petal": slots[intent].get("petal")}
        for n in neighbours(slot):
            if n in by_slot and by_slot[n] not in best and usable(n) \
                    and slots[by_slot[n]].get("petal") == slots[intent].get("petal"):
                heapq.heappush(heap, (cost + cost_of(n), hex_distance(n), by_slot[n], n, chain + (intent,)))
    for intent, sl in slots.items():
        slot = tuple(sl["slot"])
        if intent not in best and sl.get("state") in ("unreachable", "event") and slot not in learned and frontier(slot):
            best[intent] = {"requires": None, "cost": None, "state": "unreachable", "petal": sl.get("petal"),
                            "blocked": True}
    for entry in best.values():
        entry.setdefault("blocked", False)
    return best


# ---- panel reading ----------------------------------------------------------------------------------------------
def _clean(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def read_panel(nodes: Iterable[Mapping]) -> dict:
    """Map ``wing_screen.skill_nodes`` labels onto the canonical slots.

    -> {"canonical": bool, "reason", "labels", "on_lattice", "offset": (dx, dy), "width",
        "slots": {intent: {"slot", "cost", "state", "label"}}}. Off-lattice or wrong-zoom frames are not canonical."""
    nodes = [n for n in nodes or () if isinstance(n, Mapping) and n.get("label")]
    out = {"canonical": False, "reason": "", "labels": len(nodes), "on_lattice": 0, "offset": None, "width": None,
           "slots": {}}
    if len(nodes) < CANONICAL_MIN_LABELS:
        out["reason"] = "too_few_labels"
        return out
    widths = sorted(n["label"][2] for n in nodes)
    width = widths[len(widths) // 2]
    out["width"] = width
    if not LABEL_WIDTH_RANGE[0] <= width <= LABEL_WIDTH_RANGE[1]:
        out["reason"] = "zoom_not_canonical"
        return out
    matched, dxs, dys = [], [], []
    for node in nodes:
        x, y, w, h = node["label"]
        cx, cy = x + w / 2, y + h / 2
        i = round((cx - LATTICE_X0) / LATTICE_DX)
        k = round((cy - LATTICE_Y0) / LATTICE_DY)
        lx, ly = slot_label((i, k))
        if (i + k) % 2 == 1 and abs(cx - lx) <= LATTICE_TOLERANCE and abs(cy - ly) <= LATTICE_TOLERANCE:
            matched.append(((i, k), node))
            dxs.append(cx - lx)
            dys.append(cy - ly)
    out["on_lattice"] = len(matched)
    if len(matched) < CANONICAL_MIN_SHARE * len(nodes):
        out["reason"] = "off_lattice"
        return out
    dxs.sort(), dys.sort()
    offset = (dxs[len(dxs) // 2], dys[len(dys) // 2])
    out["offset"] = (round(offset[0], 1), round(offset[1], 1))
    if abs(offset[0]) > 4 or abs(offset[1]) > 4:
        out["reason"] = "lattice_shifted"
        return out
    # The lattice repeats: a view panned by whole lattice steps (third demo #386 during the entry slide, 2 rows
    # lower) also fits it. The six inner-ring nodes around the avatars always cost 20 (0 once learned) on both teams'
    # panels, so at least four of them (OCR misses an 「SP: 0」 now and then) must be read there, none with another cost.
    # 2026-10-14 live: support cards that cut SP costs by 10 (the new UR, 咲耶 Twilight) make an inner node cost 10,
    # so the ring allows 0/10/20 (outer nodes cost >= 30 around it, which still rejects a panned view).
    # 2026-10-29 live (wing5b/wing6, S4 every SP session skipped): OCR missed three 「SP: 0」 ring labels on a learned
    # panel, so three are enough when no label sits on the avatars (a panned view has labels there).
    at = {slot: node for slot, node in matched}
    ring = [at.get(slot) for slot in INNER_RING]
    need = 3 if not any(slot in at for slot in CENTRE_SLOTS) else 4
    if sum(1 for node in ring if node is not None and node.get("cost") in INNER_RING_COSTS) < need or any(
            node is not None and node.get("cost") not in INNER_RING_COSTS for node in ring):
        out["reason"] = "inner_ring_mismatch"
        return out
    slots = {}
    for slot, node in matched:
        intent = slot_intent(slot)
        if slot in SLOTS and intent not in slots:
            slots[intent] = {"slot": list(slot), "cost": node.get("cost"), "state": node.get("state"),
                             "label": list(node["label"]), "petal": petal_of(slot)}
    out["canonical"] = True
    out["slots"] = slots
    return out


# ---- info bar / dialogs ---------------------------------------------------------------------------------------
_ATTR_WORDS = (("Vocal", "Vo"), ("Dance", "Da"), ("Visual", "Vi"), ("メンタル", "Me"))


def normalise(text: str) -> str:
    """OCR clean-up of skill text: glyph confusions seen on the native frames (``Voca|``/``VocaI`` -> ``Vocal``,
    ``Vocall倍`` -> ``Vocal1倍``, ``アピ-ル``/``アピ一ル`` -> ``アピール``, full-width digits/percent)."""
    # NFKC: full-width Latin / Roman numerals (「ボーカルアピールⅤ」 vs 「…V」, live a13 replace confirm), ＋, ＆, ～.
    t = unicodedata.normalize("NFKC", _clean(text))
    t = t.translate(str.maketrans("０１２３４５６７８９％＋．＆", "0123456789%+.&"))  # ＆: ShinyColorsDB names vs OCR
    t = re.sub(r"Voca[l|I1]", "Vocal", t)
    t = re.sub(r"Visua[l|I1]", "Visual", t)
    t = re.sub(r"(Vocal|Dance|Visual)[lI|](?=倍)", r"\g<1>1", t)
    t = re.sub(r"アピ[-ー一ｰ]?ル", "アピール", t)
    return t


def _attributes(prefix: str) -> tuple:
    out = []
    for word, attr in _ATTR_WORDS:
        if word in prefix and attr not in out:
            out.append(attr)
    for short in ("Vo", "Da", "Vi"):
        if re.search(short + r"(?![a-z])", prefix) and short not in out:
            out.append(short)
    return tuple(out)


def classify_skill(name: str | None, description: str | None) -> dict:
    """Node kind/value from the info-bar (or dialog) text. Unknown -> kind None.

    -> {"kind": live_skill|cap_up|passive|None, "attributes", "value", "multiplier", "name"}."""
    name = normalise(name or "")
    desc = normalise(description or "")
    out = {"kind": None, "attributes": (), "value": None, "multiplier": None, "name": name or None}
    m = re.search(r"((?:Vocal|Dance|Visual|メンタル|&|＆)+)上限\+(\d{1,4})", desc)
    if m:
        out.update(kind="cap_up", attributes=_attributes(m.group(1)), value=float(m.group(2)))
        return out
    m = re.search(r"(Vocal|Dance|Visual|Vo|Da|Vi|Excellent)(\d+(?:\.\d+)?)倍アピ", desc)   # tail may be cut off
    if m:
        attrs = () if m.group(1) == "Excellent" else _attributes(m.group(1))
        mult = float(m.group(2))
        out.update(kind="live_skill", attributes=attrs, value=mult, multiplier=mult)
        return out
    if re.search(r"メンタル\d{1,3}%回復", desc):
        out.update(kind="live_skill", attributes=(), value=0.0, multiplier=0.0)   # メンタルキュア: no appeal
        return out
    # Passive: the effect is the node NAME (「Vocal&Visual35%UP」), the description holds [条件][確率][最大].
    m = re.search(r"((?:Vocal|Dance|Visual|&|＆)+)(\d{1,3})%UP", name) or re.search(
        r"((?:Vocal|Dance|Visual|&|＆)+)(\d{1,3})%UP", desc)
    if m and not re.search(r"倍アピール", desc):
        pct = float(m.group(2)) / 100.0
        prob = re.search(r"確率[:：]?(\d{1,3})%", name + desc)
        chance = float(prob.group(1)) / 100.0 if prob else 1.0
        out.update(kind="passive", attributes=_attributes(m.group(1)), value=round(pct * chance, 4))
        cond = re.search(r"条件[:：]?([^\]］\[]+)", name + desc)
        out["condition"] = cond.group(1) if cond else None
        return out
    return out


def condition_met(condition: str | None, p_idol: str | None = None, members=()) -> bool | None:
    """Can a passive's [条件:…] be met in a W.I.N.G. audition (only the P idol performs)? False for the Fes-type
    conditions the wikis call never met in a Produce (最大メンタル thresholds of 1000+ — the
    idol's Me stays near its cap of 500–700 — ポジション担当, unit-wide 全員がライブに参加) and for 「Xがライブに参加」 unless X
    is the P idol; True for turn / mental-share / buff conditions; None = no condition. Unknown P idol -> False."""
    if not condition:
        return None
    c = normalise(condition)
    m = re.search(r"最大メンタル(\d{3,5})以上", c)
    if m and int(m.group(1)) >= 1000:
        return False
    if "ポジション" in c or "全員" in c:
        return False
    m = re.search(r"(.+?)がライブに参加", c)
    if m:
        # 育成の指針: in a Produce the P idol and the four Active S idols (not the guest)
        # satisfy 「Xがライブに参加」; ``members`` lists the S idols' names.
        who = re.sub(r"^.*?(以降|以前)", "", m.group(1))
        names = [normalise(n) for n in (p_idol, *members) if n]
        return any(n.endswith(who) or who.endswith(n) for n in names)
    return True


INFO_NAME_Y = (562, 605)
INFO_DESC_Y = (606, 700)
INFO_X = (580, 1075)


def parse_info_text(texts: Texts) -> dict | None:
    """Name/description of the info bar (texts of the ``Review.InfoText`` reader or the whole frame). None when no
    text is inside the bar's text area."""
    name_parts, desc_parts = [], []
    for text, box in sorted(((t, tuple(b)) for t, b in texts), key=lambda tb: (tb[1][1], tb[1][0])):
        x, y, w, h = box
        cy = y + h / 2
        if not INFO_X[0] <= x <= INFO_X[1] or w < 12:
            continue
        t = _clean(text)
        if re.fullmatch(r"(Plus|Link|SP|必要|\d{1,3}|[.…·:：]+|SP[:：]?\d{1,3}|上限.?|Vo\.?|Da\.?|Vi\.?)", t):
            continue
        if INFO_NAME_Y[0] <= cy < INFO_NAME_Y[1] and 620 <= x <= 935 and len(t) >= 2:   # left of Plus/SP badges
            name_parts.append(t)
        elif INFO_DESC_Y[0] <= cy < INFO_DESC_Y[1]:
            desc_parts.append(t)
    if not name_parts and not desc_parts:
        return None
    name = "".join(name_parts) or None
    desc = "".join(desc_parts) or None
    return {"name": name, "description": desc, **{k: v for k, v in classify_skill(name, desc).items() if k != "name"}}


def parse_learn_confirm(texts: Texts, *, sp: int | None = None) -> dict:
    """スキル習得確認: ``cost`` (SPをN消費), ``sp_before``/``sp_after`` (「86 ▶ 66」, glued 「8666」 split by cost) and
    the skill ``name``. Unread values are None."""
    out = {"cost": None, "sp_before": None, "sp_after": None, "name": None}
    for text, box in texts:
        t = normalise(text)
        m = re.search(r"SPを(\d{1,3})消費", t)
        if m:
            out["cost"] = int(m.group(1))
        if 405 <= box[0] <= 470 and 275 <= box[1] <= 305 and len(t) >= 2:
            out["name"] = t
    numbers = [(box[0], normalise(text)) for text, box in texts if 400 <= box[1] <= 440 and 600 <= box[0] <= 860]
    digits = "".join(re.sub(r"[^\d▶>»]", "", t) for _, t in sorted(numbers))
    parts = [p for p in re.split(r"[▶>»]+", digits) if p]
    if len(parts) == 2:
        out["sp_before"], out["sp_after"] = int(parts[0]), int(parts[1])
    elif len(parts) == 1 and out["cost"] is not None:
        glued = parts[0]
        for cut in range(1, len(glued)):
            before, after = int(glued[:cut]), int(glued[cut:])
            if before - after == out["cost"] and (sp is None or before == sp):
                out["sp_before"], out["sp_after"] = before, after
                break
    return out


FORGET_ROWS = ((60, 195), (196, 332), (333, 470), (471, 610))   # y bands of the 4 skill rows (#428, #1295, #744)


def parse_forget_rows(texts: Texts) -> list[dict] | None:
    """忘れるスキル選択: 4 rows {"row", "name", "description", kind/multiplier/attributes}; None unless all 4 rows
    have a name and the four 選択 buttons are read."""
    buttons = [b for t, b in texts if _clean(t) == "選択" and b[0] >= 780]
    rows = []
    for index, (lo, hi) in enumerate(FORGET_ROWS, 1):
        name = [t for t, b in texts if 340 <= b[0] <= 480 and lo <= b[1] + b[3] / 2 < lo + 70 and len(_clean(t)) >= 2
                and _clean(t) != "選択"]
        desc = [t for t, b in sorted(texts, key=lambda tb: (tb[1][1], tb[1][0]))
                if 300 <= b[0] <= 330 and lo + 40 <= b[1] + b[3] / 2 < hi]
        info = classify_skill(" ".join(name), "".join(desc))
        rows.append({"row": index, "name": normalise("".join(name)) or None, "description": normalise("".join(desc)),
                     "kind": info["kind"], "attributes": info["attributes"], "multiplier": info["multiplier"]})
    if len(buttons) != 4 or any(r["name"] is None for r in rows):
        return None
    return rows


_KANA_FOLD = str.maketrans("ポホペヘプフピヒパハ", "ボボベベブブビビババ")


def same_skill(a: str | None, b: str | None) -> bool:
    """Skill-name equality tolerant to OCR's dakuten/handakuten swaps (ボ/ポ/ホ) and a dropped trailing 「I」."""
    if not a or not b:
        return False
    # 2026-10-14 live a8: 「にゃんにゃんにゃ～ん♡」 in the 習得確認 dialog failed the name check 20 times; OCR reads the
    # decorative marks and the wave dash unreliably. Compare without them (and with ー/～/〜/~ as one long mark).
    fold = lambda s: re.sub(r"[IⅠl|]+$", "", re.sub(r"[♡♥❤♪♫☆★○●◇◆・!！?？「」『』\s]", "",  # noqa: E731
                                                    re.sub(r"[～〜~ｰ一-]", "ー", normalise(s))).translate(_KANA_FOLD))
    return fold(a) == fold(b)


def parse_replace_dialog(texts: Texts) -> dict:
    """スキル入れ替え確認: the skill to forget, the new skill (names; None when unread) and the OCR boxes of its OK /
    キャンセル buttons. The dialog grows with the description length (second demo #744: OK x 652–763 / y 524–576; the
    third demo's taller dialog put OK lower, around y ~616), so the caller clicks only when the read OK box lies inside a
    measured table rect."""
    forget = [normalise(t) for t, b in texts if 455 <= b[0] <= 620 and 205 <= b[1] <= 240]
    new = [normalise(t) for t, b in texts if 455 <= b[0] <= 620 and 330 <= b[1] <= 360]
    ok = [tuple(b) for t, b in texts if _clean(t) in ("OK", "0K", "ОК") and 440 <= b[1] <= 640]
    # the ForgetSelect page's own キャンセル (x 570, y 644 on #744) sits below the dialog
    cancel = [tuple(b) for t, b in texts if _clean(t) == "キャンセル" and 440 <= b[1] <= 615 and b[0] < 640]
    return {"forget": forget[0] if len(forget) == 1 else None, "new": new[0] if len(new) == 1 else None,
            "ok_box": ok[0] if len(ok) == 1 else None, "cancel_box": cancel[0] if len(cancel) == 1 else None}


def box_inside(box, rect, inner: float = 0.0) -> bool:
    """Centre of the OCR ``box`` inside ``rect`` (shrunk by ``inner`` of its size on each side)."""
    if not box or not rect:
        return False
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    x, y, w, h = rect
    return x + w * inner <= cx <= x + w * (1 - inner) and y + h * inner <= cy <= y + h * (1 - inner)


# ---- forget rule (「忘记技能四个初始技能优先从回复5%技能开始，逐个替换，然后低倍率刀，总之就是换掉最弱的技能，
# 当四个tab已经满了且当前技能盘新技能评分都低就不用执着于学技能了」) ------------------------------------------------------------
# The game starts every Produce with four basic Live skills (third demo #428): ボーカル/ダンス/ビジュアルアピールI (×1, one per
# attribute) and メンタルキュアI (メンタル5%回復, no appeal). Strength of a held or new Live skill = appeal multiplier × the
# weight of its attribute for this run: the primary attribute 1.0, another attribute its stat relative to the primary
# (``stats`` known) or ``OFFATTR_WEIGHT``; Excellent/All skills 1.0. A recovery skill (no appeal) has strength 0 and is
# always the first to go; then the weakest appeal skill. A forget happens only when the new skill is stronger than the
# victim by ``FORGET_MARGIN`` (relative) — otherwise the learn is cancelled and the SP goes to caps / passives.
OFFATTR_WEIGHT = 0.5
FORGET_MARGIN = 0.10
OFFATTR_MIN_WEIGHT = 0.25


def attribute_weight(attributes, primary: str, stats=None, offattr_weight: float = OFFATTR_WEIGHT) -> float:
    attrs = tuple(a for a in (attributes or ()) if a in ("Vo", "Da", "Vi"))
    if not attrs or primary in attrs:
        return 1.0
    if isinstance(stats, Mapping):
        own = stats.get(primary)
        others = [stats.get(a) for a in attrs]
        if isinstance(own, (int, float)) and own > 0 and all(isinstance(v, (int, float)) for v in others):
            return round(max(OFFATTR_MIN_WEIGHT, min(1.0, max(others) / own)), 4)
    return offattr_weight


def skill_strength(skill: Mapping, primary: str, stats=None, offattr_weight: float = OFFATTR_WEIGHT) -> float | None:
    """Comparable strength of a Live skill row/node ({kind, multiplier, attributes}); None when unread."""
    mult = skill.get("multiplier")
    if mult is None:
        return None
    if skill.get("kind") == "live_skill" and float(mult) == 0.0:
        return 0.0                                       # メンタルキュア & co.: no appeal
    return round(float(mult) * attribute_weight(skill.get("attributes"), primary, stats, offattr_weight), 4)


def is_recovery(skill: Mapping) -> bool:
    return skill.get("multiplier") == 0.0 or bool(re.search(r"回復", str(skill.get("description") or "")))


def choose_forget(rows: Sequence[Mapping], new_multiplier: float | None, primary: str, *,
                  new_attributes=(), stats=None, margin: float = FORGET_MARGIN,
                  offattr_weight: float = OFFATTR_WEIGHT) -> dict:
    """Which current Live skill to forget for a new one: recovery skills first, then the weakest appeal skill (strength
    = multiplier × attribute weight); only when the new skill beats it by ``margin``. Any unread row -> no choice."""
    if new_multiplier is None:
        return {"row": None, "reason": "new_skill_unread"}
    if any(r.get("multiplier") is None for r in rows):
        return {"row": None, "reason": "current_skill_unread"}
    new = skill_strength({"kind": "live_skill", "multiplier": new_multiplier, "attributes": new_attributes},
                         primary, stats, offattr_weight)
    scored = [(0 if is_recovery(r) else 1, skill_strength(r, primary, stats, offattr_weight) or 0.0, r["row"], r)
              for r in rows]
    rank, strength, _, victim = min(scored, key=lambda t: t[:3])
    if new <= 0 or new <= strength * (1 + margin) or (rank == 1 and new - strength < 1e-9):
        return {"row": None, "reason": "no_weaker_skill", "new_strength": new, "weakest": strength,
                "weakest_name": victim.get("name")}
    return {"row": victim["row"], "name": victim.get("name"), "multiplier": victim["multiplier"],
            "strength": strength, "new_strength": new,
            "reason": "recovery_first" if rank == 0 else "weakest_appeal"}
