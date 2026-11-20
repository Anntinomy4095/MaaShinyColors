"""Bind the KnowhowScan Pipeline to the action table (offline; no game input).

``assets/resource/base/pipeline/knowhow_scan.json`` ships the K3 scan with ``input_action_missing:<id>`` placeholders
(no Click) and every runnable node disabled. This tool produces the bound pair from that file and the current table:

- ``pipeline_actions.json``: the ``knowhow-scan`` family and its entries (``knowhow_scan.ACTIONS``, all enabled:
  every rect is measured on a native frame; the wheel *step* is what the calibration mode measures), plus the
  KnowhowScan navigation nodes on ``home-idol`` / ``idol-portal-list`` / ``nav-back``. Other entries are untouched.
- ``knowhow_scan.json``: each placeholder becomes a table-bound ``Click`` (``target`` = the action rect, still
  ``enabled: false`` — only the supervised KnowhowScan entry of tools/run_tasks.py enables them by action id);
  non-click nodes are enabled; ``SC.KnowhowScan.Main`` closes the shared known pop-ups like CardScan.
- Drifting-grid tap slots (``knowhow_grid.SLOT_ACTIONS``): one Want recognition + one table-bound
  ``Click`` node per slot (``SC.KnowhowScan.Grid.OpenSlot.Y{k}C{c}``, ``enabled: false``, guarded by
  ``SC.KnowhowScan.List.Guard``), listed in ``SC.KnowhowScan.Main`` after the fixed tiles. The fixed-tile Want nodes
  carry ``"key": null`` so they never match a slot decision (the scan's ``open_tile`` names its slot in ``key``; the
  calibration mode keeps the fixed ``knowhow-tile-r*c*`` taps on the top screen).

Default: write both files plus ``DIFF.md`` to ``dev_assets/private/pending-20261012-knowhow/`` (a live run keeps the
current table). ``--apply`` writes them into the repository (host: 「重新载入动作表」 or restart); ``--check`` exits 1
when the repository differs from the bound result.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import knowhow_grid as kg  # noqa: E402
from agent.core import knowhow_scan as ks  # noqa: E402
from agent.core.pipeline_actions import parse_table  # noqa: E402

TABLE = ROOT / "assets" / "input" / "pipeline_actions.json"
PIPELINE = ROOT / "assets" / "resource" / "base" / "pipeline" / "knowhow_scan.json"
PENDING = ROOT / "dev_assets" / "private" / "pending-20261012-knowhow"
PLACEHOLDER = "input_action_missing:"
WHEEL_POST_DELAY = 2000      # host: ≤30 inputs per action and minute; keep two seconds even before OCR time
TILE_POST_DELAY = 800
# Detail / grid wheel: short wait, then the session proves the frame settled before reading it (detail: one full notch
# measured or two identical captures; grid: two identical captures, checked on pixels before any OCR) and paces itself
# to the host limit (knowhow_scan.DETAIL_PACE / GRID_PACE); 2026-10-24.
POST_DELAYS = {"knowhow-detail-scroll-down": 600, "knowhow-grid-scroll-down": 600, "knowhow-grid-scroll-up": 600}
SETTLE_TAILS = {"knowhow-detail-scroll-down": "画面停稳由 Agent 核对（位移一整格或两帧相同），并按宿主限速自行等待。",
                "knowhow-grid-scroll-down": "画面停稳由 Agent 核对（两帧相同才做文字识别），并按宿主限速自行等待。",
                "knowhow-grid-scroll-up": "画面停稳由 Agent 核对（两帧相同才做文字识别），并按宿主限速自行等待。"}
SETTLE_POST_DELAY = 300      # SC.KnowhowScan.List.Settle: re-capture while a wheel's settle check is pending


def wheel_doc(action_id: str, wheel: int) -> str:
    delay = POST_DELAYS.get(action_id, WHEEL_POST_DELAY)
    tail = SETTLE_TAILS.get(action_id, "宿主每动作每分钟 ≤30 次；步长使用当前校准。")
    return f"表绑定滚轮 {action_id}（{wheel:+d}）；滚动后等待 {delay}ms，{tail}"


def settle_nodes(nodes: dict) -> None:
    """SC.KnowhowScan.List.Settle (+ its Want): the no-input re-capture of a wheel's settle check, with a shorter wait
    than List.Hold (loading / unreadable frames keep 800 ms). Idempotent."""
    nodes["SC.KnowhowScan.Want.Settle"] = {"recognition": "Custom", "custom_recognition": "SC.KnowhowScan.Want",
                                           "custom_recognition_param": {"want": "settle"}}
    nodes["SC.KnowhowScan.List.Settle"] = {
        "recognition": "And", "all_of": ["SC.KnowhowScan.List.Guard", "SC.KnowhowScan.Want.Settle"],
        "pre_delay": 0, "post_delay": SETTLE_POST_DELAY, "max_hit": 6000,
        "focus": {"Node.Recognition.Succeeded": "书库：滚轮后画面尚未停稳：不点击，再截一帧（有上限）。"}}
KEEP_PROTECT = "SC.KnowhowScan.Keep.Protect"


def keep_nodes(nodes: dict, table_text: str) -> None:
    """SC.KnowhowScan.Keep.Protect (+ its Want): the keep step's 保護 press on the detail panel, table-bound and
    ``enabled: false`` (run_tasks admits knowhow-detail-protect only for a run with locking on). Idempotent."""
    action = {a["id"]: a for a in json.loads(table_text)["actions"]}["knowhow-detail-protect"]
    if KEEP_PROTECT not in action["nodes"]:
        raise ValueError(f"{KEEP_PROTECT} 未列入动作 knowhow-detail-protect")
    nodes["SC.KnowhowScan.Want.KeepProtect"] = {"recognition": "Custom", "custom_recognition": "SC.KnowhowScan.Want",
                                                "custom_recognition_param": {"want": "press_protect"}}
    nodes[KEEP_PROTECT] = {
        "enabled": False, "recognition": "And", "all_of": [LIST_GUARD, "SC.KnowhowScan.Want.KeepProtect"],
        "pre_delay": 0, "post_delay": TILE_POST_DELAY, "max_hit": ks.NEWEST_MAX,
        "focus": {"Node.Action.Starting": "锁书：左栏是计划锁定的新书（✓、标题、能力、ノウハウ数一致，未保護），按一次「保護」"},
        "action": "Click", "target": list(action["rect"]),
        "doc": "表绑定 knowhow-detail-protect：只在锁书步骤、已绑定且未保護的新书上按一次；之后须左栏变为「保護解除」且该格出现锁，"
               "否则停止且不再按（结果不确定）。",
        "on_error": ["SC.KnowhowScan.Retry"], "next": ["SC.KnowhowScan.Sent.List"]}


FAMILY_AFTER = "card-scan"


def render_table(data: dict) -> str:
    """The committed layout of pipeline_actions.json (one line per action field), as tools/wing_nav_build.py."""
    dump = lambda value: json.dumps(value, ensure_ascii=False)  # noqa: E731
    head = [f'    "{key}": {dump(value)}' for key, value in data.items() if key != "actions"]
    actions = ["        {\n" + ",\n".join(f'            "{key}": {dump(value)}' for key, value in action.items())
               + "\n        }" for action in data["actions"]]
    return "{\n" + ",\n".join(head + ['    "actions": [\n' + ",\n".join(actions) + "\n    ]"]) + "\n}\n"


def table_entries() -> list[dict]:
    out = []
    for a in ks.ACTIONS:
        entry = {"id": a["id"], "family": ks.FAMILY, "consuming": False, "enabled": True, "rect": list(a["rect"])}
        if "wheel" in a:
            entry["wheel"] = a["wheel"]
        entry["source"] = a["source"]
        entry["nodes"] = list(a["nodes"])
        out.append(entry)
    return out


def sync_table(text: str) -> str:
    """Table text with the knowhow-scan family/entries (re)written and the navigation nodes added; same newline style."""
    newline = "\r\n" if "\r\n" in text else "\n"
    data = json.loads(text)
    families = [f for f in data["families"] if f != ks.FAMILY]
    at = families.index(FAMILY_AFTER) + 1 if FAMILY_AFTER in families else len(families)
    data["families"] = families[:at] + [ks.FAMILY] + families[at:]
    kept = []
    for action in data["actions"]:
        if action["family"] == ks.FAMILY:
            continue
        extra = ks.NODE_ADDITIONS.get(action["id"])
        if extra:
            action = {**action, "nodes": list(action["nodes"]) + [n for n in extra if n not in action["nodes"]]}
        kept.append(action)
    # knowhow entries follow the last card-scan entry (same neighbourhood as the family list)
    last = max((i for i, a in enumerate(kept) if a["family"] == FAMILY_AFTER), default=len(kept) - 1)
    data["actions"] = kept[:last + 1] + table_entries() + kept[last + 1:]
    rendered = render_table(data)
    parse_table(rendered.encode("utf-8"))          # same validation as the host
    return rendered.replace("\n", newline) if newline != "\n" else rendered


def bound_pipeline(nodes: dict, table_text: str) -> dict:
    """knowhow_scan.json with every placeholder bound to its table action (Click, target = rect, enabled: false)."""
    table = {a["id"]: a for a in json.loads(table_text)["actions"]}
    out = {}
    for name, node in nodes.items():
        node = copy.deepcopy(node)
        doc = str(node.get("doc", ""))
        if doc.startswith(PLACEHOLDER):
            action_id = doc[len(PLACEHOLDER):].split(" ")[0]
            action = table[action_id]
            if name not in action["nodes"]:
                raise ValueError(f"{name} 未列入动作 {action_id}")
            node.pop("doc")
            wheel = action.get("wheel")
            node.update(enabled=False, action="Click", target=list(action["rect"]))
            if wheel is not None:
                node["post_delay"] = POST_DELAYS.get(action_id, WHEEL_POST_DELAY)
                node["doc"] = wheel_doc(action_id, wheel)
            elif action_id.startswith("knowhow-tile-"):
                node["post_delay"] = TILE_POST_DELAY
            focus = node.get("focus") or {}
            if "Node.Recognition.Succeeded" in focus:
                focus = {"Node.Action.Starting": focus["Node.Recognition.Succeeded"]}
            node["focus"] = focus
            node = {k: node[k] for k in sorted(node, key=lambda k: (k == "next", k == "on_error"))}
        elif node.get("enabled") is False and "action" not in node and name.endswith((".Entry",)):
            node.pop("enabled")
        elif node.get("enabled") is False and node.get("action") != "Click":
            node.pop("enabled")
        out[name] = node
    main = out["SC.KnowhowScan.Main"]
    if "[JumpBack]SC.Popup.CloseKnown" not in main["next"]:
        at = main["next"].index("SC.KnowhowScan.Halted") + 1
        main["next"].insert(at, "[JumpBack]SC.Popup.CloseKnown")
    entry = out["SC.KnowhowScan.Entry"]
    entry["focus"] = {"Node.Action.Starting": (
        "ノウハウブック监督扫描（仅 tools/run_tasks.py --live --supervised-task 的 KnowhowScan 单次入口可运行；点击节点默认禁用，"
        "按动作 id 放行）：首页→アイドル→アイドル一覧→ノウハウブック。校准模式只滚轮测步长、打开少量书；扫描模式切评价值顺"
        "→逐屏读网格与详情→恢复原排序。读新书且开启锁书时只对计划内的新书按一次「保護」；不売却/マーク/発現，每帧存私有原图。")}
    return out


def slot_nodes(nodes: dict, table_text: str) -> dict:
    """``nodes`` with the OpenSlot Want/Click nodes (re)generated after the fixed tiles and listed in Main."""
    table = {a["id"]: a for a in json.loads(table_text)["actions"]}
    generated = {}
    for a in kg.SLOT_ACTIONS:
        node = ks.slot_node(a["id"])
        k, c = node.rsplit(".", 1)[1][1:].split("C")
        want = f"SC.KnowhowScan.Want.GridOpenSlotY{k}C{c}"
        action = table[a["id"]]
        if node not in action["nodes"]:
            raise ValueError(f"{node} 未列入动作 {a['id']}")
        generated[want] = {"recognition": "Custom", "custom_recognition": "SC.KnowhowScan.Want",
                           "custom_recognition_param": {"want": "open_tile", "key": a["id"]}}
        generated[node] = {
            "enabled": False, "recognition": "And", "all_of": ["SC.KnowhowScan.List.Guard", want],
            "pre_delay": 0, "post_delay": TILE_POST_DELAY, "max_hit": 600,
            "focus": {"Node.Action.Starting": f"书库网格：点第{int(c) + 1}列 y 槽 {k}（槽顶 {kg.SLOT_TOPS[int(k)]}）上的书"
                                              "（逐帧定位的格子，只读详情）"},
            "action": "Click", "target": list(action["rect"]),
            "doc": f"表绑定 {a['id']}：漂移网格点击槽（knowhow_grid.slot_for 按定位到的格子选槽，矩形在封面安全区内）；"
                   "点击后须该格出现 ✓ 且左栏详情与该格一致，否则停止。",
            "on_error": ["SC.KnowhowScan.Retry"], "next": ["SC.KnowhowScan.Sent.List"]}
    out = {}
    anchor = f"SC.KnowhowScan.Grid.OpenTile.R{ks.ROWS - 1}C{ks.COLS - 1}"
    for name, node in nodes.items():
        if name in generated:
            continue
        node = copy.deepcopy(node)
        param = node.get("custom_recognition_param")
        if name.startswith("SC.KnowhowScan.Want.GridOpenTileR") and isinstance(param, dict):
            node["custom_recognition_param"] = {**{k: v for k, v in param.items() if k != "key"}, "key": None}
        out[name] = node
        if name == anchor:
            out.update(generated)
    if not all(name in out for name in generated):
        raise ValueError(f"{anchor} 缺失，无法放置 OpenSlot 节点")
    main = out["SC.KnowhowScan.Main"]
    slots = [f"[JumpBack]{ks.slot_node(a['id'])}" for a in kg.SLOT_ACTIONS]
    rest = [n for n in main["next"] if n not in slots]
    at = rest.index(f"[JumpBack]{anchor}") + 1
    main["next"] = rest[:at] + slots + rest[at:]
    return out


def render_pipeline(nodes: dict) -> str:
    return json.dumps(nodes, ensure_ascii=False, indent=4) + "\n"


def diff_note(old_text: str, new_text: str) -> str:
    old, new = json.loads(old_text), json.loads(new_text)
    old_nodes = {a["id"]: a["nodes"] for a in old["actions"]}
    lines = ["# KnowhowScan 动作表切换（tools/knowhow_scan_build.py 生成）", "",
             "基于生成时仓库 `assets/input/pipeline_actions.json`；其他条目逐字保留。若仓库表已被他人改动，请重新运行本工具，"
             "不要直接覆盖。", "",
             f"- 新动作族 `{ks.FAMILY}`（位于 `{FAMILY_AFTER}` 之后）。",
             f"- 新增 {len(ks.ACTIONS)} 个动作（全部 enabled、非消费）："]
    for a in table_entries():
        lines.append(f"  - `{a['id']}` rect {a['rect']}" + (f" wheel {a['wheel']}" if "wheel" in a else "")
                     + f" → {', '.join(a['nodes'])}")
    lines.append("- 既有动作追加节点：")
    for a in new["actions"]:
        added = [n for n in a["nodes"] if n not in old_nodes.get(a["id"], a["nodes"])]
        if added and a["family"] != ks.FAMILY:
            lines.append(f"  - `{a['id']}` + {', '.join(added)}")
    lines += ["- `knowhow_scan.json`：占位节点改为表绑定 Click（仍 enabled:false），其余节点启用，Main 加 "
              "`[JumpBack]SC.Popup.CloseKnown`；30 个漂移网格点击槽 `SC.KnowhowScan.Grid.OpenSlot.Y{k}C{c}`"
              "（Want 按 key=槽动作 id 匹配，固定格 Want 带 key=null）。", "",
              "两个文件须同时切换（`click_node_actions` 要求表节点与 Pipeline 点击节点一致）："
              "`.venv\\Scripts\\python.exe tools\\knowhow_scan_build.py --apply`，之后宿主「重新载入动作表」或重启。", ""]
    return "\n".join(lines)


def build(table_text: str | None = None, pipeline_text: str | None = None):
    table_text = TABLE.read_bytes().decode("utf-8") if table_text is None else table_text
    nodes = json.loads(PIPELINE.read_text(encoding="utf-8") if pipeline_text is None else pipeline_text)
    new_table = sync_table(table_text)
    if any(str(n.get("doc", "")).startswith(PLACEHOLDER) for n in nodes.values()):
        nodes = bound_pipeline(nodes, new_table)
    for action in table_entries():
        if "wheel" not in action:
            continue
        for name in action["nodes"]:
            nodes[name]["post_delay"] = POST_DELAYS.get(action["id"], WHEEL_POST_DELAY)
            nodes[name]["doc"] = wheel_doc(action["id"], action["wheel"])
    nodes = slot_nodes(nodes, new_table)
    settle_nodes(nodes)
    keep_nodes(nodes, new_table)
    # A completed observation must back out before testing every tile. On live 2026-10-20,
    # those repeated recognitions took >5s and expired each fresh frame before List.Back.
    # 2026-10-23: the same holds on the portal / idol list (one round over the ~50 tile/slot nodes re-reads the list
    # title and took ~8.8 s, so 一覧 never got a fresh permit: frame_stale_before_permit). These navigation nodes
    # are guarded by pages exclusive of the knowhow list, so testing them first changes no decision.
    early = ["[JumpBack]SC.KnowhowScan.List.Back", "[JumpBack]SC.KnowhowScan.List.Settle",
             "[JumpBack]SC.KnowhowScan.List.Hold",
             "[JumpBack]SC.KnowhowScan.Portal.OpenList", "[JumpBack]SC.KnowhowScan.IdolList.OpenKnowhow",
             "[JumpBack]SC.KnowhowScan.IdolList.Back", "[JumpBack]SC.KnowhowScan.Portal.Back",
             "[JumpBack]SC.KnowhowScan.Home.AtHome"]
    main = nodes["SC.KnowhowScan.Main"]["next"]
    # 2026-10-24 round audit (frame-0345, detail page): 18 nodes ran before Detail.ScrollDown, ~1.0 s per detail step
    # (SC.Popup.CloseKnown alone 0.53 s). The list-page inputs of the scan loop come right after List.Hold, whose
    # capture is already observed: each tests its cached Want (peek) and then List.Guard (undimmed list page), so a
    # popup, loading screen or other page still fails them and reaches the nodes below unchanged.
    loop = ["[JumpBack]SC.KnowhowScan.Detail.ScrollDown", "[JumpBack]SC.KnowhowScan.Grid.ScrollDown",
            "[JumpBack]SC.KnowhowScan.Grid.ScrollUp"] + [f"[JumpBack]{ks.slot_node(a['id'])}" for a in kg.SLOT_ACTIONS] + [
            f"[JumpBack]{KEEP_PROTECT}"]
    rest = [name for name in main if name not in early and name not in loop]
    at = rest.index("SC.KnowhowScan.Halted") + 1
    nodes["SC.KnowhowScan.Main"]["next"] = rest[:at] + early[:3] + loop + early[3:] + rest[at:]
    peek_list_nodes(nodes)
    return new_table, render_pipeline(nodes)      # already bound: idempotent


LIST_GUARD = "SC.KnowhowScan.List.Guard"


def peek_list_nodes(nodes: dict) -> None:
    """List-page nodes after List.Hold test the Want decision first, as a cached ``peek``, then List.Guard.

    Maa tests the whole next list on one capture. List.Back / List.Hold ([List.Guard, Want]) come first, so on the list
    page that capture is already observed; the later ~50 nodes re-read the list-title OCR guard (~70 ms each) only to
    reach a cached Want. 2026-10-23 round audit (tools/audit_round_times.py): the last slot took ~4.5 s from capture,
    near the 5 s frame permit. A peek never observes; the node still needs List.Guard, so no decision changes."""
    main = [re.sub(r"^\[\w+\]", "", n) for n in nodes["SC.KnowhowScan.Main"]["next"]]
    users = {}
    for name, node in nodes.items():
        for part in node.get("all_of", []) + node.get("any_of", []):
            users.setdefault(part, set()).add(name)
    for name in main[main.index("SC.KnowhowScan.List.Hold") + 1:]:
        node = nodes.get(name, {})                          # shared nodes (SC.Popup.*) live in other files
        parts = node.get("all_of")
        if node.get("recognition") != "And" or not parts or LIST_GUARD not in parts:
            continue
        want = [p for p in parts if p != LIST_GUARD]
        if len(want) != 1 or not want[0].startswith("SC.KnowhowScan.Want.") or users[want[0]] != {name}:
            raise ValueError(f"{name} 不是 [List.Guard, 专用 Want] 结构，不能改为缓存先判")
        nodes[name]["all_of"] = [want[0], LIST_GUARD]
        param = {k: v for k, v in nodes[want[0]]["custom_recognition_param"].items() if k != "peek"}
        nodes[want[0]]["custom_recognition_param"] = {**param, "peek": True}  # last: stable across rebuilds


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="KnowhowScan 动作表/Pipeline 绑定（离线，不发送输入）")
    parser.add_argument("--out-dir", type=Path, default=PENDING, help="待切换目录（须在 dev_assets/private 下）")
    parser.add_argument("--apply", action="store_true", help="写入仓库（实机运行期间不要使用）")
    parser.add_argument("--check", action="store_true", help="仓库与绑定结果一致时返回 0")
    args = parser.parse_args(argv)
    table_text = TABLE.read_bytes().decode("utf-8")
    pipeline_text = PIPELINE.read_text(encoding="utf-8")
    new_table, new_pipeline = build(table_text, pipeline_text)
    if args.check:
        same = new_table == table_text and new_pipeline == pipeline_text
        print("KnowhowScan 绑定一致" if same else "KnowhowScan 尚未绑定或与生成结果不一致")
        return 0 if same else 1
    if args.apply:
        TABLE.write_bytes(new_table.encode("utf-8"))
        PIPELINE.write_bytes(new_pipeline.encode("utf-8"))
        print(f"wrote {TABLE.relative_to(ROOT)} and {PIPELINE.relative_to(ROOT)}")
        return 0
    out = (args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir).resolve()
    if not out.is_relative_to((ROOT / "dev_assets" / "private").resolve()):
        print("--out-dir 必须位于 dev_assets/private", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    (out / "pipeline_actions.json").write_bytes(new_table.encode("utf-8"))
    (out / "knowhow_scan.json").write_bytes(new_pipeline.encode("utf-8"))
    (out / "DIFF.md").write_text(diff_note(table_text, new_table), encoding="utf-8")
    print(f"wrote {out} (pipeline_actions.json, knowhow_scan.json, DIFF.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
