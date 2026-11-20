#!/usr/bin/env python3
"""Fetch PP-OCR v5/v6 candidate models for an offline comparison (pinned to one HEAD commit)."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_ROOT = ROOT / "dev_assets" / "private"
REPOSITORY = "MaaXYZ/MaaCommonAssets"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
MODEL_FILES = ("det.onnx", "rec.onnx", "keys.txt")
CANDIDATES = {
    "ppocr_v5_zh_cn": "OCR/ppocr_v5/zh_cn",
    "ppocr_v6_medium": "OCR/ppocr_v6/medium",
    "ppocr_v6_small": "OCR/ppocr_v6/small",
}
MAX_BYTES = 200 * 1024 * 1024
# Direct public HTTPS only; do not consult environment proxy configuration.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_url(url, limit):
    request = urllib.request.Request(url, headers={
        "User-Agent": "MaaShinyColors-ModelFetcher", "Accept": "application/vnd.github+json"})
    with OPENER.open(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("下载响应超过预期大小")
    return data


def blob_sha1(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def verify_bytes(data, size, sha):
    if len(data) != size:
        raise ValueError("模型文件大小不匹配")
    if blob_sha1(data) != sha:
        raise ValueError("模型 Git blob SHA-1 不匹配")


def validate_provenance(record):
    """Pure structural check of a provenance record; raises ValueError."""
    if not isinstance(record, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(record.get("commit", ""))):
        raise ValueError("来源记录缺少40位提交")
    if record.get("repository") != REPOSITORY:
        raise ValueError("来源仓库不匹配")
    files = record.get("files")
    if not isinstance(files, list) or [f.get("name") for f in files] != list(MODEL_FILES):
        raise ValueError("来源记录文件列表必须为 det.onnx、rec.onnx、keys.txt")
    for item in files:
        if not (type(item.get("bytes")) is int and item["bytes"] > 0
                and re.fullmatch(r"[0-9a-f]{40}", str(item.get("git_blob_sha1", "")))
                and re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
                and str(item.get("path", "")).startswith("OCR/")):
            raise ValueError("来源记录文件字段无效")
    return record


def resolve_listing(commit, source_dir):
    """Return {name: (size, sha)} for the model files under source_dir at the pinned commit."""
    listing = json.loads(read_url(f"{API_ROOT}/contents/{source_dir}?ref={commit}", 200000))
    found = {item["name"]: (item["size"], item["sha"]) for item in listing if item.get("type") == "file"}
    missing = [name for name in MODEL_FILES if name not in found]
    if missing:
        raise ValueError(f"{source_dir} 缺少 {missing}；需要记录文件名映射")
    return {name: found[name] for name in MODEL_FILES}


def install_candidate(commit, name, source_dir):
    destination = (PRIVATE_ROOT / "models" / name).resolve()
    if not destination.is_relative_to(PRIVATE_ROOT.resolve()):
        raise ValueError("模型目的地必须位于 dev_assets/private/ 内")
    provenance_path = destination.with_name(destination.name + ".provenance.json")
    if destination.exists() or provenance_path.exists():
        raise FileExistsError(f"{name}: 模型目录或来源记录已存在；拒绝覆盖")
    destination.parent.mkdir(parents=True, exist_ok=True)
    listing = resolve_listing(commit, source_dir)
    stage = Path(tempfile.mkdtemp(prefix=".download-", dir=destination.parent)).resolve()
    provenance_created = committed = False
    try:
        provenance = {"repository": REPOSITORY, "commit": commit, "files": []}
        for file_name in MODEL_FILES:
            size, sha = listing[file_name]
            if size > MAX_BYTES:
                raise ValueError("模型文件超过大小上限")
            path = f"{source_dir}/{file_name}"
            url = f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}/{path}"
            data = read_url(url, size)
            verify_bytes(data, size, sha)
            (stage / file_name).write_bytes(data)
            provenance["files"].append({"name": file_name, "path": path, "url": url, "bytes": size,
                                        "git_blob_sha1": sha, "sha256": hashlib.sha256(data).hexdigest()})
            print(f"{name}/{file_name}: Git blob 校验通过，{size} 字节。", flush=True)
        validate_provenance(provenance)
        with provenance_path.open("x", encoding="utf-8") as handle:
            provenance_created = True
            json.dump(provenance, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        stage.rename(destination)
        committed = True
        return destination
    finally:
        if not committed and provenance_created:
            provenance_path.unlink()
        if stage.exists():
            for file_name in MODEL_FILES:
                (stage / file_name).unlink(missing_ok=True)
            stage.rmdir()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        commit = json.loads(read_url(f"{API_ROOT}/commits/HEAD", 2000000))["sha"]
        print(f"固定提交：{commit}", flush=True)
        status = 0
        for name, source_dir in CANDIDATES.items():
            try:
                install_candidate(commit, name, source_dir)
            except (ValueError, KeyError, OSError) as error:
                print(f"{name}: 下载失败：{error}")
                status = 1
        return status
    except (ValueError, KeyError, OSError) as error:
        print(f"候选 OCR 模型下载失败：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
