// SPDX-License-Identifier: AGPL-3.0-or-later
// Review mode: only what the review file puts up for review, on one board: each group's candidates with what the
// author says of them, sounds played inline with their waveform, any other kind previewed in a pane beside the list
// (the catalog's own view, mounted there), one pick per group, the user's note beside each. An item with "ref": true is
// there for comparison only (no Pick); a group of nothing but those is a reference group, not counted. The nav lists the
// reviews (picked / groups) and, under the current one, its groups and what each has picked.
// One review is drawn at a time (so its sounds load alone); "All reviews" draws every one in one scroll.
import { el, button, seg, activatable, pref, setPref, oneOf, isString } from "./ui.js";
import { audioContext, loadBuffer, levels, drawWave } from "./views/wave.js";
import { createPane } from "./review/pane.js";

const MATCH_PEAK = 0.89; // -1 dBFS: "match levels" plays and draws every clip at this peak
const MAX_BOOST = 16; // a near-silent file is boosted at most this much
const EDGE_PX = 6; // how near a cut's edge a pointer grabs it
const DRAG_PX = 4; // a pointer moves this far before a click becomes a drag
const MIN_CUT_S = 0.05; // a shorter drag is no cut
const LAZY_MARGIN = "300px"; // sound rows decode once this near the viewport
const NOTE_MAX = 2000;

// A review's link name: its title as a-z, 0-9 and dashes (#review/<slug>).
const slug = (s) => String(s || "review").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

// A group of reference items only: shown, never picked, not counted.
export const refGroup = (g) => (g.items || []).length > 0 && g.items.every((i) => i.ref);

// Groups with a pick, of all groups to pick from, over every review.
export function progress(reviews, picks) {
  let done = 0, total = 0;
  for (const r of reviews) for (const g of r.groups || []) {
    if (refGroup(g)) continue;
    total++;
    if ((g.items || []).some((i) => !i.ref && picks[i.id]?.picked)) done++;
  }
  return { done, total };
}

// board: the element to draw into; nav: the nav's list. Options:
//   open(id)      show one item in the catalog's view         picks      the picks store (picks.js)
//   want          the review a link named (slug or title)     loaderFor  kind -> view loader (the preview pane's)
//   ctx           the catalog's view context (the pane borrows its 3D stage)
//   stamp(item)   what a re-bake changes, to tell a changed item from an untouched one
//   config        the page config (data paths for the empty-state hint)
export function mount({ board, nav }, { open, picks, want, loaderFor, ctx: base, stamp, config }) {
  const audio = audioContext();
  const gain = audio.createGain();
  gain.connect(audio.destination);
  let volume = pref("sound.volume", 80);
  let autoplay = pref("sound.autoplay", true);
  let filter = pref("review.filter", "all", oneOf("all", "todo", "picked"));
  let match = pref("review.match", true); // every clip at one peak
  gain.gain.value = volume / 100;
  const boost = (row) => (match && row.peak > 0 ? Math.min(MAX_BOOST, MATCH_PEAK / row.peak) : 1);

  let reviews = [], byId = new Map(), signature = "";
  let groups = []; // { def, card, status, rows }
  let rows = []; // { id, entry, group, item, el, wave, playBtn, pickBtn, noteInput, buffer?, loading? }
  let sel = null, playing = null, raf = 0;
  let all = pref("review.all", false); // every review in one scroll; off: only the current one is drawn
  let cur = 0, curSlug = want || null; // the current review: the one drawn, or the one at the top of the all-reviews scroll
  let q = ""; // the index filter: part of a review's title, lower case
  let setEls = new Map(); // review index -> { el, none } of each drawn review
  let dead = false;
  const rowByEl = new Map();
  // sound rows decode when they come near the screen (or are chosen), not all at once
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      io.unobserve(e.target);
      const row = rowByEl.get(e.target);
      if (row) load(row);
    }
  }, { root: board, rootMargin: LAZY_MARGIN });

  // The nav's index filter, above its list.
  const qBox = el("div", "rq");
  const qInput = el("input");
  Object.assign(qInput, { placeholder: "Find a review   /", autocomplete: "off", spellcheck: false });
  qInput.setAttribute("aria-label", "Find a review");
  qInput.addEventListener("input", () => { q = qInput.value.trim().toLowerCase(); renderNav(); });
  qBox.append(qInput);
  nav.before(qBox);

  const isDone = (def) => (def.items || []).some((i) => !i.ref && picks.data[i.id]?.picked);
  // passes the All / To pick / Picked filter (a reference group only in All)
  const shows = (def) => filter === "all" || (!refGroup(def) && isDone(def) === (filter === "picked"));
  const matches = (r) => !q || (r.title || "").toLowerCase().includes(q);
  const hasVisible = (r) => (r.groups || []).some((g) => g.items?.length && shows(g));
  const find = (s) => (s ? reviews.findIndex((r) => slug(r.title) === slug(s)) : -1);

  // A link's review, else the one shown last time, else the first with a group still to pick.
  function startIndex() {
    for (const s of [curSlug, pref("review.current", null, isString)]) { const i = find(s); if (i >= 0) return i; }
    const todo = reviews.findIndex((r) => (r.groups || []).some((g) => !refGroup(g) && !isDone(g)));
    return todo >= 0 ? todo : 0;
  }

  const syncHash = () => reviews.length && history.replaceState(null, "", "#review/" + curSlug);

  // ---------- build ----------
  // What the board shows: the reviews and, of every item they name, whether it exists and what its re-bake changes.
  // A catalog change that touches none of those leaves the board (and its decoded sounds) alone.
  function signatureOf(nextReviews, nextById) {
    const used = new Set(nextReviews.flatMap((r) => (r.groups || []).flatMap((g) => (g.items || []).map((i) => i.id))));
    return JSON.stringify(nextReviews) + "|" + [...used].sort().map((id) => { const it = nextById.get(id); return it ? stamp(it) : "-" + id; }).join(",");
  }

  function build(nextReviews, nextById) {
    const next = signatureOf(nextReviews, nextById);
    byId = nextById;
    if (next === signature && body) {
      for (const r of rows) r.item = byId.get(r.id) || r.item; // fresh objects, same content
      return refresh();
    }
    signature = next;
    reviews = nextReviews;
    const scroll = board.scrollTop;
    cur = startIndex();
    curSlug = slug(reviews[cur]?.title);
    board.replaceChildren(header(), body || bodyEls()); // the body (and the preview in its pane) outlives a rebuild
    remount();
    board.scrollTop = scroll || (all ? setTop(cur) : 0);
    syncHash();
    reveal();
  }

  // Draws the shown reviews (all, or the current one) afresh, keeping the selected row if it is still there.
  function remount() {
    const keep = sel?.id;
    mountSets();
    refresh();
    const again = rows.find((r) => r.id === keep);
    if (again) choose(again, { scroll: false });
    else {
      preview(null);
      if (wide) { // the pane is never empty on open: the first candidate
        const rs = visibleRows();
        choose(rs.find((r) => !r.ref && r.item && r.item.kind !== "sound") || rs[0] || null, { scroll: false });
      }
    }
  }

  function mountSets() {
    stop();
    io.disconnect();
    rowByEl.clear();
    list.replaceChildren();
    groups = [];
    rows = [];
    sel = null;
    setEls = new Map();
    wide = false;
    if (!reviews.some((r) => r.groups?.length)) {
      const e = el("div", "empty");
      e.append(el("b", null, "Nothing to review"), el("span", null, `List what needs a pick in ${config.data?.review || "the review file"}.`));
      body.classList.remove("pv");
      pane.hidden = true;
      return list.append(e);
    }
    for (const i of all ? reviews.keys() : [cur]) {
      const r = reviews[i];
      const set = el("section", "rset");
      set.append(el("h3", null, r.title || "Review"));
      if (r.about) set.append(el("p", null, r.about));
      for (const def of r.groups || []) {
        set.append(groupCard(def));
        wide ||= (def.items || []).some((it) => byId.get(it.id) && byId.get(it.id).kind !== "sound" && loaderFor(byId.get(it.id).kind));
      }
      const none = el("div", "empty");
      none.append(el("b"));
      set.append(none);
      setEls.set(i, { el: set, none });
      list.append(set);
    }
    body.classList.toggle("pv", wide); // a pane beside the list when anything here is not a sound
    pane.hidden = !wide;
  }

  // ---------- the board's body: the list, and beside it the preview pane (review/pane.js) ----------
  let body, list, pane, wide = false;

  function bodyEls() {
    body = el("div", "rbody");
    list = el("div", "rlist");
    pane = createPane({ base, loaderFor, open });
    body.append(list, pane.el);
    return body;
  }

  const preview = (row) => pane?.preview(row); // the pane shows the selected row; the last asked wins

  // Scroll offset that puts review i just under the sticky header.
  const setTop = (i) => Math.max(0, (setEls.get(i)?.el.offsetTop ?? 0) - (head?.offsetHeight || 0) - 12);

  // Makes review i the current one: draws it alone (or scrolls to it in the all-reviews scroll) and selects its first
  // group, or its last when at is "last" (stepping back in from the next review).
  function show(i, at = "first") {
    cur = i;
    curSlug = slug(reviews[i].title);
    setPref("review.current", curSlug);
    syncHash();
    if (all) board.scrollTop = setTop(i);
    else { remount(); board.scrollTop = 0; }
    const gs = groups.filter((g) => !g.card.hidden && g.rows.length && (reviews[i].groups || []).includes(g.def));
    const g = at === "last" ? gs[gs.length - 1] : gs[0];
    choose(g?.rows[0] || null, { scroll: false });
    if (g && at === "last") g.card.scrollIntoView({ block: "nearest" });
    renderNav();
    reveal();
  }

  // Next review in direction d that the index filter shows (and, with needGroups, that has a group the pick filter
  // shows); -1 at the end.
  function step(d, needGroups) {
    for (let i = cur + d; i >= 0 && i < reviews.length; i += d) {
      if (matches(reviews[i]) && (!needGroups || hasVisible(reviews[i]))) return i;
    }
    return -1;
  }

  const reveal = () => nav.querySelector(".ridx.sel")?.scrollIntoView({ block: "nearest" });

  // All-reviews scroll: the review at the top is the current one.
  function spy() {
    if (!all || !setEls.size) return;
    const y = board.scrollTop + head.offsetHeight + 40;
    let at = setEls.keys().next().value;
    if (board.scrollTop + board.clientHeight >= board.scrollHeight - 2) at = Math.max(...setEls.keys());
    else for (const [i, s] of setEls) if (s.el.offsetTop <= y) at = i;
    if (at === cur) return;
    cur = at;
    curSlug = slug(reviews[at].title);
    syncHash();
    renderNav();
  }
  board.addEventListener("scroll", spy, { passive: true });

  let progressEl, filterSeg, head, ro;
  function header() {
    const h = (head = el("div", "rhead"));
    ro?.disconnect(); // the pane sticks just under the header, whatever height it wraps to
    ro = new ResizeObserver(() => board.style.setProperty("--rhead", h.offsetHeight + "px"));
    ro.observe(h);
    const title = el("h2", null, "Review");
    progressEl = el("span", "progress");
    progressEl.setAttribute("aria-live", "polite");
    filterSeg = seg([["all", "All"], ["todo", "To pick"], ["picked", "Picked"]], filter, (v) => {
      filter = v;
      setPref("review.filter", v);
      applyFilter();
    }, { label: "Show" });
    const allBtn = button("All reviews", () => {
      all = !all;
      setPref("review.all", all);
      allBtn.set(all);
      remount();
      board.scrollTop = all ? setTop(cur) : 0;
    }, { on: all, title: "Every review in one scroll (off: one at a time; [ ] change review)" });
    const auto = button("Autoplay", () => { autoplay = !autoplay; setPref("sound.autoplay", autoplay); auto.set(autoplay); }, { on: autoplay });
    const level = button("Match levels", () => {
      match = !match;
      setPref("review.match", match);
      level.set(match);
      rows.forEach(draw);
      if (playing) playing.level.gain.value = boost(playing.row);
    }, { on: match, title: "Play and draw every clip at the same peak" });
    const vol = el("input", "vol");
    Object.assign(vol, { type: "range", min: 0, max: 100, value: volume, title: "Volume" });
    vol.setAttribute("aria-label", "Volume");
    vol.addEventListener("input", () => { volume = +vol.value; gain.gain.value = volume / 100; setPref("sound.volume", volume); });
    const keys = el("p", "keys", "Click or ↑ ↓: preview (drag to orbit, wheel to zoom)   ·   Drag on a waveform: keep only that part   ·   ← → group   ·   [ ] review   ·   / find a review   ·   Space play   ·   P pick   ·   N note   ·   X whole file   ·   R leave review");
    h.append(title, progressEl, el("span", "grow"), filterSeg, allBtn, level, auto, vol, keys);
    return h;
  }

  function groupCard(def) {
    const card = el("div", "rgroup");
    const head = el("header");
    const status = el("span", "rstatus");
    head.append(el("h4", null, def.label || def.id), el("span", "rkey", def.id || ""), status);
    card.append(head);
    if (def.about) card.append(el("p", null, def.about));
    const group = { def, card, status, rows: [] };
    for (const entry of def.items || []) {
      const row = rowFor(entry, group);
      group.rows.push(row);
      rows.push(row);
      card.append(row.el);
    }
    groups.push(group);
    return card;
  }

  function rowFor(entry, group) {
    const item = byId.get(entry.id);
    const sound = item?.kind === "sound";
    const r = el("div", "rrow" + (sound ? "" : " plain") + (entry.ref ? " ref" : ""));
    const row = { id: entry.id, entry, group, item, el: r, ref: !!entry.ref };
    r.tabIndex = -1; // reached by the arrow keys; the selected row is the one tab stop
    r.setAttribute("role", "group");
    r.setAttribute("aria-label", entry.name || item?.name || entry.id);
    // play (a sound) or open (anything else), then its waveform; a preview kind shows in the pane
    if (sound) {
      row.playBtn = el("button", "rplay", "▶");
      row.playBtn.type = "button";
      row.playBtn.title = "Play / stop (Space)";
      row.playBtn.setAttribute("aria-label", "Play or stop");
      row.playBtn.addEventListener("click", (e) => { e.stopPropagation(); choose(row); toggle(row); });
      row.wave = el("canvas", "rwave");
      row.wave.title = "Click: play from there. Drag: keep only that part (drag its edges to adjust)";
      row.wave.setAttribute("role", "img");
      row.wave.setAttribute("aria-label", "Waveform of " + (entry.name || entry.id));
      cutting(row);
      r.append(row.playBtn, row.wave);
      rowByEl.set(r, row);
      io.observe(r);
    } else {
      const openBtn = el("button", "rplay", "↗");
      openBtn.type = "button";
      openBtn.title = "Open in its view";
      openBtn.setAttribute("aria-label", "Open in its view");
      openBtn.disabled = !item;
      openBtn.addEventListener("click", (e) => { e.stopPropagation(); open(entry.id); });
      r.append(openBtn);
    }
    const text = el("div", "rtext");
    const line = el("div", "rline");
    const number = (entry.id.split("/").pop().match(/^(\d+)_/) || [])[1];
    row.base = [number && "#" + number, entry.by, !sound && (item ? item.kind : "missing")].filter(Boolean).join("  ·  ");
    row.meta = el("span", "rby", row.base);
    line.append(el("span", "rname", entry.name || item?.name || entry.id));
    if (row.ref) line.append(el("span", "rbadge", "Reference"));
    line.append(row.meta);
    text.append(line);
    if (entry.note) text.append(el("div", "rdesc", entry.note));
    if (!item) text.append(el("div", "rdesc red", "Missing: " + entry.id));
    if (sound) {
      row.cut = null;
      row.cutEl = el("div", "rcut");
      text.append(row.cutEl);
      cutLabel(row);
    }
    if (!row.ref) {
      row.pickBtn = button("Pick", (e) => { e.stopPropagation(); pick(row); }, { on: false });
      row.pickBtn.classList.add("rpick");
    }
    row.noteInput = el("input", "rnote");
    Object.assign(row.noteInput, { placeholder: "Note (which take, why...)", maxLength: NOTE_MAX });
    row.noteInput.setAttribute("aria-label", "Note for " + (entry.name || entry.id));
    row.noteInput.addEventListener("click", (e) => { e.stopPropagation(); choose(row, { scroll: false }); });
    row.noteInput.addEventListener("input", () => picks.note(row.id, row.noteInput.value));
    row.noteInput.addEventListener("blur", () => picks.flush(row.id));
    r.append(text, ...(row.pickBtn ? [row.pickBtn] : []), row.noteInput);
    r.addEventListener("click", () => { choose(row); if (sound) playRow(row); });
    return row;
  }

  // ---------- cut: the part of a sound to keep ----------
  const fmt = (t) => t.toFixed(2);

  function cutLabel(row) {
    if (!row.cutEl) return;
    row.cutEl.classList.toggle("empty", !row.cut);
    if (!row.cut) return row.cutEl.replaceChildren("Drag on the waveform to keep only a part");
    const clear = el("button", "rclear", "✕");
    clear.type = "button";
    clear.title = "Keep the whole file (X)";
    clear.setAttribute("aria-label", "Keep the whole file");
    clear.addEventListener("click", (e) => { e.stopPropagation(); setCut(row, null); });
    row.cutEl.replaceChildren(`Keep ${fmt(row.cut[0])} – ${fmt(row.cut[1])} s  (${fmt(row.cut[1] - row.cut[0])} s)`, clear);
  }

  function setCut(row, cut) {
    row.cut = cut;
    cutLabel(row);
    draw(row);
    picks.patch(row.id, { cut });
  }

  // The waveform: a click plays from there (to the end of the cut when inside it); a drag marks the part to keep, from
  // an edge it moves that edge; letting go saves it and plays it.
  function cutting(row) {
    const box = () => row.wave.getBoundingClientRect();
    const timeAt = (x) => Math.min(1, Math.max(0, (x - box().left) / box().width)) * row.buffer.duration;
    const edgeAt = (x) => { // 0 / 1: the cut's start / end handle under x
      if (!row.cut) return null;
      const px = (t) => box().left + (t / row.buffer.duration) * box().width;
      return Math.abs(x - px(row.cut[0])) <= EDGE_PX ? 0 : Math.abs(x - px(row.cut[1])) <= EDGE_PX ? 1 : null;
    };
    row.wave.addEventListener("click", (e) => e.stopPropagation()); // the row's own click would play from the start
    row.wave.addEventListener("pointermove", (e) => {
      if (row.buffer && !row.dragging) row.wave.style.cursor = edgeAt(e.clientX) !== null ? "ew-resize" : "crosshair";
    });
    row.wave.addEventListener("pointerdown", (e) => {
      if (!row.buffer || e.button !== 0) return;
      e.preventDefault();
      choose(row, { scroll: false });
      row.wave.setPointerCapture(e.pointerId);
      const x0 = e.clientX, t0 = timeAt(x0), edge = edgeAt(x0);
      const anchor = edge === null ? t0 : row.cut[1 - edge]; // the end that stays put
      const onMove = (m) => {
        if (!row.dragging && Math.abs(m.clientX - x0) < DRAG_PX) return;
        if (!row.dragging) stop();
        row.dragging = true;
        const t = timeAt(m.clientX);
        row.cut = [Math.min(anchor, t), Math.max(anchor, t)];
        cutLabel(row);
        draw(row);
      };
      const onUp = () => {
        row.wave.removeEventListener("pointermove", onMove);
        row.wave.removeEventListener("pointerup", onUp);
        row.wave.removeEventListener("pointercancel", onUp);
        if (!row.dragging) {
          const inside = row.cut && t0 >= row.cut[0] && t0 < row.cut[1];
          return play(row, t0, inside ? row.cut[1] : null);
        }
        row.dragging = false;
        const cut = row.cut[1] - row.cut[0] < MIN_CUT_S ? null : [+fmt(row.cut[0]), +fmt(row.cut[1])];
        setCut(row, cut);
        if (cut) play(row, cut[0], cut[1]);
      };
      row.wave.addEventListener("pointermove", onMove);
      row.wave.addEventListener("pointerup", onUp);
      row.wave.addEventListener("pointercancel", onUp);
    });
  }

  // ---------- picks ----------
  // One pick per group, in one atomic write: picking one drops the group's other pick (its note stays).
  function pick(row) {
    if (row.ref) return; // a reference is looked at, never picked
    if (picks.data[row.id]?.picked) picks.patch(row.id, { picked: false });
    else picks.pickInGroup(row.group.rows.filter((r) => !r.ref).map((r) => r.id), row.id);
  }

  // Draws the picks into the rows, the group cards and the nav.
  function refresh() {
    const data = picks.data;
    for (const g of groups) {
      const chosen = g.rows.find((r) => !r.ref && data[r.id]?.picked);
      g.card.classList.toggle("done", !!chosen);
      g.status.classList.toggle("done", !!chosen);
      const kept = chosen && data[chosen.id].cut;
      g.status.textContent = refGroup(g.def) ? "Reference"
        : chosen ? "Picked: " + (chosen.entry.name || chosen.id) + (kept ? `, ${fmt(kept[0])} – ${fmt(kept[1])} s` : "")
        : "Not picked";
      for (const r of g.rows) {
        const p = data[r.id];
        if (r.cutEl && !r.dragging && String(r.cut || null) !== String(p?.cut || null)) {
          r.cut = p?.cut || null;
          cutLabel(r);
          draw(r);
        }
        if (r.pickBtn) {
          r.el.classList.toggle("picked", !!p?.picked);
          r.pickBtn.set(!!p?.picked);
          r.pickBtn.classList.toggle("primary", !!p?.picked);
          r.pickBtn.label(p?.picked ? "Picked" : "Pick");
        }
        if (document.activeElement !== r.noteInput) r.noteInput.value = p?.note || "";
      }
    }
    if (progressEl) { const { done, total } = progress(reviews, data); progressEl.textContent = `${done} / ${total} picked`; }
    applyFilter();
    renderNav();
  }

  function applyFilter() {
    for (const g of groups) g.card.hidden = !shows(g.def);
    for (const [i, s] of setEls) {
      s.none.hidden = (reviews[i].groups || []).some((g) => shows(g));
      s.none.firstChild.textContent = filter === "todo" ? "Everything here is picked" : filter === "picked" ? "Nothing picked here yet" : "No groups in this review";
    }
    if (sel && sel.group.card.hidden) choose(visibleRows()[0] || null, { scroll: false });
  }

  // The index: every review the filter box lets through, under the part of its title before the colon, with its picked /
  // groups; the current one has its groups under it.
  function renderNav() {
    nav.replaceChildren();
    let last;
    reviews.forEach((r, i) => {
      if (!matches(r)) return;
      const m = /^([^:]+):\s*(.+)$/.exec(r.title || "");
      const head = m ? m[1] : "Other";
      if (head !== last) nav.append(el("div", "ghead", (last = head)));
      const counted = (r.groups || []).filter((g) => !refGroup(g)); // reference groups are not picked from
      const total = counted.length;
      const done = counted.filter(isDone).length;
      const row = activatable(el("div", "row item ridx" + (i === cur ? " sel" : "")), () => show(i));
      row.title = r.title || "";
      if (i === cur) row.setAttribute("aria-current", "true");
      const name = m ? m[2] : r.title || "Review";
      row.append(el("span", "star" + (total && done === total ? " on" : done ? " note" : "")),
        el("span", "name", name[0].toUpperCase() + name.slice(1)), el("span", "meta", `${done} / ${total}`));
      nav.append(row);
      if (i === cur) for (const g of groups.filter((g) => (r.groups || []).includes(g.def))) nav.append(groupNavRow(g));
    });
  }

  function groupNavRow(g) {
    const chosen = g.rows.find((x) => !x.ref && picks.data[x.id]?.picked);
    const row = activatable(el("div", "row item rgrp" + (sel?.group === g ? " sel" : "")), () => {
      if (g.card.hidden) { // filtered out: show everything again
        filter = "all";
        setPref("review.filter", filter);
        filterSeg.set(filter);
        applyFilter();
      }
      g.card.scrollIntoView({ block: "start", behavior: "smooth" });
      choose(g.rows[0] || null, { scroll: false });
    });
    row.append(el("span", "star" + (chosen ? " on" : "")), el("span", "name", g.def.label || g.def.id),
      el("span", "meta", refGroup(g.def) ? "ref" : chosen ? (chosen.entry.name || "").slice(0, 18) : "—"));
    return row;
  }

  // ---------- selection ----------
  const visibleRows = () => rows.filter((r) => !r.group.card.hidden);

  function choose(row, { scroll = true } = {}) {
    if (sel === row) return;
    if (sel) { sel.el.classList.remove("sel"); sel.el.tabIndex = -1; sel.el.removeAttribute("aria-current"); }
    sel = row;
    preview(row);
    if (!row) return renderNav();
    row.el.classList.add("sel");
    row.el.tabIndex = 0;
    row.el.setAttribute("aria-current", "true");
    if (scroll) row.el.scrollIntoView({ block: "nearest" });
    if (row.item?.kind === "sound") load(row);
    renderNav();
  }

  function move(d) {
    const list = visibleRows();
    if (!list.length) return;
    const i = list.indexOf(sel);
    const next = list[i < 0 ? 0 : Math.min(list.length - 1, Math.max(0, i + d))];
    if (next === sel) return;
    choose(next);
    if (autoplay && next.wave) playRow(next);
  }

  function moveGroup(d) {
    const gs = groups.filter((g) => !g.card.hidden && g.rows.length);
    const i = gs.indexOf(sel?.group);
    const j = i < 0 ? 0 : i + d;
    if (!all && (j < 0 || j >= gs.length)) { // past the shown review's edge: into the next one with a group to show
      const n = step(d, true);
      if (n >= 0) show(n, d < 0 ? "last" : "first");
      return;
    }
    if (!gs.length) return;
    const g = gs[Math.min(gs.length - 1, Math.max(0, j))];
    choose(g.rows[0]);
    g.card.scrollIntoView({ block: "nearest" });
  }

  // ---------- audio ----------
  // Decodes a sound row's file (shared cache, at most a few at a time); a failure is shown once, not retried in a loop.
  function load(row) {
    if (row.failed || !row.item) return Promise.resolve(null);
    row.loading ||= loadBuffer(row.item)
      .then((b) => {
        row.buffer = b;
        row.peak = levels(b).peak;
        row.meta.textContent = [row.base, b.duration.toFixed(2) + " s"].filter(Boolean).join("  ·  ");
        if (!dead) draw(row);
        return b;
      })
      .catch((e) => { row.failed = true; row.loading = null; row.meta.textContent = row.base + "  ·  can't decode (" + e.message + ")"; return null; });
    return row.loading;
  }

  function toggle(row) {
    if (playing?.row === row) stop();
    else playRow(row);
  }

  // The row's sound: its cut when it has one, else the whole file.
  const playRow = (row) => (row.cut ? play(row, row.cut[0], row.cut[1]) : play(row, 0));

  // Plays row from at (s) to end (s; null: the file's end).
  async function play(row, at, end) {
    const b = await load(row);
    if (!b || sel !== row || dead) return; // moved on while it loaded
    stop();
    audio.resume();
    const source = audio.createBufferSource();
    const level = audio.createGain();
    level.gain.value = boost(row);
    source.buffer = b;
    source.connect(level).connect(gain);
    source.onended = () => { if (playing?.source === source) stop(); };
    if (end != null) source.start(0, at, Math.max(0.01, end - at));
    else source.start(0, at);
    playing = { row, source, level, at, startedAt: audio.currentTime - at };
    row.playBtn.textContent = "■";
    row.el.classList.add("playing");
    tick();
  }

  function stop() {
    if (!playing) return;
    const { row, source, level } = playing;
    playing = null;
    source.onended = null;
    try { source.stop(); } catch { /* already stopped */ }
    level.disconnect();
    cancelAnimationFrame(raf);
    row.playBtn.textContent = "▶";
    row.el.classList.remove("playing");
    draw(row);
  }

  function tick() {
    if (!playing) return;
    draw(playing.row);
    raf = requestAnimationFrame(tick);
  }

  // min / max per pixel column (cached per buffer and width), the played part bright, a playhead while it plays
  function draw(row) {
    if (!row.buffer || !row.wave) return;
    const on = playing?.row === row;
    drawWave(row.wave, row.buffer, { playhead: on ? audio.currentTime - playing.startedAt : null, from: on ? playing.at : 0,
      cut: row.cut, boost: boost(row), rest: "kept", head: "playhead" });
  }

  const onResize = () => rows.forEach(draw);
  addEventListener("resize", onResize);
  const themeOff = base.theme.onChange(() => rows.forEach(draw));

  return {
    build,
    update: refresh,
    // a link's #review/<slug> while the board is up
    select(name) {
      const i = find(name);
      if (i >= 0) show(i);
    },
    destroy() {
      stop();
      dead = true;
      io.disconnect();
      pane?.destroy(); // hands the 3D stage back to the catalog
      ro?.disconnect();
      themeOff();
      removeEventListener("resize", onResize);
      board.removeEventListener("scroll", spy);
      gain.disconnect();
      board.replaceChildren();
      nav.replaceChildren();
      qBox.remove();
    },
    // keys while the board is up (app.js routes them here); true when handled
    key(e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { move(e.key === "ArrowDown" ? 1 : -1); return true; }
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") { moveGroup(e.key === "ArrowRight" ? 1 : -1); return true; }
      if (e.key === "[" || e.key === "]") { const n = step(e.key === "]" ? 1 : -1); if (n >= 0) show(n); return true; }
      if (e.key === "/") { qInput.focus(); return true; }
      if (!sel) { if (e.key === " ") { move(1); return true; } return false; }
      if (e.key === " " && sel.wave) { toggle(sel); return true; }
      if (e.key === "p" || e.key === "P") { pick(sel); return true; } // a reference ignores it
      if ((e.key === "x" || e.key === "X") && sel.cut) { setCut(sel, null); return true; }
      if (e.key === "n" || e.key === "N" || e.key === "Enter") { sel.noteInput.focus(); return true; }
      return !!pane?.key(e) || e.key === " "; // the preview's own keys first
    },
  };
}
