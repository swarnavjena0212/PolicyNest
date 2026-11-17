# PolicyNest MVP: project context and handoff

This file brings a new Claude session up to speed. It was written on 13 Nov 2026 at the end of the first build session.

## What this project is

PolicyNest is a fictional insurance broker used in the Masters' Union Applied AI Challenge, problem PS-01 "AI-First Onboarding" (problem owner: InsuranceDekho). The task is an AI-first onboarding product for POSP (Point of Sales Person) insurance agents: read the applicant's documents, cross-check them against the rulebook, decide approve / needs more info / reject with reasons, ask for everything missing in one message, and route judgement calls to a human.

The numbered folders `01_…` to `08_…` in this directory are the source package (brief, rulebook v3.2, datasets, specimens, specs). Start with `SOURCE_PACKAGE_INDEX.md`, `01_ORIGINAL_CHALLENGE/PS-01_CHALLENGE_BRIEF.md` and `03_RULEBOOK_AND_REGULATIONS/PS-01_ONBOARDING_RULEBOOK_V3_2.md`.

## What the user asked for (their decisions, keep to them)

- An MVP that works on **one ideal case**: whatever the applicant adds verifies and passes.
- **No API keys** and no calls to government or bank services. PAN, penny-drop, face-match and IRDAI registry results are simulated. **Changed 14 Nov:** one exception, the Onboarding Copilot's Anthropic key. It lives only in the root `.env` (placeholder `your-key-here`, never deployed) and in the `policynest-copilot` Embarko app's env vars. The browser never sees it.
- **Focused onboarding (14 Nov, user's design, inspired by HackerRank and Codeforces):**
  - a first-visit checklist ("Before you start");
  - a 30-minute sitting for details and documents (full screen optional on desktop, never on phones), extendable by 10 minutes at a time, with the timer hideable or switched off;
  - leaving the screen warns and logs `FOCUS_EXIT`, and **never restarts or wipes**;
  - details on 4 progressive pages, documents one at a time;
  - autosave and resume across sittings, only with the applicant's consent;
  - instant feedback after each upload;
  - an **Onboarding Copilot**: rulebook retrieval plus live app state, with Claude reached through the proxy.
  - The 30-minute sitting is a product choice, not a rule. The resume window follows the state machine: 30 days in DRAFT, 14 once rework is open.
- **Real OCR** on uploads, and each slot must accept only its own document type (PAN in PAN, Aadhaar in Aadhaar, and so on).
- UI: **white, light, warm theme** by default. On 14 Nov the user shared a dark finance-dashboard reference ("Ethereal") and chose **both themes with a toggle** (moon/sun button in the top bar, remembered in localStorage), keeping the left sidebar. Borrowed from the reference: gradient hero card, glassy rounded tiles (22 px radius), pill buttons, journey bars, checks donut, avatar ring.
- Readability (14 Nov): darker muted text, document tiles wrap instead of cutting words, journey labels in full (Details … Certificate).
- **Look v3 (14 Nov, from four more references the user sent: Fusion, Smart Home, TaskLab, Calescence):** bento infographics. Light = grey canvas, white 26 px cards, orange/lime/black; dark = near-black, graphite, orange/lime/lavender. Floating sidebar with profile ring. Dashboard: greeting + KPI numbers with chips, progress gauge, orange next-step card, coloured document tiles, hatched journey bars, checks dot map with a dark summary card, lime compliance card, training, agent card. Application: pill stepper, document tiles + 6-segment progress. Evaluation: KPIs, gauge, heat-grid confusion matrix, precision/recall cards, reason bars, state bars. Audit: KPIs, round cards, filterable vertical timeline. **The review console is unchanged on purpose: the user will send separate references for it.** The user's design goal: tell the applicant's progress through visuals; attractive, not dull.
- **Encouraging pop-ups** (`cheer`, `cheerDoc`): every success shows a toast that starts with the applicant's first name (from the PAN once read, else the typed name): details saved, each document verified (batched when several arrive together), all six in, verified, fixes received, each training module, training complete, certified; gentle wording for a follow-up or a failed exam. English only for now. Layout taken from the user's reference screenshots: left sidebar, progress header, dashboard of stage cards, numbered circle stepper, per-document verification cards with an "AI observation", progress ring, compliance checklist.
- The site must be **publicly accessible** to the user's friends.
- **Phase 1–3 decisions (14 Nov, after a skill audit and a grilling round; full list in `planning/PHASE2_SPEC_AND_AUDIT.md` Part D):**
  - **Contrast:** text on orange fills is near-black, not white (white on `#FF6B1A` failed at 2.85:1). The orange itself stays.
  - **Queue:** the reviewer-queue *data* (routed cases, constructed Human-only cases labelled "constructed", KPI figures) may be built now. The queue *screens* still wait for the user's review-console references. Seeded cases show ages labelled **simulated**.
  - **Reviewer keys:** A / M / R (Approve / Ask / Reject), not F1–F3.
  - **Face failures:** two selfie failures below 0.60 in a row send the case to a reviewer with an internal fraud-desk referral (rulebook §4.6); the applicant sees only a neutral message.
  - **Node.js LTS** may be installed (winget) to run `eval/run_eval.js`.
  - **Held-out set:** labelled by the user independently and frozen before the engine sees it.
  - **Usability results:** recorded with participant codes only, in `planning/usability/results.csv`.
  - **Build order:** accessibility fixes → queue data → Node and held-out set → process map → specimen prints and photo bench → deck last.
  - **Go-ahead:** on 15 Nov, passes 1–2 and the original list were built and deployed after the user's OK (accessibility and trust, honest numbers, queue data, the §4.6 selfie rule, discovery questions). Live check passed: engine hash match, T1–T10. Still open: the deploy with the user's OK and a live check, held-out labels, specimen prints, the optional phone-photo bench, the deck, and the console redesign (waits for references).

- **PR #1 (15 Nov, Hardik Nagpal, written with Codex; merged on GitHub, NOT deployed):** it adds accounts and JWT cookies, a SQLite store, reviewer assignment, appeals, telemetry and analytics, a hybrid-retrieval Copilot over a "POSP manual", and an account/workspace UI. It reverses the "no accounts, no server store" decision; the team must confirm the direction. Verified review, defects and deploy blockers: `planning/PR1_CODEX_REVIEW.md`. The live site runs the pre-PR build plus the role switch. Do not redeploy the proxy with the old recipe: it now needs `kb.json`, `rag_index.json`, `posp_manual.json` and a writable `POLICYNEST_DB_PATH`.

**Product spec:** `PRODUCT.md` lists every functional goal with its status (Built / Partial / Not built / Future), the T1–T10 results and the success metrics. Update it when a goal's status changes.

## Where things are

| Path | What it is |
| --- | --- |
| `policynest_mvp/PolicyNest.html` | **The source of the whole app.** One self-contained file: CSS, HTML, vanilla JS. No build tools, no framework. It has no `<html>/<head>/<body>` tags by design. |
| `policynest_mvp/ocr-worker.js`, `pdf-worker.js` | One-line same-origin worker shims that `importScripts` Tesseract.js and pdf.js from a CDN. |
| `policynest_mvp/build_site.py` | Wraps `PolicyNest.html` into `site/index.html` and copies the shims. Run after every source change. |
| `policynest_mvp/site/` | The deployable folder: `index.html`, the two shims, the two Tesseract `.wasm` files, `kb.json` (the Copilot's rulebook passages, built by `build_site.py`), and `embarko.json` (holds the Embarko `appId`). `build_site.py` refuses to build if an API key appears in `site/`. |
| `policynest_mvp/copilot_api/` | The Copilot proxy: `main.py` (Python standard-library server plus the `anthropic` SDK; reads `ANTHROPIC_API_KEY`, `COPILOT_MODEL` (default `claude-opus-5-5`) and `COPILOT_EFFORT` (default `low`)), `requirements.txt` and `embarko.json`. Deployed as its own Embarko server app, **`policynest-copilot`** (https://policynest-copilot.embarko.app). Static sites cannot hold env vars. It has an origin allow-list (in production only the live site; never `null`), a 24 KB body cap, and guardrails in the system prompt.
- **Rate limits (user decision, 14 Nov, G-24 and G-25):**
  - 20 questions per 10 minutes per client;
  - a global cap of 30 per minute (`COPILOT_GLOBAL_PER_MIN`);
  - **50 AI answers per UTC day** (`COPILOT_DAILY_CAP`). After that, the page falls back to rulebook answers and says so.
- **Client address:** the second entry from the right of `X-Forwarded-For` (`XFF_TRUSTED_HOPS=2`, measured on Embarko). `/api/health` reports `daily_left` and `xff_hops`. |
| `.env` / `.env.example` | Local secrets (placeholder only). Never deploy or commit them; `.gitignore` lists `.env`. |
| `tools/check_pack.py` | Static check pack: the engine hash (PINNED), JS syntax, banned phrases and the build. |
| `eval/queue.py`, `eval/kpis.py` | Build the reviewer-queue seed (`eval/out/queue_seed.json`) and the ops KPI figures (`eval/out/kpis.json`), with a source for each figure. |
| `eval/holdout/` | The held-out set procedure (`README.md`) and label templates. The labels are not made yet. |
| `planning/DISCOVERY_QUESTIONS.md` | The discovery questions for the problem owner. All are "not asked". |

## How the app works

- **Views** (sidebar): Dashboard, Application, Review console (the "ops console"), Audit trail. Also Evaluation. Hash routes: `#dashboard`, `#apply`, `#review`, `#evaluation`, `#audit`.
- **Application steps**: Your details → Documents → Verification → Training → Exam → Certificate. All state is one object `S`.
- **Start of the application:** before step 0, `stepIntro` shows the checklist and the save-consent box; `stepResume` shows when the applicant is not in focus mode.
- **Saving:** with consent, `persist()` writes `S` to `localStorage` key `pn-onboarding-v1`. It drops every `url`, `thumb` or `text` key, so images and raw OCR text are never saved. It skips saving while checks run. `restore()` loads the save on boot and deletes it once expired. Reviewer-only material is never saved: the reviewer note (`rnote`), the AI recommendation for a reviewer, and internal events. `restore()` strips internal events that an older build saved.
- **Sitting and focus:** `startSitting`, `tick`, `endSitting`, `enterFocus` and `leaveFocus` run the sitting. `focusUI` drives the `#focusbar`. `visibilitychange` warns the applicant. `fullscreenchange` shows a "Return to full screen" banner, not a warning; closing full screen is not an exit. Touch devices get no full screen and no warnings. The timer can be hidden or its limit turned off. Warnings use a native `<dialog>`, where Escape keeps the sitting going. Both handlers are ignored while the file picker is open (`PICKING`).
- **Instant feedback:** `afterDoc` calls `feedbackFor`, which runs the *unchanged* engine `decide` on the documents read so far and keeps only the checks for that document; `fbHtml` renders them. A refused file gets `rejectFeedback`.
- **Copilot** (`cp*` functions):
  - `kbLoad` fetches `kb.json` and the proxy's `/api/health`;
  - `retrieve` is BM25;
  - `copilotContext` sends first name, step, document statuses and issue codes only, never numbers;
  - `cpNudge` gives proactive suggestions: refused upload, an issue found, 90 s idle, 10 min left, a return after leaving the screen, a resume;
  - `intentAnswer` covers the common questions when the AI is unavailable.
- **OCR and type gate** (`readDoc`, `classify`, `judge`, `addDoc`): images go through Tesseract.js 5.1.0 in the browser; PDFs use their text layer via pdf.js 3.11.174, else the first page is OCR'd. `classify` scores keywords and number patterns to decide PAN / Aadhaar / Class 10 or 12 marksheet / bank document. A wrong or unrecognised file is refused with a reason. The selfie slot refuses documents and text-heavy images and uses the browser `FaceDetector` only where it exists. Raw OCR text is never stored; Aadhaar is masked to the last four digits.
- **Engine block** (between `/* ENGINE:START` and `/* ENGINE:END */` in the source; pure functions, no DOM, so the test-set evaluation can run the same code in Node): `parseDoc` reads name, DOB, PAN, Aadhaar last four, address state, board, result, IFSC, account last four from each document's OCR text (fixes O/0 and I/l/1 noise by position; a field containing unreadable characters counts as not read). `decide` runs about 25 checks with the **PAN card's OCR values as the anchor**; typed form values are only compared (INFO). `nameTier` has the rulebook name tiers (tier 6 = surname change, Human only; tier 7 = different person, NAME_MISMATCH). `followupItems`/`followupText` build the one consolidated message (English and Hindi); `rejectReasons` gives plain-language rejection reasons.
- **Simulated services** (`SIM`, `buildX`): penny-drop returns the holder name read from the cheque; face match 0.91 and IRDAI registry "no record" are fixed, except when a selfie specimen variant sets a score.
- **"Use specimen"** builds synthetic document text from the typed details in the same layout as the PS-01 test set and sends it through the same parser. A **variant** dropdown per slot adds one defect (blurry, DOB off by a year, spelling variant, different person, parent's account, company PAN, other state, unlisted board, Class 10 fail, face score 0.68 or 0.52) so T2–T10 can be triggered without real documents. The variant pickers, the specimen buttons and "Load sample applicant" sit in a closed Demo controls panel, opened with `?demo=1`.
- **Follow-up and re-check** (`finishDecision`, `fixPanel`, `recheck`): NEEDS_MORE_INFO sends one message listing every fix (simulated WhatsApp/SMS). Re-uploads are tracked in `S.changed`; `recheck` re-reads only those documents, re-runs checks that involve them (or everything anchored on the PAN if the PAN changed) and marks the rest "carried over". Each round is in `S.rounds` and shown on the Audit trail page; every event carries its round.
- **Rules added in step 4:** §4.1 any document with OCR confidence below 0.75 is untrusted (REUPLOAD; its values are not used); §4.2 no O/0 correction when the typed PAN equals the card's characters; Step 0 typed-detail rejections (declared education below Class 10, typed PAN format/holder, typed DOB under 18) when the document cannot settle them. The form now asks for highest education.
- REJECT shows plain-language reasons (no fraud wording) and a **Dispute** button, which routes the case to a reviewer (§9).
- **Reviewer routing** (`openReview`, `reviewCard`, `reviewerAct`, step 3): `decisionOf` sends a different-person name (NAME_MISMATCH) straight to HUMAN_REVIEW; other Human-only items (surname change, face 0.60 to 0.75, unlisted board) go there once the applicant's fixes are in. The Review console shows a reviewer task at the top: AI recommendation, evidence side by side (PAN anchor vs the other document; PAN image vs selfie with the score; board vs the recognised list), a note, and Approve / Ask applicant for more info / Reject (dispute: Uphold / Ask / Overturn). Reject needs a note; a confirmed different person logs an internal fraud-desk referral and the applicant gets a neutral message. A person's approval is kept on later re-checks while the evidence is unchanged. Human touches and reviewer time are logged.

## Deployment

**Live since 17 Nov 2026 (commit `8813672`):** the reviewer side, the 30-minute timer, the voice guide, the camera check and the sign-out rate-limit fix.

- **Server env var:** `ADMIN_INVITE_CODE` was added. Its value is in the user's git-ignored `.env`; never print or commit it.
- **Live checks:**
  - a reviewer sign-up is `reviewer_pending`, and pending accounts get 403 on the queue;
  - applicants get 403 on the reviewer and admin routes;
  - a wrong invite code gets 403;
  - sign-out returns 200 and the session is gone;
  - T1–T10 10/10 on the live demo.
- **Status overview:** `PORTAL_STATUS.md`. The independent audit of the voice and camera build is still to do.

**Live since 16 Nov 2026: the production applicant portal (commits up to `eaf7328`).**

- **Sites:**
  - https://policynest.embarko.app is the production build (`site/`, `build_site.py`): landing page, accounts, no demo tools.
  - https://policynest-demo.embarko.app is the demo build (`site-demo/`, `build_site.py --mode demo`): demo controls, `?demo=1`, "View as". Its app id is `cmwiqathn0hfu13bahr07m7bt`.
- **Proxy:** `policynest-copilot` runs the new backend (accounts, saving, notifications, the Gemini assistant). Package it with `python tools/package_proxy.py <out.tar.gz>`.
  - **Env vars on Embarko:** `GEMINI_API_KEY`, `JWT_SECRET` (random, set by Claude, never printed), `COPILOT_ORIGINS` (both sites), `RAG_SEMANTIC=0`, `GEMINI_MODEL=gemini-3.5-flash`, `GEMINI_FALLBACK_MODEL=gemini-3.5-flash-lite`.
  - **Storage:** SQLite in Embarko's `DATA_DIR`, which is **not backed up**.
  - **No SMTP:** password-recovery emails are not sent.
- **Live check on 16 Nov:**
  - `/api/health` reports `llm:true` (gemini); `/api/ready` passes.
  - CORS allows only the two sites.
  - T1–T10 10/10 on the live demo.
  - The live assistant answers grounded and cited, in English and Hindi, and refuses ID numbers and outcome predictions.
  - Production redirects `#apply` to `#welcome` and shows no demo controls.
- **Report:** `planning/PORTAL_VERIFICATION.md`. Open: minor findings m1 to m9 and the review-console redesign.


- **Live site: https://policynest.embarko.app** (Embarko static hosting, app name `policynest`, claimed and permanent, owned by the user's Embarko account).
- The deploy token is stored on this machine at `~/.embarko/credentials` (`C:\Users\swarn\.embarko\credentials`). Never print it or commit it.
- Claude can also redeploy through the Embarko connector in claude.ai (`deploy_app`, app name `policynest`) when it is signed in.
- To redeploy after a change (docs: https://embarko.ai/docs/quickstart.md):

```bash
python policynest_mvp/build_site.py
tar -czf /tmp/app.tar.gz -C policynest_mvp/site .
curl -sS -X POST 'https://ship.embarko.ai/apps' -H 'X-App-Name: policynest' -H 'X-Agent-Name: claude' --oauth2-bearer "$(cat ~/.embarko/credentials)" -F 'source=@/tmp/app.tar.gz'
```

- **Embarko's Content Security Policy matters**: it blocks `blob:` images and `blob:` workers. That is why thumbnails are `data:` URLs and why, on http(s), workers load from the same-origin shim files (`WEB` flag in the source). Opened as a local `file://` page, the libraries' default blob workers are used instead. The CSP also blocks `data:` fetches, so on http(s) Tesseract's `corePath` points at the small `tesseract-core-(simd-)lstm.js` build (SIMD detected by `wasmSimd`), which loads its `.wasm` from the worker's folder; `build_site.py` copies the two `.wasm` files from `policynest_mvp/vendor/` into `site/` (14 Nov). Without this the default core tries an embedded `data:` wasm first and logs CSP errors before falling back.
- **Copilot proxy:** to redeploy, run `build_site.py` first (it writes `copilot_api/kb.json`), then tar `policynest_mvp/copilot_api` excluding `.env*`, `__pycache__` and `test_main.py`, and POST it the same way with `X-App-Name: policynest-copilot`.
  - **Since 15 Nov (PRD pass 3, ADR 0002):** the proxy runs its own BM25 over `kb.json` and ignores any `passages` the browser sends. It also checks `context` against a fixed schema (unknown fields dropped), refuses ID numbers with `400 id_number` before any model call, and replaces answers that contain fraud wording or "you are approved/rejected". Haiku models are called without the fallback beta. The page falls back to rulebook answers on any non-200 reply.
  - **Corpus:** `build_site.py` removes the sentences and table rows that carry fraud-desk wording, ships only applicant-safe passages (`audience: "applicant"`) to both `site/kb.json` and `copilot_api/kb.json`, and refuses to build if any such word is left.
  - **Tests:** `python policynest_mvp/copilot_api/test_main.py` runs 14 contract tests with the model stubbed; no key or SDK is needed.
  - **To turn on AI answers,** the user adds `ANTHROPIC_API_KEY` in the app's App setup tab at https://embarko.ai/app. Or set it over HTTP: PUT `/api/apps/policynest-copilot/env-vars` with `apply: true`.
  - **Until then,** `/api/health` reports `llm:false` and the Copilot answers from the rulebook and app state only.
- A private claude.ai artifact copy and a one-page status note (a Claude doc) exist on the **previous** Claude account; a different account cannot open them. Their content is summarised below.

## Status against the brief (as of 15 Nov 2026)

**15 Nov (built and live (15 Nov)):** passes 1–2 and the original list: accessibility, the softer focus mode, the empty form and Demo controls, honest numbers, queue data, the §4.6 two-selfie rule, the held-out procedure and the discovery questions. The master audit's 1 blocker and 3 major findings are fixed in `82aacd4`; F-10 and F-11 are open. Details: `BUILD_LOG.md` section 3b.

**Works:** reviewer routing for Human-only cases and disputes (step 3, 14 Nov); clean applicant journey end to end to CERTIFIED; cross-checks on OCR-read values (step 1, 13 Nov); one consolidated follow-up message in English/Hindi with partial re-check and rounds in the audit trail (step 2, 13 Nov); plain-language REJECT; on-device OCR with per-slot type checks; rule citations; privacy masking; public URL. Tested in headless Chrome with Tesseract served from npm (the CDN is blocked in Claude's workspace).

**Test scenarios (via specimen variants):** T1 to T10 all behave as the brief expects (T4 and T8 route to the reviewer task; T4 is never auto-rejected).

**Not built / not done:**
1. ~~Cross-checks use typed details~~ done (step 1).
2. ~~Needs-more-info path~~ done (step 2).
3. ~~Reviewer routing~~ done (step 3). The review console holds one application, not a multi-case queue.
4. ~~Reject path~~ done (plain-language reasons; disputes are built in step 3; the reject panel also offers Start a new application and Delete my saved progress).
5. ~~Evaluation~~ done (step 4, 14 Nov): `eval/run_eval.js` runs the engine block from PolicyNest.html on all 600; results in `eval/out/`, report in `eval/EVALUATION_REPORT.md`, and an **Evaluation** page in the app (`#evaluation`, data embedded by `eval/embed.py`). First run 98.0%; after three rulebook-based refinements 100.0% (in-sample; say so). Fairness by gender/state/name pattern included. Before/after simulation added (`eval/simulate.py` → `eval/out/simulation.json`, shown on the Evaluation page): median 8.4 → 7.4 days with the engine, 6.1 with the training link at KYC; +106 agents if silence fell to 10%; reviewer minutes estimate 0.38. **Still not done:** applicant clarity test with five users; real reviewer minutes; OCR on real phone photos.
6. OCR is tested only on clean generated images, never on real phone photos or the files in `05_SPECIMEN_DOCUMENTS/sample_specimens` (those may be real people's documents, so they were deliberately not opened or published).
7. No notes from the "discovery call" were ever provided; the status note's section on it is a placeholder.

## Regression runner

`python tools/t1_t10.py [base_url]` (default `http://localhost:8000`; start the server yourself, e.g. `.claude/launch.json` "site") drives T1 to T10 through the page and exits 0 only if all pass. Run it against the live URL after a deploy. A pure-white synthetic canvas is held by the photo check: in tests, use "Use this photo anyway" or a specimen.

## How to test

Open the page with `?demo=1` (`.claude/launch.json` "site" serves `policynest_mvp/site` on port 8000; or use the live URL with `?demo=1` and a `?v=N` cache-buster) and drive it with JavaScript: go to `#apply`, tick `#f-save` if you want autosave, click `[data-act="begin-nofs"]` (or `begin`), click `[data-act="sample"]` inside the Demo controls panel to fill the sample applicant, click `[data-act="details-next"]` four times (one per details page), then either `[data-act="spec-all"]` or set a `File` on `#up-PAN` etc. through a `DataTransfer` and dispatch a `change` event. A canvas with typed text ("INCOME TAX DEPARTMENT", a PAN-format number) makes a good synthetic PAN. Then `[data-act="submit"]`, wait about 8 seconds, and continue through training and exam (pass mark 50%). `S` is inside an IIFE and is not on `window`.
- **Tiles:** the document tiles are `[data-dk="i"]` buttons, and only the open document's card is in the DOM.
- **Leaving the screen:** simulate it by overriding `document.hidden` and dispatching `visibilitychange`.
- **Timer:** test it by overriding `Date.now`.
- **Engine check:** the engine block's SHA-256 must match the live site's, or the evaluation must be re-run. Since the 15 Nov deploy the live site runs `c2063887…`, the same as the source and the evaluation. The current hash is `c2063887…` (`tools/check_pack.py` PINNED). Redeploy before any live T1–T10 check.

## Working preferences observed

- The user wants working links quickly and judges by what they see in the browser.
- Be plain about what is simulated versus real; the app's own copy labels simulated results.
- Do not invent numbers (no fake analytics charts, no accuracy claims without running the test set).
