import { createMotionPlayer } from "./office-motion-player.js?v=7";

export async function createMotionSequence(host, segments, onFrame) {
  const motions = segments.map(s => ({ ...s.motion, loop:false }));
  const duration = motions.reduce((sum,p) => sum+p.duration,0);
  const player = await createMotionPlayer(host);
  // Keep one camera for the complete original range and all authored segments.
  const end = Math.max(...motions.map(p => p.sourceEnd ?? p.startTime+p.duration));
  await player.load({ ...motions[0], startTime:0, sourceEnd:end, duration:end, tracks:{}, sourceLoop:false });
  let time=0, playing=false, active=-1, raf=null, previous=0, lastDraw=0;
  function draw() {
    let start=0, index=motions.length-1;
    for(let i=0;i<motions.length;i++){if(time<start+motions[i].duration || i===motions.length-1){index=i;break;}start+=motions[i].duration;}
    if(index!==active){active=index;player.setProject(motions[index],{});}
    player.seek(time-start);
    player.canvas.dataset.storyTime=time.toFixed(3);player.canvas.dataset.segment=String(index+1);
    onFrame({time,duration,playing});
  }
  function tick(now) {
    if(!playing)return;
    const delta=(now-previous)/1000;previous=now;
    time=Math.min(duration,time+delta);
    if(now-lastDraw>=1000/30 || time>=duration){lastDraw=now;draw();}
    if(time>=duration){playing=false;draw();raf=null;}else raf=requestAnimationFrame(tick);
  }
  function play(value) {
    if(raf!==null)cancelAnimationFrame(raf);raf=null;
    if(value && time>=duration)time=0;
    playing=value;previous=performance.now();lastDraw=0;draw();
    if(playing&&!document.hidden)raf=requestAnimationFrame(tick);
  }
  const visibility=()=>{if(raf!==null)cancelAnimationFrame(raf);raf=null;
    if(playing&&!document.hidden){previous=performance.now();raf=requestAnimationFrame(tick);}};
  document.addEventListener("visibilitychange",visibility);draw();
  return {play,seek(value){play(false);time=Math.max(0,Math.min(duration,value));draw();},
    get playing(){return playing;},destroy(){if(raf!==null)cancelAnimationFrame(raf);document.removeEventListener("visibilitychange",visibility);player.destroy();}};
}
