"""Default-off supervised exploration grid and jewel/paid guard (监督探索).

Mirrors ``browser_host/ProduceExploreGrid.cs``: an 18x32 grid of 40x40 px cells on the 1280x720 canvas, ids
``explore-rRRcCC``, family ``produce-explore``, never consuming. The grid is generated, not stored in
``assets/input/pipeline_actions.json``, so that table, its SHA and its disjointness check are unaffected. Requests carry
only a cell id; the host picks the point (20% margin) itself. Pure functions only — no network, no Maa import.
"""
from __future__ import annotations

import hashlib
import math
import re
import secrets
import unicodedata

FAMILY = "produce-explore"
VERSION = "produce-explore-grid.v1"
ID_PREFIX = "explore-"
CELL = 40
ROWS = 18
COLS = 32
MARGIN_PERCENT = 20
DESCRIPTOR = (f"SC.ProduceExploreGrid.v1\ncanvas=1280x720\ncell={CELL}\nrows={ROWS}\ncols={COLS}\n"
              f"margin_percent={MARGIN_PERCENT}\nconsuming=false\nfamily=produce-explore\nid=explore-rRRcCC\n")
GRID_SHA256 = hashlib.sha256(DESCRIPTOR.encode("utf-8")).hexdigest()
PIPELINE_COMMAND = "pipeline-click"
MAX_FRAME_AGE_MS = 4000  # host binding allows 5000 ms; leave room for the host's own interval wait

# Fail-closed screen guard: any OCR text containing one of these (after NFKC + whitespace removal) refuses the click.
# Jewels / paid currency / purchases / shop / exchange / gacha are never explored; stamina items are allowed.
# 交换 (simplified) is how PP-OCR v6 small reads 交換 on the shop exchange dialog (observed on private samples).
GUARD_WORDS = ("ジュエル", "有償", "課金", "購入", "ショップ", "交換", "交换", "ガシャ", "ガチャ")
# Fixed in-game phrases that contain a guard word but never spend: the Collabo Fes live skill-slot hint
# 「スライドで交換あと3回」 (2026-11-18 live). Removed from the text before matching; the rest of the text is still checked.
BENIGN_PHRASES = ("スライドで交換", "スライドで交换")

_CELL_RE = re.compile(r"r([0-9]{2})c([0-9]{2})")
_ID_RE = re.compile(r"explore-r([0-9]{2})c([0-9]{2})")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def id_of(row, col):
    _require(type(row) is int and type(col) is int and 0 <= row < ROWS and 0 <= col < COLS, "探索单元越界")
    return f"{ID_PREFIX}r{row:02d}c{col:02d}"


def parse_id(action):
    """Strict ``explore-rRRcCC`` parse; returns (row, col) or raises."""
    match = _ID_RE.fullmatch(action) if isinstance(action, str) else None
    _require(match is not None, "探索单元 id 无效")
    row, col = int(match.group(1)), int(match.group(2))
    _require(row < ROWS and col < COLS, "探索单元越界")
    return row, col


def is_cell(action):
    try:
        parse_id(action)
        return True
    except ValueError:
        return False


def action_for_cell(cell):
    """``rRRcCC`` (the CLI spelling) -> ``explore-rRRcCC``."""
    match = _CELL_RE.fullmatch(cell) if isinstance(cell, str) else None
    _require(match is not None, "单元格式必须为 rRRcCC（两位行号、两位列号）")
    return id_of(int(match.group(1)), int(match.group(2)))


def all_ids():
    return [id_of(r, c) for r in range(ROWS) for c in range(COLS)]


def rect(action):
    row, col = parse_id(action)
    return (col * CELL, row * CELL, CELL, CELL)


def inner_rect(action):
    x, y, w, h = rect(action)
    m = (CELL * MARGIN_PERCENT + 99) // 100
    return (x + m, y + m, w - 2 * m, h - 2 * m)


def normalize(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)) if isinstance(text, str) else ""


def guard_matches(texts):
    """Return every guard hit as ``{"text", "word"}``; an empty list means the frame passed."""
    # The jewel recovery dialog (「ジュエルを使用して…回復しますか」) is caught by ジュエル itself.
    normalized = [normalize(t) for t in texts]
    for phrase in BENIGN_PHRASES:
        normalized = [t.replace(phrase, "") for t in normalized]
    return [{"text": t, "word": w} for t in normalized for w in GUARD_WORDS if w in t]


def validate_explore_status(status):
    """Host status for one exploration click: ready Composition game, pipeline input + grid on, same grid SHA."""
    _require(isinstance(status, dict), "宿主状态无效")
    _require(status.get("composition_game") is True and status.get("browser_demo") is False and status.get("ready") is True,
             "探索只允许已就绪的 Composition 游戏宿主")
    _require(status.get("minimized") is False and status.get("window_visible") is True and status.get("webview_visible") is True,
             "宿主窗口必须可见且未最小化")
    _require(status.get("pipeline_input") is True and status.get("supervised_navigation") is None,
             "宿主未启用 --enable-pipeline-input")
    _require(status.get("produce_explore") is True, "宿主未启用 --enable-produce-explore")
    _require(status.get("explore_grid_version") == VERSION and status.get("explore_grid_sha256") == GRID_SHA256,
             "宿主探索网格版本或SHA不一致（需重新构建宿主）")
    return status


def explore_click_request(session, status, frame_sha256, action, nonce=None, *, capture_seq, frame_binding_seq):
    """Bind one exploration cell to the host's latest fresh frame; same wire format as ``pipeline-click``."""
    validate_explore_status(status)
    from agent.core.webview_controller import (PIPELINE_PROTOCOL_VERSION, pipeline_frame_sequences, validate_pipeline_session)
    validate_pipeline_session(session, session.get("pid"))
    _require(all(type(value) is int and 0 < value < 2**63 for value in (capture_seq, frame_binding_seq))
             and pipeline_frame_sequences(status) == (capture_seq, frame_binding_seq), "探索原始帧序号已变化")
    _require(is_cell(action), "未知探索单元")
    _require(action not in session.get("actions", ()), "探索单元不得与固定动作表重名")
    _require(isinstance(frame_sha256, str) and re.fullmatch("[0-9a-f]{64}", frame_sha256) is not None
             and status.get("frame_sha256") == frame_sha256, "宿主当前帧已变化（可能有其他截图客户端）")
    age = status.get("frame_age_ms")
    _require(type(age) in (int, float) and math.isfinite(age) and 0 <= age <= MAX_FRAME_AGE_MS, "宿主当前帧已超时")
    width, height = status.get("source_capture_width"), status.get("source_capture_height")
    _require(type(width) is int and type(height) is int and abs(width - 1280) <= 2 and abs(height - 720) <= 2,
             "截图源尺寸不符合规范画布")
    nonce = secrets.token_hex(16) if nonce is None else nonce
    _require(isinstance(nonce, str) and re.fullmatch("[0-9a-f]{32}", nonce) is not None, "单次编号无效")
    return {"token": session["token"], "command": PIPELINE_COMMAND, "protocol_version": PIPELINE_PROTOCOL_VERSION,
            "capture_seq": capture_seq, "frame_binding_seq": frame_binding_seq, "action": action, "frame_sha256": frame_sha256,
            "source_width": width, "source_height": height, "nonce": nonce}

