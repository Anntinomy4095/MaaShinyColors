"""Import the user's existing recognition templates into a portable installation.

No screenshots, profiles, accounts, game dumps or executable resources are copied.
The launcher offers a folder picker; CLI accepts a project root or resource_native.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.build_native_resource import CANVAS, pipeline_templates, png_size, roi_ok


def import_templates(source: Path, root: Path = ROOT) -> int:
    source = source.resolve()
    if not (source / "manifest.json").is_file():
        source = source / "dev_assets" / "private" / "resource_native"
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("canvas") != list(CANVAS) or manifest.get("template_prefix") != "native/":
        raise ValueError("识别模板格式不匹配")
    required = pipeline_templates(root / "assets" / "resource" / "base" / "pipeline")
    entries = {}
    for record in manifest.get("templates", []):
        name = record.get("name", "")
        path = PurePosixPath(name)
        if ("\\" in name or path.is_absolute() or ".." in path.parts or len(path.parts) < 3
                or path.parts[0] != "native" or path.suffix != ".png" or name in entries):
            raise ValueError(f"模板名称无效：{name}")
        file = (source / "image" / name).resolve()
        if not file.is_relative_to((source / "image").resolve()) or not file.is_file():
            raise ValueError(f"缺少模板：{name}")
        if hashlib.sha256(file.read_bytes()).hexdigest() != record.get("sha256"):
            raise ValueError(f"模板校验失败：{name}")
        size = png_size(file)
        if record.get("size") != list(size) or not (0 < size[0] <= CANVAS[0] and 0 < size[1] <= CANVAS[1]):
            raise ValueError(f"模板尺寸无效：{name}")
        for roi in required.get(name[len("native/"):], []):
            if not roi_ok(roi) or size[0] > roi[2] or size[1] > roi[3]:
                raise ValueError(f"模板不适合当前版本：{name}")
        entries[name] = (file, {key: record[key] for key in ("name", "sha256", "size")})
    missing = {"native/" + name for name in required} - entries.keys()
    if missing:
        raise ValueError(f"缺少当前版本模板：{', '.join(sorted(missing)[:5])}")
    if not entries:
        raise ValueError("没有可导入的识别模板")
    target = (root / "dev_assets" / "private" / "resource_native").resolve()
    if not target.is_relative_to((root / "dev_assets" / "private").resolve()):
        raise ValueError("模板目录不在本地数据目录内")
    # Validate all source bytes before changing any local template.
    for name, (file, _) in sorted(entries.items()):
        dest = target / "image" / name
        if not dest.resolve().is_relative_to(target):
            raise ValueError("目标模板路径无效")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if file != dest.resolve():
            shutil.copy2(file, dest)
    target.mkdir(parents=True, exist_ok=True)
    clean = {"version": 1, "canvas": list(CANVAS), "template_prefix": "native/",
             "generated_by": "tools/import_local_resources.py",
             "templates": [record for _, record in entries.values()]}
    (target / "manifest.json").write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(entries)


def main() -> int:
    parser = argparse.ArgumentParser(description="导入本机已有识别模板，不导入账号或截图")
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    try:
        count = import_templates(args.source)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"导入失败：{error}")
        return 1
    print(f"已导入 {count} 个识别模板。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
