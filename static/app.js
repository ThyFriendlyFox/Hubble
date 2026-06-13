const WEIGHT_LABELS = {
  intelligence: "INTELLIGENCE",
  coding: "CODING",
  agentic: "AGENTIC",
  arena: "ARENA ELO",
  usage: "REAL USAGE",
  downloads: "DOWNLOADS",
  likes: "LIKES",
  price: "CHEAP PRICE",
};

let state = {
  models: [],
  weights: {},
  sort: { key: "rank", dir: 1 },
  search: "",
  scoredOnly: false,
  hideNoise: true,
};

const $ = (s) => document.querySelector(s);

function fmt(n) {
  if (n === null || n === undefined) return "—";
  if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return n.toLocaleString();
}
function price(p) {
  if (p === null || p === undefined) return "—";
  return "$" + (p * 1e6).toFixed(2);
}
function ago(s) {
  if (s === null || s === undefined) return "—";
  if (s < 60) return s + "s";
  if (s < 3600) return Math.round(s / 60) + "m";
  return Math.round(s / 3600) + "h";
}

async function load(refresh = false) {
  $("#refresh").disabled = true;
  $("#rows").innerHTML = `<tr><td colspan="12" class="loading">FETCHING LIVE INDEX</td></tr>`;
  const wq = Object.entries(state.weights).map(([k, v]) => `${k}:${v}`).join(",");
  const url = `/api/models?weights=${encodeURIComponent(wq)}${refresh ? "&refresh=1" : ""}`;
  try {
    const r = await fetch(url);
    const data = await r.json();
    if (data.error) {
      $("#rows").innerHTML = `<tr><td colspan="12" class="loading">⚠ ${data.error}</td></tr>`;
      return;
    }
    state.models = data.models;
    if (!Object.keys(state.weights).length) {
      state.weights = data.weights;
      renderWeights();
    }
    const a = data.ages || {};
    $("#freshness").textContent =
      `HF ${ago(a.huggingface)} · OR ${ago(a.openrouter)} · ARENA ${ago(a.lmarena)}`;
    $("#status").textContent =
      `${data.count} MODELS INDEXED · SCORE = WEIGHTED BLEND OF INTELLIGENCE · CODING · AGENTIC · ARENA · USAGE · DOWNLOADS · LIKES · PRICE`;
    render();
    loadFeed();
    renderFit();
  } catch (e) {
    $("#rows").innerHTML = `<tr><td colspan="12" class="loading">⚠ ${e}</td></tr>`;
  } finally {
    $("#refresh").disabled = false;
  }
}

const ETYPE_LABEL = {
  new_leader: "NEW #1",
  new_model: "NEW MODEL",
  big_climber: "CLIMBER",
  price_drop: "PRICE DROP",
};

async function loadFeed() {
  try {
    const r = await fetch("/api/whats-new?limit=12");
    const data = await r.json();
    const el = $("#feed");
    if (!data.events || !data.events.length) {
      const swept = data.snapshots > 0;
      el.innerHTML = `<div class="feed-empty">${
        swept
          ? "No changes detected since the last sweep — the index is steady."
          : "Calibrating — Hubble takes its first reading on the next sweep."
      }</div>`;
      return;
    }
    el.innerHTML = data.events
      .map((e) => {
        const link = e.key && e.key.includes("/")
          ? `https://huggingface.co/${e.key}`
          : null;
        const head = link
          ? e.headline.replace(e.name, `<a href="${link}" target="_blank" rel="noopener">${e.name}</a>`)
          : e.headline;
        return `<div class="feed-item t-${e.type}">
          <span class="dot"></span>
          <span class="etype">${ETYPE_LABEL[e.type] || e.type}</span>
          <span class="ehead">${head}</span>
          <span class="etime">${ago(Math.round(Date.now() / 1000 - e.ts))}</span>
        </div>`;
      })
      .join("");
  } catch (e) {
    $("#feed").innerHTML = `<div class="feed-empty">⚠ ${e}</div>`;
  }
}

function renderWeights() {
  $("#weights").innerHTML = Object.keys(WEIGHT_LABELS)
    .map((k) => {
      const v = state.weights[k] ?? 0;
      return `<div class="weight">
        <label>${WEIGHT_LABELS[k]} <b id="wv-${k}">${v}</b></label>
        <input type="range" min="0" max="50" value="${v}" data-w="${k}">
      </div>`;
    })
    .join("");
  $("#weights").querySelectorAll("input[type=range]").forEach((el) => {
    el.addEventListener("input", () => {
      const k = el.dataset.w;
      state.weights[k] = +el.value;
      $("#wv-" + k).textContent = el.value;
    });
    el.addEventListener("change", () => load(false));
  });
}

function filtered() {
  let rows = state.models;
  if (state.hideNoise) rows = rows.filter((m) => !m.noise);
  if (state.search) {
    const q = state.search.toLowerCase();
    rows = rows.filter(
      (m) =>
        (m.name || "").toLowerCase().includes(q) ||
        (m.hf_id || "").toLowerCase().includes(q)
    );
  }
  if (state.scoredOnly) rows = rows.filter((m) => m.score !== null);
  const { key, dir } = state.sort;
  rows = [...rows].sort((a, b) => {
    let av = a[key], bv = b[key];
    if (typeof av === "string") return av.localeCompare(bv) * dir;
    av = av ?? -Infinity; bv = bv ?? -Infinity;
    return (av - bv) * dir;
  });
  return rows;
}

function podium(rows) {
  const top = rows.filter((m) => m.score !== null).slice(0, 3);
  const maxScore = Math.max(...top.map((m) => m.score), 1);
  const place = ["01 · GOLD", "02 · SILVER", "03 · BRONZE"];
  $("#podium").innerHTML = top
    .map((m, i) => {
      const link = m.hf_id
        ? `https://huggingface.co/${m.hf_id}`
        : `https://openrouter.ai/${m.or_id}`;
      const stats = [
        m.usage_rank ? `USAGE <b>#${m.usage_rank}</b>` : null,
        m.intelligence_index ? `INTL <b>${m.intelligence_index}</b>` : null,
        m.arena_elo ? `ARENA <b>${Math.round(m.arena_elo)}</b>` : null,
      ].filter(Boolean).join("&nbsp;&nbsp;·&nbsp;&nbsp;");
      const pct = (m.score / maxScore) * 100;
      return `<div class="pod rank-${i + 1}">
        <div class="pod-place">${place[i]}</div>
        <div class="pod-score">${m.score}<span class="pod-score-x">/100</span></div>
        <div class="pod-meter"><i style="width:${pct}%"></i></div>
        <div class="pod-name"><a href="${link}" target="_blank" rel="noopener">${m.name}</a></div>
        <div class="pod-stats">${stats || "—"}</div>
      </div>`;
    })
    .join("");
}

function render() {
  const rows = filtered();
  $("#status").textContent =
    `${rows.length} / ${state.models.length} MODELS SHOWN · SCORE = WEIGHTED BLEND OF INTELLIGENCE · CODING · AGENTIC · ARENA · USAGE · DOWNLOADS · LIKES · PRICE`;
  podium(rows);
  const maxScore = Math.max(...rows.map((m) => m.score || 0), 1);
  $("#rows").innerHTML = rows
    .map((m) => {
      const link = m.hf_id
        ? `https://huggingface.co/${m.hf_id}`
        : `https://openrouter.ai/${m.or_id}`;
      const badges =
        (m.sources.includes("huggingface") ? `<span class="badge">HF</span>` : "") +
        (m.sources.includes("openrouter") ? `<span class="badge">OR</span>` : "") +
        (m.intelligence_index ? `<span class="badge">AA</span>` : "");
      const pct = m.score ? (m.score / maxScore) * 100 : 0;
      return `<tr class="${m.rank === 1 ? "top1" : ""}">
        <td class="rankcell">${String(m.rank).padStart(2, "0")}</td>
        <td class="left name"><a href="${link}" target="_blank" rel="noopener">${m.name}</a></td>
        <td class="scorecell scorebar">${m.score ?? "—"}<i style="width:${pct}%"></i></td>
        <td>${m.intelligence_index ?? "<span class=dim>—</span>"}</td>
        <td>${m.coding_index ?? "<span class=dim>—</span>"}</td>
        <td>${m.agentic_index ?? "<span class=dim>—</span>"}</td>
        <td>${m.arena_elo ? Math.round(m.arena_elo) : "<span class=dim>—</span>"}</td>
        <td>${m.usage_rank ? "#" + m.usage_rank : "<span class=dim>—</span>"}</td>
        <td>${fmt(m.downloads)}</td>
        <td>${fmt(m.likes)}</td>
        <td>${price(m.price_completion)}</td>
        <td class="left">${badges}</td>
      </tr>`;
    })
    .join("");
}

/* ── hardware fit ──────────────────────────── */
const GPU_PRESETS = [
  { label: "Custom", vram: null },
  { label: "RTX 5090 · 32GB", vram: 32 },
  { label: "RTX 4090 / 3090 · 24GB", vram: 24 },
  { label: "RTX 5080 / 4080 · 16GB", vram: 16 },
  { label: "RTX 4070 Ti / 3060 · 12GB", vram: 12 },
  { label: "RTX 4060 / 3050 · 8GB", vram: 8 },
  { label: "Apple M-series (unified)", vram: 0, unified: true },
  { label: "A100 · 40GB", vram: 40 },
  { label: "A100 / H100 · 80GB", vram: 80 },
  { label: "CPU only", vram: 0 },
];
const QUANT_BYTES = { q4: 0.55, q8: 1.0, fp16: 2.0 };
const QUANT_LABEL = { q4: "Q4", q8: "Q8", fp16: "FP16" };

function estVram(paramsB, quant) {
  // weights + ~20% for KV cache / activations / runtime
  return paramsB * QUANT_BYTES[quant] * 1.2;
}

function initFitControls() {
  const sel = $("#fit-gpu");
  sel.innerHTML = GPU_PRESETS.map((g, i) => `<option value="${i}">${g.label}</option>`).join("");
  sel.value = "2"; // default 24GB
  sel.addEventListener("change", () => {
    const g = GPU_PRESETS[+sel.value];
    if (g.vram !== null && !g.unified) $("#fit-vram").value = g.vram;
    if (g.unified) $("#fit-vram").value = 0;
    renderFit();
  });
  ["fit-vram", "fit-ram", "fit-quant", "fit-use"].forEach((id) =>
    $("#" + id).addEventListener("input", renderFit)
  );
}

function renderFit() {
  const vram = +$("#fit-vram").value || 0;
  const ram = +$("#fit-ram").value || 0;
  const quant = $("#fit-quant").value;
  const useKey = $("#fit-use").value;
  const preset = GPU_PRESETS[+$("#fit-gpu").value];
  const unified = preset && preset.unified;
  // On unified-memory (Apple), usable "VRAM" ≈ most of system RAM.
  const budget = unified ? ram * 0.75 : vram;

  const candidates = state.models
    .filter((m) => m.local && m.params_b && !m.noise)
    .map((m) => {
      const need = estVram(m.params_b, quant);
      let fit, note;
      if (need <= budget) { fit = "fits"; note = "runs on GPU"; }
      else if (need <= budget * 1.12) { fit = "tight"; note = "tight — trim context"; }
      else if (m.params_b * QUANT_BYTES[quant] <= ram) { fit = "offload"; note = "CPU / partial offload (slower)"; }
      else { fit = "toobig"; note = "exceeds RAM at this quant"; }
      return { ...m, need, fit, note };
    });

  const runnable = candidates.filter((m) => m.fit === "fits" || m.fit === "tight");
  const metric = (m) => (m[useKey] != null ? m[useKey] : -1);
  runnable.sort((a, b) => metric(b) - metric(a) || (b.score || 0) - (a.score || 0));

  // summary + best pick
  const useLabel = $("#fit-use").selectedOptions[0].textContent;
  const best = runnable[0];
  if (best) {
    const link = `https://huggingface.co/${best.hf_id}`;
    $("#fit-summary").innerHTML =
      `BEST PICK FOR ${budget.toFixed(0)}GB ${unified ? "UNIFIED" : "VRAM"} @ ${QUANT_LABEL[quant]} · OPTIMISING ${useLabel}<br>
       <span class="pick"><a href="${link}" target="_blank" rel="noopener">${best.name}</a></span><br>
       <span class="muted2">${best.params_b}B params · ~${best.need.toFixed(1)}GB to load · score <b>${best.score ?? "—"}</b>${
        best.intelligence_index ? ` · intl <b>${best.intelligence_index}</b>` : ""
      }${best.coding_index ? ` · code <b>${best.coding_index}</b>` : ""}</span>`;
  } else {
    $("#fit-summary").innerHTML =
      `<span class="muted2">Nothing fits ${budget.toFixed(0)}GB at ${QUANT_LABEL[quant]}. Try a smaller quant, or a model under ~${(budget / (QUANT_BYTES[quant] * 1.2)).toFixed(0)}B params.</span>`;
  }

  // table: runnable first (ranked), then a few near-misses for context
  const nearMiss = candidates
    .filter((m) => m.fit === "offload" || m.fit === "toobig")
    .sort((a, b) => (b.score || 0) - (a.score || 0))
    .slice(0, 8);
  const rows = [...runnable, ...nearMiss];

  $("#fit-rows").innerHTML = rows
    .map((m) => {
      const link = `https://huggingface.co/${m.hf_id}`;
      const badge = { fits: "FITS", tight: "TIGHT", offload: "OFFLOAD", toobig: "TOO BIG" }[m.fit];
      return `<tr>
        <td class="left"><span class="fit-badge fit-${m.fit}">${badge}</span></td>
        <td class="left name"><a href="${link}" target="_blank" rel="noopener">${m.name}</a></td>
        <td>${m.params_b}B</td>
        <td>${m.need.toFixed(1)}GB</td>
        <td class="scorecell">${m.score ?? "—"}</td>
        <td>${m.intelligence_index ?? "—"}</td>
        <td>${m.coding_index ?? "—"}</td>
        <td>${m.agentic_index ?? "—"}</td>
        <td class="left dim">${m.note}</td>
      </tr>`;
    })
    .join("");
  $("#fit-status").textContent =
    `${runnable.length} MODELS FIT · ${candidates.length} OPEN-WEIGHT MODELS WITH KNOWN SIZE · EST. VRAM = PARAMS × ${QUANT_BYTES[quant]}B × 1.2 OVERHEAD`;
}

/* ── tab switching ─────────────────────────── */
function switchView(view) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== "view-" + view));
  if (view === "feed") loadFeed();
  if (view === "fit") renderFit();
}
$("#tabs").addEventListener("click", (e) => {
  const tab = e.target.closest(".tab");
  if (tab) switchView(tab.dataset.view);
});

// ── events ──
document.querySelectorAll("th[data-sort]").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    if (state.sort.key === key) state.sort.dir *= -1;
    else state.sort = { key, dir: key === "name" || key === "rank" ? 1 : -1 };
    render();
  });
});
$("#search").addEventListener("input", (e) => { state.search = e.target.value; render(); });
$("#scored-only").addEventListener("change", (e) => { state.scoredOnly = e.target.checked; render(); });
$("#hide-noise").addEventListener("change", (e) => { state.hideNoise = e.target.checked; render(); });
$("#refresh").addEventListener("click", () => load(true));
$("#reset-weights").addEventListener("click", () => { state.weights = {}; load(false); });

initFitControls();
load(false);
