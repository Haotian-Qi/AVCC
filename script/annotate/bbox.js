// Bbox mode (button "BBox" or E): fix the face boxes.
//   Click a box on the video to select it, drag inside to move it, drag a corner to resize it.
//   With an ID picked (0 1 2 B) that has no box at this frame, drag on empty space to draw one.
// Edits spread over the scope (this frame, +1/5/10 s, to the end, from the start, the typed
// frame range, the whole segment); drawing and Fix jump touch the current frame only. Edits
// stay in the page until Save bbox, which sends {op: set|del} per frame and box. Unsaved
// edits of a segment are kept when another segment is opened, and come back with it.
// Uses the page's globals: video, bboxData, currentVid, currentSeg, videoFPS, selectedIndex,
// drawBBoxes, seekTo, resetWorkspaceForNew.
(() => {
    const BB = {
        on: false, sel: null, scope: 'frame',
        markA: null, markB: null,
        orig: new Map(),     // "f|s" -> box or null, as it was before the first edit since load
        pend: {},            // "video/seg" -> Map("f|s" -> box or null), every touched box
        undo: [], ref: null, key: '', nChanged: 0,
        drag: null, lastFrame: -1,
    };
    const fps = () => videoFPS || 25;
    const curFrame = () => Math.floor(video.currentTime * fps());
    const segKey = () => `${currentVid}/${currentSeg}`;
    const isTyping = e => /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) || e.target.isContentEditable;
    const $ = id => document.getElementById(id);

    const btn = $('bbModeBtn');
    const edit = $('bbEdit');
    const ectx = edit.getContext('2d');
    const msg = $('bbMsg');
    let msgT = 0;
    function say(t, ms = 3500) {
        msg.textContent = t;
        msg.style.display = 'block';
        clearTimeout(msgT);
        msgT = setTimeout(() => { msg.style.display = 'none'; }, ms);
    }

    // Big view: the video fills the window so a box can be drawn precisely.
    function big(on = !document.body.classList.contains('bbbig')) {
        document.body.classList.toggle('bbbig', on && BB.on);
        BB.lastFrame = -1;
        window.dispatchEvent(new Event('resize'));
    }

    $('bbScope').onchange = e => { BB.scope = e.target.value; e.target.blur(); BB.lastFrame = -1; };
    $('bbUndo').onclick = e => { undo(); e.currentTarget.blur(); };
    $('bbSave').onclick = e => { save(); e.currentTarget.blur(); };
    $('bbFix').onclick = e => { fixJump(BB.sel); e.currentTarget.blur(); };
    $('bbBig').onclick = e => { big(); e.currentTarget.blur(); };
    $('bbSwap').onclick = e => { swapIds($('bbSwA').value, $('bbSwB').value); e.currentTarget.blur(); };
    ['bbSwA', 'bbSwB'].forEach(id => $(id).onchange = e => e.target.blur());
    $('bbBigBar').onclick = e => {
        const a = e.target.dataset.a;
        if (!a) return;
        e.stopPropagation();
        e.target.blur();
        ({ fix: () => fixJump(BB.sel), undo, save, big: () => big(false) })[a]();
    };
    btn.onclick = () => { toggle(); btn.blur(); };

    // Typed range: both boxes filled gives the range, and Scope switches to it.
    function typedMark() {
        const a = $('bbMarkA').value, b = $('bbMarkB').value;
        if (a === '' && b === '') { BB.markA = BB.markB = null; return; }
        const fa = Math.max(0, Math.round(+(a === '' ? b : a))), fb = Math.max(0, Math.round(+(b === '' ? a : b)));
        BB.markA = Math.min(fa, fb);
        BB.markB = Math.max(fa, fb);
        BB.scope = 'mark';
        $('bbScope').value = 'mark';
        BB.lastFrame = -1;
        say(`frames ${BB.markA}–${BB.markB} (${BB.markB - BB.markA + 1} frames); scope = frame range`);
    }
    ['bbMarkA', 'bbMarkB'].forEach(id => {
        $(id).addEventListener('change', typedMark);
        $(id).addEventListener('keydown', e => {
            if (e.key === 'Enter') { typedMark(); e.target.blur(); }
            e.stopPropagation();
        });
    });

    // ---------- data ----------
    const K = (f, s) => `${f}|${s}`;
    function boxAt(f, s) { const l = bboxData[f]; return l ? l.find(b => b.spk === s) || null : null; }
    const copy = b => b ? { x1: b.x1, y1: b.y1, x2: b.x2, y2: b.y2 } : null;
    function rawSet(f, s, b) {
        let l = bboxData[f];
        if (l) { const i = l.findIndex(x => x.spk === s); if (i >= 0) l.splice(i, 1); }
        if (b) {
            if (!l) l = bboxData[f] = [];
            l.push({ spk: s, x1: Math.round(b.x1), y1: Math.round(b.y1), x2: Math.round(b.x2), y2: Math.round(b.y2) });
        }
    }
    let tx = null;   // the edit in progress: key -> {f, s, prev}
    function setBox(f, s, b) {
        const k = K(f, s);
        if (!BB.orig.has(k)) BB.orig.set(k, copy(boxAt(f, s)));
        if (tx && !tx.has(k)) tx.set(k, { f, s, prev: copy(boxAt(f, s)) });
        rawSet(f, s, b);
        pendMap().set(k, copy(boxAt(f, s)));
    }
    function pendMap() { return BB.pend[segKey()] || (BB.pend[segKey()] = new Map()); }
    function begin() { tx = new Map(); }
    function commit(label) {
        if (tx && tx.size) BB.undo.push({ label, ch: [...tx.values()] });
        if (BB.undo.length > 200) BB.undo.shift();
        const n = tx ? tx.size : 0;
        tx = null;
        afterEdit();
        return n;
    }
    function undo() {
        const u = BB.undo.pop();
        if (!u) return say('nothing to undo');
        for (const c of u.ch) { rawSet(c.f, c.s, c.prev); pendMap().set(K(c.f, c.s), copy(c.prev)); }
        afterEdit();
        say(`undone: ${u.label}`);
    }
    function changed() {
        const out = [];
        for (const [k, cur] of pendMap()) {
            const o = BB.orig.get(k);
            const same = (!o && !cur) || (o && cur && o.x1 === cur.x1 && o.y1 === cur.y1 && o.x2 === cur.x2 && o.y2 === cur.y2);
            if (!same) { const [f, s] = k.split('|'); out.push({ f: +f, s, cur }); }
        }
        return out;
    }
    function afterEdit() {
        BB.lastFrame = -1;
        BB.nChanged = changed().length;
        drawBBoxes(video.currentTime);
    }
    function frameCount() {
        let last = -1;
        for (const f in bboxData) if (+f > last) last = +f;
        return Math.max(last + 1, Math.round((video.duration || 0) * fps()));
    }

    // ---------- scope ----------
    function scopeRange() {
        const cf = curFrame(), N = frameCount();
        switch (BB.scope) {
            case 'frame': return [cf, cf];
            case '1': case '5': case '10': return [cf, Math.min(N - 1, cf + fps() * +BB.scope - 1)];
            case 'end': return [cf, N - 1];
            case 'start': return [0, cf];
            case 'seg': return [0, N - 1];
            case 'mark':
                if (BB.markA == null || BB.markB == null) { say('type the first and last frame first'); return null; }
                return [Math.min(BB.markA, BB.markB), Math.max(BB.markA, BB.markB)];
        }
        return [cf, cf];
    }

    // ---------- edits ----------
    function applyDelta(s, d, label) {   // d = [dx1, dy1, dx2, dy2], from the box at the current frame
        const r = scopeRange();
        if (!r) return;
        begin();
        for (let f = r[0]; f <= r[1]; f++) {
            const b = boxAt(f, s);
            if (!b) continue;
            setBox(f, s, { x1: b.x1 + d[0], y1: b.y1 + d[1], x2: b.x2 + d[2], y2: b.y2 + d[3] });
        }
        const n = commit(label);
        say(`${label}: ${n} frame(s) of ID ${s}`);
    }
    function del(s) {
        const r = scopeRange();
        if (!r) return;
        begin();
        for (let f = r[0]; f <= r[1]; f++) if (boxAt(f, s)) setBox(f, s, null);
        say(`deleted ${commit(`delete ${s}`)} box(es) of ID ${s}`);
    }
    function nearest(s, f, dir) {
        const N = frameCount();
        for (let g = f + dir; g >= 0 && g < N; g += dir) { const b = boxAt(g, s); if (b) return [g, b]; }
        return null;
    }

    // Fix jump: the dashed box is the selected ID's last box before this frame. J copies it
    // onto this frame, nothing else; step on with . and press J again.
    function fixJump(s) {
        if (!s) return say('pick an ID first (0 1 2 B, or click its box)');
        const cf = curFrame(), pre = nearest(s, cf, -1);
        if (!pre) return say(`ID ${s} has no box before frame ${cf}`);
        begin();
        setBox(cf, s, copy(pre[1]));
        commit(`fix jump ${s}`);
        say(`frame ${cf}: ID ${s} now has its box from frame ${pre[0]}; Ctrl+Z undoes`);
    }
    // Swap two IDs over the scope: in every frame, the box of a becomes b's and b's becomes a's.
    function swapIds(a, b) {
        if (!a || !b || a === b) return say('pick two different IDs to swap');
        const r = scopeRange();
        if (!r) return;
        begin();
        let n = 0;
        for (let f = r[0]; f <= r[1]; f++) {
            const x = copy(boxAt(f, a)), y = copy(boxAt(f, b));
            if (!x && !y) continue;
            setBox(f, a, y);
            setBox(f, b, x);
            n++;
        }
        commit(`swap ${a} <-> ${b}`);
        say(`swapped ID ${a} <-> ${b} in ${n} frame(s), frames ${r[0]}–${r[1]}; Ctrl+Z undoes`);
    }

    // ---------- save / reload ----------
    async function save() {
        const ch = changed();
        if (!ch.length) return say('no bbox changes to save');
        const ops = ch.map(c => c.cur ? { op: 'set', f: c.f, s: c.s, ...c.cur } : { op: 'del', f: c.f, s: c.s });
        const key = segKey();
        $('bbSave').disabled = true;
        try {
            const r = await fetch('api/save/bbox', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ video: currentVid, seg: currentSeg, ops }),
            });
            const j = await r.json();
            if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
            delete BB.pend[key];
            if (segKey() === key) { BB.orig.clear(); BB.undo = []; BB.nChanged = 0; }
            say(`saved ${j.applied} box edit(s) to ${j.path} (${j.rows} rows)`, 6000);
        } catch (e) {
            say(`NOT SAVED: ${e.message}`, 10000);
        } finally {
            $('bbSave').disabled = false;
        }
    }
    // The page loaded a new bbox table: put back the unsaved edits of this segment.
    function onReload() {
        const key = segKey();
        if (BB.key && BB.key !== key) {
            const left = BB.pend[BB.key] ? BB.pend[BB.key].size : 0;
            if (left) say(`${BB.key}: unsaved bbox edits are kept in this page; go back and Save bbox`, 6000);
            BB.markA = BB.markB = null;
            BB.sel = null;
        }
        BB.key = key;
        BB.ref = bboxData;
        BB.orig.clear();
        BB.undo = [];
        const p = BB.pend[key];
        if (p) for (const [k, b] of p) { const [f, s] = k.split('|'); BB.orig.set(k, copy(boxAt(+f, s))); rawSet(+f, s, b); }
        BB.lastFrame = -1;
        BB.nChanged = changed().length;
    }
    window.addEventListener('beforeunload', e => {
        if (Object.values(BB.pend).some(m => m.size)) { e.preventDefault(); e.returnValue = ''; }
    });

    function goFrame(f) {
        if (!video.paused) video.pause();
        seekTo((f + 0.5) / fps(), false);
    }

    // ---------- the edit layer over the video ----------
    function toVideo(e) {
        const r = edit.getBoundingClientRect(), vw = video.videoWidth, vh = video.videoHeight;
        const sc = Math.min(r.width / vw, r.height / vh);
        return { x: (e.clientX - r.left - (r.width - vw * sc) / 2) / sc, y: (e.clientY - r.top - (r.height - vh * sc) / 2) / sc, sc };
    }
    function hit(p) {
        const f = curFrame(), l = (bboxData[f] || []).slice();
        const hs = 12 / p.sc;
        if (BB.sel) {   // the selected box's corners first
            const b = boxAt(f, BB.sel);
            if (b) for (const [cx, cy, c] of [[b.x1, b.y1, 'tl'], [b.x2, b.y1, 'tr'], [b.x1, b.y2, 'bl'], [b.x2, b.y2, 'br']])
                if (Math.abs(p.x - cx) < hs && Math.abs(p.y - cy) < hs) return { s: BB.sel, corner: c };
        }
        l.sort((a, b) => (a.x2 - a.x1) * (a.y2 - a.y1) - (b.x2 - b.x1) * (b.y2 - b.y1));
        const b = l.find(b => p.x >= b.x1 && p.x <= b.x2 && p.y >= b.y1 && p.y <= b.y2);
        return b ? { s: b.spk, corner: null } : null;
    }
    edit.addEventListener('mousedown', e => {
        if (!BB.on || !video.videoWidth) return;
        e.preventDefault();
        if (!video.paused) video.pause();
        const p = toVideo(e), h = hit(p), f = curFrame();
        if (h) {
            BB.sel = h.s;
            BB.drag = { mode: h.corner || 'move', s: h.s, p0: p, b0: copy(boxAt(f, h.s)), cur: copy(boxAt(f, h.s)) };
        } else if (BB.sel && !boxAt(f, BB.sel)) {
            BB.drag = { mode: 'draw', s: BB.sel, p0: p, b0: null, cur: { x1: p.x, y1: p.y, x2: p.x, y2: p.y } };
        } else BB.sel = null;
        BB.lastFrame = -1;
    });
    window.addEventListener('mousemove', e => {
        const d = BB.drag;
        if (!d) return;
        const p = toVideo(e), dx = p.x - d.p0.x, dy = p.y - d.p0.y, b = d.b0;
        if (d.mode === 'move') d.cur = { x1: b.x1 + dx, y1: b.y1 + dy, x2: b.x2 + dx, y2: b.y2 + dy };
        else if (d.mode === 'draw') d.cur = { x1: Math.min(d.p0.x, p.x), y1: Math.min(d.p0.y, p.y), x2: Math.max(d.p0.x, p.x), y2: Math.max(d.p0.y, p.y) };
        else {
            const c = copy(b);
            if (d.mode[0] === 't') c.y1 = b.y1 + dy; else c.y2 = b.y2 + dy;
            if (d.mode[1] === 'l') c.x1 = b.x1 + dx; else c.x2 = b.x2 + dx;
            d.cur = { x1: Math.min(c.x1, c.x2), y1: Math.min(c.y1, c.y2), x2: Math.max(c.x1, c.x2), y2: Math.max(c.y1, c.y2) };
        }
        BB.lastFrame = -1;
    });
    window.addEventListener('mouseup', () => {
        const d = BB.drag;
        if (!d) return;
        BB.drag = null;
        BB.lastFrame = -1;
        const c = d.cur;
        if (d.mode === 'draw') {
            if (c.x2 - c.x1 < 8 || c.y2 - c.y1 < 8) return;
            begin();
            setBox(curFrame(), d.s, c);
            commit(`draw ${d.s}`);
            return say(`new box for ID ${d.s} at this frame`);
        }
        const b = d.b0, dd = [c.x1 - b.x1, c.y1 - b.y1, c.x2 - b.x2, c.y2 - b.y2].map(Math.round);
        if (dd.every(v => v === 0)) return;
        applyDelta(d.s, dd, d.mode === 'move' ? 'move' : 'resize');
    });

    function drawEdit() {
        const vw = video.videoWidth, vh = video.videoHeight;
        if (!vw) return;
        if (edit.width !== vw || edit.height !== vh) { edit.width = vw; edit.height = vh; }
        ectx.clearRect(0, 0, vw, vh);
        const f = curFrame(), lw = Math.max(2, vw / 500);
        if (BB.sel) {
            // where the selected ID was one frame ago (dashed), so a jump shows while stepping
            const p = boxAt(f - 1, BB.sel);
            if (p) {
                ectx.setLineDash([lw * 2, lw * 2]);
                ectx.strokeStyle = 'rgba(255,255,255,0.6)';
                ectx.lineWidth = lw;
                ectx.strokeRect(p.x1, p.y1, p.x2 - p.x1, p.y2 - p.y1);
                ectx.setLineDash([]);
            }
            const b = BB.drag && BB.drag.s === BB.sel ? BB.drag.cur : boxAt(f, BB.sel);
            if (b) {
                ectx.strokeStyle = '#fff';
                ectx.lineWidth = lw * 1.5;
                ectx.strokeRect(b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1);
                const hs = lw * 4;
                ectx.fillStyle = '#fff';
                for (const [x, y] of [[b.x1, b.y1], [b.x2, b.y1], [b.x1, b.y2], [b.x2, b.y2]]) ectx.fillRect(x - hs, y - hs, hs * 2, hs * 2);
            } else {
                ectx.fillStyle = 'rgba(255,80,80,0.9)';
                ectx.font = `bold ${Math.max(16, vw / 45)}px system-ui`;
                ectx.fillText(`ID ${BB.sel}: no box at this frame. Drag to draw one.`, 12, vh - 16);
            }
        }
        const sc = { frame: 'this frame', end: 'to end', start: 'from start', mark: 'frame range', seg: 'whole segment' }[BB.scope] || `+${BB.scope} s`;
        ectx.fillStyle = 'rgba(0,0,0,0.6)';
        ectx.fillRect(0, 0, vw, Math.max(22, vw / 50));
        ectx.fillStyle = '#fff';
        ectx.font = `${Math.max(13, vw / 80)}px system-ui`;
        ectx.fillText(`BBOX  frame ${f}  ·  ${BB.sel ? 'ID ' + BB.sel : 'click a box'}  ·  scope: ${sc}`, 8, Math.max(16, vw / 70));
    }

    // ---------- mode, keys, loop ----------
    function toggle(on = !BB.on) {
        if (on && !currentSeg) return say('open a segment first');
        BB.on = on;
        document.body.classList.toggle('bbmode', on);
        btn.classList.toggle('on', on);
        if (on) {
            // a selected word keeps the playhead inside itself, which would stop frame stepping
            if (selectedIndex !== -1) resetWorkspaceForNew();
            if (bboxData !== BB.ref) onReload();
            BB.lastFrame = -1;
        } else {
            BB.drag = null;
            ectx.clearRect(0, 0, edit.width, edit.height);
            big(false);
        }
        window.dispatchEvent(new Event('resize'));
    }
    window.addEventListener('keydown', e => {
        if (isTyping(e)) return;
        if (!BB.on) {
            if (e.key === 'e' && !e.ctrlKey && !e.metaKey && !e.altKey && currentSeg) {
                toggle(true);
                e.preventDefault();
                e.stopImmediatePropagation();
            }
            return;
        }
        const k = e.key, ctrl = e.ctrlKey || e.metaKey, f = curFrame();
        let used = true;
        if (ctrl && (k === 'z' || k === 'Z')) undo();
        else if (ctrl && (k === 's' || k === 'S')) save();
        else if (ctrl || e.altKey) used = false;
        else if (k === 'e' || k === 'E') toggle(false);
        else if (['0', '1', '2', 'b', 'B'].includes(k)) {
            BB.sel = k.toUpperCase();
            say(`ID ${BB.sel}` + (boxAt(f, BB.sel) ? '' : ': no box here, drag on the video to draw one'));
        }
        else if (k === 'Delete' || k === 'Backspace') { if (BB.sel) del(BB.sel); else say('pick an ID first'); }
        else if (e.code === 'Comma' || e.code === 'Period') goFrame(Math.max(0, f + (e.code === 'Comma' ? -1 : 1) * (e.shiftKey ? 5 : 1)));
        else if (k === '[') { BB.markA = f; if (BB.markB == null) BB.markB = f; }
        else if (k === ']') { BB.markB = f; if (BB.markA == null) BB.markA = f; BB.scope = 'mark'; $('bbScope').value = 'mark'; }
        else if (k === 'j' || k === 'J') fixJump(BB.sel);
        else if (k === 'z' || k === 'Z') big();
        else if (k === 'Escape') { BB.sel = null; BB.markA = BB.markB = null; }
        else used = false;
        if (used) { e.preventDefault(); e.stopImmediatePropagation(); BB.lastFrame = -1; }
    }, true);

    function loop() {
        requestAnimationFrame(loop);
        if (!BB.on) return;
        if (bboxData !== BB.ref || segKey() !== BB.key) onReload();
        $('bbPend').textContent = BB.nChanged ? `${BB.nChanged} unsaved box edit(s)` : '';
        // the frame boxes show the range unless one is being typed in
        for (const [id, v] of [['bbMarkA', BB.markA == null ? '' : Math.min(BB.markA, BB.markB)],
                               ['bbMarkB', BB.markA == null ? '' : Math.max(BB.markA, BB.markB)]])
            if (document.activeElement !== $(id) && $(id).value !== String(v)) $(id).value = v;
        const f = curFrame();
        if (f !== BB.lastFrame || !video.paused || BB.drag) {
            BB.lastFrame = f;
            drawEdit();
        }
    }
    requestAnimationFrame(loop);
    window.bboxMode = { BB, toggle, big, fixJump, swapIds, applyDelta, del, undo, changed, scopeRange, save };
})();
