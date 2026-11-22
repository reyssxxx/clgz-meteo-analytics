/* Метеоаналитика — интерфейс. Без фреймворков: состояние в адресной строке, экраны — функции render*. */
'use strict';

// ------------------------------------------------------------------ константы и утилиты
const C = { accent: '#2A6439', accent2: '#22502E', ink: '#1E2421', ink2: '#57605B', ink3: '#89908C',
  line: '#D5D9DE', grid: '#E7EAED', warm: '#C8323C', cold: '#2F6DB5', dry: '#B0731B', wet: '#1F8A86', other: '#9CA3A0' };
const MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const MSHORT = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const SEASONS = ['Зима', 'Весна', 'Лето', 'Осень'];
// Что показывает строка управления на каждом экране
const CTL = {
  overview: ['var', 'station', 'period', 'settings'],
  tables: ['var', 'station', 'period'],
  trends: ['var', 'station', 'scope', 'settings'],
  compare: ['var', 'station', 'cmp', 'settings'],
  stations: ['var', 'period'],
  agro: ['station', 'period', 'settings'],
  data: ['var', 'station', 'period', 'settings'],
  forecast: ['station'],
};
const PERIOD_PAGES = new Set(Object.keys(CTL).filter(p => CTL[p].includes('period')));
const SCOPES = [['', 'Год целиком'], ...['Зима', 'Весна', 'Лето', 'Осень'].map((s, k) => [`s${[12, 3, 6, 9][k]}`, s]),
  ...['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']
    .map((m, k) => [`m${k + 1}`, m[0].toUpperCase() + m.slice(1)])];

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const cap = s => s ? s[0].toUpperCase() + s.slice(1) : s;
function fmt(x, d = 1, sign = false) {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  const s = Math.abs(x).toFixed(d).replace('.', ',').replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return (x < 0 && Number(Math.abs(x).toFixed(d)) !== 0 ? '−' : sign && x > 0 ? '+' : '') + s;
}
const pad = n => String(n).padStart(2, '0');
const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseISO = s => { const [y, m, d] = s.slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); };
const ruDate = (s, year = true) => { const d = parseISO(s); return `${d.getDate()} ${MONTHS_GEN[d.getMonth()]}${year ? ' ' + d.getFullYear() : ''}`; };
function plural(n, forms) { n = Math.abs(n) % 100; const n1 = n % 10;
  return n > 10 && n < 20 ? forms[2] : n1 > 1 && n1 < 5 ? forms[1] : n1 === 1 ? forms[0] : forms[2]; }

function toast(msg, err = false) {
  const t = $('#toast'); t.textContent = msg; t.className = 'toast' + (err ? ' err' : ''); t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 4200);
}

// ------------------------------------------------------------------ состояние
const S = { page: 'overview', ds: 'clgz', station: '', var: 't_air', kind: 'season', y: 0, i: 0,
  cs: '', ce: '', base: 'all', clean: true, fill: false };
let META = null;

function readHash() {
  const [page, q] = location.hash.slice(1).split('?');
  if (page) S.page = page;
  const p = new URLSearchParams(q || '');
  for (const k of ['ds', 'station', 'var', 'kind', 'cs', 'ce', 'base']) if (p.has(k)) S[k] = p.get(k);
  for (const k of ['y', 'i']) if (p.has(k)) S[k] = Number(p.get(k));
  for (const k of ['clean', 'fill']) if (p.has(k)) S[k] = p.get(k) === '1';
}
function writeHash() {
  const p = new URLSearchParams({ ds: S.ds, station: S.station, var: S.var, kind: S.kind, y: S.y, i: S.i,
    base: S.base, clean: S.clean ? 1 : 0, fill: S.fill ? 1 : 0 });
  if (S.kind === 'custom') { p.set('cs', S.cs); p.set('ce', S.ce); }
  history.replaceState(null, '', `#${S.page}?${p}`);
}

// ------------------------------------------------------------------ периоды
function bounds(kind = S.kind, y = S.y, i = S.i) {
  if (kind === 'month') return [new Date(y, i, 1), new Date(y, i + 1, 0)];
  if (kind === 'season') return i === 0 ? [new Date(y - 1, 11, 1), new Date(y, 2, 0)] : [new Date(y, 3 * i - 1, 1), new Date(y, 3 * i + 2, 0)];
  if (kind === 'year') return [new Date(y, 0, 1), new Date(y, 11, 31)];
  return [parseISO(S.cs), parseISO(S.ce)];
}
function periodLabel(kind = S.kind, y = S.y, i = S.i) {
  if (kind === 'month') return `${cap(MONTHS[i])} ${y}`;
  if (kind === 'season') return i === 0 ? `Зима ${y - 1}/${String(y).slice(2)}` : `${SEASONS[i]} ${y}`;
  if (kind === 'year') return `${y} год`;
  const [a, b] = bounds(); return `${ruDate(iso(a))} — ${ruDate(iso(b))}`;
}
function shifted(n) {
  let { y, i } = S;
  if (S.kind === 'month') { const t = y * 12 + i + n; y = Math.floor(t / 12); i = t % 12; }
  else if (S.kind === 'season') { if (Math.abs(n) === 12) y += Math.sign(n); else { const t = y * 4 + i + n; y = Math.floor(t / 4); i = t % 4; } }
  else if (S.kind === 'year') y += Math.abs(n) === 12 ? 10 * Math.sign(n) : n;
  return { y, i };
}
function dataRange() {
  // на экране сравнения станций период общий для пары — берём весь доступный диапазон
  if (S.page === 'stations') {
    const xs = META.stations;
    return [parseISO(xs.map(s => s.start).sort()[0]), parseISO(xs.map(s => s.end).sort().slice(-1)[0])];
  }
  const st = META.stations.find(s => s.id === S.station) || META.stations[0];
  return [parseISO(st.start), parseISO(st.end)];
}
function fits(kind, y, i) { const [a, b] = bounds(kind, y, i); const [lo, hi] = dataRange(); return b <= hi && a >= lo; }
function latestPeriod(kind) {
  const [, hi] = dataRange();
  if (kind === 'month') { let y = hi.getFullYear(), i = hi.getMonth(); if (!fits('month', y, i)) { i--; if (i < 0) { i = 11; y--; } } return { y, i }; }
  if (kind === 'year') { let y = hi.getFullYear(); if (!fits('year', y, 0)) y--; return { y, i: 0 }; }
  let y = hi.getFullYear() + 1, i = 0;
  for (let k = 0; k < 12; k++) { if (fits('season', y, i)) return { y, i }; i--; if (i < 0) { i = 3; y--; } }
  return { y: hi.getFullYear(), i: 2 };
}
function ensurePeriod() {
  if (S.kind === 'custom') {
    const [lo, hi] = dataRange();
    if (!S.cs || !S.ce) { S.cs = iso(new Date(hi.getFullYear(), 0, 1)); S.ce = iso(hi); }
    if (parseISO(S.ce) < lo || parseISO(S.cs) > hi || S.cs > S.ce) {
      S.cs = iso(lo);
      S.ce = iso(hi);
    } else {
      if (parseISO(S.cs) < lo) S.cs = iso(lo);
      if (parseISO(S.ce) > hi) S.ce = iso(hi);
    }
    return;
  }
  if (!S.y || !fits(S.kind, S.y, S.i)) {
    Object.assign(S, latestPeriod(S.kind));
    if (!fits(S.kind, S.y, S.i)) {
      const [lo, hi] = dataRange();
      Object.assign(S, { kind: 'custom', cs: iso(lo), ce: iso(hi) });
    }
  }
}
function periodParams() { const [a, b] = bounds(); return { start: iso(a), end: iso(b) }; }

// ------------------------------------------------------------------ API
let routeSeq = 0;
const STALE = new Error('stale');
async function api(path, params = {}, withPeriod = true) {
  const mine = routeSeq;
  const base = S.base;
  const q = new URLSearchParams({ ds: S.ds, station: S.station, base, clean: S.clean, fill: S.fill,
    ...(withPeriod ? periodParams() : {}), ...params });
  const r = await fetch(`${path}?${q}`);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `Ошибка сервера (${r.status})`);
  if (mine !== routeSeq) throw STALE;   // пока ждали ответ, пользователь переключил экран или период
  return body;
}
function apiUrl(path, extra = {}) {
  const base = S.base;
  return `${path}?${new URLSearchParams({ ds: S.ds, station: S.station, var: S.var, base, clean: S.clean, fill: S.fill, ...periodParams(), ...extra })}`;
}

// ------------------------------------------------------------------ Plotly
Plotly.register({ moduleType: 'locale', name: 'ru', dictionary: {}, format: {
  days: ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'],
  shortDays: ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'],
  months: MONTHS, shortMonths: MSHORT, date: '%d.%m.%Y', decimal: ',', thousands: ' ' } });
const FONT = { family: '"Source Sans 3", system-ui, sans-serif', size: 13, color: C.ink2 };
// по умолчанию графики не масштабируются мышью — чтобы нельзя было случайно сбить вид выделением
const AX = { gridcolor: C.grid, linecolor: C.line, zeroline: false, tickfont: { size: 12 }, automargin: true, fixedrange: true };
// приближение только по времени и только на длинных рядах: протянуть — приблизить, двойной клик — сбросить
const ZOOM_HINT = '<span class="zoomhint">Протяните по графику, чтобы приблизить; двойной клик — сбросить</span>';
function lay(extra = {}) {
  const base = { font: FONT, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    margin: { l: 52, r: 16, t: 76, b: 34 }, showlegend: false, hovermode: 'x unified', dragmode: false,
    hoverlabel: { bgcolor: '#fff', bordercolor: C.line, font: { family: FONT.family, size: 13, color: C.ink } },
    xaxis: { ...AX }, yaxis: { ...AX } };
  for (const [k, v] of Object.entries(extra)) base[k] = (typeof v === 'object' && !Array.isArray(v) && base[k]) ? { ...base[k], ...v } : v;
  return base;
}
const CFG = { displayModeBar: false, responsive: true, locale: 'ru' };
function plot(id, data, layout) {
  const el = document.getElementById(id);
  if (!el) return null;
  Plotly.react(el, data, lay(layout), CFG);
  el._baseAnn = (layout && layout.annotations) || [];
  el._baseShapes = (layout && layout.shapes) || [];
  freezeAxes(el);
  return el;
}
/* Закрепляет диапазоны осей после первой отрисовки. Иначе Plotly при наведении расширяет оси,
   чтобы вместить плашку и маркер у края, — график «растягивается» и скачет под курсором. */
function freezeAxes(el) {
  const fl = el._fullLayout, upd = {};
  for (const k of Object.keys(fl)) {
    if (/^[xy]axis\d*$/.test(k) && fl[k].range) {
      upd[`${k}.range`] = fl[k].range.slice();
      upd[`${k}.autorange`] = false;
    }
  }
  if (Object.keys(upd).length) Plotly.relayout(el, upd);
}
function downloadPng(id, name) {
  const el = document.getElementById(id);
  if (el) Plotly.downloadImage(el, { format: 'png', filename: name, width: el.clientWidth * 2, height: el.clientHeight * 2, scale: 1 });
}
const DIVERGE = [[0, C.cold], [0.25, '#A9C3E3'], [0.5, '#F2F2F2'], [0.75, '#EDAAA0'], [1, C.warm]];
const DIVERGE_WET = [[0, C.dry], [0.25, '#E7C998'], [0.5, '#F2F2F2'], [0.75, '#9ED3CF'], [1, C.wet]];

// ------------------------------------------------------------------ реакция графиков на наведение
// Стандартные подсказки Plotly на этих графиках выключены (hoverinfo: 'none'), вместо них —
// своя плашка-аннотация, подсветка наведённого и приглушение остального.
const DIM = 0.16;
function onHover(el, over, out) {
  if (!el) return;
  el.removeAllListeners?.('plotly_hover'); el.removeAllListeners?.('plotly_unhover');
  // Plotly сам присылает hover не чаще раза за кадр; своя очередь через requestAnimationFrame
  // «залипала», если кадр не отрисовался (вкладка в фоне), — поэтому обрабатываем сразу.
  // Повторное наведение на ту же точку пропускаем, чтобы не перерисовывать впустую.
  let lastKey = '';
  el.on('plotly_hover', ev => {
    const p = ev.points[0] || {};
    const key = `${p.curveNumber}:${p.pointNumber}`;
    if (key === lastKey) return;
    lastKey = key;
    over(ev);
  });
  el.on('plotly_unhover', ev => { lastKey = ''; out(ev); });
}
/* Плашка наведения — один HTML-элемент поверх всей страницы (а не аннотация внутри графика):
   не уползает под заголовки панелей, не заставляет Plotly пересчитывать оси,
   у правого края разворачивается влево, у верхнего — уходит под точку, за окно не выходит. */
const TIP = Object.assign(document.createElement('div'), { className: 'ctip', hidden: true });
document.body.appendChild(TIP);
function showTipXY(el, px, py, html, opts = {}) {
  const r = el.getBoundingClientRect();
  TIP.innerHTML = html;
  TIP.hidden = false;
  const w = TIP.offsetWidth, h = TIP.offsetHeight, gap = 14;
  const ax = r.left + px, ay = r.top + py;
  let x = px > r.width * 0.6 ? ax - w - gap : ax + gap;
  let y = opts.below ? ay + gap : ay - h - gap;
  if (y < Math.max(r.top + 4, 64)) y = ay + gap;            // у верхнего края — под точкой
  x = Math.min(Math.max(x, 8), innerWidth - w - 8);
  y = Math.min(Math.max(y, 64), innerHeight - h - 8);
  TIP.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
}
/* координаты точки графика в пикселях относительно его контейнера */
function ptPx(pt) {
  const xa = pt.xaxis, ya = pt.yaxis;
  return [xa._offset + xa.l2p(xa.d2l(pt.x)), ya._offset + ya.l2p(ya.d2l(pt.y))];
}
function showTipAt(el, pt, html, opts = {}) {
  const [px, py] = ptPx(pt);
  showTipXY(el, px, opts.top ? pt.yaxis._offset + 6 : py, html, opts.top ? { below: true } : {});
}
function hideTip() { TIP.hidden = true; }
addEventListener('scroll', hideTip, { passive: true });
const setAnn = (el, extra) => Plotly.relayout(el, { annotations: [...el._baseAnn, ...extra] });
const setShapes = (el, extra) => Plotly.relayout(el, { shapes: [...el._baseShapes, ...extra] });
const vline = (x, xref = 'x') => ({ type: 'line', xref, yref: 'paper', x0: x, x1: x, y0: 0, y1: 1, line: { color: C.ink3, width: 1, dash: 'dot' } });

/* Линии: наведённая выходит на первый план, остальные бледнеют; на линии точка, рядом плашка.
   styleOf(i) → исходный стиль трассы {color, width, opacity}; label(pt) → текст плашки;
   dot — индекс пустой трассы-маркера в конце. */
function lineFocus(el, n, styleOf, label, dot) {
  if (!el) return;
  const idx = [...Array(n).keys()];
  const reset = () => {
    Plotly.restyle(el, { opacity: idx.map(i => styleOf(i).opacity), 'line.width': idx.map(i => styleOf(i).width),
      'line.color': idx.map(i => styleOf(i).color) }, idx);
    Plotly.restyle(el, { x: [[]], y: [[]] }, [dot]);
    hideTip();
  };
  onHover(el, ev => {
    const pt = ev.points.find(p => p.curveNumber < n);
    if (!pt) return;
    const k = pt.curveNumber, s = styleOf(k);
    const hot = s.hotColor || (s.color === C.other ? C.ink2 : s.color);
    Plotly.restyle(el, {
      opacity: idx.map(i => i === k ? 1 : styleOf(i).keep ? Math.max(.35, styleOf(i).opacity * .5) : DIM),
      'line.width': idx.map(i => i === k ? Math.min(s.width + .8, 3) : styleOf(i).width),
      'line.color': idx.map(i => i === k ? hot : styleOf(i).color) }, idx);
    Plotly.restyle(el, { x: [[pt.x]], y: [[pt.y]], 'marker.color': [hot] }, [dot]);
    showTipAt(el, pt, label(pt));
  }, reset);
}

/* Столбики: наведённый ярче и с обводкой, остальные бледнеют; над ним плашка */
function barFocus(el, trace, label) {
  if (!el) return;
  const n = el.data[trace].y.length;
  const base = el.data[trace].marker.color;
  onHover(el, ev => {
    const pt = ev.points.find(p => p.curveNumber === trace);
    if (!pt) return;
    Plotly.restyle(el, { 'marker.opacity': [Array.from({ length: n }, (_, i) => i === pt.pointNumber ? 1 : .32)],
      'marker.line.width': [Array.from({ length: n }, (_, i) => i === pt.pointNumber ? 2 : 0)],
      'marker.line.color': C.ink }, [trace]);
    // плашка у верхнего края графика сбоку от столбика — не обрезается даже у самых высоких
    showTipAt(el, pt, label(pt), { top: true });
  }, () => {
    Plotly.restyle(el, { 'marker.opacity': 1, 'marker.line.width': 0, 'marker.color': [base] }, [trace]);
    hideTip();
  });
}

/* Одиночные линии: за курсором по линиям едут точки, вертикальная направляющая, плашка */
function followDot(el, traces, dot, label) {
  if (!el) return;
  onHover(el, ev => {
    const pts = ev.points.filter(p => traces.includes(p.curveNumber) && p.y !== null && p.y !== undefined);
    if (!pts.length) return;
    Plotly.restyle(el, { x: [pts.map(p => p.x)], y: [pts.map(p => p.y)], 'marker.color': [pts.map(p => p.data.line?.color || C.accent)] }, [dot]);
    setShapes(el, [vline(pts[0].x, pts[0].xaxis._id)]);
    const top = pts.reduce((a, b) => (b.y > a.y ? b : a));
    showTipAt(el, top, label(pts));
  }, () => { Plotly.restyle(el, { x: [[]], y: [[]] }, [dot]); setShapes(el, []); hideTip(); });
}
const dotTrace = (yaxis = 'y') => ({ x: [], y: [], yaxis, mode: 'markers', hoverinfo: 'skip', showlegend: false,
  marker: { size: 8, color: C.accent, line: { color: '#fff', width: 2 } } });

/* Тепловая карта: наведённая клетка в рамке, рядом плашка */
function cellFocus(el, label, cell) {
  if (!el) return;
  onHover(el, ev => {
    const pt = ev.points[0];
    if (!pt || pt.z === null || pt.z === undefined) return;
    const [x0, x1, y0, y1] = cell(pt);
    setShapes(el, [{ type: 'rect', xref: 'x', yref: 'y', x0, x1, y0, y1, line: { color: C.ink, width: 2.5 } }]);
    showTipAt(el, pt, label(pt));
  }, () => { setShapes(el, []); hideTip(); });
}

// ------------------------------------------------------------------ общие куски разметки
const ICON = {
  dl: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 4v11m0 0l-4-4m4 4l4-4M5 20h14"/></svg>',
  doc: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 3h7l5 5v13H7z M14 3v5h5"/></svg>',
};
function panelTitle(title, sub, tip, cls = '') {
  return `<div class="ctitle ${cls}"><b>${esc(title)}</b><span>${esc(sub)}</span>${tip ? `<span class="info" tabindex="0">i</span><div class="tip">${esc(tip)}</div>` : ''}</div>`;
}
const dlBtn = (chart, name) => `<button class="pill" data-png="${chart}" data-name="${esc(name)}">${ICON.dl}Скачать</button>`;
const toggle = (id, opts, cur) => `<div class="toggle" id="${id}">${opts.map(([v, t]) => `<button data-v="${v}" class="${v === cur ? 'on' : ''}">${t}</button>`).join('')}</div>`;
const varById = id => META.variables.find(v => v.id === id) || { id, label: id, unit: '' };
const empty = (title, text) => `<div class="box empty"><b>${esc(title)}</b>${esc(text)}</div>`;

// ------------------------------------------------------------------ ОБЗОР
const UI = { yearsMode: 'values', freq: 'MS', trendScope: '', agroPick: '', cmp: null, ref: '' };
const stationChanges = typeof BroadcastChannel === 'function' ? new BroadcastChannel('meteo-public-stations') : null;
let metadataRefresh = 0;

async function refreshCaseStations() {
  if (S.ds !== 'clgz') return;
  const refresh = ++metadataRefresh;
  const params = new URLSearchParams({ ds: S.ds, clean: S.clean });
  const response = await fetch('/api/meta?' + params, { cache: 'no-store' });
  if (!response.ok) throw new Error('Не удалось обновить список станций.');
  const meta = await response.json();
  if (refresh !== metadataRefresh || S.ds !== 'clgz') return;
  applyMeta(meta);
  await route();
}
stationChanges?.addEventListener('message', () => {
  if (META) refreshCaseStations().catch(error => toast(error.message, true));
});

async function renderOverview(main) {
  const v = varById(S.var);
  const o = await api('/api/overview', { var: S.var });
  const label = periodLabel();
  const isYears = o.years.mode === 'years';
  const cum = o.years.cumulative;
  main.innerHTML = `
  <div class="grid2">
    <div class="box panel">
      <div class="tb">${dlBtn('ch-years', 'график')}<span class="sp"></span>
        ${isYears ? toggle('yearsMode', [['values', 'Значения'], ['anom', 'Отклонения']], UI.yearsMode) : ZOOM_HINT}</div>
      ${panelTitle(v.label + (cum ? ', нарастающим итогом' : ''), isYears ? `${label} на фоне всех лет наблюдений станции` : `${label}, по месяцам`,
        isYears ? 'Каждая серая линия — тот же период в другой год наблюдений. Пунктир — среднее за выбранный период (годы задаются в настройках), цветная линия — выбранный период. В подсказке указано число лет для каждой даты.' : '')}
      <div class="chart" id="ch-years"></div>
      ${isYears ? `<div class="legend"><span class="chip year">${esc(label)}</span><span class="chip norm">Среднее за выбранный период</span><span class="chip others">Другие годы: ${o.years.lines.length}</span></div>` : ''}
    </div>
    <div class="box panel">
      <div class="tb">${dlBtn('ch-heat', 'отклонения')}</div>
      ${panelTitle('Отклонение от среднего', `${v.label}, месяц к среднему за ${o.base.join('–')}${o.heatmap.var.sum ? ', %' : ', ' + v.unit}`,
        'Каждая клетка — месяц. Цвет показывает, насколько он отличался от среднего значения этого месяца за выбранные годы. Пустые клетки — месяцы с неполными данными (больше 10 неполных дней или 5 подряд). Рамкой выделен выбранный период.')}
      <div class="chart" id="ch-heat"></div>
      <div class="cbar">${o.heatmap.inverted ? 'суше' : 'холоднее'} <i class="${o.heatmap.inverted ? 'inv' : ''}"></i> ${o.heatmap.inverted ? 'влажнее' : 'теплее'}</div>
    </div>
  </div>
  <div class="grid21 mt">
    <div class="box panel" id="rank-panel">
      <div class="head"><b>${esc(rankTitle())}</b><span>${esc(v.label.toLowerCase())}${o.ranking.category ? `, ${o.ranking.category.rank}-е место из ${o.ranking.category.n}` : ''}</span>
        ${o.ranking.category ? `<span class="cat cat-${o.ranking.category.index}">${esc(o.ranking.category.label)}</span>` : ''}<span class="sp"></span>${dlBtn('ch-rank', 'по годам')}</div>
      <div class="chart short" id="ch-rank"></div>
    </div>
    <div class="box facts">${factsHtml(o.facts, label)}</div>
  </div>`;
  drawYears(o.years);
  drawHeat(o.heatmap);
  drawRank(o.ranking);
  $('#yearsMode')?.addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return;
    UI.yearsMode = b.dataset.v; $$('#yearsMode button').forEach(x => x.classList.toggle('on', x === b)); drawYears(o.years); });
}
function yearsLabel(n) {
  return `${n} ${plural(n, ['год', 'года', 'лет'])}`;
}
function parameterDifference(a, b, v) {
  return v.id === 'wind_dir' ? ((a - b + 540) % 360) - 180 : a - b;
}
function rankTitle() {
  if (S.kind === 'month') return `${cap(MONTHS[S.i])} по годам`;
  if (S.kind === 'season') return `${SEASONS[S.i]} по годам`;
  if (S.kind === 'year') return 'Годы';
  return 'Тот же период по годам';
}

function drawYears(y) {
  const v = y.var;
  const u = v.unit;
  if (y.mode === 'series') {
    const tr = [];
    if (y.band) tr.push({ x: y.x, y: y.band.hi, mode: 'lines', line: { width: 0 }, hoverinfo: 'skip' },
      { x: y.x, y: y.band.lo, mode: 'lines', line: { width: 0 }, fill: 'tonexty', fillcolor: 'rgba(42,100,57,.10)', name: 'Tmin…Tmax', hoverinfo: 'skip' });
    const ni = tr.length;
    tr.push({ x: y.x, y: y.norm, mode: 'lines', name: 'Среднее за выбранный период', line: { color: C.ink, width: 1.6, dash: 'dash' }, hoverinfo: 'none' });
    tr.push({ x: y.x, y: y.y, type: v.sum ? 'bar' : 'scatter', mode: 'lines', name: v.label, marker: { color: C.accent },
      line: { color: C.accent, width: 2 }, hoverinfo: 'none' });
    tr.push(dotTrace());
    const el = plot('ch-years', tr, { hovermode: 'x', dragmode: 'zoom', xaxis: { type: 'date', fixedrange: false }, yaxis: { ticksuffix: ' ' + u } });
    const monthName = x => { const d = parseISO(String(x)); return `${cap(MONTHS[d.getMonth()])} ${d.getFullYear()}`; };
    if (v.sum) barFocus(el, ni + 1, pt => `<b>${monthName(pt.x)}</b><br>${fmt(pt.y)} ${u}`);
    else followDot(el, [ni, ni + 1], tr.length - 1, pts => {
      const val = pts.find(p => p.curveNumber === ni + 1), nrm = pts.find(p => p.curveNumber === ni);
      return `<b>${monthName(pts[0].x)}</b><br>${val ? fmt(val.y) + ' ' + u : '—'}` +
        (nrm ? `<br><span style="color:${C.ink2}">среднее ${fmt(nrm.y)} (${yearsLabel(y.base_years[nrm.pointNumber])})${val ? ', ' + fmt(parameterDifference(val.y, nrm.y, v), 1, true) : ''}</span>` : '');
    });
    return;
  }
  const anom = UI.yearsMode === 'anom';
  const tr = [];
  const ann = [];
  if (!anom && y.mark) ann.push({ x: y.mark.x, y: y.mark.y, text: esc(y.mark.text), showarrow: true, arrowhead: 0, arrowcolor: C.accent,
    ax: 40, ay: -34, bgcolor: C.accent, bordercolor: C.accent, borderpad: 4, font: { color: '#fff', size: 13 } });
  const layout = { xaxis: { type: 'date', tickformat: '%-d %b', dtick: 'M1' },
    yaxis: { ticksuffix: ' ' + u, zeroline: anom, zerolinecolor: C.ink3 }, annotations: ann };
  if (anom) {
    const d = y.current.y.map((c, k) => c === null || y.norm[k] === null ? null : parameterDifference(c, y.norm[k], v));
    const pos = v.sum ? C.wet : C.warm, neg = v.sum ? C.dry : C.cold;
    tr.push({ x: y.x, y: d, type: 'bar', name: 'Отклонение', marker: { color: d.map(z => z >= 0 ? pos : neg) }, hoverinfo: 'none' });
    const el = plot('ch-years', tr, { ...layout, hovermode: 'x', bargap: .15 });
    barFocus(el, 0, pt => `<b>${ruDate(String(pt.x))}</b><br>${fmt(pt.y, 1, true)} ${u} к среднему (${yearsLabel(y.base_years[pt.pointNumber])})` +
      `<br><span style="color:${C.ink2}">${v.sum ? 'накоплено' : 'было'} ${fmt(y.current.y[pt.pointNumber])} ${u}</span>`);
    return;
  }
  // все годы серыми линиями: наведение на любую линию выводит её на первый план
  const L = y.lines.length;
  for (const l of y.lines) tr.push({ x: y.x, y: l.y, name: String(l.year), mode: 'lines', line: { color: C.other, width: 1 }, opacity: .38, hoverinfo: 'none' });
  tr.push({ x: y.x, y: y.norm, mode: 'lines', name: 'Среднее за выбранный период', line: { color: C.ink, width: 2, dash: 'dash' }, hoverinfo: 'none' });
  tr.push({ x: y.x, y: y.current.y, mode: 'lines', name: String(y.current.year), line: { color: C.accent, width: 2.6 }, hoverinfo: 'none' });
  tr.push(dotTrace());
  const el = plot('ch-years', tr, { ...layout, hovermode: 'closest', hoverdistance: 24 });
  const styleOf = i => i < L ? { color: C.other, width: 1, opacity: .38 }
    : i === L ? { color: C.ink, width: 2, opacity: 1, keep: true } : { color: C.accent, width: 2.6, opacity: 1, keep: true };
  lineFocus(el, L + 2, styleOf, pt => {
    const day = ruDate(String(pt.x), false);
    const k = pt.pointNumber, nrm = y.norm[k];
    const dev = nrm === null ? '' : `<br><span style="color:${C.ink2}">${pt.curveNumber === L ? 'В среднем' : fmt(parameterDifference(pt.y, nrm, v), 1, true) + ' ' + u + ' к среднему'} (${yearsLabel(y.base_years[k])})</span>`;
    return `<b>${esc(pt.data.name)}</b>   ${fmt(pt.y)} ${u}<br><span style="color:${C.ink2}">${y.cumulative ? 'на ' : ''}${day}</span>${dev}`;
  }, L + 2);
}

function drawHeat(h) {
  const z = h.z, lim = h.limit;
  const shapes = h.selected.map(([yr, m]) => ({ type: 'rect', xref: 'x', yref: 'y', x0: m - 1.5, x1: m - .5, y0: yr - .5, y1: yr + .5,
    line: { color: C.ink, width: 2 } }));
  const el = plot('ch-heat', [{ type: 'heatmap', z, x: [...Array(12).keys()], y: h.years, zmin: -lim, zmax: lim, zmid: 0,
    colorscale: h.inverted ? DIVERGE_WET : DIVERGE, showscale: false, xgap: 2, ygap: 2, hoverinfo: 'none' }],
  { hovermode: 'closest', shapes, margin: { l: 52, r: 16, t: 76, b: 30 },
    xaxis: { tickvals: [...Array(12).keys()], ticktext: MSHORT, showgrid: false, linecolor: 'rgba(0,0,0,0)' },
    yaxis: { autorange: 'reversed', showgrid: false, dtick: 5, linecolor: 'rgba(0,0,0,0)' } });
  cellFocus(el, pt => `<b>${cap(MONTHS[pt.x])} ${pt.y}</b><br>${fmt(pt.z, 1, true)} ${esc(h.unit)}` +
    `<br><span style="color:${C.ink2}">${pt.z > 0 ? (h.inverted ? 'влажнее' : 'теплее') : (h.inverted ? 'суше' : 'холоднее')} среднего (${yearsLabel(h.base_years[pt.x])})</span>`,
  pt => [pt.x - .5, pt.x + .5, pt.y - .5, pt.y + .5]);
}

function drawRank(r) {
  if (!r.years.length) { $('#ch-rank').innerHTML = '<div class="empty">Недостаточно сопоставимых лет для выбранного периода.</div>'; return; }
  const v = r.var;
  const sorted = r.values.filter(x => x !== null).sort((a, b) => b - a);
  const el = plot('ch-rank', [{ type: 'bar', x: r.years, y: r.values, marker: { color: r.years.map(y => y === r.selected ? C.accent : '#BFC5C2') },
    hoverinfo: 'none' }],
  { margin: { l: 48, r: 12, t: 10, b: 28 }, hovermode: 'x', bargap: .25,
    yaxis: { range: v.sum ? undefined : rankRange(r.values), ticksuffix: ' ' + (v.unit.length < 3 ? v.unit : '') },
    shapes: r.norm === null ? [] : [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: r.norm, y1: r.norm, line: { color: C.ink, width: 1.5, dash: 'dash' } }],
    annotations: r.norm === null ? [] : [{ xref: 'paper', x: 0, y: r.norm, xanchor: 'left', yanchor: 'bottom', showarrow: false,
      text: `среднее ${fmt(r.norm)} ${esc(v.unit)} ${esc(r.base_label || '')}`, bgcolor: '#fff', font: { size: 12, color: C.ink } }] });
  barFocus(el, 0, pt => {
    const place = sorted.indexOf(pt.y) + 1;
    const dev = r.norm === null ? '' : `, ${fmt(pt.y - r.norm, 1, true)} к среднему`;
    return `<b>${pt.x}</b>   ${fmt(pt.y)} ${esc(v.unit)}<br><span style="color:${C.ink2}">${place}-е место из ${sorted.length}${dev}</span>`;
  });
}
function rankRange(vals) { const f = vals.filter(x => x !== null); const lo = Math.min(...f), hi = Math.max(...f), p = (hi - lo) * .15 || 1; return [lo - p, hi + p]; }

function factsHtml(f, label) {
  const tone = t => ({ warm: 'warm', cold: 'cold', dry: 'dry', wet: 'wet' }[t] || '');
  const items = f.facts.map(x => `<div class="fact"><div class="k ${tone(x.tone)}">${esc(x.value)}</div><div class="d">${esc(x.text)}</div></div>`).join('');
  const all = f.insights.map(i => `<li>${esc(i.text)}</li>`).join('');
  return `<h3>Коротко: ${esc(label.toLowerCase())}</h3>${items || '<div class="d">Недостаточно данных за период.</div>'}
    <details class="more"><summary>Все выводы (${f.insights.length})</summary><ul>${all}</ul></details>
    <div class="actions"><a class="btn" href="${apiUrl('/api/report.html')}">${ICON.doc}Отчёт HTML</a>
    <a class="pill" href="${apiUrl('/api/report.xlsx')}">${ICON.dl}Таблицы Excel</a></div>`;
}

// ------------------------------------------------------------------ ТАБЛИЦЫ
/* То, что сейчас сводят вручную в Excel: суточная таблица за месяц или декадная за период */
async function renderTables(main) {
  const mode = S.kind === 'month' ? 'daily' : 'decades';
  const [t, review] = await Promise.all([api('/api/table', { mode }), api('/api/quality/selection', { var: S.var, mode })]);
  const cols = t.columns;
  const cell = (c, v) => {
    if (v === null || v === undefined) return '<td class="num na">—</td>';
    if (c.key === 'wind_dir') return `<td class="num" title="${fmt(v, 0)}°">${RUMB[Math.round(((v % 360) + 360) % 360 / 22.5) % 16]}</td>`;
    return `<td class="num">${fmt(v, 1)}</td>`;
  };
  const groups = [];
  for (const c of cols) { const g = groups[groups.length - 1]; if (g && g.name === c.group) g.n++; else groups.push({ name: c.group, n: 1 }); }
  const head = `<thead><tr><th rowspan="2" class="first">${mode === 'daily' ? 'Дата' : 'Период'}</th>${groups.map(g => `<th colspan="${g.n}" class="grp">${esc(g.name)}</th>`).join('')}
    ${mode === 'daily' ? `<th rowspan="2" class="num">${esc(t.coverage_label)}</th>` : ''}</tr>
    <tr>${cols.map(c => `<th class="num">${esc(c.label)}</th>`).join('')}</tr></thead>`;
  const sumRow = (r, cls, label) => `<tr class="${cls}"><td class="first">${esc(label || r.label)}</td>${cols.map(c => {
    const v = r.values[c.key];
    if (r.kind === 'month' && r.valid && r.valid[c.key] === false)
      return `<td class="num na" title="Месяц не учитывается: больше 10 неполных дней или 5 подряд (полных дней: ${r.days[c.key]} из ${r.of})">н/п</td>`;
    if (r.kind === 'decade' && v !== null && r.days && r.days[c.key] < 7) return cell(c, v).replace('<td class="num">', `<td class="num warnc" title="Полных дней в декаде: ${r.days[c.key]}">`);
    return cell(c, v);
  }).join('')}${mode === 'daily' ? '<td></td>' : ''}</tr>`;
  let body = '';
  if (mode === 'daily') {
    const dec = t.summary.filter(s => s.kind === 'decade'), month = t.summary.find(s => s.kind === 'month');
    t.rows.forEach((r, k) => {
      body += `<tr class="${r.complete ? '' : 'inc'}" ${r.complete ? '' : `title="Неполные сутки: ${r.obs} из ${r.of_obs} — в расчёт не входят"`}>
        <td class="first">${r.label}</td>${cols.map(c => cell(c, r.values[c.key])).join('')}
        <td class="num obs">${r.obs ? r.obs : '—'}${r.complete ? '' : ' <span class="badge">неполные</span>'}</td></tr>`;
      const last = k === t.rows.length - 1 || t.rows[k + 1].decade !== r.decade;
      if (last) body += sumRow(dec[r.decade - 1], 'dec');
    });
    body += sumRow(month, 'mon', 'Среднее за месяц');
    if (Object.keys(t.extremes).length) body += `<tr class="dec"><td class="first">Экстремум за месяц</td>${cols.map(c => {
      const e = t.extremes[c.key];
      return e ? `<td class="num">${fmt(e.value, 1)}<small> ${e.date}</small></td>` : '<td></td>';
    }).join('')}<td></td></tr>`;
  } else {
    const span = cols.length + 1;
    for (const b of t.blocks) {
      const empty = b.rows.every(r => cols.every(c => r.values[c.key] === null));
      if (empty) { body += `<tr class="mh nod"><td class="first">${esc(b.label)}</td><td colspan="${span - 1}" class="na">нет данных</td></tr>`; continue; }
      body += `<tr class="mh"><td class="first" colspan="${span}">${esc(b.label)}</td></tr>`;
      b.rows.forEach(r => { body += r.kind === 'month' ? sumRow(r, 'mon', r.partial ? r.label : 'За месяц') : sumRow(r, 'decr', `${r.label} (${r.start_day}–${r.end_day})`); });
    }
    const tot = t.total;
    body += `<tr class="tot"><td class="first">${tot.label}</td>${cols.map(c => {
      const v = tot.values[c.key];
      return v === null ? `<td class="num na" title="Валидных месяцев: ${tot.valid_months[c.key]} из ${tot.months}${tot.label === 'Год' ? ' — годовое значение только при всех 12' : ''}">н/п</td>` : cell(c, v);
    }).join('')}</tr>`;
  }
  const sub = mode === 'daily'
    ? `Полных суток: ${t.complete_days} из ${t.days}. Сутки полные, если ${t.rule}. Флаги качества исключены.`
    : 'По каждому месяцу — три декады (1–10, 11–20, 21–конец) и месяц. «н/п» — месяц неполный: больше 10 неполных дней или 5 подряд.';
  main.innerHTML = `<div class="box panel tables">
    <div class="filehead"><b>${esc(S.station)}: ${esc(mode === 'daily' ? t.title : periodLabel())}</b>
      <span class="hint2">${mode === 'daily' ? 'суточная таблица' : 'декадная таблица'}</span><span class="sp"></span>
      <a class="btn" href="${apiUrl('/api/table.xlsx', { mode })}">${ICON.dl}Скачать Excel</a></div>
    <p class="note" style="padding-top:2px">${esc(sub)}${mode === 'daily' ? '' : ' Для суточной таблицы выберите «Месяц».'}</p>
    <div class="tablewrap dtwrap"><table class="t dt">${head}<tbody>${body}</tbody></table></div></div>${reviewHtml(review)}`;
}
const RUMB = ['С', 'ССВ', 'СВ', 'ВСВ', 'В', 'ВЮВ', 'ЮВ', 'ЮЮВ', 'Ю', 'ЮЮЗ', 'ЮЗ', 'ЗЮЗ', 'З', 'ЗСЗ', 'СЗ', 'ССЗ'];

// ------------------------------------------------------------------ ТРЕНДЫ
async function renderTrends(main) {
  const scopeOpts = SCOPES;
  const sc = UI.trendScope;
  const params = { var: S.var, start: '', end: '' };
  if (sc.startsWith('s')) params.season = sc.slice(1);
  if (sc.startsWith('m')) params.month = sc.slice(1);
  const t = await api('/api/trends', params, false);
  const v = varById(S.var);
  const scopeName = (scopeOpts.find(o => o[0] === sc) || scopeOpts[0])[1].toLowerCase();
  main.innerHTML = `
  ${t.stripes ? `<div class="box panel"><div class="tb">${dlBtn('ch-stripes', 'полосы')}</div>
    ${panelTitle('Полосы потепления', `Отклонение годовой температуры от среднего ${t.stripes.base_label}`, 'Синие полосы показывают более холодные годы, красные — более тёплые.', 'static')}
    <div class="chart stripes" id="ch-stripes"></div></div>` : ''}
  <div class="grid21 mt">
    <div class="box panel"><div class="tb">${dlBtn('ch-trend', 'тренд')}</div>
      ${panelTitle(v.label, `${cap(scopeName)}, среднее за каждый год${v.sum ? ' (сумма)' : ''}`, 'Пунктир — линейный тренд, серая линия — скользящее среднее за 5 лет. Значимость тренда проверяется тестом Манна–Кендалла.')}
      <div class="chart" id="ch-trend"></div>
      <div class="legend"><span class="chip year">${esc(v.label)}</span><span class="chip norm">Линейный тренд</span><span class="chip others">Среднее за 5 лет</span></div>
    </div>
    <div class="box panel"><div class="head"><b>Как меняется климат</b><span>${esc(scopeName)}, весь ряд</span></div>
      <div class="tablewrap"><table class="t"><thead><tr><th>Параметр</th><th class="num">За 10 лет</th><th>Вывод</th></tr></thead><tbody>
      ${t.table.map(r => `<tr class="${r.var === S.var ? 'sel' : ''}" data-var="${r.var}" style="cursor:pointer"><td>${esc(r.label)}</td>
        <td class="num">${fmt(r.slope, 2, true)} ${esc(r.unit)}</td>
        <td><span class="verdict ${r.significant ? (r.slope > 0 ? 'up' : 'down') : ''}">${esc(r.verdict)}</span></td></tr>`).join('')}
      </tbody></table></div>
      <p class="note">«Растёт» и «снижается» — только если изменение статистически значимо (тест Манна–Кендалла, p < 0,05). Наклон — оценка Сена, устойчивая к выбросам.</p>
    </div>
  </div>`;
  if (t.stripes) {
    const a = t.stripes.anomaly, lim = Math.max(...a.filter(x => x !== null).map(Math.abs)) || 1;
    const yrs = t.stripes.years;
    const sel = plot('ch-stripes', [{ type: 'heatmap', z: [a], x: yrs, y: [0], zmin: -lim, zmax: lim, zmid: 0, colorscale: DIVERGE, showscale: false,
      hoverinfo: 'none' }], { margin: { l: 16, r: 16, t: 70, b: 26 }, hovermode: 'closest',
      xaxis: { showgrid: false, linecolor: 'rgba(0,0,0,0)', dtick: 5 }, yaxis: { visible: false } });
    const ranked = a.filter(x => x !== null).sort((p, q) => q - p);
    cellFocus(sel, pt => `<b>${pt.x}</b>   ${fmt(pt.z, 2, true)} °C к среднему<br><span style="color:${C.ink2}">${ranked.indexOf(pt.z) + 1}-й по теплу из ${ranked.length}</span>`,
      pt => [pt.x - .5, pt.x + .5, -.5, .5]);
  }
  const s = t.series;
  const tr = [{ x: s.years, y: s.values, mode: 'lines+markers', name: v.label, line: { color: C.accent, width: 2 }, marker: { size: 5, color: C.accent },
    hoverinfo: 'none' },
  { x: s.years, y: s.smooth, mode: 'lines', name: 'Среднее за 5 лет', line: { color: C.other, width: 2 }, hoverinfo: 'none' }];
  if (s.ols_slope !== null) {
    const x0 = s.years.length ? s.years[0] : 0;
    tr.push({ x: s.years, y: s.years.map(yy => s.intercept + s.ols_slope / 10 * (yy - x0)), mode: 'lines', name: 'Тренд',
      line: { color: C.ink, dash: 'dash', width: 1.8 }, hoverinfo: 'none' });
  }
  const hasTrend = tr.length === 3;
  tr.push(dotTrace());
  const tel = plot('ch-trend', tr, { hovermode: 'x', xaxis: { dtick: 5 }, yaxis: { ticksuffix: ' ' + v.unit },
    annotations: s.sen === null ? [] : [{ xref: 'paper', yref: 'paper', x: 1, y: 1.0, xanchor: 'right', yanchor: 'bottom', showarrow: false,
      text: `${fmt(s.sen, 2, true)} ${esc(v.unit)} за 10 лет, ${s.p < .05 ? 'значимо' : 'незначимо'}`, font: { size: 13, color: s.p < .05 ? C.accent : C.ink2 } }] });
  followDot(tel, [0], tr.length - 1, pts => {
    const p0 = pts[0], i = p0.pointNumber;
    const trendV = hasTrend ? tel.data[2].y[i] : null, sm = s.smooth[i];
    return `<b>${p0.x}</b>   ${fmt(p0.y)} ${v.unit}` +
      (trendV !== null ? `<br><span style="color:${C.ink2}">линия тренда ${fmt(trendV)}, ${fmt(p0.y - trendV, 1, true)}</span>` : '') +
      (sm !== null && sm !== undefined ? `<br><span style="color:${C.ink2}">среднее за 5 лет ${fmt(sm)}</span>` : '');
  });
  $$('tr[data-var]', main).forEach(tr => tr.addEventListener('click', () => { S.var = tr.dataset.var; syncControls(); route(); }));
}

// ------------------------------------------------------------------ СРАВНЕНИЕ
function ensureCmp() {
  const [lo, hi] = dataRange();
  const inRange = c => c && [c.a0, c.a1, c.b0, c.b1].every(d => parseISO(d) >= lo && parseISO(d) <= hi);
  if (inRange(UI.cmp)) return;
  const y0 = lo.getFullYear(), y1 = hi.getFullYear(), span = y1 - y0 + 1;
  UI.cmp = span >= 20 ? { a0: `${Math.max(y0, 1991)}-01-01`, a1: `${Math.max(y0, 1991) + 14}-12-31`, b0: `${y1 - 14}-01-01`, b1: `${y1}-12-31` }
    : { a0: iso(lo), a1: iso(new Date(lo.getFullYear(), 11, 31)), b0: iso(new Date(y1, 0, 1)), b1: iso(hi) };
}

async function renderCompare(main) {
  ensureCmp();
  const c = UI.cmp;
  const r = await api('/api/compare', { var: S.var, ...c }, false);
  const v = varById(S.var);
  main.innerHTML = `
  <div class="grid2">
    <div class="box panel"><div class="tb">${dlBtn('ch-cycle', 'годовой ход')}</div>
      ${panelTitle('Годовой ход: ' + v.label.toLowerCase(), `A: ${r.a_label}, B: ${r.b_label}`, 'Среднее значение каждого месяца в периоде A и B. Столбики снизу — разница B − A.')}
      <div class="chart" id="ch-cycle"></div>
      <div class="legend"><span class="chip others">A: ${esc(r.a_label)}</span><span class="chip year">B: ${esc(r.b_label)}</span></div>
    </div>
    <div class="box panel"><div class="head"><b>Разница между периодами</b><span>сравниваются ${esc(r.unit)}</span></div>
      ${r.rows.length ? `<div class="tablewrap"><table class="t"><thead><tr><th>Параметр</th><th class="num">A</th><th class="num">B</th><th class="num">B − A</th><th>Вывод</th></tr></thead><tbody>
      ${r.rows.map(x => `<tr class="${x.var === S.var ? 'sel' : ''}" data-var="${x.var}" style="cursor:pointer"><td>${esc(x.label)}</td><td class="num">${fmt(x.a)}</td><td class="num">${fmt(x.b)}</td>
        <td class="num">${fmt(x.diff, 2, true)}${x.pct !== null ? ` (${fmt(x.pct, 0, true)} %)` : ''}</td>
        <td><span class="verdict ${x.significant ? (x.diff > 0 ? 'up' : 'down') : ''}">${esc(x.significant ? (x.diff > 0 ? 'выше в B' : 'ниже в B') : 'разница незначима')}</span></td></tr>`).join('')}
      </tbody></table></div>
      <p class="note">Значимость — тест Манна–Уитни, p < 0,05. Сравниваются ${esc(r.unit)}, а не сутки: соседние дни похожи друг на друга, и по ним почти любая разница выглядела бы значимой.</p>`
      : '<div class="empty">Периоды слишком короткие: нужно хотя бы 3 недели или 3 года в каждом.</div>'}
    </div>
  </div>`;
  const tr = [];
  const x = MSHORT;
  if (v.sum) {
    tr.push({ type: 'bar', x, y: r.cycle.a, name: 'A', marker: { color: '#BFC5C2' }, hovertemplate: `A: %{y:.1f} ${v.unit}<extra></extra>` },
      { type: 'bar', x, y: r.cycle.b, name: 'B', marker: { color: C.accent }, hovertemplate: `B: %{y:.1f} ${v.unit}<extra></extra>` });
  } else {
    tr.push({ x, y: r.cycle.a, mode: 'lines+markers', name: 'A', line: { color: C.other, width: 2.4 }, marker: { size: 6 }, hovertemplate: `A: %{y:.1f} ${v.unit}<extra></extra>` },
      { x, y: r.cycle.b, mode: 'lines+markers', name: 'B', line: { color: C.accent, width: 2.4 }, marker: { size: 6 }, hovertemplate: `B: %{y:.1f} ${v.unit}<extra></extra>` });
  }
  const diff = r.cycle.b.map((b, k) => b === null || r.cycle.a[k] === null ? null : parameterDifference(b, r.cycle.a[k], v));
  tr.push({ type: 'bar', x, y: diff, yaxis: 'y2', name: 'B − A', marker: { color: diff.map(d => d >= 0 ? (v.sum ? C.wet : C.warm) : (v.sum ? C.dry : C.cold)) },
    opacity: .55, hovertemplate: `B − A: %{y:+.2f} ${v.unit}<extra></extra>` });
  tr.forEach(t => { t.hoverinfo = 'none'; delete t.hovertemplate; });
  tr.push(dotTrace());
  const cel = plot('ch-cycle', tr, { barmode: 'group', hovermode: 'x', yaxis: { domain: [.3, 1], ticksuffix: ' ' + v.unit },
    yaxis2: { ...AX, domain: [0, .22], zeroline: true, zerolinecolor: C.ink3 }, xaxis: { anchor: 'y2' } });
  const cycleTip = pts => {
    const k = MSHORT.indexOf(pts[0].x), a = r.cycle.a[k], b = r.cycle.b[k];
    return `<b>${cap(MONTHS[k])}</b><br><span style="color:${C.ink2}">A</span> ${fmt(a)} ${v.unit} (${yearsLabel(r.cycle.years_a[k])})<br><span style="color:${C.accent}">B</span> ${fmt(b)} ${v.unit} (${yearsLabel(r.cycle.years_b[k])})` +
      (a !== null && b !== null ? `<br>разница ${fmt(diff[k], 2, true)}` : '');
  };
  if (v.sum) barFocus(cel, 1, pt => cycleTip([pt]));
  else followDot(cel, [0, 1], tr.length - 1, cycleTip);
  $$('tr[data-var]', main).forEach(t => t.addEventListener('click', () => { S.var = t.dataset.var; syncControls(); route(); }));
}

// ------------------------------------------------------------------ СТАНЦИИ
const PALETTE = [C.accent, '#B0731B', '#2F6DB5', '#8C5BA8', '#C8323C', '#1F8A86', '#6B6B6B'];
const CA = C.accent, CB = '#B0731B';
/* Две станции за один период: по умолчанию Кр1 «поляна» и Кр2 «поле».
   Месячные средние (только валидные месяцы обеих), разница A − B и средний суточный ход по общим полным дням. */
async function renderStations(main) {
  const r = await api('/api/pair', { var: S.var, a: UI.pa || '', b: UI.pb || '' });
  UI.pa = r.a; UI.pb = r.b;
  const v = r.var, u = v.unit;
  const pick = (id, cur, color) => `<label class="pill"><span class="tag" style="background:${color}"></span><select id="${id}" class="bare">${r.stations.map(n => `<option ${n === cur ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select></label>`;
  const head = `<div class="tb">${pick('pa', r.a, CA)}<span class="vs">и</span>${pick('pb', r.b, CB)}<span class="sp"></span>
    ${r.common_days ? `<span class="pairsum">${pairSentence(r)}</span>` : ''}</div>`;
  if (!r.common_days) {
    main.innerHTML = `<div class="box panel">${head}<div class="empty"><b>Нет общих полных дней</b>У станций «${esc(r.a)}» и «${esc(r.b)}» в выбранном периоде нет дней, когда обе работали полностью. АМЕ-60 и Сокол М1 — разные приборы без периода совместной работы, по заданию их ряды не сравниваются и не объединяются.</div></div>`;
    bindPair(); return;
  }
  const mx = r.months.x;
  main.innerHTML = `<div class="box panel">${head}</div>
  <div class="grid2 mt">
    <div class="box panel"><div class="tb">${dlBtn('ch-pm', 'по месяцам')}</div>
      ${panelTitle(`${v.label}: по месяцам`, `${periodLabel()}, столбики — разница`, 'Месячные средние двух станций. Учитываются только месяцы, валидные на обеих (не более 10 неполных дней и нет 5 подряд). Столбики внизу — разница A − B.')}
      <div class="chart" id="ch-pm"></div>
      <div class="legend"><span class="chip" style="border-color:${CA};color:${CA}">${esc(r.a)}</span><span class="chip" style="border-color:${CB};color:${CB}">${esc(r.b)}</span></div></div>
    <div class="box panel"><div class="tb">${dlBtn('ch-pd', 'суточный ход')}</div>
      ${panelTitle('Средний суточный ход', `по ${r.common_days} ${plural(r.common_days, ['общему дню', 'общим дням', 'общим дням'])}, время станции`, 'Среднее значение для каждого часа суток. Берутся только дни, полные на обеих станциях, и только валидные значения. Время — как записано на станциях, без сдвига.')}
      <div class="chart" id="ch-pd"></div>
      <div class="legend"><span class="chip" style="border-color:${CA};color:${CA}">${esc(r.a)}</span><span class="chip" style="border-color:${CB};color:${CB}">${esc(r.b)}</span></div></div>
  </div>
  <div class="box panel mt"><div class="head"><b>По месяцам</b><span>${esc(v.label.toLowerCase())}, ${esc(u)}</span></div>
    <div class="tablewrap"><table class="t"><thead><tr><th>Месяц</th><th class="num">${esc(r.a)}</th><th class="num">${esc(r.b)}</th><th class="num">Разница</th></tr></thead><tbody>
    ${mx.map((x, k) => { const d = parseISO(x); const a = r.months.a[k], b = r.months.b[k], df = r.months.diff[k];
      return `<tr><td>${cap(MONTHS[d.getMonth()])} ${d.getFullYear()}</td><td class="num">${a === null ? '<span class="na">н/п</span>' : fmt(a)}</td><td class="num">${b === null ? '<span class="na">н/п</span>' : fmt(b)}</td>
      <td class="num">${df === null ? '—' : `<b class="${df > 0 ? 'warm' : df < 0 ? 'cold' : ''}">${fmt(df, 2, true)}</b>`}</td></tr>`; }).join('')}
    </tbody></table></div><p class="note">«н/п» — месяц неполный на этой станции. Разница — только для месяцев, валидных на обеих. Высота датчиков в исходном наборе не указана.</p></div>`;
  if (r.suspicious.length) {
    const items = r.suspicious.map(x => { const d = parseISO(x.month); return `${MONTHS[d.getMonth()]} ${d.getFullYear()} (${fmt(x.diff, 1, true)} ${esc(u)} при обычных ${fmt(x.typical, 1, true)})`; });
    main.insertAdjacentHTML('afterbegin', `<div class="box alert"><b>Возможен сбой датчика.</b> Разница между станциями резко выбивается из обычной: ${items.join('; ')}.
      Соседние станции меняются синхронно, поэтому такой скачок больше похож на ошибку прибора, чем на погоду. Значения при этом в допустимом диапазоне и флагом не отмечены — стоит проверить их вручную.</div>`);
  }
  const diffBar = (x, d, ax) => ({ type: 'bar', x, y: d, yaxis: ax, hoverinfo: 'none', opacity: .55,
    marker: { color: d.map(z => z === null ? 'rgba(0,0,0,0)' : z >= 0 ? C.warm : C.cold) } });
  const mLabels = mx.map(x => { const d = parseISO(x); return `${MSHORT[d.getMonth()]} ${String(d.getFullYear()).slice(2)}`; });
  const lay2 = { hovermode: 'x', yaxis: { domain: [.32, 1], ticksuffix: ' ' + u }, yaxis2: { ...AX, domain: [0, .24], zeroline: true, zerolinecolor: C.ink3 }, xaxis: { anchor: 'y2' } };
  const pm = plot('ch-pm', [
    { x: mLabels, y: r.months.a, mode: 'lines+markers', line: { color: CA, width: 2.4 }, marker: { size: 6 }, hoverinfo: 'none', connectgaps: false },
    { x: mLabels, y: r.months.b, mode: 'lines+markers', line: { color: CB, width: 2.4 }, marker: { size: 6 }, hoverinfo: 'none', connectgaps: false },
    diffBar(mLabels, r.months.diff, 'y2'), dotTrace()], lay2);
  followDot(pm, [0, 1], 3, pts => { const k = pts[0].pointNumber;
    return `<b>${mLabels[k]}</b><br><span style="color:${CA}">${esc(r.a)}</span> ${fmt(r.months.a[k])} ${u}<br><span style="color:${CB}">${esc(r.b)}</span> ${fmt(r.months.b[k])} ${u}` +
      (r.months.diff[k] !== null ? `<br>разница ${fmt(r.months.diff[k], 2, true)}` : ''); });
  const hrs = r.diurnal.hours.map(h => `${String(h).padStart(2, '0')}:00`);
  const dd = r.diurnal.a.map((a, k) => a === null || r.diurnal.b[k] === null ? null : parameterDifference(a, r.diurnal.b[k], v));
  const pd = plot('ch-pd', [
    { x: hrs, y: r.diurnal.a, mode: 'lines+markers', line: { color: CA, width: 2.4, shape: 'spline' }, marker: { size: 5 }, hoverinfo: 'none' },
    { x: hrs, y: r.diurnal.b, mode: 'lines+markers', line: { color: CB, width: 2.4, shape: 'spline' }, marker: { size: 5 }, hoverinfo: 'none' },
    diffBar(hrs, dd, 'y2'), dotTrace()], { ...lay2, xaxis: { anchor: 'y2', dtick: 3 } });
  followDot(pd, [0, 1], 3, pts => { const k = pts[0].pointNumber;
    return `<b>${hrs[k]}</b><br><span style="color:${CA}">${esc(r.a)}</span> ${fmt(r.diurnal.a[k])} ${u}<br><span style="color:${CB}">${esc(r.b)}</span> ${fmt(r.diurnal.b[k])} ${u}` +
      (dd[k] !== null ? `<br>разница ${fmt(dd[k], 2, true)}` : ''); });
  bindPair();
}
function pairSentence(r) {
  const d = r.mean_diff, u = r.var.unit;
  if (d === null) return '';
  if (r.var.id === 'wind_dir') return `Средняя угловая разница: ${fmt(d, 2, true)}° по ${r.common_days} общим дням.`;
  const word = Math.abs(d) < 0.05 ? 'практически не отличается от' : (d > 0 ? 'выше, чем на' : 'ниже, чем на');
  return `В среднем на «${esc(r.a)}» ${esc(r.var.label.toLowerCase())} ${word} «${esc(r.b)}»${Math.abs(d) < 0.05 ? '' : ` — на ${fmt(Math.abs(d), 2)} ${esc(u)}`}; выше в ${r.days_a_higher} днях из ${r.common_days}.`;
}
function bindPair() {
  $('#pa')?.addEventListener('change', e => { UI.pa = e.target.value; if (UI.pa === UI.pb) UI.pb = ''; route(); });
  $('#pb')?.addEventListener('change', e => { UI.pb = e.target.value; route(); });
}

// ------------------------------------------------------------------ АГРОКЛИМАТ
/* Агропоказатели одного года — компактно, по смысловым группам */
function agroFacts(row) {
  const has = c => row[c] !== undefined && row[c] !== null;
  const n0 = (c, d = 0) => has(c) ? fmt(row[c], d) : '—';
  const dt = c => has(c) ? ruDate(row[c], false) : '—';
  const item = (value, label, unit = '') => `<div class="ag-item"><b>${esc(value)}${unit ? ` <small>${esc(unit)}</small>` : ''}</b><span>${esc(label)}</span></div>`;
  const g = row['ГТК Селянинова'];
  const gtk = g === undefined || g === null ? '' : g < 1 ? 'засушливо' : g <= 1.5 ? 'достаточно влажно' : 'избыточно влажно';
  const trans = [0, 5, 10].map(t => `<tr><td>через ${t} °C</td><td>${dt(`Переход через ${t} °C весной`)}</td><td>${dt(`Переход через ${t} °C осенью`)}</td></tr>`).join('');
  return `<div class="ag-grid">
    <section><h4>Тепло</h4>
      ${item(n0('Σ активных T > 10 °C'), 'сумма активных температур > 10 °C', '°C')}
      ${item(n0('Σ эффективных T > 5 °C'), 'сумма эффективных температур > 5 °C', '°C')}
      ${item(n0('Вегетационный период > 10 °C, дней'), 'вегетационный период > 10 °C', 'дн.')}
      ${item(has('ГТК Селянинова') ? fmt(g, 2) : '—', `ГТК Селянинова${gtk ? ', ' + gtk : ''}`)}</section>
    <section><h4>Устойчивый переход</h4>
      <table class="ag-t"><thead><tr><th></th><th>весной</th><th>осенью</th></tr></thead><tbody>${trans}</tbody></table></section>
    <section><h4>Заморозки</h4>
      ${item(dt('Последний весенний заморозок'), 'последний весенний')}
      ${item(dt('Первый осенний заморозок'), 'первый осенний')}
      ${item(n0('Безморозный период, дней'), 'безморозный период', 'дн.')}</section>
    <section><h4>Осадки${has('Макс. высота снега, см') ? ' и снег' : ''}</h4>
      ${item(n0('Осадки за год, мм'), 'за год', 'мм')}
      ${item(n0('Осадки май–сентябрь, мм'), 'за май–сентябрь', 'мм')}
      ${has('Макс. высота снега, см') ? item(n0('Макс. высота снега, см'), 'макс. высота снега', 'см') : ''}</section>
  </div>`;
}
async function renderAgro(main) {
  const r = await api('/api/agro');
  const num = r.columns.filter(c => r.rows.some(x => typeof x[c] === 'number'));
  if (!UI.agroPick || !num.includes(UI.agroPick)) UI.agroPick = num[0] || '';
  const single = r.rows.length === 1;   // один год: столбик не нужен — показываем значения крупно
  const ev = r.events;
  const kinds = { 'Волна жары': C.warm, 'Волна холода': C.cold, 'Засушливый период': C.dry, 'Сильные осадки': C.wet };
  main.innerHTML = `
  <div class="grid21">
    ${single ? `<div class="box facts"><h3>Агроклимат ${r.rows[0].year} года</h3>${agroFacts(r.rows[0])}
      <p class="note" style="padding:10px 0 0">Динамика по годам — выберите период в несколько лет («Период» в строке управления).</p></div>`
    : `<div class="box panel"><div class="tb">${dlBtn('ch-agro', 'агро')}<span class="sp"></span>
      ${num.length ? `<label class="pill">Показатель <select id="agro-pick" style="border:0;background:none;outline:none;max-width:320px">${num.map(c => `<option ${c === UI.agroPick ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></label>` : ''}</div>
      ${panelTitle(UI.agroPick || 'Агроклимат', S.kind === 'year' || S.kind === 'custom' ? `по годам, ${periodLabel()}` : `за ${r.rows.length ? r.rows[0].year : ''} год`, 'Сумма активных температур — сумма среднесуточных температур выше 10 °C. ГТК Селянинова: меньше 1 — засушливо, 1–1,5 — достаточно влажно, больше 1,5 — избыточно.')}
      ${num.length ? '<div class="chart" id="ch-agro"></div>' : '<div class="empty" style="padding-top:90px">Нет полных лет с температурой в выбранном периоде.</div>'}</div>`}
    ${r.wind.available ? `<div class="box panel"><div class="tb">${dlBtn('ch-wind', 'роза ветров')}</div>
      ${panelTitle('Роза ветров', `повторяемость направлений, штиль ${fmt(r.wind.calm)} %`, r.wind.from_daily ? 'Построено по суточным данным (преобладающее направление за сутки). По срочным наблюдениям роза точнее.' : '')}
      <div class="chart" id="ch-wind"></div></div>` : `<div class="box panel"><div class="head"><b>Роза ветров</b></div><div class="note">В данных станции нет направления ветра.</div></div>`}
  </div>
  <div class="box panel mt"><div class="tb">${dlBtn('ch-ev', 'явления')}</div>
    ${panelTitle('Опасные и неблагоприятные явления', `${ev.length} ${plural(ev.length, ['событие', 'события', 'событий'])}, ${periodLabel()}`, 'Волна жары — 5 и более дней с максимумом от 25 °C; волна холода — 3 и более дня с минимумом до −25 °C; засушливый период — 15 и более дней без осадков от 1 мм в мае–сентябре; сильные осадки — от 20 мм за сутки.')}
    <div class="chart short" id="ch-ev"></div>
    <div class="legend">${Object.entries(kinds).map(([k, col]) => `<span class="chip" style="border-color:${col};color:${col}">${k}</span>`).join('')}</div>
  </div>
  ${single ? '' : `<div class="box panel mt"><div class="head"><b>Показатели по годам</b></div><div class="tablewrap"><table class="t"><thead><tr><th>Год</th>${r.columns.map(c => `<th class="num">${esc(c)}</th>`).join('')}</tr></thead><tbody>
    ${r.rows.slice().reverse().map(x => `<tr><td>${x.year}</td>${r.columns.map(c => `<td class="num">${typeof x[c] === 'number' ? fmt(x[c], Math.abs(x[c]) >= 100 ? 0 : x[c] % 1 ? 2 : 0) : x[c] ? ruDate(x[c], false) : '—'}</td>`).join('')}</tr>`).join('')}
  </tbody></table></div></div>`}`;
  if (UI.agroPick && !single) {
    const xs = r.rows.map(x => x.year), ys = r.rows.map(x => x[UI.agroPick]);
    const vals = ys.filter(x => typeof x === 'number'), mean = vals.reduce((a, b) => a + b, 0) / (vals.length || 1);
    const agEl = plot('ch-agro', [{ type: 'bar', x: xs, y: ys, marker: { color: C.accent }, hoverinfo: 'none' }],
      { hovermode: 'x', bargap: .3, xaxis: { dtick: xs.length > 15 ? 5 : 1 }, yaxis: { range: rankRange(ys) },
        shapes: vals.length > 2 ? [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: mean, y1: mean, line: { color: C.ink, width: 1.5, dash: 'dash' } }] : [] });
    barFocus(agEl, 0, pt => `<b>${pt.x}</b>   ${fmt(pt.y, Math.abs(pt.y) >= 100 ? 0 : 2)}` +
      (vals.length > 2 ? `<br><span style="color:${C.ink2}">${fmt(pt.y - mean, Math.abs(mean) >= 100 ? 0 : 2, true)} к среднему</span>` : ''));
  }
  if (r.wind.available) {
    const cols = ['#C9DCCD', '#8DB898', '#4F8C60', C.accent, C.accent2, '#0F2A17'];
    plot('ch-wind', r.wind.classes.map((c, k) => ({ type: 'barpolar', r: r.wind.values[c], theta: r.wind.sectors, name: c, marker: { color: cols[k % 6] },
      hoverinfo: 'none' })),
    { showlegend: true, legend: { orientation: 'h', y: -.05, font: { size: 12 } }, hovermode: 'closest', margin: { l: 30, r: 30, t: 80, b: 40 },
      polar: { bgcolor: 'rgba(0,0,0,0)', angularaxis: { direction: 'clockwise', rotation: 90, gridcolor: C.grid, linecolor: C.line },
        radialaxis: { gridcolor: C.grid, ticksuffix: ' %', angle: 90, tickfont: { size: 11 } } } });
    // наведённый румб подсвечивается во всех градациях скорости, остальные бледнеют
    const wEl = $('#ch-wind'), nCls = r.wind.classes.length, nSec = r.wind.sectors.length;
    onHover(wEl, ev => {
      const k = ev.points[0].pointNumber;
      Plotly.restyle(wEl, { 'marker.opacity': [...Array(nCls)].map(() => [...Array(nSec)].map((_, i) => i === k ? 1 : .2)) });
      const tot = r.wind.classes.reduce((a, c) => a + (r.wind.values[c][k] || 0), 0);
      showTipXY(wEl, 16, 70, `<b>${r.wind.sectors[k]}</b>   ${fmt(tot)} % времени` + r.wind.classes.map(c => `<br><span style="color:${C.ink2}">${esc(c)}</span>  ${fmt(r.wind.values[c][k])} %`).join(''), { below: true });
    }, () => { Plotly.restyle(wEl, { 'marker.opacity': 1 }); hideTip(); });
  }
  plot('ch-ev', Object.entries(kinds).map(([k, col]) => {
    const e = ev.filter(x => x['Явление'] === k);
    return { x: e.map(x => x['Начало']), y: e.map(() => k), mode: 'markers', name: k, marker: { color: col, size: e.map(x => Math.min(28, 7 + x['Дней'] * 1.4)), opacity: .75 },
      customdata: e.map(x => [x['Дней'], x['Характеристика'].replace(/(\d)\.(\d)/g, '$1,$2')]), hovertemplate: '%{x|%d.%m.%Y}: %{customdata[0]} дн., %{customdata[1]}<extra></extra>' };
  }), { hovermode: 'closest', margin: { l: 130, r: 16, t: 76, b: 30 }, xaxis: { type: 'date' }, yaxis: { showgrid: false } });
  $('#agro-pick')?.addEventListener('change', e => { UI.agroPick = e.target.value; route(); });
}

// ------------------------------------------------------------------ ПРОГНОЗ
const WD = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
async function renderForecast(main) {
  const f = await api('/api/forecast', {}, false);
  const D = f.daily, H = f.hourly, ctx = f.context;
  const advice = f.recommendations || [];
  const cards = D.time.map((t, k) => {
    const d = parseISO(t), mean = D.temperature_mean[k], c = ctx.days[k];
    const dev = mean !== null && c && c.mean !== null ? mean - c.mean : null;
    return `<div class="box fday">
      <div class="fd">${k === 0 ? 'Сегодня' : cap(WD[d.getDay()])}, ${d.getDate()} ${MONTHS_GEN[d.getMonth()]}</div>
      <div class="ft"><b>${fmt(D.temperature_2m_max[k], 0)}°</b><span>${fmt(D.temperature_2m_min[k], 0)}°</span></div>
      <div class="fw">ветер до ${fmt(D.wind_speed_10m_max[k], 0)} м/с, порывы ${fmt(D.wind_gusts_10m_max[k], 0)}</div>
      ${D.precipitation_sum[k] > 0.2 ? `<div class="fw">осадки ${fmt(D.precipitation_sum[k], 1)} мм</div>` : '<div class="fw">без осадков</div>'}
      ${dev !== null ? `<div class="fdev ${dev > 1 ? 'warm' : dev < -1 ? 'cold' : ''}">${Math.abs(dev) < 1 ? 'близко к среднему' : `${dev > 0 ? 'теплее' : 'холоднее'} среднего на ${fmt(Math.abs(dev), 1)}°`} · ${yearsLabel(c.years)}</div>` : ''}
    </div>`;
  }).join('');
  main.innerHTML = `<section class="box panel forecast-advice" aria-labelledby="forecast-advice-title">
    <div class="head"><b id="forecast-advice-title">Важные рекомендации</b><span>Район заповедника</span></div>
    ${advice.length ? `<ul>${advice.map(r => `<li>
      <b>${esc(r.title)}</b><small>${esc(ruDate(r.start, false))}${r.end !== r.start ? ' — ' + esc(ruDate(r.end, false)) : ''}</small>
      <p>${esc(r.evidence)}</p><p>${esc(r.action)}</p>
    </li>`).join('')}</ul>` : '<p class="note">По доступным данным условий для предупреждений не выявлено.</p>'}
    <p class="note">Автоматические подсказки по модели Open-Meteo за последние 14 дней и прогнозу на неделю.</p>
  </section>
  <div class="fcards">${cards}</div>
  <div class="grid21 mt">
    <div class="box panel"><div class="tb">${dlBtn('ch-ft', 'прогноз')}</div>
      ${panelTitle('Температура воздуха по часам', 'прогноз на 7 дней и среднее за те же даты', `Пунктир — средняя суточная температура этих дат по станции «${ctx.station}» за выбранный период. В карточках указан вклад по годам для каждой даты. Прогноз получен из Open-Meteo.`)}
      <div class="chart" id="ch-ft"></div></div>
    <div class="box panel"><div class="tb">${dlBtn('ch-fw', 'ветер')}</div>
      ${panelTitle('Ветер', 'средняя скорость и порывы, м/с', '')}
      <div class="chart" id="ch-fw"></div></div>
  </div>
  <p class="note">Источник прогноза: ${esc(f.source)}, точка ${fmt(f.lat, 2)}° с. ш., ${fmt(f.lon, 2)}° в. д. (район заповедника). Обновляется раз в час.
    Среднее за те же даты — по станции «${esc(ctx.station)}»; её можно сменить в строке управления.</p>`;
  const ctxX = [], ctxY = [];
  D.time.forEach((t, k) => { const c = ctx.days[k]; ctxX.push(`${t}T00:00`, `${t}T23:59`); ctxY.push(c?.mean ?? null, c?.mean ?? null); });
  const ft = plot('ch-ft', [
    { x: H.time, y: H.temperature_2m, mode: 'lines', line: { color: C.accent, width: 2.4, shape: 'spline' }, hoverinfo: 'none' },
    { x: ctxX, y: ctxY, mode: 'lines', line: { color: C.ink, width: 1.6, dash: 'dash' }, hoverinfo: 'none' }, dotTrace()],
  { hovermode: 'x', xaxis: { type: 'date', tickformat: '%-d %b', dtick: 86400000 }, yaxis: { ticksuffix: ' °C' } });
  followDot(ft, [0], 2, pts => { const k = pts[0].pointNumber, t = H.time[k];
    return `<b>${ruDate(t.slice(0, 10), false)}, ${t.slice(11, 16)}</b><br>${fmt(H.temperature_2m[k])} °C<br><span style="color:${C.ink2}">влажность ${fmt(H.relative_humidity_2m[k], 0)} %, давление ${fmt(H.surface_pressure[k], 0)} гПа</span>`; });
  const fw = plot('ch-fw', [
    { type: 'bar', x: H.time, y: H.wind_gusts_10m, marker: { color: '#C9DCCD' }, hoverinfo: 'none' },
    { x: H.time, y: H.wind_speed_10m, mode: 'lines', line: { color: C.accent, width: 2 }, hoverinfo: 'none' }, dotTrace()],
  { hovermode: 'x', bargap: 0, xaxis: { type: 'date', tickformat: '%-d %b', dtick: 86400000 }, yaxis: { ticksuffix: ' м/с' } });
  followDot(fw, [1], 2, pts => { const k = pts[0].pointNumber, t = H.time[k];
    return `<b>${ruDate(t.slice(0, 10), false)}, ${t.slice(11, 16)}</b><br>ветер ${fmt(H.wind_speed_10m[k])} м/с, порывы ${fmt(H.wind_gusts_10m[k])}<br><span style="color:${C.ink2}">направление ${RUMB[Math.round(H.wind_direction_10m[k] / 22.5) % 16]}</span>`; });
}

// ------------------------------------------------------------------ ДАННЫЕ И ОТЧЁТЫ
function observationTime(value) {
  return esc(String(value).replace('T', ' ').slice(0, 16));
}

function reviewHtml(review) {
  const dateRange = row => observationTime(row['Время']) +
    (row['Конец'] === row['Время'] ? '' : ` — ${observationTime(row['Конец'])}`);
  const source = review.var.id === review.source.id ? '' : ` · по данным «${esc(review.source.label)}»`;
  return `<div class="box panel mt">
    <div class="head"><b>Проверка месячных расчётов</b><span>${esc(review.var.label)}, ${esc(review.var.unit)}${source}</span></div>
    <p class="note">Использованные измерения образуют суточные значения, из которых рассчитывается месячное. Контекстные предупреждения остаются в данных для проверки специалистом.</p>
    <div class="tablewrap"><table class="t"><thead><tr><th>Месяц</th><th class="num">Значение</th><th class="num">Полных дней</th>
      <th class="num">Использовано</th><th class="num">Исключено</th><th>Причины исключений</th></tr></thead><tbody>
      ${review.audit.map(m => {
        const causes = [];
        if (m['Неполные сутки']) causes.push(`неполные сутки × ${m['Неполные сутки']}`);
        if (m['Неполный месяц']) causes.push(`неполный месяц × ${m['Неполный месяц']}`);
        if (m['Причины при импорте']) causes.push(m['Причины при импорте']);
        return `<tr><td>${esc(m['Месяц'])}</td><td class="num">${fmt(m['Значение'])}</td>
          <td class="num">${m['Полных дней']} из ${m['Число дней в выборе']}</td><td class="num">${m['Использовано']}</td>
          <td class="num">${m['Исключено']}</td><td>${esc(causes.join(', ') || '—')}</td></tr>`;
      }).join('')}</tbody></table></div>
    <details class="note"><summary>Важные аномалии · ${review.important_total}</summary>
      ${review.important.length ? `<ul>${review.important.map(i => `<li>${dateRange(i)} · ${esc(i['Статус'])} · ${esc(i['Проблема'])} ·
        ${fmt(i['Значение'])} ${esc(review.source.unit)}. ${esc(i['Контекст'])}</li>`).join('')}</ul>`
        : '<p>За выбранный период важных предупреждений нет.</p>'}
      ${review.important_total > review.important.length ? '<p>Все эпизоды доступны в Excel.</p>' : ''}
    </details>
    <details class="note"><summary>Исключения при импорте · ${review.excluded_at_import}</summary>
      ${review.exclusions.length ? `<ul>${review.exclusions.map(i => `<li>${observationTime(i['Время'])} ·
        ${fmt(i['Значение'])} ${esc(review.source.unit)} · ${esc(i['Причина'])} · ${esc(i['Файл'])}</li>`).join('')}</ul>`
        : '<p>За выбранный период исключений при импорте нет.</p>'}
      ${review.excluded_at_import > review.exclusions.length ? '<p>Полный список доступен в Excel.</p>' : ''}
    </details>
  </div>`;
}

function recoveryPreviewHtml(preview) {
  const formatDate = value => ruDate(String(value).slice(0, 10));
  const candidates = preview.station_candidates;
  const donors = candidates.filter(c => c.gap_days_covered && c.overlap_days && preview.parameter === 't_air');
  return `
    <p><b>Дней без полного значения: ${preview.missing_days} из ${preview.days}</b>.
      Из них с неполными наблюдениями: ${preview.incomplete_days}.</p>
    ${preview.gaps.length ? `<div class="tablewrap"><table class="t"><thead><tr><th>Пропуск</th><th class="num">Дней</th></tr></thead><tbody>
      ${preview.gaps.slice(0, 8).map(g => `<tr><td>${formatDate(g.start)} — ${formatDate(g.end)}</td><td class="num">${g.days}</td></tr>`).join('')}
      </tbody></table></div>` : ''}
    ${preview.gaps.length > 8 ? '<p>Остальные интервалы войдут в черновик.</p>' : ''}
    ${candidates.length ? `<details><summary>Покрытие других станций</summary><div class="tablewrap"><table class="t"><thead><tr><th>Станция</th>
      <th class="num">Дней пропуска с данными</th><th class="num">Общих дней в архиве</th></tr></thead><tbody>
      ${candidates.map(c => `<tr><td>${esc(c.station)}</td><td class="num">${c.gap_days_covered}</td><td class="num">${c.overlap_days}</td></tr>`).join('')}
      </tbody></table></div></details>` : ''}
    <form id="recovery-form" class="recovery-form">
      <label>Источник<select id="recovery-source" class="field">
        <option value="archive">Летопись природы</option>
        ${donors.map(c => `<option value="station:${esc(c.station)}">${esc(c.station)}</option>`).join('')}
      </select></label>
      <div id="recovery-archive" class="recovery-form">
        <label>Шаг записи<select id="recovery-resolution" class="field"><option value="day">Сутки</option><option value="month">Месяц</option></select></label>
        <label>Том и страница или ссылка<input id="recovery-reference" class="field" maxlength="500"></label>
        <label>Дата и значение, по одной строке <textarea id="recovery-values" class="field" rows="3"></textarea></label>
      </div>
      <div class="actions">
        <button class="btn" type="submit">Предпросмотр восстановления</button>
        <button class="pill" id="recovery-draft" type="button">Скачать черновик JSON</button>
      </div>
    </form>
    <div id="recovery-proposal" aria-live="polite"></div>
    <details id="recovery-saved"><summary>Сохранённые значения</summary>
      <div class="recovery-form"><label>Шаг графика<select id="recovery-chart-step" class="field">
        <option value="day">Сутки</option><option value="month">Месяц</option></select></label></div>
      <div id="recovery-series"></div>
    </details>`;
}

function recoveryRowsHtml(rows, unit) {
  if (!rows.length) return '<p>За выбранный период нет значений.</p>';
  return `<div class="tablewrap"><table class="t"><thead><tr><th>Дата</th><th class="num">Значение, ${esc(unit)}</th>
    <th>Источник</th><th class="num">90% ошибок до</th></tr></thead><tbody>
    ${rows.slice(0, 30).map(r => `<tr><td>${esc(r.resolution === 'month' ? r.date.slice(0, 7) : r.date)}</td>
      <td class="num">${fmt(r.value, 2)}</td><td>${esc(r.source)}${r.has_observation ? ' · есть официальное значение' : ''}
      ${r.validation_days ? `<small> · проверено на ${r.validation_days} днях</small>` : ''}</td>
      <td class="num">${fmt(r.error90, 2)}</td></tr>`).join('')}
    </tbody></table></div>${rows.length > 30 ? `<p>Показано 30 из ${rows.length} значений.</p>` : ''}`;
}

async function inspectRecovery(panel, button) {
  const result = $('#recovery-result', panel);
  button.disabled = true;
  result.textContent = 'Проверяю покрытие…';
  try {
    const preview = await api('/api/recovery/preview', { var: S.var });
    result.innerHTML = recoveryPreviewHtml(preview);
    const find = id => $('#' + id, panel);
    const form = find('recovery-form');
    const source = find('recovery-source');
    const proposal = find('recovery-proposal');
    const step = find('recovery-resolution');
    const saved = find('recovery-saved');
    const invalidated = () => { proposal.replaceChildren(); };
    const updateExample = () => {
      const d = preview.start;
      find('recovery-values').placeholder = `${step.value === 'month' ? d.slice(0, 7) : d}; 5,2`;
    };
    updateExample();
    form.addEventListener('input', invalidated);
    source.addEventListener('change', () => { find('recovery-archive').hidden = source.value !== 'archive'; });
    step.addEventListener('change', updateExample);
    const selection = () => {
      const fromArchive = source.value === 'archive';
      return { dataset: preview.dataset, station: preview.station, parameter: preview.parameter,
        start: preview.start, end: preview.end, source: fromArchive ? 'archive' : 'station',
        resolution: fromArchive ? step.value : 'day',
        donor: fromArchive ? '' : source.value.slice('station:'.length),
        reference: find('recovery-reference').value.trim() };
    };
    find('recovery-draft').addEventListener('click', () => {
      const draft = { ...preview, status: 'draft', selection: selection() };
      const link = document.createElement('a');
      link.href = URL.createObjectURL(new Blob([JSON.stringify(draft, null, 2) + '\n'], { type: 'application/json' }));
      link.download = `восстановление-${preview.parameter}-${preview.start}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    });

    const refreshSaved = async () => {
      const display = find('recovery-series');
      const resolution = find('recovery-chart-step').value;
      display.textContent = 'Загружаю значения…';
      try {
        const data = await api('/api/recovery/saved', { var: preview.parameter, resolution });
        if (resolution !== find('recovery-chart-step').value) return;
        const rows = data.rows.filter(r => r.resolution === resolution);
        display.innerHTML = (data.rows.length ? `<a class="pill" href="${apiUrl('/api/recovery/report.xlsx')}">Скачать отдельный Excel</a>` : '')
          + (rows.length ? '<div id="recovery-chart" class="chart short"></div>' : '') + recoveryRowsHtml(rows, preview.unit);
        if (!rows.length) return;
        const observed = data.observations;
        const traces = [{ name: 'Наблюдения', x: observed.dates, y: observed.values, mode: 'lines+markers',
          marker: { size: 4, color: C.ink }, connectgaps: false }];
        for (const [method, name, color, symbol] of [['archive', 'Летопись', C.accent, 'diamond'],
                                                   ['station_offset', 'Оценка по станции', C.warm, 'triangle-up']]) {
          const sourceRows = rows.filter(r => r.method === method && !r.has_observation);
          if (!sourceRows.length) continue;
          traces.push({ name, x: sourceRows.map(r => r.date), y: sourceRows.map(r => r.value), mode: 'markers',
            marker: { size: 8, color, symbol }, text: sourceRows.map(r => esc(r.source)),
            hovertemplate: '%{x} · %{y}<br>%{text}<extra></extra>',
            ...(method === 'station_offset' ? { error_y: { type: 'data', array: sourceRows.map(r => r.error90) } } : {}) });
        }
        plot('recovery-chart', traces, { showlegend: true, legend: { orientation: 'h' }, yaxis: { ticksuffix: ' ' + preview.unit } });
      } catch (e) {
        if (e !== STALE) display.textContent = e.message;
      }
    };
    saved.addEventListener('toggle', () => { if (saved.open) refreshSaved(); });
    find('recovery-chart-step').addEventListener('change', refreshSaved);

    const post = async (payload, save = false) => {
      const response = await fetch('/api/recovery/proposal?save=' + save, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Проверьте формат дат и значений.');
      return data;
    };
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const payload = selection();
      proposal.textContent = 'Проверяю значения…';
      const draftState = proposal.textContent;
      try {
        if (payload.source === 'archive') {
          payload.entries = find('recovery-values').value.trim().split(/\n/).filter(line => line.trim()).map(line => {
            const parts = line.split(';').map(part => part.trim());
            const format = payload.resolution === 'month' ? /^\d{4}-\d{2}$/ : /^\d{4}-\d{2}-\d{2}$/;
            if (parts.length !== 2 || !format.test(parts[0]) || !/^[+-]?\d+(?:[.,]\d+)?$/.test(parts[1])) {
              throw new Error('Укажите дату и числовое значение через точку с запятой.');
            }
            return { date: parts[0] + (payload.resolution === 'month' ? '-01' : ''), value: Number(parts[1].replace(',', '.')) };
          });
        }
        const data = await post(payload);
        if (proposal.textContent !== draftState) return;
        proposal.innerHTML = `<p><b>Предложено значений: ${data.rows.length}</b></p>` + recoveryRowsHtml(data.rows, preview.unit)
          + data.skipped.map(r => `<p>Месяц ${r.month}: ${esc(r.reason)}</p>`).join('')
          + (data.rows.length ? '<button class="btn" id="recovery-apply" type="button">Сохранить в отдельный ряд</button>' : '');
        const apply = find('recovery-apply');
        apply?.addEventListener('click', async () => {
          apply.disabled = true;
          try {
            const accepted = await post({ ...payload, preview_token: data.preview_token }, true);
            proposal.textContent = `Добавлено значений: ${accepted.saved}. Повторяющиеся записи учтены один раз.`;
            if (saved.open) refreshSaved();
            else saved.open = true;
          } catch (error) {
            proposal.textContent = error.message;
          }
        });
      } catch (e) {
        proposal.textContent = e.message;
      }
    });
  } catch (e) {
    if (e !== STALE) result.textContent = e.message;
  } finally {
    button.disabled = false;
  }
}

function bindStationSources(main) {
  const form = $('#station-data-request', main);
  const status = $('#station-request-status', main);
  form.addEventListener('submit', event => {
    event.preventDefault();
    const { start, end } = form.elements;
    end.setCustomValidity(start.value > end.value ? 'Дата окончания должна быть не раньше даты начала.' : '');
    if (form.reportValidity()) status.textContent = 'Демо: форма заполнена. В рабочей версии запрос получит заповедник.';
  });
  form.addEventListener('input', () => {
    form.elements.end.setCustomValidity('');
    status.textContent = '';
  });
  const catalog = $('#public-station-catalog', main);
  const list = $('#public-station-list', catalog);
  async function loadCatalog() {
    if (!catalog.open || list.dataset.loaded) return;
    list.dataset.loaded = '1';
    list.textContent = 'Загружаю каталог…';
    try {
      const stations = await api('/api/public-stations', {}, false);
      list.innerHTML = stations.map(s => `<div class="station-source-row"><span>${esc(s.name)}</span>
        <button type="button" class="${s.added ? 'pill' : 'btn'}" data-public-station="${esc(s.id)}"
          aria-label="${s.added ? 'Удалить' : 'Добавить'}: ${esc(s.name)}">${s.added ? 'Удалить' : 'Добавить'}</button></div>`).join('');
      for (const button of $$('[data-public-station]', list)) {
        button.addEventListener('click', async () => {
          const station = stations.find(s => s.id === button.dataset.publicStation);
          const origin = routeSeq;
          const buttons = $$('[data-public-station]', list);
          buttons.forEach(item => { item.disabled = true; });
          button.textContent = station.added ? 'Удаляю…' : 'Добавляю…';
          try {
            const url = '/api/public-stations/' + encodeURIComponent(station.id);
            const response = await fetch(station.added ? url : url + '/add',
              { method: station.added ? 'DELETE' : 'POST' });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Не удалось изменить список станций.');
            toast((station.added ? 'Станция удалена: ' : 'Станция добавлена: ') + station.name);
            stationChanges?.postMessage(data.dataset);
            if (origin === routeSeq) S.ds = data.dataset;
            await refreshCaseStations();
          } catch (error) {
            toast(error.message, true);
          } finally {
            buttons.forEach(item => { item.disabled = false; });
            button.textContent = station.added ? 'Удалить' : 'Добавить';
          }
        });
      }
    } catch (error) {
      delete list.dataset.loaded;
      if (error !== STALE) list.textContent = error.message;
    }
  }
  catalog.addEventListener('toggle', () => {
    if (!catalog.isConnected) return;
    UI.publicCatalogOpen = catalog.open;
    if (catalog.open) loadCatalog();
  });
  if (catalog.open) loadCatalog();
}

async function renderData(main) {
  const [sets, q] = await Promise.all([fetch('/api/datasets').then(r => r.json()), api('/api/quality', {}, false)]);
  const roles = [['', '— не использовать —'], ['__time', 'Дата и время'], ['__station', 'Станция'], ...META.all_variables.map(v => [v.id, `${v.label}, ${v.unit}`])];
  const kinds = Object.entries(q.issue_kinds);
  main.innerHTML = `
  <div class="grid12">
    <div class="stack">
      <div class="box panel"><div class="head"><b>Наборы данных</b></div>
        <div class="dslist">${sets.map(s => `<button data-ds="${s.id}" class="${s.id === S.ds ? 'on' : ''}">${esc(s.name)}</button>`).join('')}</div>
        <label class="drop" id="drop"><input type="file" id="file" multiple accept=".csv,.txt,.xlsx" hidden>
          <b>Добавить файлы с метеостанции</b><br>В том же формате, например kr1_2026.csv или ame60_2022.csv — данные допишутся в ряд станции.<br>Перетащите сюда или нажмите.</label>
        <div class="station-sources">
          <details class="station-source">
            <summary>Запросить метеоданные с другой станции</summary>
            <form id="station-data-request" class="station-request-form">
              <label>Заповедник или организация<input name="recipient" required maxlength="150"></label>
              <label>Метеостанция<input name="station" required maxlength="150"></label>
              <label>Показатели<input name="parameters" required placeholder="Например, температура и влажность"></label>
              <div class="station-request-dates">
                <label>С даты<input name="start" type="date" required></label>
                <label>По дату<input name="end" type="date" required></label>
              </div>
              <label>Почта для ответа<input name="email" type="email" required></label>
              <label>Комментарий<textarea name="comment" rows="2"></textarea></label>
              <button class="btn" type="submit">Отправить</button>
              <p id="station-request-status" role="status"></p>
            </form>
          </details>
          <details class="station-source" id="public-station-catalog" ${UI.publicCatalogOpen ? 'open' : ''}>
            <summary>Добавить из доступных метеостанций</summary>
            <p>Открытые суточные данные NOAA GHCN Daily за 2024–2025 гг.</p>
            <div id="public-station-list" aria-live="polite"></div>
          </details>
        </div>
      </div>
      <div class="box panel"><div class="head"><b>Отчёты</b></div>
        <p class="note" style="padding-top:2px">${esc(S.station)}, ${esc(periodLabel())}. Станция и период — в строке управления вверху.</p>
        <div class="actions" style="padding:8px 16px 0"><a class="btn" href="${apiUrl('/api/report.html')}">${ICON.doc}Отчёт HTML</a>
          <a class="pill" href="${apiUrl('/api/report.xlsx')}">${ICON.dl}Таблицы Excel</a></div>
        <p class="note">Все станции и файлы сразу, без интерфейса: <code>python -m meteo.batch папка</code></p></div>
      <div class="box panel" id="recovery-panel"><div class="head"><b>Восстановление пропусков</b></div>
        <p class="note">${esc(S.station)} · ${esc(varById(S.var).label)} · ${esc(periodLabel())}.
          Черновик содержит интервалы и сведения об источниках. Официальный ряд хранится отдельно.</p>
        <div class="actions recovery-actions">
          <button class="btn" id="recovery-preview" type="button">Проверить пропуски</button>
          <a class="pill" href="https://www.clgz.ru/node/18449" target="_blank" rel="noopener noreferrer">Летопись природы</a>
        </div>
        <div class="note recovery-result" id="recovery-result" aria-live="polite"></div>
      </div>
    </div>
    <div class="stack">
      ${META.stations.map(station => {
        const files = q.files.filter(f => f.stations.includes(station.id));
        return `<details class="box station-source station-file-group"><summary>${esc(station.id)}</summary>
        <div class="stack">${files.map((f, n) => {
        const meta = parseSummary(f.summary);
        const cols = [...f.time, ...(f.station_column ? [f.station_column] : []), ...Object.keys(f.mapping), ...f.unmapped];
        return `<div class="box panel" data-file="${esc(f.name)}">
        <div class="filehead"><b>${esc(f.name)}</b><span class="sp"></span><button class="btn" data-apply="${n}">Применить сопоставление</button></div>
        <div class="filemeta">${meta.map(([k, v]) => `<span>${esc(k)}: <b>${esc(v)}</b></span>`).join('')}</div>
        <div class="mapgrid"><div class="head2">Колонка в файле</div><div class="head2">Что в ней</div>
        ${cols.map(col => {
          const cur = f.time.includes(col) ? '__time' : col === f.station_column ? '__station' : (f.mapping[col] || '');
          return `<div class="col" title="${esc(col)}">${esc(col)}</div><div class="sel"><select class="field ${cur ? '' : 'unused'}" data-col="${esc(col)}">${roles.map(([k, t]) => `<option value="${k}" ${k === cur ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></div>`;
        }).join('')}</div></div>`;
        }).join('')}</div></details>`;
      }).join('')}
    </div>
  </div>
  <div class="grid2 mt">
    <div class="box panel"><div class="tb">${dlBtn('ch-comp', 'полнота')}</div>
      ${panelTitle('Полнота данных', `${S.station}, % дней с наблюдениями`, 'Доля дней в году, за которые есть значение параметра.')}
      <div class="chart tall" id="ch-comp"></div></div>
    <div class="stack">
      <div class="box panel"><div class="head"><b>Проверка качества</b><span>${q.issues_total} ${plural(q.issues_total, ['замечание', 'замечания', 'замечаний'])}</span></div>
        ${kinds.length ? `<div class="tablewrap"><table class="t"><tbody>${kinds.map(([k, n]) => `<tr><td>${esc({ 'Вне': 'Вне физического диапазона', 'Выброс': 'Одиночные выбросы', 'Скачок': 'Резкие скачки уровня', 'Одинаковое': 'Залипание датчика', 'Tmin': 'Tmin больше Tmax' }[k] || k)}</td><td class="num">${n}</td>
          <td><span class="verdict ${['Вне', 'Выброс'].includes(k) ? 'warn' : ''}">предупреждение</span></td></tr>`).join('')}</tbody></table></div>`
          : '<div class="note">Подозрительных значений не найдено.</div>'}
        <p class="note">Пропусков в ряду: ${q.gaps_total}.</p></div>
      ${q.issues.length ? `<div class="box panel"><div class="head"><b>Подозрительные значения</b><span>первые ${q.issues.length}</span></div>
        <div class="tablewrap" style="max-height:300px;overflow:auto"><table class="t"><thead><tr><th>Время</th><th>Параметр</th><th class="num">Значение</th><th>Проблема</th><th>Приоритет</th><th>Контекст</th></tr></thead><tbody>
        ${q.issues.map(i => `<tr><td>${observationTime(i['Время'])}</td><td>${esc(i['Параметр'])}</td><td class="num">${fmt(i['Значение'])}</td>
          <td>${esc(i['Проблема'])}</td><td>${esc(i['Приоритет'])}</td><td>${esc(i['Контекст'])}</td></tr>`).join('')}
        </tbody></table></div></div>` : ''}
    </div>
  </div>`;
  bindStationSources(main);
  const recoveryPanel = $('#recovery-panel', main);
  const recoveryButton = $('#recovery-preview', recoveryPanel);
  recoveryButton.addEventListener('click', () => inspectRecovery(recoveryPanel, recoveryButton));
  const cz = q.completeness;
  plot('ch-comp', [{ type: 'heatmap', z: cz.z, x: cz.vars, y: cz.years, zmin: 0, zmax: 100, colorscale: [[0, '#F4E3C4'], [0.7, '#E3ECE5'], [1, C.accent]],
    showscale: false, xgap: 2, ygap: 2, hoverinfo: 'none' }],
  { hovermode: 'closest', margin: { l: 52, r: 16, t: 76, b: 90 }, yaxis: { autorange: 'reversed', showgrid: false, dtick: 5 }, xaxis: { showgrid: false, tickangle: -30 } });
  cellFocus($('#ch-comp'), pt => `<b>${pt.y}</b>   ${fmt(pt.z, 0)} %<br><span style="color:${C.ink2}">${esc(pt.x)}</span>`,
    pt => { const c = pt.pointNumber[1]; return [c - .5, c + .5, pt.y - .5, pt.y + .5]; });

  $$('.dslist button').forEach(b => b.addEventListener('click', () => switchDataset(b.dataset.ds)));
  $$('select[data-col]').forEach(s => s.addEventListener('change', () => s.classList.toggle('unused', !s.value)));
  const drop = $('#drop'), file = $('#file');
  ['dragenter', 'dragover'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', ev => upload(ev.dataTransfer.files));
  file.addEventListener('change', () => upload(file.files));
  $$('[data-apply]').forEach(b => b.addEventListener('click', async () => {
    const card = b.closest('[data-file]'); const m = { file: card.dataset.file, mapping: {}, time: [], station: null };
    $$('select[data-col]', card).forEach(s => { const c = s.dataset.col, v = s.value;
      if (v === '__time') m.time.push(c); else if (v === '__station') m.station = c; else if (v) m.mapping[c] = v; });
    if (!m.time.length) return toast('Укажите хотя бы одну колонку с датой и временем.', true);
    const r = await fetch(`/api/datasets/${S.ds}/mapping`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(m) });
    const body = await r.json();
    if (!r.ok) return toast(body.error || 'Не удалось применить сопоставление', true);
    toast('Сопоставление применено, данные пересчитаны'); await loadMeta(); route();
  }));
}
/* Сводка загрузчика (текст) → пары «что — значение» для компактной строки над сопоставлением */
function parseSummary(text) {
  const out = [];
  const enc = text.match(/Кодировка (\S+), разделитель «(.+?)», десятичный знак «(.+?)»/);
  if (enc) out.push(['Кодировка', enc[1].replace('-sig', '')], ['Разделитель', enc[2] === '\t' ? 'табуляция' : enc[2]], ['Десятичный знак', enc[3]]);
  const rows = text.match(/Строк: (\d+) → (\d+); битых дат (\d+), дублей (\d+), заглушек (\d+)/);
  if (rows) out.push(['Строк', fmt(Number(rows[2]), 0)], ['Битых дат', rows[3]], ['Дублей', rows[4]], ['Заглушек пропусков', rows[5]]);
  const conv = [...text.matchAll(/\((.+? → .+?)\)/g)].map(m => m[1]);
  if (conv.length) out.push(['Единицы', conv.join(', ')]);
  return out;
}
async function upload(files) {
  if (!files || !files.length) return;
  const fd = new FormData(); [...files].forEach(f => fd.append('files', f));
  toast('Загружаю и разбираю файлы…');
  const r = await fetch(`/api/datasets/${S.ds}/files`, { method: 'POST', body: fd });
  const body = await r.json();
  if (!r.ok) return toast(body.error || 'Не удалось загрузить', true);
  toast(`Добавлено: ${body.added.join(', ')} → ${body.stations.join(', ')}`);
  await loadMeta(); route();
}
async function switchDataset(id) { S.ds = id; S.station = ''; S.y = 0; await loadMeta(); route(); }

// ------------------------------------------------------------------ каркас
const PAGES = { overview: renderOverview, tables: renderTables, forecast: renderForecast, trends: renderTrends, compare: renderCompare, stations: renderStations, agro: renderAgro, data: renderData };

async function loadMeta() {
  applyMeta(await api('/api/meta', {}, false));
}

function applyMeta(meta) {
  META = meta;
  if (!META.stations.some(s => s.id === S.station))
    S.station = META.stations.slice().sort((a, b) => (a.end < b.end ? 1 : -1))[0].id;   // по умолчанию — самые свежие данные
  const st = META.stations.find(s => s.id === S.station);
  if (!st.vars.includes(S.var)) S.var = st.vars.includes('t_air') ? 't_air' : st.vars[0];
  syncControls();
}
/* Годы для «среднего за выбранный период»: по умолчанию все годы станции */
function syncBase() {
  const [lo, hi] = dataRange();
  const ys = [];
  for (let y = lo.getFullYear(); y <= hi.getFullYear(); y++) ys.push(y);
  let [a, b] = S.base === 'all' ? [ys[0], ys[ys.length - 1]] : S.base.split('-').map(Number);
  if (!ys.includes(a)) a = ys[0];
  if (!ys.includes(b)) b = ys[ys.length - 1];
  $('#base-from').innerHTML = ys.map(y => `<option ${y === a ? 'selected' : ''}>${y}</option>`).join('');
  $('#base-to').innerHTML = ys.map(y => `<option ${y === b ? 'selected' : ''}>${y}</option>`).join('');
}
function syncControls() {
  const st = META.stations.find(s => s.id === S.station);
  $('#station').innerHTML = META.stations.map(s => `<option ${s.id === S.station ? 'selected' : ''}>${esc(s.id)}</option>`).join('');
  // на экране сравнения станций — параметры любой из станций; иначе — выбранной
  const varsHere = S.page === 'stations' ? META.stations.flatMap(x => x.vars) : st.vars;
  if (!varsHere.includes(S.var)) S.var = varsHere.includes('t_air') ? 't_air' : varsHere[0];
  $('#var').innerHTML = META.variables.filter(v => varsHere.includes(v.id)).map(v => `<option value="${v.id}" ${v.id === S.var ? 'selected' : ''}>${esc(v.label)}</option>`).join('');
  $('#latest b').textContent = ruDate(st.end);
  $('#latest span').textContent = `Данные с ${parseISO(st.start).getFullYear()} по`;
  ensurePeriod();
  $$('#kind button').forEach(b => b.classList.toggle('on', b.dataset.kind === S.kind));
  $('#period-box').classList.toggle('custom-on', S.kind === 'custom');
  $('#cur-text').textContent = periodLabel();
  if (!$('#picker').hidden) renderPicker();
  for (const b of $$('#datenav .nb')) { const t = shifted(Number(b.dataset.step)); b.disabled = !fits(S.kind, t.y, t.i); }
  const [lo, hi] = dataRange();
  for (const id of ['c-start', 'c-end']) { $('#' + id).min = iso(lo); $('#' + id).max = iso(hi); }
  $('#c-start').value = S.cs; $('#c-end').value = S.ce;
  syncBase();
  // в строке — только то, чем управляется текущий экран
  const ctl = new Set(CTL[S.page] || []);
  $$('#controls [data-ctl]').forEach(el => { el.hidden = !ctl.has(el.dataset.ctl); });
  if (ctl.has('scope')) $('#scope').innerHTML = SCOPES.map(([k, n]) => `<option value="${k}" ${k === UI.trendScope ? 'selected' : ''}>${n}</option>`).join('');
  if (ctl.has('cmp')) {
    ensureCmp();
    for (const k of ['a0', 'a1', 'b0', 'b1']) { const el = $('#' + k); el.min = iso(lo); el.max = iso(hi); el.value = UI.cmp[k]; }
  }
  $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.page === S.page));
}

// ------------------------------------------------------------------ мини-календарь периода
const CHEV = d => `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="${d < 0 ? 'M15 6l-6 6 6 6' : 'M9 6l6 6-6 6'}"/></svg>`;
const SEASON_SPAN = ['дек — фев', 'мар — май', 'июн — авг', 'сен — ноя'];
function openPicker() {
  hideTip();
  UI.pickYear = S.y;
  renderPicker();
  $('#picker').hidden = false;
  $('#cur').setAttribute('aria-expanded', 'true');
}
function closePicker() {
  const pk = $('#picker');
  if (pk && !pk.hidden) { pk.hidden = true; $('#cur').setAttribute('aria-expanded', 'false'); }
}
function renderPicker() {
  const [lo, hi] = dataRange();
  const y0 = lo.getFullYear(), y1 = hi.getFullYear() + (S.kind === 'season' ? 1 : 0);
  const py = UI.pickYear ?? S.y;
  const head = (label, prevOk, nextOk) => `<div class="ph"><button data-act="-1" ${prevOk ? '' : 'disabled'} aria-label="Назад">${CHEV(-1)}</button>
    <b>${label}</b><button data-act="1" ${nextOk ? '' : 'disabled'} aria-label="Вперёд">${CHEV(1)}</button></div>`;
  let html;
  if (S.kind === 'year') {
    const start = py - (((py - y0) % 12) + 12) % 12;
    const years = Array.from({ length: 12 }, (_, k) => start + k);
    html = head(`${start} — ${start + 11}`, start > y0, start + 11 < y1) + `<div class="pg">${years.map(y =>
      `<button data-pick="${y},0" class="${y === S.y ? 'on' : ''}" ${fits('year', y, 0) ? '' : 'disabled'}>${y}</button>`).join('')}</div>`;
  } else if (S.kind === 'season') {
    html = head(String(py), py > y0, py < y1) + `<div class="pg two">${SEASONS.map((n, i) =>
      `<button data-pick="${py},${i}" class="${py === S.y && i === S.i ? 'on' : ''}" ${fits('season', py, i) ? '' : 'disabled'}>
        ${i === 0 ? `Зима ${py - 1}/${String(py).slice(2)}` : n}<small>${SEASON_SPAN[i]}</small></button>`).join('')}</div>`;
  } else {
    html = head(String(py), py > y0, py < y1) + `<div class="pg">${MSHORT.map((m, i) =>
      `<button data-pick="${py},${i}" class="${py === S.y && i === S.i ? 'on' : ''}" ${fits('month', py, i) ? '' : 'disabled'}>${cap(m)}</button>`).join('')}</div>`;
  }
  $('#picker').innerHTML = html;
}

async function route() {
  const seq = ++routeSeq;
  if (!PAGES[S.page]) S.page = 'overview';
  syncControls(); writeHash(); hideTip();
  const main = $('#page');
  main.classList.add('loading');
  try {
    await PAGES[S.page](main);
  } catch (e) {
    if (e === STALE) return;
    if (seq === routeSeq) { main.innerHTML = empty('Не получилось построить экран', e.message); console.error(e); }
  } finally { if (seq === routeSeq) main.classList.remove('loading'); }
}

function bind() {
  $('#nav').addEventListener('click', e => { const a = e.target.closest('a[data-page]'); if (a) { e.preventDefault(); S.page = a.dataset.page; route(); } });
  $('#var').addEventListener('change', e => { S.var = e.target.value; route(); });
  $('#station').addEventListener('change', e => { S.station = e.target.value; UI.ref = ''; route(); });
  $('#scope').addEventListener('change', e => { UI.trendScope = e.target.value; route(); });
  // периоды A/B применяются сразу, как только обе даты пары корректны
  for (const k of ['a0', 'a1', 'b0', 'b1']) $('#' + k).addEventListener('change', () => {
    const c = { a0: $('#a0').value, a1: $('#a1').value, b0: $('#b0').value, b1: $('#b1').value };
    if (Object.values(c).every(Boolean) && c.a0 <= c.a1 && c.b0 <= c.b1) { UI.cmp = c; route(); }
  });
  $('#kind').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return;
    if (S.kind !== b.dataset.kind) { const [a, z] = bounds(); S.kind = b.dataset.kind;
      if (S.kind === 'custom') { S.cs = iso(a); S.ce = iso(z); } else { const t = latestPeriod(S.kind); S.y = t.y; S.i = t.i; } route(); } });
  $('#datenav').addEventListener('click', e => {
    const b = e.target.closest('.nb');
    if (b && !b.disabled) { closePicker(); Object.assign(S, shifted(Number(b.dataset.step))); route(); }
  });
  $('#cur').addEventListener('click', () => ($('#picker').hidden ? openPicker() : closePicker()));
  $('#picker').addEventListener('click', e => {
    e.stopPropagation();
    const b = e.target.closest('button');
    if (!b || b.disabled) return;
    if (b.dataset.act) { UI.pickYear += Number(b.dataset.act) * (S.kind === 'year' ? 12 : 1); renderPicker(); return; }
    if (b.dataset.pick) { const [y, i] = b.dataset.pick.split(',').map(Number); S.y = y; S.i = i; closePicker(); route(); }
  });
  document.addEventListener('click', e => { if (!e.target.closest('#datenav')) closePicker(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closePicker(); });
  for (const id of ['c-start', 'c-end']) $('#' + id).addEventListener('change', () => {
    const a = $('#c-start').value, b = $('#c-end').value; if (a && b && a <= b) { S.cs = a; S.ce = b; route(); } });
  const pop = $('#settings'), btn = $('#settings-btn');
  btn.addEventListener('click', () => { pop.hidden = !pop.hidden; btn.setAttribute('aria-expanded', String(!pop.hidden)); });
  document.addEventListener('click', e => { if (!e.target.closest('.settings')) { pop.hidden = true; btn.setAttribute('aria-expanded', 'false'); } });
  for (const id of ['base-from', 'base-to']) $('#' + id).addEventListener('change', () => {
    const [lo, hi] = dataRange();
    let a = Number($('#base-from').value), b = Number($('#base-to').value);
    if (a > b) [a, b] = [b, a];
    S.base = a === lo.getFullYear() && b === hi.getFullYear() ? 'all' : `${a}-${b}`;
    route();
  });
  document.addEventListener('click', e => { const b = e.target.closest('[data-png]'); if (b) downloadPng(b.dataset.png, `${b.dataset.name} ${S.station} ${periodLabel()}`); });
  document.addEventListener('keydown', e => {
    if (e.target.closest('input, select, textarea') || !PERIOD_PAGES.has(S.page) || S.kind === 'custom') return;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { const t = shifted(e.key === 'ArrowLeft' ? -1 : 1); if (fits(S.kind, t.y, t.i)) { Object.assign(S, t); route(); } }
  });
  window.addEventListener('hashchange', () => { const p = S.page; readHash(); if (p !== S.page) route(); });
}

(async function init() {
  readHash(); bind();
  try { await loadMeta(); } catch (e) {
    if (S.ds !== 'clgz') { S.ds = 'clgz'; S.station = ''; await loadMeta(); } else { $('#page').innerHTML = empty('Сервер недоступен', e.message); return; }
  }
  route();
})();
