"""ノウハウ page views (ui/components/knowhow-views.js): team usability, Halo picks, STEP trio, grid location honesty,
search recipe, 整理复核 facts, produce-end comparison, coverage map and scan status. Offline, synthetic data only."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
NODE = shutil.which("node")


def run_node(body, data=None):
    script = ("const data = JSON.parse(process.argv[1]);\n"
              "const base = 'file:///' + process.cwd().replace(/\\\\/g, '/') + '/';\n"
              "const d = await import(new URL('ui/components/knowhow-data.js', base));\n"
              "const v = await import(new URL('ui/components/knowhow-views.js', base));\n"
              "const fx = (await import(new URL('ui/fixtures/knowhow-demo.js', base))).default;\n"
              "const books = d.cleanBooks(fx); const pending = d.cleanBooks({ books: fx.pending_books });\n"
              + body)
    result = subprocess.run([NODE, "--input-type=module", "-e", script, json.dumps(data, ensure_ascii=False)],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def bk(title, idol, rows, ev=None, count=1, total=None, **extra):
    return {"fingerprint": f"b-{title}", "title": title, "idol": idol, "stats": {"vo": 1, "da": 1, "vi": 1, "me": 1},
            "rows": [{"name": n, "level": lv} for n, lv in rows], "knowhow_count": len(rows) if total is None else total,
            "eval_value": ev, "count": count, **extra}


@unittest.skipUnless(NODE, "node not installed")
class KnowhowViewTests(unittest.TestCase):
    def test_library_reload_follows_completed_runs_and_scans_only(self):
        out = run_node(
            "const state = {runs:[{id:'wing-1'}],host:{knowhow_scan:{finished_seq:2,frames:10}}};"
            "const key = d.knowhowReloadKey(state);"
            "console.log(JSON.stringify({key,"
            " tick:d.knowhowReloadKey({...state,host:{knowhow_scan:{finished_seq:2,frames:20}}}),"
            " finish:d.knowhowReloadKey({...state,runs:[{id:'wing-2'}]}),"
            " scan:d.knowhowReloadKey({...state,host:{knowhow_scan:{finished_seq:3}}}),"
            " legacy:d.knowhowReloadKey({...state,runs:[{run_id:'wing-1'}]}),"
            " synced:d.knowhowReloadKey({...state,host:{...state.host,game_assets:{seq:1,modules:{'knowhow.js':1}}}}),"
            " other:d.knowhowReloadKey({...state,host:{...state.host,game_assets:{seq:1,modules:{'owned-levels.js':1}}}})}));")
        self.assertNotEqual(out["synced"], out["key"])     # the host copied a newer knowhow.js (command-line scan)
        self.assertEqual(out["other"], out["key"])
        self.assertEqual(out["tick"], out["key"])
        self.assertNotEqual(out["finish"], out["key"])
        self.assertNotEqual(out["scan"], out["key"])
        self.assertEqual(out["legacy"], out["key"])

    def test_usability_tristate_and_general_book(self):
        out = run_node(
            "const team = v.teamFromSlots([{slot:'P',label:'P',id:'1',idol:'浅倉透'},{slot:'S1',label:'S1',id:'2',idol:'樋口円香'}]);\n"
            "const half = v.teamFromSlots([{slot:'P',label:'P',id:'1',idol:'浅倉透'},{slot:'S1',label:'S1',id:'2',idol:null}]);\n"
            "const b = (idol, title) => ({ idol, title, rows: [] });\n"
            "console.log(JSON.stringify([team.verified, half.verified, half.unresolved,\n"
            " v.bookUsability(b('樋口円香'), team), v.bookUsability(b('福丸小糸'), team), v.bookUsability(b(null, '【x】?'), team),\n"
            " v.bookUsability(b(null, '283プロのノウハウブック'), null), v.bookUsability(b('福丸小糸'), half),\n"
            " v.bookUsability(b('浅倉透'), half), v.bookUsability(b('福丸小糸'), null), v.teamFromIdols([]).verified]));")
        self.assertEqual(out, [True, False, ["S1"], "blocked", "ok", "unknown", "ok", "unknown", "blocked", "unknown", False])

    def test_halo_picks_exclude_team_and_follow_matcher_order(self):
        out = run_node(
            "const team = v.teamFromDemo(fx.demo_team); const r = v.haloPicks(books, v.templateList('both'), team);\n"
            "console.log(JSON.stringify({ picks: r.picks.map((p) => [p.book.idol, p.usability, p.hits.length]),\n"
            " blocked: [...new Set(r.blocked.map((b) => b.idol))].sort(), missing: r.missing.map((m) => m.name),\n"
            " none: v.haloPicks(books, v.templateList('both'), null).picks.map((p) => p.usability),\n"
            " noneHits: v.haloPicks(books, v.templateList('both'), null).picks.map((p) => p.hits.length) }));")
        self.assertEqual(len(out["picks"]), 3)
        self.assertTrue(all(u == "ok" for _, u, _ in out["picks"]))
        team = {"浅倉透", "杜野凛世", "月岡恋鐘", "芹沢あさひ", "黛冬優子", "白瀬咲耶"}
        self.assertEqual(set(out["blocked"]), team)
        self.assertFalse({idol for idol, _, _ in out["picks"]} & team)
        self.assertEqual([h for _, _, h in out["picks"]], sorted([h for _, _, h in out["picks"]], reverse=True))
        self.assertIn("アピールUP(思い出低)", out["missing"])
        # Without a team only the 283 book can be 可带; idol books stay 需确认 (never assumed usable).
        self.assertTrue(set(out["none"]) <= {"ok", "unknown"})
        # 2026-10-24: an unverified team does not rank the 283 book above stronger 需确认 books; the matcher order holds
        self.assertEqual(out["noneHits"], sorted(out["noneHits"], reverse=True))

    def test_step_trio_coverage_and_copies(self):
        data = {"books": [bk("a", "A", [("X", 5)]), bk("b", "B", [("Y", 3)], count=2), bk("c", "C", [("Z", 1)]),
                          bk("t", "T", [("X", 6), ("Y", 6), ("Z", 6)])],
                "wanted": [{"name": "X", "purpose": "step", "min_level": 5}, {"name": "Y", "purpose": "step"},
                           {"name": "Z", "purpose": "step", "min_level": 2}, {"name": "W", "purpose": "halo"}]}
        out = run_node(
            "const bs = d.cleanBooks({ books: data.books }); const team = v.teamFromIdols(['T']);\n"
            "const r = v.stepTrio(bs, data.wanted, team);\n"
            "console.log(JSON.stringify({ slots: r.slots.map((s) => s && s.book.title), covered: r.covered.map((i) => i.name),\n"
            " gaps: r.gaps.map((i) => i.name), items: r.items.map((i) => i.name), blocked: r.blocked.map((b) => b.title) }));", data)
        self.assertEqual(out["items"], ["X", "Y", "Z"])
        self.assertEqual(sorted(t for t in out["slots"] if t), ["a", "b", "b"])  # identical copies may fill two slots
        self.assertEqual(out["covered"], ["X", "Y"])
        self.assertEqual(out["gaps"], ["Z"])  # Z Lv1 < min 2 and the only higher holder is a team idol
        self.assertEqual(out["blocked"], ["t"])

    def test_grid_location_exact_only_with_proven_grid(self):
        values = [1020, 1000, 1000] + [900 - i for i in range(16)]           # 19 tiles of a scanned 評価値 grid
        data = {"books": [bk("h", "A", [("X", 1)], ev=1020), bk("t1", "B", [("X", 1)], ev=1000), bk("t2", "C", [("Y", 1)], ev=1000),
                          bk("n", "D", [("X", 1)], ev=None), bk("f15", "E", [("Q", 4)], ev=885), bk("z", "F", [("Q", 1)], ev=777)],
                "proven": {"owned_total": 19, "grid": {"complete": True, "values": values, "owned_total": 19}},
                "short": {"owned_total": 20, "grid": {"complete": True, "values": values, "owned_total": 20}},
                "flagged": {"owned_total": 19, "grid": {"complete": False, "values": values, "owned_total": 19}}}
        out = run_node(
            "const bs = d.cleanBooks({ books: data.books }); const by = Object.fromEntries(bs.map((b) => [b.title, b]));\n"
            "const s = (k) => d.cleanScan(data[k]);\n"
            "console.log(JSON.stringify([v.gridLocation(by.h, bs, { owned_total: 19 }), v.gridLocation(by.h, bs, s('short')),\n"
            " v.gridLocation(by.t1, bs, s('flagged')), v.gridLocation(by.f15, [], s('proven')),\n"
            " v.gridLocation(by.h, bs, s('proven'), 1), v.gridLocation(by.n, bs, s('proven')), v.gridLocation(by.z, bs, s('proven')),\n"
            " s('short').grid.complete, s('proven').grid.complete]));", data)
        self.assertTrue(out[0]["approx"])  # no grid record: the old 「all read books have a value」 rule no longer suffices
        self.assertIn("1020 附近", out[0]["text"])
        self.assertNotRegex(out[0]["text"], r"第\d+页")
        self.assertTrue(out[1]["approx"])  # 19 recorded values but 所持数 20: not proven (cleanScan drops complete)
        self.assertIn("19/20", out[1]["why"])
        self.assertTrue(out[2]["approx"])  # the scan itself flagged the grid partial
        self.assertEqual(out[3]["text"], "第2页 第1行 第4列")  # counted on the grid values, not on the read books
        self.assertTrue(out[3]["exact"])
        self.assertIn("1–2列", out[4]["text"])  # one book received after the scan may push it back by one
        self.assertIn("扫描后新增 1 本", out[4]["notes"][0])
        self.assertTrue(out[5]["approx"])  # 評価値 not read for this book
        self.assertTrue(out[6]["approx"])  # value not in the scanned grid (changed since)
        self.assertEqual(out[7:], [False, True])
        ties = run_node("const bs = d.cleanBooks({ books: data.books });\n"
                        "console.log(JSON.stringify(v.gridLocation(bs[1], bs, d.cleanScan(data.proven))));", data)
        self.assertEqual(ties["text"], "第1页 第1行 第2–3列")
        self.assertFalse(ties["exact"])
        self.assertIn("同分顺序未知", ties["notes"][0])

    def test_load_reads_pending_books_and_grid_from_the_data_module(self):
        out = run_node(
            "const loaded = await d.loadKnowhowData({ demo: true }); const x = await v.loadExtras({ demo: true });\n"
            "const real = await v.loadExtras({ demo: false });\n"
            "console.log(JSON.stringify({ pending: loaded.pending.length, grid: loaded.scan.grid.complete, n: loaded.scan.grid.values.length,\n"
            " team: Boolean(x.demoTeam), pendingInExtras: 'pending' in x, real }));")
        self.assertEqual(out, {"pending": 2, "grid": True, "n": 39, "team": True, "pendingInExtras": False,
                               "real": {"demoTeam": None}})

    def test_search_recipe_does_not_claim_name_search(self):
        out = run_node(
            "const b = books.find((x) => x.title === '【示例7】樋口円香'); const loc = v.gridLocation(b, books, fx.scan, 0);\n"
            "const hits = v.haloPicks(books, v.templateList('both'), v.teamFromDemo(fx.demo_team)).picks[0].hits;\n"
            "console.log(JSON.stringify(v.searchRecipe(b, loc, hits)));")
        self.assertIn("ノウハウ評価値順，▼降順", out)
        self.assertIn("アイドル：樋口円香", out)
        self.assertIn("类别 → 效果组", out)
        self.assertIn("不会替你点击", out)
        self.assertNotRegex(out, r"詳細検索[^\n]*选「")

    def test_review_facts_are_facts_not_sale_advice(self):
        data = {"books": [bk("a", "A", [("X", 2), ("Y", 1)]), bk("b", "A", [("X", 3), ("Y", 1), ("Z", 1)]),
                          bk("c", "A", [("X", 1)], protected=True), bk("d", "A", [("X", 1)], marked=True),
                          bk("e", "A", [("X", 1)], total=3), bk("f", "A", [("W", 1)]), bk("g", "B", [("Q", 1)], count=2),
                          bk("h", "C", [("X", 1)])],
                "wanted": [{"name": "W"}]}
        out = run_node(
            "const bs = d.cleanBooks({ books: data.books }); const r = v.reviewFacts(bs, data.wanted);\n"
            "console.log(JSON.stringify({ facts: r.facts.map((f) => [f.book.title, f.by.map((b) => b.title), f.copies]),\n"
            " excluded: r.excluded, note: v.REVIEW_NOTE }));", data)
        self.assertEqual(out["facts"], [["a", ["b"], 0], ["g", [], 1]])
        self.assertEqual(out["excluded"], {"protected": 1, "marked": 1, "incomplete": 1, "wanted": 1})
        self.assertEqual(out["note"], "是否出售由你决定，Maa 不会出售")

    def test_pending_notes_compare_with_library(self):
        out = run_node("console.log(JSON.stringify(v.pendingNotes(pending, books, v.templateList('both'))"
                       ".map((p) => [p.notes.map((n) => [n.text, n.wanted]), p.notHigher])));")
        self.assertEqual(out[0][0][0], ["Dance上限++ Lv5，书库最高 Lv4", True])
        self.assertEqual(out[1], [[], 3])

    def test_wanted_hits_need_the_min_level_and_summary_counts_protected_new_books(self):
        data = {"books": [bk("A", "灯織", [("Vocal上限+", 5), ("Dance上限+", 2)])],
                "pending": [bk("B", "めぐる", [("Vocal上限+", 3)], protected=True, first_seen="2026-10-11T22:00:00"),
                            bk("C", "真乃", [("Dance上限+", 4)], protected=False)]}
        wanted = [{"name": "Vocal上限+", "min_level": 4}, {"name": "Dance上限+", "min_level": 4}]
        out = run_node("const books2 = d.cleanBooks({ books: data.books }); const pend = d.cleanBooks({ books: data.pending });"
                       "console.log(JSON.stringify({ hits: d.wantedHits(books2[0], data.wanted).map((m) => [m.name, m.level]),"
                       " low: d.wantedHits(pend[0], data.wanted), seen: pend.map((b) => b.seen), prot: pend.map((b) => b.protected),"
                       " sum: d.knowhowSummary({ books: books2, pending: pend }, data.wanted) }));",
                       {**data, "wanted": wanted})
        self.assertEqual(out["hits"], [["Vocal上限+", 5]])
        self.assertEqual(out["low"], [])                       # Lv3 < min Lv4: not a hit
        self.assertEqual(out["seen"], ["2026-10-11T22:00:00", None])
        self.assertEqual(out["prot"], [True, False])
        self.assertEqual(out["sum"], {"books": 1, "pending": 2, "wantedBooks": 2, "pendingWanted": 1, "pendingProtected": 1})

    def test_coverage_map_and_scan_status(self):
        out = run_node(
            "const m = v.coverageMap(books, v.templateList('both'));\n"
            "const row = m.rows.find((r) => r.item.name === 'パーフェクトリィ');\n"
            "console.log(JSON.stringify({ first: m.idols[0], gap: m.rows.find((r) => r.item.name === 'アピールUP(思い出低)').met,\n"
            " cell: row.cells['樋口円香'].level, status: v.scanStatus(books, fx.scan, pending.length),\n"
            " unknown: v.scanStatus(books, null).text, full: v.scanStatus(books.filter((b) => b.complete), { owned_total: 32 }) }));")
        self.assertEqual(out["gap"], 0)
        self.assertEqual(out["cell"], 5)
        self.assertEqual(out["status"]["text"], "已读 33/39 本详情，结果仅覆盖已读部分")
        self.assertTrue(out["status"]["partial"])
        self.assertIn("所持总数未读取", out["unknown"])
        self.assertFalse(out["full"]["partial"])

    def test_library_query_and_sort(self):
        out = run_node(
            "const w = v.templateList('both'); const t = (rows) => rows.map((r) => r.book.title);\n"
            "console.log(JSON.stringify({ lv: t(v.libraryRows(books, w, { query: 'Lv4 Dance上限' })),\n"
            " joined: t(v.libraryRows(books, w, { query: 'lv4dance上限 樋口' })), idol: t(v.libraryRows(books, w, { query: '円香' })).length,\n"
            " ev: v.libraryRows(books, w, { sort: 'eval' }).map((r) => r.book.eval_value).slice(0, 3),\n"
            " parsed: v.parseQuery('Lv5 Vocal上限') }));")
        self.assertEqual(out["joined"], ["【示例7】樋口円香"])
        self.assertIn("【示例7】樋口円香", out["lv"])
        self.assertEqual(out["idol"], 4)
        self.assertEqual(out["ev"], [1020, 1010, 1010])
        self.assertEqual(out["parsed"], [{"level": 5, "text": "vocal上限"}])

    def test_library_filters_and_facets(self):
        # 2026-10-13 书库: status chips, idol, one ノウハウ at ≥ Lv; facets list idols and ノウハウ names with counts.
        data = [bk("A", "樋口円香", [("Vocal上限UP", 5), ("思い出+", 3)], protected=True, complete=True),
                bk("B", "福丸小糸", [("Vocal上限UP", 2)], complete=True),
                bk("C", "福丸小糸", [("思い出+", 5)], complete=False)]
        out = run_node(
            "const bs = d.cleanBooks({ books: data }); const rows = v.libraryRows(bs, []);\n"
            "const t = (o) => v.filterLibrary(rows, o).map((r) => r.book.title).sort();\n"
            "console.log(JSON.stringify({ prot: t({ status: 'protected' }), idol: t({ idol: '福丸小糸' }),\n"
            " lv: t({ entry: 'Vocal上限UP', minLevel: 3 }), all: t({}), facets: v.libraryFacets(bs) }));", data)
        self.assertEqual(out["prot"], ["A"])
        self.assertEqual(out["idol"], ["B", "C"])
        self.assertEqual(out["lv"], ["A"])
        self.assertEqual(out["all"], ["A", "B", "C"])
        self.assertEqual(out["facets"]["idols"], [["樋口円香", 1], ["福丸小糸", 2]])
        self.assertEqual(sorted(out["facets"]["entries"]), [["Vocal上限UP", 2], ["思い出+", 2]])

    def test_templates_are_labelled_and_sanitised(self):
        out = run_node("console.log(JSON.stringify([v.templateList('halo'), v.templateList('step'), v.templateList('both')]));")
        halo, step, both = out
        self.assertTrue(all(i["purpose"] == "halo" for i in halo))
        self.assertTrue(all(i["purpose"] == "step" for i in step))
        self.assertEqual(len(both), len(halo) + len(step) - 2)  # 思い出(ノウハウ) and エキシビションマッチ merged to "any"
        self.assertEqual({i["name"] for i in both if i["purpose"] == "any"}, {"思い出(ノウハウ)", "エキシビションマッチ"})


class KnowhowPanelStaticTests(unittest.TestCase):
    def test_panel_never_sells_and_keeps_future_options_disabled(self):
        panel = (UI / "components" / "knowhow-panel.js").read_text(encoding="utf-8")
        views = (UI / "components" / "knowhow-views.js").read_text(encoding="utf-8")
        self.assertNotIn("SELL_TITLE", panel)
        self.assertNotIn("sell_candidates", panel)
        self.assertNotIn("extras.pending", panel)    # pending books come from the real data module, not the fixture path
        self.assertNotIn("fixtures/knowhow-demo.js", views[views.index("export async function loadExtras"):].split("if (!demo)")[0])
        self.assertIn("this.scanControl()", panel)            # 「扫描ノウハウ」 (knowhow-scan.js)
        self.assertNotIn("kh-k4", panel)    # 2026-10-24: no disabled 打マーク option (Maa never marks books)
        self.assertIn("RECOMMENDED_ITEMS", panel)          # 添加条目 = the researched recommended entries
        flow = (UI / "components" / "knowhow-workflow.js").read_text(encoding="utf-8")
        for text in (panel, flow):
            self.assertNotIn("selectEl(", text)               # 2026-10-24: pills and the inline book picker, no dropdowns
        self.assertIn("REVIEW_NOTE", panel)
        for text in (panel, views):
            for forbidden in ("bridge.request", ".request(", "startRun", "setOption", "run.start", "allow_spend"):
                self.assertNotIn(forbidden, text)
        # setTab = navigation only (编队未设置 → Produce page)
        self.assertEqual(set(re.findall(r"actions\??\.(\w+)", panel)), {"setPref", "setTab", "knowhowScanStart", "knowhowScanStop"})
        self.assertNotRegex(views, r"\bdocument\.|\bwindow\.")  # pure module


if __name__ == "__main__":
    unittest.main()


class KnowhowCompactViewTests(unittest.TestCase):
    def test_group_collapse_and_short_names(self):
        books = [bk("A", "樋口円香", [("思い出(ノウハウ)+", 5), ("歌姫", 3)], ev=900),
                 dict(bk("A", "樋口円香", [("歌姫", 3), ("思い出(ノウハウ)+", 5)], ev=900, count=2), fingerprint="b-A2"),
                 bk("B", "福丸小糸", [("歌姫", 1)]),
                 bk("C", "福丸小糸", [("歌姫", 2)], total=3)]
        out = run_node(
            "const bs = d.cleanBooks({ books: data });\n"
            "const rows = v.collapseIdentical(bs.map((book) => ({ book })));\n"
            "const cls = (b) => (b.title === 'A' ? 'completed' : b.title === 'B' ? 'one' : 'zzz');\n"
            "const groups = v.groupLibrary(rows, cls);\n"
            "console.log(JSON.stringify({ copies: rows.map((r) => r.copies), groups: groups.map((g) => [g.key, g.kinds, g.copies]),\n"
            "  open: v.defaultOpenGroup(groups), openMain: v.defaultOpenGroup(groups, 'b-B'),\n"
            "  names: [v.shortName('思い出(ノウハウ)+'), v.shortName('歌姫(G.R.A.D.)'), v.shortName('エキシビションマッチ強化アピール'), v.shortName('アピールUP(思い出高)'), v.shortName('アピールUP(思い出低)')] }));",
            books)
        self.assertEqual(out["copies"], [3, 1, 1])  # identical content collapses ×N; incomplete books never merge
        self.assertEqual(out["groups"], [["completed", 1, 3], ["one", 1, 1], ["other", 1, 1]])
        self.assertEqual(out["open"], "completed")
        self.assertEqual(out["openMain"], "one")
        self.assertEqual(out["names"][:2], ["思い出+", "歌姫"])
        self.assertEqual(out["names"][3:], ["アピUP(高)", "アピUP(低)"])   # high / low stay apart when shortened
        self.assertTrue(out["names"][2].startswith("エキシビ") and out["names"][2].endswith("…"))

    def test_auto_step_book_entries(self):
        books = [{"title": "【ポシェットの中には】 福丸小糸", "stats": {"vo": 110, "da": 30, "vi": 30, "me": 40}, "eval_value": 1010},
                 {"title": "【A】x", "stats": {"vo": 1, "da": 1, "vi": 1}, "eval_value": 10},
                 {"title": "【A】x", "stats": {"vo": 1, "da": 1, "vi": 1, "me": 1}, "eval_value": None},
                 {"title": "a/b", "stats": {"vo": 1, "da": 1, "vi": 1, "me": 1}, "eval_value": 10},
                 {"title": "【ＡＢ】x", "stats": {"vo": 1, "da": 1, "vi": 1, "me": 2001}, "eval_value": 10}]
        out = run_node("console.log(JSON.stringify(data.map(v.autoStepBook)));", books)
        # prefs.auto_step_books (Weekly Produce 带书「ノウハウ页指定」): title NFKC without spaces, all of Vo/Da/Vi/Me/評価値 read
        self.assertEqual(out[0], {"title": "【ポシェットの中には】福丸小糸", "vo": 110, "da": 30, "vi": 30, "me": 40, "eval": 1010})
        self.assertEqual(out[1:], [None, None, None, None])
        panel = (UI / "components" / "knowhow-panel.js").read_text(encoding="utf-8")
        self.assertIn('actions.setPref("auto_step_books", entries)', panel)
        self.assertIn('"设为 Auto 带书"', panel)
        self.assertIn("loc.position > PER_PAGE", panel)   # Auto only looks at the picker's first screen
