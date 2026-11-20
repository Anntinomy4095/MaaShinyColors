#!/usr/bin/env python3
"""Prepare existing character sprites locally; optional public props come from Noto Emoji."""
from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CHARACTER_SOURCES = {
    "mano": "https://image.shinycolors.wiki/4/4d/ManoChibiR.png",
    "tenka": "https://image.shinycolors.wiki/6/68/TenkaChibiR.png",
    "amana": "https://image.shinycolors.wiki/0/04/AmanaChibiR.png",
    "hiori": "https://image.shinycolors.wiki/1/1d/HioriChibiR.png",
    "meguru": "https://image.shinycolors.wiki/1/11/MeguruChibiR.png",
    "kogane": "https://image.shinycolors.wiki/8/80/KoganeChibiR.png",
    "mamimi": "https://image.shinycolors.wiki/6/69/MamimiChibiR.png",
    "sakuya": "https://image.shinycolors.wiki/6/64/SakuyaChibiR.png",
    "yuika": "https://image.shinycolors.wiki/a/a1/YuikaChibiR1.png",
    "kiriko": "https://image.shinycolors.wiki/4/48/KirikoChibiR.png",
    "kaho": "https://image.shinycolors.wiki/1/12/KahoChibiR.png",
    "chiyoko": "https://image.shinycolors.wiki/e/ec/ChiyokoChibiR.png",
    "juri": "https://image.shinycolors.wiki/f/fd/JuriChibiR.png",
    "rinze": "https://image.shinycolors.wiki/f/f0/RinzeChibiR.png",
    "natsuha": "https://image.shinycolors.wiki/3/3c/NatsuhaChibiR.png",
    "chiyuki": "https://image.shinycolors.wiki/7/79/ChiyukiChibiR.png",
    "asahi": "https://image.shinycolors.wiki/f/f0/AsahiChibiR.png",
    "fuyuko": "https://image.shinycolors.wiki/9/90/FuyukoChibiR.png",
    "mei": "https://image.shinycolors.wiki/8/86/MeiChibiR.png",
    "toru": "https://image.shinycolors.wiki/7/7e/ToruChibiR.png",
    "madoka": "https://image.shinycolors.wiki/5/5f/MadokaChibiR.png",
    "koito": "https://image.shinycolors.wiki/3/35/KoitoChibiR.png",
    "hinana": "https://image.shinycolors.wiki/d/d4/HinanaChibiR.png",
    "nichika": "https://image.shinycolors.wiki/c/cb/NichikaChibiR.png",
    "mikoto": "https://image.shinycolors.wiki/a/af/MikotoChibiR.png",
    "luca": "https://image.shinycolors.wiki/9/9c/LucaChibiR.png",
    "hana": "https://image.shinycolors.wiki/a/a0/HanaChibiR.png",
    "haruki": "https://image.shinycolors.wiki/0/07/HarukiChibiR.png",
}
PROP_SOURCES = {"dove.svg": "emoji_u1f54a.svg", "parcel.svg": "emoji_u1f4e6.svg",
                "controller.svg": "emoji_u1f3ae.svg", "gift.svg": "emoji_u1f381.svg", "sleep.svg": "emoji_u1f4a4.svg",
                "camera.svg": "emoji_u1f4f7.svg", "castle.svg": "emoji_u1f3f0.svg", "flag.svg": "emoji_u1f6a9.svg",
                "cup.svg": "emoji_u1f964.svg", "tea.svg": "emoji_u1f375.svg", "cookie.svg": "emoji_u1f36a.svg",
                "rainbow.svg": "emoji_u1f308.svg", "paper.svg": "emoji_u1f4c4.svg", "book.svg": "emoji_u1f4d6.svg",
                "leaf.svg": "emoji_u1f343.svg", "music.svg": "emoji_u1f3b5.svg", "stone.svg": "emoji_u1faa8.svg", "sparkle.svg": "emoji_u2728.svg"}
NOTO_ROOT = "https://raw.githubusercontent.com/googlefonts/noto-emoji/v2.051/svg/"
PRIVATE = ROOT / "dev_assets/private/easter-eggs"
OUTPUT = ROOT / "ui/game-assets"


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "MaaShinyColors-local-assets/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def rig_dove(source, *, flying):
    """Separate the existing Noto wing paths. No new character outlines are drawn."""
    ns = "http://www.w3.org/2000/svg"
    ET.register_namespace("", ns)
    tag = lambda name: "{" + ns + "}" + name
    paths = ET.fromstring(source).findall(tag("path"))
    svg = ET.Element(tag("svg"), {"viewBox": "0 0 128 128"})
    ET.SubElement(svg, tag("title")).text = "Noto Emoji dove, articulated existing wing paths"
    defs = ET.SubElement(svg, tag("defs"))
    for name, points in (("body", "0,0 49,0 49,60 59,66 88,85 128,85 128,128 0,128"),
                         ("wing", "49,0 128,0 128,85 88,85 59,66 49,60")):
        clip = ET.SubElement(defs, tag("clipPath"), {"id": name})
        ET.SubElement(clip, tag("polygon"), {"points": points})
    if flying:
        ET.SubElement(svg, tag("style")).text = (
            ".wing{animation:flap .38s ease-in-out infinite;transform-origin:0 0}"
            ".rear{animation-delay:-.08s}"
            "@keyframes flap{0%,100%{transform:scale(1,1)}50%{transform:scale(1,.12)}}"
            "@media(prefers-reduced-motion:reduce){.wing{animation:none}}")
    rear = ET.SubElement(svg, tag("g"), {"transform": "translate(59 66)"})
    rear_motion = ET.SubElement(rear, tag("g"), {"class": "wing rear"} if flying else {"transform": "scale(.45 .2)"})
    rear_path = copy.deepcopy(paths[4])
    rear_path.set("transform", "translate(-59 -66)")
    rear_motion.append(rear_path)
    body = ET.SubElement(svg, tag("g"), {"clip-path": "url(#body)"})
    body.append(copy.deepcopy(paths[6]))
    front = ET.SubElement(svg, tag("g"), {"transform": "translate(59 66)"})
    motion = ET.SubElement(front, tag("g"), {"class": "wing"} if flying else {"transform": "rotate(40) scale(.72 .28)"})
    clipped = ET.SubElement(motion, tag("g"), {"transform": "translate(-59 -66)", "clip-path": "url(#wing)"})
    for index in (6, 7, 8): clipped.append(copy.deepcopy(paths[index]))
    for index in (9, 10): svg.append(copy.deepcopy(paths[index]))
    return ET.tostring(svg, encoding="utf-8", xml_declaration=True)


def prepare(*, fetch=False, with_props=False):
    from PIL import Image

    cache = PRIVATE / "characters"
    out = OUTPUT / "office"
    report = {"characters": {}, "props": {}, "missing": []}
    def character(entry):
        key, url = entry
        source = cache / f"{key}.png"
        try:
            if fetch and not source.is_file():
                blob = download(url)
                with Image.open(io.BytesIO(blob)) as image:
                    image.verify()
                cache.mkdir(parents=True, exist_ok=True)
                source.write_bytes(blob)
            if not source.is_file():
                return key, None
            blob = source.read_bytes()
            with Image.open(io.BytesIO(blob)) as image:
                size = list(image.size)
            out.mkdir(parents=True, exist_ok=True)
            (out / source.name).write_bytes(blob)
            return key, {"src": f"./office/{source.name}", "source": url,
                "size": size, "sha256": hashlib.sha256(blob).hexdigest(), "local_only": True}
        except (OSError, ValueError) as error:
            return key, {"error": str(error)}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for key, result in pool.map(character, CHARACTER_SOURCES.items()):
            if result is not None: report["characters"][key] = result
            if not result or "src" not in result: report["missing"].append(key)
    if with_props:
        props = ROOT / "ui/assets/office"
        props.mkdir(parents=True, exist_ok=True)
        for name, remote in PROP_SOURCES.items():
            destination = props / name
            blob = destination.read_bytes() if destination.is_file() else download(NOTO_ROOT + remote)
            blob = blob.replace(b' xmlns:xlink="http://www.w3.org/1999/xlink"', b'')
            (props / name).write_bytes(blob)
            report["props"][name] = {"source": NOTO_ROOT + remote, "sha256": hashlib.sha256(blob).hexdigest()}
        notice = props / "NOTICE.txt"
        original = notice.read_bytes().split(b"\nMaaShinyColors:")[0] if notice.is_file() else download(NOTO_ROOT + "LICENSE")
        notice.write_bytes(original + b"\nMaaShinyColors: unused xlink namespaces removed. dove-rest.svg and dove-flight.svg reuse the original dove paths with clipping, transforms and wing animation; the olive branch is omitted. Other graphic paths unchanged.\n")
        if not (props / "LICENSE.txt").is_file():
            (props / "LICENSE.txt").write_bytes(download("https://www.apache.org/licenses/LICENSE-2.0.txt"))
    props = ROOT / "ui/assets/office"
    if (props / "dove.svg").is_file():
        original = (props / "dove.svg").read_bytes()
        for name, flying in (("dove-rest.svg", False), ("dove-flight.svg", True)):
            (props / name).write_bytes(rig_dove(original, flying=flying))
    if report["characters"]:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        usable = {key: value for key, value in report["characters"].items() if "src" in value}
        (OUTPUT / "office-eggs.js").write_text("// Local character artwork.\nexport default " +
            json.dumps(usable, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    (PRIVATE / "sources.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="准备彩蛋素材（游戏人物仅保存在本机）")
    parser.add_argument("--fetch", action="store_true", help="下载缺少的八组合 28 人人物 SD")
    parser.add_argument("--with-props", action="store_true", help="下载仓库使用的 Noto Emoji 道具与许可")
    args = parser.parse_args(argv)
    result = prepare(fetch=args.fetch, with_props=args.with_props)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
