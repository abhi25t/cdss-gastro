from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from fastapi.testclient import TestClient

# Force the dashboard to use local sample data (not Firestore) for tests, and pin a
# session secret so login tokens are deterministic and no secret file is written.
os.environ.setdefault("CDSS_SOURCE", "sample")
os.environ.setdefault("CDSS_SESSION_SECRET", "test-secret-do-not-use-in-prod")

from cdss import CDSSPipeline
from cdss.api import app
from cdss.dashboard.app import app as dashboard_app
from cdss.knowledge import KnowledgeGraph, validate
from cdss.questionnaire import FlowEngine
from cdss.recommendations import InvestigationEngine, TreatmentEngine
from cdss.rules import ConditionEngine, RedFlagEngine, ScoringEngine


class FrameworkTest(unittest.TestCase):
    def test_v1_loads_and_validates(self) -> None:
        kg = KnowledgeGraph.load("knowledge_graph/v1")
        report = validate(kg)

        self.assertEqual(kg.version, "v1")
        self.assertIn("q_location", kg.questions)
        self.assertTrue(report.is_valid, report.as_dict())

    def test_v1_flow_branching(self) -> None:
        kg = KnowledgeGraph.load("knowledge_graph/v1")
        flow = FlowEngine(kg)

        self.assertEqual(flow.next_question("q_main_complaint", "abdominal_pain"), {"next_question": "q_location"})
        self.assertEqual(flow.next_question("q_location", "ruq", "abdominal_pain"), {"next_question": "q_fatty_food"})
        self.assertEqual(flow.next_question("q_fatty_food", True, "abdominal_pain"), {"next_question": "q_jaundice"})

    def test_v1_conditions_scoring_and_explainability(self) -> None:
        kg = KnowledgeGraph.load("knowledge_graph/v1")
        conditions = ConditionEngine(kg).evaluate(
            {
                "q_location": "ruq",
                "q_fatty_food": "yes",
                "q_fever": "yes",
                "q_black_stool": "no",
            }
        )
        diagnoses = ScoringEngine(kg).score(conditions)

        self.assertTrue(conditions["ruq_pain"])
        self.assertEqual(diagnoses[0].diagnosis, "Acute Cholecystitis")
        self.assertEqual(diagnoses[0].score, 85)
        self.assertIn("Ruq Pain", diagnoses[0].supporting_evidence)
        self.assertIn("Fever Present", diagnoses[0].supporting_evidence)

    def test_v1_red_flags_and_recommendations(self) -> None:
        kg = KnowledgeGraph.load("knowledge_graph/v1")
        conditions = ConditionEngine(kg).evaluate({"q_black_stool": "yes"})
        flags = RedFlagEngine(kg).detect(conditions)
        diagnoses = ScoringEngine(kg).score(conditions)

        self.assertEqual(flags, [{"flag": "GI Bleeding", "urgency": "immediate"}])
        self.assertIn("Upper GI Endoscopy", InvestigationEngine(kg).recommend(diagnoses)["Peptic Ulcer Disease"])
        self.assertIn("Proton pump inhibitor", TreatmentEngine(kg).recommend(diagnoses)["Peptic Ulcer Disease"])

    def test_all_versions_load_without_application_changes(self) -> None:
        for version in ("v1", "v2", "v2.1", "v3", "v4"):
            with self.subTest(version=version):
                kg = KnowledgeGraph.load(f"knowledge_graph/{version}")
                self.assertGreater(len(kg.questions), 0)
                self.assertIsInstance(validate(kg).as_dict(), dict)

    def test_v1_output_has_no_v3_only_fields(self) -> None:
        # Back-compat guard: the older /run contract must not gain v3 keys.
        kg = KnowledgeGraph.load("knowledge_graph/v1")
        result = CDSSPipeline(kg).run({"q_location": "ruq", "q_fatty_food": "yes", "q_fever": "yes"}).as_dict()
        self.assertNotIn("symptom_summary", result)
        self.assertNotIn("draft_hpi", result)
        for diagnosis in result["diagnoses"]:
            self.assertNotIn("confidence", diagnosis)
            self.assertNotIn("evidence_against", diagnosis)

    def test_validation_cli_v1(self) -> None:
        result = subprocess.run(
            [sys.executable, "examples/validate_kg.py", "--kg-version", "v1"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("v1: PASS (0 errors, 0 warnings)", result.stdout)

    def test_run_demo_uses_patient_case(self) -> None:
        result = subprocess.run(
            [sys.executable, "examples/run_demo.py", "--case", "v1_gi_bleeding"],
            check=False,
            capture_output=True,
            text=True,
        )
        output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output["case"]["id"], "v1_gi_bleeding")
        self.assertEqual(output["red_flags"], [{"flag": "GI Bleeding", "urgency": "immediate"}])
        self.assertEqual(output["diagnoses"][0]["diagnosis"], "Peptic Ulcer Disease")

    def test_run_demo_defaults_to_v21_case(self) -> None:
        result = subprocess.run(
            [sys.executable, "examples/run_demo.py", "--kg-version", "v2.1"],
            check=False,
            capture_output=True,
            text=True,
        )
        output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output["case"]["id"], "v2_1_gerd_generated")
        self.assertEqual(output["diagnoses"][0]["diagnosis"], "GERD")

    def test_run_cases_cli(self) -> None:
        result = subprocess.run(
            [sys.executable, "examples/run_cases.py"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS v1_biliary_pain", result.stdout)
        self.assertIn("PASS v1_gi_bleeding", result.stdout)
        self.assertIn("PASS v2_1_gerd_generated", result.stdout)
        self.assertIn("Summary: 4 passed, 0 failed", result.stdout)

    def test_run_cases_cli_json_named_case(self) -> None:
        result = subprocess.run(
            [sys.executable, "examples/run_cases.py", "--case", "v1_biliary_pain", "--json"],
            check=False,
            capture_output=True,
            text=True,
        )
        output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output[0]["case_id"], "v1_biliary_pain")
        self.assertTrue(output[0]["passed"])

    def test_validation_profiles_for_v21(self) -> None:
        prototype = subprocess.run(
            [sys.executable, "examples/validate_kg.py", "--kg-version", "v2.1", "--profile", "prototype"],
            check=False,
            capture_output=True,
            text=True,
        )
        clinical = subprocess.run(
            [sys.executable, "examples/validate_kg.py", "--kg-version", "v2.1", "--profile", "clinical", "--max-issues", "1"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(prototype.returncode, 0, prototype.stderr)
        self.assertIn("v2.1: PASS (0 errors, 0 warnings)", prototype.stdout)
        self.assertEqual(clinical.returncode, 1)
        self.assertIn("v2.1: FAIL", clinical.stdout)

    def test_pipeline_runs_end_to_end(self) -> None:
        pipeline = CDSSPipeline.from_version("v1")
        result = pipeline.run(
            {
                "q_location": "ruq",
                "q_fatty_food": "yes",
                "q_fever": "yes",
                "q_black_stool": "no",
            }
        )
        output = result.as_dict()

        self.assertTrue(output["validation"]["valid"])
        self.assertIn("ruq_pain", output["true_conditions"])
        self.assertEqual(output["diagnoses"][0]["diagnosis"], "Acute Cholecystitis")
        self.assertIn("supporting_evidence", output["diagnoses"][0])
        self.assertIn("Acute Cholecystitis", output["investigations"])
        self.assertIn("Acute Cholecystitis", output["treatments"])

    def test_pipeline_runs_v21_generated_rules(self) -> None:
        pipeline = CDSSPipeline.from_version("v2.1")
        result = pipeline.run({"feature_272": True, "feature_100": True})
        output = result.as_dict()

        self.assertTrue(output["validation"]["valid"])
        self.assertIn("condition_272", output["true_conditions"])
        self.assertIn("condition_100", output["true_conditions"])
        self.assertEqual(output["diagnoses"][0]["diagnosis"], "GERD")
        self.assertEqual(output["diagnoses"][0]["score"], 15)


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_versions(self) -> None:
        response = self.client.get("/versions")
        self.assertEqual(response.status_code, 200)
        versions = response.json()["versions"]
        for version in ("v1", "v2", "v2.1"):
            self.assertIn(version, versions)

    def test_run_v1_biliary(self) -> None:
        response = self.client.post(
            "/run",
            json={
                "kg_version": "v1",
                "answers": {"q_location": "ruq", "q_fatty_food": "yes", "q_fever": "yes"},
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["diagnoses"][0]["diagnosis"], "Acute Cholecystitis")
        self.assertIn("supporting_evidence", body["diagnoses"][0])
        self.assertIn("Acute Cholecystitis", body["investigations"])
        self.assertIn("Acute Cholecystitis", body["treatments"])
        self.assertIn("disclaimer", body)

    def test_run_unknown_version_returns_404(self) -> None:
        response = self.client.post("/run", json={"kg_version": "v9", "answers": {}})
        self.assertEqual(response.status_code, 404)
        detail = response.json()["detail"]
        self.assertIn("v1", detail["available_versions"])

    def test_run_rejects_malformed_body(self) -> None:
        response = self.client.post("/run", json={"answers": {}})
        self.assertEqual(response.status_code, 422)

    def test_validate_prototype_passes(self) -> None:
        response = self.client.post("/validate", json={"kg_version": "v2.1", "profile": "prototype"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["profile"], "prototype")

    def test_validate_clinical_reports_errors(self) -> None:
        response = self.client.post("/validate", json={"kg_version": "v2.1", "profile": "clinical"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["valid"])
        self.assertGreater(body["error_count"], 0)

    def test_validate_unknown_profile_returns_400(self) -> None:
        response = self.client.post("/validate", json={"kg_version": "v1", "profile": "bogus"})
        self.assertEqual(response.status_code, 400)

    def test_validate_unknown_version_returns_404(self) -> None:
        response = self.client.post("/validate", json={"kg_version": "v9"})
        self.assertEqual(response.status_code, 404)


class DashboardTest(unittest.TestCase):
    def setUp(self) -> None:
        # The dashboard's source is a module-level singleton; reset its in-memory
        # "seen" state so tests don't leak into each other.
        from cdss.dashboard import app as dash_module
        from cdss.dashboard import auth
        if hasattr(dash_module.source, "_seen"):
            dash_module.source._seen.clear()
        self.client = TestClient(dashboard_app)
        # Log in as Nitin (all sample cases are tagged with his slug).
        self.client.cookies.set("cdss_session", auth.make_token("nitin"))

    def test_requires_login(self) -> None:
        anon = TestClient(dashboard_app)
        self.assertEqual(anon.get("/api/triage").status_code, 401)

    def test_only_shows_logged_in_doctors_patients(self) -> None:
        from cdss.dashboard import auth
        other = TestClient(dashboard_app)
        other.cookies.set("cdss_session", auth.make_token("krithi"))
        data = other.get("/api/triage").json()
        self.assertEqual(data["count"], 0)  # Krithi has no sample patients

    def test_triage_orders_first_come_first_serve(self) -> None:
        data = self.client.get("/api/triage").json()
        self.assertEqual(data["source"], "SampleSource")
        self.assertEqual(data["doctor"], "nitin")
        self.assertEqual(data["count"], 4)

        patients = data["patients"]
        # Arrival order: positions are 1..N and created_at is non-decreasing.
        self.assertEqual([p["position"] for p in patients], [1, 2, 3, 4])
        timestamps = [p["created_at"] for p in patients]
        self.assertEqual(timestamps, sorted(timestamps))

        # The pipeline still runs: the GI-bleeding case keeps its immediate red flag,
        # but it is NOT pushed to the front (risk no longer drives ordering).
        gi = next(p for p in patients if p["uhid"] == "DEMO-V1_GI_BLEEDING")
        self.assertTrue(any(f["urgency"] == "immediate" for f in gi["red_flags"]))
        self.assertNotEqual(gi["position"], 1)

        # Every card carries the new arrival-queue fields.
        for patient in patients:
            self.assertGreaterEqual(patient["waiting_minutes"], 0)
            self.assertTrue(patient["chief_complaint"])
            self.assertIn("main_symptom", patient)

    def test_mark_seen_hides_patient(self) -> None:
        before = self.client.get("/api/triage").json()
        target = before["patients"][0]["id"]

        self.client.post(f"/api/seen/{target}")

        after = self.client.get("/api/triage").json()
        self.assertEqual(after["count"], before["count"] - 1)
        self.assertNotIn(target, [p["id"] for p in after["patients"]])

        # Still visible when explicitly including seen patients.
        with_seen = self.client.get("/api/triage?include_seen=true").json()
        seen = next(p for p in with_seen["patients"] if p["id"] == target)
        self.assertEqual(seen["status"], "seen")

    def test_dashboard_page_and_assets_serve(self) -> None:
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertIn("Triage Dashboard", self.client.get("/").text)
        self.assertEqual(self.client.get("/dashboard.js").status_code, 200)
        self.assertEqual(self.client.get("/dashboard.css").status_code, 200)

    def test_anonymous_root_redirects_to_login(self) -> None:
        anon = TestClient(dashboard_app, follow_redirects=False)
        resp = anon.get("/")
        self.assertEqual(resp.status_code, 307)
        self.assertEqual(resp.headers["location"], "/login")

    def test_patient_detail_api_shape(self) -> None:
        d = self.client.get("/api/patient/v1_biliary_pain").json()
        for key in ("chief_complaint", "main_symptom", "draft_hpi", "answers",
                    "answers_summary", "differential", "suggested_tests",
                    "suggested_medications", "waiting_minutes", "patient_age", "patient_sex"):
            self.assertIn(key, d)
        self.assertEqual(d["differential"][0]["diagnosis"], "Acute Cholecystitis")
        self.assertEqual(d["differential"][0]["score"], 85)
        self.assertIn("Ultrasound Abdomen", d["suggested_tests"])
        self.assertGreaterEqual(d["waiting_minutes"], 0)
        self.assertTrue(d["answers_summary"])

    def test_patient_detail_carries_age_sex(self) -> None:
        d = self.client.get("/api/patient/v3_pancreatitis").json()
        self.assertEqual(d["patient_age"], "54")
        self.assertEqual(d["patient_sex"], "male")

    def test_consultation_save_round_trip(self) -> None:
        import tempfile
        from cdss.dashboard import consultations
        original = consultations.DB_PATH
        consultations.DB_PATH = os.path.join(tempfile.mkdtemp(), "c.db")
        try:
            body = {
                "note": {"chief_complaint": "Abdominal Pain",
                         "provisional_diagnosis": ["Acute Cholecystitis"],
                         "tests": ["Ultrasound Abdomen"],
                         "prescribed_medications": ["IV fluids"]},
                "suggestions": {
                    "diagnoses": {"offered": ["Acute Cholecystitis", "Biliary Colic"],
                                  "accepted": ["Acute Cholecystitis"]},
                    "tests": {"offered": ["CBC", "Ultrasound Abdomen"], "accepted": ["Ultrasound Abdomen"]},
                    "medications": {"offered": ["IV fluids"], "accepted": ["IV fluids"]}},
            }
            resp = self.client.post("/api/patient/v1_biliary_pain/consultation", json=body)
            self.assertEqual(resp.status_code, 200)
            cid = resp.json()["consultation_id"]
            saved = consultations.load_consultation(cid, path=consultations.DB_PATH)
            self.assertEqual(saved["final_diagnosis"], "Acute Cholecystitis")
            # accepted vs ignored suggestion telemetry is captured for future mining.
            flags = {(i["value"], i["accepted"]) for i in saved["items"] if i["source"] == "suggested"}
            self.assertIn(("Acute Cholecystitis", 1), flags)
            self.assertIn(("Biliary Colic", 0), flags)
            # symptom features captured from the pipeline's true conditions
            symptoms = {i["value"] for i in saved["items"] if i["kind"] == "symptom"}
            self.assertIn("ruq_pain", symptoms)
            # saving marks the patient seen (drops from the default queue)
            self.assertNotIn("v1_biliary_pain", [p["id"] for p in self.client.get("/api/triage").json()["patients"]])
        finally:
            consultations.DB_PATH = original

    def test_consultation_logs_doctor_additional_findings(self) -> None:
        import tempfile
        from cdss.dashboard import consultations
        original = consultations.DB_PATH
        consultations.DB_PATH = os.path.join(tempfile.mkdtemp(), "c.db")
        try:
            body = {"note": {"additional_findings": ["night sweats", "recent travel"]},
                    "suggestions": {}}
            resp = self.client.post("/api/patient/v1_biliary_pain/consultation", json=body)
            self.assertEqual(resp.status_code, 200)
            saved = consultations.load_consultation(resp.json()["consultation_id"], path=consultations.DB_PATH)
            # Doctor-added findings are mineable items: kind=symptom, source=doctor.
            doc_symptoms = {i["value"] for i in saved["items"]
                            if i["kind"] == "symptom" and i["source"] == "doctor"}
            self.assertIn("night sweats", doc_symptoms)
            self.assertIn("recent travel", doc_symptoms)
            self.assertEqual(saved["note"]["additional_findings"], ["night sweats", "recent travel"])
        finally:
            consultations.DB_PATH = original

    def test_malformed_consultation_is_422(self) -> None:
        self.assertEqual(
            self.client.post("/api/patient/v1_biliary_pain/consultation", json={"bad": 1}).status_code, 422
        )

    def test_patient_ownership_enforced(self) -> None:
        from cdss.dashboard import auth
        other = TestClient(dashboard_app, follow_redirects=False)
        other.cookies.set("cdss_session", auth.make_token("krithi"))
        self.assertEqual(other.get("/api/patient/v1_biliary_pain").status_code, 404)
        self.assertEqual(other.get("/patient/v1_biliary_pain").status_code, 404)
        self.assertEqual(
            other.post("/api/patient/v1_biliary_pain/consultation", json={"note": {}}).status_code, 404
        )
        # Anonymous: API 401, page redirects to login.
        anon = TestClient(dashboard_app, follow_redirects=False)
        self.assertEqual(anon.get("/api/patient/v1_biliary_pain").status_code, 401)
        self.assertEqual(anon.get("/patient/v1_biliary_pain").status_code, 307)


class AuthTest(unittest.TestCase):
    def test_password_hash_roundtrip(self) -> None:
        from cdss.dashboard import auth
        stored = auth.hash_password("s3cret!")
        self.assertTrue(auth.verify_password("s3cret!", stored))
        self.assertFalse(auth.verify_password("wrong", stored))

    def test_session_token_roundtrip_and_tamper(self) -> None:
        from cdss.dashboard import auth
        token = auth.make_token("nitin")
        self.assertEqual(auth.read_token(token), "nitin")
        self.assertIsNone(auth.read_token("nitin.deadbeef"))   # bad signature
        self.assertIsNone(auth.read_token(None))


class NotifyListenerTest(unittest.TestCase):
    """The email listener's decision logic is pure (no Firestore/SMTP needed)."""

    CREDS = {"email": "dr@h.com", "password": "x", "receptionist": "recep@h.com"}

    def _plan(self, submission, creds=None, name="Nitin Jagtap"):
        from cdss.notify.listener import plan_confirmation
        return plan_confirmation(submission, creds, name)

    def test_patient_with_email_gets_confirmation(self) -> None:
        plan = self._plan({"patient_name": "Asha", "uhid": "UH1", "patient_email": "asha@x.com"}, self.CREDS)
        self.assertEqual(plan.action, "patient")
        self.assertEqual(plan.recipient, "asha@x.com")
        self.assertIn("Nitin Jagtap", plan.subject)

    def test_no_email_notifies_receptionist(self) -> None:
        plan = self._plan({"patient_name": "Ravi", "uhid": "UH2", "patient_email": ""}, self.CREDS)
        self.assertEqual(plan.action, "receptionist")
        self.assertEqual(plan.recipient, "recep@h.com")
        self.assertIn("Ravi", plan.subject)

    def test_doctor_without_credentials_is_skipped(self) -> None:
        plan = self._plan({"patient_name": "Ravi", "patient_email": "r@x.com"}, None)
        self.assertEqual(plan.action, "skip")

    def test_no_email_and_no_receptionist_is_skipped(self) -> None:
        plan = self._plan({"patient_name": "Ravi"}, {"email": "a@b.com", "password": "x"})
        self.assertEqual(plan.action, "skip")


class V3SymptomFirstTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kg = KnowledgeGraph.load("knowledge_graph/v3")
        cls.pipeline = CDSSPipeline(cls.kg)

    def test_v3_loads_and_validates_clinical(self) -> None:
        self.assertTrue(self.kg.is_symptom_first)
        self.assertIn("abdominal_pain", self.kg.symptoms)
        report = validate(self.kg, profile="clinical")
        self.assertTrue(report.is_valid, report.as_dict())

    def test_v3_weighted_differential_ranks_and_is_explainable(self) -> None:
        # Classic acute pancreatitis presentation.
        result = self.pipeline.run({
            "q_main_complaint": "abdominal_pain", "q_ap_site": "epigastric", "q_ap_onset": "sudden",
            "q_ap_character": "boring", "q_ap_radiation": "to_back", "q_ap_severity": "severe",
            "q_ap_timing": "constant", "q_ap_fatty": "no", "q_ap_relieved_food": "no",
            "q_ap_fever": "no", "q_ap_vomiting": "yes", "q_ap_jaundice": "no",
        }).as_dict()
        diffs = result["diagnoses"]
        self.assertEqual(diffs[0]["diagnosis"], "Acute Pancreatitis")
        # Confidence is a normalised 0-100 across the differential and is ranked.
        confidences = [d["confidence"] for d in diffs]
        self.assertTrue(all(0 <= c <= 100 for c in confidences))
        self.assertEqual(confidences, sorted(confidences, reverse=True))
        self.assertIn("Radiation to the back", diffs[0]["supporting_evidence"])

    def test_v3_negative_evidence_lowers_score(self) -> None:
        # Relief by food argues FOR peptic ulcer but AGAINST pancreatitis.
        result = self.pipeline.run({
            "q_main_complaint": "abdominal_pain", "q_ap_site": "epigastric", "q_ap_onset": "gradual",
            "q_ap_character": "burning", "q_ap_radiation": "none", "q_ap_severity": "mild",
            "q_ap_timing": "fasting", "q_ap_fatty": "no", "q_ap_relieved_food": "yes",
            "q_ap_fever": "no", "q_ap_vomiting": "no", "q_ap_jaundice": "no",
        }).as_dict()
        by_name = {d["diagnosis"]: d for d in result["diagnoses"]}
        self.assertEqual(result["diagnoses"][0]["diagnosis"], "Peptic Ulcer Disease")
        # Pancreatitis, if present, carries the negative finding as evidence-against.
        if "Acute Pancreatitis" in by_name:
            self.assertIn("Relieved by food/antacids", by_name["Acute Pancreatitis"].get("evidence_against", []))

    def test_v3_structured_summary_and_hpi_are_deterministic(self) -> None:
        result = self.pipeline.run({
            "q_main_complaint": "abdominal_pain", "q_ap_site": "ruq", "q_ap_onset": "gradual",
            "q_ap_character": "colicky", "q_ap_radiation": "to_right_shoulder", "q_ap_severity": "moderate",
            "q_ap_timing": "after_meals", "q_ap_fatty": "yes", "q_ap_relieved_food": "no",
            "q_ap_fever": "yes", "q_ap_vomiting": "no", "q_ap_jaundice": "no",
        }).as_dict()
        summary = result["symptom_summary"]
        self.assertEqual(summary["chief_complaint"], "Abdominal Pain")
        self.assertEqual(summary["findings"]["Site"], "right upper quadrant")
        self.assertEqual(summary["findings"]["Radiation"], "to the right shoulder")
        self.assertIn("fever", summary["findings"]["Associated symptoms"])
        self.assertTrue(result["draft_hpi"].startswith("Patient presents with abdominal pain"))

    def test_v3_reachability_validator_flags_unreachable_workup(self) -> None:
        from pathlib import Path
        from cdss.knowledge.models import Flow, Question, Symptom
        broken = KnowledgeGraph(
            version="vbroken", path=Path("."),
            questions={"q_a": Question("q_a", "A", "yes_no"), "q_b": Question("q_b", "B", "yes_no")},
            flows={"f": Flow(id="f", start="q_a", transitions={})},  # q_b never reached
            symptoms={"s": Symptom(id="s", label="S", flow="f", workup=["q_a", "q_b"])},
        )
        codes = {issue.code for issue in validate(broken).issues}
        self.assertIn("unreachable_workup_question", codes)


class V4QuestionTypeTest(unittest.TestCase):
    """v4 introduces number / text / multi_choice questions. They must flow through
    the summary engine and the dashboard answer labels cleanly (not as raw lists)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.kg = KnowledgeGraph.load("knowledge_graph/v4")
        cls.pipeline = CDSSPipeline(cls.kg)

    def test_v4_loads_all_chief_complaints_and_validates_clinical(self) -> None:
        self.assertTrue(self.kg.is_symptom_first)
        self.assertEqual(len(self.kg.symptoms), 17)
        report = validate(self.kg, profile="clinical")
        self.assertTrue(report.is_valid, report.as_dict())

    def test_v4_multi_choice_renders_in_summary(self) -> None:
        result = self.pipeline.run({
            "q_main_complaint": "heartburn", "hb_location": "chest", "hb_rising": "yes",
            "hb_taste": "yes", "hb_timing": "at_night", "hb_triggers": ["spicy", "coffee"],
        }).as_dict()
        # multi_choice joins option labels — never a raw "['spicy', ...]".
        self.assertEqual(result["symptom_summary"]["findings"]["Aggravating"], "Spicy food and Coffee")
        self.assertIn("triggered by Spicy food and Coffee", result["draft_hpi"])
        self.assertNotIn("[", result["draft_hpi"])

    def test_v4_number_renders_in_summary(self) -> None:
        result = self.pipeline.run({
            "q_main_complaint": "diarrhoea", "di_duration": "lt_2w", "di_frequency": 6,
            "di_consistency": "watery", "di_blood": "no",
        }).as_dict()
        self.assertEqual(result["symptom_summary"]["findings"]["Frequency"], "6")
        self.assertIn("6 times a day", result["draft_hpi"])

    def test_v4_dashboard_answer_label_handles_new_types(self) -> None:
        from cdss.dashboard import summary as dsummary
        self.assertEqual(dsummary.answer_label(self.kg, "hb_triggers", ["spicy", "coffee"]), "Spicy food, Coffee")
        self.assertEqual(dsummary.answer_label(self.kg, "di_frequency", 6), "6")
        self.assertEqual(dsummary.answer_label(self.kg, "gen_surgery_detail", "Appendix 2019"), "Appendix 2019")

    def test_v4_flow_chains_symptom_then_general_block(self) -> None:
        from cdss.questionnaire.flow_engine import FlowEngine
        fe = FlowEngine(self.kg)
        # End of a symptom flow chains into the shared general block...
        end_of_diarrhoea = fe.next_question("di_recent_antibiotics", "no")["next_question"]
        self.assertEqual(end_of_diarrhoea, self.kg.flows["general"].start)
        # ...and the last general question ends the questionnaire (no re-chain).
        self.assertIsNone(fe.next_question("gen_fatigue", "no")["next_question"])

    def test_v3_flow_does_not_chain_to_general(self) -> None:
        # v3 has no separate general flow, so symptom flows still end at None.
        from cdss.questionnaire.flow_engine import FlowEngine
        v3 = KnowledgeGraph.load("knowledge_graph/v3")
        self.assertNotIn("general", v3.flows)
        fe = FlowEngine(v3)
        # A terminal v3 question must not invent a next question.
        self.assertIsNone(fe.next_question("q_ap_jaundice", "no")["next_question"])

    def test_v4_new_symptom_differential_is_scoped(self) -> None:
        # Rectal bleeding + a shared general finding (weight loss) must not pull in
        # diagnoses from other chief complaints.
        result = self.pipeline.run({
            "q_main_complaint": "rectal_bleeding", "rb_colour": "mixed_in_stool",
            "rb_pain": "no", "rb_bowel_change": "yes", "gen_weight_loss": "yes",
        }).as_dict()
        self.assertEqual(result["diagnoses"][0]["diagnosis"], "Colorectal Cancer")
        allowed = {"Haemorrhoids", "Anal Fissure", "Colorectal Cancer",
                   "Inflammatory Bowel Disease", "Diverticular Disease"}
        self.assertTrue({d["diagnosis"] for d in result["diagnoses"]}.issubset(allowed))


class V4MultiSymptomTest(unittest.TestCase):
    """Multi-symptom intake: q_main_complaint is multi-select; each complaint gets a
    full work-up, then the shared general block; the differential is one deduped list."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.kg = KnowledgeGraph.load("knowledge_graph/v4")
        cls.pipeline = CDSSPipeline(cls.kg)

    def test_main_complaint_is_multi_select(self) -> None:
        self.assertEqual(self.kg.questions["q_main_complaint"].type, "multi_choice")

    def test_flow_walks_each_complaint_then_general(self) -> None:
        from cdss.questionnaire.flow_engine import FlowEngine
        fe = FlowEngine(self.kg)
        chosen = ["heartburn", "constipation"]
        first = fe.next_question("q_main_complaint", chosen, chosen_symptoms=chosen)["next_question"]
        self.assertEqual(first, self.kg.flows["heartburn"].start)
        # End of the first complaint's flow advances to the second complaint's start.
        after_first = fe.next_question("hb_prior_scope", "no", chosen_symptoms=chosen)["next_question"]
        self.assertEqual(after_first, self.kg.flows["constipation"].start)
        # End of the last complaint's flow chains into the shared general block.
        after_last = fe.next_question("co_laxatives", "no", chosen_symptoms=chosen)["next_question"]
        self.assertEqual(after_last, self.kg.flows["general"].start)

    def test_resolve_symptoms_returns_all_selected(self) -> None:
        from cdss.recommendations.summary_engine import resolve_symptoms
        syms = resolve_symptoms(self.kg, {"q_main_complaint": ["heartburn", "constipation"]})
        self.assertEqual([s.id for s in syms], ["heartburn", "constipation"])

    def test_shared_diagnosis_deduped_with_merged_evidence(self) -> None:
        # Diarrhoea + constipation both include colorectal cancer — it must appear ONCE.
        result = self.pipeline.run({
            "q_main_complaint": ["diarrhoea", "constipation"],
            "di_duration": "gt_4w", "di_blood": "yes",
            "co_duration": "lt_1m", "co_blood": "yes", "gen_weight_loss": "yes",
        }).as_dict()
        names = [d["diagnosis"] for d in result["diagnoses"]]
        self.assertEqual(names.count("Colorectal Cancer"), 1)
        # One HPI paragraph + one structured summary per complaint.
        self.assertEqual(result["draft_hpi"].count("Patient presents with"), 2)
        self.assertEqual(len(result["symptom_summaries"]), 2)

    def test_assess_detail_tags_complaints_and_sends_maps(self) -> None:
        from cdss.api.registry import PipelineRegistry
        from cdss.dashboard.triage import assess_detail
        sub = {"id": "m1", "kg_version": "v4", "answers": {
            "q_main_complaint": ["diarrhoea", "constipation"],
            "di_duration": "gt_4w", "di_blood": "yes", "co_blood": "yes", "gen_weight_loss": "yes"}}
        d = assess_detail(sub, PipelineRegistry())
        crc = next(x for x in d["differential"] if x["diagnosis"] == "Colorectal Cancer")
        self.assertCountEqual(crc["chief_complaints"], ["Diarrhoea", "Constipation"])
        self.assertIn("Colorectal Cancer", d["tests_by_diagnosis"])
        self.assertEqual(len(d["symptom_summaries"]), 2)
        self.assertIn("Diarrhoea", d["chief_complaint"])
        self.assertIn("Constipation", d["chief_complaint"])

    def test_single_complaint_still_works(self) -> None:
        # A one-element selection behaves like the old single-symptom path (no tags).
        from cdss.api.registry import PipelineRegistry
        from cdss.dashboard.triage import assess_detail
        sub = {"id": "s1", "kg_version": "v4", "answers": {
            "q_main_complaint": ["heartburn"], "hb_rising": "yes", "hb_taste": "yes"}}
        d = assess_detail(sub, PipelineRegistry())
        self.assertEqual(d["chief_complaint"], "Heartburn / Acid Reflux")
        self.assertNotIn("chief_complaints", d["differential"][0])


class V4InteractiveQuestionTest(unittest.TestCase):
    """region_select (abdomen diagram) + bristol_select (stool chart) — multi-select
    answers that drive the differential and render in the HPI, plus the new
    constipation questions (urge, timing, Bristol)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.kg = KnowledgeGraph.load("knowledge_graph/v4")
        cls.pipeline = CDSSPipeline(cls.kg)

    def test_new_question_types_declared(self) -> None:
        self.assertEqual(self.kg.questions["ap_site"].type, "region_select")
        self.assertEqual(self.kg.questions["co_bristol"].type, "bristol_select")
        # Region options keep the clinical site value AND map to an SVG region key.
        opt = self.kg.questions["ap_site"].options[0]
        self.assertIn("region", opt)
        self.assertIn("value", opt)

    def test_constipation_workup_includes_new_questions(self) -> None:
        workup = self.kg.symptoms["constipation"].workup
        for qid in ("co_urge", "co_timing", "co_bristol"):
            self.assertIn(qid, workup)
        # Flow reaches them in order: frequency -> urge -> timing -> bristol -> straining.
        trans = self.kg.flows["constipation"].transitions
        self.assertEqual(trans["co_frequency"]["default"], "co_urge")
        self.assertEqual(trans["co_urge"]["default"], "co_timing")
        self.assertEqual(trans["co_timing"]["default"], "co_bristol")
        self.assertEqual(trans["co_bristol"]["default"], "co_straining")

    def test_list_answer_drives_condition_via_membership(self) -> None:
        # A multi-region pain answer satisfies the existing `ap_site == 'ruq'` rule.
        conditions = ConditionEngine(self.kg).evaluate({"ap_site": ["ruq", "epigastric"]})
        self.assertTrue(conditions["ap_ruq"])
        self.assertTrue(conditions["ap_epigastric"])
        self.assertFalse(conditions["ap_rlq"])
        # Bristol 1/2 triggers the hard-stool condition (OR over membership).
        conditions = ConditionEngine(self.kg).evaluate({"co_bristol": ["2"]})
        self.assertTrue(conditions["co_hard_stool"])
        conditions = ConditionEngine(self.kg).evaluate({"co_bristol": ["5"]})
        self.assertFalse(conditions["co_hard_stool"])

    def test_multi_region_pain_ranks_and_renders(self) -> None:
        result = self.pipeline.run({
            "q_main_complaint": ["abdominal_pain"], "ap_duration": "1_4w",
            "ap_site": ["ruq", "epigastric"], "ap_character": "cramping",
            "ap_radiation": "right_shoulder", "ap_meals": "worse_eating",
        }).as_dict()
        # Biliary colic (RUQ + radiation to shoulder) should surface.
        self.assertIn("Biliary Colic", [d["diagnosis"] for d in result["diagnoses"]])
        self.assertIn("right upper quadrant and epigastrium", result["draft_hpi"])

    def test_bristol_and_urge_feed_functional_constipation(self) -> None:
        result = self.pipeline.run({
            "q_main_complaint": ["constipation"], "co_duration": "gt_6m",
            "co_frequency": "once_3plus_days", "co_urge": "no", "co_timing": ["morning"],
            "co_bristol": ["1", "2"], "co_straining": "yes",
        }).as_dict()
        top = result["diagnoses"][0]["diagnosis"]
        self.assertEqual(top, "Functional Constipation")
        self.assertIn("Bristol type 1", result["draft_hpi"])

    def test_browser_export_carries_new_types(self) -> None:
        from webapp.build_kg_json import build_payload
        payload = build_payload("v4")
        self.assertEqual(payload["questions"]["ap_site"]["type"], "region_select")
        self.assertEqual(payload["questions"]["co_bristol"]["type"], "bristol_select")
        self.assertEqual(len(payload["questions"]["co_bristol"]["options"]), 7)
        self.assertIn("region", payload["questions"]["ap_site"]["options"][0])


if __name__ == "__main__":
    unittest.main()
