#!/usr/bin/env python3
"""Recognition-only screenshot replay; no windows, devices, or input tasks."""
from __future__ import annotations

import argparse
import json
import hashlib
import math
import struct
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANVAS = (1280, 720)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_manifest(data, base):
    """Validate all configuration before importing Maa or reading images."""
    require(isinstance(data, dict) and set(data) == {"version", "canvas", "scenes", "samples"}, "清单字段无效")
    require(data["version"] == 1 and data["canvas"] == list(CANVAS), "仅支持版本1和1280x720画布")
    scenes = data["scenes"]
    require(isinstance(scenes, list) and scenes, "场景不能为空")
    names = set()
    for scene in scenes:
        require(isinstance(scene, dict), "场景须为对象")
        algorithm = scene.get("recognition")
        require(algorithm in {"ColorMatch", "TemplateMatch"}, "仅允许ColorMatch或TemplateMatch识别")
        fields = {"name", "recognition", "roi"} | ({"lower", "upper", "count"} if algorithm == "ColorMatch" else {"template", "threshold"})
        require(set(scene) == fields, "场景仅允许识别参数；拒绝Pipeline、Custom和动作")
        name = scene["name"]
        require(isinstance(name, str) and name.startswith("SC.") and name not in names, "场景须使用唯一SC.名称")
        names.add(name)
        roi = scene["roi"]
        require(isinstance(roi, list) and len(roi) == 4 and all(type(v) is int for v in roi), "ROI必须是四个整数")
        x, y, w, h = roi
        require(x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= 1280 and y + h <= 720, "ROI超出画布")
        if algorithm == "ColorMatch":
            for key in ("lower", "upper"):
                color = scene[key]
                require(isinstance(color, list) and len(color) == 3 and all(type(v) is int and 0 <= v <= 255 for v in color), "RGB范围无效")
            require(all(a <= b for a, b in zip(scene["lower"], scene["upper"])), "颜色下界超过上界")
            require(type(scene["count"]) is int and 0 < scene["count"] <= w * h, "像素阈值无效")
        else:
            threshold = scene["threshold"]
            require(type(threshold) in (int, float) and math.isfinite(threshold) and 0 < threshold <= 1, "模板阈值须为有限数且在(0,1]内")
            template = template_path(scene, base)
            tw, th = png_dimensions(template)
            require(0 < tw <= w and 0 < th <= h, "模板尺寸须为正且不大于ROI")
    require(isinstance(data["samples"], list) and data["samples"], "样本不能为空")
    identifiers = set()
    for sample in data["samples"]:
        require(isinstance(sample, dict) and set(sample) == {"id", "image", "expected"}, "样本字段无效")
        require(isinstance(sample["id"], str) and sample["id"] and sample["id"] not in identifiers, "样本ID须唯一")
        identifiers.add(sample["id"])
        require(isinstance(sample["image"], str) and sample["image"], "图像路径无效")
        path = (base / sample["image"]).resolve()
        require(path.is_relative_to(base.resolve()) and path.suffix.lower() == ".png" and path.is_file(), "图像须为清单目录内的现有PNG")
        expected = sample["expected"]
        require(isinstance(expected, dict) and set(expected) == {"scene", "allowed", "reason"}, "expected字段无效")
        require(expected["scene"] is None or expected["scene"] in names, "expected引用未知场景")
        require(type(expected["allowed"]) is bool and expected["reason"] in {"positive_guard", "unknown_state", "ambiguous_state", "invalid_dimensions"}, "expected结果无效")
        require((expected["allowed"] and expected["scene"] is not None and expected["reason"] == "positive_guard") or (not expected["allowed"] and expected["scene"] is None and expected["reason"] != "positive_guard"), "expected语义冲突")
    return data


def png_dimensions(path):
    with path.open("rb") as stream:
        header = stream.read(24)
    require(len(header) == 24 and header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR", "PNG文件头无效")
    return struct.unpack(">II", header[16:24])


def template_path(scene, base):
    require(base is not None, "模板识别必须指定清单目录")
    name = scene.get("template")
    require(isinstance(name, str) and name, "模板须为单个PNG相对路径")
    relative = Path(name)
    require(not relative.is_absolute() and not relative.drive, "模板不允许绝对路径")
    base = Path(base).resolve()
    path = (base / relative).resolve()
    require(path.is_relative_to(base) and path.suffix.lower() == ".png" and path.is_file(), "模板须为清单目录内的现有PNG")
    return path


def decide(recognitions, dimensions):
    if tuple(dimensions) != CANVAS:
        return {"scene": None, "allowed": False, "reason": "invalid_dimensions"}
    hits = [item["scene"] for item in recognitions if item["hit"]]
    if len(hits) == 1:
        return {"scene": hits[0], "allowed": True, "reason": "positive_guard"}
    return {"scene": None, "allowed": False, "reason": "ambiguous_state" if hits else "unknown_state"}


def read_png(path, expected_size=CANVAS):
    """Decode common RGB/RGBA screenshots without an optional imaging package."""
    import numpy as np
    blob = path.read_bytes()
    width, height = png_dimensions(path)
    require(0 < width <= CANVAS[0] and 0 < height <= CANVAS[1], "PNG尺寸超出支持范围")
    require(expected_size is None or (width, height) == tuple(expected_size), "PNG尺寸不符合要求")
    position, compressed, channels = 8, bytearray(), None
    while position < len(blob):
        require(position + 12 <= len(blob), "PNG块被截断")
        length = struct.unpack(">I", blob[position:position + 4])[0]
        kind = blob[position + 4:position + 8]
        payload = blob[position + 8:position + 8 + length]
        require(position + 12 + length <= len(blob), "PNG数据被截断")
        crc = struct.unpack(">I", blob[position + 8 + length:position + 12 + length])[0]
        require(zlib.crc32(kind + payload) & 0xffffffff == crc, "PNG校验失败")
        if kind == b"IHDR":
            require(len(payload) == 13 and payload[8] == 8 and payload[9] in (2, 6) and payload[10:] == b"\0\0\0", "仅支持8位非交错RGB/RGBA PNG")
            channels = 3 if payload[9] == 2 else 4
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
        position += length + 12
    require(channels is not None, "缺少PNG图像头")
    stride = width * channels
    limit = height * (stride + 1)
    decompressor = zlib.decompressobj()
    raw = decompressor.decompress(bytes(compressed), limit + 1)
    require(len(raw) == limit and decompressor.eof, "PNG解压长度无效")
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is not None:
        import io
        with Image.open(io.BytesIO(blob)) as source:
            require(source.mode == ('RGB' if channels == 3 else 'RGBA'), "PNG颜色模式不符")
            pixels = np.asarray(source)
            if channels == 4:
                require(bool((pixels[:, :, 3] == 255).all()), "拒绝透明截图")
            return np.ascontiguousarray(pixels[:, :, :3][:, :, ::-1])
    decoded = bytearray(height * stride)
    for y in range(height):
        offset = y * (stride + 1)
        filter_type = raw[offset]
        require(filter_type in range(5), "PNG过滤器无效")
        row = bytearray(raw[offset + 1:offset + 1 + stride])
        for x in range(stride):
            left = row[x - channels] if x >= channels else 0
            above = decoded[(y - 1) * stride + x] if y else 0
            corner = decoded[(y - 1) * stride + x - channels] if y and x >= channels else 0
            predictor = 0
            if filter_type == 1: predictor = left
            elif filter_type == 2: predictor = above
            elif filter_type == 3: predictor = (left + above) // 2
            elif filter_type == 4:
                p = left + above - corner
                distances = (abs(p - left), abs(p - above), abs(p - corner))
                predictor = (left, above, corner)[distances.index(min(distances))]
            row[x] = (row[x] + predictor) & 255
        decoded[y * stride:(y + 1) * stride] = row
    rgb = np.frombuffer(decoded, dtype=np.uint8).reshape(height, width, channels)
    if channels == 4:
        require(bool((rgb[:, :, 3] == 255).all()), "拒绝透明截图")
    return np.ascontiguousarray(rgb[:, :, :3][:, :, ::-1])


class MaaRecognizer:
    """Own an isolated resource and controller; invoke only post_recognition."""
    def __init__(self):
        from maa.controller import CustomController
        from maa.library import Library
        from maa.resource import Resource
        from maa.tasker import Tasker

        class ScreenshotOnlyController(CustomController):
            input_attempts = 0
            def connect(self): return True
            def request_uuid(self): return "SC.Offline.ScreenshotReplay"
            def screencap(self): return None
            def deny(self, *args):
                self.input_attempts += 1
                return False
            start_app = stop_app = click = swipe = touch_down = touch_move = touch_up = deny
            click_key = input_text = key_down = key_up = scroll = relative_move = deny
            def shell(self, *args):
                self.input_attempts += 1
                return None

        self.version = Library.version()
        require(self.version == "v5.12.3", "回放要求固定MaaFramework v5.12.3")
        self.controller = ScreenshotOnlyController()
        require(self.controller.post_connection().wait().succeeded, "截图控制器初始化失败")
        self.temp = tempfile.TemporaryDirectory(prefix="sc-replay-")
        bundle = Path(self.temp.name)
        (bundle / "pipeline").mkdir()
        (bundle / "pipeline" / "replay.json").write_text("{}", encoding="utf-8")
        self.resource = Resource()
        require(self.resource.post_bundle(bundle).wait().succeeded, "空识别资源加载失败")
        self.tasker = Tasker()
        require(self.tasker.bind(self.resource, self.controller) and self.tasker.inited, "识别器绑定失败")
        self._template_cache = {}

    @property
    def input_attempts(self):
        return self.controller.input_attempts

    def recognize(self, scene, image, base=None):
        from maa.pipeline import JColorMatch, JRecognitionType, JTemplateMatch
        algorithm = scene.get("recognition", "ColorMatch")
        if algorithm == "ColorMatch":
            param = JColorMatch(lower=[scene["lower"]], upper=[scene["upper"]], roi=scene["roi"], count=scene["count"], method=4, connected=False)
            reco_type = JRecognitionType.ColorMatch
        else:
            require(algorithm == "TemplateMatch", "不允许自定义或其他识别类型")
            path = template_path(scene, base)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            template = self._template_cache.get(digest)
            if template is None:
                template = read_png(path, expected_size=None)
            require(template.shape[1] <= scene["roi"][2] and template.shape[0] <= scene["roi"][3], "模板大于ROI")
            require(bool((template != template[0, 0]).any()), "拒绝恒色模板")
            name = "SC.Replay.Template." + digest + ".png"
            if digest not in self._template_cache:
                require(self.resource.override_image(name, template), "模板载入Maa资源失败")
                self._template_cache[digest] = template
            param = JTemplateMatch(template=[name], roi=scene["roi"], threshold=[scene["threshold"]], method=5)
            reco_type = JRecognitionType.TemplateMatch
        job = self.tasker.post_recognition(reco_type, param, image).wait()
        detail = job.get()
        require(detail is not None and len(detail.nodes) == 1 and detail.nodes[0].recognition is not None, "Maa未返回实际识别详情")
        result = detail.nodes[0].recognition
        if algorithm == "TemplateMatch":
            score = max((item.score for item in result.all_results), default=None)
            return {"scene": scene["name"], "algorithm": algorithm, "roi": scene["roi"], "template": scene["template"], "hit": result.hit, "score": score, "threshold": scene["threshold"], "confidence": score, "confidence_kind": "template_match_score_not_probability", "results": [{"box": list(item.box), "score": item.score} for item in result.all_results], "raw_detail": result.raw_detail}
        count = max((item.count for item in result.all_results), default=0)
        return {"scene": scene["name"], "algorithm": "ColorMatch", "roi": scene["roi"], "hit": result.hit, "matched_pixels": count, "minimum_pixels": scene["count"], "confidence": count / (scene["roi"][2] * scene["roi"][3]), "confidence_kind": "matched_pixel_fraction_not_probability", "raw_detail": result.raw_detail}


def replay(data, base, recognizer):
    results = []
    for sample in data["samples"]:
        path = (base / sample["image"]).resolve()
        dimensions = png_dimensions(path)
        recognitions = []
        if dimensions == CANVAS:
            image = read_png(path)
            recognitions = [recognizer.recognize(scene, image, base) if scene["recognition"] == "TemplateMatch" else recognizer.recognize(scene, image) for scene in data["scenes"]]
        actual = decide(recognitions, dimensions)
        results.append({"id": sample["id"], "image": sample["image"], "dimensions": list(dimensions), "recognitions": recognitions, "actual": actual, "expected": sample["expected"], "passed": actual == sample["expected"]})
    return {"offline_only": True, "recognition_only": True, "framework_version": recognizer.version, "passed": all(item["passed"] for item in results), "samples": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description="仅对本地PNG进行Maa识别回放，不执行任务或输入。")
    parser.add_argument("--manifest", type=Path, default=ROOT / "tests/fixtures/replay/manifest.json")
    parser.add_argument("--report", type=Path, default=ROOT / "dev_assets/private/replay/report.json")
    args = parser.parse_args(argv)
    try:
        manifest = args.manifest.resolve()
        data = validate_manifest(json.loads(manifest.read_text(encoding="utf-8")), manifest.parent)
        report = replay(data, manifest.parent, MaaRecognizer())
        exit_code = 0 if report["passed"] else 1
    except Exception as exc:
        report = {"offline_only": True, "recognition_only": True, "passed": False, "error": str(exc)}
        exit_code = 2
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(args.report), "error": report.get("error")}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
