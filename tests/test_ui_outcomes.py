"""Batch 4: ui/components/outcomes.js maps real task outcome codes only (no drift from agent/core).

2026-10-26: every W.I.N.G. stop code the Agent can latch has player text (no bare English code in the UI)."""
import ast
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from agent.core import business_task as bt
from agent.core import grade_task as gt
from agent.core import match_task as mt
from agent.core import shop_task as st

ROOT = Path(__file__).resolve().parent.parent
OUTCOMES_JS = ROOT / "ui" / "components" / "outcomes.js"


def mapped_codes():
    text = OUTCOMES_JS.read_text(encoding="utf-8")
    body = text.split("export const OUTCOME_TEXT = {", 1)[1].split("};", 1)[0]
    return re.findall(r"^\s*([a-z][a-z0-9_]*):", body, re.M)



# W.I.N.G. navigation stop reasons (agent/core/wing_nav.py, wing_custom.py) shown with outcomeText.
WING_STOPS = {"team_not_found_in_units", "team_fingerprint_mismatch", "team_fingerprint_unverified", "team_duplicate_idol",
              "unit_search_budget", "unit_page_mismatch", "click_unavailable", "wait_stalled", "team_edit_not_automated",
              "picker_sort_not_automated"}
# 推荐编队: the unit edit's stops (agent/core/wing_team_edit.py + wing_nav._decide_team_pick); "_partial" =
# earlier slots of the unit were already changed.
TEAM_EDIT_STOPS = {"team_rules_violated", "team_edit_card_not_in_list", "team_edit_tile_not_enabled",
                   "team_edit_scroll_not_enabled", "team_edit_tile_misaligned", "team_edit_tile_ambiguous",
                   "team_edit_scroll_budget", "team_edit_scroll_overlap_lost", "team_edit_scroll_unsettled",
                   "team_edit_picker_unread", "team_edit_picker_kind", "team_edit_picker_not_opened",
                   "team_edit_pick_unconfirmed", "team_edit_slot_unverified", "team_edit_slot_unread",
                   "team_edit_slot_blank", "team_edit_slot_repeat", "team_edit_idol_in_other_slot", "team_edit_budget",
                   "team_edit_unverified",
                   # 选卡列表按属性排序 (agent/core/wing_team_edit.py picker sort, 2026-10-14)
                   "picker_sort_popup_unknown", "picker_sort_unverified", "picker_sort_key_unconfirmed",
                   "picker_sort_apply_unconfirmed",
                   # Auto 自动编队 edit-only prep (wing_nav._edit_only_back, 2026-10-30)
                   "team_edit_done", "team_edit_state_missing", "unit_back_no_effect", "unit_back_unexpected_screen"}
PARTIAL = {"team_edit_card_not_in_list", "team_edit_tile_not_enabled", "team_edit_scroll_not_enabled"}
WING_STOPS = WING_STOPS | TEAM_EDIT_STOPS | {f"{c}_partial" for c in PARTIAL}

# ---- W.I.N.G. stop codes: Python source -> outcomes.js --------------------------------------------------------------
# A stop code reaches the UI as the wing report's ``outcome`` (WingSession.report -> UiTaskResult.WingReason ->
# outcomeText(code, "AutoProduce")). It is the first reason ``WingNavRuntime.stop`` latched. The extractor reads the
# four literal forms the Agent uses, plus the Pipeline Stop nodes; reasons built at run time are listed explicitly.
CORE = ROOT / "agent" / "core"
WING_PIPELINE = ROOT / "assets" / "resource" / "base" / "pipeline" / "wing.json"
WING_STOP_MODULES = ("wing_nav.py", "wing_custom.py", "wing_options.py", "wing_team_edit.py", "wing_live_policy.py")


def _reason_codes(node):
    """Codes an expression can yield: a string literal, both branches of ``a if c else b``, and the text before ``:`` of a
    ``"code:detail"`` f-string / concatenation (the UI keeps the detail apart). Reasons computed at run time
    (``f"week_decision_{kind}"``, ``"live_" + x``) yield nothing here; see INDIRECT_STOPS."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _reason_codes(node.body) + _reason_codes(node.orelse)
    head = None
    if isinstance(node, ast.JoinedStr) and node.values:
        head = node.values[0]
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        head = node.left
    if isinstance(head, ast.Constant) and isinstance(head.value, str) and head.value.endswith(":"):
        return [head.value[:-1]]
    return []


def _stop_reasons(tree):
    """Reasons of ``_stop(code)``, ``<runtime>.stop(screen, code)``, ``NavDecision("stop", reason=code)``,
    ``_r(screen, "stop", reason=code)`` and ``{"action": "stop", "reason": code}`` step dicts."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name == "_stop" and node.args:
                found += _reason_codes(node.args[0])
            elif name == "stop" and isinstance(func, ast.Attribute) and len(node.args) >= 2:
                found += _reason_codes(node.args[1])
            elif name in ("NavDecision", "_r"):
                kind = (node.args[0] if name == "NavDecision" else node.args[1] if len(node.args) > 1 else None) if node.args else None
                if isinstance(kind, ast.Constant) and kind.value == "stop":
                    for keyword in node.keywords:
                        if keyword.arg == "reason":
                            found += _reason_codes(keyword.value)
        elif isinstance(node, ast.Dict):
            fields = {key.value: value for key, value in zip(node.keys, node.values) if isinstance(key, ast.Constant)}
            action = fields.get("action")
            if isinstance(action, ast.Constant) and action.value == "stop" and "reason" in fields:
                found += _reason_codes(fields["reason"])
    return found


def extracted_wing_stops() -> dict:
    """{code: sources} of every literal W.I.N.G. stop code in the Agent modules and the Pipeline Stop nodes."""
    codes = {}
    for name in WING_STOP_MODULES:
        tree = ast.parse((CORE / name).read_text(encoding="utf-8"))
        for code in _stop_reasons(tree):
            codes.setdefault(code, set()).add(name)
    nodes = json.loads(WING_PIPELINE.read_text(encoding="utf-8"))
    for node in nodes.values():
        if node.get("custom_action") == "SC.Wing.Stop":
            codes.setdefault(node["custom_action_param"]["reason"], set()).add("wing.json")
    return codes


# Stop codes the extractor cannot see (built at run time or passed through a variable): (code, module, source needles).
# The needles keep the list honest: a renamed or removed code makes test_indirect_wing_stops_exist fail.
_TIMING = ("NavDecision(\"stop\", reason=refusal)",)
INDIRECT_STOPS = (
    # f"week_decision_{kind}" -- WeeklyDecision.kind of a decision that is neither lesson/work/audition/rest/spend_sp
    ("week_decision_stop", "wing_nav.py", ('f"week_decision_{kind}"',)),
    ("week_decision_handoff", "wing_policy.py", ('WeeklyDecision("handoff"',)),
    # f"event_{choice.rule}" -- EventChoice.rule of a choice without an index
    ("event_ambiguous_promise_options", "wing_events.py", ('"ambiguous_promise_options"',)),
    ("event_choice_table_index_out_of_range", "wing_events.py", ('f"{rule}_index_out_of_range"', '"choice_table"')),
    ("event_config_default_index_out_of_range", "wing_events.py", ('f"{rule}_index_out_of_range"', '"config_default"')),
    # f"reward_{kind}_unconfirmed" -- the 獲得報酬 review's pending tap kinds
    ("reward_tab_unconfirmed", "wing_nav.py", ('f"reward_{kind}_unconfirmed"', '"tab"')),
    ("reward_item_unconfirmed", "wing_nav.py", ('f"reward_{kind}_unconfirmed"', '"item"')),
    ("reward_protect_unconfirmed", "wing_nav.py", ('f"reward_{kind}_unconfirmed"', '"protect"')),
    ("reward_reroll_unconfirmed", "wing_nav.py", ('f"reward_{kind}_unconfirmed"', '"reroll"')),
    # "live_" + target["reason"] / choice["reason"] -- wing_live_policy.plan_turn
    ("live_no_judge_present", "wing_live_policy.py", ('"live_" + target["reason"]', '"no_judge_present"')),
    ("live_trend_unread", "wing_live_policy.py", ('"live_" + target["reason"]', '"trend_unread"')),
    ("live_no_card", "wing_live_policy.py", ('"live_" + choice["reason"]', '"no_card"')),
    # the host refuses the timed APPEAL tap (wing_custom.TIMING_REREAD + the host-unavailable refusal)
    ("appeal_timing_unread", "wing_custom.py", ('"appeal_timing_unread"',) + _TIMING),
    ("frame_missing_or_stale", "wing_custom.py", ('"frame_missing_or_stale"',) + _TIMING),
    ("analysed_image_is_not_the_latest_frame", "wing_custom.py", ('"analysed_image_is_not_the_latest_frame"',) + _TIMING),
    ("appeal_timing_host_unavailable", "wing_custom.py", ('"appeal_timing_host_unavailable"',) + _TIMING),
    # the picker scan's stop passes its found dict through (``{"action": "stop", **found}``)
    ("picker_offset_unread", "wing_team_edit.py", ('{"reason": "picker_offset_unread"}', '{"action": "stop", **found')),
    # reason += "_partial" -- earlier slots of the unit were already changed
    *((f"{code}_partial", "wing_nav.py", ('reason += "_partial"', f'"{code}"')) for code in sorted(PARTIAL)),
)

# Codes that need no player text. Each one still shows WING_FALLBACK (advice + the code) if it ever appears.
WING_STOP_WHITELIST = {
    "begin": "Pipeline Stop.Begin placeholder; WingSession.begin latches begin_refused first and the first reason wins",
    "halted": "Pipeline Halted placeholder; the runtime is only inactive after a real reason was latched",
    "no_decision": "Pipeline Hold placeholder; replaced by the pending decision's own reason (WingSession.stop)",
    "actRefused": "camelCase Pipeline reason; UiTaskResult.WingReason forwards lower_snake codes only, so the UI never gets it",
    "noDecision": "camelCase Pipeline reason; UiTaskResult.WingReason forwards lower_snake codes only, so the UI never gets it",
    "clickUnconfirmed": "camelCase Pipeline reason; UiTaskResult.WingReason forwards lower_snake codes only, so the UI never gets it",
    "scorer_missing": "a scorer module failed to import (broken install), not a situation the player can act on",
    "intent_not_bound": "internal check: a decision clicked an intent its screen does not bind",
    "audition_hook_bad_result": "internal check: the audition hook returned something that is not a verdict",
    "live_hook_bad_result": "internal check: the live hook returned something that is not a plan",
    "live_plan_unknown_action": "internal check: the live plan named an action the navigator does not know",
    "live_agent_mode_requires_wing_audition": "internal check: wing_audition could not be imported (broken install)",
}


def mapped_wing_codes() -> set:
    """Codes outcomeText(code, "AutoProduce") finds: WING_OUTCOME_TEXT, WING_RUNTIME_TEXT, then the common OUTCOME_TEXT."""
    text = OUTCOMES_JS.read_text(encoding="utf-8")
    codes = set()
    for head in ("export const OUTCOME_TEXT = {", "export const WING_OUTCOME_TEXT = {", "const WING_RUNTIME_TEXT = {"):
        body = text.split(head, 1)[1].split("\n};", 1)[0]
        codes |= set(re.findall(r"^\s*([a-z][a-z0-9_]*):", body, re.M))
    return codes


def all_wing_stops() -> set:
    return set(extracted_wing_stops()) | {code for code, _, _ in INDIRECT_STOPS}


def run_node(body):
    result = subprocess.run(["node", "--input-type=module", "-e", body], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class WingStopTextTests(unittest.TestCase):
    def test_extractor_sees_the_known_stops(self):
        extracted = extracted_wing_stops()
        self.assertGreater(len(extracted), 120)
        for code in ("sp_primary_unknown", "act_refused", "click_unavailable", "scorer_error", "live", "lost",
                     "live_hand_unread", "options_dialog", "team_edit_slot_blank", "reward_sell_mode",
                     "picker_sort_unverified", "begin_refused", "wait_stalled"):
            self.assertIn(code, extracted, code)

    def test_indirect_wing_stops_exist(self):
        for code, module, needles in INDIRECT_STOPS:
            source = (CORE / module).read_text(encoding="utf-8")
            for needle in needles:
                self.assertIn(needle, source, f"{code}: {needle} not in {module}")

    def test_every_wing_stop_code_has_player_text(self):
        # 2026-10-26: a stop code without text showed as 「W.I.N.G. 已停止（sp_primary_unknown）」.
        missing = all_wing_stops() - mapped_wing_codes() - set(WING_STOP_WHITELIST)
        self.assertFalse(missing, f"stop codes without Chinese text in outcomes.js: {sorted(missing)}")

    def test_whitelist_is_current_and_justified(self):
        stops, mapped = all_wing_stops(), mapped_wing_codes()
        for code, why in WING_STOP_WHITELIST.items():
            self.assertTrue(why.strip(), code)
            self.assertIn(code, stops, f"{code} is no longer a stop code: drop it from the whitelist")
            self.assertNotIn(code, mapped, f"{code} has text now: drop it from the whitelist")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_outcome_text_never_falls_back_for_a_mapped_stop(self):
        codes = sorted(all_wing_stops() - set(WING_STOP_WHITELIST))
        out = run_node(
            "const O = await import('./ui/components/outcomes.js');"
            f"const codes = {json.dumps(codes)};"
            "console.log(JSON.stringify(codes.map((c) => [c, O.outcomeText(c, 'AutoProduce')])));")
        self.assertEqual([code for code, _ in out], codes)
        for code, text in out:
            self.assertNotIn("代码", text, code)
            self.assertNotIn(code, text, f"{code}: the text shows the raw code")
            self.assertRegex(text, r"[一-鿿]", code)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_fallback_is_player_text_with_the_code_last(self):
        out = run_node(
            "const O = await import('./ui/components/outcomes.js');"
            "console.log(JSON.stringify({ plain: O.outcomeText('brand_new_stop', 'AutoProduce'),"
            " detail: O.outcomeText('brand_new_stop:x_1', 'AutoProduce'), shop: O.outcomeText('brand_new_stop', 'Shop'),"
            " act: O.outcomeText('act_refused:repeat_without_progress', 'AutoProduce'),"
            " live: O.outcomeText('live:auto_state_unread', 'AutoProduce'),"
            " rest: O.outcomeText('act_refused:not_a_click', 'AutoProduce') }));")
        self.assertEqual(out["plain"], "W.I.N.G. 已停止，请看游戏画面，处理后点开始续跑（代码 brand_new_stop）")
        self.assertTrue(out["detail"].endswith("（代码 brand_new_stop：x_1）"), out["detail"])
        self.assertEqual(out["shop"], "brand_new_stop")           # only W.I.N.G. rows get the fallback
        self.assertIn("画面都没变", out["act"])                    # a detail with its own text
        self.assertTrue(out["live"].startswith("演出的自动开关"), out["live"])
        self.assertTrue(out["rest"].startswith("点击被安全检查拦下"), out["rest"])   # base text + （detail）
        self.assertTrue(out["rest"].endswith("（not_a_click）"), out["rest"])


def resource_read_reasons():
    """SC.FailTask reasons of 只读取资源 (the task is pure Pipeline: its stops live in the JSON, not in an agent module)."""
    nodes = json.loads((ROOT / "assets/resource/base/pipeline/resource_read.json").read_text(encoding="utf-8"))
    return {param["reason"] for node in nodes.values() if isinstance(param := node.get("custom_action_param"), dict) and "reason" in param}


def common_stop_reasons():
    """SC.FailTask reasons of the shared SC.Common.Stop.* nodes (ordinary tasks end there instead of the unknown reason)."""
    nodes = json.loads((ROOT / "assets/resource/base/pipeline/common/stops.json").read_text(encoding="utf-8"))
    return {node["custom_action_param"]["reason"] for name, node in nodes.items()
            if name.startswith("SC.Common.Stop.") and isinstance(node.get("custom_action_param"), dict)}


def pipeline_stop_reasons():
    """Plain-code SC.FailTask reasons of every Pipeline node. The host forwards a failed task's first plain code
    (UiTaskResult.StopReason), so each one reaches the task row; ``code:detail`` reasons are not forwarded."""
    codes = set()
    for path in (ROOT / "assets" / "resource" / "base" / "pipeline").rglob("*.json"):
        for node in json.loads(path.read_text(encoding="utf-8")).values():
            param = node.get("custom_action_param") if isinstance(node, dict) else None
            if (isinstance(node, dict) and node.get("custom_action") == "SC.FailTask" and isinstance(param, dict)
                    and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(param.get("reason")))):
                codes.add(param["reason"])
    return codes


class OutcomeTextTests(unittest.TestCase):
    def test_wing_stop_codes_exist_in_the_agent(self):
        source = "".join((ROOT / "agent/core" / name).read_text(encoding="utf-8")
                         for name in ("wing_nav.py", "wing_custom.py", "wing_team_edit.py"))
        for code in WING_STOPS:
            self.assertIn(code.removesuffix("_partial"), source)
        self.assertIn('reason += "_partial"', source)

    def test_every_mapped_code_is_a_real_outcome_or_note(self):
        known = (st.SUCCESS_OUTCOMES | st.FAILURE_OUTCOMES | gt.SUCCESS_OUTCOMES | gt.FAILURE_OUTCOMES | gt.HALT_OUTCOMES
                 | bt.SUCCESS_OUTCOMES | bt.HALT_OUTCOMES | bt.RETURN_FAILURE_OUTCOMES | mt.SUCCESS_OUTCOMES | mt.FAILURE_OUTCOMES
                 | {"config_invalid", "unit_score_mismatch", "unit_score_unreadable"} | WING_STOPS | resource_read_reasons()
                 | common_stop_reasons() | pipeline_stop_reasons())
        codes = mapped_codes()
        self.assertEqual(len(codes), len(set(codes)))
        self.assertFalse(set(codes) - known, sorted(set(codes) - known))

    def test_gacha_announcement_stop_has_player_text(self):
        # 2026-10-27: a full-screen gacha announcement stops the task with its own reason instead of the unknown one.
        self.assertIn("gacha_announcement_unhandled", common_stop_reasons())
        self.assertIn("gacha_announcement_unhandled", mapped_codes())

    def test_every_pipeline_stop_reason_has_player_text(self):
        # 2026-10-27: the host forwards a failed task's SC.FailTask reason to its row; a code without text shows raw.
        reasons = pipeline_stop_reasons()
        for code in ("maintenance", "fes_menu_open", "gacha_announcement_unhandled", "unknown_or_unhandled_state"):
            self.assertIn(code, reasons)
        self.assertFalse(reasons - set(mapped_codes()), sorted(reasons - set(mapped_codes())))

    def test_wing_team_stops_have_text(self):
        # every team stop the player can act on has its own short text (no WING_FALLBACK code)
        self.assertFalse(WING_STOPS - set(mapped_codes()), sorted(WING_STOPS - set(mapped_codes())))

    def test_batch4_outcomes_have_text(self):
        codes = set(mapped_codes())
        for code in ("collected_dispatch_disabled", "team_not_configured", "dispatch_confirm_not_enabled",
                     "dispatch_binding_mismatch", "dispatched_binding_unverified", "unit_mismatch",
                     "unit_score_mismatch", "rehearsal_not_sampled", "spend_not_enabled"):
            self.assertIn(code, codes)

    def test_panels_use_the_map(self):
        for rel in ("components/task-panel.js", "components/daily-panel.js"):
            text = (ROOT / "ui" / rel).read_text(encoding="utf-8")
            self.assertIn('import { outcomeText } from "./outcomes.js";', text, rel)
            self.assertIn("outcomeText(", text.split("import { outcomeText }", 1)[1], rel)
        panel = (ROOT / "ui/components/task-panel.js").read_text(encoding="utf-8")
        self.assertIn("停止中…", panel)
        self.assertIn("stopping: true", (ROOT / "ui/app.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
