"""Trusted Match progress and independent current-cycle proof; pure rules, never input authority.

No live cycle reader has been verified yet. Latest-open-stage selection still needs this contract.
An explicit selection instead uses the current game's Skip eligibility list (match_observed), without
claiming a reset calendar or importing persisted user preferences as progress proof.
"""
from __future__ import annotations

import dataclasses
import math
import re

from agent.core import task_common as tc

STAGE_ID = re.compile(r"A([1-9][0-9]?)-S([1-9][0-9]{0,2})")
PROOF_MAX_AGE_S = 5.0


@dataclasses.dataclass(frozen=True)
class StageProgress:
    stage_id: str
    stage_order: int
    skip_available: bool

    def __post_init__(self):
        tc.require(isinstance(self.stage_id, str) and STAGE_ID.fullmatch(self.stage_id), "Match 进度关卡标识无效")
        tc.require(type(self.stage_order) is int and self.stage_order > 0, "Match 进度顺序必须为正整数")
        tc.require(type(self.skip_available) is bool, "Match 进度可跳过状态必须为布尔值")

    @property
    def stage(self):
        return tuple(int(value) for value in STAGE_ID.fullmatch(self.stage_id).groups())


@dataclasses.dataclass(frozen=True)
class MatchProgress:
    cycle_id: str
    state: str
    stage_list_complete: bool
    stages: tuple[StageProgress, ...]

    def __post_init__(self):
        tc.require(isinstance(self.cycle_id, str) and bool(self.cycle_id.strip()), "Match 进度需要明确周期标识")
        tc.require(self.state in ("ready", "reset", "unknown"), "Match 进度状态无效")
        tc.require(type(self.stage_list_complete) is bool, "Match 列表完整性必须为布尔值")
        tc.require(type(self.stages) is tuple and all(isinstance(stage, StageProgress) for stage in self.stages),
                   "Match 进度关卡必须为不可变的已验证记录")
        tc.require(len({stage.stage for stage in self.stages}) == len(self.stages), "Match 进度关卡重复")
        tc.require(len({stage.stage_order for stage in self.stages}) == len(self.stages), "Match 进度顺序重复")

    @classmethod
    def from_dict(cls, data):
        tc.require(isinstance(data, dict) and set(data) == {"cycle_id", "state", "stage_list_complete", "stages"},
                   "Match 进度字段无效；偏好配置不能代替进度观测")
        tc.require(type(data["stages"]) is list, "Match 进度关卡必须为列表")
        stages = []
        for row in data["stages"]:
            tc.require(isinstance(row, dict) and set(row) == {"stage_id", "stage_order", "skip_available"},
                       "Match 进度关卡字段无效")
            stages.append(StageProgress(**row))
        return cls(data["cycle_id"], data["state"], data["stage_list_complete"], tuple(stages))

    def to_dict(self):
        return {"cycle_id": self.cycle_id, "state": self.state, "stage_list_complete": self.stage_list_complete,
                "stages": [dataclasses.asdict(stage) for stage in self.stages]}

    def failure(self):
        if self.state == "reset":
            return "progress_reset"
        if self.state != "ready" or not self.stage_list_complete or not self.stages:
            return "stage_evidence_incomplete"
        return None


@dataclasses.dataclass(frozen=True)
class ProgressObservation:
    """A reader's positive proof on the exact decoded image, timed with the runtime's monotonic clock."""
    cycle_id: str
    state: str
    frame_digest: str
    observed_at: float


def progress_failure(profile, observation, frame_digest, now):
    """Reject missing, reset, changed, stale or differently framed cycle evidence before any preparation."""
    if profile is None:
        return "progress_evidence_missing"
    tc.require(isinstance(profile, MatchProgress), "Match 进度合同类型无效")
    if failure := profile.failure():
        return failure
    if not isinstance(observation, ProgressObservation):
        return "progress_unreadable"
    if observation.state == "reset":
        return "progress_reset"
    if observation.state != "ready" or not isinstance(observation.cycle_id, str) or not observation.cycle_id.strip():
        return "progress_unreadable"
    if observation.cycle_id != profile.cycle_id:
        return "progress_cycle_changed"
    if not (isinstance(frame_digest, str) and re.fullmatch(r"[0-9a-f]{64}", frame_digest)
            and observation.frame_digest == frame_digest):
        return "progress_frame_mismatch"
    if (type(observation.observed_at) not in (int, float) or not math.isfinite(observation.observed_at)
            or not 0 <= now - observation.observed_at <= PROOF_MAX_AGE_S):
        return "progress_stale"
    return None
