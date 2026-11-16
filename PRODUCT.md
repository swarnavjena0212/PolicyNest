# PolicyNest Onboarding: product spec

AI-first onboarding for POSP (Point of Sales Person) insurance agents. Masters' Union Applied AI Challenge, problem PS-01, set by InsuranceDekho. PolicyNest Insurance Brokers is fictional and all data is synthetic.

- **Live:** https://policynest.embarko.app
- **Written:** 14 Nov 2026 (updated the same day after review: straight-through definition, first-run figures, exam pass mark, priorities, challenge deliverables; then after the Phase 1–3 audit and grilling: A10, R8, R9 and the agreed approach in §8; 15 Nov: passes 1–2, the §4.6 rule, queue data and discovery questions built and live (15 Nov))
- **Sources:** `01_ORIGINAL_CHALLENGE/PS-01_CHALLENGE_BRIEF.md`, `03_RULEBOOK_AND_REGULATIONS/PS-01_ONBOARDING_RULEBOOK_V3_2.md`, `02_BUSINESS_REQUIREMENTS/05_DELIVERABLES_AND_EVALUATION_CRITERIA.md`, `eval/EVALUATION_REPORT.md`
- **How the code works:** see `CLAUDE.md`. This file says what the product does and why.

Status key: **Built** (on the live site), **Partial**, **Not built**, **Future** (not committed).

---

## 1. Problem

PolicyNest onboards about 330 would-be agents a month. Between January and September 2026, 3,000 started and only 57.7% were certified. Today:

- a certified agent takes a median of 8.4 days;
- a file waits a median of 17.3 hours before a reviewer opens it;
- a reviewer spends about 21 minutes per file;
- a reviewer asks for one fix at a time, and each round trip adds about two days.

25.8% of files are returned at least once, and 27% of those applicants never reply. The steps are not the bottleneck: the best quarter of applicants finish in 6.6 days. The waiting, the manual reading and the one-at-a-time follow-ups are.

**Product goal:** AI reads and cross-checks every document, decides what the rulebook lets it decide, asks for everything missing in one message, and leaves people only the judgement calls.

## 2. Scope principle

This is a **demo-grade MVP built around one ideal case**. Whatever a clean applicant uploads verifies, and the journey runs to a certificate.

Every defect scenario (T2 to T10) can be triggered on demand through specimen variants, without real documents. This is a deliberate scope choice:

- **Synthetic data only.** No real people's documents are used, opened or published.
- **External services are simulated.** PAN, penny-drop, face match and the IRDAI registry use data-pack values or fixed values, and the app labels them as simulated. No API keys are used and no government or bank systems are called.
- **OCR is real.** It runs on the user's device, and nothing is uploaded to a server.
- **State is kept only with consent.** With the save box ticked, progress is saved on this device (never images or full document text; Aadhaar last four only) and resumes; without it, a page refresh starts a new application.

## 3. Users

| Priority | User | Needs | Served by |
|---|---|---|---|
| Primary | **Applicant** (new POSP, on a phone) | Get certified fast, know exactly what is wrong, fix it once | Dashboard, Application journey, pop-ups |
| Secondary | **Onboarding reviewer** | Spend time only on real judgement calls, with the evidence laid out | Review console |
| Tertiary | **Ops team lead** | Where files stall, how decisions split | Evaluation page (simulation, decision mix) |
| Tertiary | **Compliance officer** | No ineligible person certified, every decision explainable | Audit trail, Evaluation page (accuracy, fairness) |

## 4. Functional goals

### 4.1 Applicant journey

| # | Goal | Status |
|---|---|---|
| A1 | Six-step journey: Your details → Documents → Verification → Training → Exam → Certificate | Built |
| A2 | Typed details: name, DOB, PAN, Aadhaar last 4, mobile, email, highest education, state, city, bank account, IFSC, consent | Built |
| A3 | Six document slots: PAN, Aadhaar front, Aadhaar back, Class 10 marksheet, cheque or bank statement, selfie | Built |
| A4 | Each slot accepts only its own document type; a wrong or unreadable file is refused with the reason | Built |
| A5 | A status the applicant can see at every step: stepper, progress gauge, per-document cards with an "AI observation" | Built |
| A6 | Encouraging pop-ups that use the applicant's first name on every success; gentle wording on a follow-up or a failed exam | Built (English only); progress counts fixed 15 Nov, built and live (15 Nov) |
| A7 | Training (15 h logged across five modules) and a short exam, as placeholders. **Pass mark changed 40% → 50% on 14 Nov.** The sources conflict: the data dictionary (`06_DATA_DICTIONARY`, "≥ 50%"), the regulatory matrix ("e.g. 50%") and the state machine say 50%, while the data pack's exam-failure events say "Score below 40%". The rulebook gives no pass mark. | Built (course content and exam are out of scope per the brief) |
| A11 | First-visit checklist: the six documents, what is accepted, what gets a document refused (rulebook §3–4), the steps after documents, and consent to save on this device (box unticked by default). It also says the sitting covers details and documents only, gives the language, and warns that a shared device is not private | Built (14 Nov); checklist lines added 15 Nov, built and live (15 Nov) |
| A12 | Focused 30-minute sitting for details and documents: optional full screen ("Start without full screen"), countdown that can be hidden or turned off, warnings at 10 and 2 min, extend by 10 min (WCAG 2.2.1). Leaving warns and logs `FOCUS_EXIT` with no duration, never resets; the file picker is ignored. Closing full screen shows a banner and is not an exit. Touch devices get no full screen and no warnings. | Built (14 Nov); softer focus mode built and live 15 Nov. Tested in desktop Chromium; not tested on iOS Safari, where the full-screen request falls back to a full-viewport layout. **Changed 16 Nov (user decision, RV-FE):** a visible, gentle 30-minute timer ("28:41 left", role=timer, Hide/Show), one 5-minute announcement, and at zero a "Time's up" dialog: Take 10 more minutes (Escape does the same) or Save and exit to the dashboard. No full screen, no leave-screen warnings; progress is never wiped; the timer waits while the file picker is open, survives a reload within the sitting and stops at submission |
| A13 | Progressive pages (details over 4 pages, one document at a time) with autosave and resume across sittings. Only with consent. Never stores images or raw OCR text. Expires after 30 days (14 days once a follow-up is open). | Built (14 Nov; `localStorage`, not IndexedDB) |
| A14 | Instant feedback after every upload: what was read, what is fine, what to fix with numbered steps, and what goes to a person. Uses the unchanged engine. | Built (14 Nov) |
| A15 | Onboarding Copilot: proactive next-step suggestions plus Q&A. It uses BM25 retrieval over the rulebook and reference docs (55 passages, `kb.json`) and the live app state (first name and statuses only). Claude answers through the `policynest-copilot` proxy, which holds the key server-side. Guardrails: never decides, no fraud wording, refuses full ID numbers. | Partial: the proxy is deployed but **has no API key yet**, so answers come from the rulebook and app state only. No POSP manual exists in the source package (`MISSING_SOURCE_MATERIALS.md`), so the corpus is the rulebook and specs. |
| A8 | Certificate with POSP code, masked PAN, time taken, human touches and follow-ups | Built (marked as a demonstration certificate). Check the certificate fields, including time taken, at the live check (not seen in the master audit) |
| A9 | Light and dark themes with a toggle | Built |
| A10 | Accessibility: visible focus on upload buttons, text contrast ≥ 4.5:1 (near-black text on orange fills), targets ≥ 24 px, heading order, skip link, a narrow live region, a compact top bar on phones, links for navigation, a leave warning once data is entered | Built and live 15 Nov (H1). The 14 Nov audit's issues are in `planning/PHASE1_AUDIT_AND_PLAN.md` |
| A16 | Empty form on first visit: nothing pre-filled, consent unticked. Specimens, variant pickers and the sample applicant sit in a closed "Demo controls" panel, opened with `?demo=1` | Built and live 15 Nov (H3) |

### 4.2 Document reading (brief: Must have)

| # | Goal | Status |
|---|---|---|
| D1 | OCR on images (Tesseract.js) and PDFs (text layer, else OCR of the first page), in the browser | Built |
| D2 | Extract name, DOB, PAN, Aadhaar last 4, address state, board, result, IFSC and account last 4, each with a confidence | Built |
| D3 | Correct O/0 and I/1 noise by position, unless the typed PAN matches the card's characters as printed (§4.2) | Built |
| D4 | A document with OCR confidence below 0.75 is untrusted: re-upload, and its values are not used (§4.1) | Built |
| D5 | Raw OCR text is never stored; Aadhaar is masked to the last 4 everywhere | Built |
| D6 | OCR on real phone photos | **Not verified.** Tested only on clean generated images. |

### 4.3 Cross-checks and decision (brief: Must have)

| # | Goal | Status |
|---|---|---|
| C1 | The PAN card's OCR values are the anchor; typed values are compared and reported as information only | Built |
| C2 | Name matching under the rulebook tiers: exact, initials, swapped order, missing or added middle name, spelling variant, surname change (Human only), different person | Built |
| C3 | DOB agrees across documents; PAN format and holder type (4th character P); minimum age; Class 10 pass from a recognised board; bank-account ownership (penny-drop name); face-match thresholds; IRDAI registry | Built (external values simulated) |
| C4 | One application-level decision, APPROVE, NEEDS_MORE_INFO or REJECT, with every reason listed and the rule it applies cited | Built |
| C5 | When unsure, prefer NEEDS_MORE_INFO over REJECT | Built |
| C6 | REJECT reasons in plain language, never mentioning fraud, with a Dispute button that routes the case to a reviewer | Built |

### 4.4 Follow-up and re-check

| # | Goal | Status |
|---|---|---|
| F1 | One consolidated message listing every fix, in English or Hindi, sent by simulated WhatsApp/SMS | Built |
| F2 | Re-upload re-reads only the changed documents and re-runs only the checks that involve them; everything else is "carried over" (re-checking everything if the PAN changed) | Built |
| F3 | Each round is recorded and shown on the Audit trail | Built |

### 4.5 Human routing and the reviewer (brief: Must have / Should have)

| # | Goal | Status |
|---|---|---|
| R1 | A different person's name (NAME_MISMATCH) goes straight to a person; never auto-rejected | Built |
| R2 | Other Human-only items (surname change, face match 0.60 to 0.75, unlisted board) go to a person once the applicant's fixes are in | Built |
| R3 | Reviewer task: AI recommendation and evidence side by side (PAN vs the other document; PAN image vs selfie with the score; board vs the recognised list), a note, and Approve / Ask for more info / Reject (disputes: Uphold / Ask / Overturn) | Built |
| R4 | Reject needs a note; a confirmed different person is referred internally to the fraud desk while the applicant gets a neutral message | Built |
| R5 | A reviewer's approval is kept on later re-checks while the evidence is unchanged; human touches and reviewer time are logged | Built |
| R6 | **Reviewer dashboard with a multi-case queue**, showing the AI's decision and where it was unsure. | **Built 16 Nov (RV-FE, not deployed).** Signed-in reviewers and admins get a server queue (Needs review, Appeals, All, Decided; search), case detail (engine decision and issues, six document statuses, allow-listed details with last 4 only, timeline, appeals, review history) and Approve / Ask for more info / Reject with A / M / R and a confirm dialog. Appeals: Uphold / Overturn / Ask. Reviewers sign up themselves; an admin approves the request (`#requests`). The demo build keeps the one-application reviewer task when no staff account is signed in. Test: `tools/test_reviewer_e2e.py`. |
| R7 | Review console redesign to the new look | **Built 16 Nov** in the bento look (KPI tiles, queue rows, case bento) without the user's separate references; adjust when they arrive. |
| R8 | Rulebook §4.6: two selfie failures below 0.60 in a row go to a reviewer, with an internal fraud-desk referral; the applicant sees only the neutral "a person is checking" message | Built and live 15 Nov (`96722e8`, `b581609`), outside the engine (decided 14 Nov, Phase 3 Q6/Q18) |
| R9 | Reviewer keys A / M / R (Approve / Ask / Reject) when focus is not in a text field, instead of the cockpit spec's F1–F3 (F1 is browser help on Windows) | Not built (decided 14 Nov, Q5). Built with the queue screens |

### 4.6 Audit, evaluation, simulation (brief: Must have / Should have)

| # | Goal | Status |
|---|---|---|
| E1 | Audit trail: every decision, the rule behind it, the round, the engine version; filterable timeline; engine hash on each decision event; internal events marked and never saved | Built. Hash and internal marking added 15 Nov, built and live (15 Nov) |
| E2 | Evaluation on the 600-application test set, using the same engine code the site runs: accuracy, precision and recall for REJECT and NEEDS_MORE_INFO, confusion matrix | Built (`eval/`, Evaluation page) |
| E3 | Fairness check by gender, state and name pattern | Built |
| E4 | Before/after simulation of time-to-certificate and drop-off from the applications workbook | Built (directional, not a forecast) |
| E5 | Public URL usable by an applicant and a reviewer | Built |

### 4.6b Added 15 Nov from the external feature triage (`planning/FEATURE_TRIAGE.md`)

Built, checked in the browser, and live since 15 Nov (Embarko deployment `cmwhpsgz202ib13bal6lwopu8`; live engine hash matches; `tools/t1_t10.py` 10/10 on the live URL).

| # | Feature | Status |
|---|---|---|
| C1 | Photo check before OCR (blurry, glare, too dark, too small) with "Retake photo" or "Use this photo anyway"; heuristic, OCR confidence still decides | Live |
| C2 | Phone photos turned upright and downscaled to 1920 px before OCR | Live |
| C3 | "Take photo" (rear camera) and "Choose file" per slot; "Take selfie" opens the front camera; selfie accepts images only | Live |
| C4 | The same file in two slots is refused (SHA-256 of the file, this application only) | Live |
| C5 | Version history per document (v1, v2 with round and file hash; no images kept) | Live |
| C6 | Read aloud in Hindi or English (on-device speech; hidden where unsupported) | Live |
| C7 | Follow-up shown as a phone-style message: Copy message, Open in WhatsApp (no number pre-filled), SMS version of 160 characters or fewer; sending stays simulated | Live |
| C8 | Offline IFSC helper: format check and bank name from a bundled table; nothing is looked up online | Live |
| C9 | Scenario launcher in Demo controls: pick T1 to T10 and the variants are set | Live |
| C10 | Certificate: Print or save as PDF, and an integrity seal with "Verify this certificate" (proves the fields were not edited on this device, not that IRDAI issued it) | Live |
| C11 | "Rules applied" table on the Audit trail (every check with its rulebook section) | Live |
| C12 | `tools/t1_t10.py`: Playwright runner for T1 to T10, local or live | Live (10/10 on the live site) |
| C13 | "Good photo" hints before a slot's first upload | Live |
| R10 | Demo role switch: "View as" Applicant, Reviewer, Ops and compliance, or All views (default Applicant; All with `?demo=1`). Each role sees only its views, and a hidden view explains itself and offers a switch. Labelled "not a login"; this is not authentication | Live 15 Nov (`2365af4`, deployment `cmwhvzhjs050t13bapo81uw75`); checked live: each role lands on its first view, hidden views show the notice |

### 4.7 Stretch (future, not committed)

| # | Goal | Status |
|---|---|---|
| S1 | Conversational onboarding on WhatsApp | Future |
| S2 | Pull verified documents from DigiLocker, falling back to photos | Future |
| S3 | Second journey (e.g. loan customer) by swapping the rulebook | Future |
| S4 | Hindi pop-ups and a Hindi interface (follow-up messages already have Hindi) | Future |

## 5. Acceptance scenarios (brief Table 6)

All are triggered through specimen variants on the live site.

| # | Scenario | Expected | Status |
|---|---|---|---|
| T1 | Clean application | Approve in one pass, no human touch, every check recorded | Pass |
| T2 | Initials on marksheet | Match; approve without asking | Pass |
| T3 | MOHAMMED vs MOHAMMAD | NEEDS_MORE_INFO, NAME_SPELLING_VARIANT | Pass |
| T4 | Different person on Aadhaar and marksheet | Recommend REJECT, route to a person, never auto-reject | Pass |
| T5 | Blurry marksheet and parent's cheque | Both fixes in one message | Pass |
| T6 | PAN 4th character C | REJECT, PAN_NOT_INDIVIDUAL, in plain words | Pass |
| T7 | DOB off by a year | NEEDS_MORE_INFO, DOB_MISMATCH | Pass |
| T8 | Face match 0.68 | Route to a person | Pass |
| T9 | Different Aadhaar state | Information only; approve | Pass |
| T10 | Re-upload after NEEDS_MORE_INFO | Re-check only what changed; both rounds in the audit trail | Pass |

## 6. Success metrics (brief Table 7)

Measured values come only from `eval/EVALUATION_REPORT.md`; nothing here is estimated by hand.

**Straight-through** follows the brief's glossary: a case handled from start to finish with no human touch. Automatic follow-ups and automatic rejections therefore count; a case routed to a person does not. The stricter "approved first time, no follow-up" share is reported on its own row.

**First contact comes first, then the refined figure.** The first run on the 600 applications (engine 3.3.0) scored 98.0% accuracy, but REJECT recall was 93.3%, below the 98% target, and 3 applications were wrongly approved. The rules were then refined to follow the rulebook, and the current engine (3.4.0) scores 100.0%. Both columns are **in-sample**: the same 600 applications were used each time, and only a fresh labelled set can confirm them. An independent re-derivation of the rules from the rulebook (the autoresearch run, `AUTORESEARCH_REPORT.md`) also reached 600 of 600, but its author had already seen the data, so it is a cross-check, not a held-out test.

**Not evaluable on the test set.** The 600 applications contain no selfie scoring between 0.60 and 0.75, no surname change and no marksheet from an unlisted board. Those three Human-only rules (R2) are therefore covered only by constructed cases such as T8, never by the test set. The rulebook also supplies no list of recognised boards.

| Metric | Target | First run | Now | How measured |
|---|---|---|---|---|
| Decision accuracy | ≥ 95% | 98.0% | 100.0% | In-sample, 600 apps |
| REJECT recall | ≥ 98% | 93.3% | 100.0% | In-sample |
| Wrongful approvals | 0 | 3 | 0 | In-sample |
| Straight-through (no human touch, and correct) | ≥ 60% | not recorded | 98.2% (589 of 600; 11 go to a person) | In-sample |
| Approved first time, no follow-up (stricter) | none set | not recorded | 60.7% (364 of 600) | In-sample |
| Follow-up rounds per returned application | 1 | not recorded | 1.0 | In-sample. In 6 of 176 cases a blurry document hides a second problem. |
| Time to decision (straight-through) | < 10 min | n/a | under 1 s, measured to the decision (this demo, on this device); engine 0.04 ms on pre-read text | Observed and measured |
| Reviewer minutes per application | < 5 | n/a | 0.38 | **Simulated estimate**, not real reviewers |
| Applicant clarity | ≥ 4 / 5 | n/a | Not measured | Needs five real users |
| Median days to certificate | (baseline 8.4) | n/a | 7.4; 6.1 with the training link sent at KYC | **Simulation**, directional |

## 7. Guardrails

- DPDP Act 2023: collect only what onboarding needs and state the purpose. No full Aadhaar number in any model, log, prompt or screen.
- The AI recommends; a person confirms every NAME_MISMATCH rejection and every Human-only decision.
- Every decision cites its rule. A decision that cannot be explained is not made.
- Messages to applicants never mention fraud or show another person's data.
- Simulated results are labelled as simulated in the app. Numbers are never invented.

## 8. Known gaps, in priority order

1. **Usability tests: five applicants and two reviewers** (brief Stage 3). They need no code, so they run in parallel with item 2, and they produce the clarity score, the only target with no number. The plan is written (`USABILITY_TEST_PLAN.md`); no session has been run. The synthetic image pack it needs exists (`usability/image_pack/`). Reviewer sessions are best run after the console redesign (R7).
2. **Multi-case reviewer queue (R6)**, built once, after the review-console references arrive (R7), so the queue is not built twice.
3. OCR on real phone photos (D6), using a public dataset such as MIDV-500 or team-made specimens, never real people's documents.
4. A fresh, held-out labelled set to confirm accuracy out of sample. The held-out procedure and templates exist (`eval/holdout/`); the labels are not made.
5. Notes from the problem-owner discovery call (never provided). See `planning/DISCOVERY_QUESTIONS.md` (13 questions, all "not asked").
6. An executive presentation (rubric weight 10%): none exists in the project.

Hindi pop-ups and interface are no longer a gap; they are Future item S4.

**Agreed approach (Phase 3, 14 Nov).** The plan, spec, audit table and all 21 decisions are in `planning/PHASE1_AUDIT_AND_PLAN.md` and `planning/PHASE2_SPEC_AND_AUDIT.md` (Part D). In short:

- **Build order:** accessibility fixes (A10) → queue data → Node and the held-out set → process map → specimen print-outs and the photo bench → deck last. Usability sessions run whenever participants can be booked. No hard deadline.
- **1, usability:** five applicants from outside the team (at least two phone-first and Hindi-comfortable), two reviewers new to the app. Wording B of the follow-up is on a printed card. Hindi clarity is scored separately; the ≥ 4/5 target applies to each language. Results go to `planning/usability/results.csv`, codes only.
- **2, queue:** the data (routed test-set cases plus labelled constructed cases for the four Human-only kinds the test set lacks, and the ops-lead KPI figures) is built now. Ages of seeded cases are labelled simulated. The screens wait for the references (R7).
- **3, phone photos:** Claude makes printable specimen documents (generic layouts, fictional data, SPECIMEN across them); the user photographs them on two phones; a bench page measures the type gate and field reading. D6 moves only on measured numbers.
- **4, held-out set:** about 30–40 constructed cases, labelled by the user independently and frozen (hashed) before the engine sees them; 20 of them also serve as the Stage 1 hand-decided cases. Node.js LTS is installed to run the evaluation.
- **5, discovery:** no meeting took place; the nine questions from the brief, plus four added, are recorded as "not asked", each with today's assumption (`planning/DISCOVERY_QUESTIONS.md`).
- **6, presentation:** about 10 slides for a 7–10 minute talk, exportable to .pptx or PDF; accuracy is labelled in-sample.
- **Also added:** the Stage 1 process map from `stage_events` on the Evaluation page.

## 9. Challenge deliverables and scoring

The package's own rubric (`02_BUSINESS_REQUIREMENTS/05_DELIVERABLES_AND_EVALUATION_CRITERIA.md`) weights the work: decision accuracy and rigour 30%, product design and experience 25%, business understanding and opportunity sizing 20%, technical architecture and compliance 15%, executive presentation and defence 10%. The table lists the brief's staged deliverables (§9 of the brief) against what exists in the project today, checked on 14 Nov. "Not found" means no such file in the project folder.

| Stage | Deliverable | Rubric area | Status |
|---|---|---|---|
| 1 | Process map of today's journey from stage_events, with time and drop-off per step | Business understanding (20%) | Built and live on the Evaluation page (`stage1/tables/process_map_steps.csv`, 15 Nov). The source package has its own data-insights workbook. |
| 1 | 20 test applications decided by hand, at least 8 of them hard cases | Accuracy (30%) | Not found. Planned: 20 of the held-out cases, labelled by the user (Q9). |
| 1 | Which decisions the product makes and which it routes to a person | Accuracy | Done: R1, R2 and the statement below |
| 1 | First business case: hours and drop-offs saved | Business understanding | Partial: the E4 simulation covers days and drop-off; reviewer time is an estimate |
| 1 | Answers from the problem-owner meeting | Business understanding | Not done (no notes provided) |
| 2 | Extraction and cross-checks on all 600 applications | Accuracy | Done: E2 |
| 2 | First evaluation report: accuracy, per-class precision and recall | Accuracy | Done: `eval/EVALUATION_REPORT.md` |
| 2 | The ten cases the engine gets most wrong, and why | Accuracy | Partial: the first run's 12 wrong decisions are documented as fixes in the report; the current engine has none in-sample |
| 3 | Fairness check across states, genders and name patterns | Accuracy, compliance | Done: E3 |
| 3 | Usability tests: five applicants, two reviewers | Product design (25%) | Not done |
| 3 | Before/after simulation of time-to-certificate and drop-off | Business understanding | Done: E4 |
| 3 | An audit trail a compliance officer could read | Compliance (15%) | Done: E1 |
| 3 | A clear statement of what still needs a person, and why | Compliance | Done: see below |
| n/a | Executive presentation | Presentation (10%) | Not found. The only deck in the folder is the source package's problem breakdown. |

### What still needs a person, and why

The AI recommends; a person decides (rulebook §9, brief §8). Six situations stay with a person, because a wrong automatic call costs more than the time a reviewer spends on it:

- **A different person's name (NAME_MISMATCH).** The AI may recommend REJECT; a person confirms. A wrongful rejection loses a good agent, and a wrongful approval of someone else is a compliance breach. A confirmed case is referred internally to the fraud desk, and the applicant is told neutrally.
- **A face-match score between 0.60 and 0.75.** The rulebook has a reviewer compare the two photos.
- **A marksheet from a board not on the recognised list.** The rulebook marks it Human only but supplies no list.
- **A surname change after marriage.** The rulebook asks for a marriage certificate or gazette notification and leaves the decision to a person.
- **Any case the applicant disputes.** The REJECT screen has a Dispute button that routes the case to a reviewer.
- **Two selfie scores below 0.60 in a row (rulebook §4.6).** A reviewer decides. A confirmed case is referred internally to the fraud desk, and the applicant gets only a neutral message.

Everything else the engine decides itself, and where it is unsure it asks the applicant (NEEDS_MORE_INFO) rather than rejecting. In the evaluation, 11 of 600 applications (1.8%) went to a person.

## 10. For builders

- **Source of truth:** `policynest_mvp/PolicyNest.html`, built by `policynest_mvp/build_site.py` and deployed to Embarko. See `CLAUDE.md` for architecture, the deploy command and the CSP constraints.
- **Engine block:** the decision logic sits between `ENGINE:START` and `ENGINE:END`. It is pure and has no DOM, so `eval/run_eval.js` scores the exact code the site runs. Re-run the evaluation after any rule change, and report it as in-sample.
- **Keep to the user's decisions** listed in `CLAUDE.md`: one ideal case, no API keys, real OCR with per-slot type gates, the bento look in both themes, the review console left unchanged until the user sends references.
