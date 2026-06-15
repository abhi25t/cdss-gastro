# `doctors/` — doctor registry & credentials

This folder defines which doctors use the CDSS and holds their (secret) credentials.

> **Git:** everything here is **git-ignored** except `*.example.yaml` and this README.
> Never commit real emails, passwords, or hashes — the repo is public.

## Files

| File | Tracked? | What it is |
|------|----------|------------|
| `doctors.yaml` | ignored | The registry: a list of `{slug, name}`. Source of truth for who exists. |
| `<slug>.yaml` | ignored | One per doctor: confirmation-email creds + receptionist + dashboard password hash. |
| `email_params.yaml` | ignored | Shared SMTP server + port for the email listener. |
| `*.example.yaml` | tracked | Templates showing the format (no secrets). |

The **slug** is the single identity used everywhere: the check-in URL path
(`aig-cdss-opd-gastro.web.app/<slug>`), this credential filename, the `doctor_slug` tag
on each Firestore submission, and the dashboard login/filter. Pick it explicitly
(usually the first name); for a clash, choose a distinct slug for the second doctor.

## Add a doctor

1. Add an entry to `doctors.yaml` (`- slug: …` / `  name: …`).
2. (Optional, for confirmation email + dashboard access) `cp doctor.example.yaml <slug>.yaml`
   and fill in `email`, `password`, `receptionist`.
3. Set their dashboard password: `python -m cdss.dashboard.auth set-password <slug>`.
4. `python webapp/build_doctors_js.py` then `firebase deploy --only hosting`.
5. `python webapp/make_poster.py --doctor-slug <slug>` to print their waiting-room QR.

A doctor with only a registry entry (no `<slug>.yaml`) still works for patient check-in;
they just have no confirmation email and can't log in to the dashboard until step 2–3.

## First-time setup

```bash
cp doctors.example.yaml      doctors.yaml
cp email_params.example.yaml email_params.yaml
cp doctor.example.yaml       <slug>.yaml      # for each doctor with credentials
```
