"""Support identity for W.I.N.G. scoring: which team support sits in which Schedule room (pure; no Maa, no input).

Training in a room with a support vs a support whose support skill matches the room must score
differently. ``wing_score`` already applies masteries per room when ``ScheduleOption.support_ids`` names the supports
and ``TeamProfile`` holds them; this module produces those ids.

Evidence (native 1280x720):

- Schedule room strip (second demo #558): each support is a round face icon ~34 px wide under the room tab
  (``wing_screen.ROOM_PORTRAIT_BAND``), a stamina bar below it.
- 発動中スキル popup (Schedule サポートスキル button, second demo #558 (958,671) -> #559; close verified #563 -> #568):
  per room a header (「ビジュアルレッスン」…) and one entry per support present: the same face icon at ~64 px
  (x centre 327) and that support's active skills (「ユニットマスタリーSP Lv.10」…) or
  「発動中のサポートスキルはありません」. The Visual room of #558 (3 faces) and the Visual section of #559 show the
  same three faces in the same order.
- ユニット編成 / プロデュースユニット確認 (recording 2026-10-11 #98/#239): the six card icons
  (``wing_unit_check.read_unit_cards``) give the team's card ids.

Identity chain: team card ids (formation / unit check) -> each popup entry's skill names select the team card(s)
that own all of them (``label_entries``) -> that entry's face signature is a reference of that card
(``AvatarBook``) -> each Schedule face is matched against the references of the team's supports
(``room_support_ids``). A face is named only with a clear margin; a room is complete only when every face in it is
named, otherwise the room keeps ``support_ids`` empty and the scorer marks it (``support_ids`` /
``support_profiles_incomplete``) and uses its lower bounds.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping

SIG_N = 12                    # samples per axis inside the face circle
SIG_FRACTION = 0.68           # sample radius / circle radius (stays inside the grey ring)
MATCH_MAX = 0.085             # mean abs RGB difference (0..1) accepted as the same face
MATCH_MARGIN = 0.025          # the second-best team support must be at least this much further away
ROOM_AVATAR_TOP_PAD = 0       # circle top = first non-background row of the cluster
POPUP_AVATAR_X = (293, 362)   # 発動中スキル entry face column (#559/#563)
POPUP_AVATAR_D = (54, 72)     # its diameter range
POPUP_Y = (130, 545)          # list viewport of the popup
ROOM_HEADERS = (
    ("lesson_vo", r"(ボ|ホ)ーカルレッスン"), ("lesson_da", r"(ダ|タ)ンスレッスン"), ("lesson_vi", r"(ビ|ヒ)(ジ|シ)ュアルレッスン"),
    ("work_radio", r"ラジオの収録"), ("work_talk", r"トークイベント"), ("work_magazine", r"雑誌の撮影"),
)
NO_SKILL = re.compile(r"発動中の.*ありません")
SKILL_LINE = re.compile(r"^(?P<name>.+?)\s*Lv\.?\s*(?P<lv>\d{1,2})$")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text)))


def circle_signature(image, cx: float, cy: float, radius: float):
    """Face signature: SIG_N x SIG_N RGB samples (each a 3x3-ish box mean) inside SIG_FRACTION of the circle,
    scaled 0..1. Scale independent (the room icon ~34 px and the popup icon ~64 px give comparable vectors)."""
    import numpy as np
    a = np.asarray(image)[:, :, :3].astype(float)
    r = radius * SIG_FRACTION
    half = max(1, int(round(radius / 10)))
    out = []
    for j in range(SIG_N):
        for i in range(SIG_N):
            dx = (i + 0.5) / SIG_N * 2 - 1
            dy = (j + 0.5) / SIG_N * 2 - 1
            if dx * dx + dy * dy > 1:
                continue
            x, y = int(round(cx + dx * r)), int(round(cy + dy * r))
            patch = a[max(0, y - half):y + half + 1, max(0, x - half):x + half + 1]
            out.append(patch.reshape(-1, 3).mean(axis=0) / 255.0)
    return np.asarray(out).reshape(-1)


def distance(a, b) -> float:
    import numpy as np
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if a.shape != b.shape:
        return float("inf")
    return float(np.abs(a - b).mean())


def room_avatar_circles(image) -> list[list[tuple[float, float, float]]] | None:
    """Per Schedule room (fixed order of ``wing_screen.ROOMS``): [(cx, cy, r)] of each face in the strip under the
    tab. Same clustering as ``wing_screen.room_portraits`` (whose counts these lists match)."""
    if image is None:
        return None
    import numpy as np
    from agent.core import wing_screen as wsc
    a = np.asarray(image)[:, :, :3].astype(int)
    y, h = wsc.ROOM_PORTRAIT_BAND
    out = []
    for x, w in wsc.ROOM_SLOT_X:
        strip = a[y:y + h, x + 6:x + w - 6]
        colours, counts = np.unique(strip.reshape(-1, 3), axis=0, return_counts=True)
        bg = colours[counts.argmax()]
        diff = np.abs(strip - bg).sum(axis=2) > 60
        cols = np.nonzero(diff.sum(axis=0) >= 10)[0]
        circles = []
        for c0, c1 in wsc._clusters(cols, min_width=17, gap=2)[:3]:
            rows = np.nonzero(diff[:, c0:c1 + 1].sum(axis=1) >= 3)[0]
            d = c1 - c0 + 1
            top = y + int(rows.min()) + ROOM_AVATAR_TOP_PAD if rows.size else y
            circles.append((x + 6 + (c0 + c1) / 2, top + d / 2, d / 2))
        out.append(circles)
    return out


def room_avatar_signatures(image) -> list[list]:
    circles = room_avatar_circles(image) or []
    return [[circle_signature(image, *c) for c in room] for room in circles]


def popup_avatar_circles(image) -> list[tuple[float, float, float]]:
    """Faces of the 発動中スキル list (x centre ~327): vertical runs of non-white pixels of a face's height."""
    import numpy as np
    a = np.asarray(image)[:, :, :3].astype(int)
    cx = sum(POPUP_AVATAR_X) / 2
    band = a[POPUP_Y[0]:POPUP_Y[1], POPUP_AVATAR_X[0]:POPUP_AVATAR_X[1]]
    # A face row always holds the grey ring on both sides (skin can be near white); gaps between entries are white.
    ink = ((band.sum(axis=2) < 690).sum(axis=1)) >= 4
    out, start = [], None
    for i, flag in enumerate(list(ink) + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            d = i - start
            if POPUP_AVATAR_D[0] <= d <= POPUP_AVATAR_D[1]:
                top = POPUP_Y[0] + start
                if top > POPUP_Y[0] + 2 and POPUP_Y[0] + i < POPUP_Y[1] - 2:     # fully visible only
                    out.append((cx, top + d / 2, d / 2))
            start = None
    return out


def read_active_skills(texts: Iterable, image) -> dict:
    """発動中スキル popup (visible part): {"sections": [{"room", "y"}], "entries": [{"room", "y", "skills": [(name,
    lv)], "none": bool, "signature"}]}. An entry belongs to the last section header above it; entries above the first
    visible header have room None (scrolled; not used)."""
    rows = []
    for text, box in texts or ():
        t = _norm(text)
        if not t:
            continue
        x, y, w, h = box
        rows.append((y + h / 2, x, t))
    rows.sort()
    headers = []
    for yc, x, t in rows:
        for rid, pattern in ROOM_HEADERS:
            if x < 420 and re.search(pattern, t):
                headers.append({"room": rid, "y": yc})
                break
    entries = []
    for cx, cy, r in popup_avatar_circles(image) if image is not None else ():
        room = None
        for h in headers:
            if h["y"] < cy:
                room = h["room"]
        # Skill lines of this entry: blue names at x >= 370 between this face's top and the next face's top.
        entries.append({"room": room, "y": cy, "r": r, "skills": [], "none": False,
                        "signature": circle_signature(image, cx, cy, r)})
    lv_boxes = [(yc, t) for yc, x, t in rows if x >= 850 and re.fullmatch(r"Lv\.?\d{1,2}", t)]
    for i, e in enumerate(entries):
        lo = e["y"] - e["r"] - 4
        hi = entries[i + 1]["y"] - entries[i + 1]["r"] - 4 if i + 1 < len(entries) else POPUP_Y[1]
        nxt = [h["y"] for h in headers if h["y"] > e["y"]]
        if nxt:
            hi = min(hi, nxt[0])
        for yc, x, t in rows:
            if not (lo <= yc < hi and 370 <= x < 720):
                continue
            if NO_SKILL.search(t.replace("サボート", "サポート")):
                e["none"] = True
                continue
            if "行動" in t or "回復" in t or re.fullmatch(r"[\d+%]+", t):
                continue
            m = SKILL_LINE.match(t)
            if m:                                   # name and level read as one text
                e["skills"].append((skill_name(m.group("name")), int(m.group("lv"))))
                continue
            lv = [int(re.sub(r"\D", "", lt)) for ly, lt in lv_boxes if abs(ly - yc) <= 10]
            if len(lv) == 1:
                e["skills"].append((skill_name(t), lv[0]))
    return {"sections": headers, "entries": entries}


def skill_name(text: str) -> str:
    """Skill name with the OCR confusions seen on the popup (#559/#563): 「ュニット」, 「体カ」, 「ボ/ホ」."""
    t = _norm(text)
    t = re.sub(r"^ュ", "ユ", t).replace("体カ", "体力").replace("サボート", "サポート")
    return t


def card_skill_names(card_raw: Mapping[str, Any]) -> set[str]:
    return {skill_name(s.get("id", "")) for s in card_raw.get("support_skills") or () if isinstance(s, Mapping)}


def label_entries(entries: list[Mapping[str, Any]], team_supports: Iterable[str],
                  cards: Mapping[str, Mapping[str, Any]]) -> list[tuple[str, Any]]:
    """[(card_id, signature)] for popup entries whose active skill names belong to exactly one team support card
    (S1–S4 + guest). An entry without skills (「発動中のサポートスキルはありません」) or with an ambiguous owner is not
    labelled."""
    owners = {cid: card_skill_names(cards.get(cid) or {}) for cid in team_supports}
    out = []
    for e in entries:
        names = {skill_name(n) for n, _ in e.get("skills") or ()}
        if not names:
            continue
        match = [cid for cid, have in owners.items() if have and names <= have]
        if len(match) == 1:
            out.append((match[0], e["signature"]))
    return out


class AvatarBook:
    """Per-run face references: card id -> signatures (from labelled popup entries)."""

    def __init__(self, refs: Mapping[str, list] | None = None):
        self.refs: dict[str, list] = {k: list(v) for k, v in (refs or {}).items()}

    def add(self, card: str, signature) -> None:
        self.refs.setdefault(card, [])
        if len(self.refs[card]) < 6:
            self.refs[card].append(signature)

    def match(self, signature, candidates: Iterable[str]) -> tuple[str | None, dict]:
        """(card | None, {card: best distance}) among ``candidates``; None without references for every candidate or
        without a clear margin (a candidate without references could be the face -> never guessed)."""
        cands = list(candidates)
        dists = {c: min((distance(signature, s) for s in self.refs.get(c, ())), default=float("inf"))
                 for c in cands}
        if not cands or any(d == float("inf") for d in dists.values()):
            known = {c: d for c, d in dists.items() if d != float("inf")}
            best = min(known, key=known.get) if known else None
            # Without references for every candidate, only an extremely close face is accepted.
            if best is not None and known[best] <= MATCH_MAX / 2 and all(
                    d - known[best] >= MATCH_MARGIN for c, d in known.items() if c != best):
                return best, dists
            return None, dists
        order = sorted(dists, key=dists.get)
        best = order[0]
        second = dists[order[1]] if len(order) > 1 else float("inf")
        if dists[best] <= MATCH_MAX and second - dists[best] >= MATCH_MARGIN:
            return best, dists
        return None, dists

    def to_dict(self) -> dict:
        return {k: [list(map(float, s)) for s in v] for k, v in self.refs.items()}


def room_support_ids(room_sigs: list[list] | None, book: AvatarBook | None,
                     team_supports: Iterable[str]) -> list[tuple | None] | None:
    """Per room: tuple of support card ids (every face named, no card twice) or None (unknown). None without input."""
    if room_sigs is None or book is None:
        return None
    team = list(team_supports)
    out: list[tuple | None] = []
    for sigs in room_sigs:
        ids = []
        for sig in sigs:
            card, _ = book.match(sig, team)
            if card is None or card in ids:
                ids = None
                break
            ids.append(card)
        out.append(tuple(ids) if ids is not None else None)
    return out


def team_from_unit_cards(cards: Mapping[str, Any] | None) -> dict | None:
    """{"P", "S": [4], "guest"} when all six slots are accepted readings of distinct cards, else None."""
    if not isinstance(cards, Mapping):
        return None
    ids = {}
    for slot in ("P", "S1", "S2", "S3", "S4", "guest"):
        reading = cards.get(slot)
        if not (isinstance(reading, Mapping) and reading.get("status") == "accepted"
                and isinstance(reading.get("card_id"), str)):
            return None
        ids[slot] = reading["card_id"]
    if len(set(ids.values())) != 6:
        return None
    return {"P": ids["P"], "S": [ids[s] for s in ("S1", "S2", "S3", "S4")], "guest": ids["guest"]}
