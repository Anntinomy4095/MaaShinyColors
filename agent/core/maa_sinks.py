"""Event sinks the Maa Python binding would otherwise keep for the life of the process."""
from __future__ import annotations


def track(owner, remove: str, sink_id) -> None:
    """Record a sink id from ``owner.add_*_sink`` for :func:`release`; ``remove`` names the matching remove method."""
    ids = getattr(owner, "_sc_sink_ids", None)
    owner._sc_sink_ids = [*(ids if isinstance(ids, list) else ()), (remove, sink_id)]


def release(owner) -> None:
    """Unregister the recorded sinks. The binding keeps every sink in a class-level dict (``Tasker._sink_holder``) until
    ``remove_*_sink`` (``clear_sinks`` leaves it there); ours reach the tasker, its Resource and OCR model, so every run
    in one process stayed alive (2026-10-27: +0.33 GB per replay test, 17 GB after 47 tests, then a native abort in
    OCR)."""
    ids = getattr(owner, "_sc_sink_ids", None)
    if not isinstance(ids, list):   # nothing tracked (test stand-ins included)
        return
    for remove, sink_id in ids:
        try:
            getattr(owner, remove)(sink_id)
        except Exception:
            pass
    owner._sc_sink_ids = []
