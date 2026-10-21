"""Animated pipe & sensor schematic for the dashboard.

Draws the supply tank, main pipe and one branch per zone with its sensors:
  FT  flow sensor     - turbine spins faster with more flow
  PT  pressure gauge  - needle shows the pressure
  V   valve           - green open, red closed, yellow moving
  LT  level sensor    - zone tank fills / drains
Water moves along the pipes; a leaking branch turns red and shows a burst.

Built once in the browser, then only the values are updated every second,
so the animations run smoothly.
"""

import streamlit as st

CSS = """
.wrap { font-family: system-ui, -apple-system, 'Segoe UI', sans-serif; }
svg { width: 100%; height: auto; display: block; background: #0f1b2d; border-radius: 14px; }
text { fill: #e2e8f0; font-size: 13px; }
.small { font-size: 11px; fill: #94a3b8; }
.value { font-size: 14px; font-weight: 700; }
.track { fill: none; stroke: #23324a; stroke-linecap: round; }
.water { fill: none; stroke-linecap: round; stroke-dasharray: 10 12; animation: flow 1s linear infinite; }
.water.stopped { animation: none; opacity: .25; }
@keyframes flow { to { stroke-dashoffset: -44; } }
.turbine { transform-box: fill-box; transform-origin: center; animation: spin 1s linear infinite; }
.turbine.stopped { animation: none; }
@keyframes spin { to { transform: rotate(360deg); } }
.burst { animation: pulse 0.9s ease-out infinite; transform-box: fill-box; transform-origin: center; }
@keyframes pulse { 0% { transform: scale(.6); opacity: 1; } 100% { transform: scale(1.6); opacity: 0; } }
.wave { animation: wave 2.5s linear infinite; }
@keyframes wave { to { transform: translateX(-40px); } }
.waves path { animation: blink 1.6s infinite; }
.waves path:nth-child(2) { animation-delay: .2s; } .waves path:nth-child(3) { animation-delay: .4s; }
.ctl { cursor: pointer; }
.ctl:hover { filter: brightness(1.35) drop-shadow(0 0 6px #38bdf8); }
.hint { font-size: 12px; fill: #7dd3fc; }
@keyframes blink { 50% { opacity: .2; } }
"""

JS = r"""
const NS = 'http://www.w3.org/2000/svg';
const W = 1000, H = 520, MAIN_Y = 110;
const OK = '#22c55e', FAULT = '#f59e0b', OFF = '#64748b', LEAK = '#ef4444', WATER = '#38bdf8';

function el(tag, attrs, parent, text) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text !== undefined) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}

function pipe(svg, d, width) {
  el('path', { d, class: 'track', 'stroke-width': width + 8 }, svg);
  return el('path', { d, class: 'water', stroke: WATER, 'stroke-width': width }, svg);
}

function build(root, zones, css) {
  root.innerHTML = '';
  const style = document.createElement('style');   // styles + animations
  style.textContent = css;
  root.appendChild(style);
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}` });
  const wrap = document.createElement('div');
  wrap.className = 'wrap';
  wrap.appendChild(svg);
  root.appendChild(wrap);
  const refs = { zones: {} };
  // Branches spaced evenly, leaving room on the right for the sensor labels
  const xs = zones.map((_, i) => 250 + i * (W - 400) / Math.max(zones.length - 1, 1));

  // Supply tank
  el('text', { x: 85, y: 38, 'text-anchor': 'middle', class: 'small' }, svg, 'SUPPLY TANK');
  el('rect', { x: 30, y: 50, width: 110, height: 150, rx: 10, fill: '#0b1220', stroke: WATER, 'stroke-width': 2 }, svg);
  el('rect', { x: 32, y: 80, width: 106, height: 118, rx: 8, fill: '#1d4ed8', opacity: .85 }, svg);
  const g = el('g', {}, svg);
  el('path', { d: 'M32 80 q10 -5 20 0 t20 0 t20 0 t20 0 t20 0 t20 0 v8 h-120 z', fill: '#7dd3fc', class: 'wave', opacity: .6 }, g);

  // Main pipe
  refs.main = pipe(svg, `M140 ${MAIN_Y} H ${xs[xs.length - 1]}`, 10);
  el('text', { x: 160, y: MAIN_Y - 16, class: 'small' }, svg, 'MAIN PIPE');
  el('text', { x: W - 20, y: 30, 'text-anchor': 'end', class: 'hint' }, svg, '👆 Click FT, PT or V to control it · click a pipe to make a leak');
  refs.clock = el('text', { x: 160, y: 30, class: 'value' }, svg);

  // Make a group clickable: sends {kind, zone} back to Python
  const control = (g, kind, zone) => {
    g.classList.add('ctl');
    g.addEventListener('click', () => root.__set('click', { kind, zone, nonce: Date.now() }));
  };

  zones.forEach((z, i) => {
    const x = xs[i], r = {};
    // Branch pipe (+ a wide invisible strip so the pipe is easy to click)
    r.pipe = pipe(svg, `M${x} ${MAIN_Y} V 410`, 7);
    const hit = el('path', { d: `M${x} ${MAIN_Y + 40} V 410`, stroke: 'transparent', 'stroke-width': 26,
                             fill: 'none', 'pointer-events': 'stroke' }, svg);
    r.pipeTitle = el('title', {}, hit);
    control(hit, 'PIPE', z.id);

    // Node label + wireless signal + battery / manual tags
    el('text', { x: x + 34, y: MAIN_Y + 30, class: 'value' }, svg, `Zone ${z.id}`);
    el('text', { x: x + 34, y: MAIN_Y + 46, class: 'small' }, svg, z.name);
    r.tags = el('text', { x: x + 34, y: MAIN_Y + 62, class: 'small', style: 'fill:#fbbf24;font-weight:700' }, svg);
    const waves = el('g', { class: 'waves', transform: `translate(${x - 52} ${MAIN_Y + 26})`, fill: 'none', 'stroke-width': 2.5, 'stroke-linecap': 'round' }, svg);
    r.waves = [6, 11, 16].map(rad => el('path', { d: `M ${-rad} 0 A ${rad} ${rad} 0 0 1 ${rad} 0`, stroke: OK }, waves));
    el('circle', { cx: 0, cy: 2, r: 2.5, fill: '#e2e8f0' }, waves);

    // FT - flow sensor with turbine
    const ft = el('g', { transform: `translate(${x} 185)` }, svg);
    el('rect', { x: -44, y: -26, width: 150, height: 52, fill: 'transparent' }, ft);   // easy click target
    r.ftBox = el('rect', { x: -22, y: -22, width: 44, height: 44, rx: 8, fill: '#0b1220', 'stroke-width': 3 }, ft);
    r.turbine = el('g', { class: 'turbine' }, ft);
    for (let a = 0; a < 4; a++) el('path', { d: 'M0 0 L4 -14 L-4 -14 Z', fill: WATER, transform: `rotate(${a * 90})` }, r.turbine);
    el('circle', { r: 3, fill: '#e2e8f0' }, ft);
    el('text', { x: -32, y: 5, 'text-anchor': 'end', class: 'small' }, ft, 'FT');
    r.ftText = el('text', { x: 32, y: 5, class: 'value' }, ft);
    el('title', {}, ft, 'Flow sensor (L/min) - click to control');
    control(ft, 'FT', z.id);

    // PT - pressure gauge
    const pt = el('g', { transform: `translate(${x} 262)` }, svg);
    el('rect', { x: -44, y: -26, width: 150, height: 52, fill: 'transparent' }, pt);   // easy click target
    r.ptRing = el('circle', { r: 22, fill: '#0b1220', 'stroke-width': 3 }, pt);
    el('path', { d: 'M -15 11 A 19 19 0 1 1 15 11', fill: 'none', stroke: '#334155', 'stroke-width': 3 }, pt);
    r.needle = el('line', { x1: 0, y1: 0, x2: 0, y2: -16, stroke: '#f8fafc', 'stroke-width': 2.5, 'stroke-linecap': 'round' }, pt);
    el('circle', { r: 3, fill: '#f8fafc' }, pt);
    el('text', { x: -32, y: 5, 'text-anchor': 'end', class: 'small' }, pt, 'PT');
    r.ptText = el('text', { x: 32, y: 5, class: 'value' }, pt);
    el('title', {}, pt, 'Pressure sensor (bar) - click to control');
    control(pt, 'PT', z.id);

    // Leak burst (between gauge and valve)
    r.burst = el('g', { transform: `translate(${x} 300)`, style: 'display:none' }, svg);
    el('circle', { r: 14, fill: 'none', stroke: LEAK, 'stroke-width': 3, class: 'burst' }, r.burst);
    el('text', { x: 20, y: 5, fill: LEAK, style: 'fill:#ef4444;font-weight:800' }, r.burst, '💧 LEAK');

    // V - valve
    const v = el('g', { transform: `translate(${x} 338)` }, svg);
    el('rect', { x: -40, y: -24, width: 130, height: 46, fill: 'transparent' }, v);   // easy click target
    el('title', {}, v, 'Valve - click to open / close');
    control(v, 'V', z.id);
    r.v1 = el('polygon', { points: '-15,-12 -15,12 0,0', stroke: '#0b1220', 'stroke-width': 2 }, v);
    r.v2 = el('polygon', { points: '15,-12 15,12 0,0', stroke: '#0b1220', 'stroke-width': 2 }, v);
    el('line', { x1: 0, y1: 0, x2: 0, y2: -16, stroke: '#cbd5e1', 'stroke-width': 3 }, v);
    el('rect', { x: -7, y: -21, width: 14, height: 6, rx: 2, fill: '#cbd5e1' }, v);
    el('text', { x: -32, y: 5, 'text-anchor': 'end', class: 'small' }, v, 'V');
    r.vText = el('text', { x: 24, y: 5, class: 'value' }, v);

    // LT - zone tank with level sensor
    const lt = el('g', { transform: `translate(${x - 30} 410)` }, svg);
    r.ltBox = el('rect', { x: 0, y: 0, width: 60, height: 80, rx: 8, fill: '#0b1220', 'stroke-width': 3 }, lt);
    r.fill = el('rect', { x: 3, width: 54, rx: 5, fill: '#2563eb', opacity: .9 }, lt);
    el('text', { x: -10, y: 45, 'text-anchor': 'end', class: 'small' }, lt, 'LT');
    r.ltText = el('text', { x: 30, y: 46, 'text-anchor': 'middle', class: 'value' }, lt);
    el('title', {}, lt, 'Water level sensor (%)');

    r.status = el('text', { x: x + 40, y: 470, class: 'value' }, svg);
    refs.zones[z.id] = r;
  });
  return refs;
}

function update(refs, zones) {
  let total = 0;
  for (const z of zones) {
    const r = refs.zones[z.id];
    if (!r) continue;
    // Each sensor's outline shows its own health
    const health = (mode) => z.status === 'offline' ? OFF : (mode && mode !== 'ok') ? FAULT : OK;
    const sensorColor = health('ok');
    const flow = z.flow ?? 0, pressure = z.pressure ?? 0;
    const flowing = z.valve === 'OPEN' && flow > 1 && z.status !== 'offline';
    total += flowing ? z.flow : 0;

    // pipe colour and water movement
    r.pipe.setAttribute('stroke', z.status === 'leak' ? LEAK : WATER);
    r.pipe.classList.toggle('stopped', !flowing);
    r.pipe.style.animationDuration = (60 / Math.max(flow, 1)).toFixed(2) + 's';
    r.pipeTitle.textContent = `Zone ${z.id} pipe: ${flow.toFixed(0)} L/min, ${pressure.toFixed(2)} bar - click to make a leak here`;

    r.waves.forEach(w => w.setAttribute('stroke', z.status === 'offline' ? OFF : z.weak_signal ? FAULT : OK));
    r.tags.textContent = [z.battery_low ? '🪫 low battery' : '', z.weak_signal ? '📶 weak signal' : '',
                          z.manual ? '✋ manual' : ''].filter(Boolean).join('  ');
    r.ftBox.setAttribute('stroke', health(z.ft_mode));
    r.ptRing.setAttribute('stroke', health(z.pt_mode));
    r.ltBox.setAttribute('stroke', sensorColor);

    // flow sensor turbine
    r.turbine.classList.toggle('stopped', !flowing || (z.ft_mode && z.ft_mode !== 'ok'));
    r.turbine.style.animationDuration = (40 / Math.max(flow, 1)).toFixed(2) + 's';
    r.ftText.textContent = z.flow == null ? '— no signal' : `${flow.toFixed(0)} L/min`;
    r.ftText.style.fill = health(z.ft_mode) === FAULT ? FAULT : '';

    // pressure gauge needle: 0..5 bar -> -120..120 degrees
    const p = Math.max(0, Math.min(pressure, 5));
    r.needle.setAttribute('transform', `rotate(${(-120 + p / 5 * 240).toFixed(1)})`);
    r.ptText.textContent = z.pressure == null ? '— no signal' : `${pressure.toFixed(2)} bar`;
    r.ptText.style.fill = health(z.pt_mode) === FAULT ? FAULT : '';

    // valve
    const vColor = z.valve === 'OPEN' ? OK : z.valve === 'CLOSED' ? LEAK : '#facc15';
    r.v1.setAttribute('fill', vColor);
    r.v2.setAttribute('fill', vColor);
    r.vText.textContent = z.valve;
    r.vText.style.fill = vColor;

    // zone tank level
    const lvl = Math.max(0, Math.min(z.level, 100));
    r.fill.setAttribute('y', (3 + 74 * (1 - lvl / 100)).toFixed(1));
    r.fill.setAttribute('height', (74 * lvl / 100).toFixed(1));
    r.ltText.textContent = `${z.level.toFixed(0)}%`;

    r.burst.style.display = z.status === 'leak' ? '' : 'none';
    const label = { normal: ['● OK', OK], leak: ['● LEAK', LEAK], fault: ['● SENSOR FAULT', FAULT],
                    offline: ['● OFFLINE', OFF], closed: ['● ISOLATED', '#a78bfa'] }[z.status];
    r.status.textContent = label[0];
    r.status.style.fill = label[1];
  }
  refs.main.classList.toggle('stopped', total < 1);
}

// Short two-tone alarm when a new leak is detected
function beep() {
  try {
    const ctx = window.__agAudio || (window.__agAudio = new (window.AudioContext || window.webkitAudioContext)());
    [880, 660, 880].forEach((f, i) => {
      const o = ctx.createOscillator(), g = ctx.createGain(), t = ctx.currentTime + i * 0.25;
      o.frequency.value = f; o.type = 'square';
      g.gain.setValueAtTime(0.06, t); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.22);
      o.connect(g); g.connect(ctx.destination); o.start(t); o.stop(t + 0.23);
    });
  } catch (e) { /* the browser blocks sound until you have clicked the page once */ }
}

export default function (component) {
  const { data, parentElement, setStateValue } = component;
  if (!data) return;
  parentElement.__set = setStateValue;
  const sig = data.zones.map(z => z.id).join(',');
  if (!parentElement.__refs || parentElement.__sig !== sig) {
    parentElement.__refs = build(parentElement, data.zones, data.css);
    parentElement.__sig = sig;
  }
  update(parentElement.__refs, data.zones);
  parentElement.__refs.clock.textContent = data.clock || '';
  // Beep when the leak counter goes up (not for leaks that existed before the page
  // opened). A null counter means the sound is switched off.
  if (data.beep == null) {
    parentElement.__beep = undefined;
  } else {
    if (parentElement.__beep !== undefined && data.beep > parentElement.__beep) beep();
    parentElement.__beep = data.beep;
  }
}
"""

_schematic = st.components.v2.component("aquaguard_schematic", js=JS)


def schematic(zones, clock="", beep=0, key="schematic"):
    """Draw the schematic. Returns {"kind": "FT"|"PT"|"V"|"PIPE", "zone": id} when one is clicked.

    zones: list of {id, name, flow, pressure, level, valve, status, ft_mode, pt_mode,
                    battery_low, weak_signal, manual}
    clock: text shown top-left (simulated time)
    beep:  a counter; the browser beeps whenever it goes up
    """
    result = _schematic(data={"zones": zones, "css": CSS, "clock": clock, "beep": beep},
                        key=key, on_click_change=lambda: None)
    click = result.click
    # A click arrives as state with a unique nonce: handle each one exactly once
    if not click or st.session_state.get("_last_click") == click["nonce"]:
        return None
    st.session_state["_last_click"] = click["nonce"]
    return click
