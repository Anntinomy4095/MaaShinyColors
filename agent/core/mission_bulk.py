"""デイリー 「一括受取」 for daily mission rewards (2026-10-10 user request): pure reading and decision, no Maa, no input.

Why: the login reward is not always the same ticket (2026-10-10 it was a SILVER training ticket while the single-claim
contract expects bronze -> ``login_reward_unverified``). The user asked for the game's own bulk receive instead.

Native evidence (``dev_assets/private/fes-match-reader-20261007/``): ``missions_before_bulk`` (デイリー, 2 claimable rows
then a CLEAR row, 一括受取 pink), ``missions_bulk_confirm`` (「ミッション報酬一括受け取り」 / 「ミッション報酬を新しいものから
100件まで受け取ります。」 / キャンセル + OK), ``missions_bulk_result`` (「以下の報酬を受け取りました」 「トレーニングチケット ×2」 OK),
``missions_after_bulk_bottom`` (the same rows CLEAR!!, 一括受取 grey). 2026-10-10 live frame
``wing_samples/2026-10-10/20261010T122544308778Z``: login (silver ×1, 受け取る), money shop (silver ×1, 受け取る),
Fes (bronze, 詳細).

Rules (``decide``):

- the デイリー page guard, no overlay, the daily tab selected, one enabled pink 「一括受取」;
- the list is ordered claimable -> incomplete (詳細) -> CLEAR (the page footer: 「達成済みのミッションは未達成のミッションの
  下部に表示されます」, native frames agree): every claimable row is read only when a non-claimable row (詳細 or CLEAR!!)
  is visible below the last claimable one on the same frame (dropped 2026-10-11: the list may continue below the screen);
- every claimable row is complete (a/a), shows 受け取る and is a known DAILY mission (all daily rewards are free
  training tickets, フェザージュエル or マニー; an unknown title stops);
- one press per observed claimable set and run (2026-10-12 user decision: rewards may be bulk-claimed several times a
  day, e.g. after half of the missions and again after the rest). A confirmed claim does not block a later run that
  reads NEW claimable rows. An ``uncertain`` record (press possibly sent, outcome unproven) blocks every new press until
  a fresh daily page resolves it (``reobserved_state``): the recorded titles are no longer claimable -> ``received``;
  all of them still claimable with the pink button -> ``resolved_unclaimed``; anything else keeps the block.
  A stop before the press left the controller (node disabled, guard failed, frame refused) is ``not_sent``, never
  uncertain.

Result: the dialog must carry the positive body and only free item kinds (``present_claim.item_kind``); completion is
proven only by a fresh daily page without any 受け取る, a grey 一括受取 and CLEAR!! on every claimed title still visible.
Rewards of other tabs (e.g. イベント with its own badge) may be included by the game; they are received, never spent,
and reported as ``receipt_scope_wider``.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from agent.core import mission_status as ms
from agent.core import present_claim as pc

PRIVATE = Path(__file__).resolve().parents[2] / "dev_assets" / "private"
LEDGER_PATH = PRIVATE / "mission-claim" / "bulk-ledger.json"
DAILY_TITLES = (
    "ゲームにログインしよう", "マニーショップで買い物をしよう", "フェスをプレイしよう",
    "サポートアイドルをトレーニングしよう", "プロデュースをプレイしよう",
    "デイリーミッションを全てクリアしよう",   # 2026-10-14 live: the all-clear row (4/4) after the other five
)
# ウィークリー (2026-10-15 user: claim weekly rewards in the same visit; native 20261014T133952681572Z, 6 rows;
# wikiwiki ミッション: rewards フェザージュエル / training tickets; 「※更新は日曜05:00」).
WEEKLY_TITLES = (
    "フェスアイドルを誕生させよう", "フェスアイドルを3人誕生させよう", "フェスで勝利しよう",
    "フェスを5回プレイしよう", "フェスを15回プレイしよう", "ウィークリーミッションを全てクリアしよう",
)
TAB_TITLES = {"daily": DAILY_TITLES, "weekly": WEEKLY_TITLES}
# Selected tab: pink label pixels left of the red count badge (2026-10-15: selected 491–624, unselected 0).
TAB_ROIS = {"daily": (20, 93, 130, 58), "weekly": (20, 178, 130, 58)}
TAB_MIN_PINK = 200


def selected_tab(rgb):
    """``daily`` / ``weekly`` when exactly one of the two tab labels is pink on this RGB frame, else None."""
    import numpy as np
    hits = []
    for tab, (x, y, w, h) in TAB_ROIS.items():
        p = np.asarray(rgb)[y:y + h, x:x + w].astype(int)
        pink = (p[:, :, 0] >= 230) & (p[:, :, 1] >= 40) & (p[:, :, 1] <= 140) & (p[:, :, 2] >= 110) & (p[:, :, 2] <= 200)
        if int(pink.sum()) >= TAB_MIN_PINK:
            hits.append(tab)
    return hits[0] if len(hits) == 1 else None


def weekly_cycle_id(now) -> str:
    """Weekly reset Sunday 05:00 JST: the Sunday (JST, 05:00 boundary) that started this week, as 'W-YYYY-MM-DD'."""
    import datetime as dt
    if now.tzinfo is None:
        raise ValueError("now 必须带时区")
    jst = now.astimezone(dt.timezone(dt.timedelta(hours=9))) - dt.timedelta(hours=5)
    start = jst.date() - dt.timedelta(days=(jst.weekday() + 1) % 7)
    return "W-" + start.isoformat()


def top_incomplete(rows):
    """2026-10-15 user: progress = the topmost unfinished row (rewards sort above it, CLEAR!! rows below)."""
    rest = sorted((r for r in rows if r.get("status") == "incomplete"), key=lambda r: r.get("title_y", 0))
    return {"title": rest[0]["title"], "progress": rest[0].get("progress")} if rest else None


FREE_KINDS = frozenset({"training_ticket", "jewel", "money", "skip_ticket"})
BULK_ROI = (1095, 622, 165, 80)
BULK_TEXTS = ("一括受取", "一括受け取り")
RESULT_ROI = (380, 255, 520, 210)          # the item box of the result dialog (native: 「トレーニングチケット ×2」 at y ~363)
RESULT_BODY = "以下の報酬を受け取りました"
QUANTITY_PAT = re.compile(r"^(?P<name>.+?)[x×X](?P<qty>\d{1,7})$")


def _norm(text):
    return re.sub(r"\s+", "", text or "")


@dataclasses.dataclass(frozen=True)
class DailyList:
    rows: tuple            # mission_status.parse_rows rows (title, progress, button, status, title_y)
    claimable: tuple       # titles of claimable rows, top to bottom
    terminated: bool       # a 詳細 / CLEAR!! row is visible below the last claimable row
    bulk_buttons: int

    def to_dict(self):
        return {"rows": [dict(r) for r in self.rows], "claimable": list(self.claimable),
                "terminated": self.terminated, "bulk_buttons": self.bulk_buttons}


def read_list(list_results, page_results=(), titles=DAILY_TITLES):
    """Daily list rows (list OCR) and the 一括受取 label (page OCR) of one frame."""
    # Banners (the mission pass 「…パス」 / 「購入可能！」, 2026-10-11 live) carry neither progress nor a button.
    rows = [r for r in ms.parse_rows(list_results) if not (r["progress"] is None and r["button"] is None)]
    # A known daily mission with 受け取る whose progress is cut off at the list edge (2026-10-11 live: the training row)
    # is claimable: every daily reward is free and the result dialog is checked for free kinds anyway.
    rows = [dict(r, status="claimable") if r["status"] == "unknown" and r["button"] == ms.CLAIM_TEXT
            and _norm(r["title"]) in titles else r for r in rows]
    claimable = tuple(_norm(r["title"]) for r in rows if r["status"] == "claimable")
    last = max((r["title_y"] for r in rows if r["status"] == "claimable"), default=None)
    terminated = last is not None and any(r["title_y"] > last and r["button"] in (ms.DETAIL_TEXT, "CLEAR!!")
                                          for r in rows)
    bulk = sum(1 for i in pc._items(page_results) if pc._inside(i["box"], BULK_ROI) and i["text"] in BULK_TEXTS)
    return DailyList(tuple(rows), claimable, terminated, bulk)


def decide(daily: DailyList, *, page_guard: bool, overlay: bool, bulk_enabled, ledger_block, frame,
           enabled: bool = True, titles=DAILY_TITLES):
    """present_claim.Decision: ``claim`` (target mission-bulk-receive), ``empty`` (nothing to receive) or ``stop``."""
    reasons = []
    if not enabled:
        reasons.append("disabled")
    if frame is None or not frame.valid():
        reasons.append("frame_unbound")
    if not page_guard:
        reasons.append("daily_page_not_proven")
    if overlay:
        reasons.append("overlay_present")
    if ledger_block:
        reasons.append(f"ledger:{ledger_block}")
    if reasons:
        return pc.Decision("stop", tuple(reasons), frame=frame, rows=len(daily.claimable))
    if not daily.claimable:
        if any(r["status"] in ("claimed", "incomplete") for r in daily.rows) and bulk_enabled is not True:
            return pc.Decision("empty", ("no_claimable_daily_rows",), frame=frame)
        return pc.Decision("stop", ("no_claimable_rows_unproven",), frame=frame)
    # 2026-10-11: no longer required that a 詳細/CLEAR row shows below the last claimable one. The daily tab holds only the
    # five known missions, the game's 一括受取 takes all of them, and the result dialog must list only free kinds.
    unknown = [t for t in daily.claimable if t not in titles]
    if unknown:
        reasons.append("unknown_daily_mission")
    if any(r["status"] == "unknown" and r["button"] == ms.CLAIM_TEXT for r in daily.rows):
        reasons.append("claim_row_unreadable")
    if daily.bulk_buttons != 1:
        reasons.append(f"bulk_button_count_{daily.bulk_buttons}")
    if bulk_enabled is not True:
        reasons.append("bulk_button_not_enabled")
    if reasons:
        return pc.Decision("stop", tuple(dict.fromkeys(reasons)), frame=frame, rows=len(daily.claimable))
    return pc.Decision("claim", ("free_daily_bulk_receive",), target="mission-bulk-receive", frame=frame,
                       rows=len(daily.claimable))


def classify_result(results):
    """(state, items, reasons): ``receipt`` only with the positive body and every item line a free kind."""
    items = pc._items(results)
    texts = [i["text"] for i in items]
    blockers = pc.blocking_reasons(texts)
    if blockers:
        return ("overflow" if "limit_warning" in blockers else "uncertain"), [], blockers
    if not any(RESULT_BODY in t for t in texts):
        return "uncertain", [], ["no_positive_result_text"]
    lines = []
    for item in items:
        if not pc._inside(item["box"], RESULT_ROI):
            continue
        match = QUANTITY_PAT.match(item["text"])
        if match is None:
            continue
        kind = pc.item_kind(match.group("name"))
        if kind not in FREE_KINDS:
            return "uncertain", [], [f"result_item_not_free:{match.group('name')}"]
        lines.append({"name": match.group("name"), "kind": kind, "quantity": int(match.group("qty"))})
    if not lines:
        return "uncertain", [], ["result_items_unread"]
    return "receipt", lines, []


def terminal(before: DailyList, after: DailyList, bulk_enabled):
    """``received`` when no row shows 受け取る any more, the 一括受取 button turned grey (nothing left to receive; native
    missions_after_bulk_bottom) and every claimed title still visible shows CLEAR!!. Claimed rows move to the list
    bottom, so they need not be visible."""
    if after.claimable:
        return "uncertain"
    if bulk_enabled is not False:
        return "uncertain"
    titles = {_norm(r["title"]): r for r in after.rows}
    # 2026-10-13 live: a claimed row whose progress OCR is covered (the reward icon overlaps it) reads status "unknown" but
    # still carries the CLEAR!! stamp; the stamp is the claimed proof, a 受け取る / 詳細 / unstamped row is not.
    if any(titles[t]["status"] != "claimed" and titles[t]["button"] != "CLEAR!!"
           for t in before.claimable if t in titles):
        return "uncertain"
    return "received"


def reobserved_state(titles, daily: DailyList, bulk_enabled):
    """Resolution of an ``uncertain`` bulk record from a fresh, guarded daily page (caller checks page and overlay).

    ``received``: none of the recorded titles is claimable any more and the claimable set is fully read (nothing left
    with a grey button, or a 詳細/CLEAR row below the last claimable one); a completed daily mission cannot become
    incomplete within the cycle, so not claimable means claimed. ``resolved_unclaimed``: every recorded title is still
    claimable with exactly one pink 一括受取 (the game's bulk takes everything, so nothing was received). Otherwise
    ``None``: the record keeps blocking."""
    if any(r["status"] == "unknown" and r["button"] == ms.CLAIM_TEXT for r in daily.rows):
        return None
    claimable = set(daily.claimable)
    if titles is None:   # legacy record without titles: only a page with nothing left to claim resolves it
        if not claimable and bulk_enabled is False and any(r["status"] == "claimed" for r in daily.rows):
            return "received"
        return None
    titles = {_norm(t) for t in titles}
    if not titles:
        return None
    if titles <= claimable and bulk_enabled is True and daily.bulk_buttons == 1:
        return "resolved_unclaimed"
    if titles & claimable:
        return None
    visible = {_norm(r["title"]): r for r in daily.rows}
    if any(visible[t]["status"] not in ("claimed",) for t in titles if t in visible):
        return None
    if not claimable and bulk_enabled is False:
        return "received"
    if claimable and daily.terminated:
        return "received"
    return None


class MissionBulkLedger(pc.PresentLedger):
    """The bulk ledger: present ledger states plus ``not_sent`` (pending closed before any press left the controller)
    and the re-observation outcomes of ``uncertain`` (``received`` / ``resolved_unclaimed``). Records carry the claimed
    ``titles``. Nothing counts per day: only blocking states (pending, receipt_observed, uncertain, overflow, error)
    written by the same run stop a new press; an earlier run's record only warns."""

    STATES = pc.STATES | {"not_sent", "resolved_unclaimed"}
    TRANSITIONS = {
        "pending": pc.TRANSITIONS["pending"] | {"not_sent"},
        "receipt_observed": pc.TRANSITIONS["receipt_observed"],
        "uncertain": frozenset({"received", "resolved_unclaimed"}),
    }

    def begin_bulk(self, cycle, decision, now, titles):
        return self.begin(cycle, decision, now, extra={"titles": list(titles)})

    def reobserve(self, cycle, daily: DailyList, bulk_enabled, now):
        """Resolve this cycle's ``uncertain`` records from a fresh guarded page; returns [(id, state), ...]."""
        resolved = []
        for record in [r for r in self.records if r["cycle"] == cycle and r["state"] == "uncertain"]:
            state = reobserved_state(record.get("titles"), daily, bulk_enabled)
            if state == "resolved_unclaimed" and "receipt_observed" in record["history"]:
                state = None   # a receipt was read: rows still claimable contradict it, keep the block
            if state is not None:
                self.transition(record["id"], state, now, "reobserved_daily_page")
                resolved.append((record["id"], state))
        return resolved
