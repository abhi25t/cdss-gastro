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
        for version in ("v1", "v2", "v2.1", "v3"):
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


if __name__ == "__main__":
    unittest.main()
