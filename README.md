# CDSS Framework

Python framework for loading versioned gastroenterology CDSS knowledge graphs,
validating references, running questionnaire flow logic, deriving conditions,
scoring diagnoses with explainability, detecting red flags, and returning
investigation/treatment recommendations.

## Structure

```text
cdss/
  pipeline.py
  knowledge/
    loader.py
    models.py
    validator.py
  questionnaire/
    flow_engine.py
    session.py
  rules/
    condition_engine.py
    scoring_engine.py
    diagnosis_engine.py
    red_flag_engine.py
  recommendations/
    investigation_engine.py
    treatment_engine.py
  api/
    app.py
    registry.py
    __init__.py
examples/
  patient_cases/
  run_cases.py
  run_demo.py
  validate_kg.py
tests/
  test_framework.py
```

## Usage

```python
from cdss import CDSSPipeline

KG_VERSION = "v1"
pipeline = CDSSPipeline.from_version(KG_VERSION)
result = pipeline.run({
    "q_location": "ruq",
    "q_fatty_food": "yes",
    "q_fever": "yes",
})
print(result.as_dict()["diagnoses"])
```

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

Run the end-to-end demo:

```bash
python3 examples/run_demo.py --kg-version v1
```

Run a reusable patient case:

```bash
python3 examples/run_demo.py --case v1_biliary_pain
python3 examples/run_demo.py --case v1_gi_bleeding
python3 examples/run_demo.py --case v2_1_gerd_generated
```

Run all patient case regression checks:

```bash
python3 examples/run_cases.py
```

Validate one knowledge graph version:

```bash
python3 examples/validate_kg.py --kg-version v1
```

Use the prototype profile for structural/scalability datasets:

```bash
python3 examples/validate_kg.py --kg-version v2.1 --profile prototype
```

Use the clinical profile when the knowledge graph should be complete enough for
clinical review:

```bash
python3 examples/validate_kg.py --kg-version v2.1 --profile clinical
```

Validate all versions:

```bash
python3 examples/validate_kg.py --all
```

## REST API

A FastAPI app exposes the pipeline over HTTP. Pipelines are loaded and validated
once per version at first use and cached (they are stateless, so the cached
instance is shared across requests).

Run the server:

```bash
python3 -m uvicorn cdss.api:app --reload
```

Endpoints:

| Method | Path        | Body                                  | Purpose                              |
|--------|-------------|---------------------------------------|--------------------------------------|
| GET    | `/health`   | —                                     | Liveness check                       |
| GET    | `/versions` | —                                     | List available knowledge graph versions |
| POST   | `/run`      | `{ "kg_version", "answers" }`         | Run the pipeline for a full answer set |
| POST   | `/validate` | `{ "kg_version", "profile"? }`        | Validate a version (`prototype`/`clinical`) |

Example:

```bash
curl -X POST localhost:8000/run \
  -H 'Content-Type: application/json' \
  -d '{"kg_version":"v1","answers":{"q_location":"ruq","q_fatty_food":"yes","q_fever":"yes"}}'
```

An unknown `kg_version` returns `404`; an unknown validation `profile` returns
`400`; a malformed body returns `422`. Interactive OpenAPI docs are served at
`/docs`.

**MVP scope:** `/run` is the *batch* path — the client submits a complete set of
answers in one call. The stateful **dynamic questionnaire** (interactive,
question-by-question navigation backed by the flow engine and
`QuestionnaireSession`) is intentionally deferred to a future `/questionnaire`
endpoint set.
