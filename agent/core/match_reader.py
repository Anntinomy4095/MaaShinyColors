"""Read-only native Match homepage fields. Ranking deadlines are not reset-cycle proof.

Measured on nav-v21-match-help-close-execute-frames/frame-023.png (1280x720).
Only three complete area headers fit this viewport. Their achievement percentages
do not establish individual stages, the end of the list, or the latest cleared stage.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import math
import re
import unicodedata

from agent.core import match_task as mt, task_common as tc

DEADLINE_BOX = (630, 35, 185, 36)
AREA_HEADER = (22, 214, 200, 48)
AREA_PERCENT = (228, 218, 157, 42)
AREA_ROW_DY = 136
VISIBLE_ROWS = 3


@dataclasses.dataclass(frozen=True)
class RankingDeadline:
    raw: str
    month: int
    day: int
    hour: int
    minute: int


def parse_ranking_deadline(text):
    """Month/day and time only: never invent a year or a reset boundary."""
    if not isinstance(text, str):
        return None
    normalized = unicodedata.normalize("NFKC", text).strip()
    spaced = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})\s+([0-9]{1,2}):([0-9]{2})\s*まで", normalized)
    candidates = [tuple(map(int, spaced.groups()))] if spaced else []
    if not spaced:
        joined = re.fullmatch(r"([0-9]{1,2})/([0-9]{2,4}):([0-9]{2})まで", tc.normalize_text(text))
        if joined:
            digits = joined[2]
            candidates = [(int(joined[1]), int(digits[:cut]), int(digits[cut:]), int(joined[3]))
                          for cut in (1, 2) if 1 <= len(digits) - cut <= 2]
    valid = []
    for month, day, hour, minute in candidates:
        try:
            dt.datetime(2000, month, day, hour, minute)  # validates values, including February 29
        except ValueError:
            continue
        valid.append(RankingDeadline(text, month, day, hour, minute))
    return valid[0] if len(valid) == 1 else None


@dataclasses.dataclass(frozen=True)
class AreaObservation:
    row: int
    area_id: int | None
    difficulty: str | None
    achievement: int | None
    header_raw: str | None
    achievement_raw: str | None


@dataclasses.dataclass(frozen=True)
class MainObservation:
    frame_digest: str
    observed_at: float
    rights: tuple | None
    ranking_deadline: RankingDeadline | None
    ranking_raw: str | None
    areas: tuple[AreaObservation, ...]

    def to_dict(self):
        return {**dataclasses.asdict(self), "scope": "visible_area_headers",
                "stage_list_complete": False, "latest_stage": None, "input_authorized": False}


def _shift(box, row):
    x, y, w, h = box
    return x, y + row * AREA_ROW_DY, w, h


def read_main(vision, image, observed_at, rights_text=None):
    """Positive native page guard, original pixel digest/time, and visible fields; no input or progress grant."""
    if getattr(image, "shape", None) != (720, 1280, 3):
        return None
    if not vision.hit("SC.Nav.MatchMain.Guard", image):
        return None
    tc.require(type(observed_at) in (float, int) and math.isfinite(observed_at), "Match 采样时间无效")
    text = rights_text if rights_text is not None else vision.number(image, mt.RIGHTS_FIELD, "light", mt.RIGHTS_PATTERN)
    rights = tc.ratio(text)
    if rights is not None and rights[1] != mt.RIGHTS_MAXIMUM:
        rights = None
    ranking_raw = vision.text(image, DEADLINE_BOX)
    areas = []
    for row in range(VISIBLE_ROWS):
        header = vision.text(image, _shift(AREA_HEADER, row))
        achievement = vision.text(image, _shift(AREA_PERCENT, row))
        name = re.fullmatch(r"エリア([1-9][0-9]?)(Normal|Hard|VeryHard)", header or "")
        percent = re.fullmatch(r"達成率(100|[0-9]{1,2})%?", achievement or "")
        areas.append(AreaObservation(row + 1, int(name[1]) if name else None, name[2] if name else None,
                                     int(percent[1]) if percent else None, header, achievement))
    return MainObservation(tc.image_digest(image), observed_at, rights,
                           parse_ranking_deadline(ranking_raw), ranking_raw, tuple(areas))
