"""W.I.N.G. classification shortcuts (2026-10-25 speed batch; pure, no Maa import, no input).

The live loop classifies each new frame from one whole-frame OCR plus the guards in ``SC.Wing.Nav.Loop`` dispatch order
(agent/core/wing_custom.py ``classify``; 1.0-2.4 s per frame live). Two shortcuts give the same answer for less work:

- Same view: a frame whose thumbnail matches the last classified frame keeps its classification. ``same_view`` is
  stricter than ``wing_custom._same_view`` (mean only): an overlay such as a support-skill cut-in over Schedule or the
  「Now Loading」 text changes a few regions while the mean stays small, so every 80x80 px block must match too.
  tools/wing_speed_audit.py (every labelled demo frame): no pair of frames with incompatible screens passes
  (nearest: 26.3 for a cut-in over Schedule), while 38 % of consecutive same-screen pairs do.
- Hint: test one expected screen with its native guard and its known confusers instead of the whole dispatch order.
  ``HINT_CONFUSERS[screen]`` lists every screen that the full classifier chose on a labelled frame where this screen's
  guard also hit (plus Loading); the hint answers the screen only when its guard hits and no confuser's guard does.
  The audit replays that rule (native ROI OCR) against the full classification (whole-frame OCR) on every labelled
  frame; screens without enough evidence, or with a frame the rule would answer differently, are not listed.

Neither shortcut sends input: every click still needs the Click node's native screen guard on its own frame.
"""
from __future__ import annotations

THUMB_STEP = 16             # 1280x720 -> 80x45 grey samples (same as wing_custom._thumb)
VIEW_MEAN_DIFF = 4.0        # mean |Δ| over the thumbnail
VIEW_BLOCK = 5              # 5x5 thumbnail cells = 80x80 px
VIEW_BLOCK_DIFF = 15.0      # largest block mean |Δ| (overlays measured from 26.3 up)

# tools/wing_speed_audit.py --hint-backend maa --emit (native ROI OCR, 2378 labelled demo frames; the boxes backend
# accepts a superset). Not listed: screens with < 3 frames, a hint answered on another screen's frame (Commu, FesBirth,
# Live, NoticeNext, ScheduleBusy, SetupTransition), or a hint that answers < 50 % of the screen's own frames.
HINT_CONFUSERS: dict[str, tuple[str, ...]] = {
    "ActiveSkills": ("Loading",),
    "AuditionLocked": ("Loading",),
    "AuditionPass": ("Loading",),
    "AuditionTab": ("AuditionLocked", "Loading"),
    "CheerHelp": ("Loading",),
    "Choice": ("Loading",),
    "ClearReward": ("Loading",),
    "CommuFastForwardOff": ("CommuSkip", "Loading"),
    "CommuSkip": ("Loading",),
    "Evaluation": ("Loading",),
    "FesDetail": ("Loading",),
    "FesDetailPopup": ("Loading",),
    "ForgetSelect": ("Loading", "ReplaceDialog"),
    "Home": ("HomeMinigamePopup", "Loading", "MissionPopup"),
    "Hub": ("HubBanner", "Loading"),
    "HubFinal": ("Loading",),
    "IdolList": ("Loading",),
    "IdolPicker": ("IdolPickerConfirm", "Loading"),
    "ItemPopup": ("Loading",),
    "ItemSelect": ("Loading", "StartConfirm"),
    "ItemUseConfirm": ("Loading",),
    "Loading": (),
    "MinigameCourse": ("Loading",),
    "MinigameGame": ("Loading",),
    "MinigameMenu": ("Loading",),
    "MinigameResult": ("Loading",),
    "MinigameRules": ("Loading",),
    "MinigameTop": ("Loading",),
    "MissionPopup": ("Loading",),
    "OnAir": ("Loading",),
    "Options": ("Loading",),
    "Owned": ("Loading",),
    "PassCommu": ("FesDetailPopup", "Loading", "RankUpCommu", "RewardDetail", "SeasonEndCommu"),
    "ProduceSelect": ("Loading",),
    "ProduceSelectOther": ("Loading", "ProduceSelect"),
    "RankPyramid": ("Loading",),
    "ResultJudge": ("Loading",),
    "Review": ("Loading",),
    "Reward": ("Loading",),
    "RewardDetail": ("EndDestructiveDialog", "Loading", "Reward"),
    "RewardSellMode": ("Loading",),
    "Schedule": ("ActiveSkills", "Loading"),
    "StartConfirm": ("Loading",),
    "SupportDetail": ("Loading",),
    "SupportSkillCutIn": ("Loading",),
    "TitleScreen": ("Loading",),
    "TotalEval": ("Loading",),
    "Trend": ("Loading",),
    "Tutorial": ("ItemPopup", "ItemUsed", "Loading", "MinigameRules"),
    "UnitEditDialog": ("Loading",),
    "UnitSelect": ("Loading", "UnitEditDialog"),
    "WingQualified": ("Loading",),
}


def thumb(image):
    """80x45 grey thumbnail of a 1280x720 frame (channel order does not matter); None for a non-array."""
    try:
        return image[::THUMB_STEP, ::THUMB_STEP].astype("float32").mean(axis=2)
    except Exception:
        return None


def view_diff(a, b) -> tuple[float, float] | None:
    """(mean |Δ|, largest block mean |Δ|) of two thumbnails; None when they cannot be compared."""
    if a is None or b is None or getattr(a, "shape", None) != getattr(b, "shape", None):
        return None
    import numpy as np
    d = np.abs(a - b)
    h, w = d.shape
    hb, wb = h // VIEW_BLOCK * VIEW_BLOCK, w // VIEW_BLOCK * VIEW_BLOCK
    blocks = d[:hb, :wb].reshape(hb // VIEW_BLOCK, VIEW_BLOCK, wb // VIEW_BLOCK, VIEW_BLOCK).mean(axis=(1, 3))
    return float(d.mean()), float(blocks.max())


def same_view(a, b) -> bool:
    diff = view_diff(a, b)
    return diff is not None and diff[0] <= VIEW_MEAN_DIFF and diff[1] <= VIEW_BLOCK_DIFF
