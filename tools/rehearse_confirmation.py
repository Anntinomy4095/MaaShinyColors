#!/usr/bin/env python3
"""Exercise one-time confirmation on owned PNGs; never authorize device input."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.confirmation import ConfirmationEvidence, ConfirmationLedger
from tools.manage_capture_session import private_path
from tools.replay_screenshots import (
    CANVAS, MaaRecognizer, decide, png_dimensions, read_png, require,
    template_path, validate_manifest,
)

FIXTURES = ROOT / "tests/fixtures/replay_template"
MANIFEST = FIXTURES / "manifest.json"


class GuardRejected(ValueError):
    def __init__(self, reason, observation):
        super().__init__(reason)
        self.observation = observation


def load_configuration(path):
    path = Path(path).resolve()
    blob = path.read_bytes()
    data = validate_manifest(json.loads(blob.decode("utf-8")), path.parent)
    require(all(scene["recognition"] == "TemplateMatch" for scene in data["scenes"]),
            "确认演练仅支持模板匹配框")
    identity = {
        "manifest_sha256": hashlib.sha256(blob).hexdigest(),
        "templates": {scene["template"]: hashlib.sha256(template_path(scene, path.parent).read_bytes()).hexdigest()
                      for scene in data["scenes"]},
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return data, digest, identity


def observe_frame(path, configuration, recognizer):
    """Positive scene and unique target must both come from this PNG recognition."""
    path, configuration = Path(path), Path(configuration)
    data, config_hash, identity = load_configuration(configuration)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    dimensions = png_dimensions(path)
    observation = {"image_sha256": digest, "configuration_sha256": config_hash,
                   "configuration": identity, "dimensions": list(dimensions), "recognitions": []}
    if dimensions != CANVAS:
        raise GuardRejected("invalid_dimensions", observation)
    image = read_png(path)
    recognitions = [recognizer.recognize(scene, image, configuration.parent) for scene in data["scenes"]]
    require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, "识别期间图片发生变化")
    require(load_configuration(configuration)[1] == config_hash, "识别期间配置或模板发生变化")
    observation["recognitions"] = recognitions
    guard = decide(recognitions, dimensions)
    observation["guard"] = guard
    if not guard["allowed"]:
        raise GuardRejected(guard["reason"], observation)
    hit = next(item for item in recognitions if item["scene"] == guard["scene"])
    results = hit.get("results")
    if not isinstance(results, list) or len(results) != 1:
        raise GuardRejected("ambiguous_target", observation)
    result = results[0]
    evidence = ConfirmationEvidence(
        image_sha256=digest, configuration_sha256=config_hash, scene=guard["scene"],
        candidate_id="candidate:" + guard["scene"], box=tuple(result["box"]), score=result["score"],
    )
    evidence.validate()
    x, y, width, height = evidence.box
    rx, ry, rw, rh = hit["roi"]
    require(rx <= x and ry <= y and x + width <= rx + rw and y + height <= ry + rh,
            "识别框超出配置ROI")
    return evidence, observation


def confirm_frame(ledger, proposal_id, path, configuration, recognizer, *, approved):
    try:
        evidence, observation = observe_frame(path, configuration, recognizer)
    except Exception:
        # An unreadable/unknown current screen invalidates the pending proposal.
        ledger.cancel_all()
        raise
    receipt = ledger.confirm(proposal_id, evidence, approved=approved)
    return receipt, observation


class SimulatedClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def rehearse(recognizer_factory=None):
    # Fixed owned fixtures; no arbitrary game page, Pipeline, window or session input.
    _, digest, identity = load_configuration(MANIFEST)
    recognizer = (recognizer_factory or MaaRecognizer)()
    initial, initial_observation = observe_frame(FIXTURES / "positive_a.png", MANIFEST, recognizer)
    current = initial
    changed, changed_observation = observe_frame(FIXTURES / "positive_b.png", MANIFEST, recognizer)
    cases = []

    def case(name, operation, expected_error=None):
        item = {"id": name, "expected": "rejected" if expected_error else "confirmed"}
        try:
            receipt = operation()
            item.update(receipt=receipt, passed=expected_error is None and receipt.get("confirmed") is True
                        and receipt.get("input_authorized") is False and receipt.get("input_enabled") is False)
        except ValueError as exc:
            item.update(error=str(exc), passed=expected_error is not None and expected_error in str(exc))
            if isinstance(exc, GuardRejected):
                item["observation"] = exc.observation
        cases.append(item)

    def pending():
        clock = SimulatedClock()
        ledger = ConfirmationLedger(clock=clock, ttl_seconds=30)
        return ledger, ledger.issue(initial), clock

    ledger, proposal, _ = pending()
    confirmation_observation = {}
    def confirm_positive():
        receipt, observation = confirm_frame(ledger, proposal["proposal_id"], FIXTURES / "positive_a.png",
                                             MANIFEST, recognizer, approved=True)
        confirmation_observation.update(observation)
        return receipt
    case("same_frame_confirm", confirm_positive)
    cases[-1].update(proposal=proposal, initial_observation=initial_observation,
                    current_observation=confirmation_observation)
    case("duplicate_confirmation", lambda: ledger.confirm(proposal["proposal_id"], current, approved=True), "proposal_consumed")

    for name, evidence in (
        ("changed_frame", changed),
        ("changed_scene", replace(current, scene="SC.Replay.Other")),
        ("changed_candidate", replace(current, candidate_id="other_candidate")),
        ("changed_box", replace(current, box=(261, 240, 80, 64))),
        ("changed_configuration", replace(current, configuration_sha256="0" * 64)),
    ):
        ledger, proposal, _ = pending()
        case(name, lambda: ledger.confirm(proposal["proposal_id"], evidence, approved=True), "evidence_mismatch")
        case(name + "_retry", lambda: ledger.confirm(proposal["proposal_id"], current, approved=True), "proposal_")

    ledger, proposal, clock = pending()
    clock.now = 30.0
    case("expired", lambda: ledger.confirm(proposal["proposal_id"], current, approved=True), "proposal_expired")
    for name, action, error in (
        ("cancelled", lambda l, p: l.cancel(p), "proposal_cancelled"),
        ("stopped", lambda l, p: l.cancel_all(), "proposal_cancelled"),
        ("superseded", lambda l, p: l.issue(current), "proposal_superseded"),
    ):
        ledger, proposal, _ = pending()
        action(ledger, proposal["proposal_id"])
        case(name, lambda: ledger.confirm(proposal["proposal_id"], current, approved=True), error)
    ledger, proposal, _ = pending()
    case("declined", lambda: ledger.confirm(proposal["proposal_id"], current, approved=False), "approval_required")
    case("declined_retry", lambda: ledger.confirm(proposal["proposal_id"], current, approved=True), "proposal_")
    foreign = ConfirmationLedger()
    case("foreign_session", lambda: foreign.confirm(proposal["proposal_id"], current, approved=True), "unknown_proposal")

    for name, path, error in (
        ("unknown", FIXTURES / "unknown.png", "unknown_state"),
        ("similar", FIXTURES / "similar_negative.png", "unknown_state"),
        ("ambiguous_scene", FIXTURES / "ambiguous.png", "ambiguous_state"),
        ("wrong_dimensions", FIXTURES / "invalid_size.png", "invalid_dimensions"),
        ("multiple_targets", ROOT / "tests/fixtures/confirmation/multiple_targets.png", "ambiguous_target"),
    ):
        ledger, proposal, _ = pending()
        case(name, lambda: confirm_frame(ledger, proposal["proposal_id"], path, MANIFEST, recognizer, approved=True)[0], error)
        case(name + "_retry", lambda: ledger.confirm(proposal["proposal_id"], current, approved=True), "proposal_cancelled")

    attempts = getattr(recognizer, "input_attempts", None)
    return {"version": 1, "rehearsal_only": True, "offline_only": True,
            "input_authorized": False, "input_enabled": False, "input_attempts": attempts,
            "framework_version": recognizer.version, "clock_kind": "simulated_monotonic",
            "approval_source": "scripted_test_not_user_consent", "configuration_sha256": digest,
            "configuration": identity, "changed_frame_observation": changed_observation,
            "cases": cases, "metrics": {"cases": len(cases), "faileds": sum(not c["passed"] for c in cases)},
            "passed": all(c["passed"] for c in cases) and attempts == 0}


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="自绘PNG的一次确认离线演练；不会授权或发送设备输入。")
    parser.add_argument("--report", type=Path, default=ROOT / "dev_assets/private/confirmation_checks" / uuid4().hex / "report.json")
    args = parser.parse_args(argv)
    try:
        output = private_path(args.report)
        require(output.suffix.lower() == ".json" and output.name != "session.json", "报告须为私有JSON且不能覆盖会话文件")
    except ValueError as exc:
        print(str(exc))
        return 2
    try:
        report = rehearse()
        code = 0 if report["passed"] else 1
    except Exception as exc:
        report = {"passed": False, "rehearsal_only": True, "input_authorized": False,
                  "input_enabled": False, "error": str(exc)}
        code = 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(output), "error": report.get("error")}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
