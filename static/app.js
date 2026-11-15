/* Bank of Dad — vanilla JS dashboard. No build, no framework. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let STATE = { kids: [], config: null };
let SERVER_NOW = 0;     // server clock at last fetch (epoch s, honours fast-forward)
let FETCH_AT = 0;       // local clock at last fetch

// Escape user-controlled text before interpolating it into HTML or attributes.
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const dollars = (c) => "$" + (c / 100).toFixed(2);
const coins = (c) => Math.round(c / 10); // 1 coin = 10¢, for a tactile count
const WEEK = 7 * 86400;

function chartDollarLabel(cents) {
  const amount = cents / 100;
  const short = (value, suffix) => {
    const digits = value < 10 && value % 1 ? 1 : 0;
    return `$${value.toFixed(digits)}${suffix}`;
  };
  if (amount >= 1e9) return short(amount / 1e9, "B");
  if (amount >= 1e6) return short(amount / 1e6, "M");
  if (amount >= 1e3) return short(amount / 1e3, "K");
  return "$" + (cents % 100 === 0 ? amount : amount.toFixed(2));
}

// Current server time, advanced by real wall-clock since the last fetch, so
// savings/bonds keep growing smoothly between refreshes.
const serverNow = () => SERVER_NOW + (Date.now() / 1000 - FETCH_AT);

let DAD_PIN = null; // remembered after a correct entry, for this page session

async function api(path, body) {
  const headers = { "Content-Type": "application/json" };
  if (DAD_PIN && path.startsWith("/api/admin")) headers["X-Dad-Pin"] = DAD_PIN;
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  return res.json();
}

async function refresh() {
  STATE = await api("/api/state");
  SERVER_NOW = STATE.server_now || Date.now() / 1000;
  FETCH_AT = Date.now() / 1000;
  render();
}

/* ---- SVG stock chart (hand-rolled, dependency-free) ------------------- */
const SERIES = [
  { m: "wallet",  c: "#3b82f6", g: ["#3b82f6", "#60a5fa"], w: 3.5, label: "🪙 Wallet" },
  { m: "savings", c: "#10b981", g: ["#0ea371", "#34d399"], w: 3.5, label: "🏦 Savings" },
  { m: "invest",  c: "#8b5cf6", g: ["#7c3aed", "#c084fc"], w: 4,   label: "🚀 Invest" },
  { m: "total",   c: "#f5a300", g: ["#f59e0b", "#ffd23f"], w: 5.5, label: "💰 All money" },
];

const bestTerm = () =>
  STATE.config.bonds.reduce((a, b) => (b.return_pct > a.return_pct ? b : a),
                            STATE.config.bonds[0]);

// How far back the chart shows. Default "all" so kids see their whole growth
// story; changeable via the range toggle under each chart.
const RANGES = [["1m", "1M", 30], ["3m", "3M", 90], ["1y", "1Y", 365], ["all", "All", Infinity]];
const RANGE_DAYS = { "1m": 30, "3m": 90, "1y": 365, "all": Infinity };
let chartRange = "all";
try { chartRange = localStorage.getItem("bod_range") || "all"; } catch (e) {}

// How far the projection looks ahead, shared by the cards and the expanded
// graph. Default one year: over three months compounding looks like a straight
// line, so the card never showed the curve. Tap "in 1Y" on a card to cycle.
const FUTURE_RANGES = [["3m", "3M", 91], ["1y", "1Y", 365], ["5y", "5Y", 1825]];
let chartFutureRange = "1y";
try { chartFutureRange = localStorage.getItem("bod_future_range") || "1y"; } catch (e) {}
function setFutureRange(key) {
  chartFutureRange = key;
  try { localStorage.setItem("bod_future_range", chartFutureRange); } catch (e) {}
}

// Active "what if" scenarios per kid id: { save, m1, m3 } in cents. When set,
// the main chart overlays a projected trajectory for that scenario.
let WHATIF = {};

// Value at time t of a lock that is re-invested in the same term every time it
// pays out: flat at its principal until it matures, then it jumps (×payout/
// principal) and locks again. Stairs, not a smooth curve — that's how a lock
// really pays — but stairs that keep doubling, the long-term compounding lesson.
function relockValue(principal, payout, termSec, firstMature, t) {
  if (!principal || t < firstMature) return principal;
  const n = Math.floor((t - firstMature) / termSec) + 1;
  return principal * Math.pow(payout / principal, n);
}

// Projected "all money" trajectory if the kid moved `wf` coins now: save -> savings,
// m1 -> 1-month lock, m3 -> 3-month lock. Starts at today's total and diverges.
// New locks re-invest like the real ones (see relockValue).
function whatIfProjection(cur, fut, wf) {
  const cfg = STATE.config, wr = cfg.savings_weekly_pct / 100, now = fut[0].t;
  const b1 = cfg.bonds.find((b) => b.key === "1mo") || cfg.bonds[0];
  const b3 = cfg.bonds.find((b) => b.key === "3mo") || cfg.bonds[cfg.bonds.length - 1];
  const lock = (amt, b, t) => relockValue(amt, amt * (1 + b.return_pct / 100),
                                          b.days * 86400, now + b.days * 86400, t);
  const moved = wf.save + wf.m1 + wf.m3;
  return fut.map((p) => {
    const savings = wf.save * Math.pow(1 + wr, (p.t - now) / WEEK);
    return { t: p.t, total: p.total - moved + savings + lock(wf.m1, b1, p.t) + lock(wf.m3, b3, p.t) };
  });
}

// Project each pool forward from today over the selected range. Wallet stays
// flat (spending money you keep on hand) so the contrast is clear: money you
// DON'T save stays put. Savings compounds weekly (interest is collected back
// into savings). Invest follows the kid's real locks and assumes each payout
// is re-invested in the same term, so it climbs in stairs.
function project(cur, now, futureDays, bonds = []) {
  const wr = STATE.config.savings_weekly_pct / 100;
  const end = now + Math.max(1, Math.round(futureDays / 7)) * WEEK;
  const locks = bonds.map((b) => ({
    p: b.principal_cents, pay: b.payout_cents,
    term: Math.max(86400, b.mature_ts - b.buy_ts),
    first: Math.max(b.mature_ts, now + 1),   // a ready lock re-invests right away
  }));
  // Weekly samples, plus a point on each side of every payout so the stairs
  // step straight up instead of sloping across a week.
  const ts = [];
  for (let t = now; t <= end; t += WEEK) ts.push(t);
  for (const l of locks) {
    for (let m = l.first; m <= end; m += l.term) ts.push(m - 1, m);
  }
  ts.sort((a, b) => a - b);
  return [...new Set(ts)].map((t) => {
    const wallet = cur.wallet;
    const savings = cur.savings * Math.pow(1 + wr, (t - now) / WEEK);
    const invest = locks.reduce((sum, l) => sum + relockValue(l.p, l.pay, l.term, l.first, t), 0);
    return { t, wallet, savings, invest, total: wallet + savings + invest };
  });
}

// Four-line money chart: real history (solid) flowing into a projected future
// (dashed). Shows All money, Wallet, Savings and Invest together so it's obvious
// which pools drive the climb.
// Clean up history so the past line is coherent: total comes from the per-pool
// breakdown (consistent with "now"), snapshots within ~1 day of now are dropped
// (the live "now" point supersedes them — avoids a same-x vertical cliff), and
// ancient clusters separated by a big time gap (e.g. after a fast-forward) are
// trimmed so we only draw the recent, connected run.
function cleanHistory(k, now) {
  let h = (k.history || []).map((p) => ({
    t: p.ts,
    wallet: p.wallet, savings: p.savings, invest: p.invest,
    total: (p.wallet || 0) + (p.savings || 0) + (p.invest || 0),
  }));
  // Collapse points at (nearly) the same time — several actions in one moment
  // would otherwise stack into a vertical segment. Keep the latest value.
  const collapsed = [];
  for (const p of h) {
    const last = collapsed[collapsed.length - 1];
    if (last && p.t - last.t < 3600) collapsed[collapsed.length - 1] = p;
    else collapsed.push(p);
  }
  h = collapsed.filter((p) => now - p.t > 86400);  // drop near-now snapshots
  // Show everything within the selected range (default "all" = full history).
  const days = RANGE_DAYS[chartRange] ?? Infinity;
  if (days !== Infinity) h = h.filter((p) => (now - p.t) <= days * 86400);
  return h;
}

// The points one kid's chart draws, plus their $ range (lo..hi).
function chartData(k) {
  const now = serverNow();
  // "Now" point: matches the "all money" net worth shown above the chart, so
  // the line lands exactly where the headline number says it should.
  const cur = {
    t: now,
    wallet: k.wallet_cents,
    savings: k.savings_cents,
    invest: k.invest_cents,
  };
  cur.total = cur.wallet + cur.savings + cur.invest;   // == owned (hero)

  const past = cleanHistory(k, now).concat([cur]);
  const futureRange = FUTURE_RANGES.find(([key]) => key === chartFutureRange) || FUTURE_RANGES[1];
  const futureLabel = futureRange[1], futureDays = futureRange[2];
  const fut = project(cur, now, futureDays, k.bonds || []); // fut[0] == today, so the lines join
  const all = past.concat(fut);
  const wf = WHATIF[k.id];
  const wfPts = wf ? whatIfProjection(cur, fut, wf) : null;
  let lo = Infinity, hi = -Infinity;
  all.forEach((p) => SERIES.forEach((s) => {
    if (p[s.m] < lo) lo = p[s.m];
    if (p[s.m] > hi) hi = p[s.m];
  }));
  if (wfPts) wfPts.forEach((p) => { if (p.total > hi) hi = p.total; }); // fit the overlay
  return { now, cur, past, fut, all, wf, wfPts, futureLabel, lo, hi };
}

// One $ scale shared by every card on the dashboard, so a kid whose money is
// headed higher really gets a taller line (instead of every chart stretching
// to fill its own box). Set in render(); the expanded graph uses its own.
let CARD_SCALE = null;

function moneyChart(k, expanded = false) {
  const wide = expanded && window.innerWidth > 600;
  const W = wide ? 800 : 340, H = expanded ? (wide ? 360 : 260) : 140;
  const pad = 8, padL = 32, padB = 22;
  const { now, cur, past, fut, all, wf, wfPts, futureLabel, ...range } = chartData(k);
  if (all.length < 2) return `<svg class="chart"></svg>`;
  const { lo, hi } = (!expanded && CARD_SCALE) || range;

  const t0 = all[0].t, t1 = fut[fut.length - 1].t;
  // Piecewise x so "now" sits in the CENTER: past fills the left half, the
  // projected future fills the right half (regardless of their day-spans).
  const mid = padL + (W - pad - padL) * 0.5;
  const xs = (t) => (t <= now)
    ? padL + ((t - t0) / ((now - t0) || 1)) * (mid - padL)
    : mid + ((t - now) / ((t1 - now) || 1)) * (W - pad - mid);
  // Headroom so a flat line at the minimum (e.g. $0 savings) lifts off the axis
  // instead of hiding on it.
  const span = (hi - lo) || 1;
  const loP = lo - span * 0.10, hiP = hi + span * 0.06;
  const ys = (v) => (H - padB) - ((v - loP) / (hiP - loP)) * (H - padB - pad);
  const pathOf = (arr, m) =>
    arr.map((p, i) => (i ? "L" : "M") + xs(p.t).toFixed(1) + " " + ys(p[m]).toFixed(1)).join(" ");

  // Gradient strokes + a glowing gold "All money" hero line with a gold area
  // fill underneath. Ids are suffixed per kid so multiple cards don't collide.
  const uid = k.id;
  let defs = "<defs>";
  SERIES.forEach((s) => {
    // userSpaceOnUse across the chart width — objectBoundingBox gradients don't
    // paint on a flat (zero-height-bbox) line, which hid flat projections.
    defs += `<linearGradient id="g-${s.m}-${uid}" gradientUnits="userSpaceOnUse" x1="${pad}" y1="0" x2="${W - pad}" y2="0">
      <stop offset="0" stop-color="${s.g[0]}"/><stop offset="1" stop-color="${s.g[1]}"/></linearGradient>`;
  });
  defs += `<linearGradient id="fill-total-${uid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffd23f" stop-opacity=".22"/>
      <stop offset="1" stop-color="#ffd23f" stop-opacity="0"/></linearGradient>
    <filter id="glow-${uid}" x="-10%" y="-40%" width="120%" height="180%">
      <feGaussianBlur stdDeviation="2.2" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter></defs>`;

  const xToday = xs(now).toFixed(1);
  let svg = defs;
  // gold area under the All money line (whole timeline)
  const allPts = past.concat(fut.slice(1));
  const areaD = allPts.map((p, i) => (i ? "L" : "M") + xs(p.t).toFixed(1) + " " + ys(p.total).toFixed(1)).join(" ")
    + ` L ${xs(allPts[allPts.length - 1].t).toFixed(1)} ${H - padB} L ${xs(allPts[0].t).toFixed(1)} ${H - padB} Z`;
  svg += `<path d="${areaD}" fill="url(#fill-total-${uid})"/>`;
  // y-axis: a few "nice" $ gridlines + labels in the left gutter
  const niceStep = (raw) => {
    const e = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    const f = (raw || 1) / e;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e;
  };
  const step = niceStep(hi / 4);
  for (let v = 0; v <= hi + 1; v += step) {
    const gy = ys(v).toFixed(1);
    svg += `<line x1="${padL}" y1="${gy}" x2="${W - pad}" y2="${gy}" stroke="#2d2150" stroke-width="1" opacity=".07"/>`;
    svg += `<text x="${padL - 5}" y="${(+gy + 3).toFixed(1)}" font-size="8" fill="#2d2150" opacity=".5" text-anchor="end">${chartDollarLabel(v)}</text>`;
  }
  const pastLabel = past.length > 1
    ? `<text x="${padL}" y="${H - 6}" font-size="9" fill="#2d2150" opacity=".45">past</text>`
    : "";
  svg += `<line x1="${xToday}" y1="${pad}" x2="${xToday}" y2="${H - padB}" stroke="#2d2150" stroke-width="1" stroke-dasharray="3 3" opacity=".3"/>
    <text x="${xToday}" y="${H - 6}" font-size="9" fill="#2d2150" opacity=".55" text-anchor="middle">now</text>
    ${pastLabel}`;
  // On the card the horizon label doubles as a tiny tap target that cycles
  // 3M → 1Y → 5Y (no extra row of buttons on the small tablet screen).
  svg += expanded
    ? `<text x="${W - pad}" y="${H - 6}" font-size="9" fill="#2d2150" opacity=".45" text-anchor="end">in ${futureLabel}</text>`
    : `<g class="future-cycle" role="button" aria-label="Change how far ahead to look">
        <rect x="${W - pad - 44}" y="${H - 17}" width="44" height="15" rx="7.5" fill="#ffd23f" opacity=".35"/>
        <text x="${W - pad - 22}" y="${H - 6}" font-size="9" font-weight="800" fill="#2d2150" opacity=".8" text-anchor="middle">in ${futureLabel} ▸</text>
      </g>`;
  const fe = fut[fut.length - 1];
  SERIES.forEach((s) => {
    const stroke = `url(#g-${s.m}-${uid})`;
    // Glow only on the thick gold line — blurring the thin dashed lines washed
    // their small dashes into near-invisibility.
    const glow = s.m === "total" ? ` filter="url(#glow-${uid})"` : "";
    svg += `<path d="${pathOf(past, s.m)}" fill="none" stroke="${stroke}" stroke-width="${s.w}" stroke-linecap="round" stroke-linejoin="round"${glow}/>`;
    svg += `<path d="${pathOf(fut, s.m)}" fill="none" stroke="${stroke}" stroke-width="${s.w}" stroke-dasharray="2 5" stroke-linecap="round" stroke-linejoin="round"${glow}/>`;
    // a crisp end-dot at each line's projected tip so every line reads clearly
    svg += `<circle cx="${xs(fe.t).toFixed(1)}" cy="${ys(fe[s.m]).toFixed(1)}" r="${s.m === "total" ? 4.5 : 3.2}" fill="${s.g[1]}"${s.m === "total" ? ` filter="url(#glow-${uid})"` : ""}/>`;
  });
  // Rocket rides the Invest line; once every lock has paid out, it sits at
  // the last point where Invest still had money in it.
  const lastInv = [...fut].reverse().find((p) => p.invest > 0);
  if (lastInv) svg += `<text x="${xs(lastInv.t)}" y="${ys(lastInv.invest) - 5}" font-size="13" text-anchor="end">🚀</text>`;

  // "What if" overlay: a bright trajectory showing where their money COULD go
  const WF_COLOR = "#9333ea";
  if (wfPts) {
    const wd = wfPts.map((p, i) => (i ? "L" : "M") + xs(p.t).toFixed(1) + " " + ys(p.total).toFixed(1)).join(" ");
    svg += `<path d="${wd}" fill="none" stroke="${WF_COLOR}" stroke-width="4" stroke-dasharray="1 5" stroke-linecap="round" stroke-linejoin="round"/>`;
    const we = wfPts[wfPts.length - 1];
    svg += `<circle cx="${xs(we.t).toFixed(1)}" cy="${ys(we.total).toFixed(1)}" r="4.5" fill="${WF_COLOR}"/>`;
    svg += `<text x="${(xs(we.t) - 4).toFixed(1)}" y="${(ys(we.total) - 6).toFixed(1)}" font-size="10" font-weight="900" fill="${WF_COLOR}" text-anchor="end">could be ✨</text>`;
  }

  const legend = '<div class="chart-legend">' +
    SERIES.map((s) => `<span><i style="background:${s.c}"></i>${s.label}</span>`).join("") +
    (wfPts ? `<span><i style="background:${WF_COLOR}"></i>🔮 Could be</span>` : "") +
    "</div>";
  const banner = wf
    ? `<div class="whatif-banner">🔮 Showing a "what if" — <b>${dollars(wfPts[wfPts.length - 1].total)}</b> by ${futureLabel}!
         <button class="wi-clear">Clear ✕</button></div>`
    : "";
  // range toggle is rendered in the card's combined controls row (see kidCard)
  // Remember every point's position so a tap can show its numbers (see wireChartTips).
  const tipKey = `${k.id}-${expanded ? "x" : "c"}`;
  CHART_TIPS[tipKey] = {
    W, H, top: pad, bottom: H - padB, now,
    pts: past.concat(fut.slice(1)).map((p, i) => ({
      ...p, x: xs(p.t), ys: Object.fromEntries(SERIES.map((s) => [s.m, ys(p[s.m])])),
      could: wfPts && p.t >= now ? (wfPts.find((q) => q.t === p.t) || {}).total : undefined,
    })),
  };
  return `<svg class="chart${expanded ? " chart-expanded" : ""}" data-tip="${tipKey}" viewBox="0 0 ${W} ${H}">${svg}<g class="chart-tip"></g></svg>${banner}${legend}`;
}

// Tap a chart to see the numbers at that moment: a line, a dot on each pool,
// and a small box with the date and the $ of each pool. Drawn inside the SVG,
// so it takes no extra height. Tap the box (or wait) to hide it. The pick is
// kept across the 30s auto-refresh so it doesn't vanish under a kid's finger.
const CHART_TIPS = {};
let CHART_TIP = null;   // { key, t }
let chartTipTimer = null;

function tipDate(t, now) {
  if (Math.abs(t - now) < 86400) return "Today";
  const d = new Date(t * 1000), sameYear = d.getFullYear() === new Date(now * 1000).getFullYear();
  return d.toLocaleDateString(undefined, sameYear
    ? { month: "short", day: "numeric" }
    : { month: "short", day: "numeric", year: "numeric" });
}

function drawChartTip(svg) {
  const g = $(".chart-tip", svg), info = CHART_TIPS[svg.dataset.tip];
  if (!g) return;
  const p = info && CHART_TIP && CHART_TIP.key === svg.dataset.tip
    ? info.pts.find((q) => q.t === CHART_TIP.t) : null;
  if (!p) { g.innerHTML = ""; return; }
  const rows = [...SERIES].reverse().map((s) => [s.c, s.label.split(" ")[0], p[s.m]]);
  if (p.could !== undefined) rows.push(["#9333ea", "🔮", p.could]);
  const bw = 78, rh = 11, bh = 14 + rows.length * rh;
  const bx = p.x > info.W / 2 ? p.x - bw - 6 : p.x + 6, by = info.top;
  let h = `<line x1="${p.x}" y1="${info.top}" x2="${p.x}" y2="${info.bottom}" stroke="#2d2150" stroke-width="1" opacity=".35"/>`;
  SERIES.forEach((s) => {
    h += `<circle cx="${p.x}" cy="${p.ys[s.m]}" r="3.2" fill="#fff" stroke="${s.c}" stroke-width="2"/>`;
  });
  h += `<rect x="${bx}" y="${by}" width="${bw}" height="${bh}" rx="6" fill="#fff" stroke="#2d2150" stroke-opacity=".15"/>`;
  h += `<text x="${bx + 6}" y="${by + 10}" font-size="8" font-weight="800" fill="#2d2150" opacity=".6">${tipDate(p.t, info.now)}</text>`;
  rows.forEach(([c, icon, v], i) => {
    const y = by + 21 + i * rh;
    h += `<text x="${bx + 6}" y="${y}" font-size="8">${icon}</text>
      <text x="${bx + bw - 6}" y="${y}" font-size="8.5" font-weight="900" fill="${c}" text-anchor="end">${dollars(v)}</text>`;
  });
  g.innerHTML = h;
}

function wireChartTips(root) {
  $$("svg.chart[data-tip]", root).forEach((svg) => {
    drawChartTip(svg);
    svg.addEventListener("click", (e) => {
      if (e.target.closest(".future-cycle")) return;
      const info = CHART_TIPS[svg.dataset.tip];
      if (!info) return;
      if (e.target.closest(".chart-tip rect, .chart-tip text")) {
        CHART_TIP = null;
      } else {
        const pt = svg.createSVGPoint();
        pt.x = e.clientX; pt.y = e.clientY;
        const x = pt.matrixTransform(svg.getScreenCTM().inverse()).x;
        const near = info.pts.reduce((a, b) => (Math.abs(b.x - x) < Math.abs(a.x - x) ? b : a));
        CHART_TIP = { key: svg.dataset.tip, t: near.t };
        clearTimeout(chartTipTimer);
        chartTipTimer = setTimeout(() => {
          CHART_TIP = null;
          $$("svg.chart[data-tip]").forEach(drawChartTip);
        }, 10000);
      }
      drawChartTip(svg);
    });
  });
}

/* ---- Render ----------------------------------------------------------- */
function render() {
  const wrap = $("#kids");
  wrap.innerHTML = "";
  if (!STATE.kids.length) {
    wrap.innerHTML = '<p>Welcome! Open Dad controls to add your first kid.</p>';
  }
  const ranges = STATE.kids.map(chartData);
  CARD_SCALE = ranges.length ? {
    lo: Math.min(...ranges.map((r) => r.lo)),
    hi: Math.max(...ranges.map((r) => r.hi)),
  } : null;
  for (const k of STATE.kids) wrap.appendChild(kidCard(k));
  wireChartTips(wrap);
}

function kidCard(k) {
  const el = document.createElement("section");
  el.className = "card";
  el.style.setProperty("--kc", k.color);
  el.style.setProperty("--kc-soft", k.color + "22");

  const hasPending = k.pending_cents > 0;
  const weeklyRate = STATE.config.savings_weekly_pct / 100;

  el.innerHTML = `
    <div class="who">
      <div class="avatar">${esc(k.emoji)}</div>
      <div class="name">${esc(k.name)}</div>
      <div class="total">
        <div class="label">💰 All my money</div>
        <div class="big">${dollars(k.owned_cents)}</div>
        <div class="coins"><span class="coin-count">🪙 ${coins(k.owned_cents)} coins</span> · 💵 ${dollars(k.wallet_cents)} to spend</div>
      </div>
    </div>
    ${moneyChart(k)}
    <div class="chart-controls">
      <div class="wallet-actions">
        <button class="btn-spend" title="Spend">💸</button>
        <button class="btn-whatif" title="What if?">🔮</button>
        <button class="btn-history" title="History">📜</button>
        <button class="btn-expand" title="Expand graph" aria-label="Expand graph">⛶</button>
      </div>
      <div class="chart-range">
        ${RANGES.map(([rk, l]) => `<button class="rng ${rk === chartRange ? "on" : ""}" data-rng="${rk}">${l}</button>`).join("")}
      </div>
    </div>
    ${nextAllowanceChip(k)}
    ${hasPending ? rewardsBlock(k) : ""}

    <div class="pool savings">
      <div class="pool-head">
        <span class="pool-title">🏦 Savings</span>
        <span class="pool-badge">📈 +${STATE.config.savings_weekly_pct}% / week</span>
      </div>
      <div class="pool-val s-val">${dollars(k.savings_cents)}</div>
      ${savingsBody(k, weeklyRate)}
      <button class="btn-save" style="margin-top:10px;width:100%;border:none;border-radius:14px;padding:12px;color:#fff;font-weight:800;font-size:17px">🏦 Save coins</button>
    </div>

    <div class="pool bonds">
      <div class="pool-head">
        <span class="pool-title">🚀 Invest</span>
        <span class="pool-badge">lock it → up to +${bestTerm().return_pct}%!</span>
      </div>
      ${bondsBody(k)}
      <button class="btn-bond" style="margin-top:10px;width:100%;border:none;border-radius:14px;padding:12px;color:#fff;font-weight:800;font-size:17px">🚀 Invest coins</button>
    </div>
  `;

  $$(".reward-btn", el).forEach((b) => {
    b.onclick = (e) => onCollect(k, b.dataset.kind, e);
  });
  $(".btn-save", el).onclick = () => openSaveModal(k);
  $(".btn-bond", el).onclick = () => openBondModal(k);
  $(".btn-spend", el).onclick = () => openSpendModal(k);
  $(".btn-whatif", el).onclick = () => openWhatIf(k);
  $(".btn-history", el).onclick = () => openHistoryModal(k);
  $(".btn-expand", el).onclick = () => openGraphModal(k);
  const cycle = $(".future-cycle", el);
  if (cycle) cycle.onclick = () => {
    const i = FUTURE_RANGES.findIndex(([key]) => key === chartFutureRange);
    setFutureRange(FUTURE_RANGES[(i + 1) % FUTURE_RANGES.length][0]);
    render();   // both cards share the horizon
  };
  $$(".rng", el).forEach((b) => {
    b.onclick = () => {
      chartRange = b.dataset.rng;
      try { localStorage.setItem("bod_range", chartRange); } catch (e) {}
      render();   // re-render both cards with the new range
    };
  });
  const clr = $(".wi-clear", el);
  if (clr) clr.onclick = () => { delete WHATIF[k.id]; render(); };

  // The per-second tick keeps the reward total + invest bars creeping up live.
  el._live = {
    weeklyRate, bonds: k.bonds,
    savingsPrincipal: k.savings_cents,
    rewardBase: k.pending_cents,
    interestBase: k.pending_interest_cents,
  };
  return el;
}

// Friendly "time until" label for a countdown (seconds).
function untilLabel(s) {
  if (s <= 0) return "any moment!";
  const d = s / 86400;
  if (d >= 1.5) return `in ${Math.round(d)} days`;
  if (d >= 0.75) return "tomorrow";
  const h = s / 3600;
  if (h >= 1) return `in ${Math.round(h)} hour${Math.round(h) === 1 ? "" : "s"}`;
  const m = Math.round(s / 60);
  return m > 1 ? `in ${m} min` : "very soon!";
}

// Countdown to the next allowance — only when it isn't already waiting to collect.
function nextAllowanceChip(k) {
  if (!k.allowance_enabled)
    return `<div class="next-allow no-allowance">☹️ No allowance</div>`;
  if (!(k.allowance_cents > 0) || k.pending_allowance_cents > 0 || !k.next_allowance_ts) return "";
  const secs = k.next_allowance_ts - serverNow();
  return `<div class="next-allow">🪙 Next allowance <b>${dollars(k.allowance_cents)}</b>
     <span class="na-when">${untilLabel(secs)}</span></div>`;
}

// The rewards area — a compact tile per income source, side by side on one row
// (wrapping only if they don't fit). The label names the source; the big
// emoji+amount is the satisfying, animated thing you tap. Each source has its
// own currency: 🪙 coins (allowance), 💵 dollars (savings), 💎 gems (invest).
const REWARD_EMOJI = { allowance: "🪙", interest: "💵", invest: "💎" };

function rewardsBlock(k) {
  const tile = (kind, cls, label, cents, numCls = "") =>
    `<button class="reward-btn ${cls}" data-kind="${kind}">
       <span class="rb-label">${label}</span>
       <span class="rb-amt">${REWARD_EMOJI[kind]} <span class="rb-num ${numCls}">+${dollars(cents)}</span></span>
     </button>`;
  let tiles = "";
  if (k.pending_allowance_cents > 0)
    tiles += tile("allowance", "r-allow", "Allowance", k.pending_allowance_cents);
  if (k.pending_interest_cents > 0)
    tiles += tile("interest", "r-int", "Savings Interest", k.pending_interest_cents, "r-interest");
  if (k.pending_bonds_cents > 0)
    tiles += tile("invest", "r-inv", "Investment Income", k.pending_bonds_cents);
  return `<div class="rewards">${tiles}</div>`;
}

// Savings: a concrete "in 3 months" projection (the chart shows the curve).
function savingsBody(k, weeklyRate) {
  if (k.savings_cents <= 0) {
    return `<div class="pool-empty">Tap <b>Save coins</b> and watch them grow every week! 🌱</div>`;
  }
  const future = k.savings_cents * Math.pow(1 + weeklyRate, 13);
  return `<div class="pool-sub">📈 Growing now… in 3 months ≈ <b>${dollars(future)}</b></div>`;
}

// Invest: a progress bar per lock, value climbing toward the big payout.
function bondsBody(k) {
  if (!k.bonds.length) {
    return `<div class="pool-empty">Lock coins away and they grow into a LOT more! 🚀</div>`;
  }
  return k.bonds.map((b, i) =>
    `<div class="bond-row ${b.ready ? "ready" : ""}" data-i="${i}">
       <div class="bond-top">
         <span>${b.emoji} ${b.term} lock</span>
         <span><span class="bond-now">${dollars(bondValue(b))}</span>
               → <span class="bond-goal">${dollars(b.payout_cents)}</span></span>
       </div>
       <div class="bar"><i style="width:${(bondProgress(b) * 100).toFixed(1)}%"></i></div>
       <div class="bond-days">${b.ready
         ? "✅ Ready! Tap Get rewards 🎉"
         : `${b.days_left} day${b.days_left === 1 ? "" : "s"} until payday 🎉`}</div>
     </div>`
  ).join("");
}

const bondProgress = (b) => {
  if (b.ready) return 1;
  const span = b.mature_ts - b.buy_ts || 1;
  return Math.max(0, Math.min(1, (serverNow() - b.buy_ts) / span));
};
const bondValue = (b) =>
  b.ready ? b.payout_cents
    : Math.round(b.principal_cents + (b.payout_cents - b.principal_cents) * bondProgress(b));

// Per-second tick: the hero is settled money (changes only when you collect),
// but the REWARD waiting to be collected creeps up as savings interest accrues,
// and the invest progress bars advance.
function tick() {
  const now = serverNow();
  $$("#kids .card").forEach((card) => {
    const live = card._live;
    if (!live) return;
    if (live.savingsPrincipal > 0) {
      const grown = live.savingsPrincipal * live.weeklyRate * ((now - SERVER_NOW) / WEEK);
      const intEl = $(".r-interest", card);   // savings-interest reward creeps up
      if (intEl) intEl.textContent = `+${dollars(live.interestBase + grown)}`;
    }
    $$(".bond-row", card).forEach((row) => {
      const b = live.bonds[+row.dataset.i];
      if (!b) return;
      $(".bond-now", row).textContent = dollars(bondValue(b));
      $(".bar > i", row).style.width = (bondProgress(b) * 100).toFixed(1) + "%";
    });
  });
}

/* ---- Actions ---------------------------------------------------------- */
// Collect is OPTIMISTIC: all the fun (sound, coins, number spin) happens instantly
// on tap, and the server call runs in the background — so latency to the server
// never leaves a kid tapping a dead button. We already know the amount client-side.
function onCollect(k, kind, ev) {
  const btn = ev.currentTarget;
  if (!btn || btn._busy) return;                 // guard against double-tap
  btn._busy = true;
  audioCtx();

  const amounts = {
    allowance: k.pending_allowance_cents,
    interest: k.pending_interest_cents,
    invest: k.pending_bonds_cents,               // the hero grows by the gain
  };
  const delta = amounts[kind] || 0;
  const rect = btn.closest(".card")?.querySelector(".chart")?.getBoundingClientRect();

  // instant feedback
  playRewardSound();
  const n = Math.min(60, 16 + coins(delta));
  if (rect) rainOver(rect, n, REWARD_EMOJI[kind]);
  else burst(ev.clientX, ev.clientY, n, REWARD_EMOJI[kind]);
  if (delta > 0) spinHero(k.id, delta);
  btn.remove();                                  // gone immediately — can't re-tap

  // fire the real collect in the background; resync when it returns (or fails)
  api("/api/collect", { kid_id: k.id, kind }).then(refresh, refresh);
}

// Spin the hero number UP by `delta`, starting from whatever's currently shown
// (so rapid back-to-back collects chain correctly), with the coin count.
function spinHero(kidId, delta) {
  const cards = $$("#kids .card");
  const idx = STATE.kids.findIndex((x) => x.id === kidId);
  const card = cards[idx];
  const bigEl = card && $(".big", card);
  if (!bigEl) return;
  const ccEl = $(".coin-count", card);
  const fromC = Math.round((parseFloat(bigEl.textContent.replace(/[^0-9.]/g, "")) || 0) * 100);
  const toC = fromC + delta;
  const start = performance.now(), dur = 1100;
  const step = (t) => {
    const p = Math.min(1, (t - start) / dur);
    const ease = 1 - Math.pow(1 - p, 3);          // easeOutCubic
    const v = Math.round(fromC + (toC - fromC) * ease);
    bigEl.textContent = dollars(v);
    if (ccEl) ccEl.textContent = `🪙 ${coins(v)} coins`;
    bigEl.classList.toggle("pop", p < 1);
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function modalClose() {
  $("#modal").classList.add("hidden");
  $("#modal").classList.remove("graph-panel");
  $("#modalCard").classList.remove("graph-modal-card");
}

function openGraphModal(k) {
  const modal = $("#modal");
  const card = $("#modalCard");
  modal.classList.add("graph-panel");
  card.classList.add("graph-modal-card");

  const draw = () => {
    const current = STATE.kids.find((kid) => kid.id === k.id) || k;
    card.innerHTML = `
      <div class="graph-modal-head">
        <h2>${esc(current.emoji)} ${esc(current.name)}'s Money Over Time</h2>
        <button class="graph-close" aria-label="Close graph">✕</button>
      </div>
      <div class="expanded-chart">${moneyChart(k, true)}</div>
      <div class="graph-future-controls">
        <span>Look into the future:</span>
        ${FUTURE_RANGES.map(([key, label]) => `<button class="future-rng ${key === chartFutureRange ? "on" : ""}" data-future-range="${key}">${label}</button>`).join("")}
      </div>`;
    $(".graph-close", card).onclick = modalClose;
    wireChartTips(card);
    $$(".future-rng", card).forEach((button) => {
      button.onclick = () => {
        setFutureRange(button.dataset.futureRange);
        draw();
        render();   // keep the cards on the same horizon
      };
    });
    const clear = $(".wi-clear", card);
    if (clear) clear.onclick = () => { delete WHATIF[k.id]; draw(); render(); };
  };

  draw();
  modal.classList.remove("hidden");
}

/* ---- "What if?" overlay builder -------------------------------------- */
// Pick how much to put into savings / a 1-month lock / a 3-month lock, then tap
// "Let's see!" to overlay that trajectory on the real graph (current vs could-be).
function openWhatIf(k) {
  const card = $("#modalCard");
  const cfg = STATE.config;
  const b1 = cfg.bonds.find((b) => b.key === "1mo") || cfg.bonds[0];
  const b3 = cfg.bonds.find((b) => b.key === "3mo") || cfg.bonds[cfg.bonds.length - 1];
  const pre = WHATIF[k.id] || { save: 0, m1: 0, m3: 0 };
  const f = (c) => (c ? (c / 100).toFixed(2) : "");
  card.innerHTML = `
    <h2>🔮 What if?</h2>
    <p>Try putting some coins to work, then tap <b>Let's see!</b> to watch your
       graph show where your money <i>could</i> go. (You have <b>${dollars(k.wallet_cents)}</b>.)</p>
    <div class="wi-input"><span>🏦 Save</span><span class="wi-dollar">$</span>
      <input class="wi-save" type="number" inputmode="decimal" step="0.5" min="0" placeholder="0" value="${f(pre.save)}"></div>
    <div class="wi-input"><span>🌱 ${b1.label} invest <small>(+${b1.return_pct}%)</small></span><span class="wi-dollar">$</span>
      <input class="wi-m1" type="number" inputmode="decimal" step="0.5" min="0" placeholder="0" value="${f(pre.m1)}"></div>
    <div class="wi-input"><span>🚀 ${b3.label} invest <small>(+${b3.return_pct}%)</small></span><span class="wi-dollar">$</span>
      <input class="wi-m3" type="number" inputmode="decimal" step="0.5" min="0" placeholder="0" value="${f(pre.m3)}"></div>
    <button class="modal-go" style="background:#7c3aed;margin-top:6px">🔮 Let's see!</button>
    <button class="close">Cancel</button>`;
  const cents = (sel) => Math.round((parseFloat($(sel, card).value) || 0) * 100);
  $(".modal-go", card).onclick = () => {
    const wf = { save: cents(".wi-save"), m1: cents(".wi-m1"), m3: cents(".wi-m3") };
    if (wf.save + wf.m1 + wf.m3 <= 0) { delete WHATIF[k.id]; }
    else WHATIF[k.id] = wf;
    modalClose();
    render();
  };
  $(".close", card).onclick = modalClose;
  $("#modal").classList.remove("hidden");
}

function amountGrid(maxCents, min) {
  const opts = [100, 200, 500, 1000].filter((c) => c <= maxCents);
  if (maxCents >= min && !opts.includes(maxCents)) opts.push(maxCents);
  return opts.map((c) =>
    `<button data-amt="${c}">${c === maxCents ? "All " : ""}${dollars(c)}</button>`
  ).join("") || `<p>Not enough coins yet — collect some rewards first! 🎁</p>`;
}

// Amount picker: quick preset buttons + a free-entry "$" field, kept in sync.
// card._amt (cents) is the source of truth. onChange fires after any change.
function wireAmountGrid(card, onChange) {
  const custom = $(".custom-amt", card);
  const clearSel = () => $$(".amount-grid button", card).forEach((x) => x.classList.remove("sel"));
  $$(".amount-grid button", card).forEach((b) => {
    b.onclick = () => {
      clearSel();
      b.classList.add("sel");
      card._amt = parseInt(b.dataset.amt, 10);
      if (custom) custom.value = (card._amt / 100).toFixed(2);
      onChange && onChange();
    };
  });
  if (custom) custom.oninput = () => {
    clearSel();
    const v = parseFloat(custom.value || "0");
    card._amt = v > 0 ? Math.round(v * 100) : 0;
    onChange && onChange();
  };
}

// Treat Enter as "done" on tablet number keyboards without submitting money.
function wireEnterToBlur(card) {
  $$('input[type="number"]', card).forEach((input) => {
    input.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      input.blur();
    });
  });
}

function openSaveModal(k) {
  const card = $("#modalCard");
  const cfg = STATE.config;
  card.innerHTML = `
    <h2>🏦 ${esc(k.name)}'s Savings</h2>
    <p>Savings grow <b>${cfg.savings_weekly_pct}% every week</b>! 📈<br>
       You have <b>${dollars(k.wallet_cents)}</b> coins to save.</p>
    <div class="amount-grid">${amountGrid(k.wallet_cents, cfg.min_move_cents)}</div>
    <div class="spend-amt-row"><span class="spend-dollar">$</span>
      <input class="custom-amt savings-amt" type="number" inputmode="decimal" enterkeyhint="done" step="0.5" min="0" placeholder="or type any amount"></div>
    <button class="modal-go" data-act="save">🏦 Save it!</button>
    ${k.savings_cents > 0 ? `
    <div class="spend-amt-row"><span class="spend-dollar">$</span>
      <input class="withdraw-amt savings-amt" type="number" inputmode="decimal" enterkeyhint="done" step="0.5" min="0" placeholder="amount to take out (blank = all)"></div>
    <button class="modal-go" style="background:#888" data-act="withdraw">💵 Take out of savings (${dollars(k.savings_cents)} saved)</button>` : ""}
    <button class="close">Cancel</button>
  `;
  wireAmountGrid(card);
  wireEnterToBlur(card);
  $(".close", card).onclick = modalClose;
  $$(".modal-go", card).forEach((btn) => {
    btn.onclick = async (e) => {
      const act = btn.dataset.act;
      if (act === "save") {
        if (!card._amt) return;
        await api("/api/save", { kid_id: k.id, amount_cents: card._amt });
        burst(e.clientX, e.clientY, 10, "📈");
      } else {
        // Take out the typed amount; a blank/zero field means take out everything.
        const v = parseFloat(($(".withdraw-amt", card) || {}).value || "0");
        const cents = v > 0 ? Math.round(v * 100) : k.savings_cents;
        await api("/api/withdraw", { kid_id: k.id, amount_cents: cents });
      }
      modalClose();
      await refresh();
    };
  });
  $("#modal").classList.remove("hidden");
}

function openBondModal(k) {
  const card = $("#modalCard");
  const cfg = STATE.config;
  card._term = cfg.bonds[0].key;
  const picks = cfg.bonds.map((b, i) =>
    `<button data-term="${b.key}" class="${i === 0 ? "sel" : ""}">
       ${b.emoji} ${b.label}<br><small>+${b.return_pct}% 🤑</small></button>`
  ).join("");
  card.innerHTML = `
    <h2>🚀 Invest in the Bank of Dad</h2>
    <p>Lock your coins away for a while and they grow into a <b>LOT</b> more!
       Locking longer pays off most. Pick how long:</p>
    <div class="bond-pick">${picks}</div>
    <p>How many coins to lock? (You have <b>${dollars(k.wallet_cents)}</b>)</p>
    <div class="amount-grid">${amountGrid(k.wallet_cents, cfg.min_move_cents)}</div>
    <div class="spend-amt-row"><span class="spend-dollar">$</span>
      <input class="custom-amt" type="number" inputmode="decimal" step="0.5" min="0" placeholder="or type any amount"></div>
    <div id="bondPreview" style="text-align:center;font-size:20px;font-weight:bold;min-height:26px"></div>
    <button class="modal-go" style="background:#8b5cf6">🔒 Lock it in!</button>
    <button class="close">Cancel</button>
  `;
  const preview = () => {
    const b = cfg.bonds.find((x) => x.key === card._term);
    const el = $("#bondPreview");
    if (card._amt && b) {
      const payout = Math.round(card._amt * (1 + b.return_pct / 100));
      el.textContent = `${dollars(card._amt)} → ${dollars(payout)} in ${b.days} days! 🎉`;
    } else { el.textContent = ""; }
  };
  wireAmountGrid(card, preview);
  $$(".bond-pick button", card).forEach((b) => {
    b.onclick = () => {
      $$(".bond-pick button", card).forEach((x) => x.classList.remove("sel"));
      b.classList.add("sel");
      card._term = b.dataset.term;
      preview();
    };
  });
  $(".close", card).onclick = modalClose;
  $(".modal-go", card).onclick = async (e) => {
    if (!card._amt) return;
    const r = await api("/api/bond", { kid_id: k.id, term: card._term, amount_cents: card._amt });
    if (r.ok) burst(e.clientX, e.clientY, 14, "🚀");
    modalClose();
    await refresh();
  };
  $("#modal").classList.remove("hidden");
}

function openSpendModal(k) {
  const card = $("#modalCard");
  const cfg = STATE.config;
  card.innerHTML = `
    <h2>💸 Spend coins</h2>
    <p>You have <b>${dollars(k.wallet_cents)}</b> to spend. What did you buy?</p>
    <input class="spend-reason" placeholder="What for? (e.g. ice cream 🍦)" maxlength="40">
    <div class="spend-amt-row">
      <span class="spend-dollar">$</span>
      <input class="spend-amt" type="number" inputmode="decimal" step="0.25" min="0"
             max="${(k.wallet_cents / 100).toFixed(2)}" placeholder="0.00">
    </div>
    <button class="modal-go" style="background:#e0457b">💸 Spend it!</button>
    <button class="close">Cancel</button>
  `;
  $(".close", card).onclick = modalClose;
  $(".modal-go", card).onclick = async (e) => {
    const v = parseFloat($(".spend-amt", card).value || "0");
    if (!(v > 0)) return;
    const reason = $(".spend-reason", card).value.trim();
    const r = await api("/api/spend", { kid_id: k.id, amount_cents: Math.round(v * 100), reason });
    if (r.ok) burst(e.clientX, e.clientY, 12, "💸");
    modalClose();
    await refresh();
  };
  $("#modal").classList.remove("hidden");
}

// Kid-friendly metadata for each transaction kind.
const TX_META = {
  allowance:   { emoji: "🪙", sign: "+", cls: "tx-pos" },
  interest:    { emoji: "💵", sign: "+", cls: "tx-pos", tail: "→ savings" },
  bond_payout: { emoji: "💎", sign: "+", cls: "tx-pos" },
  gift:        { emoji: "🎁", sign: "+", cls: "tx-pos" },
  spend:       { emoji: "🛍️", sign: "-", cls: "tx-neg" },
  save:        { emoji: "🏦", sign: "",  cls: "tx-move" },
  withdraw:    { emoji: "🏦", sign: "",  cls: "tx-move" },
  invest:      { emoji: "🚀", sign: "",  cls: "tx-move" },
};

function agoLabel(ts) {
  const s = Math.max(0, serverNow() - ts);
  if (s < 90) return "just now";
  const m = Math.round(s / 60); if (m < 60) return `${m} min ago`;
  const h = Math.round(s / 3600); if (h < 24) return `${h} hr ago`;
  const d = Math.round(s / 86400); return d === 1 ? "yesterday" : `${d} days ago`;
}

async function openHistoryModal(k) {
  const card = $("#modalCard");
  card.innerHTML = `<h2>📜 ${esc(k.name)}'s History</h2><div class="tx-list">Loading…</div>
    <button class="close">Close</button>`;
  $(".close", card).onclick = modalClose;
  $("#modal").classList.remove("hidden");
  const r = await api(`/api/transactions?kid_id=${k.id}`);
  const list = $(".tx-list", card);
  if (!r.transactions || !r.transactions.length) {
    list.innerHTML = `<p class="pool-empty">No history yet — collect, save or spend to get started! 🌱</p>`;
    return;
  }
  list.innerHTML = r.transactions.map((t) => {
    const m = TX_META[t.kind] || { emoji: "•", sign: "", cls: "" };
    const amt = `${m.sign}${dollars(Math.abs(t.amount_cents))}`;
    return `<div class="tx-row">
      <span class="tx-emoji">${m.emoji}</span>
      <span class="tx-note">${esc(t.note || t.kind)}${m.tail ? ` <i>${m.tail}</i>` : ""}<br><span class="tx-time">${agoLabel(t.ts)}</span></span>
      <span class="tx-amt ${m.cls}">${amt}</span>
    </div>`;
  }).join("");
}

// Dad-only: edit/delete transactions (balance is corrected to match).
const AMOUNT_EDITABLE = new Set(["gift", "allowance", "interest", "spend"]);

async function openEditHistory(k) {
  const card = $("#modalCard");
  const render = (txs) => {
    card.innerHTML = `<h2>📜 Edit ${esc(k.name)}'s transactions</h2>
      <p style="font-size:13px;opacity:.65">Editing an amount or deleting a row also fixes the balance.</p>
      <div class="etx-list">${txs.length ? "" : '<p class="pool-empty">No transactions yet.</p>'}</div>
      <button class="close">Close</button>`;
    const list = $(".etx-list", card);
    txs.forEach((t) => {
      const m = TX_META[t.kind] || { emoji: "•" };
      const editable = AMOUNT_EDITABLE.has(t.kind);
      const row = document.createElement("div");
      row.className = "etx";
      row.innerHTML = `
        <span class="etx-emoji">${m.emoji}</span>
        <input class="etx-note" value="${esc(t.note || "")}">
        <span class="etx-amtwrap">$<input class="etx-amt" type="number" step="0.25" min="0"
           value="${(Math.abs(t.amount_cents) / 100).toFixed(2)}" ${editable ? "" : "disabled title='amount fixed for this type'"}></span>
        <button class="etx-save">Save</button>
        <button class="etx-del">🗑️</button>`;
      $(".etx-save", row).onclick = async () => {
        const note = $(".etx-note", row).value;
        const body = { tx_id: t.id, note };
        if (editable) body.amount_cents = Math.round(parseFloat($(".etx-amt", row).value || "0") * 100);
        const r = await api("/api/admin/tx/edit", body);
        $(".etx-save", row).textContent = r.ok ? "✓" : "✗";
        await refresh();
      };
      $(".etx-del", row).onclick = async () => {
        if (!confirm(`Delete "${t.note}"? This also fixes the balance.`)) return;
        const r = await api("/api/admin/tx/delete", { tx_id: t.id });
        if (!r.ok) { alert(r.message); return; }
        await refresh();
        load();
      };
      list.appendChild(row);
    });
    $(".close", card).onclick = modalClose;
  };
  const load = async () => {
    const r = await api(`/api/transactions?kid_id=${k.id}`);
    render(r.transactions || []);
  };
  card.innerHTML = `<h2>📜 Edit ${esc(k.name)}'s transactions</h2><p>Loading…</p>`;
  $("#modal").classList.remove("hidden");
  await load();
}

/* ---- Reward sound (synthesized, no audio file) ----------------------- */
let _actx = null;
function audioCtx() {
  try {
    _actx = _actx || new (window.AudioContext || window.webkitAudioContext)();
    if (_actx.state === "suspended") _actx.resume();   // must be called from a gesture
    return _actx;
  } catch (e) { return null; }
}
// A cheerful ascending chime + a little sparkle, like collecting coins.
function playRewardSound() {
  const ctx = audioCtx();
  if (!ctx) return;
  const t0 = ctx.currentTime;
  const note = (f, t, dur, gain, type) => {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type || "triangle"; o.frequency.value = f;
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(gain, t + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g).connect(ctx.destination);
    o.start(t); o.stop(t + dur + 0.02);
  };
  [523.25, 659.25, 783.99, 1046.5].forEach((f, i) => note(f, t0 + i * 0.085, 0.22, 0.2));
  note(1568.0, t0 + 0.34, 0.4, 0.12, "sine");   // sparkle on top
}

/* ---- Confetti / coin burst ------------------------------------------- */
function burst(x, y, count = 16, emoji) {
  const pool = emoji ? [emoji] : ["🪙", "💰", "⭐", "🎉", "✨"];
  const layer = $("#confetti");
  for (let i = 0; i < count; i++) {
    const bit = document.createElement("div");
    bit.className = "confetti-bit";
    bit.textContent = pool[i % pool.length];
    bit.style.left = (x || window.innerWidth / 2) + (Math.random() * 120 - 60) + "px";
    bit.style.top = (y || 120) + "px";
    bit.style.animation = `fall ${1 + Math.random()}s ease-in forwards`;
    layer.appendChild(bit);
    setTimeout(() => bit.remove(), 2200);
  }
}

// Rain currency across a region (the chart) — bits start spread along the top
// edge and fall through the whole area.
function rainOver(rect, count = 24, emoji) {
  const pool = emoji ? [emoji] : ["🪙", "💰", "⭐", "🎉", "✨"];
  const layer = $("#confetti");
  const dist = rect.height + 36;
  for (let i = 0; i < count; i++) {
    const bit = document.createElement("div");
    bit.className = "confetti-bit";
    bit.textContent = pool[i % pool.length];
    bit.style.left = (rect.left + Math.random() * rect.width) + "px";
    bit.style.top = (rect.top - 14) + "px";
    bit.style.animation = "none";
    layer.appendChild(bit);
    const dur = 900 + Math.random() * 900;
    const anim = bit.animate(
      [
        { transform: "translateY(0) rotate(0deg)", opacity: 1 },
        { transform: `translateY(${dist}px) rotate(${360 + Math.random() * 360}deg)`, opacity: 0 },
      ],
      { duration: dur, delay: Math.random() * 350, easing: "cubic-bezier(.35,.15,.6,1)", fill: "forwards" }
    );
    anim.onfinish = () => bit.remove();
    setTimeout(() => bit.remove(), dur + 600);
  }
}

/* ---- Dad PIN gate ---------------------------------------------------- */
// A big tappable numeric keypad. Resolves to the entered PIN, or null if
// cancelled. opts.title sets the heading; opts.mandatory hides Cancel and
// disables backdrop-dismiss (used for the forced first-run setup).
function askPin(err, opts = {}) {
  const title = opts.title || "🔒 Dad PIN";
  return new Promise((resolve) => {
    let pin = "";
    const pad = document.createElement("div");
    pad.className = "panel";
    const draw = () => {
      pad.innerHTML = `
        <div class="panel-card pin-card">
          <h2>${title}</h2>
          ${err ? `<div class="pin-err">${err}</div>` : ""}
          <div class="pin-dots">${"●".repeat(pin.length)}<span class="pin-empty">${"○".repeat(Math.max(0, 4 - pin.length))}</span></div>
          <div class="keypad">
            ${[1,2,3,4,5,6,7,8,9].map((n) => `<button data-k="${n}">${n}</button>`).join("")}
            <button data-k="clear">⌫</button>
            <button data-k="0">0</button>
            <button data-k="ok" class="kp-ok">✓</button>
          </div>
          ${opts.mandatory ? "" : `<button class="close">Cancel</button>`}
        </div>`;
      $$(".keypad button", pad).forEach((b) => {
        b.onclick = () => {
          const k = b.dataset.k;
          if (k === "clear") pin = pin.slice(0, -1);
          else if (k === "ok") { cleanup(); resolve(pin); return; }
          else if (pin.length < 4) pin += k;
          draw();
          // 4-digit PINs auto-submit (after the 4th dot shows)
          if (pin.length === 4) setTimeout(() => { cleanup(); resolve(pin); }, 160);
        };
      });
      if (!opts.mandatory) {
        $(".close", pad).onclick = () => { cleanup(); resolve(null); };
        pad.onclick = (e) => { if (e.target === pad) { cleanup(); resolve(null); } };
      }
    };
    const cleanup = () => pad.remove();
    draw();
    document.body.appendChild(pad);
  });
}

// First-run: if no Dad PIN exists yet, force setting one before the app is used,
// so the Dad controls are never left unprotected.
async function ensurePinSetup() {
  const st = await api("/api/admin/status");
  if (st.pin_set) return;
  let err = "Welcome! Create a Dad PIN to protect the controls.";
  while (true) {
    const p1 = await askPin(err, { mandatory: true, title: "🔒 Set Dad PIN" });
    if (!p1 || p1.length < 4) { err = "Use at least 4 digits."; continue; }
    const p2 = await askPin("Re-enter to confirm", { mandatory: true, title: "🔒 Confirm PIN" });
    if (p1 !== p2) { err = "PINs didn't match — try again."; continue; }
    const r = await api("/api/admin/pin", { new_pin: p1 });
    if (r.ok) { DAD_PIN = null; return; }   // don't linger; re-entered on first Dad open
    err = r.message || "Couldn't save — try again.";
  }
}

/* ---- Dad panel ------------------------------------------------------- */
async function openDad() {
  const st = await api("/api/admin/status");
  if (st.pin_set) {                  // ask for the PIN every single time
    let err = "";
    while (true) {
      const entered = await askPin(err);
      if (entered === null) return;               // cancelled
      const v = await api("/api/admin/verify", { pin: entered });
      if (v.ok) { DAD_PIN = entered; break; }
      err = "Nope, try again!";
    }
  }
  renderDadPanel();
}

// Forget the PIN whenever the Dad panel closes, so it must be re-entered.
function closeDad() {
  $("#dadPanel").classList.add("hidden");
  DAD_PIN = null;
}

function renderDadPanel() {
  const box = $("#dadKids");
  box.innerHTML = STATE.kids.map((k) => `
    <div class="dk" data-id="${k.id}">
      <div class="dk-head">${esc(k.emoji)} ${esc(k.name)} — ${dollars(k.owned_cents)}</div>
      <div class="row gift-row">
        <input type="number" placeholder="$ amount" class="gift-amt" step="0.5" min="0">
        <input type="text" placeholder="What for? (e.g. chores 🧹)" class="gift-note" maxlength="40">
        <button class="gift-btn" data-id="${k.id}">🎁 Give money</button>
      </div>
      <div class="row dk-allow">
        <label class="alw-toggle"><input type="checkbox" class="alw-enabled" ${k.allowance_enabled ? "checked" : ""}> Allowance on</label>
        <span>💵 Allowance $<input type="number" class="alw-amt" step="0.5" min="0" value="${(k.allowance_cents / 100).toFixed(2)}"></span>
        <span>every <input type="number" class="alw-days" min="1" step="1" value="${k.allowance_period_days}"> days</span>
        <button class="alw-btn" data-id="${k.id}">Save</button>
      </div>
      <div class="row">
        <button class="edithist-btn" data-id="${k.id}">📜 Edit transactions</button>
      </div>
    </div>`).join("");
  $$(".edithist-btn", box).forEach((btn) => {
    btn.onclick = () => openEditHistory(STATE.kids.find((x) => x.id === +btn.dataset.id));
  });
  $$(".gift-btn", box).forEach((btn) => {
    btn.onclick = async () => {
      const dk = btn.closest(".dk");
      const v = parseFloat($(".gift-amt", dk).value || "0");
      if (v > 0) {
        // Same shape as the Spend flow: an amount plus a reason for the ledger.
        const note = $(".gift-note", dk).value.trim();
        await api("/api/admin/gift",
          { kid_id: +btn.dataset.id, amount_cents: Math.round(v * 100), note });
        await refresh();
        renderDadPanel();
      }
    };
  });
  $$(".alw-btn", box).forEach((btn) => {
    btn.onclick = async () => {
      const dk = btn.closest(".dk");
      const amt = parseFloat($(".alw-amt", dk).value || "0");
      const days = parseInt($(".alw-days", dk).value || "0", 10);
      const enabled = $(".alw-enabled", dk).checked;
      if (amt >= 0 && days >= 1) {
        await api("/api/admin/kid/allowance",
          { kid_id: +btn.dataset.id, amount_cents: Math.round(amt * 100), period_days: days, enabled });
        btn.textContent = "Saved ✓";
        await refresh();
      }
    };
  });
  $$(".alw-enabled", box).forEach((toggle) => {
    const update = () => toggle.closest(".dk-allow").classList.toggle("is-off", !toggle.checked);
    toggle.onchange = update;
    update();
  });
  renderDadRates();
  $("#dadPanel").classList.remove("hidden");
}

// Dad-editable savings + investment rates.
function renderDadRates() {
  const cfg = STATE.config;
  const box = $("#dadRates");
  if (!box) return;
  box.innerHTML = `
    <div class="row dk-allow"><span>🏦 Savings</span>
      <input class="rate-sav" type="number" step="0.5" min="0" value="${cfg.savings_weekly_pct}"><span>% / week</span></div>
    ${cfg.bonds.map((b) => `
      <div class="row dk-allow"><span>${b.emoji} ${b.label} invest</span>
        <input class="rate-bond" data-key="${b.key}" type="number" step="5" min="0" value="${b.return_pct}"><span>%</span></div>`).join("")}
    <div class="row"><button id="saveRates" class="go">Save rates</button></div>`;
  $("#saveRates").onclick = async () => {
    const returns = {};
    $$(".rate-bond").forEach((i) => { returns[i.dataset.key] = parseFloat(i.value || "0"); });
    await api("/api/admin/rates", {
      savings_weekly_pct: parseFloat($(".rate-sav").value || "0"),
      returns,
    });
    $("#saveRates").textContent = "Saved ✓";
    await refresh();
    renderDadRates();
  };
}

async function changePin() {
  const st = await api("/api/admin/status");
  let current = null;
  if (st.pin_set) {
    current = DAD_PIN || (await askPin("Enter current PIN"));
    if (current === null) return;
  }
  const next = await askPin(st.pin_set ? "Enter NEW PIN (✓ empty to remove)" : "Set a 4-digit PIN");
  if (next === null) return;
  const r = await api("/api/admin/pin", { current_pin: current, new_pin: next });
  alert(r.message || (r.ok ? "Saved" : "Failed"));
  if (r.ok) DAD_PIN = next || null;
}

function wireGlobal() {
  $("#dadBtn").onclick = openDad;
  $("#closeDad").onclick = closeDad;
  $("#addKidBtn").onclick = async () => {
    const name = $("#newKidName").value.trim();
    if (!name) return;
    await api("/api/admin/kid", { name, emoji: $("#newKidEmoji").value || "🐣" });
    $("#newKidName").value = "";
    await refresh();
    renderDadPanel();
  };
  $("#changePinBtn").onclick = changePin;
  $$(".ff").forEach((b) => {
    b.onclick = async () => {
      await api("/api/admin/fastforward", { days: +b.dataset.days });
      await refresh();
      renderDadPanel();
    };
  });
  // tap backdrop to close
  $("#modal").onclick = (e) => { if (e.target.id === "modal") modalClose(); };
  $("#dadPanel").onclick = (e) => { if (e.target.id === "dadPanel") closeDad(); };
}

// Register the service worker for installable/offline PWA. Only works in a
// secure context (HTTPS or localhost) — over plain http it's skipped silently.
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

wireGlobal();
(async () => {
  await refresh();
  await ensurePinSetup();      // force a Dad PIN on first launch
})();
setInterval(refresh, 30000); // gentle auto-refresh for the radiator
setInterval(tick, 1000);     // smooth live growth of savings + bonds
