#!/usr/bin/env python3
"""Read-only resolution of one historical, non-consuming fixed S-card selection."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.daily_config import load_json
from agent.core.foreground_guard import interpret_foreground
from agent.core.training_flow import load_guard, stage_matches
from tools.replay_foreground import load_configuration
from tools.replay_screenshots import MaaRecognizer, read_png, template_path
from tools.selection_loop_protocol import (ACTION, PRIVATE, RESOLUTION, digest, legacy_private_path, nonce_valid,
                                           private_path, require)

LEGACY_LEDGER = PRIVATE / "daily_navigation/training-select-asahi.json"
PENDING = PRIVATE / "daily_navigation/training-confirm-one-bronze.runner-pending.json"
CONFIRM_LEDGER = PENDING.with_name("training-confirm-one-bronze.json")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def producer_files():
    return [Path(__file__).resolve(), ROOT / "tools/selection_loop_protocol.py", ROOT / "agent/core/training_flow.py",
            ROOT / "tools/replay_screenshots.py", ROOT / "tools/replay_foreground.py",
            ROOT / "agent/core/foreground_guard.py", ROOT / "tests/fixtures/training_flow/pipeline.json"]


def process_exited(pid):
    require(type(pid) is int and pid > 0 and sys.platform == "win32", "旧宿主PID或平台无效")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        require(ctypes.get_last_error() == 87, "无法证明旧宿主已退出")
        return True
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle(handle)
    return False


def consumption_unused():
    require(not PENDING.exists(), "Confirm runner pending存在，不能解决导航循环")
    if CONFIRM_LEDGER.exists():
        data = load_json(CONFIRM_LEDGER)
        require(set(data) == {"Version", "Navigation", "MaxAttempts", "Attempts", "LastPid", "LastFrameSha256", "LastNonce"}
                and type(data["Version"]) is int and data["Version"] == 1
                and data["Navigation"] == "training-confirm-one-bronze"
                and type(data["MaxAttempts"]) is int and data["MaxAttempts"] == 1
                and type(data["Attempts"]) is int and data["Attempts"] == 0
                and data["LastPid"] is None and data["LastFrameSha256"] is None and data["LastNonce"] is None,
                "Confirm原生尝试已用或不确定，不能解决导航循环")


def validate_legacy(report, ledger):
    fields = {"Version", "Navigation", "MaxAttempts", "Attempts", "LastPid", "LastFrameSha256", "LastNonce"}
    require(type(ledger) is dict and set(ledger) == fields and type(ledger["Version"]) is int and ledger["Version"] == 1
            and ledger["Navigation"] == ACTION and type(ledger["MaxAttempts"]) is int and ledger["MaxAttempts"] == 1
            and type(ledger["Attempts"]) is int and ledger["Attempts"] == 1
            and type(ledger["LastPid"]) is int and ledger["LastPid"] > 0
            and digest(ledger["LastFrameSha256"]) and nonce_valid(ledger["LastNonce"]), "旧选卡单次账本无效")
    require(report.get("action") == ACTION and report.get("passed") is False and report.get("input_enabled") is True
            and report.get("offline_only") is False and report.get("resource_consumption") is False
            and type(report.get("input_attempts")) is int and report["input_attempts"] == 1
            and report.get("response", {}).get("ok") is True and report["response"].get("attempted") is True,
            "旧报告必须保留原失败事实和真实非消费输入尝试")
    names = ["SC.DailyNavigation." + ACTION + "." + name for name in ("Observe", "Click")]
    trace = report.get("node_trace")
    require(type(trace) is list and [n.get("name") for n in trace] == names
            and all(n.get("completed") is True and n.get("hit") is True and n.get("action_success") is True for n in trace),
            "旧报告缺少实际Maa输入节点链")
    frames = report.get("frames")
    checks = report.get("postcondition_checks")
    require(type(frames) is list and len(frames) >= 4 and type(checks) is list and len(checks) == len(frames) - 3
            and frames[2].get("sha256") == ledger["LastFrameSha256"], "旧账本与报告fresh前帧或后置链不匹配")
    for item in frames:
        require(type(item) is dict and set(item) == {"image", "sha256"} and digest(item["sha256"])
                and sha(legacy_private_path(item["image"])) == item["sha256"], "旧原始截图哈希不匹配")
    # Bind the proof to current locations; SHA above ties each rebased file to the original record.
    return ({**frames[2], "image": str(legacy_private_path(frames[2]["image"]))},
            {**frames[-1], "image": str(legacy_private_path(frames[-1]["image"]))})


def validate_offline(proof, data, truth, digest_value):
    require(proof.get("passed") is True and proof.get("offline_only") is True and proof.get("real_input_enabled") is False
            and type(proof.get("real_input_attempts")) is int and proof["real_input_attempts"] == 0
            and proof.get("framework_version") == "v5.12.3" and proof.get("configuration_sha256") == digest_value,
            "原生profile完整离线证明不匹配")
    samples = proof.get("samples")
    require(type(samples) is list and type(proof.get("sample_count")) is int
            and proof["sample_count"] == len(data["samples"]) == len(samples)
            and {s["id"] for s in samples} == {s["id"] for s in data["samples"]}, "原生profile离线样本不完整")
    by_id = {s["id"]: s for s in data["samples"]}
    for item in samples:
        expected = truth[by_id[item["id"]]["image"]]
        require(item.get("passed") is True and item.get("stage") == expected["stage"]
                and item.get("expected_guard") is expected["expected_guard"]
                and item.get("guard_proof") is expected["expected_guard"]
                and stage_matches(item["stage"], item["recognitions"]) is expected["expected_guard"]
                and type(item.get("real_input_attempts")) is int and item["real_input_attempts"] == 0
                and item.get("other_input_calls") == 0, "离线样本真值或完整守卫不匹配")


def resolve(args):
    consumption_unused()
    report_path = private_path(str(args.legacy_report))
    ledger_blob, report_blob = LEGACY_LEDGER.read_bytes(), report_path.read_bytes()
    ledger, report = load_json(LEGACY_LEDGER), load_json(report_path)
    pre, post = validate_legacy(report, ledger)
    require(process_exited(ledger["LastPid"]), "旧宿主仍在运行，禁止解决遗留输入")
    data, truth, guard_digest = load_guard(args.manifest, args.sources)
    offline_path = private_path(str(args.offline_proof))
    validate_offline(load_json(offline_path), data, truth, guard_digest)
    fore_data, fore_rules, fore_digest, _ = load_configuration(args.foreground_manifest, args.foreground_contracts)
    reco = MaaRecognizer()
    image = read_png(private_path(post["image"]))
    hits = [reco.recognize(s, image, args.manifest.parent) for s in data["scenes"] if s["name"].startswith("SC.Training.Zero.")]
    state = interpret_foreground([reco.recognize(s, image, args.foreground_manifest.parent) for s in fore_data["scenes"]],
                                 (1280, 720), fore_rules)
    require(stage_matches("Zero", hits) and state["state"] == "no_known_popup" and reco.input_attempts == 0,
            "旧选卡原始后帧未通过完整Zero和前景守卫")
    files = [LEGACY_LEDGER, report_path, offline_path, args.manifest, args.sources, args.foreground_manifest,
             args.foreground_contracts, *producer_files()]
    files += [legacy_private_path(f["image"]) for f in report["frames"]]
    files += [template_path(s, args.manifest.parent) for s in data["scenes"]]
    files += [args.manifest.parent / s["image"] for s in data["samples"]]
    files += [template_path(s, args.foreground_manifest.parent) for s in fore_data["scenes"] if s["recognition"] == "TemplateMatch"]
    bindings = [{"path": str(p), "sha256": sha(p)} for p in sorted({Path(p).resolve() for p in files})]
    require(LEGACY_LEDGER.read_bytes() == ledger_blob and report_path.read_bytes() == report_blob, "解决过程中旧证据变化")
    require(load_guard(args.manifest, args.sources)[2] == guard_digest
            and load_configuration(args.foreground_manifest, args.foreground_contracts)[2] == fore_digest, "解决过程中模板配置变化")
    consumption_unused()
    return {"version": 1, "kind": "SC.TrainingSelection.LegacyResolution", "action": ACTION,
            "passed": True, "real_input_attempts": 0, "resource_consumption": False,
            "legacy_ledger_path": str(LEGACY_LEDGER.resolve()), "legacy_ledger_sha256": hashlib.sha256(ledger_blob).hexdigest(),
            "legacy_report_path": str(report_path), "legacy_report_sha256": hashlib.sha256(report_blob).hexdigest(),
            "legacy_pid": ledger["LastPid"], "legacy_nonce": ledger["LastNonce"], "original_report_passed": False,
            "pre_frame": pre, "post_frame": post, "configuration_sha256": guard_digest, "foreground_sha256": fore_digest,
            "post_recognitions": hits, "foreground": state, "files": bindings,
            "limitations": "旧报告路径来自旧工作区，按固定旧根重定位并逐帧SHA核验；旧报告没有PID/nonce字段；用旧账本与报告的fresh前帧SHA关联。保留失败原件；原始后帧重新校准不是独立holdout。"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读解决旧固定选卡结果；不授权消费或重置预算")
    for name in ("legacy-report", "manifest", "sources", "offline-proof", "foreground-manifest", "foreground-contracts"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    require(not RESOLUTION.exists(), "固定解决证明已经存在，禁止覆盖或改名绕过")
    result = resolve(args)
    RESOLUTION.parent.mkdir(parents=True, exist_ok=True)
    with RESOLUTION.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"passed": True, "real_input_attempts": 0, "resolution": str(RESOLUTION), "sha256": sha(RESOLUTION)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
