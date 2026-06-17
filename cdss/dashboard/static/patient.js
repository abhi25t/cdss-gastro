/* Consultation page: pre-fills the note from the patient's questionnaire and lets
   the doctor click rules-based suggestions into the note. Saves on-prem. */
(function () {
  "use strict";

  const submissionId = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const NOTE_FIELDS = [
    "chief_complaint", "history_present_illness", "past_history", "current_medications",
    "allergies", "family_history", "findings", "provisional_diagnosis", "tests",
    "prescribed_medications", "advice_followup",
  ];
  const LIST_FIELDS = new Set(["provisional_diagnosis", "tests", "prescribed_medications"]);

  // group -> { target note field, offered values, accepted set }
  const groups = {
    diagnoses: { field: "provisional_diagnosis", offered: [], accepted: new Set() },
    tests: { field: "tests", offered: [], accepted: new Set() },
    medications: { field: "prescribed_medications", offered: [], accepted: new Set() },
  };

  async function load() {
    const res = await fetch(`/api/patient/${encodeURIComponent(submissionId)}`);
    if (res.status === 401) { location = "/login"; return; }
    if (res.status === 404) { document.getElementById("pMeta").textContent = "Patient not found."; return; }
    if (!res.ok) { document.getElementById("pMeta").textContent = "Error loading patient."; return; }
    render(await res.json());
  }

  function render(d) {
    document.getElementById("pName").textContent = d.patient_name || "Patient";
    const sex = { male: "Male", female: "Female", other: "Other" }[d.patient_sex] || "";
    const demo = [d.patient_age ? `${d.patient_age}y` : "", sex].filter(Boolean).join(" ");
    document.getElementById("pMeta").textContent =
      [d.uhid || "no UHID", demo, d.chief_complaint || "—", `waiting ${d.waiting_minutes} min`]
        .filter(Boolean).join(" · ");

    // Pre-fill the note from the structured intake.
    setVal("chief_complaint", d.chief_complaint && d.chief_complaint !== "—" ? d.chief_complaint : "");
    setVal("history_present_illness", d.draft_hpi || "");

    renderPills("diagnoses", (d.differential || []).map((x) => ({
      value: x.diagnosis,
      hint: pillHint(x),
    })));
    renderPills("tests", (d.suggested_tests || []).map((v) => ({ value: v })));
    renderPills("medications", (d.suggested_medications || []).map((v) => ({ value: v })));

    const flags = d.red_flags || [];
    if (flags.length) {
      document.getElementById("redFlagBox").hidden = false;
      document.getElementById("g_redflags").innerHTML = flags
        .map((f) => `<span class="pill pill--flag" title="${esc(f.urgency)}">⚑ ${esc(f.flag)}</span>`).join("");
    }
  }

  function pillHint(x) {
    const parts = [];
    if (x.confidence != null) parts.push(`${x.confidence}%`);
    else if (x.score != null) parts.push(`score ${x.score}`);
    if ((x.supporting_evidence || []).length) parts.push("for: " + x.supporting_evidence.join(", "));
    if ((x.evidence_against || []).length) parts.push("against: " + x.evidence_against.join(", "));
    return parts.join(" · ");
  }

  function renderPills(group, items) {
    const g = groups[group];
    g.offered = items.map((it) => it.value);
    const box = document.getElementById("g_" + group);
    box.innerHTML = items.map((it) =>
      `<button type="button" class="pill" data-group="${group}" data-value="${esc(it.value)}"${
        it.hint ? ` title="${esc(it.hint)}"` : ""}>${esc(it.value)}</button>`).join("")
      || `<span class="pill-empty">none</span>`;
    box.querySelectorAll(".pill").forEach((btn) => btn.addEventListener("click", () => togglePill(group, btn)));
  }

  function togglePill(group, btn) {
    const g = groups[group];
    const value = btn.getAttribute("data-value");
    const field = g.field;
    const lines = getLines(field);
    if (g.accepted.has(value)) {
      g.accepted.delete(value);
      btn.classList.remove("on");
      setVal(field, lines.filter((l) => l !== value).join("\n"));
    } else {
      g.accepted.add(value);
      btn.classList.add("on");
      if (!lines.includes(value)) lines.push(value);
      setVal(field, lines.join("\n"));
    }
  }

  async function save() {
    const note = {};
    NOTE_FIELDS.forEach((f) => { note[f] = LIST_FIELDS.has(f) ? getLines(f) : getVal(f); });
    const suggestions = {};
    Object.entries(groups).forEach(([group, g]) => {
      suggestions[group] = { offered: g.offered, accepted: [...g.accepted] };
    });
    const status = document.getElementById("saveStatus");
    status.textContent = "Saving…";
    try {
      const res = await fetch(`/api/patient/${encodeURIComponent(submissionId)}/consultation`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note, suggestions }),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      status.textContent = "Saved ✓ — patient marked seen.";
    } catch (err) {
      status.textContent = "Save failed — try again.";
      console.error(err);
    }
  }

  function getVal(f) { return document.getElementById("f_" + f).value.trim(); }
  function setVal(f, v) { document.getElementById("f_" + f).value = v; }
  function getLines(f) { return getVal(f).split("\n").map((l) => l.trim()).filter(Boolean); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  document.getElementById("saveBtn").addEventListener("click", save);
  document.getElementById("printBtn").addEventListener("click", () => window.print());
  load();
})();
