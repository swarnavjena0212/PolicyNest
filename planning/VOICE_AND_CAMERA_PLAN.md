# Voice guide and camera check: plan

**Date:** 16 Nov 2026. **Requested by the user:** "a voice assistant that should guide the applicant along the process", and "a system check for camera, like a web pop-up, to check whether the camera is working or not."

## 1. Voice guide

**Tech choice.** It uses the browser's own speech:

- `speechSynthesis` reads text aloud. It already powers the "Read aloud" buttons.
- `SpeechRecognition` / `webkitSpeechRecognition` takes spoken questions. It already exists for the Copilot microphone.

So there is no new service, no new key and no cost. Answers to spoken questions go through the existing Gemini Copilot, with its guardrails.

**Switch.**

- A **Voice guide** on/off toggle sits in the onboarding bar, next to Ask Copilot.
- It is off by default and remembered per device.
- It is offered once on the "Before you start" checklist: "Turn on the voice guide?"

**What it says.** Short, warm, in the UI language (English `en-IN` or Hindi `hi-IN`). Every line comes from the i18n tables, so text and speech always match. It speaks:

- when a stage or details page opens ("Page 2 of 4: your contact details. Type your mobile number and email.");
- when a document slot opens ("Next, your PAN card. Place it flat in daylight with all four corners visible.");
- after every upload: the instant-feedback result in plain words, using the same text as `fbHtml` / `rejectFeedback`;
- the follow-up items, the decision, the training and exam steps, and the timer's 5-minute note and time's-up message.

**Talk to it.**

- A mic button asks the Copilot by voice. The answer is spoken, and also shown in the drawer.
- Push to talk only: it never listens all the time.
- The mic button is shown only when `SpeechRecognition` exists (Chrome, Edge, Android). Elsewhere the guide speaks and the applicant types.

**Rules.**

- It never speaks ID numbers: it reuses the same scrub as `t()`.
- Speech stops on:
  - Stop;
  - Escape;
  - a page change;
  - opening the file picker;
  - turning the guide off.
- It respects `prefers-reduced-motion` for its animations.
- It never speaks on page load without a user action, because browsers block that.
- **Privacy note in "How we protect your information":** speech recognition may be processed by the browser maker's service (Chrome sends audio to Google). Text-to-speech runs in the browser. Nothing is recorded by PolicyNest.

**Accessible.**

- The toggle uses `aria-pressed`.
- The spoken text is also shown in a visible caption line ("Guide: …") in an `aria-live="polite"` region.
- Screen-reader users can keep the guide off: the captions are what their screen reader reads anyway.

## 2. Camera check (pop-up)

**When it appears.**

- Once, when the applicant presses Start on the onboarding.
- On demand, from a "Check camera" link on the Documents step and on the Selfie card.

**What it does.**

1. A native `<dialog>` titled **Check your camera** asks for camera access with `getUserMedia({video:true})`.
2. It shows a live preview and a status line:
   - **Camera works** (the video plays and frames are not black), shown with a green tick;
   - **Camera blocked**: allow it in the browser's site settings (the address-bar lock icon), with per-browser hints;
   - **No camera found**;
   - **Camera in use by another app**: close the other app;
   - **Not supported or not https**.
3. Buttons: **Retry**, **Continue** and **Upload photos instead** (the applicant can always upload files from the gallery, so a failed check never blocks onboarding).
4. On close it stops every track, so the camera light goes off.

**Rules.**

- **Nothing is recorded, saved or sent.** The preview is shown only in the dialog.
- The result is logged as an applicant-safe event (`CAMERA_CHECK ok|blocked|none|busy`) for the audit trail.
- The voice guide reads the result.

## 3. Build and test

- **Files:** one lane on `PolicyNest.html` (after the reviewer lane), with en/hi strings at parity, and the trust section updated.
- **Tests:** a new `tools/test_voice_camera.py` (Playwright). Chromium's `--use-fake-device-for-media-stream` gives a fake camera; a test with permission denied covers the blocked path; `speechSynthesis` is stubbed to capture what would be spoken. It checks:
  - the guide speaks on step and document changes in English and Hindi;
  - no digits that look like an ID are ever spoken;
  - Stop and Escape work;
  - the camera check covers the ok, denied and no-device paths;
  - the tracks are stopped on close;
  - uploading still works when the camera is denied.
- **Regression:** `check_pack`, `check_prod_build`, `test_model.mjs`, `test_portal_e2e`, `test_reviewer_e2e`, and T1–T10 10/10.
- **Audit:** an end-to-end check, then fixes, then a deploy with the user's OK.
