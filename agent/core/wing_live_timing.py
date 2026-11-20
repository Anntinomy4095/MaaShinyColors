"""W.I.N.G. Live APPEAL timing bar: read the bar, predict the cursor and plan when to tap (pure, no input).

Evidence (2026-10-10 offline study; private frames referenced by number only, never committed):

* Layout (1280x720): the inner bar spans x 543..890 at y ~485..522. Zones are fixed colour runs, left to right:
  full メンタル: Normal cyan 543..716, Good orange 718..803, Perfect white 805..890 (demo2 #1953, #1954, ...);
  reduced メンタル (demo1 #548 / #742, 255/398 = 64 %): Bad purple 543..627, Normal 629..716, Good 718..803,
  Perfect 805..838, Good 839..890. The layout must therefore be read per frame, never assumed.
* Cursor: a thin (~8 px) yellow vertical line crossing the bar and the dark frame above/below it.
* Motion: a left-to-right sawtooth (wraps from 890 back to 543), linear at ~355-365 px/s, period ~0.965 s (Grade Fes
  0.3 s series 2026-10-03, residual <1 % of a period); the W.I.N.G. demo (1.5 s sampling, aliased) fits 0.98-1.0 s.
* The judgement follows the cursor position at the tap (Grade turn 1: tap flash on white -> Perfect; turn 2: flash at
  the left end -> Normal).

``plan_tap`` returns the delay after which a tap issued by our pipeline (fixed, calibrated ``latency_s`` from the end of
the decision to the pointer-down) lands on the centre of the widest Perfect run. The caller decides whether to sleep
that long (bounded by one period) or to wait for another frame; an unreadable bar returns ``None`` so the caller keeps
its previous behaviour (tap now) instead of letting the game's own timeout pick Normal.
"""
from __future__ import annotations

import dataclasses
import math
import re
from typing import Any, Iterable, Sequence

BAR_X0, BAR_X1 = 543, 890            # inner bar, inclusive, 1280x720 canvas
BAR_SPAN = BAR_X1 - BAR_X0 + 1       # 348 px == one period
BAR_ROWS = (497, 503, 509)           # rows sampled for the zone colours (median label per column)
CURSOR_BANDS = ((468, 479), (527, 534))  # dark frame above / below the bar where only the cursor is yellow
DEFAULT_PERIOD_S = 0.97              # 0.965 (Grade, dense) .. 0.99 (W.I.N.G. demo, aliased): calibrate per run
# Maa path after the decision returns "click" -> pointer-down, run log 2026-10-10 (4 linked appeal taps): capture->intent
# minus decision seconds = 0.617/0.624/0.620/0.617 s (Click pre_delay 500 ms + Maa/host overhead), intent->Down ~0.09 s
# (audit write + PipelineGesture hover 50+30 ms). Unverified by a timed tap: calibrate from logged results.
# 2026-10-28: Click pre_delay is 0 and the APPEAL tap has no settle wait (wing_custom.NO_SETTLE_INTENTS): 0.71 - 0.5.
DEFAULT_LATENCY_S = 0.21
CAPTURE_TO_DECIDE_S = 0.05           # host frame age at capture (median 45 ms, p90 69 ms)
PERIOD_RANGE_S = (0.90, 1.05)
MIN_COVERAGE = 0.90                  # labelled fraction of the bar needed to trust the layout
MIN_RUN_PX = 12                      # shorter runs are sparkles / the cursor's own colour fringe
# 2026-10-14 live a6 (turn 4, wing_samples/2026-10-14/20261014T090346941322Z): a narrow Perfect strip of 9-11 px between
# two Good runs (x 779-789). A white run this wide with Good on both sides is the Perfect zone, not a sparkle.
MIN_PERFECT_PX = 6
MERGE_GAP_PX = 14                    # the cursor (or a sparkle) hides up to this many columns of one zone
JUDGEMENT_MULTIPLIER = {"Perfect": 1.5, "Good": 1.1, "Normal": 1.0, "Bad": 0.5}
_LABELS = {"P": "Perfect", "G": "Good", "N": "Normal", "B": "Bad"}


@dataclasses.dataclass(frozen=True)
class TimingBar:
    zones: tuple[tuple[str, int, int], ...]   # (judgement, x0, x1) inclusive, left to right
    cursor_x: float | None                     # None: cursor hidden (sparkle, bar ends, flash)

    def zone_at(self, x: float) -> str | None:
        for name, x0, x1 in self.zones:
            if x0 - 1 <= x <= x1 + 1:
                return name
        return None

    def perfect_aim(self) -> float | None:
        """Centre of the widest Perfect run (the aim point of a planned tap)."""
        runs = [(x1 - x0, (x0 + x1) / 2) for name, x0, x1 in self.zones if name == "Perfect"]
        return max(runs)[1] if runs else None


@dataclasses.dataclass(frozen=True)
class TimingPlan:
    delay_s: float           # wait this long after the decision, then issue the tap
    tap_now: bool            # delay is within ``tolerance_s`` (or a period minus it): tap without waiting
    aim_x: float
    cursor_x: float
    predicted_x: float       # cursor position at the expected pointer-down if the caller waits ``delay_s``
    predicted_zone: str | None
    window_s: float          # width of the aimed Perfect run in seconds


def host_timing_plan(bar: TimingBar | None, *, capture_started_ms: float,
                     capture_completed_ms: float, period_s: float = DEFAULT_PERIOD_S,
                     event_offset_ms: float = 0.0) -> dict | None:
    """A host-clock Down deadline; capture midpoint is an estimate, not a render timestamp.

    The host rolls this phase forward after its cooldown and hover. ``event_offset_ms``
    is the estimated Down-to-game judgement delay (0 until supervised calibration).
    No Python clock or fixed RPC/recognition latency enters the deadline.
    """
    values = (capture_started_ms, capture_completed_ms, period_s, event_offset_ms)
    if (not all(type(v) in (int, float) and math.isfinite(v) for v in values)
            or not 0 <= capture_started_ms <= capture_completed_ms
            or not PERIOD_RANGE_S[0] <= period_s <= PERIOD_RANGE_S[1]
            or not 0 <= event_offset_ms <= 200
            or bar is None or bar.cursor_x is None):
        return None
    aim = bar.perfect_aim()
    if aim is None:
        return None
    period_ms = period_s * 1000
    estimate = (capture_started_ms + capture_completed_ms) / 2
    phase_ms = ((phase(aim) - phase(bar.cursor_x)) % 1) * period_ms
    target = estimate + (phase_ms - event_offset_ms) % period_ms
    latest = capture_started_ms + 5000
    if target > latest:
        return None
    width = max(x1 - x0 + 1 for name, x0, x1 in bar.zones
                if name == "Perfect" and (x0 + x1) / 2 == aim)
    return {"request": {"target_down_ms": target, "period_ms": period_ms, "latest_down_ms": latest},
            "capture_estimated_ms": estimate,
            "capture_half_interval_ms": (capture_completed_ms - capture_started_ms) / 2,
            "aim_x": aim, "cursor_x": bar.cursor_x, "zones": bar.zones,
            "window_ms": width / BAR_SPAN * period_ms,
            "period_source": "configured", "event_offset_ms": event_offset_ms,
            "judgement_event": "uncalibrated"}


def fit_phase(observations: Sequence[tuple[float, float, float]], *, period_s: float = DEFAULT_PERIOD_S,
              max_spread_ms: float = 60.0) -> dict | None:
    """Pool several frames of ONE bar: ``(capture_started_ms, capture_completed_ms, cursor_x)`` each, oldest first.

    Each frame's capture midpoint is a noisy estimate of when its pixels were rendered (the capture API takes
    ~240-470 ms). The cursor is a fixed sawtooth, so every frame implies the same phase at a common reference time;
    their circular mean is the pooled estimate. Returns ``cursor_x`` -- the pooled cursor position at the LAST
    frame's midpoint, ready for ``host_timing_plan`` on that frame -- and ``spread_ms``, the worst disagreement.
    One outlier (sparkle, half-hidden cursor) is dropped when at least three frames remain; a larger spread
    returns None so the caller keeps the single-frame plan."""
    if not PERIOD_RANGE_S[0] <= period_s <= PERIOD_RANGE_S[1]:
        return None
    rows = [(float(s), float(c), float(x)) for s, c, x in observations
            if all(isinstance(v, (int, float)) and math.isfinite(v) for v in (s, c, x)) and 0 <= s <= c]
    if len(rows) < 2:
        return None
    period_ms = period_s * 1000
    t_last = (rows[-1][0] + rows[-1][1]) / 2
    # Phase every frame implies for the last frame's midpoint.
    phases = [(phase(x) + (t_last - (s + c) / 2) / period_ms) % 1.0 for s, c, x in rows]

    def pooled(values):
        angle = math.atan2(sum(math.sin(2 * math.pi * p) for p in values), sum(math.cos(2 * math.pi * p) for p in values))
        mean = (angle / (2 * math.pi)) % 1.0
        errors = [abs((p - mean + 0.5) % 1.0 - 0.5) * period_ms for p in values]
        return mean, errors

    mean, errors = pooled(phases)
    dropped = None
    if max(errors) > max_spread_ms and len(phases) >= 4:
        dropped = errors.index(max(errors))
        mean, errors = pooled([p for i, p in enumerate(phases) if i != dropped])
    if max(errors) > max_spread_ms:
        return None
    return {"cursor_x": round(x_at_phase(mean), 2), "spread_ms": round(max(errors), 1), "frames": len(rows),
            "dropped": dropped, "period_s": period_s}


def _classify(rgb) -> str:
    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    if r > 225 and g > 225 and b > 225:
        return "P"
    if r > 200 and 130 < g < 215 and b < 90:
        return "G"
    if r < 90 and g > 150 and b > 150:
        return "N"
    if r > 90 and g < 100 and b > 170:
        return "B"
    return "."


def _runs(labels: Sequence[str]) -> list[list]:
    runs: list[list] = []
    for i, c in enumerate(labels):
        x = BAR_X0 + i
        if runs and runs[-1][0] == c:
            runs[-1][2] = x
        else:
            runs.append([c, x, x])
    return runs


def _zones(labels: Sequence[str]) -> list[list] | None:
    raw = [r for r in _runs(labels) if r[0] != "."]

    def keep(i: int) -> bool:
        c, x0, x1 = raw[i]
        if x1 - x0 + 1 >= MIN_RUN_PX:
            return True
        return (c == "P" and x1 - x0 + 1 >= MIN_PERFECT_PX and 0 < i < len(raw) - 1
                and raw[i - 1][0] == raw[i + 1][0] == "G" and x0 - raw[i - 1][2] <= 3 and raw[i + 1][1] - x1 <= 3)

    runs: list[list] = []
    for i, r in enumerate(raw):
        # A short piece of the zone the cursor just cut (a6 turn 4: Bad 543-601 | cursor | Bad 611-621) continues
        # the previous kept run of the same colour.
        if keep(i) or (runs and runs[-1][0] == r[0] and r[1] - runs[-1][2] - 1 <= MERGE_GAP_PX):
            runs.append(r)
    merged: list[list] = []
    for run in runs:
        if merged and merged[-1][0] == run[0] and run[1] - merged[-1][2] - 1 <= MERGE_GAP_PX:
            merged[-1][2] = run[2]
        else:
            merged.append(list(run))
    if not merged:
        return None
    # Hidden columns between two different zones (cursor on a boundary) go to the zones' shared edge.
    merged[0][1], merged[-1][2] = BAR_X0, BAR_X1
    for left, right in zip(merged, merged[1:]):
        gap = right[1] - left[2] - 1
        if gap > MERGE_GAP_PX:
            return None
        left[2] += gap // 2
        right[1] = left[2] + 1
    return merged


def _cursor_x(a) -> float | None:
    import numpy as np
    band = np.concatenate([a[y0:y1, BAR_X0 - 13:BAR_X1 + 10, :3] for y0, y1 in CURSOR_BANDS], axis=0).astype(int)
    yellow = ((band[..., 0] > 190) & (band[..., 1] > 180) & (band[..., 2] < 170)).sum(axis=0)
    cols = np.where(yellow >= band.shape[0] * 0.6)[0]
    if len(cols) == 0 or cols.max() - cols.min() > 12:
        return None
    x = float(cols.mean()) + BAR_X0 - 13
    return min(max(x, float(BAR_X0)), float(BAR_X1))


def read_timing_bar(image: Any) -> TimingBar | None:
    """The APPEAL bar on an RGB 1280x720 frame, or None when the bar is not cleanly visible."""
    import numpy as np
    a = np.asarray(image)
    if a.ndim != 3 or a.shape[0] != 720 or a.shape[1] != 1280:
        return None
    rows = [[_classify(p) for p in a[y, BAR_X0:BAR_X1 + 1, :3]] for y in BAR_ROWS]
    labels = []
    for column in zip(*rows):
        known = [c for c in column if c != "."]
        labels.append(max(set(known), key=known.count) if len(known) >= 2 else ".")
    if sum(c != "." for c in labels) < MIN_COVERAGE * BAR_SPAN:
        return None
    zones = _zones(labels)
    if not zones or not any(z[0] == "P" for z in zones):
        return None
    # The four メンタル stages (wikiwiki 「オーディションの基本」, 2022-08 revision): 1 = N G P (wide Perfect at the
    # right end), 2 = B N G P G, 3 = the same with a thin 「棒」 Perfect, 4 = Good much wider, Bad/Normal narrower.
    # Accept that family -- optional Bad, optional Normal, Good, exactly one Perfect, optional trailing Good --
    # anything else (two Perfect runs, Perfect first, ...) is a flash / overlay frame.
    if not re.fullmatch(r"B?N?GPG?", "".join(z[0] for z in zones)):
        return None
    return TimingBar(tuple((_LABELS[c], x0, x1) for c, x0, x1 in zones), _cursor_x(a))


STOP_BAND = (527, 535)                     # dark frame under the bar: the stopped cursor stays a narrow yellow column
STOP_MAX_WIDTH = 8
CONFETTI_BANDS = ((430, 484), (524, 585))  # above / below the bar, x 540..910
CONFETTI_MIN_PX = 800


def read_appeal_result(image: Any, zones: Sequence[tuple[str, int, int]] | None = None) -> dict | None:
    """One post-tap frame (host burst): where the cursor stopped and, when visible, the judgement.

    Evidence (private: 2026-10-03 Grade frames, 2026-10-14 W.I.N.G. bursts, 2026-10-24 Grade recording at 10 fps):
    the stopped bar stays ~0.3 s; the cursor is a narrow yellow column in the dark frame under the bar, while the tap's
    expanding ring adds further columns -- ``stop_x`` is given only when exactly one narrow column is left (callers
    with several frames should prefer the column that persists). Perfect throws multicolour (pink / lavender) confetti
    around the bar on W.I.N.G. (Grade shows few: rely on ``stop_x`` there), Good yellow stars and a large flash, Normal
    a small white sparkle. The confetti decides Perfect; otherwise ``zones`` (the planned bar's layout) turn
    ``stop_x`` into the judgement. On the labelled private frames: 21 of 27 judged, none wrong."""
    import numpy as np
    a = np.asarray(image)
    if a.ndim != 3 or a.shape[0] != 720 or a.shape[1] != 1280:
        return None
    y0, y1 = STOP_BAND
    band = a[y0:y1, BAR_X0 - 3:BAR_X1 + 4, :3].astype(int)
    r, g, b = band[..., 0], band[..., 1], band[..., 2]
    yellow = ((r > 180) & (g > 170) & (b < 200) & (r + g > 2.2 * b)).mean(axis=0)
    clusters: list[list[int]] = []
    for c in np.where(yellow >= 0.6)[0]:
        x = int(c) + BAR_X0 - 3
        if clusters and x - clusters[-1][1] <= 2:
            clusters[-1][1] = x
        else:
            clusters.append([x, x])
    candidates = [(x0 + x1) / 2 for x0, x1 in clusters
                  if x1 - x0 + 1 <= STOP_MAX_WIDTH and BAR_X0 - 2 <= (x0 + x1) / 2 <= BAR_X1 + 2]
    stop = candidates[0] if len(candidates) == 1 else None
    region = np.concatenate([a[y0:y1, 540:910, :3] for y0, y1 in CONFETTI_BANDS], axis=0).astype(int)
    r, g, b = region[..., 0], region[..., 1], region[..., 2]
    pink = int(((r > 200) & (g < 175) & (b > 200)).sum())
    lavender = int(((b > 220) & (r > 110) & (r < 200) & (g > 110) & (g < 200)).sum())
    confetti = pink >= CONFETTI_MIN_PX and lavender >= CONFETTI_MIN_PX
    judgement = None
    if confetti:   # unambiguous; a lone column next to the confetti can still be a ring crossing (W.I.N.G. 10-14 #12)
        judgement = "Perfect"
    elif stop is not None and zones:
        judgement = TimingBar(tuple(tuple(z) for z in zones), None).zone_at(stop)
    return {"stop_x": stop, "candidates": candidates, "confetti": {"pink": pink, "lavender": lavender},
            "perfect_confetti": confetti, "judgement": judgement}


def phase(x: float) -> float:
    """Position on the bar as a fraction of one period (0 at the left end)."""
    return ((x - BAR_X0) / BAR_SPAN) % 1.0


def x_at_phase(p: float) -> float:
    return BAR_X0 + (p % 1.0) * BAR_SPAN


def predict_x(cursor_x: float, after_s: float, period_s: float = DEFAULT_PERIOD_S) -> float:
    """Cursor position ``after_s`` seconds after a frame that showed it at ``cursor_x``."""
    return x_at_phase(phase(cursor_x) + after_s / period_s)


def plan_tap(bar: TimingBar | None, *, elapsed_s: float, latency_s: float,
             period_s: float = DEFAULT_PERIOD_S, tolerance_s: float = 0.03) -> TimingPlan | None:
    """When to tap so the pointer-down lands on the Perfect centre.

    ``elapsed_s``: time from the frame's capture to now (decision time so far). ``latency_s``: calibrated time from now
    (the decision returning "tap") to the game's pointer-down. Returns None when the bar or cursor is unreadable."""
    if bar is None or bar.cursor_x is None or not period_s > 0:
        return None
    aim = bar.perfect_aim()
    if aim is None:
        return None
    lead = elapsed_s + latency_s
    delay = ((phase(aim) - phase(bar.cursor_x)) * period_s - lead) % period_s
    predicted = predict_x(bar.cursor_x, lead + delay, period_s)
    width = max(x1 - x0 + 1 for name, x0, x1 in bar.zones if name == "Perfect" and (x0 + x1) / 2 == aim)
    tap_now = delay <= tolerance_s or delay >= period_s - tolerance_s
    return TimingPlan(delay_s=round(delay, 4), tap_now=tap_now, aim_x=aim, cursor_x=bar.cursor_x,
                      predicted_x=round(predicted, 1), predicted_zone=bar.zone_at(predicted),
                      window_s=round(width / BAR_SPAN * period_s, 4))


def align_delay(image: Any, *, elapsed_s: float, latency_s: float = DEFAULT_LATENCY_S,
                period_s: float = DEFAULT_PERIOD_S, max_delay_s: float | None = None) -> tuple[float, TimingPlan | None]:
    """Seconds the caller should sleep before emitting the APPEAL tap (0.0 = tap now, the previous behaviour).

    ``elapsed_s`` is measured from the decision's start; the frame's capture age is added here. Never more than one
    period (or ``max_delay_s``); an unreadable bar or cursor returns (0.0, None)."""
    plan = plan_tap(read_timing_bar(image), elapsed_s=elapsed_s + CAPTURE_TO_DECIDE_S, latency_s=latency_s,
                    period_s=period_s)
    if plan is None or plan.tap_now:
        return 0.0, plan
    limit = period_s if max_delay_s is None else min(period_s, max_delay_s)
    return (plan.delay_s, plan) if plan.delay_s <= limit else (0.0, plan)


def estimate_period(observations: Iterable[tuple[float, float]], *, lo: float = PERIOD_RANGE_S[0],
                    hi: float = PERIOD_RANGE_S[1], step: float = 0.001) -> tuple[float, float] | None:
    """(period_s, worst wrapped phase residual) from >=3 (time_s, cursor_x) of ONE timing bar, searched in lo..hi.

    The sampling interval is usually longer than half a period, so only a narrow prior range is meaningful."""
    obs = [(float(t), float(x)) for t, x in observations]
    if len(obs) < 3:
        return None
    best: tuple[float, float] | None = None
    n = int(round((hi - lo) / step)) + 1
    for i in range(n):
        p = lo + i * step
        # phase offset per observation; the best common offset minimises the worst wrapped residual
        offsets = [(phase(x) - t / p) % 1.0 for t, x in obs]
        for ref in offsets:
            worst = max(abs((o - ref + 0.5) % 1.0 - 0.5) for o in offsets)
            if best is None or worst < best[1]:
                best = (round(p, 4), worst)
    return best


def expected_mix(bar: TimingBar, aim_x: float, sigma_s: float, period_s: float = DEFAULT_PERIOD_S,
                 samples: int = 2001) -> dict[str, float]:
    """Judgement distribution of a tap aimed at ``aim_x`` with Gaussian timing error ``sigma_s`` (wrapped).

    sigma_s <= 0 is an exact tap; a very large sigma approaches the random (untimed) mix."""
    import math
    out = {name: 0.0 for name in JUDGEMENT_MULTIPLIER}
    if sigma_s <= 0:
        zone = bar.zone_at(aim_x)
        if zone:
            out[zone] = 1.0
        return out
    total = 0.0
    for i in range(samples):
        e = (i / (samples - 1) * 2 - 1) * 4 * sigma_s
        w = math.exp(-0.5 * (e / sigma_s) ** 2)
        zone = bar.zone_at(predict_x(aim_x, e, period_s))
        if zone:
            out[zone] += w
            total += w
    return {k: round(v / total, 4) for k, v in out.items()} if total else out
