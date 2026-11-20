"""Offline cross-source comparisons and explicit, drift-bound field reviews."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import math
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .models import CardData

_MISSING = object()


def _canonical(value: Any) -> Any:
    """JSON-equivalent comparisons permit integer mapping keys and list/tuple input."""
    if isinstance(value, Mapping):
        if any(not isinstance(k, (str, int)) or isinstance(k, bool) for k in value):
            raise ValueError("mapping keys must be strings or integers")
        if len({str(k) for k in value}) != len(value):
            raise ValueError("ambiguous mapping keys")
        if any(isinstance(k, str) and k.isdigit() and str(int(k)) != k for k in value):
            raise ValueError("numeric mapping keys must be canonical")
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite review value")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("review values must be JSON-compatible")


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    left, right = _canonical(left), _canonical(right)
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _validate_card(card: CardData) -> None:
    CardData.from_dict(asdict(card))
    for group in ("panels", "owned_live_skills", "support_skills"):
        entries = getattr(card, group)
        if len({item.id for item in entries}) != len(entries):
            raise ValueError(f"duplicate {group} ID")


def _evidence(value: Mapping[str, Any], *, date: bool = False) -> None:
    for key in (("source_url", "locator", "reviewed_at", "reason") if date else ("source_url", "locator")):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"review evidence requires {key}")
    url = urlsplit(value["source_url"])
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("review source must be a public HTTPS URL")
    if date:
        try:
            datetime.fromisoformat(value["reviewed_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("reviewed_at must be an ISO date/time") from exc


def _source(card: CardData, field: str) -> dict[str, Any]:
    candidates = [field]
    parts = field.split(".")
    if len(parts) >= 2 and parts[0] in ("panels", "owned_live_skills", "support_skills") and parts[1].isdigit():
        entries = getattr(card, parts[0])
        if int(parts[1]) < len(entries):
            candidates.append(".".join([parts[0], entries[int(parts[1])].id, *parts[2:]]))
    for candidate in candidates:
        while candidate:
            if candidate in card.provenance:
                return deepcopy(dict(card.provenance[candidate]))
            candidate = candidate.rpartition(".")[0]
    return deepcopy(dict(card.provenance.get("card", {"url": card.source_url})))


def compare_card_sources(primary: CardData, reference: CardData, *,
                         panel_map: Mapping[str, str] | None = None,
                         idol_alias: Mapping[str, Any] | None = None,
                         live_map: Mapping[str, str] | None = None,
                         support_map: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Compare exact mechanics, never infer cross-language panel correspondence.

    Card IDs/kinds must already be bound by the caller. A differently written idol
    needs an explicit {primary, reference, source_url, locator} alias record.
    Live/support IDs match literally unless an explicit map is supplied.
    """
    _validate_card(primary)
    _validate_card(reference)
    if primary.card_id != reference.card_id or primary.kind != reference.kind:
        raise ValueError("comparison requires identical bound card_id and kind")
    if primary.idol != reference.idol:
        if not isinstance(idol_alias, Mapping):
            raise ValueError("idol mismatch requires explicit alias evidence")
        _evidence(idol_alias)
        if idol_alias.get("primary") != primary.idol or idol_alias.get("reference") != reference.idol:
            raise ValueError("idol alias does not bind these sources")
    report: dict[str, Any] = dict(card_id=primary.card_id, kind=primary.kind,
        primary_source=_source(primary, "card"), reference_source=_source(reference, "card"),
        idol_alias=deepcopy(dict(idol_alias)) if idol_alias is not None else None,
        comparisons=[], unmatched={})

    def compare(field, left, right, reference_field=None):
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            left, right = _canonical(left), _canonical(right)
            for key in sorted(left.keys() | right.keys()):
                compare(f"{field}.{key}", left.get(key, _MISSING), right.get(key, _MISSING),
                        f"{reference_field or field}.{key}")
            return
        status = ("missing_primary" if left is _MISSING else "missing_reference" if right is _MISSING else
                  "unknown_both" if left is None and right is None else
                  "unknown_primary" if left is None else "unknown_reference" if right is None else
                  "same" if _equal(left, right) else "conflict")
        report["comparisons"].append(dict(field=field, reference_field=reference_field or field,
            status=status, primary_value=None if left is _MISSING else _canonical(left),
            reference_value=None if right is _MISSING else _canonical(right),
            primary_present=left is not _MISSING, reference_present=right is not _MISSING,
            primary_provenance=_source(primary, field), reference_provenance=_source(reference, reference_field or field)))

    for field in ("levels", "max_levels", "te_eligible"):
        compare(field, getattr(primary, field), getattr(reference, field))
    for group, mapping in (("panels", panel_map), ("owned_live_skills", live_map), ("support_skills", support_map)):
        left = {item.id: (index, asdict(item)) for index, item in enumerate(getattr(primary, group))}
        right = {item.id: (index, asdict(item)) for index, item in enumerate(getattr(reference, group))}
        if mapping is None:
            mapping = {} if group == "panels" else {key: key for key in left.keys() & right.keys()}
        if (not isinstance(mapping, Mapping) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items())
                or len(set(mapping.values())) != len(mapping) or set(mapping) - left.keys() or set(mapping.values()) - right.keys()):
            raise ValueError(f"{group} map must be an explicit one-to-one mapping of existing IDs")
        for key, target in mapping.items():
            li, lv = left[key]
            ri, rv = right[target]
            fields = ("kind", "sp_cost", "required_lb", "event", "effects", "unsupported") if group == "panels" else tuple(k for k in lv if k != "id")
            for field in fields:
                compare(f"{group}.{li}.{field}", lv.get(field, _MISSING), rv.get(field, _MISSING), f"{group}.{ri}.{field}")
        report["unmatched"][group] = dict(primary=sorted(left.keys() - mapping.keys()),
                                         reference=sorted(right.keys() - set(mapping.values())))
    report["summary"] = {status: sum(c["status"] == status for c in report["comparisons"])
                         for status in sorted({c["status"] for c in report["comparisons"]})}
    return report


_REVIEW_FIELDS = {
    "panels": {"kind", "sp_cost", "required_lb", "event", "effects", "unsupported"},
    "support_skills": {"kind", "attribute", "room", "required_lb", "levels", "unsupported"},
    "owned_live_skills": {"components", "unlock_level", "buff_attribute", "buff_amount", "buff_turns", "heal_fraction", "unsupported"},
}


def _allowed(path: list[str]) -> bool:
    if any(p.isdigit() and str(int(p)) != p for p in path):
        return False
    if path[0] in ("levels", "max_levels"):
        return len(path) == 1 or (path[1].isdigit() and (len(path) == 2 or
               (path[0] == "levels" and len(path) == 3 and path[2] in ("Vo", "Da", "Vi", "Me"))))
    if path == ["te_eligible"]:
        return True
    if len(path) < 3 or path[0] not in _REVIEW_FIELDS or not path[1].isdigit() or path[2] not in _REVIEW_FIELDS[path[0]]:
        return False
    # Nested mapping keys are allowed only inside mechanics mappings, never identity or topology.
    return len(path) == 3 or (path[2] in ("effects", "levels", "components") and
                             all(p and p not in ("id", "prerequisites", "prerequisites_known") for p in path[3:]))


def _parent(raw: dict[str, Any], parts: list[str]) -> tuple[Any, Any]:
    node: Any = raw
    for index, part in enumerate(parts):
        if isinstance(node, (list, tuple)):
            if not part.isdigit() or str(int(part)) != part or int(part) >= len(node):
                raise ValueError("review list index must identify an existing entry")
            key: Any = int(part)
        elif isinstance(node, dict):
            key = int(part) if part.isdigit() and int(part) in node else part
        else:
            raise ValueError("review path does not traverse a mapping/list")
        if index == len(parts) - 1:
            return node, key
        if isinstance(node, dict) and key not in node:
            raise ValueError("review parent mapping does not exist")
        node = node[key]
    raise ValueError("empty review path")


def review_card(primary: CardData, changes: Sequence[Mapping[str, Any]]) -> CardData:
    """Apply only explicit field corrections; leave identity, topology and group gaps intact.

    Missing mapping leaves may be inserted with expected_previous=None. Lists may
    only address existing entries. All changes are validated atomically; originals
    remain untouched, and successful records retain PARTIAL review status.
    """
    _validate_card(primary)
    raw = deepcopy(asdict(primary))
    seen = set()
    for change in changes:
        if not isinstance(change, Mapping):
            raise ValueError("each review change must be a mapping")
        _evidence(change, date=True)
        field = change.get("field")
        if (not isinstance(field, str) or not field or
                any(field == prior or field.startswith(prior + ".") or prior.startswith(field + ".") for prior in seen) or
                not _allowed(field.split(".")) or "value" not in change or "expected_previous" not in change):
            raise ValueError("duplicate, invalid or forbidden review field")
        seen.add(field)
        expected_hash = change.get("expected_source_sha256")
        original = primary.provenance.get("card", {})
        if expected_hash is not None and (not isinstance(expected_hash, str) or
                not re.fullmatch(r"[a-fA-F0-9]{64}", expected_hash) or
                expected_hash.lower() != str(original.get("content_sha256", "")).lower()):
            raise ValueError("review source SHA256 does not match primary source")
        parts = field.split(".")
        parent, key = _parent(raw, parts)
        previous = parent.get(key, _MISSING) if isinstance(parent, dict) else parent[key]
        if not _equal(None if previous is _MISSING else previous, change["expected_previous"]):
            raise ValueError(f"{field}: expected_previous does not match current value")
        _canonical(change["value"])
        previous_source = _source(primary, field)
        parent[key] = deepcopy(change["value"])
        raw["provenance"][field] = dict(status="REVIEWED_FIELD", previous_value=None if previous is _MISSING else _canonical(previous),
            previous_present=previous is not _MISSING, reviewed_value=_canonical(change["value"]),
            previous_source=previous_source, original_source=deepcopy(dict(original)),
            source_url=change["source_url"], locator=change["locator"], reviewed_at=change["reviewed_at"], reason=change["reason"],
            expected_source_sha256=expected_hash)
        # A corrected leaf resolves only its exact unknown; never clear a whole missing group.
        if not isinstance(change["value"], (Mapping, list, tuple)) and change["value"] is not None:
            raw["unknown_fields"] = tuple(u for u in raw["unknown_fields"] if u != field)
    raw["status"] = "PARTIAL"
    try:
        reviewed = CardData.from_dict(raw)
        _validate_card(reviewed)
    except (TypeError, KeyError, AttributeError, ValueError) as exc:
        raise ValueError("review violates CardData mechanics contract: " + str(exc)) from exc
    return reviewed
