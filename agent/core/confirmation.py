from __future__ import annotations

from dataclasses import dataclass
import math
import re
import threading
import time
from typing import Any, Callable
import uuid


_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
_SCENE_PATTERN = re.compile(r"SC\.[A-Za-z0-9_.-]+")
_CANVAS = (1280, 720)


@dataclass(frozen=True)
class ConfirmationEvidence:
    """Immutable screenshot evidence for an offline confirmation rehearsal."""

    image_sha256: str
    configuration_sha256: str
    scene: str
    candidate_id: str
    box: tuple[int, int, int, int]
    score: float
    dimensions: tuple[int, int] = _CANVAS
    recognized: bool = True
    available: bool = True

    def validate(self) -> None:
        for name in ("image_sha256", "configuration_sha256"):
            value = getattr(self, name)
            if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
                raise ValueError(f"invalid_{name}")
        if type(self.scene) is not str or _SCENE_PATTERN.fullmatch(self.scene) is None:
            raise ValueError("invalid_scene")
        if type(self.candidate_id) is not str or not self.candidate_id.strip():
            raise ValueError("invalid_candidate_id")
        if (
            type(self.dimensions) is not tuple
            or len(self.dimensions) != 2
            or any(type(value) is not int for value in self.dimensions)
            or self.dimensions != _CANVAS
        ):
            raise ValueError("invalid_dimensions")
        if (
            type(self.box) is not tuple
            or len(self.box) != 4
            or any(type(value) is not int for value in self.box)
        ):
            raise ValueError("invalid_box")
        x, y, width, height = self.box
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError("invalid_box")
        if x + width > self.dimensions[0] or y + height > self.dimensions[1]:
            raise ValueError("invalid_box")
        if type(self.score) not in (int, float) or not 0.95 <= self.score <= 1:
            raise ValueError("invalid_score")
        if not math.isfinite(self.score):
            raise ValueError("invalid_score")
        if self.recognized is not True:
            raise ValueError("invalid_recognized")
        if self.available is not True:
            raise ValueError("invalid_available")

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_sha256": self.image_sha256,
            "configuration_sha256": self.configuration_sha256,
            "scene": self.scene,
            "candidate_id": self.candidate_id,
            "box": list(self.box),
            "score": self.score,
            "dimensions": list(self.dimensions),
            "recognized": self.recognized,
            "available": self.available,
        }


@dataclass
class _Proposal:
    evidence: ConfirmationEvidence
    issued_at: float
    expires_at: float
    status: str = "pending"


class ConfirmationLedger:
    """Process-local, one-use receipts; never an authorization for device input."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = 30,
        session_id: str | None = None,
    ) -> None:
        if (
            type(ttl_seconds) not in (int, float)
            or not 0 < ttl_seconds <= 120
            or not math.isfinite(ttl_seconds)
        ):
            raise ValueError("invalid_ttl_seconds")
        if not callable(clock):
            raise ValueError("invalid_clock")
        if session_id is not None and (type(session_id) is not str or not session_id.strip()):
            raise ValueError("invalid_session_id")
        self._clock = clock
        self._ttl_seconds = float(ttl_seconds)
        self._session_id = session_id if session_id is not None else str(uuid.uuid4())
        self._proposals: dict[str, _Proposal] = {}
        self._pending_id: str | None = None
        self._lock = threading.Lock()

    @property
    def session_id(self) -> str:
        return self._session_id

    @staticmethod
    def _validate_evidence(evidence: ConfirmationEvidence) -> None:
        if type(evidence) is not ConfirmationEvidence:
            raise ValueError("invalid_evidence")
        evidence.validate()

    def _now(self) -> float:
        try:
            value = self._clock()
            if type(value) not in (int, float):
                raise ValueError("invalid_clock")
            result = float(value)
        except Exception as exc:
            raise ValueError("invalid_clock") from exc
        if not math.isfinite(result):
            raise ValueError("invalid_clock")
        return result

    def _find_pending(self, proposal_id: str) -> _Proposal:
        if type(proposal_id) is not str or not proposal_id:
            raise ValueError("invalid_proposal_id")
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ValueError("unknown_proposal")
        if proposal.status != "pending":
            raise ValueError(f"proposal_{proposal.status}")
        return proposal

    def _finish(self, proposal_id: str, status: str) -> None:
        self._proposals[proposal_id].status = status
        if self._pending_id == proposal_id:
            self._pending_id = None

    def _result(self, proposal_id: str, evidence: ConfirmationEvidence) -> dict[str, Any]:
        return {
            "proposal_id": proposal_id,
            "session_id": self.session_id,
            "evidence": evidence.to_dict(),
            "input_authorized": False,
            "input_enabled": False,
            "rehearsal_only": True,
        }

    def issue(self, evidence: ConfirmationEvidence) -> dict[str, Any]:
        self._validate_evidence(evidence)
        with self._lock:
            now = self._now()
            if self._pending_id is not None:
                self._finish(self._pending_id, "superseded")
            proposal_id = str(uuid.uuid4())
            self._proposals[proposal_id] = _Proposal(evidence, now, now + self._ttl_seconds)
            self._pending_id = proposal_id
            result = self._result(proposal_id, evidence)
            result.update(ttl_seconds=self._ttl_seconds, remaining_seconds=self._ttl_seconds)
            return result

    def confirm(
        self,
        proposal_id: str,
        current_evidence: ConfirmationEvidence,
        *,
        approved: bool,
    ) -> dict[str, Any]:
        with self._lock:
            proposal = self._find_pending(proposal_id)
            try:
                now = self._now()
                if now < proposal.issued_at:
                    raise ValueError("invalid_clock")
                if now >= proposal.expires_at:
                    self._finish(proposal_id, "expired")
                    raise ValueError("proposal_expired")
                if type(approved) is not bool:
                    raise ValueError("invalid_approval")
                if not approved:
                    self._finish(proposal_id, "cancelled")
                    raise ValueError("approval_required")
                self._validate_evidence(current_evidence)
                if current_evidence != proposal.evidence:
                    raise ValueError("evidence_mismatch")
            except ValueError:
                if proposal.status == "pending":
                    self._finish(proposal_id, "rejected")
                raise
            self._finish(proposal_id, "consumed")
            result = self._result(proposal_id, proposal.evidence)
            result["confirmed"] = True
            return result

    def cancel(self, proposal_id: str) -> dict[str, Any]:
        with self._lock:
            proposal = self._find_pending(proposal_id)
            self._finish(proposal_id, "cancelled")
            result = self._result(proposal_id, proposal.evidence)
            result["cancelled"] = True
            return result

    def cancel_all(self) -> None:
        with self._lock:
            if self._pending_id is not None:
                self._finish(self._pending_id, "cancelled")
