// Synthetic produce-end payloads for the browser preview (?open=produce-end / produce-end-plain). Not game data:
// the EX skill names are placeholders; numbers follow the 2026-10-05 demo end screens (#962/#964/#965).
export const SAMPLE_END_PAYLOAD = {
  version: 1,
  summary: {
    scenario: "wing", rank: "B+", final_fans: 529253, affection: 25,
    stats: { vo: 1492, da: 400, vi: 339, me: 656 }, stat_caps: { vo: 1492, da: 545, vi: 595, me: 675 },
    sp_lost: 248, live_skills: ["cheer+", "DIVEIN!", "DIVEIN!+"],
    ex_skills: [
      { name: "示例 EX（Vo 初期値UP）", tier: "rainbow", target: "produce", idol: null },
      { name: "示例 EX（Me 上限UP）", tier: "gold", target: "support", idol: null },
      { name: "示例 EX", tier: "silver", target: "produce", idol: null },
      { name: null, tier: null, target: null, idol: null },
    ],
    knowhow_books: [{ name: null, level: 3, count: 1 }],
    rewards: [], trust_level: 7, communication_level: [6, 35], wing_won: true, true_end: true, fes_idol_created: true,
    screens_seen: ["Evaluation", "FesBirth", "FesBirthConfirm", "TotalEval", "ClearReward", "Reward"],
    unknowns: ["ex_tier"], best_ex_tier: "rainbow",
  },
  recommendation: {
    suggestion: "keep", reasons: ["ex_tier_rainbow", "rank_B+", "true_end"], default_choice: "keep",
    choices: ["keep", "later_in_game"], requires_user_confirmation: true, auto_action: null,
    knowhow_book: { suggestion: "keep", auto_action: null },
  },
  notifications: [
    { key: "ex_rainbow", tone: "accent", message: "获得虹色 EX 技能：示例 EX（Vo 初期値UP）" },
    { key: "ex_gold", tone: "success", message: "获得金色 EX 技能：示例 EX（Me 上限UP）" },
    { key: "ex_tier_unread", tone: "warning", message: "有 EX 技能稀有度未读，请在游戏内确认" },
    { key: "knowhow_book", tone: "info", message: "获得ノウハウブック 1 冊（Lv.3）；売却/保護 请在游戏内自行决定" },
    { key: "true_end", tone: "success", message: "已看到 True End" },
    { key: "sp_lost", tone: "info", message: "誕生时剩余 SP 248 已失去" },
  ],
  stop_reason: "produce_end_review",
  auto_delete: false,
};

// A low-rank run where the user opted into release suggestions: still keep by default, nothing is executed.
export const PLAIN_END_PAYLOAD = {
  version: 1,
  summary: {
    scenario: "wing", rank: "D", final_fans: 41200, stats: { vo: 410, da: 220, vi: 198, me: 160 }, stat_caps: {},
    sp_lost: null, live_skills: [], ex_skills: [{ name: "示例 EX", tier: "bronze", target: "support", idol: null }],
    knowhow_books: [], trust_level: null, true_end: false, unknowns: ["sp_lost", "knowhow_books", "trust_level"], best_ex_tier: "bronze",
  },
  recommendation: { suggestion: "release_candidate", reasons: ["rank_D_below_C"], default_choice: "keep", requires_user_confirmation: true, auto_action: null },
  notifications: [
    { key: "true_end_missing", tone: "warning", message: "本次未确认 True End" },
    { key: "release_candidate", tone: "warning", message: "按你的规则这张フェスアイドル可考虑在游戏内移籍；Maa 不会执行，默认保留" },
    { key: "unknowns", tone: "warning", message: "未读字段：sp_lost、knowhow_books、trust_level" },
  ],
  stop_reason: "produce_finished_home",
  auto_delete: false,
};
