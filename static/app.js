/* Home page: upload, staged progress, results table. */

const MAX_MB = 12;
const STAGES = [
  'Reading your photo\u2026',
  'Finding the face\u2026',
  'Comparing with 82,620 faces\u2026',
  'Sorting the matches\u2026',
];

let picked = null;
let busy = false;

const $ = s => document.querySelector(s);
const drop = $('#drop');
const fileInput = $('#file');

document.getElementById('menubar').innerHTML = menuBar();
$('#statusbar').innerHTML = statusBar([
  'Ready', '82,620 faces indexed', '10,000 photos',
]);

drop.addEventListener('click', () => fileInput.click());
drop.addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); }
});
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.add('hot');
}));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.remove('hot');
}));
drop.addEventListener('drop', e => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) pick(f);
});
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) pick(fileInput.files[0]);
});

for (let i = 0; i < 28; i++) {
  const seg = document.createElement('i');
  seg.className = 'seg';
  $('#progress').appendChild(seg);
}

function pick(f) {
  if (!f.type.startsWith('image/')) {
    dialog('That is not an image', 'Please choose a JPG, PNG or HEIC photo.');
    return;
  }
  if (f.size > MAX_MB * 1024 * 1024) {
    dialog('Photo is too large', `Please use a photo under ${MAX_MB} MB. Most phone photos can be cropped or resized first.`);
    return;
  }
  picked = f;
  $('#preview').src = URL.createObjectURL(f);
  $('#pickedName').textContent = f.name;
  $('#pickedSize').textContent = `${(f.size / 1024 / 1024).toFixed(2)} MB`;
  $('#picked').style.display = 'block';
  $('#results').innerHTML = '';
  setStatus('Ready to search', '82,620 faces indexed', '10,000 photos');
}

$('#clear').addEventListener('click', () => {
  picked = null;
  fileInput.value = '';
  $('#picked').style.display = 'none';
  $('#progressWrap').style.display = 'none';
  $('#results').innerHTML = '';
  setStatus('Ready', '82,620 faces indexed', '10,000 photos');
});

function setStatus(...segs) {
  $('#statusbar').innerHTML = statusBar(segs);
}

function setProgress(pct, text) {
  $('#progressWrap').style.display = 'block';
  const segs = document.querySelectorAll('#progress .seg');
  const n = Math.round(segs.length * pct / 100);
  segs.forEach((s, i) => s.classList.toggle('on', i < n));
  $('#progressText').textContent = text || '';
}

$('#go').addEventListener('click', run);

async function run() {
  if (!picked || busy) return;
  busy = true;
  const go = $('#go');
  go.disabled = true;
  $('#results').innerHTML = '';
  document.body.style.cursor = 'wait';

  const t0 = Date.now();
  setProgress(6, STAGES[0]);
  setStatus('Uploading\u2026', '', '');

  // The request takes ~13s on the free tier. Advance the bar honestly:
  // we cannot know real server progress, so we slow-step and never reach 100%
  // until the response actually arrives.
  let stage = 0;
  const timer = setInterval(() => {
    const secs = (Date.now() - t0) / 1000;
    const pct = Math.min(90, 8 + secs * 6);
    const next = Math.min(STAGES.length - 1, Math.floor(pct / 30));
    if (next !== stage) { stage = next; }
    setProgress(pct, STAGES[stage]);
    setStatus(`Searching\u2026 ${secs.toFixed(0)}s`, '', '');
  }, 350);

  const body = new FormData();
  body.append('selfie', picked);

  try {
    const res = await fetch('/api/search', { method: 'POST', body });
    const data = await res.json().catch(() => ({}));
    clearInterval(timer);

    if (!res.ok) throw new Error(data.detail || `Server error (${res.status})`);

    setProgress(100, 'Done');
    const secs = ((Date.now() - t0) / 1000).toFixed(0);
    render(data, secs);
    setStatus(`${data.matches.length} photo(s) found`,
              `${data.matches.length} object(s)`,
              `${secs} s`);
  } catch (e) {
    clearInterval(timer);
    $('#progressWrap').style.display = 'none';
    setStatus('Search failed', '', '');
    dialog('Something went wrong', friendlyError(e.message || String(e)));
  } finally {
    busy = false;
    go.disabled = false;
    document.body.style.cursor = '';
  }
}

function friendlyError(msg) {
  if (/no face/i.test(msg)) {
    return `${msg}\n\nTips:\n\u2022 Use a photo where your face is clearly visible\n\u2022 Avoid sunglasses and heavy shadow\n\u2022 Face the camera straight on\nThe face should take up a good part of the picture.`;
  }
  if (/too small/i.test(msg)) {
    return `${msg}\n\nTry a photo taken closer to your face.`;
  }
  if (/too large/i.test(msg)) {
    return `The photo is bigger than ${MAX_MB} MB. Try cropping it first.`;
  }
  if (/fetch|network|Failed/i.test(msg)) {
    return 'Could not reach the server.\n\nThe free server sleeps when nobody uses it, so the first visit can take about a minute to wake up. Please try again in 30 seconds.';
  }
  return msg;
}

function render(data, secs) {
  const m = data.matches || [];
  const box = $('#results');

  if (!m.length) {
    box.innerHTML = `
      <div class="notebox warn">
        <b>No photos found</b>
        Your face was detected, but no photo in the archive matched closely enough.
        <br><br>
        Common reasons:
        <ul class="tick cross" style="margin-top:6px">
          <li>Your face is turned away or partly covered</li>
          <li>The photo is blurry or in poor light</li>
          <li>The picture is old, or you look quite different now</li>
        </ul>
        Try a sharp, front-facing photo taken at the ceremony if you have one.
        <div style="margin-top:10px">
          <button class="btn" onclick="document.getElementById('clear').click()">Try another photo</button>
        </div>
      </div>`;
    return;
  }

  const strong = m.filter(x => x.score >= 0.5);
  const possible = m.filter(x => x.score < 0.5);

  const rows = (list, cls) => list.map(x => {
    const parts = x.photo.split('/');
    const name = parts.pop();
    const archive = parts[0] || '';
    const tag = archive.endsWith('-001') ? 'Archive 1'
              : archive.endsWith('-002') ? 'Archive 2' : archive;
    const rest = parts.slice(1).filter(p => !/^\d{2}-\d{2}-\d{4}$/.test(p));
    return `<tr>
      <td class="fname">${esc(name)}</td>
      <td class="dim">${esc([tag, ...rest].join(' › '))}</td>
      <td class="num">${(x.score * 100).toFixed(0)}%</td>
      <td><button class="btn small copy" data-path="${esc(x.photo)}">Copy</button></td>
    </tr>`;
  }).join('');

  box.innerHTML = `
    <div class="row" style="justify-content:space-between;align-items:center;margin-bottom:6px">
      <b>${m.length} photo(s) found in ${secs} s</b>
      <span>
        <button class="btn small" id="dl">Save list</button>
        <button class="btn small" id="copyAll">Copy all paths</button>
      </span>
    </div>

    <table class="files">
      <thead><tr>
        <th>File name</th><th>Folder</th><th style="text-align:right">Match</th><th></th>
      </tr></thead>
      <tbody>${rows(strong)}</tbody>
    </table>

    ${possible.length ? `
      <div class="notebox">
        <b>${possible.length} possible match(es) &mdash; check these yourself</b>
        These scored lower. They may be you at an angle, or someone who looks similar.
      </div>
      <table class="files">
        <thead><tr>
          <th>File name</th><th>Folder</th><th style="text-align:right">Match</th><th></th>
        </tr></thead>
        <tbody>${rows(possible)}</tbody>
      </table>` : ''}

    <div class="notebox" style="margin-top:14px">
      <b>What now?</b>
      Use <b>Copy</b> to grab a file name, then search the shared photo folder
      for it. Match % is how confident the face comparison is &mdash; 100% means
      a near-exact match.
    </div>`;

  document.getElementById('copyAll').addEventListener('click', async () => {
    await copyText(m.map(x => x.photo).join('\n'), 'copyAll');
  });

  document.getElementById('dl').addEventListener('click', () => {
    const text = m.map(x => `${x.photo}\t${(x.score * 100).toFixed(0)}%`).join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'my-graduation-photos.txt';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  });

  box.querySelectorAll('.copy').forEach(b => {
    b.addEventListener('click', () => copyText(b.dataset.path, b));
  });
}

async function copyText(text, btn) {
  const done = () => { if (btn && btn.textContent) {
    const old = btn.textContent; btn.textContent = 'Copied';
    setTimeout(() => { btn.textContent = old; }, 1200);
  } };
  try {
    await navigator.clipboard.writeText(text);
    done();
  } catch {
    // clipboard API needs HTTPS + permission; fall back to a selectable box
    dialog('Copy this', text, true);
  }
}

/* A tiny Win95-style modal instead of alert(), which looks out of place. */
function dialog(title, body, selectable) {
  const d = el(`<div style="position:fixed;inset:0;background:rgba(0,0,0,0.25);
      display:flex;align-items:center;justify-content:center;padding:16px;z-index:99">
    <div class="window" style="margin:0;max-width:460px;width:100%">
      <div class="titlebar"><span class="title">${esc(title)}</span></div>
      <div class="content">
        ${selectable
          ? `<textarea readonly style="width:100%;height:120px;font-family:'Courier New',monospace;font-size:11px;
              border:2px solid;border-color:#808080 #fff #fff #808080;padding:4px">${esc(body)}</textarea>`
          : `<p style="white-space:pre-wrap">${esc(body)}</p>`}
        <div style="text-align:right;margin-top:12px"><button class="btn primary">OK</button></div>
      </div>
    </div>
  </div>`);
  d.querySelector('button').addEventListener('click', () => d.remove());
  document.body.appendChild(d);
}
