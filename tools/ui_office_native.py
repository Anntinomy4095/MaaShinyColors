#!/usr/bin/env python3
"""Prepare unmodified native office SD/standing resources for local previews only.

Without --fetch this tool only rebuilds modules from locally cached sources.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "dev_assets/private/easter-eggs/native"
OUTPUT = ROOT / "ui/game-assets/office-native"
USER_AGENT = "MaaShinyColors-asset-research/1.0"
API = "https://api.shinycolors.moe/spine/"
ASSETS = "https://cf-static.shinycolors.moe/"
VENDOR = "https://raw.githubusercontent.com/ShinyColorsDB/ShinyColorsDB-SpineViewer/main/public/"
KEYS = ("mano", "hiori", "meguru", "kogane", "mamimi", "sakuya", "yuika", "kiriko",
        "kaho", "chiyoko", "juri", "rinze", "natsuha", "amana", "tenka", "chiyuki",
        "asahi", "fuyuko", "mei", "toru", "madoka", "koito", "hinana", "nichika",
        "mikoto", "luca", "hana", "haruki")
VENDOR_FILES = {
    "pixi.js": VENDOR + "v8.5.2.pixi.min.js",
    "spine.js": VENDOR + "v8-spine37.js",
    "csp.js": "https://unpkg.com/pixi.js@8.5.2/dist/packages/unsafe-eval.js",
}
CASUAL_ORDERS = {"first-ssr-casual": 1, "casual-extra-1": 2, "casual-extra-2": 3}
CASUAL_OVERRIDES = {
    "mano": {3: 4},
    "meguru": {2: 4, 3: 6},
    "kogane": {1: 4, 2: 6},
    "sakuya": {1: 6},
    "kiriko": {3: 6},
    "kaho": {3: 7},
    "chiyoko": {3: 4},
    "juri": {1: 7},
    "rinze": {2: 4},
    "natsuha": {1: 8, 2: 6, 3: 7},
    "amana": {3: 4},
    "chiyuki": {3: 4},
    "mei": {2: 4, 3: 7},
    "hinana": {2: 5, 3: 7},
    "hana": {2: 5},
}
VARIANT_DIRS = {
    "cb": "", "stand": "stand/", "idol-road": "idol-road/", "staff": "staff/",
    **{kind: f"{kind}/" for kind in CASUAL_ORDERS},
}
STAFF_ID = 91
STAFF_CARD = "2040910010"


def source_bytes(url: str, destination: Path, fetch: bool) -> bytes:
    if destination.is_file():
        return destination.read_bytes()
    if not fetch:
        raise FileNotFoundError(f"Missing local source: {destination}; use --fetch explicitly")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return content


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_module(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("export default " + json.dumps(value, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")


def casual_card_id(key: str, kind: str) -> str:
    position = CASUAL_ORDERS[kind]
    order = CASUAL_OVERRIDES.get(key, {}).get(position, position)
    return f"104{KEYS.index(key) + 1:03d}{order:03d}0"


def prepare_vendor(fetch: bool) -> None:
    records = []
    for alias, url in VENDOR_FILES.items():
        original = url.rsplit("/", 1)[-1]
        raw = source_bytes(url, PRIVATE / "vendor" / original, fetch)
        target = OUTPUT / "vendor" / alias
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        records.append({"source": url, "original": original, "local": str(target.relative_to(ROOT)),
                        "sha256": sha256(raw), "bytes": len(raw)})
    write_json(PRIVATE / "vendor-sources.json", records)


def prepare_character(key: str, fetch: bool, kind: str = "cb") -> dict:
    if kind not in VARIANT_DIRS:
        raise ValueError(f"Unknown model kind: {kind}")
    if (key == "hazuki") != (kind == "staff"):
        raise ValueError(f"Invalid character/variant pair: {key}/{kind}")
    idol_id = STAFF_ID if kind == "staff" else KEYS.index(key) + 1
    character_cache = PRIVATE / key
    cache = character_cache if kind == "cb" else character_cache / kind
    directory_url = API + f"dresslist?idolId={idol_id}"
    directory_bytes = source_bytes(directory_url, character_cache / "dresslist.json", fetch)
    directory = json.loads(directory_bytes)
    if kind in CASUAL_ORDERS or kind in ("idol-road", "staff"):
        card_id = casual_card_id(key, kind) if kind in CASUAL_ORDERS else (
            STAFF_CARD if kind == "staff" else f"194{idol_id:03d}0010")
        rarity = "S_SSR" if kind == "staff" else "P_SSR"
        dress = next((row for row in directory if row.get("enzaId") == card_id
                      and row.get("dressType") == rarity), None)
        if dress is None:
            raise ValueError(f"{rarity} {card_id} is missing from the dress list")
        # Use the card ID even if its dress-list entry omits the SD model.
        asset_type = "cb" if kind in CASUAL_ORDERS else "cb_costume"
        model_path = f"spine/idols/{asset_type}/{card_id}/"
    else:
        selected = next(((dress, asset["path"]) for dress in directory
                         for asset in dress.get("assets", {}).get("idols", [])
                         if asset.get("type") == kind and asset.get("path", "").startswith(f"spine/idols/{kind}/")), None)
        if selected is None:
            raise ValueError(f"No normal-clothes {kind} model in verified directory for {key}")
        dress, model_path = selected
    base = ASSETS + model_path
    asset_cache = cache
    if kind in CASUAL_ORDERS and (kind != "first-ssr-casual" or not card_id.endswith("0010")):
        asset_cache /= card_id
    raw_json = source_bytes(base + "data.json", asset_cache / "data.json", fetch)
    raw_atlas = source_bytes(base + "data.atlas", asset_cache / "data.atlas", fetch)
    skeleton = json.loads(raw_json)
    if (not isinstance(skeleton.get("bones"), list) or not skeleton["bones"]
            or not isinstance(skeleton.get("animations"), dict)
            or not isinstance(skeleton.get("skeleton", {}).get("spine"), str)):
        raise ValueError(f"Invalid native skeleton for {key}/{kind}")
    atlas = raw_atlas.decode("utf-8-sig")
    page_names = re.findall(r"(?m)^([^\r\n]+\.png)\r?$", atlas)
    if not page_names:
        raise ValueError(f"No PNG atlas page in {key}")
    records = [{"source": directory_url, "file": "dresslist.json", "sha256": sha256(directory_bytes)},
               {"source": base + "data.json", "file": "data.json", "sha256": sha256(raw_json)},
               {"source": base + "data.atlas", "file": "data.atlas", "sha256": sha256(raw_atlas)}]
    pages = []
    prefix = VARIANT_DIRS[kind]
    for name in dict.fromkeys(page_names):
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError(f"Unexpected atlas page name: {name}")
        raw_page = source_bytes(base + name, asset_cache / name, fetch)
        if not (raw_page.startswith(b"\x89PNG\r\n\x1a\n") or (raw_page[:4] == b"RIFF" and raw_page[8:12] == b"WEBP")):
            raise ValueError(f"Invalid PNG/WebP atlas page for {key}: {name}")
        output_page = OUTPUT / prefix / key / name
        output_page.parent.mkdir(parents=True, exist_ok=True)
        output_page.write_bytes(raw_page)
        pages.append({"name": name, "src": f"./{prefix}{key}/{name}"})
        records.append({"source": base + name, "file": name, "sha256": sha256(raw_page), "bytes": len(raw_page)})
    animations = list(skeleton.get("animations", {}))
    write_module(OUTPUT / prefix / f"{key}.js", {"skeleton": skeleton, "atlas": atlas,
                                       "pages": pages, "animations": animations})
    metadata = {"module": f"./{prefix}{key}.js", "kind": kind, "idolId": idol_id, "cardId": dress.get("enzaId"),
                "spineVersion": skeleton.get("skeleton", {}).get("spine"),
                "animations": animations,
                "bones": [{field: bone[field] for field in ("name", "parent") if field in bone}
                          for bone in skeleton["bones"]],
                "bounds": {field: skeleton.get("skeleton", {}).get(field, 0)
                           for field in ("x", "y", "width", "height")}}
    write_json(cache / "report.json", {"key": key, "idolId": idol_id, "dressName": dress.get("dressName"),
                                      "modelPath": model_path, "metadata": metadata, "sources": records})
    return metadata


def write_index() -> dict:
    available = {}
    for key in (*KEYS, "hazuki"):
        models = {}
        for kind, prefix in VARIANT_DIRS.items():
            cache = PRIVATE / key if kind == "cb" else PRIVATE / key / kind
            report = cache / "report.json"
            if report.is_file() and (OUTPUT / prefix / f"{key}.js").is_file():
                metadata = json.loads(report.read_text(encoding="utf-8"))["metadata"]
                if kind not in CASUAL_ORDERS or metadata["cardId"] == casual_card_id(key, kind):
                    models[kind] = metadata
        if models:
            # Preserve existing cb top-level consumers, adding explicit model variants.
            available[key] = {**models.get("cb", {}), "models": models}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    prefix = "export const runtime={pixi:'./vendor/pixi.js',spine:'./vendor/spine.js',csp:'./vendor/csp.js'};\n"
    body = "export default " + json.dumps(available, ensure_ascii=False, separators=(",", ":")) + ";\n"
    (OUTPUT / "index.js").write_text(prefix + body, encoding="utf-8")
    return available


def prepare_outfits(key: str, fetch: bool, kinds: tuple[str, ...]) -> list[str]:
    failed = []
    for kind in kinds:
        label = f"{key}/{kind}"
        try:
            result = prepare_character(key, fetch, kind)
            print(f"{label}: {len(result['animations'])} original animations", flush=True)
        except (OSError, ValueError, KeyError) as exc:
            failed.append(label)
            print(f"{label}: {type(exc).__name__}: {exc}", flush=True)
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="Explicitly allow fetching missing public sources")
    parser.add_argument("--only", choices=(*KEYS, "hazuki"), help="Prepare one character first")
    parser.add_argument("--kind", choices=(*VARIANT_DIRS, "all", "casual"), default="cb",
                        help="Use casual for the visitor outfits or staff for Hazuki")
    args = parser.parse_args()
    roster = ("hazuki",) if args.kind == "staff" else KEYS
    if args.only and args.only not in roster:
        parser.error(f"--only {args.only} does not support --kind {args.kind}")
    selected = [args.only] if args.only else roster
    if args.fetch:
        source_bytes(API + "idollist", PRIVATE / "idollist.json", True)
    prepare_vendor(args.fetch)
    failed = []
    groups = {"all": ("cb", "stand"), "casual": tuple(CASUAL_ORDERS)}
    kinds = groups.get(args.kind, (args.kind,))
    with ThreadPoolExecutor(max_workers=min(6, len(selected))) as pool:
        pending = [pool.submit(prepare_outfits, key, args.fetch, kinds) for key in selected]
        for future in as_completed(pending):
            failed.extend(future.result())
    available = write_index()
    print(f"Local native characters: {len(available)}; {OUTPUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
