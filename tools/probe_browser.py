#!/usr/bin/env python3
"""Read-only MaaFramework browser window discovery.

This tool only enumerates desktop window metadata. It does not create a
controller, capture pixels, focus a window, or send input.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Pattern


DEFAULT_CLASS_REGEX = (
    r"^(?:Chrome_WidgetWin_1|MozillaWindowClass|WindowsForms10\..*)$"
)
DEFAULT_TITLE_REGEX = (
    r"(?:シャイニーカラーズ|シャニマス|shinycolors|Shiny Colors)"
)


@dataclass(frozen=True)
class WindowRecord:
    hwnd: int
    class_name: str
    title: str
    client_width: int = 0
    client_height: int = 0
    visible: bool = True
    minimized: bool = False

    @property
    def hwnd_hex(self) -> str:
        return f"0x{self.hwnd:08X}"


@dataclass(frozen=True)
class ProbeMatch:
    window: WindowRecord
    target_match: bool
    connection_ready: bool


def normalize_hwnd(value: object) -> int:
    if isinstance(value, int):
        return value
    pointer_value = getattr(value, "value", None)
    if isinstance(pointer_value, int):
        return pointer_value
    raise TypeError(f"Unsupported window handle: {value!r}")


def compile_patterns(
    class_regex: str = DEFAULT_CLASS_REGEX,
    title_regex: str = DEFAULT_TITLE_REGEX,
) -> tuple[Pattern[str], Pattern[str]]:
    return (
        re.compile(class_regex),
        re.compile(title_regex, re.IGNORECASE),
    )


def select_browser_windows(
    windows: Iterable[WindowRecord],
    class_pattern: Pattern[str],
    title_pattern: Pattern[str],
) -> list[ProbeMatch]:
    matches: list[ProbeMatch] = []
    for window in windows:
        if not class_pattern.search(window.class_name):
            continue
        if not window.title:
            continue
        is_target = bool(title_pattern.search(window.title))
        ready = (
            is_target
            and window.visible
            and not window.minimized
            and window.client_width > 0
            and window.client_height > 0
        )
        matches.append(ProbeMatch(window, is_target, ready))
    return sorted(
        matches,
        key=lambda item: (
            not item.target_match,
            not item.connection_ready,
            item.window.title.casefold(),
        ),
    )


def redact_title(title: str, target_match: bool, show_titles: bool) -> str:
    if target_match or show_titles:
        return title
    return f"<已脱敏：{len(title)} 字符>"


def _window_metadata(hwnd: int) -> tuple[int, int, bool, bool]:
    if os.name != "nt":
        return 0, 0, True, False

    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL

    rect = wintypes.RECT()
    width = 0
    height = 0
    if user32.GetClientRect(hwnd, ctypes.byref(rect)):
        width = max(0, rect.right - rect.left)
        height = max(0, rect.bottom - rect.top)
    return (
        width,
        height,
        bool(user32.IsWindowVisible(hwnd)),
        bool(user32.IsIconic(hwnd)),
    )


def discover_with_maa(
    library_dir: Path | None = None,
) -> tuple[str, list[WindowRecord]]:
    """Load MaaFramework and enumerate windows through MaaToolkit."""

    try:
        import maa
        from maa.library import Library
        from maa.toolkit import Toolkit
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "找不到 maafw Python 包；请使用 .venv\\Scripts\\python.exe 运行，"
            "或先执行 scripts\\bootstrap.ps1。"
        ) from exc

    if library_dir is None:
        library_dir = Path(maa.__file__).resolve().parent / "bin"

    Library.open(library_dir)
    framework_version = Library.version()
    records: list[WindowRecord] = []
    for window in Toolkit.find_desktop_windows():
        hwnd = normalize_hwnd(window.hwnd)
        width, height, visible, minimized = _window_metadata(hwnd)
        records.append(
            WindowRecord(
                hwnd=hwnd,
                class_name=window.class_name,
                title=window.window_name,
                client_width=width,
                client_height=height,
                visible=visible,
                minimized=minimized,
            )
        )
    return framework_version, records


def report_payload(
    framework_version: str,
    matches: Iterable[ProbeMatch],
    show_titles: bool,
    enumerated_window_count: int = 0,
) -> dict[str, Any]:
    match_list = list(matches)
    windows: list[dict[str, Any]] = []
    for item in match_list:
        window_payload = asdict(item.window)
        window_payload["hwnd_hex"] = item.window.hwnd_hex
        window_payload["title"] = redact_title(
            item.window.title,
            item.target_match,
            show_titles,
        )
        window_payload["target_match"] = item.target_match
        window_payload["connection_ready"] = item.connection_ready
        windows.append(window_payload)
    return {
        "framework_version": framework_version,
        "enumerated_window_count": enumerated_window_count,
        "browser_candidate_count": len(match_list),
        "target_match_count": sum(item.target_match for item in match_list),
        "connection_ready_count": sum(
            item.connection_ready for item in match_list
        ),
        "windows": windows,
        "safety": {
            "controller_created": False,
            "screenshot_captured": False,
            "input_sent": False,
        },
    }


def print_human(payload: dict[str, Any]) -> None:
    print(f"MaaFramework: {payload['framework_version']}")
    print(
        f"顶层窗口总数: {payload['enumerated_window_count']}；"
        "浏览器候选: "
        f"{payload['browser_candidate_count']}；"
        f"游戏标题匹配: {payload['target_match_count']}；"
        f"当前可连接: {payload['connection_ready_count']}"
    )
    for window in payload["windows"]:
        size = f"{window['client_width']}x{window['client_height']}"
        flags: list[str] = []
        if window["target_match"]:
            flags.append("目标")
        if window["connection_ready"]:
            flags.append("可连接")
        if window["minimized"]:
            flags.append("已最小化")
        flag_text = f" [{' / '.join(flags)}]" if flags else ""
        print(
            f"- {window['hwnd_hex']} {window['class_name']} {size}"
            f"{flag_text} {window['title']}"
        )

    safety = payload["safety"]
    print(
        "安全边界: "
        f"controller={safety['controller_created']}, "
        f"screenshot={safety['screenshot_captured']}, "
        f"input={safety['input_sent']}"
    )
    if payload["target_match_count"] == 0:
        print(
            "结论: MaaToolkit 可运行，但未发现标题匹配的游戏窗口。"
            "请由你手动打开并保持游戏窗口非最小化后重试。"
        )
    elif payload["connection_ready_count"] == 0:
        print(
            "结论: 已识别游戏窗口，但窗口不可见、最小化或客户区尺寸为零。"
        )
    else:
        print(
            "结论: 已取得可供 Win32Controller 使用的窗口句柄；"
            "本次未创建控制器。"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "通过 MaaToolkit 只读枚举浏览器窗口；"
            "不创建控制器、不截图、不聚焦、不发送输入。"
        )
    )
    parser.add_argument(
        "--class-regex",
        default=DEFAULT_CLASS_REGEX,
        help="浏览器顶层窗口类正则。",
    )
    parser.add_argument(
        "--title-regex",
        default=DEFAULT_TITLE_REGEX,
        help="目标游戏窗口标题正则（忽略大小写）。",
    )
    parser.add_argument(
        "--show-titles",
        action="store_true",
        help="显示非目标浏览器窗口标题；默认脱敏。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出结果。",
    )
    parser.add_argument(
        "--require-target",
        action="store_true",
        help="若没有当前可连接的目标窗口，则返回非零退出码。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        class_pattern, title_pattern = compile_patterns(
            args.class_regex,
            args.title_regex,
        )
        version, windows = discover_with_maa()
    except (OSError, RuntimeError, TypeError, re.error) as exc:
        print(f"ERROR: 浏览器探测失败：{exc}", file=sys.stderr)
        return 2

    matches = select_browser_windows(windows, class_pattern, title_pattern)
    payload = report_payload(
        version,
        matches,
        args.show_titles,
        enumerated_window_count=len(windows),
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_human(payload)

    if args.require_target and payload["connection_ready_count"] == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
