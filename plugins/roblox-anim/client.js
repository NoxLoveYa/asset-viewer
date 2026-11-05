// SPDX-License-Identifier: AGPL-3.0-or-later
// roblox-anim, client side: the Animations category and its view (view.js).
const DEFAULT_FPS = 30;

export function register(viewer) {
  viewer.addCategory({
    kind: "anim",
    label: "Animations",
    icon: "anim",
    view: () => import("/plugins/roblox-anim/view.js"),
    rowMeta: (i) => ((i.frames - 1) / (i.fps > 0 ? i.fps : DEFAULT_FPS)).toFixed(2) + " s",
  });
  viewer.keyHelp("Space play · ← → frame · Home start · L loop · G layers · A aim held");
}
