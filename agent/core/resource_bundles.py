"""Maa resources: public pipelines, packaged cropped templates, then optional local overrides.

Templates cropped from private captures are never committed. tools/build_native_resource.py writes
them to dev_assets/private/resource_native (gitignored); every launcher that runs SC.Common nodes
must post it or the packaged assets/resource/recognition after assets/resource/base. Without either the native/ templates are missing,
every guard fails, and the shared nodes end in SC.Common.FailTask without clicking.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE_BUNDLE = ROOT / "assets" / "resource" / "base"
PACKAGED_RECOGNITION_BUNDLE = ROOT / "assets" / "resource" / "recognition"
PRIVATE_NATIVE_BUNDLE = ROOT / "dev_assets" / "private" / "resource_native"


def bundle_paths(include_private: bool = True) -> list[Path]:
    """Bundles to pass to Resource.post_bundle, in load order (later bundles add images)."""
    paths = [BASE_BUNDLE]
    if (PACKAGED_RECOGNITION_BUNDLE / "manifest.json").is_file():
        paths.append(PACKAGED_RECOGNITION_BUNDLE)
    if include_private and (PRIVATE_NATIVE_BUNDLE / "manifest.json").is_file():
        paths.append(PRIVATE_NATIVE_BUNDLE)
    return paths


BASE_OCR_MODEL = BASE_BUNDLE / "model" / "ocr"
# Same ppocr_v6 small model that tools/configure.py copies from MaaCommonAssets at install time.
PRIVATE_OCR_MODEL = ROOT / "dev_assets" / "private" / "models" / "ppocr_v6_small"
OCR_FILES = ("det.onnx", "rec.onnx", "keys.txt")


def ocr_model_dir():
    """OCR model to post after the bundles when the base bundle has none (development checkout), else None."""
    if all((BASE_OCR_MODEL / name).is_file() for name in OCR_FILES):
        return None
    if all((PRIVATE_OCR_MODEL / name).is_file() for name in OCR_FILES):
        return PRIVATE_OCR_MODEL
    return None
