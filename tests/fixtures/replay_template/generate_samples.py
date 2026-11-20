"""Generate owned geometric badges and screenshot fixtures using only stdlib."""
import json
import struct
import zlib
from pathlib import Path

BASE = Path(__file__).resolve().parent


def write_png(name, width, height, pixels):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    rows = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")
    (BASE / name).write_bytes(png)


def badge(mirror=False, similar=False):
    pixels = bytearray()
    for y in range(64):
        for x in range(80):
            xx = 79 - x if mirror else x
            color = (42, 51, 66)
            if 4 <= xx < 76 and 4 <= y < 60: color = (218, 221, 229)
            if 8 <= xx < 72 and 8 <= y < 56: color = (35, 81, 141)
            if 12 <= xx < 25 and 12 <= y < 48: color = (245, 180, 55)
            if 25 <= xx < 62 and 35 <= y < 48: color = (245, 180, 55)
            if 37 <= xx < 55 and 14 <= y < 29 and abs(xx - 46) + abs(y - 21) <= 11: color = (230, 75, 103)
            if similar and 8 <= xx < 72 and 8 <= y < 35: color = (65, 143, 91)
            pixels.extend(color)
    return pixels


def paste(canvas, patch, x, y):
    for row in range(64):
        start = ((y + row) * 1280 + x) * 3
        canvas[start:start + 80 * 3] = patch[row * 80 * 3:(row + 1) * 80 * 3]


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    a, b = badge(), badge(mirror=True)
    write_png("badge-a.png", 80, 64, a)
    write_png("badge-b.png", 80, 64, b)
    samples = []
    for name in ("positive_a", "positive_b", "similar_negative", "unknown", "invalid_size", "ambiguous"):
        canvas = bytearray((24, 28, 36)) * (1280 * 720)
        if name in ("positive_a", "ambiguous"): paste(canvas, a, 260, 240)
        if name in ("positive_b", "ambiguous"): paste(canvas, b, 760, 240)
        if name == "similar_negative": paste(canvas, badge(similar=True), 260, 240)
        if name == "invalid_size": write_png(name + ".png", 640, 360, bytearray((24, 28, 36)) * (640 * 360))
        else: write_png(name + ".png", 1280, 720, canvas)
        positive = name.startswith("positive_")
        scene = "SC.Replay.Badge." + name[-1].upper() if positive else None
        reason = "positive_guard" if positive else "invalid_dimensions" if name == "invalid_size" else "ambiguous_state" if name == "ambiguous" else "unknown_state"
        samples.append({"id": name, "image": name + ".png", "expected": {"scene": scene, "allowed": positive, "reason": reason}})
    scenes = [{"name": "SC.Replay.Badge." + letter.upper(), "recognition": "TemplateMatch", "roi": [x, 180, 300, 200], "template": "badge-" + letter + ".png", "threshold": 0.95} for letter, x in (("a", 200), ("b", 700))]
    (BASE / "manifest.json").write_text(json.dumps({"version": 1, "canvas": [1280, 720], "scenes": scenes, "samples": samples}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
