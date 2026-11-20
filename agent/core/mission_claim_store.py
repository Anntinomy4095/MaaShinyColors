"""Private durable wrapper for the offline claim contract; no input or Maa adapter.

Initialization is explicit. Missing, damaged or conflicting journals never become empty
history. A prepared decision escapes only after its pending event commits. This does not
protect against an externally restored older database or authorize any game operation.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
from threading import RLock

from agent.core.mission_claim import ClaimDecision, ClaimRecord, ClaimTracker


PRIVATE = Path(__file__).resolve().parents[2] / "dev_assets" / "private"
VERSION = 1
SCHEMA = {
    "claim_meta": """CREATE TABLE claim_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL,
    cycle_id TEXT NOT NULL,
    max_attempts INTEGER NOT NULL,
    event_count INTEGER NOT NULL,
    halt_reason TEXT
)""",
    "claim_events": """CREATE TABLE claim_events (
    seq INTEGER PRIMARY KEY,
    mission_id TEXT NOT NULL,
    outcome TEXT NOT NULL
)""",
}


class ClaimJournalError(RuntimeError):
    """Stable failure code; never expose database contents as a runtime decision."""


@dataclass(frozen=True)
class _Snapshot:
    history: tuple[ClaimRecord, ...]
    halt_reason: str | None
    event_count: int


def _check(condition, reason="claim_journal_invalid"):
    if not condition:
        raise ClaimJournalError(reason)


def _private_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to(PRIVATE.resolve()) or path.suffix != ".sqlite3":
        raise ValueError("领取账本必须为 dev_assets/private 内的 .sqlite3 文件")
    return path


def _commit(connection):
    connection.commit()


@contextmanager
def _transaction(path):
    connection = None
    try:
        _check(path.is_file(), "claim_journal_missing")
        # rw never creates a replacement if a previously initialized file vanishes.
        connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True,
                                     timeout=0, isolation_level=None)
        _check(connection.execute("PRAGMA journal_mode").fetchone() == ("delete",))
        connection.execute("PRAGMA synchronous=EXTRA")
        _check(connection.execute("PRAGMA synchronous").fetchone() == (3,))
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        _commit(connection)
    except (sqlite3.Error, OSError) as exc:
        raise ClaimJournalError("claim_journal_io_failed") from exc
    finally:
        # Closing a transaction that did not commit rolls it back. Never replay it.
        if connection is not None:
            connection.close()


def initialize_claim_journal(path, cycle_id, *, max_attempts=1):
    """Explicit offline creation; refuses overwrite, even after incomplete creation.

    A future runtime must select its existing trusted journal, never call this as a
    recovery fallback. A new filename is not evidence that earlier history is absent.
    """
    ClaimTracker(cycle_id, max_attempts=max_attempts)  # validate before filesystem changes
    path = _private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Reserve without truncating any existing evidence. Failed initialization stays
    # on disk and is rejected on open; there is no automatic delete/recreate path.
    with path.open("xb"):
        pass
    with _transaction(path) as connection:
        for sql in SCHEMA.values():
            connection.execute(sql)
        connection.execute("INSERT INTO claim_meta VALUES (1, ?, ?, ?, 0, NULL)",
                           (VERSION, cycle_id, max_attempts))
        _read_snapshot(connection, cycle_id, max_attempts)
    return path


def _read_snapshot(connection, cycle_id, max_attempts):
    _check(connection.execute("PRAGMA quick_check").fetchall() == [("ok",)])
    objects = connection.execute("SELECT type, name, sql FROM sqlite_master "
                                 "WHERE name NOT GLOB 'sqlite_*'").fetchall()
    _check(sorted(objects) == sorted(("table", name, sql) for name, sql in SCHEMA.items()))
    header = connection.execute("SELECT id, version, cycle_id, max_attempts, event_count, "
                                "halt_reason FROM claim_meta").fetchall()
    _check(len(header) == 1)
    key, version, stored_cycle, stored_cap, count, halt = header[0]
    _check(type(key) is int and key == 1 and type(version) is int and version == VERSION)
    _check(isinstance(stored_cycle, str) and stored_cycle and stored_cycle == stored_cycle.strip())
    _check(type(stored_cap) is int and 0 < stored_cap < 2**63)
    _check(stored_cycle == cycle_id and stored_cap == max_attempts, "claim_journal_binding_mismatch")
    _check(type(count) is int and count >= 0)
    _check(halt is None or isinstance(halt, str) and len(halt) <= 100
           and re.fullmatch(r"claim_[a-z0-9_]+", halt))
    events = connection.execute("SELECT seq, mission_id, outcome FROM claim_events ORDER BY seq").fetchall()
    _check(len(events) == count)
    records = []
    identities = set()
    for expected_seq, (seq, mission_id, outcome) in enumerate(events, 1):
        _check(type(seq) is int and seq == expected_seq)
        try:
            record = ClaimRecord(stored_cycle, mission_id, outcome)
        except ValueError as exc:
            raise ClaimJournalError("claim_journal_invalid") from exc
        if outcome == "pending":
            _check(mission_id not in identities and len(records) < stored_cap
                   and all(r.outcome == "claimed" for r in records))
            identities.add(mission_id)
            records.append(record)
        else:
            _check(bool(records) and records[-1].mission_id == mission_id
                   and records[-1].outcome == "pending")
            records[-1] = record
    return _Snapshot(tuple(records), halt, count)


def _checkpoint(connection, before, history, halt):
    transition = None
    if history != before.history:
        if (len(history) == len(before.history) + 1 and history[:-1] == before.history
                and history[-1].outcome == "pending"):
            transition = history[-1]
        elif (history and len(history) == len(before.history) and history[:-1] == before.history[:-1]
              and before.history[-1].outcome == "pending"
              and history[-1].cycle_id == before.history[-1].cycle_id
              and history[-1].mission_id == before.history[-1].mission_id
              and history[-1].outcome in ("claimed", "uncertain", "refused")):
            transition = history[-1]
        else:
            raise ClaimJournalError("claim_journal_invalid")
    count = before.event_count
    if transition is not None:
        count += 1
        connection.execute("INSERT INTO claim_events VALUES (?, ?, ?)",
                           (count, transition.mission_id, transition.outcome))
    if transition is not None or halt != before.halt_reason:
        connection.execute("UPDATE claim_meta SET event_count=?, halt_reason=? WHERE id=1", (count, halt))
    return _Snapshot(history, halt, count)


class DurableClaimTracker:
    """Default-disabled, durable offline decisions; never input authorization.

    All methods lock the instance and perform a fresh exclusive database transaction.
    Stale snapshots and IO failures latch a local stop; they never overwrite another
    owner's history. Persisted pending/uncertain/refused blocks a recovered instance.
    """

    def __init__(self, path, cycle_id, *, enabled=False, max_attempts=1):
        ClaimTracker(cycle_id, enabled=enabled, max_attempts=max_attempts)
        self._path = _private_path(path)
        self._cycle_id, self._enabled, self._max_attempts = cycle_id, enabled, max_attempts
        self._lock = RLock()
        self._snapshot = self._tracker = None
        self._stop_reason = None
        self._journal_failed = False
        self._closed = False
        try:
            with _transaction(self._path) as connection:
                snapshot = _read_snapshot(connection, cycle_id, max_attempts)
            self._snapshot = snapshot
            self._tracker = ClaimTracker(cycle_id, enabled=enabled, max_attempts=max_attempts,
                                         history=snapshot.history)
            # A recovered observer must not poison the active owner's transaction.
            self._stop_reason = snapshot.halt_reason
            if self._stop_reason is None and any(r.outcome != "claimed" for r in snapshot.history):
                self._stop_reason = "claim_previous_attempt_unresolved"
        except (ClaimJournalError, sqlite3.Error, OSError) as exc:
            self._journal_failed = True
            self._stop_reason = str(exc) if isinstance(exc, ClaimJournalError) else "claim_journal_io_failed"

    @property
    def history(self):
        """Last verified durable history; None means unavailable, never empty recovery."""
        with self._lock:
            return None if self._snapshot is None or self._journal_failed else self._snapshot.history

    @property
    def attempts(self):
        history = self.history
        return None if history is None else len(history)

    def close(self):
        with self._lock:
            self._closed = True  # pending stays pending; closing never resolves it

    def _call(self, operation, *args, **kwargs):
        with self._lock:
            if not self._enabled:
                return ClaimDecision("disabled", "claim_contract_disabled")
            if self._closed or self._stop_reason:
                return ClaimDecision("stopped", "claim_journal_closed" if self._closed else self._stop_reason)
            try:
                with _transaction(self._path) as connection:
                    before = _read_snapshot(connection, self._cycle_id, self._max_attempts)
                    _check(before == self._snapshot, "claim_journal_changed")
                    decision = getattr(self._tracker, operation)(*args, **kwargs)
                    # Repeated polling sees the existing pending owner; it is not
                    # a new ambiguous attempt and must not prevent that owner finishing.
                    halt = (decision.reason if decision.state == "stopped"
                            and decision.reason != "claim_attempt_pending" else before.halt_reason)
                    after = _checkpoint(connection, before, self._tracker.history, halt)
                    _check(_read_snapshot(connection, self._cycle_id, self._max_attempts) == after)
                # In-memory bookkeeping is published only after commit acknowledgment.
                self._snapshot = after
                if decision.state == "stopped" and decision.reason != "claim_attempt_pending":
                    self._stop_reason = decision.reason
                return decision
            except (ClaimJournalError, sqlite3.Error, OSError) as exc:
                self._journal_failed = True
                self._stop_reason = str(exc) if isinstance(exc, ClaimJournalError) else "claim_journal_io_failed"
                return ClaimDecision("stopped", self._stop_reason)

    def begin(self, observation, mission_id, *, frame, now):
        return self._call("begin", observation, mission_id, frame=frame, now=now)

    def accept_receipt(self, attempt, receipt, *, frame, now):
        return self._call("accept_receipt", attempt, receipt, frame=frame, now=now)

    def finish(self, attempt, observation, *, frame, now):
        return self._call("finish", attempt, observation, frame=frame, now=now)

    def refuse(self):
        return self._call("refuse")

    def interrupt(self):
        return self._call("interrupt")
