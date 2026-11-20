#!/usr/bin/env node
// Author additive motion data without loading native skeletons or textures.
import { readFile, writeFile } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { newMotion, parseMotion, sampleTrack, setMotionKey, moveMotionKey, resizeMotion } from '../ui/components/office-motion.js';

const HELP = `用法：node tools/office_motion.mjs <命令> [文件] [选项]
  new --out FILE [--character mano --base __setup__ --duration 3 --name NAME]
      [--model cb|stand --start SEC --end SEC --seed head,body --pin Pchan]
  key FILE --bone NAME --at SEC [--rotation N --x N --y N --curve smooth|linear|hold]
  move FILE --bone NAME --from SEC --at SEC
  duration FILE --seconds SEC
  source FILE --base NAME --start SEC --end SEC [--loop true|false --expression face_smile --gaze eye_left]
  inspect FILE
  module FILE --out MODULE
  scene MANIFEST --out MODULE
时间以秒计；key、move 对齐到 30 fps。写入命令直接覆盖指定文件。
双人场景：{version:2,name:"名称",duration:15,actors:[{id:"mano",x:0.7,view:"upper",segments:[{at:0,motion:"片段.json",mix:0.35}]}]}。
scene 清单：{version:1,name:"名称",segments:[{motion:"相对路径.motion.json",label:"可选标签，不显示字幕"}]}。
输出只含作者动作数据，不包含原生骨骼或图集。`;

function options(args, allowed) {
  const result = {};
  while (args.length) {
    const flag = args.shift();
    if (!flag.startsWith('--') || !allowed.includes(flag.slice(2))) throw new Error(`未知选项：${flag}`);
    const key = flag.slice(2), value = args.shift();
    if (value === undefined || value.startsWith('--')) throw new Error(`缺少 --${key} 的值`);
    if (key in result) throw new Error(`重复选项：--${key}`);
    result[key] = value;
  }
  return result;
}
function required(opts, key) {
  if (!opts[key]?.trim()) throw new Error(`缺少 --${key}`);
  return opts[key];
}
function number(opts, key, fallback) {
  const raw = opts[key] ?? fallback;
  if (raw === undefined || String(raw).trim() === '' || !Number.isFinite(Number(raw))) throw new Error(`--${key} 必须是有限数值`);
  return Number(raw);
}
function time(opts, key, fallback) {
  const value = number(opts, key, fallback);
  if (value < 0) throw new Error(`--${key} 不能小于零`);
  return value;
}
function atTime(project, opts, key) {
  const value = time(opts, key);
  if (value > project.duration) throw new Error(`--${key} 超出动作时长`);
  return value;
}
async function readMotion(file) { return parseMotion(await readFile(file, 'utf8')); }
async function save(file, value, module = false) {
  const json = JSON.stringify(value, null, 2);
  await writeFile(file, module ? `export default ${json};\n` : `${json}\n`, 'utf8');
  console.log(`已写入 ${file}`);
}
function source(project, opts) {
  project.baseAnimation = required(opts, 'base');
  project.startTime = time(opts, 'start');
  project.sourceEnd = time(opts, 'end');
  if ('loop' in opts) {
    if (!['true', 'false'].includes(opts.loop)) throw new Error('--loop 只能是 true 或 false');
    project.sourceLoop = opts.loop === 'true';
  }
  for (const [option, field] of [['expression', 'expressionAnimation'], ['gaze', 'gazeAnimation']]) {
    if (option in opts) project[field] = opts[option] === 'none' ? '' : opts[option];
  }
  return parseMotion(project);
}
async function main(args) {
  const command = args.shift();
  if (!command || ['help', '--help', '-h'].includes(command)) { console.log(HELP); return; }
  if (command === 'new') {
    const opts = options(args, ['out', 'character', 'base', 'duration', 'name', 'start', 'end', 'seed', 'model', 'pin']);
    const file = required(opts, 'out');
    let project = newMotion(opts.character || 'mano', opts.base || '__setup__', number(opts, 'duration', 3));
    if ('model' in opts) project.modelKind = opts.model;
    if ('name' in opts) project.name = required(opts, 'name');
    project.startTime = time(opts, 'start', 0);
    if ('end' in opts) project.sourceEnd = time(opts, 'end');
    if ('pin' in opts) {
      project.worldPositionBones = opts.pin.split(',').map(name => name.trim());
      if (project.worldPositionBones.some(name => !name)) throw new Error('--pin 必须是逗号分隔的骨骼名称');
    }
    project = parseMotion(project);
    if ('seed' in opts) {
      const bones = opts.seed.split(',').map(bone => bone.trim());
      if (bones.some(bone => !bone)) throw new Error('--seed 必须是逗号分隔的骨骼名称');
      for (const bone of new Set(bones)) project = setMotionKey(project, bone, 0, {});
    }
    await save(file, parseMotion(project)); return;
  }
  if (!['key', 'move', 'duration', 'source', 'inspect', 'module', 'scene'].includes(command)) throw new Error(`未知命令：${command}`);
  const file = args.shift();
  if (!file || file.startsWith('--')) throw new Error('缺少输入文件');
  if (command === 'scene') {
    const opts = options(args, ['out']), out = required(opts, 'out');
    const manifest = JSON.parse(await readFile(file, 'utf8'));
    const embed = async segment => {
      if (!segment || typeof segment.motion !== 'string' || !segment.motion.trim() || ('label' in segment && typeof segment.label !== 'string')) throw new Error('场景片段格式不正确');
      const motion = await readMotion(resolve(dirname(resolve(file)), segment.motion));
      return { ...segment, motion };
    };
    if (manifest?.version === 2) {
      if (typeof manifest.name !== 'string' || !Array.isArray(manifest.actors) || !manifest.actors.length) throw new Error('场景清单格式不正确');
      const actors = [];
      for (const actor of manifest.actors) {
        const segments = [];
        for (const segment of actor.segments) segments.push(await embed(segment));
        actors.push({ ...actor, segments });
      }
      await save(out, { ...manifest, actors }, true); return;
    }
    if (manifest?.version !== 1 || typeof manifest.name !== 'string' || !Array.isArray(manifest.segments) || !manifest.segments.length) throw new Error('场景清单格式不正确');
    const segments = [];
    for (const segment of manifest.segments) segments.push(await embed(segment));
    await save(out, { version: 1, name: manifest.name, segments }, true); return;
  }
  const allowed = { key: ['bone', 'at', 'rotation', 'x', 'y', 'curve'], move: ['bone', 'from', 'at'], duration: ['seconds'], source: ['base', 'start', 'end', 'loop', 'expression', 'gaze'], inspect: [], module: ['out'] };
  const opts = options(args, allowed[command]);
  let project = await readMotion(file);
  if (command === 'inspect') {
    console.log(JSON.stringify({ version: project.version, name: project.name, character: project.character, modelKind: project.modelKind || "cb", target: project.target,
      baseAnimation: project.baseAnimation, startTime: project.startTime, sourceEnd: project.sourceEnd,
      sourceLoop: project.sourceLoop, worldPositionBones: project.worldPositionBones, expressionAnimation: project.expressionAnimation, gazeAnimation: project.gazeAnimation, duration: project.duration, fps: project.fps,
      keyTimes: Object.fromEntries(Object.entries(project.tracks).map(([bone, keys]) => [bone, keys.map(key => key.time)])) }, null, 2));
    return;
  }
  if (command === 'module') { await save(required(opts, 'out'), project, true); return; }
  if (command === 'key') {
    const bone = required(opts, 'bone'), at = atTime(project, opts, 'at');
    const curve = opts.curve || 'smooth';
    if (!['smooth', 'linear', 'hold'].includes(curve)) throw new Error('--curve 必须是 smooth、linear 或 hold');
    // Sample at the frame that will actually receive the key, preserving unspecified components.
    const frameTime = Math.min(project.duration, Math.round(at * project.fps) / project.fps);
    const pose = sampleTrack(project.tracks[bone], frameTime);
    for (const component of ['rotation', 'x', 'y']) if (component in opts) pose[component] = number(opts, component);
    project = setMotionKey(project, bone, at, pose, curve);
  } else if (command === 'move') {
    const bone = required(opts, 'bone'), from = atTime(project, opts, 'from'), at = atTime(project, opts, 'at');
    if (!(project.tracks[bone] || []).some(key => Math.abs(key.time - from) < .5 / project.fps)) throw new Error('指定位置没有关键帧');
    project = moveMotionKey(project, bone, from, at);
  } else if (command === 'duration') project = resizeMotion(project, number(opts, 'seconds'));
  else if (command === 'source') project = source(project, opts);
  await save(file, parseMotion(project));
}
main(process.argv.slice(2)).catch(error => { console.error(`错误：${error.message}`); process.exitCode = 1; });
