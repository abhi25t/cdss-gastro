/* Copy this file to `firebase-config.js` and paste your own values from the
 * Firebase console: Project settings (gear icon) → "Your apps" → Web app → SDK
 * setup and configuration → Config.
 *
 * NOTE: these values are NOT secret — they are meant to live in the browser.
 * Security comes from the Firestore rules (see SETUP_FIREBASE.md), not from
 * hiding this config. The real firebase-config.js is git-ignored just to keep
 * project-specific values out of the repo.
 */
window.FIREBASE_CONFIG = {
  apiKey: "PASTE_API_KEY",
  authDomain: "PASTE_PROJECT_ID.firebaseapp.com",
  projectId: "PASTE_PROJECT_ID",
  storageBucket: "PASTE_PROJECT_ID.appspot.com",
  messagingSenderId: "PASTE_SENDER_ID",
  appId: "PASTE_APP_ID",
};
