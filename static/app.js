/* THE OBSERVATORY — one frontend, every telescope.
   Nothing here knows about LLMs, defense or capital: the table columns, the
   weighting sliders and the podium stats are all rendered from the metadata
   each telescope declares server-side. */

const $ = (s) => document.querySelector(s);

let state = {
  scopes: [],          // catalog
  slug: null,          // active telescope
  meta: null,          // active telescope metadata
  rows: [],
  panel: null,
  weights: {},
  sort: { key: "rank", dir: 1 },
  search: "",
  scoredOnly: false,
  hideNoise: true,
};

/* ── formatters ─────────────────────────────── */
function fmtNum(n) {
  if (n === null || n === undefined || n === "") return "<span class=dim>—</span>";
  if (typeof n !== "number") return String(n);
  return (Math.round(n * 100) / 100).toLocaleString();
}
function fmtInt(n) {
  if (n === null || n === undefined) return "<span class=dim>—</span>";
  if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return Math.round(n).toLocaleString();
}
function fmtMoney(n) {
  if (n === null || n === undefined) return "<span class=dim>—</span>";
  const a = Math.abs(n);
  if (a >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return "$" + (n / 1e3).toFixed(1) + "K";
  return "$" + Math.round(n).toLocaleString();
}
function fmtPrice(p) {
  if (p === null || p === undefined) return "<span class=dim>—</span>";
  return "$" + (p * 1e6).toFixed(2);
}
function fmtPct(n) {
  if (n === null || n === undefined) return "<span class=dim>—</span>";
  return n.toFixed(1) + "%";
}
function fmtSigned(n) {
  if (n === null || n === undefined) return "<span class=dim>—</span>";
  const cls = n > 0 ? "up" : n < 0 ? "down" : "";
  return `<span class="${cls}">${n > 0 ? "+" : ""}${n.toFixed(2)}</span>`;
}
function fmtText(s) {
  if (s === null || s === undefined || s === "") return "<span class=dim>—</span>";
  return String(s);
}
const FORMATTERS = {
  num: fmtNum, int: fmtInt, money: fmtMoney, price: fmtPrice,
  pct: fmtPct, signed: fmtSigned, text: fmtText, date: fmtText,
  score: (n) => (n === null || n === undefined ? "—" : n),
  rank: (n) => (n === null || n === undefined ? "<span class=dim>—</span>" : "#" + n),
};
function cell(row, col) {
  return (FORMATTERS[col.fmt] || fmtNum)(row[col.field]);
}
function ago(s) {
  if (s === null || s === undefined) return "—";
  if (s < 60) return Math.round(s) + "s";
  if (s < 3600) return Math.round(s / 60) + "m";
  if (s < 86400) return Math.round(s / 3600) + "h";
  return Math.round(s / 86400) + "d";
}

/* ── score calculation tooltip ─────────────── */
const tip = document.getElementById("calc-tip");
let tipKey = null;

function fmtRaw(field, value) {
  const col = state.meta && state.meta.columns.find((c) => c.field === field);
  return (FORMATTERS[(col && col.fmt) || "num"] || fmtNum)(value);
}

function rowByKey(key) {
  return state.rows.find((r) => String(r.key) === key);
}

function buildTip(row) {
  const breakdown = row.score_breakdown || {};
  const entries = Object.values(breakdown).sort((a, b) => b.points - a.points);
  const rowsHtml = entries
    .map(
      (b) => `<div class="ct-row">
        <span class="ct-label">${b.label}</span>
        <span class="ct-raw">${fmtRaw(b.field, b.raw)}</span>
        <span class="ct-weight">×${b.weight}</span>
        <span class="ct-pts">${b.points.toFixed(1)}</span>
      </div>`
    )
    .join("");
  let dampenNote = "";
  if (row.dampened) {
    const meta = state.meta || {};
    const qualityLabels = (meta.signals || [])
      .filter((s) => (meta.quality_signals || []).includes(s.key))
      .map((s) => s.label);
    dampenNote = `<div class="ct-formula"><span class="ct-dampen">⚠ ×${meta.dampen}</span> — no independent evidence present (needs one of: ${qualityLabels.join(", ") || "a quality signal"}).</div>`;
  }
  return `
    <div class="ct-head">
      <span class="ct-name">${row.name || ""}</span>
      <span class="ct-score">${row.score ?? "—"}<span>/100</span></span>
    </div>
    <div class="ct-colheads"><span>SIGNAL</span><span>RAW</span><span>WEIGHT</span><span>PTS</span></div>
    ${rowsHtml || '<div class="ct-row"><span class="ct-label dim">no scored signals</span></div>'}
    <div class="ct-formula">SCORE = Σ(NORMALISED × WEIGHT) ÷ Σ(WEIGHT)${row.dampened ? " × DAMPEN" : ""}</div>
    ${dampenNote}`;
}

function positionTip(x, y) {
  const pad = 16;
  const w = tip.offsetWidth || 300;
  const h = tip.offsetHeight || 120;
  let left = x + pad;
  let top = y + pad;
  if (left + w > window.innerWidth - 8) left = x - w - pad;
  if (top + h > window.innerHeight - 8) top = y - h - pad;
  tip.style.left = Math.max(8, left) + "px";
  tip.style.top = Math.max(8, top) + "px";
}

function hideTip() {
  tipKey = null;
  tip.classList.remove("show");
  tip.hidden = true;
}

function wireTipDelegation(container) {
  container.addEventListener("mousemove", (e) => {
    const el = e.target.closest("[data-key]");
    if (!el) {
      if (tipKey !== null) hideTip();
      return;
    }
    if (el.dataset.key !== tipKey) {
      const row = rowByKey(el.dataset.key);
      if (!row || !row.score_breakdown) { hideTip(); return; }
      tipKey = el.dataset.key;
      tip.innerHTML = buildTip(row);
      tip.hidden = false;
      requestAnimationFrame(() => tip.classList.add("show"));
    }
    positionTip(e.clientX, e.clientY);
  });
  container.addEventListener("mouseleave", hideTip);
}
wireTipDelegation($("#rows"));
wireTipDelegation($("#podium"));

/* ── observatory catalog + switcher ─────────── */
async function loadCatalog() {
  const r = await fetch("/api/observatory");
  const data = await r.json();
  state.scopes = data.telescopes;
  renderStrip();
  renderScopeGrid();
  const enabled = state.scopes.filter((s) => s.enabled);
  if (!state.slug || !enabled.some((s) => s.slug === state.slug)) {
    state.slug = enabled.length ? enabled[0].slug : null;
  }
  return enabled;
}

function renderStrip() {
  $("#scope-strip").innerHTML = state.scopes
    .map(
      (s) => `<button class="scope-chip ${s.enabled ? "" : "off"} ${
        s.slug === state.slug ? "active" : ""
      }" data-slug="${s.slug}" ${s.enabled ? "" : "disabled"}
        title="${s.enabled ? s.tagline : "DISABLED — enable under TELESCOPES"}">
        <span class="g">${s.glyph}</span>${s.name}
        <span class="dom">${s.domain}</span>
      </button>`
    )
    .join("");
  $("#scope-strip").querySelectorAll(".scope-chip").forEach((el) => {
    el.addEventListener("click", () => {
      if (el.disabled) return;
      state.slug = el.dataset.slug;
      state.weights = {};
      state.sort = { key: "rank", dir: 1 };
      renderStrip();
      switchView("board");
      load(false);
    });
  });
}

function renderScopeGrid() {
  $("#scope-grid").innerHTML = state.scopes
    .map(
      (s) => `<div class="scope-card ${s.enabled ? "on" : ""}">
        <div class="sc-head">
          <span class="sc-glyph">${s.glyph}</span>
          <span class="sc-name">${s.name}</span>
          <label class="switch">
            <input type="checkbox" data-toggle="${s.slug}" ${s.enabled ? "checked" : ""}>
            <span class="slider"></span>
          </label>
        </div>
        <div class="sc-domain">${s.domain} · ${s.entity_label}</div>
        <div class="sc-tagline">${s.tagline}</div>
        <div class="sc-sources">${s.sources_label || ""}</div>
        ${s.caveat ? `<div class="sc-caveat">${s.caveat}</div>` : ""}
        <div class="sc-poll">SWEEPS EVERY ${Math.round(s.poll_seconds / 3600)}H</div>
      </div>`
    )
    .join("");
  $("#scope-grid").querySelectorAll("input[data-toggle]").forEach((el) => {
    el.addEventListener("change", async () => {
      const slug = el.dataset.toggle;
      el.disabled = true;
      try {
        await fetch(`/api/observatory/${slug}/toggle`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: el.checked }),
        });
        await loadCatalog();
        if (state.slug) load(false);
      } finally {
        el.disabled = false;
      }
    });
  });
}

/* ── board ──────────────────────────────────── */
async function load(refresh = false) {
  if (!state.slug) {
    $("#rows").innerHTML = `<tr><td class="loading">NO TELESCOPE ENABLED — TURN ONE ON UNDER TELESCOPES</td></tr>`;
    return;
  }
  $("#refresh").disabled = true;
  const cols = state.meta ? state.meta.columns.length + 1 : 8;
  $("#rows").innerHTML = `<tr><td colspan="${cols}" class="loading">OBSERVING</td></tr>`;
  const wq = Object.entries(state.weights).map(([k, v]) => `${k}:${v}`).join(",");
  const url = `/api/telescope/${state.slug}?weights=${encodeURIComponent(wq)}${
    refresh ? "&refresh=1" : ""
  }`;
  try {
    const r = await fetch(url);
    const data = await r.json();
    if (data.error) {
      $("#rows").innerHTML = `<tr><td colspan="${cols}" class="loading">⚠ ${data.error}</td></tr>`;
      $("#refresh").disabled = false;
      return;
    }
    state.meta = data.telescope;
    state.rows = data.rows;
    state.panel = data.panel;
    if (!Object.keys(state.weights).length) {
      state.weights = data.weights;
      renderWeights();
    }
    applyIdentity(data);
    renderHead();
    render();
    renderPanel();
    loadFeed();
  } catch (e) {
    $("#rows").innerHTML = `<tr><td colspan="${cols}" class="loading">⚠ ${e}</td></tr>`;
  } finally {
    $("#refresh").disabled = false;
  }
}

function applyIdentity(data) {
  const m = data.telescope;
  $("#wordmark").textContent = m.name;
  $("#tagline").textContent = `${m.tagline} · ${m.sources_label || ""}`;
  document.title = `${m.name} — ${m.tagline}`;
  $("#podium-label").textContent = `TOP RANKED · ${m.entity_label}`;
  $("#table-label").textContent = `FULL INDEX · ${m.entity_label}`;
  const cav = $("#caveat");
  if (m.caveat) {
    cav.innerHTML = `<b>WHAT THIS INSTRUMENT CAN'T SEE —</b> ${m.caveat}`;
    cav.hidden = false;
  } else cav.hidden = true;
  const ages = Object.entries(data.ages || {})
    .map(([k, v]) => `${k.toUpperCase()} ${ago(v)}`)
    .join(" · ");
  $("#freshness").textContent = ages || "—";
}

function renderHead() {
  const cols = state.meta.columns;
  $("#thead-row").innerHTML =
    `<th class="left" data-sort="rank">#</th>` +
    cols
      .map(
        (c) =>
          `<th class="${c.fmt === "text" ? "left" : ""}" data-sort="${c.field}">${c.label}</th>`
      )
      .join("");
  $("#thead-row").querySelectorAll("th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (state.sort.key === key) state.sort.dir *= -1;
      else state.sort = { key, dir: key === "rank" || key === "name" ? 1 : -1 };
      render();
    });
  });
}

function renderWeights() {
  const signals = state.meta ? state.meta.signals : [];
  $("#weights").innerHTML = signals
    .map((s) => {
      const v = state.weights[s.key] ?? 0;
      return `<div class="weight">
        <label>${s.label} <b id="wv-${s.key}">${v}</b></label>
        <input type="range" min="0" max="50" value="${v}" data-w="${s.key}">
      </div>`;
    })
    .join("");
  $("#weights").querySelectorAll("input[type=range]").forEach((el) => {
    el.addEventListener("input", () => {
      state.weights[el.dataset.w] = +el.value;
      $("#wv-" + el.dataset.w).textContent = el.value;
    });
    el.addEventListener("change", () => load(false));
  });
}

function filtered() {
  let rows = state.rows;
  if (state.hideNoise) rows = rows.filter((r) => !r.noise);
  if (state.search) {
    const q = state.search.toLowerCase();
    rows = rows.filter((r) =>
      Object.values(r).some(
        (v) => typeof v === "string" && v.toLowerCase().includes(q)
      )
    );
  }
  if (state.scoredOnly) rows = rows.filter((r) => r.score !== null);
  const { key, dir } = state.sort;
  return [...rows].sort((a, b) => {
    let av = a[key], bv = b[key];
    if (typeof av === "string" || typeof bv === "string")
      return String(av ?? "").localeCompare(String(bv ?? "")) * dir;
    av = av ?? -Infinity; bv = bv ?? -Infinity;
    return (av - bv) * dir;
  });
}

function podium(rows) {
  const top = rows.filter((r) => r.score !== null).slice(0, 3);
  const maxScore = Math.max(...top.map((r) => r.score), 1);
  const place = ["01 · GOLD", "02 · SILVER", "03 · BRONZE"];
  // Podium stats = the first three non-name columns this telescope declares.
  const statCols = state.meta.columns
    .filter((c) => c.field !== "name" && c.field !== "score")
    .slice(0, 3);
  $("#podium").innerHTML = top
    .map((r, i) => {
      const stats = statCols
        .map((c) => `${c.label} <b>${cell(r, c)}</b>`)
        .join("&nbsp;&nbsp;·&nbsp;&nbsp;");
      const pct = (r.score / maxScore) * 100;
      const name = r.link
        ? `<a href="${r.link}" target="_blank" rel="noopener">${r.name}</a>`
        : r.name;
      return `<div class="pod rank-${i + 1}">
        <div class="pod-place">${place[i]}</div>
        <div class="pod-score" data-key="${r.key}">${r.score}<span class="pod-score-x">/100</span></div>
        <div class="pod-meter"><i style="width:${pct}%"></i></div>
        <div class="pod-name">${name}</div>
        <div class="pod-stats">${stats || "—"}</div>
      </div>`;
    })
    .join("");
}

function render() {
  const rows = filtered();
  const cols = state.meta.columns;
  const label = state.meta.entity_label;
  $("#status").textContent =
    `${rows.length} / ${state.rows.length} ${label} SHOWN · SCORE = WEIGHTED BLEND OF ` +
    state.meta.signals.map((s) => s.label).join(" · ");
  podium(rows);
  const maxScore = Math.max(...rows.map((r) => r.score || 0), 1);
  $("#rows").innerHTML = rows
    .map((r) => {
      const tds = cols
        .map((c) => {
          if (c.field === "name") {
            const inner = r.link
              ? `<a href="${r.link}" target="_blank" rel="noopener">${r.name}</a>`
              : r.name;
            return `<td class="left name">${inner}${
              r.stealth ? ' <span class="badge">STEALTH</span>' : ""
            }</td>`;
          }
          if (c.field === "score") {
            const pct = r.score ? (r.score / maxScore) * 100 : 0;
            return `<td class="scorecell scorebar" data-key="${r.key}">${r.score ?? "—"}<i style="width:${pct}%"></i></td>`;
          }
          return `<td class="${c.fmt === "text" ? "left" : ""}">${cell(r, c)}</td>`;
        })
        .join("");
      return `<tr class="${r.rank === 1 ? "top1" : ""}">
        <td class="rankcell left">${String(r.rank).padStart(2, "0")}</td>${tds}</tr>`;
    })
    .join("");
}

function renderPanel() {
  const p = state.panel;
  if (!p) { $("#panel-section").hidden = true; return; }
  $("#panel-section").hidden = false;
  $("#panel-title").textContent = p.title;
  $("#panel-sub").textContent = p.subtitle || "";
  $("#panel-head").innerHTML = p.columns
    .map((c) => `<th class="${c.fmt === "text" ? "left" : ""}">${c.label}</th>`)
    .join("");
  $("#panel-rows").innerHTML = p.rows
    .map(
      (r) =>
        "<tr>" +
        p.columns
          .map((c) => `<td class="${c.fmt === "text" ? "left" : ""}">${cell(r, c)}</td>`)
          .join("") +
        "</tr>"
    )
    .join("");
}

/* ── merged feed ────────────────────────────── */
const ETYPE_LABEL = {
  new_leader: "NEW #1", new_model: "NEW MODEL", new_entrant: "NEW ENTRY",
  new_candidate: "NEW RAISE", new_prime: "NEW PRIME", big_climber: "CLIMBER",
  price_drop: "PRICE DROP", big_award: "BIG AWARD", funding_drop: "FUNDING DROP",
  regime_change: "REGIME", stealth_raise: "STEALTH", faint_signal: "FAINT SIGNAL",
  breakout: "BREAKOUT", crossing_over: "CROSSOVER", rate_spike: "RATE SPIKE",
  rate_drop: "RATE DROP", congestion: "CONGESTION",
};

async function loadFeed() {
  try {
    const r = await fetch("/api/observatory/whats-new?limit=40");
    const data = await r.json();
    const el = $("#feed");
    if (!data.events || !data.events.length) {
      el.innerHTML = `<div class="feed-empty">Calibrating — the observatory announces changes after its second sweep of each telescope.</div>`;
      return;
    }
    el.innerHTML = data.events
      .map((e) => {
        const head = e.link
          ? e.headline.replace(
              e.name,
              `<a href="${e.link}" target="_blank" rel="noopener">${e.name}</a>`
            )
          : e.headline;
        return `<div class="feed-item t-${e.type}">
          <span class="dot"></span>
          <span class="escope">${e.glyph || "🔭"} ${e.telescope_name || e.telescope || ""}</span>
          <span class="etype">${ETYPE_LABEL[e.type] || e.type}</span>
          <span class="ehead">${head}</span>
          <span class="etime">${ago(Date.now() / 1000 - e.ts)}</span>
        </div>`;
      })
      .join("");
  } catch (e) {
    $("#feed").innerHTML = `<div class="feed-empty">⚠ ${e}</div>`;
  }
}

/* ── roadmap ────────────────────────────────── */
async function loadRoadmap() {
  const el = $("#roadmap");
  if (el.dataset.loaded) return;
  try {
    const r = await fetch("/api/roadmap");
    const data = await r.json();
    el.innerHTML = data.phases
      .map(
        (p) => `<div class="rm-phase">
          <div class="rm-head"><span class="rm-status s-${p.status}">${p.status}</span>
            <span class="rm-title">${p.title}</span></div>
          <div class="rm-note">${p.note || ""}</div>
          <ul class="rm-items">${p.items
            .map(
              (i) =>
                `<li class="i-${i.done ? "done" : "todo"}"><span class="mark">${
                  i.done ? "▰" : "▱"
                }</span> <b>${i.name}</b>${i.detail ? ` — ${i.detail}` : ""}</li>`
            )
            .join("")}</ul>
        </div>`
      )
      .join("");
    el.dataset.loaded = "1";
  } catch (e) {
    el.innerHTML = `<div class="feed-empty">⚠ ${e}</div>`;
  }
}

/* ── views ──────────────────────────────────── */
function switchView(view) {
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.view === view)
  );
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== "view-" + view));
  if (view === "feed") loadFeed();
  if (view === "roadmap") loadRoadmap();
}
$("#tabs").addEventListener("click", (e) => {
  const tab = e.target.closest(".tab");
  if (tab) switchView(tab.dataset.view);
});

$("#search").addEventListener("input", (e) => { state.search = e.target.value; render(); });
$("#scored-only").addEventListener("change", (e) => { state.scoredOnly = e.target.checked; render(); });
$("#hide-noise").addEventListener("change", (e) => { state.hideNoise = e.target.checked; render(); });
$("#refresh").addEventListener("click", () => load(true));
$("#reset-weights").addEventListener("click", () => { state.weights = {}; load(false); });

/* ── boot ───────────────────────────────────── */
(async function boot() {
  await loadCatalog();
  load(false);
})();
