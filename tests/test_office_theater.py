"""小剧场作者目录；只导入公开作者动作数据，不加载原生资源。"""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class OfficeTheaterTests(unittest.TestCase):
    def run_node(self, body):
        result = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e", """
import assert from 'node:assert/strict';
import {THEATER_IDOLS,THEATER_SCENES,findTheaterScene,scenesForIdol,
  theaterIdolName,loadTheaterScene} from './ui/components/office-theater.js';
""" + body], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_idol_order_names_and_scene_participation(self):
        self.run_node("""
assert.deepEqual(THEATER_IDOLS.map(i=>i.key),['mano','hiori','meguru','amana','tenka','asahi','fuyuko','koito','hinana']);
assert.deepEqual(THEATER_IDOLS.map(i=>i.name),['真乃','灯织','巡','甘奈','甜花','旭','冬优子','小糸','雏菜']);
assert.deepEqual(scenesForIdol().map(s=>s.id),['rehearsal','photo','curiosity','twins','mimic','peek','q-theater']);
assert.deepEqual(scenesForIdol('all'),THEATER_SCENES);
assert.deepEqual(scenesForIdol('mano').map(s=>s.id),['mimic','peek','q-theater']);
assert.deepEqual(scenesForIdol('tenka').map(s=>s.id),['twins','peek','q-theater']);
for (const [idol,id] of [['hiori','rehearsal'],['meguru','rehearsal'],['koito','photo'],['hinana','photo'],['asahi','curiosity'],['fuyuko','curiosity'],['amana','twins']])
  assert.deepEqual(scenesForIdol(idol).map(s=>s.id),[id]);
assert.deepEqual(scenesForIdol('luca'),[]);assert.deepEqual(scenesForIdol('unknown'),[]);
assert.equal(theaterIdolName('tenka'),'甜花');assert.equal(theaterIdolName('unknown'),'unknown');
for(const scene of THEATER_SCENES) {
  assert.equal(findTheaterScene(scene.id),scene);
  assert.ok(scene.cast.includes(scene.editActor));
}
assert.equal(findTheaterScene('unknown'),undefined);
""")

    def test_module_urls_and_dynamic_load_return_author_scene(self):
        self.run_node("""
for(const scene of THEATER_SCENES) {
  const version=scene.id==='q-theater'?'2':'1';
  const filename=scene.id==='q-theater'?'q-theater':`q-${scene.id}`;
  assert.equal(scene.module,`../assets/office-scenes/${filename}.js?v=${version}`);
}
const expected=(await import('./ui/assets/office-scenes/q-mimic.js?v=1')).default;
assert.equal(await loadTheaterScene('mimic'),expected);
assert.ok(expected.actors[0].segments.length>0);
await assert.rejects(loadTheaterScene('missing'),/找不到小剧场：missing/);
""")


if __name__ == "__main__":
    unittest.main()
