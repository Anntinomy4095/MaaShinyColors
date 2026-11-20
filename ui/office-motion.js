import { findTheaterScene, loadTheaterScene, theaterIdolName } from "./components/office-theater.js?v=1";
import { h } from "./components/dom.js";
import { OFFICE_CHARACTER_KEYS } from "./components/office-eggs-stories.js?v=4";
import { createMotionPlayer } from "./components/office-motion-player.js?v=7";
import { newMotion, parseMotion, sampleTrack, setMotionKey, moveMotionKey, removeMotionKey,
  resizeMotion, NEUTRAL, EDITABLE_BONES } from "./components/office-motion.js?v=5";

const $ = id => document.getElementById(id), NS = "http://www.w3.org/2000/svg";
const NAMES = ["真乃","灯织","巡","恋钟","摩美美","咲耶","结华","雾子","果穗","智代子","树里","凛世","夏叶","甘奈","甜花","千雪","旭","冬优子","爱依","透","圆香","小糸","雏菜","日花","美琴","路加","羽那","春希"];
const LABELS = Object.fromEntries(EDITABLE_BONES), LAST = "sc-office-motion-last", PREVIEW = "sc-office-motion-preview-v1";
const CLIPS = { blank:"空动作",touch:"触碰",skill1:"技能",skill2:"技能 2", yes:"点头",yes2:"点头 2",no:"摇头",no2:"摇头 2",hello:"招呼",salute:"敬礼",think:"思考",
  smile1:"微笑 1",smile2:"微笑 2",smile3:"微笑 3",shy1:"害羞 1",shy2:"害羞 2",sad1:"难过 1",sad2:"难过 2",surp1:"惊讶 1",surp2:"惊讶 2",anger1:"生气 1",anger2:"生气 2",anger3:"生气 3",
  face_wait:"默认",face_smile:"微笑",face_smile2:"微笑 2",face_joy:"喜悦",face_shy:"害羞",face_cry:"流泪",face_sad:"难过",face_surp:"惊讶",face_anger:"生气",face_anger2:"生气 2",face_serious:"认真",face_trouble:"困扰",face_close:"闭眼",face_close2:"闭眼 2",face_close3:"闭眼 3",
  eye_front:"正前方",eye_left:"向左",eye_right:"向右",arm_down:"放下手",arm_up_R:"右手抬起",wait1:"待机 1",wait2:"待机 2",wait3:"待机 3",wait4:"待机 4", wait:"待机", ok:"成功", ng:"失败", talk_wait:"交谈待机", talk_ok:"交谈成功", talk_ng:"交谈失败", photo_wait:"摄影待机", shop:"商店" };
let project, player, info, frame, bone = "head", pending = {}, undoStack = [], redoStack = [], drag = null;
let selection = null, cursors = [], points = new Map(), gizmo, knob, xAxis, yAxis;
const clone = value => structuredClone(value);
const id = () => `m-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,6)}`;
const modelKind = p => p.modelKind || "cb";
const keyOf = p => `sc-office-motion-draft:${p.character}:${p.target}:${modelKind(p)==="stand"?"stand:":""}${p.draftId || "main"}`;
const snap = value => Math.max(0, Math.min(project.duration, Math.round(value * project.fps) / project.fps));
const hasPending = () => Object.keys(pending).length > 0;
const pose = name => pending[name] || sampleTrack(project.tracks[name], player?.time || 0);
function status(text = "") { $("status").textContent = text; }
function fresh(character = "mano", base = "__setup__", duration = 3, target = "character", model = "cb") {
  let next = { ...newMotion(character, base, duration), draftId:id(), name:`${NAMES[OFFICE_CHARACTER_KEYS.indexOf(character)]} · ${base==="__setup__"?"新动作":CLIPS[base]||base}`,
    target, modelKind:model, sourceEnd:base === "__setup__" ? 0 : duration, sourceLoop:false };
  const initial = target === "bird" ? ["Pchan","wing_L","wing_R"] : model === "stand" ? ["head"] : ["head","body","arm_L_top","arm_R_top"];
  for (const name of initial) next = setMotionKey(next, name, 0, NEUTRAL);
  return next;
}
function drafts() {
  const all = new Map();
  for (let i=0; i<localStorage.length; i++) {
    const key=localStorage.key(i); if (!key.startsWith("sc-office-motion-draft:")) continue;
    try { const p=parseMotion(localStorage.getItem(key)), old=all.get(keyOf(p));
      if (!old || (p.updatedAt||0)>(old.updatedAt||0)) all.set(keyOf(p),p); } catch { /* Skip invalid drafts. */ }
  }
  return [...all.values()].sort((a,b)=>(b.updatedAt||0)-(a.updatedAt||0));
}
function projectList() {
  $("projects").replaceChildren(...drafts().map(p=>h("option", { value:keyOf(p), text:`${modelKind(p)==="stand"?"立绘 · ":""}${p.name || `${p.character} · 草稿`}` })));
  $("projects").value=keyOf(project);
}
function persist() {
  if (!project) return;
  try { project.updatedAt=Date.now(); localStorage.setItem(keyOf(project),JSON.stringify(project));
    localStorage.setItem(LAST,keyOf(project)); $("saved").textContent="✓"; projectList(); }
  catch { $("saved").textContent=""; status("本机保存失败，可导出 JSON"); }
}
function snapshot() { return { project:clone(project), pending:clone(pending), time:player.time, bone }; }
function history() { undoStack.push(snapshot()); if (undoStack.length>60) undoStack.shift(); redoStack=[]; syncHistory(); }
function syncHistory() { $("undo").disabled=!undoStack.length; $("redo").disabled=!redoStack.length; }
function syncControls() {
  const overlay=project.baseAnimation!=="__setup__";
  if (document.activeElement!==$("name")) $("name").value=project.name||"";
  $("character").value=project.character; $("model-kind").value=modelKind(project); $("kind").value=overlay?"overlay":"new"; $("clip").value=project.baseAnimation;
  $("clip-field").hidden=!overlay; $("source-settings").hidden=!overlay; $("original").parentElement.hidden=!overlay;
  $("bird").checked=project.target==="bird"; $("bird").parentElement.hidden=!info?.bones.some(([n])=>n==="Pchan");
  $("face-settings").hidden=modelKind(project)!=="stand";
  $("expression").value=project.expressionAnimation||""; $("gaze").value=project.gazeAnimation||"";
  if (document.activeElement!==$("duration")) $("duration").value=Number(project.duration.toFixed(3));
  if (document.activeElement!==$("start")) $("start").value=Number((project.startTime||0).toFixed(3));
  if (document.activeElement!==$("source-end")) $("source-end").value=Number((project.sourceEnd ?? (project.startTime||0)+project.duration).toFixed(3));
  $("source-loop").checked=project.sourceLoop ?? (project.sourceEnd===undefined);
  $("loop").checked=project.loop; $("total").textContent=`/ ${project.duration.toFixed(2)} s`;
  $("x-label").textContent=overlay?"偏移 X":"局部 X"; $("y-label").textContent=overlay?"偏移 Y":"局部 Y";
  $("angle-label").textContent=overlay?"旋转偏移":"旋转";
  syncHistory();
}
function inspector() {
  if (!project || !player) return;
  const current=pose(bone);
  for (const name of ["x","y","rotation"]) if (document.activeElement!==$(name)) $(name).value=Number(current[name].toFixed(3));
  const key=(project.tracks[bone]||[]).find(k=>Math.abs(k.time-player.time)<.5/project.fps);
  if (pending[bone]?.curve) $("curve").value=pending[bone].curve;
  else if (key) $("curve").value=key.curve||"smooth";
  $("remove-key").disabled=!key || $("original").checked; $("pending").textContent=hasPending()?`未记录 ×${Object.keys(pending).length}`:"";
  $("add-key").classList.toggle("primary",hasPending());
  const comparing=$("original").checked;
  for (const name of ["x","y","rotation","reset-bone","add-key","curve","expression","gaze"]) $(name).disabled=comparing;
  drawGizmo();
}
function svg(tag, attributes={}) {
  const element=document.createElementNS(NS,tag);
  for (const [key,value] of Object.entries(attributes)) element.setAttribute(key,String(value));
  return element;
}
function chooseBone(name, repaint=true) {
  bone=name; if(!Array.from($("bone").options).some(o=>o.value===name)){$("bone-search").value="";boneOptions();} $("bone").value=name; player.setSelectedBone(name); inspector(); if (repaint && !drag) timeline();
}
function availableBones() {
  return (info.bones||[]).filter(([name])=>project.target==="bird" ? ["Pchan","wing_L","wing_R"].includes(name) : project.baseAnimation!=="__setup__" || !["Pchan","wing_L","wing_R"].includes(name));
}
function boneOptions() {
  const query=$("bone-search").value.trim().toLowerCase(), common=new Set((info.commonBones||[]).map(([name])=>name));
  const options=availableBones().filter(([name,label])=>!query||`${name} ${label}`.toLowerCase().includes(query));
  const groups=[h("optgroup",{label:"常用部位"}),h("optgroup",{label:"全部骨骼"})];
  for(const[name,label] of options) groups[common.has(name)?0:1].append(h("option",{value:name,text:label,title:name}));
  $("bone").replaceChildren(...groups.filter(g=>g.children.length)); $("bone").value=bone;
}
function boneControls() {
  const options=availableBones();
  $("bone-search").value="";boneOptions();
  if (!options.some(([name])=>name===bone)) bone=options[0]?.[0]||"head";
  $("bone").value=bone; points.clear(); $("handles").replaceChildren();
  player.setSelectedBone(bone);
  const links=svg("g"); $("handles").append(links);
  for (const [name,label] of options) {
    const group=svg("g",{class:"bone-hit",tabindex:0,role:"button","aria-label":label});
    const title=svg("title"); title.textContent=`${label} · 拖动移动`;
    group.append(svg("circle",{class:"hit",r:12}),svg("circle",{class:"bone-point",r:4}),title);
    group.addEventListener("pointerdown",e=>startBoneDrag(e,name,"move"));
    group.addEventListener("keydown",e=>nudge(e,name,"move"));
    group.style.display="none"; $("handles").append(group); points.set(name,{group,link:svg("line",{class:"bone-link"})}); links.append(points.get(name).link);
  }
  gizmo=svg("g"); gizmo.append(svg("circle",{r:39,class:"gizmo-ring"}));
  xAxis=svg("path",{d:"M 10 0 H 53 M 47 -4 L 53 0 L 47 4",class:"gizmo-axis-x","stroke-width":3,role:"button",tabindex:0,"aria-label":"水平移动"});
  yAxis=svg("path",{d:"M 0 -10 V -53 M -4 -47 L 0 -53 L 4 -47",class:"gizmo-axis-y","stroke-width":3,role:"button",tabindex:0,"aria-label":"垂直移动"});
  knob=svg("circle",{r:7,class:"gizmo-rotate",role:"button",tabindex:0,"aria-label":"旋转手柄"});
  const tip=svg("title"); tip.textContent="拖动旋转"; knob.append(tip);
  knob.addEventListener("pointerdown",e=>startBoneDrag(e,bone,"rotate"));
  knob.addEventListener("keydown",e=>nudge(e,bone,"rotate"));
  xAxis.addEventListener("pointerdown",e=>startBoneDrag(e,bone,"x"));
  yAxis.addEventListener("pointerdown",e=>startBoneDrag(e,bone,"y"));
  xAxis.addEventListener("keydown",e=>nudge(e,bone,"move")); yAxis.addEventListener("keydown",e=>nudge(e,bone,"move"));
  const center=svg("circle",{r:7,class:"gizmo-center",role:"button",tabindex:0,"aria-label":"移动所选部位"});
  center.addEventListener("pointerdown",e=>startBoneDrag(e,bone,"move"));center.addEventListener("keydown",e=>nudge(e,bone,"move"));
  gizmo.append(xAxis,yAxis,knob,center); $("handles").append(gizmo);
}
function drawGizmo() {
  if (!frame || !gizmo) return;
  for (const entry of points.values()) {entry.group.style.display="none";entry.link.style.display="none";}
  for (const point of frame.points) {
    const entry=points.get(point.name); if (!entry) continue;
    entry.group.style.display="";
    entry.group.setAttribute("transform",`translate(${point.x} ${point.y})`);
    entry.group.classList.toggle("selected",point.name===bone);
    const parent=frame.points.find(p=>p.name===point.parent);
    entry.link.style.display=parent?"":"none";
    if (parent) for (const [key,value] of Object.entries({x1:parent.x,y1:parent.y,x2:point.x,y2:point.y})) entry.link.setAttribute(key,value);
  }
  const center=frame.points.find(p=>p.name===bone);
  gizmo.style.display=center && !$("original").checked?"":"none";
  if (center) { gizmo.setAttribute("transform",`translate(${center.x} ${center.y})`);
    knob.setAttribute("cx",Math.cos(center.angle)*39); knob.setAttribute("cy",Math.sin(center.angle)*39); }
}
function onFrame(next) {
  frame=next;
  $("play").textContent=next.playing?"Ⅱ":"▶"; $("play").setAttribute("aria-label",next.playing?"暂停":"播放");
  if (document.activeElement!==$("time-number")) $("time-number").value=next.time.toFixed(3);
  for (const cursor of cursors) cursor.style.left=`${next.time/project.duration*100}%`;
  $("timeline").querySelector('[role="slider"]')?.setAttribute("aria-valuenow",next.time.toFixed(3));
  if (!drag) inspector(); else drawGizmo();
}
function commit(next, { save=true, repaint=true }={}) {
  project=next; player.setProject(project,pending); syncControls(); inspector();
  if (repaint && !drag) timeline();
  if (save) { status(); persist(); }
}
function seek(value) {
  player.play(false);
  if (hasPending()) { history(); pending={}; player.setWorkingPose({}); }
  player.seek(Math.max(0,Math.min(project.duration,value))); selection=null; inspector();
  if (!drag) timeline();
}
function timeline() {
  if (drag) return;
  cursors=[]; $("timeline").replaceChildren();
  const makeTrack=()=>{const track=h("div",{class:"timeline-track"});
    const steps=Math.max(2,Math.min(12,Math.ceil(project.duration)));
    for (let i=0;i<=steps;i++) {const t=project.duration*i/steps;
      const tick=h("span",{class:"timeline-tick"}); tick.style.left=`${i/steps*100}%`;track.append(tick);}
    const cursor=h("i",{class:"timeline-cursor"});cursor.style.left=`${player.time/project.duration*100}%`;cursors.push(cursor);track.append(cursor);return track;};
  const ruler=makeTrack(); ruler.setAttribute("role","slider");ruler.setAttribute("aria-label","时间轴");ruler.setAttribute("tabindex","0");
  ruler.setAttribute("aria-valuemin","0");ruler.setAttribute("aria-valuemax",project.duration);ruler.setAttribute("aria-valuenow",player.time.toFixed(3));
  ruler.querySelectorAll(".timeline-tick").forEach((el,i,all)=>{el.textContent=(project.duration*i/(all.length-1)).toFixed(1);});
  ruler.addEventListener("pointerdown",e=>{if(e.button!==0)return;e.preventDefault();seekAt(e,ruler);ruler.setPointerCapture(e.pointerId);
    const move=event=>seekAt(event,ruler),up=()=>{ruler.removeEventListener("pointermove",move);ruler.removeEventListener("pointerup",up);ruler.removeEventListener("pointercancel",up);ruler.removeEventListener("lostpointercapture",up);};
    ruler.addEventListener("pointermove",move);ruler.addEventListener("pointerup",up);ruler.addEventListener("pointercancel",up);ruler.addEventListener("lostpointercapture",up);});
  ruler.addEventListener("keydown",e=>{if(!["Home","End","ArrowLeft","ArrowRight","PageUp","PageDown"].includes(e.key))return;e.preventDefault();
    seek(e.key==="Home"?0:e.key==="End"?project.duration:player.time+(["ArrowLeft","PageDown"].includes(e.key)?-1:1)*(e.key.startsWith("Page")?project.duration/10:1/project.fps));$("timeline").querySelector('[role="slider"]')?.focus();});
  $("timeline").append(h("div",{class:"timeline-row timeline-ruler"},h("span",{class:"timeline-label",text:"全部部位"}),ruler));
  const names=new Set([...Object.keys(project.tracks),...Object.keys(pending),bone]);
  for (const name of [...names].sort((a,b)=>(info.bones||[]).findIndex(([n])=>n===a)-(info.bones||[]).findIndex(([n])=>n===b))) {
    const track=makeTrack();
    for (const key of project.tracks[name]||[]) {
      const button=h("button",{class:`timeline-key${Math.abs(key.time-player.time)<.5/project.fps?" active":""}`,text:"◆",type:"button",
        "aria-label":`${LABELS[name]||name} ${key.time.toFixed(3)} 秒`,title:`${key.time.toFixed(3)} s · ${key.rotation.toFixed(1)}°`});
      button.style.left=`${key.time/project.duration*100}%`;
      button.addEventListener("pointerdown",e=>startKeyDrag(e,name,key,track,button));
      button.addEventListener("keydown",e=>keyKeyboard(e,name,key));track.append(button);
    }
    $("timeline").append(h("div",{class:`timeline-row${bone===name?" selected":""}`},
      h("button",{class:"timeline-label",text:LABELS[name]||name,onclick:()=>chooseBone(name)}),track));
  }
}
function seekAt(event,track) {
  // Keep the ruler in place while scrubbing; only keys/cursors need to change later.
  player.play(false); if (hasPending()) {history();pending={};player.setWorkingPose({});}
  const rect=track.getBoundingClientRect(); player.seek(snap((event.clientX-rect.left)/rect.width*project.duration));inspector();
}
function keyKeyboard(event,name,key) {
  if(!["ArrowLeft","ArrowRight","Delete","Backspace","Enter"].includes(event.key))return;
  event.preventDefault();event.stopPropagation();chooseBone(name,false);
  if(event.key==="Enter"){seek(key.time);return;} if($("original").checked)return;history();
  if(["ArrowLeft","ArrowRight"].includes(event.key))pending={};
  const at=snap(key.time+(event.key==="ArrowLeft"?-1:1)/project.fps);
  if(["Delete","Backspace"].includes(event.key))commit(removeMotionKey(project,name,key.time));
  else {commit(moveMotionKey(project,name,key.time,at));player.seek(at);timeline();
    $("timeline").querySelector(`[aria-label="${LABELS[name]||name} ${at.toFixed(3)} 秒"]`)?.focus();}
}
function startKeyDrag(event,name,key,track,button) {
  if(event.button!==0)return;event.preventDefault();event.stopPropagation();player.play(false);button.focus();chooseBone(name,false);
  if($("original").checked){seek(key.time);return;}
  drag={kind:"key",pointer:event.pointerId,name,key,track,button,startX:event.clientX,width:track.getBoundingClientRect().width,
    before:snapshot(),redo:redoStack.slice(),changed:false};button.setPointerCapture(event.pointerId);
  button.addEventListener("pointermove",moveDrag);button.addEventListener("pointerup",finishDrag,{once:true});button.addEventListener("pointercancel",cancelDrag,{once:true});
}
function screenPoint(event) {
  const r=$("handles").getBoundingClientRect(),scale=Math.min(r.width/800,r.height/450);
  return {x:(event.clientX-r.left-(r.width-800*scale)/2)/scale,y:(event.clientY-r.top-(r.height-450*scale)/2)/scale};
}
function alignEditTime() {player.play(false);player.seek(snap(player.time));}
function startBoneDrag(event,name,kind) {
  if(event.button!==0 || $("original").checked)return;
  event.preventDefault();event.stopPropagation();event.currentTarget.focus();alignEditTime();chooseBone(name,false);
  const point=screenPoint(event), center=frame.points.find(p=>p.name===name);
  drag={kind,pointer:event.pointerId,name,start:point,center,pose:clone(pose(name)),before:snapshot(),redo:redoStack.slice(),changed:false,
    previousAngle:Math.atan2(point.y-center.y,point.x-center.x),angle:0,sign:player.rotationSign(name)};
  $("handles").setPointerCapture(event.pointerId);
}
function editPose(name,value) {
  if($("auto-key").checked) {delete pending[name];project=setMotionKey(project,name,player.time,value,$("curve").value);}
  else pending={...pending,[name]:{...value,curve:$("curve").value}};
  commit(project,{save:$("auto-key").checked&&!drag,repaint:false});
}
function moveDrag(event) {
  if(!drag || drag.pointer!==event.pointerId)return;
  if(drag.kind==="key") {
    const at=snap(drag.key.time+(event.clientX-drag.startX)/drag.width*project.duration);
    if(!drag.changed && at===drag.key.time)return;
    if(!drag.changed){history();drag.changed=true;pending={};}
    project=moveMotionKey(drag.before.project,drag.name,drag.key.time,at);player.setProject(project,pending);player.seek(at);
    drag.button.style.left=`${at/project.duration*100}%`;selection={bone:drag.name,time:at};inspector();return;
  }
  const point=screenPoint(event), dx=point.x-drag.start.x,dy=point.y-drag.start.y;
  if(!drag.changed && Math.hypot(dx,dy)<2)return;
  if(!drag.changed){history();drag.changed=true;}
  const value={...drag.pose};
  if(drag.kind==="rotate") {
    const angle=Math.atan2(point.y-drag.center.y,point.x-drag.center.x);
    drag.angle+=Math.atan2(Math.sin(angle-drag.previousAngle),Math.cos(angle-drag.previousAngle))*180/Math.PI;drag.previousAngle=angle;
    value.rotation+=drag.angle*drag.sign;
  } else {const d=player.localDelta(drag.name,drag.kind==="y"?0:dx,drag.kind==="x"?0:dy);value.x+=d.x;value.y+=d.y;}
  editPose(drag.name,value);
}
function finishDrag() {
  if(!drag)return;
  const done=drag;drag=null;
  if(done.kind==="key") {done.button.removeEventListener("pointermove",moveDrag);if(!done.changed)seek(done.key.time);}
  if(done.changed)persist();inspector();timeline();
}
function cancelDrag() {
  if(!drag)return;const before=drag.before,changed=drag.changed,redo=drag.redo;
  if(drag.kind==="key")drag.button.removeEventListener("pointermove",moveDrag);
  drag=null;if(changed){undoStack.pop();restore(before);redoStack=redo;}timeline();syncHistory();
}
$("handles").addEventListener("pointermove",moveDrag);$("handles").addEventListener("pointerup",finishDrag);$("handles").addEventListener("pointercancel",cancelDrag);
function nudge(event,name,kind) {
  if($("original").checked)return;
  if(event.key==="Enter"){chooseBone(name);return;}
  if(!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(event.key))return;
  event.preventDefault();alignEditTime();chooseBone(name,false);history();const value={...pose(name)},n=event.shiftKey?5:1;
  if(kind==="rotate")value.rotation+=(["ArrowLeft","ArrowUp"].includes(event.key)?-n:n)*player.rotationSign(name);
  else {const d=player.localDelta(name,event.key==="ArrowLeft"?-n:event.key==="ArrowRight"?n:0,event.key==="ArrowUp"?-n:event.key==="ArrowDown"?n:0);value.x+=d.x;value.y+=d.y;}
  editPose(name,value);timeline();
}
function recordPose() {
  if($("original").checked)return;
  alignEditTime();history();const all=hasPending()?pending:{[bone]:pose(bone)};
  let next=project;for(const[name,value]of Object.entries(all))next=setMotionKey(next,name,player.time,value,value.curve||$("curve").value);
  pending={};commit(next);
}
function restore(state) {project=state.project;pending=state.pending;bone=state.bone;player.play(false);player.setProject(project,pending);player.seek(state.time);
  $("bone").value=bone;syncControls();inspector();timeline();persist();}
function undo(){if(drag)cancelDrag();if(!undoStack.length)return;redoStack.push(snapshot());restore(undoStack.pop());syncHistory();}
function redo(){if(drag)cancelDrag();if(!redoStack.length)return;undoStack.push(snapshot());restore(redoStack.pop());syncHistory();}
function busy(value){document.body.setAttribute("aria-busy",value);for(const el of document.querySelectorAll("button,input,select"))el.disabled=value;}
async function loadProject(next) {
  const previous=project, previousPending=pending;
  busy(true);status("加载中…");pending={};project=next;
  try {
    info=await player.load(project);undoStack=[];redoStack=[];$("original").checked=false;$("zoom").value="1";$("view").value="full";
    Object.assign(LABELS,Object.fromEntries(info.bones));
    const groups=new Map([['动作',[]],['表情',[]],['视线',[]],['口型',[]]]);
    for(const clip of info.animations) {
      const group=clip.name.startsWith("face_")?"表情":clip.name.startsWith("eye_")?"视线":clip.name.startsWith("lip_")?"口型":"动作";
      groups.get(group).push(h("option",{value:clip.name,text:`${CLIPS[clip.name]||clip.name} · ${clip.duration.toFixed(1)} s`}));
    }
    $("clip").replaceChildren(...[...groups].filter(([,items])=>items.length).map(([label,items])=>h("optgroup",{label},...items)));
    for(const[id,prefix] of [["expression","face_"],["gaze","eye_"]]) {
      const clips=info.animations.filter(a=>a.name.startsWith(prefix));
      $(id).replaceChildren(h("option",{value:"",text:"跟随动作"}),...clips.map(a=>h("option",{value:a.name,text:CLIPS[a.name]||a.name})));
    }
    boneControls();resizeHandles();busy(false);syncControls();inspector();timeline();persist();status();
  } catch(error) {project=previous;pending=previousPending;busy(false);if(project)syncControls();status(error.message||"模型未能加载");}
}
for(const[index,name]of OFFICE_CHARACTER_KEYS.entries())$("character").append(h("option",{value:name,text:NAMES[index]}));
$("new").onclick=()=>{persist();bone="head";loadProject(fresh(project.character,"__setup__",3,"character",modelKind(project)));};
$("projects").onchange=()=>{const raw=localStorage.getItem($("projects").value);if(raw)loadProject(parseMotion(raw));};
let nameEditing=false;$("name").onfocus=()=>nameEditing=false;$("name").onblur=()=>nameEditing=false;
$("name").oninput=()=>{if(!nameEditing){history();nameEditing=true;}project.name=$("name").value;persist();};
$("character").onchange=()=>{persist();const character=$("character").value;bone="head";loadProject(drafts().find(p=>p.character===character&&p.target!=="bird"&&modelKind(p)===modelKind(project))||fresh(character,"__setup__",3,"character",modelKind(project)));};
$("model-kind").onchange=()=>{persist();const model=$("model-kind").value;bone="head";loadProject(drafts().find(p=>p.character===project.character&&p.target!=="bird"&&modelKind(p)===model)||fresh(project.character,"__setup__",3,"character",model));};
$("kind").onchange=()=>{persist();const clip=info.animations.find(a=>a.name==="wait")||info.animations[0];const base=$("kind").value==="new"?"__setup__":clip.name;const duration=base==="__setup__"?3:Math.max(.1,clip.duration);loadProject(fresh(project.character,base,duration,"character",modelKind(project)));};
$("clip").onchange=()=>{history();const base=$("clip").value,len=info.animations.find(a=>a.name===base).duration;
  let next=resizeMotion(project,Math.max(.1,len));next={...next,baseAnimation:base,startTime:0,sourceEnd:len,sourceLoop:false};pending={};commit(next);seek(0);player.fit();};
$("bird").onchange=()=>{persist();const target=$("bird").checked?"bird":"character";bone=target==="bird"?"Pchan":"head";
  const next=fresh(project.character,target==="bird"?"wait":"__setup__",3,target,modelKind(project));if(target==="bird")next.sourceEnd=0;loadProject(next);};
$("bone").onchange=()=>chooseBone($("bone").value);
$("bone-search").oninput=boneOptions;
for(const[id,field] of [["expression","expressionAnimation"],["gaze","gazeAnimation"]])$(id).onchange=()=>{history();commit({...project,[field]:$(id).value});};
for(const name of ["x","y","rotation"]) {
  let editing=false;$(name).onfocus=()=>{editing=false;if(player)alignEditTime();};$(name).onblur=()=>{editing=false;inspector();timeline();};
  $(name).oninput=()=>{const value=$(name).valueAsNumber;if(!Number.isFinite(value))return;if(!editing){history();editing=true;}
    editPose(bone,{...pose(bone),[name]:value});timeline();};
}
$("reset-bone").onclick=()=>{alignEditTime();history();editPose(bone,{...NEUTRAL});timeline();};
$("add-key").onclick=recordPose;$("remove-key").onclick=()=>{history();commit(removeMotionKey(project,bone,player.time));};
$("curve").onchange=()=>{alignEditTime();history();editPose(bone,pose(bone));timeline();};
$("auto-key").onchange=()=>{if($("auto-key").checked&&hasPending())recordPose();};
$("undo").onclick=undo;$("redo").onclick=redo;
$("play").onclick=()=>{if(hasPending())seek(player.time);player.play(!player.playing);};$("rewind").onclick=()=>seek(0);
$("time-number").oninput=()=>seek(Number($("time-number").value)||0);
$("speed").onchange=()=>player.setSpeed(Number($("speed").value));$("loop").onchange=()=>{history();commit({...project,loop:$("loop").checked});};
let editingRange=false;
for(const name of ["duration","start","source-end"]) {
  $(name).onfocus=()=>{editingRange=false;player.play(false);};$(name).onblur=()=>{editingRange=false;syncControls();};
  $(name).oninput=()=>{const value=$(name).valueAsNumber;if(!Number.isFinite(value)||value<0||name==="duration"&&value===0)return;
    if(!editingRange){history();editingRange=true;}let next=clone(project);
    if(name==="duration"){next.sourceEnd??=(next.startTime||0)+next.duration;next=resizeMotion(next,value);}
    if(name==="start"){next.startTime=value;next.sourceEnd=Math.max(value,next.sourceEnd??value+next.duration);}
    if(name==="source-end"){next.sourceEnd=value;next.startTime=Math.min(value,next.startTime||0);}
    commit(next);};
}
$("source-loop").onchange=()=>{history();commit({...project,sourceLoop:$("source-loop").checked});};
$("view").onchange=()=>{$("zoom").value="1";player.setZoom(1);player.setView($("view").value);};
$("zoom").onchange=()=>player.setZoom(Number($("zoom").value));$("fit").onclick=()=>{$("zoom").value="1";$("view").value="full";player.setView("full");player.setZoom(1);player.fit();};
$("bones").onchange=()=>$("handles").style.visibility=$("bones").checked?"visible":"hidden";
$("original").onchange=()=>{player.setOriginal($("original").checked);inspector();};
$("preview").onclick=()=>{localStorage.setItem(PREVIEW,JSON.stringify(project));};
$("export").onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(project,null,2)],{type:"application/json"}));
  const link=h("a",{href:url,download:`${(project.name||"motion").replace(/[\/:*?"<>|]/g,"-")}.json`});link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);status("已导出已记录的动作");};
$("import").onclick=()=>$("file").click();$("file").onchange=async()=>{const file=$("file").files?.[0];if(!file)return;
  try {const next=parseMotion(await file.text());next.draftId=id();next.name=next.name||file.name.replace(/\.json$/i,"");await loadProject(next);status("已导入");}catch(e){status(e.message);}$("file").value="";};
$("example").onclick=async()=>{try {
  if(modelKind(project)==="stand") {
    const clip=info.animations.find(a=>a.name==="hello")||info.animations.find(a=>a.name==="wait")||info.animations[0];
    let next=fresh(project.character,clip.name,Math.max(.1,clip.duration),"character","stand");
    next=setMotionKey(next,"head",next.duration*.4,{rotation:3});
    next=setMotionKey(next,"head",next.duration*.7,{rotation:-2});
    next=setMotionKey(next,"head",next.duration,NEUTRAL);
    await loadProject(next);return;
  }
  const story=(await import("./assets/office-scenes/pigeon-mimic.js?v=1")).default;
  const next=clone(story.segments[1].motion);next.draftId=id();await loadProject(next);}catch(e){status(e.message);}};
document.addEventListener("keydown",e=>{
  if(e.key==="Escape"&&drag){cancelDrag();return;}
  if(e.defaultPrevented||e.target.closest?.("input,select,textarea,[contenteditable]"))return;
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="z"){e.preventDefault();e.shiftKey?redo():undo();}
  else if(e.code==="Space"&&!e.target.closest?.("button,a,[role=button],[role=slider]")){e.preventDefault();$("play").click();}
  else if(e.key.toLowerCase()==="k"){e.preventDefault();recordPose();}
});
function resizeHandles() {
  const rect=$("handles").getBoundingClientRect(),scale=Math.min(rect.width/800,rect.height/450)||1;
  for(const circle of $("handles").querySelectorAll(".hit"))circle.setAttribute("r",Math.max(12,11/scale));
  for(const element of $("handles").querySelectorAll(".gizmo-rotate,.gizmo-center"))element.setAttribute("r",Math.max(7,7/scale));
}
const resizeObserver=new ResizeObserver(resizeHandles);resizeObserver.observe($("stage"));
try {
  busy(true);player=await createMotionPlayer($("stage"),onFrame);
  let next;try {const last=localStorage.getItem(LAST);if(last&&localStorage.getItem(last))next=parseMotion(localStorage.getItem(last));}catch{}
  const requestedModel=new URLSearchParams(location.search).get("model");
  if(["cb","stand"].includes(requestedModel)) {
    const character=next?.character||"mano";
    next=drafts().find(p=>p.character===character&&modelKind(p)===requestedModel&&p.target!=="bird")||fresh(character,"__setup__",3,"character",requestedModel);
  }
  const example=new URLSearchParams(location.search).get("example");
  const entry=findTheaterScene(example);
  if(entry) {
    const story=await loadTheaterScene(example),requestedActor=new URLSearchParams(location.search).get("actor");
    const actor=story.actors.find(item=>item.id===(requestedActor||entry.editActor))||story.actors[0];
    const segment=actor.segments.find(item=>Object.keys(item.motion.tracks).length)||actor.segments[0];
    const saved=drafts().find(p=>p.exampleId===example&&(p.exampleActor===actor.id||!p.exampleActor&&p.character===segment.motion.character));
    if(saved)next=saved;
    else {next=clone(segment.motion);next.draftId=id();next.exampleId=example;next.exampleActor=actor.id;
      next.name=`${entry.title} · ${theaterIdolName(actor.id)}`;}
  }
  await loadProject(next||drafts()[0]||fresh());
}catch(e){status(e.message);}
window.addEventListener("pagehide",()=>{resizeObserver.disconnect();persist();player?.destroy();});
