"""Portable installs have no Git metadata; restrict generated assets to local folders."""
import json
from pathlib import Path


def portable_output_allowed(path: Path, root: Path) -> bool:
    root, path = root.resolve(), path.resolve()
    if not path.is_relative_to(root):
        return False
    # Do not replace the repository's gitignore check with a package marker.
    if (root / ".git").exists():
        return False
    allowed = (root / "ui/game-assets", root / "dev_assets/private")
    if not any(path != base and path.is_relative_to(base.resolve()) for base in allowed):
        return False
    try:
        manifest = json.loads((root / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
        return manifest.get("name") == "MaaShinyColors" and (root / "project_policy.json").is_file()
    except (OSError, ValueError, AttributeError):
        return False
