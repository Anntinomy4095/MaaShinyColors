"""ノウハウブック library (phases K1/K2): offline data model, knowhow-name catalogue,
the user's "wanted" list, a matcher and screen parsers (ノウハウブック一覧 calibrated on native frames 2026-10-08; picker page
UNCALIBRATED, see CALIBRATION). No game input, no clicks, no scan driver.

User decision: the user registers which knowhow (name, min Lv, purpose Halo/STEP) is useful; Maa records
locally which books satisfy it and tells the user. K2 never infers sale eligibility; review facts describe only
observed target matches and structural coverage. Deleting is never automated; selling / protecting new books after a
Produce is a separate opt-in decision (agent/core/knowhow_keep.py).

Naming note: ``agent.core.produce_end.KnowhowBook`` is really ONE knowhow entry (name, Lv, count) read from the
獲得報酬 screen, not a book. It is left unchanged (other agents and tests use it); this module calls a row
``KnowhowRow`` and a book ``KnowhowBookRecord`` and accepts ``produce_end.KnowhowBook`` (or its dict form) as a row.

Persistence (all private, never committed):
- scan results: ``dev_assets/private/knowhow/inventory.json`` (``save_inventory`` / ``load_inventory``);
- optional extra catalogue names: ``dev_assets/private/knowhow/catalog.json`` (``{"entries": [{"name": ...}]}``);
- UI copy: ``ui/game-assets/knowhow.js`` (gitignored, same pattern as ``owned-levels.js``) via ``export_ui_module``;
- the wanted list lives in the UI profile ``prefs.knowhow_wanted`` (dev_assets/private/ui/profile.json).
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_DIR = ROOT / "dev_assets" / "private" / "knowhow"
INVENTORY_PATH = PRIVATE_DIR / "inventory.json"
PRIVATE_CATALOG_PATH = PRIVATE_DIR / "catalog.json"
UI_MODULE_PATH = ROOT / "ui" / "game-assets" / "knowhow.js"
PROFILE_PATH = ROOT / "dev_assets" / "private" / "ui" / "profile.json"

STATS = ("vo", "da", "vi", "me")
LEVELS = range(1, 7)                       # Lv.1–5, item knowhow Lv.5/6
MAX_ROWS = 21                              # ノウハウ数 N/21
PURPOSES = ("halo", "step", "any")
MATCH_KINDS = ("exact", "prefix", "contains")
POSITIONS = ("center", "leader", "vocal", "dance", "visual")
MAX_WANTED = 64                            # same cap as ui/protocol.json prefs.knowhow_wanted
MAX_NAME = 40
_NAME_FORBIDDEN = re.compile(r"[\x00-\x1f/\\:<>\"'`]")

# Compatibility hook for explicitly supplied weights; the default counts each threshold-satisfied target once.
DEFAULT_LEVEL_WEIGHT: Mapping[int, float] = {level: 1 for level in LEVELS}

UNCERTAINTIES = (
    "官方ノウハウ評価値仅为游戏元数据，不参与默认用途排序",
    "位置标签与完整扫描覆盖尚未绑定；已读取记录未发现不等于全库没有",
    "内容指纹不是物理唯一 ID；相同内容保留实体数量，最高 Lv 不推出可出售",
    "识别：ノウハウブック一覧 已用原生 1280×720 帧离线校准；编成选书页与扫描驱动未校准，未做全库扫描",
)


def normalize_name(text: Any) -> str:
    """NFKC (full-width ＋（）→ +() ), no whitespace, ring marks 〇/○/◯ → ◯ (OCR reads the ◯ suffix as 〇); the key used
    for all knowhow name comparisons. Mirrored by normalizeName in ui/components/knowhow-data.js."""
    return re.sub(r"[〇○◯]", "◯", re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))))


def clean_name(text: Any) -> str | None:
    """A storable knowhow name (≤40 chars, no control/path/quote characters) or None."""
    name = normalize_name(text)
    if not name or len(name) > MAX_NAME or _NAME_FORBIDDEN.search(name):
        return None
    return name


def _int_in(value: Any, low: int, high: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and re.fullmatch(r"\d{1,6}", value.strip()):
        value = int(value.strip())
    return value if isinstance(value, int) and low <= value <= high else None


# ------------------------------------------------------------------------------------------------- catalogue
@dataclasses.dataclass(frozen=True)
class CatalogEntry:
    name: str                               # display name; also the match key (normalised)
    group: str                              # head | instant | ability | unit | low | seen | private
    purposes: tuple[str, ...] = ("halo", "step")
    match: str = "exact"                    # suggested match kind when the user ticks it
    note: str = ""
    source: str = "doc"                     # doc | private (local cache) | seen (own library)

    def to_dict(self) -> dict:
        return {"name": self.name, "group": self.group, "purposes": list(self.purposes), "match": self.match,
                "note": self.note, "source": self.source}


def _e(name, group, purposes=("halo", "step"), match="exact", note=""):
    return CatalogEntry(name, group, tuple(purposes), match, note)


# Generic knowhow type names. Not the wiki table;
# exact in-game spellings of several entries are unverified, hence the prefix/contains match kinds.
# Keep in sync with GENERIC_CATALOG in ui/components/knowhow-data.js (tests/test_knowhow_books.py checks parity).
GENERIC_CATALOG: tuple[CatalogEntry, ...] = (
    _e("VocalUP", "head", match="prefix", note="头ノウハウ；+/++/+++ 档"),
    _e("DanceUP", "head", match="prefix", note="头ノウハウ"),
    _e("VisualUP", "head", match="prefix", note="头ノウハウ"),
    _e("メンタルUP", "head", match="prefix", note="头ノウハウ（样本：メンタルUP+）"),
    _e("Vocal上限", "head", match="prefix", note="头ノウハウ（样本：Vocal上限+/++/+++）"),
    _e("Dance上限", "head", match="prefix", note="头ノウハウ"),
    _e("Visual上限", "head", match="prefix", note="头ノウハウ"),
    _e("メンタル上限", "head", match="prefix", note="头ノウハウ"),
    _e("思い出(ノウハウ)", "instant", match="prefix", note="+/++"),
    _e("エキスパート目標達成", "instant", ("step",)),
    _e("エキシビションマッチ", "instant", match="prefix", note="++ 档常用"),
    _e("ファン感謝祭クリア", "instant", match="prefix"),
    _e("Landing Point大成功", "instant", match="prefix"),
    _e("アピールUP(思い出高)", "ability", ("halo",)),
    _e("アピールUP(思い出低)", "ability", ("halo",)),
    _e("パーフェクトリィ", "ability", ("halo",)),
    _e("スタートダッシュ", "ability", ("halo",)),
    _e("スロースターター", "ability", ("halo",)),
    _e("基礎能力値UP", "ability", ("halo",), "prefix", "(+3%)/(+6%)"),
    _e("オールラウンダー", "ability", ("halo",), "prefix", "◯/◎"),
    _e("適正", "ability", ("halo",), "contains", "◯◯適正◯/◎（按位置）"),
    _e("スペシャリスト", "ability", ("halo",), "prefix", "按位置"),
    _e("ロールモデル", "ability", ("halo",)),
    _e("エース", "ability", ("halo",)),
    _e("歌姫", "ability", ("halo",)),
    _e("プロダンサー", "ability", ("halo",)),
    _e("トップモデル", "ability", ("halo",)),
    _e("施設効率相談", "ability", ("halo",), "prefix"),
    _e("パッシブスキル延長", "ability", ("halo",), "prefix"),
    _e("昂揚", "ability", ("halo",)),
    _e("ファイナルライブ", "ability", ("halo",), "prefix"),
    _e("越える絆", "unit", ("halo",), note="两ユニット编队"),
    _e("一致団結", "unit", ("halo",), note="单ユニット编队"),
    _e("WING優勝", "low", ("step",), note="不可转记；Halo 价值低"),
    _e("WING準決勝", "low", ("step",), note="不可转记；Halo 价值低"),
    _e("ファン感MVP", "low", ("step",)),
    _e("GRAD予選", "low", ("step",)),
    _e("GRAD決勝", "low", ("step",)),
    _e("ファンの声援", "low", ("step",), note="Halo 编书时常视为占格"),
    _e("エントリー目標達成", "low", ("step",)),
)


def load_private_catalog(path: Path = PRIVATE_CATALOG_PATH) -> tuple[CatalogEntry, ...]:
    """Optional extra names from a local research cache (never committed). Missing/invalid file -> ()."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    out = []
    for item in (data.get("entries") if isinstance(data, Mapping) else data) or ():
        raw = item if isinstance(item, Mapping) else {"name": item}
        name = clean_name(raw.get("name"))
        if not name:
            continue
        purposes = tuple(p for p in (raw.get("purposes") or ("halo", "step")) if p in ("halo", "step")) or ("halo", "step")
        match = raw.get("match") if raw.get("match") in MATCH_KINDS else "exact"
        out.append(CatalogEntry(name, str(raw.get("group") or "private")[:16], purposes, match, str(raw.get("note") or "")[:60],
                                "private"))
    return tuple(out)


def build_catalog(books: Iterable["KnowhowBookRecord"] = (), private: Iterable[CatalogEntry] | None = None) -> list[CatalogEntry]:
    """Generic names + private cache + every row name seen in the user's own books (deduplicated by normalised name)."""
    seen: dict[str, CatalogEntry] = {}
    for entry in GENERIC_CATALOG:
        seen.setdefault(normalize_name(entry.name), entry)
    for entry in (load_private_catalog() if private is None else private):
        seen.setdefault(normalize_name(entry.name), entry)
    for book in books:
        for row in book.rows:
            key = normalize_name(row.name)
            if key and key not in seen and clean_name(row.name):
                seen[key] = CatalogEntry(clean_name(row.name), "seen", ("halo", "step"), "exact", "", "seen")
    return list(seen.values())


# ------------------------------------------------------------------------------------------------- wanted list
@dataclasses.dataclass(frozen=True)
class WantedItem:
    name: str
    match: str = "exact"
    min_level: int = 1
    purpose: str = "any"                    # halo | step | any
    position: str | None = None             # optional Fes position tag (display/filter only for now)

    @classmethod
    def from_dict(cls, value: Any) -> "WantedItem | None":
        """Same sanitising as ui/components/knowhow-data.js cleanWanted and the host UiProfileStore."""
        if isinstance(value, str):
            value = {"name": value}
        if not isinstance(value, Mapping):
            return None
        name = clean_name(value.get("name"))
        if not name:
            return None
        match = value.get("match") if value.get("match") in MATCH_KINDS else "exact"
        level = _int_in(value.get("min_level"), 1, 6) or 1
        purpose = value.get("purpose") if value.get("purpose") in PURPOSES else "any"
        position = value.get("position") if value.get("position") in POSITIONS else None
        return cls(name, match, level, purpose, position)

    def key(self) -> tuple:
        return (normalize_name(self.name), self.match, self.purpose)

    def matches_name(self, row_name: Any) -> bool:
        want, have = normalize_name(self.name), normalize_name(row_name)
        if not want or not have:
            return False
        if self.match == "prefix":
            return have.startswith(want)
        if self.match == "contains":
            return want in have
        return have == want

    def to_dict(self) -> dict:
        out = {"name": self.name, "match": self.match, "min_level": self.min_level, "purpose": self.purpose}
        if self.position:
            out["position"] = self.position
        return out


def clean_wanted(values: Any) -> list[WantedItem]:
    out: list[WantedItem] = []
    keys = set()
    for value in values if isinstance(values, (list, tuple)) else ():
        item = WantedItem.from_dict(value)
        if item and item.key() not in keys and len(out) < MAX_WANTED:
            keys.add(item.key())
            out.append(item)
    return out


def load_wanted_from_profile(path: Path = PROFILE_PATH) -> list[WantedItem]:
    """The wanted list saved by the UI (prefs.knowhow_wanted); missing profile -> []."""
    try:
        profile = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return clean_wanted(((profile or {}).get("prefs") or {}).get("knowhow_wanted"))


# ------------------------------------------------------------------------------------------------- book model
@dataclasses.dataclass(frozen=True)
class KnowhowRow:
    name: str
    level: int | None = None
    effect: str | None = None

    @classmethod
    def from_any(cls, value: Any) -> "KnowhowRow | None":
        """dict {name, level, effect?}, a (name, level) pair, or a produce_end.KnowhowBook (one knowhow entry)."""
        if hasattr(value, "name") and hasattr(value, "level") and not isinstance(value, Mapping):
            value = {"name": value.name, "level": value.level}
        elif isinstance(value, (tuple, list)) and len(value) == 2:
            value = {"name": value[0], "level": value[1]}
        if not isinstance(value, Mapping):
            return None
        name = repair_reading(value.get("name"))
        if not name:
            return None
        effect = normalize_name(value.get("effect")) or None
        return cls(name, _int_in(value.get("level"), 1, 6), effect)

    def to_dict(self) -> dict:
        return {"name": self.name, "level": self.level, "effect": self.effect}


@dataclasses.dataclass(frozen=True)
class KnowhowBookRecord:
    title: str | None = None                # 【カード名】アイドル名 or 「283プロのノウハウブック」
    idol: str | None = None
    stats: Mapping[str, int] = dataclasses.field(default_factory=dict)
    rows: tuple[KnowhowRow, ...] = ()
    knowhow_count: int | None = None        # N of 「ノウハウ数 N/21」 when read
    eval_value: int | None = None           # official ノウハウ評価値 (picker tile in 評価値順)
    marked: bool | None = None              # マーク badge on the tile (None = not read)
    protected: bool | None = None           # 保護 (no sample yet)
    source: str = "scan"                    # scan | produce_end | fixture
    frames: tuple[str, ...] = ()            # private frame refs (relative names), never committed
    unknowns: tuple[str, ...] = ()
    count: int = 1                          # identical books observed separately
    first_seen: str | None = None
    last_seen: str | None = None

    @property
    def complete(self) -> bool:
        """Complete content only: title, four stats, all rows/levels and no unresolved detail gaps."""
        return (bool(self.title) and all(k in self.stats for k in STATS) and bool(self.rows)
                and self.knowhow_count is not None and len(self.rows) == self.knowhow_count
                and all(r.level in LEVELS for r in self.rows)
                and not any(u.startswith(("rows.", "merge.")) for u in self.unknowns))

    @property
    def fingerprint(self) -> str:
        """Content fingerprint, never physical identity. Incomplete readings always use 'p-'."""
        rows = sorted((r.name, r.level or 0) for r in self.rows)
        stats = [self.stats.get(k) for k in STATS]
        payload = json.dumps([normalize_name(self.title), stats, rows], ensure_ascii=False)
        return ("b-" if self.complete else "p-") + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

    def levels(self) -> dict[str, int]:
        """Highest level per row name in this book."""
        out: dict[str, int] = {}
        for r in self.rows:
            if r.level is not None:
                out[r.name] = max(out.get(r.name, 0), r.level)
        return out

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "KnowhowBookRecord":
        stats = {k: v for k in STATS if (v := _int_in((value.get("stats") or {}).get(k), 0, 450)) is not None}
        rows = tuple(r for r in (KnowhowRow.from_any(v) for v in value.get("rows") or ()) if r)[:MAX_ROWS]
        title = repair_reading(value.get("title")) or None
        unknowns = [str(u) for u in value.get("unknowns") or ()]
        if value.get("complete") is False:
            unknowns.append("rows.incomplete")
        if any(isinstance(r, Mapping) and r.get("clipped") for r in value.get("rows") or ()):
            unknowns.append("rows.clipped")
        return cls(title=title, idol=repair_reading(value.get("idol")) or idol_from_title(title), stats=stats, rows=rows,
                   knowhow_count=_int_in(value.get("knowhow_count"), 0, MAX_ROWS),
                   eval_value=_int_in(value.get("eval_value"), 0, 99999),
                   marked=value.get("marked") if isinstance(value.get("marked"), bool) else None,
                   protected=value.get("protected") if isinstance(value.get("protected"), bool) else None,
                   source=str(value.get("source") or "scan")[:16],
                   frames=tuple(str(f) for f in value.get("frames") or () if isinstance(f, str))[:32],
                   unknowns=tuple(dict.fromkeys(unknowns))[:16],
                   count=_int_in(value.get("count"), 1, 999) or 1,
                   first_seen=value.get("first_seen"), last_seen=value.get("last_seen"))

    def to_dict(self) -> dict:
        return {"fingerprint": self.fingerprint, "title": self.title, "idol": self.idol, "stats": dict(self.stats),
                "rows": [r.to_dict() for r in self.rows], "knowhow_count": self.knowhow_count,
                "eval_value": self.eval_value, "marked": self.marked, "protected": self.protected,
                "complete": self.complete, "source": self.source, "frames": list(self.frames),
                "unknowns": list(self.unknowns), "count": self.count, "first_seen": self.first_seen,
                "last_seen": self.last_seen}


def idol_from_title(title: str | None) -> str | None:
    """「【ひとつ、はばたく】櫻木真乃」 -> 櫻木真乃; other titles (e.g. 283プロのノウハウブック) -> None."""
    m = re.fullmatch(r"【[^】]+】(.+)", normalize_name(title)) if title else None
    return m.group(1) if m else None


def same_book_title(a: str | None, b: str | None) -> bool:
    """Two detail-page readings of one book's title. Kana card names OCR unstably across scroll steps (live 2026-10-12:
    「[<5<5】三峰結華」 / 「[<5<ら】三峰結華」 / 「【<ら<5】三峰結華」), so only the idol part after 】 must agree; the
    caller also binds the page by the ✓ tile, the four stats and the ノウハウ数."""
    a, b = normalize_name(a or ""), normalize_name(b or "")
    if not a or not b:
        return False
    if a == b:
        return True
    close = re.compile(r"[】\]］」]")            # live 2026-10-12: 】 also OCRs as ] (「[<5<5]三峰結華」)
    parts_a, parts_b = close.split(a), close.split(b)
    if len(parts_a) < 2 or len(parts_b) < 2:
        return False
    ia, ib = parts_a[-1], parts_b[-1]
    if len(ia) >= 2 and ia == ib:
        return True
    # 2026-10-13 live: the stylised title row read 「…】幽谷霧子」 twice and once 「…】幽谷鼎子」 (0.84); 2026-10-23 live
    # seg10: 「…】黛冬優子」 / 「…】黛冬偃優子」; 2026-10-24 seg26: 「…】斑ル力」 / 「…】斑儿ル力」 (斑鳩ルカ). One substituted,
    # inserted or dropped glyph in a name of >= 4 characters (every idol name) is an OCR slip; the caller's ✓-tile,
    # stats and ノウハウ数 binding still decides.
    return _idol_slip(ia, ib)


def _one_edit(a: str, b: str) -> bool:
    """Levenshtein distance exactly 1."""
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _idol_slip(a: str, b: str) -> bool:
    """One OCR slip in an idol name: the longer reading has >= 4 characters (a dropped glyph leaves 3)."""
    return max(len(a), len(b)) >= 4 and min(len(a), len(b)) >= 3 and _one_edit(a, b)


def _same_idol(a: str, b: str) -> bool:
    # Same tolerance as ``same_book_title`` (2026-10-23 live seg20: 「1樋口円香」 / 「樋口円香」 stopped book 357).
    return a == b or _idol_slip(a, b)


def _merge(old: KnowhowBookRecord, new: KnowhowBookRecord) -> KnowhowBookRecord:
    pick = lambda a, b: b if b is not None else a  # noqa: E731 - newer observation wins when present
    return dataclasses.replace(
        old, eval_value=pick(old.eval_value, new.eval_value), marked=pick(old.marked, new.marked),
        protected=pick(old.protected, new.protected), knowhow_count=pick(old.knowhow_count, new.knowhow_count),
        frames=tuple(dict.fromkeys(old.frames + new.frames))[-32:],
        unknowns=tuple(dict.fromkeys(old.unknowns + new.unknowns)),
        first_seen=old.first_seen or new.first_seen, last_seen=new.last_seen or old.last_seen)


def _like(a: str, b: str, ratio: float = 0.85) -> bool:
    import difflib

    a, b = normalize_name(a), normalize_name(b)
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= ratio


def _row_like(part: str, name: str) -> bool:
    """A 獲得報酬 row reading and a scanned row name: equal, or sharing a run of at least 5 characters (the pane cuts
    long names: 「おもいッきりア今る」 is 「今がキラキラ!おもいッきりアイドル!O」, live 2026-11-15)."""
    import difflib

    a, b = normalize_name(part), normalize_name(name)
    if a == b:
        return True
    m = difflib.SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b))
    return m.size >= min(5, len(a))


def _covers(part: KnowhowBookRecord, book: KnowhowBookRecord, *, fuzzy: bool = False) -> bool:
    """A cut-off produce-end reading ``part`` fits ``book``: same title and stats (both required) and its (name, Lv)
    rows a sub-multiset of the book's rows. ``fuzzy`` (a book a newest-books scan just read): the title and row names
    may differ by OCR noise (live 2026-11-15: 「巡り行くく」, a cut row name); every row still needs its own book row at
    the same level."""
    import collections

    if not part.title or not (_like(part.title, book.title) if fuzzy else normalize_name(part.title) == normalize_name(book.title)):
        return False
    if not part.stats or set(part.stats) != set(STATS) or any(book.stats.get(k) != v for k, v in part.stats.items()):
        return False
    if fuzzy:
        free = list(book.rows)
        for r in part.rows:
            hit = next((b for b in free if b.level == r.level and _row_like(r.name, b.name)), None)
            if hit is None:
                return False
            free.remove(hit)
        return bool(part.rows)
    have = collections.Counter((r.name, r.level) for r in book.rows)
    want = collections.Counter((r.name, r.level) for r in part.rows)
    return bool(want) and all(have[k] >= n for k, n in want.items())


@dataclasses.dataclass
class KnowhowInventory:
    books: dict[str, KnowhowBookRecord] = dataclasses.field(default_factory=dict)
    pending: list[KnowhowBookRecord] = dataclasses.field(default_factory=list)   # produce_end partials
    scanned_at: str | None = None
    owned_total: int | None = None          # 所持数 N/600 when read (completeness check)
    # 評価値 grid coverage of the last scan (knowhow_scan): {complete, basis, values, owned_total, sort, direction,
    # scanned_at}. ``complete`` is True only when consecutive screens were overlap-proven and the tile count equals
    # 所持数; the UI gives an in-game page/row/column only then.
    grid: dict | None = None
    # Library top-up (knowhow_scan.library_sync, 2026-10-25): the books newest-books scans read since the last complete
    # library scan (its 所持数 = grid.owned_total is the baseline), oldest first: [{fp, signature, date, at}].
    recent: list[dict] = dataclasses.field(default_factory=list)
    owned_seen: dict | None = None          # last 所持数 any scan read: {owned, capacity, at}
    sync: dict | None = None                # last top-up check: {state, owned_now, known, new, reason, at}

    def upsert(self, book: KnowhowBookRecord, *, duplicate: bool = False) -> str:
        """Add or merge one observation. ``duplicate=True`` = a second, separately observed identical book."""
        fp = book.fingerprint
        if fp in self.books:
            merged = _merge(self.books[fp], book)
            self.books[fp] = dataclasses.replace(merged, count=merged.count + 1) if duplicate else merged
        else:
            self.books[fp] = book
        return fp

    def add_pending(self, book: KnowhowBookRecord) -> None:
        self.pending.append(book)

    def reconcile_pending(self, candidates: Iterable[str] | None = None) -> list[tuple[KnowhowBookRecord, str]]:
        """Resolve produce-end partial books whose (name, Lv) multiset equals exactly one scanned book. With
        ``candidates`` (fingerprints of the books a newest-books scan just read: the latest acquisitions) a pending
        book whose rows were cut off (``rows.partial``) also resolves when its rows are a sub-multiset of exactly one
        candidate with the same title and stats (2026-10-25: the 獲得報酬 pane showed 2 of 3 rows, so the exact rule
        never resolved it)."""
        pool = self.books if candidates is None else {fp: self.books[fp] for fp in candidates if fp in self.books}
        resolved, left = [], []
        for part in self.pending:
            key = sorted((r.name, r.level) for r in part.rows)
            hits = [fp for fp, b in pool.items() if b.complete and b.count == 1
                    and sorted((r.name, r.level) for r in b.rows) == key]
            if not hits and candidates is not None and "rows.partial" in part.unknowns:
                hits = [fp for fp, b in pool.items() if b.complete and b.count == 1 and _covers(part, b)]
            if not hits and candidates is not None and part.title and part.stats:
                # the 獲得報酬 pane shows the first rows only (a say "Halo" book has ~21) and OCR may garble the title
                hits = [fp for fp, b in pool.items() if b.complete and b.count == 1 and _covers(part, b, fuzzy=True)]
            if len(hits) == 1:
                resolved.append((part, hits[0]))
            else:
                left.append(part)
        self.pending = left
        return resolved

    def to_dict(self) -> dict:
        return {"version": 1, "scanned_at": self.scanned_at, "owned_total": self.owned_total, "grid": self.grid,
                "books": [b.to_dict() for b in self.books.values()], "pending": [b.to_dict() for b in self.pending],
                "recent": list(self.recent), "owned_seen": self.owned_seen, "sync": self.sync}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "KnowhowInventory":
        inv = cls(scanned_at=data.get("scanned_at"), owned_total=_int_in(data.get("owned_total"), 0, 600),
                  grid=clean_grid(data.get("grid")))
        renamed = {}                    # stored fingerprint -> fingerprint after repair_reading
        for raw in data.get("books") or ():
            if isinstance(raw, Mapping):
                book = KnowhowBookRecord.from_dict(raw)
                count = _int_in(raw.get("count"), 1, 999) or 1
                if isinstance(raw.get("fingerprint"), str) and raw["fingerprint"] != book.fingerprint:
                    renamed[raw["fingerprint"]] = book.fingerprint
                    if book.fingerprint in inv.books:          # two copies whose readings differed only by the fix
                        count += inv.books[book.fingerprint].count
                inv.books[book.fingerprint] = dataclasses.replace(book, count=min(count, 999))
        inv.pending = [KnowhowBookRecord.from_dict(r) for r in data.get("pending") or () if isinstance(r, Mapping)]
        inv.recent = [dict(e, fp=renamed.get(e["fp"], e["fp"])) for e in clean_recent(data.get("recent"))]
        inv.owned_seen = _clean_owned_seen(data.get("owned_seen"))
        inv.sync = clean_sync(data.get("sync"))
        return inv


def clean_grid(value: Any) -> dict | None:
    """Sanitised 評価値 grid coverage record; ``complete`` additionally requires len(values) == owned_total."""
    if not isinstance(value, Mapping):
        return None
    values = [v for v in (_int_in(x, 0, 99999) for x in value.get("values") or ()) if v is not None][:600]
    owned = _int_in(value.get("owned_total"), 0, 600)
    complete = (value.get("complete") is True and owned is not None and len(values) == owned
                and all(a >= b for a, b in zip(values, values[1:])))
    return {"complete": complete, "basis": str(value.get("basis") or "")[:200], "values": values, "owned_total": owned,
            "sort": SORT_EVAL if value.get("sort") == SORT_EVAL else None,
            "direction": "desc" if value.get("direction") == "desc" else None,
            "scanned_at": value.get("scanned_at") if isinstance(value.get("scanned_at"), str) else None}


def clean_recent(value: Any) -> list[dict]:
    """Sanitised ``KnowhowInventory.recent``: fingerprint, the 獲得順 tile key (date, Lv5 N, ノウハウ数 M), date."""
    out = []
    for e in value if isinstance(value, list) else ():
        if not isinstance(e, Mapping) or not isinstance(e.get("fp"), str) or not re.fullmatch(r"[bp]-[0-9a-f]{6,64}", e["fp"]):
            continue
        sig = e.get("signature")
        sig = list(sig)[:3] if isinstance(sig, (list, tuple)) and len(sig) >= 3 else None
        date = e.get("date") if isinstance(e.get("date"), str) and re.fullmatch(r"20\d\d-\d\d-\d\d", e["date"]) else None
        out.append({"fp": e["fp"], "signature": sig, "date": date,
                    "at": e.get("at") if isinstance(e.get("at"), str) else None})
    return out[:600]


def _clean_owned_seen(value: Any) -> dict | None:
    if not isinstance(value, Mapping) or _int_in(value.get("owned"), 0, 600) is None:
        return None
    return {"owned": _int_in(value.get("owned"), 0, 600), "capacity": _int_in(value.get("capacity"), 0, 600),
            "at": value.get("at") if isinstance(value.get("at"), str) else None}


SYNC_STATES = ("up_to_date", "new_books", "too_many", "fewer_owned", "needs_full_scan", "no_baseline", "unknown")


def clean_sync(value: Any) -> dict | None:
    if not isinstance(value, Mapping) or value.get("state") not in SYNC_STATES:
        return None
    return {"state": value["state"], "owned_now": _int_in(value.get("owned_now"), 0, 600),
            "known": _int_in(value.get("known"), 0, 600),
            "new": value.get("new") if isinstance(value.get("new"), int) and not isinstance(value.get("new"), bool) else None,
            "reason": str(value.get("reason") or "")[:80] or None,
            "at": value.get("at") if isinstance(value.get("at"), str) else None}


def record_removed(inv: KnowhowInventory, fps: Iterable[str], *, owned_now: int | None = None,
                   at: str | None = None) -> dict:
    """Books Maa sold (knowhow_keep; one fingerprint per physical copy): drop each copy and lower the count
    ``library_sync`` compares with 所持数 by the same amount. A book a top-up read leaves ``recent`` (the newest such
    entry); any other leaves the baseline (``grid.owned_total``) together with one tile of its 評価値, else the grid can
    no longer prove itself complete and the next sync asks for a library scan. ``owned_now``: 所持数 re-read after the
    sale (default: the last one seen minus the copies removed). Unknown fingerprints are reported, not guessed."""
    removed, missing, from_recent, from_baseline, grid_lost = [], [], 0, 0, False
    for fp in fps:
        book = inv.books.get(fp)
        if book is None:
            missing.append(fp)
            continue
        if book.count > 1:
            inv.books[fp] = dataclasses.replace(book, count=book.count - 1)
        else:
            del inv.books[fp]
        removed.append(fp)
        hit = next((i for i in range(len(inv.recent) - 1, -1, -1) if inv.recent[i].get("fp") == fp), None)
        if hit is not None:
            del inv.recent[hit]
            from_recent += 1
            continue
        from_baseline += 1
        grid = inv.grid
        if grid and grid.get("owned_total"):
            grid["owned_total"] -= 1
            values = grid.get("values") or []
            if book.eval_value in values:
                values.remove(book.eval_value)
            else:
                grid_lost = True
            if grid_lost:
                grid["complete"] = False
    if removed and inv.owned_total is not None:
        inv.owned_total = max(0, inv.owned_total - len(removed))
    if owned_now is not None or (removed and inv.owned_seen):
        seen = dict(inv.owned_seen or {})
        seen["owned"] = owned_now if owned_now is not None else max(0, int(seen.get("owned") or 0) - len(removed))
        seen["at"] = at or seen.get("at")
        inv.owned_seen = _clean_owned_seen(seen)
    return {"removed": len(removed), "missing": missing, "recent": from_recent, "baseline": from_baseline,
            "grid_complete": bool(inv.grid and inv.grid.get("complete"))}


def _check_private(path: Path) -> Path:
    path = Path(path).resolve()
    allowed = [(ROOT / "dev_assets" / "private").resolve(), (ROOT / "ui" / "game-assets").resolve()]
    if not any(path.is_relative_to(a) for a in allowed):
        raise ValueError("knowhow data must stay under dev_assets/private or ui/game-assets (gitignored)")
    return path


def save_inventory(inv: KnowhowInventory, path: Path = INVENTORY_PATH) -> Path:
    path = _check_private(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(inv.to_dict(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def load_inventory(path: Path = INVENTORY_PATH) -> KnowhowInventory:
    try:
        return KnowhowInventory.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError, AttributeError):
        return KnowhowInventory()


# ------------------------------------------------------------------------------------------------- matcher
def default_score(book: KnowhowBookRecord, matched: Sequence[tuple[WantedItem, int]],
                  level_weight: Mapping[int, float] = DEFAULT_LEVEL_WEIGHT) -> float:
    """Count threshold-satisfied targets; optional caller weights are not game probability estimates."""
    return float(sum(level_weight.get(level, 0) for _, level in matched))


def analyze(books: Iterable[KnowhowBookRecord], wanted: Iterable[WantedItem | Mapping | str], *, purpose: str | None = None,
            exclude_idols: Iterable[str] | None = None, level_weight: Mapping[int, float] = DEFAULT_LEVEL_WEIGHT,
            score: Callable[..., float] | None = None) -> dict:
    """Inventory presence and verified team usability are separate. No sale eligibility is inferred.

    An omitted team yields usable/team_satisfied=None; satisfied reports inventory presence then.
    An explicitly supplied exclusion set binds compatibility only,
    not positions or a full Produce strategy. Official evaluation remains display metadata.
    """
    books = list(books)
    items = [w if isinstance(w, WantedItem) else WantedItem.from_dict(w) for w in wanted]
    items = [w for w in items if w and (purpose not in ("halo", "step") or w.purpose in (purpose, "any"))]
    team_verified = exclude_idols is not None
    excluded = {normalize_name(i) for i in exclude_idols or ()}
    def usable(book):
        if not team_verified or not book.idol:
            return None
        return normalize_name(book.idol) not in excluded
    score_fn = score or (lambda book, matched: default_score(book, matched, level_weight))

    best: dict[str, dict] = {}
    for book in books:
        for name, level in book.levels().items():
            cur = best.get(name)
            if cur is None or level > cur["level"]:
                best[name] = {"level": level, "books": [book.fingerprint]}
            elif level == cur["level"] and book.fingerprint not in cur["books"]:
                cur["books"].append(book.fingerprint)
    counts = {book.fingerprint: book.count for book in books}
    for cur in best.values():
        cur["physical_count"] = sum(counts[fp] for fp in cur["books"])
        cur["unique"] = cur["physical_count"] == 1

    wanted_out = []
    per_book: dict[str, list[tuple[int, int]]] = {b.fingerprint: [] for b in books}
    uncertain_hits: dict[str, list[int]] = {b.fingerprint: [] for b in books}
    for index, item in enumerate(items):
        holders = []
        for book in books:
            levels = [r.level for r in book.rows if item.matches_name(r.name)]
            known = [lv for lv in levels if lv is not None]
            if known and max(known) >= item.min_level:
                holders.append({"book": book.fingerprint, "level": max(known), "eval_value": book.eval_value,
                                "usable": usable(book), "count": book.count})
                per_book[book.fingerprint].append((index, max(known)))
            elif levels and None in levels:
                uncertain_hits[book.fingerprint].append(index)
        holders.sort(key=lambda h: (-h["level"], h["book"]))
        wanted_out.append({"item": item.to_dict(), "holders": holders, "owned": bool(holders),
                           "satisfied": any(h["usable"] is True for h in holders) if team_verified else bool(holders),
                           "team_satisfied": any(h["usable"] is True for h in holders) if team_verified else None,
                           "best_level": holders[0]["level"] if holders else None})

    book_out = []
    for book in books:
        fp = book.fingerprint
        matched = [(items[i], lv) for i, lv in per_book[fp]]
        book_out.append({"book": fp, "idol": book.idol, "title": book.title, "eval_value": book.eval_value,
                         "matched": [{"index": i, "name": items[i].name, "level": lv} for i, lv in per_book[fp]],
                         "score": score_fn(book, matched), "best_in": sorted(n for n, cur in best.items() if fp in cur["books"]),
                         "usable": usable(book), "count": book.count,
                         "complete": book.complete, "marked": book.marked, "uncertain_matches": uncertain_hits[fp]})
    def rank(info):
        return ({True: 0, None: 1, False: 2}[info["usable"]], -info["score"], info["book"])
    ranking = [b["book"] for b in sorted(book_out, key=rank)]
    outclassed = _outclassed(books)  # structural coverage only, not usefulness or sale advice
    uncertain, review = [], []
    for book, info in zip(books, book_out):
        info["outclassed_by"] = [r["by"] for r in outclassed if r["book"] == book.fingerprint]
        if items and not info["matched"]:
            review.append({"book": book.fingerprint, "reasons": ["已读取条目未命中当前目标"],
                           "count": book.count, "complete": book.complete})
        if items and (not book.complete or info["uncertain_matches"]):
            uncertain.append({"book": book.fingerprint, "reason": "详情未完整读取或存在未知项，目标缺口尚待复核"})
        elif items and info["usable"] is None:
            uncertain.append({"book": book.fingerprint, "reason": "未绑定队伍或书名义未知，兼容性未验证"})
    notes = list(UNCERTAINTIES)
    if not team_verified:
        notes.append("未提供实际 P/S 队伍；仅报告库存持有，兼容性未验证")
    return {"wanted": wanted_out, "books": book_out, "ranking": ranking, "best_holders": best,
            "sell_candidates": [], "review_facts": review, "outclassed": outclassed, "uncertain": uncertain,
            "team_verified": team_verified, "notes": notes, "advice_only": True}


def _outclassed(books: Sequence[KnowhowBookRecord]) -> list[dict]:
    """Structural coverage only: same idol, complete contents, B stats/levels cover A.
    This relation proves neither target usefulness nor sale eligibility; identical copies remain separate counts."""
    out = []
    for a in books:
        if not (a.complete and a.idol):
            continue
        la = a.levels()
        for b in books:
            if b is a or not b.complete or normalize_name(b.idol) != normalize_name(a.idol) or b.fingerprint == a.fingerprint:
                continue
            lb = b.levels()
            if all(b.stats.get(k, -1) >= a.stats.get(k, 10 ** 6) for k in STATS) and all(lb.get(n, 0) >= lv for n, lv in la.items()):
                out.append({"book": a.fingerprint, "by": b.fingerprint})
    return out


# ------------------------------------------------------------------------------------------------- recognition (K1)
# Two layouts share the left detail panel but not the grid:
# - ノウハウブック一覧 (list page): CALIBRATED offline on the native 1280x720 frames in
#   dev_assets/private/live-capture-20261008-knowhow/ (38 know-how frames, 2026-10-08) with the project's Maa OCR model
#   (tests/maa_probe.ocr_items) plus pixel checks; replayed by tests/test_knowhow_native_20261008.py.
# - ノウハウブック選択 (picker inside the Produce preparation flow): only the 715x402 side-browser JPEGs exist
#   (dev_assets/private/sidebrowser-knowhow-20261008/); its geometry below stays UNCALIBRATED.
# Per-part status; ``CALIBRATED`` (whole K1 incl. picker and the scan driver) is True only when every part is.
CALIBRATION: Mapping[str, bool] = {
    "list_screen": True,     # 一覧 normal / 保護 mode / 売却 mode / sort·filter·search·mark popups, sort key+direction, 所持数
    "list_tiles": True,      # 15 tile rects, 評価値, Lv5↑ count, ノウハウ数, マーク, 保護 lock, ✓, greyed, present
    "detail_panel": True,    # title, 4 stats, ノウハウ数, rows (name/Lv, two-line names, ◯/◎ suffix, clipped edges), 保護
    "filter_active": True,   # 2026-10-12 native (knowhow-sample-20261012 #118–#129): 「絞り込みON」 vs 「絞り込み」
    "picker": False,         # ノウハウブック選択 page: no native frame
    "scan_driver": False,    # K3: offline decision core agent/core/knowhow_scan.py built; wheel step + supervised run pending
}
CALIBRATED = all(CALIBRATION.values())
Texts = Sequence                            # [(text, (x, y, w, h)[, score]) | {"text", "box", "score"}, ...]

# --- picker page (UNCALIBRATED; sketched x1.79 from the side-browser JPEG) ---
PICKER_GRID = {"x": 533, "y": 179, "pitch_x": 140, "pitch_y": 140, "tile": 126, "cols": 5, "rows": 3}  # UNCALIBRATED
TILE_VALUE_ROI = (0.45, 0.74, 0.58, 0.30)  # relative to tile (x, y, w, h); white digits on the dark strip UNCALIBRATED
TILE_NAME_ROI = (0.12, 0.12, 0.76, 0.40)   # idol short name on the cover                               UNCALIBRATED
TILE_BADGE_ROI = (0.00, 0.00, 0.22, 0.22)  # マーク badge, top-LEFT corner (選択 ✓ is top-right)          UNCALIBRATED
TILE_COVER_ROI = (0.20, 0.12, 0.60, 0.50)  # book cover area used for the greyed (unselectable) test     UNCALIBRATED
GREY_VALUE_MAX = 0.58                      # mean HSV value of a greyed (darkened) cover; others ≥0.66     UNCALIBRATED
GREY_SATURATION_MAX = 0.30                 # and its mean saturation (pastel 283 book is 0.16 but bright)  UNCALIBRATED
BADGE_INK_MIN = 0.15                       # fraction of saturated/dark pixels in the badge corner         UNCALIBRATED

# --- 一覧 page grid (CALIBRATED 2026-10-08; measured values in comments) ---
LIST_GRID = {"x": 534, "y": 180, "pitch_x": 138.4, "pitch_y": 157.5, "tile": 108, "cols": 5, "rows": 3}
LIST_VALUE_ROI = (40, 78, 76, 34)          # px from tile origin: right-aligned 評価値 (lock icon OCR noise "8"/"0" is at x<25)
LIST_LV5_ROI = (-10, 108, 128, 34)         # 「Lv.5↑xN/M」 pill under the tile: N rows at Lv5+, M = ノウハウ数
LIST_NAME_ROI = (22, 14, 68, 40)           # idol short name on the cover (OCR unreliable: display only, never identity)
LIST_MARK_ROI = (4, 4, 26, 26)             # マーク badge top-left: ink 0.50–0.53 marked vs 0.00
LIST_LOCK_ROI = (2, 86, 20, 20)            # 保護 lock bottom-left: yellow 0.34–0.38 vs 0.00
LIST_CHECK_ROI = (80, -2, 30, 30)          # pink ✓ top-right (book shown in the detail panel): 0.32 vs ≤0.04
# 2026-10-23 live: a book used by a running オートプロデュース carries a pink 「使用中」 tag in the same corner (0.34 in
# LIST_CHECK_ROI) but no selection frame. The shown book also has a pink frame: top/left edges 0.50–0.67 on every ✓ tile
# of the 10-12..10-20 scans (289 tiles), 0.00 on the 使用中 tile.
LIST_SELECTED_FRAME_ROIS = ((10, -6, 60, 6), (-6, 30, 6, 60))
SELECTED_FRAME_PINK_MIN = 0.30
LIST_FRAME_ROI = (3, 40, 6, 40)            # white card edge: mean 242–247 normal, 145–147 greyed (売却 mode, protected)
LIST_OVERLAY_ROI = (25, 74, 92, 40)        # per-sort number/date at the cover's lower right (2026-10-12 native #064–#112):
                                           # x21 / 150 / 2026/10/12 start at tile x+34..+126, y+80..+92
LIST_PILL_ROI = (20, 114, 70, 20)          # grey pill: mean ≈175 when a book occupies the slot, ≈254 empty background
MARK_INK_MIN, LOCK_YELLOW_MIN, CHECK_PINK_MIN = 0.20, 0.15, 0.15
FRAME_GREY_MAX, PILL_PRESENT_MAX = 200, 215
# --- 一覧 page-level guards ---
LIST_TITLE_ROI = (0, 0, 330, 70)
LIST_SORT_ROI = (955, 98, 182, 48)
LIST_FILTER_ROI = (780, 96, 175, 50)        # 「絞り込み」 / 「絞り込みON」 button
LIST_DIR_ROI = (1135, 96, 105, 52)
LIST_OWNED_ROI = (1135, 648, 135, 50)
LIST_PROTECT_BOX = (530, 108, 42, 38)      # 保護 toggle: pink check 0.10 when on, 0.00 off
LIST_SALE_BOX = (654, 108, 42, 38)         # 売却 toggle: same
LIST_SEPARATOR = (525, 160, 710, 8)        # grid header rule: pink 0.12 in either mode, 0.00 normal
TOGGLE_INNER = ((537, 115, 28, 23), (661, 115, 28, 23))
TOGGLE_OFF_RGB = (154, 140, 162)           # unchecked square (std 0 on all normal frames)
TOGGLE_TOLERANCE = 12
MODE_PINK_MIN = 0.04
# --- left detail panel (CALIBRATED on the 一覧 page; the picker's panel is assumed identical but unverified) ---
DETAIL_TITLE_ROI = (140, 92, 320, 36)
DETAIL_STAT_ROIS = {"vo": (185, 132, 90, 38), "da": (345, 132, 90, 38),
                    "vi": (185, 170, 90, 38), "me": (345, 170, 90, 38)}
DETAIL_LIST_ROI = (15, 262, 440, 306)      # visible rows y 262..568 (above: 「ノウハウ」 banner; below: ノウハウ数 bar)
DETAIL_NAME_X = (48, 345)                  # name token must start in this x range (type icons OCR as noise at x 21–37)
DETAIL_LEVEL_X = (350, 430)                # centre x of 「Lv.N」
DETAIL_WRAP_X = 345                        # a name line reaching this right edge wraps onto a second line
DETAIL_COUNT_ROI = (355, 565, 110, 45)     # 「21 / 21」 (OCR reads it apart from 「ノウハウ数」)
DETAIL_PROTECT_ROI = (110, 630, 200, 60)   # bottom-left button: 保護 / 保護解除
DETAIL_EMPTY = "ノウハウブックを選択してください"
RING_MAX_X = 352                           # ◯/◎ suffix search stops before the Lv column

DESTRUCTIVE_MARKERS = ("売却する", "保護する", "選択ノウハウブック数", "ノウハウブック数", "売却後マニー", "削除する",
                       "リロードする", "再抽選", "所持枠解放する")
SALE_MARKERS = ("売却する", "選択ノウハウブック数", "ノウハウブック数", "売却後マニー")
# Whole-token matches, first hit wins. The 絞り込み popup also shows 「ノウハウブックマーク」 as a section label once
# scrolled (live 2026-10-12, kfilter1 frame 31), so its own header 「該当するノウハウブック」 is checked first.
POPUP_MARKERS = (("filter", ("該当するノウハウブック",)), ("sort", ("並び替え",)), ("search", ("絞り込みノウハウ選択",)),
                 ("mark", ("ノウハウブックマーク", "はずす")), ("preset", ("ノウハウブックプリセット",)))
SORT_EVAL = "ノウハウ評価値順"
SORT_DATE = "獲得順"           # newest-books scan (knowhow_scan ScanOptions.newest; tile overlay = acquisition date)
# What each sort key prints on the tile cover (2026-10-12 native, knowhow-sample-20261012): デフォルト / Lv順 nothing.
SORT_OVERLAY = {"ノウハウ評価値順": "value", "ノウハウ数順": "count", "Vocal上昇値順": "vo", "Dance上昇値順": "da",
                "Visual上昇値順": "vi", "メンタル上昇値順": "me", "獲得順": "date"}
SORT_KEYS = ("デフォルト", "ノウハウ数順", "ノウハウ評価値順", "Vocal上昇値順", "Dance上昇値順", "Visual上昇値順",
             "メンタル上昇値順", "獲得順", "Lv順")
# Observed Maa OCR confusions on these frames (simplified-Chinese glyphs, 達/連, く as 「<」 and the ら after it as 「5」:
# 疲れに<い, 【<5<5】三峰結華; 2026-11-01). Applied to OCR text and, via ``repair_reading``, to stored readings.
_OCR_FIXES = (("值", "値"), ("增", "増"), ("目標連成", "目標達成"), ("<", "く"), ("く5", "くら"))
_SCENARIO_ABBR = ((re.compile(r"\(G\.?R\.?A\.?D\.?\)"), "(G.R.A.D.)"), (re.compile(r"\(W\.?I\.?N\.?G\.?\)"), "(W.I.N.G.)"),
                  (re.compile(r"\(S\.?T\.?E\.?P\.?\)"), "(S.T.E.P.)"))
_MEANINGFUL = re.compile(r"[0-9A-Za-z぀-ヿ一-鿿]")


def _ocr_fix(text: str) -> str:
    for bad, good in _OCR_FIXES:
        text = text.replace(bad, good)
    return text


def repair_reading(text: Any) -> str:
    """A stored name/title reading with the OCR fixes applied (title 「[」 → 「【」 too); readings saved before a fix was
    added are repaired on load, so their fingerprints match new scans."""
    text = _ocr_fix(normalize_name(text))
    return "【" + text[1:] if text.startswith("[") and "】" in text else text


def _tokens(texts: Texts) -> list[tuple[str, tuple[int, int, int, int], float]]:
    """(text, box, score) with NFKC/whitespace normalisation and the observed OCR glyph fixes."""
    out = []
    for item in texts or ():
        if isinstance(item, Mapping):
            text, box, score = item.get("text"), item.get("box"), item.get("score", 1.0)
        else:
            text, box = item[0], item[1]
            score = item[2] if len(item) > 2 else 1.0
        if not box or len(box) != 4:
            continue
        out.append((_ocr_fix(normalize_name(text)), tuple(int(v) for v in box), float(1.0 if score is None else score)))
    return out


def _center(box) -> tuple[float, float]:
    x, y, w, h = box
    return x + w / 2, y + h / 2


def _inside(box, roi) -> bool:
    cx, cy = _center(box)
    x, y, w, h = roi
    return x <= cx <= x + w and y <= cy <= y + h


def tile_box(row: int, col: int) -> tuple[int, int, int, int]:
    """Picker page tile (UNCALIBRATED)."""
    g = PICKER_GRID
    return (g["x"] + col * g["pitch_x"], g["y"] + row * g["pitch_y"], g["tile"], g["tile"])


def list_tile_box(row: int, col: int) -> tuple[int, int, int, int]:
    """一覧 page tile (calibrated): the white card, 108x108 px."""
    g = LIST_GRID
    return (round(g["x"] + col * g["pitch_x"]), round(g["y"] + row * g["pitch_y"]), g["tile"], g["tile"])


def _sub(tile, rel) -> tuple[int, int, int, int]:
    x, y, w, h = tile
    rx, ry, rw, rh = rel
    return (round(x + rx * w), round(y + ry * h), round(rw * w), round(rh * h))


def _off(tile, rel) -> tuple[int, int, int, int]:
    return (tile[0] + rel[0], tile[1] + rel[1], rel[2], rel[3])


def _patch(image, roi):
    import numpy as np
    x, y, w, h = roi
    patch = np.asarray(image[max(0, y):y + h, max(0, x):x + w], dtype=np.int32)
    return patch.reshape(-1, 3) if patch.size else None


def _pink(image, roi) -> float | None:
    p = _patch(image, roi)
    if p is None:
        return None
    r, g, b = p[:, 0], p[:, 1], p[:, 2]
    return float(((r > 200) & (g < 140) & (b > 120) & (r - g > 90)).mean())


def _mean(image, roi) -> float | None:
    p = _patch(image, roi)
    return None if p is None else float(p.mean())


# OCR can split overlapping title boxes into 「ノウハウブックー」 + 「一覧」 (2026-10-20 live frame 0022).
# Normalise the duplicated stroke as well as the existing 一/ー/dash variants of 「一覧」.
_TITLE_DASHES = re.compile(r"[一ー\-‐‑–—―−－]+覧")


def _join_title(toks) -> str:
    return _TITLE_DASHES.sub("一覧", "".join(t for t, b, _ in sorted(toks, key=lambda i: i[1][0]) if _inside(b, LIST_TITLE_ROI)))


def _list_guards(image) -> dict:
    """Pixel guards for the 一覧 page: protect/sale multi-select modes add only pink (no new text in 保護 mode)."""
    import numpy as np
    toggles = []
    for roi in TOGGLE_INNER:
        p = _patch(image, roi)
        toggles.append(p is not None and bool(np.all(np.abs(p.mean(axis=0) - TOGGLE_OFF_RGB) <= TOGGLE_TOLERANCE)))
    return {"protect_pink": _pink(image, LIST_PROTECT_BOX), "sale_pink": _pink(image, LIST_SALE_BOX),
            "separator_pink": _pink(image, LIST_SEPARATOR), "toggles_off": all(toggles)}


def classify_knowhow_screen(texts: Texts, image=None) -> dict:
    """{screen: list|picker|None, popup, mode, sort, eval_sorted, direction, owned, capacity, filter_active, refused,
    selected, detail_empty, ok, calibrated, guards}.

    ``ok`` means: a 一覧 frame in normal mode without popup or destructive marker (pixel guards need ``image``), or a
    picker frame without destructive marker or popup (picker: UNCALIBRATED, text-only). 保護 mode shows no new text, so
    without an image the 一覧 mode is unknown and the frame is not ok. Destructive/multi-select markers always refuse.
    ``filter_active``: True for 「絞り込みON」, False for a plain 「絞り込み」 button, None when unread."""
    toks = _tokens(texts)
    words = [t for t, _, _ in toks]
    refused = [m for m in DESTRUCTIVE_MARKERS if any(m in w for w in words)]
    title = _join_title(toks)
    head = "".join(t for t, b, _ in toks if b[1] < 70 and b[0] < 500)
    screen = ("picker" if "ノウハウブック選択" in head else "list" if "ノウハウブック一覧" in title else None)
    popup = next((kind for kind, marks in POPUP_MARKERS if any(w == m for m in marks for w in words)), None)
    sort_words = [t for t, b, _ in toks if _inside(b, LIST_SORT_ROI)] if screen == "list" else []
    sort = next((k for k in SORT_KEYS for w in sort_words if w == k), None)
    if sort is None and screen != "list":
        sort = next((w for w in words if w.endswith("順") and ("ノウハウ" in w or "上昇値" in w or w in ("獲得順", "Lv順"))),
                    None)
        if sort is None and any(w == "デフォルト" for w in words):
            sort = "デフォルト"
    dir_words = [t for t, b, _ in toks if _inside(b, LIST_DIR_ROI)] if screen == "list" else words
    direction = "desc" if any("降順" in w for w in dir_words) else "asc" if any("昇順" in w for w in dir_words) else None
    owned = capacity = None
    for t, b, _ in toks:
        m = re.fullmatch(r"(\d{1,3})/(\d{3})", t)
        if m and _inside(b, LIST_OWNED_ROI) and int(m.group(1)) <= int(m.group(2)):
            owned, capacity = int(m.group(1)), int(m.group(2))
    selected = next((int(m.group(1)) for w in words for m in [re.fullmatch(r"選択数(\d)/3", w)] if m), None)
    filter_words = [t for t, b, _ in toks if _inside(b, LIST_FILTER_ROI)] if screen == "list" else []
    filter_active = (True if any(w.replace(" ", "") in ("絞り込みON", "絞り込みOn") for w in filter_words)
                     else False if any(w == "絞り込み" for w in filter_words) else None)
    guards = _list_guards(image) if image is not None and screen == "list" else None
    mode = None
    if screen == "list":
        if any(m in refused for m in SALE_MARKERS):
            mode = "sale"
        if guards is not None:
            if (guards["sale_pink"] or 0) >= MODE_PINK_MIN:
                mode = "sale"
            elif (guards["protect_pink"] or 0) >= MODE_PINK_MIN:
                mode = "protect"
            elif mode is None and guards["toggles_off"] and (guards["separator_pink"] or 0) < MODE_PINK_MIN / 2:
                mode = "normal"
    ok = not refused and popup is None and ((screen == "list" and mode == "normal") or screen == "picker")
    return {"screen": screen, "popup": popup, "mode": mode, "sort": sort, "eval_sorted": sort == SORT_EVAL,
            "direction": direction, "owned": owned, "capacity": capacity, "filter_active": filter_active, "refused": refused,
            "selected": selected, "detail_empty": any(DETAIL_EMPTY in w for w in words), "ok": ok,
            "calibrated": CALIBRATION["list_screen"] if screen == "list" else False, "guards": guards}


def _hsv_stats(image, roi):
    import numpy as np
    x, y, w, h = roi
    x0, y0 = max(0, x), max(0, y)
    patch = np.asarray(image[y0:y + h, x0:x + w], dtype=np.float32)
    if patch.size == 0:
        return None
    patch = patch.reshape(-1, 3) / 255.0
    mx, mn = patch.max(axis=1), patch.min(axis=1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    return sat, mx


def tile_greyed(image, tile) -> bool | None:
    """Picker page (UNCALIBRATED): greyed = the idol is in the team (unselectable). None without an image."""
    if image is None:
        return None
    stats = _hsv_stats(image, _sub(tile, TILE_COVER_ROI))
    return None if stats is None else bool(stats[1].mean() < GREY_VALUE_MAX and stats[0].mean() < GREY_SATURATION_MAX)


def tile_badge(image, tile) -> bool | None:
    """Picker page (UNCALIBRATED) マーク badge in the tile's top-left corner. A selected tile's pink frame also bleeds
    into this corner (0.24 on the reference), so callers must scan with 選択数 0 (クリア first)."""
    if image is None:
        return None
    stats = _hsv_stats(image, _sub(tile, TILE_BADGE_ROI))
    if stats is None:
        return None
    sat, val = stats
    ink = ((sat > 0.35) | (val < 0.45)).mean()
    return bool(ink >= BADGE_INK_MIN)


def list_tile_pixels(image, tile) -> dict:
    """一覧 tile pixel features (calibrated): marked, protected (lock), checked (✓), greyed, present."""
    import numpy as np
    out = {"marked": None, "protected": None, "checked": None, "greyed": None, "present": None}
    if image is None:
        return out
    p = _patch(image, _off(tile, LIST_MARK_ROI))
    if p is not None:
        mx, mn = p.max(axis=1), p.min(axis=1)
        sat = (mx - mn) / np.maximum(mx, 1)
        out["marked"] = bool(((sat > 0.35) | (mx < 110)).mean() >= MARK_INK_MIN)
    p = _patch(image, _off(tile, LIST_LOCK_ROI))
    if p is not None:
        out["protected"] = bool(((p[:, 0] > 200) & (p[:, 1] > 150) & (p[:, 2] < 110)).mean() >= LOCK_YELLOW_MIN)
    pink = _pink(image, _off(tile, LIST_CHECK_ROI))
    frame_pink = [_pink(image, _off(tile, roi)) for roi in LIST_SELECTED_FRAME_ROIS]
    out["checked"] = (None if pink is None or None in frame_pink
                      else pink >= CHECK_PINK_MIN and min(frame_pink) >= SELECTED_FRAME_PINK_MIN)
    frame, pill = _mean(image, _off(tile, LIST_FRAME_ROI)), _mean(image, _off(tile, LIST_PILL_ROI))
    out["greyed"] = None if frame is None else frame < FRAME_GREY_MAX
    out["present"] = None if pill is None else pill < PILL_PRESENT_MAX
    return out


# The ↑ glyph between 「Lv.5」 and 「x」 OCRs as nothing, 「+」, 「↑」 or both (live 2026-10-12: 「Lv.5+↑x13/21」).
# 2026-10-23 live: under the shown book the pink selection frame's edge OCRs as a leading 「(」 (「(Lv.5+x12/21」); one
# frame glyph on either side is tolerated.
_LV5 = re.compile(r"[(（|｜\[]?Lv\.?5[^x\d/]{0,2}x(\d{1,2})/(\d{1,2})[)）|｜\]]?")


_OVERLAY_PATTERNS = {"value": re.compile(r"\d{2,4}"), "vo": re.compile(r"\d{1,4}"), "da": re.compile(r"\d{1,4}"),
                     "vi": re.compile(r"\d{1,4}"), "me": re.compile(r"\d{1,4}"),
                     "count": re.compile(r"[x×X]\s?(\d{1,2})"), "date": re.compile(r"(20\d\d)/(\d\d)/(\d\d)")}


def _tile_overlay(toks, tile, kind, count) -> Any:
    """The sort's per-tile overlay (``SORT_OVERLAY``): int for value/stats/count, "YYYY-MM-DD" for 獲得順, else None.
    Exactly one matching token inside the overlay ROI; a ノウハウ数順 reading must agree with the pill's M."""
    if kind is None:
        return None
    pattern = _OVERLAY_PATTERNS[kind]
    hits = [m for t, b, _ in toks if _inside(b, _off(tile, LIST_OVERLAY_ROI)) for m in [pattern.fullmatch(t)] if m]
    if len(hits) != 1:
        return None
    m = hits[0]
    if kind == "date":
        y, mo, d = (int(v) for v in m.groups())
        try:
            return _dt.date(y, mo, d).isoformat()
        except ValueError:
            return None
    if kind == "count":
        n = int(m.group(1))
        return n if 1 <= n <= MAX_ROWS and (count is None or n == count) else None
    return int(m.group(0))


def parse_filter_popup(texts: Texts) -> dict:
    """絞り込み popup header: {"open": bool, "count": int|None} from 「該当するノウハウブック N」 (live as conditions change,
    before 決定; 2026-10-12 native #117/#122/#130: 25 / 403 / 195)."""
    toks = _tokens(texts)
    head = [(t, b) for t, b, _ in toks if b[1] < 100]
    label = next((b for t, b in head if "該当するノウハウブック" in t), None)
    if label is None:
        return {"open": False, "count": None}
    nums = [int(t) for t, b in head if re.fullmatch(r"\d{1,3}", t) and b[0] > label[0] + label[2] - 4
            and abs((b[1] + b[3] / 2) - (label[1] + label[3] / 2)) < 20]
    return {"open": True, "count": nums[0] if len(nums) == 1 else None}


def tile_key(tile: Mapping[str, Any], sort: str | None) -> tuple:
    """Within-run grid fingerprint of a tile (not a book id): cover short name, Lv5/ノウハウ数 pill, protect/mark flags
    and the sort's overlay. 獲得順 adds the acquisition date, which keeps it stable across scans."""
    return (sort, tile.get("name"), tile.get("lv5"), tile.get("knowhow_count"), tile.get("protected"),
            tile.get("marked"), tile.get("overlay"))


def parse_list_tiles(texts: Texts, image=None) -> dict:
    """一覧 grid (calibrated): per tile {row, col, box, value, lv5, knowhow_count, name, marked, protected, checked,
    greyed, present}. ``value`` (official 評価値) only in ノウハウ評価値順; ``order_ok`` checks the visible values follow
    the sort direction. Refuses (``tiles: []``) unless the frame is a normal-mode 一覧 without popup (image required)."""
    screen = classify_knowhow_screen(texts, image)
    out = {**screen, "tiles": [], "order_ok": None, "calibrated": CALIBRATION["list_tiles"]}
    if screen["screen"] != "list" or not screen["ok"]:
        return out
    toks = _tokens(texts)
    for row in range(LIST_GRID["rows"]):
        for col in range(LIST_GRID["cols"]):
            tile = list_tile_box(row, col)
            values = [t for t, b, _ in toks if _inside(b, _off(tile, LIST_VALUE_ROI)) and re.fullmatch(r"\d{2,4}", t)]
            pills = [m for t, b, _ in toks if _inside(b, _off(tile, LIST_LV5_ROI)) for m in [_LV5.fullmatch(t)] if m]
            names = [t for t, b, _ in toks if _inside(b, _off(tile, LIST_NAME_ROI))
                     and re.fullmatch(r"[぀-ヿ一-鿿]{1,4}", t)]
            lv5 = count = None
            if len(pills) == 1:
                n, m = int(pills[0].group(1)), int(pills[0].group(2))
                if n <= m <= MAX_ROWS:
                    lv5, count = n, m
            overlay = _tile_overlay(toks, tile, SORT_OVERLAY.get(screen["sort"]), count)
            out["tiles"].append({"row": row, "col": col, "box": list(tile), "overlay": overlay,
                                 "value": int(values[0]) if len(values) == 1 and screen["eval_sorted"] else None,
                                 "lv5": lv5, "knowhow_count": count, "name": names[0] if len(names) == 1 else None,
                                 **list_tile_pixels(image, tile)})
    vals = [t["value"] for t in out["tiles"] if t["value"] is not None]
    if screen["eval_sorted"] and vals:
        pairs = list(zip(vals, vals[1:]))
        if screen["direction"] == "desc":
            out["order_ok"] = all(a >= b for a, b in pairs)
        elif screen["direction"] == "asc":
            out["order_ok"] = all(a <= b for a, b in pairs)
    return out


def parse_picker_tiles(texts: Texts, image=None) -> dict:
    """Picker grid in ノウハウ評価値順 (5×3 tiles, UNCALIBRATED): per tile {row, col, value, name, marked, greyed, empty}.

    ``value`` is the official 評価値 (digits only inside the value ROI, exactly one token, else None).
    ``name`` is the cover short name (OCR is unreliable for kanji short names: display only, never identity).
    Refuses (``tiles: []``) on a destructive marker, a popup or a frame that is not the picker page. A 一覧 frame is
    delegated to the calibrated ``parse_list_tiles``.
    """
    screen = classify_knowhow_screen(texts, image)
    if screen["screen"] == "list":
        return parse_list_tiles(texts, image)
    out = {**screen, "tiles": [], "calibrated": CALIBRATION["picker"]}
    if screen["refused"] or screen["screen"] is None or screen["popup"]:
        return out
    toks = _tokens(texts)
    for row in range(PICKER_GRID["rows"]):
        for col in range(PICKER_GRID["cols"]):
            tile = tile_box(row, col)
            vroi, nroi = _sub(tile, TILE_VALUE_ROI), _sub(tile, TILE_NAME_ROI)
            values = [t for t, b, _ in toks if _inside(b, vroi) and re.fullmatch(r"\d{1,4}", t)]
            names = [t for t, b, _ in toks if _inside(b, nroi) and not re.fullmatch(r"[\d,]+", t)]
            value = int(values[0]) if len(values) == 1 and screen["eval_sorted"] else None
            out["tiles"].append({"row": row, "col": col, "value": value, "name": names[0] if len(names) == 1 else None,
                                 "marked": tile_badge(image, tile), "greyed": tile_greyed(image, tile),
                                 "empty": not values and not names})
    return out


_LV = re.compile(r"Lv\.?(\d)")
_COUNT = re.compile(r"ノウハウ数(\d{1,2})/(\d{1,2})")
_RINGS = "◯◎"


def _trailing_ring(image, box) -> str | None:
    """◯/◎ suffix right after (or at the end of) a name token; Maa OCR drops ◎ and reads ◯ as 〇.

    Last ink blob (columns split by a ≥2 px gap) in [box end − 28, box end + 48]: a round glyph 12–24 px with empty
    corners and centre; 4 ink runs across its middle rows = ◎, 2 = ◯. Calibrated on オールラウンダー◯/◎ and Dance適正◎."""
    import numpy as np
    x, y, w, h = box
    x0, x1, y0, y1 = max(0, x + w - 28), min(RING_MAX_X, x + w + 48), y + 2, y + h - 1
    if x1 - x0 < 12 or y1 - y0 < 12:
        return None
    ink = np.asarray(image[y0:y1, x0:x1], dtype=np.int32).max(axis=2) < 200
    cols = ink.sum(axis=0) >= 2
    blobs, start, end, gap = [], None, 0, 0
    for i, c in enumerate(cols):
        if c:
            start = i if start is None else start
            end, gap = i, 0
        elif start is not None:
            gap += 1
            if gap >= 2:
                blobs.append((start, end))
                start = None
    if start is not None:
        blobs.append((start, end))
    if not blobs:
        return None
    s, e = blobs[-1]
    sub = ink[:, s:e + 1]
    rows = np.where(sub.sum(axis=1) > 0)[0]
    sub = sub[rows[0]:rows[-1] + 1]
    hh, ww = sub.shape
    if not (12 <= ww <= 24 and 12 <= hh <= 24 and abs(ww - hh) <= 4):
        return None
    corners = max(sub[:3, :3].mean(), sub[:3, -3:].mean(), sub[-3:, :3].mean(), sub[-3:, -3:].mean())
    cy, cx = hh // 2, ww // 2
    if corners > 0.34 or sub[cy - 1:cy + 2, cx - 1:cx + 2].any():
        return None
    runs = max(int((sub[r][1:] & ~sub[r][:-1]).sum() + sub[r][0]) for r in range(cy - 1, cy + 2))
    return "◎" if runs >= 4 else "◯" if runs == 2 else None


def _lines(tokens, gap: float = 8):
    """Group (text, box, score) tokens into lines by centre y, each line sorted by x."""
    lines: list[list] = []
    for tok in sorted(tokens, key=lambda t: _center(t[1])[1]):
        if lines and abs(_center(tok[1])[1] - _center(lines[-1][-1][1])[1]) <= gap:
            lines[-1].append(tok)
        else:
            lines.append([tok])
    return [sorted(line, key=lambda t: t[1][0]) for line in lines]


DETAIL_ICON_X = 20                         # type icon left edge (OCR boxes that swallowed the icon start here)
DETAIL_ICON_NAME_X = 30                    # 2026-10-12 live: OCR may swallow the type icon into the name (box x 32)


def _name_start(box) -> bool:
    """A name token starts in the name column, or at the type icon when OCR merged the icon into a whole name (wide,
    name-height box (h >= 20: 2026-10-12 book 7 read the same name at h 23); lone icon noise such as 「A」 is narrow and effect lines start left of x 30)."""
    x, _, w, h = box
    return DETAIL_NAME_X[0] <= x < DETAIL_NAME_X[1] or (DETAIL_ICON_NAME_X <= x < DETAIL_NAME_X[0] and w >= 60 and h >= 20)


_GLUED_LV = re.compile(r"(.{2,}?)(Lv\.?\d)")
GLUED_LV_W = 52                              # px of a 「Lv.N」 token (live: 50-55)


def _split_glued_levels(toks: list) -> list:
    """A long name read together with its level as one token (2026-10-23 live seg21: 「LandingPoint大成功(アビリティ)Lv.1」
    box (56,416,356,31)) is split into the name and a 「Lv.N」 token at the box's right end, when that end lies in the
    level column; otherwise the row had no level and was dropped (detail_rows_missing 8/9)."""
    out = []
    for t, b, sc in toks:
        m = _GLUED_LV.fullmatch(t)
        x, y, w, h = b
        lv_x = x + w - GLUED_LV_W
        if m and DETAIL_LEVEL_X[0] <= lv_x + GLUED_LV_W // 2 <= DETAIL_LEVEL_X[1] and lv_x - 4 > x:
            # The name ran on into its Lv on one line, so it did not wrap: its box ends before DETAIL_WRAP_X.
            end = min(lv_x - 4, DETAIL_WRAP_X - 1)
            out += [(m.group(1), (x, y, end - x, h), sc), (m.group(2), (lv_x, y, GLUED_LV_W, h), sc)]
        else:
            out.append((t, b, sc))
    return out


def parse_detail_panel(texts: Texts, image=None) -> dict:
    """Left detail panel: {title, idol, stats, rows: [{name, level, effect, clipped, score, repairs}], knowhow_count,
    protected, empty, dropped}.

    One row = a 「Lv.N」 token in the Lv column. Its name = name-column tokens from 24 px above to 12 px below the Lv
    centre (a wrapped second line is joined); the effect = left-column lines below it, up to the next row. A row is
    ``clipped`` when its name touches the list top, only its second line is visible, its Lv crosses the bottom edge, or
    a wrapped name's tail is unread; a row whose name is fully hidden is dropped (counted in ``dropped``).
    Repairs (recorded per row): ◯/◎ suffix from pixels (needs ``image``), the wrapped closing parenthesis, and
    scenario abbreviations split across lines, e.g. ``(G.R`` + ``A.D.)``."""
    toks = _split_glued_levels(_tokens(texts))
    title_parts = [(b[0], t) for t, b, _ in toks if _inside(b, DETAIL_TITLE_ROI)]
    title = repair_reading("".join(t for _, t in sorted(title_parts))) or None
    stats = {}
    for key, roi in DETAIL_STAT_ROIS.items():
        vals = [t for t, b, _ in toks if _inside(b, roi) and re.fullmatch(r"\d{1,3}", t)]
        if len(vals) == 1:
            stats[key] = int(vals[0])
    count = None
    for t, b, _ in toks:
        m = _COUNT.search(t) or (re.fullmatch(r"(\d{1,2})/(\d{1,2})", t) if _inside(b, DETAIL_COUNT_ROI) else None)
        if m and int(m.group(1)) <= int(m.group(2)) <= MAX_ROWS:
            count = int(m.group(1))
            break
    prot = [t for t, b, _ in toks if _inside(b, DETAIL_PROTECT_ROI) and t.startswith("保護")]
    protected = True if "保護解除" in prot else False if prot else None
    lx, ly, lw, lh = DETAIL_LIST_ROI
    top, bottom = ly, ly + lh
    in_list = [tok for tok in toks if _inside(tok[1], DETAIL_LIST_ROI) and _MEANINGFUL.search(tok[0])]
    levels = sorted((tok for tok in in_list
                     if _LV.fullmatch(tok[0]) and DETAIL_LEVEL_X[0] <= _center(tok[1])[0] <= DETAIL_LEVEL_X[1]),
                    key=lambda tok: _center(tok[1])[1])
    others = [tok for tok in in_list if _center(tok[1])[0] < DETAIL_LEVEL_X[0] and not _LV.fullmatch(tok[0])]
    rows, dropped = [], 0
    for i, (lv_text, lv_box, lv_score) in enumerate(levels):
        lcy = _center(lv_box)[1]
        next_cy = _center(levels[i + 1][1])[1] if i + 1 < len(levels) else bottom + 24
        name_toks = [tok for tok in others if _name_start(tok[1]) and lcy - 24 <= _center(tok[1])[1] <= lcy + 12]
        if not name_toks:
            dropped += 1
            continue
        lines = _lines(name_toks)
        name = "".join(t for line in lines for t, _, _ in line)
        repairs: list[str] = []
        first_top = min(b[1] for _, b, _ in lines[0])
        clipped = first_top < top + 2 or _center(lines[0][0][1])[1] - lcy > 2 or lv_box[1] + lv_box[3] > bottom
        if image is not None:
            ring = _trailing_ring(image, lines[-1][-1][1])
            base = name.rstrip(_RINGS)
            if ring and name[len(base):] != ring:
                name = base + ring
                repairs.append("ring")
        line_end = max(b[0] + b[2] for _, b, _ in lines[0])
        if len(lines) == 1 and line_end >= DETAIL_WRAP_X:
            if name.count("(") > name.count(")") and lcy + 16 <= bottom:
                name += ")"
                repairs.append("closing_paren")
            else:
                clipped = True              # the wrapped tail was not read: never guess the missing text
        for pattern, canon in _SCENARIO_ABBR:
            fixed = pattern.sub(canon, name)
            if fixed != name:
                name = fixed
                repairs.append("abbreviation")
        name_bottom = max(_center(b)[1] for line in lines for _, b, _ in line)
        effect_toks = [tok for tok in others if tok not in name_toks
                       and max(lcy + 8, name_bottom + 6) < _center(tok[1])[1] < next_cy - 24]
        effect = "".join(t for line in _lines(effect_toks) for t, _, _ in line) or None
        score = min([lv_score] + [s for line in lines for _, _, s in line])
        rows.append({"name": name, "level": int(_LV.fullmatch(lv_text).group(1)), "effect": effect, "clipped": clipped,
                     "score": round(score, 3), "repairs": sorted(set(repairs)), "y": round(lcy)})
    return {"title": title, "idol": idol_from_title(title), "stats": stats, "rows": rows, "knowhow_count": count,
            "protected": protected, "empty": any(DETAIL_EMPTY in t for t, _, _ in toks), "dropped": dropped,
            "calibrated": CALIBRATION["detail_panel"]}


LIST_PILL_MIN_X = 500                       # grid pills only (the detail panel's own 「Lv.N」 rows lie left of it)
_PILL_LIKE = re.compile(r"Lv\.?5.*x")


def _pill_ok(text: str) -> bool:
    m = _LV5.fullmatch(text)
    return bool(m) and int(m.group(1)) <= int(m.group(2)) <= MAX_ROWS


def refine_list_pills(texts: Texts, image, ocr) -> list:
    """Re-read grid pills whose full-frame OCR does not parse (2026-10-23 live seg4: 「Lv.5↑x11/21」 read as
    「Lv.5+x11/2」 on every hold). The pill box (+6 px, wider right) is upscaled x2 and read by
    ``ocr(rgb_crop) -> [(text, (x, y, w, h), score)]``; a single token matching ``_LV5`` with score >= 0.6 replaces the
    original token in its original box (the reading must give N <= M <= 21; 「x11/2」 fully matches but is not). Otherwise the original token stays (the tile remains unreadable)."""
    import numpy as np
    from PIL import Image

    toks = _tokens(texts)
    out = []
    for t, b, sc in toks:
        x, y, w, h = b
        if x < LIST_PILL_MIN_X or _pill_ok(t) or not _PILL_LIKE.search(t):
            out.append((t, b, sc))
            continue
        crop = np.ascontiguousarray(image[max(0, y - 6):y + h + 6, max(0, x - 10):x + w + 18])
        big = np.asarray(Image.fromarray(crop).resize((crop.shape[1] * 2, crop.shape[0] * 2), Image.LANCZOS))
        try:
            found = _tokens([(rt, rb, rs) for rt, rb, rs in (ocr(big) or ()) if rt])
        except Exception:
            found = []
        good = [(rt, rs) for rt, _, rs in found if _pill_ok(rt) and rs >= REREAD_MIN_SCORE]
        out.append((good[0][0], b, good[0][1]) if len(found) == 1 and len(good) == 1 else (t, b, sc))
    return out


def reread_detail_count(image, ocr) -> list:
    """Token for the detail panel's 「N / M」 ノウハウ数 when the full-frame OCR did not give one (2026-10-23 live seg18:
    「14 / 21」 read 「147 21」, the slash as 7). ``DETAIL_COUNT_ROI`` is upscaled x2 and read by ``ocr``; exactly one
    「N/M」 token with score >= 0.6 becomes a token centred in the ROI. Returns [] otherwise."""
    import numpy as np
    from PIL import Image

    x, y, w, h = DETAIL_COUNT_ROI
    crop = np.ascontiguousarray(image[y:y + h, x:x + w])
    big = np.asarray(Image.fromarray(crop).resize((w * 2, h * 2), Image.LANCZOS))
    try:
        found = _tokens([(rt, rb, rs) for rt, rb, rs in (ocr(big) or ()) if rt])
    except Exception:
        return []
    counts = [(t, s) for t, _, s in found if re.fullmatch(r"\d{1,2}/\d{1,2}", t) and s >= REREAD_MIN_SCORE]
    return [(counts[0][0], (x + w // 4, y + h // 4, w // 2, h // 2), counts[0][1])] if len(counts) == 1 else []


def reread_detail_stats(image, ocr, keys) -> list:
    """Tokens for detail-panel stats the full read missed (2026-10-24 book 419: メンタル 「1」, a lone thin digit, was not
    detected on any page). Each ``DETAIL_STAT_ROIS`` crop is upscaled x2 (then x3) and read by ``ocr``; exactly one
    1–3 digit token with score >= 0.6 becomes a token centred in the ROI. Unread stats give nothing."""
    import numpy as np
    from PIL import Image

    out = []
    for key in keys:
        x, y, w, h = DETAIL_STAT_ROIS[key]
        crop = np.ascontiguousarray(image[y:y + h, x:x + w])
        for scale in (2, 3):
            big = np.asarray(Image.fromarray(crop).resize((w * scale, h * scale), Image.LANCZOS))
            try:
                found = _tokens([(rt, rb, rs) for rt, rb, rs in (ocr(big) or ()) if rt])
            except Exception:
                return out
            vals = [(t, s) for t, _, s in found if re.fullmatch(r"\d{1,3}", t) and s >= REREAD_MIN_SCORE]
            if len(vals) == 1:
                out.append((vals[0][0], (x + w // 4, y + h // 4, w // 2, h // 2), vals[0][1]))
                break
    return out


def reread_out_of_order(texts: Texts, tiles, image, ocr, descending: bool = True) -> list | None:
    """Tokens with the 評価値 of every tile on either side of an order break re-read from its ``LIST_VALUE_ROI``
    (2026-10-23 live seg22: a fully visible 795 read 「195」, same length, so ``refine_list_values`` kept it). Returns
    the new token list, or None when nothing was broken or no re-read changed a value (the caller's stop stands)."""
    valued = [t for t in tiles if t.get("value") is not None and t.get("box")]
    suspects = set()
    for a, b in zip(valued, valued[1:]):
        if (a["value"] < b["value"]) if descending else (a["value"] > b["value"]):
            suspects |= {id(a), id(b)}
    if not suspects:
        return None
    suspect_tiles = [t for t in valued if id(t) in suspects]
    rois = [_off(t["box"], LIST_VALUE_ROI) for t in suspect_tiles]
    fresh = reread_missing_values([{**t, "value": None} for t in suspect_tiles], image, ocr)
    new = {i: tok for i, roi in enumerate(rois) for tok in fresh if _inside(tok[1], roi)}
    if all(tok[0] == str(suspect_tiles[i]["value"]) for i, tok in new.items()):
        return None
    out = [tok for tok in _tokens(texts)
           if not (re.fullmatch(r"\d{2,4}", tok[0]) and any(_inside(tok[1], rois[i]) for i in new))]
    return out + list(new.values())


def reread_missing_values(tiles, image, ocr) -> list:
    """Value tokens for present tiles whose 評価値 the full-frame OCR did not detect at all (2026-10-23 live seg9: tile
    「羽那」 915 had no token on every hold). Each tile's ``LIST_VALUE_ROI`` (+6 px) is upscaled x2 and read by ``ocr``;
    exactly one 2-4 digit token with score >= 0.6 (other noise ignored) becomes a token centred in that ROI. Returns only
    the new tokens."""
    import numpy as np
    from PIL import Image

    extra = []
    for tile in tiles:
        if tile.get("present") is not True or tile.get("value") is not None or not tile.get("box"):
            continue
        x, y, w, h = _off(tile["box"], LIST_VALUE_ROI)
        crop = np.ascontiguousarray(image[max(0, y - 6):y + h + 6, max(0, x - 6):x + w + 6])
        if crop.size == 0:
            continue
        big = np.asarray(Image.fromarray(crop).resize((crop.shape[1] * 2, crop.shape[0] * 2), Image.LANCZOS))
        try:
            found = _tokens([(rt, rb, rs) for rt, rb, rs in (ocr(big) or ()) if rt])
        except Exception:
            found = []
        # 2026-10-23 live seg12: the selected locked tile read 「775」 (0.99) plus frame-edge noise 「1」 (0.57).
        values = [(rt, rs) for rt, _, rs in found if re.fullmatch(r"\d{2,4}", rt) and rs >= REREAD_MIN_SCORE]
        if len(values) == 1:
            extra.append((values[0][0], (x + w // 4, y + h // 4, w // 2, h // 2), values[0][1]))
    return extra


def refine_list_values(texts: Texts, image, ocr) -> list:
    """Re-read grid 評価値 tokens shorter than the longest one on screen (2026-10-23 live seg5: the selected tile's 875
    beside the pink selection frame read as 「87」 and stopped the scan with grid_order_broken; seg17: 510 read 「10」,
    the lost digit on the left). The value box (+30 px left, +20 px right) is upscaled x2 and read by ``ocr``; the one
    2-4 digit token with score >= 0.6, longer than the original, replaces it in its original box. A correct short value (1000 → 999 on one
    screen) reads the same and stays."""
    import numpy as np
    from PIL import Image

    toks = _tokens(texts)
    values = [len(t) for t, b, _ in toks if b[0] >= LIST_PILL_MIN_X and re.fullmatch(r"\d{2,4}", t)]
    longest = max(values, default=0)
    out = []
    for t, b, sc in toks:
        x, y, w, h = b
        if x < LIST_PILL_MIN_X or not re.fullmatch(r"\d{2,4}", t) or len(t) >= longest:
            out.append((t, b, sc))
            continue
        crop = np.ascontiguousarray(image[max(0, y - 6):y + h + 6, max(0, x - 30):x + w + 20])
        big = np.asarray(Image.fromarray(crop).resize((crop.shape[1] * 2, crop.shape[0] * 2), Image.LANCZOS))
        try:
            found = _tokens([(rt, rb, rs) for rt, rb, rs in (ocr(big) or ()) if rt])
        except Exception:
            found = []
        # One confident 2-4 digit number longer than the original; 1-glyph noise (lock icon 「8」/「0」) is ignored.
        values = [(rt, rs) for rt, _, rs in found if re.fullmatch(r"\d{2,4}", rt) and rs >= REREAD_MIN_SCORE]
        good = len(values) == 1 and len(values[0][0]) > len(t)
        out.append((values[0][0], b, values[0][1]) if good else (t, b, sc))
    return out


DETAIL_TALL_NAME = 34                       # a name box taller than one line (~22-30 px) = two stacked lines
DETAIL_LOW_SCORE = 0.8
REREAD_MIN_SCORE = 0.6


def refine_detail_tokens(texts: Texts, image, ocr) -> list:
    """Re-read stacked two-line names of the detail list one line at a time (2026-10-12 live calibration frames 2-3:
    「今がキラキラ！ おもいッきりア / イドル！(Vo)」 was one det box read as 「今る」 at 0.58).

    For each name-column token taller than one line (both halves) or scored below 0.8 (its own band), the name column
    of the band is cropped, upscaled x2 and read by ``ocr(rgb_crop) -> [(text, (x, y, w, h), score)]``; when every band
    of the token reads text (score >= 0.6), the name-column tokens of those bands are replaced by the re-read tokens
    (frame coordinates). Otherwise the original tokens stay: this never removes evidence it cannot replace."""
    import numpy as np
    from PIL import Image

    toks = _tokens(texts)
    lx, ly, lw, lh = DETAIL_LIST_ROI
    x0, x1 = DETAIL_NAME_X[0], DETAIL_LEVEL_X[0] - 4

    def read_at(top, bottom, scale):
        crop = np.ascontiguousarray(image[top:bottom, x0:x1])
        big = np.asarray(Image.fromarray(crop).resize((crop.shape[1] * scale, crop.shape[0] * scale), Image.LANCZOS))
        try:
            found = [(t, (x0 + bx // scale, top + by // scale, max(1, bw // scale), max(1, bh // scale)), float(sc))
                     for t, (bx, by, bw, bh), sc in (ocr(big) or ()) if t]
        except Exception:
            return []
        meaningful = [r for r in _tokens(found) if _MEANINGFUL.search(r[0])]
        # A band with unreadable text failed as a whole (2026-10-23 live seg1: the second line read 「Fル.!」 at 0.42
        # beside the effect line 「ocal+20」; keeping only the latter dropped 「イドル！(Vo)」 from the name).
        return meaningful if all(r[2] >= REREAD_MIN_SCORE for r in meaningful) else []

    def read(top, bottom):
        # x2 first; a failed band is tried once more at x3 (2026-10-23 live seg16: 「イドル！(Vi)」 read 「町イルイてV」 0.57 at
        # x2 and 「リイドルイ（Vi」 0.76 at x3; it was the only unclipped view of that row).
        return read_at(top, bottom, 2) or read_at(top, bottom, 3)

    # Suspicious name tokens (tall or low score) that overlap vertically form one group (2026-10-12 live: the low-score
    # first line and the tall second-line box of one stacked name); each group is re-read once, line by line, so that
    # re-reading one line never discards the other line's evidence.
    lv_ys = [_center(b)[1] for t, b, _ in toks if _LV.fullmatch(t)]
    clean_names = [b for t, b, sc in toks if x0 - 8 <= b[0] < x1 and b[3] <= DETAIL_TALL_NAME
                   and sc >= DETAIL_LOW_SCORE and not _LV.fullmatch(t)]

    def icon_merged(b) -> bool:
        # 2026-10-12 live: 「アピールUP(思い出高&低)(G.R / A.D.)」 read as one box (25,408,349,55) with the type icon.
        # Only a tall box with a 「Lv.N」 beside its lines is a name; merged effect lines have no Lv there. A Lv line
        # whose own clean name starts at or above this box is not this box's (2026-10-23 live seg3: the effect text
        # under 「Dance適正 ○」 read as one box (20,424,338,49) and its re-read band cut the name into a clipped
        # duplicate). A second-line fragment such as 「A.D.)」 (60,439,70,33) starts below the box and does not count.
        def named(y) -> bool:
            return any(n[1] <= b[1] + 2 and n[1] <= y <= n[1] + n[3] for n in clean_names)

        return (DETAIL_ICON_X <= b[0] < x0 - 8 and b[3] > DETAIL_TALL_NAME
                and any(b[1] <= y <= b[1] + b[3] + 12 and not named(y) for y in lv_ys))

    groups: list[list[int]] = []
    for t, b, sc in sorted(toks, key=lambda tok: tok[1][1]):
        if not (_inside(b, DETAIL_LIST_ROI) and (x0 - 8 <= b[0] < x1 or icon_merged(b))
                and (b[3] > DETAIL_TALL_NAME or sc < DETAIL_LOW_SCORE)):
            continue
        if _LV.fullmatch(t) or b[1] < ly + 2:
            continue                                   # clipped at the list top: leave it to the clipped rule
        if groups and b[1] < groups[-1][1] - 4:
            groups[-1][1] = max(groups[-1][1], b[1] + b[3])
        else:
            groups.append([b[1], b[1] + b[3]])
    out = list(toks)
    for g_top, g_bottom in groups:
        mid = (g_top + g_bottom) // 2
        halves = ([(g_top - 2, mid + 2), (mid - 2, g_bottom + 2)] if g_bottom - g_top > DETAIL_TALL_NAME
                  else [(g_top - 4, g_bottom + 4)])
        bands = [(max(ly, a), min(ly + lh, z)) for a, z in halves]
        if any(z - a < 10 for a, z in bands):
            continue
        reads = [read(a, z) for a, z in bands]
        if not all(reads):
            continue
        top, bottom = bands[0][0], bands[-1][1]
        out = [tok for tok in out if not ((x0 - 8 <= tok[1][0] < x1 or icon_merged(tok[1]))
                                          and top <= _center(tok[1])[1] < bottom
                                          and not _LV.fullmatch(tok[0]))] + [r for found in reads for r in found]
    return out


def _name_tail(name: str) -> tuple[int, str]:
    stripped = name.rstrip("+")
    return len(name) - len(stripped), stripped[-1:] if stripped[-1:] in _RINGS else ""


_TYPE_SUFFIX = re.compile(r"\((Vo|Da|Vi|Me)")


def _type_suffix(name: str) -> str | None:
    m = _TYPE_SUFFIX.search(name or "")
    return m.group(1) if m else None


def _ocr_close(a: str, b: str) -> bool:
    """Same row read twice with an OCR slip. Tier suffixes (+/++/+++) and ◯/◎ must agree exactly."""
    from difflib import SequenceMatcher
    if a == b:
        return True
    if _type_suffix(a) != _type_suffix(b):
        return False                     # 「…(Vo)」 vs 「…(Da)」: sibling rows of one live skill, never one row
    return _name_tail(a) == _name_tail(b) and SequenceMatcher(None, a, b).ratio() >= 0.8


def _catalog_known(name: str) -> bool:
    return any(normalize_name(e.name) == name or (e.match == "prefix" and name.startswith(normalize_name(e.name)))
               for e in GENERIC_CATALOG)


def _reading_rank(row: Mapping[str, Any]) -> tuple:
    return (not row.get("clipped"), _catalog_known(row["name"]), row.get("score") or 0)


GEOMETRY_TOLERANCE = 12                     # px: a row's Lv centre after a measured shift
DETAIL_ROW_PITCH = (99, 109)                # px between consecutive rows' Lv centres (live 2026-10-23: 102-106)


def _reconcile(old: dict, new: dict) -> dict:
    if old["name"] != new["name"]:
        keep, other = (new, old) if _reading_rank(new) > _reading_rank(old) else (old, new)
        variants = set(keep.get("variants") or ()) | set(other.get("variants") or ()) | {other["name"]}
        return {**keep, "variants": sorted(variants)}
    if old.get("clipped") and not new.get("clipped"):
        return {**new, **({"variants": old["variants"]} if old.get("variants") else {})}
    return old


def _merge_by_shift(merged: list, prev: list, rows: list, dy: int, unknowns: list) -> list:
    """Rows of the previous page (``prev`` = [(y, merged index)]) seen again ``dy`` px higher are the same rows; the
    rest must follow them. Returns this page's [(y, merged index)]."""
    placed, last = [], None
    for row in rows:
        y = row.get("y")
        hit = None if y is None else next((i for py, i in prev if abs(py - dy - y) <= GEOMETRY_TOLERANCE), None)
        if hit is not None:
            if merged[hit].get("level") != row.get("level"):
                unknowns.append("merge.level_conflict")
            merged[hit] = _reconcile(merged[hit], row)
            if last is not None and hit <= last:
                unknowns.append("merge.geometry_conflict")
            last = hit
        else:
            if prev and y is not None and y - (prev[-1][0] - dy) < -GEOMETRY_TOLERANCE:
                unknowns.append("merge.geometry_conflict")   # an unseen row above rows already seen
            merged.append(row)
            hit = last = len(merged) - 1
        placed.append((y, hit))
    if prev and rows and not any(abs(py - dy - (r.get("y") or -999)) <= GEOMETRY_TOLERANCE for py, _ in prev for r in rows):
        # No row read on both pages. The pages still join when this page's first row sits exactly one row below the
        # previous page's last read row (2026-10-24 book 414: the first page missed its third row, read on the next).
        first, last = rows[0].get("y"), prev[-1][0]
        if first is None or last is None or not DETAIL_ROW_PITCH[0] <= first - (last - dy) <= DETAIL_ROW_PITCH[1]:
            unknowns.append("merge.overlap_lost")
    return placed


def merge_detail_pages(pages: Sequence[Mapping[str, Any]], shifts: Sequence[int | None] | None = None,
                       vocabulary: Any = None) -> dict:
    """Merge only contiguous observations of the same book; keep gap/conflict evidence even if counts match.

    Overlap = the longest run where the previous tail equals the next head by (name, Lv). Only when no exact overlap
    exists may names differ by an OCR slip (same Lv, same +/◯/◎ tail, similarity ≥0.8). The kept reading prefers an
    unclipped one, then a catalogue-known name, then the higher OCR score; the other spelling is kept in ``variants``.

    ``shifts[i]`` = measured content shift (px, > 0 = scrolled down) from page i-1 to page i (``shifts[0]`` unused):
    rows are then paired by their Lv centre ``y`` instead of by name (``_merge_by_shift``).

    ``vocabulary`` (``knowhow_vocab.Vocabulary``): a row still clipped after merging (never read whole: 2026-10-23
    live, garbled two-line names) is accepted when its Lv was read and all of its readings that snap trust one known
    name; it takes that name (the reading is kept in ``reading``). Other rows are left as read."""
    merged: list[dict] = []
    unknowns = list(dict.fromkeys(str(u) for p in pages for u in p.get("unknowns") or ()))
    titles = [p["title"] for p in pages if p.get("title")]
    idols = [normalize_name(p["idol"]) for p in pages if p.get("idol")]
    if any(not same_book_title(titles[0], t) for t in titles[1:]) or any(not _same_idol(idols[0], i) for i in idols[1:]):
        unknowns.append("merge.book_conflict")
    counts = {p["knowhow_count"] for p in pages if p.get("knowhow_count") is not None}
    if len(counts) > 1:
        unknowns.append("merge.count_conflict")
    for stat in STATS:
        if len({p["stats"][stat] for p in pages if stat in (p.get("stats") or {})}) > 1:
            unknowns.append("merge.stats_conflict")
    protected = {p["protected"] for p in pages if isinstance(p.get("protected"), bool)}
    if len(protected) > 1:
        unknowns.append("merge.protected_conflict")
    prev: list = []
    for index, page in enumerate(pages):
        if page.get("complete") is False:
            unknowns.append("merge.incomplete_page")
        rows = [dict(r) for r in page.get("rows") or ()]
        if shifts is not None:
            # 2026-10-12 live: one detail wheel step = 160 px ≈ 1.5 rows, so the shared row is often cut on one side
            # and two-line names read differently per page; the measured pixel shift pairs rows by position instead.
            dy = shifts[index] if index < len(shifts) else None
            if index and (dy is None or any(r.get("y") is None for r in rows)):
                unknowns.append("merge.shift_unknown")
                break
            prev = _merge_by_shift(merged, prev, rows, dy or 0, unknowns)
            continue
        # A row cut at a page edge is read whole on the neighbouring page (one wheel step keeps >= 1 row overlap):
        # leading clipped rows are dropped after the first page, trailing ones before the last.
        if page is not pages[0]:
            while rows and rows[0].get("clipped"):
                rows.pop(0)
        if page is not pages[-1]:
            while rows and rows[-1].get("clipped"):
                rows.pop()
        best = 0
        for loose in (False, True):
            for k in range(min(len(merged), len(rows)), 0, -1):
                if all(a.get("level") == b.get("level") and (a["name"] == b["name"] or (loose and _ocr_close(a["name"], b["name"])))
                       for a, b in zip(merged[-k:], rows[:k])):
                    best = k
                    break
            if best:
                break
        if merged and rows and not best:
            unknowns.append("merge.overlap_lost")
        for i in range(best):
            j = len(merged) - best + i
            old, new = merged[j], rows[i]
            if old["name"] != new["name"]:
                keep, other = (new, old) if _reading_rank(new) > _reading_rank(old) else (old, new)
                variants = set(keep.get("variants") or ()) | set(other.get("variants") or ()) | {other["name"]}
                merged[j] = {**keep, "variants": sorted(variants)}
            elif old.get("clipped") and not new.get("clipped"):
                merged[j] = {**new, **({"variants": old["variants"]} if old.get("variants") else {})}
        merged.extend(rows[best:])
    if vocabulary is not None:
        for i, row in enumerate(merged):
            if not row.get("clipped") or row.get("level") not in LEVELS:
                continue
            snaps = [vocabulary.snap(n, row.get("effect")) for n in [row["name"], *(row.get("variants") or ())]]
            names = {sn.name for sn in snaps if sn.trusted}
            if len(names) == 1:
                merged[i] = {**row, "name": names.pop(), "clipped": False, "reading": row["name"],
                             "repairs": [*(row.get("repairs") or ()), "vocab"]}
    if any(r.get("clipped") for r in merged):
        unknowns.append("rows.clipped")
    if any(r.get("level") not in LEVELS for r in merged):
        unknowns.append("rows.level_unknown")
    count = next(iter(counts)) if len(counts) == 1 else None
    if count is None or len(merged) != count:
        unknowns.append("rows.count_unverified")
    title = next((p.get("title") for p in pages if p.get("title")), None)
    idol = next((p.get("idol") for p in pages if p.get("idol")), None)
    stats = {k: p["stats"][k] for p in pages for k in STATS if k in (p.get("stats") or {})}
    return {"title": title, "idol": idol, "stats": stats, "rows": merged, "knowhow_count": count,
            "protected": next(iter(protected)) if len(protected) == 1 else None,
            "unknowns": list(dict.fromkeys(unknowns)), "complete": bool(merged) and not unknowns}


def book_from_detail(merged: Mapping[str, Any], *, eval_value: int | None = None, marked: bool | None = None,
                     frames: Sequence[str] = (), seen_at: str | None = None) -> KnowhowBookRecord:
    unknowns = list(merged.get("unknowns") or ())
    unknowns += [k for k in ("title", "knowhow_count") if not merged.get(k)]
    unknowns += [f"stats.{k}" for k in STATS if k not in (merged.get("stats") or {})]
    if merged.get("complete") is False:
        unknowns.append("rows.incomplete")
    if any(r.get("clipped") for r in merged.get("rows") or ()):
        unknowns.append("rows.clipped")
    return KnowhowBookRecord.from_dict({**merged, "eval_value": eval_value, "marked": marked, "source": "scan",
                                        "frames": list(frames), "unknowns": list(dict.fromkeys(unknowns)),
                                        "first_seen": seen_at, "last_seen": seen_at})


def book_from_produce_end(reading: Any, *, frame: str | None = None, seen_at: str | None = None) -> KnowhowBookRecord | None:
    """K2 incremental ingest from the 獲得報酬 ノウハウブック tab: ``wing_screen.read_end(...)["knowhow_books"]`` (rows of
    the selected book: [{name, level}]), a ProduceEndSummary, or its dict. Title/stats are not on that screen, so the
    record is partial (goes to ``KnowhowInventory.pending`` until a scan reconciles it)."""
    rows = getattr(reading, "knowhow_books", None)
    if rows is None and isinstance(reading, Mapping):
        rows = reading.get("knowhow_books")
    if rows is None and isinstance(reading, (list, tuple)):
        rows = reading
    parsed = [r for r in (KnowhowRow.from_any(v) for v in rows or ()) if r]
    if not parsed:
        return None
    return KnowhowBookRecord(rows=tuple(parsed[:MAX_ROWS]), source="produce_end", frames=(frame,) if frame else (),
                             unknowns=("title", "stats", "eval_value", "knowhow_count"), first_seen=seen_at, last_seen=seen_at)


def book_from_reward(book: Mapping[str, Any], *, frame: str | None = None,
                     seen_at: str | None = None) -> KnowhowBookRecord | None:
    """A book read in the 獲得報酬 detail pane (wing_screen.parse_reward_detail via the wing_nav review): title,
    owner idol, four stats, visible knowhow rows and 保護. That pane has no 「ノウハウ数」, so the record stays partial
    (``knowhow_count`` unknown; ``rows.partial`` when the list may continue) and goes to ``KnowhowInventory.pending``
    until a K-series scan reconciles it."""
    if not isinstance(book, Mapping) or not book.get("title"):
        return None
    unknowns = ["knowhow_count", "eval_value"] + (["rows.partial"] if book.get("rows_partial") else [])
    rec = KnowhowBookRecord.from_dict({"title": book.get("title"), "idol": book.get("idol"), "stats": book.get("stats"),
                                       "rows": book.get("rows") or (), "protected": book.get("protected"),
                                       "source": "produce_end", "frames": [frame] if frame else [],
                                       "unknowns": unknowns, "first_seen": seen_at, "last_seen": seen_at})
    return rec if rec.rows else None


def ingest_reward_books(inv: "KnowhowInventory", books: Iterable[Mapping[str, Any]], *,
                        seen_at: str | None = None) -> int:
    """Add the 獲得報酬 books of one Produce to ``inv.pending`` (then try to reconcile); returns the number added."""
    added = 0
    for raw in books or ():
        rec = book_from_reward(raw, seen_at=seen_at)
        if rec is not None:
            inv.add_pending(rec)
            added += 1
    if added:
        inv.reconcile_pending()
    return added


# ------------------------------------------------------------------------------------------------- UI export
def canonical_rows(books: list[dict]) -> int:
    """Display names for the panel: each row reading that the vocabulary snaps with confidence (knowhow_vocab, status
    ``snapped``) is shown under its canonical name, the reading kept as ``raw``; ambiguous / unknown readings stay as
    read. The private inventory keeps the readings. Returns the number of renamed rows."""
    from agent.core import knowhow_vocab as kv   # lazy: knowhow_vocab imports this module

    vocab = kv.Vocabulary.load(table=kv.learn_table(books))
    renamed = 0
    for book in books:
        for row in book.get("rows") or ():
            snap = vocab.snap(row.get("name", ""), row.get("effect"))
            if snap.status == "snapped" and snap.name != row.get("name"):
                row["raw"], row["name"] = row.get("name"), snap.name
                renamed += 1
    return renamed


def ui_payload(inv: KnowhowInventory, catalog: Sequence[CatalogEntry] | None = None) -> dict:
    """What ui/game-assets/knowhow.js carries: books, produce-end books awaiting reconcile (``pending_books``),
    non-generic catalogue names and scan status incl. the 評価値 grid coverage (``scan.grid``; location hints in the
    UI only when ``grid.complete``)."""
    books = list(inv.books.values())
    cat = build_catalog(books) if catalog is None else list(catalog)
    generic = {normalize_name(e.name) for e in GENERIC_CATALOG}
    grid = clean_grid(inv.grid)
    shown, pending = [b.to_dict() for b in books], [b.to_dict() for b in inv.pending]
    canonical_rows(shown + pending)
    from agent.core import knowhow_scan as ks      # imports this module; the UI shows the same state the sell check uses
    return {"version": 1, "generated_by": "agent/core/knowhow_books.py",
            "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "scan": {"calibrated": CALIBRATED, "scanned_at": inv.scanned_at, "owned_total": inv.owned_total,
                     "pending": len(inv.pending), "grid_complete": bool(grid and grid["complete"]), "grid": grid,
                     "sync": ks.library_sync(inv)},
            "catalog": [e.to_dict() for e in cat if normalize_name(e.name) not in generic],
            "books": shown, "pending_books": pending}


def export_ui_module(inv: KnowhowInventory, path: Path = UI_MODULE_PATH, catalog: Sequence[CatalogEntry] | None = None) -> Path:
    path = _check_private(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ("// Generated by agent/core/knowhow_books.py; local, gitignored. Advice only: Maa never sells or protects.\n"
            f"export default {json.dumps(ui_payload(inv, catalog), ensure_ascii=False, indent=1)};\n")
    path.write_text(text, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ノウハウブック local library (offline; no game input)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export-ui", help="write ui/game-assets/knowhow.js from the private inventory")
    ex.add_argument("--inventory", type=Path, default=INVENTORY_PATH)
    ex.add_argument("--out", type=Path, default=UI_MODULE_PATH)
    rep = sub.add_parser("report", help="print the wanted-list analysis (profile prefs.knowhow_wanted)")
    rep.add_argument("--inventory", type=Path, default=INVENTORY_PATH)
    rep.add_argument("--profile", type=Path, default=PROFILE_PATH)
    rep.add_argument("--purpose", choices=("halo", "step"))
    args = parser.parse_args(argv)
    inv = load_inventory(args.inventory)
    if args.cmd == "export-ui":
        print(export_ui_module(inv, args.out))
        return 0
    result = analyze(inv.books.values(), load_wanted_from_profile(args.profile), purpose=args.purpose)
    json.dump({k: result[k] for k in ("wanted", "ranking", "sell_candidates", "uncertain", "outclassed")}, sys.stdout,
              ensure_ascii=False, indent=1)
    print()
    return 0


__all__ = [
    "GENERIC_CATALOG", "CatalogEntry", "WantedItem", "KnowhowRow", "KnowhowBookRecord", "KnowhowInventory",
    "normalize_name", "clean_name", "clean_wanted", "build_catalog", "load_private_catalog", "load_wanted_from_profile",
    "analyze", "default_score", "parse_picker_tiles", "parse_detail_panel", "merge_detail_pages", "classify_knowhow_screen",
    "book_from_detail", "book_from_produce_end", "save_inventory", "load_inventory", "export_ui_module", "ui_payload",
    "clean_grid", "parse_list_tiles", "record_removed",
]

if __name__ == "__main__":
    raise SystemExit(main())
