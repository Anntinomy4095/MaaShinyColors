"""Rebase paths recorded by a former checkout of this repository.

Old evidence files keep their original absolute paths and are never rewritten:
other proofs bind their SHA-256. Callers must still verify each file's hash.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "dev_assets/private"
# Former workspace roots, most recent first.
LEGACY_ROOTS = (r"D:\Machine Learning\maa",)


def rebase_legacy(value: str, root: Path = ROOT) -> str:
    """Map a path under a known former root onto ``root``; other values are returned unchanged."""
    if not isinstance(value, str):
        raise ValueError("路径必须为字符串")
    for old in LEGACY_ROOTS:
        prefix = old + "\\"
        if value[:len(prefix)].lower() == prefix.lower():
            return str(root / value[len(prefix):])
    return value


def legacy_private_path(value: str, private: Path = PRIVATE) -> Path:
    """Resolve a recorded path, rebased if legacy, and require it to stay inside ``private``."""
    path = Path(rebase_legacy(value, private.parents[1])).resolve()
    if not path.is_relative_to(private.resolve()):
        raise ValueError("文件必须位于private")
    return path
