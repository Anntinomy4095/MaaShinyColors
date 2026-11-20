// Last observed Match boundaries, for display only; the task always reads fresh game pages before choosing a stage.
import { gameDay } from "./store.js";

const STAGE = /^A[1-8]-S([1-9]|1[0-9]|2[0-5])$/;
const stageText = (id) => typeof id === "string" && STAGE.test(id) ? id.replace(/^A/, "").replace("-S", "-") : null;

export function matchProgressSummary(state, now = new Date()) {
  const rows = (state.runReadings || []).filter((r) => {
    const source = r.mode || (state.runs || []).find((run) => (run.run_id || run.id) === r.run_id)?.mode;
    const at = Date.parse(r.ts || "");
    return source === "supervised_daily" && r.kind === "match" && r.readings?.progress
      && Number.isFinite(at) && at <= now.getTime() && gameDay(new Date(at)) === gameDay(now);
  }).sort((a,b) => Date.parse(b.ts) - Date.parse(a.ts));
  const row = rows[0];
  if (!row) return "";
  const progress = row.readings.progress;
  const latest = stageText(progress.latest_open_stage);
  const skippable = progress.skip_list_complete === true ? stageText(progress.latest_skippable_stage) : null;
  const at = new Date(row.ts).toLocaleTimeString("zh-CN", {hour:"2-digit",minute:"2-digit",hour12:false});
  return `上次读取 ${at}：最新开放 ${latest || "尚未读取"}；最新可跳过 ${skippable || (progress.skip_list_complete ? "尚未读取" : "列表未读完")}`;
}
