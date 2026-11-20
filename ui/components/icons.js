// Self-drawn 24x24 line icons (project-original geometry; no game or third-party icon assets).
// One consistent set: 1.5px stroke, round caps, monochrome (currentColor), rendered at 16 or 20 px.
// Kept deliberately small — most of the UI is text-first.
export const ICONS = {
  dot: { circles: [[12, 12, 2]] },
  grip: { circles: [[9, 7, 0.9], [15, 7, 0.9], [9, 12, 0.9], [15, 12, 0.9], [9, 17, 0.9], [15, 17, 0.9]] },
  chevron: { paths: ["M7 10l5 5 5-5"] },
  more: { circles: [[6, 12, 1.1], [12, 12, 1.1], [18, 12, 1.1]] },
  help: { paths: ["M9.6 9.4a2.5 2.5 0 1 1 3.4 2.3c-.6.3-1 .8-1 1.5v.3", "M12 16.6v.1"], circles: [[12, 12, 8.5]] },
  check: { paths: ["M5.5 12.5l4.2 4 8.8-9"] },
  search: { paths: ["M15.5 15.5L20 20"], circles: [[10.5, 10.5, 5.5]] },
  close: { paths: ["M6.5 6.5l11 11M17.5 6.5l-11 11"] },
  swap: { paths: ["M3 16.5v-1a4.5 4.5 0 0 1 9 0v1", "M13.8 16.5v-1a3.6 3.6 0 0 1 7.2 0v1",
    "M5 20h14m-2.6-2.5L19 20l-2.6 2.5"], circles: [[7.5, 7, 2.8], [17.4, 8, 2.4]] },
  plus: { paths: ["M12 6v12M6 12h12"] },
  // 面板位置: 拆分到独立窗口 (a window with an arrow out) / 放回游戏窗口 (a split window, arrow into the side pane).
  popout: { paths: ["M10.5 5.5h-5v13h13v-5", "M14 5.5h4.5V10", "M18.5 5.5l-7 7"] },
  dockin: { paths: ["M4.5 5.5h15v13h-15z", "M14.5 5.5v13", "M7 12h4.5", "M9.5 10l2 2-2 2"] },
  // Fallbacks for item icons when no local in-game crop exists (ui/game-assets is optional and gitignored).
  coin: { paths: ["M9.5 9.5h5M9.5 14.5h5"], circles: [[12, 12, 7.5]] },
  ticket: { paths: ["M4 7.5h16v3a1.5 1.5 0 0 0 0 3v3H4v-3a1.5 1.5 0 0 0 0-3z", "M14.5 7.5v9"] },
  flag: { paths: ["M6 20.5V4", "M6 4.5h11l-2.5 4 2.5 4H6"] },
  gem: { paths: ["M7.5 5h9l3.5 4.5L12 19.5 4 9.5z", "M4 9.5h16"] },
  card: { paths: ["M6.5 3.5h11v17h-11z", "M9 16.5h6"], circles: [[12, 10, 2.5]] },
  // 2026-10-08: task / tab / item stand-ins drawn when no in-game icon fits or the private assets are missing. Same
  // grid and stroke as above; task-icons.js shows them on a soft tinted plate (pastel game palette, tokens.css).
  home: { paths: ["M4 11.5L12 5l8 6.5", "M6.5 10v9.5h11V10", "M10.5 19.5v-4.5h3v4.5"] },
  list: { paths: ["M10 7.5h9M10 12h9M10 16.5h9", "M5 7.5h.5M5 12h.5M5 16.5h.5"] },
  can: { paths: ["M8 6.5h8l1 2v10.5a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V8.5z", "M8.5 4h7", "M12.8 10.5l-2 3h2.4l-2 3"] },
  book: { paths: ["M12 7c-2-1.5-4.5-2-7.5-1.5v13c3-.5 5.5 0 7.5 1.5 2-1.5 4.5-2 7.5-1.5v-13C16.5 5 14 5.5 12 7z", "M12 7v13"] },
  wing: { paths: ["M20 5.5c-5.5 0-9.5 2.5-11.5 7L5 18.5h6c4.5 0 7.5-4 9-13z", "M9.5 13h5", "M7.8 16h4.2"] },
  shield: { paths: ["M12 3.5l7 2.5v5.5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z", "M9 12l2 2 4-4"] },
  gear: { paths: ["M12 4v2M12 18v2M4 12h2M18 12h2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4"], circles: [[12, 12, 2.5], [12, 12, 6]] },
  shop: { paths: ["M5 9.5L6.5 5h11L19 9.5", "M5 9.5a2.3 2.3 0 0 0 4.7 0 2.3 2.3 0 0 0 4.6 0 2.3 2.3 0 0 0 4.7 0", "M6 11.5V19h12v-7.5", "M10 19v-4h4v4"] },
  mission: { paths: ["M8 5H6.5v15h11V5H16", "M9 3.8h6v2.7H9z", "M9 12.5l2 2 4-4"] },
  business: { paths: ["M5.5 4.5h10v15h-10z", "M8.5 8.5h4M8.5 11.5h4", "M18.5 6.5v10l-.9 2-.9-2v-10z"] },
  present: { paths: ["M5 10.5h14v9H5z", "M4 7.5h16v3H4z", "M12 7.5v12", "M12 7.5C11 5 7.5 4.5 7.5 6.3s3 1.2 4.5 1.2c1.5 0 4.5.6 4.5-1.2S13 5 12 7.5z"] },
  fes: { paths: ["M4 7.5h16v3a1.5 1.5 0 0 0 0 3v3H4v-3a1.5 1.5 0 0 0 0-3z", "M13 14.3V9.5l2.5-.8"], circles: [[11.8, 14.3, 1.2]] },
  calendar: { paths: ["M5 6.5h14v13H5z", "M5 10h14", "M9 4.5v3M15 4.5v3", "M8.5 13.5h2M13.5 13.5h2M8.5 16.5h2"] },
  scan: { paths: ["M4 8.5V5h3.5M16.5 5H20v3.5M20 15.5V19h-3.5M7.5 19H4v-3.5", "M13.7 13.7L16 16"], circles: [[11.5, 11.5, 3]] },
  play: { paths: ["M8.5 6.5v11l9-5.5z"] },
  stamina: { paths: ["M13 6.5l-4 6h3.5l-1.5 5 4.5-6.5h-3.5z"], circles: [[12, 12, 8.5]] },
  drink: { paths: ["M10 3.5h4v3l1.5 2.5v10.5h-7V9L10 6.5z", "M8.5 12.5h7"] },
  capsule: { paths: ["M4.5 12h5.7M13.8 12h5.7"], circles: [[12, 12, 7.5], [12, 12, 1.8]] },
};
