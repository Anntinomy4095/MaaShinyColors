"""Native daily-login evidence reader; no Maa, IO or input authorization.

The caller supplies recognitions from the same RGB frame and a calibrated private ticket
template. A clock alone never establishes the daily cycle. Claim sending remains a separate step.
Native evidence: private missions_daily_current/after_bulk_bottom frames.
"""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import hashlib
import re

from agent.core import mission_status as ms
from agent.core import present_claim as pc
from agent.core import weekly_missions as wm
from agent.core.frame_permit import CANONICAL_SHAPE, FrameCapture
from agent.core.mission_claim import ClaimObservation, ClaimRow, ClaimTarget

LOGIN_ID = "daily.login"
LOGIN_TITLE = "ゲームにログインしよう"
REWARD = ("bronze_ticket", 1)
TEMPLATE_DIGESTS = {
    "bronze_ticket": "3f25f2fc16440dc58f241ac521fa14177bea7e023ff3594099331b33e331c98b",
    "silver_ticket": "9ef3f7309d79984f1b1d9ebedf279a013bcc7a5e161b993d9e8da7641b072e49",
}
ROIS = {"list": (255, 100, 975, 492), "daily_timer": (26, 125, 160, 28)}
# Crop from the calibrated private current frame, without its quantity or other row content.
SILVER_REFERENCE_RECT = (283, 250, 80, 66)
BRONZE_REFERENCE_RECT = (283, 389, 80, 66)
TEMPLATE_MAX_ERROR = 0.004
RESET_MARGIN_S = 300
QUANTITY = re.compile(r"^[x×]\s*(\d{1,4}|[lI])$")


def check_daily_cycle(timer_results, observed_at):
    """Trust JST05:00 only when a bound daily mission/tab timer confirms that reset."""
    if not isinstance(observed_at, dt.datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
        return None, "daily_clock_invalid"
    timer = wm.parse_timer(timer_results)
    if timer is None or not 0 <= timer[0] < timer[1] <= 86460:
        return None, "daily_timer_unread"
    local = observed_at.astimezone(wm.JST)
    reset = local.replace(hour=5, minute=0, second=0, microsecond=0)
    if reset <= local:
        reset += dt.timedelta(days=1)
    left = (reset - local).total_seconds()
    low, high = timer
    if not low - wm.CLOCK_TOLERANCE_S <= left <= high + wm.CLOCK_TOLERANCE_S:
        return None, "daily_timer_clock_mismatch"
    if min(left, low) <= RESET_MARGIN_S:
        return None, "daily_reset_too_close"
    return "daily:" + (reset - dt.timedelta(days=1)).date().isoformat(), None


def _items(results):
    return ms._items(results)


def _inside(box, rect):
    x, y, w, h = box
    rx, ry, rw, rh = rect
    return w > 0 and h > 0 and rx <= x and ry <= y and x + w <= rx + rw and y + h <= ry + rh


def _quantity(items, title_y):
    values = []
    rect = (345, title_y + 58, 38, 35)
    for item in items:
        match = QUANTITY.fullmatch(item["text"])
        if match and _inside(item["box"], rect):
            value = match.group(1)
            values.append(1 if value in ("l", "I") else int(value))
    return values[0] if len(values) == 1 else None


def _claimed_fields_visible(items, title_y):
    """Require complete OCR boxes for this row, excluding the footer's counter column."""
    fields = (
        [i for i in items if i["text"].replace(" ", "") == LOGIN_TITLE and i["box"][1] == title_y],
        [i for i in items if ms.PROGRESS.fullmatch(i["text"])
         and _inside(i["box"], (560, title_y + 35, 140, 72))],
        [i for i in items if ms.CLAIMED.fullmatch(i["text"])
         and _inside(i["box"], (1042, title_y - 35, 168, 120))],
        [i for i in items if QUANTITY.fullmatch(i["text"])
         and _inside(i["box"], (345, title_y + 58, 38, 35))],
    )
    # Every pixel searched for the private reward template must also be above the list edge.
    return (title_y - 27 >= ROIS["list"][1]
            and title_y + 85 <= ROIS["list"][1] + ROIS["list"][3]
            and all(len(group) == 1 and _inside(group[0]["box"], ROIS["list"]) for group in fields))


def template_verified(template_rgb, expected_reward):
    """Bind the expected ticket kind to its independently reviewed pixels."""
    import numpy as np

    template = np.asarray(template_rgb) if template_rgb is not None else np.empty(0)
    return (expected_reward in TEMPLATE_DIGESTS and template.shape == (66, 80, 3)
            and template.dtype == np.uint8
            and hashlib.sha256(template.tobytes()).hexdigest() == TEMPLATE_DIGESTS[expected_reward])


def ticket_matches(image_rgb, title_y, template_rgb):
    """Compare colour as well as lettering; bronze and silver both say TRAINING."""
    import numpy as np

    image = np.asarray(image_rgb)
    template = np.asarray(template_rgb) if template_rgb is not None else np.empty(0)
    if (image.shape != CANONICAL_SHAPE or image.dtype != np.uint8
            or template.shape != (66, 80, 3) or template.dtype != np.uint8):
        return False
    region = image[title_y - 24:title_y + 85, 273:378]
    if region.shape != (109, 105, 3) or float(template.std()) < 5:
        return False
    # The calibrated search area has only 1144 offsets; numpy suffices without a new OpenCV dependency.
    reference = template.astype(np.float32)
    reference_energy = float(np.square(reference).sum())
    source = region.astype(np.float32)
    for y in range(source.shape[0] - reference.shape[0] + 1):
        for x in range(source.shape[1] - reference.shape[1] + 1):
            patch = source[y:y + reference.shape[0], x:x + reference.shape[1]]
            denominator = (float(np.square(patch).sum()) * reference_energy) ** 0.5
            if denominator and float(np.square(patch - reference).sum()) / denominator <= TEMPLATE_MAX_ERROR:
                return True
    return False


# Historical matcher name; this only compares pixels and does not assign a reward kind.
silver_matches = ticket_matches


@dataclass(frozen=True)
class LoginReading:
    state: str
    cycle_id: str | None
    progress: tuple[int, int] | None
    reward: tuple[str, int] | None
    target: tuple[int, int, int, int] | None
    pixel_digest: str | None
    reasons: tuple[str, ...]
    row_state: str | None = None  # diagnostic only; ``state`` alone is eligible for the claim contract
    frame: FrameCapture | None = None
    # Diagnostics only: the reward quantity read in the row and, when the expected ticket did not match,
    # another reviewed ticket kind whose pixels did. Never turns the reading into a claimable one.
    reward_quantity: int | None = None
    observed_reward: str | None = None

    def to_dict(self):
        return {"mission_id": LOGIN_ID, "state": self.state, "cycle_id": self.cycle_id,
                "progress": self.progress, "reward": self.reward, "target": self.target,
                "pixel_digest": self.pixel_digest, "reasons": list(self.reasons),
                "observed_row_state": self.row_state, "frame_supplied": self.frame is not None,
                "reward_quantity": self.reward_quantity, "observed_reward": self.observed_reward,
                "input_authorized": False}

    def observation(self, frame):
        """Adapt a trusted reading only to the original host frame; never invent capture sequences."""
        if (self.state not in ("claimable", "claimed", "incomplete") or self.reasons or self.cycle_id is None
                or self.frame is None or frame != self.frame
                or not isinstance(frame, FrameCapture) or frame.shape != CANONICAL_SHAPE
                or frame.pixel_digest != self.pixel_digest):
            return None
        targets = () if self.target is None else (ClaimTarget(LOGIN_ID, "single_daily", self.target, True, True),)
        return ClaimObservation(self.cycle_id, frame, "daily_missions", True, True,
                                (ClaimRow(LOGIN_ID, self.progress, self.state),), targets)


def read_login(*, image_rgb, list_results, timer_results, observed_at, ticket_template=None,
               expected_reward="bronze_ticket", silver_template=None,
               page_guard=False, overlay=True, frame=None, other_templates=None):
    """Read one stable identity wherever it is fully visible, including after list reordering.

    ``page_guard`` and ``overlay`` must come from recognitions on this same frame. A readiness
    result does not authorize input, initialize a journal or describe a receipt. ``frame`` is the
    original host capture; without it this remains a read-only report, even for a complete row.
    ``other_templates`` ({kind: reviewed template}) only explains a failed reward check: when the expected ticket
    does not match but another reviewed kind does, ``login_reward_kind_mismatch:<kind>`` is added (2026-10-12: every
    native login row so far shows Silver x1 while the runtime default expects Bronze x1). The expected kind is never
    replaced by the observed one.
    """
    import numpy as np

    image = np.asarray(image_rgb)
    if image.shape != CANONICAL_SHAPE or image.dtype != np.uint8:
        return LoginReading("unknown", None, None, None, None, None, ("daily_image_invalid",))
    digest = hashlib.sha256(np.ascontiguousarray(image[:, :, ::-1]).tobytes()).hexdigest()
    bound_frame = frame if (isinstance(frame, FrameCapture) and frame.shape == CANONICAL_SHAPE
                            and frame.pixel_digest == digest) else None
    cycle, problem = check_daily_cycle(timer_results, observed_at)
    reasons = [] if problem is None else [problem]
    # Legacy silver callers must select silver explicitly. Never relabel their template as bronze.
    if silver_template is not None:
        if expected_reward != "silver_ticket" or ticket_template is not None:
            reasons.append("login_reward_template_mismatch")
        else:
            ticket_template = silver_template
    if page_guard is not True:
        reasons.append("daily_page_unverified")
    if overlay is not False:
        reasons.append("daily_overlay_present")
    if wm.tab_selection(image).get("selected") != "daily":
        reasons.append("daily_tab_unverified")
    items = _items(list_results)
    parsed_rows = ms.parse_rows(items)
    rows = [row for row in parsed_rows if row["title"].replace(" ", "") == LOGIN_TITLE]
    if len(rows) != 1:
        reasons.append("login_duplicate" if rows else "login_not_visible")
        return LoginReading("unknown", cycle, None, None, None, digest, tuple(reasons), frame=bound_frame)
    row = rows[0]
    ty = row["title_y"]
    # The sidebar can retain its page-entry value. Prefer the deadline belonging to this exact row;
    # conflicting/duplicate row deadlines must not be rescued by a sidebar or the system clock.
    row_timers = [i for i in items if "あと" in i["text"]
                  and _inside(i["box"], (870, ty + 55, 160, 40))]
    if row_timers:
        cycle, problem = check_daily_cycle(row_timers, observed_at)
        reasons = [r for r in reasons if not r.startswith("daily_timer_")
                   and r not in ("daily_clock_invalid", "daily_reset_too_close")]
        if problem:
            reasons.append(problem)
    # Claimable rows still require the entire calibrated row and full button. CLEAR terminal rows
    # can end closer to the edge, but only when every actual key field is wholly readable.
    _, list_top, _, list_height = ROIS["list"]
    complete = (_claimed_fields_visible(items, ty) if row["status"] == "claimed"
                else list_top <= ty - 27 and ty + 97 <= list_top + list_height)
    if not complete:
        reasons.append("login_row_clipped")
    if any(other is not row and ty - 27 <= other["title_y"] < ty + 97 for other in parsed_rows):
        reasons.append("login_row_ambiguous")
    progress = tuple(row["progress"]) if row["progress"] is not None else None
    if progress not in ((0, 1), (1, 1)) or row["status"] not in ("claimable", "claimed", "incomplete"):
        reasons.append("login_state_unverified")
    reward = observed = None
    quantity = _quantity(items, ty) if complete else None
    if (complete and quantity == 1
            and template_verified(ticket_template, expected_reward)
            and ticket_matches(image, ty, ticket_template)):
        reward = (expected_reward, 1)
    else:
        reasons.append("login_reward_unverified")
        if not complete:
            pass
        elif quantity != 1:
            reasons.append("login_reward_quantity_unread" if quantity is None else "login_reward_quantity_not_one")
        elif not template_verified(ticket_template, expected_reward):
            reasons.append("login_reward_template_unverified")
        else:
            for kind, template in sorted((other_templates or {}).items()):
                if (kind != expected_reward and template_verified(template, kind)
                        and ticket_matches(image, ty, template)):
                    observed = kind
                    reasons.append(f"login_reward_kind_mismatch:{kind}")
                    break
            else:
                reasons.append("login_reward_icon_unmatched")
    target = None
    if row["status"] == "claimable":
        rect = (1042, ty - 4, 168, 80)
        buttons = [item for item in items if item["text"] == ms.CLAIM_TEXT and _inside(item["box"], rect)]
        if len(buttons) != 1 or pc.pink_count(image, rect) < 2000:
            reasons.append("login_button_unverified")
        else:
            target = rect
    if reasons:
        return LoginReading("unknown", cycle, progress, reward, None, digest, tuple(reasons), row["status"], bound_frame,
                            quantity, observed)
    return LoginReading(row["status"], cycle, progress, reward, target, digest, (), row["status"], bound_frame,
                        quantity, observed)
