#!/usr/bin/env python3
"""Fetch the fixed public Japanese OCR candidate with verified Git blob identity."""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_ROOT = ROOT / "dev_assets" / "private"
DESTINATION = PRIVATE_ROOT / "models" / "ja_ppocrv3_v4"
REPOSITORY = "MaaXYZ/MaaCommonAssets"
COMMIT = "dabcd4681ac990dc4361de26416d986abd80e4aa"
SOURCES = (
    ("det.onnx", "OCR/ppocr_v4/zh_cn/det.onnx", 4731945,
     "5d1f1cdefb6fa5c8c2893e4640b60bde1f3c2f4a"),
    ("rec.onnx", "OCR/ppocr_v3/ja_jp/rec.onnx", 10075749,
     "1eaf5ee42083776c83d94118d5637f63ad608d3d"),
    ("keys.txt", "OCR/ppocr_v3/ja_jp/keys.txt", 17332,
     "339d4b89e5159a346636641a0814874faa59754a"),
)
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
# Direct public HTTPS only; do not consult environment proxy configuration.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_url(url, limit):
    request = urllib.request.Request(url, headers={
        "User-Agent": "MaaShinyColors-ModelFetcher",
        "Accept": "application/vnd.github+json",
    })
    with OPENER.open(request, timeout=20) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("下载响应超过预期大小")
    return data


def verify_bytes(data, size, sha):
    if len(data) != size:
        raise ValueError("模型文件大小不匹配")
    blob = b"blob " + str(size).encode("ascii") + b"\0" + data
    if hashlib.sha1(blob).hexdigest() != sha:
        raise ValueError("模型 Git blob SHA-1 不匹配")


def fetch_file(source):
    name, path, size, sha = source
    raw_url = f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/{path}"
    used_url = raw_url
    try:
        data = read_url(raw_url, size)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
        print(f"{name}: raw 下载失败（{type(error).__name__}），核验固定提交 API 来源。", flush=True)
        contents_url = f"{API_ROOT}/contents/{path}?ref={COMMIT}"
        metadata = json.loads(read_url(contents_url, 100000))
        blob_url = f"{API_ROOT}/git/blobs/{sha}"
        if (metadata.get("path") != path or metadata.get("name") != name
                or metadata.get("size") != size or metadata.get("sha") != sha
                or metadata.get("git_url") != blob_url):
            raise ValueError("固定提交中的模型路径、大小或 blob 身份不匹配")
        blob = json.loads(read_url(blob_url, size * 2 + 100000))
        if (blob.get("sha") != sha or blob.get("size") != size
                or blob.get("encoding") != "base64"):
            raise ValueError("GitHub API blob 身份或编码不匹配")
        data = base64.b64decode("".join(blob["content"].split()), validate=True)
        used_url = blob_url
    verify_bytes(data, size, sha)
    return data, {
        "name": name, "path": path, "url": used_url, "raw_url": raw_url,
        "bytes": size, "git_blob_sha1": sha,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def install_model(destination=DESTINATION):
    destination = Path(destination).resolve()
    private_root = PRIVATE_ROOT.resolve()
    if not destination.is_relative_to(private_root):
        raise ValueError("模型目的地必须位于仓库 dev_assets/private/ 内")
    provenance_path = destination.with_name(destination.name + ".provenance.json")
    if destination.exists() or provenance_path.exists():
        raise FileExistsError("模型目录或来源记录已存在；保留现有文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".download-", dir=destination.parent)).resolve()
    if not stage.is_relative_to(private_root):
        raise ValueError("模型暂存目录越过私有目录")
    provenance_created = False
    committed = False
    try:
        provenance = {"repository": REPOSITORY, "commit": COMMIT, "files": []}
        for source in SOURCES:
            data, record = fetch_file(source)
            (stage / source[0]).write_bytes(data)
            provenance["files"].append(record)
            print(f"{source[0]}: Git blob 校验通过，{len(data)} 字节。", flush=True)
        # Publish the complete provenance before making the model directory visible.
        with provenance_path.open("x", encoding="utf-8") as handle:
            provenance_created = True
            json.dump(provenance, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        if destination.exists():
            raise FileExistsError("模型目的地已存在；保留现有文件")
        stage.rename(destination)
        committed = True
        return destination
    finally:
        if not committed and provenance_created:
            provenance_path.unlink()
        if stage.exists():
            # Both resolved paths were checked above. Delete only known filenames.
            for source in SOURCES:
                (stage / source[0]).unlink(missing_ok=True)
            stage.rmdir()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        destination = install_model()
    except (ValueError, KeyError, OSError) as error:
        print(f"OCR 模型下载失败：{error}")
        return 1
    print(f"OCR 模型已就绪：{destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
