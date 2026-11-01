"use strict";
const FPS = 25;
const MIN_WIN = 1, WORD_WIN = 15;
const STORE = "avcc-p1-viewer";
const view = { win: 20, t0: 0, follow: true };
const $ = id => document.getElementById(id);
const video = $("video"), stage = $("stage"), overlay = $("overlay");
const tl = $("timeline"), ov = $("overview");
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const esc = s => s.replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

let splits = [], segs = [], picked = null, seg = null, loads = 0, eventMap = null;
let evFilter = "all", lastEv, lastSig = "", capHtml = "";
let pal = {}, palGen = 0;

function readColors() {
  pal = { fg: css("--fg"), mut: css("--mut"), line: css("--line"), hold: css("--hold"), shift: css("--shift"), spk: {} };
  palGen++;
}
const spkColor = s => pal.spk[s] ??= css(`--s${s}`) || pal.mut;
const spkVar = s => `var(--s${s}, var(--mut))`;
const short = s => /^\d+$/.test(s) ? `S${s}` : s;
const bySpeaker = (a, b) => (isNaN(a) - isNaN(b)) || (isNaN(a) ? a.localeCompare(b) : a - b);

function store(key) { try { localStorage.setItem(STORE, key); } catch (_) {} }
function recall() { try { return localStorage.getItem(STORE); } catch (_) { return null; } }

async function text(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.text();
}
const rows = t => t.split("\n").filter(l => l.trim()).map(l => l.split("\t"));
const item = f => ({ spk: f[2], start: +f[3], end: +f[3] + +f[4], text: f.slice(5).join("\t").trim() });

function track(items) {
  items.sort((a, b) => a.start - b.start);
  let span = 0;
  for (const x of items) span = Math.max(span, x.end - x.start);
  return { items, span };
}

function within(tr, t0, t1) {
  const a = tr.items, t = t0 - tr.span;
  let lo = 0, hi = a.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (a[mid].start < t) lo = mid + 1; else hi = mid;
  }
  const out = [];
  for (let i = lo; i < a.length && a[i].start <= t1; i++) if (a[i].end >= t0) out.push(a[i]);
  return out;
}

function indexBoxes(csv) {
  const lines = csv.split("\n"), n = lines.length;
  const frame = new Int32Array(n), who = new Uint8Array(n), xy = new Float32Array(n * 4), ids = [];
  let m = 0, last = -1;
  for (let i = 1; i < n; i++) {
    const f = lines[i].split(",");
    if (f.length < 6) continue;
    const k = +f[0];
    if (!(k >= 0)) continue;
    let s = ids.indexOf(f[1]);
    if (s < 0) s = ids.push(f[1]) - 1;
    frame[m] = k; who[m] = s;
    xy[m * 4] = +f[2]; xy[m * 4 + 1] = +f[3]; xy[m * 4 + 2] = +f[4]; xy[m * 4 + 3] = +f[5];
    if (k > last) last = k;
    m++;
  }
  const first = new Int32Array(last + 2);
  for (let i = 0; i < m; i++) first[frame[i] + 1]++;
  for (let k = 1; k < first.length; k++) first[k] += first[k - 1];
  const next = first.slice(), order = new Int32Array(m);
  for (let i = 0; i < m; i++) order[next[frame[i]]++] = i;
  return { ids, who, xy, first, order, rows: m, last };
}

async function testEvents() {
  if (!eventMap) {
    const map = new Map();
    for (const f of rows(await text("/data/test_events.txt"))) {
      if (f.length < 7) continue;
      const k = `${f[0]}/${f[1]}`;
      if (!map.has(k)) map.set(k, []);
      map.get(k).push({ prev: f[2], start: +f[3], end: +f[3] + +f[4], next: f[5], type: f[6].trim() });
    }
    eventMap = map;
  }
  return eventMap;
}

async function load(e) {
  const test = e.split === "test";
  const [w, u, b, ev] = await Promise.all([
    text(`/data/word/${e.key}.txt`), text(`/data/utterance/${e.key}.txt`),
    text(`/data/bbox/${e.key}.csv`).catch(() => ""), test ? testEvents().catch(() => new Map()) : null]);
  const words = rows(w).map(item), utts = rows(u).map(item), boxes = indexBoxes(b);
  const speakers = [...new Set([...boxes.ids, ...words.map(x => x.spk), ...utts.map(x => x.spk)])].sort(bySpeaker);
  const bySpk = new Map(speakers.map(k => [k, {
    words: track(words.filter(x => x.spk === k)),
    utts: track(utts.filter(x => x.spk === k))
  }]));
  let end = (boxes.last + 1) / FPS;
  for (const x of utts) end = Math.max(end, x.end);
  return { ...e, words, utts, boxes, speakers, bySpk, events: track(test ? ev.get(e.key) || [] : []),
    duration: e.duration || Math.max(2, end) };
}

const clock = {
  hasVideo: false, t: 0, playing: false, last: 0,
  get time() { return this.hasVideo ? video.currentTime : this.t; },
  seek(t) {
    t = Math.max(0, Math.min(seg ? seg.duration : 0, t));
    if (seg && !view.follow && (t < view.t0 || t > view.t0 + view.win)) view.t0 = t - view.win / 2;
    if (this.hasVideo) video.currentTime = t; else this.t = t;
  },
  toggle() {
    if (this.hasVideo) { if (video.paused) video.play().catch(() => {}); else video.pause(); return; }
    this.playing = !this.playing; this.last = performance.now();
  },
  get isPlaying() { return this.hasVideo ? !video.paused : this.playing; },
  tick(now) {
    if (!this.hasVideo && this.playing) {
      this.t += (now - this.last) / 1000; this.last = now;
      if (seg && this.t >= seg.duration) { this.t = seg.duration; this.playing = false; }
    }
  }
};

const fmt = t => { const m = Math.floor(t / 60), s = t - m * 60; return `${m}:${s.toFixed(1).padStart(4, "0")}`; };
const fmt2 = t => { const m = Math.floor(t / 60), s = t - m * 60; return `${m}:${s.toFixed(2).padStart(5, "0")}`; };
const fmtLen = t => { const s = Math.round(t); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

function fill(el, items, value) {
  el.replaceChildren(...items.map(([v, label]) => new Option(label, v)));
  el.value = value;
}

function showPicker(e) {
  const sp = splits.find(s => s.name === e.split), vid = sp.videos.find(v => v.id === e.video);
  fill($("split"), splits.map(s => [s.name, `${s.name} (${s.videos.length} videos)`]), e.split);
  fill($("videoPick"), sp.videos.map(v => [v.id, `${v.id} (${v.segments.length})`]), e.video);
  fill($("seg"), vid.segments.map(s => [s.id, s.duration ? `${s.id} · ${fmtLen(s.duration)}` : s.id]), e.name);
  const i = segs.indexOf(e);
  $("pos").textContent = `${i + 1} / ${segs.length}`;
  $("prevSeg").disabled = i === 0;
  $("nextSeg").disabled = i === segs.length - 1;
}

async function open(key) {
  const e = segs.find(s => s.key === key) || segs[0];
  picked = e;
  showPicker(e);
  video.pause();
  const id = ++loads;
  $("segName").textContent = `${e.key} (loading)`;
  let s;
  try { s = await load(e); }
  catch (err) {
    if (id === loads) {
      $("segName").textContent = e.key;
      $("stats").innerHTML = `<div class="err">Could not load annotations: ${esc(err.message)}</div>`;
    }
    return;
  }
  if (id !== loads) return;
  seg = { ...s, id };
  clock.t = 0; clock.playing = false;
  stage.classList.remove("missing"); clock.hasVideo = true;
  $("novideo").textContent = `Could not load videos/${seg.key}.mp4. The annotations can still be played and browsed.`;
  video.src = `/data/videos/${seg.key}.mp4`;
  video.load();
  renderSide();
  setFollow(true); setWin(Math.min(20, seg.duration));
  history.replaceState(null, "", `#${seg.key}`);
  store(seg.key);
}

function step(d) {
  const i = segs.indexOf(picked) + d;
  if (i >= 0 && i < segs.length) open(segs[i].key);
}

$("split").onchange = e => open(segs.find(s => s.split === e.target.value).key);
$("videoPick").onchange = e => open(segs.find(s => s.video === e.target.value).key);
$("seg").onchange = e => open(`${$("videoPick").value}/${e.target.value}`);
$("prevSeg").onclick = () => step(-1);
$("nextSeg").onclick = () => step(1);

video.addEventListener("error", () => { if (video.getAttribute("src")) { clock.hasVideo = false; stage.classList.add("missing"); } });
video.addEventListener("loadedmetadata", () => {
  if (seg && isFinite(video.duration) && video.duration > 0 && Math.abs(video.duration - seg.duration) > 0.01) {
    seg.duration = video.duration;
    renderSide();
    setWin(view.win);
  }
});

function renderSide() {
  const s = seg, test = s.split === "test", n = t => s.events.items.filter(e => e.type === t).length;
  const speech = {}, count = {};
  for (const u of s.utts) speech[u.spk] = (speech[u.spk] || 0) + (u.end - u.start);
  for (const w of s.words) count[w.spk] = (count[w.spk] || 0) + 1;
  const onScreen = s.speakers.filter(k => k !== "B").length;
  $("segName").textContent = s.key;
  $("stats").innerHTML = [
    ["Split", s.split],
    ["Length", fmt(s.duration)],
    ["Speakers", s.speakers.includes("B") ? `${onScreen} + B` : onScreen],
    ["Words", s.words.length.toLocaleString()],
    ["Utterances", s.utts.length.toLocaleString()],
    ["Face boxes", s.boxes.rows.toLocaleString()],
    ...(test ? [["Turn events", `${n("hold")} hold · ${n("shift")} shift`]] : []),
    ...s.speakers.map(k => [`Speaker ${k}`, `${(speech[k] || 0).toFixed(1)} s speech · ${(count[k] || 0).toLocaleString()} words`])
  ].map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join("");
  $("legend").innerHTML = s.speakers.map(k =>
    `<span><i style="background:${spkVar(k)}"></i>speaker ${k}${k === "B" ? " (off screen)" : ""}</span>`).join("")
    + (test ? `<span><i style="background:var(--hold)"></i>hold</span><span><i style="background:var(--shift)"></i>shift</span>` : "");
  $("files").innerHTML = [["word", "txt", "per word"], ["utterance", "txt", "per utterance"],
    ["rttm", "rttm", "per utterance"], ["bbox", "csv", "per frame"]]
    .map(([l, ext, what]) => `<li><a href="/data/${l}/${s.key}.${ext}"><code>${l}/${s.key}.${ext}</code></a><span>${what}</span></li>`)
    .join("") + (test ? `<li><a href="/data/test_events.txt"><code>test_events.txt</code></a><span>all test segments</span></li>` : "");
  $("prevEv").disabled = $("nextEv").disabled = !s.events.items.length;
  renderEvents();
}

function renderEvents() {
  const test = seg.split === "test";
  const list = seg.events.items.filter(e => evFilter === "all" || e.type === evFilter);
  $("evFilter").hidden = !test;
  $("evCount").textContent = test ? `(${list.length})` : "";
  $("evRows").innerHTML = !test ? `<tr class="none"><td>Only test segments have turn events.</td></tr>`
    : !list.length ? `<tr class="none"><td>No events in this segment.</td></tr>`
    : list.map(e =>
      `<tr data-t="${e.start}"><td><span class="tag ${e.type}">${e.type}</span></td>
       <td>${fmt(e.start)}</td><td>${e.prev} → ${e.next}</td><td>${Math.round((e.end - e.start) * 1000)} ms</td></tr>`).join("");
  lastEv = undefined;
  for (const tr of $("evRows").querySelectorAll("tr[data-t]"))
    tr.onclick = () => { clock.seek(+tr.dataset.t - 2); if (!clock.isPlaying) clock.toggle(); };
}
$("evFilter").onclick = e => {
  if (!e.target.dataset.f) return;
  evFilter = e.target.dataset.f;
  [...$("evFilter").children].forEach(b => b.classList.toggle("on", b === e.target));
  renderEvents();
};

function jumpEvent(dir) {
  if (!seg) return;
  const t = clock.time + 2, ev = seg.events.items;
  const target = dir > 0 ? ev.find(e => e.start > t + 0.05) : [...ev].reverse().find(e => e.start < t - 0.5);
  if (target) clock.seek(target.start - 2);
}

$("play").onclick = () => clock.toggle();
$("back").onclick = () => clock.seek(clock.time - 5);
$("fwd").onclick = () => clock.seek(clock.time + 5);
$("prevEv").onclick = () => jumpEvent(-1);
$("nextEv").onclick = () => jumpEvent(1);
document.addEventListener("change", e => { if (e.target.matches("select, input[type=checkbox]")) e.target.blur(); });
document.addEventListener("keydown", e => {
  if (!seg || e.ctrlKey || e.metaKey || e.altKey || e.target.closest?.("input, select, textarea")) return;
  if (e.code === "Space") { e.preventDefault(); document.activeElement?.blur(); clock.toggle(); }
  else if (e.key === "ArrowLeft") clock.seek(clock.time - 5);
  else if (e.key === "ArrowRight") clock.seek(clock.time + 5);
  else if (e.key === "j" || e.key === "J") jumpEvent(-1);
  else if (e.key === "k" || e.key === "K") jumpEvent(1);
  else if (e.key === "+" || e.key === "=") setWin(view.win / 1.5);
  else if (e.key === "-" || e.key === "_") setWin(view.win * 1.5);
  else if (e.key === "[") step(-1);
  else if (e.key === "]") step(1);
});

function fitCanvas(c, h) {
  const dpr = window.devicePixelRatio || 1, w = c.clientWidth;
  if (h) c.style.height = h + "px";
  const H = c.clientHeight;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(H * dpr)) {
    c.width = Math.round(w * dpr); c.height = Math.round(H * dpr);
  }
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  return [g, w, H];
}

function speaking(t, spk) {
  return within(seg.bySpk.get(spk).utts, t, t).some(u => t < u.end);
}

function drawOverlay(t) {
  const [g, W, H] = fitCanvas(overlay);
  g.clearRect(0, 0, W, H);
  const bx = seg.boxes, f = Math.floor(t * FPS + 1e-6);
  if (!$("showBox").checked || f < 0 || f + 1 >= bx.first.length) return;
  const srcW = video.videoWidth || 1920, srcH = video.videoHeight || 1080;
  let vw = W, vh = W * srcH / srcW;
  if (vh > H) { vh = H; vw = H * srcW / srcH; }
  const ox = (W - vw) / 2, oy = (H - vh) / 2, sx = vw / srcW, sy = vh / srcH;
  g.font = "600 12px system-ui, sans-serif";
  for (let j = bx.first[f]; j < bx.first[f + 1]; j++) {
    const i = bx.order[j], spk = bx.ids[bx.who[i]], col = spkColor(spk);
    const on = seg.bySpk.has(spk) && speaking(t, spk);
    const x = ox + bx.xy[i * 4] * sx, y = oy + bx.xy[i * 4 + 1] * sy;
    const w = (bx.xy[i * 4 + 2] - bx.xy[i * 4]) * sx, h = (bx.xy[i * 4 + 3] - bx.xy[i * 4 + 1]) * sy;
    g.lineWidth = on ? 3 : 1.5; g.strokeStyle = col; g.globalAlpha = on ? 1 : .7;
    g.strokeRect(x, y, w, h);
    const label = on ? `${short(spk)} speaking` : short(spk);
    const tw = g.measureText(label).width + 10, ly = Math.max(0, y - 18);
    g.fillStyle = col; g.fillRect(x, ly, tw, 18);
    g.fillStyle = "#000"; g.globalAlpha = 1; g.fillText(label, x + 5, ly + 13);
  }
  g.globalAlpha = 1;
}

function drawCaption(t) {
  let html = "";
  if ($("showCap").checked) {
    for (const k of seg.speakers) {
      const ws = within(seg.bySpk.get(k).words, t - 2.5, t);
      if (!ws.length) continue;
      const txt = ws.slice(-10).map(w => {
        const s = /^\[.*\]$/.test(w.text) ? `<i>${esc(w.text)}</i>` : esc(w.text);
        return w.start <= t && t < w.end ? `<span class="now">${s}</span>` : s;
      }).join(" ");
      html += `<div class="ln"><b style="color:${spkVar(k)}">${short(k)}</b>${txt}</div>`;
    }
  }
  if (html !== capHtml) { capHtml = html; $("caption").innerHTML = html; }
}

function lanes() { return seg.split === "test" ? ["events", ...seg.speakers] : seg.speakers; }

function viewRange(t) {
  const win = Math.min(view.win, seg.duration);
  if (!view.follow) view.t0 = Math.max(0, Math.min(seg.duration - win, view.t0));
  let t0 = view.follow ? t - win / 2 : view.t0;
  t0 = Math.max(0, Math.min(seg.duration - win, t0));
  return [t0, t0 + win, win];
}

const zoomToWin = v => MIN_WIN * Math.pow(seg.duration / MIN_WIN, v / 1000);
const winToZoom = w => Math.round(1000 * Math.log(w / MIN_WIN) / Math.log(seg.duration / MIN_WIN));
function setWin(w, anchor) {
  if (!seg) return;
  const [t0, , win] = viewRange(clock.time);
  w = Math.max(MIN_WIN, Math.min(seg.duration, w));
  if (anchor != null && !view.follow) view.t0 = anchor - (anchor - t0) * w / win;
  else if (!view.follow) view.t0 = t0 + (win - w) / 2;
  view.win = w;
  $("zoom").value = winToZoom(w);
  $("zoomVal").textContent = w >= 60 ? fmt(w).replace(/\.0$/, "") : `${w < 10 ? w.toFixed(1) : Math.round(w)} s`;
}
function setFollow(on) {
  if (!on && seg) view.t0 = viewRange(clock.time)[0];
  view.follow = on; $("follow").checked = on;
}
$("zoom").oninput = e => { if (seg) setWin(zoomToWin(+e.target.value)); };
$("zoomIn").onclick = () => setWin(view.win / 1.5);
$("zoomOut").onclick = () => setWin(view.win * 1.5);
$("zoomFit").onclick = () => { if (!seg) return; setFollow(false); setWin(seg.duration); view.t0 = 0; };
$("follow").onchange = e => setFollow(e.target.checked);

function tickStep(win, px) {
  const want = win / Math.max(4, px / 90);
  return [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600].find(s => s >= want) || 1200;
}

let hits = [];
function tipHtml(h) {
  const it = h.item;
  if (h.kind === "event") {
    const ms = Math.round((it.end - it.start) * 1000);
    return `<b>${it.type}</b> · speaker ${it.prev} → ${it.next}<br><span class="m">${fmt2(it.start)} – ${fmt2(it.end)} · gap ${ms} ms</span>`;
  }
  const len = h.kind === "word" ? `${Math.round((it.end - it.start) * 1000)} ms` : `${(it.end - it.start).toFixed(2)} s`;
  return `<b style="color:${spkVar(it.spk)}">speaker ${it.spk}</b> · ${h.kind}<br>${esc(it.text)}<br>
    <span class="m">${fmt2(it.start)} – ${fmt2(it.end)} · ${len}</span>`;
}

function drawTimeline(t) {
  const L = lanes(), laneH = 28, top = 18, labelW = 64;
  const [g, W] = fitCanvas(tl, top + L.length * laneH + 4);
  const H = tl.clientHeight;
  g.clearRect(0, 0, W, H);
  const [t0, t1, win] = viewRange(t);
  const X = s => labelW + (s - t0) / win * (W - labelW);
  const words = win <= WORD_WIN;
  hits = [];
  const step = tickStep(win, W - labelW), dec = step < 1 ? 1 : 0, minor = step / 5;
  const pxPerS = (W - labelW) / win;
  g.font = "11px system-ui, sans-serif"; g.fillStyle = pal.mut; g.strokeStyle = pal.line; g.lineWidth = 1;
  if (minor * pxPerS >= 12) {
    g.globalAlpha = .3;
    for (let k = Math.ceil(t0 / minor); k * minor <= t1 + 1e-9; k++) {
      const x = X(k * minor); g.beginPath(); g.moveTo(x, top); g.lineTo(x, H); g.stroke();
    }
    g.globalAlpha = 1;
  }
  for (let k = Math.ceil(t0 / step - 1e-9); k * step <= t1 + 1e-9; k++) {
    const sec = k * step, x = X(sec);
    g.globalAlpha = .9; g.beginPath(); g.moveTo(x, top - 4); g.lineTo(x, H); g.stroke();
    g.globalAlpha = 1;
    const m = Math.floor(sec / 60 + 1e-9), r = (sec - m * 60).toFixed(dec);
    if (x + 3 >= labelW) g.fillText(`${m}:${r.padStart(dec ? 4 : 2, "0")}`, x + 3, 11);
  }
  g.font = "600 12px system-ui, sans-serif";
  L.forEach((name, i) => {
    g.fillStyle = name === "events" ? pal.mut : spkColor(name);
    g.fillText(name === "events" ? "events" : `speaker ${name}`, 0, top + i * laneH + 18);
  });
  g.save(); g.beginPath(); g.rect(labelW, 0, W - labelW, H); g.clip();
  const lane0 = L.length - seg.speakers.length;
  if (lane0) {
    g.font = "11px system-ui, sans-serif";
    for (const e of within(seg.events, t0, t1)) {
      const x = X(e.start), w = Math.max(2, X(e.end) - x);
      g.fillStyle = e.type === "hold" ? pal.hold : pal.shift;
      g.globalAlpha = .9; g.fillRect(x, top + 5, w, laneH - 10);
      g.globalAlpha = .12; g.fillRect(x, top, w, L.length * laneH);
      g.globalAlpha = 1;
      const ms = Math.round((e.end - e.start) * 1000);
      const txt = [`${e.type} ${ms} ms`, `${ms} ms`].find(l => g.measureText(l).width + 8 <= w);
      if (txt) { g.fillStyle = "#fff"; g.fillText(txt, x + 4, top + 18); }
      hits.push({ x1: x, x2: x + w, y1: top, y2: top + laneH, kind: "event", item: e });
    }
  }
  const box = (x, y, w, h, r) => { g.beginPath(); g.roundRect ? g.roundRect(x, y, w, h, r) : g.rect(x, y, w, h); };
  const label = (txt, x, y, w) => {
    if (w < 14) return;
    g.save(); g.beginPath(); g.rect(x, y - 12, w - 3, 16); g.clip();
    g.fillStyle = "#000"; g.fillText(txt, x + 3, y); g.restore();
  };
  g.font = "12px system-ui, sans-serif";
  seg.speakers.forEach((k, i) => {
    const sp = seg.bySpk.get(k), col = spkColor(k);
    const y = top + (lane0 + i) * laneH + 3, h = laneH - 6;
    for (const u of within(sp.utts, t0, t1)) {
      const x = X(u.start), x2 = X(u.end);
      if (!words) {
        g.fillStyle = col; g.globalAlpha = .85; box(x, y, Math.max(1, x2 - x), h, 4); g.fill(); g.globalAlpha = 1;
        if (x2 - x > 30) label(u.text, Math.max(x, labelW), y + 15, x2 - Math.max(x, labelW));
        hits.push({ x1: x, x2: Math.max(x2, x + 3), y1: y, y2: y + h, kind: "utterance", item: u });
        continue;
      }
      g.fillStyle = col; g.globalAlpha = .16; box(x, y, x2 - x, h, 4); g.fill();
      g.globalAlpha = .9; g.strokeStyle = col; g.lineWidth = 1.5; box(x, y, x2 - x, h, 4); g.stroke();
      g.globalAlpha = 1;
      hits.push({ x1: x, x2, y1: y, y2: y + h, kind: "utterance", item: u });
    }
    if (!words) return;
    for (const w of within(sp.words, t0, t1)) {
      const wx = X(w.start), ww = Math.max(2, X(w.end) - wx), now = w.start <= t && t < w.end;
      g.fillStyle = col; g.globalAlpha = now ? 1 : .8; box(wx, y + 3, ww, h - 6, 3); g.fill(); g.globalAlpha = 1;
      if (now) { g.strokeStyle = pal.fg; g.lineWidth = 1.5; box(wx, y + 3, ww, h - 6, 3); g.stroke(); }
      label(w.text, wx, y + 15, ww);
      hits.push({ x1: wx, x2: wx + ww, y1: y, y2: y + h, kind: "word", item: w });
    }
  });
  g.restore();
  const px = X(t);
  if (px >= labelW && px <= W) {
    g.strokeStyle = pal.fg; g.lineWidth = 2;
    g.beginPath(); g.moveTo(px, top - 6); g.lineTo(px, H); g.stroke();
  }
  tl._view = { t0, win, labelW, W };
}

const ovBack = document.createElement("canvas");
let ovKey = "";
function drawOverview(t) {
  const n = seg.speakers.length;
  const [g, W, H] = fitCanvas(ov, Math.max(34, 8 + 8 * n));
  const dpr = window.devicePixelRatio || 1, X = s => s / seg.duration * W;
  const key = [seg.id, W, H, dpr, seg.duration, palGen].join();
  if (key !== ovKey) {
    ovKey = key;
    ovBack.width = ov.width; ovBack.height = ov.height;
    const b = ovBack.getContext("2d"), rowH = (H - 8) / Math.max(1, n);
    b.setTransform(dpr, 0, 0, dpr, 0, 0);
    b.fillStyle = pal.line; b.fillRect(0, 0, W, H);
    seg.speakers.forEach((k, i) => {
      b.fillStyle = spkColor(k);
      for (const u of seg.bySpk.get(k).utts.items) b.fillRect(X(u.start), 4 + i * rowH, Math.max(1, X(u.end) - X(u.start)), rowH - 1);
    });
    for (const e of seg.events.items) { b.fillStyle = e.type === "hold" ? pal.hold : pal.shift; b.fillRect(X(e.start), 0, 2, 4); }
  }
  g.clearRect(0, 0, W, H);
  g.drawImage(ovBack, 0, 0, W, H);
  const [t0, , win] = viewRange(t);
  g.strokeStyle = pal.fg; g.lineWidth = 1.5; g.strokeRect(X(t0), 0.75, Math.max(3, X(win)), H - 1.5);
  g.fillStyle = pal.fg; g.fillRect(X(t) - 1, 0, 2, H);
}

let drag = null;
const timeAt = x => { const v = tl._view; return v.t0 + (x - v.labelW) / (v.W - v.labelW) * v.win; };
const local = (el, e) => { const r = el.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
tl.addEventListener("pointerdown", e => {
  if (!tl._view) return;
  drag = { x: e.clientX, t0: tl._view.t0, moved: false };
  tl.setPointerCapture(e.pointerId);
});
tl.addEventListener("pointermove", e => {
  const tip = $("tip");
  if (drag) {
    const dx = e.clientX - drag.x, v = tl._view;
    if (Math.abs(dx) > 4) {
      if (!drag.moved) { drag.moved = true; setFollow(false); tl.classList.add("grab"); }
      view.t0 = drag.t0 - dx / (v.W - v.labelW) * v.win;
    }
    tip.style.display = "none";
    return;
  }
  const [mx, my] = local(tl, e);
  const h = hits.filter(h => mx >= h.x1 && mx <= h.x2 && my >= h.y1 && my <= h.y2).pop();
  if (!h) { tip.style.display = "none"; return; }
  tip.innerHTML = tipHtml(h); tip.style.display = "block";
  const wrap = $("tlWrap").getBoundingClientRect(), r = tl.getBoundingClientRect();
  const left = Math.min(r.left - wrap.left + mx + 12, wrap.width - tip.offsetWidth - 4);
  tip.style.left = Math.max(4, left) + "px";
  tip.style.top = (r.top - wrap.top + my + 16) + "px";
});
tl.addEventListener("pointerup", e => {
  const [mx] = local(tl, e);
  if (drag && !drag.moved && mx >= tl._view.labelW) clock.seek(timeAt(mx));
  drag = null; tl.classList.remove("grab");
});
tl.addEventListener("pointerleave", () => { $("tip").style.display = "none"; });
tl.addEventListener("wheel", e => {
  if (!tl._view || !seg) return;
  e.preventDefault();
  if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
    setFollow(false); view.t0 += e.deltaX / (tl._view.W - tl._view.labelW) * tl._view.win;
    return;
  }
  const [mx] = local(tl, e);
  const anchor = mx >= tl._view.labelW ? timeAt(mx) : null;
  setWin(view.win * Math.exp(e.deltaY * 0.0015), view.follow ? null : anchor);
}, { passive: false });
ov.addEventListener("click", e => {
  if (!seg) return;
  const t = local(ov, e)[0] / ov.clientWidth * seg.duration;
  clock.seek(t);
  if (!view.follow) view.t0 = t - view.win / 2;
});

function frame(now) {
  clock.tick(now);
  if (seg) {
    const t = clock.time;
    const sig = [seg.id, t, seg.duration, view.win, view.t0, view.follow, window.devicePixelRatio,
      tl.clientWidth, overlay.clientWidth, overlay.clientHeight, video.videoWidth,
      $("showBox").checked, $("showCap").checked, palGen].join();
    if (sig !== lastSig) {
      lastSig = sig;
      $("time").textContent = `${fmt(t)} / ${fmt(seg.duration)}`;
      drawOverlay(t); drawCaption(t); drawTimeline(t); drawOverview(t);
    }
    const label = clock.isPlaying ? "Pause" : "Play";
    if ($("play").textContent !== label) $("play").textContent = label;
    const cur = seg.events.items.find(e => e.start - 0.3 <= t && t <= e.end + 0.3);
    if (cur !== lastEv) {
      lastEv = cur;
      for (const tr of $("evRows").children) tr.classList.toggle("cur", !!cur && +tr.dataset.t === cur.start);
    }
  }
  requestAnimationFrame(frame);
}

matchMedia("(prefers-color-scheme: dark)").addEventListener("change", readColors);

(async () => {
  readColors();
  requestAnimationFrame(frame);
  try { splits = JSON.parse(await text("/list.json")); }
  catch (err) {
    document.querySelector(".grid").innerHTML = `<div class="panel err">Could not load the segment list (${esc(err.message)}).
      Start the viewer with <code>python3 script/viewer.py</code> and open the address it prints.</div>`;
    return;
  }
  for (const sp of splits) for (const v of sp.videos) for (const s of v.segments)
    segs.push({ split: sp.name, video: v.id, name: s.id, duration: s.duration, key: `${v.id}/${s.id}` });
  if (!segs.length) {
    document.querySelector(".grid").innerHTML = `<div class="panel err">No segments found under word/.</div>`;
    return;
  }
  const nv = splits.reduce((n, s) => n + s.videos.length, 0);
  $("summary").textContent = `${segs.length} segments, ${nv} videos: ${splits.map(s => `${s.videos.length} ${s.name}`).join(", ")}`;
  const hash = () => decodeURIComponent(location.hash.slice(1));
  window.addEventListener("hashchange", () => { if (hash() !== picked?.key && segs.some(s => s.key === hash())) open(hash()); });
  const start = [hash(), recall()].find(k => segs.some(s => s.key === k)) || segs[0].key;
  await open(start);
})();
