"""Email confirmation listener — runs on the on-prem server.

Watches the Firestore `submissions` collection. For each new submission it sends an
instant confirmation:

  * patient gave an email  -> confirm to the patient
  * patient gave no email  -> notify the doctor's receptionist (who tells the patient)

The doctor's sending mailbox + SMTP password live in doctors/<slug>.yaml; the shared
SMTP host/port in doctors/email_params.yaml. A doctor with no credential file (or no
email configured) is skipped gracefully. Each submission is processed at most once,
tracked by the `confirmation_sent` flag on the document (idempotent across restarts).

Usage:
    python -m cdss.notify.listener            # live listener (long-running)
    python -m cdss.notify.listener --once     # one pass over pending, then exit (testing)
"""

from __future__ import annotations

import argparse
import smtplib
import threading
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from cdss import doctors
from cdss.dashboard.source import DEFAULT_KEY_PATH

COLLECTION = "submissions"


@dataclass
class Plan:
    """The decided action for one submission (pure, no I/O — easy to unit-test)."""
    action: str          # "patient" | "receptionist" | "skip"
    reason: str          # human-readable, for logs and skip explanations
    recipient: str = ""
    subject: str = ""
    body: str = ""


def plan_confirmation(submission: dict[str, Any], credentials: dict[str, Any] | None,
                      doctor_display_name: str) -> Plan:
    """Decide who (if anyone) gets the confirmation email for one submission."""
    name = str(submission.get("patient_name") or "").strip() or "Patient"
    uhid = str(submission.get("uhid") or "").strip() or "—"
    patient_email = str(submission.get("patient_email") or "").strip()
    dr = f"Dr. {doctor_display_name}"

    if not credentials:
        return Plan("skip", f"no credential file for this doctor; cannot send")
    sender = str(credentials.get("email") or "").strip()
    password = str(credentials.get("password") or "").strip()
    if not sender or not password:
        return Plan("skip", "doctor has no sending mailbox configured")

    if patient_email:
        return Plan(
            action="patient",
            reason="patient email present",
            recipient=patient_email,
            subject=f"Your check-in with {dr} has been received",
            body=(
                f"Dear {name},\n\n"
                f"{dr}'s clinic has received your check-in details. Please wait in the "
                f"waiting area — you will be called by name when it is your turn.\n\n"
                f"Hospital ID: {uhid}\n\n"
                f"This is an automated confirmation. Please do not reply.\n"
            ),
        )

    receptionist = str(credentials.get("receptionist") or "").strip()
    if not receptionist:
        return Plan("skip", "patient gave no email and no receptionist is configured")
    return Plan(
        action="receptionist",
        reason="no patient email; notifying receptionist",
        recipient=receptionist,
        subject=f"New check-in (no email): {name} — {uhid}",
        body=(
            f"A patient checked in for {dr} without giving an email address.\n\n"
            f"Name: {name}\n"
            f"Hospital ID: {uhid}\n\n"
            f"Please let them know their details were received.\n"
        ),
    )


def send_email(params: dict[str, Any], credentials: dict[str, Any], plan: Plan) -> None:
    """Send one email via the shared SMTP server using the doctor's mailbox."""
    sender = str(credentials["email"]).strip()
    password = str(credentials["password"]).strip()
    host = str(params.get("smtp_server") or "").strip()
    port = int(params.get("port") or 587)
    if not host:
        raise RuntimeError("email_params.yaml has no smtp_server")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = plan.recipient
    msg["Subject"] = plan.subject
    msg.set_content(plan.body)

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(sender, password)
        smtp.send_message(msg)


def _process_doc(db, doc, *, dry_run: bool = False) -> str:
    """Handle one Firestore document snapshot. Returns a short status string."""
    data = doc.to_dict() or {}
    # Only act on docs created with the new schema and not yet handled.
    if data.get("confirmation_sent") is not False:
        return "already-handled"

    slug = doctors.canonical_slug(data.get("doctor_slug", ""))
    credentials = doctors.load_credentials(slug)
    params = doctors.load_email_params()
    plan = plan_confirmation(data, credentials, doctors.doctor_name(slug))

    if plan.action == "skip":
        print(f"[listener] {doc.id} ({slug}): skip — {plan.reason}")
        if not dry_run:
            doc.reference.update({"confirmation_sent": True, "confirmation_to": f"skipped:{plan.reason}"})
        return "skipped"

    if dry_run:
        print(f"[listener] {doc.id} ({slug}): would email {plan.action} <{plan.recipient}>")
        return f"would-{plan.action}"

    try:
        send_email(params, credentials, plan)
    except Exception as exc:  # noqa: BLE001 — log and leave for retry on next pass
        print(f"[listener] {doc.id} ({slug}): SEND FAILED — {exc}")
        return "send-failed"

    doc.reference.update({"confirmation_sent": True, "confirmation_to": f"{plan.action}:{plan.recipient}"})
    print(f"[listener] {doc.id} ({slug}): emailed {plan.action} <{plan.recipient}>")
    return f"sent-{plan.action}"


def run_once(db, *, dry_run: bool = False) -> int:
    """Process all currently-pending submissions once. Returns the count handled."""
    handled = 0
    for doc in db.collection(COLLECTION).where("confirmation_sent", "==", False).stream():
        result = _process_doc(db, doc, dry_run=dry_run)
        if result not in ("already-handled",):
            handled += 1
    print(f"[listener] one pass complete — {handled} submission(s) handled")
    return handled


def watch(db) -> None:
    """Live listener: react to new submissions as they arrive (Ctrl-C to stop)."""
    done = threading.Event()

    def on_snapshot(col_snapshot, changes, read_time):
        for change in changes:
            if change.type.name in ("ADDED", "MODIFIED"):
                try:
                    _process_doc(db, change.document)
                except Exception as exc:  # noqa: BLE001
                    print(f"[listener] error handling {change.document.id}: {exc}")

    query = db.collection(COLLECTION).where("confirmation_sent", "==", False)
    watch_handle = query.on_snapshot(on_snapshot)
    print("[listener] watching for new submissions… (Ctrl-C to stop)")
    try:
        done.wait()
    except KeyboardInterrupt:
        print("\n[listener] stopping.")
    finally:
        watch_handle.unsubscribe()


def _init_db(key_path=DEFAULT_KEY_PATH):
    import firebase_admin
    from firebase_admin import credentials as fb_credentials, firestore

    if not key_path.exists():
        raise SystemExit(
            f"Service account key not found at {key_path}. See webapp/SETUP_FIREBASE.md Part D."
        )
    if not firebase_admin._apps:
        firebase_admin.initialize_app(fb_credentials.Certificate(str(key_path)))
    return firestore.client()


def main() -> int:
    ap = argparse.ArgumentParser(description="CDSS patient check-in email listener.")
    ap.add_argument("--once", action="store_true",
                    help="Process pending submissions once and exit (default: live watch).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Decide and log recipients but send nothing / mark nothing.")
    args = ap.parse_args()

    db = _init_db()
    if args.once or args.dry_run:
        run_once(db, dry_run=args.dry_run)
    else:
        watch(db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
