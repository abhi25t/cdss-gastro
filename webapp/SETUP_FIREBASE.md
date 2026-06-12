# Firebase setup (one-time, ~10 minutes)

These are the steps only you can do — they happen in the Firebase website. I've
already written all the code; this connects it to your own Firebase project.
Nothing here costs money (the free "Spark" plan is enough for a clinic).

You'll set up two things:
1. The **patient side** — phones write their answers to a cloud database.
2. The **doctor side** — a secret key file so the dashboard (later) can read them.

---

## Part A — Create the project and database

1. Go to <https://console.firebase.google.com> and sign in with your Google
   account (the same `docnits13@gmail.com` is fine).
2. Click **Add project**. Give it a name like `cdss-triage`. You can disable
   Google Analytics (not needed). Click through to **Create project**.
3. In the left menu open **Build → Firestore Database** → **Create database**.
   - Choose a location close to your hospital (e.g. an India region).
   - Start in **Production mode** (we'll paste secure rules in Part C).

## Part B — Register the patient web app

4. Click the gear icon (top left) → **Project settings**.
5. Scroll to **Your apps** → click the **`</>`** (Web) icon.
6. Give it a nickname like `patient-app`. **Do not** check "Firebase Hosting".
   Click **Register app**.
7. You'll see a `firebaseConfig = { ... }` block. Copy those values.
8. On your computer, in `webapp/patient/`, copy `firebase-config.example.js` to
   `firebase-config.js` and paste your values in. (The real file is git-ignored.)

   ```bash
   cd webapp/patient
   cp firebase-config.example.js firebase-config.js
   # then edit firebase-config.js and paste your apiKey, projectId, etc.
   ```

## Part C — Turn on anonymous sign-in and lock down the rules

9. Left menu → **Build → Authentication** → **Get started** →
   **Sign-in method** tab → enable **Anonymous** → Save.
   (This lets patient phones write without making an account.)

10. Left menu → **Build → Firestore Database → Rules** tab. Replace everything
    with the rules below and click **Publish**:

    ```
    rules_version = '2';
    service cloud.firestore {
      match /databases/{database}/documents {
        match /submissions/{id} {
          // A signed-in (anonymous) patient may CREATE one submission with a
          // sensible shape — and do nothing else.
          allow create: if request.auth != null
            && request.resource.data.uhid is string
            && request.resource.data.uhid.size() > 0
            && request.resource.data.kg_version is string
            && request.resource.data.answers is map;

          // No browser client can read, change, or delete submissions.
          // The doctor dashboard reads them with a service-account key
          // (Part D), which bypasses these rules safely on the server side.
          allow read, update, delete: if false;
        }
      }
    }
    ```

    What this guarantees: a patient phone can only *drop off* a submission. It
    cannot read anyone's data, list submissions, or edit/delete them. Patient
    data is never readable from a browser.

## Part D — Service-account key for the doctor dashboard (do this now, used later)

11. Gear icon → **Project settings → Service accounts** tab.
12. Click **Generate new private key** → **Generate key**. A `.json` file
    downloads.
13. Move that file to the project root and name it `serviceAccountKey.json`:

    ```bash
    mv ~/Downloads/<the-downloaded-file>.json /home/ai/proj/CDSS/serviceAccountKey.json
    ```

    **Keep this file private** — it grants full access to your database. It's
    already git-ignored so it won't be committed. This file lives only on the
    doctor's machine.

---

## Test the patient side

1. Rebuild/serve the patient app:
   ```bash
   /home/ai/pyenv/cdss/bin/python webapp/build_kg_json.py --kg-version v1
   cd webapp/patient && /home/ai/pyenv/cdss/bin/python -m http.server 6200
   ```
2. Open it on your phone, complete a questionnaire, and tap **Submit to doctor**.
3. In the Firebase console → **Firestore Database → Data**, you should see a new
   `submissions` document appear with the UHID and answers.

If submission fails, open the browser console (or check the on-screen error) —
the most common causes are a typo in `firebase-config.js` or forgetting to enable
Anonymous sign-in (Part C step 9).

---

## Part E — Publish the patient app + print the waiting-room poster

So far the app only runs on your own laptop (`http.server`). Patients in the waiting
room are on **cellular** and can't reach your laptop, so we publish the app to
**Firebase Hosting** — same project, free, gives you a public HTTPS address like
`https://cdss-triage.web.app`. Because it's the same Firebase project, that address
is *automatically* allowed to sign in (no extra Auth step).

> **Is it safe to put the app on the public internet?** Yes. The values in
> `firebase-config.js` are *meant* to be public — every Firebase web app ships them
> to the browser. Your data is protected by the create-only rules from Part C (a
> phone can only drop off a submission; it can't read anyone's data), not by hiding
> that file. The secret `serviceAccountKey.json` is **not** part of the app and is
> never published.

**1. Install the Firebase command-line tool** (this machine has no Node, so use the
standalone installer):

```bash
curl -sL https://firebase.tools | bash      # may ask for your password (sudo)
firebase --version                           # confirm it installed
```

**2. Log in to Google** (opens a browser sign-in). Run this yourself; on this
terminal type it with a leading `!` so the output shows up here:

```bash
firebase login --no-localhost      # prints a link + code to paste back
```

**3. Point the project at your Firebase project** — copy the example and set your id
(this file is git-ignored, like `firebase-config.js`):

```bash
cp .firebaserc.example .firebaserc
# edit .firebaserc and set "default" to your project id, e.g. cdss-triage
# (or run: firebase use --add  and pick it from the list)
```

**4. Build the questionnaire data and deploy:**

```bash
/home/ai/pyenv/cdss/bin/python webapp/build_kg_json.py --kg-version v1
firebase deploy --only hosting
```

When it finishes it prints your **Hosting URL** (e.g. `https://cdss-triage.web.app`).
Open it in any browser to confirm the app loads, then open it on a phone **using
mobile data** (not hospital wifi) and submit a test questionnaire — it should appear
in Firestore and on the dashboard exactly as before.

> If anonymous sign-in is ever rejected on the live URL, add the domain manually:
> Firebase console → **Authentication → Settings → Authorized domains → Add domain**
> → `cdss-triage.web.app`. (It's normally added for you automatically.)

**5. Print the waiting-room poster.** This makes a printable sheet with a big QR code
that opens the app when a patient points their phone camera at it:

```bash
/home/ai/pyenv/cdss/bin/pip install "qrcode[pil]"      # one time
/home/ai/pyenv/cdss/bin/python webapp/make_poster.py --url https://cdss-triage.web.app
# writes webapp/poster.png — open and print it (A4). Use --out poster.pdf for a PDF.
# customise the wording with --title and --subtitle
```

Stick the printed poster up in the waiting room. (This QR holds the *app address* —
it is not the same as the UHID barcode the app itself scans on each patient.)

---

## Notes / for later

- **App Check** (extra protection against abuse from outside the app) is optional
  hardening we can add after the POC works.
- **End-of-day cleanup**: submissions are only needed for that day's triage. We'll
  add automatic deletion of old records (data minimization) on the dashboard side.
