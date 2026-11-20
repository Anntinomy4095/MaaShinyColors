"""动作编辑模型的行为回归；仅运行 Node，不加载浏览器或游戏资源。"""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class OfficeMotionTests(unittest.TestCase):
    def run_node(self, body):
        header = """
import assert from 'node:assert/strict';
import {newMotion, sampleTrack, sampleMotion, setMotionKey, moveMotionKey,
  removeMotionKey, parseMotion, applyMotionPose, saveMotionOverride,
  readMotionOverrides, clearMotionOverride, sourceTimeAt, resizeMotion, clearBoneMotion, motionBoneList} from './ui/components/office-motion.js';
const near = (actual, expected) => assert.ok(Math.abs(actual-expected)<1e-9,
  `${actual} differs from ${expected}`);
const freeze = value => { if(value && typeof value==='object') {
  Object.values(value).forEach(freeze); Object.freeze(value); } return value; };
"""
        result = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e", header + body],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_standing_model_roundtrip_and_rig_specific_bones(self):
        self.run_node("""
const original=newMotion('mano','wait',3);
assert.ok(!('modelKind' in parseMotion(original)));
const standing={...original,modelKind:'stand',expressionAnimation:'face_smile',gazeAnimation:'eye_left'};
assert.deepEqual(parseMotion(JSON.stringify(standing)),standing);
assert.throws(()=>parseMotion({...original,modelKind:'unknown'}));
assert.throws(()=>parseMotion({...standing,expressionAnimation:17}));
const list=motionBoneList(['root','Control','head','body','arm_L_top','Hand_L','face_control','custom_hair'],'stand');
assert.ok(list.commonBones.some(([name,label])=>name==='Control'&&label==='整体'));
assert.ok(list.commonBones.some(([name,label])=>name==='arm_L_top'&&label==='左上臂'));
assert.ok(list.commonBones.some(([name,label])=>name==='Hand_L'&&label==='左手'));
assert.ok(list.bones.some(([name])=>name==='custom_hair'));
assert.ok(!list.handles.includes('face_control'));
assert.ok(list.handles.includes('head'));
assert.equal(new Set(list.bones.map(([name])=>name)).size,list.bones.length);
const other=motionBoneList(['All','body_under','arm_top_L','arm_R_bottm_down','hand_L_up'],'stand');
assert.deepEqual(other.commonBones,[['All','整体'],['body_under','身体'],['arm_top_L','左上臂'],['hand_L_up','左手'],['arm_R_bottm_down','右前臂']]);
""")

    def test_story_timeline_delay_blend_end_and_backward_seek(self):
        self.run_node("""
const {storyActorFrame}=await import('./ui/components/office-story-player.js');
const first=newMotion('mano','wait',1.2),second=newMotion('mano','hello',8.4);
const actor={segments:[{at:0,motion:first},{at:1.2,motion:second,mix:.4}]};freeze(actor);
assert.equal(storyActorFrame(actor,.6).motion.baseAnimation,'wait');
const cut=storyActorFrame(actor,1.2);
assert.equal(cut.index,1);assert.equal(cut.time,0);
assert.deepEqual(cut.transition,{motion:first,elapsed:0,duration:.4});
const during=storyActorFrame(actor,1.4);near(during.time,.2);near(during.transition.elapsed,.2);
assert.equal(storyActorFrame(actor,1.7).transition,null);
assert.equal(storyActorFrame(actor,20).time,8.4);
storyActorFrame(actor,20);
assert.deepEqual(storyActorFrame(actor,1.4),during);
assert.equal(storyActorFrame(actor,-1).time,0);
""")

    def test_interpolation_curves_and_boundaries(self):
        self.run_node("""
const make = curve => [{time:1,x:0,y:10,rotation:-20,curve},
  {time:3,x:8,y:2,rotation:20,curve:'linear'}];
assert.deepEqual(sampleTrack([],2),{x:0,y:0,rotation:0});
assert.deepEqual(sampleTrack(make('linear'),0),{x:0,y:0,rotation:0});
assert.deepEqual(sampleTrack(make('linear'),1),{x:0,y:10,rotation:-20});
assert.deepEqual(sampleTrack(make('linear'),4),{x:8,y:2,rotation:20});
assert.deepEqual(sampleTrack(make('linear'),1.5),{x:2,y:8,rotation:-10});
const smooth=sampleTrack(make('smooth'),1.5);
near(smooth.x,1.25); near(smooth.y,8.75); near(smooth.rotation,-13.75);
assert.deepEqual(sampleTrack(make('hold'),2.99),{x:0,y:10,rotation:-20});
assert.deepEqual(sampleTrack(make('hold'),3),{x:8,y:2,rotation:20});
""")

    def test_insertion_only_adds_current_key_and_aligns_to_frames(self):
        self.run_node("""
const original=freeze(newMotion('mano','wait',3));
const edited=setMotionKey(original,'head',.51,{rotation:12});
assert.deepEqual(original.tracks,{});
assert.deepEqual(edited.tracks.head.map(k=>k.time),[1/2]);
assert.deepEqual(sampleTrack(edited.tracks.head,0),{x:0,y:0,rotation:0});
assert.deepEqual(sampleTrack(edited.tracks.head,3),{x:0,y:0,rotation:12});
assert.equal(sampleTrack(edited.tracks.head,.5).rotation,12);
const replaced=setMotionKey(edited,'head',.502,{rotation:-4});
assert.equal(replaced.tracks.head.length,1);
assert.equal(sampleTrack(replaced.tracks.head,.5).rotation,-4);
assert.equal(sampleTrack(edited.tracks.head,.5).rotation,12);
const zero=setMotionKey(original,'head',0,{x:4,y:-2,rotation:7});
assert.equal(zero.tracks.head.length,1);
assert.deepEqual(sampleTrack(zero.tracks.head,1),{x:4,y:-2,rotation:7});
assert.equal(setMotionKey(original,'head',-1,{rotation:5}).tracks.head[0].time,0);
assert.equal(setMotionKey(original,'head',20,{rotation:5}).tracks.head.at(-1).time,3);
""")

    def test_move_key_replaces_collision_preserves_pose_and_clamps(self):
        self.run_node("""
let source=setMotionKey(newMotion('mano','wait',3),'head',1,{x:2,rotation:10},'hold');
source=setMotionKey(source,'head',2,{x:5,rotation:20},'linear');
freeze(source);
const moved=moveMotionKey(source,'head',1,2.01);
assert.deepEqual(moved.tracks.head.map(k=>k.time),[2]);
assert.equal(moved.tracks.head[0].rotation,10);
assert.equal(moved.tracks.head[0].x,2); assert.equal(moved.tracks.head[0].curve,'hold');
assert.deepEqual(source.tracks.head.map(k=>k.time),[1,2]);
assert.equal(source.tracks.head[1].rotation,20);
const left=moveMotionKey(source,'head',1,-4);
assert.deepEqual(left.tracks.head.map(k=>k.time),[0,2]);
assert.equal(left.tracks.head[0].rotation,10);
const right=moveMotionKey(source,'head',1,99);
assert.deepEqual(right.tracks.head.map(k=>k.time),[2,3]);
assert.equal(right.tracks.head.at(-1).rotation,10);
assert.deepEqual(moveMotionKey(source,'head',.5,2),source);
""")

    def test_delete_uses_frame_tolerance_and_removes_empty_track(self):
        self.run_node("""
const source=freeze(setMotionKey(newMotion('mano','wait',2),'head',1,{rotation:8}));
const removed=removeMotionKey(source,'head',1+.1/30);
assert.ok(!('head' in removed.tracks));
assert.equal(source.tracks.head.length,1);
assert.deepEqual(removeMotionKey(source,'head',1+.6/30),source);
const end=removeMotionKey(removed,'head',2);
assert.ok(!('head' in end.tracks));
assert.deepEqual(sampleMotion(end,1),{});
""")

    def test_json_roundtrip_preserves_editable_motion_and_detaches_objects(self):
        self.run_node("""
const source=setMotionKey(newMotion('mano','wait',2),'wing_L',1,{x:3,y:-4,rotation:17},'hold');
source.target='bird'; source.startTime=5.6667; source.loop=false;
const parsed=parseMotion(JSON.stringify(source));
assert.deepEqual(parsed,source);
assert.deepEqual(sampleMotion(parsed,1),{wing_L:{x:3,y:-4,rotation:17}});
const copy=parseMotion(source); copy.tracks.wing_L[0].rotation=99;
assert.equal(source.tracks.wing_L[0].rotation,17);
for(const mutate of [p=>p.version=99,p=>p.duration=0,
  p=>p.tracks.wing_L[0].time=3,p=>p.tracks.wing_L[0].rotation=Infinity]) {
  const invalid=structuredClone(source); mutate(invalid);
  assert.throws(()=>parseMotion(invalid));
}
assert.throws(()=>parseMotion('{broken'));
""")

    def test_pose_is_additive_ignores_missing_bones_and_keeps_project_unchanged(self):
        self.run_node("""
let project=setMotionKey(newMotion('mano','wait',2),'head',0,{x:3,y:-4,rotation:12});
project=setMotionKey(project,'not-in-model',0,{rotation:5}); freeze(project);
const before=JSON.stringify(project);
const head={x:100,y:200,rotation:-30};
const skeleton={findBone:name=>name==='head'?head:null};
applyMotionPose(skeleton,project,1);
assert.deepEqual(head,{x:103,y:196,rotation:-18});
assert.equal(JSON.stringify(project),before);
const sampled=sampleMotion(project,1); sampled.head.x=999;
assert.equal(sampleMotion(project,1).head.x,3);
""")

    def test_source_range_mapping_freeze_and_legacy_timing(self):
        self.run_node("""
const legacy=newMotion('mano','wait',4);legacy.startTime=2;
assert.equal(sourceTimeAt(legacy,1.5),3.5);
assert.equal(sourceTimeAt(legacy,5),7);
const ranged=freeze({...legacy,sourceEnd:10,sourceLoop:false});
assert.equal(sourceTimeAt(ranged,0),2);assert.equal(sourceTimeAt(ranged,1),4);
assert.equal(sourceTimeAt(ranged,4),10);
assert.equal(sourceTimeAt(ranged,-1),2);assert.equal(sourceTimeAt(ranged,8),10);
const still={...ranged,sourceEnd:2};
for(const time of [0,1,4])assert.equal(sourceTimeAt(still,time),2);
assert.deepEqual(parseMotion(JSON.stringify(ranged)),ranged);
assert.deepEqual(parseMotion(JSON.stringify(still)),still);
assert.ok(!('sourceEnd' in parseMotion(JSON.stringify(legacy))));
assert.ok(!('sourceLoop' in parseMotion(JSON.stringify(legacy))));
for(const optional of [{sourceEnd:1},{sourceEnd:Infinity},{sourceEnd:null},
  {sourceLoop:'false'},{sourceLoop:0},{sourceLoop:null}])
  assert.throws(()=>parseMotion({...legacy,...optional}));
assert.equal(parseMotion({...legacy,sourceLoop:true}).sourceLoop,true);
""")

    def test_resize_retimes_keys_merges_collisions_and_preserves_source(self):
        self.run_node("""
let source={...newMotion('mano','wait',4),startTime:2,sourceEnd:10,sourceLoop:false};
source=setMotionKey(source,'head',1,{rotation:8},'hold');
source=setMotionKey(source,'head',3,{rotation:24},'linear');freeze(source);
const short=resizeMotion(source,2);
assert.deepEqual(short.tracks.head.map(k=>k.time),[.5,1.5]);
assert.equal(short.tracks.head[0].curve,'hold');
assert.equal(short.sourceEnd,10);assert.equal(short.sourceLoop,false);
assert.equal(sourceTimeAt(short,1),6);assert.equal(sourceTimeAt(source,1),4);
assert.equal(source.duration,4);assert.deepEqual(source.tracks.head.map(k=>k.time),[1,3]);
let crowded=setMotionKey(newMotion('mano','wait',1),'head',.2,{rotation:3});
crowded=setMotionKey(crowded,'head',.3,{rotation:7});
const merged=resizeMotion(crowded,.1);
assert.equal(merged.tracks.head.length,1);
near(merged.tracks.head[0].time,1/30);assert.equal(merged.tracks.head[0].rotation,7);
assert.deepEqual(resizeMotion(newMotion(),3).tracks,{});
for(const duration of [0,-1,Infinity,NaN])assert.throws(()=>resizeMotion(source,duration));
""")

    def test_clear_one_bone_keeps_other_tracks_and_project_metadata(self):
        self.run_node("""
let source=setMotionKey(newMotion('mano','wait',3),'head',1,{rotation:9});
source=setMotionKey(source,'hand_R',2,{rotation:12});source.sourceEnd=7;freeze(source);
const cleared=clearBoneMotion(source,'head');
assert.ok(!('head' in cleared.tracks));
assert.deepEqual(cleared.tracks.hand_R,source.tracks.hand_R);assert.equal(cleared.sourceEnd,7);
assert.equal(source.tracks.head.length,1);
assert.deepEqual(clearBoneMotion(source,'missing'),source);
""")

    def test_working_pose_replaces_track_pose_without_double_addition(self):
        self.run_node("""
let source=setMotionKey(newMotion(),'head',0,{x:3,y:4,rotation:12});
source=setMotionKey(source,'hand_R',0,{rotation:6});freeze(source);
const working=freeze({head:{x:1,y:-2,rotation:5},neck:{rotation:7},missing:{x:8}});
const bones={head:{x:10,y:20,rotation:30},hand_R:{x:0,y:0,rotation:10},neck:{x:2,y:3,rotation:4}};
applyMotionPose({findBone:name=>bones[name]||null},source,1,working);
assert.deepEqual(bones.head,{x:11,y:18,rotation:35});
assert.deepEqual(bones.hand_R,{x:0,y:0,rotation:16});
assert.deepEqual(bones.neck,{x:2,y:3,rotation:11});
assert.deepEqual(sampleMotion(source,1).head,{x:3,y:4,rotation:12});
assert.equal(working.head.rotation,5);
""")

    def test_world_position_pin_compensates_rotated_scaled_parent_and_keeps_author_offset(self):
        self.run_node("""
function rig() {
  const root={name:'root',parent:null,x:13,y:-7,rotation:30,scaleX:-2,scaleY:.5};
  const head={name:'head',parent:root,x:4,y:8,rotation:20};
  const hair={name:'hair_F',parent:head,x:2,y:6,rotation:-15};
  const bird={name:'Pchan',parent:hair,x:9,y:3,rotation:11};
  const bones=[root,head,hair,bird];
  const skeleton={findBone:name=>bones.find(b=>b.name===name),updateWorldTransform(){
    for(const bone of bones) {
      const r=bone.rotation*Math.PI/180, sx=bone.scaleX??1, sy=bone.scaleY??1;
      const a=Math.cos(r)*sx,b=-Math.sin(r)*sy,c=Math.sin(r)*sx,d=Math.cos(r)*sy;
      const p=bone.parent;
      if(p) {
        bone.worldX=p.a*bone.x+p.b*bone.y+p.worldX;bone.worldY=p.c*bone.x+p.d*bone.y+p.worldY;
        bone.a=p.a*a+p.b*c;bone.b=p.a*b+p.b*d;bone.c=p.c*a+p.d*c;bone.d=p.c*b+p.d*d;
      } else Object.assign(bone,{a,b,c,d,worldX:bone.x,worldY:bone.y});
    }
  }};
  skeleton.updateWorldTransform();return {skeleton,head,hair,bird};
}
let project=setMotionKey(newMotion(),'head',0,{x:5,y:-3,rotation:90});
const native=rig(), nx=native.bird.worldX, ny=native.bird.worldY;
const unpinned=rig();applyMotionPose(unpinned.skeleton,project,0);unpinned.skeleton.updateWorldTransform();
assert.ok(Math.hypot(unpinned.bird.worldX-nx,unpinned.bird.worldY-ny)>1);
project.worldPositionBones=['Pchan','missing','root'];
const pinned=rig();applyMotionPose(pinned.skeleton,project,0);
near(pinned.bird.worldX,nx);near(pinned.bird.worldY,ny);
const dx=3,dy=-4;
project=setMotionKey(project,'Pchan',0,{x:dx,y:dy,rotation:7});freeze(project);
const shifted=rig();applyMotionPose(shifted.skeleton,project,0);
near(shifted.bird.worldX,nx+native.hair.a*dx+native.hair.b*dy);
near(shifted.bird.worldY,ny+native.hair.c*dx+native.hair.d*dy);
assert.equal(shifted.bird.rotation,18);assert.equal(shifted.head.rotation,110);
assert.deepEqual(parseMotion(JSON.stringify(project)).worldPositionBones,['Pchan','missing','root']);
for(const value of ['Pchan',null,[12],['Pchan',null]])
  assert.throws(()=>parseMotion({...newMotion(),worldPositionBones:value}));
assert.ok(!('worldPositionBones' in parseMotion(newMotion())));
""")

    def test_local_overrides_roundtrip_and_clear_one_target(self):
        self.run_node("""
const values=new Map(); const storage={getItem:k=>values.get(k)??null,
  setItem:(k,v)=>values.set(k,v)};
const person=setMotionKey(newMotion('mano','wait',2),'head',1,{rotation:9});
const bird={...newMotion('mano','wait',1.6),target:'bird',startTime:5.6667};
saveMotionOverride(person,storage); saveMotionOverride(bird,storage);
assert.deepEqual(readMotionOverrides(storage),{mano:person,pigeon:bird});
person.tracks.head[0].rotation=99;
assert.equal(readMotionOverrides(storage).mano.tracks.head[0].rotation,9);
clearMotionOverride('pigeon',storage);
assert.deepEqual(Object.keys(readMotionOverrides(storage)),['mano']);
""")


if __name__ == "__main__":
    unittest.main()