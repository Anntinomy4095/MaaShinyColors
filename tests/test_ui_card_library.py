"""Card library controls and truthful partial results; offline DOM stand-in only."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRELUDE = (
    "import './tests/fixtures/ui/fake_dom.mjs';"
    "const m = await import('./ui/components/card-library.js');"
    "const find = (n,k) => n?.dataset?.focusKey === k ? n : (n?.childNodes||[]).reduce((a,c)=>a||find(c,k),null);"
    "const data = JSON.parse(process.argv[1]);"
)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class CardLibraryUiTests(unittest.TestCase):
    def run_node(self, body, data=None):
        result = subprocess.run(["node", "--input-type=module", "-e", PRELUDE + body, json.dumps(data)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_first_use_is_one_explicit_click_even_without_game(self):
        out = self.run_node(
            "let updates=0; const host={connected:false,card_library:{supported:true,state:'idle'}};"
            "const view=m.cardLibraryView({host},{cardLibraryUpdate:()=>updates++});"
            "const btn=find(view,'card-library-update'); const before=updates;"
            "btn.listeners.click[0](); console.log(JSON.stringify({before,updates,disabled:btn.getAttribute('disabled'),text:view.textContent,title:btn.getAttribute('title')}));")
        self.assertEqual(out["before"], 0)
        self.assertEqual(out["updates"], 1)
        self.assertIsNone(out["disabled"])
        self.assertIn("下载卡资料", out["text"])
        self.assertIn("不含持有卡、Lv、凸", out["title"])   # 2026-10-24: the explanation is the button tooltip
        self.assertNotIn("持有卡", out["text"])

    def test_card_details_status_for_auto_produce(self):
        # 2026-11-23 (user): 自动挖矿 needs the details; a remaining-time estimate while they download
        out = self.run_node(
            "const fresh=m.cardDetailsStatus({card_library:{supported:true,state:'idle'}});"
            "const busy=m.cardDetailsStatus({card_library:{supported:true,state:'running',phase:'cards',updated_cards:400,total_cards:1522}});"
            "const done=m.cardDetailsStatus({card_library:{supported:true,state:'done',last:{cards_complete:true,local_cards:1522}}});"
            "console.log(JSON.stringify({fresh,busy,done}));")
        self.assertEqual((out["fresh"]["ready"], out["fresh"]["minutes"]), (False, 45))
        self.assertEqual((out["busy"]["ready"], out["busy"]["minutes"]), (False, 33))      # 1122 left x 1.75 s
        self.assertTrue(out["done"]["ready"])

    def test_wing_banner_shows_until_details_are_complete(self):
        script = ("import './tests/fixtures/ui/fake_dom.mjs';"
                  "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
                  "const w = await import('./ui/components/wing-panel.js');"
                  "let clicks=0; const a={cardLibraryUpdate:()=>clicks++};"
                  "const miss=w.cardDetailsBanner({host:{card_library:{supported:true,state:'idle'}}},a);"
                  "const find=(n,k)=>n?.dataset?.focusKey===k?n:(n?.childNodes||[]).reduce((x,c)=>x||find(c,k),null);"
                  "find(miss,'wing-library-download').listeners.click[0]();"
                  "const busy=w.cardDetailsBanner({host:{card_library:{supported:true,state:'running',phase:'cards',updated_cards:10,total_cards:100}}},a);"
                  "const done=w.cardDetailsBanner({host:{card_library:{supported:true,state:'done',last:{cards_complete:true,local_cards:5}}}},a);"
                  "console.log(JSON.stringify({miss:miss.textContent,clicks,busy:busy.textContent,done:done===null,"
                  "busyButton:!!find(busy,'wing-library-download')}));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertIn("自动挖矿需要卡资料", out["miss"])
        self.assertEqual(out["clicks"], 1)
        self.assertIn("10 / 100", out["busy"])
        self.assertFalse(out["busyButton"])
        self.assertTrue(out["done"])

    def test_progress_can_be_stopped_without_second_start(self):
        out = self.run_node(
            "let stops=0; const host={card_library:{supported:true,state:'running',phase:'cards',updated_cards:100,total_cards:1471}};"
            "const view=m.cardLibraryView({host},{cardLibraryStop:()=>stops++});"
            "find(view,'card-library-stop').listeners.click[0]();"
            "console.log(JSON.stringify({stops,disabled:find(view,'card-library-update').getAttribute('disabled'),text:view.textContent}));")
        self.assertEqual(out["stops"], 1)
        self.assertIsNotNone(out["disabled"])
        self.assertIn("100 / 1471", out["text"])

    def test_automatic_refresh_is_labelled(self):
        # 2026-10-31: the host refreshes a stale library when it is read; the line says so.
        out = self.run_node("console.log(JSON.stringify(m.cardLibraryResult(m.cardLibraryModel(data))));",
                            {"card_library": {"state": "running", "phase": "catalog", "auto": True}})
        self.assertEqual(out, "自动检查卡目录")

    def test_missing_images_do_not_hide_successful_card_update(self):
        model = {"card_library": {"state": "partial", "last": {"cards_complete": True, "status": "stopped",
                 "local_cards": 1471, "added_cards": 4, "changed_cards": 0, "icons_failed": 1}}}
        out = self.run_node("const model=m.cardLibraryModel(data);console.log(JSON.stringify({result:m.cardLibraryResult(model),summary:m.cardLibrarySummary(model)}));", model)
        self.assertIn("新增 4 张", out["result"])
        self.assertIn("1 张图片未下载", out["result"])
        self.assertIn("1471", out["summary"])

    def test_partial_card_download_never_claims_latest(self):
        out = self.run_node("console.log(JSON.stringify(m.cardLibraryResult(m.cardLibraryModel(data))));",
                             {"card_library": {"state": "partial", "last": {"cards_complete": False, "updated_cards": 100, "remaining_cards": 1371}}})
        self.assertIn("剩余 1371 张", out)
        self.assertNotIn("最新", out)

    def test_stop_and_missing_host_explain_the_actual_action(self):
        out = self.run_node(
            "const view=m.cardLibraryView({host:{}},{});console.log(JSON.stringify({disabled:find(view,'card-library-update').getAttribute('disabled'),"
            "result:m.cardLibraryResult(m.cardLibraryModel({card_library:{state:'stopped'}}))}));")
        self.assertIsNotNone(out["disabled"])
        self.assertIn("已停止", out["result"])

    def test_protocol_uses_empty_params_and_refreshes_the_active_list(self):
        protocol = json.loads((ROOT / "ui/protocol.json").read_text(encoding="utf-8"))
        for command in ("cards.library.update", "cards.library.stop"):
            self.assertFalse(protocol["commands"][command]["params"]["additionalProperties"])
            self.assertEqual(protocol["commands"][command]["params"]["properties"], {})
        app = (ROOT / "ui/app.js").read_text(encoding="utf-8")
        self.assertIn('bridge.request("cards.library.update", {})', app)
        self.assertIn('loadGameAssets({ force: true })', app)
        self.assertIn('libraryAfter > libraryBefore', app)

    def test_updated_manifest_is_visible_without_restarting(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "components").mkdir()
            (base / "game-assets").mkdir()
            (base / "package.json").write_text('{"type":"module"}', encoding="utf-8")
            for name in ("game-assets.js", "dom.js", "icons.js", "glyphs.js"):
                shutil.copy2(ROOT / "ui/components" / name, base / "components" / name)
            module = json.dumps((base / "components/game-assets.js").as_uri())
            target = json.dumps(str(base / "game-assets/manifest.js"))
            script = (
                f"const m=await import({module});const {{writeFileSync}}=await import('node:fs');"
                f"const write=(id)=>writeFileSync({target},'export default '+JSON.stringify({{cards:[{{id,rarity:'SSR',thumbnail_missing:true,src:null}}]}}));"
                "Date.now=()=>123;write('2040010010');const a=await m.loadGameAssets();"
                "write('2040010020');const b=await m.loadGameAssets();const c=await m.loadGameAssets({force:true});"
                "write('2040010030');const d=await m.loadGameAssets({force:true});"
                "console.log(JSON.stringify([a,b,c,d].map(x=>x.cards[0].id)));"
            )
            result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                                    capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), ["2040010010", "2040010010", "2040010020", "2040010030"])


if __name__ == "__main__":
    unittest.main()
