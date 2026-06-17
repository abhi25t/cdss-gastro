/* Patient questionnaire — runs entirely in the browser.
 * Collects answers by walking the knowledge graph flow (mirrors the Python
 * FlowEngine), then emits a JSON payload. Submission to the cloud (Firestore)
 * is stubbed in submitToBackend() — wired up in the next step. */

(function () {
  "use strict";

  const KG = window.KG_DATA;
  const DOCTORS = window.DOCTORS || {};
  const appEl = document.getElementById("app");

  // ---- Doctor (multi-doctor routing) -------------------------------------
  // Each doctor has a slug. In production the slug is the URL path (/nitin),
  // served to index.html by a Firebase Hosting rewrite. For local dev (plain
  // http.server, no rewrite) we also accept ?doctor=nitin.
  function resolveDoctorSlug() {
    const fromPath = window.location.pathname.split("/").filter(Boolean)[0];
    const fromQuery = new URLSearchParams(window.location.search).get("doctor");
    return String(fromPath || fromQuery || "").trim().toLowerCase();
  }

  // ---- State -------------------------------------------------------------
  const state = {
    doctorSlug: "",
    doctorName: "",
    uhid: "",
    patientName: "",
    patientAge: "",
    patientSex: "",
    patientEmail: "",
    answers: {},          // { question_id: value }  (yes_no -> "yes"/"no")
    current: null,        // current question id
    history: [],          // visited question ids, for the Back button
    symptomFlows: [],     // ordered chief-complaint flows still to walk (multi-symptom)
  };

  // ---- Flow engine (mirrors cdss/questionnaire/flow_engine.py) ------------
  function answerKey(answer) {
    if (typeof answer === "boolean") return answer ? "yes" : "no";
    return String(answer).trim().toLowerCase();
  }

  // v4 asks symptom-specific questions first, then ONE shared general block. When a
  // symptom flow ends, chain into this flow (asked once). Absent for v1/v3 → no-op.
  const GENERAL_FLOW = "general";
  const generalQuestionIds = flowQuestionIds(KG.flows[GENERAL_FLOW]);

  function flowQuestionIds(flow) {
    const ids = new Set();
    if (!flow) return ids;
    if (flow.start) ids.add(flow.start);
    for (const [source, branches] of Object.entries(flow.transitions)) {
      ids.add(source);
      for (const target of Object.values(branches)) if (target) ids.add(target);
    }
    return ids;
  }

  // Order selected chief complaints by their position in the q_main_complaint options
  // (deterministic), keeping only those that route to a flow.
  function orderComplaints(chosen) {
    const opts = ((KG.questions[KG.entry_question] || {}).options || []).map((o) => o.value);
    const want = new Set(chosen.map((c) => String(c).trim().toLowerCase()));
    const ordered = opts.filter((v) => want.has(v) && KG.flows[v]);
    chosen.forEach((c) => {
      const v = String(c).trim().toLowerCase();
      if (KG.flows[v] && !ordered.includes(v)) ordered.push(v);
    });
    return ordered;
  }

  function nextQuestion(questionId, answer) {
    const key = answerKey(answer);
    for (const flow of Object.values(KG.flows)) {
      const branches = flow.transitions[questionId];
      if (!branches) continue;
      const next = branches[key] ?? branches["default"] ?? null;
      if (next) return next;
      break;
    }
    return afterSymptomFlow(questionId);
  }

  // End of a symptom flow → the next selected complaint, else the shared general block.
  function afterSymptomFlow(questionId) {
    const owning = owningSymptomFlow(questionId);
    if (owning >= 0) {
      for (let i = owning + 1; i < state.symptomFlows.length; i++) {
        const flow = KG.flows[state.symptomFlows[i]];
        if (flow && flow.start) return flow.start;
      }
    }
    if (KG.flows[GENERAL_FLOW] && !generalQuestionIds.has(questionId)) {
      return KG.flows[GENERAL_FLOW].start;
    }
    return null;
  }

  function owningSymptomFlow(questionId) {
    for (let i = 0; i < state.symptomFlows.length; i++) {
      const flow = KG.flows[state.symptomFlows[i]];
      if (flow && flowQuestionIds(flow).has(questionId)) return i;
    }
    return -1;
  }

  function totalAnswered() {
    return Object.keys(state.answers).length;
  }

  // ---- Rendering ---------------------------------------------------------
  function render(html) {
    appEl.innerHTML = html;
  }

  function progressBar() {
    // Rough progress: answered so far vs a typical path length (symptom block + the
    // shared general block, which dominates the v4 path).
    const approxPathLength = Math.max(6, generalQuestionIds.size + 6);
    const pct = Math.min(95, Math.round((totalAnswered() / approxPathLength) * 100));
    return `<div class="progress"><div class="progress__bar" style="width:${pct}%"></div></div>`;
  }

  function footer() {
    return `<p class="appfoot">Gastroenterology check-in · v${KG.version} · decision support, reviewed by your doctor</p>`;
  }

  function renderStart() {
    render(`
      <div class="spacer"></div>
      <div class="card">
        <p class="eyebrow">Welcome</p>
        <h1>Before you see Dr. ${escapeHtml(state.doctorName)}</h1>
        <p class="subtle">Please answer a few questions about your symptoms while you wait. Your doctor will review your answers.</p>
        <div class="field">
          <label for="patientName">Your name</label>
          <input class="input" id="patientName" inputmode="text" autocomplete="name"
                 placeholder="Full name" value="${escapeAttr(state.patientName)}" />
        </div>
        <div class="field">
          <label for="patientAge">Age</label>
          <input class="input" id="patientAge" inputmode="numeric" type="number" min="0" max="120"
                 placeholder="Years" value="${escapeAttr(state.patientAge)}" />
        </div>
        <div class="field">
          <label for="patientSex">Sex</label>
          <select class="input" id="patientSex">
            <option value=""${state.patientSex === "" ? " selected" : ""}>Select…</option>
            <option value="male"${state.patientSex === "male" ? " selected" : ""}>Male</option>
            <option value="female"${state.patientSex === "female" ? " selected" : ""}>Female</option>
            <option value="other"${state.patientSex === "other" ? " selected" : ""}>Other</option>
          </select>
        </div>
        <div class="field">
          <label for="patientEmail">Email <span class="subtle">(optional)</span></label>
          <input class="input" id="patientEmail" inputmode="email" autocomplete="email"
                 placeholder="you@example.com — for a confirmation" value="${escapeAttr(state.patientEmail)}" />
        </div>
        <div class="field">
          <label for="uhid">Hospital ID (UHID)</label>
          <input class="input" id="uhid" inputmode="text" autocomplete="off"
                 placeholder="e.g. UH00123456" value="${escapeAttr(state.uhid)}" />
          <div class="scan-row">
            <button class="btn" id="scanBtn" type="button">📷 Scan barcode</button>
          </div>
          <div id="scanner"><video id="scanVideo" playsinline muted></video></div>
          <p class="subtle" id="scanMsg"></p>
        </div>
      </div>
      <div class="footer-actions">
        <button class="btn btn--primary" id="startBtn">Start</button>
      </div>
      <div class="spacer"></div>
      ${footer()}
    `);

    const nameInput = document.getElementById("patientName");
    const ageInput = document.getElementById("patientAge");
    const sexInput = document.getElementById("patientSex");
    const emailInput = document.getElementById("patientEmail");
    const uhidInput = document.getElementById("uhid");
    nameInput.addEventListener("input", (e) => { state.patientName = e.target.value.trim(); });
    ageInput.addEventListener("input", (e) => { state.patientAge = e.target.value.trim(); });
    sexInput.addEventListener("change", (e) => { state.patientSex = e.target.value; });
    emailInput.addEventListener("input", (e) => { state.patientEmail = e.target.value.trim(); });
    uhidInput.addEventListener("input", (e) => { state.uhid = e.target.value.trim(); });
    document.getElementById("scanBtn").addEventListener("click", startScan);
    document.getElementById("startBtn").addEventListener("click", () => {
      const msg = document.getElementById("scanMsg");
      if (!state.patientName) {
        nameInput.focus();
        msg.textContent = "Please enter your name first.";
        return;
      }
      const age = parseInt(state.patientAge, 10);
      if (!state.patientAge || isNaN(age) || age < 0 || age > 120) {
        ageInput.focus();
        msg.textContent = "Please enter a valid age.";
        return;
      }
      if (!state.patientSex) {
        sexInput.focus();
        msg.textContent = "Please select your sex.";
        return;
      }
      if (!state.uhid) {
        uhidInput.focus();
        msg.textContent = "Please enter or scan your Hospital ID first.";
        return;
      }
      if (state.patientEmail && !isValidEmail(state.patientEmail)) {
        emailInput.focus();
        msg.textContent = "That email doesn't look right — fix it, or leave it blank.";
        return;
      }
      stopScan();
      goTo(KG.entry_question);
    });
  }

  function renderQuestion(qid) {
    const q = KG.questions[qid];
    if (!q) return renderReview();

    // Tap-to-advance types answer on click; entry types (number/text/multi_choice)
    // collect input and need a Continue button.
    let bodyHtml;
    let instant = false;
    if (q.type === "yes_no") {
      instant = true;
      bodyHtml = `
        <div class="yesno-row">
          <button class="btn btn--yesno" data-value="no">No</button>
          <button class="btn btn--yesno" data-value="yes">Yes</button>
        </div>`;
    } else if (q.type === "number") {
      bodyHtml = `
        <div class="field">
          <input class="input" id="inputField" type="number" inputmode="numeric"
                 min="0" step="1" placeholder="Enter a number" />
        </div>`;
    } else if (q.type === "text") {
      bodyHtml = `
        <div class="field">
          <textarea class="input" id="inputField" rows="3" placeholder="Type your answer"></textarea>
        </div>`;
    } else if (q.type === "multi_choice") {
      bodyHtml =
        `<p class="subtle hint">Choose all that apply.</p><div class="options">` +
        q.options.map((o) =>
          `<button type="button" class="btn btn--multi" data-value="${escapeAttr(o.value)}">${escapeHtml(o.label)}</button>`
        ).join("") +
        `</div>`;
    } else if (q.type === "region_select") {
      bodyHtml =
        `<p class="subtle hint">Tap the area(s) where you feel it. Tap again to unselect.</p>` +
        `<div class="region-picker" id="regionPicker">Loading diagram…</div>` +
        `<p class="subtle" id="regionSel">Tap the area(s) above.</p>`;
    } else if (q.type === "bristol_select") {
      bodyHtml =
        `<p class="subtle hint">Pick the 1–2 types that look most like yours.</p>` +
        `<div class="bristol-list" id="bristolList"></div>`;
    } else {
      // single_choice (and any other choice type)
      instant = true;
      bodyHtml = `<div class="options">` +
        q.options.map((o) =>
          `<button class="btn" data-value="${escapeAttr(o.value)}">${escapeHtml(o.label)}</button>`
        ).join("") +
        `</div>`;
    }

    render(`
      ${progressBar()}
      <div class="card">
        <p class="eyebrow">Question ${totalAnswered() + 1}</p>
        <p class="question-text">${escapeHtml(q.text)}</p>
        ${bodyHtml}
        <p class="err" id="qErr"></p>
      </div>
      <div class="footer-actions">
        ${instant ? "" : `<button class="btn btn--primary" id="continueBtn">Continue</button>`}
        ${state.history.length ? `<button class="btn--ghost" id="backBtn">← Back</button>` : ""}
      </div>
      <div class="spacer"></div>
      ${footer()}
    `);

    if (instant) {
      appEl.querySelectorAll("[data-value]").forEach((btn) => {
        btn.addEventListener("click", () => answer(qid, btn.getAttribute("data-value")));
      });
    } else if (q.type === "multi_choice") {
      wireMultiChoice(qid);
    } else if (q.type === "region_select") {
      wireRegion(qid, q);
    } else if (q.type === "bristol_select") {
      wireBristol(qid, q);
    } else {
      wireEntry(qid, q);
    }
    const backBtn = document.getElementById("backBtn");
    if (backBtn) backBtn.addEventListener("click", goBack);
  }

  // Multi-select: toggle pills, with "None" being mutually exclusive with the rest.
  function wireMultiChoice(qid) {
    const pills = Array.from(appEl.querySelectorAll(".btn--multi"));
    const selected = new Set();
    pills.forEach((btn) => {
      btn.addEventListener("click", () => {
        const val = btn.getAttribute("data-value");
        const nowOn = btn.classList.toggle("btn--selected");
        if (!nowOn) { selected.delete(val); return; }
        selected.add(val);
        if (val === "none") {
          pills.forEach((other) => {
            if (other !== btn) { other.classList.remove("btn--selected"); selected.delete(other.getAttribute("data-value")); }
          });
        } else {
          const none = pills.find((p) => p.getAttribute("data-value") === "none");
          if (none) { none.classList.remove("btn--selected"); selected.delete("none"); }
        }
      });
    });
    document.getElementById("continueBtn").addEventListener("click", () => {
      if (selected.size === 0) {
        document.getElementById("qErr").textContent = "Please choose at least one option.";
        return;
      }
      answer(qid, Array.from(selected));
    });
  }

  // Region selector: load the abdomen diagram and let the patient tap one or more
  // regions ("All over" is exclusive). Each region maps to a clinical site value.
  function wireRegion(qid, q) {
    const host = document.getElementById("regionPicker");
    const selLine = document.getElementById("regionSel");
    const valueByRegion = {};
    const labelByValue = {};
    q.options.forEach((o) => {
      if (o.region) valueByRegion[o.region] = o.value;
      labelByValue[o.value] = o.label;
    });
    const selected = new Set();
    const ENTIRE = "diffuse";

    function refresh() {
      selLine.textContent = selected.size
        ? "Selected: " + Array.from(selected).map((v) => labelByValue[v] || v).join(", ")
        : "Tap the area(s) above.";
    }

    function pick(value, on, syncEl) {
      if (on) {
        selected.add(value);
        if (value === ENTIRE) {
          // "All over" clears the individual regions.
          Array.from(selected).forEach((v) => { if (v !== ENTIRE) selected.delete(v); });
        } else {
          selected.delete(ENTIRE);
        }
      } else {
        selected.delete(value);
      }
      if (syncEl) syncEl();
      refresh();
    }

    fetch("abdominopelvic_regions.svg")
      .then((r) => { if (!r.ok) throw new Error("svg"); return r.text(); })
      .then((svg) => {
        host.innerHTML = svg;
        const svgEl = host.querySelector("svg");
        if (svgEl) { svgEl.removeAttribute("width"); svgEl.removeAttribute("height"); svgEl.classList.add("region-svg"); }
        const cells = Array.from(host.querySelectorAll("[data-region]"));
        const sync = () => cells.forEach((c) => {
          const v = valueByRegion[c.getAttribute("data-region")];
          c.classList.toggle("selected", !!v && selected.has(v));
        });
        cells.forEach((cell) => {
          const value = valueByRegion[cell.getAttribute("data-region")];
          if (!value) return;
          const toggle = () => pick(value, !selected.has(value), sync);
          cell.addEventListener("click", toggle);
          cell.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
          });
        });
        sync();
      })
      .catch(() => {
        // Fallback if the diagram can't load: plain region pills.
        host.innerHTML =
          `<div class="options">` +
          q.options.map((o) =>
            `<button type="button" class="btn btn--multi" data-value="${escapeAttr(o.value)}">${escapeHtml(o.label)}</button>`
          ).join("") + `</div>`;
        const pills = Array.from(host.querySelectorAll(".btn--multi"));
        const sync = () => pills.forEach((b) => b.classList.toggle("btn--selected", selected.has(b.getAttribute("data-value"))));
        pills.forEach((b) => {
          const value = b.getAttribute("data-value");
          b.addEventListener("click", () => pick(value, !selected.has(value), sync));
        });
        sync();
      });

    document.getElementById("continueBtn").addEventListener("click", () => {
      if (selected.size === 0) {
        document.getElementById("qErr").textContent = "Please tap at least one area.";
        return;
      }
      answer(qid, Array.from(selected));
    });
  }

  // Bristol stool chart: pick up to 2 types. Art ported from bristol-stool-chart.html.
  const BRISTOL_ART = {
    "1": `<circle cx="20" cy="23" r="7" fill="#6B4423"/><circle cx="40" cy="18" r="6.5" fill="#7A4F28"/><circle cx="58" cy="26" r="7" fill="#6B4423"/><circle cx="76" cy="20" r="6" fill="#7A4F28"/><circle cx="48" cy="33" r="5.5" fill="#8B5A2B"/>`,
    "2": `<path d="M8 23 Q14 12 22 22 Q30 12 40 22 Q50 12 60 22 Q70 12 84 22 Q90 30 80 33 Q60 38 40 35 Q18 34 8 28 Z" fill="#7A4F28"/>`,
    "3": `<rect x="8" y="16" width="80" height="16" rx="8" fill="#7A4F28"/><path d="M24 16 L26 32 M44 16 L42 32 M62 16 L64 32 M76 16 L74 32" stroke="#6B4423" stroke-width="1.5"/>`,
    "4": `<path d="M8 26 Q26 12 48 24 Q70 36 88 22" stroke="#8B5A2B" stroke-width="13" fill="none" stroke-linecap="round"/>`,
    "5": `<ellipse cx="24" cy="24" rx="13" ry="10" fill="#8B5A2B"/><ellipse cx="52" cy="22" rx="11" ry="9" fill="#A0703D"/><ellipse cx="76" cy="26" rx="10" ry="8" fill="#8B5A2B"/>`,
    "6": `<path d="M10 24 Q12 14 22 18 Q26 10 36 16 Q44 9 52 17 Q62 11 70 18 Q82 14 84 24 Q88 32 78 34 Q66 40 54 35 Q42 41 30 35 Q16 36 10 28 Z" fill="#A0703D"/>`,
    "7": `<path d="M14 26 Q10 18 20 20 Q24 12 34 20 Q46 14 56 22 Q70 18 78 26 Q90 28 82 33 Q60 39 40 35 Q20 36 14 30 Z" fill="#B8843E"/><circle cx="26" cy="38" r="2.5" fill="#B8843E"/><circle cx="68" cy="39" r="2" fill="#B8843E"/>`,
  };

  function wireBristol(qid, q) {
    const list = document.getElementById("bristolList");
    const selected = [];
    const MAX = 2;
    q.options.forEach((o) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "bristol-row";
      row.setAttribute("data-value", o.value);
      row.innerHTML =
        `<span class="bristol-num">${escapeHtml(o.value)}</span>` +
        `<svg class="bristol-art" viewBox="0 0 96 46" aria-hidden="true">${BRISTOL_ART[o.value] || ""}</svg>` +
        `<span class="bristol-desc">${escapeHtml(o.label)}</span>`;
      row.addEventListener("click", () => {
        const i = selected.indexOf(o.value);
        if (i >= 0) {
          selected.splice(i, 1);
          row.classList.remove("selected");
        } else {
          if (selected.length >= MAX) {
            document.getElementById("qErr").textContent = "You can choose up to 2 types.";
            return;
          }
          selected.push(o.value);
          row.classList.add("selected");
        }
        document.getElementById("qErr").textContent = "";
      });
      list.appendChild(row);
    });
    document.getElementById("continueBtn").addEventListener("click", () => {
      if (selected.length === 0) {
        document.getElementById("qErr").textContent = "Please choose at least one type.";
        return;
      }
      answer(qid, selected.slice());
    });
  }

  // Number / free-text entry.
  function wireEntry(qid, q) {
    const field = document.getElementById("inputField");
    field.focus();
    field.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && q.type === "number") {
        e.preventDefault();
        document.getElementById("continueBtn").click();
      }
    });
    document.getElementById("continueBtn").addEventListener("click", () => {
      const err = document.getElementById("qErr");
      const raw = field.value.trim();
      if (q.type === "number") {
        const n = Number(raw);
        if (raw === "" || !Number.isFinite(n) || n < 0) {
          err.textContent = "Please enter a valid number.";
          field.focus();
          return;
        }
        answer(qid, n);
      } else {
        if (raw === "") {
          err.textContent = "Please type an answer.";
          field.focus();
          return;
        }
        answer(qid, raw);
      }
    });
  }

  function renderReview() {
    const rows = Object.entries(state.answers).map(([qid, value]) => {
      const q = KG.questions[qid];
      return `<li>
        <span class="review-q">${escapeHtml(q ? q.text : qid)}</span>
        <span class="review-a">${escapeHtml(labelFor(qid, value))}</span>
      </li>`;
    }).join("");

    render(`
      <div class="card">
        <p class="eyebrow">Review</p>
        <h1>Check your answers</h1>
        <p class="subtle">Name: <strong>${escapeHtml(state.patientName)}</strong></p>
        <p class="subtle">Age / Sex: <strong>${escapeHtml(state.patientAge)} · ${escapeHtml(labelForSex(state.patientSex))}</strong></p>
        <p class="subtle">Hospital ID: <strong>${escapeHtml(state.uhid)}</strong></p>
        ${state.patientEmail ? `<p class="subtle">Email: <strong>${escapeHtml(state.patientEmail)}</strong></p>` : ""}
        <ul class="review-list">${rows}</ul>
      </div>
      <div class="footer-actions">
        <button class="btn btn--primary" id="submitBtn">Submit to doctor</button>
        <button class="btn--ghost" id="backBtn">← Back</button>
      </div>
      <div class="spacer"></div>
      ${footer()}
    `);

    document.getElementById("submitBtn").addEventListener("click", submit);
    document.getElementById("backBtn").addEventListener("click", goBack);
  }

  function renderDone() {
    render(`
      <div class="spacer"></div>
      <div class="card center">
        <div class="done-icon">✓</div>
        <h1>All done</h1>
        <p class="subtle">Your answers have been sent. Please return to your seat — the doctor will call you.</p>
        <div class="note">You do not need to keep this page open.</div>
      </div>
      <div class="spacer"></div>
      ${footer()}
    `);
  }

  // ---- Navigation --------------------------------------------------------
  function goTo(qid) {
    state.current = qid;
    if (!qid) return renderReview();
    renderQuestion(qid);
  }

  function answer(qid, value) {
    state.answers[qid] = value;
    state.history.push(qid);
    // The entry question (q_main_complaint) may select several chief complaints; build
    // the ordered queue of symptom flows and jump to the first one's start.
    if (qid === KG.entry_question) {
      const chosen = orderComplaints(Array.isArray(value) ? value : [value]);
      if (chosen.length) {
        state.symptomFlows = chosen;
        return goTo(KG.flows[chosen[0]].start);
      }
    }
    goTo(nextQuestion(qid, value));
  }

  function goBack() {
    const prev = state.history.pop();
    if (prev === undefined) return renderStart();
    delete state.answers[prev];
    // Drop any answers that came after `prev` (the path may change).
    state.current = prev;
    renderQuestion(prev);
  }

  // ---- Submission --------------------------------------------------------
  function buildPayload() {
    return {
      doctor_slug: state.doctorSlug,
      uhid: state.uhid,
      patient_name: state.patientName,
      patient_age: state.patientAge,
      patient_sex: state.patientSex,
      patient_email: state.patientEmail,
      kg_version: KG.version,
      answers: { ...state.answers },
      submitted_at: new Date().toISOString(),
    };
  }

  async function submit() {
    const payload = buildPayload();
    const btn = document.getElementById("submitBtn");
    if (btn) { btn.disabled = true; btn.textContent = "Sending…"; }
    try {
      // firebase-submit.js sets window.CDSS_submitToBackend when Firebase is
      // configured. Without it (local testing), fall back to logging.
      const send = window.CDSS_submitToBackend || fallbackSubmit;
      await send(payload);
      renderDone();
    } catch (err) {
      if (btn) { btn.disabled = false; btn.textContent = "Submit to doctor"; }
      alert("Could not send your answers. Please check your connection and try again.");
      console.error(err);
    }
  }

  // Used when Firebase isn't configured yet (local testing): logs the payload
  // so its shape can be inspected via the console / window.__lastSubmission.
  async function fallbackSubmit(payload) {
    console.log("[no backend configured] Submission payload:", payload);
    window.__lastSubmission = payload;
  }

  // ---- Barcode scanning --------------------------------------------------
  let scanStream = null;
  let scanLoop = null;

  async function startScan() {
    const scanner = document.getElementById("scanner");
    const msg = document.getElementById("scanMsg");
    if (!("BarcodeDetector" in window)) {
      msg.textContent = "Scanning isn't supported on this phone — please type your Hospital ID.";
      return;
    }
    try {
      const detector = new window.BarcodeDetector();
      scanStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
      });
      const video = document.getElementById("scanVideo");
      video.srcObject = scanStream;
      await video.play();
      scanner.classList.add("active");
      msg.textContent = "Point the camera at the barcode on your hospital card.";

      scanLoop = setInterval(async () => {
        try {
          const codes = await detector.detect(video);
          if (codes.length) {
            const value = codes[0].rawValue;
            document.getElementById("uhid").value = value;
            state.uhid = value.trim();
            msg.textContent = "Scanned: " + value;
            stopScan();
          }
        } catch (_) { /* transient detect errors are fine */ }
      }, 400);
    } catch (err) {
      msg.textContent = "Couldn't open the camera — please type your Hospital ID.";
      console.error(err);
    }
  }

  function stopScan() {
    if (scanLoop) { clearInterval(scanLoop); scanLoop = null; }
    if (scanStream) { scanStream.getTracks().forEach((t) => t.stop()); scanStream = null; }
    const scanner = document.getElementById("scanner");
    if (scanner) scanner.classList.remove("active");
  }

  // ---- Helpers -----------------------------------------------------------
  function labelFor(qid, value) {
    const q = KG.questions[qid];
    if (q && q.type === "yes_no") return value === "yes" ? "Yes" : "No";
    if (Array.isArray(value)) {
      return value.map((v) => optionLabel(q, v)).join(", ") || "—";
    }
    return optionLabel(q, value);
  }

  function optionLabel(q, value) {
    if (q && q.options && q.options.length) {
      const opt = q.options.find((o) => o.value === value);
      if (opt) return opt.label;
    }
    return String(value);
  }

  function labelForSex(value) {
    return { male: "Male", female: "Female", other: "Other" }[value] || "—";
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function escapeAttr(s) { return escapeHtml(s); }

  function isValidEmail(s) {
    // Deliberately lenient — just catches obvious typos, not RFC-perfect.
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s);
  }

  function renderNoDoctor() {
    render(`
      <div class="spacer"></div>
      <div class="card center">
        <h1>Almost there</h1>
        <p class="subtle">Please scan the QR code on your doctor's check-in card to begin. If you typed the address by hand, double-check it with the reception desk.</p>
      </div>
      <div class="spacer"></div>
      ${footer()}
    `);
  }

  // ---- Boot --------------------------------------------------------------
  if (!KG || !KG.entry_question) {
    render(`<div class="card"><h1>Setup needed</h1><p class="subtle">Questionnaire data did not load. Run <code>build_kg_json.py</code> and reload.</p></div>`);
    return;
  }

  // A valid doctor slug is required — it tags the submission and routes the
  // confirmation. An unknown/missing slug means the patient reached the wrong URL.
  state.doctorSlug = resolveDoctorSlug();
  const doctor = DOCTORS[state.doctorSlug];
  if (!doctor) {
    renderNoDoctor();
    return;
  }
  state.doctorName = doctor.name;
  renderStart();
})();
