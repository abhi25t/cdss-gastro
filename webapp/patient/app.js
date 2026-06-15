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
    patientEmail: "",
    answers: {},          // { question_id: value }  (yes_no -> "yes"/"no")
    current: null,        // current question id
    history: [],          // visited question ids, for the Back button
  };

  // ---- Flow engine (mirrors cdss/questionnaire/flow_engine.py) ------------
  function answerKey(answer) {
    if (typeof answer === "boolean") return answer ? "yes" : "no";
    return String(answer).trim().toLowerCase();
  }

  function nextQuestion(questionId, answer) {
    const key = answerKey(answer);

    // q_main_complaint routes to a flow's start question by its answer value.
    if (questionId === KG.entry_question && KG.flows[key]) {
      return KG.flows[key].start;
    }
    for (const flow of Object.values(KG.flows)) {
      const branches = flow.transitions[questionId];
      if (!branches) continue;
      return branches[key] ?? branches["default"] ?? null;
    }
    return null;
  }

  function totalAnswered() {
    return Object.keys(state.answers).length;
  }

  // ---- Rendering ---------------------------------------------------------
  function render(html) {
    appEl.innerHTML = html;
  }

  function progressBar() {
    // Rough progress: answered so far vs a typical path length.
    const approxPathLength = 5;
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
    const emailInput = document.getElementById("patientEmail");
    const uhidInput = document.getElementById("uhid");
    nameInput.addEventListener("input", (e) => { state.patientName = e.target.value.trim(); });
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

    let optionsHtml;
    if (q.type === "yes_no") {
      optionsHtml = `
        <div class="yesno-row">
          <button class="btn btn--yesno" data-value="no">No</button>
          <button class="btn btn--yesno" data-value="yes">Yes</button>
        </div>`;
    } else {
      optionsHtml = `<div class="options">` +
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
        ${optionsHtml}
      </div>
      <div class="footer-actions">
        ${state.history.length ? `<button class="btn--ghost" id="backBtn">← Back</button>` : ""}
      </div>
      <div class="spacer"></div>
      ${footer()}
    `);

    appEl.querySelectorAll("[data-value]").forEach((btn) => {
      btn.addEventListener("click", () => answer(qid, btn.getAttribute("data-value")));
    });
    const backBtn = document.getElementById("backBtn");
    if (backBtn) backBtn.addEventListener("click", goBack);
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
    const next = nextQuestion(qid, value);
    goTo(next);
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
    if (q && q.options) {
      const opt = q.options.find((o) => o.value === value);
      if (opt) return opt.label;
    }
    return String(value);
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
