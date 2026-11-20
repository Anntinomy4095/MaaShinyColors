#!/usr/bin/env python3
"""Go-home page registry: the extra pages ``SC.Common.GoHome`` can back out of with the shared back key (nav-back).

The shared back key is table action ``nav-back`` [20,628,74,66]. Every Click node must be bound to a table action
(``tools/check_project.py``), and the host refuses a table whose hash differs from the file it loaded, so these new
Back nodes ship as a *proposal* (``docs/proposals/go_home_pages_20261009.json``) until the table can be rebuilt:

    .venv\\Scripts\\python.exe tools\\go_home_pages.py --write            # regenerate the proposal JSON
    .venv\\Scripts\\python.exe tools\\go_home_pages.py --check            # exit 1 when the proposal is stale
    .venv\\Scripts\\python.exe tools\\go_home_pages.py --apply            # pipeline + table (needs --i-know-table-changes)
    .venv\\Scripts\\python.exe tools\\replay_common_pipeline.py --proposal  # offline replay with the proposal merged

``--apply`` writes ``common/pages.json``, patches ``SC.Nav.BackOne`` in ``common/navigation.json`` and appends the new
node names to the ``nav-back`` entry of ``assets/input/pipeline_actions.json`` (text patch, formatting kept). Run it only
when no host or W.I.N.G. run is using the table, then rebuild the host. Offline only; never sends input.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "assets" / "resource" / "base" / "pipeline"
COMMON = PIPELINE / "common"
TABLE = ROOT / "assets" / "input" / "pipeline_actions.json"
PROPOSAL = ROOT / "docs" / "proposals" / "go_home_pages_20261009.json"
BACK_ACTION = "nav-back"
BACK_RECT = [20, 628, 74, 66]
BACK_UNDIMMED = "SC.Common.Color.BackUndimmed"
PAGES_FILE = "common/pages.json"
NAVIGATION_FILE = "common/navigation.json"

# Static header text only: titles and tab labels, never numbers, badges or portraits.
ANCHORS = {
    "SC.Common.Anchor.MatchStages.Title": {"recognition": "OCR", "roi": [38, 10, 220, 52], "expected": ["^ステージ選択$"],
                                           "threshold": 0.5},
    "SC.Common.Anchor.MatchStages.Progress": {"recognition": "OCR", "roi": [160, 90, 90, 40], "expected": ["^達成率$"],
                                              "threshold": 0.5},
    "SC.Common.Anchor.ProduceSetup.SelectTitle": {"recognition": "OCR", "roi": [38, 10, 230, 52],
                                                  "expected": ["^プロデュース選択$"], "threshold": 0.5},
    "SC.Common.Anchor.ProduceSetup.UnitTitle": {"recognition": "OCR", "roi": [38, 10, 230, 52],
                                                "expected": ["^ユニット編成$"], "threshold": 0.5},
    "SC.Common.Anchor.ProduceSetup.ItemTitle": {"recognition": "OCR", "roi": [38, 10, 330, 52],
                                                "expected": ["^プロデュースアイテム準備$"], "threshold": 0.5},
    # The three-tab strip exists only on the pre-start setup pages (プロデュース選択 | ユニット編成 | アイテム選択).
    "SC.Common.Anchor.ProduceSetup.TabUnit": {"recognition": "OCR", "roi": [740, 10, 150, 45],
                                              "expected": ["^ユニット編成$"], "threshold": 0.5},
    "SC.Common.Anchor.ProduceSetup.TabItem": {"recognition": "OCR", "roi": [980, 10, 150, 50],
                                              "expected": ["^アイテム選択$"], "threshold": 0.5},
    # 2026-10-13 (pending --apply): pages the user may leave the game on; only the back key, never a page button.
    "SC.Common.Anchor.ItemList.Title": {"recognition": "OCR", "roi": [38, 10, 260, 52], "expected": ["^アイテム[一ー－-]?覧$"],
                                        "threshold": 0.5},
    "SC.Common.Anchor.ItemList.ConsumeTab": {"recognition": "OCR", "roi": [20, 92, 166, 72],
                                             "expected": ["消費アイテム"], "threshold": 0.5},
    "SC.Common.Anchor.Gacha.Title": {"recognition": "OCR", "roi": [38, 10, 190, 52], "expected": ["^ガシャ$"],
                                     "threshold": 0.5},
    "SC.Common.Anchor.Gacha.Rates": {"recognition": "OCR", "roi": [980, 660, 130, 50], "expected": ["^提供割合$"],
                                     "threshold": 0.5},
    # 2026-10-26 live (popup-samples-20261026/event-mission-receipt-on-training-event.png): the 育成イベント page the
    # 「イベント累計報酬獲得」 OK opens. Header label, 遊び方 and 報酬アイドル (static text; the logo is art).
    "SC.Common.Anchor.TrainingEvent.Kind": {"recognition": "OCR", "roi": [110, 14, 175, 64], "expected": ["^育成イベント$"],
                                            "threshold": 0.5},
    "SC.Common.Anchor.TrainingEvent.Help": {"recognition": "OCR", "roi": [60, 218, 110, 48], "expected": ["^遊び方$"],
                                            "threshold": 0.5},
    "SC.Common.Anchor.TrainingEvent.RewardIdol": {"recognition": "OCR", "roi": [170, 218, 160, 48],
                                                  "expected": ["^報酬アイドル$"], "threshold": 0.5},
}
# Present: either list tab is lit (pink) and the title reads プレゼント; the footer text is absent on the received tab.
EXTRA_NODES = {
    "SC.Nav.Present.Tab": {"recognition": "Or",
                           "any_of": ["SC.Present.Color.UnclaimedTab", "SC.Present.Color.ReceivedTab"]},
}


@dataclass(frozen=True)
class Page:
    key: str
    zh: str
    guard: tuple          # anchors of the page guard (And); a str entry is a node name, ("from", node) copies its all_of
    lands: tuple          # guards of the pages this Back may legitimately land on (plus Home/loading/popups)
    note: str
    timeout: int = 10000  # ms the Back waits for its Settled page


HOME = "SC.Common.Home.Guard"
PAGES = (
    Page("Present", "プレゼント箱", ("SC.Present.Guard.Title", "SC.Nav.Present.Tab", BACK_UNDIMMED), (HOME,),
         "未受取/受取済タブ + 左下返回键未变暗；一括受取确认框、领取结果弹窗不满足（返回键变暗）→ 停止"),
    Page("IdolList", "アイドル一覧", (("from", "SC.CardScan.List.Guard"),), ("SC.Nav.IdolPortal.Guard",),
         "标题 + 筛选 + 当前标签颜色 + 返回键；回到アイドル页"),
    Page("KnowhowList", "ノウハウブック一覧", (("from", "SC.KnowhowScan.List.Guard"),),
         ("SC.Nav.IdolList.Guard", "SC.Nav.IdolPortal.Guard"),
         "标题 + 所持数 + 返回键未变暗（弹窗/並び替え/絞り込み遮暗时不满足）；回到アイドル一覧（2026-10-12 实机）"),
    Page("Business", "営業 Top", (("from", "SC.Business.Top.Guard"),), (HOME,),
         "営業 Top 守卫（标题 + Talk + 返回键）；结果报告页不在此列（business_report_pending 停止）"),
    Page("MatchStages", "Match ステージ選択", ("SC.Common.Anchor.MatchStages.Title", "SC.Common.Anchor.MatchStages.Progress",
                                              BACK_UNDIMMED), ("SC.Nav.MatchMain.Guard",),
         "标题 ステージ選択 + 達成率 + 返回键；返回 MATCH 主页（区域列表）"),
    Page("GradeDifficulty", "Grade 难度选择", (("from", "SC.Grade.Difficulty.Guard"), BACK_UNDIMMED),
         ("SC.Nav.FesMain.Guard",), "返回键在弹窗外，不改任何难度/まとめて消費；落点 Fes 主页（或模式选择）"),
    Page("GradeUnit", "Grade 参加ユニット（テンション決定之前）", (("from", "SC.Grade.Unit.Guard"),),
         ("SC.Grade.Difficulty.Guard", "SC.Nav.FesMain.Guard"),
         "テンション決定之前的参加ユニット页；决定后（Tension/Live）由 SC.Common.Stop.GradeLive 停止"),
    Page("ProduceSelect", "Produce プロデュース選択（开始前）",
         ("SC.Common.Anchor.ProduceSetup.SelectTitle", "SC.Common.Anchor.ProduceSetup.TabUnit",
          "SC.Common.Anchor.ProduceSetup.TabItem", BACK_UNDIMMED), (HOME,),
         "三标签条 + 页首标题；进行中的 Produce（Hub 等）没有标签条，由 produce_in_progress 停止"),
    Page("ProduceUnit", "Produce ユニット編成（开始前）",
         ("SC.Common.Anchor.ProduceSetup.UnitTitle", "SC.Common.Anchor.ProduceSetup.TabUnit",
          "SC.Common.Anchor.ProduceSetup.TabItem", BACK_UNDIMMED), ("SC.Nav.ProduceSelect.Guard",), "返回プロデュース選択"),
    Page("ProduceItem", "Produce アイテム選択（开始前）",
         ("SC.Common.Anchor.ProduceSetup.ItemTitle", "SC.Common.Anchor.ProduceSetup.TabUnit",
          "SC.Common.Anchor.ProduceSetup.TabItem", BACK_UNDIMMED), ("SC.Nav.ProduceUnit.Guard",),
         "返回ユニット編成；开始确认框（体力 30）在更后面，不属于本页"),
    Page("ItemList", "アイテム一覧（首页メニュー）",
         ("SC.Common.Anchor.ItemList.Title", "SC.Common.Anchor.ItemList.ConsumeTab", BACK_UNDIMMED), (HOME,),
         "标题 + 左侧「消費アイテム」标签文字 + 返回键未变暗；不点「使う」/标签；使用确认框遮暗返回键→停止"),
    Page("Gacha", "ガシャ",
         ("SC.Common.Anchor.Gacha.Title", "SC.Common.Anchor.Gacha.Rates", BACK_UNDIMMED), (HOME,),
         "标题 ガシャ + 「提供割合」+ 返回键未变暗；只按返回键，不点任何抽卡/购买按钮；购买确认框遮暗返回键→停止"),
    # 2026-10-22 live: the shop opens on プレミアム (ジュエルSALE); after a failed Shop run every later task started there.
    Page("ShopPremium", "ショップ プレミアム标签",
         (("from", "SC.Shop.Premium.Guard"), "SC.Common.Anchor.ShopList.Back", BACK_UNDIMMED), (HOME,),
         "ショップ标题 + プレミアム选中 + マニー未选 + 特定商取引法表示 + 返回键未变暗；只按返回键，不点任何商品/购买按钮"),
    # 2026-10-25 live: the WeeklyProduce read-only sample stops on this page (row1_empty / input_disabled).
    Page("AutoSchedule", "オートスケジュール（周常 Auto，开始前）", (("from", "SC.WeeklyProduce.Schedule.Guard"),),
         ("SC.Nav.ProduceSelect.Guard",),
         "标题 + プロデュース回数 + 決定 + 返回键未变暗；只按返回键，不点 ±/決定/設定；开始确认框遮暗返回键→停止",
         # 2026-10-30 live: after ← the schedule stayed under the bottom-right 「Now Loading」 strip for ~20 s before
         # プロデュース選択 showed (the strip also sits on usable pages, so it is not treated as loading).
         timeout=30000),
    # 2026-10-26 live: Produce end -> home 「イベント累計報酬獲得」 OK (user: Maa handles it) -> this page.
    Page("TrainingEvent", "育成イベント页（活动累计奖励 OK 之后）",
         ("SC.Common.Anchor.TrainingEvent.Kind", "SC.Common.Anchor.TrainingEvent.Help",
          "SC.Common.Anchor.TrainingEvent.RewardIdol", BACK_UNDIMMED), (HOME,),
         "育成イベント + 遊び方 + 報酬アイドル + 返回键未变暗；只按返回键，不点イベントショップ/イベントミッション/プロデュース；"
         "奖励收据弹窗遮暗返回键→先由弹窗处理"),
    # 2026-11-22 live: after a leftover Collabo LIVE REPORT is closed (SC.Startup.CollaboReport.Close) the game shows
    # the Collabo pages, which no go-home page knew (Startup: unknown_or_unhandled_state).
    Page("CollaboEvent", "Collabo 活动页", (("from", "SC.Collabo.Event.Guard"), "SC.Collabo.Anchor.Deadline", "SC.Common.Anchor.FesMain.Back", BACK_UNDIMMED), (HOME,),
         "コラボフェスパス 页；只按返回键，不点会场/开始"),
    Page("CollaboDifficulty", "Collabo 难度选择", (("from", "SC.Collabo.Difficulty.Guard"), "SC.Common.Anchor.FesMain.Back", BACK_UNDIMMED),
         ("SC.Nav.CollaboEvent.Guard",), "不改难度/等级；落点 Collabo 活动页"),
    Page("CollaboUnit", "Collabo 编队选择", (("from", "SC.Collabo.Unit.Guard"), "SC.Common.Anchor.FesMain.Back", BACK_UNDIMMED),
         ("SC.Nav.CollaboDifficulty.Guard", "SC.Nav.CollaboEvent.Guard"), "不点ライブ；落点难度选择"),
)
STOPS_PREFIX = "SC.Common.Stop."


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def all_nodes(pipeline=PIPELINE):
    nodes = {}
    for path in sorted(Path(pipeline).rglob("*.json")):
        for name, node in load(path).items():
            nodes.setdefault(name, node)
    return nodes


def page_guard_name(page):
    return f"SC.Nav.{page.key}.Guard"


def _guard_all_of(page, nodes):
    items = []
    for entry in page.guard:
        if isinstance(entry, tuple):
            items.extend(nodes[entry[1]]["all_of"])
        else:
            items.append(entry)
    out = []
    for item in items:  # keep order, drop duplicates
        if item not in out:
            out.append(item)
    if BACK_UNDIMMED not in out:
        out.append(BACK_UNDIMMED)
    return out


def popup_settled_base(nodes):
    """The known-popup and loading guards every Settled list shares (from an existing Settled node)."""
    base = nodes["SC.Nav.TrainingSelected.Settled"]["any_of"]
    return [name for name in base if not name.startswith("SC.Nav.")]


def build_pages(nodes):
    """Nodes of ``common/pages.json``: anchors, guards, disabled Back clicks and their Settled lists."""
    out = copy.deepcopy(ANCHORS)
    out.update(copy.deepcopy(EXTRA_NODES))
    popups = popup_settled_base(nodes)
    for page in PAGES:
        guard = page_guard_name(page)
        all_of = _guard_all_of(page, nodes)
        out[guard] = {"recognition": "And", "all_of": all_of}
        settled = [HOME] + [n for n in popups if n != HOME] + list(page.lands)
        out[f"SC.Nav.{page.key}.Back"] = {
            "enabled": False, "recognition": "And", "all_of": list(all_of), "action": "Click",
            "target": list(BACK_RECT), "pre_delay": 0, "post_delay": 500, "max_hit": 3, "timeout": page.timeout,
            "next": [f"SC.Nav.{page.key}.Settled"], "on_error": ["SC.Common.FailTask"],
            "focus": {"Node.Action.Starting": f"返回上一页（{page.zh}）"}}
        out[f"SC.Nav.{page.key}.Settled"] = {"recognition": "Or", "any_of": settled, "pre_delay": 0, "post_delay": 0}
    return out


def build_back_one(current, pages):
    """``SC.Nav.BackOne`` with the new pages inserted before the reasoned stops (which stay last)."""
    node = copy.deepcopy(current)
    names = [page_guard_name(p) for p in PAGES]
    backs = [f"SC.Nav.{p.key}.Back" for p in PAGES]

    def insert(items, new, marker):
        items = [i for i in items if i not in new]
        index = next((n for n, i in enumerate(items) if i.startswith(marker)), len(items))
        return items[:index] + new + items[index:]

    node["any_of"] = insert(node["any_of"], names, STOPS_PREFIX)
    node["next"] = insert(node["next"], backs, STOPS_PREFIX if any(i.startswith(STOPS_PREFIX) for i in node["next"])
                          else "SC.Nav.NotHandled")
    return node


def build_proposal(pipeline=PIPELINE):
    nodes = all_nodes(pipeline)
    pages = build_pages(nodes)
    navigation = load(Path(pipeline) / NAVIGATION_FILE)
    back_nodes = [f"SC.Nav.{p.key}.Back" for p in PAGES]
    return {
        "version": 1,
        "generated_by": "tools/go_home_pages.py",
        "why_a_proposal": "Click nodes must be bound in assets/input/pipeline_actions.json and the host compares its table "
                          "hash with the file; the table was in its W.I.N.G. live state when this was written.",
        "apply": ".venv\\Scripts\\python.exe tools\\go_home_pages.py --apply --i-know-table-changes",
        "table_bindings": {BACK_ACTION: back_nodes},
        "pipeline": {PAGES_FILE: pages,
                     NAVIGATION_FILE: {"SC.Nav.BackOne": build_back_one(navigation["SC.Nav.BackOne"], pages)}},
    }


def page_targets():
    """Click node -> target rect of the pages file (for the shared contract tests)."""
    return {f"SC.Nav.{p.key}.Back": list(BACK_RECT) for p in PAGES}


def merged_pipeline(proposal):
    """{node: definition} of every proposal pipeline node (what the replay passes as ``pipeline_override``)."""
    merged = {}
    for nodes in proposal["pipeline"].values():
        merged.update(copy.deepcopy(nodes))
    return merged


def patch_table_text(text, bindings):
    """Append node names to the ``nodes`` array of the named actions; the rest of the file stays byte-identical."""
    for action_id, names in bindings.items():
        match = re.search(r'("id":\s*"%s".*?"nodes":\s*\[)([^\]]*)(\])' % re.escape(action_id), text, re.S)
        if match is None:
            raise ValueError(f"动作表没有动作 {action_id}")
        existing = re.findall(r'"([^"]+)"', match.group(2))
        add = [n for n in names if n not in existing]
        if not add:
            continue
        body = match.group(2).rstrip() + "".join(f', "{n}"' for n in add) if existing else \
            ", ".join(f'"{n}"' for n in add)
        text = text[:match.start(2)] + body + text[match.end(2):]
    return text


def _dump(data):
    return json.dumps(data, ensure_ascii=False, indent=4) + "\n"


def apply(proposal, pipeline=PIPELINE, table=TABLE, *, write_table=True):
    pipeline = Path(pipeline)
    (pipeline / PAGES_FILE).write_text(_dump(proposal["pipeline"][PAGES_FILE]), encoding="utf-8", newline="\n")
    nav_path = pipeline / NAVIGATION_FILE
    navigation = load(nav_path)
    navigation.update(proposal["pipeline"][NAVIGATION_FILE])
    nav_path.write_text(_dump(navigation), encoding="utf-8", newline="\n")
    if write_table:
        table = Path(table)
        raw = table.read_bytes().decode("utf-8")
        patched = patch_table_text(raw, proposal["table_bindings"])
        json.loads(patched)
        table.write_bytes(patched.encode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true", help="regenerate docs/proposals/go_home_pages_20261009.json")
    parser.add_argument("--check", action="store_true", help="exit 1 when the proposal file is stale")
    parser.add_argument("--apply", action="store_true", help="write pages.json, patch BackOne and the action table")
    parser.add_argument("--i-know-table-changes", action="store_true",
                        help="confirm no host/W.I.N.G. run uses assets/input/pipeline_actions.json")
    args = parser.parse_args(argv)
    proposal = build_proposal()
    text = _dump(proposal)
    if args.write:
        PROPOSAL.parent.mkdir(parents=True, exist_ok=True)
        PROPOSAL.write_text(text, encoding="utf-8", newline="\n")
    if args.check:
        if not PROPOSAL.is_file() or PROPOSAL.read_text(encoding="utf-8") != text:
            print("proposal is stale; run tools/go_home_pages.py --write")
            return 1
    if args.apply:
        if not args.i_know_table_changes:
            print("--apply edits assets/input/pipeline_actions.json; add --i-know-table-changes after the live run")
            return 2
        apply(load(PROPOSAL))
        print("applied: rebuild the host, then run the tests and tools/check_project.py")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
