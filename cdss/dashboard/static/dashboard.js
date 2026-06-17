/* Triage dashboard: polls /api/triage and renders patients ranked by risk. */
(function () {
  "use strict";

  const board = document.getElementById("board");
  const statusEl = document.getElementById("status");
  const sourceEl = document.getElementById("sourceLabel");
  const doctorEl = document.getElementById("doctorName");
  const showSeenEl = document.getElementById("showSeen");
  const REFRESH_MS = 5000;
  let timer = null;

  async function load() {
    try {
      const res = await fetch(`/api/triage?include_seen=${showSeenEl.checked}`);
      if (res.status === 401) { window.location = "/login"; return; }
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      render(data);
      if (data.doctor_name) doctorEl.textContent = `Dr. ${data.doctor_name}`;
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
    board.querySelectorAll("[data-href]").forEach((el) => {
      el.addEventListener("click", () => { window.location = el.getAttribute("data-href"); });
    });
    board.querySelectorAll("[data-seen]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();  // don't open the patient page when marking seen
        markSeen(btn.getAttribute("data-seen"));
      });
    });
    tickWaiting();
  }

  function card(p) {
    const seen = p.status === "seen";
    // Red flags no longer reorder the queue; show a non-reordering safety badge only.
    const hasFlag = (p.red_flags || []).some((f) => f.urgency === "immediate" || f.urgency === "urgent");
    const flagBadge = hasFlag ? `<span class="flag flag--immediate">⚑ red flag</span>` : "";

    const symptom = p.main_symptom && p.main_symptom !== "—"
      ? ` · <span class="symptom">${esc(p.main_symptom)}</span>` : "";

    const action = seen
      ? `<span class="seen-tag">✓ Seen</span>`
      : `<button class="seen-btn" data-seen="${esc(p.id)}">Mark seen</button>`;

    return `
      <article class="patient ${seen ? "seen" : ""}" data-flag="${hasFlag ? "1" : "0"}"
               data-href="/patient/${encodeURIComponent(p.id)}">
        <div class="rank"><small>Queue</small>${p.position}</div>
        <div class="pinfo">
          <h2>${esc(p.patient_name || "Unknown")} <span class="uhid">${demographics(p)}· ${esc(p.uhid || "no UHID")}</span></h2>
          <div class="complaint"><strong>${esc(p.chief_complaint || "—")}</strong>${symptom}</div>
          <div class="meta">
            <span class="clock">🕒 ${fmtTime(p.created_at)}</span>
            <span class="waiting" data-created="${esc(p.created_at || "")}">waiting ${p.waiting_minutes} min</span>
            ${flagBadge}
          </div>
          ${p.error ? `<div class="alt-dx">⚠ ${esc(p.error)}</div>` : ""}
        </div>
        <div class="pactions">${action}</div>
      </article>`;
  }

  // "45y M · " when age/sex present, else "".
  function demographics(p) {
    const sex = { male: "M", female: "F", other: "O" }[p.patient_sex] || "";
    const parts = [];
    if (p.patient_age) parts.push(`${esc(p.patient_age)}y`);
    if (sex) parts.push(sex);
    return parts.length ? `· ${parts.join(" ")} ` : "";
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d.getTime()) ? "—" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function waitMins(iso) {
    if (!iso) return 0;
    const d = new Date(iso);
    return isNaN(d.getTime()) ? 0 : Math.max(0, Math.floor((Date.now() - d.getTime()) / 60000));
  }

  // Tick the waiting time between polls so it doesn't look frozen.
  function tickWaiting() {
    document.querySelectorAll(".waiting").forEach((el) => {
      el.textContent = `waiting ${waitMins(el.getAttribute("data-created"))} min`;
    });
  }
  setInterval(tickWaiting, 30000);

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
