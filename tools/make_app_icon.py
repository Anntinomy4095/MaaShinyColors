"""Build the app icons from the one source picture ``browser_host/app_icon.png`` (1024 px, transparent corners).

Writes ``browser_host/app.ico`` (16-128 px: window, taskbar, tray and the portable launcher) and ``ui/icon.png``
(256 px: panel header, page icon and the host's own header). The .ico stops at 128 px: with a 256 px frame the
launcher is mostly compressed image data, and endpoint protection on a test machine refused to start it.
Run: ``.venv/Scripts/python.exe tools/make_app_icon.py``.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "browser_host" / "app_icon.png"
ICO = ROOT / "browser_host" / "app.ico"
UI_ICON = ROOT / "ui" / "icon.png"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128)


def scaled(img: Image.Image, size: int) -> Image.Image:
    frame = img.resize((size, size), Image.LANCZOS)
    # small frames lose the outline in the downscale; a light unsharp mask keeps the face readable
    return frame.filter(ImageFilter.UnsharpMask(radius=0.6, percent=60, threshold=2)) if size <= 48 else frame


def main() -> int:
    img = Image.open(SOURCE).convert("RGBA")
    if img.size != (1024, 1024):
        raise SystemExit(f"{SOURCE.relative_to(ROOT)} must be 1024x1024, got {img.size}")
    frames = [scaled(img, n) for n in ICO_SIZES]
    frames[-1].save(ICO, sizes=[f.size for f in frames], append_images=frames[:-1])
    scaled(img, 256).save(UI_ICON, optimize=True)
    print(f"wrote {ICO.relative_to(ROOT)} and {UI_ICON.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
