// SPDX-License-Identifier: AGPL-3.0-or-later
// Sound view: the waveform, play / stop from any point (click it), loop, autoplay on select, volume; peak and RMS level.
import { el, button, sep, pref, setPref } from "../ui.js";
import { onChange } from "../theme.js";
import { audioContext, loadBuffer, levels, toDb, drawWave } from "./wave.js";

export async function mount(item, ctx) {
  const audio = audioContext();
  const gain = audio.createGain();
  gain.connect(audio.destination);
  let buffer, source, startedAt = 0, offset = 0, raf = 0, token = 0;
  let loop = pref("sound.loop", false);
  let autoplay = pref("sound.autoplay", true);
  let volume = pref("sound.volume", 80);
  gain.gain.value = volume / 100;

  const wave = el("div", "wave");
  const canvas = el("canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", "Waveform: click to play from there");
  wave.append(canvas);
  ctx.view2d.append(wave);

  const playBtn = button("Play", () => (source ? stop() : buffer && play(0)), { key: "Space" });
  const loopBtn = button("Loop", () => { loop = !loop; setPref("sound.loop", loop); loopBtn.set(loop); if (source) source.loop = loop; }, { key: "L", on: loop });
  const autoBtn = button("Autoplay", () => { autoplay = !autoplay; setPref("sound.autoplay", autoplay); autoBtn.set(autoplay); }, { on: autoplay });
  const vol = el("input", "vol");
  Object.assign(vol, { type: "range", min: 0, max: 100, value: volume, title: "Volume" });
  vol.setAttribute("aria-label", "Volume");
  vol.addEventListener("input", () => { volume = +vol.value; gain.gain.value = volume / 100; setPref("sound.volume", volume); });
  const time = el("span", "readout");
  ctx.bar.append(playBtn, loopBtn, autoBtn, sep(), vol, sep(), time);

  wave.addEventListener("click", (e) => {
    if (!buffer) return;
    const r = wave.getBoundingClientRect();
    play(((e.clientX - r.left) / r.width) * buffer.duration);
  });

  function position() {
    if (!source) return offset;
    const t = audio.currentTime - startedAt;
    return loop ? t % buffer.duration : Math.min(t, buffer.duration);
  }

  function play(at) {
    stop();
    audio.resume();
    source = audio.createBufferSource();
    source.buffer = buffer;
    source.loop = loop;
    source.connect(gain);
    source.onended = () => { if (source && !source.loop) stop(true); };
    offset = at;
    startedAt = audio.currentTime - at;
    source.start(0, at);
    playBtn.label("Stop");
    playBtn.set(true);
    tick();
  }

  function stop(ended) {
    if (source) { source.onended = null; try { source.stop(); } catch { /* already stopped */ } source = null; }
    offset = ended ? 0 : offset;
    cancelAnimationFrame(raf);
    playBtn.label("Play");
    playBtn.set(false);
    draw();
  }

  function tick() {
    draw();
    if (source) raf = requestAnimationFrame(tick);
  }

  function draw() {
    if (!buffer) return;
    const at = position();
    drawWave(canvas, buffer, { playhead: source ? at : null, axis: true });
    time.textContent = at.toFixed(2) + " / " + buffer.duration.toFixed(2) + " s";
  }

  async function update(next) {
    const t = ++token;
    stop();
    offset = 0;
    let b;
    try {
      b = await loadBuffer(next);
    } catch (e) {
      if (t === token) { buffer = null; canvas.width = 0; item = next; ctx.meta("Can't decode this file: " + e.message); }
      return;
    }
    if (t !== token) return;
    item = next;
    buffer = b;
    const ch = buffer.numberOfChannels === 1 ? "mono" : buffer.numberOfChannels === 2 ? "stereo" : buffer.numberOfChannels + " ch";
    const lv = levels(buffer);
    ctx.meta(`${buffer.duration.toFixed(2)} s  ·  ${(buffer.sampleRate / 1000).toFixed(1)} kHz  ·  ${ch}  ·  peak ${toDb(lv.peak)} dB  ·  RMS ${toDb(lv.rms)} dB`);
    draw();
    if (autoplay && navigator.userActivation?.hasBeenActive) play(0); // before any click the browser keeps audio muted
  }

  const onResize = () => draw();
  addEventListener("resize", onResize);
  const unsubTheme = onChange(draw);
  await update(item);
  return {
    update,
    destroy() { token++; stop(); gain.disconnect(); unsubTheme(); removeEventListener("resize", onResize); },
    key(e) {
      if (e.key === " ") { source ? stop() : buffer && play(0); return true; }
      if (e.key === "l" || e.key === "L") { loopBtn.click(); return true; }
    },
  };
}
