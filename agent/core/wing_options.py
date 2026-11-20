"""In-game オプション → プロデュース設定 for W.I.N.G. runs (2026-10-13 read-only check; 2026-10-14 adaptive).

User goal: Maa reads the user's プロデュース設定 and behaves correctly for either value instead of requiring
fixed values. Three rows change what the navigator sees (``DEPENDENT``):

* 休む選択時に確認画面を表示 -- ON: 休む opens ``RestConfirm`` (OK clears the planned rest). OFF: 休む rests at once; the
  planned rest is done when the tap was sent and the next frame proves progress (week counter / rest animation).
* ライブ選択時に確認画面を表示 -- ON: the final/semi-final Hub tap leads to ``LiveConfirm``. OFF: straight to the Live.
* W.I.N.G.編 ファン数未達成、振り返り警告ポップアップを表示 -- OFF: never shown. ON: the popup is not sampled yet; an
  unknown frame in its context stops with a clear reason (``WARNING_POPUP_SCREEN`` is the classifier placeholder).

The other rows (アイドルへのタッチ判定, 未読イベント / カットイン 早送り解除) are recorded but either value is handled
(``CommuFastForwardOff`` turns 早送り back on). Each toggle's selected half is drawn pink (#299: ON (210,44,134) /
OFF lavender (242,241,254)); a row is read only when its label is on screen and exactly one half is pink.

The record is one private JSON per local game profile (this install = the user's account). A run needs a complete,
fresh reading (every dependent row, none older than ``MAX_AGE_DAYS``, not marked stale by a symptom). Without one,
Maa reads the page itself on the run's first Hub (``read_step``: オプション → read → wheel the list (read-only) → read
→ ←); it never taps a toggle, 保存 or 初期設定に戻す, and any dialog or unknown screen on the way stops the run. The
read path's targets stay disabled until a supervised live sample (``read_path_ready``); until then a missing / stale
record stops the run and asks the user to open the page once (a page the user opened is read on any run).
Evidence: supervised recording captures-20261011-164549Z (#294 Hub → tap (1204,231) → #296/#298 Now Loading → #299
options top, #303 middle, #306 bottom, ← (64,666) → #310 Hub).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_FILE = ROOT / "dev_assets" / "private" / "wing-ui" / "produce-settings.json"
EVIDENCE = "dev_assets/private/captures-20261011-164549Z #299 #303 #306"

# key: (label pattern, Chinese label)
ROWS: dict[str, tuple[str, str]] = {
    "idol_touch": (r"タッチ判定", "アイドルへのタッチ判定"),
    "unread_ff_release": (r"未読イベント", "未読イベント開始時に早送り解除"),
    "cutin_ff_release": (r"カットイン", "サポートアイドルスキルカットイン発生時に早送り解除"),
    "rest_confirm": (r"休む選択時", "休む選択時に確認画面を表示"),
    "live_confirm": (r"ライブ選択時", "ライブ選択時に確認画面を表示"),
    "wing_warning_popup": (r"振り返り警告|ファン数未達成", "W.I.N.G.編 ファン数未達成、振り返り警告ポップアップ"),
}
DEPENDENT = ("rest_confirm", "live_confirm", "wing_warning_popup")   # rows the navigator adapts to
REQUIRED = DEPENDENT                                                  # rows a run needs a fresh reading of
# Behaviour without a settings record (offline replays / tests): the values of the 10-11 recording.
ASSUMED = {"rest_confirm": "ON", "live_confirm": "ON", "wing_warning_popup": "OFF"}
MAX_AGE_DAYS = 7

LIST_ROI = (455, 95, 400, 510)          # labels of the visible rows (SC.Wing.Read.Options.List)
DIALOG_ROI = (455, 150, 440, 420)       # centre of the page (labels only; thumbnails x<455 and toggles x>865 excluded)
VIEW_Y = (100, 600)                     # list viewport; a toggle clipped by it is not read
ON_X, OFF_X = (875, 1015), (1040, 1180)  # inner columns of the ON / OFF halves (#299 buttons 865–1028 / 1030–1195)
BAND = (-30, 40)                        # toggle rows relative to a label line's top (two/three-line labels too)
SELECTED_MIN, OTHER_MAX = 0.25, 0.05    # pink share of the band: selected half / unselected half
# Any of these in the page centre = a dialog over the options page (e.g. 「変更を保存しますか」): stop, never answer.
DIALOG_TEXT = re.compile(r"保存しますか|保存されて|破棄|変更内容|変更を|よろしいですか|キャンセル|はい|いいえ")
# 保存 always answers with 「オプション / 設定を保存しました。」 and one button 閉じる (live 2026-10-25, wing10 seg2): only a
# fix pass that just tapped 保存 closes it (once); anywhere else it stops like any other dialog.
SAVED_TEXT = re.compile(r"設定を保存しました")

# Maa's own read path (all three targets need a supervised live sample before ``read_path_ready``).
READ_INTENTS = {"open": "hub_options", "scroll": "options_scroll_down", "back": "options_back"}
OPTIONS_SCROLL_RECT = (500, 330, 300, 60)   # list centre, labels column (no toggle, no button under it)
OPTIONS_SCROLL_WHEEL = -60                  # one read-only wheel step down (same delta as the picker wheel)
OPTIONS_MAX_SCROLLS = 6                     # recording: the list moved 140 + 188 px from top to bottom
OPTIONS_SETTLE_WAITS = 4                    # frames until two consecutive readings agree
WARNING_POPUP_SCREEN = "WingWarningPopup"   # classifier placeholder until the popup is sampled

# 2026-10-25 user (「游戏设置能否也由maa自己改」→「开关打开后改好并保留」): with the W.I.N.G. switch 「自动改游戏设置」
# (RunOptions.settings_fix, default off) the first Hub opens オプション, taps OFF on these rows when they read ON, taps 保存,
# then re-opens the page and reads them again (``new_read("verify")``). The values are kept (never restored); nothing
# else is touched (アイドルへのタッチ判定 stays until a tap fast lane exists). A tap is sent only with the row label at the
# position its OFF rect was measured on (list top #299 / list end #306); any doubt stops without a retry.
FIX_VALUES = {"unread_ff_release": "OFF", "wing_warning_popup": "OFF"}
FIX_INTENTS = {"unread_ff_release": "options_unread_off", "wing_warning_popup": "options_popup_off"}
SAVE_INTENT = "options_save"
SAVED_CLOSE_INTENT = "options_saved_close"
FIX_ROW_TOP = {"unread_ff_release": 280, "wing_warning_popup": 504}   # label tops on #299 / #306 (OCR)
FIX_ROW_TOLERANCE = 6
# Screens around which the warning popup can show (fans short before the W.I.N.G. stage / 振り返り reminder).
WARNING_CONTEXT = ("Hub", "HubSemiFinal", "HubFinal", "Schedule", "AuditionTab", "Review", "LiveConfirm",
                   "ScheduleBusy")


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def _pink_share(image, x_range: Sequence[int], y0: int, y1: int) -> float:
    import numpy as np
    patch = np.asarray(image)[y0:y1, x_range[0]:x_range[1], :3].astype(int)
    if patch.size == 0:
        return 0.0
    r, g, b = patch[..., 0], patch[..., 1], patch[..., 2]
    pink = (r > 185) & (g < 120) & (b > 100) & (b < 195) & (r - g > 90)
    return float(pink.mean())


def row_tops(texts: Iterable[tuple]) -> dict:
    """{key: label top y} of the rows whose label is on screen exactly once (the list position signature)."""
    texts = list(texts)
    out = {}
    for key, (pattern, _) in ROWS.items():
        tops = [box[1] for text, box in texts if re.search(pattern, _clean(text))]
        if len(tops) == 1:
            out[key] = tops[0]
    return out


def read_toggles(texts: Iterable[tuple], image) -> dict:
    """{key: "ON" | "OFF" | None} for every row of ``ROWS`` (None: label not on screen or the toggle unreadable)."""
    out: dict = {key: None for key in ROWS}
    if image is None:
        return out
    for key, top in row_tops(texts).items():
        y0, y1 = top + BAND[0], top + BAND[1]
        if y0 < VIEW_Y[0] - 10 or y1 > VIEW_Y[1] + 10:
            continue
        on, off = _pink_share(image, ON_X, y0, y1), _pink_share(image, OFF_X, y0, y1)
        if on >= SELECTED_MIN and off <= OTHER_MAX:
            out[key] = "ON"
        elif off >= SELECTED_MIN and on <= OTHER_MAX:
            out[key] = "OFF"
    return out


def dialog_text(texts: Iterable[tuple]) -> str | None:
    """Text of a dialog over the options page (DIALOG_ROI texts), None when only row labels are there."""
    for text, _ in texts:
        if DIALOG_TEXT.search(_clean(text)):
            return _clean(text)
    return None


def saved_dialog(texts: Iterable[tuple]) -> bool:
    """「設定を保存しました。」 (the answer to 保存) in the page centre (DIALOG_ROI texts)."""
    return any(SAVED_TEXT.search(_clean(text)) for text, _ in texts)


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _age_days(stamp, now: _dt.datetime) -> float | None:
    try:
        seen = _dt.datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    if seen.tzinfo is None:
        seen = seen.astimezone()
    return (now - seen).total_seconds() / 86400


def status(record: Mapping[str, Any] | None, now: _dt.datetime | None = None) -> dict:
    """``ok`` when every dependent row has a value (either), was seen within MAX_AGE_DAYS and nothing marked the
    record stale. ``values`` = the dependent rows' recorded values."""
    now = now or _dt.datetime.now().astimezone()
    values = dict((record or {}).get("values") or {})
    seen = dict((record or {}).get("seen_at") or {})
    missing = [k for k in REQUIRED if values.get(k) not in ("ON", "OFF")]
    expired = []
    for k in REQUIRED:
        if k in missing:
            continue
        age = _age_days(seen.get(k), now)
        if age is None or age > MAX_AGE_DAYS or age < -1:
            expired.append(k)
    stale = (record or {}).get("stale")
    return {"ok": not missing and not expired and not stale, "missing": missing, "expired": expired, "stale": stale,
            "values": {k: values.get(k) for k in REQUIRED}}


def value(record: Mapping[str, Any] | None, key: str) -> str:
    """Recorded value of a dependent row, else the ASSUMED behaviour (no record = the 10-11 recording)."""
    v = ((record or {}).get("values") or {}).get(key)
    return v if v in ("ON", "OFF") else ASSUMED[key]


def missing_text(missing: Sequence[str]) -> str:
    return ("需要读取 Produce 设置：请在游戏中打开 オプション → プロデュース設定，"
            "停在能看到「" + "」「".join(ROWS[k][1] for k in missing) + "」的位置后点开始")


def expired_text(expired: Sequence[str]) -> str:
    return (f"Produce 设置超过 {MAX_AGE_DAYS} 天没读：请打开 オプション → プロデュース設定，"
            "停在能看到「" + "」「".join(ROWS[k][1] for k in expired) + "」的位置后点开始")


def stale_text(why: str) -> str:
    return f"Produce 设置可能已变化（{why}）：请打开 オプション → プロデュース設定 确认后重试"


WARNING_POPUP_TEXT = "游戏开着「ファン数未達成、振り返り警告ポップアップ」，此弹窗还没采样，已停止"


def read_path_ready(targets: Mapping[str, tuple]) -> bool:
    """Maa may open the options page itself only when all three read-path targets are verified."""
    return all(targets.get(i, (None, False))[1] for i in READ_INTENTS.values())


def fix_path_ready(targets: Mapping[str, tuple]) -> bool:
    """The settings fix needs the read path plus both OFF taps and 保存 verified (supervised sample)."""
    return read_path_ready(targets) and all(targets.get(i, (None, False))[1]
                                            for i in (*FIX_INTENTS.values(), SAVE_INTENT, SAVED_CLOSE_INTENT))


def fix_satisfied(record: Mapping[str, Any] | None, now: _dt.datetime | None = None) -> bool:
    """The record already shows every ``FIX_VALUES`` row at its value, read within MAX_AGE_DAYS and not stale: the
    page is not opened (nothing to change)."""
    now = now or _dt.datetime.now().astimezone()
    record = record or {}
    if record.get("stale"):
        return False
    values, seen = dict(record.get("values") or {}), dict(record.get("seen_at") or {})
    for key, want in FIX_VALUES.items():
        age = _age_days(seen.get(key), now)
        if values.get(key) != want or age is None or age > MAX_AGE_DAYS or age < -1:
            return False
    return True


def new_read(kind: str = "read") -> dict:
    """State of one pass over the page (kept in RunMemory so a rolled-back tap also rolls the state back). ``kind``:
    ``read`` (read-only), ``fix`` (tap OFF on ``FIX_VALUES`` rows that read ON, then 保存) or ``verify`` (read-only
    re-read after a fix; every ``FIX_VALUES`` row must read its value)."""
    if kind not in ("read", "fix", "verify"):
        raise ValueError(kind)
    return {"phase": "reading", "kind": kind, "scrolls": 0, "pending": None, "before": None, "last": None,
            "settle": 0, "seen": [], "frames": 0, "values": {}, "tapped": [], "toggle": None, "saved": False,
            "closed": False, "save_waits": 0}


def _needed(state: Mapping[str, Any]) -> tuple:
    return REQUIRED + (tuple(k for k in FIX_VALUES if k not in REQUIRED) if state.get("kind", "read") != "read" else ())


def read_step(state: dict, reading: Mapping[str, Any]) -> dict:
    """One オプション frame of Maa's own pass -> {"action": wait | scroll | toggle | save | close | back | stop, "observe":
    {key: value} | None, "reason"}. Every reading needs two consecutive frames that agree (rows and values); a wheel
    step that did not move the list means the list end. A ``read`` / ``verify`` pass only wheels the list and taps ←.
    A ``fix`` pass also taps OFF (``"key"``) on a ``FIX_VALUES`` row that reads ON when its label sits at
    ``FIX_ROW_TOP`` (the unread row at the list top, the popup row at the list end), checks the next reading (that row
    OFF, every other row unchanged), and taps 保存 once all rows were read. 保存 is proven by 「設定を保存しました。」:
    閉じる once, then the page must read the same values without a dialog, then ←."""
    state["frames"] += 1
    if reading.get("dialog"):
        return {"action": "stop", "reason": "options_dialog", "dialog": reading["dialog"], "observe": None}
    if reading.get("saved"):
        if state["phase"] != "saving":
            return {"action": "stop", "reason": "options_dialog", "dialog": "設定を保存しました", "observe": None}
        if not state.get("closed"):
            state["closed"], state["last"], state["settle"] = True, None, 0
            return {"action": "close", "reason": "options_saved_close", "observe": None}
        state["settle"] += 1                # 閉じる sent: the dialog may still be fading out
        if state["settle"] > OPTIONS_SETTLE_WAITS:
            return {"action": "stop", "reason": "options_saved_close_unconfirmed", "observe": None}
        return {"action": "wait", "reason": "options_saved_closing", "observe": None}
    tops = dict(reading.get("tops") or {})
    values = {k: v for k, v in dict(reading.get("values") or {}).items() if v in ("ON", "OFF")}
    sig = [sorted(tops.items()), sorted(values.items())]
    if sig != state["last"] or not tops:
        state["last"] = sig
        state["settle"] += 1
        if state["settle"] > OPTIONS_SETTLE_WAITS:
            return {"action": "stop", "reason": "options_rows_unread" if not tops else "options_unsettled",
                    "observe": None}
        return {"action": "wait", "reason": "options_settling", "observe": None}
    state["settle"] = 0
    at_end = False
    if state["pending"] == "down":
        at_end = sig[0] == state["before"]
        state["pending"] = None
    known = state.setdefault("values", {})
    changed = sorted(k for k, v in values.items() if known.get(k) not in (None, v))
    toggled, state["toggle"] = state.get("toggle"), None
    if toggled is not None:
        # The frame after an OFF tap: that row now reads OFF at the same place, nothing else changed.
        if values.get(toggled) != FIX_VALUES[toggled] or changed != [toggled]:
            return {"action": "stop", "reason": "options_toggle_unconfirmed", "key": toggled, "changed": changed,
                    "observe": None}
    elif changed:
        return {"action": "stop", "reason": "options_value_changed", "changed": changed, "observe": None}
    known.update(values)
    state["seen"] = sorted(set(state["seen"]) | set(values))
    out = {"observe": values}
    if state["phase"] == "saving":
        if not state.get("closed"):
            # Settled page without 「設定を保存しました。」 yet: a few frames, then the save is unproven.
            state["save_waits"] = state.get("save_waits", 0) + 1
            if state["save_waits"] > OPTIONS_SETTLE_WAITS:
                return {**out, "action": "stop", "reason": "options_save_unconfirmed"}
            return {**out, "action": "wait", "reason": "options_save_pending"}
        # The reading after 閉じる (no dialog, settled, the same values): leave; the verify pass re-reads the page.
        if not any(k in values for k in FIX_VALUES):
            return {**out, "action": "stop", "reason": "options_save_unconfirmed"}
        state["phase"], state["saved"] = "leaving", True
        return {**out, "action": "back", "reason": "options_saved"}
    kind = state.get("kind", "read")
    pending_fix = False
    if kind == "fix":
        for key, want in FIX_VALUES.items():
            if values.get(key) in (None, want):
                continue
            if key in state["tapped"]:
                return {**out, "action": "stop", "reason": "options_toggle_unconfirmed", "key": key}
            top = tops.get(key)
            if top is not None and abs(top - FIX_ROW_TOP[key]) <= FIX_ROW_TOLERANCE:
                state["tapped"].append(key)
                state["toggle"], state["last"] = key, None
                return {**out, "action": "toggle", "key": key, "reason": "options_fix_toggle"}
            if key == "unread_ff_release" or at_end:
                # Not where its OFF rect was measured (the list did not open at the top / ends elsewhere).
                return {**out, "action": "stop", "reason": "options_row_moved", "key": key, "top": top}
            pending_fix = True              # the popup row above the list end: keep wheeling down
    needed = _needed(state)
    if all(k in state["seen"] for k in needed) and not pending_fix:
        off = all(known.get(k) == v for k, v in FIX_VALUES.items())
        if kind == "verify" and not off:
            return {**out, "action": "stop", "reason": "options_fix_not_kept",
                    "values": {k: known.get(k) for k in FIX_VALUES}}
        if kind == "fix" and state["tapped"]:
            state["phase"], state["last"] = "saving", None
            return {**out, "action": "save", "reason": "options_fix_save"}
        state["phase"] = "leaving"
        return {**out, "action": "back", "reason": "options_read_done"}
    if at_end:
        return {**out, "action": "stop", "reason": "options_rows_unread",
                "missing": [k for k in needed if k not in state["seen"]]}
    if state["scrolls"] >= OPTIONS_MAX_SCROLLS:
        return {**out, "action": "stop", "reason": "options_scroll_budget"}
    state["scrolls"] += 1
    state["pending"], state["before"], state["last"] = "down", sig[0], None
    return {**out, "action": "scroll", "reason": "options_read_scroll"}


class SettingsGate:
    """Per-run view of the record: ``observe`` merges a read of the options page, ``invalidate`` marks a symptom
    (e.g. a rest without 確認画面). ``dirty`` tells the session to save. Pure: the session owns the file."""

    def __init__(self, record: Mapping[str, Any] | None = None):
        self.record: dict = json.loads(json.dumps(dict(record or {})))
        self.record.setdefault("version", 1)
        self.record.setdefault("values", {})
        self.record.setdefault("seen_at", {})
        self.dirty = False
        self.passed = False             # this run already passed the gate

    def status(self, now: _dt.datetime | None = None) -> dict:
        return status(self.record, now)

    def value(self, key: str) -> str:
        return value(self.record, key)

    def observe(self, values: Mapping[str, Any], source: str = "options_page", at: str | None = None) -> dict:
        read = {k: v for k, v in dict(values or {}).items() if k in ROWS and v in ("ON", "OFF")}
        if read:
            stamp = at or _now()
            self.record["values"].update(read)
            self.record["seen_at"].update({k: stamp for k in read})
            self.record["source"], self.record["updated_at"] = source, stamp
            if self.record.get("stale"):
                reread = sorted(set(self.record.get("reread") or ()) | set(read))
                self.record["reread"] = reread
                if all(k in reread for k in REQUIRED):
                    self.record["stale"], self.record["reread"] = None, []   # every dependent row re-read
            self.dirty = True
        return read

    def mark_stale(self, why: str) -> None:
        """The game may differ from the record (e.g. 保存 sent, not re-read yet): the next pass or run re-reads it."""
        self.record["stale"], self.record["reread"] = why, []
        self.dirty = True

    def invalidate(self, key: str, value: str, why: str) -> None:
        """A symptom contradicted the record: keep the observed value, but make the next run re-read the page."""
        self.record["values"][key] = value
        self.record["seen_at"][key] = _now()
        self.record["stale"], self.record["reread"] = why, []
        self.record["updated_at"] = self.record["seen_at"][key]
        self.dirty = True


class SettingsStore:
    """The private record file (atomic write). ``path`` must stay under dev_assets/private."""

    def __init__(self, path: Path | str = SETTINGS_FILE):
        self.path = Path(path)

    def load(self) -> dict | None:
        if not self.path.is_file():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("values"), dict):
            raise ValueError("Produce 设置记录格式无效")
        return data

    def save(self, record: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
