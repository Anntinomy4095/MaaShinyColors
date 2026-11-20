"""W.I.N.G. screen classifier and state readers (navigation framework, offline-first; no input, no Maa required).

The Pipeline is the single source of truth for recognition: anchors (``SC.Wing.Anchor.*``), readers
(``SC.Wing.Read.*``) and screen guards (``SC.Wing.Screen.*``) live in ``wing_draft.json`` and ``wing.json``; the
dispatch order is the ``next`` list of ``SC.Wing.Nav.Loop`` (first handler whose guard hits wins, exactly as Maa
walks a ``next`` list). This module evaluates those nodes through a pluggable *backend*:

* ``BoxBackend`` emulates OCR from pre-computed whole-frame OCR boxes (``[text, [x, y, w, h]]``): a box belongs to a
  ROI when its centre is inside the ROI or at least half of its area is; ``expected`` regexes are searched in each
  box text after ``replace``. ColorMatch is computed exactly from the RGB image (pixels inside ``lower..upper``).
  Fast, used by unit tests and the quick demo replay.
* ``tools/wing_demo_replay.py --backend maa`` runs the same nodes through the real MaaFramework OCR (ROI crop),
  which is the authoritative replay.

Readers are pure parsers over ``(text, box)`` lists so the runtime (Maa ``context.run_recognition`` on a reader node)
and the offline replay share one implementation. A value that cannot be read is ``None``; nothing is guessed.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = ROOT / "assets" / "resource" / "base" / "pipeline"
PIPELINE_FILES = (PIPELINE_DIR / "wing_draft.json", PIPELINE_DIR / "wing.json")
CANVAS = (1280, 720)
LOOP = "SC.Wing.Nav.Loop"
# Never-run node holding the classification priority (overlays before the screens under them). SC.Wing.Nav.Loop lists
# the same handlers most-frequent first: each handler hits only for the screen the frame was classified as, so the
# loop's order changes how many handlers are asked, not which one runs.
PRIORITY = "SC.Wing.Nav.Priority"
SCREEN_PREFIX = "SC.Wing.Screen."
HANDLER_PREFIX = "SC.Wing.On."

Box = tuple  # (x, y, w, h)
Texts = Sequence[tuple]  # [(text, (x, y, w, h)), ...]


# ---------------------------------------------------------------------------------------------------------------
# Pipeline loading
# ---------------------------------------------------------------------------------------------------------------
def load_nodes(paths: Iterable[Path] = PIPELINE_FILES) -> dict:
    nodes: dict = {}
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for name, node in data.items():
            if name in nodes:
                raise ValueError(f"duplicate W.I.N.G. node {name} ({Path(path).name})")
            nodes[name] = node
    return nodes


def dispatch_order(nodes: Mapping[str, Any], loop: str = LOOP) -> list[tuple[str, str]]:
    """``[(handler, screen), ...]`` in ``SC.Wing.Nav.Loop`` order. A handler names its screen by its guard reference
    or, without one (2026-10-25: decision and wait handlers rely on the classification; the Click node keeps the native
    guard), by its own name ``SC.Wing.On.<screen>``; the screen's guard node must exist either way."""
    order = []
    source = nodes[PRIORITY] if loop == LOOP and PRIORITY in nodes else nodes[loop]
    for handler in source["next"]:
        if not handler.startswith(HANDLER_PREFIX):
            continue                    # SC.Wing.Halted (stopped runtime) is not a screen handler
        refs = [r for r in nodes[handler].get("all_of", []) if isinstance(r, str) and r.startswith(SCREEN_PREFIX)]
        if len(refs) > 1:
            raise ValueError(f"{handler}: guard must reference at most one SC.Wing.Screen.* node")
        screen = refs[0][len(SCREEN_PREFIX):] if refs else handler[len(HANDLER_PREFIX):]
        if SCREEN_PREFIX + screen not in nodes:
            raise ValueError(f"{handler}: no {SCREEN_PREFIX}{screen} guard")
        order.append((handler, screen))
    return order


# ---------------------------------------------------------------------------------------------------------------
# Frames and backends
# ---------------------------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Frame:
    """One 1280x720 frame: whole-frame OCR boxes and (optionally, lazily) the RGB pixels for ColorMatch."""
    texts: Sequence[tuple] = ()
    image_loader: Callable[[], Any] | None = None
    key: str = ""
    _image: Any = None

    @property
    def image(self):
        if self._image is None and self.image_loader is not None:
            self._image = self.image_loader()
        return self._image


def box_in_roi(box: Sequence[int], roi: Sequence[int]) -> bool:
    x, y, w, h = box
    rx, ry, rw, rh = roi
    if w <= 0 or h <= 0:
        return False
    cx, cy = x + w / 2, y + h / 2
    if rx <= cx < rx + rw and ry <= cy < ry + rh:
        return True
    ix = max(0, min(x + w, rx + rw) - max(x, rx))
    iy = max(0, min(y + h, ry + rh) - max(y, ry))
    return ix * iy >= 0.5 * w * h


def apply_replace(text: str, replace: Iterable) -> str:
    for pattern, repl in replace or ():
        text = re.sub(pattern, repl, text)
    return text


def color_count(image, roi: Sequence[int], lower: Sequence[int], upper: Sequence[int]) -> int:
    """Pixels of the RGB ``image`` (H x W x 3 uint8) inside ``roi`` whose channels are all within lower..upper."""
    import numpy as np
    x, y, w, h = roi
    patch = np.asarray(image)[y:y + h, x:x + w, :3]
    lo = np.array(lower, dtype=np.uint8)
    hi = np.array(upper, dtype=np.uint8)
    return int(np.all((patch >= lo) & (patch <= hi), axis=2).sum())


class BoxBackend:
    """OCR emulation over whole-frame OCR boxes + exact ColorMatch on the RGB image. Never sends input."""
    name = "boxes"

    def ocr(self, node: Mapping[str, Any], frame: Frame) -> list[tuple]:
        roi = node.get("roi", [0, 0, *CANVAS])
        out = []
        for text, box in frame.texts:
            if text and box_in_roi(box, roi):
                out.append((apply_replace(text, node.get("replace")), tuple(box)))
        return out

    def ocr_hit(self, node: Mapping[str, Any], frame: Frame) -> bool:
        expected = node.get("expected") or []
        texts = self.ocr(node, frame)
        if not expected:
            return bool(texts)
        return any(re.search(p, t) for t, _ in texts for p in expected)

    def color_hit(self, node: Mapping[str, Any], frame: Frame) -> bool | None:
        if frame.image is None:
            return None
        return color_count(frame.image, node["roi"], node["lower"], node["upper"]) >= int(node.get("count", 1))


# ---------------------------------------------------------------------------------------------------------------
# Recognition evaluation
# ---------------------------------------------------------------------------------------------------------------
class Evaluator:
    """Evaluates W.I.N.G. recognition nodes on one frame with per-frame caching.

    ``customs`` maps a custom recognition name to ``fn(param, frame) -> bool`` (offline stand-ins for the Agent's
    ``SC.Wing.State`` / ``SC.Wing.Decide``); an unknown custom recognition is a miss.
    """

    def __init__(self, nodes: Mapping[str, Any], backend=None, customs: Mapping[str, Callable] | None = None):
        self.nodes = nodes
        self.backend = backend or BoxBackend()
        self.customs = dict(customs or {})
        self._cache: dict = {}
        self._frame_key = None

    def _reset(self, frame: Frame):
        if self._frame_key is not frame:
            self._cache = {}
            self._frame_key = frame

    def hit(self, ref, frame: Frame) -> bool:
        self._reset(frame)
        key = ref if isinstance(ref, str) else json.dumps(ref, sort_keys=True, ensure_ascii=False)
        if key not in self._cache:
            node = self.nodes[ref] if isinstance(ref, str) else ref
            self._cache[key] = bool(self._eval(node, frame))
        return self._cache[key]

    def _eval(self, node: Mapping[str, Any], frame: Frame) -> bool:
        kind = node.get("recognition", "DirectHit")
        if kind == "DirectHit":
            return True
        if kind == "OCR":
            return self.backend.ocr_hit(node, frame)
        if kind == "ColorMatch":
            return bool(self.backend.color_hit(node, frame))
        if kind == "And":
            return all(self.hit(r, frame) for r in node["all_of"])
        if kind == "Or":
            return any(self.hit(r, frame) for r in node["any_of"])
        if kind == "Custom":
            fn = self.customs.get(node.get("custom_recognition"))
            return bool(fn(node.get("custom_recognition_param", {}), frame)) if fn else False
        raise ValueError(f"unsupported recognition {kind}")

    def texts(self, name: str, frame: Frame) -> list[tuple]:
        """Reader texts of an OCR node (expected ignored)."""
        return self.backend.ocr(self.nodes[name], frame)


@dataclasses.dataclass(frozen=True)
class Classification:
    screen: str | None           # first screen in dispatch order whose guard hits; None = unknown (wait, never act)
    handler: str | None
    hits: tuple = ()             # every screen whose guard hits (for confusion analysis)


class ScreenClassifier:
    def __init__(self, nodes: Mapping[str, Any] | None = None, backend=None, customs=None):
        self.nodes = nodes if nodes is not None else load_nodes()
        self.order = dispatch_order(self.nodes)
        self.evaluator = Evaluator(self.nodes, backend, customs)

    def screen_hit(self, screen: str, frame: Frame) -> bool:
        return self.evaluator.hit(SCREEN_PREFIX + screen, frame)

    def classify(self, frame: Frame, *, all_hits: bool = False) -> Classification:
        first = None
        hits = []
        for handler, screen in self.order:
            if self.screen_hit(screen, frame):
                hits.append(screen)
                if first is None:
                    first = (handler, screen)
                    if not all_hits:
                        break
        if first is None:
            return Classification(None, None, tuple(hits))
        return Classification(first[1], first[0], tuple(hits))


# ---------------------------------------------------------------------------------------------------------------
# Readers (pure parsers)
# ---------------------------------------------------------------------------------------------------------------
STAT_ORDER = ("Vo", "Da", "Vi", "Me")
RANK_LETTERS = "FEDCBAS"
_STAT_CELL = re.compile(r"^\s*([A-Z区])?\s*/\s*(\d{2,4})\s*$")
_NUMBER = re.compile(r"^\+?\s*([\d,]{1,9})\s*人?$")
ROOMS = (  # schedule room labels left -> right (W.I.N.G. layout observed 2026-10-05; label text regexes)
    ("lesson_vo", "lesson", "Vo", r"(ボ|ホ)ーカル"),
    ("lesson_da", "lesson", "Da", r"(ダ|タ)ンス"),
    ("lesson_vi", "lesson", "Vi", r"(ビ|ヒ)(ジ|シ)ュアル"),
    ("work_radio", "work", None, r"ラジオ"),
    ("work_talk", "work", None, r"トーク"),
    ("work_magazine", "work", None, r"雑誌"),
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def parse_int(text: str) -> int | None:
    m = _NUMBER.match(_clean(text))
    if not m:
        return None
    digits = m.group(1).replace(",", "")
    return int(digits) if digits.isdigit() else None


def sorted_texts(texts: Texts) -> list[tuple]:
    return sorted(((t, tuple(b)) for t, b in texts), key=lambda tb: (tb[1][0], tb[1][1]))


def parse_season(texts: Texts) -> int | str | None:
    """シーズンN -> N; W.I.N.G. -> "wing"."""
    for text, _ in texts:
        t = _clean(text)
        m = re.search(r"(シ[ーｰ]?[ズス]ン|ズン)([1-4])", t)
        if m:
            return int(m.group(2))
        if re.search(r"W\.?I\.?N\.?G", t):
            return "wing"
    return None


def parse_weeks_left(texts: Texts) -> int | None:
    found = [int(m.group(1)) for t, _ in texts for m in [re.fullmatch(r"([1-8])週?", _clean(t))] if m]
    return found[0] if len(set(found)) == 1 else None


def parse_fans_left(texts: Texts) -> int | str | None:
    """Header 「ファン人数あと N人」: a number, or "clear" when the CLEAR!/Congratulation! badge replaces it."""
    joined = "".join(_clean(t) for t, _ in sorted_texts(texts))
    if re.search(r"CLEAR|LEAR!|Congratulation|aullation|auution|auultion", joined, re.I):
        return "clear"
    nums = [parse_int(_clean(t).replace("数あと", "")) for t, _ in sorted_texts(texts)]
    nums = [n for n in nums if n is not None]
    if not nums:
        m = re.search(r"([\d,]{1,9})人", joined)
        return int(m.group(1).replace(",", "")) if m else None
    return nums[-1]


def parse_status_row(texts: Texts) -> dict:
    """Schedule status row 「Vo C/368  Da E/125  Vi E/118  Me D/213  SP 213  1,513(fans)」 -> values.

    Cells are ``<rank>/<value>``; the rank letter may be split off or misread, only the value is used. SP is the
    first plain number right of the 4th cell (before the fans); fans is the right-most comma/large number."""
    cells = []
    for text, box in sorted_texts(texts):
        t = _clean(text)
        m = _STAT_CELL.match(t)
        if m:
            cells.append((box[0], int(m.group(2)), box))
    out: dict = {"stats": None, "sp": None, "fans": None}
    if len(cells) == 3:
        # 2026-10-12 live (new Produce, week 1): the メンタル cell 「F/ 90」 lost its rank and slash and read as a bare
        # 「90」. Accept one bare number one cell-pitch right of the 3rd cell (the next bare number further right is SP).
        pitch = cells[2][0] - cells[1][0]
        bare = [(box[0], int(_clean(text)), box) for text, box in sorted_texts(texts)
                if re.fullmatch(r"\d{2,4}", _clean(text)) and 0.6 * pitch <= box[0] - cells[2][0] <= 1.6 * pitch]
        if len(bare) == 1:
            cells.append(bare[0])
    if len(cells) < 4:
        return out
    cells = cells[:4]
    out["stats"] = {k: v for k, (_, v, _) in zip(STAT_ORDER, cells)}
    right = cells[-1][2][0] + cells[-1][2][2]
    numbers = []
    for text, box in sorted_texts(texts):
        if box[0] < right - 5:
            continue
        t = _clean(text)
        if re.fullmatch(r"\d{1,4}", t) or re.fullmatch(r"[\d,]{4,9}", t):
            numbers.append((box[0], t))
    if numbers:
        fans = [t for _, t in numbers if "," in t or len(t) >= 5]
        if fans:
            out["fans"] = int(fans[-1].replace(",", ""))
        plain = [int(t) for x, t in numbers if "," not in t and len(t) <= 4]
        if plain and (len(numbers) >= 2 or not fans):
            out["sp"] = plain[0]
        if out["fans"] is None and len(plain) >= 2:
            out["fans"] = plain[-1]
            out["sp"] = plain[0]
    return out


def parse_rooms(texts: Texts) -> list[dict]:
    """Room cards on the lesson/work tab: label box, raised (selected) flag and the Lv (number or "MAX") above it."""
    rooms = []
    labels = []
    for text, box in texts:
        t = _clean(text)
        for rid, kind, attr, pattern in ROOMS:
            if re.search(pattern, t) and (kind != "lesson" or "レッスン" in t or "ッスン" in t):
                labels.append((rid, kind, attr, box))
                break
    if not labels:
        return rooms
    base = max(b[1] for *_, b in labels)
    for rid, kind, attr, box in sorted(labels, key=lambda r: r[3][0]):
        rooms.append({"id": rid, "kind": kind, "attribute": attr, "box": list(box),
                      "selected": box[1] < base - 15})
    return rooms


ROOM_SLOT_ROIS = ((270, 480, 170, 75), (440, 480, 165, 75), (605, 480, 170, 75), (775, 480, 160, 75),
                  (935, 480, 160, 75), (1100, 480, 165, 75))   # fixed W.I.N.G. room order (demo 2026-10-05)


def read_rooms(evaluator: "Evaluator", frame: Frame) -> list[dict]:
    """Room cards by fixed slot. ``label_ok``: the slot text matches the room name (or 「…ッスン」 for a lesson
    slot); a raised label (y < 510) marks the selected card. A slot without readable text is left out."""
    rooms = []
    for (rid, kind, attr, pattern), roi in zip(ROOMS, ROOM_SLOT_ROIS):
        texts = [(t, b) for t, b in evaluator.backend.ocr({"roi": list(roi), "threshold": 0.3}, frame)
                 if len(_clean(t)) >= 3 and b[3] >= 12 and not re.search(r"Exc|xcel|cellent|nent", t)]
        if not texts:
            continue
        joined = "".join(_clean(t) for t, _ in texts)
        ok = bool(re.search(pattern, joined)) or (kind == "lesson" and bool(re.search(r"(レ|ル)?ッスン|ルスン", joined)))
        box = min((b for _, b in texts), key=lambda b: b[1])
        rooms.append({"id": rid, "kind": kind, "attribute": attr, "box": list(box), "label_ok": ok,
                      "selected": box[1] < 510})
    return rooms


# Gain preview of the SELECTED room (2026-10-10, demo1 #247/#259/#270/#325/#326/#338/#460): a row of 「+N」 texts at
# y ≈ 338-344 (h ≈ 29) right above the status row, one per status column. Text centres: Vo ≈ 462, Da ≈ 583, Me ≈ 825,
# SP ≈ 945, fans ≈ 1091; Vi ≈ 704 is inferred from the 121 px stat pitch (no Vi preview in the samples). A column
# without a 「+N」 gains nothing (e.g. a Da lesson shows Da, Me, SP, fans only).
PREVIEW_ROI = (400, 326, 760, 50)
PREVIEW_COLUMNS = (("Vo", 462), ("Da", 583), ("Vi", 704), ("Me", 825), ("sp", 945), ("fans", 1091))
PREVIEW_COLUMN_TOLERANCE = 35
_PREVIEW_VALUE = re.compile(r"^\+([\d,]{1,7})$")


def parse_preview(texts: Texts) -> dict | None:
    """「+N」 texts of the preview row -> {"preview": {stat: n}, "sp_gain": n|None, "fan_gain": n|None}.

    None when nothing was read, a text sits between columns or two texts claim one column (never guessed)."""
    found: dict = {}
    for text, box in texts:
        m = _PREVIEW_VALUE.match(_clean(text))
        if not m or box[1] + box[3] > PREVIEW_ROI[1] + PREVIEW_ROI[3] + 4:
            continue
        cx = box[0] + box[2] / 2
        near = [(abs(cx - x), key) for key, x in PREVIEW_COLUMNS if abs(cx - x) <= PREVIEW_COLUMN_TOLERANCE]
        if not near:
            return None
        key = min(near)[1]
        if key in found:
            return None
        found[key] = int(m.group(1).replace(",", ""))
    if not found:
        return None
    return {"preview": {k: v for k, v in found.items() if k in STAT_ORDER},
            "sp_gain": found.get("sp"), "fan_gain": found.get("fans")}


def read_preview(evaluator: "Evaluator", frame: Frame) -> dict | None:
    return parse_preview(evaluator.backend.ocr({"roi": list(PREVIEW_ROI), "threshold": 0.3}, frame))


def parse_room_levels(texts: Texts, rooms: list[dict]) -> None:
    """Attach ``level`` (1..5 or 5 for MAX) from 「Lv.N」 texts in the band above the room labels (in place)."""
    tokens = []
    for text, box in sorted_texts(texts):
        t = _clean(text)
        if re.search(r"MAX", t):
            tokens.append((box, 5))
        m = re.fullmatch(r"Lv\.?([1-5])", t)
        if m:
            tokens.append((box, int(m.group(1))))
        elif re.fullmatch(r"[1-5]", t):
            tokens.append((box, int(t)))
    for room in rooms:
        x, y, w, _ = room["box"]
        cx = x + w / 2
        near = [(abs((b[0] + b[2] / 2) - (cx + 40)), v) for b, v in tokens if b[1] < y and y - b[1] < 110
                and x - 40 <= b[0] <= x + w + 60]
        room["level"] = min(near)[1] if near else None


# 強化週間 limited auditions: id -> (season, name pattern on the panel).
SPECIAL_AUDITION_NAMES = {
    "machikado": (1, re.compile(r"街角|朝ごはん")),
    "ereebest": (2, re.compile(r"エレ.{0,2}ベスト")),
    "mezase": (3, re.compile(r"めざせ")),
    "kaeritakunai": (4, re.compile(r"帰りたく|今夜は")),
}


SPECIAL_BY_SEASON = {season: key for key, (season, _) in SPECIAL_AUDITION_NAMES.items()}
SPECIAL_AUDITION_MARKER = re.compile(r"特殊ルール|特別オーディション")


def special_audition(texts: Texts) -> str | None:
    """Id of the limited audition named on the panel, or None (a regular audition)."""
    joined = "".join(_clean(t) for t, _ in texts)
    hits = [k for k, (_, pattern) in SPECIAL_AUDITION_NAMES.items() if pattern.search(joined)]
    return hits[0] if len(hits) == 1 else None


def parse_audition(texts: Texts) -> dict:
    out: dict = {"reward": None, "difficulty": None, "required_rank": None, "recommended": {}, "trend": None,
                 "special": special_audition(texts)}
    st = sorted_texts(texts)
    for text, box in st:
        t = _clean(text)
        m = re.search(r"アイドルランク([A-FS])以上", t)
        if m:
            out["required_rank"] = m.group(1)
        m = re.fullmatch(r"\+(\d{4,6})", t)
        if m and box[0] > 950:
            out["reward"] = int(m.group(1))
    diff = [(b, _clean(t)) for t, b in st if re.search(r"難易度", t) and b[0] > 700]
    if diff:
        b0, t0 = diff[0]
        m = re.search(r"難易度[:：]?(\d{1,2})$", t0)
        if m:
            out["difficulty"] = int(m.group(1))
        else:
            nums = [(abs(b[1] - b0[1]), int(_clean(t))) for t, b in st
                    if re.fullmatch(r"\d{1,2}", _clean(t)) and b[0] > b0[0] + b0[2] - 10 and abs(b[1] - b0[1]) < 20]
            if nums:
                out["difficulty"] = min(nums)[1]
    trend = [(b[0], a) for t, b in st for a in ("Vo", "Da", "Vi")
             if 100 <= b[1] <= 145 and b[0] > 840 and re.fullmatch(a + r"\.?", _clean(t), re.I)]
    if len(trend) == 3 and len({a for _, a in trend}) == 3:
        out["trend"] = {a: i + 1 for i, (_, a) in enumerate(sorted(trend))}
    return out


# 推奨能力値 row geometry at rest (native Maa OCR, demo #418/#503/#1562/#2355): the 「推奨能力値」 label starts at x 429;
# the 「Vo.」「Da.」「Vi.」 labels at +146/+274/+415 from it and the Me icon at about +549. Values sit right of their
# label (rank letter, then the number). The panel slides while paging, so positions are taken relative to the anchor.
RECOMMENDED_ANCHOR_X = 429
RECOMMENDED_LABEL_DX = {"Vo": 146, "Da": 274, "Vi": 415}
# Values are right-aligned: the number's right edge sits at +265 (Vo, #418 「150」 / #503 「70」) and +667 (Me); Da/Vi
# follow the label pitch (+128/+141). A value is assigned to the nearest right edge within 50 px.
RECOMMENDED_VALUE_RIGHT_DX = (("Vo", 265), ("Da", 393), ("Vi", 534), ("Me", 667))
RECOMMENDED_MAX_SHIFT = 45          # the anchor further than this from rest = the panel is still sliding -> unread
RECOMMENDED_ANCHOR_TOLERANCE = 25   # anchors (label + attribute labels) must agree on the shift within this
_REC_VALUE = re.compile(r"(?:[A-FS]/?)?(\d{2,4})")


def parse_recommended(texts: Texts) -> dict:
    """「推奨能力値 Vo. F 70  Da. _  Vi. _  [Me] F 70」 -> {"Vo": 70, "Me": 70}.

    2026-10-09 (live S1 run read ``{}`` on every page): native OCR often misses one attribute label (always 「Vi.」 on
    the S1 pages of demo 2, #503/#666/#944) and reads the empty-column underscores as ``11``/``1_`` with a 5 px box,
    so a parser that required all three labels returned nothing. The row is now located from any anchor (the
    「推奨能力値」 label and/or the attribute labels, which must agree), a value goes to the column whose right-aligned value edge is
    nearest, a merged 「C 350」 token is accepted, glyph-sized boxes only (height >= 15) count as values,
    and a duplicated column or a still-sliding panel returns {} (unread, never guessed)."""
    shifts = []
    for text, box in texts:
        t = _clean(text)
        if "推奨" in t and box[2] < 200:                      # a whole-ROI blob 「推能力恒A500DA500」 is not an anchor
            shifts.append(box[0] - RECOMMENDED_ANCHOR_X)
            continue
        m = re.fullmatch(r"(Vo|Da|Vi)\.?", t, re.I)
        if m and box[3] >= 10:
            shifts.append(box[0] - RECOMMENDED_ANCHOR_X - RECOMMENDED_LABEL_DX[m.group(1).capitalize()])
    if not shifts or max(shifts) - min(shifts) > RECOMMENDED_ANCHOR_TOLERANCE:
        return {}
    shift = sorted(shifts)[len(shifts) // 2]
    if abs(shift) > RECOMMENDED_MAX_SHIFT:
        return {}
    out: dict = {}
    for text, box in texts:
        m = _REC_VALUE.fullmatch(_clean(text))
        if not m or box[3] < 15:
            continue
        rel = box[0] + box[2] - shift - RECOMMENDED_ANCHOR_X     # right edge: a merged rank letter stays in column
        gap, key = min((abs(rel - dx), attr) for attr, dx in RECOMMENDED_VALUE_RIGHT_DX)
        if gap > 50:
            continue                                           # outside the row (e.g. the neighbouring page)
        if key in out:
            return {}
        value = int(m.group(1))
        if not 10 <= value <= 2000:
            return {}
        out[key] = value
    return out


def parse_live(texts: Texts) -> dict:
    out = {"auto": None, "speed": None}
    for text, box in texts:
        t = _clean(text)
        if box[0] < 1095 and t in ("ON", "OFF"):
            out["auto"] = t
        m = re.search(r"([23])倍速", t)
        if m:
            out["speed"] = int(m.group(1))
    return out


_CHOICE_TEXT = re.compile(r"[぀-ヿ一-鿿！-～!?…]")


def parse_choice(texts_by_slot: Mapping[str, Texts]) -> dict:
    """Option text per slot. A slot whose text has no kana/kanji/full-width character is background noise, not an
    option (live 2026-10-14: a two-option event read the window's 「8」 as Center 「B」 and the middle gap was
    tapped five times)."""
    out = {}
    for slot, texts in texts_by_slot.items():
        text = "".join(_clean(t) for t, _ in sorted(texts, key=lambda tb: (tb[1][1], tb[1][0])))
        out[slot] = text if text and _CHOICE_TEXT.search(text) else None
    return out


def parse_owned_stats(texts: Texts) -> dict | None:
    """所持スキル panel 「961 / 1059」 x4 (Vo Da / Vi Me in two rows) -> {stat: (value, cap)}."""
    cells = []
    for text, box in texts:
        m = re.fullmatch(r"(\d{2,4})/(\d{3,4})", _clean(text))
        if m:
            cells.append((box[1] // 20, box[0], int(m.group(1)), int(m.group(2))))
    if len(cells) != 4:
        return None
    cells.sort()
    return {k: (v, c) for k, (_, _, v, c) in zip(STAT_ORDER, cells)}


def parse_promise(texts: Texts) -> bool:
    return any("約束" in _clean(t) for t, _ in texts)


def stamina_fill(image, roi: Sequence[int] = (1006, 24, 246, 8)) -> float | None:
    """Fraction (0..1) of the header 体力 bar that is filled: coloured (saturated) pixels vs the dark empty track."""
    if image is None:
        return None
    import numpy as np
    x, y, w, h = roi
    patch = np.asarray(image)[y:y + h, x:x + w, :3].astype(int)
    mx, mn = patch.max(axis=2), patch.min(axis=2)
    filled = ((mx - mn) >= 60) & (mx >= 150)
    cols = filled.mean(axis=0) >= 0.5
    if not cols.any():
        return 0.0
    # The fill is contiguous from the left: the right-most filled column bounds it.
    return round((int(np.nonzero(cols)[0].max()) + 1) / w, 3)


def _clusters(columns, min_width=8, gap=3) -> list[tuple[int, int]]:
    out: list = []
    for c in columns:
        if out and c - out[-1][1] <= gap:
            out[-1][1] = c
        else:
            out.append([c, c])
    return [(a, b) for a, b in out if b - a >= min_width]


def status_marks(image, roi: Sequence[int]):
    """(badge, icon): x extents of the orange 「SP」 badge (right-most orange cluster) and of the green fan icon right of
    it inside a status row. None when either mark is not found."""
    if image is None:
        return None
    import numpy as np
    x, y, w, h = roi
    a = np.asarray(image)[y:y + h, x:x + w, :3].astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    orange = _clusters(np.nonzero((((r > 220) & (g > 90) & (g < 180) & (b < 90)).sum(0)) >= 6)[0] + x)
    green = _clusters(np.nonzero((((g > 150) & (r < 120) & (b < 120)).sum(0)) >= 6)[0] + x)
    if not orange:
        return None
    badge = orange[-1]
    icons = [c for c in green if c[0] > badge[1]]
    if not icons:
        return None
    icon = icons[0]
    if icon[0] - badge[1] < 20:
        return None
    return (int(badge[0]), int(badge[1])), (int(icon[0]), int(icon[1]))


def status_regions(image, roi: Sequence[int]):
    """(sp_roi, fans_roi) inside a status row: the SP value sits between the orange 「SP」 badge and the green fan icon;
    the fan count is right of the icon. None when either mark is not found."""
    marks = status_marks(image, roi)
    if marks is None:
        return None
    (_, badge_right), (icon_left, icon_right) = marks
    x, y, w, h = roi
    # 2026-10-14 live a5/a6: the tight box (badge edge + 2 .. icon - 2) made ROI OCR split 「274」 into 「2」「4」 and
    # 「317」 into 「3」, so SP read as 3/4 and the pre-audition session was skipped as unaffordable. Widen by a few px
    # on both sides (the badge's 「P」 and the icon's edge do not OCR as digits).
    sp = (badge_right - 6, y, icon_left - badge_right + 10, h)
    # Start 40 px right of the icon: a narrow leading 「1」 right after the icon is dropped by ROI OCR otherwise.
    fans = (icon_right + 40, y, x + w - icon_right - 40, h)
    return sp, fans


def status_digit_rois(image, roi: Sequence[int]):
    """(stats_roi, sp_digits_roi, fans_digits_roi): reads that never touch the SP badge or the dotted underline.
    2026-10-29 (wing5b S4 week 8): the badge edge inside the widened SP sub-ROI read as a leading 「7」 (SP 17 -> 717),
    and the badge glued to the メンタル value read 358 as 3581 (241 as 2414 on demo 2 #1086). The stat cells end 2 px
    left of the badge; the SP digits (3 px inside badge and icon) and the fan count (right of the icon, above the
    dotted underline that read as a leading 「1」: 727 -> 1727) are read as one line (``only_rec``: no detector, so
    「274」 is not split as on 10-14). Stats 54/57 (Me 11 wrong) -> 57/57, fans
    55/57 -> 57/57, SP 67/67 on the labelled frames."""
    marks = status_marks(image, roi)
    if marks is None:
        return None
    (badge_left, badge_right), (icon_left, icon_right) = marks
    x, y, w, h = roi
    return ((x, y, badge_left - 2 - x, h), (badge_right + 3, y + 6, icon_left - badge_right - 6, h - 10),
            (icon_right + 40, y + 6, x + w - icon_right - 40, h - 10))


def read_status(evaluator: "Evaluator", frame: Frame, node_name: str) -> dict:
    """Status row: stats from the 4 「rank/value」 cells (works on split or merged OCR text); SP and fans from
    sub-ROIs located by the SP badge / fan icon colours (merged OCR glues the icon to the SP digits)."""
    digits = status_digit_rois(frame.image, evaluator.nodes[node_name]["roi"])
    texts = (evaluator.backend.ocr({"roi": list(digits[0]), "threshold": 0.3}, frame) if digits is not None
             else evaluator.texts(node_name, frame))
    out = parse_status_row(texts)
    joined = " ".join(t for t, _ in sorted_texts(texts))
    cells = re.findall(r"/\s*(\d{2,4})", joined)
    if out["stats"] is None and len(cells) >= 4:
        out["stats"] = {k: int(v) for k, v in zip(STAT_ORDER, cells[:4])}
    groups = re.findall(r"\d+", joined)
    if out["stats"] is None and digits is not None and len(groups) == 4 and all(2 <= len(g) <= 4 for g in groups):
        # The badge-free row holds only the four values; OCR sometimes drops a 「/」 (recording wing4b: 「D 278 E/108E/128
        # E/147」). A rank letter read as a digit makes a value too large for wing_numbers, which re-reads it.
        out["stats"] = {k: int(v) for k, v in zip(STAT_ORDER, groups)}
    if out["stats"] is None and len(cells) == 3 and re.search(r"/\s*$", joined) and texts:
        # 2026-10-12 live (week 1): merged OCR 「vo.E/ 108 Da.E/108viE/108 F/」 ends at the 4th cell's slash; its value
        # (「90」) sits just right of the merged box. Read that one cell on its own.
        last = max(texts, key=lambda tb: tb[1][0] + tb[1][2])[1]
        right = last[0] + last[2]
        width = 70 if digits is None else max(10, min(70, digits[0][0] + digits[0][2] - (right - 4)))   # stop at the badge
        sub = (right - 4, last[1] - 4, width, last[3] + 8)
        vals = [re.match(r"(\d{2,4})", _clean(t)) for t, _ in evaluator.backend.ocr({"roi": list(sub), "threshold": 0.3}, frame)]
        vals = [int(m.group(1)) for m in vals if m]   # the SP badge may follow: 「90SP」
        if len(vals) == 1:
            out["stats"] = {k: int(v) for k, v in zip(STAT_ORDER, [*cells, vals[0]])}
    regions = status_regions(frame.image, evaluator.nodes[node_name]["roi"])
    if regions is not None:
        sp_roi, fans_roi = regions
        sp = []
        if digits is not None:
            line = evaluator.backend.ocr({"roi": list(digits[1]), "threshold": 0.3, "only_rec": True}, frame)
            sp = [int(_clean(t)) for t, _ in line if re.fullmatch(r"\d{1,4}", _clean(t))] if len(line) == 1 else []
        if len(sp) != 1:
            sp = [parse_int(t) for t, _ in evaluator.backend.ocr({"roi": list(sp_roi), "threshold": 0.3}, frame)]
            sp = [v for v in sp if v is not None]
        if len(sp) != 1:
            band = evaluator.backend.ocr({"roi": list(sp_band(sp_roi)), "threshold": 0.3}, frame)
            sp = [int(_clean(t)) for t, _ in band if re.fullmatch(r"\d{1,4}", _clean(t))] if len(band) == 1 else []
        out["sp"] = sp[0] if len(sp) == 1 else None
        fans = None
        if digits is not None:
            line = evaluator.backend.ocr({"roi": list(digits[2]), "threshold": 0.3, "only_rec": True}, frame)
            fans = parse_int("".join(_clean(t) for t, _ in line)) if len(line) == 1 else None
        if fans is None:
            fans_texts = evaluator.backend.ocr({"roi": list(fans_roi), "threshold": 0.3}, frame)
            fans = parse_int("".join(_clean(t) for t, _ in sorted_texts(fans_texts)))
        out["fans"] = fans
    return out


def sp_band(sp_roi) -> tuple:
    """Retry ROI for SP when the sub-ROI read is not one number: badge edge .. fan icon, 30 px around the digits.
    2026-10-25 live (wing11/12, SP 5): the 45 px sub-ROI found no text box and read the lone 「5」 as 「M」; this band
    reads 「5」 (1.0). Demo Schedule frames: equal to the sub-ROI read on 130/131, the other (SP 2 read as 「22」) right."""
    x, y, w, h = sp_roi
    return x + 6, y + 10, w - 10, h - 15


TREND_COLOURS = {"Vo": (240, 80, 160), "Da": (50, 150, 240), "Vi": (250, 160, 30)}


def trend_from_pixels(image, anchor_box: Sequence[int]) -> dict | None:
    """Audition 「流行 ①X ②Y ③Z」: classify the three attribute labels right of the 流行 text by colour."""
    if image is None or anchor_box is None:
        return None
    import numpy as np
    x, y, w, h = anchor_box
    arr = np.asarray(image)[:, :, :3].astype(int)
    order = []
    for i in range(3):
        sx = x + w + 50 + 112 * i
        patch = arr[max(0, y - 6):y + h + 6, sx:sx + 60]
        if patch.size == 0:
            return None
        score = {}
        for attr, (cr, cg, cb) in TREND_COLOURS.items():
            d = np.abs(patch[..., 0] - cr) + np.abs(patch[..., 1] - cg) + np.abs(patch[..., 2] - cb)
            score[attr] = int((d < 90).sum())
        best = max(score, key=score.get)
        if score[best] < 40:
            return None
        order.append(best)
    if len(set(order)) != 3:
        return None
    return {a: i + 1 for i, a in enumerate(order)}


def parse_unit(counter: Texts, tabs: Texts) -> dict:
    out = {"unit_index": None, "unit_total": None, "unit_page": None}
    for text, _ in counter:
        # the refresh icon left of the counter is sometimes read as a letter (third demo #288 「G17/20」)
        m = re.fullmatch(r"\D?(\d{1,2})/(\d{1,2})", _clean(text))
        if m:
            out["unit_index"], out["unit_total"] = int(m.group(1)), int(m.group(2))
    numbers = [int(m.group(1)) for t, _ in tabs for m in [re.search(r"ット(\d{1,2})$", _clean(t))] if m]
    pages = {(n - 1) // 10 for n in numbers if 1 <= n <= 20}
    if len(pages) == 1:
        out["unit_page"] = pages.pop()
    return out


# ---------------------------------------------------------------------------------------------------------------
# Live (Auto OFF) readers — measured on the second demo recording (2026-10-06, manual turns #1946–#2004, #2172–#2210,
# #2655–#2690). Judges are always Vo / Da / Vi from top to bottom; a judge
# that left (審査終了) leaves its slot empty. The bar under each judge fills with OUR accumulated appeal (empty at the
# start of the Live, 10 segments), so it is ``our_dealt``, not the remaining interest.
# ---------------------------------------------------------------------------------------------------------------
LIVE_JUDGE_ATTRS = ("Vo", "Da", "Vi")
LIVE_JUDGE_BOXES = ((15, 95, 125, 145), (15, 262, 125, 145), (15, 430, 125, 145))
LIVE_JUDGE_BARS = ((24, 223, 107, 13), (24, 390, 107, 12), (24, 556, 107, 13))
LIVE_JUDGE_ROWS = ((60, 245), (245, 412), (412, 580))          # y bands of the 流行 badge / ★倍 / Excellent texts
LIVE_HAND_SLOTS = ((310, 220), (540, 220), (770, 220))         # x, width of the 3 hand cards (labels y ~600–645)
LIVE_HAND_RAISED_Y = 622                                       # card attribute label above this y = selected card
LIVE_TOOLTIP_ROI = (300, 400, 680, 210)                       # selected card tooltip incl. the LINK layout (x 310–968)
# 「Excellent」 appeal card (2026-10-10, wing-live-20261009/live-now.png slot 2; second demo #794–#804, #997–#998):
# its pink icon circle holds a blue→pink→orange gradient 「Excellent」 word instead of a 「Vo.」 square, which the
# whole-frame OCR rarely reads (only 「Excellen」 once, #800). Its appeal is Excellent on any judge, so it is reported
# as attribute "All" (``wing_audition`` APPEAL_ATTRIBUTES). Pixel cue: >=10 blue AND >=10 orange text pixels in the
# word band of the slot (lowered y 662–682, raised 30 px higher); Vo/Da/Vi icons of all 282 demo-2 Live frames have 0.
LIVE_EXCELLENT_BAND = (135, 65, 662, 20)                       # x offset in the slot, width, lowered y, height
LIVE_EXCELLENT_LIFT = 30
LIVE_EXCELLENT_MIN_PIXELS = 10
LIVE_SPECIAL_BOX = (120, 95, 580, 135)                         # green-sparkle card icon: x offset, width, y, height
LIVE_SPECIAL_MIN_PIXELS = 300                                  # live 2026-10-15: 959 green px on the lowered card
LIVE_SPECIAL_RAISED_Y = 645                                    # icon centroid above this y = selected (lowered 660)
LIVE_EXCELLENT_TEXT_RAISED_Y = 650                             # OCR 「Excellent」 above this y = selected card
LIVE_RANKING_PANEL = (860, 340)                                # x >= / y < : rivals' 「Good」/「Perfect」 labels
_JUDGE_RGB = {"Vo": lambda r, g, b: (r > 200) & (g < 110) & (b > 60) & (b < 170),
              "Da": lambda r, g, b: (b > 200) & (r < 90) & (g > 50) & (g < 170),
              "Vi": lambda r, g, b: (r > 215) & (g > 100) & (g < 185) & (b < 70)}


def live_judges(image, texts: Texts = ()) -> list[dict] | None:
    """Three judge slots: ``present`` (its coloured box is shown), ``trend_rank`` (★2倍 = 1, ★1.5倍 = 2, the third
    present judge without a multiplier = 3), ``our_dealt`` (fill fraction of the bar, 0..1) and ``excellent`` (the
    「Excellent!!」 arrow of the selected card points at it). None without pixels."""
    if image is None:
        return None
    import numpy as np
    a = np.asarray(image)[:, :, :3].astype(int)
    out = []
    for attr, (x, y, w, h), (bx, by, bw, bh) in zip(LIVE_JUDGE_ATTRS, LIVE_JUDGE_BOXES, LIVE_JUDGE_BARS):
        patch = a[y:y + h, x:x + w]
        colour = _JUDGE_RGB[attr](patch[..., 0], patch[..., 1], patch[..., 2]).mean()
        present = bool(colour >= 0.12)
        dealt = None
        if present:
            row = a[by + bh // 2, bx:bx + bw]
            pink = (row[:, 0] > 200) & (row[:, 1] < 130) & (row[:, 2] > 60)
            dark = row.max(axis=1) < 80
            if (pink | dark).mean() >= 0.8:     # the bar is visible (not covered by an effect)
                filled = np.nonzero(pink)[0]
                dealt = 0.0 if not len(filled) else round((int(filled.max()) + 1) / bw, 2)
        out.append({"attribute": attr, "present": present, "left": not present, "trend_rank": None,
                    "our_dealt": dealt, "excellent": False})
    for text, box in texts:
        t = _clean(text)
        cy = box[1] + box[3] / 2
        if box[0] > 330:
            continue
        slot = next((i for i, (lo, hi) in enumerate(LIVE_JUDGE_ROWS) if lo <= cy < hi), None)
        if slot is None:
            continue
        if re.search(r"★?2倍", t) and box[0] < 80:
            out[slot]["trend_rank"] = 1
        elif re.search(r"★?1\.?5倍", t) and box[0] < 80:
            out[slot]["trend_rank"] = 2
        if re.search(r"xcel|Excel", t):
            out[slot]["excellent"] = True
    known = [j["trend_rank"] for j in out if j["trend_rank"]]
    if sorted(known) == [1, 2]:
        rest = [j for j in out if j["trend_rank"] is None]
        if len(rest) == 1:
            rest[0]["trend_rank"] = 3
    return out


def _excellent_card_pixels(image, x: int) -> bool | None:
    """Excellent icon of the hand card at slot ``x``: False = lowered, True = raised, None = not seen / ambiguous."""
    if image is None:
        return None
    import numpy as np
    a = np.asarray(image)
    if a.ndim != 3 or a.shape[0] < 720 or a.shape[1] < 1280:
        return None
    dx, w, y, h = LIVE_EXCELLENT_BAND
    seen = []
    for raised, top in ((False, y), (True, y - LIVE_EXCELLENT_LIFT)):
        band = a[top:top + h, x + dx:x + dx + w, :3].astype(int)
        r, g, b = band[..., 0], band[..., 1], band[..., 2]
        blue = int(((b > 200) & (r < 170) & (g < 190)).sum())
        orange = int(((r > 220) & (g > 120) & (g < 215) & (b < 140)).sum())
        if blue >= LIVE_EXCELLENT_MIN_PIXELS and orange >= LIVE_EXCELLENT_MIN_PIXELS:
            seen.append(raised)
    return seen[0] if len(seen) == 1 else None


def _special_card_pixels(image, x: int) -> bool | None:
    """Green-sparkle icon of the hand card at slot ``x`` (live 2026-10-15, not Vo/Da/Vi/Excellent): False = lowered,
    True = raised, None = not seen."""
    if image is None:
        return None
    import numpy as np
    a = np.asarray(image)
    if a.ndim != 3 or a.shape[0] < 720 or a.shape[1] < 1280:
        return None
    dx, w, y, h = LIVE_SPECIAL_BOX
    box = a[y:y + h, x + dx:x + dx + w, :3].astype(int)
    r, g, b = box[..., 0], box[..., 1], box[..., 2]
    ys, _ = np.nonzero((g > 170) & (r < 120) & (b < 190) & (g - r > 80))
    if len(ys) < LIVE_SPECIAL_MIN_PIXELS:
        return None
    return bool(y + ys.mean() < LIVE_SPECIAL_RAISED_Y)


def live_hand(texts: Texts, image=None) -> list[dict]:
    """Hand cards by slot: attribute label (「Vo.」/「Da.」/「Vi.」, or "All" for the 「Excellent」 appeal card read
    from OCR or, with pixels, from its icon colours) and ``raised`` (the selected card sits higher)."""
    hand = []
    for x, w in LIVE_HAND_SLOTS:
        labels = [(b, m.group(1)) for t, b in texts for m in [re.fullmatch(r"(Vo|Da|Vi)\.?", _clean(t))]
                  if m and x <= b[0] < x + w and 585 <= b[1] <= 660]
        excellent = [b for t, b in texts if re.search(r"xcel", _clean(t)) and x <= b[0] < x + w
                     and 600 <= b[1] <= 690]
        slot = len(hand) + 1
        if len(labels) == 1 and not excellent:
            box, attr = labels[0]
            hand.append({"slot": slot, "attribute": attr, "raised": box[1] < LIVE_HAND_RAISED_Y})
            continue
        if not labels:
            pixel = _excellent_card_pixels(image, x)
            if len(excellent) == 1:
                raised = excellent[0][1] < LIVE_EXCELLENT_TEXT_RAISED_Y
                if pixel is None or pixel == raised:
                    hand.append({"slot": slot, "attribute": "All", "raised": raised})
                    continue
            elif not excellent and pixel is not None:
                hand.append({"slot": slot, "attribute": "All", "raised": pixel})
                continue
            special = _special_card_pixels(image, x) if not excellent else None
            if special is not None:
                hand.append({"slot": slot, "attribute": "Other", "raised": special})
                continue
        hand.append({"slot": slot, "attribute": None, "raised": None})
    return hand


def live_rivals(texts: Texts) -> dict:
    """Right-hand ranking list: rows of 「★NN name」 + 「cur/max」. Rival names are 「アイドルX」; the one other row is
    ours (its name is never stored). -> {our_stars, mental, mental_max, rival_stars}."""
    stars, hps = [], []
    for text, box in texts:
        t = _clean(text)
        if box[0] < 990:
            continue
        m = re.match(r"^[★*]?(\d{2})(.*)$", t)
        if m and box[0] < 1020:
            stars.append((box[1], int(m.group(1)), m.group(2)))
            continue
        m = re.fullmatch(r"(\d{2,4})/(\d{2,4})", t) or re.fullmatch(r"(\d{3})[1lI|](\d{3})", t)
        if m and 1040 <= box[0] < 1080 and int(m.group(1)) <= int(m.group(2)):
            hps.append((box[1], int(m.group(1)), int(m.group(2))))
    names = [b[1] for t, b in texts if b[0] >= 1060 and re.search(r"アイドル", t)]
    rows = []
    for y, value, rest in sorted(stars):
        rival = bool(re.search(r"アイドル", rest)) or any(abs(ny - y) <= 12 for ny in names)
        hp = next(((c, mx) for hy, c, mx in hps if 20 <= hy - y <= 52), None)
        rows.append({"y": y, "stars": value, "rival": rival, "hp": hp})
    ours = [r for r in rows if not r["rival"]]
    out = {"our_stars": None, "mental": None, "mental_max": None, "rival_stars": None}
    if len(rows) >= 2 and len(ours) == 1:
        out["our_stars"] = ours[0]["stars"]
        if ours[0]["hp"]:
            out["mental"], out["mental_max"] = ours[0]["hp"]
        out["rival_stars"] = [r["stars"] for r in rows if r["rival"]]
    return out


def live_phase(texts: Texts, hand: list[dict]) -> str | None:
    """select_card | select_judge | timing | None (animation / unknown: never act).

    timing: the 「APPEAL」 bar (or 「Ready!!」) is shown; select_judge: one card is raised (its skill tooltip and the
    judges' 「Excellent!!」 arrows appear); select_card: all three cards are down and readable."""
    joined = [(_clean(t), b) for t, b in texts]
    if any(re.search(r"APPEAL|PPEAL", t) and 250 <= b[0] <= 1050 and 420 <= b[1] <= 560 for t, b in joined):
        return "timing"
    if any(re.search(r"Ready", t) for t, _ in joined):
        return "timing"
    px, py = LIVE_RANKING_PANEL      # rivals' judgement labels beside the ranking (live-now.png 「Perfect」 x878)
    if any(re.search(r"Appeal|Start|Bonus|Live ?Sk|Perfect!|Memory", t) and b[1] < 560 and 200 < b[0] < 1000
           and not (b[0] >= px and b[1] < py) for t, b in joined):
        return None
    attrs = [c["attribute"] for c in hand]
    if None in attrs:
        return None
    raised = [c["raised"] for c in hand]
    if raised.count(True) == 1:
        return "select_judge"
    if not any(raised):
        return "select_card"
    return None


def read_live(texts: Texts, image) -> dict:
    """Live readings for the navigator: auto/speed, turn, phase, memory gauge and a ``live_state`` dict for
    ``wing_audition.LiveState.from_dict`` (judges, hand, our mental/stars). Unknown values are None."""
    out = parse_live([(t, b) for t, b in texts if b[0] >= 1000 and b[1] < 90])
    turn = [int(_clean(t)) for t, b in texts if re.fullmatch(r"\d{1,2}", _clean(t)) and 120 <= b[0] <= 175
            and b[1] < 30]
    out["turn"] = turn[0] if len(turn) == 1 else None
    mem = [int(m.group(1)) for t, b in texts for m in [re.fullmatch(r"(\d{1,3})%", _clean(t))]
           if m and b[0] >= 1140 and b[1] >= 600]
    out["memory_gauge"] = mem[0] if len(mem) == 1 and mem[0] <= 100 else None
    hand = live_hand(texts, image)
    judges = live_judges(image, texts)
    out["live_phase"] = live_phase(texts, hand)
    out["selected_card"] = next((c["slot"] for c in hand if c["raised"]), None)
    rivals = live_rivals(texts)
    out["judges_present"] = [j["attribute"] for j in judges or () if j["present"]] if judges is not None else None
    out["trend"] = ({j["attribute"]: j["trend_rank"] for j in judges if j["trend_rank"]}
                    if judges and sum(1 for j in judges if j["trend_rank"]) == 3 else None)
    out["live_state"] = {
        "turn": out["turn"],
        "judges": [{"attribute": j["attribute"], "trend_rank": j["trend_rank"], "left": j["left"],
                    "our_dealt": j["our_dealt"]} for j in judges or ()],
        "options": [{"id": f"card{c['slot']}", "attribute": c["attribute"]} for c in hand if c["attribute"]],
        "memory_gauge": out["memory_gauge"],
        **{k: rivals[k] for k in ("mental", "mental_max", "our_stars", "rival_stars")},
    }
    out["excellent_judge"] = next((j["attribute"] for j in judges or () if j["excellent"]), None)
    # Card play (2026-10-09b, agent/core/wing_live_policy.py): pictures of the lowered hand cards (multiplier cache
    # key) and the selected card's tooltip (name, multiplier, appeal + buff).
    from agent.core import wing_live_policy as wlp
    out["hand_sigs"] = [None if c["raised"] or c["attribute"] is None else wlp.card_signature(image, c["slot"])
                        for c in hand]
    out["tooltip"] = wlp.parse_tooltip(texts) if out["live_phase"] == "select_judge" else None
    return out


ROOM_SLOT_X = ((272, 165), (440, 165), (607, 165), (774, 160), (936, 160), (1100, 163))   # tab x, width (#1086)
ROOM_PORTRAIT_BAND = (572, 34)          # y, height of the support-portrait strip under the tabs (#1086/#1717/#2466)
ROOM_CLOVER_BAND = (395, 65)            # y, height above/at the tab top where the 約束 clover sits (#1086 x447–473)


def room_portraits(image) -> list[int] | None:
    """Support idols present per room (0..3): portrait blobs in the strip under each tab. The strip background is
    one flat colour per room; a portrait is a run of >=18 columns that differ from it. None without pixels."""
    if image is None:
        return None
    import numpy as np
    a = np.asarray(image)[:, :, :3].astype(int)
    y, h = ROOM_PORTRAIT_BAND
    out = []
    for x, w in ROOM_SLOT_X:
        strip = a[y:y + h, x + 6:x + w - 6]
        flat = strip.reshape(-1, 3)
        colours, counts = np.unique(flat, axis=0, return_counts=True)
        bg = colours[counts.argmax()]
        diff = np.abs(strip - bg).sum(axis=2) > 60
        cols = np.nonzero(diff.sum(axis=0) >= 10)[0]
        blobs = [c for c in _clusters(cols, min_width=17, gap=2)]
        out.append(min(len(blobs), 3))
    return out


def room_clovers(image) -> list[bool] | None:
    """約束 clover (promise badge) on a room tab: saturated clover-green pixels near the tab's top-left corner."""
    if image is None:
        return None
    import numpy as np
    a = np.asarray(image)[:, :, :3].astype(int)
    y, h = ROOM_CLOVER_BAND
    out = []
    for x, _ in ROOM_SLOT_X:
        sub = a[y:y + h, max(0, x - 12):x + 58]
        # Clover green is (16–20, ~193, 20–40); the raised 雑誌の撮影 tab icon/border is (~40, ~202, ~40): R < 30.
        green = (sub[..., 0] < 30) & (sub[..., 1] > 160) & (sub[..., 2] < 70)
        out.append(bool(green.sum() >= 90))
    return out


def room_details(texts: Texts, image) -> list[dict]:
    """Per room slot (fixed W.I.N.G. order): ``level`` (Lv.N; 「MAX」 -> "MAX"), ``excellent`` (the 「Excellent!!」
    badge of the room), ``supports`` (portraits under the tab) and ``promise`` (約束 clover). Unread -> None."""
    portraits = room_portraits(image)
    clovers = room_clovers(image)
    out = []
    for i, ((rid, *_), (x, w)) in enumerate(zip(ROOMS, ROOM_SLOT_X)):
        level = None
        lv = [(b, _clean(t)) for t, b in texts if 395 <= b[1] <= 480 and x - 5 <= b[0] + b[2] / 2 < x + w]
        for b, t in lv:
            m = re.search(r"Lv\.?([1-5]|MAX)$", t, re.I)     # W.I.N.G. rooms go Lv.1–5 / MAX; 「Lv.9」 = misread
            if m:
                level = "MAX" if m.group(1).upper() == "MAX" else int(m.group(1))
                break
        if level is None:
            nums = [t for b, t in lv if re.fullmatch(r"[1-5]", t) and b[0] > x + w * 0.45]
            if len(nums) == 1 and any(re.fullmatch(r"Lv\.?", t) for _, t in lv):
                level = int(nums[0])
        excellent = any(re.search(r"xcel|Excel", t) for t, b in texts
                        if 395 <= b[1] <= 540 and x <= b[0] + b[2] / 2 < x + w)
        out.append({"id": rid, "level": level, "excellent": excellent,
                    "supports": None if portraits is None else portraits[i],
                    "promise": None if clovers is None else clovers[i]})
    return out


# 習得済み (third demo #434: the node just learned) must be tested before 習得(する).
SKILL_BUTTON_STATES = (("習得済", "learned"), ("習得", "learnable"), ("SP不足", "sp_short"), ("未解放", "locked"),
                       ("削除", "delete"))


def parse_skill_info(texts: Texts) -> dict | None:
    """振り返り info bar of the selected panel node (second demo #671/#1288/#1330/#2749): ``cost`` (必要 SP),
    ``state`` of its button (learnable 習得する / sp_short SP不足 / locked 未解放 / delete 削除 after W.I.N.G.).
    None when the bar is not shown (the node name is not read reliably by whole-frame OCR). Node icons on the panel itself still need templates."""
    # Maa ROI OCR reads the label and the badge as one text (「必要SP」, third demo #397/#402).
    if not any(re.fullmatch(r"必要(SP)?", _clean(t)) and 1060 <= b[0] <= 1110 and 540 <= b[1] <= 590 for t, b in texts):
        return None
    state = None
    for text, box in texts:
        if 1080 <= box[0] <= 1140 and 605 <= box[1] <= 650:
            state = next((v for k, v in SKILL_BUTTON_STATES if k in _clean(text)), state)
    # 「SP」 badge and the number may be read as one text (third demo #424: 「SP20」).
    cost = [int(m.group(1)) for t, b in texts for m in [re.fullmatch(r"(?:SP)?(\d{1,3})", _clean(t))]
            if m and b[0] >= 1160 and 540 <= b[1] <= 590]
    return {"cost": cost[0] if len(cost) == 1 else None, "state": state}


# Skill-panel node states (third demo #386–#567). Every node carries an
# 「SP: N」 label under its hexagon; learned nodes show 「SP: 0」. The hexagon body left/right of the icon (40–60 px
# above the label) tells the rest: light (V >= 0.80, S < 0.35) = open (reachable, learnable when SP allows), dark grey
# (V < 0.60, S < 0.25) = not reachable yet (the padlock nodes are in this class too), dark gold (S >= 0.40) = unlocked
# only by a support / idol event. Measured on #461 (29 labels, 10 checked by eye). Templates of each class are kept
# privately under dev_assets/private/wing-demo3-analysis/templates/ (never committed).
SKILL_NODE_STATES = ("learned", "open", "unreachable", "event", "unknown")


def skill_nodes(texts: Texts, image) -> list[dict]:
    """[{"cost", "label": (x, y, w, h), "state"}] for every 「SP: N」 label whose hexagon is fully on the canvas."""
    import colorsys
    out = []
    for text, (x, y, w, h) in texts:
        m = re.fullmatch(r"SP:(\d{1,3})", _clean(text))
        if not m:
            continue
        cost, cx = int(m.group(1)), x + w // 2
        # three zoom levels (label ~73 / ~50 / ~36 px wide): offsets scale with the label width
        k = w / (73.0 if len(m.group(1)) >= 2 else 67.0)
        d1, d2, up, tall = round(55 * k), round(35 * k), round(60 * k), round(40 * k)
        if image is None or k < 0.4 or y - up < 0 or cx - d1 < 0 or cx + d1 > CANVAS[0]:
            continue
        if cost == 0:
            out.append({"cost": 0, "label": (x, y, w, h), "state": "learned"})
            continue
        left = _mean_rgb(image, cx - d1, y - up, d1 - d2, tall)
        right = _mean_rgb(image, cx + d2, y - up, d1 - d2, tall)
        _, sat, val = colorsys.rgb_to_hsv(*(((left + right) / 2) / 255.0))
        if val >= 0.80 and sat < 0.35:
            state = "open"
        elif sat >= 0.40 and val < 0.70:
            state = "event"
        elif val < 0.60 and sat < 0.25:
            state = "unreachable"
        else:
            state = "unknown"
        out.append({"cost": cost, "label": (x, y, w, h), "state": state})
    return out


def skill_node_counts(nodes: list[dict]) -> dict:
    return {state: sum(n["state"] == state for n in nodes) for state in SKILL_NODE_STATES}


END_SCREENS = ("Evaluation", "FesBirth", "FesBirthConfirm", "TotalEval", "ClearReward", "Reward", "RewardDetail",
               "FesDetail", "TrueEndCommu", "MissionPopup", "EndDestructiveDialog")
END_MODAL_SCREENS = ("FesBirthConfirm", "EndDestructiveDialog")


def _value_right_of(texts: Texts, label: str, pattern: str = r"[\d,]{1,11}") -> int | None:
    """Number on the same row right of a label text (e.g. 「最終ファン人数」 -> 523,163)."""
    anchors = [b for t, b in texts if label in _clean(t)]
    if len(anchors) != 1:
        return None
    ax, ay, aw, ah = anchors[0]
    cands = []
    for text, (x, y, w, h) in texts:
        t = _clean(text).rstrip("人")
        if x > ax + aw - 5 and abs((y + h / 2) - (ay + ah / 2)) <= 22 and re.fullmatch(pattern, t):
            cands.append((x, int(t.replace(",", ""))))
    return min(cands)[1] if cands else None


def parse_live_skill_titles(texts: Texts, x_range=(80, 115), y_range=(270, 660)) -> list[str]:
    """Live-skill titles of the フェスアイドル誕生 list (third demo #621: titles at x ~92, descriptions at x ~52)."""
    rows = sorted((b[1], _clean(t)) for t, b in texts if x_range[0] <= b[0] <= x_range[1]
                  and y_range[0] <= b[1] <= y_range[1] and len(_clean(t)) >= 2)
    return [t for _, t in rows]


def parse_clear_reward(texts: Texts) -> dict:
    """プロデュースクリア報酬 (third demo #642–#650): 信頼度 Lv.N and コミュニケーションレベル a/b (it animates a level up;
    the collector keeps the latest value). Reward icons have no text and stay unread."""
    out: dict = {"trust_level": None, "communication_level": None}
    anchors = [b for t, b in texts if "信頼度" in _clean(t)]
    if len(anchors) == 1:
        ax, ay = anchors[0][0], anchors[0][1]
        lv = sorted((b[0], int(m.group(1))) for t, b in texts
                    for m in [re.fullmatch(r"Lv\.?(\d{1,2})", _clean(t))] if m and b[0] > ax and abs(b[1] - ay) <= 25)
        out["trust_level"] = lv[0][1] if lv else None
    pairs = [_clean(t) for t, b in texts if re.fullmatch(r"\d{1,2}/\d{2}", _clean(t)) and b[0] >= 850 and b[1] < 220]
    out["communication_level"] = pairs[0] if len(pairs) == 1 else None
    return out


EX_COLUMNS = (287, 760)          # icon x of the two 獲得報酬 Exスキル columns (third demo #688); rows 106 px apart
EX_NAME_DX = (165, 190)          # idol-name text x - icon x
EX_NAME_END = re.compile(r"(\+\d+|UP|\((極小|小|中|大)\))$")


def _mean_rgb(image, x, y, w, h):
    import numpy as np
    return np.asarray(image[y:y + h, x:x + w], dtype=np.float32).reshape(-1, 3).mean(axis=0)


def ex_icon_tier(image, x: int, y: int) -> str | None:
    """Tier of an Ex-skill icon from its frame colour (top border). Only 銅 (bronze, ~(128,70,40)) was observed (third
    demo #688); silver/gold/rainbow have no sample -> None, as is a frame washed out by the sparkle animation."""
    if image is None:
        return None
    r, g, b = _mean_rgb(image, x + 20, y + 1, 42, 4)
    if 100 <= r <= 165 and 45 <= g <= 105 and 15 <= b <= 75 and r - g >= 40 and g - b >= 15:
        return "bronze"
    return None


def ex_badge_target(image, x: int, y: int) -> str | None:
    """P (pink) / S (blue) badge left of the idol face -> ``produce`` / ``support``."""
    if image is None:
        return None
    r, g, b = _mean_rgb(image, x + 105, y + 20, 8, 8)
    if r > 230 and b > 180 and g < 210 and r - g >= 40:
        return "produce"
    if b > 230 and r < 180 and b - r >= 60:
        return "support"
    return None


def parse_reward_ex(texts: Texts, image=None) -> list[dict]:
    """獲得報酬 Exスキル tab: one entry per row, anchored on the idol-name text (x = icon x + ~178); the skill name is
    the line below. ``tier`` / ``target`` come from pixels and stay None when unclear. The list scrolls: the collector
    merges entries over frames."""
    out = []
    for text, (x, y, w, h) in sorted(texts, key=lambda tb: (tb[1][1], tb[1][0])):
        col = next((c for c in EX_COLUMNS if EX_NAME_DX[0] <= x - c <= EX_NAME_DX[1]), None)
        if col is None or not 95 <= y <= 560 or not re.fullmatch(r"[^\d\s+]{2,8}", _clean(text)):
            continue
        name = unicodedata.normalize("NFKC", "".join(
            _clean(t) for t, b in sorted(texts, key=lambda tb: tb[1][0]) if col + 90 <= b[0] <= col + 430
            and y + 30 <= b[1] <= y + 65))
        # Only complete effect lines (「+10」「UP」「(極小)」 endings); a line cut by the list edge or washed out by the
        # sparkle animation (#688 「サポ-」) is dropped, never completed by guessing.
        if not EX_NAME_END.search(name):
            continue
        top = y - 7
        out.append({"name": name, "idol": _clean(text), "tier": ex_icon_tier(image, col, top),
                    "target": ex_badge_target(image, col, top)})
    return out


def parse_reward_books(texts: Texts) -> list[dict]:
    """獲得報酬 ノウハウブック with a book selected (third demo #658): knowhow lines under the 「ノウハウ」 header,
    name (x 230–480) with 「Lv.N」 on the same row."""
    header = [b for t, b in texts if _clean(t) == "ノウハウ" and 380 <= b[0] <= 500 and 210 <= b[1] <= 255]
    if len(header) != 1:
        return []
    books = []
    for text, (x, y, w, h) in sorted(texts, key=lambda tb: tb[1][1]):
        if not (230 <= x <= 480 and header[0][1] + 25 <= y <= 600) or re.search(r"[+＋]\d", _clean(text)):
            continue
        level = [int(m.group(1)) for t, b in texts for m in [re.fullmatch(r"Lv\.?(\d)", _clean(t))]
                 if m and 520 <= b[0] <= 640 and abs(b[1] - y) <= 15]
        if len(level) == 1:
            books.append({"name": _clean(text), "level": level[0]})
    return books


# 獲得報酬 ノウハウブック tab (live 2026-10-10 20261010T073201859397Z-capture.png, third demo #651/#658): item grid at
# the right, first tile (738,197,106,106). Only tile 1 was observed; the pitch of further tiles (118 px) and the second
# row are an UNVERIFIED estimate (a run yields 1 book at 1x stamina, 2/3 at 2x/3x).
REWARD_GRID = {"x": 738, "y": 197, "pitch": 118, "tile": 106, "cols": 4, "rows": 2}
REWARD_TILE_STD_MIN = 20.0       # book cover std 55–76 on the three frames; empty slot 0.3–1.6
REWARD_CHECK_ROI = (78, -6, 34, 34)   # pink ✓ at the selected tile's top-right (third demo #658)
# Detail pane (third demo #658: title (353,103), stats (420,144)/(580,145)/(425,183)/(582,181), rows name x 262 /
# Lv x 565, 保護 button (271–436, 645–690), 売却 (452–617)). It is the アイドル一覧 detail panel moved right by 213 px;
# knowhow_books.parse_detail_panel (K-series, calibrated on the 一覧 page) reads it after the shift.
REWARD_DETAIL_DX = 213
REWARD_DETAIL_PANE = (222, 88, 446, 612)         # x 222–668, y 88–700 (pane + its 保護/売却 buttons)


def reward_tile_box(index: int) -> tuple[int, int, int, int]:
    """Tile ``index`` (0-based, row-major) of the 獲得報酬 ノウハウブック grid."""
    g = REWARD_GRID
    row, col = divmod(index, g["cols"])
    return (g["x"] + col * g["pitch"], g["y"] + row * g["pitch"], g["tile"], g["tile"])


def reward_items(image) -> int | None:
    """Number of book tiles (contiguous from tile 0) by pixel texture; None without pixels."""
    if image is None:
        return None
    import numpy as np
    count = 0
    for i in range(REWARD_GRID["cols"] * REWARD_GRID["rows"]):
        x, y, w, h = reward_tile_box(i)
        patch = np.asarray(image[y + 15:y + h - 16, x + 15:x + w - 16], dtype=np.float32)
        if patch.size == 0 or float(patch.std()) < REWARD_TILE_STD_MIN:
            break
        count += 1
    return count


def reward_selected(image, items: int | None) -> int | None:
    """Index of the tile carrying the pink ✓ (the book shown in the detail pane); None when none or ambiguous."""
    if image is None or not items:
        return None
    import numpy as np
    hits = []
    for i in range(items):
        x, y, _, _ = reward_tile_box(i)
        rx, ry, rw, rh = REWARD_CHECK_ROI
        p = np.asarray(image[max(0, y + ry):y + ry + rh, x + rx:x + rx + rw], dtype=np.int32).reshape(-1, 3)
        if p.size and float(((p[:, 0] > 200) & (p[:, 1] < 140) & (p[:, 2] > 120) & (p[:, 0] - p[:, 1] > 90)).mean()) > 0.08:
            hits.append(i)
    return hits[0] if len(hits) == 1 else None


def ex_list_cut(image) -> bool | None:
    """獲得報酬 Exスキル list continues beyond the visible rows: a row edge alone in the bottom band (#688: band std
    56 at y 570 with an empty gap above) or a row cut by the top edge (#693, scrolled). None without pixels."""
    if image is None:
        return None
    import numpy as np
    band = lambda y: float(np.asarray(image[y:y + 8, 300:700], dtype=np.float32).std())  # noqa: E731
    return (band(570) > 20 and band(545) < 10) or band(100) > 20


def parse_reward_detail(texts: Texts) -> dict | None:
    """Selected book in the 獲得報酬 detail pane -> knowhow_books.parse_detail_panel fields plus ``max_level`` and
    ``rows_partial`` (the list may scroll: a row near the bottom edge means more rows can be hidden). None when the
    pane shows no book (「ノウハウブックを選択してください」) or no title was read."""
    from agent.core import knowhow_books as kb
    x0, y0, w, h = REWARD_DETAIL_PANE
    shifted = [(t, (b[0] - REWARD_DETAIL_DX, b[1], b[2], b[3])) for t, b in texts
               if x0 <= b[0] + b[2] / 2 <= x0 + w and y0 <= b[1] + b[3] / 2 <= y0 + h]
    if any(kb.DETAIL_EMPTY in _clean(t) for t, _ in shifted):
        return None
    panel = kb.parse_detail_panel(shifted)
    if not panel.get("title"):
        return None
    levels = [r["level"] for r in panel["rows"] if isinstance(r.get("level"), int)]
    lowest = max((b[1] + b[3] for t, b in shifted if re.fullmatch(r"Lv\.?\d", _clean(t))), default=0)
    return {"title": panel["title"], "idol": panel["idol"], "stats": panel["stats"],
            "rows": [{k: r.get(k) for k in ("name", "level", "effect", "clipped")} for r in panel["rows"]],
            "protected": panel["protected"], "max_level": max(levels) if levels else None,
            "rows_partial": bool(panel["dropped"]) or any(r.get("clipped") for r in panel["rows"])
            or lowest >= kb.DETAIL_LIST_ROI[1] + kb.DETAIL_LIST_ROI[3] - 40}


def parse_reward_reroll(texts: Texts) -> dict | None:
    """「再抽選 1/1」 + 「消費スタミナ：30」 (ROI OCR of the button, live 2026-10-10 / third demo #658) -> {left, total,
    stamina}; None unless both numbers are read."""
    joined = "".join(_clean(t) for t, _ in texts)
    m = re.search(r"再抽.?選?(\d)/(\d)", joined)
    cost = re.search(r"スタミナ[:：]?(\d{2})", joined)
    if not m or not cost:
        return None
    return {"left": int(m.group(1)), "total": int(m.group(2)), "stamina": int(cost.group(1))}


def read_end(screen: str, texts: Texts, sp_lost_texts: Texts = (), image=None) -> dict:
    """End-of-Produce readings for ``agent.core.produce_end.READING_FIELDS`` (second demo #2730/#2735 社長からの評価,
    #2736 フェスアイドル誕生; the other end screens have no native sample yet, so their fields stay unread). Always
    returns the whole-frame OCR ``texts`` (destructive-marker check) and ``dialog`` (a modal over the page; decided
    from the classified screen, no pixel heuristic without samples)."""
    out: dict = {"texts": [t for t, _ in texts], "dialog": screen in END_MODAL_SCREENS}
    if screen == "Evaluation":
        out["final_fans"] = _value_right_of(texts, "最終ファン人数")
        out["affection"] = _value_right_of(texts, "親愛度", r"\d{1,3}")
    elif screen == "FesBirth":
        stats = parse_owned_stats([(t, b) for t, b in texts if b[1] < 200 and b[0] < 700])
        if stats:
            out["stats"] = {k: v for k, (v, _) in stats.items()}
            out["stat_caps"] = {k: c for k, (_, c) in stats.items()}
        fans = [int(_clean(t).replace(",", "")) for t, b in texts
                if re.fullmatch(r"\d{1,3}(,\d{3})+|\d{1,3}", _clean(t)) and b[1] < 130 and 400 <= b[0] < 700]
        out["fans"] = fans[0] if len(fans) == 1 else None
        out["live_skills"] = parse_live_skill_titles(texts) or None
    elif screen == "ClearReward":
        out.update(parse_clear_reward(texts))
    elif screen in ("Reward", "RewardDetail"):
        stamina = any(_clean(t) == "スタミナ" and b[0] >= 1100 and b[1] < 60 for t, b in texts)
        out["reward_tab"] = "knowhow" if stamina else "ex"
        pair = [_clean(t) for t, b in texts if re.fullmatch(r"\d{1,3}/\d{3}", _clean(t)) and b[0] >= 1100 and b[1] < 90]
        out["stamina"] = pair[0] if len(pair) == 1 else None
        if stamina:
            out["knowhow_books"] = parse_reward_books(texts) or None
            out["reward_items"] = reward_items(image)
            out["reward_selected"] = reward_selected(image, out["reward_items"])
            out["reward_book"] = parse_reward_detail(texts)
        else:
            out["ex_skills"] = parse_reward_ex(texts, image) or None
            # The list scrolls (third demo #688 -> #693: 9 skills). A row cut at the bottom edge or a list not at its
            # top -> more skills than read (``ex_rows_cut``); reading them needs a scroll, which Maa does not do.
            out["ex_rows_cut"] = ex_list_cut(image)
    elif screen == "FesBirthConfirm":
        lost = [parse_int(t) for t, _ in sp_lost_texts]
        lost = [v for v in lost if v is not None]
        out["sp_lost"] = lost[0] if len(lost) == 1 else None
    elif screen == "TotalEval":
        ranks = [m.group(1) for t, b in texts for m in [re.search(r"([SABCDEF]{1,2}\+?)\s*RANK", _clean(t))]
                 if m and 100 <= b[0] <= 560 and 100 <= b[1] <= 270]
        out["rank"] = ranks[0] if len(set(ranks)) == 1 else None
    elif screen == "FesDetail":
        out["total_fans"] = _value_right_of(texts, "計ファン人数")
    elif screen == "MissionPopup":
        mission = [t for t, b in sorted_texts(texts) if 250 <= b[0] <= 1050 and 140 <= b[1] <= 420]
        out["mission_text"] = "".join(_clean(t) for t in mission) or None
    return out


# Reader specs per screen: field -> (reader node, parser). Parsers take the node's texts (and the frame for pixels).
# Start flow. プロデュース選択 difficulty tabs (mean RGB; ビギナー/ミドル/マスター on third-demo #218–#228 and
# second-demo #195–#206): a selected tab is saturated (ビギナー R ~73, ミドル R 99–122, マスター G ~102), an unselected
# one pale (R 177 / R 175–184 / G 197); dimmed frames (popup over the page, #324) match none -> None.
DIFFICULTY_TABS = {"beginner": (600, 345, 160, 55), "middle": (790, 345, 150, 55), "master": (970, 345, 150, 55)}


def produce_difficulty(image) -> str | None:
    import numpy as np
    means = {k: np.asarray(image[y:y + h, x:x + w], dtype=float).reshape(-1, 3).mean(0)
             for k, (x, y, w, h) in DIFFICULTY_TABS.items()}
    selected = {"beginner": means["beginner"][0] < 120 and means["beginner"][1] > 200,
                "middle": means["middle"][0] < 135 and means["middle"][2] > 230,
                "master": means["master"][1] < 140 and means["master"][0] > 220}
    unselected = {"beginner": means["beginner"][0] > 150 and means["beginner"][1] > 215,
                  "middle": means["middle"][0] > 150 and means["middle"][2] > 230,
                  "master": means["master"][1] > 170 and means["master"][0] > 220}
    chosen = [k for k in DIFFICULTY_TABS if selected[k]]
    if len(chosen) == 1 and all(unselected[k] for k in DIFFICULTY_TABS if k != chosen[0]):
        return chosen[0]
    return None


def item_slots_empty(texts: Texts) -> bool | None:
    """アイテム準備: both item slots show 「アイテムを選択していません」 (two hits) -> True; one or none -> False;
    nothing read -> None."""
    if not texts:
        return None
    hits = sum(1 for t, _ in texts if "選択していません" in _clean(t))
    return hits >= 2


def parse_start_confirm(body: Texts, panel: Texts) -> dict:
    """使用確認 of the Produce start: layout (``plain`` = stamina only / ``items`` = items listed), stamina before/after
    and the 消費倍率. Unread values stay None (the decision stops)."""
    text = "".join(_clean(t) for t, _ in sorted_texts(body))
    layout = None
    if "アイテム" in text and "消" in text:
        layout = "items"
    elif re.search(r"スタミナを消.して", text):
        layout = "plain"
    multiplier = None
    numbers = []
    label_y = None
    for t, box in sorted_texts(panel):
        c = _clean(t)
        m = re.fullmatch(r"(\d)倍", c)
        if m:
            multiplier = int(m.group(1))
            continue
        if "スタミナ" in c:
            label_y = box[1] + box[3] / 2
            continue
        if re.fullmatch(r"\d{1,4}", c):
            numbers.append((box[1] + box[3] / 2, box[0], int(c)))
    row = sorted((x, v) for y, x, v in numbers if label_y is not None and abs(y - label_y) <= 18)
    before = after = None
    if len(row) == 2:
        before, after = row[0][1], row[1][1]
    return {"layout": layout, "stamina_before": before, "stamina_after": after, "multiplier": multiplier}


# Home button 「プロデュース」 / 「プロデュース中」: OCR reads the long vowel as 「-」 too (live 2026-10-29 「プロデュ-ス中」).
HOME_PRODUCE = r"^(プ|ブ)ロデュ[ー\-−‐一]ス$"
HOME_PRODUCE_RESUME = r"^(プ|ブ)ロデュ[ー\-−‐一]ス中$"

# ---- batch c (2026-10-09c): home bar, 大富豪 pages, プロデュース選択 tab / アイドルの応援,
# full-screen スキル入れ替え確認. Positions are the OCR boxes of the native demo frames (second demo #1–#192, third
# demo #190–#231); a label counts only when its box centre lies inside the click rect the decision would use.
BATCH_C_SCREENS = ("Home", "HomeMinigamePopup", "ProduceSelectOther", "MinigameCourse", "MinigameRules",
                   "MinigameGame", "MinigameResult", "ResumeDialog")
_RECTS_C = {"home_produce": (1005, 615, 105, 70), "home_minigame_menu": (640, 615, 95, 75),
            "home_minigame": (705, 540, 110, 50), "produce_tab_train": (845, 85, 150, 32)}
PASS_PATCH = (495, 450, 50, 40)          # inside the パス circle (second demo #75/#84/#92/#107: RGB ~105/191/225)


def _centre_in(box, rect, pad: int = 0) -> bool:
    x, y, w, h = rect
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    return x - pad <= cx <= x + w + pad and y - pad <= cy <= y + h + pad


def _label_at(texts: Texts, pattern: str, rect, pad: int = 0) -> bool:
    return any(re.search(pattern, _clean(t)) and _centre_in(b, rect, pad) for t, b in texts)


def _box_of(texts: Texts, pattern: str):
    hits = [tuple(b) for t, b in texts if re.search(pattern, _clean(t))]
    return hits[0] if len(hits) == 1 else None


def pass_button_enabled(image) -> bool | None:
    if image is None:
        return None
    import numpy as np
    x, y, w, h = PASS_PATCH
    r, g, b = np.asarray(image[y:y + h, x:x + w], dtype=float).reshape(-1, 3).mean(0)
    return bool(70 <= r <= 145 and 160 <= g <= 220 and 195 <= b <= 250)


def parse_rules_page(texts: Texts):
    pages = [re.fullmatch(r"([1-9])[/1l|Il]?([1-9])", _clean(t)) for t, _ in texts]
    pages = [m for m in pages if m and int(m.group(1)) <= int(m.group(2))]
    return (int(pages[0].group(1)), int(pages[0].group(2))) if len(pages) == 1 else None


def parse_cheer(texts: Texts) -> bool | None:
    """プロデュース選択 left banner 「アイドルの応援発生中！あと1/4回」 (second demo #195/#205) -> True; unread -> None."""
    return True if any("応援発生" in _clean(t) for t, _ in texts) else None


def parse_replace_confirm(texts: Texts) -> dict:
    """Full-screen スキル入れ替え確認 (catalog: 忘れるスキル name [416,152,300,30], new [416,334,300,30])."""
    def name_in(lo, hi):
        found = [_clean(t) for t, b in texts if 400 <= b[0] <= 720 and lo <= b[1] <= hi and len(_clean(t)) >= 2
                 and not re.search(r"忘れるスキル|新たに習得", _clean(t))]
        return found[0] if len(found) == 1 else None
    return {"forget": name_in(145, 190), "new": name_in(325, 372)}


def read_batch_c(screen: str, T, frame: Frame) -> dict:
    out: dict = {}
    if screen in ("Home", "HomeMinigamePopup"):
        bar = T("SC.Wing.Read.Home.Bar")
        out["home_produce_label"] = _label_at(bar, HOME_PRODUCE, _RECTS_C["home_produce"], 8)
        out["home_minigame_label"] = _label_at(bar, r"ミニゲーム", _RECTS_C["home_minigame_menu"], 12)
        out["popup_minigame"] = _label_at(T("SC.Wing.Read.Home.Popup"), r"^ミニゲーム$", _RECTS_C["home_minigame"], 8)
        # 2026-10-10: a suspended Produce turns the button into 「プロデュース中」 (restart3.png / after-restart.png).
        from agent.core import wing_resume
        out["home_produce_resume_label"] = wing_resume.label_at(bar, HOME_PRODUCE_RESUME,
                                                                wing_resume.RECTS["home_produce_resume"], 8)
    elif screen == "ResumeDialog":
        from agent.core import wing_resume
        out["resume_dialog"] = wing_resume.parse_resume_dialog(T("SC.Wing.Read.Resume.Panel"),
                                                               T("SC.Wing.Read.Resume.Buttons"))
        # 2026-10-14: the P card portrait (local card icons, strict; wing_nav verify_resume compares it with the P card)
        from agent.core import wing_unit_check
        portrait = wing_unit_check.read_unit_cards(frame.image, layout="resume_dialog")
        out["resume_dialog"]["portrait"] = (portrait or {}).get("P")
    elif screen == "ProduceSelectOther":
        out["train_tab"] = _label_at(T("SC.Wing.Read.ProduceSelect.Tabs"), r"育成(プ|ブ)ロデュース",
                                     _RECTS_C["produce_tab_train"], 6)
        from agent.core import halo_screen      # say "Halo" start (H2c): caption, 特化 tab, 次へ
        out.update(halo_screen.read_setup_select(T))
    elif screen == "MinigameCourse":
        out["course_first"] = _label_at(T("SC.Wing.Read.Minigame.CourseHead"), r"初心者", (150, 85, 250, 40), 6)
    elif screen == "MinigameRules":
        out["rules_page"] = parse_rules_page(T("SC.Wing.Read.Minigame.RulesPage"))
    elif screen == "MinigameGame":
        bar = T("SC.Wing.Read.Minigame.Bar")
        out["turn_bar"] = any(re.search(r"^(パス|出す|キャンセル)$", _clean(t)) for t, _ in bar)
        out["pass_enabled"] = pass_button_enabled(frame.image) if out["turn_bar"] else None
    elif screen == "MinigameResult":
        buttons = T("SC.Wing.Read.Minigame.ResultButtons")
        out["next_box"] = _box_of(buttons, r"^次(へ|人)$")
        out["produce_box"] = _box_of(buttons, r"^(プ|ブ)ロデュースへ$")
        out["cheer_notice"] = any("効果発生" in _clean(t) for t, _ in buttons)
    return out


def read_picker(image) -> dict:
    """Picker grid at its current scroll position: accepted card tiles (``card_inventory`` ``idol_list``), the grid
    offset and the scrollbar thumb (list top = offset 184 / thumb 191). Unreadable -> empty values."""
    out = {"picker_tiles": None, "picker_offset": None, "picker_thumb_top": None, "picker_at_bottom": None,
           "picker_scrollbar": None}
    if image is None:
        return out
    import numpy as np
    from PIL import Image
    from agent.core import card_list_scan, wing_unit_check
    index = wing_unit_check.card_index()
    rgb = np.asarray(image)[:, :, :3].astype("uint8")
    bar = card_list_scan.read_scrollbar(rgb)
    out["picker_thumb_top"] = bar.thumb_top
    # 2026-10-14 scroll scan (wing_team_edit.picker_step): end of list / still loading come from the same scrollbar.
    out["picker_at_bottom"] = bar.at_bottom if bar.status == "ok" else None
    out["picker_scrollbar"] = bar.status
    if index is None:
        return out
    from agent.core import card_inventory as ci
    try:
        result = ci.recognise(Image.fromarray(rgb), index, "idol_list")
    except ValueError:
        return out
    out["picker_offset"] = result.get("offset")
    # Top candidates per tile (coarse, fine) like wing_unit_check.read_unit_cards, so a tile the open-set matcher
    # rejected (e.g. the 「編成中」 badge, live 2026-10-11 #163/#172) can still be checked against the one wanted card.
    pil = Image.fromarray(rgb)
    tiles = []
    for t in result["tiles"]:
        x, y, w, h = t["box"]
        crop = pil.crop((x, y, x + w, y + h))
        row = index.coarse_scores([crop])[0]
        fine = ci._normalise(ci._vector(crop, ci.FINE_CROP, ci.FINE)[None])[0]
        order = [int(i) for i in np.argsort(row)[::-1][:wing_unit_check.CANDIDATES]]
        tiles.append({**{k: t.get(k) for k in ("slot", "status", "card_id", "score")},
                      "candidates": [[index.ids[i], round(float(row[i]), 4), round(float(fine @ index.fine[i]), 4)]
                                     for i in order]})
    out["picker_tiles"] = tiles
    return out


# Schedule fields that a room tap (select only, wing_nav select_room / preview_check) does not change: the header
# (season, weeks, fans to go), the status row (stats, SP, fans) and the promise flag. The next Schedule round reuses
# them (``read_screen(..., static=...)``, 2026-10-25 speed batch); rooms, the raised card, its preview, levels, band
# details and the trouble rate are read again (the raised card moves its label, level and band).
SCHEDULE_STATIC_KEY = "schedule_static"
HEADER_SCREENS = ("Hub", "HubBanner", "HubSemiFinal", "HubFinal", "Schedule", "AuditionTab", "AuditionLocked",
                  "ScheduleBusy", "SupportSkillCutIn")
STATUS_NODES = {"Schedule": "SC.Wing.Read.Schedule.StatusLesson", "AuditionTab": "SC.Wing.Read.Audition.Status",
                "AuditionLocked": "SC.Wing.Read.Audition.Status", "ScheduleBusy": "SC.Wing.Read.Schedule.StatusBusy"}
SP_PANEL_SCREENS = ("Review", "Owned", "ReplaceConfirm", "LearnConfirm")
WEEKS_NODE = "SC.Wing.Read.Hub.Weeks"


def read_header(evaluator: Evaluator, frame: Frame) -> dict:
    """Header 「シーズンN」, 「残り N 週」 and 「ファン人数あと N人」."""
    T = lambda name: evaluator.texts(name, frame) if name in evaluator.nodes else ()  # noqa: E731
    weeks = parse_weeks_left(T(WEEKS_NODE))
    if weeks is None and WEEKS_NODE in evaluator.nodes:
        # 2026-10-29 eval: the old ROI (y 5..70) cut the digit's bottom and the
        # detector found no box on 2 of 27 labelled frames; the padded ROI plus this one-line retry read 27/27.
        node = evaluator.nodes[WEEKS_NODE]
        weeks = parse_weeks_left(evaluator.backend.ocr({"roi": list(node["roi"]), "threshold": 0.3, "only_rec": True},
                                                       frame))
    return {"season": parse_season(T("SC.Wing.Read.Hub.Season")), "weeks_left": weeks,
            "fans_left": parse_fans_left(T("SC.Wing.Read.Hub.FansLeft"))}


def read_panel_sp(T) -> int | None:
    sp = [parse_int(t) for t, _ in T("SC.Wing.Read.Review.SP")]
    sp = [v for v in sp if v is not None]
    return sp[0] if len(sp) == 1 else None


def read_rank_fans(T) -> int | None:
    vals = [parse_int(t) for t, _ in T("SC.Wing.Read.Rank.Fans")]
    vals = [v for v in vals if v is not None]
    return vals[0] if len(vals) == 1 else None


def read_fes_sp(T) -> int | None:
    sp = [parse_int(t) for t, _ in T("SC.Wing.Read.FesBirth.SP")]
    return next((v for v in sp if v is not None), None)


def number_rois(screen: str | None, nodes: Mapping[str, Any], image=None) -> dict:
    """{field: ROI} the number readers of ``screen`` look at (the OCR evaluation sample records them)."""
    roi = lambda name: list(nodes[name]["roi"]) if name in nodes and "roi" in nodes[name] else None  # noqa: E731
    out: dict = {}
    if screen in HEADER_SCREENS:
        out.update(season=roi("SC.Wing.Read.Hub.Season"), weeks_left=roi(WEEKS_NODE),
                   fans_left=roi("SC.Wing.Read.Hub.FansLeft"))
    if screen in STATUS_NODES:
        row = roi(STATUS_NODES[screen])
        digits = status_digit_rois(image, row) if row is not None else None
        regions = status_regions(image, row) if row is not None else None
        out.update({k: list(digits[0]) if digits else row for k in STAT_ORDER})
        out["sp"] = list(digits[1]) if digits else row
        out["fans"] = list(digits[2]) if digits else (list(regions[1]) if regions else row)
    if screen in SP_PANEL_SCREENS:
        out["sp"] = roi("SC.Wing.Read.Review.SP")
    elif screen == "RankPyramid":
        out["fans"] = roi("SC.Wing.Read.Rank.Fans")
    elif screen == "FesBirth":
        out["sp"] = roi("SC.Wing.Read.FesBirth.SP")
    return out


def read_numbers(screen: str | None, evaluator: Evaluator, frame: Frame) -> dict:
    """Only the number fields ``read_screen`` reads on ``screen`` (season, weeks_left, fans_left, stats, sp, fans), with
    the same readers: the OCR evaluation (tools/evaluate_ocr_models.py --wing) scores exactly what the runtime reads."""
    T = lambda name: evaluator.texts(name, frame) if name in evaluator.nodes else ()  # noqa: E731
    out: dict = {}
    if screen in HEADER_SCREENS:
        out.update(read_header(evaluator, frame))
    if screen in STATUS_NODES:
        out.update(read_status(evaluator, frame, STATUS_NODES[screen]))
    if screen in SP_PANEL_SCREENS:
        out["sp"] = read_panel_sp(T)
    elif screen == "RankPyramid":
        out["fans"] = read_rank_fans(T)
    elif screen == "FesBirth":
        out["sp"] = read_fes_sp(T)
    return out


def read_screen(screen: str | None, evaluator: Evaluator, frame: Frame, static: Mapping | None = None) -> dict:
    """Read every scorer field the frame allows for ``screen``. Unknown values stay None.

    Schedule only: ``static`` (the ``SCHEDULE_STATIC_KEY`` value of the round before a room tap) replaces the header,
    status and promise reads; the result always carries the key for the next round."""
    # A reader missing from the loaded Pipeline (a newer Python on an older generated wing.json) reads nothing: the
    # decision then sees an unread value and stops instead of raising.
    T = lambda name: evaluator.texts(name, frame) if name in evaluator.nodes else ()  # noqa: E731
    out: dict = {}
    from agent.core import halo_screen
    if screen in halo_screen.HALO_READ_SCREENS:
        return halo_screen.read(screen, evaluator, frame)
    static = dict(static) if screen == "Schedule" and isinstance(static, Mapping) else None
    if screen in HEADER_SCREENS:
        if static is not None:
            out.update({k: static["header"][k] for k in ("season", "weeks_left", "fans_left")})
        else:
            out.update(read_header(evaluator, frame))
        fill = stamina_fill(frame.image)
        out["stamina_pct"] = None if fill is None else round(fill * 100)
    if screen == "Schedule":
        status = (copy.deepcopy(static["status"]) if static is not None
                  else read_status(evaluator, frame, STATUS_NODES[screen]))
        out.update(status)
        rooms = read_rooms(evaluator, frame)
        parse_room_levels(T("SC.Wing.Read.Schedule.RoomLevels"), rooms)
        out["rooms"] = rooms
        out["selected_room"] = next((r["id"] for r in rooms if r["selected"]), None)
        # The preview row belongs to the selected room; bound only when exactly one room is raised.
        selected = [r for r in rooms if r["selected"]]
        out["selected_preview"] = read_preview(evaluator, frame) if len(selected) == 1 else None
        if out["selected_preview"] is not None:
            room = selected[0]
            room["preview"] = dict(out["selected_preview"]["preview"])
            for key in ("sp_gain", "fan_gain"):
                if out["selected_preview"][key] is not None:
                    room[key] = out["selected_preview"][key]
        out["promise"] = static["promise"] if static is not None else parse_promise(T("SC.Wing.Read.Schedule.Promise"))
        out[SCHEDULE_STATIC_KEY] = {"header": {k: out[k] for k in ("season", "weeks_left", "fans_left")},
                                    "status": copy.deepcopy(status), "promise": out["promise"],
                                    "reused": static is not None}
        details = room_details(T("SC.Wing.Read.Schedule.RoomBand"), frame.image)
        out["room_details"] = details
        for room in rooms:          # attach to the label-read rooms (level from the band reader when unread)
            d = next((x for x in details if x["id"] == room["id"]), None)
            if d:
                room.update({k: d[k] for k in ("excellent", "supports", "promise")})
                if room.get("level") is None and isinstance(d["level"], int):
                    room["level"] = d["level"]
        out["room_levels"] = [d["level"] for d in details]
        out["room_supports"] = [d["supports"] for d in details] if details[0]["supports"] is not None else None
        out["room_excellent"] = [d["excellent"] for d in details]
        out["room_promise"] = [d["promise"] for d in details] if details[0]["promise"] is not None else None
        out["trouble_rate"] = parse_int(" ".join(t for t, _ in T("SC.Wing.Read.Schedule.Trouble")))
        # 2026-10-12 support identity (agent/core/wing_support_id.py): one face signature per support per room.
        from agent.core import wing_support_id as wsi
        out["room_avatar_sigs"] = wsi.room_avatar_signatures(frame.image) if frame.image is not None else None
    elif screen in ("AuditionTab", "AuditionLocked"):   # 2026-10-09: the 出場条件 overlay leaves the panel readable
        out.update(read_status(evaluator, frame, STATUS_NODES[screen]))
        panel = T("SC.Wing.Read.Audition.Panel")
        out.update(parse_audition(panel))
        if out.get("special") is None and any(SPECIAL_AUDITION_MARKER.search(_clean(t)) for t, _ in panel):
            # 2026-10-28 live S3 (wing_samples/2026-10-28/20261028T140150361610Z): the logo name is unreadable, the
            # 「特殊ルール」 button is not; each season has one limited audition.
            out["special"] = SPECIAL_BY_SEASON.get(out.get("season"))
        out["recommended"] = parse_recommended(T("SC.Wing.Read.Audition.Recommended"))
        if out.get("trend") is None:
            anchor = next((b for t, b in panel if _clean(t) == "流行"), None)
            out["trend"] = trend_from_pixels(frame.image, anchor)
    elif screen == "ScheduleBusy":
        out.update(read_status(evaluator, frame, STATUS_NODES[screen]))
    elif screen in ("Review", "Owned", "ReplaceConfirm", "LearnConfirm"):
        from agent.core import wing_skill_panel as wsp
        out["sp"] = read_panel_sp(T)
        if screen == "Review":
            out["skill_info"] = parse_skill_info(T("SC.Wing.Read.Review.Info"))
            out["skill_text"] = (wsp.parse_info_text(T("SC.Wing.Read.Review.InfoText"))
                                 if out["skill_info"] is not None else None)
            nodes = skill_nodes(T("SC.Wing.Read.Review.Full"), frame.image)
            out["skill_nodes"] = nodes
            out["skill_node_counts"] = skill_node_counts(nodes) if nodes else None
            out["skill_learned"] = out["skill_node_counts"]["learned"] if nodes else None
            from agent.core import wing_sp_view
            out["panel"] = wing_sp_view.read_panel(nodes)
        if screen == "Owned":
            out["owned"] = parse_owned_stats(T("SC.Wing.Read.Owned.Stats"))
        if screen == "ReplaceConfirm":
            out["replace"] = parse_replace_confirm(T("SC.Wing.Read.ReplaceConfirm.Body"))
        if screen == "LearnConfirm":
            out["learn_confirm"] = wsp.parse_learn_confirm(T("SC.Wing.Read.LearnConfirm.Body"), sp=out["sp"])
    elif screen == "ForgetSelect":
        from agent.core import wing_skill_panel as wsp
        out["forget_rows"] = wsp.parse_forget_rows(T("SC.Wing.Read.ForgetSelect.Rows"))
    elif screen == "ReplaceDialog":
        from agent.core import wing_skill_panel as wsp
        out["replace"] = wsp.parse_replace_dialog(T("SC.Wing.Read.ReplaceDialog.Body"))
    elif screen == "Live":
        out.update(read_live(T("SC.Wing.Read.Live.Full"), frame.image))
        tip = out.get("tooltip")
        if out.get("live_phase") == "select_judge" and (not tip or tip.get("multiplier") is None):
            # 2026-10-28 live (かわいいひと 「Vocal2.5倍アピール[興味無視]/興味0.7倍[1ターン]」): the whole-frame OCR merged the
            # two small description lines into garbage; OCR of the tooltip box alone reads them.
            from agent.core import wing_live_policy as wlp
            box = wlp.parse_tooltip(evaluator.backend.ocr({"roi": list(LIVE_TOOLTIP_ROI), "threshold": 0.3}, frame))
            if box and box.get("multiplier") is not None:
                out["tooltip"] = box
    elif screen == "LiveSkillCutIn":
        out.update(parse_live(T("SC.Wing.Read.Live.State")))
    elif screen == "RankPyramid":
        out["fans"] = read_rank_fans(T)
    elif screen == "Choice":
        out["options"] = parse_choice({s: T(f"SC.Wing.Read.Choice.{s}") for s in ("Left", "Center", "Right")})
    elif screen == "FesBirth":
        out["sp"] = read_fes_sp(T)
        out.update(read_end(screen, T("SC.Wing.Read.End.Full")))
    elif screen in ("Reward", "RewardDetail"):
        out.update(read_end(screen, T("SC.Wing.Read.End.Full"), image=frame.image))
        out["reroll"] = parse_reward_reroll(T("SC.Wing.Read.Reward.Reroll"))
        out["stamina_now"] = parse_int((out.get("stamina") or "").split("/")[0]) if out.get("stamina") else None
    elif screen == "RewardRerollConfirm":
        out["texts"] = [t for t, _ in T("SC.Wing.Read.End.Full")]
        out["reroll_dialog"] = parse_reward_reroll(T("SC.Wing.Read.End.Full"))
    elif screen in BATCH_C_SCREENS:
        out.update(read_batch_c(screen, T, frame))
    elif screen == "UnitCheck":         # mid-Produce プロデュースユニット確認: same card icons as ユニット編成 (#239)
        from agent.core import wing_unit_check
        out["unit_cards"] = wing_unit_check.read_unit_cards(frame.image)
    elif screen == "ActiveSkills":      # 発動中スキル popup: per room the supports' faces and active skills
        from agent.core import wing_support_id as wsi
        out["active_skills"] = (wsi.read_active_skills(T("SC.Wing.Read.ActiveSkills.List"), frame.image)
                                if frame.image is not None else None)
    elif screen == "IdolPicker":        # team edit picker (agent/core/wing_team_edit.py)
        from agent.core import wing_team_edit as wte
        out["picker_kind"] = wte.picker_kind(T("SC.Wing.Read.IdolPicker.Title"))
        out.update(read_picker(frame.image))
        from agent.core import picker_sort as ps    # 2026-10-14: sort header (key / direction / filter)
        out["picker_header"] = ps.parse_header(T("SC.Wing.Read.IdolPicker.SortKey"),
                                               T("SC.Wing.Read.IdolPicker.Direction"),
                                               T("SC.Wing.Read.IdolPicker.FilterText")).to_dict()
    elif screen == "PickerSort":        # 並び替え popup over the picker (agent/core/picker_sort.py)
        from agent.core import picker_sort as ps
        out["sort_popup_items"] = [[t, [int(v) for v in b]] for t, b in T("SC.Wing.Read.PickerSort.Popup")]
        if frame.image is not None:
            import numpy as np
            rgb = np.asarray(frame.image)[:, :, :3]
            out["sort_selected"] = {f"r{c[0]}c{c[1]}": round(ps.selected_share(rgb, c), 4) for c in ps.KEY_CELLS.values()}
    elif screen == "UnitSelect":
        from agent.core import wing_unit_check
        out["unit_cards"] = wing_unit_check.read_unit_cards(frame.image)
        out.update(parse_unit(T("SC.Wing.Read.UnitSelect.Counter"), T("SC.Wing.Read.UnitSelect.Tabs")))
    elif screen == "ProduceSelect":
        out["difficulty"] = produce_difficulty(frame.image)
        out["cheer"] = parse_cheer(T("SC.Wing.Read.ProduceSelect.Cheer"))
        from agent.core import halo_screen
        out["special_tab"] = halo_screen.read_setup_select(T)["special_tab"]
    elif screen == "ItemSelect":
        out["items_empty"] = item_slots_empty(T("SC.Wing.Read.ItemSelect.Slots"))
    elif screen == "StartConfirm":
        out["start_confirm"] = parse_start_confirm(T("SC.Wing.Read.StartConfirm.Body"),
                                                   T("SC.Wing.Read.StartConfirm.Panel"))
    elif screen == "Tutorial":
        page = [re.fullmatch(r"([1-7])[/1l|]?([1-7])", _clean(t)) for t, _ in T("SC.Wing.Read.Tutorial.Page")]
        page = [m for m in page if m]
        out["tutorial_page"] = (int(page[0].group(1)), int(page[0].group(2))) if len(page) == 1 else None
    elif screen == "TrueEndCommu":
        out["true_end"] = True
    elif screen == "AbandonDialog":     # say "Halo" give-up (wing_nav._decide_abandon): hint + button on this frame
        out["abandon_hold_hint"] = any("長押し" in _clean(t) for t, _ in T("SC.Wing.Read.Abandon.Hold"))
        out["abandon_button"] = any(_clean(t) == "諦める" for t, _ in T("SC.Wing.Read.Abandon.Button"))
    elif screen == "Options":           # オプション → プロデュース設定 rows (agent/core/wing_options.py)
        from agent.core import wing_options as wo
        rows = T("SC.Wing.Read.Options.List")
        out["produce_options"] = wo.read_toggles(rows, frame.image)
        out["produce_options_rows"] = wo.row_tops(rows)          # list position (two-frame settle, list end)
        dialog = T("SC.Wing.Read.Options.Dialog")
        out["options_dialog"] = wo.dialog_text(dialog)
        out["options_saved"] = wo.saved_dialog(dialog)         # 「設定を保存しました。」 after 保存 (live 10-25)
    if screen in END_SCREENS and screen not in ("FesBirth", "Reward", "RewardDetail"):
        spl = T("SC.Wing.Read.FesBirth.SPLost") if screen == "FesBirthConfirm" else ()
        out.update(read_end(screen, T("SC.Wing.Read.End.Full"), spl))
    if screen == "MissionPopup":
        # 2026-10-10 (live seg15): the login-mission popup also covers a home with a Produce still in progress; the
        # home button under it (「プロデュース中」 / 「プロデュース」, startup7.png) tells the two apart.
        from agent.core import wing_resume
        bar = T("SC.Wing.Read.Home.Bar")
        out["home_produce_resume_label"] = wing_resume.label_at(bar, HOME_PRODUCE_RESUME,
                                                                wing_resume.RECTS["home_produce_resume"], 8)
        out["home_produce_label"] = _label_at(bar, HOME_PRODUCE, _RECTS_C["home_produce"], 8)
    if screen in ("Hub", "HubSemiFinal", "HubFinal"):
        out.update(read_hub_identity(T))
    return out


def parse_hub_speaker(texts: Texts) -> str | None:
    """Hub speech bubble speaker (the P idol's given name, second demo #459/#515/#1080/#1480: 「灯織」 at (837,342))."""
    names = [_clean(t) for t, b in texts if 2 <= len(_clean(t)) <= 6 and not re.search(r"[\dA-Za-z!！?？。、…]", _clean(t))]
    return names[0] if len(names) == 1 else None


def read_hub_identity(T) -> dict:
    """Mid-Produce detection (2026-10-09c): P idol from the bubble, アイドルの応援 banner (#459 (49,427))."""
    return {"hub_speaker": parse_hub_speaker(T("SC.Wing.Read.Hub.Speaker")),
            "hub_cheer": any("応援発生" in _clean(t) for t, _ in T("SC.Wing.Read.Hub.Cheer")) or None}
