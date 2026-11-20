"""Durable spend ledger of the W.I.N.G. プロデュース開始 (stamina 30), 2026-10-11 review P0-1.

One launcher run may press プロデュース開始 at most once, but a run (segment) is a separate process: before this ledger
an uncertain start (tap sent, the expected Produce screen never confirmed) could be followed by a new segment that
started again. The ledger is written BEFORE the tap and moved through its states:

``intent`` (written before the tap; the tap is cancelled when the write fails) -> ``sent`` (controller proved the
gesture) | ``not_sent`` (controller proved no gesture) -> ``confirmed`` (an in-Produce screen was observed afterwards)
| ``uncertain`` (the run ended before confirmation).

``intent`` / ``sent`` / ``uncertain`` are *unresolved*: a later W.I.N.G. run never presses プロデュース開始 until a
fresh observation proves the state (an earlier uncertain result only warns, the screen decides) -- a
Produce in progress resolves the entry as ``confirmed`` (the run continues it); the home screen with no Produce in
progress marks it ``rechecked`` and the run may start one as usual. An unreadable ledger still stops (no intent could be
written). The user may also resolve an entry by hand (``tools/wing_spend_ledger.py resolve``). Nothing here retries a
spend within the run that sent it. The file is private (``dev_assets/private``), written atomically (temp file + ``os.replace``).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "dev_assets" / "private" / "wing-spend-ledger.json"
PENDING = ("intent", "sent", "uncertain")
STATES = PENDING + ("not_sent", "confirmed", "resolved_by_user", "rechecked")
MAX_ENTRIES = 200


def game_day(now: _dt.datetime | None = None) -> str:
    """Game day (05:00 JST rollover = UTC+4), the same key as the host audit files."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return (now.astimezone(_dt.timezone.utc) + _dt.timedelta(hours=4)).strftime("%Y-%m-%d")


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


class LedgerError(RuntimeError):
    """The ledger could not be read or written: the caller must not spend."""


class SpendLedger:
    """File-backed (``path``) or in-memory (``path=None``: offline replay/tests without a file) ledger."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else None
        self._entries: list[dict] = []

    # ---- storage --------------------------------------------------------------------------------------------
    def load(self) -> list[dict]:
        if self.path is None:
            return [dict(e) for e in self._entries]
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LedgerError(f"体力消耗账本无法读取：{exc}") from None
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list) or not all(isinstance(e, dict) and e.get("state") in STATES
                                                    and isinstance(e.get("key"), str) for e in entries):
            raise LedgerError("体力消耗账本格式无效")
        return entries

    def _save(self, entries: list[dict]) -> None:
        entries = entries[-MAX_ENTRIES:]
        if self.path is None:
            self._entries = [dict(e) for e in entries]
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + ".tmp")
            with open(temp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=1) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp, self.path)
        except OSError as exc:
            raise LedgerError(f"体力消耗账本无法写入：{exc}") from None

    # ---- queries --------------------------------------------------------------------------------------------
    def pending(self) -> list[dict]:
        return [e for e in self.load() if e.get("state") in PENDING]

    def get(self, key: str) -> dict | None:
        return next((e for e in self.load() if e.get("key") == key), None)

    # ---- transitions ----------------------------------------------------------------------------------------
    def record_intent(self, action: str, *, run_id: str | None = None, frame_sha: str | None = None,
                      detail: dict | None = None) -> str:
        """Write the ``intent`` entry before the tap; returns its key. Raises LedgerError (=> do not tap)."""
        run_id = run_id or uuid.uuid4().hex[:12]
        key = f"{game_day()}:{run_id}"
        entries = self.load()
        if any(e.get("key") == key for e in entries):
            raise LedgerError("同一运行已有体力消耗记录")
        stamp = _now()
        entries.append({"key": key, "game_day": key.split(":", 1)[0], "run_id": run_id, "action": action,
                        "state": "intent", "frame_sha": frame_sha, "created_at": stamp, "updated_at": stamp,
                        "detail": dict(detail or {}), "history": [["intent", stamp]]})
        self._save(entries)
        return key

    def mark(self, key: str, state: str, note: str | None = None) -> dict:
        if state not in STATES:
            raise ValueError("未知账本状态")
        entries = self.load()
        for entry in entries:
            if entry.get("key") == key:
                stamp = _now()
                entry["state"], entry["updated_at"] = state, stamp
                entry.setdefault("history", []).append([state, stamp] + ([note] if note else []))
                if note:
                    entry["note"] = note
                self._save(entries)
                return dict(entry)
        raise LedgerError(f"账本中没有 {key}")
