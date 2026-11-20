"""ノウハウ rule engine + 製本 plan (ui/components/knowhow-rules.js, knowhow-plan.js): entry parsing, Target units and
validation (slot limits, conflicts, 转记, Lv6), STEP trio analysis (level conflicts, duplicates, noise, loss, overflow,
team restriction), recommendations per mode, stage detection, result comparison and decisions, Target change reuse and
plan migration from the old wanted list. Offline, synthetic data only; no probability anywhere."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def run_node(body, data=None):
    script = ("const data = JSON.parse(process.argv[1]);\n"
              "const base = 'file:///' + process.cwd().replace(/\\\\/g, '/') + '/';\n"
              "const d = await import(new URL('ui/components/knowhow-data.js', base));\n"
              "const r = await import(new URL('ui/components/knowhow-rules.js', base));\n"
              "const p = await import(new URL('ui/components/knowhow-plan.js', base));\n"
              "const B = (list) => d.cleanBooks({ books: list });\n"
              + body)
    result = subprocess.run([NODE, "--input-type=module", "-e", script, json.dumps(data, ensure_ascii=False)],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def bk(title, idol, rows, count=1, **extra):
    return {"fingerprint": f"b-{title}", "title": title, "idol": idol, "stats": {"vo": 1, "da": 1, "vi": 1, "me": 1},
            "rows": [{"name": n, "level": lv} for n, lv in rows], "knowhow_count": len(rows), "count": count, **extra}


def item(name, role="required", min_level=1, ideal=None, **extra):
    return {"name": name, "role": role, "min_level": min_level, "ideal_level": ideal or min_level, **extra}


def target(items, **extra):
    return {"id": "t1", "name": "T", "scenario": "step", "strategy": "balanced", "items": items, **extra}


@unittest.skipUnless(NODE, "node not installed")
class KnowhowRuleTests(unittest.TestCase):
    def test_entry_parsing_tiers_heads_transfer_and_groups(self):
        out = run_node("console.log(JSON.stringify(['Vocal上限UP+++', 'VocalUP', 'WING優勝', 'エキシビションマッチ++',"
                       " 'アピールUP(思い出高&低)(G.R.A.D.)', 'ノービス目標達成(Da)', 'Vocal上限+++', 'ファンの声援+', '歌姫楽宴(Vo)',"
                       " 'LandingPoint(Vo)', 'プロダンサー'].map(r.parseEntry)));")
        head, up, wing, ex, grad, goal, grad_cap, fans, song, landing, dancer = out
        self.assertEqual((head["kind"], head["attr"], head["tier"], head["headType"]), ("head", "vo", 3, "上限UP"))
        self.assertEqual((up["kind"], up["tier"]), ("head", 0))
        self.assertEqual(grad_cap["kind"], "other")         # G.R.A.D. 「Vocal上限+++」 is not a head (2026-10-24 research)
        self.assertEqual(fans["exclusive"], "fans")
        self.assertEqual(song["exclusive"], "song:歌姫楽宴")
        self.assertNotEqual(song["source"], 'say "Halo"')   # the W.I.N.G. audition song is not the Halo 歌姫
        self.assertEqual(dancer["source"], 'say "Halo"')
        self.assertFalse(landing["transferable"])
        self.assertFalse(wing["transferable"])
        self.assertTrue(grad["transferable"])
        self.assertEqual(grad["source"], "G.R.A.D.")
        self.assertEqual((ex["exclusive"], ex["tier"]), ("exhibition", 2))
        self.assertEqual((goal["exclusive"], goal["attr"]), ("goal", "da"))

    def test_rules_are_settled_fixed_and_have_no_probability(self):
        out = run_node("console.log(JSON.stringify({ rules: r.RULES, vals: r.ruleValues({ exclusive_groups: false, max_entries: 99, step_consumes: true }) }));")
        statuses = {x["key"]: x["status"] for x in out["rules"]}
        self.assertEqual(statuses["probability"], "无资料")
        self.assertEqual(statuses["step_consumes"], "否定")
        self.assertEqual(statuses["halo_discount"], "确认")
        self.assertEqual(set(statuses.values()) - {"确认", "部分确认", "否定", "无资料"}, set())
        self.assertTrue(all(x.get("source") for x in out["rules"]))
        self.assertFalse(any(x.get("toggle") for x in out["rules"]))         # 2026-10-24: no user switches
        self.assertIsNone(next(x for x in out["rules"] if x["key"] == "probability")["value"])
        self.assertTrue(out["vals"]["exclusive_groups"])                     # arguments are ignored
        self.assertFalse(out["vals"]["step_consumes"])
        self.assertEqual(out["vals"]["max_entries"], 21)

    def test_target_validation_slots_conflicts_transfer_and_lv6(self):
        many = [item(f"A{i}") for i in range(21)]
        data = {"over": target(many), "pref": target([item(f"A{i}") for i in range(15)] + [item(f"P{i}", "preferred") for i in range(6)]),
                "mix": target([item("X"), item("X", "excluded"), item("思い出(ノウハウ)+", match="exact"), item("思い出(ノウハウ)++"),
                               item("WING優勝"), item("Y", min_level=6)]),
                "books": [bk("a", "A", [("X", 3)])]}
        out = run_node(
            "const codes = (t, b) => r.validateTarget(r.cleanTarget(t), b ? B(b) : null).map((i) => [i.level, i.code]);\n"
            "console.log(JSON.stringify({ over: codes(data.over), pref: codes(data.pref), mix: codes(data.mix, data.books),\n"
            " halo: codes({ ...data.mix, scenario: 'halo' }), off: r.validateTarget(r.cleanTarget(data.mix), null, { ...r.ruleValues(), exclusive_groups: false }).map((i) => i.code) }));", data)
        self.assertIn(["error", "slots_required"], out["over"])      # 21 required + head > 21 slots
        self.assertIn(["warning", "slots_preferred"], out["pref"])
        mix = dict((c, lv) for lv, c in out["mix"])
        self.assertEqual(mix["excluded_conflict"], "error")
        self.assertEqual(mix["exclusive"], "warning")               # 思い出 +/++ same group
        self.assertEqual(mix["non_transferable"], "warning")        # WING優勝 cannot be concentrated in STEP
        self.assertEqual(mix["lv6"], "info")
        self.assertEqual(mix["missing"], "warning")
        self.assertNotIn("non_transferable", [c for _, c in out["halo"]])  # Halo brings the original book
        self.assertNotIn("exclusive", out["off"])                    # the engine honours an explicit rules object

    def test_units_groups_and_strategy(self):
        t = target([item("歌姫", group="g", min_level=3, ideal=5), item("トップモデル", group="g", ideal=3), item("Z", "preferred", 2, 5),
                    item("N", "excluded")])
        data = {"t": t, "book": bk("a", "A", [("トップモデル", 1), ("Z", 3), ("Q", 2)])}
        out = run_node(
            "const t = r.cleanTarget(data.t); const b = B([data.book])[0];\n"
            "const ev = (s) => r.evaluateBook(b, { ...t, strategy: s });\n"
            "console.log(JSON.stringify({ units: r.targetUnits(t).map((u) => [u.label, u.role, u.items.length]),\n"
            " q: ev('quick').progress, bal: ev('balanced').progress, opt: ev('optimized').progress, noise: ev('balanced').noise,\n"
            " states: ev('balanced').status.map((s) => s.state) }));", data)
        self.assertEqual(out["units"], [["g", "required", 2], ["Z", "preferred", 1], ["N", "excluded", 1]])
        self.assertEqual(out["q"], {"done": 1, "total": 1})           # トップモデル Lv1 meets its own min 1 (substitute)
        self.assertEqual(out["bal"], {"done": 2, "total": 2})
        self.assertEqual(out["opt"], {"done": 0, "total": 2})         # neither reaches its ideal Lv
        self.assertEqual(out["noise"], ["Q"])
        self.assertEqual(out["states"], ["met", "met", "clear"])

    def test_trio_conflicts_level_interference_loss_and_team(self):
        t = target([item("X", min_level=3), item("Y"), item("Z"), item("N", "excluded")])
        data = {"t": t, "books": [bk("main", "M", [("VocalUP", 1), ("X", 5), ("Y", 2)]), bk("low", "L", [("X", 1), ("Z", 4), ("N", 2)]),
                                   bk("team", "T", [("Z", 5)]), bk("one", "O", [("Q", 1)])]}
        out = run_node(
            "const t = r.cleanTarget(data.t); const bs = B(data.books); const by = Object.fromEntries(bs.map((b) => [b.title, b]));\n"
            "const team = { idols: ['T'], verified: true };\n"
            "const a = r.analyzeTrio([by.main, by.low, by.team], t, { mainBook: by.main, team, books: bs });\n"
            "const dup = r.analyzeTrio([by.main, by.main, by.low], t, { mainBook: by.main, team, books: bs });\n"
            "const off = r.analyzeTrio([by.main, by.low, by.one], t, { mainBook: by.main, team, rules: { ...r.ruleValues(), low_level_interference: false } });\n"
            "const codes = (x) => x.conflicts.map((c) => c.code);\n"
            "console.log(JSON.stringify({ codes: codes(a), valid: a.valid, low: a.lowLevel, loss: a.loss, excl: a.excluded, fresh: a.fresh,\n"
            " dup: codes(dup), offLow: off.lowLevel, reach: a.reach, score: typeof a.score }));", data)
        self.assertIn("blocked", out["codes"])          # team idol T
        self.assertIn("excluded", out["codes"])
        self.assertFalse(out["valid"])
        self.assertEqual(out["low"], ["X"])              # X Lv5 on main + Lv1 copy: the result may keep the Lv1
        self.assertEqual(out["loss"], ["X", "Y"])        # on main, no second copy at ≥ min Lv (X Lv1 does not count)
        self.assertEqual(out["excl"], ["N"])
        self.assertEqual(out["fresh"], ["Z"])
        self.assertIn("copies", out["dup"])              # one physical main book used twice
        self.assertEqual(out["offLow"], [])
        self.assertEqual(out["score"], "number")

    def test_overflow_pushout_warning(self):
        rows = lambda p: [(f"{p}{i}", 1) for i in range(10)]
        data = {"t": target([item("A0")]), "books": [bk("a", "A", rows("A")), bk("b", "B", rows("B")), bk("c", "C", rows("C"))]}
        out = run_node(
            "const t = r.cleanTarget(data.t); const bs = B(data.books);\n"
            "const on = r.analyzeTrio(bs, t, {}); const off = r.analyzeTrio(bs, t, { rules: { ...r.ruleValues(), overflow_pushout: false } });\n"
            "console.log(JSON.stringify([on.conflicts.map((c) => c.code), off.conflicts.map((c) => c.code)]));", data)
        self.assertIn("overflow", out[0])
        self.assertNotIn("overflow", out[1])

    def test_recommendation_modes_use_more_than_coverage(self):
        t = target([item("X", min_level=3), item("Y"), item("Z"), item("W", "preferred"), item("N", "excluded")])
        data = {"t": t, "books": [
            bk("main", "M", [("VocalUP", 1), ("X", 5)]),
            bk("dirty", "D", [("Y", 5), ("Z", 5), ("W", 5), ("N", 1)] + [(f"n{i}", 1) for i in range(8)]),
            bk("cleanY", "C", [("Y", 4)]), bk("cleanZ", "E", [("Z", 4)]), bk("y2", "F", [("Y", 3), ("Z", 2)]),
            bk("blocked", "T", [("Y", 5), ("Z", 5), ("W", 5)])]}
        out = run_node(
            "const t = r.cleanTarget(data.t); const bs = B(data.books); const main = bs[0];\n"
            "const team = { idols: ['T'], verified: true };\n"
            "const rec = r.recommendStep(bs, t, { mainBook: main, team, mode: 'safe' });\n"
            "const names = (x) => x.trio.map((b) => b.title);\n"
            "console.log(JSON.stringify({ best: names(rec.best), modes: rec.alternatives.map((a) => [a.mode, names(a), a.valid]),\n"
            " anchored: rec.anchored, parts: Object.keys(rec.best.parts) }));", data)
        self.assertTrue(out["anchored"])
        self.assertEqual(out["best"][0], "main")
        self.assertNotIn("blocked", sum((m[1] for m in out["modes"]), []))
        self.assertNotIn("dirty", out["best"])           # broadest coverage but excluded N + noise: safe avoids it
        self.assertTrue(all(valid for _, _, valid in out["modes"]))
        for key in ("required", "fresh", "redundant", "excluded", "noise", "lowLevel", "loss", "unique", "reuse"):
            self.assertIn(key, out["parts"])

    def test_route_covers_merges_and_keeps_the_main_book_last(self):
        names = ["A", "B", "C", "D", "E", "F", "G"]
        t = target([item(n) for n in names] + [item("Z"), item("W"), item("O", "optional"), item("N", "excluded")])
        data = {"t": t, "books": [
            bk("main", "M", [("VocalUP", 1), ("A", 3)]),
            *[bk(n.lower(), f"I{n}", [(n, 3)]) for n in names[1:]],
            bk("bc", "BC", [("B", 2), ("C", 2)]),                       # covers two units at once: picked before b / c
            bk("dirty", "DI", [("D", 5), ("E", 5), ("N", 1)]),           # excluded entry: loses the tie to clean books
            bk("wing", "WG", [("W", 1)]),                               # W only on a book whose entry is not transferable
            bk("team", "T", [("F", 5), ("G", 5)])]}
        data["t"]["items"][8] = item("WING優勝")                        # W = a non-transferable entry
        data["books"][-2] = bk("wing", "WG", [("WING優勝", 1)])
        data["all"] = bk("all", "Q", [("VocalUP", 1)] + [(n, 2) for n in [*names, "Z", "WING優勝"]])
        out = run_node(
            "const t = r.cleanTarget(data.t); const bs = B(data.books); const by = Object.fromEntries(bs.map((b) => [b.title, b]));\n"
            "const team = { idols: ['T'], verified: true };\n"
            "const show = (p) => ({ absent: p.absent.map((u) => u.label), direct: p.direct.map((u) => u.label), done: p.done,\n"
            "  base: p.base?.title || null, runs: p.runs.map((x) => ({ final: x.final, now: x.now,\n"
            "  inputs: x.inputs.map((i) => i.book ? (i.filler ? '+' : '') + i.book.title : `#${i.run}`), gives: x.gives.map((u) => u.label),\n"
            "  risks: x.risks.map((k) => k.code) })) });\n"
            "const full = r.planRoute(bs, t, { mainBook: by.main, team });\n"
            "const k = (n) => r.planRoute([by.main, ...['b', 'c', 'd', 'e', 'f', 'g'].slice(0, n).map((x) => by[x])], t, { mainBook: by.main, team });\n"
            "const nomain = r.planRoute([by.b, by.c, by.d, by.e], t, { team });\n"
            "const done = r.planRoute([...bs, ...B([data.all])], t, { mainBook: by.main, team });\n"
            "const one = r.planRoute([by.main, by.wing], t, { mainBook: by.main, team });\n"
            "console.log(JSON.stringify({ full: show(full), k: [1, 2, 3, 4, 5].map((n) => show(k(n)).runs.length), k3: show(k(3)), k5: show(k(5)),\n"
            " nomain: show(nomain), done: show(done), one: show(one) }));", data)
        full = out["full"]
        self.assertEqual(full["absent"], ["Z"])                          # nobody holds Z: collect first
        self.assertEqual(full["direct"], ["WING優勝"])                   # only non-transferable: bring the book itself
        self.assertNotIn("O", sum((r["gives"] for r in full["runs"]), []))   # optional units are not part of the route
        leaves = [i for r in full["runs"] for i in r["inputs"] if not i.startswith(("#", "+"))]
        self.assertIn("bc", leaves)
        self.assertNotIn("team", " ".join(leaves))                       # same idol as the team: never brought
        self.assertNotIn("dirty", leaves)
        self.assertEqual(leaves.count("main"), 1)
        final = full["runs"][-1]
        self.assertTrue(final["final"])
        self.assertEqual(final["inputs"][0], "main")                     # the main book waits for the final run
        self.assertTrue(all(not r["final"] for r in full["runs"][:-1]))
        self.assertTrue(full["runs"][0]["now"])
        self.assertTrue(any(i.startswith("#") for i in final["inputs"]))
        self.assertEqual(sorted(sum((r["gives"] for r in full["runs"][-1:]), [])), sorted(["A", "B", "C", "D", "E", "F", "G"]))
        # main + k other leaves: one run up to 2, two up to 4, three for 5
        self.assertEqual(out["k"], [1, 1, 2, 2, 3])
        self.assertEqual(out["k3"]["runs"][0]["inputs"][:2], ["b", "c"])   # 3 leaves for 2 slots: merge 2 (+ a filler)
        self.assertTrue(out["k3"]["runs"][0]["inputs"][2].startswith("+"))
        self.assertIn("single", out["k3"]["runs"][0]["risks"])
        self.assertEqual(len(out["nomain"]["runs"]), 2)                  # no main: 4 leaves → 2 merged + 2 → final
        self.assertTrue(out["done"]["done"])
        self.assertEqual(out["done"]["runs"], [])
        self.assertEqual((out["one"]["runs"], out["one"]["base"]), ([], "main"))   # nothing to condense: collect first

    def test_route_fillers_repeat_lonely_units_and_flag_risks(self):
        t = target([item("A"), item("B")])
        rows = lambda p: [(f"{p}{i}", 1) for i in range(12)]
        data = {"t": t, "books": [
            bk("main", "M", [("VocalUP", 1), ("A", 3)] + rows("m")), bk("b", "BB", [("B", 3)] + rows("b")),
            bk("b2", "B2", [("B", 2)]), bk("junk", "J", [("Q", 1), ("R", 1)])]}
        out = run_node(
            "const t = r.cleanTarget(data.t); const bs = B(data.books); const by = Object.fromEntries(bs.map((b) => [b.title, b]));\n"
            "const plan = r.planRoute(bs, t, { mainBook: by.main });\n"
            "const short = r.planRoute([by.main, by.b], t, { mainBook: by.main });\n"
            "const run = plan.runs[0];\n"
            "console.log(JSON.stringify({ inputs: run.inputs.map((i) => (i.filler ? '+' : '') + i.book.title), risks: run.risks.map((k) => k.code),\n"
            " short: short.runs[0].risks.map((k) => k.code) }));", data)
        self.assertEqual(out["inputs"], ["main", "b2", "+b"])            # b2 has less noise; the filler repeats B, not junk
        self.assertIn("overflow", out["risks"])                           # 12 + 12 + heads > 21
        self.assertNotIn("unknown", out["risks"])                         # no verified team: said once on the 编队 line, not per run
        self.assertIn("count", out["short"])                              # only two books in the library

    def test_intermediate_result_keeps_the_main_book(self):
        t = target([item("X", "preferred"), item("Y"), item("Z")])
        data = {"t": t, "books": [bk("main", "M", [("VocalUP", 1), ("X", 3)]), bk("mid", "I", [("Y", 3), ("Z", 3)]),
                                   bk("all", "A", [("X", 3), ("Y", 3), ("Z", 3)])]}
        out = run_node(
            "const t = r.cleanTarget(data.t); const [main, mid, all] = B(data.books);\n"
            "const c = (b, o) => r.compareResult(b, main, t, o).decision;\n"
            "console.log(JSON.stringify([c(mid, {}), c(mid, { intermediate: true }), c(all, { intermediate: true })]));", data)
        self.assertEqual(out, ["main", "backup", "main"])     # more progress, but made without the main: keep it for the next run

    def test_new_book_value_against_main_book_and_library(self):
        t = target([item("X", min_level=3), item("Y"), item("Z")])
        data = {"t": t, "books": [bk("main", "M", [("VocalUP", 1), ("X", 4)]), bk("y", "A", [("Y", 2)])],
                "new": [bk("better", "N", [("X", 3), ("Y", 1)]), bk("fills", "F", [("Z", 1)]), bk("low", "L", [("X", 1)]),
                        bk("same", "S", [("Y", 1)])]}
        out = run_node(
            "const t = r.cleanTarget(data.t); const bs = B(data.books); const nb = B(data.new);\n"
            "const plan = { main: { t1: bs[0].fingerprint } };\n"
            "const v = (b, opts) => r.newBookValue(b, bs, [t], opts)?.key || null;\n"
            "console.log(JSON.stringify([...nb.map((b) => v(b, { plan })), v(nb[0], {}), r.newBookValue(nb[1], bs, [t]).text]));", data)
        self.assertEqual(out[:4], ["main", "fills", None, None])   # X Lv1 is below the minimum; Y is already held
        self.assertEqual(out[4], "main")                            # no main book: more than every library book
        self.assertIn("Z", out[5])

    def test_stage_detection(self):
        t = target([item("X"), item("Y"), item("Z")])
        data = {"t": t, "books": [
            bk("main", "M", [("VocalUP", 1), ("X", 5)]), bk("y", "A", [("Y", 3)]), bk("z", "B", [("Z", 3)]),
            bk("done", "C", [("VocalUP", 1), ("X", 2), ("Y", 2), ("Z", 2)]), bk("dirty", "D", [("X", 1), ("N", 1)])]}
        out = run_node(
            "const t = r.cleanTarget(data.t); const bs = B(data.books); const by = Object.fromEntries(bs.map((b) => [b.title, b]));\n"
            "const tx = r.cleanTarget({ ...data.t, items: [...data.t.items, { name: 'N', role: 'excluded' }] });\n"
            "const s = (tt, books, main) => r.detectStage(tt, books, main).stage;\n"
            "console.log(JSON.stringify([s(t, bs, by.main), s(t, [by.main, by.y], by.main), s(t, bs, by.done),\n"
            " s({ ...t, scenario: 'halo' }, bs, by.done), s(tx, bs, by.dirty), s(t, [by.main], by.main), s(t, bs, null)]));", data)
        self.assertEqual(out, ["final", "collect", "done", "halo", "separate", "collect", "group"])

    def test_result_comparison_and_decisions(self):
        t = target([item("X", min_level=3), item("Y"), item("Z", "preferred")])
        data = {"t": t, "other": {"id": "t2", "name": "O", "items": [item("Q")]}, "books": [
            bk("main", "M", [("VocalUP", 1), ("X", 4), ("Y", 2), ("n", 1)]),
            bk("better", "M", [("VocalUP+", 1), ("X", 4), ("Y", 3), ("Z", 2)]),
            bk("lost", "M", [("VocalUP", 1), ("Y", 2), ("Z", 2), ("Q", 1)]),
            bk("same", "M", [("VocalUP", 1), ("X", 5), ("Y", 2)]),
            bk("worse", "M", [("VocalUP", 1), ("X", 2)])]}
        out = run_node(
            "const t = r.cleanTarget(data.t); const o = r.cleanTarget(data.other); const bs = B(data.books); const by = Object.fromEntries(bs.map((b) => [b.title, b]));\n"
            "const c = (x) => r.compareResult(by[x], by.main, t, { otherTargets: [t, o] });\n"
            "const b = c('better'), l = c('lost'), s = c('same'), w = c('worse');\n"
            "console.log(JSON.stringify({ b: [b.decision, b.added.map((x) => x.name), b.removed.map((x) => x.name), b.levelUp, b.progress],\n"
            " l: [l.decision, l.lostRequired, l.others.map((x) => x.target.id)], s: [s.decision, s.levelUp.length], w: [w.decision, w.lostUnits],\n"
            " none: r.compareResult(by.same, null, t).decision }));", data)
        self.assertEqual(out["b"][0], "main")
        self.assertEqual(sorted(out["b"][1]), ["VocalUP+", "Z"])
        self.assertEqual(sorted(out["b"][2]), ["VocalUP", "n"])
        self.assertEqual(out["b"][3], [{"name": "Y", "from": 2, "to": 3}])
        self.assertEqual(out["b"][4], {"before": 2, "after": 3, "total": 3})
        self.assertEqual(out["l"][:2], ["backup", ["X"]])    # lost required X, but useful for the other Target
        self.assertEqual(out["l"][2], ["t2"])
        self.assertEqual(out["s"], ["main", 1])              # same progress, Lv up
        self.assertEqual(out["w"][0], "stop")
        self.assertEqual(out["none"], "main")

    def test_next_action_and_failures(self):
        out = run_node(
            "const t = r.cleanTarget({ id: 't', items: [{ name: 'X' }] });\n"
            "const a = (o) => r.nextAction({ target: t, ...o }).key;\n"
            "console.log(JSON.stringify([a({ stage: { stage: 'final', main: {} }, recommendation: { valid: true } }),\n"
            " a({ stage: { stage: 'final', main: {} }, lastRun: { decision: null } }), a({ stage: { stage: 'collect', absent: [{ label: 'X' }] } }),\n"
            " a({ stage: { stage: 'group', main: {} }, failures: 3 }), a({ stage: { stage: 'halo', main: {} } }),\n"
            " r.nextAction({ target: null }).key, a({ stage: { stage: 'group', main: null } })]));")
        self.assertEqual(out, ["step", "decide", "collect", "rethink", "halo", "target", "pick_main"])

    def test_target_change_reuse_and_compare(self):
        old = target([item("X"), item("Y")])
        new = target([item("X"), item("Z"), item("W", "preferred")])
        data = {"old": old, "new": new, "books": [bk("main", "M", [("X", 3), ("Y", 3)]), bk("zw", "A", [("X", 2), ("Z", 2), ("W", 1)]),
                                                     bk("z", "B", [("Z", 4)])]}
        out = run_node(
            "const o = r.cleanTarget(data.old), n = r.cleanTarget(data.new); const bs = B(data.books);\n"
            "const plan = { runs: [{ outputs: ['b-z'], reverted: false }] };\n"
            "const u = r.reusePlan(o, n, bs, { mainBook: bs[0], plan }); const c = r.compareTargets(o, n, bs);\n"
            "console.log(JSON.stringify({ added: u.added.map((x) => x.label), removed: u.removed.map((x) => x.label), keep: u.keepMain,\n"
            " suggest: u.suggestMain && u.suggestMain.title, reuse: u.reuse.map((x) => [x.unit.label, x.books.map((b) => b.title)]),\n"
            " inter: u.intermediates.map((b) => b.title), cmp: [c.shared, c.onlyA, c.onlyB, c.both.map((b) => b.title)] }));", data)
        self.assertEqual(out["added"], ["Z", "W"])
        self.assertEqual(out["removed"], ["Y"])
        self.assertFalse(out["keep"])
        self.assertEqual(out["suggest"], "zw")
        self.assertEqual(out["reuse"], [["Z", ["zw", "z"]], ["W", ["zw"]]])
        self.assertEqual(out["inter"], ["z"])
        self.assertEqual(out["cmp"], [["X"], ["Y"], ["Z", "W"], ["main", "zw"]])

    def test_classification_and_inventory_protection(self):
        t = target([item("X"), item("Y"), item("Z"), item("N", "excluded")])
        data = {"t": t, "books": [bk("full", "A", [("VocalUP", 1), ("X", 1), ("Y", 1), ("Z", 1)]), bk("two", "B", [("X", 1), ("Y", 1), ("N", 1)]),
                                   bk("pair", "C", [("X", 2), ("Y", 2), ("q", 1)]), bk("raw", "D", [("X", 1)]),
                                   bk("none", "E", [("q", 1)]), bk("made", "F", [("X", 3)]), bk("uniq", "G", [("U", 5)])]}
        out = run_node(
            "const t = r.cleanTarget(data.t); const bs = B(data.books); const plan = { runs: [{ outputs: ['b-made'], reverted: false }], main: {} };\n"
            "const tz = r.cleanTarget({ ...data.t, items: [...data.t.items, { name: 'U', min_level: 5 }] });\n"
            "const inv = r.analyzeInventory(bs, tz);\n"
            "console.log(JSON.stringify({ cls: bs.map((b) => [b.title, r.classifyBook(b, t, { plan })]),\n"
            " z: inv.find((x) => x.unit.label === 'U'), conflicts: r.libraryConflicts(bs[3], bs) }));", data)
        cls = dict(out["cls"])
        self.assertEqual(cls["full"], "completed")
        self.assertEqual(cls["two"], "separation")
        self.assertEqual(cls["pair"], "group")
        self.assertEqual(cls["raw"], "raw")
        self.assertEqual(cls["none"], "other")
        self.assertEqual(cls["made"], "intermediate")
        self.assertTrue(out["z"]["protectWarn"])
        self.assertEqual(out["z"]["unique"]["title"], "uniq")
        self.assertEqual([x["name"] for x in out["conflicts"]["lowerThan"]], ["X"])

    def test_every_target_is_mirrored_to_the_wanted_list(self):
        # The wanted list is what knowhow_keep keeps, so it covers all Targets, active first
        out = run_node(
            "let m = p.migratePlan(null, []);\n"
            "m = p.upsertTarget(m, { id: 'a', name: 'A', items: [{ name: 'Leader適正◯', min_level: 4 }, { name: 'X', role: 'excluded' }] });\n"
            "m = p.upsertTarget(m, { id: 'b', name: 'B', items: [{ name: 'Leader適正◯', min_level: 2 }, { name: 'スタートダッシュ' }] });\n"
            "m = p.setActive(m, 'b');\n"
            "console.log(JSON.stringify(p.plansToWanted(m)));")
        self.assertEqual([(w["name"], w["min_level"]) for w in out], [("Leader適正◯", 2), ("スタートダッシュ", 1)])

    def test_plan_migration_keeps_the_wanted_list(self):
        wanted = [{"name": "パーフェクトリィ", "match": "exact", "min_level": 4, "purpose": "halo", "position": "center"},
                  {"name": "思い出(ノウハウ)", "match": "prefix", "min_level": 1, "purpose": "halo"}]
        out = run_node(
            "const m = p.migratePlan(null, data.wanted); const back = p.targetToWanted(p.activeTarget(m));\n"
            "const again = p.migratePlan(JSON.parse(JSON.stringify(m)), []);\n"
            "const junk = p.migratePlan({ version: 1, targets: [{ id: 'a', items: [{ name: 'x/y' }, { name: 'ok', role: 'nope' }] }, { id: 'a' }],"
            " runs: [{ id: 'r', decision: 'weird' }], userBooks: [{ fingerprint: 'b-x', rows: [{ name: 'q' }] }], rules: { exclusive_groups: false, x: 1 } }, data.wanted);\n"
            "console.log(JSON.stringify({ m, back, again: [again.migrated, again.targets.length, again.active], junk }));", {"wanted": wanted})
        m = out["m"]
        self.assertTrue(m["migrated"])
        self.assertEqual(m["version"], 1)
        self.assertEqual(m["targets"][0]["scenario"], "halo")
        self.assertEqual([(i["name"], i["min_level"], i["role"], i.get("position")) for i in m["targets"][0]["items"]],
                         [("パーフェクトリィ", 4, "required", "center"), ("思い出(ノウハウ)", 1, "required", None)])
        self.assertEqual(out["back"], wanted)              # mirrors back without loss
        mixed = wanted + [{"name": "エキスパート目標達成", "match": "exact", "min_level": 5, "purpose": "step"}]
        back = run_node("console.log(JSON.stringify(p.targetToWanted(p.migratePlan(null, data).targets[0])));", mixed)
        self.assertEqual(back, mixed)                      # mixed purposes survive the round trip
        self.assertEqual(out["again"], [False, 1, "t-wanted"])
        junk = out["junk"]
        self.assertEqual(len(junk["targets"]), 1)          # duplicate id dropped; v1 plan wins over the wanted list
        self.assertEqual([i["name"] for i in junk["targets"][0]["items"]], ["ok"])
        self.assertEqual(junk["targets"][0]["items"][0]["role"], "required")
        self.assertIsNone(junk["runs"][0]["decision"])
        self.assertEqual(junk["userBooks"], [])            # user books need a u- fingerprint
        self.assertEqual(junk["rules"], {"exclusive_groups": False})

    def test_runs_record_decide_revert_and_user_books(self):
        out = run_node(
            "let plan = p.migratePlan({ version: 1, targets: [{ id: 't', items: [{ name: 'X' }] }], main: { t: 'b-old' } });\n"
            "const parsed = p.parseRowsText('VocalUP+ Lv5\\nX 3\\n思い出(ノウハウ)++\\tLv.4\\nbad line without level');\n"
            "plan = p.recordRun(plan, { target: 't', inputs: ['b-old', 'b-a', 'b-b'], results: [{ idol: 'A', rows: parsed.rows }, 'b-existing'], id: 'r1', at: '2026-10-13T00:00:00Z' });\n"
            "const pending = p.runsFor(plan, 't').last.decision;\n"
            "const decided = p.decideRun(plan, 'r1', 'main');\n"
            "const reverted = p.revertRun(decided, 'r1');\n"
            "let fails = p.recordRun(decided, { target: 't', inputs: [], results: [], id: 'r2' }); fails = p.decideRun(fails, 'r2', 'failed');\n"
            "const merged = p.mergeBooks(B([{ fingerprint: 'b-old', title: 'o', idol: 'O', stats: {}, rows: [{ name: 'X', level: 1 }], knowhow_count: 1 }]), decided);\n"
            "console.log(JSON.stringify({ parsed, out: plan.runs[0].outputs, user: plan.userBooks[0], pending, main: decided.main.t,\n"
            " back: [reverted.main.t, reverted.runs[0].reverted, reverted.userBooks.length], fails: p.runsFor(fails, 't').failures,\n"
            " hist: p.bookHistory(decided, 'u-r1-0'), merged: merged.map((b) => [b.fingerprint, b.source]),\n"
            " removed: p.removeUserBook(decided, 'u-r1-0').main }));")
        self.assertEqual([(r["name"], r["level"]) for r in out["parsed"]["rows"]],
                         [("VocalUP+", 5), ("X", 3), ("思い出(ノウハウ)++", 4), ("badlinewithoutlevel", None)])
        self.assertEqual(out["out"], ["u-r1-0", "b-existing"])
        self.assertEqual(out["user"]["source"], "user")
        self.assertFalse(out["user"]["complete"])          # one row without Lv
        self.assertIsNone(out["pending"])
        self.assertEqual(out["main"], "u-r1-0")
        self.assertEqual(out["back"], ["b-old", True, 1])  # revert restores the main book, keeps the result book
        self.assertEqual(out["fails"], 1)
        self.assertEqual(out["hist"]["parents"], ["b-old", "b-a", "b-b"])
        self.assertEqual(out["merged"], [["b-old", "scan"], ["u-r1-0", "user"]])
        self.assertEqual(out["removed"], {})

    def test_templates_are_valid_targets(self):
        out = run_node("console.log(JSON.stringify(p.TARGET_TEMPLATES.map((t) => { const x = p.targetFromTemplate(t.key, 'x');"
                       " return [t.key, x.items.length, r.validateTarget(x).filter((i) => i.level === 'error').length, x.note]; })));")
        keys = [k for k, *_ in out]
        for key in ("base", "center", "attr-vo", "attr-da", "attr-vi", "leader", "support", "mono", "step"):
            self.assertIn(key, keys)
        for key, n, errors, note in out:
            self.assertGreater(n, 0, key)
            self.assertEqual(errors, 0, key)
            self.assertNotIn("待核实", note)

    def test_recommended_items_clean_and_cover_the_research_groups(self):
        out = run_node("console.log(JSON.stringify(p.RECOMMENDED_ITEMS.map((g) => [g.key, g.items.map((i) => {\n"
                       " const c = r.cleanTargetItem(i); return c && [c.name, c.role, c.match, c.min_level, c.group || null]; })])));")
        groups = dict(out)
        self.assertEqual(list(groups), ["common", "center", "leader", "attr", "support", "mono", "step", "situational"])
        for key, items in groups.items():
            self.assertTrue(items and all(items), key)                       # every entry survives cleaning
        names = {i[0]: i for items in groups.values() for i in items}
        self.assertEqual(names["思い出(ノウハウ)+"][4], "思い出")             # substitutes share a group
        self.assertEqual(names["アイドル("][2], "prefix")                     # say "Halo" spelling unconfirmed: prefix
        self.assertFalse(any(i[1] == "excluded" for items in groups.values() for i in items))   # 排除 is set per item, not added
        self.assertTrue(all(i[3] == 3 for i in groups["step"]))              # STEP material: Lv3 or higher

    def test_halo_rank_puts_required_units_before_optional_ones(self):
        """2026-10-24 role-play: a book with the optional エキシビションマッチ++ ranked above books with the required
        Center適正 because only the hit count was compared."""
        common = [("思い出(ノウハウ)+", 5), ("アピールUP(思い出高)", 5), ("スロースターター", 5)]
        data = {"books": [bk("opt", "樋口円香", common + [("エキシビションマッチ++", 5)], eval_value=1020),
                          bk("req", "大崎甘奈", common + [("Center適正◎", 5)], eval_value=990),
                          bk("low", "三峰結華", common + [("Center適正◎", 2)], eval_value=980),
                          bk("team", "福丸小糸", common + [("Center適正◎", 6)], eval_value=1100)]}
        out = run_node(
            "const t = p.targetFromTemplate('center', 'tc'); const bs = B(data.books);\n"
            "const res = r.haloRank(bs, t, { idols: ['福丸小糸'], verified: true });\n"
            "console.log(JSON.stringify({ order: res.picks.map((x) => x.book.title), total: res.total,\n"
            "  first: res.picks[0].hits.map((h) => h.role), gaps: res.picks[0].gaps.map((g) => g.name), done: res.picks.map((x) => x.done) }));", data)
        self.assertEqual(out["order"], ["req", "low", "opt"])   # required first, then the Lv sum; the team's idol is left out
        self.assertEqual(out["first"][0], "required")            # chips start with the required units
        self.assertEqual(out["total"], 8)                        # balanced: required + preferred units (substitutes once)
        self.assertIn("施設効率相談", out["gaps"])
        self.assertEqual(out["done"][0], out["done"][1])         # same progress; the Lv sum decides

    def test_halo_rank_puts_required_units_before_a_clean_book(self):
        """say "Halo" inherits every entry at S1 end: an unwanted ファンの声援 only takes a slot, so a book with the required
        Center適正 and ファンの声援 ranks above a clean book without Center適正."""
        common = [("思い出(ノウハウ)+", 5), ("アピールUP(思い出高)", 5)]
        data = {"books": [bk("clean", "樋口円香", common, eval_value=900),
                          bk("fan", "大崎甘奈", common + [("Center適正◎", 5), ("ファンの声援+", 3)], eval_value=800),
                          bk("both", "三峰結華", common + [("Center適正◎", 5)], eval_value=700)]}
        out = run_node(
            "const t = p.targetFromTemplate('center', 'tc');\n"
            "console.log(JSON.stringify(r.haloRank(B(data.books), t, null).picks.map((x) => x.book.title)));", data)
        self.assertEqual(out, ["both", "fan", "clean"])   # same required: fewer excluded first; then required beats clean


class KnowhowWorkflowStaticTests(unittest.TestCase):
    def test_workflow_modules_are_display_only_and_never_claim_probability(self):
        ui = ROOT / "ui" / "components"
        code = lambda t: "\n".join(l for l in t.splitlines() if not l.lstrip().startswith(("//", "*", "/*")))
        texts = {n: code((ui / n).read_text(encoding="utf-8")) for n in ("knowhow-rules.js", "knowhow-plan.js", "knowhow-workflow.js", "knowhow-ui.js")}
        for name, text in texts.items():
            for forbidden in ("bridge.request", ".request(", "startRun", "setOption", "run.start", "allow_spend", "setPref", "localStorage"):
                self.assertNotIn(forbidden, text, name)
        for name in ("knowhow-rules.js", "knowhow-plan.js"):
            self.assertNotRegex(texts[name], r"document\.|window\.", name)    # pure modules
        flow = texts["knowhow-workflow.js"]
        self.assertIn("不是概率", flow)
        self.assertIn("不是成功率", flow)
        self.assertNotRegex(flow, r"成功率\s*\d|概率\s*\d")
        panel = (ui / "knowhow-panel.js").read_text(encoding="utf-8")
        self.assertIn("targetBar(ws, ctx.api", panel)                        # active Target shared by all sub-pages
        self.assertIn('`${PLAN_KEY}-demo`', panel)                           # preview plan never mixes with the real one


if __name__ == "__main__":
    unittest.main()
