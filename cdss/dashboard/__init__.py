"""Doctor-side triage dashboard.

Reads patient submissions (Firestore via service account, or local samples),
runs them through the CDSS pipeline, and serves a risk-ranked triage board.
Runs on the doctor's machine, which has internet access.
"""
