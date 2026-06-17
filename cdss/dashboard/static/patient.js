/* Consultation page: pre-fills the note from the patient's questionnaire and lets
   the doctor click rules-based suggestions into the note. Saves on-prem.
   - Multi-symptom: HPI is a paragraph per chief complaint; each diagnosis pill is
     tagged with the complaint(s) it relates to.
   - Clicking a diagnosis floats that diagnosis's specific tests/medicines to the top.
   - "Additional symptoms/history" chips are logged as mineable items. */
(function () {
  "use strict";

  const submissionId = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const NOTE_FIELDS = [
    "chief_complaint", "history_present_illness", "past_history", "current_medications",
    "allergies", "family_history", "findings", "provisional_diagnosis", "tests",
    "prescribed_medications", "advice_followup",
  ];
  const LIST_FIELDS = new Set(["provisional_diagnosis", "tests", "prescribed_medications"]);

  // group -> { target note field, offered values, accepted set, + (tests/meds) pool & per-diagnosis map }
  const groups = {
    diagnoses: { field: "provisional_diagnosis", offered: [], accepted: new Set() },
    tests: { field: "tests", offered: [], accepted: new Set(), pool: [], byDiagnosis: {} },
    medications: { field: "prescribed_medications", offered: [], accepted: new Set(), pool: [], byDiagnosis: {} },
  };

  // Symptoms/history the doctor adds during the visit (Workstream C).
  const additionalFindings = [];

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

    // Pre-fill the note from the structured intake (draft_hpi is one paragraph per complaint).
    setVal("chief_complaint", d.chief_complaint && d.chief_complaint !== "—" ? d.chief_complaint : "");
    setVal("history_present_illness", d.draft_hpi || "");

    renderPills("diagnoses", (d.differential || []).map((x) => ({
      value: x.diagnosis,
      hint: pillHint(x),
      tag: (x.chief_complaints || []).join(" · "),
    })));

    // Tests / medicines: keep the full ranked pool + the per-diagnosis maps for reordering.
    groups.tests.pool = d.suggested_tests || [];
    groups.tests.byDiagnosis = d.tests_by_diagnosis || {};
    groups.medications.pool = d.suggested_medications || [];
    groups.medications.byDiagnosis = d.medications_by_diagnosis || {};
    renderSuggestionOrder();

    const flags = d.red_flags || [];
    if (flags.length) {
      document.getElementById("redFlagBox").hidden = false;
      document.getElementById("g_redflags").innerHTML = flags
        .map((f) => `<span class="pill pill--flag" title="${esc(f.urgency)}">⚑ ${esc(f.flag)}</span>`).join("");
    }
    renderChips();
  }

  function pillHint(x) {
    const parts = [];
    if (x.confidence != null) parts.push(`${x.confidence}%`);
    else if (x.score != null) parts.push(`score ${x.score}`);
    if ((x.chief_complaints || []).length) parts.push("from: " + x.chief_complaints.join(", "));
    if ((x.supporting_evidence || []).length) parts.push("for: " + x.supporting_evidence.join(", "));
    if ((x.evidence_against || []).length) parts.push("against: " + x.evidence_against.join(", "));
    return parts.join(" · ");
  }

  // Order a tests/medicines pool so the selected diagnoses' specific items float to the
  // top (selection order), de-duplicated, with the rest of the pool below.
  function orderedItems(group) {
    const g = groups[group];
    const selected = [...groups.diagnoses.accepted];
    const floated = new Set();
    const items = [];
    selected.forEach((dx) => (g.byDiagnosis[dx] || []).forEach((v) => {
      if (g.pool.includes(v) && !floated.has(v)) { floated.add(v); items.push({ value: v, floated: true }); }
    }));
    g.pool.forEach((v) => { if (!floated.has(v)) items.push({ value: v }); });
    return items;
  }

  function renderSuggestionOrder() {
    renderPills("tests", orderedItems("tests"));
    renderPills("medications", orderedItems("medications"));
  }

  function renderPills(group, items) {
    const g = groups[group];
    g.offered = items.map((it) => it.value);
    const box = document.getElementById("g_" + group);
    box.innerHTML = items.map((it) => {
      const on = g.accepted.has(it.value) ? " on" : "";
      const fl = it.floated ? " pill--floated" : "";
      const tag = it.tag ? ` <span class="pill-tag">${esc(it.tag)}</span>` : "";
      const title = it.hint ? ` title="${esc(it.hint)}"` : "";
      return `<button type="button" class="pill${on}${fl}" data-group="${group}" data-value="${esc(it.value)}"${title}>${esc(it.value)}${tag}</button>`;
    }).join("") || `<span class="pill-empty">none</span>`;
    box.querySelectorAll(".pill").forEach((btn) => btn.addEventListener("click", () => togglePill(group, btn)));
  }

  function togglePill(group, btn) {
    const g = groups[group];
    const value = btn.getAttribute("data-value");
    const lines = getLines(g.field);
    if (g.accepted.has(value)) {
      g.accepted.delete(value);
      btn.classList.remove("on");
      setVal(g.field, lines.filter((l) => l !== value).join("\n"));
    } else {
      g.accepted.add(value);
      btn.classList.add("on");
      if (!lines.includes(value)) lines.push(value);
      setVal(g.field, lines.join("\n"));
    }
    // Selecting/deselecting a diagnosis re-floats its tests & medicines to the top.
    if (group === "diagnoses") renderSuggestionOrder();
  }

  // ---- Additional-findings chips (Workstream C) ----
  function renderChips() {
    const box = document.getElementById("additional_findings_chips");
    box.innerHTML = additionalFindings.map((v, i) =>
      `<span class="chip">${esc(v)}<button type="button" class="chip-x" data-i="${i}" aria-label="remove">×</button></span>`).join("");
    box.querySelectorAll(".chip-x").forEach((b) => b.addEventListener("click", () => {
      additionalFindings.splice(Number(b.getAttribute("data-i")), 1);
      renderChips();
    }));
  }

  function wireChipInput() {
    const inp = document.getElementById("f_additional_findings_input");
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const v = inp.value.trim();
        if (v && !additionalFindings.includes(v)) { additionalFindings.push(v); renderChips(); }
        inp.value = "";
      }
    });
  }

  async function save() {
    const note = {};
    NOTE_FIELDS.forEach((f) => { note[f] = LIST_FIELDS.has(f) ? getLines(f) : getVal(f); });
    note.additional_findings = additionalFindings.slice();
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
  wireChipInput();
  load();
})();
