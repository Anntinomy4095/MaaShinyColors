"""Keep / lock / sell decision for newly obtained ノウハウブック (pure, offline).

User decision: after any Produce (W.I.N.G., official Auto, later S.T.E.P. / say "Halo") Maa decides for each new book
whether to sell it, lock it (保護) or leave it. Selling happens only after the user turns 「自动卖书」 on; the strategy
(default 「只卖没用的书」) is chosen under it. Locking has its own switch. Both start off and click nothing until the
library-side executor has passed offline replay and a supervised run.

Row classes of a new book:
- *need*: the name matches a wanted item (prefs.knowhow_wanted) at or above its min Lv, or a 头ノウハウ of ++ or better.
  ``have`` = physical books elsewhere with the same row name at the same or a higher Lv (head: same type, tier >=).
- *low*: low-value or non-transferable entries (NON_TRANSFERABLE of ui/components/knowhow-rules.js)
  and heads below ++; they never keep a book.
- *other*: everything else. Such a row keeps the book unless the library already holds the same name at >= its Lv
  (judged against the inventory: a Lv5 Leader適正◯ the library lacks is not "useless" even if no Target wants it).
Counts come from complete readings only, so they can only undercount (fewer sales, more locks); OCR-garbled names never
match, which also keeps the book.

Order per book: already 保護 / incomplete reading / no wanted list -> keep; a need with fewer than ``LOCK_BELOW``
copies -> lock (when locking is on); then the sell strategy (when selling is on). A マーク-ed book is never sold.
Anything judged against the library (other rows, copy counts) needs ``counts_ok``: the library's physical count equals
the 所持数 read now (no unrecorded sale or addition). Without it only books made of low rows are sellable.
New books are planned in order and every kept or locked one joins the library for the next.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Iterable, Mapping, Sequence

from agent.core.knowhow_books import KnowhowBookRecord, KnowhowInventory, KnowhowRow, WantedItem, clean_wanted, normalize_name

STRATEGIES = ("useless_only", "keep3", "keep1")
STRATEGY_COPIES = {"useless_only": None, "keep3": 3, "keep1": 1}   # sell when every need has this many copies
LOCK_BELOW = 3                                       # lock when a need has fewer copies elsewhere
HEAD_KEEP_TIER = 2                                   # VocalUP++ and better heads are needs
SELL_MAX = 20                                        # 売却 takes at most 20 books at once (sample #118)
# Head rule of ui/components/knowhow-rules.js HEAD: {属性}UP / {属性}上限UP + 0–3 「+」. G.R.A.D.'s
# 「Vocal上限+」 is an ability, not a head.
_HEAD = re.compile(r"((?:Vocal|Dance|Visual|メンタル)(?:UP|上限UP))(\+{0,3})")
# (ファンの声援系, ファースト/ファイナルライブ, 目標達成) + NON_TRANSFERABLE (knowhow-rules.js), matched
# without the trailing +/++/+++ tier. 目標.成: the OCR also reads 達 as 遑.
_LOW = re.compile(r"^ファンの声援|^ファーストライブ|^ファイナルライブ|目標.成|^WING優勝|^WING準決勝|トワコレ|トヴコレ|^ファン感謝祭"
                  r"|^ファン感MVP|^G\.?R\.?A\.?D\.?(予選|敗者復活|決勝)|^G\.R\.A\.D\.\((Vo|Da|Vi|Le|Ce)\)$"
                  r"|^LandingPoint\((Vo|Da|Vi|Me)\)$|^LandingPoint大成功\(アビリティ\)|^W\.I\.N\.G\.\("
                  r"|^(なないろ歌合戦|週刊プリズムメロディーズ|TOPSONGS|七彩メモリーズ)")
_TIER = re.compile(r"\+{1,3}(?=(\([^)]*\))?$)")

STRATEGY_TEXT = {"useless_only": "只卖没用的书", "keep3": "库存够 3 本就卖", "keep1": "库存有 1 本就卖"}
REASON_TEXT = {
    "protected": "已锁定", "incomplete": "详情未读完", "no_wanted": "未设置想要的条目", "marked": "有マーク",
    "scarce": "库存少", "useless": "没有有用的条目", "enough": "库存已够", "needed": "有想要的条目",
    "near": "想要的条目 Lv 未达标", "below_min": "想要的条目 Lv 未达标", "unique": "库存没有", "sell_cap": "本次卖出已达上限",
    "unsynced": "书库与所持数不一致",
}


@dataclasses.dataclass(frozen=True)
class KeepSettings:
    sell: bool = False
    strategy: str = "useless_only"
    lock: bool = False

    def __post_init__(self) -> None:
        if self.strategy not in STRATEGIES:
            raise ValueError(f"strategy 必须为 {STRATEGIES} 之一")


@dataclasses.dataclass(frozen=True)
class Need:
    name: str                 # row name in the new book
    level: int                # its Lv (head: the + tier)
    want: str                 # matched wanted item, or "head"
    have: int                 # physical copies elsewhere at >= level
    fills: bool = False       # the library had no copy of the wanted item at its min Lv (a better ring / tier counts)


@dataclasses.dataclass(frozen=True)
class Verdict:
    book: str                 # fingerprint
    title: str | None
    action: str               # sell | lock | keep
    reason: str               # REASON_TEXT key
    needs: tuple[Need, ...] = ()
    unique: tuple[str, ...] = ()   # other rows the library lacks (「Leader適正◯ Lv5」)

    @property
    def text(self) -> str:
        if self.reason in ("scarce", "enough") and self.needs:
            pick = self.needs[0]
            lv = f"+{pick.level}" if head_tier(pick.name) else f"Lv{pick.level}"
            return f"{REASON_TEXT[self.reason]}：{pick.name} {lv} 另有 {pick.have} 本"
        if self.reason == "unique" and self.unique:
            return f"{REASON_TEXT['unique']}：{'、'.join(self.unique[:2])}{' 等' if len(self.unique) > 2 else ''}"
        return REASON_TEXT[self.reason]

    def to_dict(self) -> dict:
        return {"book": self.book, "title": self.title, "action": self.action, "reason": self.reason, "text": self.text,
                "needs": [dataclasses.asdict(n) for n in self.needs], "unique": list(self.unique)}


def head_tier(name: str) -> tuple[str, int] | None:
    """「VocalUP+++」 -> ("VocalUP", 3), 「メンタル上限UP+」 -> ("メンタル上限UP", 1); not a head -> None."""
    m = _HEAD.fullmatch(normalize_name(name))
    return (m.group(1), len(m.group(2))) if m else None


def is_low_value(name: str) -> bool:
    """Low-value / non-transferable entry (NON_TRANSFERABLE); tiers ignored."""
    return bool(_LOW.search(_TIER.sub("", normalize_name(name))))


class _Pool:
    """Physical copies of complete books: per row name {Lv: books} and per head type {tier: books}."""

    def __init__(self, books: Iterable[tuple[KnowhowBookRecord, int]]):
        self.names: dict[str, dict[int, int]] = {}
        self.heads: dict[str, dict[int, int]] = {}
        for book, count in books:
            self.add(book, count)

    def add(self, book: KnowhowBookRecord, count: int = 1) -> None:
        if not book.complete or count <= 0:
            return
        for name, level in book.levels().items():          # one count per name per book (its highest Lv)
            levels = self.names.setdefault(name, {})
            levels[level] = levels.get(level, 0) + count
        for head in {h for h in (head_tier(r.name) for r in book.rows) if h}:
            tiers = self.heads.setdefault(head[0], {})
            tiers[head[1]] = tiers.get(head[1], 0) + count

    def copies(self, row_name: str, level: int) -> int:
        head = head_tier(row_name)
        table = self.heads.get(head[0], {}) if head else self.names.get(row_name, {})
        return sum(n for lv, n in table.items() if lv >= level)

    def covers(self, item: WantedItem, level: int) -> bool:
        """Some physical copy holds the wanted item at >= level: a row name it matches, or a better sibling of one
        (◎ for a wanted ◯, ++ for a wanted +; W.I.N.G. 连跑 stops on a new book that fills a missing item)."""
        return any(lv >= level and n > 0 for name, levels in self.names.items()
                   if any(item.matches_name(v) for v in lesser_names(name)) for lv, n in levels.items())


def lesser_names(name: str) -> list[str]:
    """The name and the weaker siblings it supersedes: 「Center適正◎」 -> + 「Center適正◯」; 「思い出(ノウハウ)++」 -> + 「…+」."""
    out = [name]
    if name.endswith("◎"):
        out.append(name[:-1] + "◯")
    m = re.search(r"(\++)(\([^)]*\))?$", name)
    if m and not head_tier(name):
        for k in range(len(m.group(1)) - 1, 0, -1):
            out.append(name[:m.start(1)] + "+" * k + (m.group(2) or ""))
    return out


def _rows(book: KnowhowBookRecord, wanted: Sequence[WantedItem], pool: _Pool) -> tuple[list[Need], bool, list[KnowhowRow]]:
    """(needs: one per row name at its highest Lv, near: a wanted name below its min Lv, other rows)."""
    best: dict[str, Need] = {}
    near = False
    others: list[KnowhowRow] = []
    for row in book.rows:
        hits = [item for item in wanted if item.matches_name(row.name)]
        met = [item for item in hits if row.level is not None and row.level >= item.min_level]
        head = head_tier(row.name)
        if met and not head:
            cur = best.get(row.name)
            if cur is None or row.level > cur.level:
                fills = not any(pool.covers(item, item.min_level) for item in met)
                best[row.name] = Need(row.name, row.level, met[0].name, pool.copies(row.name, row.level), fills)
            continue
        near = near or bool(hits)
        if head:
            if head[1] >= HEAD_KEEP_TIER or met:     # a wanted head counts by its type and tier too
                best.setdefault(row.name, Need(row.name, head[1], met[0].name if met else "head",
                                               pool.copies(row.name, head[1])))
        elif not is_low_value(row.name):
            others.append(row)
    return list(best.values()), near, others


def decide(book: KnowhowBookRecord, wanted: Sequence[WantedItem], pool: _Pool, settings: KeepSettings, *,
           counts_ok: bool) -> Verdict:
    """One book against the rest of the library (``pool`` must not contain this physical copy)."""
    fp, title = book.fingerprint, book.title
    if book.protected:
        return Verdict(fp, title, "keep", "protected")
    if not book.complete:
        return Verdict(fp, title, "keep", "incomplete")
    if not wanted:
        return Verdict(fp, title, "keep", "no_wanted")
    needs, near, others = _rows(book, wanted, pool)
    needs.sort(key=lambda n: (n.have, n.name))
    scarce = any(n.have < LOCK_BELOW for n in needs)
    if settings.lock and scarce:
        return Verdict(fp, title, "lock", "scarce", tuple(needs))
    # The reason is the same whether or not selling is on, so a summary can show it as advice.
    unique = tuple(f"{r.name} Lv{r.level}" for r in others if not counts_ok or pool.copies(r.name, r.level or 0) < 1)
    copies = STRATEGY_COPIES[settings.strategy] if counts_ok else None
    if not needs and not near and not unique:
        sellable, reason = True, "useless"
    elif copies is not None and not unique and all(n.have >= copies for n in needs):
        sellable, reason = True, "enough" if needs else "below_min"
    else:
        sellable = False
        reason = "scarce" if scarce else "needed" if needs else "near" if near else "unique"
    if sellable and settings.sell:
        return Verdict(fp, title, "keep" if book.marked else "sell", "marked" if book.marked else reason, tuple(needs))
    return Verdict(fp, title, "keep", reason, tuple(needs), unique if reason == "unique" else ())


def physical_count(inventory: KnowhowInventory) -> int:
    return sum(b.count for b in inventory.books.values())


def plan_new_books(new_books: Sequence[KnowhowBookRecord], inventory: KnowhowInventory,
                   wanted: Iterable[WantedItem | Mapping | str], settings: KeepSettings, *,
                   owned_now: int | None = None, in_inventory: bool = True, sync: Mapping | None = None) -> dict:
    """Verdicts for the books a Produce just added, in order.

    ``in_inventory``: the newest-books scan already upserted them (one physical copy each is then taken out of the
    library before comparing). ``owned_now``: 所持数 read on the 一覧 page now; rules judged against the library apply
    only when it equals the library's physical count (``counts_ok``). ``sync``: ``knowhow_scan.library_sync`` after the
    Produce-end top-up; it supplies ``owned_now``, and nothing is sold unless its state is ``up_to_date`` (locking
    still applies).
    """
    synced = True
    if sync is not None:
        owned_now, synced = sync.get("owned_now"), sync.get("state") == "up_to_date"
    wanted = clean_wanted([w.to_dict() if isinstance(w, WantedItem) else w for w in wanted])
    taken: dict[str, int] = {}
    if in_inventory:
        for book in new_books:
            taken[book.fingerprint] = taken.get(book.fingerprint, 0) + 1
    pool = _Pool((b, b.count - taken.get(fp, 0)) for fp, b in inventory.books.items())
    total = physical_count(inventory) + (0 if in_inventory else len(new_books))
    counts_ok = synced and isinstance(owned_now, int) and owned_now == total and bool(inventory.books)
    verdicts: list[Verdict] = []
    sold = 0
    for book in new_books:
        verdict = decide(book, wanted, pool, settings, counts_ok=counts_ok)
        if verdict.action == "sell" and not synced:
            verdict = dataclasses.replace(verdict, action="keep", reason="unsynced")
        if verdict.action == "sell" and sold >= SELL_MAX:
            verdict = dataclasses.replace(verdict, action="keep", reason="sell_cap")
        if verdict.action == "sell":
            sold += 1
        else:
            pool.add(book)
        verdicts.append(verdict)
    return {"verdicts": [v.to_dict() for v in verdicts], "counts_ok": counts_ok, "synced": synced,
            "strategy": settings.strategy,
            "sell": sum(v.action == "sell" for v in verdicts), "lock": sum(v.action == "lock" for v in verdicts),
            "keep": sum(v.action == "keep" for v in verdicts)}


def target_hits(plan: Mapping | None) -> list[dict]:
    """New books that fill a wanted item the library lacked (``Need.fills``): [{book, title, entries}] in plan order.
    W.I.N.G. 连跑 stops after the run that brought one."""
    hits = []
    for verdict in (plan or {}).get("verdicts") or []:
        entries = [f"{n['name']} Lv{n['level']}" for n in verdict.get("needs") or [] if n.get("fills") and n.get("want") != "head"]
        if entries:
            hits.append({"book": verdict.get("book"), "title": verdict.get("title"), "entries": entries})
    return hits


__all__ = ["STRATEGIES", "STRATEGY_TEXT", "REASON_TEXT", "KeepSettings", "Need", "Verdict", "head_tier", "is_low_value",
           "decide", "plan_new_books", "physical_count", "target_hits", "lesser_names", "LOCK_BELOW", "SELL_MAX"]
