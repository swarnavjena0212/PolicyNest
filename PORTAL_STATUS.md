# PolicyNest portal: status

**As of:** 16 Nov 2026, afternoon. PolicyNest is a fictional broker, built for the Masters' Union Applied AI Challenge, PS-01 "AI-First Onboarding" (problem owner InsuranceDekho).

## Links

| Site | URL | What it is |
|---|---|---|
| Production portal | https://policynest.embarko.app | The applicant portal: landing page, accounts, no demo tools |
| Demo site | https://policynest-demo.embarko.app/?demo=1 | The same app plus demo controls (sample applicant, specimen documents, T1–T10 variants, "View as" role switch) |
| AI and account server | https://policynest-copilot.embarko.app/api/health | Backend health (Gemini on, daily AI cap) |
| Code | https://github.com/swarnavjena0212/PolicyNest | Source repository |

## 1. Live now

### Landing page (before sign-in)

- Only three actions: **Sign in**, **Create account** and **Account recovery**.
- Basic details: what PolicyNest is, how it works (six stages), what you need (six documents), and how we protect your information.
- English or Hindi, light or dark theme.

### Accounts

- **Sign-up:** creating an account does not sign you in. It opens the Sign in panel with your email filled in.
- **Sign in as Applicant / Reviewer:** the choice must match the account's real role.
- **Sign-out** is in the profile menu. Fixed on 16 Nov: it was blocked by a shared rate limit.
- Secure sessions use HttpOnly cookies with refresh rotation.

### Applicant dashboard

- "Ready to start", one clear next step, and the **Start my application** button.
- Application reference (PN-YYYY-NNNNN), status, last saved time, journey progress.
- Document tiles, updates, and details on demand.

### Six-stage application

1. **Your details:** four short pages that save as you type.
2. **Documents:** six uploads, one at a time.
   - Real OCR on the device.
   - Each slot accepts only its own document type.
   - Instant feedback after each upload.
3. **Verification:** about 25 rulebook checks, anchored on the PAN card.
   - Outcome: approve, needs more info, or not accepted.
   - Plain-language reasons with rule citations.
   - One follow-up message listing every fix, in English or Hindi.
4. **Training:** 15 hours in five modules.
5. **Exam:** pass mark 50%, retakes allowed.
6. **Certificate:** issued on passing.

### Saving and updates

- **Automatic server saving:**
  - save states: Saving, Saved, Failed with Retry;
  - protection when two devices save at once;
  - resume on any device at the exact point.
- **Notification centre, application timeline and profile page,** including consents.
- **What the server stores:** details and progress only. **Never** photos, OCR text, or full PAN, Aadhaar or account numbers (last four digits only).

### AI assistant (Gemini, the USP)

- **Knows where the applicant is:** stage, documents, issues and next step.
- **How it answers:** short numbered steps in English or Hindi, with rulebook sources.
- **Guardrails:** it never predicts approval, never asks for or repeats ID numbers, refuses off-topic requests and resists prompt injection.
- **Fallback:** rulebook answers when the AI is busy or the daily cap of 50 AI answers is reached.
- **Score:** 45/45 on the assistant test set with the live model.

## 2. Built and tested, not live yet

These wait for one deploy with the owner's OK.

### 30-minute timer (applicant onboarding)

- The timer shows in the onboarding bar and can be hidden.
- A gentle note appears at 5 minutes left.
- At zero: **Take 10 more minutes** or **Save and exit**. Progress is never wiped.
- No full screen and no leave-screen warnings.

### Reviewer side

- **Reviewer sign-up:** the account waits for **admin approval** and has no powers until then.
- **Admin:**
  - a **Reviewer requests** screen to approve or deny;
  - the first admin account is created with a private invite code.
- **Review console:**
  - tabs: **Needs review / Appeals / All / Decided**, with search;
  - each case shows:
    - the engine's reasons;
    - the six document statuses;
    - the typed details (last four digits only);
    - the timeline and review history.
- **Actions:** **Approve / Ask for more info / Reject** (keys A / M / R).
  - Each sends a message to the applicant.
  - An optional internal note stays private.
- **Appeals:** **Uphold / Overturn / Ask for more info**.
- **The server enforces reviewer decisions.** A rejection can't be bypassed; only an appeal overturn lifts it.
- **What reviewers see:** check results, not document photos. The photos never leave the applicant's device.

### Applicant side of reviews

- Reviewer decisions show on the dashboard:
  - **Approved:** training unlocks.
  - **More info:** the fixes are listed.
  - **Rejected:** the reason is shown, with **Dispute**.
- A dispute creates an appeal, and the outcome arrives as a notification.

## 3. Voice guide and camera check (built 17 Nov, not live yet)

- **Voice guide:**
  - a **Voice guide** button in the onboarding bar;
  - speaks each page, document and upload result in English or Hindi, with a "Guide: …" caption;
  - a push-to-talk mic to ask the assistant (Chrome, Edge and Android);
  - never speaks ID numbers;
  - uses the browser's own speech, so it's free.
- **Camera check pop-up:**
  - a live preview when onboarding starts, and on demand from the Documents and Selfie steps;
  - shows works, blocked (with how to allow it), no camera, in use, or not supported;
  - **Upload photos instead** keeps onboarding going;
  - nothing is recorded or sent, and the camera turns off when the pop-up closes.
- **Not done:** the independent audit of this build did not run (the agents hit the weekly usage limit). It is covered by its own 70-check test and the full regression only.

## 4. Test results (latest)

| Suite | Result |
|---|---|
| Backend unit tests | all pass (139) |
| Portal end-to-end | 67/67 |
| Reviewer end-to-end | 80/80 |
| Voice guide and camera check | 70/70 |
| T1–T10 brief scenarios | 10/10 (live demo and local) |
| AI assistant evaluation (live Gemini) | 45/45 |
| Engine evaluation (600 cases) | 100% in-sample (first run 98.0%) |
| Engine integrity hash | Unchanged (`c2063887…`) |

## 5. Simulated or not real yet

- **Simulated, and labelled as such:** the PAN holder check, bank penny-drop, face match and IRDAI registry.
- **WhatsApp/SMS** follow-up messages are simulated.
- **Password-recovery emails** are not sent (no email service is connected).
- **The database is not backed up** (Embarko storage).
- **The engine's own decision** is computed in the applicant's browser. Reviewer decisions are server-side and authoritative.
- **Browser voices** can sound robotic, especially in Hindi. Speaking questions works in Chrome, Edge and Android only.

## 6. Next

1. Audit the voice guide and camera check (when the usage limit resets).
2. Deploy everything with the owner's OK: both sites, the server, and the admin invite code.
3. Open items:
   - minor polish findings in `planning/PORTAL_VERIFICATION.md` and `planning/REVIEWER_VERIFICATION.md`;
   - a usability test with five applicants;
   - OCR on real phone photos.

## Detailed reports

- `planning/PORTAL_VERIFICATION.md`: production portal verification.
- `planning/REVIEWER_API.md`, `planning/REVIEWER_VERIFICATION.md`: the reviewer side.
- `planning/VOICE_AND_CAMERA_PLAN.md`: the voice guide and camera check.
- `PROJECT_SUMMARY.md`, `PRODUCT.md`: the overall project and the goal status.
