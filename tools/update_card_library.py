"""增量更新本地公共卡资料；个人持有、Lv、凸、书库和设置不在写入范围。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import shinycolorsdb_import as imp
from agent.core import shinycolorsdb_cards as converter
from agent.core.card_data_merge import _load_wiki_cache, merge_database, readiness_counts

SOURCE_REPOSITORY = "https://github.com/ShinyColorsDB/ShinyColorsDB-DataSite"
DEFAULT_ROOT = imp.DEFAULT_SNAPSHOT_ROOT


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_json(payload: Any) -> None:
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def publish(source: Path, target: Path) -> None:
    # Windows may briefly hold a newly written file/directory for antivirus indexing.
    # Only retry the atomic rename; no network request or card conversion is repeated.
    for attempt in range(5):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.1 * (attempt + 1))


def fetch_catalog(client: imp.Importer, root: Path) -> dict[str, dict]:
    """Full public catalogue, not the latest-per-idol tables. Cached indexes use ETag."""
    cache = root / "index-cache"
    idols = client.fetch_index(f"{imp.API_BASE}/info/idolList", cache)
    if not isinstance(idols, list) or not 1 <= len(idols) <= 200:
        raise imp.ImportRefused("偶像目录格式或数量异常")
    idol_ids = [row.get("idolId") for row in idols if isinstance(row, dict)]
    if (len(idol_ids) != len(idols) or any(type(i) is not int or not 1 <= i <= 9999 for i in idol_ids)
            or len(set(idol_ids)) != len(idol_ids)):
        raise imp.ImportRefused("偶像目录包含无效或重复 ID")
    rows = []
    for idol_id in idol_ids:
        info = client.fetch_index(f"{imp.API_BASE}/info/idolInfo?idolId={idol_id}", cache)
        if not isinstance(info, dict) or not isinstance(info.get("cardLists"), list) or not info["cardLists"]:
            raise imp.ImportRefused(f"偶像 {idol_id} 的卡目录不完整")
        rows.extend(info["cardLists"])
    # Reuse the existing exact ID/UUID conflict checks; never match names.
    catalog = root / "catalog.json"
    candidate = root / "catalog-candidate.json"
    write_json(candidate, rows)
    index = imp.load_index([candidate])
    if not index:
        raise imp.ImportRefused("卡目录中没有可用的精确 ID")
    if any(isinstance(row, dict) and str(row.get("cardType", ""))[:2] in ("P_", "S_")
           and row.get("enzaId") not in index for row in rows):
        raise imp.ImportRefused("部分 P/S 卡无法按精确 ID/UUID 读取，未替换目录")
    publish(candidate, catalog)
    return index


def cached_cards(root: Path) -> dict[str, dict]:
    """Newest checksum-verified raw response per card; unfinished snapshots are not active."""
    manifests = []
    for path in root.glob("snapshot-*/manifest.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifests.append((str(payload.get("generated_at") or ""), str(path), path, payload))
    found = {}
    for _, _, path, payload in sorted(manifests, reverse=True):
        if not (path.parent / "converted-cards.json").is_file():
            continue
        for entry in payload.get("entries", []):
            card_id = entry.get("enza_id")
            body_path = path.parent / entry["file"]
            if card_id in found or not body_path.is_file():
                continue
            if sha256(body_path.read_bytes()).hexdigest() != entry.get("sha256"):
                continue
            found[card_id] = entry
    return found


def update_plan(index: dict[str, dict], previous: dict[str, dict]) -> dict:
    added, changed = [], []
    for card_id, row in sorted(index.items()):
        old = previous.get(card_id)
        if old is None:
            added.append(card_id)
        elif (not row.get("card_hash") or old.get("card_hash") != row["card_hash"]
              or old.get("card_uuid") != row["card_uuid"]):
            changed.append(card_id)
    # This is a catalogue fingerprint, not a claimed upstream release/tag version.
    version_rows = [(key, row["card_uuid"], row.get("card_hash")) for key, row in sorted(index.items())]
    version = sha256(json.dumps(version_rows, separators=(",", ":")).encode()).hexdigest()
    return {"source": imp.API_BASE, "source_repository": SOURCE_REPOSITORY,
            "source_version": version, "previous_cards": len(previous), "catalog_cards": len(index),
            "added": added, "changed": changed, "absent_from_catalog": sorted(set(previous) - set(index)),
            "pending": added + changed}


def cached_wiki_cards(paths: list[Path] | None = None) -> list[dict]:
    """Use existing public Wiki evidence only; never treat merged/owned-level output as Wiki input."""
    base = imp.PRIVATE_ROOT / "true_end"
    paths = paths if paths is not None else [base / "verified-cache-cards.json",
                                            base / "reviewed-combined-cards-20261006.json"]
    cards = []
    for path in paths:
        if path.is_file():
            cards.extend(c for c in json.loads(path.read_text(encoding="utf-8")).get("cards", [])
                         if c.get("kind") in ("P", "S"))
    if paths and paths[0].parent == base:
        for log in sorted(base.glob("wiki_cache*/fetch-log.jsonl")):
            cards.extend(_load_wiki_cache(log, []))
    return cards


def apply_update(root: Path, index: dict[str, dict], report: dict, client: imp.Importer,
                 *, max_cards: int = imp.HARD_MAX_IDS, wiki_cards: list[dict] | None = None) -> dict:
    pending = report["pending"]
    if not pending:
        return {**report, "status": "unchanged", "updated_cards": 0, "rebuilt": False}
    if not 1 <= max_cards <= imp.HARD_MAX_IDS:
        raise imp.ImportRefused("每批卡明细数量必须在 1–100 之间")
    # Keep each batch bounded; only --all explicitly advances through further batches.
    selected = pending[:max_cards]
    jobs, missing = imp.plan(selected, index)
    if missing:
        raise imp.ImportRefused(f"精确映射缺失: {missing}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    stage = root / f"pending-{stamp}"
    stage.mkdir()
    client.out_dir = stage
    manifest = client.fetch(jobs)
    if any(entry.get("card_hash_changed") for entry in manifest["entries"]):
        raise imp.ImportRefused("明细与本次目录 cardHash 不一致，稍后重新检查目录")
    payload = converter.convert_snapshot(stage)
    merged = merge_database(payload, cached_wiki_cards() if wiki_cards is None else wiki_cards)
    merged["readiness_counts"] = readiness_counts(merged["cards"])
    # A new merged file must outrank older merged cards in team_recommend.load_library.
    # Old untouched cards stay in previous snapshots; no whole-library conversion or deletion.
    write_json(stage / "converted-cards.json", payload)
    write_json(stage / "merged-update.json", merged)
    result = {**report, "status": "updated", "updated_cards": len(jobs), "rebuilt": True,
              "remaining_cards": len(pending) - len(jobs), "readiness_counts": merged["readiness_counts"],
              "snapshot": f"snapshot-{stamp}-update"}
    write_json(stage / "update-report.json", result)
    publish(stage, root / result["snapshot"])
    return result


def sync_host_assets(source: Path) -> bool:
    """Portable host serves its own UI copy; syncing thumbnails does not require rebuilding it."""
    host = ROOT / "host"
    if not (host / "MaaShinyColors.BrowserHost.exe").is_file() or not (host / "ui" / "index.html").is_file():
        return False
    target = host / "ui" / "game-assets"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "manifest.js"):
        if (source / name).is_file():
            shutil.copy2(source / name, target / name)
    for name in ("cards", "items", "icons"):
        if (source / name).is_dir():
            shutil.copytree(source / name, target / name, dirs_exist_ok=True)
    return True


def refresh_icons() -> dict:
    """Optional existing thumbnail path: cached icons are reused, no host build."""
    from tools import ui_game_assets_fetch as fetch
    from tools import ui_game_assets as assets
    def signature() -> str:
        if not fetch.DB_CARDS_FILE.is_file():
            return ""
        rows = json.loads(fetch.DB_CARDS_FILE.read_text(encoding="utf-8")).get("cards", [])
        return sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    before = signature()
    fetcher = fetch.Fetcher()
    # 2026-11-22 (new install): the pinned item icons (训练券 etc.: the user-approved enza atlases and tiles, sha-checked)
    # used to come only from the developer CLI; fetch the missing ones here so the 资源 page has them too.
    items = []
    try:
        items = fetch.fetch_enza(fetcher) + fetch.fetch_tiles(fetcher)
    except Exception as exc:   # an item icon never blocks the card library
        items = [{"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:160]}"}]
    items_downloaded = sum(1 for row in items if row.get("status") == "downloaded")
    report = fetch.fetch_db(fetcher, refresh_list=True)
    report["items_downloaded"] = items_downloaded
    report["items_failed"] = sum(1 for row in items if row.get("status") not in ("present", "downloaded"))
    report["rebuilt"] = False
    # A missing thumbnail does not invalidate the successfully fetched catalogue or the other icons.
    catalog_ready = (report.get("catalog_complete") is True
                     or ("catalog_complete" not in report and not report.get("stopped")))
    missing = not (assets.OUT_DIR / "manifest.json").is_file()
    if catalog_ready and (report["downloaded"] or items_downloaded or missing or signature() != before):
        assets.build()
        report["rebuilt"] = True
        report["host_synced"] = sync_host_assets(assets.OUT_DIR)
    return report


def run_update(root: Path, *, max_cards: int = imp.HARD_MAX_IDS, all_cards: bool = False,
               with_icons: bool = False, client: imp.Importer | None = None,
               progress: Callable[[dict], None] | None = None) -> dict:
    """Fetch one catalogue and publish bounded batches, retaining an honest partial result."""
    result = {"status": "stopped", "updated_cards": 0, "rebuilt": False,
              "complete": False, "cards_complete": False, "local_cards": None,
              "remaining_cards": None, "completed_batches": 0,
              "snapshots": []}
    total_cards = None
    def emit_progress(phase: str) -> None:
        if progress is not None:
            progress({"event": "card_library_progress", "phase": phase,
                      "updated_cards": result["updated_cards"], "total_cards": total_cards,
                      "catalog_cards": result.get("catalog_cards")})
    try:
        if not 1 <= max_cards <= imp.HARD_MAX_IDS:
            raise imp.ImportRefused("每批卡明细数量必须在 1–100 之间")
        root.mkdir(parents=True, exist_ok=True)
        if with_icons:
            # 2026-11-23 (user): images first (~5 min on a new install: the scan and the 资源 thumbnails need only
            # them), then the slow polite card details (skills/stats for 自动挖矿)
            emit_progress("icons")
            result["icons"] = refresh_icons()
        client = client if client is not None else imp.Importer(root, etags=imp.previous_etags(root))
        emit_progress("catalog")
        index = fetch_catalog(client, root)
        report = update_plan(index, cached_cards(root))
        result.update(report)
        pending = report["pending"]
        total_cards = len(pending)
        result["remaining_cards"] = len(pending)
        emit_progress("cards")
        while pending:
            batch = apply_update(root, index, {**report, "pending": pending}, client, max_cards=max_cards)
            updated = batch["updated_cards"]
            result.update({key: batch[key] for key in ("snapshot", "readiness_counts")})
            result["snapshots"].append(batch["snapshot"])
            result["updated_cards"] += updated
            result["completed_batches"] += 1
            result["rebuilt"] = True
            pending = pending[updated:]
            result["remaining_cards"] = len(pending)
            emit_progress("cards")
            if not all_cards:
                break
        result["pending"] = pending
        result["cards_complete"] = not pending
        result["complete"] = not pending
        result["status"] = "updated" if result["updated_cards"] else "unchanged"
        if with_icons:
            if result["icons"].get("stopped") or result["icons"].get("failed"):
                result["status"] = "stopped"
                result["complete"] = False
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result.update(status="stopped", complete=False, error=str(exc))
        # pending tracks only atomically enabled batches, never unfinished raw files.
        if "pending" in result:
            result["pending"] = report["pending"][result["updated_cards"]:]
        sys.stderr.buffer.write(f"更新已停止：{exc}。已启用的快照保留。\n".encode("utf-8"))
    try:
        result["local_cards"] = len(cached_cards(root))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result.update(status="stopped", complete=False, error=f"无法核验本地卡数：{exc}")
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="读取公共目录并更新；默认只预览本地缓存")
    parser.add_argument("--max-cards", type=int, default=100, help="每批最多获取的新增/变更卡明细（1–100，默认只运行一批）")
    parser.add_argument("--all", action="store_true", help="与 --fetch 同用，按每批上限完成本次目录的全部新增/变更卡")
    parser.add_argument("--progress-json", action="store_true", help="向 stderr 输出按阶段和批次的 JSON 进度")
    parser.add_argument("--with-icons", action="store_true", help="同时补缺失卡图并更新卡选择列表；复用已有图片")
    args = parser.parse_args(argv)
    if not 1 <= args.max_cards <= imp.HARD_MAX_IDS:
        parser.error("--max-cards 必须在 1–100 之间")
    if args.all and not args.fetch:
        parser.error("--all 必须与 --fetch 同用")
    root = DEFAULT_ROOT
    try:
        if not args.fetch:
            catalog = root / "catalog.json"
            index = imp.load_index([catalog]) if catalog.is_file() else imp.load_index(imp.DEFAULT_INDEXES)
            report = update_plan(index, cached_cards(root))
            print_json({**report, "status": "preview", "complete_catalog": catalog.is_file(),
                              "note": "仅本地预览；无 catalog.json 时使用已有旧索引，不能代表当前完整新卡；联网更新请加 --fetch"},
                       )
            return 0
        def progress_json(payload: dict) -> None:
            sys.stderr.buffer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            sys.stderr.buffer.flush()
        result = run_update(root, max_cards=args.max_cards, all_cards=args.all, with_icons=args.with_icons,
                            progress=progress_json if args.progress_json else None)
        try:
            write_json(root / "last-update.json", result)
        except OSError as exc:
            result.update(status="stopped", complete=False, error=f"无法保存更新摘要：{exc}")
        print_json(result)
        return 1 if result["status"] == "stopped" else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.stderr.buffer.write(f"更新已停止：{exc}。已启用的旧快照保留。\n".encode("utf-8"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
