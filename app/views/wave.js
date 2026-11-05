// SPDX-License-Identifier: AGPL-3.0-or-later
// Everything sound shares, so the sound view and the review board's rows don't each carry a copy: one AudioContext, a
// decode cache (bounded by bytes, at most MAX_DECODES in flight), peak/RMS levels and the waveform painter.
import { color, onChange } from "../theme.js";
import { timedFetch } from "../api.js";
import { url } from "../ui.js";

const MAX_DECODES = 4;
const MAX_AUDIO_BYTES = 128 * 1024 * 1024; // decoded float32 samples kept, oldest dropped first
const FETCH_TIMEOUT_MS = 30000;
const HANDLE_PX = 2; // a cut's edge handles and the playhead, in css px
const BYTES_PER_SAMPLE = 4;

let ac;
export const audioContext = () => (ac ||= new AudioContext());

// ---------- decode cache ----------
const cache = new Map(); // path?mtime -> { buffer, bytes }, least recently used first
const inflight = new Map();
const queue = [];
let cacheBytes = 0;
let running = 0;

// The decoded AudioBuffer of a sound item (cached by path and mtime; a failure is not cached).
export function loadBuffer(item) {
  const key = item.path + "?" + item.mtime;
  const hit = cache.get(key);
  if (hit) {
    cache.delete(key);
    cache.set(key, hit);
    return Promise.resolve(hit.buffer);
  }
  if (inflight.has(key)) return inflight.get(key);
  const p = new Promise((resolve, reject) => {
    queue.push({ item, key, resolve, reject });
    pump();
  });
  const done = () => inflight.delete(key);
  p.then(done, done);
  inflight.set(key, p);
  return p;
}

function pump() {
  while (running < MAX_DECODES && queue.length) {
    const job = queue.shift();
    running++;
    decode(job).then(job.resolve, job.reject).finally(() => { running--; pump(); });
  }
}

async function decode({ item, key }) {
  const r = await timedFetch(url(item.path, item.mtime), {}, FETCH_TIMEOUT_MS);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  const buffer = await audioContext().decodeAudioData(await r.arrayBuffer());
  const bytes = buffer.length * buffer.numberOfChannels * BYTES_PER_SAMPLE;
  cache.set(key, { buffer, bytes });
  cacheBytes += bytes;
  for (const [k, v] of cache) {
    if (cacheBytes <= MAX_AUDIO_BYTES || cache.size <= 1) break;
    cache.delete(k);
    cacheBytes -= v.bytes;
  }
  return buffer;
}

// ---------- levels ----------
export function levels(b) {
  let peak = 0, sum = 0, n = 0;
  for (let c = 0; c < b.numberOfChannels; c++) {
    const d = b.getChannelData(c);
    for (let i = 0; i < d.length; i++) { const v = Math.abs(d[i]); if (v > peak) peak = v; sum += v * v; n++; }
  }
  return { peak, rms: n ? Math.sqrt(sum / n) : 0 };
}

export const toDb = (v) => (v > 0 ? (20 * Math.log10(v)).toFixed(1) : "-inf");

// ---------- painting ----------
const peaks = new WeakMap(); // buffer -> { w, lo, hi }: min / max per pixel column, worked out once per buffer and width

function columns(buffer, w) {
  const have = peaks.get(buffer);
  if (have?.w === w) return have;
  const data = buffer.getChannelData(0);
  const step = data.length / w;
  const out = { w, lo: new Float32Array(w), hi: new Float32Array(w) };
  for (let x = 0; x < w; x++) {
    let lo = 1, hi = -1;
    const end = Math.min(data.length, Math.floor((x + 1) * step) + 1);
    for (let s = Math.floor(x * step); s < end; s++) { const v = data[s]; if (v < lo) lo = v; if (v > hi) hi = v; }
    out.lo[x] = lo;
    out.hi[x] = hi;
  }
  peaks.set(buffer, out);
  return out;
}

let palette = null;
onChange(() => { palette = null; });
const colours = () => (palette ||= {
  axis: color("wave-axis").css, base: color("wave-base").css, kept: color("wave-kept").css, dim: color("wave-dim").css,
  played: color("wave-played").css, head: color("wave-head").css, playhead: color("wave-playhead").css,
  cutFill: color("wave-cut-fill").css, cutHandle: color("wave-cut-handle").css,
});

// Paints buffer on canvas (sized by its css box). Options:
//   playhead  seconds into the file while playing (null: not playing), from: where the playing part began
//   cut       [start, end] s the part to keep: dims the rest, draws handles
//   boost     gain applied to the drawing (match levels)
//   axis      a centre line (the sound view)
//   rest      which token colours the bars: "base" (the sound view) or "kept" (the review board, with the cut dimmed)
//   head      "head" (accent) or "playhead" (the board's)
export function drawWave(canvas, buffer, { playhead = null, from = 0, cut = null, boost = 1, axis = false, rest = "base", head = "head" } = {}) {
  if (!buffer) return;
  const dpr = devicePixelRatio || 1;
  const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
  if (!w || !h) return;
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;
  const g = canvas.getContext("2d");
  g.clearRect(0, 0, w, h);
  const c = colours();
  const { lo, hi } = columns(buffer, w);
  const px = (t) => (t / buffer.duration) * w;
  const on = playhead !== null;
  const headX = on ? px(playhead) : -1;
  const fromX = on ? px(from) : 0;
  const cutX = cut && [px(cut[0]), px(cut[1])];
  if (cutX) { g.fillStyle = c.cutFill; g.fillRect(cutX[0], 0, cutX[1] - cutX[0], h); }
  if (axis) { g.fillStyle = c.axis; g.fillRect(0, h / 2, w, 1); }
  for (let x = 0; x < w; x++) {
    const top = Math.min(1, hi[x] * boost), bottom = Math.max(-1, lo[x] * boost);
    const kept = !cutX || (x >= cutX[0] && x <= cutX[1]);
    g.fillStyle = on && x >= fromX && x <= headX ? c.played : kept ? c[rest] : c.dim;
    g.fillRect(x, ((1 - top) / 2) * h, 1, Math.max(1, ((top - bottom) / 2) * h));
  }
  if (cutX) {
    g.fillStyle = c.cutHandle;
    g.fillRect(cutX[0], 0, HANDLE_PX * dpr, h);
    g.fillRect(cutX[1] - HANDLE_PX * dpr, 0, HANDLE_PX * dpr, h);
  }
  if (on) { g.fillStyle = c[head]; g.fillRect(headX, 0, Math.max(1, dpr), h); }
}
