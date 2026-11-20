#!/usr/bin/env python3
"""Import and audit private, manually labelled offline screenshot sessions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_ROOT = ROOT / "dev_assets" / "private"
CANVAS = [1280, 720]
REASONS = {"positive_guard", "unknown_state", "ambiguous_state", "invalid_dimensions"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def private_path(path):
    path = Path(path).resolve()
    require(path.is_relative_to(PRIVATE_ROOT.resolve()), "目的地必须位于仓库dev_assets/private/内")
    return path


def slug(value, scene=False):
    pattern = r"(?:SC\.)?[A-Za-z0-9][A-Za-z0-9_.-]*" if scene else r"[A-Za-z0-9][A-Za-z0-9_-]*"
    require(isinstance(value, str) and re.fullmatch(pattern, value) and len(value) <= 100, "会话ID或场景名称无效")
    require(".." not in value and not value.endswith("."), "会话ID或场景名称无效")
    if not scene:
        require(value.upper().split(".")[0] not in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}, "会话ID为系统保留名称")
    return value


def png_bytes(path):
    """Validate CRC, structure and bounded pixel stream without imaging dependencies."""
    require(path.is_file() and path.stat().st_size <= 12 * 1024 * 1024, "PNG不存在或超过12MiB")
    blob = path.read_bytes()
    require(blob[:8] == b"\x89PNG\r\n\x1a\n", "PNG文件头无效")
    position, compressed, channels, ended = 8, bytearray(), None, False
    idat_closed, palette_seen = False, False
    while position < len(blob):
        require(position + 12 <= len(blob), "PNG块被截断")
        length = struct.unpack(">I", blob[position:position + 4])[0]
        kind = blob[position + 4:position + 8]
        end = position + 12 + length
        require(end <= len(blob), "PNG块被截断")
        payload = blob[position + 8:end - 4]
        require(zlib.crc32(kind + payload) & 0xffffffff == struct.unpack(">I", blob[end - 4:end])[0], "PNG校验失败")
        require(re.fullmatch(b"[A-Za-z]{4}", kind) is not None, "PNG块类型无效")
        if channels is None:
            require(position == 8 and kind == b"IHDR" and length == 13, "PNG图像头无效")
            require(list(struct.unpack(">II", payload[:8])) == CANVAS, "PNG尺寸必须为1280x720")
            require(payload[8] == 8 and payload[9] in (2, 6) and payload[10:] == b"\0\0\0", "仅支持8位非交错RGB/RGBA PNG")
            channels = 3 if payload[9] == 2 else 4
        elif kind == b"IDAT":
            require(not idat_closed, "PNG图像块不连续")
            compressed.extend(payload)
        elif kind == b"IEND":
            require(length == 0 and compressed and end == len(blob), "PNG结束块无效")
            ended = True
        elif kind == b"PLTE":
            require(not palette_seen and not compressed and 0 < length <= 768 and length % 3 == 0, "PNG调色板无效")
            palette_seen = True
        else:
            require(kind != b"IHDR" and (kind[0] & 32 or kind == b"PLTE"), "PNG包含不支持的关键块")
            if compressed:
                idat_closed = True
        position = end
    require(channels is not None and ended, "PNG缺少图像头或结束块")
    stride = CANVAS[0] * channels + 1
    size = CANVAS[1] * stride
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(bytes(compressed), size + 1)
    except zlib.error as exc:
        raise ValueError("PNG压缩数据无效") from exc
    require(len(raw) == size and decoder.eof and not decoder.unused_data, "PNG解压长度无效")
    require(all(raw[offset] in range(5) for offset in range(0, size, stride)), "PNG过滤器无效")
    return blob


def validate_label(sample):
    label = sample.get("label")
    require(label in ("pending", "ready"), "标签须为pending或ready")
    if label == "pending":
        require(set(sample) == {"file", "sha256", "label"}, "pending样本不允许expected")
        return False
    require(set(sample) == {"file", "sha256", "label", "expected"}, "ready样本字段无效")
    expected = sample["expected"]
    require(isinstance(expected, dict) and set(expected) == {"scene", "allowed", "reason"}, "expected字段无效")
    scene = expected["scene"]
    require(scene is None or isinstance(scene, str) and scene.startswith("SC.") and slug(scene, True), "expected.scene须为SC.名称或null")
    require(type(expected["allowed"]) is bool and expected["reason"] in REASONS, "expected结果无效")
    require((expected["allowed"] and scene is not None and expected["reason"] == "positive_guard") or (not expected["allowed"] and scene is None and expected["reason"] != "positive_guard"), "expected语义冲突")
    require(expected["reason"] != "invalid_dimensions", "1280x720有效样本不能标为invalid_dimensions")
    return True


def validate_dataset(root):
    root = private_path(root)
    report = {"version": 1, "passed": False, "sessions": 0, "samples": 0, "pending": 0, "errors": [], "hashes": [], "split_samples": {"train": 0, "holdout": 0}, "evaluation_ready": False}
    seen, ids = {}, set()
    sessions = sorted(root.glob("*/session.json")) if root.is_dir() else []
    if root.is_dir():
        for directory in root.iterdir():
            if directory.is_dir() and not (directory / "session.json").is_file():
                report["errors"].append(f"{directory.name}：缺少session.json")
    if not sessions:
        report["errors"].append("数据集为空")
    for manifest in sessions:
        name = manifest.parent.name
        try:
            require(not manifest.is_symlink() and manifest.resolve().is_relative_to(root) and manifest.parent.resolve() == manifest.parent, "会话路径不允许链接逃逸")
            data = json.loads(manifest.read_text(encoding="utf-8-sig"))
            require(isinstance(data, dict) and set(data) == {"version", "id", "split", "scene", "source", "samples"}, "会话字段无效")
            require(type(data["version"]) is int and data["version"] == 1, "仅支持版本1")
            slug(data["id"])
            slug(data["scene"], True)
            require(data["id"] == name and name not in ids, "会话ID重复或与目录不符")
            ids.add(name)
            require(data["split"] in ("train", "holdout"), "split须为train或holdout")
            require(data["source"] == {"capture": "user_supplied_png", "dimensions": CANVAS}, "采集来源或标准尺寸无效")
            require(isinstance(data["samples"], list) and data["samples"], "会话样本为空")
            report["sessions"] += 1
            filenames = set()
            for sample in data["samples"]:
                try:
                    require(isinstance(sample, dict), "样本须为对象")
                    filename = sample.get("file")
                    require(isinstance(filename, str) and re.fullmatch(r"[A-Za-z0-9_-]+\.png", filename) and filename not in filenames, "样本文件名无效或重复")
                    filenames.add(filename)
                    path = manifest.parent / filename
                    require(path.resolve().parent == manifest.parent and not path.is_symlink(), "样本路径逃逸")
                    digest = hashlib.sha256(png_bytes(path)).hexdigest()
                    require(sample.get("sha256") == digest, "样本hash不符")
                    report["samples"] += 1
                    report["split_samples"][data["split"]] += 1
                    report["hashes"].append({"session": name, "split": data["split"], "sha256": digest})
                    if digest in seen:
                        previous, split = seen[digest]
                        raise ValueError(f"重复PNG：{previous}与{name}" + ("，train/holdout泄漏" if split != data["split"] else ""))
                    seen[digest] = (name, data["split"])
                    if not validate_label(sample):
                        report["pending"] += 1
                except (ValueError, OSError, TypeError) as exc:
                    report["errors"].append(f"{name}：{exc}")
            require({path.name for path in manifest.parent.iterdir() if path.suffix.lower() == ".png"} == filenames, "存在未登记或缺失的PNG")
        except (ValueError, OSError, TypeError, KeyError) as exc:
            report["errors"].append(f"{name}：{exc}")
    report["passed"] = not report["errors"] and report["pending"] == 0 and report["samples"] > 0
    report["evaluation_ready"] = report["passed"] and all(report["split_samples"].values())
    return report


def import_session(source: Path, destination: Path, session_id: str, split: str, scene: str):
    destination = private_path(destination)
    slug(session_id)
    slug(scene, True)
    require(split in ("train", "holdout"), "split须为train或holdout")
    target = destination / session_id
    require(not target.exists(), "会话目录已存在，拒绝覆盖或改变split")
    source = Path(source).resolve()
    require(source.is_dir(), "来源必须为用户指定的PNG目录")
    paths = sorted(path for path in source.iterdir() if path.suffix.lower() == ".png")
    require(paths, "来源目录没有顶层PNG")
    existing = validate_dataset(destination)
    require(not existing["errors"] or existing["errors"] == ["数据集为空"], "现有数据集验收失败，请先修复")
    hashes = {item["sha256"] for item in existing["hashes"]}
    blobs, samples = [], []
    for path in paths:
        require(not path.is_symlink() and path.resolve().parent == source, "来源PNG不允许链接逃逸")
        blob = png_bytes(path)
        digest = hashlib.sha256(blob).hexdigest()
        require(digest not in hashes, "重复PNG，拒绝会话重复或train/holdout泄漏")
        hashes.add(digest)
        samples.append({"file": f"sample-{len(samples) + 1:04d}.png", "sha256": digest, "label": "pending"})
        blobs.append(blob)
    metadata = {"version": 1, "id": session_id, "split": split, "scene": scene, "source": {"capture": "user_supplied_png", "dimensions": CANVAS}, "samples": samples}
    destination.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".import-", dir=destination))
    try:
        for sample, blob in zip(samples, blobs):
            (staging / sample["file"]).write_bytes(blob)
        (staging / "session.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        require(not target.exists(), "会话目录已存在，拒绝覆盖")
        os.rename(staging, target)
    finally:
        if staging.exists():
            resolved = private_path(staging)
            require(resolved.parent == destination and resolved.name.startswith(".import-"), "临时目录清理路径越界")
            shutil.rmtree(resolved)
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description="导入及验收私有离线PNG会话；标签由用户人工填写。")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="导入顶层PNG，所有标签初始为pending")
    importer.add_argument("--source", type=Path, required=True)
    importer.add_argument("--destination", type=Path, required=True)
    importer.add_argument("--session-id", required=True)
    importer.add_argument("--split", choices=("train", "holdout"), required=True)
    importer.add_argument("--scene", required=True)
    checker = commands.add_parser("check", help="验收标签、尺寸、hash及训练/留出隔离")
    checker.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            data = import_session(args.source, args.destination, args.session_id, args.split, args.scene)
            print(f"已导入{len(data['samples'])}张PNG；请人工填写session.json中的ready标签和expected。")
            return 0
        report = validate_dataset(args.root)
        root = private_path(args.root)
        root.mkdir(parents=True, exist_ok=True)
        report_path = root / "validation-report.json"
        require(not report_path.is_symlink(), "报告路径不允许链接")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"验收{'通过' if report['passed'] else '失败'}：{report['sessions']}个会话，{report['samples']}张样本，{report['pending']}张待标签，{len(report['errors'])}项错误。报告：{report_path}")
        return 0 if report["passed"] else 1
    except (ValueError, OSError) as exc:
        print(f"操作失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
