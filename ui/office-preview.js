import { h } from "./components/dom.js";
import { THEATER_IDOLS, findTheaterScene, scenesForIdol, theaterIdolName, loadTheaterScene } from "./components/office-theater.js?v=1";
import { createOfficeStory } from "./components/office-story-player.js?v=4";
import { createOfficeShortScene } from "./components/office-short-scene.js?v=2";
import { createMotionPlayer } from "./components/office-motion-player.js?v=10";
import { createMotionSequence } from "./components/office-motion-sequence.js?v=2";
import { parseMotion } from "./components/office-motion.js?v=6";
const $=id=>document.getElementById(id);
const params=new URLSearchParams(location.search);
const current=findTheaterScene(params.get("story"));
let idol=params.get("idol")||"";
if(!THEATER_IDOLS.some(item=>item.key===idol)||current&&!current.cast.includes(idol))idol="";
let scene;
function update({time,duration,playing}) {
  $("time").max=duration;$("time").value=time;
  $("time-label").textContent=`${time.toFixed(1)} / ${duration.toFixed(1)}`;
  $("play").textContent=playing?"Ⅱ":"▶";$("play").setAttribute("aria-label",playing?"暂停":"播放");
}
function sceneUrl(id) {
  const url=new URL(location.href);url.searchParams.delete("motion");url.searchParams.set("story",id);
  if($("idol-picker").value)url.searchParams.set("idol",$("idol-picker").value);else url.searchParams.delete("idol");
  return url;
}
function editorLink() {
  if(!current)return;
  const selected=$("idol-picker").value;
  const actor=current.cast.includes(selected)?selected:current.editActor;
  $("editor-link").href=`office-motion.html?${new URLSearchParams({example:current.id,actor,rev:"11"})}`;
  $("cast").replaceChildren(...current.cast.map(key=>h("span",{class:key===selected?"selected":"",text:theaterIdolName(key)})));
}
function options() {
  const available=scenesForIdol($("idol-picker").value);
  const placeholder=current?[]:[h("option",{value:"",text:params.has("motion")?"编辑作品":params.get("story")==="greeting"?"立绘短片":"参考片段",disabled:true})];
  $("scene-picker").replaceChildren(...placeholder,...available.map(item=>h("option",{value:item.id,text:item.title})));
  $("scene-picker").value=current?.id||"";editorLink();return available;
}
$("idol-picker").replaceChildren(h("option",{value:"",text:"全部"}),...THEATER_IDOLS.map(item=>h("option",{value:item.key,text:item.name})));
$("idol-picker").value=idol;options();
$("idol-picker").onchange=()=>{
  const available=options();
  if(!current||!available.some(item=>item.id===current.id))location.assign(sceneUrl(available[0].id));
  else history.replaceState(null,"",sceneUrl(current.id));
};
$("scene-picker").onchange=()=>location.assign(sceneUrl($("scene-picker").value));
$("play").onclick=()=>scene.play(!scene.playing);
$("replay").onclick=()=>{scene.seek(0);scene.play(true);};
$("time").oninput=()=>scene.seek(Number($("time").value));
$("original").onchange=()=>scene.setOriginal($("original").checked);
document.addEventListener("keydown",event=>{if(!scene||event.target.closest?.("input,button,select,a"))return;
  if(event.code==="Space"){event.preventDefault();scene.play(!scene.playing);}});
try {
  if(params.has("motion")) {
    const raw=localStorage.getItem("sc-office-motion-preview-v1");
    if(!raw)throw new Error("请从动作编辑器点击预览");
    const project=parseMotion(raw);
    scene=await createMotionPlayer($("scene"),f=>update({...f,duration:f.project.duration}));await scene.load(project);
  } else if(current) {
    document.body.classList.add("story-preview");
    const story=await loadTheaterScene(current.id);
    scene=await createOfficeStory($("scene"),story,update);
    const edited=story.actors.some(actor=>actor.segments.some(segment=>Object.keys(segment.motion.tracks).length));
    $("compare-field").hidden=!edited;$("original").disabled=!edited;
  } else if(params.get("story")==="greeting") {
    document.body.classList.add("story-preview");
    const story=(await import("./assets/office-scenes/late-greeting.js?v=3")).default;
    scene=await createOfficeStory($("scene"),story,update);
  } else if(params.has("story")) {
    const story=(await import("./assets/office-scenes/pigeon-mimic.js?v=1")).default;
    scene=await createMotionSequence($("scene"),story.segments,update);
  } else scene=await createOfficeShortScene($("scene"),update);
  for(const name of ["play","replay","time"])$(name).disabled=false;
  $("scene").setAttribute("aria-busy","false");$("status").textContent="";scene.play(true);
} catch(error){$("status").textContent=error.message||"本机动作素材未能加载";}
window.addEventListener("pagehide",()=>scene?.destroy());
