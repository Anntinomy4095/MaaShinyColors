"""Private observed-run records for later calibration; no learned probabilities."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping

from .models import ATTRIBUTES, STATS, Ownership, integer, number


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    observed_at: str
    p_card: Ownership
    active: tuple[Ownership, ...]
    guest: Ownership
    strategy: str
    difficulty: str
    season_stats: tuple[Mapping[str, float], ...]
    final_stats: Mapping[str, float]
    skill_plan: tuple[str, ...]
    auditions: tuple[Mapping[str, Any], ...]
    data_snapshot_sha256: str
    success: bool

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunRecord:
        p = Ownership.from_dict(value["p_card"])
        active = tuple(Ownership.from_dict(v) for v in value["active"])
        guest = Ownership.from_dict(value["guest"])
        if len(active) != 4 or len({c.card_id for c in (p, *active, guest)}) != 6:
            raise ValueError("RunRecord needs six distinct card IDs with actual Lv/LB")
        seasons = tuple(value["season_stats"])
        if len(seasons) != 4:
            raise ValueError("RunRecord needs S1-S4 observations")
        stats = []
        for row in (*seasons, value["final_stats"]):
            if set(row) != set((*STATS, "SP", "fans")):
                raise ValueError("RunRecord stats require Vo/Da/Vi/Me/SP/fans")
            stats.append({k: number(v, k) for k, v in row.items()})
        if not isinstance(value["success"], bool):
            raise ValueError("RunRecord success must be observed boolean")
        auditions = tuple(dict(v) for v in value["auditions"])
        for row in auditions:
            if set(row.get("trend", ())) != set(ATTRIBUTES) or len(row["trend"]) != 3:
                raise ValueError("observed audition trend is required")
            integer(row.get("turns"), "observed turns", minimum=1)
            if not isinstance(row.get("success"), bool) or not row.get("audition_id"):
                raise ValueError("observed audition ID/result is required")
        stamp = str(value["observed_at"])
        if datetime.fromisoformat(stamp.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("observation time needs timezone")
        digest = value["data_snapshot_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("data snapshot SHA256 is required")
        if not value.get("run_id") or not value.get("strategy") or value.get("difficulty") not in ("Middle", "Master"):
            raise ValueError("RunRecord identity/strategy/difficulty is required")
        return cls(str(value["run_id"]), stamp, p, active, guest, str(value["strategy"]), value["difficulty"],
                   tuple(stats[:4]), stats[4], tuple(value["skill_plan"]), auditions, digest, value["success"])


def append_run_record(record: RunRecord, path: Path) -> None:
    private = Path(__file__).resolve().parents[3] / "dev_assets/private"
    if not path.resolve().is_relative_to(private.resolve()):
        raise ValueError("account observations must stay under dev_assets/private")
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if json.loads(line).get("run_id") == record.run_id:
                raise ValueError("duplicate run_id; existing observations are append-only")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(asdict(record), ensure_ascii=False, allow_nan=False) + "\n")
