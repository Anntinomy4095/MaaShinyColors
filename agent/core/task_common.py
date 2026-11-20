"""Shared pieces of the B5 daily tasks (Training, GradeFes, MatchSkip): pure helpers, no Maa import, no input.

Pattern (same as the B4 Shop task, agent/core/shop_task.py): the Pipeline owns navigation; Agent custom
recognitions read the screen and decide; state comes from the game screens plus an append-only JSONL evidence
log per task and JST game day (``dev_assets/private/run_logs/<task>-<game day>.jsonl``). The log is evidence,
never a lock: deleting it only makes the task more conservative for that day.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "dev_assets" / "private"
LOG_ROOT = PRIVATE / "run_logs"
SAMPLE_ROOT = LOG_ROOT / "samples"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def utc_now():
    return _dt.datetime.now(_dt.timezone.utc)


def game_day(now):
    """JST game day with the 05:00 boundary (same as the host's GameDayOf)."""
    return (now.astimezone(_dt.timezone.utc) + _dt.timedelta(hours=4)).strftime("%Y-%m-%d")


def params(raw):
    """Maa passes custom params as a JSON string (or already-decoded object)."""
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw else raw
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def image_digest(image):
    return hashlib.sha256(image.tobytes()).hexdigest()


def normalize_text(text):
    """NFKC, then drop all whitespace/line breaks (OCR lines may wrap)."""
    require(isinstance(text, str), "文本必须为字符串")
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def to_int(text):
    """'22,614' / '22614' / 'x22614' -> int; None when absent or malformed."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"[xX×]?([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)", text)
    return int(match.group(1).replace(",", "")) if match else None


def ratio(text):
    """'20/20' -> (20, 20); None when malformed or current > maximum."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})", text)
    if not match:
        return None
    current, maximum = int(match.group(1)), int(match.group(2))
    return (current, maximum) if 0 <= current <= maximum and maximum > 0 else None


class JsonlLog:
    """Append-only JSONL per task and JST game day (fsync per line). Evidence, not a lock."""

    def __init__(self, prefix, root=LOG_ROOT, now=utc_now):
        root = Path(root).resolve()
        require(root.is_relative_to(PRIVATE.resolve()), "任务日志必须位于 dev_assets/private")
        require(isinstance(prefix, str) and re.fullmatch(r"[a-z]+", prefix), "日志前缀无效")
        self.prefix, self.root, self.now = prefix, root, now

    def path(self, day=None):
        return self.root / f"{self.prefix}-{day or game_day(self.now())}.jsonl"

    def append(self, record):
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"time": self.now().isoformat(), "game_day": game_day(self.now()), **record},
                          ensure_ascii=False, sort_keys=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return path

    def events(self):
        path = self.path()
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class FrameSaver:
    """Saves sampled frames (BGR 1280x720) under dev_assets/private/run_logs/samples/<name>-<UTC stamp>/."""

    def __init__(self, name, root=SAMPLE_ROOT, now=utc_now, limit=80):
        root = Path(root).resolve()
        require(root.is_relative_to(PRIVATE.resolve()), "采样帧必须位于 dev_assets/private")
        self.name, self.root, self.now, self.limit = name, root, now, limit
        self.directory, self.saved = None, []

    def save(self, image):
        if len(self.saved) >= self.limit:
            return None
        from PIL import Image

        if self.directory is None:
            self.directory = self.root / (self.name + "-" + self.now().strftime("%Y%m%dT%H%M%S%fZ"))
            self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"frame-{len(self.saved) + 1:03d}.png"
        Image.fromarray(image[:, :, ::-1].copy()).save(path, format="PNG")
        self.saved.append(str(path))
        return path


# ---------------------------------------------------------------- numeric OCR preprocessing (pure numpy)
# Same arithmetic as the verified training/shop OCR path (tools/run_daily_training.py, agent/core/shop_custom.py):
# fixed crop -> channel -> normalise -> threshold -> bilinear x3 -> pad 6, then Maa OCR only_rec at 0.95.
OCR_SCALE, OCR_PADDING = 3, 6
LEVEL_VARIANTS = ("threshold128", "percentile_gray", "otsu", "bold128")


def resize_numeric(image, scale=OCR_SCALE):
    import numpy as np
    height, width = image.shape
    ys, xs = np.linspace(0, height - 1, height * scale), np.linspace(0, width - 1, width * scale)
    y0, x0 = ys.astype(int), xs.astype(int)
    y1, x1 = np.minimum(y0 + 1, height - 1), np.minimum(x0 + 1, width - 1)
    wy, wx = ys - y0, xs - x0
    top = image[y0[:, None], x0[None, :]] * (1 - wx)[None, :] + image[y0[:, None], x1[None, :]] * wx[None, :]
    bottom = image[y1[:, None], x0[None, :]] * (1 - wx)[None, :] + image[y1[:, None], x1[None, :]] * wx[None, :]
    return np.round(top * (1 - wy)[:, None] + bottom * wy[:, None]).astype(np.uint8)


def otsu_cutoff(gray):
    import numpy as np
    values = np.round(gray).astype(int).ravel()
    histogram = np.bincount(values, minlength=256).astype(float)
    levels = np.arange(256)
    best, cutoff = -1.0, 128
    for candidate in range(1, 255):
        low, high = histogram[:candidate + 1], histogram[candidate + 1:]
        if low.sum() == 0 or high.sum() == 0:
            continue
        mean_low = (low * levels[:candidate + 1]).sum() / low.sum()
        mean_high = (high * levels[candidate + 1:]).sum() / high.sum()
        spread = low.sum() * high.sum() * (mean_low - mean_high) ** 2
        if spread > best:
            best, cutoff = spread, candidate
    return cutoff


def numeric_pixels(image, crop, kind, cutoff=128, variant=None):
    """Processed 3-channel pixels for only_rec OCR; None on a blank/low-contrast crop.

    ``kind``: ``dark`` (dark text on light), ``light`` (light text on dark) or ``blue_inv``.
    ``variant``: None or one of LEVEL_VARIANTS (the lone-level-digit unanimity rule).
    """
    import numpy as np
    x, y, w, h = crop
    patch = image[y:y + h, x:x + w].astype(float)  # BGR
    raw = 255 - patch[:, :, 0] if kind == "blue_inv" else 255 - patch.mean(axis=2) if kind == "light" else patch.mean(axis=2)
    contrast = float(raw.max() - raw.min())
    if contrast < 20:
        return None
    gray = (raw - raw.min()) * 255 / contrast
    if variant == "percentile_gray":
        low, high = np.percentile(raw, 2), np.percentile(raw, 98)
        binary = np.round(np.clip((raw - low) * 255 / max(1.0, high - low), 0, 255)).astype(np.uint8)
    else:
        if variant == "otsu":
            cutoff = otsu_cutoff(gray)
        binary = np.where(gray > cutoff, 255, 0)
        if variant == "bold128":
            bold = binary.copy()
            bold[:, 1:] = np.minimum(bold[:, 1:], binary[:, :-1])
            bold[1:, :] = np.minimum(bold[1:, :], bold[:-1, :])
            binary = bold
    padded = np.pad(resize_numeric(binary), ((OCR_PADDING,) * 2,) * 2, constant_values=255)
    return np.ascontiguousarray(np.repeat(padded[:, :, None], 3, axis=2).astype(np.uint8))


# ---------------------------------------------------------------- per-run state
class TaskRun:
    """State of one task run (reset by the task's Begin action). Holds no authority of its own.

    ``phase``: task-specific progress names, ``finished`` once an outcome is set. ``halt`` outcomes end the
    task at once without any further click (the next task's GoHome handles the screen).
    """
    SUCCESS = frozenset()
    HALT = frozenset({"spend_uncertain", "spend_refused", "timeout"})

    def __init__(self, config, log):
        self.config, self.log = config, log
        self.phase = "active"
        self.outcome = None
        self.notes = []
        self.decisions = []
        self.reported = False
        self.confirm_rejected = None
        self.intent = None
        self.unreadable = 0

    @property
    def active(self):
        return self.phase not in ("finished",)

    def finish(self, outcome, *notes):
        if self.phase != "finished":
            self.phase, self.outcome = "finished", outcome
        self.notes.extend(n for n in notes if n)

    @property
    def finished(self):
        return self.phase == "finished"

    @property
    def halted(self):
        return self.finished and self.outcome in self.HALT

    @property
    def succeeded(self):
        return self.finished and self.outcome in self.SUCCESS

    def note(self, record):
        self.decisions.append(record)
        del self.decisions[:-16]

    def summary(self):
        return {"outcome": self.outcome, "phase": self.phase, "succeeded": self.succeeded, "notes": list(self.notes),
                "config": self.config.to_dict(), "decisions": list(self.decisions), "log": str(self.log.path())}
