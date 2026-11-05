// SPDX-License-Identifier: AGPL-3.0-or-later
// Mounts the view of an item into a place (the main area, or the review board's preview pane). One mount at a time and
// the last request wins: every mount gets its own slot in the DOM, so a mount that finishes late is destroyed and
// removed without touching the live one (the audit's mount race). A view is reused for the next item of the same loader.
import { el } from "./ui.js";

// view2d / bar: the elements the slots go into. ctx: the base view context (see app.js). loaderFor(kind) -> loader | null.
export function createViewHost({ view2d, bar, ctx, loaderFor }) {
  let seq = 0;
  let current = null; // { loader, view, slot }
  let pending = null; // the slot of a mount still in flight

  function makeSlot() {
    const slot = { live: true, view2d: el("div", "slot"), bar: el("div", "barslot") };
    view2d.append(slot.view2d);
    bar.append(slot.bar);
    slot.ctx = { ...ctx, view2d: slot.view2d, bar: slot.bar, meta: (t) => slot.live && ctx.meta(t), alive: () => slot.live };
    slot.remove = () => { slot.live = false; slot.view2d.remove(); slot.bar.remove(); };
    return slot;
  }

  function destroy() {
    if (pending) { pending.remove(); pending = null; } // a mount still in flight: its slot goes now, the mount is dropped when it ends
    if (current) {
      const { view, slot } = current;
      current = null;
      try { view.destroy(); } catch (e) { console.error("view destroy:", e); }
      slot.remove();
    }
    bar.hidden = true;
  }

  return {
    get view() { return current?.view || null; },

    // Shows item. Resolves when its view is up (or was superseded).
    async show(item) {
      const loader = loaderFor(item.kind);
      const mine = ++seq;
      if (!loader) { destroy(); ctx.meta(`No view for ${item.kind}`); return; }
      if (current && current.loader === loader) { await current.view.update(item); return; }
      destroy();
      const slot = pending = makeSlot();
      let view = null;
      try {
        const mod = await loader();
        if (mine !== seq) return slot.remove();
        view = await mod.mount(item, slot.ctx);
        if (mine !== seq) { try { view.destroy(); } catch { /* it was never shown */ } return slot.remove(); }
      } catch (e) {
        console.error("view:", e);
        if (mine === seq) {
          slot.view2d.append(el("div", "note", "Can't open this view: " + e.message));
          current = { loader: null, view: { update() {}, destroy() {} }, slot }; // the next show clears the slot and tries afresh
          pending = null;
          bar.hidden = true;
        } else slot.remove();
        return;
      }
      current = { loader, view, slot };
      pending = null;
      bar.hidden = false;
    },

    clear() { seq++; destroy(); },
  };
}
