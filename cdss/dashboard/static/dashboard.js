/* Triage dashboard: polls /api/triage and renders patients ranked by risk. */
(function () {
  "use strict";

  const board = document.getElementById("board");
  const statusEl = document.getElementById("status");
  const sourceEl = document.getElementById("sourceLabel");
  const showSeenEl = document.getElementById("showSeen");
  const REFRESH_MS = 5000;
  let timer = null;

  async function load() {
    try {
      const res = await fetch(`/api/triage?include_seen=${showSeenEl.checked}`);
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      render(data);
      statusEl.textContent = `${data.count} patient${data.count === 1 ? "" : "s"} · updated ${nowTime()}`;
      sourceEl.textContent = `Source: ${data.source}`;
    } catch (err) {
      statusEl.textContent = "Connection error — retrying…";
      console.error(err);
    }
  }

  function render(data) {
    if (!data.patients.length) {
      board.innerHTML = `<p class="empty">No patients waiting. New submissions appear here automatically.</p>`;
      return;
    }
    board.innerHTML = data.patients.map(card).join("");
    board.querySelectorAll("[data-seen]").forEach((btn) => {
      btn.addEventListener("click", () => markSeen(btn.getAttribute("data-seen")));
    });
  }

  function card(p) {
    const seen = p.status === "seen";
    const flags = (p.red_flags || []).map((f) => {
      const cls = f.urgency === "immediate" ? "flag--immediate"
        : f.urgency === "urgent" ? "flag--urgent" : "flag--other";
      return `<span class="flag ${cls}">⚑ ${esc(f.flag)} · ${esc(f.urgency)}</span>`;
    }).join("");

    const evidence = (p.evidence || []).map((e) => `<span class="chip">${esc(e)}</span>`).join("");

    const alts = (p.diagnoses || []).slice(1);
    const altLine = alts.length
      ? `<div class="alt-dx">Also: ${alts.map((d) => `${esc(d.diagnosis)} (${d.score})`).join(", ")}</div>`
      : "";

    const dxLine = p.top_diagnosis
      ? `<div class="dx"><strong>${esc(p.top_diagnosis)}</strong><span class="score-pill">${p.top_score}</span></div>`
      : `<div class="dx"><em>No diagnosis matched</em></div>`;

    const action = seen
      ? `<span class="seen-tag">✓ Seen</span>`
      : `<button class="seen-btn" data-seen="${esc(p.id)}">Mark seen</button>`;

    return `
      <article class="patient ${seen ? "seen" : ""}" data-tier="${esc(p.risk_tier)}">
        <div class="rank"><small>Rank</small>${p.position}</div>
        <div class="pinfo">
          <h2>${esc(p.uhid || "Unknown UHID")} <span class="uhid">· ${esc(p.kg_version)}</span></h2>
          ${flags ? `<div class="flags">${flags}</div>` : ""}
          ${dxLine}
          ${evidence ? `<div class="evidence">${evidence}</div>` : ""}
          ${altLine}
          ${p.error ? `<div class="alt-dx">⚠ ${esc(p.error)}</div>` : ""}
        </div>
        <div class="pactions">
          <span class="tier-badge tier-${esc(p.risk_tier)}">${esc(p.risk_tier)}</span>
          ${action}
        </div>
      </article>`;
  }

  async function markSeen(id) {
    try {
      await fetch(`/api/seen/${encodeURIComponent(id)}`, { method: "POST" });
      load();
    } catch (err) { console.error(err); }
  }

  async function cleanup() {
    if (!confirm("Delete all patients already marked as seen? This cannot be undone.")) return;
    try {
      const res = await fetch("/api/cleanup", { method: "POST" });
      const data = await res.json();
      alert(`Removed ${data.deleted} seen record(s).`);
      load();
    } catch (err) { console.error(err); }
  }

  function nowTime() {
    return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  document.getElementById("refreshBtn").addEventListener("click", load);
  document.getElementById("cleanupBtn").addEventListener("click", cleanup);
  showSeenEl.addEventListener("change", load);

  function startPolling() {
    if (timer) clearInterval(timer);
    timer = setInterval(load, REFRESH_MS);
  }
  load();
  startPolling();
})();
