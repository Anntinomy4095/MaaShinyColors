"""Offline, provenance-preserving adapter for English public card-page HTML.

No acquisition or account access. Layout adjacency is never a panel prerequisite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from html.parser import HTMLParser
import re
from typing import Any
from urllib.parse import unquote, urlsplit

from .models import CardData, STATS

PARSER_VERSION = "true-end-english-card-v1"
ATTR = {"Vocal": "Vo", "Dance": "Da", "Visual": "Vi", "Mental": "Me"}
ROOM = {"Vocal": "Vo", "Dance": "Da", "Visual": "Vi", "Radio": "radio",
        "Talk": "talk", "Magazine": "magazine"}


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    line: int
    children: list[Any] = field(default_factory=list)

    def text(self, *, images: bool = False) -> str:
        if self.tag in ("script", "style"):
            return ""
        if self.tag == "img":
            if not images:
                return ""
            label = self.attrs.get("alt") or self.attrs.get("title") or unquote(self.attrs.get("src", "").rsplit("/", 1)[-1])
            return f"[image:{label}]" if label else "[image:unlabelled]"
        return re.sub(r"\s+", " ", " ".join(
            child.text(images=images) if isinstance(child, _Node) else child
            for child in self.children)).strip()

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()


class _DOM(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, 1)
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {key: value or "" for key, value in attrs}, self.getpos()[0])
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


@dataclass
class _Cell:
    node: _Node
    table: int
    row: int
    column: int
    rowspan: int
    colspan: int

    @property
    def text(self):
        return self.node.text()

    def source(self):
        return dict(table=self.table, row=self.row, column=self.column,
                    rowspan=self.rowspan, colspan=self.colspan, line=self.node.line,
                    dom_id=self.node.attrs.get("id"), attributes=self.node.attrs,
                    text=self.node.text(images=True),
                    images=[n.attrs for n in self.node.walk() if n.tag == "img"])


def _table_grid(table: _Node, index: int) -> list[list[_Cell | None]]:
    def rows(node):
        for child in node.children:
            if not isinstance(child, _Node) or child.tag == "table":
                continue
            if child.tag == "tr":
                yield child
            else:
                yield from rows(child)
    grid: list[list[_Cell | None]] = []
    for r, row in enumerate(rows(table)):
        while len(grid) <= r:
            grid.append([])
        col = 0
        for node in row.children:
            if not isinstance(node, _Node) or node.tag not in ("td", "th"):
                continue
            while col < len(grid[r]) and grid[r][col] is not None:
                col += 1
            rs, cs = int(node.attrs.get("rowspan", 1)), int(node.attrs.get("colspan", 1))
            if not (1 <= rs <= 100 and 1 <= cs <= 100) or r + rs > 1000 or col + cs > 1000:
                raise ValueError("invalid table span")
            cell = _Cell(node, index, r, col, rs, cs)
            for y in range(r, r + rs):
                while len(grid) <= y:
                    grid.append([])
                while len(grid[y]) < col + cs:
                    grid[y].append(None)
                for x in range(col, col + cs):
                    if grid[y][x] is not None:
                        raise ValueError("overlapping table span")
                    grid[y][x] = cell
            col += cs
    return grid


def _numeric(text: str) -> int | None:
    return int(text.replace(",", "").lstrip("+")) if re.fullmatch(r"\+?\d[\d,]*", text) else None


class EnglishMechanicsParser:
    @staticmethod
    def live(text: str, skill_id: str, level: int = 1) -> dict[str, Any] | None:
        pattern = r"(Vocal|Dance|Visual)\s+([\d.]+)\s*[×x]\s*Appeal"
        matches = list(re.finditer(pattern, text, re.I))
        if not matches:
            return None
        # Link/change/multiple hits cannot be summed into an unconditional multiplier.
        first = matches[0]
        value = dict(id=skill_id, components={ATTR[first[1].title()]: float(first[2])},
                     unlock_level=level, unsupported=[])
        remainder = text[:first.start()] + text[first.end():]
        conditional = bool(re.search(r"per successful|Until|Change:|Link|If:", text, re.I))
        buff = re.search(r"(Vocal|Dance|Visual)\s+([\d.]+)%\s*UP\s*\[(\d+) turns?\]", remainder, re.I)
        if buff and not conditional:
            value.update(buff_attribute=ATTR[buff[1].title()], buff_amount=float(buff[2]) / 100,
                         buff_turns=int(buff[3]))
            remainder = remainder[:buff.start()] + remainder[buff.end():]
        remainder = remainder.strip(" /;")
        if remainder:
            value["unsupported"].append(remainder)
        if len(matches) > 1:
            value["unsupported"].append("ordered/conditional appeals: " + text)
        return value

    @staticmethod
    def support(name: str, description: str) -> tuple[str, str | None, str | None, list[str]]:
        if name == "Idol's Bond":
            return "bond", None, None, []
        match = re.fullmatch(r"(Vocal|Dance|Visual|Radio|Talk|Magazine|Unit|Audition) Mastery (Vo|Da|Vi|Me|SP|Sta|VoLimitUp|DaLimitUp|ViLimitUp|MeLimitUp)", name)
        if not match:
            return "unknown", None, None, [description or name]
        prefix, suffix = match.groups()
        room = ROOM.get(prefix)
        attr = suffix.removesuffix("LimitUp") if suffix != "Sta" else None
        effect = "cap" if suffix.endswith("LimitUp") else "stamina" if suffix == "Sta" else "sp" if suffix == "SP" else "stat"
        kind = ("unit_mastery_" if prefix == "Unit" else "audition_mastery_" if prefix == "Audition" else "mastery_") + effect
        # Check the actual description, including unit/room and coefficient. Names alone do not prove a formula.
        target = next((label for label, stat in ATTR.items() if stat == attr), attr)
        formula = bool(re.search(r"LV\s*[×x]\s*3.*stamina", description, re.I)) if suffix == "Sta" else bool(
            re.search(r"LV\s*[×x]\s*1\].*\b" + re.escape(target or "") + (r"\s+Limit" if effect == "cap" else "") + r"\s+gain", description, re.I))
        placement = ("unit member" in description.lower() if prefix == "Unit" else
                     "audition" in description.lower() if prefix == "Audition" else
                     f"{prefix.lower()} room" in description.lower())
        unsupported = [] if formula and placement else ["unverified/conflicting support description: " + (description or "missing")]
        return kind, attr, room, unsupported

    @staticmethod
    def panel(title: str, text: str, panel_id: str) -> tuple[str, dict[str, Any], list[str]]:
        cap = re.fullmatch(r"((?:Vocal|Dance|Visual|Mental)(?:\s*&\s*(?:Vocal|Dance|Visual|Mental))*)\s+Limit\s*\+(\d+)", text)
        if cap:
            return "cap", {ATTR[a]: int(cap[2]) for a in re.findall(r"Vocal|Dance|Visual|Mental", cap[1])}, []
        live = EnglishMechanicsParser.live(text, panel_id)
        if live:
            return "live", live, live["unsupported"]
        # The existing battle contract represents exactly one attribute per passive.
        title = re.sub(r"\((?:[0-4]☆|☆[0-4]|E\d*)\)", "", title).strip()
        buff = re.fullmatch(r"(Vocal|Dance|Visual)\s+([\d.]+)%\s*UP", title)
        condition = re.fullmatch(r"If:\s*(.*?)\s*Chance:\s*([\d.]+)%\s*Maximum\s+(\d+)[×x]", text)
        if buff:
            effects: dict[str, Any] = dict(id=panel_id, attribute=ATTR[buff[1]], buff=float(buff[2]) / 100)
            if condition:
                effects.update(probability=float(condition[2]) / 100, max_activations=int(condition[3]))
                turn = re.fullmatch(r"Turn count\s*(≤|≥|<=|>=)\s*(\d+)", condition[1])
                mental = re.fullmatch(r"Mental\s*(≤|≥|<=|>=)\s*([\d.]+)%", condition[1])
                if turn:
                    effects["end_turn" if turn[1] in ("≤", "<=") else "start_turn"] = int(turn[2])
                    return "passive", effects, []
                if mental:
                    effects["mental_ratio_max" if mental[1] in ("≤", "<=") else "mental_ratio_min"] = float(mental[2]) / 100
                    return "passive", effects, []
            effects["condition"] = text or "missing passive condition/probability/count"
            return "passive", effects, [effects["condition"]]
        return "unknown", ({"condition": text} if "If:" in text else {}), [title + " / " + text]


class EnglishCardPageParser:
    """parse(html, source_url=..., card_id=...) -> existing CardData contract.

    Raw SP values are preserved even when another language disagrees. A caller must
    audit/merge such conflicts before recommending an account-bound team.
    """
    def parse(self, html: str, *, source_url: str, card_id: str | None = None,
              kind: str | None = None, rarity: str | None = None,
              fetched_at: str | None = None) -> CardData:
        url = urlsplit(source_url)
        title = unquote(url.path.removeprefix("/wiki/"))
        if (url.scheme != "https" or url.hostname != "shinycolors.wiki" or url.username or url.password
                or url.port not in (None, 443) or url.query or url.fragment or not re.fullmatch(r"[SP]-[^/]+", title)
                or not url.path.startswith("/wiki/")):
            raise ValueError("expected a public shinycolors.wiki /wiki/{S,P}- card URL")
        inferred = title[0]
        if kind is not None and kind != inferred:
            raise ValueError("card kind conflicts with URL")
        dom = _DOM()
        dom.feed(html)
        scope = next((n for n in dom.root.walk() if "mw-parser-output" in n.attrs.get("class", "").split()), dom.root)
        result: dict[str, Any] = dict(card_id=card_id or title, name=title, idol="UNKNOWN", kind=inferred,
            status="PARTIAL", source_url=source_url, rarity=rarity, levels={}, max_levels={},
            owned_live_skills=[], support_skills=[], panels=[], events=[], provenance={}, unknown_fields=[], unsupported=[])
        base = dict(url=source_url, fetched_at=fetched_at, parser_version=PARSER_VERSION,
                    content_sha256=sha256(html.encode("utf-8")).hexdigest())
        result["provenance"]["card"] = dict(base)
        unknown, unsupported = result["unknown_fields"], result["unsupported"]

        def source(path, *cells, **extra):
            result["provenance"][path] = dict(base, cells=[c.source() for c in cells], **extra)

        for node in scope.walk():
            if node.tag != "li":
                continue
            label, sep, value = node.text().partition(":")
            if not sep:
                continue
            key = {"JP Card Name": "name", "Card Rarity": "rarity", "Unit": "unit",
                   "Release Date (Available Period)": "implementation_date"}.get(label.strip())
            if key and value.strip():
                result[key] = value.strip()
                result["provenance"][key] = dict(base, line=node.line, text=node.text())
                if key == "name":
                    idol = re.match(r"^[【〖].+[】〗](.+)$", value.strip())
                    if idol:
                        result["idol"] = idol[1]
                        result["provenance"]["idol"] = dict(base, line=node.line, text=node.text())
        if result["idol"] == "UNKNOWN":
            unknown.append("idol")

        tables = []
        section = ""
        def visit(node):
            nonlocal section
            if node.tag in ("h2", "h3", "h4"):
                section = node.text().split("[")[0].strip()
            if node.tag == "table":
                tables.append((section, node))
                return
            for child in node.children:
                if isinstance(child, _Node):
                    visit(child)
        visit(scope)
        grids = []
        for index, (section, table) in enumerate(tables):
            try:
                grids.append((section, _table_grid(table, index)))
            except (ValueError, IndexError) as exc:
                unknown.append(f"html.table.{index}: {exc}")
        descriptions = {}
        for section, grid in grids:
            if section == "Support Skills" and grid and [c.text if c else "" for c in grid[0]][:2] == ["Mastery", "Description"]:
                for row in grid[1:]:
                    if len(row) == 2 and all(row):
                        descriptions[row[0].text] = row[1]

        for section, grid in grids:
            if not grid:
                continue
            header = [c.text if c else "" for c in grid[0]]
            if section == "Stats" and header[:5] == ["Card LV", "Vocal", "Dance", "Visual", "Mental"]:
                for row in grid[1:]:
                    if len(row) != 5 or any(c is None for c in row):
                        unknown.append("stats.row")
                        continue
                    level = re.fullmatch(r"(\d+)(?:\s*\(([0-4])☆\))?", row[0].text)
                    if not level:
                        unknown.append("stats.level: " + row[0].text)
                        continue
                    lv = int(level[1])
                    result["levels"][lv] = {a: _numeric(c.text) for a, c in zip(STATS, row[1:])}
                    for a, cell in zip(STATS, row[1:]):
                        source(f"levels.{lv}.{a}", row[0], cell)
                        if result["levels"][lv][a] is None:
                            unknown.append(f"levels.{lv}.{a}")
                    if level[2] is not None:
                        result["max_levels"][int(level[2])] = lv
                        source(f"max_levels.{level[2]}", row[0])
            elif section == "Support Skills" and header == ["Appeal Name", "Description", "Card LV"]:
                for index, row in enumerate(grid[1:]):
                    if len(row) != 3 or not all(row):
                        unknown.append("owned_live.row")
                        continue
                    lv = re.fullmatch(r"(\d+)(?:-(\d+))?", row[2].text)
                    skill_id = f"owned-{index}-{row[0].text}"
                    skill = EnglishMechanicsParser.live(row[1].text, skill_id, int(lv[1])) if lv else None
                    source(f"owned_live_skills.{skill_id}", *row)
                    if skill:
                        result["owned_live_skills"].append(skill)
                    else:
                        unknown.append(f"owned_live_skills.{skill_id}")
            elif section == "Support Skills" and header[0] == "Support Skill":
                if len(grid) < 2:
                    unknown.append("support_skills.level_header")
                    continue
                levels = [c.text if c else "" for c in grid[1][1:]]
                if not levels or any(not n.isdigit() for n in levels):
                    unknown.append("support_skills.level_header")
                    continue
                for row in grid[2:]:
                    if len(row) != len(levels) + 1 or not all(row):
                        unknown.append("support_skills.row")
                        continue
                    name = row[0].text
                    desc = descriptions.get(name)
                    kind_, attr, room, issues = EnglishMechanicsParser.support(name, desc.text if desc else "")
                    sparse = {}
                    for lv, cell in zip(levels, row[1:]):
                        if cell.text:
                            amount = _numeric(cell.text)
                            if amount is None:
                                unknown.append(f"support_skills.{name}.levels.{lv}")
                            else:
                                sparse[int(lv)] = amount
                        source(f"support_skills.{name}.levels.{lv}", cell)
                    result["support_skills"].append(dict(id=name, kind=kind_, attribute=attr, room=room,
                                                       levels=sparse, unsupported=issues))
                    source(f"support_skills.{name}", row[0], *([desc] if desc else []))
            elif section in ("Support Events", "Unique Produce Events"):
                attrs = {i: ATTR.get(c.text, c.text if c.text == "SP" else None) for i, c in enumerate(grid[0]) if c}
                attrs = {i: a for i, a in attrs.items() if a}
                events = {}
                for row in grid[1:]:
                    if not row or row[0] is None or not row[0].text or len({id(c) for c in row if c}) == 1:
                        continue
                    name = row[0].text
                    if any(c and c.text == "True End" for c in row[1:]):
                        result["te_eligible"] = True
                        source("te_eligible", *[c for c in row if c], evidence="explicit card-specific True End event; availability only")
                        continue
                    if set(attrs.values()) != {*STATS, "SP"}:
                        unknown.append("events.headers")
                        continue
                    gains, issues = {}, []
                    for column, attr in attrs.items():
                        cell = row[column] if column < len(row) else None
                        amount = (0 if cell and not cell.text else _numeric(cell.text)) if cell else None
                        if amount is None:
                            issues.append(f"missing/unsupported {attr}")
                        else:
                            gains[attr] = amount
                    event = events.setdefault(name, dict(id=name, options=[]))
                    option = dict(gains=gains)
                    if section == "Unique Produce Events":
                        option["label"] = row[1].text if len(row) > 1 and row[1] else ""
                    if issues:
                        option["unsupported"] = issues
                        event["unsupported"] = issues
                    event["options"].append(option)
                    source(f"events.{name}.options.{len(event['options']) - 1}", *[c for c in row if c])
                for event in events.values():
                    if section == "Support Events" and len(event["options"]) == 1:
                        event.update(event.pop("options")[0])
                    result["events"].append(event)
            elif section == "Skill Panel":
                for row_index, row in enumerate(grid):
                    if not row or row[0] is None or not re.fullmatch(r"(\d+)\s*SP", row[0].text):
                        continue
                    for column_index, cell in enumerate(row[1:], 1):
                        if cell is None or cell.row != row[0].row or cell.row != row_index or not cell.text:
                            continue
                        if cell.column != column_index or cell.rowspan != 1:
                            continue
                        r, col = cell.row, cell.column
                        body = grid[r + 1][col] if r + 1 < len(grid) and col < len(grid[r + 1]) else None
                        if body is None or body.row != r + 1:
                            unknown.append(f"panels.body.{r}.{col}")
                            continue
                        panel_id = cell.node.attrs.get("id") or f"panel-{cell.table}-{r}-{col}"
                        label, effect_text = cell.text, body.text
                        prereq = next((n for n in body.node.walk() if "prerequisites" in n.attrs.get("class", "").split()), None)
                        if prereq:
                            effect_text = effect_text.replace(prereq.text(), "").strip()
                        pkind, effects, issues = EnglishMechanicsParser.panel(label, effect_text, panel_id)
                        # Image-only conditions stay explicit instead of becoming unconditional buffs.
                        if effects.get("condition"):
                            effects["condition"] = body.node.text(images=True)
                            issues = [label + " / " + effects["condition"]]
                        lb = re.search(r"\(([0-4])☆\)|\(☆([0-4])\)", label)
                        event = re.search(r"\((E\d*)\)", label)
                        prerequisites = []
                        known = False
                        # Accept only named textual prerequisites pointing to DOM panel IDs.
                        if prereq:
                            links = [n.attrs["href"][1:] for n in prereq.walk() if n.tag == "a" and n.attrs.get("href", "").startswith("#")]
                            if links or prereq.text() in ("Prerequisites: None", "Prerequisites: none"):
                                prerequisites, known = links, True
                        result["panels"].append(dict(id=panel_id, kind=pkind, effects=effects,
                            sp_cost=int(re.fullmatch(r"(\d+)\s*SP", row[0].text)[1]),
                            required_lb=int(lb[1] or lb[2]) if lb else 0, event=event[1] if event else None,
                            prerequisites=prerequisites, prerequisites_known=known, unsupported=issues))
                        source(f"panels.{panel_id}", row[0], cell, body, raw_title=label, raw_effect=body.node.text(images=True))
                        source(f"panels.{panel_id}.sp_cost", row[0])
                        source(f"panels.{panel_id}.required_lb", cell)
                        source(f"panels.{panel_id}.effects", cell, body)
                        if event:
                            source(f"panels.{panel_id}.event", cell)
                        if not known:
                            unknown.append(f"panels.{panel_id}.prerequisites")
                        if event and event[1] == "E":
                            unknown.append(f"panels.{panel_id}.event_identity")
            elif section == "Memory Appeal":
                unknown.append("memory_appeal")
                source("memory_appeal", *[c for row in grid for c in row if c])

        ids = {p["id"] for p in result["panels"]}
        for panel in result["panels"]:
            if panel["prerequisites_known"] and any(pid not in ids for pid in panel["prerequisites"]):
                panel["prerequisites_known"] = False
                unknown.append(f"panels.{panel['id']}.prerequisites")
        required = ("levels", "max_levels", "owned_live_skills", "support_skills", "events", "panels") if inferred == "S" else ("panels", "events")
        for name in required:
            if not result[name]:
                unknown.append(name)
        if inferred == "P" and result.get("te_eligible") is None:
            unknown.append("te_eligible")
        for skill in result["owned_live_skills"] + result["support_skills"] + result["panels"]:
            unsupported.extend(f"{skill['id']}: {s}" for s in skill.get("unsupported", []))
        result["unknown_fields"] = list(dict.fromkeys(unknown))
        result["unsupported"] = list(dict.fromkeys(unsupported))
        result["status"] = "PARTIAL" if unknown or unsupported else "OK"
        return CardData.from_dict(result)
