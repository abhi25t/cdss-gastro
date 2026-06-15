# CDSS — Gastroenterology Clinical Decision Support

A rule-based clinical decision support system for **gastroenterology triage**. It
standardizes patient history-taking and produces **explainable** diagnostic
suggestions so a physician can review and prioritise patients by risk.

> ⚠️ **Decision support only — not a substitute for clinical judgement.** Every
> output is intended for physician review. It does not diagnose or treat patients.

## What it does

```
Patient → Dynamic Questionnaire → Structured Answers → Rule Engine →
Differential Diagnosis → Recommended Investigations → Treatment Suggestions → Physician Review
```

The motivating workflow: patients fill a questionnaire on their own phones in the
waiting room before the doctor arrives; when the doctor sits down, they log in to a
dashboard of *their* waiting patients **ranked by risk** (red flags first, then
diagnosis score) and call the highest-risk patients first.

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
   • Python CDSS pipeline → risk-ranked triage dashboard (per-doctor login)
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
  questionnaire/           # flow_engine, session (branching navigation)
  rules/                   # condition, scoring, diagnosis, red_flag engines
  recommendations/         # investigation, treatment engines
  api/                     # FastAPI engine API (/run, /validate) + cached registry
  dashboard/               # doctor triage dashboard (FastAPI, per-doctor login)
  notify/                  # email confirmation listener (Firestore -> SMTP)
knowledge_graph/
  v1/  v2/  v2.1/          # versioned clinical knowledge (YAML)
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
python3 webapp/build_kg_json.py --kg-version v1
python3 webapp/build_doctors_js.py            # exports slug+name to doctors.js
cd webapp/patient && python3 -m http.server 6200
# open http://<computer-ip>:6200/?doctor=nitin on a phone on the same network
# (in production the doctor slug comes from the URL path, e.g. /nitin)
```

The app runs the branching questionnaire entirely client-side (its JS flow engine
mirrors `cdss/questionnaire/flow_engine.py`). It reads the doctor **slug** from the
URL, collects the **patient name** (required) and **email** (optional), supports UHID
entry with optional camera barcode scan, and submits to Firestore tagged with the
doctor slug.

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

### 4. Doctor triage dashboard (per-doctor login)

```bash
# Local sample data (no Firebase needed — uses examples/patient_cases):
CDSS_SOURCE=sample python3 -m uvicorn cdss.dashboard.app:app --port 6300
# open http://localhost:6300 and log in as a doctor
```

With a `serviceAccountKey.json` present (see SETUP_FIREBASE.md Part D), drop
`CDSS_SOURCE=sample` and it reads live submissions from Firestore. Each doctor logs
in (credentials in `doctors/<slug>.yaml`) and sees **only their own** patients. The
board auto-refreshes and ranks patients by **red-flag urgency first, then diagnosis
score** — so a red-flag patient is called before a higher-scoring but lower-risk one.

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
| `v1`    | Minimal, real clinical content (biliary pain, GERD, GI bleed) | Working proof of concept |
| `v2`    | Expanded structure + synthetic filler | Larger-graph / grouping structure |
| `v2.1`  | Real diagnosis/investigation names, synthetic rules | Scalability / scoring tests |

> **v2 and v2.1 contain synthetic placeholders — schema and engine test data, not
> medical truth.** A future clinician-reviewed `v3` graph drops in without code
> changes.

Validation has two profiles — `prototype` (structural) and `clinical` (strict):

```bash
python3 examples/validate_kg.py --kg-version v2.1 --profile prototype
python3 examples/validate_kg.py --all
```

Run the demo / regression cases:

```bash
python3 examples/run_demo.py --case v1_biliary_pain
python3 examples/run_cases.py
```

## Status & roadmap

**Working:** engine, explainable scoring, REST API, patient web app, Firestore
submission, the risk-ranked doctor dashboard, and **public Firebase Hosting + a
waiting-room QR poster** — the single-doctor loop is live-tested.

**In progress:** multi-doctor support — per-doctor slugs/QR codes, patient name +
optional email, instant confirmation emails, and per-doctor dashboard login, all
running on the hospital's on-prem intranet server.

**Next:** PHI hardening (end-of-day auto-delete, App Check — gated on real-patient
testing), expanded clinical content (a real `v3` ontology), and optionally an AI
consultation summary.

## License / disclaimer

For research and educational use. This software is **not a medical device** and
must not be used as the sole basis for any clinical decision. The bundled
gastroenterology reference textbook is **not** included in this repository.
