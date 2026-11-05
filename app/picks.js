// SPDX-License-Identifier: AGPL-3.0-or-later
// The picks, as the page holds them: updated on screen at once, written through one ordered queue of partial updates
// ({ id, picked?, note?, cut? } or a whole group), retried while the server can't be reached, and never overwritten by a
// poll that started before a local write (the audit's stale-poll bug). A note is saved per item, debounced by itself.
import { api, ApiError } from "./api.js";
import { sleep } from "./ui.js";

export const NOTE_DEBOUNCE_MS = 500;
const RETRY_MIN_MS = 2000;
const RETRY_MAX_MS = 15000;

export function createPicks({ onChange, onError }) {
  let data = {}; // id -> { picked, note, at, cut? }; replaced, never mutated, so a change is a new object
  let rev = 0;
  let writes = 0; // local writes started, so a poll can tell it began before one
  let stale = false; // a write was refused: the next poll must overwrite what the page assumed
  let loaded = false; // the first answer always counts, whatever its rev
  let sending = false;
  const queue = []; // { body, done }
  const notes = new Map(); // id -> { value, timer }

  const emit = () => onChange?.(data);
  const busy = () => queue.length > 0 || sending || notes.size > 0;

  function local(id, p) {
    const next = { ...(data[id] || { picked: false, note: "" }) };
    if ("picked" in p) next.picked = !!p.picked;
    if ("note" in p) next.note = p.note;
    if ("cut" in p) { if (p.cut) next.cut = p.cut; else delete next.cut; }
    data = { ...data, [id]: next };
  }

  function enqueue(body) {
    writes++;
    return new Promise((done) => {
      queue.push({ body, done });
      pump();
    });
  }

  async function pump() {
    if (sending) return;
    sending = true;
    let delay = RETRY_MIN_MS;
    while (queue.length) {
      const op = queue[0];
      try {
        const res = await api("/api/picks", { method: "POST", body: op.body });
        queue.shift();
        rev = res.data?.rev ?? rev;
        if (!queue.length && res.data?.picks) { data = res.data.picks; emit(); }
        onError?.(null);
        delay = RETRY_MIN_MS;
        op.done(true);
      } catch (e) {
        // a refusal (bad body, unreadable picks file) won't succeed on a retry; a lost connection or a 5xx will
        if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
          queue.shift();
          stale = true;
          onError?.(`Pick not saved: ${e.message}`);
          op.done(false);
        } else {
          onError?.(`Can't save picks (${e.message}); trying again`);
          await sleep(delay);
          delay = Math.min(RETRY_MAX_MS, delay * 2);
        }
      }
    }
    sending = false;
  }

  function sendNote(id) {
    const n = notes.get(id);
    if (!n) return Promise.resolve(true);
    clearTimeout(n.timer);
    notes.delete(id);
    return enqueue({ id, note: n.value });
  }

  return {
    get data() { return data; },
    get rev() { return rev; },
    get writes() { return writes; },
    get busy() { return busy(); },

    // A poll's answer; `startedAt` is `writes` as it was when that poll's request went out. Ignored when a local write
    // happened since or is still on its way: the write's own answer is newer.
    load(res, startedAt) {
      if (writes !== startedAt || busy()) return false;
      const same = loaded && !stale && JSON.stringify(res.picks || {}) === JSON.stringify(data);
      rev = res.rev;
      if (same) return false;
      loaded = true;
      stale = false;
      data = res.picks || {};
      emit();
      return true;
    },

    // Partial update of one item: the fields given change, the others stay (cut: null clears it).
    patch(id, p) {
      local(id, p);
      emit();
      return enqueue({ id, ...p });
    },

    // One pick per group, atomically: `picked` is the id to keep picked (null: none); the others of `ids` are unpicked.
    pickInGroup(ids, picked) {
      for (const id of ids) if (id === picked || data[id]?.picked) local(id, { picked: id === picked });
      emit();
      return enqueue({ group: ids, picked });
    },

    // The item's note, saved NOTE_DEBOUNCE_MS after the last key for that item alone.
    note(id, value) {
      local(id, { note: value }); // no redraw per key: the lists update when the save comes back
      const n = notes.get(id) || {};
      clearTimeout(n.timer);
      notes.set(id, { value, timer: setTimeout(() => sendNote(id), NOTE_DEBOUNCE_MS) });
    },

    // Saves a pending note now (on blur, on leaving the item).
    flush(id) { return sendNote(id); },

    // The page is going away: everything unsent goes out with keepalive.
    flushAll() {
      for (const [id, n] of notes) { clearTimeout(n.timer); queue.push({ body: { id, note: n.value }, done() {} }); }
      notes.clear();
      for (const { body } of queue) api("/api/picks", { method: "POST", body, keepalive: true }).catch(() => {});
    },
  };
}
