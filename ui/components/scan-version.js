// ES-module import cache buster for the local scan modules (ui/game-assets/*.js, rewritten by a finished host scan).
// The first load uses the plain URL; each bump appends ?v=N so a re-import fetches the new file instead of the cached one.
let version = 0;

export function bumpScanVersion() { version += 1; return version; }

export function scanSuffix() { return version > 0 ? `?v=${version}` : ""; }
