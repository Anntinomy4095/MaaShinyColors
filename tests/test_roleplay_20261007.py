"""Fixes from the 2026-10-07 role-play usability test.

Each test pins one problem a persona hit: the 已核对 button that did not clear the 主页 reminder, the Lv/凸 import that
showed every row as 「未持有」 without saying why, the unexplained 「可选 1 / 共 920」 training picker, the hidden
developer-only tabs, the shop switches whose price was not visible, the check script that ignored failures and used the
system interpreter, and the README path to the new UI. Offline: no host, no game, no input.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def run_node(script):
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class RoleplayJsHelperTests(unittest.TestCase):
    def test_acknowledged_uncertain_run_no_longer_needs_attention(self):
        # Cause: ackStatus only cleared this session's taskStatus; 主页 re-added the same uncertain result from today's
        # run history, so 已核对 looked ignored. Now acked names are skipped for history (not for a fresh result).
        out = run_node(
            "import { attentionItems } from './ui/components/daily-panel.js';"
            "const base = { schema: { tasks: [] }, annotations: { tasks: {} }, profile: { tasks: [] }, host: { connected: true },"
            "  ledger: [], runs: [{ started_at: '2026-10-07T12:00:00+09:00', tasks: [{ name: 'MatchSkip', status: 'uncertain' }] }] };"
            "const day = '2026-10-07';"
            "console.log(JSON.stringify({"
            "  before: attentionItems({ ...base, taskStatus: {} }, day).map((i) => i.key),"
            "  acked: attentionItems({ ...base, taskStatus: {}, acks: { day, tasks: ['MatchSkip'] } }, day).map((i) => i.key),"
            "  yesterday: attentionItems({ ...base, taskStatus: {}, acks: { day: '2026-10-06', tasks: ['MatchSkip'] } }, day).map((i) => i.key),"
            "  fresh: attentionItems({ ...base, taskStatus: { MatchSkip: { status: 'uncertain' } }, acks: { day, tasks: ['MatchSkip'] } }, day)"
            "    .map((i) => [i.key, i.ack]) }));")
        self.assertEqual(out["before"], ["run:MatchSkip"])
        self.assertEqual(out["acked"], [])
        self.assertEqual(out["yesterday"], ["run:MatchSkip"])   # a check counts for its own game day only
        self.assertEqual(out["fresh"], [["task:MatchSkip", "MatchSkip"]])   # a fresh result has its own 已核对

    def test_acks_survive_a_restart_for_the_same_game_day(self):
        # 2026-10-26 (user: 已核对统一并记住): one 已核对 on 主页 / 任务 / 资源, kept in localStorage.
        out = run_node(
            "const store = new Map();"
            "globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)) };"
            "const A = await import('./ui/components/acks.js');"
            "const now = new Date('2026-10-26T03:00:00Z');"
            "const today = A.loadAcks(now);"
            "A.saveAcks({ ...today, tasks: ['Shop'], ledger: [A.ledgerKey({ ts: 't1', task: 'Shop' })] });"
            "const ledger = [{ ts: 't1', task: 'Shop', status: 'uncertain' }, { ts: 't2', task: 'Training', status: 'pending' },"
            "  { ts: 't3', task: 'Training', status: 'done' }];"
            "const later = A.loadAcks(new Date('2026-10-26T10:00:00Z'));"
            "const nextDay = A.loadAcks(new Date('2026-10-27T10:00:00Z'));"
            "console.log(JSON.stringify({ later, nextDay, open: A.openLedger({ ledger, acks: later }).map((l) => l.ts),"
            "  broken: (globalThis.localStorage = { getItem() { throw new Error('x'); } }, A.loadAcks(now).tasks) }));")
        self.assertEqual(out["later"]["tasks"], ["Shop"])
        self.assertEqual(out["nextDay"]["tasks"], [])                  # yesterday's task checks are dropped
        self.assertEqual(out["nextDay"]["ledger"], ["t1|Shop"])        # a ledger check stays until the host drops the entry
        self.assertEqual(out["open"], ["t2"])
        self.assertEqual(out["broken"], [])

    def test_home_checklist_tells_unticked_from_not_yet_run(self):
        # Cause: 主页 showed 「营业 未运行」 although 营业 was never added, which read like a pending run.
        out = run_node(
            "import { itemPlanned, DEFAULT_DAILY } from './ui/components/daily-panel.js';"
            "const item = (k) => DEFAULT_DAILY.items.find((i) => i.key === k);"
            "const profile = { tasks: [{ name: 'MatchSkip', enabled: false }, { name: 'GradeFes', enabled: true },"
            "  { name: 'Shop', enabled: true }], removed_tasks: ['Shop'] };"
            "console.log(JSON.stringify({ fes: itemPlanned(item('fes'), profile), business: itemPlanned(item('business'), profile),"
            "  removed: itemPlanned(item('shop'), profile), empty: itemPlanned(item('fes'), undefined) }));")
        self.assertEqual(out, {"fes": True, "business": False, "removed": False, "empty": False})
        home = read("ui/components/home-panel.js")
        self.assertIn('const planned = itemPlanned(item, state.profile);', home)
        self.assertIn(': planned ? "未运行" : "未勾选"', home)  # 2026-10-09 home grid cell

    def test_level_import_hint_explains_unowned_rows(self):
        out = run_node(
            "import { levelImportHint } from './ui/components/team-levels-import.js';"
            "const rows = (owned) => owned.map((o, i) => ({ id: String(i), owned: o, stale: i === 0 }));"
            "console.log(JSON.stringify({ none: levelImportHint(rows([false, false]), '2026-10-07T01:00:00Z'),"
            "  some: levelImportHint(rows([true, false, false]), '2026-10-07'), all: levelImportHint(rows([true, true]), ''),"
            "  missing: levelImportHint(rows([true]), undefined) }));")
        self.assertIn("「持有卡牌」为空：先导入或勾选持有卡，再导入 Lv/凸。", out["none"])
        self.assertIn("识别于 2026-10-07，共 2 张；1 张来自较早扫描", out["none"])
        self.assertIn("2 张不在「持有卡牌」中", out["some"])
        self.assertNotIn("持有卡牌", out["all"])
        self.assertIn("未知日期", out["all"])
        self.assertIn("未知日期", out["missing"])
        for text in out.values():
            self.assertTrue(text.endswith("按「确定」才保存。"))

    def test_training_picker_explains_uncalibrated_cards_and_the_default(self):
        # 2026-10-10: the picker lists the confirmed S cards (持有卡牌) with an empty 「默认」 choice. 2026-10-26 (role-play
        # A12/D11): a card without a live training sample carries a 未校准 tag, and the footnote says so in plain words
        # (the old 「只定位、不点击」 text was stale: OPEN_UNCALIBRATED has been True since 2026-10-12).
        picker = read("ui/components/team-picker.js")
        self.assertIn("标「未校准」的卡还没有实机训练样本", picker)
        self.assertNotIn("只定位、不点击", picker)
        self.assertIn('export const TRAINING_DEFAULT = "列表第一张";', picker)


class RoleplayStaticTests(unittest.TestCase):
    def test_ack_is_remembered_and_cleared_by_a_new_run(self):
        app = read("ui/app.js")
        self.assertIn("    acks: loadAcks(),", app)
        ack = app[app.index("ackStatus: (name) => {"):app.index('toast("已核对");')]
        self.assertIn("saveAcks(acks);", ack)
        self.assertIn("store.set({ taskStatus: statuses, acks });", ack)
        start = app[app.index("startRun: async (onlyTask = null) => {"):app.index("无法开始：${error.message}")]
        self.assertIn("tasks: (s.acks?.tasks || []).filter((n) => !names.includes(n))", start)
        self.assertIn("saveAcks(acks);", start)
        badges = app[app.index("function tabBadges(state) {"):app.index("function visibleBadges(")]
        self.assertIn("const ledger = openLedger(state);", badges)   # the 资源 badge drops 已核对 entries too

    def test_developer_hint_is_always_visible(self):
        # Cause: 「W.I.N.G.」 (formerly W.I.N.G. T.E.)/「安全」 tabs need 开发者选项, but the switch had no text while off.
        panel = read("ui/components/settings-panel.js")
        # 2026-10-24 (layout carries the information): the hint is the switch's tooltip, always present while off.
        self.assertIn('switchRow("dev-on", "开发者选项", dev, () => actions.setPref("developer", !dev), { focusKey: "dev-on", hint: DEV_HINT })', panel)
        hint = re.search(r'export const DEV_HINT = "([^"]+)";', panel).group(1)
        for word in ("安全", "实机权限不变"):
            self.assertIn(word, hint)

    def test_daily_shop_switches_show_their_fixed_price(self):
        # Cause: 「金训练券」 (10 マニー daily) and 「金训练券 283」 (third card) looked like the same item.
        notes = json.loads(read("ui/annotations.json"))["options"]
        self.assertEqual(notes["商店跳过券"]["label"], "跳过券 10マニー")
        self.assertEqual(notes["商店金训练券"]["label"], "金训练券 10マニー")
        options = json.loads(read("assets/tasks/shop.json"))["option"]
        for key in ("商店跳过券", "商店金训练券"):
            self.assertIn("10 マニー", options[key]["description"])

    def test_check_script_uses_venv_and_fails_on_errors(self):
        # Cause: check.ps1 called the system `python` (no maafw: real-Maa replays skipped silently) and never looked at
        # $LASTEXITCODE, so failing tests still ended "successfully"; the try/catch never fired for a native command.
        script = read("scripts/check.ps1")
        # 2026-10-07: the interpreter is resolved (active venv > project .venv > PATH), not pinned to one path.
        self.assertIn('. (Join-Path $PSScriptRoot "resolve_python.ps1")', script)
        self.assertIn('".venv\\Scripts\\python.exe"', read("scripts/resolve_python.ps1"))
        self.assertEqual(script.count("if ($LASTEXITCODE -ne 0) { throw"), 3)
        self.assertNotIn("catch", script)

    def test_readme_produce_dry_run_survives_a_non_utf8_pipe(self):
        # Cause: README step 6 crashed with UnicodeEncodeError when its Chinese rationale went to a cp1252 pipe.
        import os
        import sys
        env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
        result = subprocess.run([sys.executable, "tools/simulate_produce.py", "tests/fixtures/produce/basic_turn.json"],
                                cwd=ROOT, capture_output=True, env=env, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertIn("rationale", json.loads(result.stdout.decode("utf-8")))

    def test_readme_quick_start_reaches_the_new_ui(self):
        readme = read("README.md")
        section = readme[readme.index("## 从源码运行"):readme.index("## 许可")]
        for needle in ("--composition-game", "--enable-pipeline-input", "tools/fetch_ocr_candidates.py", "scripts/bootstrap.ps1",
                       "tools\\package.py build --zip"):
            self.assertIn(needle, section)
        for target in re.findall(r"\]\((docs/[^)#]+)", section):
            self.assertTrue((ROOT / target).is_file(), target)
        # The OCR fetcher really installs the model the runtime loads.
        self.assertIn('"ppocr_v6_small": "OCR/ppocr_v6/small"', read("tools/fetch_ocr_candidates.py"))
        self.assertIn('"models" / "ppocr_v6_small"', read("agent/core/resource_bundles.py"))


if __name__ == "__main__":
    unittest.main()
