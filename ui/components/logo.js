// App logo: ui/icon.png, built by tools/make_app_icon.py from browser_host/app_icon.png (same picture as the window icon).
import { h } from "./dom.js";

/** Inline logo; decorative unless `label` is given. */
export function appLogo(size = 24, { label = null } = {}) {
  return h("img", {
    class: "app-logo", src: "icon.png", width: String(size), height: String(size), draggable: "false",
    alt: label ?? "", "aria-hidden": label ? null : "true",
  });
}
