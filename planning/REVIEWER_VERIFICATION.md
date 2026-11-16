# Reviewer side and 30-minute timer: verification

16 Nov 2026. This was a read-only audit of commits `b1f222c` (RV-BE) and `6a73360` (RV-FE). No source file was changed. Nothing was pushed or deployed.

## Verdict

**Works, with 3 major findings to fix before deploy. No blockers.**

What works, tested through the API and in the browser:

- The role model and admin approval hold. An applicant or `reviewer_pending` account cannot reach any reviewer or admin data or action, nobody can self-approve, and registration cannot grant a role.
- The internal note and the reviewer's identity never reach the applicant.
- Double decides and double resolves return 409, including under concurrent requests.
- Origins are enforced on every new write route.

The three major findings:

1. The server does not enforce a reviewer's reject.
2. An Aadhaar number written with hyphens passes every ID-number filter.
3. In production, with default settings, the time's-up dialog promises progress is kept, but it is lost on reload.

## Test results

| Suite | Result |
|---|---|
| Backend unittest (venv), run 3 times | **126/126** each run (100 existing + 26 `test_reviewer.py`) |
| `python tools/check_pack.py` | **ALL PASS**, engine hash `c206388747e8381e` |
| `python tools/check_prod_build.py` | **ALL PASS** (3 checks) |
| `node --test tools/test_model.mjs` | **21/21** |
| `python tools/test_portal_e2e.py` | **67/67** |
| `python tools/test_reviewer_e2e.py` (demo build, ports 9530/9531) | **69/69** |
| `python tools/t1_t10.py` on site-demo (127.0.0.1:9540) | **10/10** |
| Own adversarial API script (temporary database, port 9550) | **91/93**: the 2 failures are findings 1 and 2 |
| Own Playwright session on the **prod** build (`site/`, API on 9552, site on 9553) | **26/28**: the 2 failures are findings 2 and 3 |
| en/hi string parity (I18N, I18N_UI, I18N_RV, I18N_P5, I18N_P6) | 710 keys each side, 0 missing, 0 placeholder mismatches |

Every server I started was stopped. The scripts are in the session scratchpad (`adv_api.py`, `prod_pw.py`, `parity.mjs`). Prod screenshots are in `scratchpad/prodshots/`.

## What was checked and held

**Registration**

- `role:"admin"` in the body is ignored.
- `account_type` values `ops`, `reviewer_pending`, `service`, `Reviewer` and `ADMIN` each return 400.
- An admin invite that is missing, wrong or sent as a list returns 403 `invite_invalid`.
- Register without an Origin header returns 403.
- After 5 wrong invites the client gets 429, and the right code is refused while limited.

**reviewer_pending**

- Refused on:
  - the queue, detail, appeals and admin routes;
  - the legacy `/api/reviewer/queue`;
  - applications, notifications and appeals.
- It cannot approve its own request, even with the real `RR-…` id.
- `PATCH /api/me` cannot change the role.
- In the prod UI it sees only the waiting page, even when typing `#review` in the address bar.

**Approval**

- The admin's decide route returns 403 without an Origin or with a wrong one.
- An ID number in the admin's note returns 400.
- A second decision returns 409.
- The old access token returns 401 after approval; a refresh brings the `reviewer` role.
- In the prod UI, Check again opens the console.
- An approved reviewer gets 403 on every admin route, so cannot approve another reviewer.

**Queue**

- Only submitted applications are listed, and the counts are correct.
- A bad tab returns 400. A search with SQL injection text is harmless. An unknown reference returns 404.
- Detail leaves out email, mobile and IFSC and carries `data_notice`. The prod UI shows both notices.

**Decisions**

- Each of these returns 400 with the right code:
  - a missing message;
  - a PAN, or an Aadhaar written with spaces;
  - "fraud", "forged" or the Hindi word for fraud;
  - more than 600 characters;
  - a bad action;
  - bad items;
  - an ID number in the note.
- more_info takes the case out of Needs review. A save on the same round does not bring it back; round 2 with a decision does.
- 4 concurrent final decisions: exactly one 200 and three 409.
- more_info after a final decision on the same round returns 409.

**Leaks**

- The internal note, the reviewer's name and the reviewer's email are absent from all of these: `GET /api/applications/me`, timeline, notifications and `/api/appeals/me`.
- The applicant's `review` holds only these keys: `action`, `message`, `items`, `at`, `round`, `by`.

**Appeals**

- A PAN in the explanation returns 400 `id_number`. A second open appeal returns 409.
- An open appeal puts the case in Needs review, and a decide call returns 409 `appeal_open`.
- An applicant cannot resolve an appeal. Fraud wording returns 400. A call without an Origin returns 403.
- 3 concurrent resolves: one 200 and two 409. A re-resolve returns 409.
- The legacy resolve route is refused on an appeal that is already resolved.
- The appeal note never reaches the applicant.

**Accessibility, prod build**

- Dialogs are native with `aria-labelledby`. The time's-up dialog is an `alertdialog`.
- M on a case opens the more-info dialog with a prefilled checklist.
- Typing "r" or "a" in its textarea triggers no shortcut.
- Escape closes the dialog and returns focus to a button.
- A wrong invite sets `aria-invalid` on the invite field.

**390 px**

- No horizontal scroll on the queue, the case detail or the timer bar.

**Timer, prod build at 390 px**

- It shows 30:00 at the start, and Hide timer works.
- There is no full screen and no warning after `visibilitychange`.
- At +31 min the "Time's up" dialog opens with Take 10 more minutes and Save and exit.
- No page errors.

## Findings

### Major

**M1. The server does not enforce a reviewer's reject; the applicant's browser can certify itself.**

- **What happens:** the application status is derived only from the progress the client reports.
  - A reviewer rejected round 1.
  - Then `PUT /api/applications/me` with `stage:"certificate"`, `decision:"APPROVE"`, a passed exam and a certificate code returned 200.
  - The server status is now `CERTIFIED`, and the queue row shows `status: CERTIFIED` next to `review: reject`.
  - The same applies to a HUMAN_REVIEW case that no one has approved: nothing on the server stops training, exam or certificate.
- **Why it matters:** `planning/REVIEWER_API.md` calls reviewer decisions authoritative.
- **Fix:**
  - In `portal.put_application`, refuse or clamp progress past verification while the latest review is reject, or while the case needs review and has no approve review.
  - Have `derive_status` take the latest review into account.
  - Add a test for each.

**M2. An Aadhaar number written with hyphens passes every ID-number filter, so it is stored and shown to the applicant.**

- **What happens:** all of these use the same pattern, `\d{4}\s?\d{4}\s?\d{4}`, which allows only spaces between the groups:
  - `guard.ID_NUMBER` (reviewer messages and notes, admin notes);
  - `workflows.appeals` (appeal explanation);
  - `portal.clean_details`;
  - the console's `ID_RE`.
- **Evidence:**
  - Through the API, more_info with "Aadhaar 1234-5678-9012 is wrong" returned 200.
  - In the prod UI, a reject with "Your Aadhaar 1234-5678-9012 name differs." was accepted, and the applicant's `review.message` holds the full number.
  - "1234.5678.9012" has the same gap.
  - The likeliest real case is an applicant typing their Aadhaar into an appeal: it is then stored on the server and shown to reviewers.
- **Why it matters:** it breaks the hard rule that the server never stores a full Aadhaar number.
- **Fix:** use `\d{4}[\s.\-]?\d{4}[\s.\-]?\d{4}` (and allow the same separators in long account numbers) in all four places, then add tests.

**M3. In production with default settings, the time's-up dialog promises progress is kept, but it is lost on reload.**

- **What happens:**
  - A signed-in applicant who left "save to my account" off (the default) and did not tick save on this device typed a name.
  - At zero the dialog says "Everything you have done so far is kept". After Save and exit the toast says "Your progress is kept. You can come back at any time."
  - The server copy was empty (`details: {}`). After a reload the intro screen came back with nothing typed.
  - The Save and exit confirmation (`exit.b.unsaved`) states this limit correctly, but the time's-up path skips it.
- **Why it matters:** the user decided that progress is never lost or wiped.
- **Fix:** when neither account sync nor device saving is on:
  - in the dialog, offer "Save to my account" (or the device consent) before exiting;
  - otherwise use the `exit.b.unsaved` wording;
  - and do not show `tm.saved`.

### Minor

**m1. The `test_reviewer.py` leak test is flaky.**

- Line 313 runs `assertNotIn(str(staff.id), json.dumps(app['review']))`, which fails whenever the `at` timestamp or the round contains the id's digits. The FE lane hit this; my 3 runs passed by chance.
- **Fix:** assert that no `reviewer`, `reviewer_id` or `note` key is present, and that the reviewer's name and email do not appear.

**m2. The review console does not refresh by itself.**

- **What happens:** a case submitted while the queue is open does not appear until the reviewer reloads or clicks a tab. There is no Refresh button, and returning to `#review` keeps the stale list.
- **Fix:** refresh on focus or visibility, or poll every 60 s like the applicant side, or add a Refresh button.

**m3. A reviewer can decide an application the engine already approved, even a certified one.**

- **What happens:** the server accepts a reject, but `rvApply` ignores it because CERTIFIED is not in `RV_PRE`. The server and the applicant then disagree.
- **Fix:** refuse decisions on certified applications, or route them to an explicit revoke flow.

**m4. At 390 px the onboarding focus bar wraps to three rows.**

- **What happens:** the bar takes about 145 px and covers the first field label on details pages (see `p_timer_390.png`).
- **Fix:** move Ask Copilot and Save and exit into one row with icon buttons, or let the bar scroll away.

**m5. "Delete my saved progress" shows on the paused screen when nothing is saved on the device.**

- **Fix:** hide it unless the device copy exists.

**m6. Hindi gaps on the applicant side, as the lanes listed.**

- The reviewer's message shows as written in both languages, and the older reject and fix panel text is English only.
- Key parity is complete; this is about content.

## Not verified

- The full applicant journey to submission on the prod build: real OCR is needed and the CDN is blocked here. It is covered on the demo build by `test_reviewer_e2e.py` and T1–T10.
- The pause while the file picker is open: not covered by any test.
- Real phone browsers.

## Fixes after verification (16 Nov 2026, lane RV-FIX)

**M1. The server now enforces reviewer decisions.**

- `portal.review_hold` returns a reason when the applicant may not go past verification:
  - `rejected`: the latest reviewer decision is reject. Only an appeal overturn lifts it, because the overturn records an approve.
  - `awaiting_review`: a round was routed to a person and no reviewer has approved that round or a later one. A new round sent after a reviewer's more_info is not held, so the fixes count. Bumping the round number with no request from a reviewer does not lift the hold.
- The server records the round routed to a person in a new `applications.human_round` column (added by `ALTER TABLE` when missing).
- `put_application` refuses training, exam or certificate progress while a hold stands: `409 review_hold` with the reason. Saves that stay at verification still work, so details keep saving.
- `derive_status` now reads the latest review:
  - REJECTED after a reject;
  - NEEDS_MORE_INFO while the reviewer's request is open;
  - IN_REVIEW while a routed round is unapproved;
  - VERIFIED after a reviewer approves.
- `reviewer.decide` and the appeal overturn call `portal.refresh_status`, so the queue row and the applicant see the decision at once.
- **m3 is fixed too.** `reviewer.decide` refuses a decision once the applicant is past verification (`409 past_verification`, with a console message), so the server and the applicant's screen cannot disagree.
- **Client side.** A `review_hold` reply sets the save state to failed and fetches the reviewer's decision.
- **Tests.** `copilot_api/test_review_authority.py` (`Authority`, 7 tests) covers:
  - reject, then certificate, training or exam progress returns 409;
  - an unapproved HUMAN_REVIEW is held, including when the round is bumped;
  - more_info followed by a new round is not held;
  - a later routed round is held again;
  - an upheld appeal keeps the hold and an overturned one lifts it;
  - an engine-approved application is never held;
  - no decision is accepted once the applicant is past verification.

**M2. ID numbers written with hyphens or dots are refused.**

- `guard.NUMBER_TEXT` and `guard.ID_NUMBER` now catch:
  - 12-digit Aadhaar numbers with an optional single space, hyphen or dot between the groups;
  - any run of 9 or more digits with those separators (bank accounts);
  - a PAN.
- `workflows.appeals` (the explanation), `workflows.resolve`, `portal.clean_details` and all reviewer and admin text (through `guard.id_in`) use them.
- In the page:
  - the console's `ID_RE`, the applicant's appeal check and the Copilot question check use the same pattern;
  - so do the details allow-list (`put`) and the read-aloud redaction.
- Dates such as 16-11-2026, scores and the format example AAAAA9999A are still allowed.
- **Tests.** `test_review_authority.IdNumbers` (5 tests) covers:
  - the guard pattern;
  - reviewer message and note (reject, more_info, approve);
  - the appeal explanation and the appeal answer;
  - the admin note;
  - details.
- **m1 is fixed too.** The flaky leak assertion in `test_reviewer.py` now checks keys and the reviewer's email, not the id's digits.

**M3. The time's-up dialog no longer promises what it cannot keep.**

- `keptWhere()` tells which case applies: device (consented), account (sync on and ready), or none.
- **When nothing is saved:**
  - the dialog uses `tm.up.b.unsaved` ("Right now nothing is saved...");
  - it offers Take 10 more minutes, **Save to my account and exit** (signed-in applicants), **Save on this device and exit** and **Exit without saving**;
  - Save and exit (`askExit`) offers the same choices.
- **Save to my account** turns account saving on, boots sync and flushes. Only after a confirmed save does it exit and say "Saved to your account". Otherwise it shows `tm.save.fail` and keeps the sitting.
- **Save on this device** records the consent and saves.
- **Exit without saving** announces `tm.unsaved`. `tm.saved` is used only when the device copy exists. A signed-in applicant with sync on gets `exit.b.acct` and `tm.saved.acct`.
- All new strings are in English and Hindi.
- **Tests.** `run_timer_unsaved` in `tools/test_reviewer_e2e.py`:
  - **Signed in, sync off:** the dialog says nothing is saved and offers all four choices. Save to my account puts the typed name on the server, and after a reload the application resumes instead of showing the intro.
  - **Signed out:** Save and exit explains the limit. Save on this device keeps the details.
  - **Exit without saving:** it says "Nothing was saved".

### Re-run results (16 Nov 2026, after the fixes)

| Suite | Result |
|---|---|
| Backend `unittest` (venv, `copilot_api`) | 138 tests, OK (126 before, plus 12 new) |
| `tools/check_pack.py` | ALL PASS (engine hash c206388747e8381e) |
| `tools/check_prod_build.py` | ALL PASS |
| `node --test tools/test_model.mjs` | 21/21 pass |
| `tools/test_portal_e2e.py` | 67 checks, 0 failed |
| `tools/test_reviewer_e2e.py` | 80/80 passed (it now includes the M3 timer runs) |
| `tools/t1_t10.py` on site-demo (port 9562) | 10/10 passed |

- Both sites were rebuilt (`build_site.py --mode prod` and `--mode demo`). Nothing was deployed or pushed.
- **Still open:**
  - m2: the console does not refresh by itself;
  - m4: the focus bar wraps at 390 px;
  - m5: "Delete my saved progress" shows when nothing is saved;
  - m6: Hindi content gaps;
  - a rejected applicant who starts a new application on the same account stays held by the old reject (by design, until an appeal is overturned).
