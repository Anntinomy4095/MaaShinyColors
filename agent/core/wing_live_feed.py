"""W.I.N.G. decision log -> user-facing live status (UI 「运行中」 view).

Reads ``WingNavRuntime.log`` entries (the same records the run summary keeps) and turns them into plain Chinese:
the current step sentence, the season/week and stats header, the latest week decision (via ``wing_score.score_card``)
with its top alternatives and excluded options, and a short timeline (week plan, skill learned/forgotten, audition,
event choice, Live, stop). Read-only: never changes a decision, the memory or the log. No node names, intent ids or
reason codes reach the text; unknown codes become a generic sentence.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from agent.core import live_status

ATTRS = ("Vo", "Da", "Vi", "Me")
SCREEN_TEXT = {
    "Hub": "主页", "HubBanner": "主页", "HubSemiFinal": "主页", "HubFinal": "主页",
    "Schedule": "日程", "AuditionTab": "试镜", "ScheduleBusy": "日程", "Choice": "事件",
    "Commu": "剧情", "CommuSkip": "剧情", "Live": "Live", "LiveSkillCutIn": "Live", "ResultJudge": "Live 结果",
    "Review": "技能", "LearnConfirm": "学习", "ReplaceConfirm": "替换", "ReplaceDialog": "替换",
    "ForgetSelect": "忘记技能", "Owned": "已有技能", "Evaluation": "评价", "TotalEval": "总评",
    "Home": "游戏主页", "StartConfirm": "开始确认", "AuditionLocked": "试镜", "ProduceSelect": "育成选择",
    "ItemSelect": "道具",
}
INTENT_TEXT = {
    "skill_owned": "已有技能", "schedule_lesson_tab": "课程列表", "replace_confirm_ok": "确认替换",
    "replace_ok": "确认替换", "replace_cancel": "取消替换", "forget_cancel": "取消忘记",
    "learn_cancel": "取消学习", "produce_next": "选 W.I.N.G.", "item_decide": "确认道具",
    "start_confirm_ok": "开始育成", "hub_final_live": "决赛", "eval_next": "评价",
    "total_eval_tap": "总评", "reward_ok": "领奖", "clear_reward_ok": "领通关奖",
    "rank_next": "排名", "result_list_tap": "结果", "resume_ok": "续接育成",
    "home_produce_resume": "续接育成", "home_produce": "打开育成", "mission_popup_ok": "关任务提示",
}
REASON_TEXT = {"spend_sp_auto": "SP 学习", "owned_return": "返回技能页", "live_unread_auto": "Live 读不出，转游戏自动",
               "sp_info_settling": "等待技能画面", "sp_learn_settling": "等待学习结果"}
QUIET_STOPS = ("no_decision", "noDecision", "halted", "stop")
GROUP_SHORT = {"primary": "主", "other": "他", "sp": "SP", "support": "援", "fans": "粉",
               "risk": "险", "misc": "约"}
RULE_TEXT = {"best": "最高分", "score_best": "最高分",
             "fallback_empty_room": "兜底·空房课", "fallback_rest": "兜底·休息",
             "no_candidate": "无候选"}
EXCLUDE_TEXT = {"trouble_hard_cap": "风险过高", "gain_unknown": "缺成长值",
                "trouble_unknown": "缺失败率", "stamina_unknown": "缺体力",
                "p_win_unknown": "合格率未知"}
STOP_TEXT = {
    "produce_finished_home": "育成结束", "finished": "完成", "segment_acts_reached": "本段步数用完",
    "sp_learn_unconfirmed": "学习结果未确认",
    "sp_forget_requires_user": "需选择忘记的技能", "sp_info_unread": "技能信息未读到",
    "state_unreadable": "读数不全", "lost": "画面未识别",
    "clickUnconfirmed": "点击未确认", "audition_tab_without_plan": "试镜无计划",
    "decision_too_slow": "判断超时", "screen_unstable": "画面不稳定",
}
STOP_PREFIX_TEXT = (("act_refused", "安全检查拒绝"), ("click_not_sent", "点击未送达"),
                    ("scorer_", "评分出错"), ("week_decision_", "本周无可执行行动"),
                    ("audition_declined", "无合适试镜"), ("live:", "Live 需接手"))


def stop_text(reason) -> str:
    if not isinstance(reason, str):
        return "已停止"
    if reason in STOP_TEXT:
        return STOP_TEXT[reason]
    for prefix, text in STOP_PREFIX_TEXT:
        if reason.startswith(prefix):
            return text
    return "已停止·请查看"


def row_label(row: Mapping[str, Any]) -> str:
    """「Vo课 3人」/「工作 空房」/「试镜 +1000」/「休息」."""
    kind, attr, n = row.get("kind"), row.get("attribute"), row.get("supports")
    if kind == "audition":
        m = re.search(r"_(\d+)k", str(row.get("id") or ""))
        return f"试镜 +{int(m.group(1)) * 1000}" if m else "试镜"
    if kind == "rest":
        return "休息"
    base = f"{attr}课" if kind == "lesson" and attr else ("工作" if kind == "work" else "行动")
    if isinstance(n, int):
        base += f" {n}人" if n else " 空房"
    return base


def why_text(groups: Mapping[str, float], top: int = 3) -> str:
    items = sorted(((k, v) for k, v in (groups or {}).items() if isinstance(v, (int, float)) and abs(v) >= 0.05),
                   key=lambda kv: -abs(kv[1]))[:top]
    return " ".join(f"{GROUP_SHORT.get(k, '其他')} {v:+.1f}" for k, v in items) or "≈0"


def decision_card(info: Mapping[str, Any], season=None, weeks_left=None) -> dict | None:
    """Plain card of one ``WeeklyDecision.to_dict()``: chosen + up to 3 alternatives + excluded (collapsed in UI)."""
    from agent.core import wing_score
    plan = info.get("plan") if isinstance(info.get("plan"), Mapping) else None
    if not plan or not plan.get("scores"):
        return None
    kind = info.get("kind")
    chosen = info.get("option_id") or info.get("audition_id") or ("rest" if kind == "rest" else None)
    card = wing_score.score_card(plan, chosen=chosen, rule=info.get("rule") or plan.get("score_rule"))
    rows = sorted(card["rows"], key=lambda r: -(r.get("score") if isinstance(r.get("score"), (int, float)) else -1e9))
    best = next((r for r in rows if r["chosen"]), None)
    options = []
    for r in rows:
        if r is best or len([o for o in options if not o["chosen"]]) < 3:
            options.append({"label": row_label(r), "score": round(r["score"], 1) if isinstance(r.get("score"), (int, float)) else None,
                            "why": why_text(r["groups"]), "chosen": r is best,
                            "risk": r.get("trouble") if isinstance(r.get("trouble"), (int, float)) and r["trouble"] > 0 else None})
    excluded = [{"label": row_label(e), "reason": EXCLUDE_TEXT.get(e["reason"], "读数不全")}
                for e in card["excluded"][:8]]
    rule = card.get("rule") or ""
    return {"season": season, "weeks_left": weeks_left,
            "chosen": row_label(best) if best else None,
            "chosen_score": round(best["score"], 1) if best and isinstance(best.get("score"), (int, float)) else None,
            "why": why_text(best["groups"]) if best else None,
            "rule": next((t for k, t in RULE_TEXT.items() if rule.endswith(k)), "最高分"),
            "options": options, "excluded": excluded, "more": max(0, len(rows) - len(options))}


def _readings(wing: dict, r: Mapping[str, Any]) -> None:
    for key in ("season", "weeks_left", "fans", "fans_left", "sp"):
        if type(r.get(key)) is int:
            wing[key] = r[key]
    if type(r.get("stamina_pct")) is int:
        wing["stamina"] = r["stamina_pct"]
    stats = r.get("stats")
    if isinstance(stats, Mapping):
        wing["stats"] = {k: stats[k] for k in ATTRS if type(stats.get(k)) is int}


def _event(wing: dict, kind: str, text: str, at: str) -> None:
    wing["events"].append({"kind": kind, "text": text[:live_status.MAX_TEXT], "at": at})


def step_for(entry: Mapping[str, Any], wing: Mapping[str, Any]) -> str | None:
    """Terse label for a ``decide`` entry: what the run is doing right now."""
    d = entry.get("decision") or {}
    screen, action, intent = entry.get("screen"), d.get("action"), d.get("intent") or ""
    detail = d.get("detail") or {}
    where = SCREEN_TEXT.get(screen, "育成")
    if d.get("reason") in REASON_TEXT:
        return REASON_TEXT[d["reason"]]
    if action == "wait":
        return "Live 自动" if screen in ("Live", "LiveSkillCutIn") else f"等待 · {where}"
    if action in ("stop", "finish"):
        return stop_text(d.get("reason"))
    if action != "click":
        return None
    week = detail.get("decision") if isinstance(detail.get("decision"), Mapping) else None
    if intent in INTENT_TEXT:
        return INTENT_TEXT[intent]
    if intent == "hub_schedule":
        return "日程"
    if intent == "hub_rest":
        return "休息"
    if intent == "hub_review":
        return "SP 学习"
    if intent == "schedule_audition_tab":
        return "试镜列表" if d.get("reason") == "survey_auditions" else "试镜"
    if intent in ("audition_next", "audition_prev"):
        return "试镜列表"
    if intent.startswith("schedule_room_") or (intent == "schedule_decide" and week):
        label = row_label({"kind": week.get("kind"), "attribute": week.get("attribute"),
                           "supports": _supports(week)}) if week else "课程"
        return f"选 {label}" if intent.startswith("schedule_room_") else f"确定 {label}"
    if intent == "schedule_decide":
        verdict = detail.get("verdict") or {}
        reward = verdict.get("reward") if isinstance(verdict, Mapping) else None
        return f"试镜 +{reward}" if isinstance(reward, int) else "试镜"
    if intent.startswith("choice_"):
        text = detail.get("text")
        return f"事件 · {text[:20]}" if isinstance(text, str) and text else "事件"
    if intent.startswith("skill_node_") or intent == "skill_learn" or intent == "learn_ok":
        return "学习技能"
    if intent.startswith("forget_select_"):
        choice = detail.get("choice") or {}
        name = choice.get("name") if isinstance(choice, Mapping) else None
        return f"忘记 · {name}" if isinstance(name, str) else "忘记技能"
    if intent.startswith(("live_card_", "live_judge_", "live_appeal")):
        return "Live · 出牌"
    if intent == "live_auto":
        return "Live · 自动"
    if intent.startswith("commu_") or intent == "tap_textbox":
        return "剧情"
    if intent.startswith("unit_"):
        return "编队"
    if intent in ("page_return", "dialog_cancel", "popup_close", "item_popup_close", "cheer_close"):
        return f"返回 · {where}"
    return where


def _supports(week: Mapping[str, Any]):
    for row in ((week.get("plan") or {}).get("scores") or []):
        if row.get("id") == week.get("option_id"):
            return row.get("supports")
    return None


def apply_entry(status: live_status.LiveStatus, entry: Mapping[str, Any], memory=None) -> None:
    """Fold one log entry into ``status`` (W.I.N.G. section + step)."""
    event = entry.get("event")
    at = status.wall()
    step = None

    def mutate(wing):
        nonlocal step
        if event == "decide":
            r = entry.get("readings") or {}
            _readings(wing, r)
            d = entry.get("decision") or {}
            step = step_for(entry, wing)
            detail = d.get("detail") or {}
            week = detail.get("decision") if isinstance(detail.get("decision"), Mapping) else None
            if week and d.get("action") == "click":
                card = decision_card(week, wing.get("season"), wing.get("weeks_left"))
                fresh = card is not None and (wing.get("decision") or {}).get("key") != [wing.get("season"), wing.get("weeks_left")]
                if card is not None:
                    card["key"] = [wing.get("season"), wing.get("weeks_left")]
                    wing["decision"] = card
                    if fresh:
                        _event(wing, "week", f"{_week_text(wing)} · {card['chosen'] or '—'}", at)
            if d.get("action") == "click" and str(d.get("intent", "")).startswith("choice_"):
                text = (d.get("detail") or {}).get("text")
                _event(wing, "choice", text[:20] if isinstance(text, str) and text else "事件选项", at)
            if d.get("intent") == "schedule_decide" and isinstance((d.get("detail") or {}).get("verdict"), Mapping):
                reward = d["detail"]["verdict"].get("reward")
                _event(wing, "audition", f"+{reward}" if isinstance(reward, int) else "试镜", at)
            if str(d.get("intent", "")).startswith("forget_select_"):
                choice = (d.get("detail") or {}).get("choice") or {}
                name = choice.get("name") if isinstance(choice, Mapping) else None
                _event(wing, "forget", name if isinstance(name, str) else "技能", at)
            if entry.get("screen") in ("Live", "LiveSkillCutIn"):
                wing["live"] = {"text": step or "Live"}
            elif entry.get("screen") in ("ResultJudge",):
                wing["live"] = {"text": "Live 结果"}
            elif wing.get("live") and entry.get("screen") in SCREEN_TEXT and entry.get("screen") not in ("Loading",):
                wing["live"] = None
        elif event == "sp_session_start":
            sp = entry.get("sp")
            _event(wing, "sp", f"学习 · 余{sp}" if isinstance(sp, int) else "学习", at)
            step = "SP 学习"
        elif event == "sp_learned":
            name, cost = entry.get("name"), entry.get("cost")
            text = name if isinstance(name, str) and name else "技能"
            if isinstance(cost, int):
                text += f" −{cost}SP"
            if type(entry.get("sp")) is int:
                wing["sp"] = entry["sp"]
            _event(wing, "skill", text, at)
            step = f"学会 · {text}"
        elif event == "sp_session_end":
            _event(wing, "sp", "学习结束", at)
        elif event == "sp_nothing_affordable":
            _event(wing, "sp", "SP 不足", at)
        elif event == "win":
            season = entry.get("season")
            _event(wing, "audition", f"第{season}季 合格" if isinstance(season, int) else "合格", at)
        elif event == "stop":
            text = stop_text(entry.get("reason"))
            last = wing["events"][-1] if wing["events"] else None
            quiet = entry.get("reason") in QUIET_STOPS and last is not None and last["kind"] == "stop"
            if not quiet and (last is None or last["text"] != text):
                _event(wing, "stop", text, at)
            if not quiet:
                step = text
        elif event == "finish":
            _event(wing, "stop", "W.I.N.G. 结束", at)
            step = "W.I.N.G. 结束"
        elif event == "memory_restored":
            _event(wing, "info", "续接上一段", at)
        elif event == "lesson_failure":
            drop = entry.get("drop") if isinstance(entry.get("drop"), Mapping) else {}
            text = "大失败" if entry.get("kind") == "catastrophe" else "失败"
            parts = [f"{k}{v:+d}".replace("-", "−") for k, v in drop.items() if type(v) is int]
            _event(wing, "fail", " ".join([text, *parts]), at)

    status.wing(mutate)
    if event == "decide":
        status.frame_seen()
    if step:
        status.step(step)


def _week_text(wing: Mapping[str, Any]) -> str:
    season, left = wing.get("season"), wing.get("weeks_left")
    if isinstance(season, int) and isinstance(left, int):
        return f"剩{left}周"
    return "本周"


class WingFeed:
    """Cursor over one runtime's log; ``feed(log)`` folds the new entries into the active LiveStatus."""

    def __init__(self):
        self.log_id, self.cursor = None, 0

    def feed(self, log, memory=None, status=None) -> int:
        status = status or live_status.current()
        if status is None or not isinstance(log, list):
            return 0
        if id(log) != self.log_id:
            self.log_id, self.cursor = id(log), 0
        new = log[self.cursor:]
        self.cursor = len(log)
        for entry in new:
            if isinstance(entry, Mapping):
                try:
                    apply_entry(status, entry, memory)
                except Exception:
                    pass
        return len(new)
