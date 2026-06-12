/* Sends a completed questionnaire to Firestore.
 *
 * Loads only if window.FIREBASE_CONFIG is present (firebase-config.js). On
 * success it defines window.CDSS_submitToBackend(payload), which app.js calls on
 * Submit. Patients sign in anonymously; Firestore security rules allow them to
 * CREATE a submission and nothing else (no read/update/delete). See
 * SETUP_FIREBASE.md.
 *
 * The SDK version below can be bumped freely; it's just a CDN path. */
const SDK = "https://www.gstatic.com/firebasejs/10.12.5";

const cfg = window.FIREBASE_CONFIG;
if (!cfg || !cfg.apiKey || cfg.apiKey === "PASTE_API_KEY") {
  console.warn("[firebase-submit] No Firebase config found — submissions will be logged locally only.");
} else {
  try {
    const { initializeApp } = await import(`${SDK}/firebase-app.js`);
    const { getAuth, signInAnonymously } = await import(`${SDK}/firebase-auth.js`);
    const { getFirestore, collection, addDoc, serverTimestamp } =
      await import(`${SDK}/firebase-firestore.js`);

    const app = initializeApp(cfg);
    const auth = getAuth(app);
    const db = getFirestore(app);

    window.CDSS_submitToBackend = async function (payload) {
      await signInAnonymously(auth);
      await addDoc(collection(db, "submissions"), {
        uhid: payload.uhid,
        kg_version: payload.kg_version,
        answers: payload.answers,
        submitted_at: payload.submitted_at, // client clock (string)
        created_at: serverTimestamp(),       // trusted server time
        status: "waiting",                   // waiting | seen
      });
    };
    console.info("[firebase-submit] Firebase ready — submissions will be sent to Firestore.");
  } catch (err) {
    console.error("[firebase-submit] Failed to initialise Firebase:", err);
  }
}
