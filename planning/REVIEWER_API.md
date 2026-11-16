# Reviewer API (16 Nov 2026)

This is the backend contract for reviewer sign-up, admin approval, the review queue, reviewer decisions and appeals. The code is in `policynest_mvp/copilot_api/reviewer.py` and the tests are in `test_reviewer.py`. It builds on what already existed:

- users and token versions (`auth.py`);
- applications, the timeline and notifications (`portal.py`);
- cases, appeals and the immutable `audit_events` table (`workflows.py`);
- ABAC decisions (`policy.py`).

**What reviewers see.** Reviewers see check results, document statuses, issue codes and a few allow-listed details. They never see photos, OCR text or full ID numbers, because the server never stores them. Every detail response carries this as `data_notice`, and the UI should show it.

## Role model

| Role | How it is created | What it can do |
| --- | --- | --- |
| `applicant` | `POST /api/auth/register` (the default) | Its own application, timeline, notifications, appeals and the assistant |
| `reviewer_pending` | `register` with `account_type:"reviewer"` | Only `GET`/`PATCH /api/me`, refresh and sign-out. Every other route returns 403. |
| `reviewer` | An admin approves a reviewer request | The queue, application detail, decisions, the appeal list and appeal resolution |
| `admin` | `register` with `account_type:"admin"` and the right `invite_code`, or `admin.py provision` | Everything a reviewer can do, plus the reviewer-request routes |
| `ops` | `admin.py provision --role ops` only | Reports only. There is no queue access, because the existing policy gives ops none. |

Rules (in `policy.py`: `QUEUE_ACTIONS` and `ADMIN_ACTIONS`):

- Registration never grants `reviewer` or `ops`. It grants `admin` only with the invite code.
- Nobody can act on an application they own. The error is `403 own_application`.
- If an admin has assigned a case to reviewer A (`/api/admin/assign`), reviewer B cannot act on it (`403 forbidden`). Admins can always act. An unassigned case is open to any reviewer.
- When a request is approved, the account's role becomes `reviewer` and its `token_version` is bumped. The old access token stops working (401), and the new role applies after `POST /api/auth/refresh` or the next sign-in. The frontend should refresh on a 401.

## Registration

`POST /api/auth/register` takes `{email, password, display_name, account_type?, invite_code?}`.

| `account_type` | Result |
| --- | --- |
| missing, `""` or `"applicant"` | `201 {user:{…, role:"applicant"}}` and a session, as before |
| `"reviewer"` | `201 {user:{…, role:"reviewer_pending"}}` and a session. It also creates a `reviewer_requests` row with `status:"pending"`. |
| `"admin"` | `201 {user:{…, role:"admin"}}` only if the env var `ADMIN_INVITE_CODE` is set (at least 16 characters) and `invite_code` matches. The compare is constant-time. |
| anything else | `400 invalid_account_type` |

Admin-invite errors:

- A wrong, missing or unset code returns `403 {"error":"invite_invalid","message":"This invite code is not valid."}`. The body is identical in every case.
- Too many attempts return `429 rate_limited`. The limit is 5 attempts per client per hour and 20 per hour for all clients together. While the limit applies, even the right code is refused.
- Every refusal is audited (`ADMIN_INVITE_REFUSED` or `ADMIN_INVITE_RATE_LIMITED`, actor 0), and so is every success (`ADMIN_CREATED_BY_INVITE`).

Other errors are unchanged: `400 invalid_registration`, `409 email_taken`, and `429` from the general auth limit.

`GET /api/me` adds `reviewer_request:{status:"pending"|"approved"|"denied", created_at, decided_at}` for any account that has a request. The admin's note and identity are never included.

## Admin routes

Only admins can use these routes. Writes need an allowed `Origin`, and every decision is audited.

**`GET /api/admin/reviewer-requests?status=pending|all`** (the default is `pending`)
Returns `{items:[{id:"RR-…", user_id, display_name, email, status, created_at, decided_at}]}`, oldest first. A bad status returns `400 invalid_status`.

**`POST /api/admin/reviewer-requests/decide`** with `{id, decision:"approve"|"deny", note?}` (the note is at most 500 characters and must not contain ID numbers)

- On success: `200 {id, user_id, status:"approved"|"denied", decided_at}`.
- If the request was already decided: `409 {error:"already_decided", current:"approved"|"denied"}`.
- Other errors: `404 not_found`, `400 invalid_decision`, `400 invalid_note`, `400 id_number`.
- A denied account stays `reviewer_pending`, with its request marked `denied`. The account cannot make a second request (see the limits below).
- Audit events: `REVIEWER_REQUESTED`, then `REVIEWER_APPROVED` or `REVIEWER_DENIED`, on subject `USER-<id>`.

## Reviewer queue

Reviewers and admins can use these routes.

**Definitions** (from `reviewer.state` and `portal.is_submitted`):

- **Submitted:** `progress.decision` is set, or `progress.stage` (case-insensitive) is `verification`, `training`, `exam` or `certificate`. Applications that are not submitted are never listed.
- **needs_review:** the application is submitted, and at least one of these holds:
  - an appeal is open;
  - there is no reviewer decision yet and the engine decision is `HUMAN_REVIEW`;
  - the latest review is `more_info` and the applicant has resubmitted, meaning `progress.round` is greater than the review's round and a decision is set;
  - the latest review is final but a later round is `HUMAN_REVIEW` again.
- **decided:** the latest reviewer action is `approve` or `reject`. An application can be both decided and needs_review, for example a reject with an open appeal.
- **all:** every submitted application.

**`GET /api/reviewer/applications?tab=needs_review|all|decided&q=`** (the default tab is `needs_review`)

```json
{"items":[{"reference":"PN-2026-01234","display_name":"Asha Rao","status":"IN_REVIEW","engine_decision":"HUMAN_REVIEW",
  "submitted_at":1763280000,"updated_at":1763280100,"waiting_seconds":420,"open_issues":["NAME_SURNAME_CHANGE"],
  "documents":{"PAN":"ok","SELFIE":"ok"},"review":{"action":"more_info","at":1763280050}|null,
  "open_appeal":false,"needs_review":true,"round":1}],
 "counts":{"needs_review":1,"appeals":0,"all":2,"decided":0}}
```

- `q` matches part of the reference or the display name, ignoring case (at most 80 characters).
- `counts` covers all submitted applications and ignores `q` and `tab`. `counts.appeals` is the number of open appeals.
- `waiting_seconds` counts from when the open appeal was raised, or else from the last (re)submission. A resubmission is the server time when `(round, decision)` last changed while submitted.
- The needs_review tab lists the longest wait first. The other tabs list the latest update first. At most 200 items are returned.
- A bad tab returns `400 invalid_tab`.

**`GET /api/reviewer/applications/{reference}`** returns:

```text
{reference, display_name, status, submitted_at, updated_at,
 details:{name?,dob?,state?,city?,education?,pan_last4?,aadhaar4?,acct_last4?,language?},
 progress:{stage, documents:{SLOT:{status,issues,version}}, decision, round,
           training:{modules_done,hours}, exam:{score?,passed?,attempts?}, certificate_issued},
 state:{submitted,needs_review,decided,open_appeal},
 assigned_reviewer: name|null, own_application: bool,
 timeline:[{t,actor,key,stage,doc}],                // the applicant's timeline, newest first, at most 200
 internal_events:[{action,at,actor_role}],          // audit_events for the case (REVIEW_*, APPEAL_*, REVIEWER_ASSIGNED…)
 appeals:[{id,status,explanation,response,created_at,resolved_at}],
 reviews:[{id,action,message,note,items,round,at,source:"review"|"appeal",appeal_id,reviewer}],
 data_notice:"Reviewers see check results, … never stored on the server …"}
```

The email address, mobile number and IFSC are deliberately left out. A malformed or unknown reference returns `404 not_found`.

**`POST /api/reviewer/applications/{reference}/decision`** takes `{action:"approve"|"reject"|"more_info", message, note?, items?}`.

- **`message`** goes to the applicant. It is required for `reject` and `more_info` and optional for `approve`. It is trimmed and may be at most 600 characters.
- **`note`** is internal, at most 1500 characters, and must not contain ID numbers. It is never shown to the applicant.
- **`items`** is read only for `more_info`: at most 12 entries of `{doc, issue}`. `doc` must be one of `PAN, AADHAAR_FRONT, AADHAAR_BACK, CLASS10, BANK, SELFIE`, and `issue` must match `^[A-Z][A-Z0-9_]{0,39}$`. Duplicates are dropped.
- **On success:** `200 {reference, review:{id,action,message,note,items,round,at,source,appeal_id}}`. The response goes to the reviewer, so it includes the note.
- **Effects:**
  - an immutable `reviews` row (triggers block UPDATE and DELETE);
  - the case status (`KYC_APPROVED`, `REJECTED` or `NEEDS_MORE_INFO`) and an audit event (`REVIEW_APPROVE`, `REVIEW_REJECT` or `REVIEW_MORE_INFO`);
  - an applicant timeline row and notification, with the key `review.approved`, `review.rejected` or `review.more_info`.

| Error | Meaning |
| --- | --- |
| `400 invalid_action` | `action` is not one of the three |
| `400 message_required` | No message for reject or more_info |
| `400 message_too_long` | The message is over 600 characters |
| `400 invalid_message` | The message is not a string |
| `400 id_number` | An ID number in the message or note; the body has `field` |
| `400 unsafe_message` | Fraud wording, from `guard.fraud_wording` |
| `400 invalid_note` | The note is not a string or is too long |
| `400 invalid_items` | An item has a bad slot or issue code, or there are too many |
| `403 own_application` | The reviewer owns this application |
| `403 forbidden` | The role is not allowed, or the case is assigned to another reviewer |
| `404 not_found` | Unknown reference |
| `409 not_submitted` | The application is not submitted |
| `409 appeal_open` | Answer the appeal instead |
| `409 already_decided` | A final decision already stands for this round; the body has `review:{action,at,round}` |

A `more_info` decision can be followed by a final decision on the same round.

## The applicant's view

`GET /api/applications/me` (and `POST`, and the `application` in a `409 stale`) now includes:

```json
"review": {"action":"more_info","message":"Please upload a clearer selfie.","items":[{"doc":"SELFIE","issue":"FACE_MATCH_LOW"}],
           "at":1763280050,"round":1,"by":"a reviewer"} | null
```

This is the latest review. It never includes the internal note, the reviewer's id or the reviewer's name.

The new notification and timeline keys all use the actor `reviewer`:

| Key | Type | Action |
| --- | --- | --- |
| `review.approved` | success | `#home` |
| `review.rejected` | info | `#history` |
| `review.more_info` | action | `#documents` |
| `appeal.upheld` | info | `#history` |
| `appeal.overturned` | success | `#home` |
| `appeal.more_info` | action | `#history` |

Notifications carry only the key. The text is in `review.message`, or in the appeal's `response` (`GET /api/appeals/me`).

**Resubmission:** after a `more_info` review, the applicant fixes the documents. The frontend must then save progress with a higher `progress.round` and a `decision`, which puts the application back in needs_review. A save on the same round does not.

## Appeals

**Raising an appeal (applicant):** this is the existing `POST /api/appeals` route with `{explanation}` (10 to 1500 characters). It is linked to the applicant's case and application, and returns `201 {reference:"AP-…", status:"OPEN"}`.

- New error codes (all of these used to return `403`):
  - `400 id_number`;
  - `400 invalid_explanation`;
  - `409 appeal_open` (only one appeal can be open at a time).
- The appeal's evidence is the legacy snapshot if there is one. Otherwise it is the portal progress: the decision, the round and each slot's status and issue codes.
- The server does not check whether the application was rejected before accepting an appeal (see the limits).
- The applicant reads appeal outcomes with the existing `GET /api/appeals/me`.

**`GET /api/reviewer/appeals?status=open|all`** (the default is `open`) returns `{items:[{id, reference, display_name, status, explanation, response, created_at, resolved_at, waiting_seconds}]}`, oldest first.

The appeal `status` values are:

- `OPEN`, `UPHELD`, `OVERTURNED` and `MORE_INFO`;
- `RESOLVED` and `ESCALATED`, from the legacy route.

**`POST /api/reviewer/appeals/{id}/resolve`** takes `{outcome:"uphold"|"overturn"|"more_info", message, note?}`.

- `message` goes to the applicant (stored as the appeal's `response`). It is required, at most 600 characters, and must contain no ID numbers or fraud wording.
- On success: `200 {id, status:"UPHELD"|"OVERTURNED"|"MORE_INFO", outcome, resolved_at}`.
- Every outcome writes an audit event (`APPEAL_<STATUS>`) and an applicant notification and timeline row (`appeal.upheld`, `appeal.overturned` or `appeal.more_info`).
- `overturn` also writes a review with `action:"approve"` and `source:"appeal"`, carrying the appeal's message. It also sets the case to `KYC_APPROVED`, so the applicant's `review` becomes approve.

| Error | Meaning |
| --- | --- |
| `409 already_resolved` | The appeal already has an outcome; the body has `current` |
| `400 invalid_outcome` | Not one of the three outcomes |
| `400 message_required` | No message |
| `400 message_too_long` | The message is over 600 characters |
| `400 id_number` | An ID number in the message or note |
| `400 unsafe_message` | Fraud wording |
| `403 own_application` / `403 forbidden` | As for decisions |
| `404 not_found` | Unknown appeal |

**Known defect, fixed:** an appeal could be resolved again after a non-`RESOLVED` outcome. Any appeal that is no longer `OPEN` now refuses a second resolution, on both the new route (`409`) and the legacy `POST /api/reviewer/appeals/resolve` (now `409 already_resolved` instead of `403`).

## Honest limits

- **The engine decision comes from the browser.** The checks run in the applicant's browser, which reports `progress.decision`, `round`, statuses and issue codes to the server. A tampered client could report a false engine decision, for example `APPROVE` instead of `HUMAN_REVIEW` to stay out of the queue. Reviewer decisions are made on the server and are authoritative. The queue's "engine decision" is what the device reported, not a server-verified result.
- **The reviewer evidence is limited.** The server holds no images or OCR text, so a reviewer judges from statuses, issue codes and allow-listed details. A real face or name comparison would need evidence the MVP deliberately does not keep.
- **Resubmission depends on the round.** A resubmission is detected only when the frontend raises `progress.round` and sets a decision.
- **The invite code is reusable.** It is not single-use. Anyone holding it, within the rate limit, can create an admin. Unset `ADMIN_INVITE_CODE` after the first admin exists.
- **A denied account cannot ask again.** Each account has one reviewer request (`user_id` is unique), so a denied requester cannot re-request. An admin would need a database change, or the person would need a new account.
- **Appeals are not tied to a rejection.** The server accepts an appeal in any state, one open at a time, as before.
- **The queue is shared.** There is no claiming or locking beyond admin assignment. Two reviewers can open the same item; the second final decision on a round gets `409 already_decided`.
- **Delivery and limits.** Notifications are in-app only, with no email or SMS delivery. The queue returns at most 200 items, without pagination. Message checks are regex-based (`guard.id_in` and `guard.fraud_wording`).
