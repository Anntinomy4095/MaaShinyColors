"""Natural feeding, flight phases and visible-time story progress; no game process."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class OfficeEggTests(unittest.TestCase):
    def run_node(self, body):
        header = ("import assert from 'node:assert/strict';\n"
                  "import {OfficeEggs,FLOCK_SIZE,STORY_BEAT_MS,FOOD_MS,OFFICE_STORIES,cleanOfficeState} "
                  "from './ui/components/office-eggs-core.js';\n")
        result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", header + body], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_assets_cover_all_units_and_keep_optional_art_local(self):
        self.run_node("""
import {loadOfficeAssets} from './ui/components/office-eggs-assets.js';
import {OFFICE_CHARACTER_KEYS} from './ui/components/office-eggs-stories.js';
assert.equal(OFFICE_STORIES.length,8);
assert.equal(new Set(OFFICE_CHARACTER_KEYS).size,28);
for(const story of OFFICE_STORIES) {
  assert.equal(story.beats.length,4);
  const shown=new Set(story.beats.flatMap(b=>b.actors.map(a=>a.key)));
  assert.deepEqual([...shown].sort(),[...story.members].sort());
}
const missing=await loadOfficeAssets({load:async()=>{throw new Error('missing');}});
assert.equal(missing.charactersReady,false); assert.equal(missing.missing.length,28);
assert.ok(missing.assets.pigeon.src.endsWith('/ui/assets/office/dove-rest.svg'));
const local=await loadOfficeAssets({load:async()=>({default:Object.fromEntries(
  OFFICE_CHARACTER_KEYS.map(key=>[key,{src:`./office/${key}.png`}]))})});
assert.equal(local.charactersReady,true); assert.equal(local.missing.length,0);
const bad=await loadOfficeAssets({load:async()=>({default:{mano:{src:'https://example.invalid/private.png'}}})});
assert.ok(bad.missing.includes('mano'));
""")

    def test_food_arrivals_ignore_repeat_taps_and_flight_recovers(self):
        self.run_node("""
let now=10000, saves=[];
const toy=new OfficeEggs({clock:()=>now,random:()=>.4,onSave:s=>saves.push(s)});
assert.equal(toy.open().flock.count,1);
toy.feed(); for(let i=0;i<20;i++)toy.feed();
assert.equal(toy.snapshot().flock.count,1); assert.equal(toy.exportState().feeds,1);
now+=1200; assert.equal(toy.snapshot().flock.count,2);
now+=2800; assert.equal(toy.snapshot().flock.count,4);
assert.ok(toy.snapshot().birds.some(b=>b.pose==='peck'));
for(let i=0;i<12;i++){ now+=FOOD_MS;toy.snapshot();toy.feed();now+=4000;toy.snapshot(); }
assert.equal(toy.snapshot().flock.count,FLOCK_SIZE);
assert.equal(toy.exportState().largestFlock,FLOCK_SIZE);
assert.equal(toy.scatter().flock.mode,'flying');toy.scatter();
assert.equal(toy.exportState().flights,1);
assert.ok(toy.snapshot().birds.every(b=>b.pose==='crouch'));
now+=900;assert.ok(toy.snapshot().birds.some(b=>b.pose==='fly'));
now+=5000;assert.equal(toy.snapshot().flock.count,1);
assert.equal(toy.snapshot().flock.mode,'resting');
assert.ok(toy.snapshot().birds.every(b=>Number.isFinite(b.x)&&Number.isFinite(b.y)));
assert.ok(saves.length>0);
""")

    def test_story_uses_visible_time_and_resumes_without_click_advancement(self):
        self.run_node("""
let now=1000;
const toy=new OfficeEggs({clock:()=>now,random:()=>.3});
toy.open(); toy.close(); toy.open();
assert.equal(toy.snapshot().story.index,0);
now+=STORY_BEAT_MS+600;assert.equal(toy.snapshot().story.index,1);
toy.close();const saved=toy.exportState();now+=600000;
const copy=new OfficeEggs({saved,clock:()=>now});copy.open();
assert.equal(copy.snapshot().story.index,1);assert.equal(copy.snapshot().story.elapsedMs,600);
copy.setActive(false);now+=300000;copy.snapshot();copy.setActive(true);
assert.equal(copy.snapshot().story.index,1);
copy.pauseStory(true);now+=50000;assert.equal(copy.snapshot().story.index,1);
copy.pauseStory(false);now+=STORY_BEAT_MS;assert.equal(copy.snapshot().story.index,2);
copy.selectStory('illumination');copy.stepStory(3);now+=1700;
assert.equal(copy.snapshot().story.index,3);
assert.ok(copy.snapshot().story.actors.every(a=>typeof a.animation==='string' && a.animation.length>0));
assert.ok(copy.snapshot().story.actors.every(a=>!('pose' in a)));
assert.deepEqual(copy.snapshot().story.props,[]);
copy.selectStory('alstroemeria');copy.stepStory();
assert.equal(copy.snapshot().story.index,1);
assert.ok(copy.snapshot().story.actors.some(a=>a.key==='chiyuki'));
const taps=copy.exportState().storyTaps;copy.tapProp('missing');assert.equal(copy.exportState().storyTaps,taps);
const detached=copy.snapshot();const firstKey=detached.story.actors[0].key;
detached.story.actors[0].key='changed';detached.story.actors.length=0;
assert.equal(copy.snapshot().story.actors.length,3);
assert.equal(copy.snapshot().story.actors[0].key,firstKey);
assert.equal(cleanOfficeState({version:99,visits:9}).visits,0);
assert.equal(cleanOfficeState({version:1,visits:9,feeds:2}).feeds,2);
const noStorage=new OfficeEggs({onSave:()=>{throw new Error('quota');}});assert.equal(noStorage.open().flock.count,1);
""")


if __name__ == "__main__":
    unittest.main()
