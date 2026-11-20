"""Read-only foreground evidence interpretation; never authorize input."""
from __future__ import annotations

import re

CANVAS = (1280, 720)
STATES = {"known_popup", "partial", "conflict", "no_known_popup", "invalid_dimensions"}
NAME = re.compile(r"SC\.[A-Za-z0-9_.-]+")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_contracts(data, scene_names, sample_ids):
    require(type(data) is dict and {"version", "foreground", "expected"} <= set(data)
            and set(data) <= {"version", "foreground", "underlying", "expected"}, "前景合同字段无效")
    require(type(data["version"]) is int and data["version"] == 1, "前景合同版本无效")
    require(type(data["foreground"]) is list and data["foreground"], "前景合同不能为空")
    names = set()
    for contract in data["foreground"]:
        require(type(contract) is dict and set(contract) == {"name", "anchors"}, "合同仅允许名称与锚点，拒绝动作")
        name, anchors = contract["name"], contract["anchors"]
        require(type(name) is str and NAME.fullmatch(name) and name not in names, "合同须使用唯一SC.名称")
        names.add(name)
        require(type(anchors) is list and len(anchors) >= 2 and all(type(a) is str for a in anchors)
                and len(set(anchors)) == len(anchors) and set(anchors) <= set(scene_names), "合同须引用至少两个不同的现有锚点")
        require(all(NAME.fullmatch(a) for a in anchors), "锚点须使用SC.名称")
    underlying = data.get("underlying", [])
    require(type(underlying) is list and all(type(a) is str and NAME.fullmatch(a) for a in underlying)
            and len(set(underlying)) == len(underlying) and set(underlying) <= set(scene_names), "底层锚点引用无效")
    expected = data["expected"]
    require(type(expected) is dict and set(expected) == set(sample_ids), "离线预期须覆盖全部样本")
    for item in expected.values():
        require(type(item) is dict and set(item) == {"state", "popup"}, "离线预期字段无效")
        require(type(item["state"]) is str and item["state"] in STATES, "离线预期状态无效")
        require((item["state"] == "known_popup" and type(item["popup"]) is str and item["popup"] in names)
                or (item["state"] != "known_popup" and item["popup"] is None), "离线预期弹窗语义冲突")
    return data


def interpret_foreground(recognitions, dimensions, contracts):
    """Even absence of known popup anchors does not prove an unobstructed page."""
    result = {"state": "invalid_dimensions", "popup": None, "complete_contracts": [],
              "partial_contracts": [], "underlying_hits": [], "underlying_allowed": False,
              "input_enabled": False, "input_authorized": False, "close_authorized": False}
    if tuple(dimensions) != CANVAS:
        return result
    hits = set()
    observed = set()
    for item in recognitions:
        require(type(item) is dict and type(item.get("scene")) is str and type(item.get("hit")) is bool,
                "前景识别结果无效")
        require(item["scene"] not in observed, "前景锚点结果重复")
        observed.add(item["scene"])
        if item["hit"]:
            hits.add(item["scene"])
    required = {a for c in contracts["foreground"] for a in c["anchors"]} | set(contracts.get("underlying", []))
    require(required <= observed, "前景锚点结果缺失")
    for contract in contracts["foreground"]:
        anchors = set(contract["anchors"])
        if anchors <= hits:
            result["complete_contracts"].append(contract["name"])
        elif anchors & hits:
            result["partial_contracts"].append(contract["name"])
    result["underlying_hits"] = [a for a in contracts.get("underlying", []) if a in hits]
    complete, partial = result["complete_contracts"], result["partial_contracts"]
    if len(complete) + len(partial) > 1:
        result["state"] = "conflict"
    elif complete:
        result.update(state="known_popup", popup=complete[0])
    else:
        result["state"] = "partial" if partial else "no_known_popup"
    return result
