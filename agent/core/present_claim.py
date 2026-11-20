"""プレゼント (present box) bulk receive: pure page parser, decision, result classifier and ledger (no Maa import).

The task receives the items that are already in the present box with one 「一括受取」 press. Receiving never spends
and never chooses an item. 「领取奖励最不该出错 有东西就直接点一键领取 没有就跳过」 -- whatever the rows
are (unknown or choice items included), an enabled 「一括受取」 on the proven 未受け取り page is pressed; a grey or missing
button is a skip, not a failure.

Evidence status: native frames in ``dev_assets/private/fes-match-reader-20261007/`` prove the
unclaimed and received tabs, two complete rows, bulk confirmation, positive receipt and empty box. More than
three rows, overflow and error dialogs remain unsampled. ``LAYOUT["verified_states"]`` records native coverage.

Design:

- ``parse_page(ocr_items)`` groups OCR text into rows by their per-row receive button and collects page-level
  warning words. It does not use screen order to decide anything except row grouping.
- ``decide(page, ...)`` returns ``claim`` when the page guard, the 未受け取り tab, no overlay, no game error text and one
  enabled 「一括受取」 hold; ``empty`` (skip) when that button is grey or absent; ``stop`` only when the page itself is
  not proven.
- ``classify_result(ocr_items)`` and ``confirm(before, after)`` turn the post-press dialog and the next fresh page
  into ``received`` / ``received_partial`` / ``overflow`` / ``error`` / ``uncertain``.
- ``PresentLedger`` persists one record per attempt (``pending`` before the press) as evidence. For presents it never
  blocks a later press (``BLOCK_OPEN_ATTEMPTS``); the mission bulk ledger blocks only within the run that wrote the
  record (``block_reason(cycle, since)``; an earlier uncertain result only warns).
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

JST = _dt.timezone(_dt.timedelta(hours=9), "JST")
DAILY_RESET_HOUR = 5
CANVAS = (1280, 720)
PRIVATE = Path(__file__).resolve().parents[2] / "dev_assets" / "private"
LEDGER_PATH = PRIVATE / "present" / "ledger.json"

# Layout measured on the native unclaimed-list frames (see module docstring). OCR boxes on those frames:
# title [49,18,127,36]; tab labels 未受け取り [33,108,135,33] / 受け取り済 [29,213,145,37]; row 1 name [394,113,527,27],
# description [404,150,612,23], 獲得日時/受取期限 line y 201-227, 「受け取る」 [1061,153,127,37]; row 2 shifted by 138 px;
# 「未受取プレゼント」 [1010,578,148,22] + 「2件」 [1209,575,44,27]; footer [293,637,408,23]+[293,660,447,24];
# 「一括受取」 [1132,641,110,39] (pink 1099-1255 x 644-694); back key [20,625,77,75].
LAYOUT = {
    "verified": True,                 # geometry of the unclaimed list only; see verified_states
    "verified_states": ("unclaimed_list", "received_tab", "empty_box", "bulk_disabled", "confirm_dialog", "result_dialog"),
    "unverified_states": ("scrolled_list", "overflow_warning", "error_dialog"),
    "home_button": {"rect": [28, 162, 70, 50], "source": "fes-match-reader-20261007/home_after_present_settled.png"},
    "title_roi": [38, 10, 220, 52],
    "tab_text_roi": {"unclaimed": (28, 100, 150, 48), "received": (24, 205, 155, 52)},
    "list_roi": [240, 95, 1020, 470],          # rows only; the counter strip below y 565 is read separately
    "count_roi": [990, 566, 272, 42],
    "footer_roi": [285, 628, 480, 62],
    "bulk_roi": [1095, 622, 165, 80],
    "dialog_roi": [300, 120, 680, 480],
    "row_button_x": 1030,
    "row_text_x": 385,                         # icon art (「10連」「TRAINING」「x1」) lies left of this
    "row_line_x_max": 430,                     # name / description lines start at x 394-404 (left-aligned)
    "row_band": (-60, 60),
    "name_band": (-62, -28),                   # name line centre is 45 px above the button centre
    "deadline_band": (25, 62),
    "row_pitch": 138,
    "visible_rows_max": 3,                     # list height 470 / pitch 138: three complete rows fit
    # 2026-10-10 native 12-item list (wing_samples/2026-10-10/20261010T122051819768Z): scrollbar track x 1242,
    # dark y 108..563, white thumb y 111..237 at the list top; the 4th row is cut at the list edge.
    "scroll_x": 1242,
    "scroll_track": (109, 564),
}
TITLE_TEXTS = ("プレゼント", "プレゼントボックス")
BULK_TEXTS = ("一括受取", "一括受け取り", "一括受け取る")
ROW_BUTTON_TEXTS = ("受け取る", "受取", "受け取り")
EMPTY_PATTERNS = (re.compile(r"プレゼントは?(ありません|ございません)"), re.compile(r"受け取れるプレゼントは?(ありません|ございません)"))
# Warning words of the page / result dialog. Item names and descriptions (選択, 使用, 交換 ...) are not warnings
# (2026-10-27: a セレクトプレゼント row stopped the daily run), so only limit and game-error words are read.
BLOCKING_WORDS = {
    "上限": "limit_warning", "所持数": "limit_warning", "受け取れません": "cannot_receive",
    "受け取れない": "cannot_receive", "エラー": "error", "通信": "network", "メンテナンス": "maintenance",
}
HARD_REASONS = frozenset({"error", "network", "maintenance"})   # the game itself failed: never press on it
# Item kinds observed as mission/serial/campaign rewards (wikiwiki ミッション 2026-10-02; official serial page).
ITEM_KINDS = (
    ("jewel", ("フェザージュエル", "ジュエル")),
    ("money", ("マニー",)),
    ("training_ticket", ("トレーニングチケット",)),
    ("skip_ticket", ("スキップチケット",)),
    ("gacha_ticket", ("ガシャチケット", "ガシャ券")),
    ("select_ticket", ("セレクトチケット", "セレチケ")),
    ("fes_ticket", ("フェス参加権", "フェスチケット")),
    ("stamina_item", ("ドリンク", "スタミナ", "ソーダ")),
    ("piece", ("ピース",)),
    ("coin", ("コイン",)),
    ("key", ("鍵",)),
    # Free W.I.N.G. produce items (2026-10-11 native 31: 「283プロのタオル」 stamp-card reward); the bulk receipt names
    # this whole category with the generic label 「プロデュースアイテム」 (native 32).
    # Other stems are the produce item names already proven in item_inventory.PRODUCE_ITEMS / shop towels.
    ("produce_item", ("プロデュースアイテム", "タオル", "タルト", "アロマ", "流行雑誌", "メモ帳", "ノウハウ目録",
                      "しおり", "お守り")),
)
PROGRESS_PAT = re.compile(r"^[x×]\s*\d+$|^\d+$")
QUANTITY_PAT = re.compile(r"^(?P<name>.+?)\s*[x×X]\s*(?P<qty>\d{1,7})$")
COUNT_PAT = re.compile(r"^(\d{1,3})件$")
FOOTER_TEXTS = ("受け取れるプレゼントは100件まで", "期限が切れたプレゼントは自動的に削除")   # OCR also reads 一度 as ー度
FIXED_TEXTS = ("期限が切れたプレゼントは自動的に削除されます", "受け取れるプレゼントは100件までです")
DATE_PAT = re.compile(r"(\d{4})/(\d{2})/(\d{2})\s*(\d{2}):(\d{2})")
# Pink of the selected tab label and of enabled pink buttons (RGB), measured on the native unclaimed list:
# 未受け取り label 1362 px / 受け取り済 label 0 px; 一括受取 4469 px of bulk_roi.
PINK_LOWER = (230, 40, 110)
PINK_UPPER = (255, 140, 200)
TAB_SELECTED_MIN = 400
TAB_UNSELECTED_MAX = 40


def _box(item):
    box = item.get("box") if isinstance(item, Mapping) else None
    if not (isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
        return None
    return [int(v) for v in box]


def _items(results: Iterable[Mapping[str, Any]] | None):
    out = []
    for item in results or []:
        box = _box(item)
        text = item.get("text") if isinstance(item, Mapping) else None
        if box is None or not isinstance(text, str) or not text.strip():
            continue
        out.append({"text": text.strip().replace(" ", ""), "box": box})
    return out


def _inside(box, roi):
    x, y, w, h = box
    rx, ry, rw, rh = roi
    cx, cy = x + w / 2, y + h / 2
    return rx <= cx <= rx + rw and ry <= cy <= ry + rh


def item_kind(name: str) -> str | None:
    for kind, words in ITEM_KINDS:
        if any(word in name for word in words):
            return kind
    return None


def blocking_reasons(texts: Iterable[str]) -> list[str]:
    reasons = []
    for text in texts:
        if any(fixed in text for fixed in FIXED_TEXTS):
            continue           # the page's own permanent notice (expiry / 100-per-press) is not a warning
        for word, reason in BLOCKING_WORDS.items():
            if word in text and reason not in reasons:
                reasons.append(reason)
    return reasons


def cycle_id(now: _dt.datetime) -> str:
    """JST date of the daily cycle (05:00 JST boundary)."""
    if now.tzinfo is None:
        raise ValueError("now 必须带时区")
    local = now.astimezone(JST) - _dt.timedelta(hours=DAILY_RESET_HOUR)
    return local.date().isoformat()


# ---- page ----------------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class PresentPage:
    title: bool
    rows: tuple                 # ({"name", "quantity", "kind", "button", "deadline", "y"}, ...)
    bulk_buttons: int           # number of 「一括受取」 texts in the bulk ROI
    empty_message: bool
    blockers: tuple
    footer: bool = False        # the fixed expiry / 100-per-press notice of the present page
    count: int | None = None    # 「未受取プレゼント N件」 (None: not read)
    tab: str | None = None      # "unclaimed" | "received" | None, from pixels (see ``tab_selection``)

    def to_dict(self):
        return dataclasses.asdict(self) | {"rows": [dict(r) for r in self.rows]}


def _row_name(texts, by, layout):
    """The quantity line (``<item> ×N``) in the name band above the row button; topmost wins, never the description."""
    low, high = layout["name_band"]
    cands = sorted((i for i in texts if low <= (i["box"][1] + i["box"][3] / 2) - by <= high), key=lambda i: i["box"][1])
    for item in cands:
        match = QUANTITY_PAT.match(item["text"])
        if match:
            return match.group("name"), int(match.group("qty"))
    return None, None


def _row_obtained(texts, by, layout):
    """「獲得日時 YYYY/MM/DD hh:mm」 (left half of the date line): part of the row identity, never a decision input."""
    low, high = layout["deadline_band"]
    line = [i for i in texts if low <= (i["box"][1] + i["box"][3] / 2) - by <= high and i["box"][0] < 700]
    for item in sorted(line, key=lambda i: i["box"][0]):
        match = DATE_PAT.search(item["text"])
        if match and "まで" not in item["text"]:
            y, mo, d, h, mi = (int(g) for g in match.groups())
            return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}+09:00"
    return None


def _row_deadline(texts, by, layout):
    low, high = layout["deadline_band"]
    line = [i for i in texts if low <= (i["box"][1] + i["box"][3] / 2) - by <= high and i["box"][0] >= 780]
    for item in sorted(line, key=lambda i: i["box"][0]):
        if item["text"] == "期限なし":
            return "none"
        match = DATE_PAT.search(item["text"])
        if match and "まで" in item["text"]:
            y, mo, d, h, mi = (int(g) for g in match.groups())
            return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}+09:00"
    return None


def _row_lines(texts, buttons, layout):
    """Ids of each row's name and description lines (left-aligned in its text column): item content, never a page warning
    (2026-10-31: the description 「メンテナンス協力のお礼です」 of a jewel row stopped the daily run as a game maintenance
    error). Text elsewhere on the page, including a dialog over the list, is still read for warnings."""
    low, high = layout["name_band"][0], layout["deadline_band"][0]
    lines = set()
    for button in buttons:
        by = button["box"][1] + button["box"][3] / 2
        lines.update(id(i) for i in texts
                     if i["box"][0] <= layout["row_line_x_max"] and low <= (i["box"][1] + i["box"][3] / 2) - by < high)
    return lines


def parse_count(results, layout: Mapping[str, Any] = LAYOUT) -> int | None:
    """「未受取プレゼント N件」 -> N; None unless the label and exactly one count are in ``count_roi``."""
    items = [i for i in _items(results) if _inside(i["box"], layout["count_roi"])]
    if not any("未受取プレゼント" in i["text"] for i in items):
        return None
    counts = [int(m.group(1)) for m in (COUNT_PAT.match(i["text"]) for i in items) if m]
    return counts[0] if len(counts) == 1 else None


def parse_page(results, layout: Mapping[str, Any] = LAYOUT, *, tab: str | None = None) -> PresentPage:
    items = _items(results)
    title = any(_inside(i["box"], layout["title_roi"]) and i["text"] in TITLE_TEXTS for i in items)
    bulk = sum(1 for i in items if _inside(i["box"], layout["bulk_roi"]) and i["text"] in BULK_TEXTS)
    footer = any(_inside(i["box"], layout["footer_roi"]) and any(t in i["text"] for t in FOOTER_TEXTS) for i in items)
    in_list = [i for i in items if _inside(i["box"], layout["list_roi"])]
    buttons = sorted((i for i in in_list if i["box"][0] >= layout["row_button_x"] and i["text"] in ROW_BUTTON_TEXTS),
                     key=lambda i: i["box"][1])
    texts = [i for i in in_list if layout["row_text_x"] <= i["box"][0] < layout["row_button_x"]]
    rows = []
    for button in buttons:
        by = button["box"][1] + button["box"][3] / 2
        name, quantity = _row_name(texts, by, layout)
        rows.append({"name": name, "quantity": quantity, "kind": None if name is None else item_kind(name),
                     "button": button["text"], "deadline": _row_deadline(texts, by, layout),
                     "obtained": _row_obtained(texts, by, layout), "y": int(by)})
    empty = any(p.search(i["text"]) for i in items for p in EMPTY_PATTERNS)
    item_lines = _row_lines(texts, buttons, layout)
    warnings = blocking_reasons(i["text"] for i in items if id(i) not in item_lines)
    return PresentPage(title, tuple(rows), bulk, empty, tuple(warnings),
                       footer=footer, count=parse_count(results, layout), tab=tab)


def pink_count(image_rgb, roi) -> int:
    """Pixels of the enabled-pink range (RGB) inside ``roi`` of an HxWx3 RGB array."""
    import numpy as np

    x, y, w, h = roi
    region = np.asarray(image_rgb)[y:y + h, x:x + w, :3].astype(int)
    return int(((region >= PINK_LOWER) & (region <= PINK_UPPER)).all(axis=2).sum())


def tab_selection(image_rgb, layout: Mapping[str, Any] = LAYOUT) -> dict:
    """Pink label pixels of both tabs; ``selected`` only when one is clearly pink and the other clearly not."""
    import numpy as np

    image = np.asarray(image_rgb)
    if image.ndim != 3 or image.shape[2] < 3 or (image.shape[1], image.shape[0]) != CANVAS:
        return {"unclaimed": None, "received": None, "selected": None}
    counts = {name: pink_count(image, roi) for name, roi in layout["tab_text_roi"].items()}
    selected = None
    if counts["unclaimed"] >= TAB_SELECTED_MIN and counts["received"] <= TAB_UNSELECTED_MAX:
        selected = "unclaimed"
    elif counts["received"] >= TAB_SELECTED_MIN and counts["unclaimed"] <= TAB_UNSELECTED_MAX:
        selected = "received"
    return {**counts, "selected": selected}


# ---- scrolled list scan -------------------------------------------------------------------------
# 「一括受取」 receives every unclaimed row (newest 100), so a box longer than one screen is read completely first:
# bounded wheel steps downwards, only wholly visible rows, exactly one identical overlap between consecutive pages,
# the scrollbar thumb moving down and the 「未受取プレゼント N件」 counter unchanged. No input is decided here.
SCROLL_TOP_MAX = 113          # thumb top at (or within 2 px of) the track top: the list start
SCROLL_BOTTOM_MIN = 561       # thumb bottom at the track bottom: the list end
BULK_LIMIT = 100              # 「一度に受け取れるプレゼントは100件まで」


def row_complete(row, layout: Mapping[str, Any] = LAYOUT) -> bool:
    """The whole row (name line, button and date line) lies inside the list viewport."""
    top = layout["list_roi"][1]
    bottom = top + layout["list_roi"][3]
    return isinstance(row.get("y"), int) and top <= row["y"] - 62 and row["y"] + 62 <= bottom


def row_key(row) -> tuple:
    return (row.get("name"), row.get("quantity"), row.get("obtained"), row.get("deadline"))


def scroll_thumb(image_rgb, layout: Mapping[str, Any] = LAYOUT):
    """(top, bottom) of the white scrollbar thumb inside the dark track; None when no single thumb is drawn."""
    import numpy as np

    image = np.asarray(image_rgb)
    if image.ndim != 3 or (image.shape[1], image.shape[0]) != CANVAS:
        return None
    top, bottom = layout["scroll_track"]
    column = image[top:bottom + 1, layout["scroll_x"], :3].astype(int)
    white = [top + i for i, pixel in enumerate(column) if int(pixel.min()) >= 245]
    dark = [i for i, pixel in enumerate(column) if int(pixel.max()) <= 140]
    if not white or not dark or white[-1] - white[0] + 1 != len(white) or len(white) < 20:
        return None
    return white[0], white[-1]


class PresentScan:
    """Ordered, overlapping pages of the unclaimed list -> the complete row list (read-only, no input)."""

    def __init__(self, count: int, max_steps: int):
        if type(count) is not int or count <= 0 or type(max_steps) is not int or max_steps < 0:
            raise ValueError("プレゼント 滚动读取参数无效")
        self.count, self.max_steps = count, max_steps
        self.rows: list = []
        self.thumb = None
        self.pages = 0
        self.steps = 0
        self.blockers: list = []
        self.reason: str | None = None
        self.complete = False

    def add(self, page: PresentPage, thumb) -> bool:
        if self.reason or self.complete:
            return False
        if page.count != self.count:
            self.reason = "count_changed" if page.count is not None else "count_unread"
            return False
        if thumb is None:
            self.reason = "scrollbar_unread"
            return False
        rows = [r for r in page.rows if row_complete(r)]
        if not rows:
            self.reason = "no_complete_rows"
            return False
        if any(r["name"] is None or type(r["quantity"]) is not int for r in rows):
            self.reason = "row_name_unread"
            return False
        keys = [row_key(r) for r in rows]
        if self.pages == 0:
            if thumb[0] > SCROLL_TOP_MAX:
                self.reason = "list_start_not_observed"
                return False
            merged = list(rows)
        else:
            if thumb[0] < self.thumb[0]:
                self.reason = "scroll_direction_changed"
                return False
            if thumb == self.thumb:
                self.reason = "scroll_no_effect"
                return False
            known = [row_key(r) for r in self.rows]
            overlaps = [k for k in range(1, min(len(known), len(keys)) + 1) if known[-k:] == keys[:k]]
            if not overlaps:
                self.reason = "overlap_lost"
                return False
            if len(overlaps) > 1:
                self.reason = "overlap_ambiguous"
                return False
            merged = self.rows + rows[overlaps[0]:]
        self.rows, self.thumb = merged, thumb
        self.pages += 1
        self.blockers = list(dict.fromkeys([*self.blockers, *page.blockers]))
        if len(self.rows) > self.count:
            self.reason = "count_rows_mismatch"
            return False
        if len(self.rows) == self.count:
            self.complete = True
        elif thumb[1] >= SCROLL_BOTTOM_MIN:
            self.reason = "count_rows_mismatch"     # the list end shows fewer rows than the counter
            return False
        return True

    def wants_scroll(self) -> bool:
        if self.reason or self.complete:
            return False
        if self.steps >= self.max_steps:
            self.reason = "scroll_budget_exhausted"
            return False
        return True

    def merged_page(self, visible: PresentPage) -> PresentPage:
        """The complete list with the page-level facts (title/footer/tab/bulk/blockers) of the final visible page."""
        return dataclasses.replace(visible, rows=tuple(dict(r) for r in self.rows),
                                   blockers=tuple(dict.fromkeys([*self.blockers, *visible.blockers])))

    def to_dict(self):
        return {"count": self.count, "rows_read": len(self.rows), "pages": self.pages, "steps": self.steps,
                "complete": self.complete, "reason": self.reason, "thumb": self.thumb,
                "rows": [{k: r.get(k) for k in ("name", "quantity", "kind", "obtained", "deadline")} for r in self.rows]}


# ---- options -------------------------------------------------------------------------------------------------
# Options removed 2026-10-27 (daily cap, unknown/choice item stops, full-list scan); still accepted and ignored so an
# older pipeline override cannot fail the task.
RETIRED_OPTIONS = frozenset({"max_claims_per_day", "allow_choice_items", "stop_on_unknown_item", "max_scroll_steps",
                             "verify_all_rows"})


@dataclasses.dataclass(frozen=True)
class PresentOptions:
    enabled: bool = False

    @classmethod
    def from_params(cls, data: Mapping[str, Any] | None) -> "PresentOptions":
        data = {k: v for k, v in dict(data or {}).items() if k not in RETIRED_OPTIONS}
        unknown = set(data) - {f.name for f in dataclasses.fields(cls)}
        if unknown:
            raise ValueError(f"プレゼント 选项未知: {sorted(unknown)}")
        options = cls(**data)
        if type(options.enabled) is not bool:
            raise ValueError("enabled 必须为布尔值")
        return options


# ---- frame binding -------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class FrameRef:
    sha256: str
    seq: int
    captured_at: float           # monotonic seconds

    def valid(self) -> bool:
        return (isinstance(self.sha256, str) and len(self.sha256) == 64 and type(self.seq) is int and self.seq > 0
                and isinstance(self.captured_at, (int, float)))


MAX_FRAME_AGE_S = 5.0


# ---- decision ------------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Decision:
    kind: str                    # "claim" | "empty" | "stop"
    reasons: tuple
    target: str | None = None    # "present-bulk-receive" when kind == "claim"
    frame: FrameRef | None = None
    rows: int = 0
    input_authorized: bool = False   # never True in this module; a sender must re-verify (see Intent action)

    def to_dict(self):
        out = dataclasses.asdict(self)
        out["reasons"] = list(self.reasons)
        return out


def decide(page: PresentPage, *, options: PresentOptions, page_guard: bool, overlay: bool,
           bulk_enabled: bool | None, frame: FrameRef | None) -> Decision:
    """``claim`` on any enabled 「一括受取」 of the proven 未受け取り page, ``empty`` when it is grey or absent.

    The rows only travel as evidence: unknown, choice or unread rows, the counter and earlier attempts today never stop
    the press. The result is proven afterwards by the counter dropping / the empty box."""
    reasons = []
    if not options.enabled:
        reasons.append("disabled")
    if frame is None or not frame.valid():
        reasons.append("frame_unbound")
    if not page_guard or not page.title:
        reasons.append("present_page_not_proven")
    if page.tab != "unclaimed":
        reasons.append("unclaimed_tab_not_selected" if page.tab is None else f"wrong_tab:{page.tab}")
    if overlay:
        reasons.append("overlay_present")
    reasons += [f"blocked:{b}" for b in page.blockers if b in HARD_REASONS]
    if bulk_enabled is None:
        reasons.append("bulk_button_unread")
    if reasons:
        return Decision("stop", tuple(reasons), frame=frame, rows=len(page.rows))
    if bulk_enabled is not True:
        why = "box_empty" if not page.rows and not page.count else "bulk_disabled"
        return Decision("empty", (why,), frame=frame, rows=len(page.rows))
    if page.bulk_buttons != 1:
        return Decision("stop", (f"bulk_button_count_{page.bulk_buttons}",), frame=frame, rows=len(page.rows))
    return Decision("claim", ("bulk_receive",), target="present-bulk-receive", frame=frame, rows=len(page.rows))


def frame_matches(decision: Decision, current: FrameRef | None, now_monotonic: float) -> str | None:
    """None when ``current`` is the very frame the decision was made on and still fresh; else a reason."""
    if decision.kind != "claim" or decision.frame is None:
        return "no_claim_decision"
    if current is None or not current.valid():
        return "current_frame_unbound"
    if current != decision.frame:
        return "frame_changed"
    if not 0 <= now_monotonic - current.captured_at <= MAX_FRAME_AGE_S:
        return "frame_stale"
    return None


# ---- result --------------------------------------------------------------------------------------------------
RESULT_POSITIVE = ("受け取りました", "受取りました", "受け取り完了", "獲得しました")


def classify_result(results) -> tuple[str, list[str]]:
    """Dialog after the press: ``error`` (game error text), ``receipt`` (positive text; a limit note beside it means
    part stayed in the box, which ``confirm`` reads from the counter), ``overflow`` (limit text only) or ``uncertain``."""
    texts = [i["text"] for i in _items(results)]
    blockers = blocking_reasons(texts)
    if any(b in HARD_REASONS for b in blockers):
        return "error", blockers
    if any(word in text for text in texts for word in RESULT_POSITIVE):
        return "receipt", blockers
    if blockers:
        return "overflow", blockers
    return "uncertain", ["no_positive_result_text"]


def receipt_lines(results) -> list:
    """``[{"name", "quantity", "kind"}]`` of the 「<item> ×N」 lines inside the result dialog (evidence only)."""
    out = []
    for item in _items(results):
        if not _inside(item["box"], LAYOUT["dialog_roi"]):
            continue
        match = QUANTITY_PAT.match(item["text"])
        if match:
            name = match.group("name")
            out.append({"name": name, "quantity": int(match.group("qty")), "kind": item_kind(name)})
    return out


def confirm(before: PresentPage, after: PresentPage, receipt: str) -> str:
    """Final state from the dialog class and a fresh page read after the dialog closed."""
    if receipt != "receipt":
        return receipt if receipt in ("overflow", "error") else "uncertain"
    if any(b in HARD_REASONS for b in after.blockers) or not after.title or after.tab != "unclaimed":
        return "uncertain"
    if not after.rows and (after.empty_message or after.count == 0):
        return "received"
    if before.count is not None and after.count is not None and after.count < before.count and after.rows:
        return "received_partial"   # >100 items or items over the 所持上限 remain; never re-pressed this run
    return "uncertain"


# ---- ledger --------------------------------------------------------------------------------------------------
TERMINAL_OK = frozenset({"received", "received_partial", "refused"})
BLOCKING = frozenset({"pending", "receipt_observed", "uncertain", "overflow", "error"})
STATES = TERMINAL_OK | BLOCKING
TRANSITIONS = {
    "pending": {"refused", "receipt_observed", "uncertain", "overflow", "error"},
    "receipt_observed": {"received", "received_partial", "uncertain"},
}


class PresentLedger:
    """Append-only attempt records; ``pending`` is written before any press and never deleted automatically.
    Subclasses may widen ``STATES`` / ``BLOCKING`` / ``TRANSITIONS`` (mission_bulk.MissionBulkLedger)."""

    STATES, BLOCKING, TRANSITIONS = STATES, BLOCKING, TRANSITIONS
    # Presents: an open earlier record (crash, uncertain dialog) never stops a later press; receiving is free and a box
    # with nothing left shows a grey 「一括受取」. The mission bulk ledger sets it.
    BLOCK_OPEN_ATTEMPTS = False

    def __init__(self, path: Path | None = LEDGER_PATH):
        self.path = None if path is None else Path(path)
        self._lock = threading.Lock()
        self.records = self._load()

    def _load(self):
        if self.path is None or not self.path.is_file():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping) or data.get("version") != 1 or not isinstance(data.get("records"), list):
            raise ValueError("プレゼント 账本格式无效")
        ids = set()
        for record in data["records"]:
            if (not isinstance(record, Mapping) or record.get("state") not in self.STATES or record.get("id") in ids
                    or not isinstance(record.get("cycle"), str)):
                raise ValueError("プレゼント 账本记录无效")
            ids.add(record["id"])
        return [dict(r) for r in data["records"]]

    def _save(self):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "records": self.records}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(self.path)

    def block_reason(self, cycle: str, since: int = 0) -> str | None:
        """The latest blocking record of ``cycle`` among ``records[since:]`` (``since``: the run's first record)."""
        blocked = [r for r in self.records[since:] if r["cycle"] == cycle and r["state"] in self.BLOCKING]
        return None if not blocked else f"{blocked[-1]['state']}_today"

    def claims_today(self, cycle: str) -> int:
        """Attempts that may have pressed (everything except ``refused``) count against the daily cap."""
        return sum(1 for r in self.records if r["cycle"] == cycle and r["state"] != "refused")

    def begin(self, cycle: str, decision: Decision, now: _dt.datetime, extra: Mapping | None = None) -> dict:
        with self._lock:
            if decision.kind != "claim" or decision.frame is None:
                raise ValueError("只有 claim 决定才能开始尝试")
            if self.BLOCK_OPEN_ATTEMPTS and self.block_reason(cycle):
                raise ValueError("本周期已有未决记录")
            record = {"id": uuid.uuid4().hex, "cycle": cycle, "state": "pending", "frame_sha256": decision.frame.sha256,
                      "rows": decision.rows, "created_at": now.isoformat(timespec="seconds"),
                      "updated_at": now.isoformat(timespec="seconds"), "history": ["pending"], "detail": None,
                      **dict(extra or {})}
            self.records.append(record)
            self._save()
            return dict(record)

    def transition(self, record_id: str, state: str, now: _dt.datetime, detail: str | None = None) -> dict:
        with self._lock:
            matches = [r for r in self.records if r["id"] == record_id]
            if len(matches) != 1:
                raise ValueError("记录不存在")
            record = matches[0]
            if state not in self.TRANSITIONS.get(record["state"], ()):
                raise ValueError(f"非法状态转换 {record['state']} -> {state}")
            record["state"] = state
            record["updated_at"] = now.isoformat(timespec="seconds")
            record["history"] = [*record["history"], state]
            record["detail"] = None if detail is None else str(detail)[:200]
            self._save()
            return dict(record)
