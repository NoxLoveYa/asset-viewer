// SPDX-License-Identifier: AGPL-3.0-or-later
// The review board's preview pane: the selected item in its own view, beside the list. The same views as the catalog
// (views/model.js, image.js, a plugin's...) are mounted into it by a view host: one live view at a time. The 3D stage is
// the page's one WebGL context, so its element moves into the pane while the board is up and goes home when it leaves.
import { el, button } from "../ui.js";
import { createViewHost } from "../viewhost.js";

// base: the catalog's view context. loaderFor(kind): the view loader. open(id): show an item in the catalog's own view.
export function createPane({ base, loaderFor, open }) {
  const home = base.stage3d.parentNode;
  let asked = null, dead = false;

  const root = el("aside", "rpane");
  root.setAttribute("aria-label", "Preview");
  root.hidden = true;
  const top = el("div", "ptop");
  const name = el("span", "pname");
  const badge = el("span", "rbadge");
  const full = button("Full view", () => asked && open(asked.id), { title: "Open in the catalog's view" });
  top.append(name, badge, el("span", "grow"), full);
  const meta = el("div", "pmeta");
  const stage = el("div", "pstage");
  const view2d = el("div", "pv2d");
  const bar = el("div", "win pbar");
  bar.setAttribute("role", "toolbar");
  bar.setAttribute("aria-label", "Preview controls");
  bar.hidden = true;
  const hint = el("div", "note");
  stage.append(view2d, bar, hint);
  root.append(top, meta, stage);
  const host = createViewHost({ view2d, bar, loaderFor: (kind) => (kind === "sound" ? null : loaderFor(kind)),
    ctx: { ...base, stage3d: base.stage3d, meta: (t) => { meta.textContent = t; } } });

  function unmount() {
    host.clear();
    if (base.stage3d.parentNode !== home) home.prepend(base.stage3d);
    base.stage3d.hidden = true;
  }

  return {
    el: root,
    get hidden() { return root.hidden; },
    set hidden(v) { root.hidden = v; },

    // Asks for row (null: none); the last asked wins.
    preview(row) {
      asked = row;
      const it = row?.item;
      const viewable = !!it && it.kind !== "sound" && !!loaderFor(it.kind);
      name.textContent = row ? row.entry.name || it?.name || row.id : "";
      badge.textContent = row ? (row.ref ? "Reference" : "Candidate") : "";
      badge.hidden = !row;
      badge.classList.toggle("cand", !!row && !row.ref);
      hint.hidden = viewable;
      hint.replaceChildren(el("b", null, !row ? "Nothing selected" : !it ? "Missing" : "Sound"),
        document.createTextNode(!row ? "Click an item to preview it here." : !it ? row.id : "Sounds play on their row (Space)."));
      if (!viewable) meta.textContent = "";
      if (root.hidden || dead) return;
      if (!viewable) return unmount();
      stage.prepend(base.stage3d);
      host.show(it).catch((e) => console.warn("review preview:", e));
    },

    unmount,
    key: (e) => !!host.view?.key?.(e), // the preview's own keys: W S F (models), T and Space (images), a plugin's
    destroy() { dead = true; unmount(); }, // hands the 3D stage back to the catalog
  };
}
