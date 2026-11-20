"""Bounded public Wiki acquisition and conservative HTML card parsing."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit, urljoin
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.robotparser import RobotFileParser

from .models import CardData, PARSER_VERSION, STATS

ATTR = {"Vocal": "Vo", "Dance": "Da", "Visual": "Vi", "メンタル": "Me"}
ROOM = {"ボーカル": "Vo", "ダンス": "Da", "ビジュアル": "Vi", "ラジオ": "radio", "トーク": "talk", "雑誌": "magazine"}


@dataclass
class Cell:
    text: str
    header: bool
    row: int
    column: int
    rowspan: int = 1
    colspan: int = 1


class CardHTML(HTMLParser):
    """Read text only, expanding table spans while preserving cell origins."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[tuple[str, list[list[Cell | None]]]] = []
        self.section = ""
        self.heading: list[str] | None = None
        self.grid: list[list[Cell | None]] | None = None
        self.row = -1
        self.cell: Cell | None = None
        self.parts: list[str] = []
        self.ignore = 0
        self.all_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in ("script", "style"):
            self.ignore += 1
        if self.ignore:
            return
        if tag in ("h2", "h3", "h4"):
            self.heading = []
        elif tag == "table":
            if self.grid is not None:
                raise ValueError("nested table unsupported")
            self.grid, self.row = [], -1
        elif tag == "tr" and self.grid is not None:
            self.row += 1
            while len(self.grid) <= self.row:
                self.grid.append([])
        elif tag in ("td", "th") and self.grid is not None:
            row = self.grid[self.row]
            col = next((i for i, c in enumerate(row) if c is None), len(row))
            rs, cs = int(values.get("rowspan", 1)), int(values.get("colspan", 1))
            if not (1 <= rs <= 100 and 1 <= cs <= 100):
                raise ValueError("invalid table span")
            self.cell = Cell("", tag == "th", self.row, col, rs, cs)
            self.parts = []
        elif tag == "br":
            self.handle_data(" ")

    def handle_data(self, data):
        if self.ignore:
            return
        self.all_text.append(data)
        if self.heading is not None:
            self.heading.append(data)
        if self.cell is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ignore = max(0, self.ignore - 1)
            return
        if self.ignore:
            return
        if tag in ("h2", "h3", "h4") and self.heading is not None:
            self.section = "".join(self.heading).strip()
            self.heading = None
        elif tag in ("td", "th") and self.cell is not None and self.grid is not None:
            c = self.cell
            c.text = re.sub(r"\s+", " ", "".join(self.parts)).strip()
            for r in range(c.row, c.row + c.rowspan):
                while len(self.grid) <= r:
                    self.grid.append([])
                while len(self.grid[r]) < c.column + c.colspan:
                    self.grid[r].append(None)
                for col in range(c.column, c.column + c.colspan):
                    if self.grid[r][col] is not None:
                        raise ValueError("overlapping table span")
                    self.grid[r][col] = c
            self.cell = None
        elif tag == "table" and self.grid is not None:
            self.tables.append((self.section, self.grid))
            self.grid = None


class MechanicsParser:
    """Primitive Japanese effects; every unimplemented suffix stays unsupported."""
    @staticmethod
    def live(text: str, skill_id: str, level: int = 1) -> dict[str, Any] | None:
        matches = re.findall(r"(Vocal|Dance|Visual)\s*([\d.]+)倍アピール", text)
        parts = {ATTR[m[0]]: float(m[1]) for m in matches}
        # "Excellent N倍アピール": appeal on the targeted judge's attribute, always Excellent (LiveSkill.excellent).
        excellent = re.findall(r"Excellent\s*([\d.]+)倍アピール", text)
        if not parts and not excellent:
            return None
        remainder = re.sub(r"(Vocal|Dance|Visual|Excellent)\s*[\d.]+倍アピール", "", text)
        result: dict[str, Any] = {"id": skill_id, "components": parts, "unlock_level": level}
        if excellent:
            result["excellent"] = float(excellent[0])
        buff = re.search(r"(Vocal|Dance|Visual)([\d.]+)%UP\s*\[(\d+)ターン\]", remainder)
        if buff:
            result.update(buff_attribute=ATTR[buff[1]], buff_amount=float(buff[2]) / 100, buff_turns=int(buff[3]))
            remainder = remainder.replace(buff[0], "", 1)
        remainder = remainder.strip(" /・")
        result["unsupported"] = [remainder] if remainder else []
        if len(matches) != len(parts) or len(excellent) > 1 or (excellent and parts):
            result["unsupported"].append("multiple appeals require an ordered interpreter: " + text)
        return result

    @staticmethod
    def support(name: str) -> tuple[str, str | None, str | None]:
        room = next((v for k, v in ROOM.items() if name.startswith(k)), None)
        attr = next((a for a in (*STATS, "SP") if name.endswith(a) or name.endswith(a + "上限")), None)
        if "滞在マスタリー" in name:
            return "stay_mastery", None, room
        if "マスタリー" in name and room:
            suffix = "cap" if "上限" in name else "stamina" if "体力" in name else "sp" if attr == "SP" else "stat"
            return "mastery_" + suffix, attr, room
        if name.startswith("ユニットマスタリー") or name.startswith("オーディションマスタリー"):
            prefix = "unit_mastery" if name.startswith("ユニット") else "audition_mastery"
            # 2026-10-25: ユニットマスタリー体力 was read as a stat mastery with no attribute (its recovery was lost).
            suffix = "_cap" if "上限" in name else "_stamina" if "体力" in name else "_sp" if attr == "SP" else "_stat"
            return prefix + suffix, attr, None
        simple = {"アイドルの絆": "bond", "はづきさんの絆": "bond", "体力サポート": "stamina_support", "おやすみブースト": "rest_support", "約束リカバー": "promise_recovery", "トラブルガード": "trouble_guard", "施設マスタリー": "facility_mastery"}
        return simple.get(name, "unknown"), None, None


def discover_card_urls(html: str, source_url: str) -> list[dict[str, Any]]:
    """Discover same-project card links and SR index partitions without requests."""
    public_url(source_url)

    class Links(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.found: dict[str, dict[str, Any]] = {}
            self.href: str | None = None
            self.label: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.href = dict(attrs).get("href")
                self.label = []

        def handle_data(self, data):
            if self.href:
                self.label.append(data)

        def handle_endtag(self, tag):
            if tag != "a" or not self.href:
                return
            url = urljoin(source_url, self.href)
            try:
                public_url(url)
            except ValueError:
                self.href = None
                return
            title = unquote(urlsplit(url).path.removeprefix("/shinycolors/"))
            is_card = bool(re.match(r"^[【〖].+[】〗].+", title)) and "/" not in title
            is_partition = title.startswith("サポートアイドル一覧/")
            if is_card or is_partition:
                self.found[url] = dict(url=url, page_title=title, label="".join(self.label).strip(),
                    link_kind="card" if is_card else "index_partition", source_url=source_url)
            self.href = None

    parsed = Links()
    parsed.feed(html)
    return sorted(parsed.found.values(), key=lambda value: value["url"])


class CardPageParser:
    def parse(self, html: str, *, source_url: str, card_id: str | None = None,
              kind: str = "S", rarity: str | None = None, fetched_at: str | None = None) -> CardData:
        parsed = CardHTML()
        errors: list[str] = []
        try:
            parsed.feed(html)
        except (ValueError, IndexError) as exc:
            errors.append("html: " + str(exc))
        name = unquote(urlsplit(source_url).path.rsplit("/", 1)[-1])
        result: dict[str, Any] = dict(card_id=card_id or name, name=name, idol="UNKNOWN", kind=kind,
            status="PARTIAL", levels={}, max_levels={}, owned_live_skills=[], support_skills=[],
            panels=[], events=[], provenance={}, source_url=source_url, rarity=rarity,
            unknown_fields=[], unsupported=[])
        provenance = dict(url=source_url, fetched_at=fetched_at, parser_version=PARSER_VERSION,
                          content_sha256=sha256(html.encode()).hexdigest())
        panel_tables = [grid for section, grid in parsed.tables
                        if section == "スキルパネル" and grid and grid[0]
                        and grid[0][0] and grid[0][0].text == "SP"]
        panel_table = 0
        te_conditions: list[tuple[str, str]] = []
        for section, grid in parsed.tables:
            if not grid:
                continue
            texts = [[c.text if c else "" for c in row] for row in grid]
            header = texts[0]
            # Card-specific TE evidence must come from its condition table, not
            # unrelated headings, comments, links or generic WING explanations.
            if any(header) and all(re.fullmatch(r"True\s*End", t) for t in header if t):
                for row_index, row in enumerate(texts[1:], 1):
                    if len(row) == 2 and row[0] == "条件" and row[1]:
                        te_conditions.append((row[1], f"table:{section}/row:{row_index}/条件"))
            if not section:
                for row in texts:
                    if row[0] == "アイドル" and len(row) > 1:
                        result["idol"] = row[1]
                    if row[0] == "実装日" and len(row) > 1:
                        result["implementation_date"] = row[1]
            elif section == "ステータス" and header[:4] == ["Lv", "Vo", "Da", "Vi"]:
                for row in texts[1:]:
                    lv = re.match(r"^(\d+)", row[0])
                    if not lv or len(row) != 5:
                        errors.append("stats.row")
                        continue
                    level = int(lv[1])
                    result["levels"][level] = {a: int(n) if n.isdigit() else None for a, n in zip(STATS, row[1:])}
                    if any(n is None for n in result["levels"][level].values()):
                        errors.append(f"levels.{level}")
                    lb = re.search(r"☆([0-4])", row[0])
                    if lb:
                        result["max_levels"][int(lb[1])] = level
            elif section == "ライブスキル" and header[:3] == ["スキル名", "効果", "取得Lv"]:
                for row in texts[1:]:
                    if len(row) != 3 or not (row[2].isdigit() or row[2] == "初期"):
                        errors.append("owned_live.row")
                        continue
                    skill = MechanicsParser.live(row[1], row[0], 1 if row[2] == "初期" else int(row[2]))
                    if skill:
                        result["owned_live_skills"].append(skill)
                    else:
                        errors.append("owned_live." + row[0])
            elif section == "サポートスキル" and header[:2] == ["スキル名", "スキル効果"]:
                levels = texts[1][2:] if len(texts) > 1 else []
                if not levels or any(not (v.isdigit() or v == "最大") for v in levels):
                    errors.append("support.level_header")
                    continue
                for row in texts[2:]:
                    if len(row) != len(texts[1]) or not row[0]:
                        errors.append("support.row")
                        continue
                    kind_, attr, room = MechanicsParser.support(row[0])
                    sparse = {int(lv): float(n) for lv, n in zip(levels, row[2:]) if lv.isdigit() and n.isdigit()}
                    if any(n and not n.isdigit() for lv, n in zip(levels, row[2:]) if lv.isdigit()):
                        errors.append("support." + row[0])
                    result["support_skills"].append(dict(id=row[0], kind=kind_, levels=sparse,
                        attribute=attr, room=room, unsupported=[row[1]] if kind_ in ("unknown", "stamina_support", "rest_support", "trouble_guard", "facility_mastery", "stay_mastery", "promise_recovery") else []))
            elif section == "サポートイベント" and header[:4] == ["イベント名", "Vo", "Da", "Vi"]:
                for row in texts[1:]:
                    if len(row) != 6 or any(n and not re.fullmatch(r"\+?\d+", n) for n in row[1:]):
                        errors.append("events.row")
                        continue
                    result["events"].append(dict(id=row[0], stats={a: int(n or 0) for a, n in zip((*STATS, "SP"), row[1:])}))
            elif section == "スキルパネル" and header[0] == "SP":
                panel_table += 1
                for r, row in enumerate(grid[1:], 1):
                    for column, c in enumerate(row[1:], 1):
                        if c is None or c.row != r or c.column != column or not c.header or not c.text:
                            continue
                        sp = row[0].text if row[0] else ""
                        body = grid[r + 1][c.column] if r + 1 < len(grid) and c.column < len(grid[r + 1]) else None
                        if not sp.isdigit() or body is None or body.row != r + 1:
                            errors.append("panel.row")
                            continue
                        pid = (f"panel-table-{panel_table}-{r}-{c.column}" if len(panel_tables) > 1
                               else f"panel-{r}-{c.column}")
                        lb = re.search(r"☆([0-4])", c.text)
                        event = re.search(r"E(\d+)", c.text)
                        live = MechanicsParser.live(body.text, c.text)
                        cap = re.search(r"上限\s*\+(\d+)", body.text)
                        attrs = [a for label, a in ATTR.items() if label in body.text]
                        effects: dict[str, Any] = {}
                        unsupported: list[str] = []
                        pkind = "unknown"
                        if live:
                            pkind, effects = "live", live
                        elif cap and attrs:
                            pkind, effects = "cap", {a: int(cap[1]) for a in attrs}
                        elif "[条件:" in body.text:
                            pkind = "passive"
                            condition = re.search(r"\[条件:(.*?)\]", body.text)
                            chance = re.search(r"\[確率:(\d+)%\]", body.text)
                            count = re.search(r"\[最大:(\d+)回\]", body.text)
                            buffs = {ATTR[a]: float(n) / 100 for a, n in re.findall(r"(Vocal|Dance|Visual|メンタル)\s*(\d+)%UP", c.text)}
                            effects = dict(buffs=buffs, condition=condition[1] if condition else None,
                                probability=float(chance[1]) / 100 if chance else None,
                                max_activations=int(count[1]) if count else None)
                            unsupported = ["passive condition requires battle interpreter: " + c.text + " / " + body.text]
                        else:
                            unsupported = [c.text + " / " + body.text]
                        result["panels"].append(dict(id=pid, kind=pkind, sp_cost=int(sp), required_lb=int(lb[1]) if lb else 0,
                            prerequisites=[], prerequisites_known=False, effects=effects,
                            source_layout=dict(table=panel_table, row=r, column=c.column,
                                               rowspan=c.rowspan, colspan=c.colspan),
                            event="E" + event[1] if event else None, unsupported=unsupported))
        if len(panel_tables) > 1:
            result["unknown_fields"].append("panel_variant_selection")
        if result["idol"] == "UNKNOWN":
            errors.append("idol")
        if kind == "S":
            for field in ("levels", "max_levels", "owned_live_skills", "support_skills", "events", "panels"):
                if not result[field]:
                    errors.append(field)
        else:
            result["unknown_fields"].extend(["initial_stats", "produce_events", "memory_appeal"])
            conditions = {text for text, _ in te_conditions}
            result["te_eligible"] = None
            if len(conditions) == 1:
                condition = next(iter(conditions))
                result["te_condition"] = condition
                # Only the currently modelled rank-A condition is usable. Keep
                # fans>=500000 + WING and all other conditions explicit but unknown
                # until downstream route/target consumers bind the actual goal.
                result["te_eligible"] = (True if re.fullmatch(
                    r"アイドルランクA達成\s+かつ\s+W\.I\.N\.G\.優勝", condition) else None)
                for field in ("te_condition", "te_eligible"):
                    result["provenance"][field] = {**provenance, "locator": te_conditions[0][1],
                        "status": "PARSED" if field == "te_condition" or result["te_eligible"] else "UNKNOWN",
                        "condition": condition}
            elif len(conditions) > 1:
                result["unknown_fields"].append("te_condition.conflicting")
            if result["te_eligible"] is None:
                result["unknown_fields"].append("te_eligible")
        result["unknown_fields"].extend(errors)
        result["status"] = "PARSE_ERROR" if errors else "PARTIAL"
        for field in ("idol", "levels", "max_levels", "owned_live_skills", "support_skills", "panels", "events"):
            result["provenance"][field] = {**provenance, "locator": field, "status": "PARSE_ERROR" if any(k == field or k.startswith(field + ".") for k in errors) else "PARSED"}
        return CardData.from_dict(result)


def parse_card_page(html: str, *, source_url: str, card_id: str | None = None,
                    kind: str | None = None, rarity: str | None = None,
                    fetched_at: str | None = None) -> CardData:
    """Dispatch offline public HTML by host; acquisition policy stays separate."""
    host = urlsplit(source_url).hostname
    if host == "shinycolors.wiki":
        from .english_wiki import EnglishCardPageParser
        return EnglishCardPageParser().parse(html, source_url=source_url, card_id=card_id,
                                             kind=kind, rarity=rarity, fetched_at=fetched_at)
    if host != "wikiwiki.jp":
        raise ValueError("unsupported public card-page host")
    public_url(source_url)
    if kind not in (None, "P", "S"):
        raise ValueError("card kind must be P/S")
    # Infer only from actual card-section headings, never rarity or the page title.
    headings = CardHTML()
    try:
        headings.feed(html)
    except (ValueError, IndexError):
        pass  # Preserve the existing parser's partial/error record when kind is explicit.
    sections = {section for section, _ in headings.tables}
    # Headings without a table still provide type evidence (e.g. an incomplete P page).
    sections.add(headings.section)
    p_page = bool(sections & {"思い出アピール", "プロデュースイベント"})
    s_page = bool(sections & {"サポートスキル", "サポートイベント"})
    if p_page and s_page:
        raise ValueError("ambiguous P/S card sections")
    inferred = "P" if p_page else "S" if s_page else None
    if kind is not None and inferred is not None and kind != inferred:
        raise ValueError("card kind conflicts with page sections")
    if kind is None and inferred is None:
        raise ValueError("card kind is unknown; explicitly provide P/S")
    return CardPageParser().parse(html, source_url=source_url, card_id=card_id,
                                  kind=kind or inferred, rarity=rarity, fetched_at=fetched_at)


def public_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "wikiwiki.jp" or parsed.port is not None or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("only plain HTTPS public Wiki page URLs are allowed")
    if not (parsed.path.startswith("/shinycolors/") or parsed.path == "/robots.txt"):
        raise ValueError("URL is outside shinycolors Wiki")
    decoded = unquote(parsed.path)
    if "::" in decoded or any(part in (".", "..") for part in decoded.split("/")) or re.search(r"\.(jpg|jpeg|png|webp|gif|svg)$", decoded, re.I):
        raise ValueError("commands/assets/path traversal refused")
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect requires explicit source review")


class SnapshotManager:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.last_request = 0.0
        self.min_interval = 1.0
        self.stopped = False
        self.robots: RobotFileParser | None = None
        self.opener = build_opener(NoRedirect())  # No cookie processor.

    def _read(self, url: str) -> bytes:
        public_url(url)
        if self.stopped:
            raise RuntimeError("crawler stopped after HTTP failure; no automatic retry")
        time.sleep(max(0, self.min_interval - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        try:
            with self.opener.open(Request(url, headers={"User-Agent": "MaaShinyColorsResearch/0.1"}), timeout=30) as response:
                if "text/" not in response.headers.get("Content-Type", ""):
                    raise ValueError("non-text response refused")
                body = response.read(4_000_001)
                if len(body) > 4_000_000:
                    raise ValueError("page size exceeds bound")
                return body
        except HTTPError:
            self.stopped = True
            raise

    def fetch(self, url: str, *, allow_network: bool = False) -> dict[str, Any]:
        public_url(url)
        if not allow_network:
            raise ValueError("network disabled; explicitly select fetch mode")
        private_root = Path(__file__).resolve().parents[3] / "dev_assets/private"
        if not self.cache_dir.resolve().is_relative_to(private_root.resolve()):
            raise ValueError("raw Wiki cache must stay under dev_assets/private")
        if self.robots is None:
            robots = RobotFileParser()
            robots.parse(self._read("https://wikiwiki.jp/robots.txt").decode("utf-8").splitlines())
            self.robots = robots
            delay = robots.crawl_delay("MaaShinyColorsResearch")
            rate = robots.request_rate("MaaShinyColorsResearch")
            self.min_interval = max(1.0, float(delay or 0), rate.seconds / rate.requests if rate else 0)
            if self.min_interval > 60:
                raise ValueError("robots delay exceeds bounded fetch session")
        if not self.robots.can_fetch("MaaShinyColorsResearch", url):
            raise ValueError("robots.txt does not allow this page")
        raw = self._read(url)
        html = raw.decode("utf-8")
        digest = sha256(raw).hexdigest()
        key = sha256(url.encode()).hexdigest()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / (key + ".html")
        path.write_bytes(raw)
        date = re.search(r"Last-modified:\s*([^<]+)", html)
        metadata = dict(url=url, fetched_at=datetime.now(timezone.utc).isoformat(), content_sha256=digest,
                        page_last_modified=date[1].strip() if date else None, file=path.name,
                        parser_version=PARSER_VERSION)
        (self.cache_dir / (key + ".json")).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return metadata

    def read_cached(self, url: str) -> tuple[str, dict[str, Any]]:
        public_url(url)
        key = sha256(url.encode()).hexdigest()
        metadata = json.loads((self.cache_dir / (key + ".json")).read_text(encoding="utf-8"))
        if metadata.get("url") != url or metadata.get("file") != key + ".html":
            raise ValueError("cache metadata identity mismatch")
        raw = (self.cache_dir / (key + ".html")).read_bytes()
        if sha256(raw).hexdigest() != metadata.get("content_sha256"):
            raise ValueError("cache checksum mismatch")
        return raw.decode("utf-8"), metadata


WikiCrawler = SnapshotManager


class CardDatabase:
    def __init__(self):
        self.cards: dict[str, CardData] = {}

    def add(self, card: CardData) -> None:
        self.cards[card.card_id] = card

    def merge(self, path: Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        for value in payload["cards"]:
            self.add(CardData.from_dict(value))

    def override(self, card_id: str, field: str, value: Any, *, source_url: str,
                 reason: str, reviewed_at: str) -> CardData:
        if not all((source_url, reason.strip(), reviewed_at.strip())) or field not in CardData.__dataclass_fields__:
            raise ValueError("override requires known field, source, reason and review date")
        if field in ("card_id", "provenance", "status", "unknown_fields"):
            raise ValueError("identity/status cannot be silently overridden")
        original = asdict(self.cards[card_id])
        previous = original[field]
        original[field] = value
        original["provenance"][field] = dict(url=source_url, reason=reason, reviewed_at=reviewed_at,
            previous_value=previous, status="MANUAL_OVERRIDE", parser_version=PARSER_VERSION)
        original["unknown_fields"] = [k for k in original["unknown_fields"] if not (k == field or k.startswith(field + "."))]
        original["status"] = "PARSE_ERROR" if original["unknown_fields"] else "PARTIAL"
        card = CardData.from_dict(original)
        self.add(card)
        return card

    def export(self, path: Path) -> None:
        Path(path).write_text(json.dumps({"parser_version": PARSER_VERSION, "cards": [asdict(self.cards[k]) for k in sorted(self.cards)]}, ensure_ascii=False, indent=2), encoding="utf-8")
