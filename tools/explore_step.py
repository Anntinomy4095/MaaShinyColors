#!/usr/bin/env python3
"""监督探索单步工具（默认关闭，「监督探索」）。

每次调用最多发送一次点击：先截取新帧，要求其 SHA 与 --expect-frame 完全一致（只对已检查过的那一帧操作），
再用项目 OCR 对整帧做钻石/付费守卫，通过后经宿主 pipeline-click 端点点击一个 40×40 网格单元，
等待约 1.2 秒后截取结果帧。前后两帧与 JSONL 记录保存在 dev_assets/private/wing_samples/<JST 游戏日>/。
任何失败都不重试。--capture-only 只截图保存并打印路径与 SHA，不发送任何输入。

便捷读屏与定位方式：
--look 截图并输出整帧 OCR（「文字@rRRcCC」）；--tap-text 在新帧的 OCR 里找唯一一处文字并点它所在的格
（等同把这段文字当作 --expect-text）；--wait-for / --wait-gone 只截图轮询到文字出现/消失（有上限，不发送输入）；
--label 给画面起名写入记录；--look-after 点击后读结果帧；--brief 输出两行文字而不是 JSON。
守卫不变：每次点击仍是新帧、整帧付费词守卫、正向文字核对、只发送一次。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import produce_explore as pe

PRIVATE = ROOT / "dev_assets/private"
SAMPLE_ROOT = PRIVATE / "wing_samples"
DEFAULT_MODEL = PRIVATE / "models/ppocr_v6_small"
PIPELINE_SUFFIX = ".pipeline-input.json"
AFTER_DELAY_S = 1.2
TEXT_CELL_MARGIN = 40          # px around the OCR box of --expect-text that still counts as "on the cell"
# --allow-far-labels (2026-10-31 Match sampling: Home and the Match page carry 「ジュエル」「ショップ」「ガシャ」 nav labels):
# a guard hit is exempt only when it is a short label (not a dialog sentence) far from the tapped cell.
FAR_LABEL_MAX_CHARS = 8
FAR_LABEL_MIN_PX = 240
LOG_NAME = "explore-steps.jsonl"
LIVE_STATUS = PRIVATE / "live-status" / "current.json"
# A run's live status heartbeats every 15 s; an older "running" snapshot is a crashed run, not an active one.
LIVE_STATUS_FRESH_S = 60
WAIT_MAX_S = 120               # --wait-for / --wait-gone: bounded capture-only polling
WAIT_MIN_INTERVAL_S = 2.0


def game_day(now):
    """JST game day (05:00 boundary); same rule as the host audit and webview_controller.game_day."""
    return (now.astimezone(_dt.timezone.utc) + _dt.timedelta(hours=9 - 5)).strftime("%Y-%m-%d")


class FrameOcr:
    """Whole-frame OCR with the project's MaaFramework OCR model (PP-OCR v6 small); read-only."""

    def __init__(self, model_dir=DEFAULT_MODEL):
        from tools.replay_ocr import MaaOcrRecognizer
        self._ocr = MaaOcrRecognizer(model_dir)
        self.model = self._ocr.model

    def texts(self, png_path):
        from maa.pipeline import JOCR, JRecognitionType
        from tools.replay_screenshots import read_png
        image = read_png(Path(png_path))
        param = JOCR(expected=[], roi=(0, 0, 1280, 720), threshold=0.0)
        detail = self._ocr.tasker.post_recognition(JRecognitionType.OCR, param, image).wait().get()
        if detail is None or len(detail.nodes) != 1 or detail.nodes[0].recognition is None:
            raise RuntimeError("Maa 未返回整帧 OCR 详情")
        if self._ocr.input_attempts:
            raise RuntimeError("OCR 控制器出现输入尝试")
        return [{"text": r.text, "score": float(r.score), "box": list(r.box)} for r in detail.nodes[0].recognition.all_results]


class Deps:
    """Injected side effects (tests replace every one; nothing here is called at import)."""

    def __init__(self, sample_root=SAMPLE_ROOT, now=None, sleep=time.sleep, bridge_factory=None, ocr_factory=None,
                 send=None, read_pipeline_session=None, live_status=LIVE_STATUS):
        self.sample_root = Path(sample_root)
        self.now = now or (lambda: _dt.datetime.now(_dt.timezone.utc))
        self.sleep = sleep
        self.bridge_factory = bridge_factory or _default_bridge
        self.ocr_factory = ocr_factory or (lambda model_dir: FrameOcr(model_dir))
        self.send = send or _default_send
        self.read_pipeline_session = read_pipeline_session or _default_pipeline_session
        self.live_status = Path(live_status) if live_status else None


def _default_bridge(session_path):
    from agent.core.browser_bridge import BrowserBridge
    return BrowserBridge.from_file(session_path)


def _default_pipeline_session(session_path, capture_pid):
    from agent.core.webview_controller import validate_pipeline_session
    path = Path(str(session_path) + PIPELINE_SUFFIX)
    return validate_pipeline_session(json.loads(path.read_text(encoding="utf-8-sig")), capture_pid)


def _default_send(session, request):
    from agent.core.webview_controller import send_line
    return send_line(session, request)


def _append(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()


def active_run(path, now):
    """The run_id of a panel/runner task that is still running, else None. Any capture taken here advances the host's
    frame binding under that run (2026-10-30: a capture-only during the daily run made the Match agent's recognition
    frame stale, its scroll was refused and the task failed with the skip dialog left open)."""
    try:
        status = json.loads(Path(path).read_text(encoding="utf-8"))
        updated = _dt.datetime.fromisoformat(status["updated_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if status.get("state") not in ("starting", "running", "stopping"):
        return None
    if (now - updated).total_seconds() > LIVE_STATUS_FRESH_S:
        return None
    return status.get("run_id") or "unknown"


def far_label_exempt(item, cell_rect):
    """A guard hit that is a short label (≤ FAR_LABEL_MAX_CHARS) whose OCR box stays ≥ FAR_LABEL_MIN_PX from the tapped
    cell's centre: a page's nav badge (「ショップ」, 「ガシャ」, a 「ジュエル」 sale badge), never a dialog sentence such as
    「ジュエルを使用して…」 and never the tapped button itself."""
    if len(pe.normalize(item.get("text"))) > FAR_LABEL_MAX_CHARS:
        return False
    x, y, w, h = item["box"]
    cx, cy = cell_rect[0] + cell_rect[2] / 2, cell_rect[1] + cell_rect[3] / 2
    dx, dy = max(x - cx, 0, cx - (x + w)), max(y - cy, 0, cy - (y + h))
    return (dx * dx + dy * dy) ** 0.5 >= FAR_LABEL_MIN_PX


def cell_of_point(x, y):
    row = min(max(int(y) // pe.CELL, 0), pe.ROWS - 1)
    col = min(max(int(x) // pe.CELL, 0), pe.COLS - 1)
    return f"r{row:02d}c{col:02d}"


def box_cell(box):
    x, y, w, h = box
    return cell_of_point(x + w / 2, y + h / 2)


def compact(texts):
    """``text@rRRcCC`` per OCR item in read order (30 px bands, then x): what an agent needs to pick the next tap."""
    items = sorted(texts, key=lambda t: (t["box"][1] // 30, t["box"][0]))
    return [f"{t['text']}@{box_cell(t['box'])}" for t in items]


def find_text(texts, wanted, near=None):
    """(item, candidates) for --tap-text: the one OCR item containing ``wanted``; with ``near`` the item whose centre is
    strictly nearest that cell. ``item`` is None when there is no hit or the hits stay ambiguous."""
    key = pe.normalize(wanted)
    hits = [t for t in texts if key and key in pe.normalize(t["text"])]
    candidates = [f"{t['text']}@{box_cell(t['box'])}" for t in hits]
    if near and len(hits) > 1:
        x0, y0, w, h = pe.rect(pe.action_for_cell(near))
        cx, cy = x0 + w / 2, y0 + h / 2

        def dist(t):
            bx, by, bw, bh = t["box"]
            return ((bx + bw / 2 - cx) ** 2 + (by + bh / 2 - cy) ** 2) ** 0.5
        ranked = sorted(hits, key=dist)
        return (ranked[0] if dist(ranked[0]) < dist(ranked[1]) else None), candidates
    return (hits[0] if len(hits) == 1 else None), candidates


def _ocr_items(texts):
    return [{"text": t["text"], "box": list(t["box"])} for t in texts]


def _capture(bridge, directory, stem):
    png = bridge.screencap()
    sha = hashlib.sha256(png).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.png"
    path.write_bytes(png)
    return path, sha


def _mode(args):
    if args.capture_only:
        return "capture-only"
    if getattr(args, "look", False):
        return "look"
    if getattr(args, "wait_for", None) or getattr(args, "wait_gone", None):
        return "wait"
    return "click"


def run(args, deps):
    """Returns (exit_code, summary). Exit 0 = captured / waited / host reported sent, 2 = refused or wait timed out
    (no input), 3 = result unknown."""
    now = deps.now()
    day_dir = deps.sample_root / game_day(now)
    log = day_dir / LOG_NAME
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    mode = _mode(args)
    summary = {"mode": mode, "time": now.isoformat(), "input_requests": 0}
    if getattr(args, "label", None):
        summary["label"] = args.label
    record = dict(summary)

    def finish(code, result, **extra):
        summary.update(result=result, **extra)
        record.update(result=result, **extra)
        try:
            _append(log, record)
        except OSError as exc:
            summary["log_error"] = str(exc)
            code = code or 3
        summary["log"] = str(log)
        return code, summary

    if deps.live_status is not None and (run_id := active_run(deps.live_status, now)):
        return finish(2, "refused", reason="run_in_progress: " + str(run_id))
    try:
        bridge = deps.bridge_factory(args.session)
    except Exception as exc:  # noqa: BLE001 - report and stop, never retry
        return finish(2, "refused", reason="bridge_unavailable: " + str(exc))

    if mode == "capture-only":
        try:
            status = bridge.status()
            if status.get("ready") is not True:
                return finish(2, "refused", reason="host_not_ready")
            path, sha = _capture(bridge, day_dir, f"{stamp}-capture")
        except Exception as exc:  # noqa: BLE001
            return finish(2, "refused", reason="capture_failed: " + str(exc))
        return finish(0, "captured", frame=str(path), frame_sha256=sha)

    if mode in ("look", "wait"):
        return _look_or_wait(args, deps, bridge, day_dir, stamp, mode, record, summary, finish)

    tap_text = getattr(args, "tap_text", None)
    cell = args.cell
    if cell:
        action = pe.action_for_cell(cell)
        record.update(cell=cell, action=action, rect=list(pe.rect(action)), inner_rect=list(pe.inner_rect(action)),
                      note=args.note, expect_frame=args.expect_frame)
        summary.update(cell=cell, action=action)
    else:
        record.update(tap_text=tap_text, near=getattr(args, "near", None), note=args.note)
        summary["tap_text"] = tap_text

    # Fail closed: without the OCR model there is no jewel/paid guard, so no click.
    try:
        ocr = deps.ocr_factory(args.model_dir)
    except Exception as exc:  # noqa: BLE001
        return finish(2, "refused", reason="ocr_unavailable: " + str(exc))
    try:
        status = pe.validate_explore_status(bridge.status())
        session = deps.read_pipeline_session(args.session, bridge.session["pid"])
    except Exception as exc:  # noqa: BLE001
        return finish(2, "refused", reason="host_not_ready_for_explore: " + str(exc))

    try:
        before, before_sha = _capture(bridge, day_dir, f"{stamp}-{cell or 'tap'}-before")
        from agent.core.webview_controller import pipeline_frame_sequences
        captured_status = pe.validate_explore_status(bridge.status())
        captured_sequences = pipeline_frame_sequences(captured_status)
        if (captured_sequences != (status.get("capture_seq", -1) + 1, status.get("frame_binding_seq", -1) + 2)
                or captured_status.get("frame_sha256") != before_sha):
            raise ValueError("探索截图期间绑定变化或缺少原子帧序号")
    except Exception as exc:  # noqa: BLE001
        return finish(2, "refused", reason="capture_failed: " + str(exc))
    record.update(before=str(before), before_sha256=before_sha)
    summary.update(before=str(before), before_sha256=before_sha)
    expect_text = tap_text or getattr(args, "expect_text", None)
    if not expect_text and before_sha != args.expect_frame:
        return finish(2, "refused", reason="frame_sha_mismatch")

    try:
        texts = ocr.texts(before)
    except Exception as exc:  # noqa: BLE001
        return finish(2, "refused", reason="ocr_failed: " + str(exc))
    record["ocr"] = _ocr_items(texts)
    if tap_text:
        # The tap target comes from this frame's OCR: exactly one item (or the strictly nearest to --near) holding the
        # text; its centre cell is clicked and the text itself is the positive guard below.
        item, candidates = find_text(texts, tap_text, getattr(args, "near", None))
        record["tap_candidates"] = summary["tap_candidates"] = candidates
        if item is None:
            return finish(2, "refused", reason="tap_text_ambiguous" if candidates else "tap_text_not_found",
                          texts=compact(texts))
        cell = box_cell(item["box"])
        action = pe.action_for_cell(cell)
        record.update(cell=cell, action=action, rect=list(pe.rect(action)), inner_rect=list(pe.inner_rect(action)))
        summary.update(cell=cell, action=action)
    exempt = []
    if getattr(args, "allow_far_labels", False):
        cell_rect = pe.rect(action)
        exempt = [item["text"] for item in texts if pe.guard_matches([item["text"]]) and far_label_exempt(item, cell_rect)]
    hits = pe.guard_matches([item["text"] for item in texts if item["text"] not in exempt])
    record["ocr_guard"] = {"words": list(pe.GUARD_WORDS), "texts": [item["text"] for item in texts], "matches": hits,
                           "far_labels_exempt": exempt, "passed": not hits}
    summary["ocr_guard_matches"] = hits
    if hits:
        return finish(2, "refused", reason="paid_guard_matched")
    if expect_text:
        # Animated screens (2026-10-14 記者 choice: floating buttons) never repeat a frame SHA. The fresh frame bound
        # to the click must instead show ``expect_text`` on the clicked cell (box grown by TEXT_CELL_MARGIN px).
        at = (None if tap_text else getattr(args, "expect_text_at", None)) or cell
        x0, y0, w, h = pe.rect(pe.action_for_cell(at))
        cx, cy = x0 + w / 2, y0 + h / 2
        key = pe.normalize(expect_text)
        on_cell = [t["text"] for t in texts if key in pe.normalize(t["text"])
                   and t["box"][0] - TEXT_CELL_MARGIN <= cx <= t["box"][0] + t["box"][2] + TEXT_CELL_MARGIN
                   and t["box"][1] - TEXT_CELL_MARGIN <= cy <= t["box"][1] + t["box"][3] + TEXT_CELL_MARGIN]
        record["expect_text"] = {"text": expect_text, "at": at, "matches": on_cell}
        if not on_cell:
            return finish(2, "refused", reason="expect_text_not_on_cell")

    try:
        request = pe.explore_click_request(session, pe.validate_explore_status(bridge.status()), before_sha, action,
                                           capture_seq=captured_sequences[0], frame_binding_seq=captured_sequences[1])
    except Exception as exc:  # noqa: BLE001
        return finish(2, "refused", reason="frame_binding_refused: " + str(exc))
    try:
        _append(log, {**record, "result": "intent", "nonce": request["nonce"]})
    except OSError as exc:
        return finish(2, "refused", reason="log_write_failed_no_input: " + str(exc))

    # Exactly one send; never retried, whatever the outcome.
    summary["input_requests"] = record["input_requests"] = 1
    try:
        response = deps.send(session, request)
    except Exception as exc:  # noqa: BLE001
        response = {"ok": False, "attempted": None, "reason": "send_failed_result_unknown_no_retry: " + str(exc)}
    record.update(nonce=request["nonce"], host_response=response)
    summary["host_response"] = response

    deps.sleep(AFTER_DELAY_S)
    try:
        after, after_sha = _capture(bridge, day_dir, f"{stamp}-{cell}-after")
        record.update(after=str(after), after_sha256=after_sha)
        summary.update(after=str(after), after_sha256=after_sha)
        if getattr(args, "look_after", False):
            after_texts = ocr.texts(after)
            record["after_ocr"] = _ocr_items(after_texts)
            summary["after_texts"] = compact(after_texts)
    except Exception as exc:  # noqa: BLE001
        record["after_error"] = summary["after_error"] = str(exc)
    ok = isinstance(response, dict) and response.get("ok") is True
    if ok:
        return finish(0, "sent")
    return finish(2 if isinstance(response, dict) and response.get("attempted") is False else 3, "host_refused_or_unknown")


def _look_or_wait(args, deps, bridge, day_dir, stamp, mode, record, summary, finish):
    """--look: one capture + whole-frame OCR. --wait-for / --wait-gone: capture + OCR every ``--interval`` s until the
    pattern appears / disappears, at most ``--timeout`` s. Never sends input; a wait keeps only its last frame."""
    try:
        ocr = deps.ocr_factory(args.model_dir)
    except Exception as exc:  # noqa: BLE001
        return finish(2, "refused", reason="ocr_unavailable: " + str(exc))
    try:
        if bridge.status().get("ready") is not True:
            return finish(2, "refused", reason="host_not_ready")
    except Exception as exc:  # noqa: BLE001
        return finish(2, "refused", reason="host_not_ready: " + str(exc))
    pattern = getattr(args, "wait_for", None) or getattr(args, "wait_gone", None)
    regex = re.compile(pattern) if mode == "wait" else None
    want_present = bool(getattr(args, "wait_for", None))
    polls = 1 if mode == "look" else max(1, int(args.timeout / args.interval)) + 1
    if regex is not None:
        record["wait"] = summary["wait"] = {"pattern": pattern, "until": "present" if want_present else "absent"}
    for poll in range(polls):
        if poll:
            deps.sleep(args.interval)
        try:
            path, sha = _capture(bridge, day_dir, f"{stamp}-{mode}")
            texts = ocr.texts(path)
        except Exception as exc:  # noqa: BLE001
            return finish(2, "refused", reason="capture_failed: " + str(exc))
        if regex is None or any(regex.search(pe.normalize(t["text"])) for t in texts) == want_present:
            record["ocr"] = _ocr_items(texts)
            return finish(0, "captured" if regex is None else "waited", frame=str(path), frame_sha256=sha,
                          polls=poll + 1, texts=compact(texts))
    record["ocr"] = _ocr_items(texts)
    return finish(2, "wait_timeout", frame=str(path), frame_sha256=sha, polls=polls, texts=compact(texts))


def brief(summary):
    """--brief: first line result / reason / cell / frame, then the OCR read of the frame the agent should look at."""
    head = [summary.get("result"), summary.get("reason"), summary.get("cell"),
            summary.get("after") or summary.get("frame") or summary.get("before")]
    lines = [" ".join(str(x) for x in head if x)]
    if summary.get("result") == "refused" and summary.get("tap_candidates"):
        lines.append("candidates: " + " | ".join(summary["tap_candidates"]))
    if summary.get("ocr_guard_matches"):
        lines.append("guard: " + " | ".join(m["text"] for m in summary["ocr_guard_matches"]))
    texts = summary.get("after_texts") or summary.get("texts")
    if texts:
        lines.append(" | ".join(texts))
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(
        description="监督探索单步：对已检查的帧点击一个 40×40 网格单元（每次最多一次点击，失败不重试）；"
                    "--capture-only 仅截图。需要宿主同时启用 --enable-pipeline-input 与 --enable-produce-explore。")
    parser.add_argument("--session", type=Path, required=True, help="宿主截图桥会话文件（--bridge-session 的路径）")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--cell", help="网格单元 rRRcCC：行 00–17、列 00–31，单元左上角 = (列×40, 行×40)")
    mode.add_argument("--tap-text", help="点击新帧 OCR 里唯一含此文字的位置（它所在的格）；0 处或多处即拒绝（可用 --near 选最近的一处）")
    mode.add_argument("--capture-only", action="store_true", help="只截图保存并打印帧路径与 SHA，不发送输入")
    mode.add_argument("--look", action="store_true", help="截图并输出整帧 OCR（文字@rRRcCC），不发送输入")
    mode.add_argument("--wait-for", help="只截图轮询，直到某段 OCR 文字匹配此正则（不发送输入）")
    mode.add_argument("--wait-gone", help="只截图轮询，直到没有 OCR 文字匹配此正则（不发送输入）")
    parser.add_argument("--expect-frame", help="--cell 模式：刚检查过的帧 SHA-256；新截帧不一致即拒绝")
    parser.add_argument("--expect-text", help="--cell 模式，动画画面替代 --expect-frame：点击前新截帧的 OCR 须在该单元格上读到此文字")
    parser.add_argument("--expect-text-at", help="--expect-text 所在单元格（默认即点击单元格；用于无文字按钮，如返回键）")
    parser.add_argument("--near", help="--tap-text 有多处时取离此单元格最近的一处")
    parser.add_argument("--note", help="点击模式必填：为何点击这里（写入 JSONL 记录）")
    parser.add_argument("--label", help="给当前画面起的名字（写入 JSONL 记录，采集数据用）")
    parser.add_argument("--look-after", action="store_true", help="点击后对结果帧做 OCR 并输出")
    parser.add_argument("--timeout", type=float, default=30.0, help=f"--wait-* 上限秒数（≤{WAIT_MAX_S}）")
    parser.add_argument("--interval", type=float, default=3.0, help=f"--wait-* 轮询间隔秒数（≥{WAIT_MIN_INTERVAL_S}）")
    parser.add_argument("--brief", action="store_true", help="输出两行文字（结果 + OCR）而不是 JSON")
    parser.add_argument("--allow-far-labels", action="store_true",
                        help=f"付费词只出现在离点击格 ≥{FAR_LABEL_MIN_PX}px 的短标签（≤{FAR_LABEL_MAX_CHARS} 字，如「ショップ」角标）时放行；句子仍拒绝")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL, help="OCR 模型目录（默认 PP-OCR v6 small）；缺失则拒绝点击")
    return parser


def check_args(parser, args):
    if args.cell:
        if args.expect_text:
            if args.expect_text_at:
                try:
                    pe.action_for_cell(args.expect_text_at)
                except ValueError as exc:
                    parser.error(str(exc))
            if args.expect_frame or not args.expect_text.strip():
                parser.error("--expect-text 与 --expect-frame 二选一，且文字不能为空")
        elif not args.expect_frame or len(args.expect_frame) != 64 or any(c not in "0123456789abcdef" for c in args.expect_frame):
            parser.error("点击模式必须提供小写十六进制 64 位 --expect-frame（动画画面用 --expect-text）")
        if args.near:
            parser.error("--near 只用于 --tap-text")
        try:
            pe.action_for_cell(args.cell)
        except ValueError as exc:
            parser.error(str(exc))
    elif args.tap_text is not None:
        if not pe.normalize(args.tap_text):
            parser.error("--tap-text 不能为空")
        if args.expect_frame or args.expect_text or args.expect_text_at:
            parser.error("--tap-text 自带文字核对，不接受 --expect-frame / --expect-text / --expect-text-at")
        if args.near:
            try:
                pe.action_for_cell(args.near)
            except ValueError as exc:
                parser.error(str(exc))
    elif (args.expect_frame or args.note or args.expect_text or args.expect_text_at or args.allow_far_labels
          or args.near or args.look_after):
        parser.error("--capture-only / --look / --wait-* 不接受点击参数（--expect-* / --note / --near / --look-after）")
    if (args.cell or args.tap_text is not None) and (not args.note or not args.note.strip()):
        parser.error("点击模式必须提供 --note 说明点击原因")
    if args.wait_for is not None or args.wait_gone is not None:
        try:
            re.compile(args.wait_for if args.wait_for is not None else args.wait_gone)
        except re.error as exc:
            parser.error(f"--wait-* 正则无效：{exc}")
        if not (0 < args.timeout <= WAIT_MAX_S) or args.interval < WAIT_MIN_INTERVAL_S:
            parser.error(f"--timeout 须在 0–{WAIT_MAX_S} 秒，--interval 至少 {WAIT_MIN_INTERVAL_S} 秒")


def main(argv=None, deps=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    check_args(parser, args)
    code, summary = run(args, deps or Deps())
    print(brief(summary) if args.brief else json.dumps(summary, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
