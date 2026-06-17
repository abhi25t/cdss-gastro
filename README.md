# CDSS — Gastroenterology Clinical Decision Support

A rule-based clinical decision support system for **gastroenterology**. It
standardizes patient history-taking around the **presenting symptom** and produces an
**explainable ranked differential diagnosis** (plus suggested tests and treatments) for
a physician to review.

> ⚠️ **Decision support only — not a substitute for clinical judgement.** Every
> output is intended for physician review. It does not diagnose or treat patients.

## What it does

```
Patient → Dynamic Questionnaire → Structured Answers → Rule Engine →
Differential Diagnosis → Recommended Investigations → Treatment Suggestions → Physician Review
```

The motivating workflow: patients fill a symptom questionnaire on their own phones in
the waiting room before the doctor arrives; when the doctor sits down, they log in to a
**first-come-first-serve queue** of *their* waiting patients (showing chief complaint and
waiting time) and open each patient's **consultation page** — an editable note pre-filled
from the patient's answers on the left, and clickable rules-based suggestions (ranked
differential, tests, medications) on the right.

**Symptom-first.** Reasoning follows the natural clinical flow: a patient presents with one
or more **symptoms** (not a diagnosis), the questionnaire characterises each one fully
(SOCRATES-style), and the engine produces an **explainable ranked differential** with a
confidence per diagnosis and the evidence **for and against** each — rather than a single
all-or-nothing verdict. Patients can select **several chief complaints**; each gets its own
work-up and the differential is merged into one deduplicated ranked list.

**Multi-doctor.** Several gastroenterologists share the system. Each doctor has a
short **slug** (e.g. `nitin`) that gives them their own check-in URL
(`…/nitin`) and QR poster; submissions are tagged with that slug, and each doctor
logs in to see only their own patients.

## End-to-end architecture

```
Per-doctor QR poster (encodes  …/<slug>)
        │
        ▼
Patient web app (browser, runs the questionnaire client-side)
        │  collects name + optional email + answers, tagged with the doctor slug
        ▼
Firebase Firestore  ── patients can only create; nobody can read from a browser
        │  read via service-account key (Admin SDK)
        ▼
Hospital on-prem, intranet-only, always-on server:
   • Python CDSS pipeline → first-come-first-serve dashboard + consultation page (per-doctor login)
   • completed notes saved on-prem (SQLite) as mining-ready data for a future learning loop
   • email listener → instant confirmation to patient (or receptionist if no email)
```

Clinical logic lives in **one tested place** (the Python pipeline). The patient app
only *collects* answers. No cloud server is rented: the patient app is static, served
free from Firebase Hosting; Firestore's free tier is the data pipe; and the dashboard
+ email listener run on the hospital's **own on-prem server**, reachable only on the
intranet (it needs *outbound* internet to reach Firestore and send email).

## Repository structure

```text
cdss/
  pipeline.py              # orchestrates the engines
  knowledge/               # loader, models, validator (versioned YAML -> dataclasses)
  questionnaire/           # flow_engine, session (branching navigation; multi-symptom)
  rules/                   # condition, scoring, diagnosis, red_flag engines
  recommendations/         # investigation, treatment, summary (structured note + draft HPI) engines
  api/                     # FastAPI engine API (/run, /validate) + cached registry
  dashboard/               # doctor dashboard + consultation page (FastAPI, per-doctor login)
                           #   triage.py, summary.py, consultations.py (on-prem SQLite store)
  notify/                  # email confirmation listener (Firestore -> SMTP)
knowledge_graph/
  v1/  v2/  v2.1/          # disease-first KGs (v1 real; v2/v2.1 synthetic test data)
  v3/  v4/                 # symptom-first KGs (symptoms.yaml + weighted/signed rules); DRAFT content
  abdominopelvic_regions.svg / bristol-stool-chart.html  # interactive widget sources
doctors/                   # per-doctor registry + credentials (git-ignored; *.example tracked)
webapp/
  build_kg_json.py         # exports a KG's questionnaire to the browser
  build_doctors_js.py      # exports doctor slugs + names to the browser
  make_poster.py           # per-doctor waiting-room QR posters
  patient/                 # static patient questionnaire app (+ Firestore submit)
  SETUP_FIREBASE.md        # one-time Firebase console setup guide
examples/                  # demo CLI, patient cases, validation CLI
tests/                     # unittest suite (engine, API, dashboard)
```

## Setup

Requires Python 3.12.

```bash
git clone https://github.com/abhi25t/cdss-gastro.git
cd cdss-gastro
python3 -m venv .venv && source .venv/bin/activate   # or use your own env
pip install -r requirements.txt
```

Run the test suite:

```bash
python3 -m unittest discover -s tests -v
```

## Components

### 1. The engine (library)

```python
from cdss import CDSSPipeline

pipeline = CDSSPipeline.from_version("v1")
result = pipeline.run({
    "q_location": "ruq", "q_fatty_food": "yes",
    "q_jaundice": "no", "q_fever": "yes",
})
print(result.as_dict()["diagnoses"])
# [{'diagnosis': 'Acute Cholecystitis', 'score': 85,
#   'supporting_evidence': ['Ruq Pain', 'Fatty Food Trigger', 'Fever Present']}, ...]
```

Every diagnosis carries `supporting_evidence` — no black-box output.

### 2. REST API (engine over HTTP)

```bash
python3 -m uvicorn cdss.api:app --reload    # docs at /docs
```

| Method | Path        | Body                          | Purpose                                  |
|--------|-------------|-------------------------------|------------------------------------------|
| GET    | `/health`   | —                             | Liveness check                           |
| GET    | `/versions` | —                             | List available knowledge graph versions  |
| POST   | `/run`      | `{ kg_version, answers }`     | Run the pipeline for a full answer set    |
| POST   | `/validate` | `{ kg_version, profile? }`    | Validate a version (`prototype`/`clinical`) |

```bash
curl -X POST localhost:8000/run -H 'Content-Type: application/json' \
  -d '{"kg_version":"v1","answers":{"q_location":"ruq","q_fatty_food":"yes","q_fever":"yes"}}'
```

Unknown `kg_version` → `404`; unknown `profile` → `400`; malformed body → `422`.
Pipelines are loaded and validated once per version and cached.

> **Scope:** `/run` is the *batch* path (a complete answer set in one call). The
> stateful interactive questionnaire is served by the patient web app below.

### 3. Patient questionnaire web app

In production the app is served from **Firebase Hosting** (a public HTTPS URL — see
*Going live* below), which is how patients reach it from their own phones. For local
development, export a knowledge graph's questionnaire for the browser and serve it:

```bash
python3 webapp/build_kg_json.py --kg-version v4
python3 webapp/build_doctors_js.py            # exports slug+name to doctors.js
cd webapp/patient && python3 -m http.server 6200
# open http://<computer-ip>:6200/?doctor=nitin on a phone on the same network
# (in production the doctor slug comes from the URL path, e.g. /nitin)
```

The app runs the branching questionnaire entirely client-side (its JS flow engine
mirrors `cdss/questionnaire/flow_engine.py`). It reads the doctor **slug** from the
URL, collects the **patient name** (required), **age + sex** (required) and **email**
(optional), supports UHID entry with optional camera barcode scan, and submits to
Firestore tagged with the doctor slug. Patients pick **one or more chief complaints**;
each complaint's questions run in turn, then a shared general-history block. It renders
all question types — single/multi-select, yes/no, number, free text, a **clickable
abdomen diagram** for pain location (`region_select`, multi-region), and the **Bristol
stool chart** for constipation (`bristol_select`, up to 2). The diagram
(`abdominopelvic_regions.svg`) is fetched and inlined, with a plain-pill fallback.

To enable cloud submission, follow **[webapp/SETUP_FIREBASE.md](webapp/SETUP_FIREBASE.md)**
and copy `firebase-config.example.js` → `firebase-config.js`. Without it, the app
still runs and logs submissions locally.

**Going live (public URL + poster).** Patients on cellular can't reach a laptop's
`http.server`, so publish the static app to **Firebase Hosting** (free, same project)
and print a waiting-room QR poster (SETUP_FIREBASE.md Part E):

```bash
firebase deploy --only hosting                          # → https://<project>.web.app
python3 webapp/make_poster.py --url https://<project>.web.app   # writes webapp/poster.png
```

The web config in `firebase-config.js` is public by design (every Firebase web app
ships it); data is protected by the create-only Firestore rules, not by hiding it.

### 4. Doctor dashboard + consultation page (per-doctor login)

```bash
# Local sample data (no Firebase needed — uses examples/patient_cases):
CDSS_SOURCE=sample python3 -m uvicorn cdss.dashboard.app:app --port 6300
# open http://localhost:6300 and log in as a doctor
```

With a `serviceAccountKey.json` present (see SETUP_FIREBASE.md Part D), drop
`CDSS_SOURCE=sample` and it reads live submissions from Firestore. Each doctor logs
in (credentials in `doctors/<slug>.yaml`) and sees **only their own** patients. The
board auto-refreshes and shows patients **first-come-first-serve** (arrival order, with
waiting time). Clicking a patient opens a **consultation page**: a left-hand editable
note pre-filled from the questionnaire (chief complaint, draft history of present
illness — one paragraph per complaint, deterministically generated, no LLM — diagnosis,
tests, meds, …) and right-hand clickable suggestion pills (ranked differential + tests +
medications) that insert into the note. Clicking a **diagnosis** floats that diagnosis's
specific tests and medicines to the top of the suggestion pool. An **"additional
symptoms / history"** field lets the doctor record findings they add during the visit.
Saving stores the note on-prem in SQLite (`doctors/consultations.db`, git-ignored) as
**mining-ready** data — symptoms, diagnoses, tests, meds, plus which suggestions were
accepted vs ignored — for a future association-rule learning loop. The risk-scoring
pipeline still runs (it feeds the suggestions and an optional red-flag badge);
risk-ranking of the queue is kept in code but disabled per the doctors' request.

### 5. Email confirmation listener

Runs continuously on the on-prem server and sends each patient an instant
confirmation when they check in — or notifies the doctor's receptionist if the
patient gave no email:

```bash
python3 -m cdss.notify.listener        # watches Firestore, sends via SMTP
```

Per-doctor email + SMTP credentials live in git-ignored `doctors/<slug>.yaml`
(see `doctors/README.md` and the `*.example.yaml` templates).

## Knowledge graphs

Clinical knowledge is versioned YAML under `knowledge_graph/<version>/`. The engine
is version-agnostic — switching versions needs no code change.

| Version | Nature | Use |
|---------|--------|-----|
| `v1`    | Minimal, real clinical content (biliary pain, GERD, GI bleed), **disease-first** | Working proof of concept |
| `v2`    | Expanded structure + synthetic filler | Larger-graph / grouping structure |
| `v2.1`  | Real diagnosis/investigation names, synthetic rules | Scalability / scoring tests |
| `v3`    | **Symptom-first**: `symptoms.yaml` per chief complaint, weighted/signed differential, structured summary + draft HPI | The natural-clinical-flow redesign |
| `v4`    | Symptom-first, **17 chief complaints** authored from a clinician's history-taking spec; shared general-history block; question types number/text/multi-select plus a clickable **abdomen diagram** (`region_select`) and the **Bristol stool chart** (`bristol_select`) | Current clinical content |

> **v2 and v2.1 contain synthetic placeholders — schema and engine test data, not
> medical truth.** The **symptom-first** versions (`v3`, `v4`) are the current model;
> the engine stays version-agnostic (symptom-first behaviour is gated on whether a KG
> has a `symptoms.yaml`). **v3/v4 clinical content — including the differential weights —
> is a draft for physician review, not validated.**

Validation has two profiles — `prototype` (structural) and `clinical` (strict):

```bash
python3 examples/validate_kg.py --kg-version v4 --profile clinical
python3 examples/validate_kg.py --all
```

Run the demo / regression cases:

```bash
python3 examples/run_demo.py --case v1_biliary_pain
python3 examples/run_cases.py
```

## Status & roadmap

**Working:** the symptom-first engine with explainable ranked differentials, REST API,
the patient web app, Firestore submission, the doctor dashboard + consultation page,
multi-doctor support (per-doctor slugs/QR codes, patient name + age/sex + optional email,
confirmation emails, per-doctor login), and **public Firebase Hosting + a waiting-room QR
poster** — the loop is live-tested.

**Symptom-first model (`v3` → `v4`):** knowledge organised around chief complaints with
complete work-ups → a weighted, signed **ranked differential** (confidence + evidence
for/against). `v4` covers **17 chief complaints** with a shared general-history block.
The consultation page adds **multi-symptom intake** (several complaints, one deduplicated
differential), **diagnosis-aware** test/medicine suggestions, and a structured capture of
doctor-added findings. Completed notes are stored on-prem as mining-ready data.

**Next:** physician review of the v3/v4 weights and content; a **lab-report photo → table**
feature (OCR/vision extraction of test values vs normal ranges — the last item from the
clinician's spec, needs a vision model so it's a separate decision); PHI hardening (end-of-day
auto-delete, App Check — gated on real-patient testing); the **association-rule mining
job** (Apriori/FP-Growth) that turns the captured consultations into proposed knowledge-graph
improvements; optionally an AI consultation summary.

> **Clinical content is a draft for physician review.** The system is decision support,
> not a medical device.

## License / disclaimer

For research and educational use. This software is **not a medical device** and
must not be used as the sole basis for any clinical decision. The bundled
gastroenterology reference textbook is **not** included in this repository.
