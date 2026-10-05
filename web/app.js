/* voice-rec studio — client */
'use strict';

// ── State ──────────────────────────────────────────────────────────────────
const st = {
  settings: {},
  projects: [],
  project: null,
  sessions: [],
  sid: null,
  session: null,          // {entries, screenshots, manifest, live}
  recorder: { state: 'idle' },
  activity: { kind: 'idle' },
  pipeline: { speech: null, current: null, pending: [], pause: null },
  jobs: {},               // "project/sid" -> job
  chat: { messages: [], busy: false },
  capture: null,
  tab: 'live',
  side: 'shots',
  follow: true,
  docVersion: null,       // selected version number in the Beautified tab
  levels: new Array(42).fill(0),
  logs: [],
  claude: true,
  unreadChat: false,
  notes: { text: '', dirty: false, path: '' },
};
const CLIENT_ID = Math.random().toString(36).slice(2);

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const key = (p, s) => `${p}/${s}`;
const isLive = () => !!(st.session && st.session.live);
const recActive = () => !!st.recorder.active;

const ICON = {
  check: '<svg viewBox="0 0 12 12"><path d="M2.5 6.2l2.2 2.2 4.8-4.8"/></svg>',
  sparkle: '<svg viewBox="0 0 20 20" class="ico"><path d="M10 2.5l1.6 4.4 4.4 1.6-4.4 1.6L10 14.5l-1.6-4.4L4 8.5l4.4-1.6zM15.5 13l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/></svg>',
  chev: '<svg viewBox="0 0 16 16" class="ico" style="width:12px;height:12px"><path d="M4 6l4 4 4-4"/></svg>',
  x: '<svg viewBox="0 0 16 16" class="ico" style="width:12px;height:12px"><path d="M4 4l8 8M12 4l-8 8"/></svg>',
  camera: '<svg viewBox="0 0 20 20" class="ico"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h1.8l1.2-2h5l1.2 2h1.8A1.5 1.5 0 0 1 17 7.5v7a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 14.5z"/><circle cx="10" cy="11" r="2.8"/></svg>',
  mic: '<svg viewBox="0 0 20 20" class="ico"><rect x="7.5" y="2.5" width="5" height="10" rx="2.5"/><path d="M4.5 9.5a5.5 5.5 0 0 0 11 0M10 15v2.5"/></svg>',
  doc: '<svg viewBox="0 0 20 20" class="ico"><path d="M5.5 2.5h6l3.5 3.5v10a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 4 16V4a1.5 1.5 0 0 1 1.5-1.5z"/><path d="M11.5 2.5V6H15M7 10h6M7 13h4"/></svg>',
  screen: '<svg viewBox="0 0 24 24" class="ico"><rect x="2.5" y="4" width="19" height="13" rx="2"/><path d="M8.5 20.5h7M12 17v3.5"/></svg>',
  area: '<svg viewBox="0 0 24 24" class="ico"><path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3"/><rect x="8" y="8" width="8" height="8" rx="1" stroke-dasharray="2 2"/></svg>',
  window: '<svg viewBox="0 0 24 24" class="ico"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 8.5h18M6 6.3h.01M8.5 6.3h.01"/><path d="M14 13l4 4M18 13.5V17h-3.5"/></svg>',
  chat: '<svg viewBox="0 0 20 20" class="ico"><path d="M4 4.5h12a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5H9l-4 3v-3H4A1.5 1.5 0 0 1 2.5 13V6A1.5 1.5 0 0 1 4 4.5z"/></svg>',
  trash: '<svg viewBox="0 0 20 20" class="ico"><path d="M4 6h12M8 6V4.5h4V6M6 6l.7 9.6a1.5 1.5 0 0 0 1.5 1.4h3.6a1.5 1.5 0 0 0 1.5-1.4L14 6M8.5 9v5M11.5 9v5"/></svg>',
  open: '<svg viewBox="0 0 16 16" class="ico" style="width:14px;height:14px"><path d="M9 3h4v4M13 3L7.5 8.5M11 9.5V13H3V5h3.5"/></svg>',
};

// ── API ────────────────────────────────────────────────────────────────────
async function api(method, url, body) {
  const res = await fetch(url, {
    method, headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty */ }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error)) || `${res.status} ${res.statusText}`;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
}
const sessUrl = (p = st.project, s = st.sid) => `/api/projects/${encodeURIComponent(p)}/sessions/${encodeURIComponent(s)}`;

function toast(text, level = 'info', ms = 4200) {
  const el = document.createElement('div');
  el.className = `toast ${level}`;
  el.innerHTML = (level === 'ok' ? '<svg viewBox="0 0 20 20" class="ico"><path d="M5 10.5l3 3 7-7"/></svg>' : '') + `<span>${esc(text)}</span>`;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.style.transition = 'opacity .3s'; el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, ms);
}
const fail = e => toast(e.message || String(e), 'error', 6000);

// ── Helpers ────────────────────────────────────────────────────────────────
function fmtDur(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return (h ? `${h}:` : '') + `${String(m).padStart(h ? 2 : 1, '0')}:${String(s).padStart(2, '0')}`;
}
function fmtClock(ts) {
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
function sidLabel(sid) {
  const m = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/.exec(sid || '');
  if (!m) return sid || '—';
  const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' · ' + `${m[4]}:${m[5]}`;
}
function relTime(iso) {
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return fmtClock(d);
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + fmtClock(d);
}
const speakerColor = n => `var(--s${((n - 1) % 8) + 1})`;

function md(text) {
  // Minimal, safe Markdown → HTML (escape first).
  const blocks = [];
  let s = esc(text).replace(/```(\w*)\n?([\s\S]*?)```/g, (_, l, code) => { blocks.push(code); return `\u0000${blocks.length - 1}\u0000`; });
  const inline = t => t
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[?(\d{2}:\d{2}:\d{2})\]?/g, '<span class="ts-link" data-ts="$1">$1</span>');
  const lines = s.split('\n');
  let html = '', list = null;
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const line of lines) {
    let m;
    if ((m = /^\u0000(\d+)\u0000$/.exec(line.trim()))) { close(); html += `<pre><code>${blocks[+m[1]]}</code></pre>`; continue; }
    if ((m = /^(#{1,4})\s+(.*)$/.exec(line))) { close(); html += `<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`; continue; }
    if ((m = /^\s*[-*•]\s+(.*)$/.exec(line))) { if (list !== 'ul') { close(); html += '<ul>'; list = 'ul'; } html += `<li>${inline(m[1])}</li>`; continue; }
    if ((m = /^\s*\d+[.)]\s+(.*)$/.exec(line))) { if (list !== 'ol') { close(); html += '<ol>'; list = 'ol'; } html += `<li>${inline(m[1])}</li>`; continue; }
    if (!line.trim()) { close(); continue; }
    close();
    html += `<p>${inline(line)}</p>`;
  }
  close();
  return html.replace(/\u0000(\d+)\u0000/g, (_, i) => `<pre><code>${blocks[+i]}</code></pre>`);
}

// ── Menus / popovers ───────────────────────────────────────────────────────
let openMenuEl = null;
function openMenu(menu, anchor, align = 'left') {
  closeMenus();
  const r = anchor.getBoundingClientRect();
  menu.classList.remove('hidden');
  const w = menu.offsetWidth;
  let left = align === 'right' ? r.right - w : r.left;
  left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
  menu.style.left = `${left}px`;
  menu.style.top = `${r.bottom + 6}px`;
  anchor.classList.add('open');
  openMenuEl = { menu, anchor };
}
function closeMenus() {
  if (!openMenuEl) return;
  openMenuEl.menu.classList.add('hidden');
  openMenuEl.anchor.classList.remove('open');
  openMenuEl = null;
}
document.addEventListener('mousedown', e => {
  if (openMenuEl && !openMenuEl.menu.contains(e.target) && !openMenuEl.anchor.contains(e.target)) closeMenus();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeMenus(); closeModal(); closeLightbox(); }
});

function openModal(html, onMount) {
  $('#modalBody').innerHTML = html;
  $('#modal').classList.remove('hidden');
  if (onMount) onMount($('#modalBody'));
}
function closeModal() { $('#modal').classList.add('hidden'); }
$('#modal').addEventListener('mousedown', e => { if (e.target.id === 'modal') closeModal(); });

// ── Projects & sessions ────────────────────────────────────────────────────
async function loadProjects() {
  st.projects = await api('GET', '/api/projects');
  if (!st.project) {
    const last = st.settings.last_project;
    st.project = (st.recorder.active && st.recorder.project) ||
      (st.projects.find(p => p.name === last) || st.projects[0] || {}).name || null;
  }
  renderProjectMenu();
}

async function selectProject(name, { keepSession = false } = {}) {
  st.project = name;
  $('#projectName').textContent = name || '—';
  if (!keepSession) { st.sid = null; st.session = null; }
  loadNotes();
  await loadSessions();
  if (!keepSession) {
    const liveSid = recActive() && st.recorder.project === name && st.recorder.sid;
    const first = liveSid || (st.sessions[0] && st.sessions[0].sid);
    if (first) await selectSession(first); else renderAll();
  }
}

async function loadSessions() {
  if (!st.project) { st.sessions = []; renderSessionMenu(); return; }
  st.sessions = await api('GET', `/api/projects/${encodeURIComponent(st.project)}/sessions`);
  renderSessionMenu();
}

async function selectSession(sid, { quiet = false } = {}) {
  st.sid = sid;
  st.docVersion = null;
  try {
    st.session = await api('GET', sessUrl());
  } catch (e) { fail(e); return; }
  st.follow = true;
  if (!quiet) st.chat = { messages: [], busy: false };
  $('#chatDot').classList.add('hidden');
  $('#sessionName').innerHTML = st.session.live ? `<span style="color:var(--rec)">●</span> Live · ${esc(sidLabel(sid))}` : esc(sidLabel(sid));
  renderAll();
  loadChat();
}

async function reloadSession() {
  if (!st.project || !st.sid) return;
  try {
    st.session = await api('GET', sessUrl());
    $('#sessionName').innerHTML = st.session.live ? `<span style="color:var(--rec)">●</span> Live · ${esc(sidLabel(st.sid))}` : esc(sidLabel(st.sid));
    renderTranscript(); renderShots(); renderBeautify(); renderDoc();
  } catch (e) { /* ignore */ }
}

function renderProjectMenu() {
  const m = $('#projectMenu');
  m.innerHTML = '<div class="group">Projects</div>' + st.projects.map(p => `
    <button class="mi ${p.name === st.project ? 'on' : ''}" data-p="${esc(p.name)}">
      <span class="tick">${p.name === st.project ? '✓' : ''}</span>
      <span class="mi-main"><div class="mi-title">${esc(p.name)}</div></span>
      <span class="mi-right">${p.sessions} session${p.sessions === 1 ? '' : 's'}</span>
    </button>`).join('') + '<div class="sep"></div><button class="mi" data-new="1"><span class="tick">+</span><span class="mi-main"><div class="mi-title">New project…</div></span></button>';
  $('#projectName').textContent = st.project || 'Choose…';
}
$('#projectMenu').addEventListener('click', e => {
  const b = e.target.closest('.mi'); if (!b) return;
  closeMenus();
  if (b.dataset.new) return newProjectDialog();
  if (recActive() && b.dataset.p !== st.recorder.project) toast('Recording continues in the background.');
  selectProject(b.dataset.p).catch(fail);
});

function newProjectDialog() {
  openModal(`
    <div class="modal-head"><h2>New project</h2><p>Each project keeps its own transcripts, screenshots and documents in <code>projects/&lt;name&gt;/</code>.</p></div>
    <div class="modal-body"><div class="frow"><label for="npName">Name</label><input class="field" id="npName" placeholder="e.g. acme-discovery" autofocus></div></div>
    <div class="modal-foot"><button class="ghost-btn" data-close>Cancel</button><button class="primary-btn" id="npCreate">Create project</button></div>`,
  root => {
    const inp = $('#npName', root); inp.focus();
    const go = async () => {
      const name = inp.value.trim().replace(/\s+/g, '-');
      if (!name) return;
      try {
        await api('POST', '/api/projects', { name });
        closeModal();
        await loadProjects();
        await selectProject(name);
        toast(`Project “${name}” created`, 'ok');
      } catch (e) { fail(e); }
    };
    $('#npCreate', root).onclick = go;
    inp.onkeydown = e => { if (e.key === 'Enter') go(); };
    $('[data-close]', root).onclick = closeModal;
  });
}

function renderSessionMenu() {
  const m = $('#sessionMenu');
  if (!st.sessions.length) {
    m.innerHTML = '<div class="group">Sessions</div><div class="mi"><span class="mi-main"><div class="mi-sub">No recordings yet</div></span></div>';
    $('#sessionName').textContent = recActive() ? 'Starting…' : 'No sessions';
    return;
  }
  m.innerHTML = '<div class="group">Sessions</div>' + st.sessions.map(s => {
    const live = recActive() && st.recorder.project === st.project && st.recorder.sid === s.sid;
    const badge = s.final ? '<span class="mini-badge final">FINAL</span>' : s.versions ? `<span class="mini-badge">v${s.versions}</span>` : '';
    return `<button class="mi ${s.sid === st.sid ? 'on' : ''}" data-sid="${esc(s.sid)}">
      ${live ? '<span class="live-dot"></span>' : `<span class="tick">${s.sid === st.sid ? '✓' : ''}</span>`}
      <span class="mi-main"><div class="mi-title">${esc(sidLabel(s.sid))} ${badge}</div>
        <div class="mi-sub">${live ? 'Recording now' : `${esc(s.start)}–${esc(s.end)} · ${s.lines} lines${s.diarized ? ' · speakers' : ''}`}</div></span>
    </button>`;
  }).join('');
}
$('#sessionMenu').addEventListener('click', e => {
  const b = e.target.closest('.mi[data-sid]'); if (!b) return;
  closeMenus();
  selectSession(b.dataset.sid);
});
$('#projectBtn').onclick = e => { renderProjectMenu(); openMenu($('#projectMenu'), e.currentTarget); };
$('#sessionBtn').onclick = e => { loadSessions().then(() => openMenu($('#sessionMenu'), $('#sessionBtn'))).catch(fail); };

// ── Recorder control strip ─────────────────────────────────────────────────
function renderControl() {
  const r = st.recorder, el = $('#control');
  const s = st.settings;
  const mine = r.project === st.project;
  if (!r.active && (r.state === 'idle' || !r.state)) {
    el.innerHTML = `
      <button class="rec-btn" id="startBtn" ${st.project ? '' : 'disabled'}><span class="dot"></span>Start recording</button>
      <div class="opt-group">
        <div class="opt"><span class="opt-label">Language</span>
          <div class="seg" id="langSeg">${['auto', 'en', 'ru'].map(l => `<button data-v="${l}" class="${(s.lang || 'auto') === l ? 'on' : ''}">${l === 'auto' ? 'Auto' : l.toUpperCase()}</button>`).join('')}</div></div>
        <div class="opt"><span class="opt-label">Speakers</span>
          <select class="field" id="spkSel">${['auto', 'off', '2', '3', '4', '5', '6', '8'].map(v => `<option value="${v}" ${String(s.speakers || 'auto') === v ? 'selected' : ''}>${v === 'auto' ? 'Detect' : v === 'off' ? 'Off' : v}</option>`).join('')}</select></div>
        <div class="opt"><span class="opt-label">Auto-stop</span><input class="field num" id="minInp" type="number" min="1" value="${s.minutes || 180}"> min</div>
        ${pauseControl(s.pause || 1.5)}
        <label class="switch opt"><input type="checkbox" id="audioChk" ${s.save_audio ? 'checked' : ''}><span class="knob"></span>Save audio</label>
      </div>
      <div class="spacer"></div>
      ${r.detail === 'Session finished' && mine ? '<div class="state-sub">Last session finished · ' + esc(sidLabel(r.sid)) + '</div>' : ''}`;
    $('#startBtn').onclick = startRecording;
    $$('#langSeg button').forEach(b => b.onclick = () => { st.settings.lang = b.dataset.v; renderControl(); });
    $('#spkSel').onchange = e => { st.settings.speakers = e.target.value; };
    $('#minInp').onchange = e => { st.settings.minutes = +e.target.value || 180; };
    $('#audioChk').onchange = e => { st.settings.save_audio = e.target.checked; };
    bindPause();
    return;
  }
  if (r.state === 'error') {
    el.innerHTML = `<div><div class="error-text">${esc(r.error || 'Recognizer error')}</div><div class="state-sub mono" style="white-space:pre-wrap;max-height:3.2em;overflow:hidden">${esc((r.detail || '').split('\n').slice(-2).join('\n'))}</div></div>
      <div class="spacer"></div><button class="ghost-btn" id="showConsole">Show console</button><button class="ghost-btn" id="dismissErr">Dismiss</button>`;
    $('#showConsole').onclick = toggleConsole;
    $('#dismissErr').onclick = () => { st.recorder = { state: 'idle' }; renderControl(); };
    return;
  }
  if (r.state === 'loading') {
    el.innerHTML = `<div class="spinner"></div><div><div class="state-title">Preparing the recognizer</div><div class="state-sub">${esc(r.detail || 'Loading models…')} · first start can take a minute</div></div>
      <div class="spacer"></div><button class="stop-btn" id="stopBtn"><span class="sq"></span>Cancel</button>`;
    $('#stopBtn').onclick = stopRecording;
    return;
  }
  if (r.state === 'stopping' || r.state === 'finalizing') {
    el.innerHTML = `<div class="spinner"></div><div><div class="state-title">${r.state === 'stopping' ? 'Stopping…' : 'Finalizing session'}</div>
      <div class="state-sub">${esc(r.detail || 'Transcribing the remaining speech')}${r.diarization ? ' · then speakers are assigned' : ''}</div></div>
      <div class="spacer"></div><div class="state-sub">${esc(r.project)} · ${esc(sidLabel(r.sid))}</div>`;
    return;
  }
  // listening
  el.innerHTML = `
    <div class="rec-state"><span class="rec-pill"><span class="dot"></span>REC</span><span class="timer mono" id="timer">0:00</span></div>
    <canvas class="meter" id="meter" width="336" height="68"></canvas>
    <div class="pipe-status" id="pipeStatus"></div>
    ${pauseControl(st.pipeline.pause || s.pause || 1.5)}
    ${r.lang ? `<span class="chip" title="Detected language">${esc(r.lang.toUpperCase())}</span>` : ''}
    ${r.diarization ? '<span class="chip" title="Speaker diarization runs after the session">SPEAKERS</span>' : ''}
    <div class="spacer"></div>
    ${!mine ? `<span class="state-sub">Recording in <b>${esc(r.project)}</b></span>` : ''}
    <button class="ghost-btn" id="snapBtn" title="Take a screenshot of the capture area">${ICON.camera}Snap</button>
    <button class="stop-btn" id="stopBtn" title="Stop (same as Ctrl+C)"><span class="sq"></span>Stop</button>`;
  $('#stopBtn').onclick = stopRecording;
  $('#snapBtn').onclick = snap;
  bindPause();
  renderPipeline();
  tickTimer();
}

function pauseControl(v) {
  return `<div class="opt" title="How long a silence ends a phrase. Shorter: text appears sooner, in smaller pieces. Longer: fewer, longer chunks. Can be changed while recording.">
    <span class="opt-label">Pause</span><input class="field num" id="pauseInp" type="number" min="0.2" max="10" step="0.1" value="${(+v).toFixed(1)}"> s</div>`;
}
function bindPause() {
  const inp = $('#pauseInp'); if (!inp) return;
  inp.onchange = async () => {
    const v = Math.max(0.2, Math.min(10, +inp.value || 1.5));
    inp.value = v.toFixed(1);
    st.settings.pause = v;
    try {
      const r = await api('POST', '/api/record/pause', { seconds: v });
      if (recActive()) toast(`Pause set to ${r.pause.toFixed(1)} s, applies from the next phrase`, 'ok');
    } catch (e) { fail(e); }
  };
}

const fmtSec = sec => (sec >= 60 ? fmtDur(sec) : `${(+sec || 0).toFixed(1)} s`);
const queuedSec = () => st.pipeline.pending.reduce((a, c) => a + (c.duration || 0), 0);

function renderPipeline() {
  const pl = st.pipeline;
  const el = $('#pipeStatus');
  if (el) {
    const speaking = pl.speech != null;
    const l1 = speaking
      ? `<span class="a-dot speech"></span><b>Speech ${fmtSec(pl.speech)}</b><span class="pl-sub">recording · sent after a ${(pl.pause || 1.5).toFixed(1)} s pause</span>`
      : `<span class="a-dot"></span><b>Listening</b><span class="pl-sub">waiting for speech</span>`;
    let l2, pct = null;
    if (pl.current) {
      pct = pl.current.duration ? Math.min(100, Math.round(100 * pl.current.done / pl.current.duration)) : 0;
      l2 = `<span class="spinner tiny"></span><b>Transcribing ${fmtSec(pl.current.duration)}</b><span class="pl-sub">${pct}%${pl.pending.length ? ` · ${pl.pending.length} more queued (${fmtSec(queuedSec())})` : ''}</span>`;
    } else if (pl.pending.length) {
      l2 = `<span class="spinner tiny"></span><b>${pl.pending.length} chunk${pl.pending.length > 1 ? 's' : ''} queued</b><span class="pl-sub">${fmtSec(queuedSec())} of speech</span>`;
    } else {
      l2 = `<span class="ok-dot">${ICON.check}</span><b>Up to date</b><span class="pl-sub">all speech transcribed</span>`;
    }
    el.innerHTML = `<div class="pl-line">${l1}</div><div class="pl-line">${l2}</div>
      <div class="pl-bar${pct == null ? ' idle' : ''}"><i style="width:${pct ?? 0}%"></i></div>`;
  }
  renderChunks();
  renderGhost();
}

function renderChunks() {
  const box = $('#pipeline'); if (!box) return;
  const pl = st.pipeline;
  if (!isLive()) { box.innerHTML = ''; return; }
  const rows = [];
  if (pl.current) {
    const c = pl.current;
    const pct = c.duration ? Math.min(100, Math.round(100 * c.done / c.duration)) : 0;
    rows.push(`<div class="chunk-row"><span class="t">${esc(c.time)}</span><div class="chunk active">
      <div class="chunk-head"><span class="spinner tiny"></span><b>Transcribing ${fmtSec(c.duration)} of speech</b><span class="chunk-pct mono">${pct}%</span></div>
      <div class="pl-bar"><i style="width:${pct}%"></i></div>
      ${c.text ? `<p class="chunk-text">${esc(c.text)}<span class="caret"></span></p>` : '<p class="chunk-text muted">Whisper is working through this chunk; text appears as it is recognized.</p>'}
    </div></div>`);
  }
  pl.pending.forEach((c, i) => rows.push(`<div class="chunk-row"><span class="t">${esc(c.time)}</span><div class="chunk">
      <div class="chunk-head"><span class="q-dot"></span><b>Queued · ${fmtSec(c.duration)} of speech</b><span class="chunk-pct">${pl.current || i ? `#${i + 1} in line` : 'next'}</span></div></div></div>`));
  box.innerHTML = rows.join('');
  if (st.follow) { const t = $('#transcript'); t.scrollTop = t.scrollHeight; }
}

function tickTimer() {
  const t = $('#timer'); if (!t) return;
  const r = st.recorder;
  const from = r.listening_at || r.started_at;
  if (!from) return;
  const el = Date.now() / 1000 - from;
  t.innerHTML = fmtDur(el) + (r.minutes ? `<small>/ ${Math.round(r.minutes)} min</small>` : '');
}
setInterval(() => { tickTimer(); tickJobs(); }, 1000);

function drawMeter() {
  const c = $('#meter');
  if (c) {
    const ctx = c.getContext('2d');
    const w = c.width, h = c.height, n = st.levels.length;
    ctx.clearRect(0, 0, w, h);
    const bw = w / n;
    const css = getComputedStyle(document.documentElement);
    const speech = st.pipeline.speech != null;
    ctx.fillStyle = speech ? css.getPropertyValue('--ok').trim() : css.getPropertyValue('--faint').trim();
    st.levels.forEach((v, i) => {
      const amp = Math.min(1, Math.sqrt(v) * 4.2);
      const bh = Math.max(4, amp * h * 0.92);
      const x = i * bw + bw * 0.22, y = (h - bh) / 2;
      ctx.globalAlpha = 0.35 + 0.65 * (i / n);
      roundRect(ctx, x, y, bw * 0.56, bh, Math.min(3, bw * 0.28));
    });
    ctx.globalAlpha = 1;
  }
  requestAnimationFrame(drawMeter);
}
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); ctx.fill();
}
requestAnimationFrame(drawMeter);

async function startRecording() {
  const s = st.settings;
  try {
    const check = await api('GET', '/api/check-recorder');
    if (!check.ok) {
      openSettings();
      toast(`The recognizer's Python can't load its models: ${check.error}`, 'error', 9000);
      return;
    }
    await api('POST', '/api/record/start', {
      project: st.project, lang: s.lang || 'auto', speakers: s.speakers || 'auto',
      minutes: s.minutes || 180, pause: s.pause || 1.5, save_audio: !!s.save_audio,
    });
  } catch (e) { fail(e); }
}
async function stopRecording() { try { await api('POST', '/api/record/stop'); } catch (e) { fail(e); } }
async function snap() { try { await api('POST', '/api/record/snap'); } catch (e) { fail(e); } }
$('#snapSideBtn').onclick = snap;

// ── Transcript ─────────────────────────────────────────────────────────────
function entryHtml(e, prev, isNew) {
  if (e.deleted) return '';
  const nw = isNew ? ' new' : '';
  if (e.kind === 'screenshot') {
    const name = (e.path || '').split('/').pop();
    const shot = (st.session.screenshots || []).find(s => s.path === e.path);
    const url = shot && shot.url ? shot.url : null;
    return `<div class="row shot-row${nw}" data-time="${esc(e.time)}"><span class="t">${esc(e.time)}</span><div class="body">
      <div class="shot-inline" data-path="${esc(e.path)}">${url ? `<img src="${esc(url)}" alt="">` : ''}<span class="lbl"><b>Screenshot captured</b>${esc(name)}</span></div></div></div>`;
  }
  if (e.kind === 'selection') {
    return `<div class="row${nw}" data-time="${esc(e.time)}"><span class="t">${esc(e.time)}</span><div class="body"><div class="sel-quote"><span class="k">Selected text</span>${esc(e.text)}</div></div></div>`;
  }
  const cont = e.speaker != null && prev && prev.kind === 'speech' && prev.speaker === e.speaker;
  const spk = e.speaker != null ? `<div class="spk" style="--sc:${speakerColor(e.speaker)}">Speaker ${e.speaker}</div>` : '';
  return `<div class="row${e.speaker != null ? ' has-spk' : ''}${cont ? ' cont' : ''}${nw}" data-time="${esc(e.time)}"><span class="t">${esc(e.time)}</span><div class="body">${spk}<p>${esc(e.text)}</p></div></div>`;
}

function renderTranscript() {
  const box = $('#transcript');
  const entries = st.session ? st.session.entries : [];
  const speech = entries.filter(e => e.kind === 'speech').length;
  $('#lineCount').textContent = st.session ? speech : '';
  if (!st.session || (!entries.length && !isLive())) {
    box.innerHTML = emptyTranscript();
    return;
  }
  // Display in time order: markers are written when flushed, which can be after a phrase that started earlier.
  const sorted = entries.map((e, i) => [e, i]).sort((a, b) => (a[0].time < b[0].time ? -1 : a[0].time > b[0].time ? 1 : a[1] - b[1])).map(x => x[0]);
  box.innerHTML = sorted.map((e, i) => entryHtml(e, sorted[i - 1], false)).join('') + '<div id="pipeline"></div><div id="ghost"></div>';
  renderChunks();
  renderGhost();
  if (st.follow) box.scrollTop = box.scrollHeight;
}

function emptyTranscript() {
  if (!st.project) return `<div class="empty"><div class="art">${ICON.mic}</div><h3>Create a project to begin</h3><p>Projects keep transcripts, screenshots and documents together.</p></div>`;
  return `<div class="empty"><div class="art">${ICON.mic}</div><h3>Ready when you are</h3>
    <p>Choose what screenshots should capture (top right), then press <b>Start recording</b>. Speech appears here as it is recognized; press <kbd>⌃</kbd><kbd>⇧</kbd><kbd>S</kbd> or <b>Snap</b> to capture the screen.</p></div>`;
}

function appendEntry(idx, entry) {
  const s = st.session;
  if (!s) return;
  if (idx < s.entries.length) return;                // already have it
  if (idx > s.entries.length) { reloadSession(); return; }  // missed some → resync
  s.entries.push(entry);
  const box = $('#transcript');
  if (box.querySelector('.empty')) { renderTranscript(); return; }
  // keep time order: insert before the first row that is later than this entry
  const later = $$('#transcript .row[data-time]').find(r => r.dataset.time > entry.time);
  const tmp = document.createElement('div');
  tmp.innerHTML = entryHtml(entry, null, true);
  if (tmp.firstElementChild) box.insertBefore(tmp.firstElementChild, later || $('#pipeline'));
  $('#lineCount').textContent = s.entries.filter(e => e.kind === 'speech').length;
  if (st.follow) box.scrollTop = box.scrollHeight;
}

function renderGhost() {
  const g = $('#ghost'); if (!g) return;
  const sp = st.pipeline.speech;
  if (!isLive() || st.recorder.state !== 'listening' || sp == null) { g.innerHTML = ''; return; }
  g.innerHTML = `<div class="row ghost-row"><span class="t">now</span><div class="body"><p><span class="wave"><i></i><i></i><i></i><i></i></span>
    <span>Speaking · ${fmtSec(sp)}</span><span class="pl-sub">goes to transcription after a ${(st.pipeline.pause || 1.5).toFixed(1)} s pause</span></p></div></div>`;
  if (st.follow) { const box = $('#transcript'); box.scrollTop = box.scrollHeight; }
}

$('#transcript').addEventListener('scroll', () => {
  const box = $('#transcript');
  st.follow = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  $('#jumpLive').classList.toggle('hidden', st.follow || !isLive());
});
$('#jumpLive').onclick = () => { st.follow = true; const b = $('#transcript'); b.scrollTop = b.scrollHeight; $('#jumpLive').classList.add('hidden'); };
$('#transcript').addEventListener('click', e => {
  const si = e.target.closest('.shot-inline');
  if (si) openLightbox(si.dataset.path);
});

function jumpToTime(ts) {
  switchTab('live');
  const rows = $$('#transcript .row[data-time]');
  const row = rows.find(r => r.dataset.time >= ts) || rows[rows.length - 1];
  if (!row) return;
  st.follow = false;
  row.scrollIntoView({ block: 'center', behavior: 'smooth' });
  row.classList.add('flash');
  setTimeout(() => row.classList.remove('flash'), 1400);
}

// ── Screenshots ────────────────────────────────────────────────────────────
function shotStages(s) {
  const latest = latestVersion();
  const job = currentJob();
  const inJob = job && job.status === 'running';
  return [
    ['Captured', 'done'],
    ['Transcript', s.logged ? 'done' : 'active'],
    ['OCR', s.ocr_error ? 'err' : s.ocr ? 'done' : 'active'],
    [s.beautified ? `Beautified v${s.beautified}` : 'Beautified',
      s.beautified ? 'done' : inJob ? 'active' : ''],
  ];
}
function shotCardHtml(s, enter) {
  const url = s.url ? `${s.url}` : '';
  return `<div class="shot-card${enter ? ' enter' : ''}" data-path="${esc(s.path)}">
    <button class="shot-del" data-del="${esc(s.path)}" title="Delete screenshot">${ICON.trash}</button>
    <div class="thumb" data-path="${esc(s.path)}">${url ? `<img src="${esc(url)}" loading="lazy" alt="">` : ''}</div>
    <div class="shot-meta"><div class="top"><span class="time">${esc(s.time)}</span><span class="name">${esc(s.name)}</span></div>
    <ol class="pipeline">${shotStages(s).map(([l, c]) => `<li class="${c}" title="${esc(l)}">${esc(l)}</li>`).join('')}</ol></div></div>`;
}
function renderShots() {
  const shots = st.session ? [...st.session.screenshots] : [];
  $('#shotCount').textContent = shots.length || '';
  $('#snapSideBtn').classList.toggle('hidden', !(isLive() && st.recorder.state === 'listening' && st.side === 'shots'));
  const box = $('#shots');
  if (!shots.length) {
    box.innerHTML = `<div class="empty"><div class="art">${ICON.camera}</div><h3>No screenshots yet</h3><p>Press <kbd>⌃</kbd><kbd>⇧</kbd><kbd>S</kbd> while recording. Each screenshot is OCR'd and used by Beautify to fix names and identify speakers.</p></div>`;
    return;
  }
  box.innerHTML = shots.reverse().map(s => shotCardHtml(s, false)).join('');
}
function upsertShot(shot, isNew) {
  if (!st.session) return;
  const list = st.session.screenshots;
  const i = list.findIndex(s => s.path === shot.path);
  if (i >= 0) list[i] = shot; else list.push(shot);
  const box = $('#shots');
  const existing = box.querySelector(`.shot-card[data-path="${CSS.escape(shot.path)}"]`);
  if (existing) {
    const tmp = document.createElement('div'); tmp.innerHTML = shotCardHtml(shot, false);
    existing.replaceWith(tmp.firstElementChild);
  } else {
    if (box.querySelector('.empty')) box.innerHTML = '';
    const tmp = document.createElement('div'); tmp.innerHTML = shotCardHtml(shot, isNew);
    box.prepend(tmp.firstElementChild);
    box.scrollTop = 0;
  }
  $('#shotCount').textContent = list.length;
  // update inline thumbnail in the transcript if present
  $$(`#transcript .shot-inline[data-path="${CSS.escape(shot.path)}"]`).forEach(el => {
    if (!el.querySelector('img') && shot.url) el.insertAdjacentHTML('afterbegin', `<img src="${esc(shot.url)}" alt="">`);
  });
}
$('#shots').addEventListener('click', e => {
  const del = e.target.closest('.shot-del');
  if (del) { confirmDeleteShot(del.closest('.shot-card'), del.dataset.del); return; }
  const t = e.target.closest('.thumb'); if (t) openLightbox(t.dataset.path);
});

function confirmDeleteShot(card, path) {
  if (card.querySelector('.shot-confirm')) return;
  card.insertAdjacentHTML('beforeend', `<div class="shot-confirm"><span>Delete this screenshot?</span>
    <button class="danger-btn" data-yes>Delete</button><button class="ghost-btn small" data-no>Cancel</button></div>`);
  const box = card.querySelector('.shot-confirm');
  box.querySelector('[data-no]').onclick = ev => { ev.stopPropagation(); box.remove(); };
  box.querySelector('[data-yes]').onclick = ev => { ev.stopPropagation(); deleteShot(path); };
}

async function deleteShot(path) {
  const name = path.split('/').pop();
  try {
    await api('DELETE', `${sessUrl()}/screenshots/${encodeURIComponent(name)}`);
    removeShotLocal(path);
    toast('Screenshot deleted · kept in screenshots/.trash', 'ok');
  } catch (e) { fail(e); }
}

function removeShotLocal(path) {
  if (!st.session) return;
  const name = path.split('/').pop();
  const same = p => p && p.split('/').pop() === name;
  st.session.screenshots = st.session.screenshots.filter(s => !same(s.path));
  st.session.entries.forEach(e => { if (e.kind === 'screenshot' && same(e.path)) e.deleted = true; });
  $$('#shots .shot-card').forEach(c => { if (same(c.dataset.path)) { c.classList.add('leaving'); setTimeout(() => c.remove(), 250); } });
  $$('#transcript .shot-inline').forEach(el => { if (same(el.dataset.path)) el.closest('.row').remove(); });
  $('#shotCount').textContent = st.session.screenshots.length || '';
  if (!st.session.screenshots.length) setTimeout(renderShots, 260);
}

async function openLightbox(path) {
  const shot = (st.session.screenshots || []).find(s => s.path === path) || { path, url: null, name: path.split('/').pop(), time: '' };
  const lb = $('#lightbox');
  lb.innerHTML = `<div class="lb-img">${shot.url ? `<img src="${esc(shot.url)}" alt="">` : ''}</div>
    <div class="lb-side"><div class="lb-head"><div><b>${esc(shot.time)}</b> <span style="color:#6b7480;font-size:12px">${esc(shot.name)}</span></div>
    <div style="display:flex;gap:6px"><button class="ghost-btn small" id="lbDel" title="Delete screenshot">${ICON.trash}</button><button class="ghost-btn small" id="lbJump">Find in transcript</button><button class="ghost-btn small" id="lbClose">Close</button></div></div>
    <div class="lb-ocr" id="lbOcr">Loading OCR text…</div></div>`;
  lb.classList.remove('hidden');
  $('#lbClose').onclick = closeLightbox;
  $('#lbJump').onclick = () => { closeLightbox(); jumpToTime(shot.time); };
  $('#lbDel').onclick = () => {
    const b = $('#lbDel');
    if (b.dataset.armed) { closeLightbox(); deleteShot(path); return; }
    b.dataset.armed = '1'; b.textContent = 'Click again to delete'; b.style.color = '#ff6b78';
  };
  lb.onclick = e => { if (e.target === lb || e.target.classList.contains('lb-img')) closeLightbox(); };
  if (shot.url) {
    try {
      const r = await fetch(shot.url.replace(/\.png$/, '.txt'));
      $('#lbOcr').textContent = r.ok ? ((await r.text()).trim() || '(no text recognized)') : 'OCR is not ready yet.';
    } catch (_) { $('#lbOcr').textContent = 'OCR is not available.'; }
  }
}
function closeLightbox() { $('#lightbox').classList.add('hidden'); }

// ── Beautify ───────────────────────────────────────────────────────────────
const currentJob = () => (st.project && st.sid) ? st.jobs[key(st.project, st.sid)] : null;
function versions() { return (st.session && st.session.manifest && st.session.manifest.versions) || []; }
function latestVersion() { const d = versions().filter(v => v.status === 'done'); return d[d.length - 1] || null; }
function lastAttempt() { const v = versions(); return v[v.length - 1] || null; }

function renderBeautify() {
  const el = $('#beautifyCtl');
  if (!st.session) { el.innerHTML = ''; return; }
  const job = currentJob();
  const latest = latestVersion();
  const last = lastAttempt();
  const badge = $('#versionBadge');
  badge.textContent = latest ? (latest.mode === 'final' ? 'FINAL' : `v${latest.v}`) : '';
  badge.className = 'tab-badge' + (latest && latest.mode === 'final' ? ' final' : '');

  if (job && job.status === 'running') {
    const lastStep = job.steps[job.steps.length - 1];
    el.innerHTML = `<div class="b-status" id="bStatus"><span class="s1">Beautifying v${job.v} · ${esc(modeLabel(job.mode))}</span><span class="s2" id="bStep">${esc(lastStep ? lastStep.text : '')}</span></div>
      <button class="beautify-btn running" disabled><span class="spin-w"></span><span id="bElapsed">${fmtDur(Date.now() / 1000 - job.started)}</span></button>
      <button class="cancel-x" id="bCancel" title="Cancel">${ICON.x}</button>`;
    $('#bCancel').onclick = () => api('DELETE', sessUrl() + '/beautify').catch(fail);
    $('#bStatus').onclick = () => switchTab('beautified');
    return;
  }
  let status = '';
  if (last && last.status === 'failed' && (!latest || last.v > latest.v)) {
    status = `<div class="b-status failed" id="bStatus" title="${esc(last.error || '')}"><span class="s1">⚠ v${last.v} failed</span><span class="s2">${esc((last.error || '').slice(0, 80))}</span></div>`;
  } else if (latest) {
    const fin = latest.mode === 'final';
    status = `<div class="b-status" id="bStatus" title="Open the beautified version"><span class="s1"><span class="check">${ICON.check}</span>${fin ? 'Final version ready' : 'Beautification finished'}</span>
      <span class="s2">v${latest.v} · ${esc(relTime(latest.finished || latest.created))}${latest.turns ? ` · ${latest.turns} turns` : ''}</span></div>`;
  }
  const canRun = st.claude || st.settings.beautify_engine === 'api';
  el.innerHTML = `${status}<div class="split"><button class="beautify-btn" id="bRun" ${canRun ? '' : 'disabled title="Claude Code CLI not found"'}>${ICON.sparkle}Beautify</button><button class="beautify-btn more" id="bMore" title="More options" ${canRun ? '' : 'disabled'}>${ICON.chev}</button></div>`;
  $('#bRun').onclick = () => runBeautify(latest ? 'update' : 'full');
  $('#bMore').onclick = e => openBeautifyMenu(e.currentTarget);
  const bs = $('#bStatus'); if (bs) bs.onclick = () => switchTab('beautified');
}
const modeLabel = m => ({ update: 'update', full: 'full rebuild', final: 'final' }[m] || m);

function openBeautifyMenu(anchor) {
  const latest = latestVersion();
  const finished = !isLive();
  const m = $('#beautifyMenu');
  m.innerHTML = `
    <button class="mi" data-mode="update" ${latest ? '' : 'disabled style="opacity:.5"'}><span class="mi-main"><div class="mi-title">Update with new material</div><div class="mi-sub">Extends v${latest ? latest.v : '—'} with new speech and screenshots · fastest</div></span></button>
    <button class="mi" data-mode="full"><span class="mi-main"><div class="mi-title">Full rebuild</div><div class="mi-sub">Regenerates the whole document from the transcript</div></span></button>
    <button class="mi" data-mode="final" ${finished ? '' : 'disabled style="opacity:.5"'}><span class="mi-main"><div class="mi-title">Final version</div><div class="mi-sub">${finished ? 'Definitive document with every speaker identified; also saved to docs/' : 'Available after the recording stops (runs automatically)'}</div></span></button>`;
  m.onclick = e => {
    const b = e.target.closest('.mi[data-mode]'); if (!b || b.disabled) return;
    closeMenus(); runBeautify(b.dataset.mode);
  };
  openMenu(m, anchor, 'right');
}

async function runBeautify(mode) {
  try {
    const job = await api('POST', sessUrl() + '/beautify', { mode });
    st.jobs[key(job.project, job.sid)] = job;
    renderBeautify(); renderDoc(); renderShots();
  } catch (e) { fail(e); }
}

function tickJobs() {
  const job = currentJob();
  if (!job || job.status !== 'running') return;
  const el = $('#bElapsed'); if (el) el.textContent = fmtDur(Date.now() / 1000 - job.started);
  const je = $('#jcElapsed'); if (je) je.textContent = fmtDur(Date.now() / 1000 - job.started);
}

function onJob(job) {
  const k = key(job.project, job.sid);
  const prev = st.jobs[k];
  st.jobs[k] = { ...(prev || {}), ...job };
  if (k !== key(st.project, st.sid)) {
    if (job.status === 'done' && prev && prev.status === 'running') toast(`Beautified ${job.project} · ${sidLabel(job.sid)} (v${job.v})`, 'ok');
    return;
  }
  if (prev && prev.status === 'running' && job.status !== 'running') {
    if (job.status === 'done') toast(`Beautification finished · v${job.v}${job.mode === 'final' ? ' (final)' : ''}`, 'ok', 6000);
    else if (job.status === 'failed') toast(`Beautify failed: ${job.error || ''}`, 'error', 8000);
    st.docVersion = null;
    reloadSession();
    return;
  }
  // light update while running
  const bs = $('#bStep');
  if (bs && job.status === 'running') bs.textContent = (job.steps[job.steps.length - 1] || {}).text || '';
  else renderBeautify();
  renderJobCard();
}

function renderJobCard() {
  const card = $('#jobCard');
  const job = currentJob();
  if (!job || job.status !== 'running') { card.classList.add('hidden'); return; }
  card.classList.remove('hidden');
  const steps = job.steps.slice(-9);
  card.innerHTML = `<div class="jc-head"><div style="flex:1"><div class="jc-title">Beautifying v${job.v} · ${esc(modeLabel(job.mode))}</div>
      <div class="jc-sub">${job.lines || 0} lines · ${job.shots || 0} screenshots · <span id="jcChars">${(job.chars / 1024).toFixed(0)} KB generated</span></div></div>
      <span class="mono" id="jcElapsed" style="font-size:20px">${fmtDur(Date.now() / 1000 - job.started)}</span></div>
    <div class="bar"><i></i></div>
    <ul class="steps">${steps.map((s, i) => {
      const cur = i === steps.length - 1;
      return `<li class="${cur ? 'cur' : ''}">${cur ? '<span class="spinner"></span>' : '<svg viewBox="0 0 12 12" class="ok"><path d="M2.5 6.2l2.2 2.2 4.8-4.8"/></svg>'}<span class="st mono">${fmtDur(s.t)}</span><span class="sx">${esc(s.text)}</span></li>`;
    }).join('')}</ul>`;
}

function renderDoc() {
  const job = currentJob();
  renderJobCard();
  const vs = versions().filter(v => v.status === 'done');
  const tb = $('#docToolbar'), frame = $('#docFrame'), wrap = $('#docFrameWrap'), empty = $('#docEmpty');
  if (!vs.length) {
    tb.innerHTML = '';
    wrap.classList.add('hidden');
    const running = job && job.status === 'running';
    empty.classList.toggle('hidden', running);
    empty.innerHTML = `<div class="art">${ICON.doc}</div><h3>No beautified version yet</h3>
      <p>Beautify turns the raw transcript and screenshots into a full annotated document: every utterance kept, real speakers identified, callouts compared with what you already know.</p>
      ${st.session ? `<button class="beautify-btn" style="border-radius:10px;margin-top:10px" id="emptyBeautify">${ICON.sparkle}Beautify now</button>` : ''}`;
    const b = $('#emptyBeautify'); if (b) b.onclick = () => runBeautify('full');
    frame.removeAttribute('src');
    return;
  }
  empty.classList.add('hidden');
  wrap.classList.remove('hidden');
  const sel = vs.find(v => v.v === st.docVersion) || vs[vs.length - 1];
  st.docVersion = sel.v;
  tb.innerHTML = `
    <select class="field" id="verSel">${[...vs].reverse().map(v => `<option value="${v.v}" ${v.v === sel.v ? 'selected' : ''}>v${v.v} · ${esc(v.mode === 'final' ? 'Final' : modeLabel(v.mode))} · ${esc(relTime(v.finished || v.created))}</option>`).join('')}</select>
    <div class="doc-meta">
      ${sel.turns ? `<span><b>${sel.turns}</b> turns</span>` : ''}${sel.callouts ? `<span><b>${sel.callouts}</b> callouts</span>` : ''}
      <span><b>${(sel.referenced || []).length}/${(sel.screenshots || []).length}</b> screenshots</span>
      ${sel.doc_t_first ? `<span>covers <b>${esc(sel.doc_t_first)}–${esc(sel.doc_t_last)}</b> of ${esc(sel.t_first)}–${esc(sel.t_last)}</span>` : ''}
      ${sel.duration ? `<span>${fmtDur(sel.duration)} to generate</span>` : ''}
    </div>
    <div class="spacer"></div>
    ${sel.docs_url ? `<a class="ghost-btn small" href="${esc(sel.docs_url)}" target="_blank" title="Copy saved in the project's docs/ folder">${ICON.doc}docs/ copy</a>` : ''}
    <a class="ghost-btn small" href="${esc(sel.url)}" target="_blank">${ICON.open}Open</a>`;
  $('#verSel').onchange = e => { st.docVersion = +e.target.value; renderDoc(); };
  const src = `${sel.url}?v=${encodeURIComponent(sel.finished || sel.v)}`;
  if (frame.getAttribute('src') !== src) frame.setAttribute('src', src);
}

// ── Chat ───────────────────────────────────────────────────────────────────
async function loadChat() {
  if (!st.project || !st.sid) return;
  try {
    const d = await api('GET', sessUrl() + '/chat');
    st.chat = { messages: d.messages, busy: d.busy };
  } catch (_) { st.chat = { messages: [], busy: false }; }
  renderChat();
}

function msgHtml(m) {
  if (m.role === 'user') return `<div class="msg user" data-id="${m.id}">${esc(m.text)}</div>`;
  const tools = (m.tools || []).length ? `<div class="tools">${m.tools.slice(-4).map(t => `<span title="${esc(t)}">${esc(t)}</span>`).join('')}</div>` : '';
  const body = m.text ? `<div class="md">${md(m.text)}</div>` : (m.status === 'error' ? '' : '<div class="thinking"><i></i><i></i><i></i></div>');
  const err = m.status === 'error' ? `<div class="err">${esc(m.error || 'Something went wrong')}</div>` : '';
  return `<div class="msg assistant" data-id="${m.id}">${tools}${body}${err}</div>`;
}

function renderChat() {
  const log = $('#chatLog');
  const msgs = st.chat.messages;
  $('#clearChatBtn').classList.toggle('hidden', !(st.side === 'chat' && msgs.length));
  if (!msgs.length) {
    const q = ['Summarize the discussion so far', 'List decisions and action items', 'Who is speaking, and what are their roles?', 'What open questions should I follow up on?'];
    log.innerHTML = `<div class="empty" style="flex:0;padding:30px 10px 10px"><div class="art">${ICON.chat}</div><h3>Ask about this session</h3><p>Answers come from the ${isLive() ? 'live' : 'saved'} transcript${st.session && st.session.screenshots.length ? ' and its screenshots' : ''}.</p></div>
      <div class="suggest"><div class="hd">Try</div>${q.map(x => `<button data-q="${esc(x)}">${esc(x)}</button>`).join('')}</div>`;
  } else {
    log.innerHTML = msgs.map(msgHtml).join('');
    log.scrollTop = log.scrollHeight;
  }
  $('#composerHint').textContent = isLive() ? 'Recording keeps running while you chat.' : 'Ask anything about this transcript.';
  $('#sendBtn').disabled = st.chat.busy || !st.sid;
}
$('#chatLog').addEventListener('click', e => {
  const q = e.target.closest('[data-q]'); if (q) { $('#chatInput').value = q.dataset.q; sendChat(); return; }
  const ts = e.target.closest('.ts-link'); if (ts) jumpToTime(ts.dataset.ts);
});
$('#composer').addEventListener('submit', e => { e.preventDefault(); sendChat(); });
$('#chatInput').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendChat(); }
});
$('#chatInput').addEventListener('input', e => { const t = e.target; t.style.height = 'auto'; t.style.height = Math.min(140, t.scrollHeight) + 'px'; });
$('#clearChatBtn').onclick = async () => {
  try { await api('DELETE', sessUrl() + '/chat'); st.chat = { messages: [], busy: false }; renderChat(); } catch (e) { fail(e); }
};

async function sendChat() {
  const inp = $('#chatInput');
  const text = inp.value.trim();
  if (!text || st.chat.busy || !st.sid) return;
  if (!st.claude) { toast('Chat needs the Claude Code CLI (claude) installed', 'error'); return; }
  inp.value = ''; inp.style.height = 'auto';
  st.chat.busy = true;
  $('#sendBtn').disabled = true;
  try { await api('POST', sessUrl() + '/chat', { text }); }
  catch (e) { st.chat.busy = false; $('#sendBtn').disabled = false; fail(e); }
}

function onChatMessage(ev) {
  if (ev.project !== st.project || ev.sid !== st.sid) return;
  if (!st.chat.messages.find(m => m.id === ev.message.id)) st.chat.messages.push(ev.message);
  renderChat();
}
function onChatUpdate(ev) {
  if (ev.project !== st.project || ev.sid !== st.sid) return;
  const m = st.chat.messages.find(x => x.id === ev.id); if (!m) return;
  if (ev.delta) m.text += ev.delta;
  if (ev.tool) (m.tools = m.tools || []).push(ev.tool);
  if (ev.done) { m.text = ev.text || m.text; m.status = ev.status; m.error = ev.error; st.chat.busy = false; if (st.side !== 'chat') $('#chatDot').classList.remove('hidden'); }
  const el = $(`#chatLog .msg[data-id="${ev.id}"]`);
  if (el) {
    const tmp = document.createElement('div'); tmp.innerHTML = msgHtml(m);
    el.replaceWith(tmp.firstElementChild);
    const log = $('#chatLog'); log.scrollTop = log.scrollHeight;
  } else renderChat();
  $('#sendBtn').disabled = st.chat.busy;
}

// ── Capture area ───────────────────────────────────────────────────────────
function captureText(cfg) {
  if (!cfg || cfg.mode === 'full') return ['Capture', 'Full screen'];
  if (cfg.mode === 'region') { const r = cfg.rect || {}; return ['Area', `${Math.round(r.w)} × ${Math.round(r.h)} at ${Math.round(r.x)}, ${Math.round(r.y)}`]; }
  return ['Window', cfg.title ? `${cfg.owner} — ${cfg.title}` : cfg.owner];
}
function renderCaptureBtn() {
  const [k, l] = captureText(st.capture && st.capture.config);
  $('#captureKind').textContent = k;
  $('#captureLabel').textContent = l;
}
async function loadCapture() {
  try { st.capture = await api('GET', '/api/capture'); } catch (_) { st.capture = { config: { mode: 'full' } }; }
  renderCaptureBtn();
}

function renderCapturePop(waiting) {
  const pop = $('#capturePop');
  const cfg = (st.capture && st.capture.config) || { mode: 'full' };
  const [, label] = captureText(cfg);
  pop.innerHTML = `
    <div class="pop-title">Screenshot area</div>
    <div class="pop-sub">What <kbd>⌃</kbd><kbd>⇧</kbd><kbd>S</kbd> and Snap capture. Changes apply immediately, even mid-recording, so this window can stay out of your screenshots.</div>
    <div class="modes">
      <button class="mode ${cfg.mode === 'full' ? 'on' : ''}" data-m="full">${ICON.screen}Full screen<small>main display</small></button>
      <button class="mode ${cfg.mode === 'region' ? 'on' : ''}" data-m="region">${ICON.area}Area<small>drag to select</small></button>
      <button class="mode ${cfg.mode === 'window' ? 'on' : ''}" data-m="window">${ICON.window}Window<small>click a window</small></button>
    </div>
    ${waiting ? `<div class="waiting"><span class="spinner"></span>${waiting}</div>` : ''}
    <div class="preview">${st.capture && st.capture.preview ? `<img src="${esc(st.capture.preview)}" alt="Capture preview">` : '<span class="state-sub">No preview yet</span>'}<span class="pv-label">${esc(label)}</span></div>
    <div class="pop-row"><button class="ghost-btn small" id="winListBtn">Choose from window list</button><button class="ghost-btn small" id="pvRefresh">Refresh preview</button></div>
    <div class="win-list hidden" id="winList"></div>`;
  $$('.mode', pop).forEach(b => b.onclick = () => chooseCapture(b.dataset.m));
  $('#pvRefresh').onclick = async () => { try { st.capture = await api('POST', '/api/capture/preview'); renderCapturePop(); } catch (e) { fail(e); } };
  $('#winListBtn').onclick = async () => {
    const wl = $('#winList'); wl.classList.remove('hidden'); wl.innerHTML = '<div style="padding:10px" class="state-sub">Loading windows…</div>';
    try {
      const wins = await api('GET', '/api/capture/windows');
      wl.innerHTML = wins.map((w, i) => `<button data-i="${i}"><span class="wo">${esc(w.owner)}</span><span class="wt">${esc(w.title)}</span></button>`).join('') || '<div style="padding:10px" class="state-sub">No windows found</div>';
      $$('button', wl).forEach(b => b.onclick = async () => {
        const w = wins[+b.dataset.i];
        renderCapturePop('Saving…');
        try { st.capture = await api('POST', '/api/capture', { mode: 'window', windowId: w.windowId, owner: w.owner, title: w.title, rect: w.rect }); } catch (e) { fail(e); }
        renderCaptureBtn(); renderCapturePop();
      });
    } catch (e) { fail(e); }
  };
}
async function chooseCapture(mode) {
  try {
    if (mode === 'full') {
      renderCapturePop('Saving…');
      st.capture = await api('POST', '/api/capture', { mode: 'full' });
    } else {
      renderCapturePop(mode === 'region' ? 'Drag on screen to select the area · Esc to cancel' : 'Click the window to capture · Esc to cancel');
      const res = await api('POST', '/api/capture/select', { kind: mode });
      st.capture = res;
      if (res.cancelled) toast('Selection cancelled');
      else toast(`Screenshots will capture: ${captureText(res.config)[1]}`, 'ok');
    }
  } catch (e) { fail(e); }
  renderCaptureBtn(); renderCapturePop();
}
$('#captureBtn').onclick = e => {
  const pop = $('#capturePop');
  if (!pop.classList.contains('hidden')) { closeMenus(); return; }
  renderCapturePop();
  openMenu(pop, e.currentTarget, 'right');
  api('GET', '/api/capture').then(c => (c.preview ? c : api('POST', '/api/capture/preview')))
    .then(c => { st.capture = c; renderCapturePop(); }).catch(() => {});
};

// ── Settings ───────────────────────────────────────────────────────────────
async function openSettings() {
  let ps = { context_dir: '', style_reference: '', notes: '' };
  if (st.project) { try { ps = await api('GET', `/api/projects/${encodeURIComponent(st.project)}/settings`); } catch (_) { /* */ } }
  const s = st.settings;
  const modelOpts = (cur) => ['opus', 'sonnet', 'haiku'].map(m => `<option value="${m}" ${cur === m ? 'selected' : ''}>${m[0].toUpperCase() + m.slice(1)}</option>`).join('')
    + (cur && !['opus', 'sonnet', 'haiku'].includes(cur) ? `<option value="${esc(cur)}" selected>${esc(cur)}</option>` : '');
  openModal(`
    <div class="modal-head"><h2>Settings</h2><p>Beautify and chat run through Claude Code${st.claude ? '' : ' <b style="color:var(--rec)">(claude CLI not found)</b>'}, so they can view screenshots and use your MCP servers.</p></div>
    <div class="modal-body">
      ${st.project ? `<fieldset class="fset"><legend>Project · ${esc(st.project)}</legend>
        <div class="frow"><label>Knowledge folder</label><input class="field" id="setCtx" value="${esc(ps.context_dir)}" placeholder="/Users/you/Documents/CLIENT">
          <div class="help">Claude Code runs in this folder, so the MCP servers configured there (e.g. <b>claude-context</b>) power the NEW / CONFIRMS / UPDATES callouts.</div></div>
        <div class="frow"><label>Style reference</label><input class="field" id="setStyle" value="${esc(ps.style_reference)}" placeholder="Built-in annotated-transcript style">
          <div class="help">An HTML document whose look to copy (style only, never its content).</div></div>
        <div class="frow"><label>Notes</label><div class="help" style="grid-column:2;margin:6px 0 0">Context and corrections live in the <b>Notes</b> tab (<code>notes.md</code> in the project folder).</div></div>
      </fieldset>` : ''}
      <fieldset class="fset"><legend>AI</legend>
        <div class="frow"><label>Beautify engine</label><select class="field" id="setEngine"><option value="claude" ${s.beautify_engine !== 'api' ? 'selected' : ''}>Claude Code (recommended)</option><option value="api" ${s.beautify_engine === 'api' ? 'selected' : ''}>Gemini / OpenAI API (single pass, no tools)</option></select></div>
        <div class="frow"><label>Beautify model</label><select class="field" id="setBModel">${modelOpts(s.beautify_model)}</select></div>
        <div class="frow"><label>Chat model</label><select class="field" id="setCModel">${modelOpts(s.chat_model)}</select></div>
        <div class="frow"><label>Final version</label><label class="switch" style="padding-top:5px"><input type="checkbox" id="setAuto" ${s.auto_final_beautify ? 'checked' : ''}><span class="knob"></span>Build the final version automatically when recording stops</label></div>
      </fieldset>
      <fieldset class="fset"><legend>Recognizer</legend>
        <div class="frow"><label>Python</label><div style="display:flex;gap:8px"><input class="field" id="setPy" value="${esc(s.recorder_python)}" placeholder="Same as the server"><button class="ghost-btn small" id="checkPy" style="height:30px">Check</button></div>
          <div class="help">The interpreter that has faster-whisper, torch and easyocr installed. <span id="checkRes" class="check-res"></span></div></div>
      </fieldset>
    </div>
    <div class="modal-foot"><button class="ghost-btn" data-close>Cancel</button><button class="primary-btn" id="saveSettings">Save</button></div>`,
  root => {
    $('[data-close]', root).onclick = closeModal;
    $('#checkPy', root).onclick = async () => {
      await api('POST', '/api/settings', { recorder_python: $('#setPy').value.trim() });
      const r = await api('GET', '/api/check-recorder');
      const el = $('#checkRes'); el.className = 'check-res ' + (r.ok ? 'ok' : 'bad');
      el.textContent = r.ok ? `✓ ${r.python} is ready` : `✗ ${r.error}`;
    };
    $('#saveSettings', root).onclick = async () => {
      try {
        st.settings = await api('POST', '/api/settings', {
          beautify_engine: $('#setEngine').value, beautify_model: $('#setBModel').value, chat_model: $('#setCModel').value,
          auto_final_beautify: $('#setAuto').checked, recorder_python: $('#setPy').value.trim(),
        });
        if (st.project) await api('POST', `/api/projects/${encodeURIComponent(st.project)}/settings`, {
          context_dir: $('#setCtx').value.trim(), style_reference: $('#setStyle').value.trim(),
        });
        closeModal(); toast('Settings saved', 'ok'); renderBeautify();
      } catch (e) { fail(e); }
    };
  });
}
$('#settingsBtn').onclick = openSettings;

// ── Theme, tabs, console ───────────────────────────────────────────────────
function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}
try { applyTheme(localStorage.getItem('vr-theme')); } catch (_) { /* */ }
$('#themeBtn').onclick = () => {
  const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  const t = dark ? 'light' : 'dark';
  applyTheme(t);
  try { localStorage.setItem('vr-theme', t); } catch (_) { /* */ }
};

function switchTab(tab) {
  st.tab = tab;
  $$('#mainTabs .tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  $('#tab-live').classList.toggle('hidden', tab !== 'live');
  $('#tab-beautified').classList.toggle('hidden', tab !== 'beautified');
  if (tab === 'beautified') renderDoc();
}
$$('#mainTabs .tab').forEach(b => b.onclick = () => switchTab(b.dataset.tab));
function switchSide(side) {
  st.side = side;
  $$('#sideTabs .tab').forEach(b => b.classList.toggle('active', b.dataset.side === side));
  $('#side-shots').classList.toggle('hidden', side !== 'shots');
  $('#side-chat').classList.toggle('hidden', side !== 'chat');
  $('#side-notes').classList.toggle('hidden', side !== 'notes');
  $('#notesState').classList.toggle('hidden', side !== 'notes');
  if (side === 'chat') { $('#chatDot').classList.add('hidden'); renderChat(); setTimeout(() => $('#chatInput').focus(), 0); }
  renderShots();
  $('#clearChatBtn').classList.toggle('hidden', !(side === 'chat' && st.chat.messages.length));
}
$$('#sideTabs .tab').forEach(b => b.onclick = () => switchSide(b.dataset.side));

function toggleConsole() {
  const c = $('#console');
  c.classList.toggle('hidden');
  if (!c.classList.contains('hidden')) { const pre = $('#consoleLog'); pre.textContent = st.logs.join('\n'); pre.scrollTop = pre.scrollHeight; }
}
$('#consoleToggle').onclick = toggleConsole;
$('#consoleClose').onclick = toggleConsole;
function addLog(line) {
  st.logs.push(line); if (st.logs.length > 600) st.logs.shift();
  $('#lastLog').textContent = line;
  const c = $('#console');
  if (!c.classList.contains('hidden')) { const pre = $('#consoleLog'); pre.textContent += '\n' + line; pre.scrollTop = pre.scrollHeight; }
}

function renderAll() {
  renderControl(); renderTranscript(); renderShots(); renderBeautify(); renderDoc(); renderChat(); renderSessionMenu();
}

// ── WebSocket ──────────────────────────────────────────────────────────────
let ws, wsRetry = 0;
function connect() {
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onopen = () => { wsRetry = 0; $('#connDot').className = 'conn-dot on'; };
  ws.onclose = () => {
    $('#connDot').className = 'conn-dot off'; $('#lastLog').textContent = 'Disconnected from server — retrying…';
    setTimeout(connect, Math.min(5000, 500 * 2 ** wsRetry++));
  };
  ws.onmessage = e => handle(JSON.parse(e.data));
}

async function handle(m) {
  switch (m.type) {
    case 'hello': {
      const wasActive = st.recorder.active;
      st.recorder = m.recorder;
      if (m.recorder.pipeline) st.pipeline = m.recorder.pipeline;
      st.logs = m.log || [];
      if (st.logs.length) $('#lastLog').textContent = st.logs[st.logs.length - 1];
      else $('#lastLog').textContent = 'Connected';
      (m.jobs || []).forEach(j => { st.jobs[key(j.project, j.sid)] = j; });
      if (st.project && wasActive !== m.recorder.active) await reloadSession();
      renderControl(); renderBeautify(); renderJobCard();
      break;
    }
    case 'recorder': {
      const prev = st.recorder;
      st.recorder = m;
      if (m.pipeline) st.pipeline = m.pipeline;
      // a new live session got its id → switch to it
      if (m.active && m.sid && m.sid !== prev.sid) {
        if (st.project !== m.project) { await selectProject(m.project, { keepSession: true }); }
        await loadSessions();
        await selectSession(m.sid);
        switchTab('live');
      }
      if (m.state === 'listening' && prev.state !== 'listening') renderGhost();
      renderControl();
      if (!m.active && prev.active) { await loadSessions(); }
      break;
    }
    case 'pipeline':
      st.pipeline = { speech: m.speech, current: m.current, pending: m.pending || [], pause: m.pause };
      renderPipeline();
      break;
    case 'level':
      st.levels.push(m.rms); st.levels.shift();
      break;
    case 'entry':
      if (m.project === st.project && m.sid === st.sid) appendEntry(m.idx, m.entry);
      break;
    case 'shot':
      if (m.project === st.project && m.sid === st.sid) upsertShot(m.shot, m.new);
      break;
    case 'log':
      addLog(m.line);
      break;
    case 'session_changed':
      if (m.project === st.project) {
        await loadSessions();
        if (m.sid === st.sid) await reloadSession();
        if (m.finished && m.sid === st.sid) toast('Recording finished · transcript saved', 'ok');
      }
      break;
    case 'beautify':
      onJob(m);
      break;
    case 'beautify_progress': {
      const j = st.jobs[key(m.project, m.sid)];
      if (j) { j.chars = m.chars; const el = $('#jcChars'); if (el && m.project === st.project && m.sid === st.sid) el.textContent = `${(m.chars / 1024).toFixed(0)} KB generated`; }
      break;
    }
    case 'chat_message': onChatMessage(m); break;
    case 'chat_update': onChatUpdate(m); break;
    case 'embedding': break;
    case 'shot_deleted':
      if (m.project === st.project && m.sid === st.sid) removeShotLocal(m.path);
      break;
    case 'notes':
      if (m.project === st.project && m.client !== CLIENT_ID) applyNotes(m.text, true);
      break;
    case 'toast': toast(m.text, m.level || 'info'); break;
  }
}

// ── Notes (projects/<p>/notes.md — single source for context + corrections) ─
const notesUrl = () => `/api/projects/${encodeURIComponent(st.project)}/notes`;
let notesTimer = null;

function countCorrections(text) { return (text.match(/^\s*[-*]\s*".+?"\s*(→|->)\s*".+?"/gm) || []).length; }
function setNotesState(t, dirty) { const el = $('#notesState'); el.textContent = t; el.classList.toggle('dirty', !!dirty); }

function applyNotes(text, external) {
  st.notes.text = text;
  const area = $('#notesArea');
  if (!st.notes.dirty) {
    const pos = area.selectionStart;
    area.value = text;
    if (document.activeElement === area) area.setSelectionRange(pos, pos);
    if (external) { area.classList.remove('bump'); void area.offsetWidth; area.classList.add('bump'); }
  }
  const n = countCorrections(text);
  $('#notesCount').textContent = n ? n : '';
  if (!st.notes.dirty) setNotesState(text.trim() ? 'Saved' : '');
}

async function loadNotes() {
  if (!st.project) return;
  try {
    const d = await api('GET', notesUrl());
    st.notes.dirty = false;
    st.notes.path = d.path;
    $('#notesPath').textContent = d.path.replace(/^.*\/projects\//, 'projects/');
    applyNotes(d.text);
  } catch (e) { fail(e); }
}

async function saveNotesNow() {
  clearTimeout(notesTimer);
  if (!st.notes.dirty || !st.project) return;
  const text = $('#notesArea').value;
  setNotesState('Saving…');
  try {
    await api('PUT', notesUrl(), { text, client: CLIENT_ID });
    st.notes.dirty = false;
    applyNotes(text);
  } catch (e) { setNotesState('Not saved', true); fail(e); }
}

$('#notesArea').addEventListener('input', () => {
  st.notes.dirty = true;
  setNotesState('Editing…', true);
  clearTimeout(notesTimer);
  notesTimer = setTimeout(saveNotesNow, 700);
});
$('#notesArea').addEventListener('blur', saveNotesNow);
window.addEventListener('beforeunload', () => {
  if (st.notes.dirty && st.project) navigator.sendBeacon && fetch(notesUrl(), { method: 'PUT', keepalive: true, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: $('#notesArea').value, client: CLIENT_ID }) });
});

// ── Correction popover: select transcript text → fix it → goes to Notes ───
function selectionInfo(win, root) {
  const sel = win.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const text = sel.toString().replace(/\s+/g, ' ').trim();
  if (!text || text.length > 400) return null;
  const range = sel.getRangeAt(0);
  if (root && !root.contains(range.commonAncestorContainer)) return null;
  return { text, rect: range.getBoundingClientRect() };
}

function openFixPop(info, offset = { x: 0, y: 0 }) {
  if (!st.project) return;
  const pop = $('#fixPop');
  pop.innerHTML = `
    <div class="pop-title">Correct the transcript</div>
    <div class="pop-sub" style="margin-bottom:4px">Saved to Notes as a rule, so Beautify and Ask apply it everywhere, and live recognition uses it as a hint.</div>
    <label>Heard</label><div class="orig">${esc(info.text)}</div>
    <label for="fixTo">Should be</label><textarea class="field" id="fixTo" rows="2">${esc(info.text)}</textarea>
    <label for="fixWhy">Comment <span style="text-transform:none;letter-spacing:0;font-weight:500">(optional)</span></label>
    <input class="field" id="fixWhy" placeholder="e.g. product name, person's name">
    <div class="row-btns"><span class="kb"><kbd>↵</kbd> save · <kbd>esc</kbd> close</span><button class="ghost-btn small" id="fixCancel">Cancel</button><button class="primary-btn" id="fixSave" style="height:28px;font-size:12.5px">Add to notes</button></div>`;
  pop.classList.remove('hidden');
  const r = info.rect, w = pop.offsetWidth, h = pop.offsetHeight;
  let left = r.left + offset.x + r.width / 2 - w / 2;
  let top = r.bottom + offset.y + 10;
  if (top + h > window.innerHeight - 10) top = Math.max(10, r.top + offset.y - h - 10);
  pop.style.left = `${Math.max(10, Math.min(left, window.innerWidth - w - 10))}px`;
  pop.style.top = `${top}px`;
  const to = $('#fixTo');
  to.focus(); to.select();
  const save = async () => {
    const fixed = to.value.replace(/\s+/g, ' ').trim();
    if (!fixed || fixed === info.text) { closeFixPop(); return; }
    try {
      await saveNotesNow();
      const d = await api('POST', notesUrl() + '/correction', { original: info.text, fixed, comment: $('#fixWhy').value });
      applyNotes(d.text, true);
      closeFixPop();
      toast(`Added to Notes: “${info.text}” → “${fixed}”`, 'ok');
    } catch (e) { fail(e); }
  };
  $('#fixSave').onclick = save;
  $('#fixCancel').onclick = closeFixPop;
  pop.onkeydown = e => {
    if (e.key === 'Escape') { e.stopPropagation(); closeFixPop(); }
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); save(); }
  };
}
function closeFixPop() { $('#fixPop').classList.add('hidden'); }
document.addEventListener('mousedown', e => { if (!$('#fixPop').contains(e.target)) closeFixPop(); });

$('#transcript').addEventListener('mouseup', () => {
  setTimeout(() => { const info = selectionInfo(window, $('#transcript')); if (info) openFixPop(info); }, 0);
});
$('#docFrame').addEventListener('load', () => {
  const frame = $('#docFrame');
  let doc;
  try { doc = frame.contentDocument; } catch (_) { return; }
  if (!doc) return;
  doc.addEventListener('mousedown', closeFixPop);
  doc.addEventListener('mouseup', () => {
    setTimeout(() => {
      const info = selectionInfo(frame.contentWindow, null);
      if (info) { const fr = frame.getBoundingClientRect(); openFixPop(info, { x: fr.left, y: fr.top }); }
    }, 0);
  });
});

// ── Boot ───────────────────────────────────────────────────────────────────
(async function boot() {
  try {
    const s = await api('GET', '/api/status');
    st.settings = s.settings; st.recorder = s.recorder; st.claude = s.claude;
    if (s.recorder.pipeline) st.pipeline = s.recorder.pipeline;
    if (!s.helper) toast('screenshot_helper binary is missing — run ./compile.sh', 'error', 8000);
    await loadProjects();
    if (st.project) await selectProject(st.project);
    else renderAll();
  } catch (e) { fail(e); renderAll(); }
  loadCapture();
  connect();
})();
