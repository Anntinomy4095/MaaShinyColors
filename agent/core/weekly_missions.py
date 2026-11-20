"""Read-only weekly mission check for the weekly Produce x3 target (pure; no Maa import).

Input is one fresh 1280x720 frame of the ミッション page with the ウィークリー tab selected, plus the OCR results
of three regions on that same frame (see ``ROIS``). Output is a ``WeeklyProduceStatus`` that says how many
Produce runs are still needed this week, or why the count is unknown. An unknown count is never treated as 0
and never as "3 remaining": ``remaining`` is ``None`` and ``weekly_remaining()`` returns ``None``.

Evidence:

- Mission page layout (tabs, row layout, 「ミッション達成数 a/b」, 「一括受取」) comes from the native daily-tab frame
  ``dev_assets/private/in_app_browser/20261001-daily-research/daily-v8-Mission-native.png``. The weekly tab itself
  has **no native sample yet**; its selected-state colour is assumed to equal the measured daily selected text
  colour (pink ~(253,106,194)). ``tab_selection()`` checks both tabs so a daily-selected frame is rejected.
- Weekly mission rows (wikiwiki.jp ミッション, last edited 2026-10-02, read 2026-10-06): 「フェスアイドルを誕生させよう」
  1回 and 「フェスアイドルを3人誕生させよう」 3回; weekly reset 「日曜05:00」 (JST). The two titles share a prefix, so
  only the exact title is accepted.
- The tab subtitle shows the time left (daily 「あと14時間6分」, weekly 「あと2日14時間」 on the same frame; both agree with
  a capture at 14:54 JST on Thursday when the weekly value is floored to hours). The cycle is accepted only when
  the subtitle and the local clock agree on the same Sunday-05:00-JST reset.

Rows sorted below the visible area are reported as not visible (scrolling is input and is not done here).
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from agent.core import mission_status

JST = _dt.timezone(_dt.timedelta(hours=9), "JST")
RESET_WEEKDAY = 6          # Sunday
RESET_HOUR = 5             # 05:00 JST
PRODUCE_TITLE = "フェスアイドルを3人誕生させよう"
PRODUCE_TITLE_ONE = "フェスアイドルを誕生させよう"
PRODUCE_TARGET = 3
CANVAS = (1280, 720)

# OCR regions on the weekly page (1280x720). LIST extends ListOCR of MissionStatus upwards to the list top
# (y≈100); a row whose title is clipped is not reported, so a clipped Produce row stays "not visible".
ROIS = {
    "list": [255, 100, 975, 500],
    "counter": [950, 572, 320, 32],
    "weekly_timer": [26, 205, 160, 32],
}
# Selected-tab text colour (RGB) measured on the daily tab text [30,92,130,32] of the native frame: 320 px;
# the unselected weekly text and the red badge give 0 and 3 px in the same range.
TAB_TEXT_ROI = {"daily": (30, 92, 130, 32), "weekly": (30, 177, 130, 32)}
TAB_SELECTED_LOWER = (235, 80, 170)
TAB_SELECTED_UPPER = (255, 135, 215)
# 2026-10-07: six native daily-selected frames give 139..320 px (139 on the 2026-09-30 frame without the badge),
# the unselected weekly label 0 px on all of them; the minimum is lowered from 150 to 100.
TAB_SELECTED_MIN = 100
TAB_UNSELECTED_MAX = 40

TIMER_DAYS = re.compile(r"あと\s*(\d{1,2})\s*日\s*(\d{1,2})\s*時間")
TIMER_HOURS = re.compile(r"あと\s*(\d{1,2})\s*時間\s*(\d{1,2})\s*分")
TIMER_MINUTES = re.compile(r"あと\s*(\d{1,2})\s*分")
CLOCK_TOLERANCE_S = 180     # host clock vs game server skew we accept
MIN_REMAINING_S = 3 * 3600  # closer than this to the reset: stop (a run could cross the reset)
DEFAULT_MAX_AGE_S = 900     # a stored reading older than this is stale for weekly_remaining()

STORE_PATH = Path(__file__).resolve().parents[2] / "dev_assets" / "private" / "weekly" / "weekly_status.json"


# ---- frame-level checks -------------------------------------------------------------------------------------
def tab_selection(image_rgb) -> dict:
    """Pixel counts of the selected-tab text colour on the daily and weekly tab labels.

    ``image_rgb`` is an HxWx3 uint8 RGB array (numpy). Returns {"daily": n, "weekly": n, "selected": name|None}.
    ``selected`` is set only when exactly one tab has the selected colour and the other is clearly unselected."""
    import numpy as np

    image = np.asarray(image_rgb)
    if image.ndim != 3 or image.shape[2] < 3 or (image.shape[1], image.shape[0]) != CANVAS:
        return {"daily": None, "weekly": None, "selected": None}
    lower, upper = np.array(TAB_SELECTED_LOWER), np.array(TAB_SELECTED_UPPER)
    counts = {}
    for name, (x, y, w, h) in TAB_TEXT_ROI.items():
        region = image[y:y + h, x:x + w, :3].astype(int)
        counts[name] = int(((region >= lower) & (region <= upper)).all(axis=2).sum())
    selected = None
    if counts["weekly"] >= TAB_SELECTED_MIN and counts["daily"] <= TAB_UNSELECTED_MAX:
        selected = "weekly"
    elif counts["daily"] >= TAB_SELECTED_MIN and counts["weekly"] <= TAB_UNSELECTED_MAX:
        selected = "daily"
    return {**counts, "selected": selected}


# ---- cycle ---------------------------------------------------------------------------------------------------
def next_reset(now: _dt.datetime) -> _dt.datetime:
    """The next Sunday 05:00 JST strictly after ``now`` (aware datetime)."""
    if now.tzinfo is None:
        raise ValueError("now 必须带时区")
    local = now.astimezone(JST)
    candidate = local.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    candidate += _dt.timedelta(days=(RESET_WEEKDAY - local.weekday()) % 7)
    if candidate <= local:
        candidate += _dt.timedelta(days=7)
    return candidate


def cycle_id(now: _dt.datetime) -> str:
    """ISO date (JST) of the Sunday 05:00 reset that started the current weekly cycle."""
    return (next_reset(now) - _dt.timedelta(days=7)).date().isoformat()


def parse_timer(results: Iterable[Mapping[str, Any]]) -> tuple[int, int] | None:
    """(lower, upper) bound in seconds of the displayed 「あと…」 time; the game floors to the shown unit."""
    texts = [str(item.get("text", "")).replace(" ", "") for item in results or [] if isinstance(item, Mapping)]
    found = []
    for text in texts:
        match = TIMER_DAYS.search(text)
        if match:
            low = int(match.group(1)) * 86400 + int(match.group(2)) * 3600
            found.append((low, low + 3600))
            continue
        match = TIMER_HOURS.search(text)
        if match:
            low = int(match.group(1)) * 3600 + int(match.group(2)) * 60
            found.append((low, low + 60))
            continue
        match = TIMER_MINUTES.search(text)
        if match:
            low = int(match.group(1)) * 60
            found.append((low, low + 60))
    return found[0] if len(found) == 1 else None


def check_cycle(timer: tuple[int, int] | None, now: _dt.datetime) -> tuple[str | None, list[str]]:
    """Bind the screen timer to the clock. Returns (cycle_id, reasons); cycle_id None means untrusted."""
    if timer is None:
        return None, ["weekly_timer_unread"]
    seconds_left = (next_reset(now) - now.astimezone(JST)).total_seconds()
    low, high = timer
    if not (low - CLOCK_TOLERANCE_S <= seconds_left <= high + CLOCK_TOLERANCE_S):
        return None, [f"weekly_timer_clock_mismatch_{low}_{high}_{int(seconds_left)}"]
    if high <= MIN_REMAINING_S or seconds_left <= MIN_REMAINING_S:
        return None, ["weekly_reset_too_close"]
    return cycle_id(now), []


# ---- rows ----------------------------------------------------------------------------------------------------
def produce_row(list_results) -> tuple[dict | None, list[str]]:
    """The exact 「フェスアイドルを3人誕生させよう」 row among the visible rows, or (None, reasons)."""
    rows = mission_status.parse_rows(list_results)
    exact = [row for row in rows if row["title"].replace(" ", "") == PRODUCE_TITLE]
    if len(exact) > 1:
        return None, ["produce_row_duplicate"]
    if not exact:
        near = [row["title"] for row in rows if "誕生" in row["title"] and row["title"].replace(" ", "") != PRODUCE_TITLE_ONE]
        return None, ["produce_row_ocr_mismatch"] if near else ["produce_row_not_visible"]
    return exact[0], []


@dataclasses.dataclass(frozen=True)
class WeeklyProduceStatus:
    status: str                     # "remaining" | "complete" | "unknown"
    completed: int | None
    target: int | None
    remaining: int | None
    claimable: bool | None          # reward shown as 受け取る (a=b and claim button)
    cycle_id: str | None
    observed_at: str | None         # ISO time of the frame
    frame_sha256: str | None
    reasons: tuple

    def to_dict(self) -> dict:
        return dataclasses.asdict(self) | {"reasons": list(self.reasons)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WeeklyProduceStatus":
        fields = {f.name for f in dataclasses.fields(cls)}
        if not isinstance(data, Mapping) or set(data) != fields:
            raise ValueError("周常记录字段无效")
        status = cls(**{**data, "reasons": tuple(data["reasons"])})
        if status.status not in ("remaining", "complete", "unknown"):
            raise ValueError("周常记录状态无效")
        if status.status != "unknown":
            if not (isinstance(status.completed, int) and isinstance(status.target, int)
                    and isinstance(status.remaining, int) and status.cycle_id and status.observed_at):
                raise ValueError("可信周常记录缺字段")
            if status.remaining != status.target - status.completed or status.remaining < 0:
                raise ValueError("周常剩余次数与计数不一致")
        elif status.remaining is not None:
            raise ValueError("未知状态不能带剩余次数")
        return status


def unknown(reasons, *, observed_at=None, frame_sha256=None, cycle=None) -> WeeklyProduceStatus:
    return WeeklyProduceStatus("unknown", None, None, None, None, cycle, observed_at, frame_sha256,
                               tuple(reasons) or ("unknown",))


def evaluate(*, list_results, timer_results, now: _dt.datetime, tab: Mapping[str, Any] | None,
             page_guard: bool, overlay: bool = False, frame_sha256: str | None = None) -> WeeklyProduceStatus:
    """Combine one frame's guard, tab colour, cycle timer and the Produce row into a status.

    ``page_guard``: the Pipeline guard (ミッション title + return key + back undimmed) matched on this frame.
    ``overlay``: any known popup/loading/tutorial anchor was present (the Read node also requires its absence)."""
    observed_at = now.astimezone(JST).isoformat(timespec="seconds")
    reasons = []
    if not page_guard:
        reasons.append("mission_page_guard_failed")
    if overlay:
        reasons.append("overlay_present")
    if not tab or tab.get("selected") != "weekly":
        reasons.append("weekly_tab_not_proven")
    cycle, cycle_reasons = check_cycle(parse_timer(timer_results), now)
    reasons += cycle_reasons
    row, row_reasons = produce_row(list_results)
    reasons += row_reasons
    if reasons:
        return unknown(reasons, observed_at=observed_at, frame_sha256=frame_sha256, cycle=cycle)
    progress = row["progress"]
    if progress is None or row["status"] == "unknown":
        return unknown(["produce_progress_unread"], observed_at=observed_at, frame_sha256=frame_sha256, cycle=cycle)
    done, total = progress
    if total != PRODUCE_TARGET:
        return unknown([f"produce_target_mismatch_{total}"], observed_at=observed_at, frame_sha256=frame_sha256,
                       cycle=cycle)
    remaining = total - done
    claimable = row["status"] == "claimable"
    status = "complete" if remaining == 0 else "remaining"
    return WeeklyProduceStatus(status, done, total, remaining, claimable, cycle, observed_at, frame_sha256,
                               ("ok",))


# ---- trusted store and the public query ---------------------------------------------------------------------
def save_status(status: WeeklyProduceStatus, path: Path = STORE_PATH) -> None:
    """Persist the latest reading (unknown readings overwrite a trusted one: the newest observation wins)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "status": status.to_dict()}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)


def load_status(path: Path = STORE_PATH) -> WeeklyProduceStatus | None:
    path = Path(path)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping) or data.get("version") != 1:
        raise ValueError("周常记录版本无效")
    return WeeklyProduceStatus.from_dict(data["status"])


@dataclasses.dataclass(frozen=True)
class WeeklyRemaining:
    remaining: int | None
    reason: str
    status: WeeklyProduceStatus | None = None

    def to_dict(self) -> dict:
        return {"remaining": self.remaining, "reason": self.reason,
                "status": None if self.status is None else self.status.to_dict()}


def weekly_remaining(status: WeeklyProduceStatus | None = None, *, now: _dt.datetime | None = None,
                     path: Path = STORE_PATH, max_age_s: int = DEFAULT_MAX_AGE_S) -> WeeklyRemaining:
    """Remaining weekly Produce runs for the W.I.N.G. T.E. task, or ``remaining=None`` with a reason.

    Uses ``status`` when given, otherwise the stored reading. The reading must be trusted, from the current
    weekly cycle, and younger than ``max_age_s``; anything else returns ``None`` (never 0). Re-read the weekly
    page after every Produce run: a run's start or an arbitrary end is not a completion."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if status is None:
        try:
            status = load_status(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return WeeklyRemaining(None, f"store_invalid:{type(exc).__name__}")
        if status is None:
            return WeeklyRemaining(None, "no_reading")
    if status.status == "unknown":
        return WeeklyRemaining(None, "reading_unknown:" + ",".join(status.reasons), status)
    try:
        observed = _dt.datetime.fromisoformat(status.observed_at)
    except (TypeError, ValueError):
        return WeeklyRemaining(None, "observed_at_invalid", status)
    age = (now - observed).total_seconds()
    if age < -CLOCK_TOLERANCE_S:
        return WeeklyRemaining(None, "observed_in_future", status)
    if age > max_age_s:
        return WeeklyRemaining(None, "reading_stale", status)
    if status.cycle_id != cycle_id(now):
        return WeeklyRemaining(None, "cycle_changed", status)
    return WeeklyRemaining(status.remaining, "complete" if status.remaining == 0 else "ok", status)
