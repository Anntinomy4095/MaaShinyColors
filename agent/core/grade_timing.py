"""Grade Fes APPEAL bar: host-timed stop tap (opt-in ``live_timed_click``) and its post-tap readback.

The daily's Grade live tapped the bar untimed, so Perfect came only by chance (the white run is about
a quarter of a period). The Grade bar has the W.I.N.G. geometry and colours (x 543..890; private 2026-10-03 frames
229/240/303/304 read by ``wing_live_timing.read_timing_bar``), so the tap reuses the W.I.N.G. host-clock plan
(``SC.AppealTiming.v2`` hosts accept ``live-appeal-timing`` / ``SC.Grade.Live.Timing``):

- the bar waits for input (the 10-03 manual live kept one bar >10 s), so up to ``FRAMES`` frames of the same bar are
  pooled with ``fit_phase`` before the tap: a capture midpoint is only a noisy render time (the capture API takes
  ~240-470 ms) and pooling averages that noise;
- ``PERIOD_S`` 0.965 s (Grade 0.3 s series 2026-10-03) and ``EVENT_OFFSET_MS`` 150 (W.I.N.G. 2026-10-14/15: 11
  Perfect, 1 narrow-zone Good) still need Grade-specific calibration from the logged plans and results;
- the plan binds to the last frame's pixels and host capture (one shot; the gate refuses it on any other frame);
- frames without a readable bar or capture are skipped; after ``MAX_FRAMES`` frames, on a host without v2, or for a
  memory appeal the tap goes out untimed as before (``live_timing_fallback``). The live never stops for timing:
  the participation right is already spent.

The stopped bar stays on screen only ~0.3 s after the tap (2026-10-24 recording, 10 fps), well before Maa's next
frame, so the result is read afterwards from the host's post-tap burst (``tools/appeal_timing_report.py``), linked
to the ``live_timing_plan`` record by ``target_down_ms``.
"""
from __future__ import annotations

import dataclasses

from agent.core import wing_live_timing as wlt

ACTION = "live-appeal-timing"
PERIOD_S = 0.965
EVENT_OFFSET_MS = 150.0
FRAMES = 3            # bar frames pooled before the tap
MAX_FRAMES = 6        # bar frames seen (read or not) before the untimed fallback


def _call(rt, name):
    fn = getattr(rt.authority, name, None)
    return fn if callable(fn) else None


def _state(live):
    if live.timing is None or live.timing.get("turn") != live.turn:
        live.timing = {"turn": live.turn, "obs": [], "frames": [], "misses": 0, "bar": None}
    return live.timing


def _untimed(rt, live, reason):
    configure = _call(rt, "configure_appeal_timing")
    if configure is not None:
        configure(False)  # this one tap goes out without a plan (the previous behaviour)
    state = _state(live)
    if state.get("fallback") != reason:
        state["fallback"] = reason
        rt._log({"event": "live_timing_fallback", "turn": live.turn, "reason": reason,
                 "frames": len(state["frames"]), "readable": len(state["obs"])})
    return True


def prepare(rt, live, image, digest, *, memory=False):
    """True: tap on this frame (a plan is staged, or the untimed fallback); False: wait for another frame of the bar."""
    state = _state(live)
    supported = _call(rt, "appeal_timing_supported")
    if memory:
        return _untimed(rt, live, "memory_appeal")
    if supported is None or _call(rt, "configure_appeal_timing") is None or not supported(ACTION):
        return _untimed(rt, live, "host_without_grade_timing")
    if digest not in state["frames"]:
        state["frames"].append(digest)
        bar = wlt.read_timing_bar(image[..., ::-1])  # Maa frames are BGR
        interval = _call(rt, "capture_interval")(image)
        if bar is None or bar.cursor_x is None or interval is None:
            state["misses"] += 1
            state["bar"] = None
        else:
            state["obs"].append((interval[0], interval[1], bar.cursor_x))
            state["bar"] = bar
    bar, seen = state["bar"], len(state["frames"])
    if bar is None or len(state["obs"]) < FRAMES:
        if seen < MAX_FRAMES:
            return False
        if bar is None:
            return _untimed(rt, live, "bar_unread")
    fit = wlt.fit_phase(state["obs"][-FRAMES:], period_s=PERIOD_S)
    planned = dataclasses.replace(bar, cursor_x=fit["cursor_x"]) if fit else bar
    rt.authority.configure_appeal_timing(True)
    refusal, plan = rt.authority.stage_appeal_timing(image, planned, period_s=PERIOD_S,
                                                     event_offset_ms=EVENT_OFFSET_MS, action=ACTION)
    rt._log({"event": "live_timing_plan", "turn": live.turn, "refusal": refusal, "plan": plan, "fit": fit,
             "frame_cursor_x": bar.cursor_x, "observations": [list(o) for o in state["obs"]]})
    if refusal:
        if seen < MAX_FRAMES:
            state["bar"] = None  # this frame cannot carry the plan; try the next one
            return False
        return _untimed(rt, live, refusal)
    state["plan"] = plan
    return True

