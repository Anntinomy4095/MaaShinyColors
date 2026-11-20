"""离线精确编队档案适配；只读取本地库，不推测等级、凸数或缺失机制。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from agent.core.card_id_map import CardIdMap, WIKI_ID
from agent.core.team_recommend import clean_details, load_card_database
from agent.core.true_end.models import CardData, Ownership, extract_card_features
from agent.core.wing_score import SupportProfile, TeamProfile


@dataclass(frozen=True)
class ProfileBuild:
    profile: TeamProfile
    unknowns: tuple[str, ...]
    sources: Mapping[str, Mapping[str, Any]]

    @property
    def complete(self) -> bool:
        return not self.unknowns


def _load_library_cards() -> dict:
    try:
        from agent.core.team_recommend import load_library
        return dict(load_library().get("cards") or {})
    except (ValueError, TypeError, OSError, KeyError):
        return {}


ROOM_MAP = {"Vo": "lesson_Vo", "Da": "lesson_Da", "Vi": "lesson_Vi",
            "radio": "radio", "talk": "talk", "magazine": "magazine"}
MASTERY_KEYS = {"mastery_stat": "stat", "mastery_sp": "sp", "mastery_stamina": "stamina"}
UNIT_MASTERY_KEYS = {"unit_mastery_sp": "sp", "unit_mastery_stat": "stat", "unit_mastery_stamina": "stamina"}
UNIT_ROOM = "unit"      # SupportProfile.masteries key of the ユニットマスタリー row (wing_score.UNIT_MASTERY_ROOM)


def build_team_profile(team: Mapping[str, Any], owned: Mapping[str, Any] | None = None, *,
                       cards: Mapping[str, Any] | None = None,
                       id_map: CardIdMap | None = None,
                       library: Mapping[str, Any] | None = None) -> ProfileBuild:
    """构建四 active + guest 的 numeric-ID 档案，保留逐卡可检查的未知项。

    ``cards`` 可注入 CardData 或原始 CardData 字典；``id_map`` 必须是精确 CardIdMap。
    guest 与 active 使用同样的真实 Lv/凸合同，不因槽位默认满级。
    ``complete`` 描述数据覆盖，不授权输入，也不表示 True End 成功率。
    """
    unknowns: list[str] = []
    sources: dict[str, dict[str, Any]] = {}
    supports: dict[str, SupportProfile] = {}
    produce_unit = None
    if cards is None:
        try:
            cards, notes = load_card_database()
            unknowns.extend(f"database:{note}" for note in notes)
        except (ValueError, TypeError, OSError) as error:
            cards = {}
            unknowns.append(f"database:invalid:{error}")
        # 2026-10-12: the strict True End database holds only a handful of reviewed cards; the ShinyColorsDB library
        # (``team_recommend.load_library``, keyed by the 10-digit Enza ID that the formation reader and the config
        # use) covers every S card's support skills. It is consulted only for IDs the strict database cannot map.
        if library is None:
            library = _load_library_cards()
    library = library or {}
    if id_map is None:
        try:
            id_map = CardIdMap.load()
        except (ValueError, TypeError, OSError) as error:
            id_map = CardIdMap()
            unknowns.append(f"id_map:invalid:{error}")
    try:
        details = clean_details(owned)
    except (ValueError, TypeError) as error:
        details = {}
        unknowns.append(f"owned.details:invalid:{error}")
    active = team.get("s_cards")
    if not isinstance(active, (list, tuple)) or len(active) != 4:
        return ProfileBuild(TeamProfile(), tuple(unknowns + ["team.s_cards:requires_four"]), sources)
    slots = [("p_card", team.get("p_card")), *[(f"active.{i}", value) for i, value in enumerate(active)],
             ("guest", team.get("guest"))]
    seen: set[str] = set()
    idols: set[str] = set()
    for role, wiki_id in slots:
        if (not isinstance(wiki_id, str) or not WIKI_ID.fullmatch(wiki_id)
                or wiki_id[0] != ("1" if role == "p_card" else "2")):
            unknowns.append(f"team.{role}:invalid_numeric_id")
            continue
        if wiki_id in seen:
            unknowns.append(f"team.{role}:duplicate_card:{wiki_id}")
            continue
        seen.add(wiki_id)
        local: list[str] = []
        stats = None
        bond0 = None
        masteries: dict[str, dict[str, float]] = {}
        unit = None
        stamina_support = []
        rest_support = []
        target = id_map.true_end_id(wiki_id)
        source: dict[str, Any] = {"role": role, "numeric_id": wiki_id, "true_end_id": target}
        if (not target or target not in cards) and wiki_id in library:
            # Library card keyed by its own Enza ID (card_id == key, checked below like the strict database).
            cards = {**cards, wiki_id: library[wiki_id]}
            target = wiki_id
            source.update(true_end_id=None, library_id=wiki_id)
        sources[wiki_id] = source
        entry = details.get(wiki_id, {})
        source["ownership"] = dict(entry)
        if not target:
            local.append(f"mapping:{id_map.unmapped_reason(wiki_id)}")
        elif target not in cards:
            local.append("card:missing")
        else:
            try:
                raw = cards[target]
                card = raw if isinstance(raw, CardData) else CardData.from_dict(raw)
                if card.card_id != target or card.kind != ("P" if role == "p_card" else "S"):
                    raise ValueError("mapped card identity/kind mismatch")
                unit = card.unit
                if card.kind == "P":
                    produce_unit = unit
                if card.idol in idols:
                    local.append("team:duplicate_idol")
                idols.add(card.idol)
                source.update(source_url=card.source_url, provenance=dict(card.provenance), status=card.status)
                if card.status == "PARSE_ERROR":
                    local.append("card:parse_error")
                required = ("limit_break",) if card.kind == "P" else ("level", "limit_break")
                missing = [key for key in required if key not in entry]
                local.extend(f"ownership.{key}:missing" for key in missing)
                if not missing and card.status != "PARSE_ERROR":
                    ownership = Ownership.from_dict({"card_id": target, "level": 1, **entry})
                    if card.kind == "S" and ownership.level not in card.levels:
                        from agent.core.card_data_merge import with_owned_level
                        enriched = with_owned_level(asdict(card) if isinstance(raw, CardData) else raw,
                                                    ownership.level, ownership.limit_break)
                        card = CardData.from_dict(enriched)
                        source["owned_level"] = ((enriched.get("merge") or {}).get("derived_levels") or {}).get(
                            str(ownership.level))
                    feature = extract_card_features(card, ownership)
                    local.extend(feature.unknowns)
                    local.extend(f"unsupported:{value}" for value in feature.unsupported)
                    if card.kind == "S":
                        # A missing cap prevents verification that this level is attainable.
                        if ownership.limit_break not in card.max_levels:
                            local.append("ownership.level:cap_unverified")
                        else:
                            stats = dict(feature.stats) or None
                            bonds = []
                            for skill, amount in feature.support_effects:
                                if ownership.limit_break < skill.required_lb:
                                    continue
                                if skill.kind in ("stamina_support", "rest_support"):
                                    from agent.core.support_effects import support_skill_expectation
                                    effect = support_skill_expectation(skill, amount, card.rarity)
                                    if effect is not None:
                                        (stamina_support if skill.kind == "stamina_support" else rest_support).append(effect)
                                        continue
                                if skill.unsupported:
                                    continue
                                if skill.kind == "bond":
                                    bonds.append(amount * 5)
                                    continue
                                if skill.kind in ("audition_mastery_sp", "audition_mastery_stat"):
                                    key = "sp" if skill.kind == "audition_mastery_sp" else f"stat_{skill.attribute}"
                                    if key != "sp" and skill.attribute not in ("Vo", "Da", "Vi", "Me"):
                                        local.append(f"support_skills.{skill.id}:room_attribute_unrepresentable")
                                        continue
                                    row = masteries.setdefault("audition", {})
                                    row[key] = row.get(key, 0) + amount
                                    continue
                                unit_key = UNIT_MASTERY_KEYS.get(skill.kind)
                                if unit_key is not None:
                                    # Same-unit members at this action, including P and excluding the holder.
                                    if unit is None:
                                        local.append(f"support_skills.{skill.id}:unit_unknown")
                                    if unit_key == "stat":
                                        if skill.attribute not in ("Vo", "Da", "Vi", "Me"):
                                            local.append(f"support_skills.{skill.id}:room_attribute_unrepresentable")
                                            continue
                                        unit_key = f"stat_{skill.attribute}"
                                    row = masteries.setdefault(UNIT_ROOM, {})
                                    row[unit_key] = row.get(unit_key, 0) + amount
                                    continue
                                key = MASTERY_KEYS.get(skill.kind)
                                if key is None:
                                    local.append(f"support_skills.{skill.id}:unconsumed:{skill.kind}")
                                    continue
                                room = ROOM_MAP.get(skill.room)
                                if not room or (key == "stat" and skill.attribute not in ("Vo", "Da", "Vi", "Me")):
                                    local.append(f"support_skills.{skill.id}:room_attribute_unrepresentable")
                                    continue
                                if key == "stat" and (skill.room not in ("Vo", "Da", "Vi")
                                                      or skill.attribute != skill.room):
                                    key = f"stat_{skill.attribute}"
                                row = masteries.setdefault(room, {})
                                row[key] = row.get(key, 0) + amount
                            if len(bonds) == 1:
                                bond0 = bonds[0]
                            else:
                                local.append("bond:missing" if not bonds else "bond:ambiguous_multiple")
            except (ValueError, TypeError, KeyError) as error:
                local.append(f"card:invalid:{error}")
        # Ownership diagnostics remain explicit even if mapping is missing.
        for key in (("limit_break",) if role == "p_card" else ("level", "limit_break")):
            if key not in entry and f"ownership.{key}:missing" not in local:
                local.append(f"ownership.{key}:missing")
        local = sorted(set(local))
        source["unknowns"] = tuple(local)
        unknowns.extend(f"{wiki_id}:{value}" for value in local)
        if role != "p_card":
            supports[wiki_id] = SupportProfile(wiki_id, stats=stats, bond0=bond0, masteries=masteries,
                                              unknowns=tuple(local), unit=unit,
                                              stamina_support=tuple(stamina_support), rest_support=tuple(rest_support))
    return ProfileBuild(TeamProfile(supports, produce_unit), tuple(sorted(set(unknowns))), sources)
