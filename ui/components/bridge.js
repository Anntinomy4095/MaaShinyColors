// Host bridge. Inside the BrowserHost UI WebView2 (window.chrome.webview) every request is checked against the
// ui/protocol.json whitelist before it is posted. Outside WebView2 a MockBridge answers from synthetic fixtures
// so the whole window can be previewed in an ordinary browser. Neither bridge can enable live input or spending.

const TIMEOUT_MS = 10000;

function checkParams(spec, params) {
  if (typeof params !== "object" || params === null || Array.isArray(params)) throw new Error("params 必须为对象");
  const allowed = Object.keys(spec.params?.properties || {});
  for (const key of Object.keys(params)) {
    if (!allowed.includes(key)) throw new Error(`参数 ${key} 不在协议内`);
  }
  for (const key of spec.params?.required || []) {
    if (!(key in params)) throw new Error(`缺少参数 ${key}`);
  }
}

class BaseBridge extends EventTarget {
  constructor(protocol) {
    super();
    this.protocol = protocol;
    this.seq = 0;
  }
  spec(cmd) {
    const spec = this.protocol?.commands?.[cmd];
    if (!spec) throw new Error(`命令不在白名单内：${cmd}`);
    return spec;
  }
  emit(event, data) {
    this.dispatchEvent(new CustomEvent(event, { detail: data }));
  }
}

export class HostBridge extends BaseBridge {
  constructor(protocol, webview) {
    super(protocol);
    this.kind = "host";
    this.webview = webview;
    this.pending = new Map();
    webview.addEventListener("message", (event) => this.receive(event.data));
  }
  request(cmd, params = {}, timeoutMs = TIMEOUT_MS) {
    const spec = this.spec(cmd);
    checkParams(spec, params);
    this.seq += 1;
    const id = `ui-${this.seq}`;
    const message = { v: 1, id, cmd, params };
    const size = JSON.stringify(message).length;
    if (size > (this.protocol.limits?.max_message_bytes || 65536)) throw new Error("消息过长");
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`${cmd} 超时`)); }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.webview.postMessage(message);
    });
  }
  receive(message) {
    if (!message || typeof message !== "object" || message.v !== 1) return;
    if (typeof message.id === "string" && this.pending.has(message.id)) {
      const { resolve, reject, timer } = this.pending.get(message.id);
      clearTimeout(timer);
      this.pending.delete(message.id);
      if (message.ok) resolve(message.result || {}); else reject(new Error(String(message.error || "请求被拒绝")));
      return;
    }
    if (typeof message.event === "string" && this.protocol.events?.[message.event]) {
      this.emit(message.event, message.data || {});
    }
  }
}

// Plain step sentences of the simulated daily run (the real ones come from agent/core/live_status.py).
const MOCK_STEPS = {
  Startup: "确认游戏画面", Shop: "读取今天的折扣商品", Training: "打开训练，选择 S 卡", MatchSkip: "读取 Match 关卡进度",
  GradeFes: "读取参加权", MissionStatus: "读取每日任务进度", MissionsClaim: "领取デイリー奖励", Business: "查看营业状态",
  PresentClaim: "查看礼物箱", CollaboFes: "从首页横幅进入 Collabo Fes",
};

/** Synthetic engine for the browser preview: walks the enabled tasks with fixture outcomes. Never clicks anything. */
export class MockBridge extends BaseBridge {
  constructor(protocol, fixtures) {
    super(protocol);
    this.kind = "mock";
    this.fixtures = fixtures;
    this.timers = [];
    this.run = null;
    this.logSeq = Math.max(0, ...(fixtures.demo.logs || []).map((l) => l.seq));
  }
  async request(cmd, params = {}) {
    const spec = this.spec(cmd);
    checkParams(spec, params);
    switch (cmd) {
      case "ui.ready": return { protocol_version: 1, host: "ui_preview" };
      case "schema.get": return { schema: this.fixtures.schema, annotations: this.fixtures.annotations };
      case "profile.get": return { profile: loadLocal("sc-ui-profile") || { schema_version: 1 } };
      case "profile.save": saveLocal("sc-ui-profile", params.profile); return { saved: true };
      case "status.subscribe": this.statusTicks(Boolean(params.enabled)); this.livePlayback(Boolean(params.enabled)); return { subscribed: Boolean(params.enabled) };
      case "log.subscribe": return { subscribed: Boolean(params.enabled) };
      case "run.start": return this.start(params);
      case "run.stop": return this.stop(params.kind);
      // Developer tools exist only in the game host; the preview never records or reloads the action table.
      case "dev.action_table":
      case "game.data.clear":
      case "game.audio.set":
      case "window.background":
      case "cards.scan.start":
      case "cards.scan.stop":
      case "cards.scan.status":
      case "knowhow.scan.start":
      case "knowhow.scan.stop":
      case "knowhow.scan.status":
      case "storage.clean":
      case "storage.auto": throw new Error("界面预览不支持此功能");
      // 设置 → 关于: the preview shows the schema version only; logs, reports, links, update checks and the hotkey are host features.
      case "app.support":
        if (params.op !== "info") throw new Error("界面预览不支持此功能");
        return { version: this.fixtures.schema?.interface?.version || "—", build: { kind: "preview" }, repo: null, links: [], hotkey_keys: "Ctrl + Alt + Shift + S",
          licenses: [{ name: "MaaFramework（maafw）", license: "LGPL-3.0" }] };
      // 面板位置: with ?winlayout=1 the preview echoes the side in status.update.window_layout; no window moves.
      case "window.layout": {
        const demo = this.fixtures.demo;
        if (!demo.host?.window_layout?.supported) throw new Error("界面预览不支持此功能");
        demo.host = { ...demo.host, window_layout: { ...demo.host.window_layout, panel_side: params.panel_side } };
        this.emit("status.update", demo.host);
        return { accepted: true, panel_side: params.panel_side };
      }
      // 设置 → 存储: the preview shows the fixture sizes (status.update.storage); it never deletes anything.
      case "storage.status": return { storage: this.fixtures.demo.host?.storage || null };
      default: throw new Error(`未实现：${cmd}`);
    }
  }
  /** Preview-only ?ticks=1: a host-like status.update once per second (frame age moves, nothing else) to check flicker. */
  statusTicks(enabled) {
    clearInterval(this.statusTimer);
    if (!enabled || new URLSearchParams(globalThis.location?.search || "").get("ticks") !== "1") return;
    let seq = 0;
    this.statusTimer = setInterval(() => {
      seq += 1;
      this.emit("status.update", { ...this.fixtures.demo.host, frame_age_ms: 200 + (seq * 37) % 800, capture_seq: seq });
    }, 1000);
  }
  /** Host-like status.update carrying the live status snapshot (the host reads dev_assets/private/live-status/current.json). */
  emitLive(snapshot) {
    this.lastLive = snapshot;
    this.emit("status.update", { ...this.fixtures.demo.host, run_live: { ...snapshot, age_s: 1 } });
  }
  /**
   * Preview-only ?live=wing: plays a recorded W.I.N.G. run through the 「运行中」 view -- ui/game-assets/live-replay.js
   * (private, tools/live_status_replay.py from real segment logs) or the synthetic ui/fixtures/live-demo.js. &frame=N
   * freezes on frame N (screenshots). Never clicks anything.
   */
  async livePlayback(enabled) {
    const params = new URLSearchParams(globalThis.location?.search || "");
    if (!enabled || params.get("live") !== "wing" || this.playing) return;
    this.playing = true;
    const bust = `?t=${Date.now()}`;  // the replay file is regenerated between previews
    const data = await import(`../game-assets/live-replay.js${bust}`).catch(() => import(`../fixtures/live-demo.js${bust}`));
    const frames = data.default?.frames || [];
    if (!frames.length) return;
    this.run = { id: "preview-wing", tasks: ["Wing"], index: 0 };
    const at = params.get("frame");
    if (at !== null) {
      const n = Number(at);
      this.emitLive(frames[Math.max(0, Math.min(frames.length - 1, n < 0 ? frames.length + n : n))]);
      return;
    }
    frames.forEach((frame, i) => this.timers.push(setTimeout(() => {
      this.emitLive(frame);
      if (frame.state === "finished") this.finish("运行结束（预览回放）");
    }, 400 + i * 900)));
  }
  /** Generic live snapshot for the simulated daily run (same shape as agent/core/live_status.py). */
  liveStep(mutate) {
    if (!this.live) return;
    mutate(this.live);
    this.live.elapsed_s = Math.round((Date.now() - this.liveStarted) / 1000);
    this.live.updated_at = new Date().toISOString();
    this.live.progress = { done: this.live.tasks.filter((t) => !["pending", "running"].includes(t.state)).length, total: this.live.tasks.length };
    this.emitLive(JSON.parse(JSON.stringify(this.live)));
  }
  log(level, task, kind, text) {
    this.logSeq += 1;
    this.emit("log.append", { seq: this.logSeq, ts: new Date().toISOString(), level, task, kind, text });
  }
  start({ mode, config, spend_flags: flags }) {
    if (this.run) throw new Error("已有运行进行中");
    const runId = `preview-${Date.now().toString(36)}`;
    const tasks = config.tasks.filter((t) => t.enabled !== false).map((t) => t.name);
    this.run = { id: runId, tasks, index: -1 };
    this.emit("run.update", { run_id: runId, state: "starting", task: null });
    this.log("info", null, "run", `开始（预览，假数据）：${tasks.length} 个任务${mode === "offline_replay" ? "，离线回放" : ""}`);
    if (flags.length) this.log("debug", null, "run", `消耗开关：${flags.join(", ")}`);
    const outcomes = this.fixtures.demo.mock_outcomes || {};
    const label = (name) => this.fixtures.annotations.tasks?.[name]?.name || this.fixtures.schema.tasks.find((t) => t.name === name)?.label || name;
    this.liveStarted = Date.now();
    this.live = { version: 1, run_id: runId, state: "running", outcome: null, outcome_text: null, started_at: new Date().toISOString(),
      tasks: tasks.map((n) => ({ name: n, label: label(n), state: "pending", text: "等待中" })), current: null, step: "准备中",
      clicks: { used: 0 }, last_frame_at: new Date().toISOString(), wing: null };
    this.liveStep(() => {});
    const setTask = (name, state, text) => this.liveStep((l) => { const t = l.tasks.find((x) => x.name === name); if (t) { t.state = state; t.text = text; } });
    let delay = 500;
    tasks.forEach((name) => {
      this.timers.push(setTimeout(() => {
        this.emit("run.update", { run_id: runId, state: "running", task: name, status: "running", reason: null });
        this.log("info", name, "task_start", `${label(name)} 开始`);
        setTask(name, "running", "进行中");
        this.liveStep((l) => { l.current = name; l.step = `开始：${label(name)}`; });
      }, delay));
      this.timers.push(setTimeout(() => this.liveStep((l) => { l.step = `${label(name)}：${MOCK_STEPS[name] || "读取画面"}`; l.last_frame_at = new Date().toISOString(); }), delay + 550));
      delay += 1100;
      this.timers.push(setTimeout(() => {
        const outcome = outcomes[name] || { status: "succeeded", reason: null };
        setTask(name, { succeeded: "done", failed: "failed", skipped: "skipped" }[outcome.status] || "stopped", outcome.reason || "完成");
        this.emit("run.update", { run_id: runId, state: "running", task: name, status: outcome.status, reason: outcome.reason });
        this.log(outcome.status === "failed" ? "error" : ["uncertain", "guard_refused", "takeover"].includes(outcome.status) ? "warn" : "info",
          name, "task_end", `${label(name)} 结束：${outcome.reason || "完成"}`);
      }, delay));
      delay += 300;
    });
    this.timers.push(setTimeout(() => this.finish("运行结束（预览）"), delay));
    return { run_id: runId };
  }
  stop(kind) {
    if (!this.run) return { accepted: false };
    this.timers.forEach(clearTimeout);
    this.timers = [];
    this.log(kind === "emergency" ? "error" : "warn", null, "stop", kind === "emergency" ? "紧急停止：后续点击一律拒绝；本次运行结果不确定，需人工核对" : "已请求停止：当前任务按中断结算");
    this.emit("run.update", { run_id: this.run.id, state: "stopping", task: null, status: kind === "emergency" ? "uncertain" : "skipped", reason: kind });
    this.finish("运行已停止");
    return { accepted: true };
  }
  finish(text) {
    if (!this.run) return;
    const id = this.run.id;
    this.run = null;
    if (this.live) {
      this.liveStep((l) => { l.state = "finished"; l.outcome = text.includes("停止") ? "stopped" : "succeeded"; l.outcome_text = text; l.current = null; l.step = text;
        l.tasks.forEach((t) => { if (t.state === "pending") { t.state = "skipped"; t.text = "未开始"; } else if (t.state === "running") { t.state = "stopped"; t.text = "已中断"; } }); });
      this.live = null;
    } else if (this.lastLive && this.lastLive.state !== "finished") {
      this.emitLive({ ...this.lastLive, state: "finished", outcome: "stopped", outcome_text: text, current: null, step: text });
    }
    this.log("info", null, "run", text);
    this.emit("run.update", { run_id: id, state: "finished", task: null });
  }
}

export function loadLocal(key) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function saveLocal(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    /* storage unavailable: preview keeps working without persistence */
    return false;
  }
}

export function inWebView() {
  return Boolean(window.chrome && window.chrome.webview && typeof window.chrome.webview.postMessage === "function");
}
