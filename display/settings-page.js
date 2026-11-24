// Settings page for a MakeReady printer display.
//
// The board serves this file as its own web page (see web_server in printer-display.yaml).
// It talks to the board's built-in web API: live values arrive on /events, and changes are
// POSTed to /<kind>/<name>/<action>. The names below must match the names in the YAML;
// they are also what the board stores each setting under, so they must not be changed.
const NAME = {
  server: 'SetReady page address (not Watchtower)',
  token: 'Display token',
  link: 'MakeReady connection',
  name: 'Board name on the network',
  nameInUse: 'Network name in use',
  apply: 'Apply and restart',
  backlight: 'Backlight',
  flip: 'Screen: turn upside down',
  ip: 'IP address',
  ssid: 'Wi-Fi network in use',
  scan: 'Wi-Fi networks nearby',
  newSsid: 'Wi-Fi change 1: new network name',
  newPass: 'Wi-Fi change 2: new network password',
  wifiGo: 'Wi-Fi change 3: connect to new network',
  wifiResult: 'Wi-Fi change: result',
  version: 'Firmware version',
  update: 'Firmware update',
  check: 'Firmware update check now',
  auto: 'Install updates automatically',
};

// A small screen with a progress bar: the page's icon and logo.
const ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">' +
  '<rect x="2" y="5" width="28" height="22" rx="4" fill="#1b1e24"/>' +
  '<rect x="5" y="8" width="22" height="16" rx="1.5" fill="#111317"/>' +
  '<rect x="7" y="10.5" width="10" height="2.4" rx="1.2" fill="#eceef2"/>' +
  '<circle cx="23.5" cy="11.7" r="1.7" fill="#35b765"/>' +
  '<rect x="7" y="17.5" width="18" height="4" rx="1" fill="#2c313a"/>' +
  '<rect x="7" y="17.5" width="11" height="4" rx="1" fill="#1c8f45"/></svg>';

const CSS = `
:root { --bg:#f4f5f7; --card:#fff; --text:#16181d; --muted:#667085; --line:#dde1e7;
        --green:#16803c; --red:#c62828; --field:#fff; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#111317; --card:#1b1e24; --text:#eceef2; --muted:#9aa3b2; --line:#2c313a;
          --green:#1c8f45; --red:#ef5350; --field:#111317; } }
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin:0; background:var(--bg); color:var(--text);
       font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
.wrap { max-width:560px; margin:0 auto; padding:20px 16px 40px; }
header { display:flex; align-items:center; gap:12px; margin-bottom:18px; }
header svg { width:44px; height:44px; flex:none; }
h1 { font-size:20px; margin:0; }
.sub { color:var(--muted); font-size:14px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:16px; margin-bottom:14px; }
h2 { font-size:16px; margin:0 0 12px; }
label { display:block; font-size:14px; font-weight:600; margin:0 0 12px; }
label small, .hint { display:block; font-weight:400; color:var(--muted); font-size:13px; margin-top:4px; }
input[type=text], input[type=password], select {
  display:block; width:100%; margin-top:5px; padding:10px; font:inherit; color:var(--text);
  background:var(--field); border:1px solid var(--line); border-radius:8px; }
input[type=range] { width:100%; margin-top:8px; }
.row { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:0 0 12px; font-size:14px; }
.row b { font-weight:600; }
.val { color:var(--muted); text-align:right; overflow-wrap:anywhere; }
.btns { display:flex; flex-wrap:wrap; gap:8px; margin-top:4px; }
button { font:inherit; font-weight:600; font-size:15px; padding:10px 16px; border-radius:8px;
         border:1px solid var(--line); background:transparent; color:var(--text); cursor:pointer; }
button.primary { background:var(--green); border-color:var(--green); color:#fff; }
button:disabled { opacity:.5; cursor:default; }
.note { font-size:14px; margin:10px 0 0; }
.note:empty { display:none; }
.note.bad { color:var(--red); } .note.good { color:var(--green); }
.switch { display:flex; align-items:center; gap:10px; font-weight:600; font-size:14px; cursor:pointer; margin:0 0 12px; }
.switch input { width:20px; height:20px; margin:0; }
.offline { background:var(--red); color:#fff; border-radius:8px; padding:10px 14px; margin-bottom:14px;
           font-size:14px; display:none; }
.offline.show { display:block; }
details { font-size:13px; color:var(--muted); }
details pre { max-height:260px; overflow:auto; background:var(--field); border:1px solid var(--line);
              border-radius:8px; padding:10px; white-space:pre-wrap; overflow-wrap:anywhere; }
`;

const state = {};                       // "<kind>/<name>" -> the latest message for it
const $ = id => document.getElementById(id);
const get = (kind, name) => state[kind + '/' + name] || {};
const dirty = new Set();                // fields the person is typing in: don't overwrite

function post(kind, name, action, params) {
  const q = params ? '?' + new URLSearchParams(params) : '';
  return fetch('/' + kind + '/' + encodeURIComponent(name) + '/' + action + q, { method: 'POST' });
}

function build() {
  document.title = 'Printer display';
  const link = document.querySelector('link[rel=icon]') || document.head.appendChild(document.createElement('link'));
  link.rel = 'icon';
  link.href = 'data:image/svg+xml,' + encodeURIComponent(ICON);
  const meta = document.head.appendChild(document.createElement('meta'));
  meta.name = 'viewport';
  meta.content = 'width=device-width, initial-scale=1';
  document.head.appendChild(document.createElement('style')).textContent = CSS;

  const app = document.querySelector('esp-app') || document.body.appendChild(document.createElement('div'));
  app.innerHTML = `<div class="wrap">
  <header>${ICON}<div><h1>Printer display</h1><div class="sub" id="where">Connecting…</div></div></header>
  <div class="offline" id="offline">Lost contact with the board. Trying again…</div>

  <section class="card">
    <h2>MakeReady connection</h2>
    <label>MakeReady page address
      <input type="text" id="server" placeholder="192.168.1.10:8080" autocomplete="off" autocapitalize="none" spellcheck="false">
      <small>Where the MakeReady page runs, as in your browser's address bar. Not Watchtower's address.</small>
    </label>
    <label>Display token
      <input type="password" id="token" placeholder="Leave blank to keep the current token" autocomplete="off">
      <small>From the MakeReady page: Settings → Printer displays → Create token.</small>
    </label>
    <div class="row" style="margin-top:14px"><b>Status</b><span class="val" id="link">–</span></div>
    <div class="btns"><button class="primary" id="save">Save</button></div>
    <p class="note" id="save-note"></p>
  </section>

  <section class="card">
    <h2>Screen</h2>
    <label>Brightness <input type="range" id="bright" min="10" max="255" step="5"></label>
    <label class="switch"><input type="checkbox" id="flip"> Turn the picture upside down</label>
    <div class="hint">For mounting the board the other way up. Touch follows the picture.</div>
  </section>

  <section class="card">
    <h2>Wi-Fi</h2>
    <div class="row"><b>Connected to</b><span class="val" id="ssid">–</span></div>
    <label>Move to another network
      <select id="net"></select>
      <input type="text" id="net-other" placeholder="Network name" autocomplete="off" autocapitalize="none" spellcheck="false" hidden>
      <small>Networks the board saw when it last started. 2.4 GHz only.</small>
    </label>
    <label>Password <input type="password" id="net-pass" autocomplete="off"></label>
    <div class="btns"><button id="net-go">Connect</button></div>
    <p class="note" id="net-note"></p>
    <label style="margin-top:18px">This board's name on the network
      <input type="text" id="name" maxlength="31" autocomplete="off" autocapitalize="none" spellcheck="false">
      <small id="name-hint">Letters, numbers and hyphens, e.g. p1s-4-display. What your router lists the board as.</small>
    </label>
    <div class="btns"><button id="name-go">Save name and restart</button></div>
    <p class="note" id="name-note"></p>
  </section>

  <section class="card">
    <h2>Firmware</h2>
    <div class="row"><b>Running</b><span class="val" id="version">–</span></div>
    <div class="row"><b>Update</b><span class="val" id="update">Not checked yet</span></div>
    <div class="btns"><button id="check">Check for updates</button><button class="primary" id="install" hidden>Install update</button></div>
    <label class="switch" style="margin-top:14px"><input type="checkbox" id="auto"> Install updates automatically</label>
    <details><summary>Install from a file</summary>
      <form method="POST" action="/update" enctype="multipart/form-data" style="margin-top:10px">
        <input type="file" name="update" accept=".bin" required>
        <div class="btns" style="margin-top:10px"><button type="submit">Upload and install</button></div>
        <div class="hint">Use the .ota.bin file for this board's screen type.</div>
      </form>
    </details>
  </section>

  <details><summary>Log</summary><pre id="log"></pre></details>
</div>`;

  for (const id of ['server', 'token', 'net-pass', 'net-other', 'name']) $(id).addEventListener('input', () => dirty.add(id));

  $('save').addEventListener('click', async () => {
    const note = $('save-note');
    const server = $('server').value.trim(), token = $('token').value.trim();
    if (!server) { note.className = 'note bad'; note.textContent = 'Enter the MakeReady page address.'; return; }
    $('save').disabled = true;
    note.className = 'note'; note.textContent = 'Saving…';
    try {
      await post('text', NAME.server, 'set', { value: server });
      if (token) await post('text', NAME.token, 'set', { value: token });
      // No restart needed: the board uses the new details straight away. The status
      // line above shows how it got on.
      dirty.delete('server'); dirty.delete('token'); $('token').value = '';
      note.className = 'note good'; note.textContent = 'Saved. The board is connecting with the new details; see Status.';
    } catch { note.className = 'note bad'; note.textContent = "Couldn't reach the board."; }
    setTimeout(() => { $('save').disabled = false; }, 1500);
  });

  let brightTimer;
  $('bright').addEventListener('input', () => {
    dirty.add('bright');
    clearTimeout(brightTimer);
    brightTimer = setTimeout(() => post('light', NAME.backlight, 'turn_on', { brightness: $('bright').value }), 150);
  });
  $('bright').addEventListener('change', () => setTimeout(() => dirty.delete('bright'), 1500));
  $('flip').addEventListener('change', () => post('switch', NAME.flip, $('flip').checked ? 'turn_on' : 'turn_off'));
  $('auto').addEventListener('change', () => post('switch', NAME.auto, $('auto').checked ? 'turn_on' : 'turn_off'));

  $('net').addEventListener('change', () => { $('net-other').hidden = $('net').value !== '\u0000other'; });
  $('net-go').addEventListener('click', async () => {
    const note = $('net-note');
    const name = $('net').value === '\u0000other' ? $('net-other').value.trim() : $('net').value;
    if (!name) { note.className = 'note bad'; note.textContent = 'Choose a network.'; return; }
    note.className = 'note'; note.textContent = 'Trying ' + name + '… this page may stop responding if the board moves.';
    try {
      await post('text', NAME.newSsid, 'set', { value: name });
      await post('text', NAME.newPass, 'set', { value: $('net-pass').value });
      await new Promise(r => setTimeout(r, 300));
      await post('button', NAME.wifiGo, 'press');
      dirty.delete('net-pass'); $('net-pass').value = '';
    } catch { note.className = 'note bad'; note.textContent = "Couldn't reach the board."; }
  });

  $('name-go').addEventListener('click', async () => {
    const note = $('name-note');
    const name = $('name').value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 31);
    $('name').value = name;
    $('name-go').disabled = true;
    note.className = 'note'; note.textContent = 'Saving…';
    try {
      await post('text', NAME.name, 'set', { value: name });
      await new Promise(r => setTimeout(r, 600));          // let the board store it
      await post('button', NAME.apply, 'press');
      dirty.delete('name');
      note.className = 'note good';
      note.textContent = name ? 'Saved. The board is restarting as ' + name + '…' : 'Saved. The board is restarting with its built-in name…';
    } catch { note.className = 'note bad'; note.textContent = "Couldn't reach the board."; }
    setTimeout(() => { $('name-go').disabled = false; }, 8000);
  });

  $('check').addEventListener('click', () => {
    $('update').textContent = 'Checking…';
    post('button', NAME.check, 'press');
    // The board says nothing when a check fails, so don't wait for ever.
    setTimeout(() => {
      if ($('update').textContent === 'Checking…')
        $('update').textContent = "Couldn't check. The board may not be able to reach github.com; see the log below.";
    }, 25000);
  });
  $('install').addEventListener('click', () => {
    $('update').textContent = 'Installing… the board restarts when it is done.';
    $('install').disabled = true;
    post('update', NAME.update, 'install');
  });
}

// The board lists nearby networks as lines of "name: -60dB".
function networks(text) {
  const seen = new Map();
  for (const line of String(text || '').split('\n')) {
    const m = line.match(/^(.*): (-?\d+)dB$/);
    if (m && m[1] && (!seen.has(m[1]) || seen.get(m[1]) < +m[2])) seen.set(m[1], +m[2]);
  }
  return [...seen.entries()].sort((a, b) => b[1] - a[1]);
}

let lastScan = null;
function render() {
  const text = (name, id) => {
    const v = get('text', name).value;
    if (v !== undefined && !dirty.has(id) && document.activeElement !== $(id)) $(id).value = v;
  };
  text(NAME.server, 'server');
  const link = get('text_sensor', NAME.link).value;
  $('link').textContent = link || '–';
  $('link').style.color = /^Connected/.test(link || '') ? 'var(--green)' : '';
  text(NAME.name, 'name');
  const inUse = get('text_sensor', NAME.nameInUse).value;
  if (inUse) {
    $('name').placeholder = inUse;
    document.title = inUse;
    $('name-hint').textContent = 'Letters, numbers and hyphens, e.g. p1s-4-display. What your router lists the board as. ' +
      'Now: ' + inUse + ' (also http://' + inUse + '.local). Leave blank for the built-in name.';
  }

  const ip = get('text_sensor', NAME.ip).value, ssid = get('text_sensor', NAME.ssid).value;
  $('where').textContent = [ip, ssid && 'on ' + ssid].filter(Boolean).join(' ') || 'Connected';
  $('ssid').textContent = ssid || '–';
  $('version').textContent = get('text_sensor', NAME.version).value || '–';

  const light = get('light', NAME.backlight);
  if (light.brightness !== undefined && !dirty.has('bright')) $('bright').value = light.brightness;
  $('flip').checked = get('switch', NAME.flip).value === true;
  $('auto').checked = get('switch', NAME.auto).value === true;

  const scan = get('text_sensor', NAME.scan).value;
  if (scan !== lastScan) {                       // rebuild the list only when it changes
    lastScan = scan;
    const keep = $('net').value;
    $('net').replaceChildren(new Option('Choose a network…', ''),
      ...networks(scan).map(([n, db]) => new Option(n + (db > -60 ? '  (strong)' : db > -75 ? '  (ok)' : '  (weak)'), n)),
      new Option('Other network…', '\u0000other'));
    $('net').value = [...$('net').options].some(o => o.value === keep) ? keep : '';
  }
  const result = get('text_sensor', NAME.wifiResult).value;
  if (result) {
    $('net-note').textContent = result;
    $('net-note').className = 'note ' + (/Connected/.test(result) ? 'good' : /Couldn't|first/.test(result) ? 'bad' : '');
  }

  const up = get('update', NAME.update);
  if (up.state) {
    const st = String(up.state).toUpperCase();
    const available = st.includes('AVAILABLE');
    $('install').hidden = !available;
    if (available) { $('install').disabled = false; $('update').textContent = 'Version ' + (up.value || 'new') + ' is available'; }
    else if (st.includes('INSTALLING')) $('update').textContent = 'Installing…';
    else if (st.includes('NO UPDATE')) $('update').textContent = 'Up to date';
    else if (!/Checking|Installing/.test($('update').textContent)) $('update').textContent = 'Not checked yet';
  }
}

function listen() {
  const es = new EventSource('/events');
  es.addEventListener('state', e => {
    try { const d = JSON.parse(e.data); state[d.id] = Object.assign(state[d.id] || {}, d); render(); } catch { /* ignore */ }
  });
  es.addEventListener('log', e => {
    const pre = $('log');
    pre.textContent = (pre.textContent + e.data.replace(/\u001b\[[0-9;]*m/g, '') + '\n').split('\n').slice(-150).join('\n');
    pre.scrollTop = pre.scrollHeight;
  });
  es.onopen = () => $('offline').classList.remove('show');
  es.onerror = () => $('offline').classList.add('show');
}

build();
listen();
