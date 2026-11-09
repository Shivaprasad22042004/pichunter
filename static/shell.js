/* Shared bits for every page: window chrome, menu bar, desktop shortcuts. */

const NAV = [
  ['/',            'Home'],
  ['/about',       'About'],
  ['/how-it-works','How it works'],
  ['/privacy',     'Privacy'],
  ['/credits',     'Credits'],
  ['/bring-your-own','Bring your archive'],
  ['/stats',       'Stats'],
];

function windowShell(title, innerHTML, { icon = '\uD83D\uDCC1', menu = true, id = '' } = {}) {
  return `
  <div class="window"${id ? ` id="${id}"` : ''}>
    <div class="titlebar">
      <span class="icon">${icon}</span>
      <span class="title">${title}</span>
      <button class="tbtn" onclick="minimise(this)" aria-label="Minimise">_</button>
      <button class="tbtn" onclick="maximise()" aria-label="Maximise">\u25A1</button>
      <button class="tbtn" onclick="history.length>1?history.back():location.href='/'" aria-label="Close">\u2715</button>
    </div>
    ${menu ? menuBar() : ''}
    <div class="content">${innerHTML}</div>
  </div>`;
}

function menuBar() {
  const here = location.pathname.replace(/\/$/, '') || '/';
  const links = NAV.map(([href, label]) => {
    const active = (here === href || (href !== '/' && here.startsWith(href))) ? ' active' : '';
    return `<a href="${href}" class="${active.trim()}">${label}</a>`;
  }).join('');
  return `<div class="menubar">${links}</div>`;
}

function footer(extra = '') {
  return `
  <div class="footer-links">
    <a href="/about">About</a><span>|</span>
    <a href="/privacy">Privacy</a><span>|</span>
    <a href="/credits">Credits</a><span>|</span>
    <a href="/bring-your-own">Bring your own archive</a>
    <br><br>
    ${extra || 'DSI Graduation 2026 &middot; built for classmates, free of charge'}
  </div>`;
}

function minimise(el) {
  const win = el.closest('.window');
  const content = win.querySelector('.content');
  const bar = win.querySelector('.menubar');
  const status = win.querySelector('.statusbar');
  const hidden = content.style.display === 'none';
  content.style.display = hidden ? '' : 'none';
  if (bar) bar.style.display = hidden ? '' : 'none';
  if (status) status.style.display = hidden ? '' : 'none';
  el.textContent = hidden ? '_' : '\u25A1';
}

let _maxed = false;
function maximise() {
  const wins = document.querySelectorAll('.window');
  _maxed = !_maxed;
  wins.forEach(w => { w.style.maxWidth = _maxed ? '1200px' : ''; });
}

function statusBar(segments) {
  // Returns the segments only; the caller owns the surrounding .statusbar div.
  return segments.map((s, i) =>
    `<div class="${i === 0 ? 'grow' : ''}">${s}</div>`).join('');
}

function el(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
