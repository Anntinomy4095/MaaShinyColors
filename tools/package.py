#!/usr/bin/env python3
"""Build the portable Windows package ``MaaShinyColors/``.

Layout (repo-shaped, so the host and the Python side find the project root and ``dev_assets/private`` unchanged)::

    MaaShinyColors/
      MaaShinyColors.exe        launcher (.NET Framework csc, tools/packaging/launcher/Launcher.cs)
      project_policy.json README.md LICENSE ...             policy (root marker: + assets/interface.json)
      agent/ tools/ assets/ ui/   selected runtime files
      assets/resource/base/model/ocr/   MaaCommonAssets ppocr_v6 small (pinned sha256)
      assets/resource/recognition/     required, verified cropped recognition templates only
      host/                     self-contained ``dotnet publish`` of browser_host (win-x64)
      .venv/Scripts/            CPython 3.14 embeddable + pinned wheels (not a real venv; same path the host expects)
      PACKAGE_MANIFEST.json     versions, hashes, sources, file count
      dev_assets/private/       NOT shipped; created on first run next to the exe

Content selection is ``git ls-files --cached --others --exclude-standard`` (so everything .gitignore keeps private —
``dev_assets/private``, ``ui/game-assets``, ``.venv``, profiles — is never copied) and then an allowlist of
top-level paths, followed by an audit that fails the build on any private or game-derived path.

Subcommands::

    python tools/package.py build  [--out DIR] [--zip]   # default --out dist/MaaShinyColors
    python tools/package.py audit  --package DIR         # exclusion audit only
    python tools/package.py smoke  --package DIR         # offline smoke tests from the packaged location

Network (build only, official sources, pinned hashes): python.org (embeddable zip), PyPI (wheels, --require-hashes),
nuget.org (the .NET runtime pack for the self-contained publish), raw.githubusercontent.com MaaXYZ/MaaCommonAssets
(OCR model, only when no verified local copy exists). Never touches a running host, the game, cookies or profiles.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PACKAGING = ROOT / "tools" / "packaging"
PRIVATE = ROOT / "dev_assets" / "private"
DEFAULT_OUT = ROOT / "dist" / "MaaShinyColors"
DEFAULT_CACHE = PRIVATE / "package-build" / "cache"
# Output folders the build may delete and recreate; anything else is refused.
ALLOWED_OUT_PARENTS = (ROOT / "dist", PRIVATE / "package-build")

PYTHON_VERSION = "3.14.10"
PYTHON_TAG = "314"
EMBED_NAME = f"python-{PYTHON_VERSION}-embed-amd64.zip"
EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{EMBED_NAME}"
EMBED_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"
RUNTIME_LOCK = PACKAGING / "requirements-runtime.txt"
LAUNCHER_SOURCE = PACKAGING / "launcher" / "Launcher.cs"
HOST_PROJECT = PurePosixPath("browser_host/MaaShinyColors.BrowserHost.csproj")
HOST_EXE = "MaaShinyColors.BrowserHost.exe"
DOTNET_RUNTIME_VERSION = "10.0.14"
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"

OCR_COMMIT = "dabcd4681ac990dc4361de26416d986abd80e4aa"
OCR_FILES = {
    "det.onnx": "66c0f34caaf432553710fd9973a7134d8cf924db7109310c3d2562dcc39b209d",
    "rec.onnx": "7dcf6298d77d2a6eb44c1ebeed990826ca895a9c68d955a3eace00763d052949",
    "keys.txt": "b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d",
}
OCR_URL = "https://raw.githubusercontent.com/MaaXYZ/MaaCommonAssets/{commit}/OCR/ppocr_v6/small/{name}"
OCR_LOCAL = PRIVATE / "models" / "ppocr_v6_small"
OCR_TARGET = PurePosixPath("assets/resource/base/model/ocr")
RECOGNITION_TARGET = PurePosixPath("assets/resource/recognition")

# Visual C++ runtime, deployed app-locally next to python.exe so MaaFramework's DLLs load on a machine without the
# VC++ redistributable (Microsoft permits app-local deployment of these files). Copied as one consistent set.
VC_RUNTIME = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
              "concrt140.dll")

# Non-ignored repository paths that go into the package (prefix match on posix paths).
INCLUDE_PREFIXES = ("agent/", "assets/")
RUNTIME_TOOLS = (
    "auto_team_propose.py", "build_native_resource.py", "card_inventory_scan.py", "check_project.py",
    "grade_protocol.py", "import_local_resources.py", "local_output.py", "navigation_protocol.py",
    "owned_levels_scan.py", "repeatable_navigation_protocol.py", "replay_ocr.py", "replay_screenshots.py",
    "run_tasks.py", "shinycolorsdb_import.py", "ui_engine.py", "ui_game_assets.py", "ui_game_assets_fetch.py",
    "ui_office_native.py", "ui_schema.py", "ui_wing.py", "update_card_library.py", "wing_segments.py",
    "wing_spend_ledger.py",
)
INCLUDE_FILES = {
    "README.md", "LICENSE", "project_policy.json", "docs/AUTO_TEAM.md", "docs/AUTO_PRODUCE.md", "docs/SAFETY.md",
    "ui/annotations.json", "ui/icon.png", "tests/fixtures/ui/fake_engine.py",
} | {f"tools/{name}" for name in RUNTIME_TOOLS}
# Host build sources (copied to a private staging tree; the repo's own bin/obj are never used).
HOST_SOURCE_PREFIXES = ("browser_host/", "ui/", "assets/input/")
# Any of these path segments anywhere in the package fails the audit.
FORBIDDEN_SEGMENTS = {"dev_assets", "game-assets", "game-assets-src", "resource_native", "webview2-profile",
                      "EBWebView", ".git", "node_modules", "__pycache__", "captures", "recordings"}
FORBIDDEN_SUFFIXES = {".webp", ".jsonl", ".har", ".pyc", ".mp4", ".sqlite", ".pdb"}
# Generated (not repository) files outside host/ and .venv/.
GENERATED_FILES = ({"MaaShinyColors.exe", "PACKAGE_MANIFEST.json", "UPDATE_FILES.json"}
                   | {f"{OCR_TARGET}/{name}" for name in OCR_FILES})

# Release artifacts (2026-11-27): every full zip lists its files ({path: sha256}) so an update knows what changed; an
# update zip carries only the changed files of one version step. Player data next to the exe is never part of either.
UPDATE_FILES = "UPDATE_FILES.json"      # full zip: {"version", "files": {path: sha256}}
UPDATE_PATCH = "UPDATE_PATCH.json"      # update zip: {"from", "to", "files": {path: sha256}, "removed": [path]}
USER_DATA_PREFIXES = ("dev_assets/", "ui/game-assets/", "host/ui/game-assets/")
PACKAGE_ROOT = "MaaShinyColors"
RELEASES = PRIVATE / "package-build" / "releases"                  # <version>/MaaShinyColors.zip for later patches
RESOURCES_PACK = PRIVATE / "package-build" / "resources-private.zip"  # the user's own resource overlay (never uploaded)


def log(message: str) -> None:
    print(f"[package] {message}", flush=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], cwd: Path | None = None, env: dict | None = None, timeout: int = 1800) -> str:
    log("$ " + " ".join(str(part) for part in command))
    result = subprocess.run([str(part) for part in command], cwd=cwd, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {command[0]}\n{result.stdout[-4000:]}\n"
                           f"{result.stderr[-4000:]}")
    return result.stdout


def repository_files() -> list[str]:
    """Non-ignored files of the working tree (tracked + untracked), posix paths that exist on disk."""
    output = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
                            capture_output=True, check=True).stdout.decode("utf-8")
    files = sorted({item for item in output.split("\0") if item})
    return [item for item in files if (ROOT / item).is_file()]


def is_forbidden(relative: str) -> str | None:
    path = PurePosixPath(relative.replace("\\", "/"))
    for part in path.parts:
        if part in FORBIDDEN_SEGMENTS:
            return f"forbidden segment {part!r}"
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return f"forbidden suffix {path.suffix!r}"
    if path.parts and path.parts[0] not in (".venv", "host"):
        if (any(part.startswith(".") for part in path.parts)
                or (path.suffix.lower() == ".md" and str(path) not in INCLUDE_FILES)):
            return "local workspace file"
    return None


def select_package_files(files: list[str]) -> list[str]:
    selected = [item for item in files if item in INCLUDE_FILES or item.startswith(INCLUDE_PREFIXES)]
    return [item for item in selected if is_forbidden(item) is None]


def select_host_sources(files: list[str]) -> list[str]:
    # bin/obj are gitignored already; filtered again so a stale local build output can never leak into the publish.
    return [item for item in files if item.startswith(HOST_SOURCE_PREFIXES) and is_forbidden(item) is None
            and not item.startswith(("browser_host/bin/", "browser_host/obj/"))
            and (not item.startswith("ui/fixtures/") or item == "ui/fixtures/schema.json")]


def checked_output(out: Path) -> Path:
    out = out.resolve()
    if not any(out != parent and out.is_relative_to(parent) for parent in ALLOWED_OUT_PARENTS):
        raise SystemExit(f"--out must be under dist/ or dev_assets/private/package-build/: {out}")
    return out


def fetch(url: str, target: Path, sha256: str) -> Path:
    if target.is_file() and sha256_file(target) == sha256:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    log(f"download {url}")
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "MaaShinyColors-package/1"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    actual = sha256_file(partial)
    if actual != sha256:
        partial.unlink()
        raise RuntimeError(f"sha256 mismatch for {url}: {actual}")
    partial.replace(target)
    return target


def authenticode(paths: list[Path]) -> dict[str, str]:
    """Status + signer of Authenticode signatures (PowerShell Get-AuthenticodeSignature)."""
    script = "$ErrorActionPreference = 'Stop'; " + "; ".join(f"$s = Get-AuthenticodeSignature -LiteralPath '{str(path).replace(chr(39), chr(39) * 2)}'; "
                       f"Write-Output ('{path.name}|' + $s.Status + '|' + $s.SignerCertificate.Subject)" for path in paths)
    shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    output = run([shell, "-NoProfile", "-NonInteractive", "-Command", script])
    result = {}
    for line in output.splitlines():
        if "|" in line:
            name, status, subject = (line.split("|", 2) + [""])[:3]
            result[name] = f"{status} {subject}".strip()
    return result


# ---------------------------------------------------------------------------------------------------------- build

def build_host(files: list[str], out: Path, cache: Path, edition: str = "release") -> dict:
    staging = cache / "host-src"
    checked_output(staging)
    if staging.exists():
        shutil.rmtree(staging)
    for item in select_host_sources(files):
        target = staging / item
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / item, target)
    sdk = run(["dotnet", "--version"]).strip()
    run(["dotnet", "publish", str(staging / HOST_PROJECT), "-c", "Release", "-r", "win-x64", "--self-contained", "true",
         "-p:PublishSingleFile=false", "-p:DebugType=none", "-p:SatelliteResourceLanguages=en",
         f"-p:RuntimeFrameworkVersion={DOTNET_RUNTIME_VERSION}", "-nologo", "-o", str(out / "host")], cwd=staging, timeout=3600)
    for leftover in (out / "host" / "ui" / "game-assets",):
        if leftover.exists():
            checked_output(leftover)
            shutil.rmtree(leftover)
    if not (out / "host" / HOST_EXE).is_file():
        raise RuntimeError("dotnet publish produced no host executable")
    return {"dotnet_sdk": sdk, "dotnet_runtime": DOTNET_RUNTIME_VERSION, "runtime_identifier": "win-x64",
            "self_contained": True, "edition": edition}


def build_python(out: Path, cache: Path) -> dict:
    scripts = out / ".venv" / "Scripts"
    embed = fetch(EMBED_URL, cache / EMBED_NAME, EMBED_SHA256)
    with zipfile.ZipFile(embed) as archive:
        archive.extractall(scripts)
    signatures = authenticode([scripts / "python.exe", scripts / f"python{PYTHON_TAG}.dll"])
    if not all(value.startswith("Valid") and "Python Software Foundation" in value for value in signatures.values()):
        raise RuntimeError(f"embedded Python signature check failed: {signatures}")
    # Embedded mode ignores PYTHONPATH and does not add the script folder; list the package root explicitly.
    (scripts / f"python{PYTHON_TAG}._pth").write_text(
        f"python{PYTHON_TAG}.zip\n.\nLib\\site-packages\n..\\..\nimport site\n", encoding="ascii")
    site_packages = scripts / "Lib" / "site-packages"
    wheelhouse = cache / "wheels"
    pins = {line.split("--hash=sha256:", 1)[1].strip() for line in RUNTIME_LOCK.read_text().splitlines()
            if "--hash=sha256:" in line}
    cached = {sha256_file(path) for path in wheelhouse.glob("*.whl")}
    sources = ["--no-index", "--find-links", str(wheelhouse)] if pins <= cached else []
    run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--require-hashes",
         "--no-deps", "--only-binary=:all:", "--platform", "win_amd64", "--python-version", PYTHON_TAG[0] + "." +
         PYTHON_TAG[1:], "--implementation", "cp", "--abi", f"cp{PYTHON_TAG}", "--target", str(site_packages),
         "--cache-dir", str(cache / "pip"), *sources, "-r", str(RUNTIME_LOCK)], timeout=3600)
    # Only bytecode caches are removed; numpy/scipy "tests" packages stay (numpy.testing imports numpy._core.tests).
    for folder in sorted(site_packages.rglob("__pycache__"), reverse=True):
        shutil.rmtree(folder, ignore_errors=True)
    shutil.rmtree(site_packages / "bin", ignore_errors=True)  # console-script shims point at the build interpreter
    system32 = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"
    vc = {}
    if all((system32 / name).is_file() for name in VC_RUNTIME):
        for name in VC_RUNTIME:
            shutil.copy2(system32 / name, scripts / name)
            vc[name] = sha256_file(scripts / name)
    else:
        log("WARNING: VC++ runtime not found in System32; the package then needs the VC++ redistributable")
    return {"python": PYTHON_VERSION, "embed_url": EMBED_URL, "embed_sha256": EMBED_SHA256,
            "embed_signatures": signatures, "lock": RUNTIME_LOCK.relative_to(ROOT).as_posix(),
            "lock_sha256": sha256_file(RUNTIME_LOCK), "vc_runtime_sha256": vc}


def build_ocr(out: Path, cache: Path) -> dict:
    target = out / OCR_TARGET
    target.mkdir(parents=True, exist_ok=True)
    sources = {}
    for name, digest in OCR_FILES.items():
        local = OCR_LOCAL / name
        if local.is_file() and sha256_file(local) == digest:
            source, sources[name] = local, "local verified copy (same pinned sha256)"
        else:
            url = OCR_URL.format(commit=OCR_COMMIT, name=name)
            source, sources[name] = fetch(url, cache / "ocr" / name, digest), url
        shutil.copy2(source, target / name)
    return {"repository": "MaaXYZ/MaaCommonAssets", "commit": OCR_COMMIT, "path": "OCR/ppocr_v6/small",
            "sha256": OCR_FILES, "sources": sources}


def build_launcher(out: Path) -> dict:
    if not CSC.is_file():
        raise RuntimeError(f".NET Framework compiler not found: {CSC}")
    run([str(CSC), "/nologo", "/target:winexe", "/platform:anycpu", "/optimize+", "/r:System.Windows.Forms.dll",
         f"/win32icon:{ROOT / 'browser_host' / 'app.ico'}",
         f"/out:{out / 'MaaShinyColors.exe'}", str(LAUNCHER_SOURCE)])
    return {"source": LAUNCHER_SOURCE.relative_to(ROOT).as_posix(), "sha256": sha256_file(out / "MaaShinyColors.exe")}


def recognition_records(bundle: Path, pipeline_dir: Path, *, exact: bool = False) -> list[dict]:
    """Validate only required cropped PNGs; never copy private metadata or source captures."""
    from tools.build_native_resource import CANVAS, pipeline_templates, png_size, roi_ok
    required = {"native/" + name: rois for name, rois in pipeline_templates(pipeline_dir).items()}
    if not required:
        raise ValueError("recognition pipeline has no required templates")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or manifest.get("canvas") != list(CANVAS) or manifest.get("template_prefix") != "native/":
        raise ValueError("recognition manifest canvas/prefix mismatch")
    if exact and set(manifest) != {"version", "canvas", "template_prefix", "templates"}:
        raise ValueError("recognition manifest contains unexpected metadata")
    entries = {}
    for record in manifest.get("templates", []):
        name = record.get("name")
        if not isinstance(name, str) or name in entries:
            raise ValueError("recognition manifest has an invalid or duplicate name")
        entries[name] = record
    if required.keys() - entries.keys():
        raise ValueError("missing required recognition templates: " + ", ".join(sorted(required.keys() - entries.keys())[:5]))
    if exact and entries.keys() != required.keys():
        raise ValueError("recognition bundle contains unneeded templates")
    clean = []
    for name, rois in sorted(required.items()):
        path = PurePosixPath(name)
        if "\\" in name or path.is_absolute() or ".." in path.parts or path.suffix != ".png" or path.parts[0] != "native":
            raise ValueError(f"invalid recognition template name: {name}")
        record = entries[name]
        if exact and set(record) != {"name", "sha256", "size"}:
            raise ValueError(f"recognition template contains unexpected metadata: {name}")
        source = (bundle / "image" / name).resolve()
        if not source.is_relative_to((bundle / "image").resolve()) or not source.is_file():
            raise ValueError(f"missing recognition template: {name}")
        if sha256_file(source) != record.get("sha256"):
            raise ValueError(f"recognition template hash mismatch: {name}")
        size = png_size(source)
        if list(size) != record.get("size") or not (0 < size[0] <= CANVAS[0] and 0 < size[1] <= CANVAS[1]):
            raise ValueError(f"recognition template size mismatch: {name}")
        if any(not roi_ok(roi) or size[0] > roi[2] or size[1] > roi[3] for roi in rois):
            raise ValueError(f"recognition template does not fit its ROI: {name}")
        clean.append({"name": name, "sha256": record["sha256"], "size": list(size)})
    return clean


@contextmanager
def recognition_source(source: Path, pipeline_dir: Path):
    """Read only required crops from an exported bundle or an existing portable zip."""
    if source.is_dir():
        yield source / RECOGNITION_TARGET if (source / RECOGNITION_TARGET).is_dir() else source
        return
    from tools.build_native_resource import pipeline_templates
    with zipfile.ZipFile(source) as archive, tempfile.TemporaryDirectory() as folder:
        manifests = [name for name in archive.namelist()
                     if name == f"{RECOGNITION_TARGET}/manifest.json"
                     or name.endswith(f"/{RECOGNITION_TARGET}/manifest.json")]
        if len(manifests) != 1:
            raise ValueError("resource zip must contain exactly one assets/resource/recognition/manifest.json")
        prefix = manifests[0][:-len("manifest.json")]
        bundle = Path(folder)
        (bundle / "manifest.json").write_bytes(archive.read(manifests[0]))
        for name in pipeline_templates(pipeline_dir):
            # Pipeline template names, rather than arbitrary archive entries, choose the extracted files.
            relative = PurePosixPath("native") / name
            if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative):
                raise ValueError(f"invalid pipeline template name: {name}")
            target = bundle / "image" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            member = prefix + "image/" + relative.as_posix()
            if archive.namelist().count(member) != 1:
                raise ValueError(f"missing or duplicate recognition template: {relative}")
            target.write_bytes(archive.read(member))
        yield bundle


def build_recognition(out: Path, source: Path | None = None, *, pipeline_dir: Path | None = None) -> dict:
    from tools.build_native_resource import CANVAS
    source = source or PRIVATE / "resource_native"
    pipeline_dir = pipeline_dir or out / "assets/resource/base/pipeline"
    target = out / RECOGNITION_TARGET
    with recognition_source(source, pipeline_dir) as bundle:
        records = recognition_records(bundle, pipeline_dir)
        for record in records:
            dest = target / "image" / record["name"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundle / "image" / record["name"], dest)
    clean = {"version": 1, "canvas": list(CANVAS), "template_prefix": "native/", "templates": records}
    manifest = target / "manifest.json"
    manifest.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": str(RECOGNITION_TARGET), "templates": len(records), "manifest_sha256": sha256_file(manifest)}


def export_recognition(args) -> int:
    """One-time CI bootstrap: export crops, never the private directory or source screenshots."""
    archive = checked_output(args.out)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        staging = Path(folder)
        result = build_recognition(staging, args.recognition_source,
                                   pipeline_dir=ROOT / "assets/resource/base/pipeline")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
            for path in sorted((staging / RECOGNITION_TARGET).rglob("*")):
                if path.is_file():
                    handle.write(path, path.relative_to(staging).as_posix())
    log(f"recognition resources {archive}: {result['templates']} required templates")
    return 0


def git_version() -> dict:
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"commit": git("rev-parse", "--short", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(git("status", "--porcelain"))}


def tree_size(folder: Path) -> tuple[int, int]:
    count = size = 0
    for path in folder.rglob("*"):
        if path.is_file():
            count += 1
            size += path.stat().st_size
    return count, size


def build(args) -> int:
    out = checked_output(args.out)
    cache = args.cache.resolve()
    started = time.monotonic()
    timings = {}
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    files = repository_files()
    selected = select_package_files(files)
    for item in selected:
        target = out / item
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / item, target)
    timings["copy_sources_s"] = round(time.monotonic() - started, 1)
    log(f"copied {len(selected)} repository files")
    manifest = {"name": "MaaShinyColors", "built_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                "source": git_version(), "repository_files": len(selected), "readme_source": "README.md",
                "edition": args.edition}
    # HEAD alone does not identify a beta built from an uncommitted working tree.
    source_files = sorted(set(selected + select_host_sources(files)))
    manifest["source_sha256"] = {item: sha256_file(ROOT / item) for item in source_files}
    for label, step in (("recognition", lambda: build_recognition(out, args.recognition_source)),
                        ("ocr", lambda: build_ocr(out, cache)), ("python", lambda: build_python(out, cache)),
                        ("host", lambda: build_host(files, out, cache, args.edition)),
                        ("launcher", lambda: build_launcher(out))):
        if label == "host" and args.skip_host:
            continue
        begun = time.monotonic()
        manifest[label] = step()
        timings[f"{label}_s"] = round(time.monotonic() - begun, 1)
    manifest["timings"] = timings
    (out / "PACKAGE_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                                encoding="utf-8")
    problems = audit(out)
    if problems:
        for problem in problems[:50]:
            log(f"AUDIT FAIL {problem}")
        return 1
    count, size = tree_size(out)
    log(f"package {out}: {count} files, {size / 2**20:.1f} MiB, {time.monotonic() - started:.0f} s")
    if args.zip:
        write_archive(out)
    return 0


def write_archive(package: Path) -> Path:
    """Repack after smoke without shipping the local data created by those checks."""
    package = checked_output(package)
    problems = audit(package, runtime=True)
    if problems:
        raise RuntimeError(f"package audit failed: {problems[:5]}")
    archive = package.parent / f"{package.name}.zip"
    begun = time.monotonic()
    shipped = []
    for path in sorted(package.rglob("*")):
        if path.is_file():
            relative = path.relative_to(package).as_posix()
            if (relative.startswith("dev_assets/") or "__pycache__" in relative.split("/") or relative.endswith(".pyc")
                    or relative == UPDATE_FILES):
                continue
            shipped.append(relative)
    listing = {"version": folder_version(package), "files": {item: sha256_file(package / item) for item in shipped}}
    (package / UPDATE_FILES).write_text(json.dumps(listing, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
        for relative in shipped + [UPDATE_FILES]:
            handle.write(package / relative, PurePosixPath(package.name, relative).as_posix())
    log(f"zip {archive}: {archive.stat().st_size / 2**20:.1f} MiB, {time.monotonic() - begun:.0f} s")
    return archive


# ---------------------------------------------------------------------------------------------- release artifacts

def interface_version(text: str) -> str | None:
    """The "version" of assets/interface.json (JSONC: comments and trailing commas, so not json.loads)."""
    match = re.search(r'"version"\s*:\s*"([^"]+)"', text)
    return match.group(1) if match else None


def folder_version(package: Path) -> str | None:
    try:
        return interface_version((package / "assets" / "interface.json").read_text(encoding="utf-8"))
    except OSError:
        return None


def version_key(text: str) -> tuple:
    core = text.strip().lstrip("vV").split("+", 1)[0]
    numbers, _, suffix = core.partition("-")
    return tuple(int(p) if p.isdigit() else 0 for p in numbers.split(".")) + ((1,) if not suffix else (0, suffix))


def is_user_data(relative: str) -> bool:
    return relative.startswith(USER_DATA_PREFIXES)


def _zip_entries(handle: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """{path below the single top folder: entry}; folders skipped, backslash names (Compress-Archive) accepted."""
    entries, roots = {}, set()
    for info in handle.infolist():
        name = info.filename.replace("\\", "/")
        if name.endswith("/"):
            continue
        root, _, relative = name.partition("/")
        if not relative or ".." in relative.split("/") or relative.startswith("/"):
            raise ValueError(f"unexpected zip entry {info.filename!r}")
        roots.add(root)
        entries[relative] = info
    if len(roots) != 1:
        raise ValueError(f"zip must have one top folder, got {sorted(roots)[:5]}")
    return entries


def _entry_sha(handle: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with handle.open(info) as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def zip_listing(archive: Path) -> tuple[str, dict[str, str]]:
    """(version, {path: sha256}) of a full package zip, hashed from its contents (old zips have no UPDATE_FILES)."""
    with zipfile.ZipFile(archive) as handle:
        entries = _zip_entries(handle)
        version = interface_version(handle.read(entries["assets/interface.json"]).decode("utf-8"))
        if version is None:
            raise ValueError(f"{archive.name}: no version in assets/interface.json")
        return version, {path: _entry_sha(handle, info) for path, info in entries.items()}


def make_patch(old: Path, new: Path, out_dir: Path) -> Path:
    """MaaShinyColors-update-<old>-to-<new>.zip: the files that differ, UPDATE_PATCH.json and nothing of the player's."""
    old_version, old_files = zip_listing(old)
    new_version, new_files = zip_listing(new)
    if version_key(new_version) <= version_key(old_version):
        raise ValueError(f"update must go forward: {old_version} -> {new_version}")
    changed = sorted(path for path, sha in new_files.items() if old_files.get(path) != sha)
    removed = sorted(path for path in old_files if path not in new_files and path != UPDATE_FILES)
    bad = [path for path in changed + removed if is_user_data(path)]
    if bad:
        raise ValueError(f"player data in a release zip: {bad[:5]}")
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{PACKAGE_ROOT}-update-{old_version}-to-{new_version}.zip"
    patch = {"from": old_version, "to": new_version, "files": {path: new_files[path] for path in changed},
             "removed": removed}
    with zipfile.ZipFile(new) as source, zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
        entries = _zip_entries(source)
        for path in changed:
            handle.writestr(f"{PACKAGE_ROOT}/{path}", source.read(entries[path]))
        handle.writestr(f"{PACKAGE_ROOT}/{UPDATE_PATCH}", json.dumps(patch, ensure_ascii=False, indent=1) + "\n")
    log(f"update {archive.name}: {len(changed)} changed, {len(removed)} removed, {archive.stat().st_size / 2**20:.1f} MiB")
    return archive


def with_resources(release: Path, resources: Path, out: Path) -> Path:
    """The release zip plus the user's private resource overlay (player-data paths only; never uploaded)."""
    with zipfile.ZipFile(release) as base, zipfile.ZipFile(resources) as extra:
        program, overlay = _zip_entries(base), _zip_entries(extra)
        bad = [path for path in overlay if not is_user_data(path)]
        if bad:
            raise ValueError(f"resource pack outside player data: {bad[:5]}")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
            for path, info in program.items():
                handle.writestr(f"{PACKAGE_ROOT}/{path}", base.read(info))
            for path, info in overlay.items():
                handle.writestr(f"{PACKAGE_ROOT}/{path}", extra.read(info))
    log(f"with resources {out.name}: {len(program)} + {len(overlay)} files, {out.stat().st_size / 2**20:.1f} MiB")
    return out


def resources_from(with_resources_zip: Path, release: Path, out: Path) -> Path:
    """Recover the resource overlay from an older with-resources zip: its player-data files not in the release."""
    with zipfile.ZipFile(with_resources_zip) as full, zipfile.ZipFile(release) as base:
        program, entries = _zip_entries(base), _zip_entries(full)
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
            kept = 0
            for path, info in entries.items():
                if is_user_data(path) and path not in program:
                    handle.writestr(f"{PACKAGE_ROOT}/{path}", full.read(info))
                    kept += 1
    log(f"resource pack {out}: {kept} files")
    return out


def release(args) -> int:
    """The three artifacts of one version in dist/release-<version>/: full zip and update zip (GitHub), full zip with
    the private resources (local only). The full zip is kept under package-build/releases for the next update."""
    full = args.zip.resolve()
    version, _ = zip_listing(full)
    out = checked_output(args.out or ROOT / "dist" / f"release-{version}")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copy2(full, out / f"{PACKAGE_ROOT}.zip")
    previous = args.previous
    if previous is None and RELEASES.is_dir():
        older = [p for p in RELEASES.iterdir() if (p / f"{PACKAGE_ROOT}.zip").is_file()
                 and version_key(p.name) < version_key(version)]
        previous = max(older, key=lambda p: version_key(p.name)) / f"{PACKAGE_ROOT}.zip" if older else None
    upload = [out / f"{PACKAGE_ROOT}.zip"]
    if previous is not None:
        upload.append(make_patch(previous, full, out))
    else:
        log("no previous release zip: no update zip")
    resources = args.resources or RESOURCES_PACK
    local = with_resources(full, resources, out / f"{PACKAGE_ROOT}-with-resources.zip") if resources.is_file() else None
    if local is None:
        log(f"no resource pack at {resources}: no with-resources zip")
    kept = RELEASES / version
    kept.mkdir(parents=True, exist_ok=True)
    shutil.copy2(full, kept / f"{PACKAGE_ROOT}.zip")
    (out / "SHA256SUMS.txt").write_text("".join(f"{sha256_file(p)}  {p.name}\n" for p in upload), encoding="utf-8")
    log("upload to GitHub: " + ", ".join(p.name for p in upload) + (f"; local only: {local.name}" if local else ""))
    return 0


# ---------------------------------------------------------------------------------------------------------- audit

def audit(package: Path, files: list[str] | None = None, runtime: bool = False) -> list[str]:
    """Problems that make a package unshippable; empty list = clean. ``build`` runs it before the zip and before
    anything executes from the folder. ``runtime=True`` (smoke) tolerates what running the package creates: the
    root ``dev_assets/`` data folder and Python bytecode caches."""
    package = package.resolve()
    files = repository_files() if files is None else files
    allowed = set(select_package_files(files))
    allowed = {p for p in allowed if not p.startswith(f"{RECOGNITION_TARGET}/")}
    ui_allowed = {item[len("ui/"):] for item in select_host_sources(files) if item.startswith("ui/")}
    problems = []
    try:
        bundle = package / RECOGNITION_TARGET
        if not bundle.resolve().is_relative_to(package):
            raise ValueError("recognition bundle escapes the package")
        records = recognition_records(bundle, package / "assets/resource/base/pipeline", exact=True)
        allowed.add(f"{RECOGNITION_TARGET}/manifest.json")
        allowed.update(f"{RECOGNITION_TARGET}/image/{r['name']}" for r in records)
    except (OSError, ValueError, KeyError, TypeError) as error:
        problems.append(f"{RECOGNITION_TARGET}: {error}")
    for path in package.rglob("*"):
        relative = path.relative_to(package).as_posix()
        if runtime and (relative == "dev_assets" or relative.startswith("dev_assets/") or "__pycache__" in
                        relative.split("/") or relative.endswith(".pyc")):
            continue
        reason = is_forbidden(relative)
        if reason:
            problems.append(f"{relative}: {reason}")
            continue
        if not path.is_file():
            continue
        top = relative.split("/", 1)[0]
        if top == ".venv":
            continue
        if top == "host":
            if relative.startswith("host/ui/") and relative[len("host/ui/"):] not in ui_allowed:
                problems.append(f"{relative}: host ui file is not a repository ui file")
            continue
        if relative not in allowed and relative not in GENERATED_FILES:
            problems.append(f"{relative}: not a selected repository file")
    for required in ("MaaShinyColors.exe", f"host/{HOST_EXE}", ".venv/Scripts/python.exe", "project_policy.json",
                     "assets/interface.json", "tools/ui_engine.py", "PACKAGE_MANIFEST.json"):
        if not (package / required).is_file():
            problems.append(f"{required}: missing")
    return problems


def audit_command(args) -> int:
    problems = audit(args.package)
    for problem in problems:
        log(f"AUDIT FAIL {problem}")
    log("audit clean" if not problems else f"audit: {len(problems)} problem(s)")
    return 1 if problems else 0


# ---------------------------------------------------------------------------------------------------------- smoke

def smoke(args) -> int:
    """Offline smoke tests from the packaged location. Never opens the game URL. Writes only package/dev_assets."""
    package = args.package.resolve()
    pre = audit(package, runtime=True)
    if pre:
        log(f"refusing smoke: audit has {len(pre)} problem(s): {pre[:5]}")
        return 1
    python = package / ".venv" / "Scripts" / "python.exe"
    launcher = package / "MaaShinyColors.exe"
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("PYTHON", "VIRTUAL_ENV"))}
    results = []

    def step(name, command, ok_codes=(0,), timeout=900, check=None):
        begun = time.monotonic()
        try:
            completed = subprocess.run([str(part) for part in command], cwd=package, env=env, capture_output=True,
                                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
            passed = completed.returncode in ok_codes
            detail = (completed.stdout + completed.stderr).strip()[-1500:]
            if passed and check is not None:
                passed, detail = check(completed)
            code = completed.returncode
        except subprocess.TimeoutExpired:
            passed, code, detail = False, None, "timeout"
        results.append({"step": name, "passed": passed, "exit": code, "seconds": round(time.monotonic() - begun, 1),
                        "detail": detail[-1500:]})
        log(f"{'PASS' if passed else 'FAIL'} {name} ({results[-1]['seconds']} s, exit {code})")

    imports = ("import sys, maa, numpy, scipy, scipy.optimize, PIL; from maa.library import Library; "
               "from maa.resource import Resource; import agent.core.resource_bundles as rb; "
               "print(sys.version.split()[0], sys.executable); print('maa', Library.version()); "
               "print('ocr_dir', rb.ocr_model_dir(), 'base_ocr', all((rb.BASE_OCR_MODEL / n).is_file() for n in rb.OCR_FILES))")
    step("python_imports_maa", [python, "-X", "utf8", "-c", imports])

    def resource_check(completed):
        return ("loaded True" in completed.stdout, completed.stdout.strip()[-800:])
    load = ("from maa.resource import Resource; import agent.core.resource_bundles as rb; r = Resource(); "
            "bundles=rb.bundle_paths(include_private=False); "
            "assert rb.PACKAGED_RECOGNITION_BUNDLE in bundles; "
            "[r.post_bundle(str(p)).wait() for p in bundles]; print('loaded', r.loaded)")
    step("maa_resource_load_complete_bundle", [python, "-X", "utf8", "-c", load], check=resource_check)
    step("check_project", [python, "-X", "utf8", "tools/check_project.py"])
    def launcher_ready(completed):
        try:
            report = json.loads((package / "dev_assets/private/launcher-check.json").read_text(encoding="utf-8"))
            passed = report.get("default_input_enabled") is True and report.get("recognition_ready") is True
            return passed, json.dumps({k: report.get(k) for k in ("passed", "default_input_enabled", "recognition_ready")})
        except (OSError, ValueError, TypeError) as error:
            return False, f"launcher readiness report unavailable: {error}"
    step("launcher_check", [launcher, "--check"], check=launcher_ready)
    step("launcher_refuses_live_flags", [launcher, "--enable-pipeline-input"], ok_codes=(2,))
    step("ui_engine_offline_replay", [python, "-X", "utf8", "-c", _ENGINE_SMOKE], ok_codes=(0,))
    if not args.skip_host:
        step("host_self_test_ui", [launcher, "--self-test-ui"], timeout=900)
        step("host_self_test_ui_dock", [launcher, "--self-test-ui-dock"], timeout=900)
    report = package / "dev_assets" / "private" / "package-smoke" / f"smoke-{time.strftime('%Y%m%d-%H%M%S')}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"package": str(package), "results": results}, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    failed = [item["step"] for item in results if not item["passed"]]
    log(f"smoke report {report}; failed: {failed or 'none'}")
    return 1 if failed else 0


# Runs the real tools/ui_engine.py (profile -> run_tasks, read-only) against one synthetic offline frame inside the
# package's private folder. Pass = the engine starts, converts the profile, loads Maa and finishes with a summary;
# A blank synthetic frame may fail recognition; missing runtime templates fail the audit before this smoke.
_ENGINE_SMOKE = r"""
import json, subprocess, sys, uuid
from pathlib import Path
from PIL import Image
root = Path.cwd()
run = root / "dev_assets" / "private" / "package-smoke" / ("engine-" + uuid.uuid4().hex[:8])
frames = run / "frames"; frames.mkdir(parents=True)
Image.new("RGB", (1280, 720), (24, 24, 24)).save(frames / "000001.png")
profile = {"schema_version": 1, "preset": None, "tasks": [{"name": "Startup", "enabled": True}], "task_timeout_s": 60}
(run / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
argv = [sys.executable, "-X", "utf8", str(root / "tools" / "ui_engine.py"), "--profile", str(run / "profile.json"),
        "--events", str(run / "events.jsonl"), "--stop-file", str(run / "stop.flag"), "--summary", str(run / "summary.json"),
        "--offline-frames", str(frames)]
done = subprocess.run(argv, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
events = (run / "events.jsonl").read_text(encoding="utf-8").splitlines() if (run / "events.jsonl").exists() else []
summary = json.loads((run / "summary.json").read_text(encoding="utf-8")) if (run / "summary.json").exists() else None
print("engine exit", done.returncode, "events", len(events), "summary", bool(summary))
print((done.stdout + done.stderr)[-1200:])
if summary: print(json.dumps(summary, ensure_ascii=False)[:1200])
sys.exit(0 if done.returncode in (0, 1) and summary is not None and events else 1)
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MaaShinyColors 便携包构建 / 审计 / 冒烟")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", type=Path, default=DEFAULT_OUT)
    b.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    b.add_argument("--zip", action="store_true", help="also write <out>.zip next to the folder")
    b.add_argument("--edition", choices=("release",), default="release")
    b.add_argument("--skip-host", action="store_true", help="skip dotnet publish (Python-side checks only)")
    b.add_argument("--recognition-source", type=Path,
                   help="cropped resource directory, exported resources zip, or existing portable zip")
    r = sub.add_parser("resources", help="export only required recognition crops for one-time CI setup")
    r.add_argument("--recognition-source", type=Path)
    r.add_argument("--out", type=Path, default=PRIVATE / "package-build" / "recognition-resources.zip")
    a = sub.add_parser("audit")
    a.add_argument("--package", type=Path, default=DEFAULT_OUT)
    s = sub.add_parser("smoke")
    s.add_argument("--package", type=Path, default=DEFAULT_OUT)
    s.add_argument("--skip-host", action="store_true")
    z = sub.add_parser("archive", help="zip an audited package, excluding local runtime data")
    z.add_argument("--package", type=Path, default=DEFAULT_OUT)
    rel = sub.add_parser("release", help="full zip + update zip (GitHub) + with-resources zip (local) in dist/release-<v>")
    rel.add_argument("--zip", type=Path, default=DEFAULT_OUT.parent / f"{PACKAGE_ROOT}.zip")
    rel.add_argument("--previous", type=Path, help="previous full zip (default: newest kept release below this one)")
    rel.add_argument("--resources", type=Path, help=f"private resource pack (default {RESOURCES_PACK})")
    rel.add_argument("--out", type=Path)
    pack = sub.add_parser("resources-from", help="recover the private resource pack from an old with-resources zip")
    pack.add_argument("--with-resources", type=Path, required=True)
    pack.add_argument("--release", type=Path, required=True)
    pack.add_argument("--out", type=Path, default=RESOURCES_PACK)
    keep = sub.add_parser("keep-release", help="keep a full zip as the base of later update zips")
    keep.add_argument("--zip", type=Path, required=True)
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        raise SystemExit("the portable package is Windows x64 only")
    if args.command == "archive":
        write_archive(args.package)
        return 0
    if args.command == "resources-from":
        resources_from(args.with_resources, args.release, args.out)
        return 0
    if args.command == "keep-release":
        version, _ = zip_listing(args.zip)
        (RELEASES / version).mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.zip, RELEASES / version / f"{PACKAGE_ROOT}.zip")
        log(f"kept {version}")
        return 0
    return {"build": build, "audit": audit_command, "smoke": smoke, "resources": export_recognition,
            "release": release}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
