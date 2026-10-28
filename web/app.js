/* ushi - client */
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
  external: { files: [], speakers: [] },
  frames: { scenes: [], selection: null, stats: null, busy: false },
  vis: { lines: new Set(), shots: new Set(), frames: new Set() },   // hidden items (left out of Beautify)
  starting: false,
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
  pause: '<svg viewBox="0 0 20 20" class="ico"><path d="M7.5 5v10M12.5 5v10"/></svg>',
  play: '<svg viewBox="0 0 20 20" class="ico"><path d="M7 5l8 5-8 5z"/></svg>',
  open: '<svg viewBox="0 0 16 16" class="ico" style="width:14px;height:14px"><path d="M9 3h4v4M13 3L7.5 8.5M11 9.5V13H3V5h3.5"/></svg>',
  eye: '<svg viewBox="0 0 20 20" class="ico"><path d="M1.8 10S4.8 4.5 10 4.5 18.2 10 18.2 10 15.2 15.5 10 15.5 1.8 10 1.8 10z"/><circle cx="10" cy="10" r="2.6"/></svg>',
  similar: '<svg viewBox="0 0 20 20" class="ico"><path d="M5.6 6.5A11 11 0 0 0 2.5 10s2.5 4.5 7 4.5c1 0 1.9-.2 2.7-.5M8 5.7a7 7 0 0 1 1.5-.2c4.5 0 7 4.5 7 4.5a11.6 11.6 0 0 1-1.4 2M3.5 3.5l12 12"/><path d="M14.5 2.5h3v3M17.5 2.5l-3 3" /></svg>',
  eyeOff: '<svg viewBox="0 0 20 20" class="ico"><path d="M8.2 4.7A8 8 0 0 1 10 4.5c5.2 0 8.2 5.5 8.2 5.5a14 14 0 0 1-2.4 3.1M5.3 6.2A13.6 13.6 0 0 0 1.8 10s3 5.5 8.2 5.5a7.7 7.7 0 0 0 3.6-.9M8.2 8.3a2.6 2.6 0 0 0 3.5 3.5M3 3l14 14"/></svg>',
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

function toast(text, level = 'info', ms = 4200, action = null) {
  const el = document.createElement('div');
  el.className = `toast ${level}`;
  el.innerHTML = (level === 'ok' ? '<svg viewBox="0 0 20 20" class="ico"><path d="M5 10.5l3 3 7-7"/></svg>' : '') + `<span>${esc(text)}</span>`
    + (action ? `<button class="toast-act">${esc(action.label)}</button>` : '');
  if (action) el.querySelector('.toast-act').onclick = () => { el.remove(); action.fn(); };
  $('#toasts').appendChild(el);
  setTimeout(() => { el.style.transition = 'opacity .3s'; el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, ms);
}
// Model downloads on first use: one live toast per model, gone when it is done.
const dlToasts = {};
function onDownload(m) {
  let el = dlToasts[m.label];
  if (m.finished) {
    if (el) el.remove();
    delete dlToasts[m.label];
    if (m.error) toast(`Could not download ${m.label}: ${m.error}`, 'error', 10000);
    else toast(`${m.label} downloaded`, 'ok');
    api('GET', '/api/status').then(s => { st.models = s.models; renderAll(); }).catch(() => {});
    return;
  }
  if (!el) {
    el = dlToasts[m.label] = document.createElement('div');
    el.className = 'toast download';
    el.innerHTML = '<div class="dl-text"></div><div class="dl-bar"><i></i></div>';
    $('#toasts').appendChild(el);
  }
  const total = m.total_mb ? ` of ~${fmtMB(m.total_mb)}` : '';
  el.querySelector('.dl-text').textContent = `Downloading ${m.label} (first use): ${fmtMB(m.done_mb)}${total}`;
  el.querySelector('.dl-bar i').style.width = m.total_mb ? `${Math.min(99, 100 * m.done_mb / m.total_mb)}%` : '30%';
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
  if (!m) return sid || '-';
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
// Speakers: numbers from diarization, or "Me" / "Them" (app+mic recordings: microphone / the app)
const speakerColor = n => n === 'Me' ? 'var(--s4)' : n === 'Them' ? 'var(--s2)' : `var(--s${((n - 1) % 8) + 1})`;
const speakerName = n => typeof n === 'string' ? n : `Speaker ${n}`;

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
    const last = st.settings.last_project, want = new URLSearchParams(location.search).get('project');
    st.project = (want && st.projects.some(p => p.name === want) && want) || (st.recorder.active && st.recorder.project) ||
      (st.projects.find(p => p.name === last) || st.projects[0] || {}).name || null;
  }
  renderProjectMenu();
}

async function selectProject(name, { keepSession = false } = {}) {
  st.project = name;
  $('#projectName').textContent = name || '-';
  if (!keepSession) { st.sid = null; st.session = null; resetFrames(); applyVis(null); }
  loadNotes();
  loadExternal();
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
  renderExternal();
  st.docVersion = null;
  try {
    st.session = await api('GET', sessUrl());
  } catch (e) { fail(e); return; }
  applyVis(st.session.visibility);
  resetFrames();
  st.follow = true;
  if (!quiet) st.chat = { messages: [], busy: false };
  $('#chatDot').classList.add('hidden');
  $('#sessionName').innerHTML = st.session.live ? `<span style="color:var(--rec)">●</span> Live · ${esc(sidLabel(sid))}` : esc(sidLabel(sid));
  renderAll();
  loadChat();
  loadFrames();
}

async function reloadSession() {
  if (!st.project || !st.sid) return;
  try {
    st.session = await api('GET', sessUrl());
    applyVis(st.session.visibility);
    $('#sessionName').innerHTML = st.session.live ? `<span style="color:var(--rec)">●</span> Live · ${esc(sidLabel(st.sid))}` : esc(sidLabel(st.sid));
    renderTranscript(); renderShots(); renderBeautify(); renderDoc();
    if (!recActive()) renderControl();
    loadFrames();
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
  m.innerHTML = '<button class="mi" data-new-session="1"><span class="tick">+</span><span class="mi-main"><div class="mi-title">New session</div><div class="mi-sub">Next recording starts a fresh transcript</div></span></button><div class="sep"></div><div class="group">Sessions</div>' + st.sessions.map(s => {
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
  if (e.target.closest('[data-new-session]')) { closeMenus(); newSession(); return; }
  const b = e.target.closest('.mi[data-sid]'); if (!b) return;
  closeMenus();
  selectSession(b.dataset.sid);
});
$('#projectBtn').onclick = e => { renderProjectMenu(); openMenu($('#projectMenu'), e.currentTarget); };
$('#sessionBtn').onclick = e => { loadSessions().then(() => openMenu($('#sessionMenu'), $('#sessionBtn'))).catch(fail); };

// ── Recorder control strip ─────────────────────────────────────────────────
function renderControl() {
  renderControlStrip();
  renderMedia();
}
function renderControlStrip() {
  const r = st.recorder, el = $('#control');
  const s = st.settings;
  const mine = r.project === st.project;
  const idle = !r.active && (r.state === 'idle' || !r.state);

  if (idle && !st.starting) {
    // Start appends to the selected session; "New session" starts a fresh transcript.
    const kind = s.source || 'mic', isFile = isFileKind(kind), isVideo = kind !== 'file' && isFile;
    const cont = st.sid && st.session && !st.session.live && !isFile;
    loadSourceLists();
    el.innerHTML = `
      <div class="rec-main">
        <button class="rec-btn" id="recBtn" ${st.project ? '' : 'disabled'}><span class="dot"></span>${{ file: 'Transcribe file', video: 'Transcribe video', 'video-rt': 'Play & transcribe' }[kind] || 'Start recording'}</button>
        <div class="rec-hint">${isFile ? `New session · ${media.start || media.end != null ? `range ${fmtPos(media.start)} – ${media.end != null ? fmtPos(media.end) : 'end'}` : 'times are positions in the file'}` : cont
          ? `Appends to <b>${esc(sidLabel(st.sid))}</b> · <button class="link-btn" id="newSessBtn">new session instead</button>`
          : 'Starts a new session'}${kind === 'app+mic' ? ' · use headphones' : ''}</div>
      </div>
      <div class="opt-group">
        ${sourceControl(s)}
        <div class="opt"><span class="opt-label">Engine</span>
          <select class="field" id="engSel" title="Speech recognition model">${Object.entries(ENGINES).map(([v]) => `<option value="${v}" ${(s.engine || 'whisper') === v ? 'selected' : ''}>${esc(engineLabel(v))}</option>`).join('')}</select></div>
        <div class="opt"><span class="opt-label">Language</span>
          <div class="seg" id="langSeg">${['auto', 'en', 'ru'].map(l => `<button data-v="${l}" class="${(s.lang || 'auto') === l ? 'on' : ''}" ${engineBlocksLang(s.engine, l) ? `disabled title="${ENGINES.gigaam.name} recognizes Russian only"` : ''}>${l === 'auto' ? 'Auto' : l.toUpperCase()}</button>`).join('')}</div></div>
        <div class="opt"><span class="opt-label">Speakers</span>
          <select class="field" id="spkSel">${['auto', 'off', '2', '3', '4', '5', '6', '8'].map(v => `<option value="${v}" ${String(s.speakers || 'auto') === v ? 'selected' : ''}>${v === 'auto' ? 'Detect' : v === 'off' ? 'Off' : v}</option>`).join('')}</select></div>
        ${isFile ? '' : `<div class="opt"><span class="opt-label">Auto-stop</span><input class="field num" id="minInp" type="number" min="1" value="${esc(s.minutes || 180)}"> min</div>`}
        ${pauseControl(s.pause || 1.5)}
        ${isFile ? '' : `<label class="switch opt"><input type="checkbox" id="audioChk" ${s.save_audio ? 'checked' : ''}><span class="knob"></span>Save audio</label>`}
        ${kind === 'file' ? '' : `<label class="switch opt" title="${isVideo ? 'Take a frame of the video every second (full resolution) and keep the ones that show something new' : 'Capture the screen every second and keep the frames that show something new'}"><input type="checkbox" id="autoChk" ${s.auto_frames ? 'checked' : ''}><span class="knob"></span>Auto frames</label>`}
      </div>`;
    $('#recBtn').onclick = startRecording;
    const nb = $('#newSessBtn'); if (nb) nb.onclick = newSession;
    bindSource();
    $$('#langSeg button').forEach(b => b.onclick = () => { st.settings.lang = b.dataset.v; renderControl(); });
    $('#engSel').onchange = e => {
      st.settings.engine = e.target.value;
      if (engineBlocksLang(st.settings.engine, st.settings.lang)) st.settings.lang = 'ru';
      renderControl();
    };
    $('#spkSel').onchange = e => { st.settings.speakers = e.target.value; };
    if (!isFile) {
      $('#minInp').onchange = e => { st.settings.minutes = +e.target.value || 180; };
      $('#audioChk').onchange = e => { st.settings.save_audio = e.target.checked; };
    }
    if (kind !== 'file') $('#autoChk').onchange = e => { st.settings.auto_frames = e.target.checked; };
    bindPause();
    return;
  }
  if (r.state === 'error' && !st.starting) {
    el.innerHTML = `<div><div class="error-text">${esc(r.error || 'Recognizer error')}</div><div class="state-sub mono" style="white-space:pre-wrap;max-height:3.2em;overflow:hidden">${esc((r.detail || '').split('\n').slice(-2).join('\n'))}</div></div>
      <div class="spacer"></div><button class="ghost-btn" id="showConsole">Show console</button><button class="ghost-btn" id="dismissErr">Dismiss</button>`;
    $('#showConsole').onclick = toggleConsole;
    $('#dismissErr').onclick = () => { st.recorder = { state: 'idle' }; renderControl(); };
    return;
  }
  if (r.state === 'ready' && !st.starting) {
    el.innerHTML = `
      <button class="rec-btn" id="recBtn"><span class="dot"></span>Start recording</button>
      <div><div class="state-title">Ready to record</div>
        <div class="state-sub">Models are loaded · recording starts when you press Start</div></div>
      <div class="spacer"></div><button class="ghost-btn" id="cancelReady">Cancel</button>`;
    $('#recBtn').onclick = startRecording;
    $('#cancelReady').onclick = cancelReady;
    return;
  }
  if (st.starting || r.state === 'loading') {
    el.innerHTML = `
      <button class="rec-btn busy" disabled><span class="spin-w"></span>Starting recording…</button>
      <div><div class="state-title">Preparing the recognizer</div>
        <div class="state-sub">${esc(r.state === 'loading' && r.detail ? r.detail : 'Launching…')} · the first start can take a minute</div></div>`;
    return;
  }
  if (r.state === 'stopping' || r.state === 'finalizing') {
    el.innerHTML = `
      <button class="stop-btn busy" disabled><span class="spin-w dark"></span>Stopping…</button>
      <div><div class="state-title">${r.state === 'stopping' ? 'Finishing the last phrase' : 'Finalizing session'}</div>
        <div class="state-sub">${esc(r.detail || 'Transcribing the remaining speech')}${r.diarization ? ' · then speakers are assigned' : ''}</div></div>
      <div class="spacer"></div><div class="state-sub">${esc(r.project)} · ${esc(sidLabel(r.sid))}</div>`;
    return;
  }
  // listening
  const isFile = isFileKind(r.source), isVideo = isFile && r.source !== 'file';
  el.innerHTML = `
    <button class="stop-btn" id="recBtn" title="Stop (same as Ctrl+C)"><span class="sq"></span>${isFile ? 'Stop' : 'Stop recording'}</button>
    <button class="ghost-btn mute-btn${r.muted ? ' on' : ''}" id="muteBtn" title="${isFile ? (r.muted ? 'Continue reading the file' : 'Pause reading the file; the session keeps running') : r.muted ? 'Unmute the microphone and continue' : 'Mute the microphone; the session keeps running'}">${r.muted ? ICON.play + 'Resume' : ICON.pause + 'Pause'}</button>
    <div class="rec-state"><span class="rec-pill${r.muted ? ' paused' : ''}"><span class="dot"></span>${r.muted ? 'PAUSED' : 'REC'}</span><span class="timer mono" id="timer">0:00</span></div>
    <canvas class="meter" id="meter" width="336" height="68"></canvas>
    <div class="pipe-status" id="pipeStatus"></div>
    ${pauseControl(st.pipeline.pause || s.pause || 1.5)}
    ${isFile ? '' : minutesControl(r.minutes || s.minutes || 180)}
    ${r.engine && r.engine !== 'whisper' ? `<span class="chip" title="Speech recognition engine">${esc(ENGINES[r.engine]?.short || r.engine)}</span>` : ''}
    ${r.lang ? `<span class="chip" title="Detected language">${esc(r.lang.toUpperCase())}</span>` : ''}
    ${r.diarization ? '<span class="chip" title="Speakers are numbered live and refined after the session">SPEAKERS</span>' : ''}
    ${sourceChip(r)}
    <div class="spacer"></div>
    ${!mine ? `<span class="state-sub">Recording in <b>${esc(r.project)}</b></span>` : ''}
    ${isFile && !isVideo ? '' : `<button class="ghost-btn auto-btn${r.auto_frames ? ' on' : ''}" id="autoBtn" title="${r.auto_frames ? 'Stop automatic frames' : isVideo ? 'Take a frame of the video every second and keep the ones that show something new' : 'Capture the screen every second and keep the frames that show something new'}"><span class="auto-dot"></span>Auto${r.auto_frames && r.frame_stats && r.frame_stats.scenes != null ? ` · ${r.frame_stats.scenes}` : ''}</button>`}
    ${isFile && r.source !== 'video-rt' ? '' : `<button class="ghost-btn" id="snapBtn" title="${isVideo ? 'Save the video frame on screen (full resolution)' : 'Take a screenshot of the capture area'}">${ICON.camera}Snap</button>`}`;
  $('#recBtn').onclick = stopRecording;
  $('#muteBtn').onclick = toggleMute;
  const ab = $('#autoBtn'); if (ab) ab.onclick = toggleAutoFrames;
  const sb = $('#snapBtn'); if (sb) sb.onclick = snap;
  bindPause();
  bindMinutes();
  renderPipeline();
  tickTimer();
}

// ── Audio source: microphone, an app's audio (+ mic), or an audio file ─────
const SOURCES = [['mic', 'Microphone'], ['app+mic', 'App + mic'], ['app', 'App only'], ['file', 'Audio file'],
  ['video-rt', 'Video file (real-time player)'], ['video', 'Video file (background)']];
const FILE_KINDS = ['file', 'video', 'video-rt'];
const isFileKind = k => FILE_KINDS.includes(k);
const audioUrl = () => `/api/projects/${encodeURIComponent(st.project)}/audio`;
const audioSrc = { apps: null, files: null, filesProject: null, loading: false };

function sourceControl(s) {
  const kind = s.source || 'mic';
  let extra = '';
  if (kind === 'app' || kind === 'app+mic') {
    const a = audioSrc.apps;
    if (!a) extra = '<span class="state-sub">Loading apps…</span>';
    else if (!a.available) extra = `<span class="state-sub" title="${esc(a.error)}">App audio is unavailable</span>`;
    else {
      const list = a.apps.slice();
      if (s.source_app && !list.some(x => x.bundle === s.source_app)) list.push({ bundle: s.source_app, name: s.source_app, gone: true });
      extra = `<select class="field" id="appSel" title="The app whose audio to transcribe (● = playing sound now)">
        ${list.length ? list.map(x => `<option value="${esc(x.bundle)}" ${x.bundle === s.source_app ? 'selected' : ''}>${x.playing ? '● ' : ''}${esc(x.name)}${x.gone ? ' (not running)' : ''}</option>`).join('') : '<option value="">No apps with audio</option>'}
      </select><button class="ghost-btn small" id="appRefresh" title="Refresh the list of apps">↻</button>`;
      // Windows without per-app capture: say that every sound will be in the recording.
      const note = (list.find(x => x.note) || {}).note;
      if (note) extra += `<span class="state-sub" title="${esc(note)}">⚠ All apps are recorded</span>`;
    }
  } else if (isFileKind(kind)) {
    const files = (audioSrc.files || []).filter(f => kind === 'file' || f.video);
    extra = files.length
      ? `<select class="field" id="fileSel" title="${kind === 'file' ? "Audio file to transcribe (stored in the project's audio folder)" : "Video file to transcribe (stored in the project's audio folder)"}">
          ${files.map(f => `<option value="${esc(f.name)}" ${f.name === s.source_file ? 'selected' : ''}>${esc(f.name)}</option>`).join('')}
          <option value="__upload">Upload another file…</option></select>`
      : '<button class="ghost-btn small" id="fileUpload">Choose a file…</button>';
  }
  return `<div class="opt"><span class="opt-label">Source</span>
    <select class="field" id="srcSel" title="What to transcribe">${SOURCES.map(([v, l]) => `<option value="${v}" ${kind === v ? 'selected' : ''}>${l}</option>`).join('')}</select>${extra}</div>`;
}

function bindSource() {
  $('#srcSel').onchange = e => { st.settings.source = e.target.value; fitSourceFile(); renderControl(); };
  const as = $('#appSel'); if (as) as.onchange = e => { st.settings.source_app = e.target.value; };
  const ar = $('#appRefresh'); if (ar) ar.onclick = () => { audioSrc.apps = null; renderControl(); };
  const fs = $('#fileSel');
  if (fs) fs.onchange = e => {
    if (e.target.value === '__upload') { e.target.value = st.settings.source_file || ''; pickAudioFile(); }
    else st.settings.source_file = e.target.value;
  };
  const fu = $('#fileUpload'); if (fu) fu.onclick = pickAudioFile;
}

// Fetch what the selected source needs (apps playing audio / the project's audio files), once.
async function loadSourceLists() {
  const s = st.settings, kind = s.source || 'mic';
  const needApps = (kind === 'app' || kind === 'app+mic') && !audioSrc.apps;
  const needFiles = isFileKind(kind) && st.project && audioSrc.filesProject !== st.project;
  if (audioSrc.loading || (!needApps && !needFiles)) return;
  audioSrc.loading = true;
  try {
    if (needApps) {
      audioSrc.apps = await api('GET', '/api/audio/apps');
      const apps = audioSrc.apps.apps || [];
      if (!apps.some(a => a.bundle === s.source_app) && apps.length && (!s.source_app || apps[0].playing))
        s.source_app = (apps.find(a => a.playing) || apps[0]).bundle;
    }
    if (needFiles) {
      audioSrc.files = (await api('GET', audioUrl())).files;
      audioSrc.filesProject = st.project;
      fitSourceFile();
    }
  } catch (e) { fail(e); } finally { audioSrc.loading = false; }
  if (!st.recorder.active && !st.starting) renderControl();
}

// The selected file must suit the source: a video source needs a video file.
function fitSourceFile() {
  const s = st.settings, files = audioSrc.files;
  if (!files || !isFileKind(s.source)) return;
  const ok = files.filter(f => s.source === 'file' || f.video);
  if (!ok.some(f => f.name === s.source_file)) s.source_file = ok.length ? ok[0].name : '';
}

function pickAudioFile() {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.accept = 'audio/*,video/*,.mp3,.m4a,.wav,.aac,.flac,.ogg,.opus,.aiff,.caf,.webm,.mp4,.mov,.mkv';
  inp.onchange = () => { if (inp.files[0]) uploadAudio(inp.files[0]); };
  inp.click();
}

async function uploadAudio(file) {
  if (!st.project) return;
  toast(`Uploading ${file.name}…`);
  try {
    const res = await fetch(`${audioUrl()}?name=${encodeURIComponent(file.name)}`, { method: 'PUT', body: file });
    const d = await res.json().catch(() => null);
    if (!res.ok) throw new Error((d && d.detail) || `${res.status} ${res.statusText}`);
    audioSrc.files = d.files; audioSrc.filesProject = st.project;
    const up = d.files.find(f => f.name === d.name);
    if (!isFileKind(st.settings.source) || (up && !up.video)) st.settings.source = up && up.video ? 'video' : 'file';
    st.settings.source_file = d.name;
    renderControl();
    toast(`${d.name} is ready. Set the range if needed and press the red button.`, 'ok');
  } catch (e) { fail(e); }
}

function sourceChip(r) {
  const kind = r.source || 'mic';
  if (kind === 'mic') return '';
  if (isFileKind(kind)) {
    const p = r.source_progress;
    return `<span class="chip" id="srcChip" title="${esc(r.source_name || 'Audio file')}">${kind === 'file' ? 'FILE' : 'VIDEO'}${p && p.total ? ` ${fmtDur(p.done)} / ${fmtDur(p.total)}` : ''}</span>`;
  }
  const name = (r.source_name || r.source_app || 'app').toUpperCase();
  return `<span class="chip" title="${kind === 'app+mic' ? 'App audio → Them, microphone → Me' : 'App audio only'}">${esc(name)}${kind === 'app+mic' ? ' + MIC' : ''}</span>`;
}

// ── Media panel: the chosen audio / video file - the range to transcribe, the real-time player ──
// Idle: a player with native controls; "Set start" / "Set end" take its position. While a file
// is being transcribed the range is fixed; "video-rt" plays along, kept in step with the
// recognizer's position (source_progress.pos), background modes just show where it is.
const media = { key: '', kind: '', duration: 0, start: 0, end: null, live: false };
const mediaUrl = (project, name) => `/api/projects/${encodeURIComponent(project)}/audio/${encodeURIComponent(name)}/media`;
const VIDEO_RE = /\.(mp4|mov|mkv|webm|m4v|avi)$/i;

function fmtPos(sec) {
  const tenths = Math.round(Math.max(0, +sec || 0) * 10);
  return `${fmtDur(Math.floor(tenths / 10))}.${tenths % 10}`;
}
// '90', '1:30', '1:02:03.5' → seconds; '' → null; garbage → NaN
function parsePos(v) {
  v = String(v ?? '').trim();
  if (!v) return null;
  const parts = v.split(':');
  if (parts.length > 3 || parts.some(p => !/^\d+(\.\d*)?$/.test(p.trim()))) return NaN;
  return parts.reduce((a, p) => a * 60 + parseFloat(p), 0);
}

// The running file session, or (idle / starting) the file picked in the source menu.
function mediaTarget() {
  const r = st.recorder, s = st.settings;
  if (r.active && isFileKind(r.source)) {
    const o = r.options || {};
    return o.source_file ? { project: r.project, name: o.source_file, kind: r.source, live: true } : null;
  }
  if ((r.active || r.state === 'error') && !st.starting) return null;
  if (isFileKind(s.source) && s.source_file && st.project)
    return { project: st.project, name: s.source_file, kind: s.source, live: !!st.starting };
  return null;
}

function renderMedia() {
  const panel = $('#mediaPanel'), t = mediaTarget();
  if (!t) {
    const v = $('#mVideo'); if (v) v.pause();
    if (media.key) { panel.innerHTML = ''; media.key = ''; }
    panel.classList.add('off');
    return;
  }
  const key = `${t.project}/${t.name}`;
  const file = (audioSrc.files || []).find(f => f.name === t.name);
  panel.classList.remove('off');
  panel.classList.toggle('audio', !(file ? file.video : VIDEO_RE.test(t.name)));
  if (media.key !== key) buildMedia(t, key);
  const v = $('#mVideo');
  if (media.live && !t.live) {          // the run is over: back to the idle player
    v.pause(); v.playbackRate = 1;
    if (media.kind !== 'video-rt' || v.dataset.autoMuted) { v.muted = false; delete v.dataset.autoMuted; }
  }
  media.live = t.live; media.kind = t.kind;
  panel.classList.toggle('live', t.live);
  v.controls = !t.live;
  if (t.live && t.kind !== 'video-rt') v.muted = true;
  $('#mRangeCtl').classList.toggle('hidden', t.live);
  if (t.live) mediaSync(); else updateMedia();
}

function buildMedia(t, key) {
  const panel = $('#mediaPanel'), o = st.recorder.options || {};
  media.key = key; media.duration = 0;
  media.start = t.live ? (+o.start || 0) : 0;
  media.end = t.live && o.end != null ? +o.end : null;
  panel.innerHTML = `
    <video id="mVideo" preload="metadata" playsinline src="${esc(mediaUrl(t.project, t.name))}"></video>
    <div class="media-side">
      <div class="m-title"><b>${esc(t.name)}</b><span class="state-sub" id="mState"></span></div>
      <div class="m-track" id="mTrack" title="Click to move the player here"><div class="m-range" id="mRange"></div><div class="m-done" id="mDone"></div><div class="m-head" id="mHead"></div></div>
      <div class="m-ctl">
        <span class="m-now mono" id="mNow">0:00.0</span>
        <span id="mRangeCtl" class="m-ctl">
          <span class="sep"></span>
          <button class="ghost-btn small" id="mSetStart" title="Start transcribing at the player's position">Set start</button>
          <input class="field m-time" id="mStart" title="Start: seconds, M:SS or H:MM:SS">
          <span>–</span>
          <input class="field m-time" id="mEnd" placeholder="end" title="End: seconds, M:SS or H:MM:SS (empty = the end of the file)">
          <button class="ghost-btn small" id="mSetEnd" title="Stop transcribing at the player's position">Set end</button>
          <button class="link-btn" id="mReset" title="Transcribe the whole file">Whole file</button>
          <span class="state-sub" id="mLen"></span>
        </span>
      </div>
      <div class="state-sub" id="mHint"></div>
    </div>`;
  const v = $('#mVideo');
  v.addEventListener('loadedmetadata', () => {
    media.duration = isFinite(v.duration) ? v.duration : 0;
    if (!media.live) v.currentTime = media.start;
    updateMedia();
  });
  v.addEventListener('timeupdate', updateMedia);
  v.addEventListener('seeked', updateMedia);
  $('#mTrack').onclick = e => {
    if (media.live || !media.duration) return;
    const r = e.currentTarget.getBoundingClientRect();
    v.currentTime = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * media.duration;
  };
  $('#mSetStart').onclick = () => setRange(v.currentTime, media.end != null && media.end <= v.currentTime ? null : media.end);
  $('#mSetEnd').onclick = () => setRange(media.start, v.currentTime);
  $('#mReset').onclick = () => setRange(0, null);
  $('#mStart').onchange = e => { const x = parsePos(e.target.value); setRange(x == null ? 0 : x, media.end); };
  $('#mEnd').onchange = e => setRange(media.start, parsePos(e.target.value));
}

function setRange(start, end) {
  const dur = media.duration || Infinity;
  if (Number.isNaN(start) || Number.isNaN(end)) { toast('Use seconds, M:SS or H:MM:SS', 'error'); updateMedia(); return; }
  start = Math.max(0, Math.min(start, dur));
  if (end != null && end >= dur - 0.05) end = null;      // up to the end of the file
  if (end != null && end <= start) { toast('The end must be after the start', 'error'); updateMedia(); return; }
  media.start = Math.round(start * 10) / 10;
  media.end = end == null ? null : Math.round(end * 10) / 10;
  const v = $('#mVideo'); if (v && (v.currentTime < media.start || (media.end != null && v.currentTime > media.end))) v.currentTime = media.start;
  renderControlStrip();     // the Start button's hint shows the range
  updateMedia();
}

function updateMedia() {
  const v = $('#mVideo'); if (!v) return;
  const r = st.recorder, p = r.source_progress, dur = media.duration;
  const end = media.end ?? dur, pct = x => `${dur ? Math.max(0, Math.min(100, 100 * x / dur)) : 0}%`;
  const livePos = media.live && p && p.pos != null ? p.pos : null;
  const head = media.live && media.kind !== 'video-rt' && livePos != null ? livePos : v.currentTime;
  const rg = $('#mRange'); rg.style.left = pct(media.start); rg.style.width = dur ? `calc(${pct(end)} - ${pct(media.start)})` : '0';
  const dn = $('#mDone');
  dn.style.display = livePos != null ? '' : 'none';
  if (livePos != null) { dn.style.left = pct(media.start); dn.style.width = `calc(${pct(livePos)} - ${pct(media.start)})`; }
  $('#mHead').style.left = pct(head);
  $('#mNow').textContent = `${fmtPos(head)}${dur ? ` / ${fmtDur(dur)}` : ''}`;
  const si = $('#mStart'), ei = $('#mEnd');
  if (document.activeElement !== si) si.value = fmtPos(media.start);
  if (document.activeElement !== ei) ei.value = media.end == null ? '' : fmtPos(media.end);
  $('#mLen').textContent = dur ? `${fmtDur(end - media.start)} to transcribe` : '';
  $('#mState').textContent = media.live && livePos != null
    ? `${p.paused ? 'paused at' : media.kind === 'video-rt' ? 'playing' : 'reading'} ${fmtPos(livePos)} · range ${fmtDur(media.start)}–${fmtDur(end)}` : '';
  const hint = $('#mHint');
  if (media.live) {
    hint.innerHTML = media.kind === 'video-rt'
      ? `Plays in step with the recognizer · <b>Pause</b> / <b>Stop</b> above · <kbd>⌃</kbd><kbd>⇧</kbd><kbd>S</kbd> or <b>Snap</b> saves the frame on screen${v.dataset.autoMuted ? ' · <button class="link-btn" id="mUnmute">sound is off: turn on</button>' : ''}`
      : 'Read faster than real time, not played · the picture shows where the recognizer is';
    const um = $('#mUnmute'); if (um) um.onclick = () => { v.muted = false; delete v.dataset.autoMuted; updateMedia(); };
    return;
  }
  const d = r.source_done, name = (st.settings.source_file || '');
  const resume = d && !d.complete && d.file === name && d.position > media.start + 1;
  hint.innerHTML = (resume ? `Last run stopped at <b>${fmtPos(d.position)}</b> · <button class="link-btn" id="mResume">continue from there</button> · ` : '') +
    ({ 'video-rt': 'Plays from the start mark in real time; Ctrl+Shift+S takes the video frame, not a screenshot',
       video: 'Faster than real time in the background; auto frames come straight from the video',
       file: 'Faster than real time' }[media.kind] || '');
  const rb = $('#mResume'); if (rb) rb.onclick = () => setRange(d.position, media.end);
}

// Keep the real-time player on the recognizer's clock; in the background modes show its position.
function mediaSync() {
  const v = $('#mVideo'), r = st.recorder, p = r.source_progress;
  if (!v || !media.live) return;
  if (!p || p.pos == null) {                      // not reading yet (decoding the audio)
    if (Math.abs(v.currentTime - media.start) > 0.3 && !v.seeking) v.currentTime = media.start;
    updateMedia();
    return;
  }
  if (media.kind === 'video-rt') {
    if (p.paused || r.state !== 'listening') {
      if (!v.paused) v.pause();
      if (Math.abs(v.currentTime - p.pos) > 0.3 && !v.seeking) v.currentTime = p.pos;
    } else {
      const drift = v.currentTime - p.pos;
      if (Math.abs(drift) > 0.8) { if (!v.seeking) v.currentTime = p.pos; v.playbackRate = 1; }
      else v.playbackRate = drift > 0.15 ? 0.95 : drift < -0.15 ? 1.05 : 1;
      if (v.paused && !v.ended) v.play().catch(() => {           // no sound without a click: play muted
        v.muted = true; v.dataset.autoMuted = '1'; v.play().catch(() => {}); updateMedia();
      });
    }
  } else if (!v.seeking && Math.abs(v.currentTime - p.pos) > 2) {
    if (!v.paused) v.pause();
    v.currentTime = p.pos;
  }
  updateMedia();
}

// Called inside the click on "Play & transcribe": a play() now lets later play() calls have sound.
function unlockPlayer() {
  const v = $('#mVideo'); if (!v) return;
  v.currentTime = media.start;
  const pr = v.play();
  if (pr) pr.then(() => { if (!(st.recorder.source_progress && st.recorder.active)) v.pause(); }).catch(() => {});
}

function newSession() {
  st.sid = null; st.session = null; st.chat = { messages: [], busy: false };
  applyVis(null);
  $('#sessionName').textContent = 'New session';
  renderAll();
  loadFrames();
}

function pauseControl(v) {
  return `<div class="opt" title="How long a silence ends a phrase. Shorter: text appears sooner, in smaller pieces. Longer: fewer, longer chunks. Can be changed while recording.">
    <span class="opt-label">Phrase gap</span><input class="field num" id="pauseInp" type="number" min="0.2" max="10" step="0.1" value="${(+v).toFixed(1)}"> s</div>`;
}
function bindPause() {
  const inp = $('#pauseInp'); if (!inp) return;
  inp.onchange = async () => {
    const v = Math.max(0.2, Math.min(10, +inp.value || 1.5));
    inp.value = v.toFixed(1);
    st.settings.pause = v;
    try {
      const r = await api('POST', '/api/record/pause', { seconds: v });
      if (recActive()) toast(`Phrase gap set to ${r.pause.toFixed(1)} s, applies from the next phrase`, 'ok');
    } catch (e) { fail(e); }
  };
}

function minutesControl(v) {
  return `<div class="opt" title="Recording stops on its own after this many minutes. Can be changed while recording.">
    <span class="opt-label">Auto-stop</span><input class="field num" id="minLiveInp" type="number" min="1" step="1" value="${esc(Math.round(+v))}"> min</div>`;
}
function bindMinutes() {
  const inp = $('#minLiveInp'); if (!inp) return;
  inp.onchange = async () => {
    const r = st.recorder;
    const v = Math.max(1, Math.round(+inp.value || 180));
    const elapsed = (Date.now() / 1000 - (r.listening_at || r.started_at || Date.now() / 1000)) / 60;
    if (v <= elapsed) {
      inp.value = Math.round(r.minutes || 180);
      toast(`Already recording for ${Math.floor(elapsed)} min: pick a later auto-stop`, 'error');
      return;
    }
    inp.value = v;
    st.settings.minutes = v;
    try {
      const res = await api('POST', '/api/record/minutes', { minutes: v });
      toast(`Auto-stop set to ${Math.round(res.minutes)} min`, 'ok');
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
    const isFile = isFileKind(st.recorder.source);
    const l1 = st.recorder.muted
      ? `<span class="a-dot paused"></span><b>Paused</b><span class="pl-sub">${isFile ? 'reading the file is on hold' : `${(st.recorder.source || 'mic') === 'mic' ? 'microphone' : 'audio'} muted`} · session continues</span>`
      : speaking
      ? `<span class="a-dot speech"></span><b>Speech ${fmtSec(pl.speech)}</b><span class="pl-sub">${isFile ? 'in the file' : 'recording'} · sent after a ${(pl.pause || 1.5).toFixed(1)} s pause${isFile ? '' : ' · <button class="link-btn cut-btn" title="End this phrase now and send it to the recognizer, without waiting for the pause">Cut</button>'}</span>`
      : `<span class="a-dot"></span><b>${isFile ? 'Reading the file' : 'Listening'}</b><span class="pl-sub">waiting for speech</span>`;
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
  t.innerHTML = fmtDur(el) + (r.minutes && !isFileKind(r.source) ? `<small>/ ${Math.round(r.minutes)} min</small>` : '');
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
    const speech = st.pipeline.speech != null && !st.recorder.muted;
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

// Speech recognition engines (main.py --engine)
const ENGINES = {
  whisper: { name: 'Whisper turbo', short: 'WHISPER' },
  gigaam: { name: 'GigaAM v3 (RU)', short: 'GIGAAM', ruOnly: true },
  parakeet: { name: 'Parakeet v3 (RU+EN)', short: 'PARAKEET' },
};
// Not downloaded yet (the app can ship without models): say so next to the engine.
const fmtMB = mb => mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${Math.round(mb)} MB`;
const engineLabel = v => {
  const m = st.models && st.models[v];
  return ENGINES[v].name + (m && !m.cached ? ` · downloads ${fmtMB(m.mb)} on first use` : '');
};
const engineBlocksLang = (engine, lang) => !!ENGINES[engine || 'whisper']?.ruOnly && lang === 'en';

async function startRecording() {
  if (st.starting) return;
  const s = st.settings;
  st.starting = true;
  renderControl();
  if (st.recorder.state === 'ready') {         // models already loaded: start right away
    closeReadyPrompt();
    try { await api('POST', '/api/record/start', { minutes: s.minutes || 180, pause: s.pause || 1.5 }); }
    catch (e) { st.starting = false; renderControl(); fail(e); }
    return;
  }
  try {
    const engine = s.engine || 'whisper';
    if (engineBlocksLang(engine, s.lang)) s.lang = 'ru';
    const check = await api('GET', `/api/check-recorder?engine=${encodeURIComponent(engine)}`);
    if (!check.ok) {
      st.starting = false; renderControl();
      openSettings();
      toast(`The recognizer's Python can't load its models: ${check.error}`, 'error', 9000);
      return;
    }
    const kind = s.source || 'mic';
    if ((kind === 'app' || kind === 'app+mic') && !s.source_app) throw new Error('Pick the app whose audio to transcribe');
    if (isFileKind(kind) && !s.source_file) throw new Error(kind === 'file' ? 'Choose an audio file first' : 'Choose a video file first');
    if (kind === 'video-rt') unlockPlayer();      // still inside the click: allow playback with sound later
    const app = ((audioSrc.apps && audioSrc.apps.apps) || []).find(a => a.bundle === s.source_app);
    await api('POST', '/api/record/start', {
      project: st.project, engine, lang: s.lang || 'auto', speakers: s.speakers || 'auto',
      minutes: s.minutes || 180, pause: s.pause || 1.5, save_audio: !isFileKind(kind) && !!s.save_audio,
      auto_frames: kind !== 'file' && !!s.auto_frames,
      continue_sid: !isFileKind(kind) && st.sid && st.session && !st.session.live ? st.sid : '',
      source: kind, source_app: s.source_app || '', source_file: s.source_file || '',
      source_name: isFileKind(kind) ? s.source_file : kind === 'mic' ? '' : (app ? app.name : s.source_app),
      ...(isFileKind(kind) ? { start: media.start || 0, end: media.end } : {}),
    });
  } catch (e) { st.starting = false; renderControl(); fail(e); }
}
async function cancelReady() {
  closeReadyPrompt();
  try { await api('POST', '/api/record/stop'); } catch (e) { fail(e); }
}
// The models took a while to load: don't start recording behind the user's back, ask.
// Closing this without an answer leaves the Start button, which then starts at once.
function offerReady() {
  openModal(`<div class="modal-head"><h2>Ready to record</h2><p>Models are loaded. Recording has not started yet: start it now?</p></div>
    <div class="modal-foot"><button class="ghost-btn" id="readyCancel">Cancel</button><button class="ghost-btn" data-close>Later</button><button class="primary-btn" id="readyGo">Start recording</button></div>`,
  root => {
    $('[data-close]', root).onclick = closeModal;
    $('#readyCancel', root).onclick = cancelReady;
    $('#readyGo', root).onclick = startRecording;
    $('#readyGo', root).focus();
  });
  if (document.hidden && 'Notification' in window && Notification.permission === 'granted') {
    try { new Notification('ushi: ready to record', { body: 'Models are loaded. Recording starts when you press Start.' }); } catch (e) { /* not supported here */ }
  }
}
function closeReadyPrompt() { if ($('#readyGo')) closeModal(); }
async function stopRecording() {
  if (st.recorder.state !== 'listening') return;
  st.recorder = { ...st.recorder, state: 'stopping', detail: 'Stopping' };
  renderControl();
  try { await api('POST', '/api/record/stop'); } catch (e) { fail(e); }
}
async function toggleMute() {
  const muted = !st.recorder.muted;
  const btn = $('#muteBtn'); if (btn) btn.disabled = true;
  try { await api('POST', '/api/record/mute', { muted }); }
  catch (e) { fail(e); if (btn) btn.disabled = false; }
}
async function toggleAutoFrames() {
  const on = !st.recorder.auto_frames;
  const btn = $('#autoBtn'); if (btn) btn.disabled = true;
  try { await api('POST', '/api/record/auto', { on }); st.settings.auto_frames = on; }
  catch (e) { fail(e); if (btn) btn.disabled = false; }
}
async function cutPhrase() { try { await api('POST', '/api/record/cut'); } catch (e) { fail(e); } }
// The pipeline status is re-rendered several times a second, so the Cut links are delegated,
// on pointerdown: a click needs press and release on the same node, and a re-render between
// the two replaces it, so the click never fires.
document.addEventListener('pointerdown', e => { if (e.button === 0 && e.target.closest('.cut-btn')) { e.preventDefault(); cutPhrase(); } });
async function snap() {
  // Watching a video: the frame on screen, not where the recognizer's clock is.
  const v = $('#mVideo'), at = st.recorder.source === 'video-rt' && v ? v.currentTime : undefined;
  try { await api('POST', '/api/record/snap', at != null ? { at } : {}); } catch (e) { fail(e); }
}
$('#snapSideBtn').onclick = snap;

// ── Transcript ─────────────────────────────────────────────────────────────
// ── Visibility: hidden lines / screenshots / frames are kept but left out of Beautify ──
const VIS_FIELD = { line: 'lines', shot: 'shots', frame: 'frames' };
const isHidden = (kind, key) => st.vis[VIS_FIELD[kind]].has(kind === 'frame' ? +key : String(key));
function applyVis(v) {
  v = v || {};
  st.vis = { lines: new Set(v.lines || []), shots: new Set(v.shots || []), frames: new Set((v.frames || []).map(Number)) };
}
const EYE_TITLE = {
  line: ['Hide this line: it stays here, faded, but Beautify leaves it out', 'Show this line again'],
  shot: ['Hide this screenshot: Beautify leaves it out', 'Use this screenshot again'],
  frame: ['Hide this frame: Beautify leaves it out', 'Use this frame again'],
};
const SIM_TITLE = {
  line: 'Hide this line and the ones like it (same or nearly the same words)',
  shot: 'Hide this picture and every one that looks like it (screenshots and frames)',
  frame: 'Hide this frame and every picture that looks like it (frames and screenshots)',
};
const simBtn = (kind, key) => `<button class="sim-btn" data-sim="${kind}" data-key="${esc(key)}" title="${esc(SIM_TITLE[kind])}">${ICON.similar}</button>`;
const tools = (kind, key) => `<span class="row-tools">${simBtn(kind, key)}${eyeBtn(kind, key)}</span>`;

// "Such and similar" for lines: same words after normalising, or (longer lines) mostly the
// same character trigrams - repeated phrases, recognizer hallucinations, filler like "угу".
const normText = t => (t || '').toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, ' ').replace(/\s+/g, ' ').trim();
function trigrams(s) { const g = new Set(), x = ` ${s} `; for (let i = 0; i < x.length - 2; i++) g.add(x.slice(i, i + 3)); return g; }
function textSimilar(a, b, ga) {
  if (!a || !b) return false;
  if (a === b) return true;
  const wa = a.split(' '), wb = b.split(' ');
  if (wa.length <= 3 || wb.length <= 3) {
    const A = new Set(wa), B = new Set(wb);
    return A.size === B.size && [...A].every(w => B.has(w));
  }
  if (Math.min(a.length, b.length) / Math.max(a.length, b.length) < 0.6) return false;
  const gb = trigrams(b); let inter = 0;
  ga.forEach(g => { if (gb.has(g)) inter++; });
  return inter / (ga.size + gb.size - inter) >= 0.6;
}

async function hideSimilar(kind, key) {
  if (!st.project || !st.sid) return;
  try {
    let undo, n;
    if (kind === 'line') {
      const target = (st.session.entries || []).find(e => e.key === key);
      if (!target) return;
      const a = normText(target.text), ga = trigrams(a);
      const keys = [...new Set(st.session.entries.filter(e => (e.kind === 'speech' || e.kind === 'selection') && e.key
        && e.kind === target.kind && textSimilar(a, normText(e.text), ga)).map(e => e.key))];
      const fresh = keys.filter(k => !st.vis.lines.has(k));
      fresh.forEach(k => st.vis.lines.add(k)); refreshVisDom();
      applyVis(await api('POST', sessUrl() + '/visibility', { kind: 'line', keys, hidden: true }));
      n = fresh.length;
      undo = async () => { applyVis(await api('POST', sessUrl() + '/visibility', { kind: 'line', keys: fresh, hidden: false })); refreshVisDom(); };
      toast(n > 1 ? `Hid ${n} similar lines` : n ? 'Hid this line (no similar ones)' : 'Already hidden', 'ok', 10000, n ? { label: 'Undo', fn: undo } : null);
    } else {
      toast('Looking for similar pictures…', 'info', 2500);
      const r = await api('POST', sessUrl() + '/hide-similar', { kind, key });
      applyVis(r.visibility);
      const fr = r.hidden.frame || [], sh = r.hidden.shot || [];
      n = fr.length + sh.length;
      undo = async () => {
        if (fr.length) applyVis(await api('POST', sessUrl() + '/visibility', { kind: 'frame', keys: fr, hidden: false }));
        if (sh.length) applyVis(await api('POST', sessUrl() + '/visibility', { kind: 'shot', keys: sh, hidden: false }));
        refreshVisDom();
      };
      const parts = [fr.length ? `${fr.length} frame${fr.length > 1 ? 's' : ''}` : '', sh.length ? `${sh.length} screenshot${sh.length > 1 ? 's' : ''}` : ''].filter(Boolean);
      toast(n ? `Hid ${parts.join(' and ')} that look alike` : 'Already hidden', 'ok', 10000, n ? { label: 'Undo', fn: undo } : null);
    }
    refreshVisDom();
  } catch (e) { fail(e); }
}
document.addEventListener('click', e => {
  const b = e.target.closest('.sim-btn');
  if (!b) return;
  e.stopPropagation(); e.preventDefault();
  hideSimilar(b.dataset.sim, b.dataset.key);
}, true);

function eyeBtn(kind, key) {
  const h = isHidden(kind, key);
  return `<button class="eye-btn" data-eye="${kind}" data-key="${esc(key)}" title="${esc(EYE_TITLE[kind][h ? 1 : 0])}">${h ? ICON.eyeOff : ICON.eye}</button>`;
}
const visAttr = (kind, key) => `data-vis="${kind}:${esc(key)}"`;

// ── Describe: a vision model writes what an image shows (replaces its OCR text in Beautify) ──
const DESC_TITLE = ['Describe: a vision model writes what is on this image; Beautify uses it instead of the OCR text',
  'Described · click to describe again'];
function descBtn(path, described) {
  if (!path) return '';
  return `<button class="desc-btn${described ? ' on' : ''}" data-desc="${esc(path)}" title="${esc(DESC_TITLE[described ? 1 : 0])}">${ICON.sparkle}</button>`;
}
const textUrl = (url, ext) => url ? url.replace(/\.(png|jpe?g|webp)$/i, ext) + `?t=${Date.now()}` : null;
async function fetchText(url) {
  if (!url) return '';
  try { const r = await fetch(url); return r.ok ? (await r.text()).trim() : ''; } catch (_) { return ''; }
}
// What the lightbox shows under an image: its description and its OCR text.
async function imageTextsHtml(url, extra = '') {
  const [desc, ocr] = await Promise.all([fetchText(textUrl(url, '.desc.md')), fetchText(textUrl(url, '.txt'))]);
  return [extra, desc ? 'Description:\n' + desc : '', ocr ? 'OCR:\n' + ocr : 'OCR is not ready yet.'].filter(Boolean).join('\n\n');
}
const describing = new Set();
async function describeImage(path) {
  if (!st.project || !st.sid || describing.has(path)) return;
  describing.add(path);
  const btns = () => $$(`.desc-btn[data-desc="${CSS.escape(path)}"]`);
  btns().forEach(b => b.classList.add('busy'));
  try {
    await api('POST', sessUrl() + '/describe', { path });
    markDescribed(path);
    toast('Described · Beautify will use it instead of the OCR text', 'ok');
  } catch (e) { fail(e); }
  describing.delete(path);
  btns().forEach(b => b.classList.remove('busy'));
}
function markDescribed(path) {
  const shot = st.session && (st.session.screenshots || []).find(s => s.path === path);
  if (shot && !shot.described) upsertShot({ ...shot, described: true }, false);
  const sc = st.frames.scenes.find(s => (s.last_path || s.path) === path);
  if (sc && !sc.described) upsertFrame({ id: sc.id, described: true });
  $$(`.desc-btn[data-desc="${CSS.escape(path)}"]`).forEach(b => { b.classList.add('on'); b.title = DESC_TITLE[1]; });
  const lb = $('#lightbox');
  if (!lb.classList.contains('hidden') && lb.dataset.path === path && lb.refreshTexts) lb.refreshTexts();
}
document.addEventListener('click', e => {
  const b = e.target.closest('.desc-btn');
  if (!b) return;
  e.stopPropagation(); e.preventDefault();
  describeImage(b.dataset.desc);
}, true);
const hidCls = (kind, key) => (isHidden(kind, key) ? ' is-hidden' : '');

function refreshVisDom() {
  $$('[data-vis]').forEach(el => {
    const i = el.dataset.vis.indexOf(':');
    const kind = el.dataset.vis.slice(0, i), k = el.dataset.vis.slice(i + 1);
    const h = isHidden(kind, k);
    el.classList.toggle('is-hidden', h);
    $$('.eye-btn', el).forEach(b => {
      if (b.dataset.eye !== kind || b.dataset.key !== k) return;
      b.innerHTML = h ? ICON.eyeOff : ICON.eye; b.title = EYE_TITLE[kind][h ? 1 : 0];
    });
  });
  renderFramesBar();
}

async function toggleHidden(kind, key) {
  if (!st.project || !st.sid) return;
  const field = VIS_FIELD[kind], k = kind === 'frame' ? +key : String(key);
  const hidden = !st.vis[field].has(k);
  if (hidden) st.vis[field].add(k); else st.vis[field].delete(k);
  refreshVisDom();
  try { applyVis(await api('POST', sessUrl() + '/visibility', { kind, key: k, hidden })); refreshVisDom(); }
  catch (e) { fail(e); if (hidden) st.vis[field].delete(k); else st.vis[field].add(k); refreshVisDom(); }
}
document.addEventListener('click', e => {
  const b = e.target.closest('.eye-btn');
  if (!b) return;
  e.stopPropagation(); e.preventDefault();
  toggleHidden(b.dataset.eye, b.dataset.key);
}, true);

function entryHtml(e, prev, isNew) {
  if (e.deleted) return '';
  const nw = isNew ? ' new' : '';
  if (e.kind === 'screenshot') {
    const name = (e.path || '').split('/').pop();
    const shot = (st.session.screenshots || []).find(s => s.path === e.path);
    const url = shot && shot.url ? shot.url : null;
    return `<div class="row shot-row${nw}${hidCls('shot', name)}" data-time="${esc(e.time)}" ${visAttr('shot', name)}><span class="t">${esc(e.time)}</span><div class="body">
      <div class="shot-inline" data-path="${esc(e.path)}">${url ? `<img src="${esc(url)}" alt="">` : ''}<span class="lbl"><b>Screenshot</b>${esc(name)}</span></div></div>${tools('shot', name)}</div>`;
  }
  const k = e.key || '';
  if (e.kind === 'selection') {
    return `<div class="row${nw}${hidCls('line', k)}" data-time="${esc(e.time)}" ${visAttr('line', k)}><span class="t">${esc(e.time)}</span><div class="body"><div class="sel-quote"><span class="k">Selected text</span>${esc(e.text)}</div></div>${k ? tools('line', k) : ''}</div>`;
  }
  const cont = e.speaker != null && prev && prev.kind === 'speech' && prev.speaker === e.speaker;
  const spk = e.speaker != null ? `<div class="spk" style="--sc:${speakerColor(e.speaker)}">${esc(speakerName(e.speaker))}</div>` : '';
  return `<div class="row${e.speaker != null ? ' has-spk' : ''}${cont ? ' cont' : ''}${nw}${hidCls('line', k)}" data-time="${esc(e.time)}" ${visAttr('line', k)}><span class="t">${esc(e.time)}</span><div class="body">${spk}<p>${esc(e.text)}</p></div>${k ? tools('line', k) : ''}</div>`;
}

// An auto frame shown in the transcript at the moment it appeared on screen.
function frameRowHtml(g, isNew) {
  const s = g.scene, url = s.last_url || s.url, t = hms(s.wall_start);
  return `<div class="row shot-row frame-row${isNew ? ' new' : ''}${hidCls('frame', s.id)}" data-time="${esc(t)}" data-fid="${s.id}" ${visAttr('frame', s.id)}><span class="t">${esc(t)}</span><div class="body">
    <div class="shot-inline frame-inline" data-fid="${s.id}">${url ? `<img src="${esc(url)}" alt="">` : ''}<span class="lbl"><b>Auto frame #${s.id}</b>on screen ${fmtSceneDur(g.duration)}${g.shows > 1 ? ` · shown ${g.shows}×` : ''}${s.cls ? ` · ${esc(s.cls)}` : ''}</span></div></div>${tools('frame', s.id)}</div>`;
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
  const items = entries.map((e, i) => ({ t: e.time, i, e }))
    .concat(frameGroups().map((g, j) => ({ t: hms(g.scene.wall_start), i: -1, g })));
  items.sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : a.i - b.i));
  box.innerHTML = items.map((x, i) => x.g ? frameRowHtml(x.g, false) : entryHtml(x.e, items[i - 1] && items[i - 1].e, false)).join('')
    + '<div id="pipeline"></div><div id="ghost"></div>';
  renderChunks();
  renderGhost();
  if (st.follow) box.scrollTop = box.scrollHeight;
}

function emptyTranscript() {
  if (!st.project) return `<div class="empty"><div class="art">${ICON.mic}</div><h3>Create a project to begin</h3><p>Projects keep transcripts, screenshots and documents together.</p></div>`;
  return `<div class="empty"><div class="art">${ICON.mic}</div><h3>${st.sid ? 'Ready when you are' : 'New session'}</h3>
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

const CUT_BTN = '<button class="link-btn cut-btn" title="End this phrase now and send it to the recognizer, without waiting for the pause">Cut</button>';
function renderGhost() {
  const g = $('#ghost'); if (!g) return;
  const r = st.recorder;
  if (!isLive() || !r.active) { g.innerHTML = ''; return; }
  const sp = st.pipeline.speech;
  let inner;
  if (r.state === 'stopping' || r.state === 'finalizing') {
    inner = `<span class="spinner tiny"></span><span>Finishing…</span><span class="pl-sub">${esc(r.detail || 'transcribing the remaining speech')}</span>`;
  } else if (r.state === 'ready') {
    inner = `<span class="a-dot"></span><span>Ready</span><span class="pl-sub">models are loaded · press Start recording</span>`;
  } else if (r.state !== 'listening') {
    inner = `<span class="spinner tiny"></span><span>Starting…</span><span class="pl-sub">${esc(r.detail || 'loading models')}</span>`;
  } else if (r.muted) {
    inner = `<span class="a-dot paused"></span><span>Paused</span><span class="pl-sub">${isFileKind(r.source) ? 'reading the file is on hold' : 'microphone is muted'} · press Resume to continue</span>`;
  } else if (sp != null) {
    inner = `<span class="wave"><i></i><i></i><i></i><i></i></span><span>Speaking · ${fmtSec(sp)}</span>
      <span class="pl-sub">goes to transcription after a ${(st.pipeline.pause || 1.5).toFixed(1)} s pause</span>${isFileKind(r.source) ? '' : CUT_BTN}`;
  } else {
    inner = `<span class="listen-dot"></span><span class="shimmer">Listening…</span><span class="pl-sub">waiting for speech</span>`;
  }
  g.innerHTML = `<div class="row ghost-row"><span class="t">now</span><div class="body"><p>${inner}</p></div></div>`;
  if (st.follow) { const box = $('#transcript'); box.scrollTop = box.scrollHeight; }
}

$('#transcript').addEventListener('scroll', () => {
  const box = $('#transcript');
  st.follow = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  $('#jumpLive').classList.toggle('hidden', st.follow || !isLive());
});
$('#jumpLive').onclick = () => { st.follow = true; const b = $('#transcript'); b.scrollTop = b.scrollHeight; $('#jumpLive').classList.add('hidden'); };
$('#transcript').addEventListener('click', e => {
  const fi = e.target.closest('.frame-inline');
  if (fi) { openFrameLightbox(+fi.dataset.fid); return; }
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
    ...(s.described ? [['Described', 'done']] : []),
    [s.beautified ? `Beautified v${s.beautified}` : 'Beautified',
      s.beautified ? 'done' : inJob ? 'active' : ''],
  ];
}
function shotCardHtml(s, enter) {
  const url = s.url ? `${s.url}` : '';
  return `<div class="shot-card${enter ? ' enter' : ''}${hidCls('shot', s.name)}" data-path="${esc(s.path)}" data-time="${esc(s.time)}" ${visAttr('shot', s.name)}>
    <div class="card-tools">${descBtn(s.path, s.described)}${simBtn('shot', s.name)}${eyeBtn('shot', s.name)}<button class="shot-del" data-del="${esc(s.path)}" title="Delete screenshot">${ICON.trash}</button></div>
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
  const t = e.target.closest('.thumb'); if (t) { openLightbox(t.dataset.path); return; }
  const card = e.target.closest('.shot-card'); if (card && card.dataset.time) jumpToTime(card.dataset.time);
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
    <div style="display:flex;gap:6px"><button class="ghost-btn small" id="lbDel" title="Delete screenshot">${ICON.trash}</button><button class="ghost-btn small lb-desc" id="lbDesc">${ICON.sparkle}Describe</button><button class="ghost-btn small" id="lbJump">Find in transcript</button><button class="ghost-btn small" id="lbClose">Close</button></div></div>
    <div class="lb-ocr" id="lbOcr">Loading OCR text…</div></div>`;
  lb.classList.remove('hidden');
  lb.dataset.path = path;
  lb.refreshTexts = async () => { if (shot.url) $('#lbOcr').textContent = await imageTextsHtml(shot.url); };
  lightboxDescribe(path);
  $('#lbClose').onclick = closeLightbox;
  $('#lbJump').onclick = () => { closeLightbox(); jumpToTime(shot.time); };
  $('#lbDel').onclick = () => {
    const b = $('#lbDel');
    if (b.dataset.armed) { closeLightbox(); deleteShot(path); return; }
    b.dataset.armed = '1'; b.textContent = 'Click again to delete'; b.style.color = '#ff6b78';
  };
  lb.onclick = e => { if (e.target === lb || e.target.classList.contains('lb-img')) closeLightbox(); };
  lb.refreshTexts();
}
// The lightbox's Describe button: busy while the model works, then the texts are reloaded.
function lightboxDescribe(path) {
  const b = $('#lbDesc'); if (!b) return;
  const sync = () => { b.disabled = describing.has(path); b.lastChild.textContent = describing.has(path) ? 'Describing…' : 'Describe'; };
  sync();
  b.onclick = async () => { const p = describeImage(path); sync(); await p; sync(); };
}
function closeLightbox() { $('#lightbox').classList.add('hidden'); }

// ── Auto frames ────────────────────────────────────────────────────────────
// Scenes come from autoframes.py (live: 'frame' events; saved: frames/<sid>/scenes.json).
// A revisit (same_as) is the same image shown again: counted on its original card.
const hms = iso => (iso || '').slice(11, 19);
const secs = (a, b) => Math.max(0, (new Date(b) - new Date(a)) / 1000);
const fmtSceneDur = s => s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`;

// Frames belong to one session: cleared whenever the project or session changes.
function resetFrames() { st.frames = { scenes: [], selection: null, stats: null, busy: false }; }

async function loadFrames() {
  if (!st.project || !st.sid) { resetFrames(); renderFrames(); return; }
  const project = st.project;
  const sid = st.sid;
  try {
    const d = await api('GET', sessUrl() + '/frames');
    if (sid !== st.sid || project !== st.project) return;   // switched meanwhile
    st.frames.scenes = d.scenes; st.frames.selection = d.selection;
  } catch (_) { st.frames.scenes = []; st.frames.selection = null; }
  st.frames.stats = isLive() ? st.recorder.frame_stats || null : null;
  renderFrames();
  if (st.frames.scenes.length) renderTranscript();   // frames sit in the transcript at their time
}

function upsertFrame(scene) {
  const list = st.frames.scenes;
  const i = list.findIndex(s => s.id === scene.id);
  if (i < 0 && !scene.path) return;     // an update (OCR done…) of a frame this page doesn't have
  if (i >= 0) list[i] = { ...list[i], ...scene }; else list.push(scene);
  renderFrames();
  syncFrameRow(scene.same_as || scene.id, i < 0 && !scene.same_as);
}

// Keep the transcript's inline row for a frame in step (new / updated / shown again).
function syncFrameRow(id, isNew) {
  const box = $('#transcript');
  if (!box || box.querySelector('.empty')) return;
  const g = frameGroups().find(x => x.scene.id === id); if (!g) return;
  const tmp = document.createElement('div'); tmp.innerHTML = frameRowHtml(g, isNew);
  const row = tmp.firstElementChild;
  const old = box.querySelector(`.row[data-fid="${id}"]`);
  if (old) { old.replaceWith(row); return; }
  const later = $$('#transcript .row[data-time]').find(r => r.dataset.time > row.dataset.time);
  box.insertBefore(row, later || $('#pipeline'));
  if (st.follow) box.scrollTop = box.scrollHeight;
}

function jumpToFrame(id) {
  switchTab('live');
  const row = $(`#transcript .row[data-fid="${id}"]`);
  if (!row) return;
  st.follow = false;
  row.scrollIntoView({ block: 'center', behavior: 'smooth' });
  row.classList.add('flash');
  setTimeout(() => row.classList.remove('flash'), 1400);
}

function frameGroups() {
  const byId = new Map();
  for (const s of st.frames.scenes) {
    if (s.same_as) continue;
    byId.set(s.id, { scene: s, shows: 1, duration: secs(s.wall_start, s.wall_end) });
  }
  for (const s of st.frames.scenes) {
    const g = s.same_as && byId.get(s.same_as);
    if (g) { g.shows++; g.duration += secs(s.wall_start, s.wall_end); }
  }
  return [...byId.values()];
}

function renderFramesBar() {
  const bar = $('#framesBar');
  const live = isLive();
  const stats = st.frames.stats;
  const groups = frameGroups();
  const n = groups.length;
  const used = groups.filter(g => !isHidden('frame', g.scene.id)).length;
  let html = '';
  if (live) {
    const on = st.recorder.auto_frames;
    html = `<div class="fb-stats">${on ? '<span class="auto-dot live"></span>' : ''}${stats
      ? `<b>${stats.frames}</b> frames → <b>${stats.scenes}</b> scenes${stats.revisits ? ` · ${stats.revisits} repeats` : ''}${stats.l1 && stats.l1.faces ? ` · ${stats.l1.faces} faces skipped` : ''}${stats.l1 && !stats.l1.ready ? ' · smart filter loading' : ''} · <span class="fb-state">${esc(stats.state)}</span>`
      : on ? 'Watching the screen…' : 'Auto frames are off'}</div>
      ${st.recorder.state === 'listening' ? `<button class="ghost-btn small" id="fbAuto">${on ? 'Stop' : 'Start'} auto frames</button>` : ''}`;
  } else if (n) {
    const lastN = (st.frames.selection && st.frames.selection.n) || 10;
    html = `<div class="fb-stats" title="Visible frames go to Beautify as screenshots, at their place in the transcript"><b>${used}</b> of ${n} frames go to Beautify</div>
      <div class="fb-actions"><label class="fb-n">Best <input class="field num" id="fbN" type="number" min="1" max="100" value="${lastN}"></label>
      <button class="ghost-btn small" id="fbPick" ${st.frames.busy ? 'disabled' : ''} title="Keep the N frames with the most said about them (pointing phrases, time on screen) and hide the rest. The eye brings any frame back.">${ICON.sparkle}Pick</button></div>`;
  }
  bar.innerHTML = html;
  bar.classList.toggle('hidden', !html);
  const a = $('#fbAuto'); if (a) a.onclick = toggleAutoFrames;
  const p = $('#fbPick'); if (p) p.onclick = pickFrames;
}

function frameCardHtml(g) {
  const s = g.scene;
  const url = s.last_url || s.url;
  const tags = [];
  if (g.shows > 1) tags.push(`<span class="ftag" title="Shown ${g.shows} times">×${g.shows}</span>`);
  if (s.last_path) tags.push('<span class="ftag" title="Changed while on screen; the card shows the last state">updated</span>');
  if (s.scroll_of) tags.push(`<span class="ftag" title="Looks like a scrolled #${s.scroll_of}">scroll</span>`);
  if (s.described) tags.push('<span class="ftag" title="Described by the vision model: Beautify uses it instead of the OCR text">described</span>');
  else if (s.ocr_ready) tags.push('<span class="ftag" title="The text on it is recognized (OCR)">OCR</span>');
  if (s.cls) tags.push(`<span class="ftag" title="Level 1 (CLIP) guess, ${Math.round((s.cls_score || 0) * 100)}%">${esc(s.cls)}</span>`);
  if (s.features && s.features.deixis) tags.push(`<span class="ftag hot" title="${esc((s.deixis || []).join(' · '))}">“${s.features.deixis} pointer${s.features.deixis > 1 ? 's' : ''}”</span>`);
  return `<div class="frame-card${s.selected ? ' picked' : ''}${hidCls('frame', s.id)}" data-id="${s.id}" ${visAttr('frame', s.id)} title="Show in the transcript">
    <div class="card-tools">${descBtn(s.last_path || s.path, s.described)}${simBtn('frame', s.id)}${eyeBtn('frame', s.id)}</div>
    <div class="thumb" data-fid="${s.id}" title="Open">${url ? `<img src="${esc(url)}" loading="lazy" alt="">` : ''}${s.selected ? '<span class="pick-star" title="Picked">★</span>' : ''}</div>
    <div class="shot-meta"><div class="top"><span class="time">${esc(hms(s.wall_start))}</span><span class="name">#${s.id} · ${fmtSceneDur(g.duration)}${s.score != null ? ` · score ${s.score.toFixed(1)}` : ''}</span></div>
    <div class="ftags">${tags.join('')}</div></div></div>`;
}

function renderFrames() {
  const groups = frameGroups();
  $('#frameCount').textContent = groups.length || '';
  renderFramesBar();
  const box = $('#frames');
  if (!groups.length) {
    box.innerHTML = `<div class="empty"><div class="art">${ICON.camera}</div><h3>No auto frames</h3><p>Turn on <b>Auto frames</b> before or during a recording. The screen is captured every second; only frames that show something new and stay still are kept, talking heads and transitions are skipped.</p></div>`;
    return;
  }
  if (isLive()) groups.reverse();
  box.innerHTML = groups.map(frameCardHtml).join('');
}

async function pickFrames() {
  const n = +($('#fbN') && $('#fbN').value) || 10;
  st.frames.busy = true; renderFramesBar();
  try {
    const d = await api('POST', sessUrl() + '/frames/select', { n });
    st.frames.scenes = d.scenes; st.frames.selection = d.selection;
    st.vis.frames = new Set(d.scenes.filter(s => s.hidden && !s.same_as).map(s => s.id));
  } catch (e) { fail(e); }
  st.frames.busy = false; renderFrames(); refreshVisDom();
}

$('#frames').addEventListener('click', e => {
  const t = e.target.closest('.thumb[data-fid]'); if (t) { openFrameLightbox(+t.dataset.fid); return; }
  const card = e.target.closest('.frame-card'); if (card) jumpToFrame(+card.dataset.id);
});

function openFrameLightbox(id) {
  const s = st.frames.scenes.find(x => x.id === id); if (!s) return;
  const lb = $('#lightbox');
  const f = s.features;
  const info = [
    `Shown ${hms(s.wall_start)}–${hms(s.wall_end)}`,
    s.changed != null ? `${Math.round(s.changed * 100)}% different from the previous scene` : '',
    s.mask_frac ? `${Math.round(s.mask_frac * 100)}% masked as moving (webcam)` : '',
    f ? `${f.words} words spoken · ${f.deixis} pointing phrase${f.deixis === 1 ? '' : 's'}${s.deixis && s.deixis.length ? ': ' + s.deixis.join(' · ') : ''}` : '',
    s.caption || '',
  ].filter(Boolean).join('\n');
  const urls = [s.url, s.last_url].filter(Boolean);
  lb.innerHTML = `<div class="lb-img${urls.length > 1 ? ' two' : ''}">${urls.map(u => `<img src="${esc(u)}" alt="">`).join('')}</div>
    <div class="lb-side"><div class="lb-head"><div><b>${esc(hms(s.wall_start))}</b> <span style="color:#6b7480;font-size:12px">frame #${s.id}${s.last_url ? ' · first and last state' : ''}</span></div>
    <div style="display:flex;gap:6px"><button class="ghost-btn small lb-desc" id="lbDesc">${ICON.sparkle}Describe</button><button class="ghost-btn small" id="lbJump">Find in transcript</button><button class="ghost-btn small" id="lbClose">Close</button></div></div>
    <div class="lb-ocr" id="lbOcr">${esc(info)}</div></div>`;
  lb.classList.remove('hidden');
  const shown = s.last_path || s.path;
  lb.dataset.path = shown;
  lb.refreshTexts = async () => { $('#lbOcr').textContent = await imageTextsHtml(s.last_url || s.url, info); };
  lb.refreshTexts();
  lightboxDescribe(shown);
  $('#lbClose').onclick = closeLightbox;
  $('#lbJump').onclick = () => { closeLightbox(); jumpToTime(hms(s.wall_start)); };
  lb.onclick = e => { if (e.target === lb || e.target.classList.contains('lb-img')) closeLightbox(); };
}

// ── Beautify ───────────────────────────────────────────────────────────────
const currentJob = () => (st.project && st.sid) ? st.jobs[key(st.project, st.sid)] : null;
function versions() { return (st.session && st.session.manifest && st.session.manifest.versions) || []; }
function latestVersion() { const d = versions().filter(v => v.status === 'done'); return d[d.length - 1] || null; }
function lastAttempt() { const v = versions(); return v[v.length - 1] || null; }

function renderBeautify() {
  const el = $('#beautifyCtl');
  if (!st.session) { el.innerHTML = ''; $('#versionBadge').textContent = ''; return; }
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
      <span class="s2">${latest.warning ? '⚠ ' : ''}v${latest.v} · ${esc(relTime(latest.finished || latest.created))}${latest.turns ? ` · ${latest.turns} turns` : ''}</span></div>`;
  }
  const canRun = !!(st.ai && st.ai.ready);
  el.innerHTML = `${status}<div class="split"><button class="beautify-btn" id="bRun" title="${canRun ? esc(st.ai.label) : esc((st.ai && st.ai.problem) || 'AI is not set up') + ' - click to set up'}">${ICON.sparkle}Beautify</button><button class="beautify-btn more" id="bMore" title="More options">${ICON.chev}</button></div>`;
  $('#bRun').onclick = () => canRun ? runBeautify(latest ? 'update' : 'full') : openAiSetup();
  $('#bMore').onclick = e => openBeautifyMenu(e.currentTarget);
  const bs = $('#bStatus'); if (bs) bs.onclick = () => switchTab('beautified');
}
const modeLabel = m => ({ update: 'update', full: 'full rebuild', final: 'final' }[m] || m);

function openBeautifyMenu(anchor) {
  const latest = latestVersion();
  const finished = !isLive();
  const m = $('#beautifyMenu');
  // API engine only: a small model can't be trusted with the whole document (it fills in the style reference)
  const layout = st.settings.api_layout || 'model';
  const layoutItem = (v, title, sub) => `<button class="mi" data-layout="${v}"><span class="tick">${layout === v ? '✓' : ''}</span><span class="mi-main"><div class="mi-title">${title}</div><div class="mi-sub">${sub}</div></span></button>`;
  const layouts = st.settings.beautify_engine !== 'api' ? '' : '<div class="sep"></div><div class="group">Layout</div>'
    + layoutItem('model', 'Model writes the whole document', 'Header, styles and footer too · for strong models')
    + layoutItem('app', 'App builds the frame', 'The model writes only the conversation · for small local models')
    + '<div class="sep"></div>';
  m.innerHTML = `
    <button class="mi" data-mode="update" ${latest ? '' : 'disabled style="opacity:.5"'}><span class="mi-main"><div class="mi-title">Update with new material</div><div class="mi-sub">Extends v${latest ? latest.v : '-'} with new speech and screenshots · fastest</div></span></button>
    <button class="mi" data-mode="full"><span class="mi-main"><div class="mi-title">Full rebuild</div><div class="mi-sub">Regenerates the whole document from the transcript</div></span></button>
    <button class="mi" data-mode="final" ${finished ? '' : 'disabled style="opacity:.5"'}><span class="mi-main"><div class="mi-title">Final version</div><div class="mi-sub">${finished ? 'Definitive document with every speaker identified; also saved to docs/' : 'Available after the recording stops (runs automatically)'}</div></span></button>
    ${layouts}<button class="mi" data-prompts="1"><span class="mi-main"><div class="mi-title">Edit prompt(s)…</div><div class="mi-sub">What Beautify asks the model to do, for this project only</div></span></button>
    <button class="mi mi-ai" data-ai="1"><span class="mi-main"><div class="mi-title">AI: ${esc(st.ai ? st.ai.label : '-')}</div><div class="mi-sub">Change the provider (Claude Code, Gemini, OpenAI, …), API key, model</div></span></button>`;
  m.onclick = e => {
    if (e.target.closest('.mi[data-ai]')) { closeMenus(); openAiSetup(); return; }
    const lb = e.target.closest('.mi[data-layout]');
    if (lb) {
      api('POST', '/api/settings', { api_layout: lb.dataset.layout }).then(s => {
        st.settings = s;
        $$('.mi[data-layout]', m).forEach(x => { $('.tick', x).textContent = x.dataset.layout === s.api_layout ? '✓' : ''; });
      }).catch(fail);
      return;
    }
    if (e.target.closest('.mi[data-prompts]')) { closeMenus(); window.open(`/prompts?project=${encodeURIComponent(st.project)}`, 'ushi-prompts'); return; }
    const b = e.target.closest('.mi[data-mode]'); if (!b || b.disabled) return;
    closeMenus();
    if (!(st.ai && st.ai.ready)) { openAiSetup(); return; }   // the menu opens without AI too: Edit prompt(s)
    runBeautify(b.dataset.mode);
  };
  openMenu(m, anchor, 'right');
}

// After a recording: ask before starting the (long) final Beautify run.
function offerBeautify(project, sid) {
  if (st.jobs[key(project, sid)] && st.jobs[key(project, sid)].status === 'running') return;
  openModal(`<div class="modal-head"><h2>Recording finished</h2><p>Build the final beautified version of <b>${esc(sidLabel(sid))}</b> now?
    Hidden lines, screenshots and frames are left out. You can always start it later with <b>Beautify</b>.</p></div>
    <div class="modal-foot"><button class="ghost-btn" data-close>Not now</button><button class="primary-btn" id="offerGo">${ICON.sparkle}Build final version</button></div>`,
  root => {
    $('[data-close]', root).onclick = closeModal;
    $('#offerGo', root).onclick = async () => {
      closeModal();
      try {
        const job = await api('POST', sessUrl(project, sid) + '/beautify', { mode: 'final' });
        st.jobs[key(job.project, job.sid)] = job;
        renderBeautify(); renderDoc(); renderShots();
      } catch (e) { fail(e); }
    };
  });
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
      <div class="jc-sub">${job.lines || 0} lines · ${job.shots || 0} screenshots${job.frames ? ` · ${job.frames} frames (added after)` : ''} · <span id="jcChars">${(job.chars / 1024).toFixed(0)} KB generated</span>${job.log_url ? ` · <a href="${esc(job.log_url)}" target="_blank" title="What the model is doing, with timings (refresh to follow)">Log</a>` : ''}</div></div>
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
  // Every run is kept: done ones can be opened, failed / cancelled ones are listed for their log.
  const all = versions().filter(v => v.status !== 'running');
  const lastFailed = all.length && all[all.length - 1].status !== 'done' ? all[all.length - 1] : null;
  tb.innerHTML = `
    <label class="ver-label" for="verSel" title="Every Beautify run is kept">History</label>
    <select class="field" id="verSel">${[...all].reverse().map(v => `<option value="${v.v}" ${v.v === sel.v ? 'selected' : ''} ${v.status === 'done' ? '' : 'disabled'}>v${v.v} · ${esc(v.mode === 'final' ? 'Final' : modeLabel(v.mode))} · ${esc(relTime(v.finished || v.created))}${v.status === 'done' ? (v.duration ? ` · ${fmtDur(v.duration)}` : '') : ` · ${esc(v.status)}`}</option>`).join('')}</select>
    <div class="doc-meta">
      ${sel.turns ? `<span><b>${sel.turns}</b> turns</span>` : ''}${sel.callouts ? `<span><b>${sel.callouts}</b> callouts</span>` : ''}
      <span><b>${(sel.referenced || []).length}/${(sel.screenshots || []).length}</b> screenshots</span>
      ${sel.doc_t_first ? `<span>covers <b>${esc(sel.doc_t_first)}–${esc(sel.doc_t_last)}</b> of ${esc(sel.t_first)}–${esc(sel.t_last)}</span>` : ''}
      ${sel.warning ? `<span class="doc-warn" title="${esc(sel.warning)} - see the Log">⚠ incomplete</span>` : ''}
      ${sel.duration ? `<span>${fmtDur(sel.duration)} to generate</span>` : ''}${sel.cost ? `<span>$${(+sel.cost).toFixed(2)}</span>` : ''}
    </div>
    <div class="spacer"></div>
    ${lastFailed && lastFailed.log_url ? `<a class="ghost-btn small" href="${esc(lastFailed.log_url)}" target="_blank" title="Log of the last run (v${lastFailed.v}, ${esc(lastFailed.status)})">v${lastFailed.v} log</a>` : ''}
    ${sel.prompt_file_url ? `<a class="ghost-btn small" href="${esc(sel.prompt_file_url)}" target="_blank" title="The exact prompt sent for v${sel.v}">Prompt</a>` : ''}
    ${sel.log_url ? `<a class="ghost-btn small" href="${esc(sel.log_url)}" target="_blank" title="What happened during v${sel.v}: every model call and tool call with timings">Log</a>` : ''}
    ${sel.docs_url ? `<a class="ghost-btn small" href="${esc(sel.docs_url)}" target="_blank" title="Copy saved in the project's docs/ folder">${ICON.doc}docs/ copy</a>` : ''}
    <button class="ghost-btn small" id="docEmbed" title="Save this version as one HTML file with the images embedded (vN-emb.html), to share without the image folders">${ICON.doc}Embed images</button>
    <button class="ghost-btn small" id="docReveal" title="Show this version's HTML file in its folder">${ICON.open}Open</button>`;
  $('#verSel').onchange = e => { st.docVersion = +e.target.value; renderDoc(); };
  $('#docEmbed').onclick = async e => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const d = await api('POST', `${sessUrl()}/versions/${sel.v}/embed`);
      toast(`Saved ${d.name} (${(d.size / 1e6).toFixed(1)} MB)`, 'ok');
      await api('POST', '/api/reveal', { url: d.url });
    } catch (err) { fail(err); } finally { btn.disabled = false; }
  };
  $('#docReveal').onclick = async () => {
    try { await api('POST', '/api/reveal', { url: sel.url }); } catch (e) { fail(e); }
  };
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
  if (!(st.ai && st.ai.ready)) { toast(`Chat needs AI set up: ${(st.ai && st.ai.problem) || ''}`, 'error', 6000, { label: 'Set up', fn: openAiSetup }); return; }
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
  return ['Window', cfg.title ? `${cfg.owner} - ${cfg.title}` : cfg.owner];
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
    <div class="preview" id="pvBox">${st.capture && st.capture.preview
      ? `<div class="pv-stage" id="pvStage"><img id="pvImg" src="${esc(st.capture.preview)}" alt="Capture preview">${cfg.frames_crop ? cropBoxHtml(cfg.frames_crop) : ''}</div>`
      : '<span class="state-sub">No preview yet</span>'}<span class="pv-label">${esc(label)}</span></div>
    <div class="pop-row"><button class="ghost-btn small" id="winListBtn">Choose from window list</button><button class="ghost-btn small" id="pvRefresh">Refresh preview</button></div>
    <div class="crop-row">${cfg.frames_crop
      ? `<span><b>Auto frames:</b> the framed part only <span class="state-sub" id="cropInfo">(${cropInfo(cfg)}) · drag to move, edges to resize</span></span><span class="crop-btns"><button class="ghost-btn small" id="detectBtn">Detect again</button><button class="ghost-btn small" id="cropReset">Use whole area</button></span>`
      : `<span class="state-sub">Auto frames use the whole area. If the video is only part of it (a browser or call window), detect it so slide changes aren't drowned out by the page.</span><span class="crop-btns"><button class="ghost-btn small" id="cropHand">Set by hand</button><button class="ghost-btn small" id="detectBtn">Detect video</button></span>`}</div>
    <div class="win-list hidden" id="winList"></div>`;
  $('#detectBtn').onclick = detectVideo;
  const cr = $('#cropReset'); if (cr) cr.onclick = () => saveCrop(null);
  const ch = $('#cropHand'); if (ch) ch.onclick = () => saveCrop({ x: 0.15, y: 0.15, w: 0.7, h: 0.7 });
  bindCropEditor();
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
// ── Auto-frames crop editor (on the capture preview) ───────────────────────
const cropInfo = cfg => `${Math.round(cfg.frames_crop.w * 100)}% × ${Math.round(cfg.frames_crop.h * 100)}%${cfg.frames_crop_method ? ', ' + esc(cfg.frames_crop_method) : ''}`;
const CROP_HANDLES = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'];
function cropBoxHtml(c) {
  return `<div class="crop-box" id="cropBox" style="left:${c.x * 100}%;top:${c.y * 100}%;width:${c.w * 100}%;height:${c.h * 100}%" title="Drag to move · drag an edge or corner to resize">
    ${CROP_HANDLES.map(h => `<span class="ch ch-${h}" data-h="${h}"></span>`).join('')}</div>`;
}

async function saveCrop(crop) {
  try {
    st.capture = await api('POST', '/api/capture/crop', crop ? { crop } : {});
    if (crop && crop.w === 0.7 && crop.h === 0.7) toast('Drag the frame onto the video', 'ok');
  } catch (e) { fail(e); }
  renderCapturePop();
}

// Size the stage to the image as displayed (object-fit: contain), so the crop's percentages
// line up with the picture; then let the frame be dragged and resized.
function bindCropEditor() {
  const img = $('#pvImg'), stage = $('#pvStage'), box = $('#pvBox');
  if (!img || !stage) return;
  const fit = () => {
    if (!img.naturalWidth) return;
    const s = Math.min(box.clientWidth / img.naturalWidth, box.clientHeight / img.naturalHeight);
    stage.style.width = `${img.naturalWidth * s}px`;
    stage.style.height = `${img.naturalHeight * s}px`;
  };
  if (img.complete) fit(); else img.onload = fit;
  const cb = $('#cropBox'); if (!cb) return;
  cb.addEventListener('pointerdown', e => {
    e.preventDefault(); e.stopPropagation();
    const mode = e.target.dataset.h || 'move';
    const r = stage.getBoundingClientRect();
    const start = { ...st.capture.config.frames_crop };
    const px = e.clientX, py = e.clientY;
    cb.setPointerCapture(e.pointerId);
    cb.classList.add('dragging');
    let cur = start;
    const MIN = 0.05;
    const move = ev => {
      const dx = (ev.clientX - px) / r.width, dy = (ev.clientY - py) / r.height;
      let { x, y, w, h } = start;
      if (mode === 'move') {
        x = Math.min(Math.max(0, x + dx), 1 - w); y = Math.min(Math.max(0, y + dy), 1 - h);
      } else {
        if (mode.includes('w')) { const nx = Math.min(Math.max(0, x + dx), x + w - MIN); w += x - nx; x = nx; }
        if (mode.includes('e')) w = Math.min(Math.max(MIN, w + dx), 1 - x);
        if (mode.includes('n')) { const ny = Math.min(Math.max(0, y + dy), y + h - MIN); h += y - ny; y = ny; }
        if (mode.includes('s')) h = Math.min(Math.max(MIN, h + dy), 1 - y);
      }
      cur = { x, y, w, h };
      Object.assign(cb.style, { left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` });
      const info = $('#cropInfo'); if (info) info.textContent = `(${Math.round(w * 100)}% × ${Math.round(h * 100)}%, manual) · drag to move, edges to resize`;
    };
    const up = () => {
      cb.removeEventListener('pointermove', move);
      cb.removeEventListener('pointerup', up);
      cb.removeEventListener('pointercancel', up);
      cb.classList.remove('dragging');
      if (cur !== start) saveCrop(cur);
    };
    cb.addEventListener('pointermove', move);
    cb.addEventListener('pointerup', up);
    cb.addEventListener('pointercancel', up);
  });
}

async function detectVideo() {
  renderCapturePop('Watching the capture area for ~6 s · keep the video playing');
  try {
    st.capture = await api('POST', '/api/capture/detect-video');
    toast('Auto frames will keep only the video area', 'ok');
  } catch (e) { fail(e); }
  renderCapturePop();
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
  let aiInfo = null;
  try { aiInfo = await api('GET', '/api/ai'); } catch (_) { /* old server: no AI choice */ }
  let vis = null;
  try { vis = await api('GET', '/api/ai/vision'); } catch (_) { /* old server */ }
  const picker = aiInfo && aiPicker(aiInfo);
  const modelOpts = (cur) => ['opus', 'sonnet', 'haiku'].map(m => `<option value="${m}" ${cur === m ? 'selected' : ''}>${m[0].toUpperCase() + m.slice(1)}</option>`).join('')
    + (cur && !['opus', 'sonnet', 'haiku'].includes(cur) ? `<option value="${esc(cur)}" selected>${esc(cur)}</option>` : '');
  openModal(`
    <div class="modal-head"><h2>Settings</h2></div>
    <div class="modal-body">
      <fieldset class="fset"><legend>AI for Beautify and chat</legend>
        ${picker ? picker.html : ''}
        <div class="ai-claude">
        <div class="frow"><label>Beautify model</label><select class="field" id="setBModel">${modelOpts(s.beautify_model)}</select></div>
        <div class="frow"><label>Chat model</label><select class="field" id="setCModel">${modelOpts(s.chat_model)}</select></div>
        </div>
        <div class="frow"><label>Final version</label><label class="switch" style="padding-top:5px"><input type="checkbox" id="setAuto" ${s.auto_final_beautify ? 'checked' : ''}><span class="knob"></span>Offer to build the final version when recording stops</label></div>
      </fieldset>
      ${vis ? `<fieldset class="fset"><legend>Screen descriptions</legend>
        <div class="frow"><label>Vision model</label><div><div style="display:flex;gap:8px">
          <select class="field" id="visProv" style="width:auto"><option value="">Auto</option>${Object.entries(vis.providers).map(([id, p]) => `<option value="${id}" ${vis.provider === id ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select>
          <input class="field" id="visModel" value="${esc(vis.model)}"></div>
          <div class="help" id="visHelp"></div></div></div>
        <div class="frow" id="visKeyRow"><label>API key</label><input class="field" id="visKey" type="password" autocomplete="off"></div>
      </fieldset>` : ''}
      ${st.project ? `<fieldset class="fset"><legend>Project · ${esc(st.project)}</legend>
        <div class="frow"><label>Knowledge folder</label><input class="field" id="setCtx" value="${esc(ps.context_dir)}" placeholder="/Users/you/Documents/CLIENT">
          <div class="help">Claude Code runs in this folder, so the MCP servers configured there (e.g. <b>claude-context</b>) power the NEW / CONFIRMS / UPDATES callouts of Update runs.</div></div>
        <div class="frow"><label>Style reference</label><input class="field" id="setStyle" value="${esc(ps.style_reference)}" placeholder="Built-in annotated-transcript style">
          <div class="help">An HTML document whose look to copy (style only, never its content).</div></div>
        <div class="frow"><label>Notes</label><div class="help" style="grid-column:2;margin:6px 0 0">Context and corrections live in the <b>Notes</b> tab (<code>notes.md</code> in the project folder).</div></div>
      </fieldset>` : ''}
      <fieldset class="fset"><legend>Recognizer</legend>
        <div class="frow" ${st.bundledPython ? 'hidden' : ''}><label>Python</label><div style="display:flex;gap:8px"><input class="field" id="setPy" value="${esc(s.recorder_python)}" placeholder="Same as the server"><button class="ghost-btn small" id="checkPy" style="height:30px">Check</button></div>
          <div class="help">The interpreter that has faster-whisper, torch and easyocr installed. <span id="checkRes" class="check-res"></span></div></div>
        <div class="frow"><label>Device</label><select class="field" id="setDevice" style="width:auto">
          ${[['auto', 'Auto (GPU when available)'], ['cuda', 'GPU (CUDA)'], ['cpu', 'CPU']].map(([v, l]) => `<option value="${v}" ${(s.device || 'auto') === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <div class="help">Where Whisper and the speaker model run. An NVIDIA GPU makes Whisper several times faster; it falls back to the CPU if CUDA doesn't load.</div></div>
        <div class="frow"><label>Auto frames</label><label class="switch" style="padding-top:5px"><input type="checkbox" id="setL1" ${s.frames_l1 !== false ? 'checked' : ''}><span class="knob"></span>Smart filter (CLIP): drop webcam faces, merge look-alike frames</label>
          <div class="help">Applies to the next recording. Off = pixel comparison only.</div></div>
      </fieldset>
    </div>
    <div class="modal-foot"><button class="ghost-btn" data-close>Cancel</button><button class="primary-btn" id="saveSettings">Save</button></div>`,
  root => {
    $('[data-close]', root).onclick = closeModal;
    const ai = picker && picker.mount(root);
    // Claude Code model choices only make sense with Claude Code.
    const syncClaude = () => $('.ai-claude', root).classList.toggle('hidden', !!ai && ai.form().engine !== 'claude');
    $$('.ai-card', root).forEach(c => c.addEventListener('click', syncClaude));
    syncClaude();
    const ai0 = ai && JSON.stringify(ai.form());
    // Describe's vision model: Auto = the Beautify provider if it sees images, else one with a key.
    const visForm = () => vis && { provider: $('#visProv', root).value, model: $('#visModel', root).value.trim(), api_key: $('#visKey', root).value.trim() };
    const vis0 = vis && JSON.stringify(visForm());
    const syncVis = () => {
      if (!vis) return;
      const id = $('#visProv', root).value, p = vis.providers[id];
      $('#visModel', root).placeholder = p ? p.model : (vis.effective.model || 'model');
      $('#visKeyRow', root).classList.toggle('hidden', !p);
      $('#visKey', root).placeholder = p && p.key ? `Saved: ${p.key} - type to replace` : p && p.env_key ? 'Using the key from the environment' : 'Paste your API key';
      $('#visHelp', root).textContent = p ? 'The Describe button next to each screenshot and auto frame sends the image to this model. The same key is used if you pick this provider for Beautify.'
        : vis.problem ? `Auto: ${vis.problem}` : `Auto: now ${vis.providers[vis.effective.provider] ? vis.providers[vis.effective.provider].name : vis.effective.provider} · ${vis.effective.model}. Describe sends the image to it.`;
    };
    if (vis) { $('#visProv', root).onchange = syncVis; syncVis(); }
    const aiChanged = () => !!ai && JSON.stringify(ai.form()) !== ai0;
    $('#checkPy', root).onclick = async () => {
      await api('POST', '/api/settings', { recorder_python: $('#setPy').value.trim() });
      const r = await api('GET', `/api/check-recorder?engine=${encodeURIComponent(st.settings.engine || 'whisper')}`);
      const el = $('#checkRes'); el.className = 'check-res ' + (r.ok ? 'ok' : 'bad');
      el.textContent = r.ok ? `✓ ${r.python} is ready` : `✗ ${r.error}`;
    };
    $('#saveSettings', root).onclick = async () => {
      const why = aiChanged() && ai.missing();
      if (why) { toast(why, 'error'); return; }
      try {
        if (aiChanged()) await api('POST', '/api/ai', ai.form());
        if (vis && JSON.stringify(visForm()) !== vis0) await api('POST', '/api/ai/vision', visForm());
        st.settings = await api('POST', '/api/settings', {
          beautify_model: $('#setBModel').value, chat_model: $('#setCModel').value,
          auto_final_beautify: $('#setAuto').checked, recorder_python: $('#setPy').value.trim(),
          frames_l1: $('#setL1').checked, device: $('#setDevice').value,
        });
        if (st.project) await api('POST', `/api/projects/${encodeURIComponent(st.project)}/settings`, {
          context_dir: $('#setCtx').value.trim(), style_reference: $('#setStyle').value.trim(),
        });
        closeModal(); toast('Settings saved', 'ok'); await refreshAi();
      } catch (e) { fail(e); }
    };
  });
}
$('#settingsBtn').onclick = openSettings;

// ── AI provider (Beautify + chat): Claude Code or an LLM API ────────────────
// The AI picker (cards + key / host / model), shared by the first-run dialog and Settings.
function aiPicker(a) {
  const P = a.providers;
  const sel = { engine: a.engine === 'claude' && !a.claude ? 'api' : a.engine, provider: a.provider };
  const card = (id, title, sub, disabled) => `<button type="button" class="ai-card ${disabled ? 'off' : ''}" data-id="${id}" ${disabled ? 'disabled' : ''}><b>${esc(title)}</b><span>${sub}</span></button>`;
  const html = `
      <div class="ai-cards">
        ${card('claude', 'Claude Code', a.claude ? 'Installed · best results: sees screenshots, uses your MCP servers' : 'Not installed on this computer (<a href="https://claude.com/claude-code" target="_blank">get it</a>)', !a.claude)}
        ${Object.entries(P).map(([id, p]) => card(id, p.name, p.key ? `Key saved (${esc(p.key)})` : p.env_key ? 'Key from environment' : id === 'custom' ? 'vLLM, llama.cpp, any OpenAI-style API' : p.local ? 'Runs on this computer · no key' : 'Needs an API key', false)).join('')}
      </div>
      <div class="ai-claude"><button type="button" class="ghost-btn small" id="aiClaudeTest">Test Claude Code</button>
        <span id="aiClaudeRes" class="check-res"></span></div>
      <div class="ai-api">
        <div class="frow"><label>API key</label><div><input class="field" id="aiKey" type="password" autocomplete="off">
          <div class="help" id="aiKeyHelp"></div></div></div>
        <div class="frow"><label>Host (base URL)</label><div><input class="field" id="aiBase">
          <div class="help">OpenAI-compatible endpoint. Leave empty for the provider's default. Local servers: /v1 is added when there is no path.</div></div></div>
        <div class="frow"><label>Model</label><div><div style="display:flex;gap:8px"><input class="field" id="aiModel" list="aiModels"><datalist id="aiModels"></datalist>
          <button type="button" class="ghost-btn small" id="aiTest" style="height:30px;white-space:nowrap">Test</button></div>
          <div class="help"><span id="aiTestRes" class="check-res"></span></div></div></div>
        <div class="help ai-note">With an API, Beautify is one pass over the transcript and the screenshots' text (no images, no tools); chat sees the transcript and your notes.</div>
      </div>`;
  const mount = root => {
    const show = () => {
      $$('.ai-card', root).forEach(c => c.classList.toggle('on', sel.engine === 'claude' ? c.dataset.id === 'claude' : c.dataset.id === sel.provider));
      const isApi = sel.engine === 'api';
      $('.ai-api', root).classList.toggle('hidden', !isApi);
      $('.ai-claude', root).classList.toggle('hidden', isApi || !a.claude);
      if (!isApi) return;
      const p = P[sel.provider];
      const same = sel.provider === a.provider;
      $('#aiKey', root).value = '';
      $('#aiKey', root).placeholder = p.key ? `Saved: ${p.key} - type to replace` : p.env_key ? 'Using the key from the environment' : p.local ? 'Optional for local servers' : 'Paste your API key';
      $('#aiKeyHelp', root).innerHTML = p.key_url ? `Get one at <a href="${p.key_url}" target="_blank">${esc(p.key_url.replace('https://', ''))}</a>. Stored on this computer only.` : 'Stored on this computer only.';
      $('#aiBase', root).value = same ? a.base_url : '';
      $('#aiBase', root).placeholder = p.base_url || 'http://localhost:11434/v1';
      $('#aiModel', root).value = same ? a.model : '';
      $('#aiModel', root).placeholder = p.model || 'model name';
      $('#aiModels', root).innerHTML = '';
      $('#aiTestRes', root).textContent = '';
      if (p.local && p.base_url && !same) $('#aiTest', root).click();   // list the installed models
    };
    const form = () => ({ engine: sel.engine, provider: sel.provider, api_key: $('#aiKey', root).value.trim(),
      base_url: $('#aiBase', root).value.trim(), model: $('#aiModel', root).value.trim() });
    $$('.ai-card', root).forEach(c => c.onclick = () => {
      if (c.dataset.id === 'claude') sel.engine = 'claude'; else { sel.engine = 'api'; sel.provider = c.dataset.id; }
      show();
    });
    $('#aiClaudeTest', root).onclick = async () => {
      const el = $('#aiClaudeRes', root);
      el.className = 'check-res'; el.textContent = 'Asking Claude…';
      try {
        const r = await api('POST', '/api/ai/test-claude');
        el.className = 'check-res ' + (r.ok ? 'ok' : 'bad');
        el.textContent = r.ok ? `✓ Works (answered “${r.reply}”)` : `✗ ${r.error}`;
        el.title = r.path || '';
      } catch (e) { el.className = 'check-res bad'; el.textContent = `✗ ${e.message}`; }
    };
    $('#aiTest', root).onclick = async () => {
      const el = $('#aiTestRes', root);
      el.className = 'check-res'; el.textContent = 'Checking…';
      try {
        const f = form();
        const r = await api('POST', '/api/ai/test', { ...f, model: f.model || P[sel.provider].model });
        if (r.models && r.models.length) $('#aiModels', root).innerHTML = r.models.map(m => `<option value="${esc(m)}">`).join('');
        el.className = 'check-res ' + (r.ok ? 'ok' : 'bad');
        el.textContent = r.ok ? `✓ Works${r.reply ? ` (answered “${r.reply}”)` : ''}${r.models && r.models.length ? ` · ${r.models.length} models available` : ''}` : `✗ ${r.error}`;
      } catch (e) { el.className = 'check-res bad'; el.textContent = `✗ ${e.message}`; }
    };
    // '' when the choice can be saved, else what is missing (and focus goes there).
    const missing = () => {
      const f = form();
      if (f.engine !== 'api') return '';
      const p = P[f.provider];
      if (!f.api_key && !p.key && !p.env_key && !p.local) { $('#aiKey', root).focus(); return 'Enter the API key'; }
      if (!f.base_url && !p.base_url) { $('#aiBase', root).focus(); return 'Enter the host (base URL)'; }
      if (!f.model && !p.model) { $('#aiModel', root).focus(); return 'Enter the model name (Test lists the available ones)'; }
      return '';
    };
    show();
    return { form, missing };
  };
  return { html, mount };
}

async function openAiSetup({ firstRun = false } = {}) {
  let a;
  try { a = await api('GET', '/api/ai'); } catch (e) { fail(e); return; }
  const picker = aiPicker(a);
  openModal(`
    <div class="modal-head"><h2>${firstRun ? 'Welcome to ushi' : 'AI for Beautify and chat'}</h2>
      <p>${firstRun ? 'Recording and transcription work fully offline. To turn transcripts into annotated documents (Beautify) and to chat about them, choose an AI. You can change it later in Settings.' : 'Beautify and chat use this AI. Recording does not need it.'}</p></div>
    <div class="modal-body">${picker.html}</div>
    <div class="modal-foot">${firstRun ? '<button class="ghost-btn" id="aiLater">Later</button>' : '<button class="ghost-btn" data-close>Cancel</button>'}<button class="primary-btn" id="aiSave">Save</button></div>`,
  root => {
    const ai = picker.mount(root);
    const close = $('[data-close]', root); if (close) close.onclick = closeModal;
    const later = $('#aiLater', root);
    if (later) later.onclick = async () => {
      // Remember the answer, so the dialog doesn't come back on every start.
      try { st.settings = await api('POST', '/api/settings', { ai_setup_done: true }); } catch (_) { /* */ }
      closeModal(); refreshAi();
    };
    $('#aiSave', root).onclick = async () => {
      const why = ai.missing();
      if (why) { toast(why, 'error'); return; }
      try {
        const r = await api('POST', '/api/ai', ai.form());
        closeModal();
        await refreshAi();
        toast(r.ready ? `AI: ${r.label}` : `Saved, but: ${r.problem}`, r.ready ? 'ok' : 'error');
      } catch (e) { fail(e); }
    };
  });
}
async function refreshAi() {
  try {
    const s = await api('GET', '/api/status');
    st.settings = s.settings; st.ai = s.ai; st.claude = s.claude;
  } catch (_) { /* */ }
  renderBeautify();
}

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
  $('#side-frames').classList.toggle('hidden', side !== 'frames');
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
  renderControl(); renderTranscript(); renderShots(); renderFrames(); renderBeautify(); renderDoc(); renderChat(); renderSessionMenu();
}

// ── Batch queue: how many tasks are still in progress (badge on the Batch button) ──
const batchStates = new Map();
function renderBatchBadge() {
  const n = [...batchStates.values()].filter(s => s !== 'done').length;
  const held = [...batchStates.values()].some(s => s === 'hold');
  const b = $('#batchBadge');
  b.textContent = n; b.classList.toggle('hidden', !n);
  b.title = held ? 'Some tasks are on hold' : `${n} task(s) in progress`;
  b.style.background = held ? 'var(--warn-soft)' : ''; b.style.color = held ? 'var(--warn)' : '';
}
async function loadBatchCount() {
  try {
    const d = await api('GET', '/api/batch');
    batchStates.clear();
    d.tasks.forEach(t => batchStates.set(t.id, t.state));
    renderBatchBadge();
  } catch (_) { /* older server */ }
}

// ── WebSocket ──────────────────────────────────────────────────────────────
let ws, wsRetry = 0;
function connect() {
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onopen = () => { wsRetry = 0; $('#connDot').className = 'conn-dot on'; };
  ws.onclose = () => {
    $('#connDot').className = 'conn-dot off'; $('#lastLog').textContent = 'Disconnected from server - retrying…';
    setTimeout(connect, Math.min(5000, 500 * 2 ** wsRetry++));
  };
  ws.onmessage = e => handle(JSON.parse(e.data));
}

async function handle(m) {
  switch (m.type) {
    case 'batch_task': batchStates.set(m.task.id, m.task.state); renderBatchBadge(); break;
    case 'batch_removed': batchStates.delete(m.id); renderBatchBadge(); break;
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
      if (m.active || m.state === 'error') st.starting = false;
      if (m.state === 'ready' && prev.state !== 'ready') offerReady();
      else if (m.state !== 'ready') closeReadyPrompt();
      // a live session got its id (new, or the continued one) → show it live
      if (m.active && m.sid && (m.sid !== prev.sid || !prev.active || !isLive())) {
        if (st.project !== m.project) { await selectProject(m.project, { keepSession: true }); }
        await loadSessions();
        await selectSession(m.sid);
        switchTab('live');
      }
      renderControl();
      renderGhost();
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
    case 'source_progress': {
      st.recorder.source_progress = { done: m.done, total: m.total, offset: m.offset, pos: m.pos, paused: m.paused };
      mediaSync();
      const chip = $('#srcChip');
      if (chip) chip.outerHTML = sourceChip(st.recorder);
      break;
    }
    case 'entry':
      if (m.project === st.project && m.sid === st.sid) appendEntry(m.idx, m.entry);
      break;
    case 'shot':
      if (m.project === st.project && m.sid === st.sid) upsertShot(m.shot, m.new);
      break;
    case 'log':
      addLog(m.line);
      break;
    case 'frame':
      if (m.project === st.project && m.sid === st.sid) upsertFrame(m.scene);
      break;
    case 'described':
      if (m.project === st.project && m.sid === st.sid) markDescribed(m.path);
      break;
    case 'visibility':
      if (m.project === st.project && m.sid === st.sid) { applyVis(m.visibility); refreshVisDom(); }
      break;
    case 'offer_beautify': offerBeautify(m.project, m.sid); break;
    case 'frame_stats':
      st.recorder.frame_stats = m;
      if (m.project === st.project && m.sid === st.sid) { st.frames.stats = m; renderFramesBar(); }
      { const b = $('#autoBtn'); if (b && st.recorder.auto_frames) b.lastChild.textContent = `Auto · ${m.scenes}`; }
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
    case 'external':
      if (m.project === st.project) { st.external = { files: m.files, speakers: m.speakers }; renderExternal(); }
      break;
    case 'toast': toast(m.text, m.level || 'info'); break;
    case 'download': onDownload(m); break;
  }
}

// ── Notes (projects/<p>/notes.md - single source for context + corrections) ─
const notesUrl = () => `/api/projects/${encodeURIComponent(st.project)}/notes`;
let notesTimer = null;

function countCorrections(text) { return (text.match(/^\s*[-*]?\s*".+?"\s*(→|->|<--)\s*\S/gm) || []).length; }
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

// ── External transcripts (projects/<p>/external/ - e.g. Teams, for speaker names) ─
const extUrl = () => `/api/projects/${encodeURIComponent(st.project)}/external`;

async function loadExternal() {
  st.external = { files: [], speakers: [] };
  renderExternal();
  if (!st.project) return;
  try { st.external = await api('GET', extUrl()); } catch (_) { /* keep empty */ }
  renderExternal();
}

function renderExternal() {
  const list = $('#extList');
  $('#extBox').classList.toggle('hidden', !st.project);
  const files = st.external.files || [];
  if (!files.length) {
    list.innerHTML = `<div class="ext-empty">Drop the Teams transcript of this meeting here (.vtt or .docx). Beautify takes speaker names from it and keeps our spelling of brands and terms.</div>`;
    return;
  }
  list.innerHTML = files.map(f => {
    const on = f.sid == null || f.sid === st.sid;
    const bind = f.sid == null ? 'All sessions' : f.sid === st.sid ? 'This session' : sidLabel(f.sid);
    const tip = f.sid == null ? 'Used for every session in this project. Click to use it for this session only.'
      : f.sid === st.sid ? 'Used when beautifying this session. Click to use it for every session.'
      : 'Belongs to another session. Click to use it for this session.';
    const spk = (f.speakers || []).length;
    return `<div class="ext-item${on ? '' : ' off'}" data-name="${esc(f.name)}">
      <div class="ext-main"><div class="ext-name" title="${esc(f.path)}">${esc(f.original)}</div>
      <div class="ext-meta">${f.lines} lines${spk ? ` · ${spk} speaker${spk > 1 ? 's' : ''}: ${esc(f.speakers.slice(0, 4).join(', '))}${spk > 4 ? '…' : ''}` : ''}</div></div>
      <button class="ext-bind" data-act="bind" title="${esc(tip)}">${esc(bind)}</button>
      <button class="ext-del" data-act="del" title="Remove">×</button></div>`;
  }).join('');
}

async function uploadExternal(fileList) {
  if (!st.project) return;
  for (const file of fileList) {
    try {
      const q = new URLSearchParams({ name: file.name, ...(st.sid ? { sid: st.sid } : {}) });
      const res = await fetch(`${extUrl()}?${q}`, { method: 'PUT', body: file });
      const d = await res.json().catch(() => null);
      if (!res.ok) throw new Error((d && d.detail) || `${res.status} ${res.statusText}`);
      st.external = d;
      renderExternal();
      const rec = d.files[d.files.length - 1];
      toast(`Added ${file.name}${rec && rec.speakers.length ? ` · ${rec.speakers.length} speakers` : ''}. Beautify will use it.`, 'ok');
    } catch (e) { fail(e); }
  }
}

$('#extUploadBtn').addEventListener('click', () => $('#extFile').click());
$('#extFile').addEventListener('change', e => { uploadExternal([...e.target.files]); e.target.value = ''; });
$('#extList').addEventListener('click', async e => {
  const btn = e.target.closest('[data-act]');
  if (!btn) return;
  const item = btn.closest('.ext-item'), name = item.dataset.name;
  const f = st.external.files.find(x => x.name === name);
  try {
    if (btn.dataset.act === 'del') {
      st.external = await api('DELETE', `${extUrl()}/${encodeURIComponent(name)}`);
      toast(`Removed ${f ? f.original : name}`);
    } else {
      const sid = f && f.sid === st.sid ? null : st.sid;
      st.external = await api('PATCH', `${extUrl()}/${encodeURIComponent(name)}`, { sid });
    }
    renderExternal();
  } catch (err) { fail(err); }
});
{
  const zone = $('#side-notes'), box = $('#extBox');
  zone.addEventListener('dragover', e => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); box.classList.add('drag'); } });
  zone.addEventListener('dragleave', e => { if (!zone.contains(e.relatedTarget)) box.classList.remove('drag'); });
  zone.addEventListener('drop', e => {
    if (!e.dataTransfer.files.length) return;
    e.preventDefault(); box.classList.remove('drag');
    uploadExternal([...e.dataTransfer.files]);
  });
}

// ── Correction popover: select transcript text → fix it → goes to Notes ───
function selectionInfo(win, root) {
  const sel = win.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const text = sel.toString().replace(/\s+/g, ' ').trim();
  if (!text || text.length > 400) return null;
  const range = sel.getRangeAt(0);
  if (root && !root.contains(range.commonAncestorContainer)) return null;
  return { text, rect: range.getBoundingClientRect(), time: selectionTime(range) };
}

// Time of the selected line: the transcript row, or the nearest time stamp in a Beautified document.
function selectionTime(range) {
  let n = range.startContainer;
  if (n.nodeType !== 1) n = n.parentElement;
  const row = n && n.closest('[data-time]');
  if (row) return row.dataset.time;
  for (let el = n; el && el.tagName !== 'BODY'; el = el.parentElement) {
    const t = el.querySelector && el.querySelector('.time');
    const m = t && /\d{2}:\d{2}:\d{2}/.exec(t.textContent);
    if (m) return m[0];
  }
  return '';
}

let fixMode = 'fix';
function openFixPop(info, offset = { x: 0, y: 0 }) {
  if (!st.project) return;
  const pop = $('#fixPop');
  const who = fixMode === 'who';
  const names = st.external.speakers || [];
  pop.innerHTML = `
    <div class="pop-title">${who ? 'Who said it' : 'Correct the transcript'}</div>
    <div class="seg" id="fixMode"><button data-m="fix" class="${who ? '' : 'on'}">Fix text</button><button data-m="who" class="${who ? 'on' : ''}">Who said it</button></div>
    <div class="pop-sub" style="margin-bottom:4px">${who
      ? 'Saved to Notes as <code>"quote" &lt;-- Name</code>, so Beautify attributes this line (and that voice) to the person.'
      : 'Saved to Notes as a rule, so Beautify and Ask apply it everywhere, and live recognition uses it as a hint.'}</div>
    ${who ? `
    <label>Quote${info.time ? ` · ${esc(info.time)}` : ''}</label><div class="orig quote">${esc(info.text)}</div>
    <label for="fixWho">Said by</label><input class="field" id="fixWho" list="fixWhoList" placeholder="e.g. Jane Doe" autocomplete="off">
    <datalist id="fixWhoList">${names.map(n => `<option value="${esc(n)}">`).join('')}</datalist>
    <div class="row-btns"><span class="kb"><kbd>↵</kbd> save · <kbd>esc</kbd> close</span><button class="ghost-btn small" id="fixCancel">Cancel</button><button class="primary-btn" id="fixSave" style="height:28px;font-size:12.5px">Add to notes</button></div>` : `
    <label>Heard</label><div class="orig">${esc(info.text)}</div>
    <label for="fixTo">Should be</label><textarea class="field" id="fixTo" rows="2">${esc(info.text)}</textarea>
    <label for="fixWhy">Comment <span style="text-transform:none;letter-spacing:0;font-weight:500">(optional)</span></label>
    <input class="field" id="fixWhy" placeholder="e.g. product name, person's name">
    <div class="row-btns"><span class="kb"><kbd>↵</kbd> save · <kbd>esc</kbd> close</span><button class="ghost-btn small" id="fixCancel">Cancel</button><button class="primary-btn" id="fixSave" style="height:28px;font-size:12.5px">Add to notes</button></div>`}`;
  pop.classList.remove('hidden');
  const r = info.rect, w = pop.offsetWidth, h = pop.offsetHeight;
  let left = r.left + offset.x + r.width / 2 - w / 2;
  let top = r.bottom + offset.y + 10;
  if (top + h > window.innerHeight - 10) top = Math.max(10, r.top + offset.y - h - 10);
  pop.style.left = `${Math.max(10, Math.min(left, window.innerWidth - w - 10))}px`;
  pop.style.top = `${top}px`;
  $('#fixMode').onclick = e => {
    const b = e.target.closest('[data-m]');
    if (b && b.dataset.m !== fixMode) { fixMode = b.dataset.m; openFixPop(info, offset); }
  };
  const field = who ? $('#fixWho') : $('#fixTo');
  field.focus(); field.select();
  const save = async () => {
    try {
      if (who) {
        const speaker = field.value.replace(/\s+/g, ' ').trim();
        if (!speaker) { field.focus(); return; }
        await saveNotesNow();
        const d = await api('POST', notesUrl() + '/speaker', { quote: info.text, speaker, time: info.time || '' });
        applyNotes(d.text, true);
        if (!st.external.speakers.includes(speaker)) st.external.speakers.push(speaker);
        closeFixPop();
        toast(`Added to Notes: “${info.text}” ← ${speaker}`, 'ok');
        return;
      }
      const fixed = field.value.replace(/\s+/g, ' ').trim();
      if (!fixed || fixed === info.text) { closeFixPop(); return; }
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
    st.settings = s.settings; st.recorder = s.recorder; st.claude = s.claude; st.models = s.models;
    st.ai = s.ai; st.bundledPython = s.bundled_python;
    if (s.recorder.pipeline) st.pipeline = s.recorder.pipeline;
    if (!s.helper) toast(s.platform === 'win32' ? 'Screenshots are off - pip install mss pillow'
      : 'screenshot_helper binary is missing - run ./compile.sh', 'error', 8000);
    await loadProjects();
    if (st.project) await selectProject(st.project);
    else renderAll();
    // a link from the Batch page: /?project=…&session=…
    const qs = new URLSearchParams(location.search);
    if (qs.get('session') && qs.get('project') === st.project) await selectSession(qs.get('session')).catch(() => {});
    if (qs.has('project')) history.replaceState(null, '', '/');
    loadBatchCount();
  } catch (e) { fail(e); renderAll(); }
  loadCapture();
  connect();
  if (st.ai && !st.ai.setup_done) openAiSetup({ firstRun: true });   // first start: which AI to use
})();
