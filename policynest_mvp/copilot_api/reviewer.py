"""Reviewer workspace: reviewer sign-up with admin approval, the shared review queue, reviewer decisions and appeals.

Built on what exists: users and token versions (auth), applications, timeline and notifications (portal),
cases, appeals and the immutable audit_events table (workflows), and ABAC decisions (policy).

What reviewers see: check results, statuses, issue codes and allow-listed details (name, date of birth, state,
city, education, last four characters of PAN, Aadhaar and account). The server never holds photos, OCR text or
full identity numbers, so a reviewer cannot see them. Messages to applicants refuse ID numbers and fraud wording;
the internal note is never shown to the applicant, and the applicant only ever sees "a reviewer".
"""
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time

import auth
import guard
import policy
import portal
import workflows
from portal import PortalError

ACTIONS = ('approve', 'reject', 'more_info')
FINAL = ('approve', 'reject')
OUTCOMES = {'uphold': 'UPHELD', 'overturn': 'OVERTURNED', 'more_info': 'MORE_INFO'}
REVIEW_KEY = {'approve': 'review.approved', 'reject': 'review.rejected', 'more_info': 'review.more_info'}
APPEAL_KEY = {'uphold': 'appeal.upheld', 'overturn': 'appeal.overturned', 'more_info': 'appeal.more_info'}
CASE_STATUS = {'approve': 'KYC_APPROVED', 'reject': 'REJECTED', 'more_info': 'NEEDS_MORE_INFO'}
DETAIL_KEYS = ('name', 'dob', 'state', 'city', 'education', 'pan_last4', 'aadhaar4', 'acct_last4', 'language')
ISSUE_RE = re.compile(r'^[A-Z][A-Z0-9_]{0,39}$')
REFERENCE_RE = re.compile(r'^PN-\d{4}-\d{5}$')
APPEAL_RE = re.compile(r'^AP-[0-9a-f]{16}$')
REQUEST_RE = re.compile(r'^RR-[0-9a-f]{16}$')
MESSAGE_MAX, NOTE_MAX, ITEMS_MAX = 600, 1500, 12
TABS = ('needs_review', 'all', 'decided')
DATA_NOTICE = ('Reviewers see check results, statuses, issue codes and a few allow-listed details only. '
               'Document photos, OCR text and full ID numbers are never stored on the server, so they cannot be shown here.')
INVITE_MESSAGE = 'This invite code is not valid.'


# ---------------------------------------------------------------- schema
def init():
    workflows.init()  # cases, appeals, audit_events (immutable) and the portal tables
    with auth._db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS reviewer_requests (
          id TEXT PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id), status TEXT NOT NULL,
          created_at INTEGER NOT NULL, decided_at INTEGER, decided_by INTEGER, note TEXT)""")
        db.execute("""CREATE TABLE IF NOT EXISTS reviews (
          id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, applicant_id INTEGER NOT NULL, reviewer_id INTEGER NOT NULL,
          action TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', items TEXT NOT NULL DEFAULT '[]',
          round INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT 'review', appeal_id TEXT, created_at INTEGER NOT NULL)""")
        db.execute('CREATE INDEX IF NOT EXISTS reviews_applicant ON reviews(applicant_id, id)')
        db.execute("CREATE TRIGGER IF NOT EXISTS reviews_no_update BEFORE UPDATE ON reviews BEGIN SELECT RAISE(ABORT,'immutable'); END")
        db.execute("CREATE TRIGGER IF NOT EXISTS reviews_no_delete BEFORE DELETE ON reviews BEGIN SELECT RAISE(ABORT,'immutable'); END")


def _audit(db, subject, actor_id, action):
    db.execute('INSERT INTO audit_events(case_id,actor_id,action,occurred) VALUES(?,?,?,?)', (subject, actor_id, action, int(time.time())))


def _record(db, applicant_id, key):
    """Applicant timeline row plus notification (key only). Retries the millisecond if two land together."""
    t = time.time_ns() // 1_000_000
    for i in range(5):
        if portal.record(db, applicant_id, key, t=t + i):
            return


# ---------------------------------------------------------------- registration and admin bootstrap
_invite_lock = threading.Lock()
_invite_hits = {}
INVITE_PER_CLIENT = (5, 3600)   # attempts per client per hour
INVITE_GLOBAL = (20, 3600)      # all clients together per hour


def _invite_limited(client):
    now = time.time()
    with _invite_lock:
        for k in list(_invite_hits):
            _invite_hits[k] = [t for t in _invite_hits[k] if now - t < INVITE_PER_CLIENT[1]]
            if not _invite_hits[k]:
                del _invite_hits[k]
        mine = _invite_hits.get(client, [])
        total = sum(len(v) for v in _invite_hits.values())
        if len(mine) >= INVITE_PER_CLIENT[0] or total >= INVITE_GLOBAL[0]:
            return True
        _invite_hits[client] = mine + [now]
        return False


def reset_limits():
    with _invite_lock:
        _invite_hits.clear()


def _invite_ok(given):
    expected = os.environ.get('ADMIN_INVITE_CODE', '')
    configured = len(expected) >= 16 and not expected.startswith('your-')
    # Compare even when unset (against a random value), so both refusals take the same path.
    target = expected if configured else secrets.token_urlsafe(24)
    given = given if isinstance(given, str) and len(given) <= 256 else ''
    return hmac.compare_digest(given.encode('utf-8'), target.encode('utf-8')) and configured


def register_account(body, client):
    """POST /api/auth/register. account_type: applicant (default), reviewer (pending admin approval) or admin
    (only with ADMIN_INVITE_CODE). Registration never grants reviewer or ops power directly."""
    kind = body.get('account_type')
    kind = 'applicant' if kind in (None, '') else kind
    if kind not in ('applicant', 'reviewer', 'admin'):
        raise PortalError(400, 'invalid_account_type')
    email, password, name = body.get('email'), body.get('password'), body.get('display_name')
    if kind == 'applicant':
        return auth.register(email, password, name)
    init()
    now = int(time.time())
    if kind == 'reviewer':
        def pending(db, uid):
            db.execute('INSERT INTO reviewer_requests(id,user_id,status,created_at) VALUES(?,?,?,?)',
                       ('RR-' + secrets.token_hex(8), uid, 'pending', now))
            _audit(db, 'USER-%d' % uid, uid, 'REVIEWER_REQUESTED')
        return auth.register(email, password, name, role='reviewer_pending', after_insert=pending)
    if _invite_limited(client):
        with auth._db() as db:
            _audit(db, 'AUTH', 0, 'ADMIN_INVITE_RATE_LIMITED')
        raise PortalError(429, 'rate_limited')
    if not _invite_ok(body.get('invite_code')):
        with auth._db() as db:
            _audit(db, 'AUTH', 0, 'ADMIN_INVITE_REFUSED')
        raise PortalError(403, 'invite_invalid', message=INVITE_MESSAGE)
    return auth.register(email, password, name, role='admin',
                         after_insert=lambda db, uid: _audit(db, 'USER-%d' % uid, uid, 'ADMIN_CREATED_BY_INVITE'))


# ---------------------------------------------------------------- admin: reviewer requests
def _admin(user):
    if not policy.allows(user, 'reviewers.manage', {'sensitivity': 'reviewer_only'}):
        raise PortalError(403, 'forbidden')


def list_requests(user, params):
    _admin(user)
    init()
    status = (params.get('status') or ['pending'])[0]
    if status not in ('pending', 'all'):
        raise PortalError(400, 'invalid_status')
    sql = ('SELECT r.id,r.user_id,u.display_name,u.email,r.status,r.created_at,r.decided_at FROM reviewer_requests r '
           'JOIN users u ON u.id=r.user_id')
    if status == 'pending':
        sql += " WHERE r.status='pending'"
    with auth._db() as db:
        rows = db.execute(sql + ' ORDER BY r.created_at, r.id').fetchall()
    return {'items': [dict(r) for r in rows]}


def decide_request(user, body):
    _admin(user)
    init()
    rid, decision, note = body.get('id'), body.get('decision'), body.get('note')
    if not isinstance(rid, str) or not REQUEST_RE.fullmatch(rid):
        raise PortalError(404, 'not_found')
    if decision not in ('approve', 'deny'):
        raise PortalError(400, 'invalid_decision')
    if note is not None and (not isinstance(note, str) or len(note) > 500):
        raise PortalError(400, 'invalid_note')
    if note and guard.id_in(note):
        raise PortalError(400, 'id_number', field='note')
    now = int(time.time())
    with auth._db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM reviewer_requests WHERE id=?', (rid,)).fetchone()
        if not row:
            raise PortalError(404, 'not_found')
        if row['status'] != 'pending':
            raise PortalError(409, 'already_decided', current=row['status'])
        status = 'approved' if decision == 'approve' else 'denied'
        db.execute('UPDATE reviewer_requests SET status=?,decided_at=?,decided_by=?,note=? WHERE id=?',
                   (status, now, user['id'], (note or '').strip(), rid))
        if status == 'approved':
            # The new role applies on the next refresh or sign-in: the old access token no longer matches.
            db.execute("UPDATE users SET role='reviewer',token_version=token_version+1 WHERE id=? AND role='reviewer_pending'",
                       (row['user_id'],))
        _audit(db, 'USER-%d' % row['user_id'], user['id'], 'REVIEWER_APPROVED' if status == 'approved' else 'REVIEWER_DENIED')
    return {'id': rid, 'user_id': row['user_id'], 'status': status, 'decided_at': now}


# ---------------------------------------------------------------- queue state
def _staff(user):
    if not policy.allows(user, 'queue.read', {'sensitivity': 'reviewer_only'}):
        raise PortalError(403, 'forbidden')


def _round(progress):
    r = progress.get('round')
    return int(r) if isinstance(r, int) and not isinstance(r, bool) else 0


def _latest_reviews(db, applicant_id=None):
    sql = 'SELECT * FROM reviews'
    args = ()
    if applicant_id is not None:
        sql += ' WHERE applicant_id=?'
        args = (applicant_id,)
    latest = {}
    for r in db.execute(sql + ' ORDER BY id', args):
        latest[r['applicant_id']] = r
    return latest


def _open_appeals(db, applicant_id=None):
    sql = "SELECT c.applicant_id,a.id,a.created FROM appeals a JOIN cases c ON c.id=a.case_id WHERE a.status='OPEN'"
    args = ()
    if applicant_id is not None:
        sql += ' AND c.applicant_id=?'
        args = (applicant_id,)
    out = {}
    for r in db.execute(sql + ' ORDER BY a.created', args):
        out.setdefault(r['applicant_id'], r)
    return out


def state(progress, review, appeal):
    """needs_review: the engine routed the current round to a person and nobody has decided it; or the applicant
    resubmitted (a new round with a decision) after a reviewer asked for more information; or an appeal is open.
    decided: the latest reviewer action is approve or reject."""
    submitted = portal.is_submitted(progress)
    rnd = _round(progress)
    needs = False
    if submitted:
        if appeal is not None:
            needs = True
        elif review is None:
            needs = progress.get('decision') == 'HUMAN_REVIEW'
        elif review['action'] == 'more_info':
            needs = progress.get('decision') is not None and rnd > review['round']
        else:  # a final decision exists: only a later round routed to a person again needs one
            needs = rnd > review['round'] and progress.get('decision') == 'HUMAN_REVIEW'
    return {'submitted': submitted, 'needs_review': needs, 'decided': bool(review is not None and review['action'] in FINAL),
            'open_appeal': appeal is not None}


def _issues(progress):
    out = set()
    for v in (progress.get('documents') or {}).values():
        if isinstance(v, dict):
            out.update(i for i in v.get('issues', []) if isinstance(i, str))
    return sorted(out)


def _queue_item(row, progress, review, appeal, st, now):
    since = appeal['created'] if appeal is not None else (row['last_submitted_at'] or row['submitted_at'] or row['updated_at'])
    return {'reference': row['reference'], 'display_name': row['display_name'], 'status': row['status'],
            'engine_decision': progress.get('decision'), 'submitted_at': row['submitted_at'], 'updated_at': row['updated_at'],
            'waiting_seconds': max(0, now - since) if since else None, 'open_issues': _issues(progress),
            'documents': {k: v.get('status') for k, v in (progress.get('documents') or {}).items() if isinstance(v, dict)},
            'review': {'action': review['action'], 'at': review['created_at']} if review is not None else None,
            'open_appeal': st['open_appeal'], 'needs_review': st['needs_review'], 'round': _round(progress)}


def queue(user, params):
    _staff(user)
    init()
    tab = (params.get('tab') or ['needs_review'])[0]
    if tab not in TABS:
        raise PortalError(400, 'invalid_tab')
    q = str((params.get('q') or [''])[0]).strip().lower()[:80]
    now = int(time.time())
    with auth._db() as db:
        rows = db.execute('SELECT a.*,u.display_name AS display_name FROM applications a JOIN users u ON u.id=a.user_id').fetchall()
        latest, appeals = _latest_reviews(db), _open_appeals(db)
    counts = {'needs_review': 0, 'appeals': len(appeals), 'all': 0, 'decided': 0}
    items = []
    for row in rows:
        progress = json.loads(row['progress'] or '{}')
        review, appeal = latest.get(row['user_id']), appeals.get(row['user_id'])
        st = state(progress, review, appeal)
        if not st['submitted']:
            continue
        counts['all'] += 1
        counts['needs_review'] += st['needs_review']
        counts['decided'] += st['decided']
        if (tab == 'needs_review' and not st['needs_review']) or (tab == 'decided' and not st['decided']):
            continue
        if q and q not in row['reference'].lower() and q not in row['display_name'].lower():
            continue
        items.append(_queue_item(row, progress, review, appeal, st, now))
    if tab == 'needs_review':
        items.sort(key=lambda i: -(i['waiting_seconds'] or 0))  # longest wait first
    else:
        items.sort(key=lambda i: -(i['updated_at'] or 0))
    return {'items': items[:200], 'counts': counts}


# ---------------------------------------------------------------- detail
def _app_by_reference(db, reference):
    if not isinstance(reference, str) or not REFERENCE_RE.fullmatch(reference):
        raise PortalError(404, 'not_found')
    row = db.execute('SELECT a.*,u.display_name AS display_name FROM applications a JOIN users u ON u.id=a.user_id WHERE a.reference=?',
                     (reference,)).fetchone()
    if not row:
        raise PortalError(404, 'not_found')
    return row


def _case(db, applicant_id):
    return db.execute('SELECT * FROM cases WHERE applicant_id=?', (applicant_id,)).fetchone()


def _resource(row, case):
    return {'applicant_id': row['user_id'], 'reviewer_id': case['reviewer_id'] if case else None, 'sensitivity': 'reviewer_only'}


def _review_out(r, names=None):
    out = {'id': r['id'], 'action': r['action'], 'message': r['message'], 'note': r['note'], 'items': json.loads(r['items']),
           'round': r['round'], 'at': r['created_at'], 'source': r['source'], 'appeal_id': r['appeal_id']}
    if names is not None:
        out['reviewer'] = names.get(r['reviewer_id'], 'a reviewer')
    return out


def detail(user, reference):
    _staff(user)
    init()
    with auth._db() as db:
        row = _app_by_reference(db, reference)
        case = _case(db, row['user_id'])
        if not policy.allows(user, 'queue.read', _resource(row, case)):
            raise PortalError(403, 'forbidden')
        progress = json.loads(row['progress'] or '{}')
        details = json.loads(row['details'] or '{}')
        reviews = db.execute('SELECT * FROM reviews WHERE applicant_id=? ORDER BY id', (row['user_id'],)).fetchall()
        names = {r['id']: r['display_name'] for r in db.execute(
            "SELECT id,display_name FROM users WHERE role IN ('reviewer','admin')")}
        timeline = [dict(r) for r in db.execute(
            'SELECT t,actor,key,stage,doc FROM timeline WHERE user_id=? ORDER BY t DESC,id DESC LIMIT 200', (row['user_id'],))]
        events, appeals = [], []
        if case:
            events = [{'action': r['action'], 'at': r['occurred'], 'actor_role': r['role'] or 'system'} for r in db.execute(
                'SELECT e.action,e.occurred,u.role FROM audit_events e LEFT JOIN users u ON u.id=e.actor_id WHERE e.case_id=? ORDER BY e.id',
                (case['id'],))]
            appeals = [{'id': r['id'], 'status': r['status'], 'explanation': r['explanation'], 'response': r['response'],
                        'created_at': r['created'], 'resolved_at': r['resolved']} for r in db.execute(
                'SELECT * FROM appeals WHERE case_id=? ORDER BY created', (case['id'],))]
        appeal = _open_appeals(db, row['user_id']).get(row['user_id'])
    review = reviews[-1] if reviews else None
    docs = {k: {'status': v.get('status'), 'issues': v.get('issues', []), 'version': v.get('version', 1)}
            for k, v in (progress.get('documents') or {}).items() if isinstance(v, dict)}
    training, exam = progress.get('training') or {}, progress.get('exam') or {}
    return {
        'reference': row['reference'], 'display_name': row['display_name'], 'status': row['status'],
        'submitted_at': row['submitted_at'], 'updated_at': row['updated_at'],
        'details': {k: details[k] for k in DETAIL_KEYS if k in details},
        'progress': {'stage': progress.get('stage'), 'documents': docs, 'decision': progress.get('decision'), 'round': _round(progress),
                     'training': {'modules_done': len(training.get('modules', [])), 'hours': training.get('hours', 0)},
                     'exam': {k: exam[k] for k in ('score', 'passed', 'attempts') if k in exam},
                     'certificate_issued': bool((progress.get('certificate') or {}).get('code'))},
        'state': state(progress, review, appeal),
        'assigned_reviewer': names.get(case['reviewer_id']) if case and case['reviewer_id'] else None,
        'own_application': row['user_id'] == user['id'],
        'timeline': timeline, 'internal_events': events, 'appeals': appeals,
        'reviews': [_review_out(r, names) for r in reviews],
        'data_notice': DATA_NOTICE,
    }


# ---------------------------------------------------------------- decisions
def _message(value, required, field='message'):
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise PortalError(400, 'invalid_' + field)
    value = value.strip()
    if required and not value:
        raise PortalError(400, field + '_required')
    if len(value) > MESSAGE_MAX:
        raise PortalError(400, field + '_too_long', max=MESSAGE_MAX)
    if guard.id_in(value):
        raise PortalError(400, 'id_number', field=field)
    if guard.fraud_wording(value):
        raise PortalError(400, 'unsafe_message', field=field)
    return value


def _note(value):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value) > NOTE_MAX:
        raise PortalError(400, 'invalid_note', max=NOTE_MAX)
    if guard.id_in(value):
        raise PortalError(400, 'id_number', field='note')
    return value.strip()


def _items(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > ITEMS_MAX:
        raise PortalError(400, 'invalid_items')
    out = []
    for i in value:
        if not isinstance(i, dict) or i.get('doc') not in portal.SLOTS or not isinstance(i.get('issue'), str) \
                or not ISSUE_RE.fullmatch(i['issue']):
            raise PortalError(400, 'invalid_items')
        pair = {'doc': i['doc'], 'issue': i['issue']}
        if pair not in out:
            out.append(pair)
    return out


def _set_case(db, applicant_id, status, actor_id, action):
    db.execute('INSERT OR IGNORE INTO cases VALUES(?,?,NULL,?)', ('PN-' + secrets.token_hex(8), applicant_id, 'DRAFT'))
    case = _case(db, applicant_id)
    db.execute('UPDATE cases SET status=? WHERE id=?', (status, case['id']))
    _audit(db, case['id'], actor_id, action)


def decide(user, reference, body):
    _staff(user)
    init()
    action = body.get('action')
    if action not in ACTIONS:
        raise PortalError(400, 'invalid_action')
    message = _message(body.get('message'), required=action != 'approve')
    note = _note(body.get('note'))
    items = _items(body.get('items')) if action == 'more_info' else []
    now = int(time.time())
    with auth._db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = _app_by_reference(db, reference)
        case = _case(db, row['user_id'])
        if row['user_id'] == user['id']:
            raise PortalError(403, 'own_application')
        if not policy.allows(user, 'queue.decide', _resource(row, case)):
            raise PortalError(403, 'forbidden')
        progress = json.loads(row['progress'] or '{}')
        if not portal.is_submitted(progress):
            raise PortalError(409, 'not_submitted')
        if portal.past_verification(progress):
            # the applicant already moved on to training, the exam or a certificate: a decision now would leave the
            # server and the applicant's screen disagreeing (no revoke flow exists yet)
            raise PortalError(409, 'past_verification')
        if _open_appeals(db, row['user_id']):
            raise PortalError(409, 'appeal_open')  # answer it through POST /api/reviewer/appeals/{id}/resolve
        rnd = _round(progress)
        latest = _latest_reviews(db, row['user_id']).get(row['user_id'])
        if latest is not None and latest['action'] in FINAL and latest['round'] >= rnd:
            # a final decision stands for this round; an open appeal is answered through the appeals route
            raise PortalError(409, 'already_decided', review={'action': latest['action'], 'at': latest['created_at'], 'round': latest['round']})
        cur = db.execute('INSERT INTO reviews(application_id,applicant_id,reviewer_id,action,message,note,items,round,source,created_at) '
                         'VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (row['id'], row['user_id'], user['id'], action, message, note, json.dumps(items), rnd, 'review', now))
        _set_case(db, row['user_id'], CASE_STATUS[action], user['id'], 'REVIEW_' + action.upper())
        _record(db, row['user_id'], REVIEW_KEY[action])
        portal.refresh_status(db, row['user_id'])
        saved = db.execute('SELECT * FROM reviews WHERE id=?', (cur.lastrowid,)).fetchone()
    return {'reference': reference, 'review': _review_out(saved)}


def applicant_review(db, applicant_id):
    """What the applicant sees of the latest decision: never the note or the reviewer's identity."""
    try:
        r = db.execute('SELECT * FROM reviews WHERE applicant_id=? ORDER BY id DESC LIMIT 1', (applicant_id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not r:
        return None
    return {'action': r['action'], 'message': r['message'], 'items': json.loads(r['items']), 'at': r['created_at'],
            'round': r['round'], 'by': 'a reviewer'}


# ---------------------------------------------------------------- appeals
def list_appeals(user, params):
    _staff(user)
    init()
    status = (params.get('status') or ['open'])[0]
    if status not in ('open', 'all'):
        raise PortalError(400, 'invalid_status')
    sql = ('SELECT a.*,c.applicant_id,u.display_name,app.reference AS reference FROM appeals a JOIN cases c ON c.id=a.case_id '
           'JOIN users u ON u.id=c.applicant_id LEFT JOIN applications app ON app.user_id=c.applicant_id')
    if status == 'open':
        sql += " WHERE a.status='OPEN'"
    now = int(time.time())
    with auth._db() as db:
        rows = db.execute(sql + ' ORDER BY a.created').fetchall()
    return {'items': [{'id': r['id'], 'reference': r['reference'], 'display_name': r['display_name'], 'status': r['status'],
                       'explanation': r['explanation'], 'response': r['response'], 'created_at': r['created'],
                       'resolved_at': r['resolved'], 'waiting_seconds': max(0, now - r['created']) if r['status'] == 'OPEN' else None}
                      for r in rows]}


def resolve_appeal(user, appeal_id, body):
    _staff(user)
    init()
    if not isinstance(appeal_id, str) or not APPEAL_RE.fullmatch(appeal_id):
        raise PortalError(404, 'not_found')
    outcome = body.get('outcome')
    if outcome not in OUTCOMES:
        raise PortalError(400, 'invalid_outcome')
    message = _message(body.get('message'), required=True)
    note = _note(body.get('note'))
    now = int(time.time())
    with auth._db() as db:
        db.execute('BEGIN IMMEDIATE')
        appeal = db.execute('SELECT a.*,c.applicant_id,c.reviewer_id FROM appeals a JOIN cases c ON c.id=a.case_id WHERE a.id=?',
                            (appeal_id,)).fetchone()
        if not appeal:
            raise PortalError(404, 'not_found')
        if appeal['applicant_id'] == user['id']:
            raise PortalError(403, 'own_application')
        resource = {'applicant_id': appeal['applicant_id'], 'reviewer_id': appeal['reviewer_id'], 'sensitivity': 'reviewer_only'}
        if not policy.allows(user, 'queue.appeal', resource):
            raise PortalError(403, 'forbidden')
        if appeal['status'] != 'OPEN':
            raise PortalError(409, 'already_resolved', current=appeal['status'])
        status = OUTCOMES[outcome]
        db.execute('UPDATE appeals SET status=?,response=?,resolved=? WHERE id=?', (status, message, now, appeal_id))
        _audit(db, appeal['case_id'], user['id'], 'APPEAL_' + status)
        if outcome == 'overturn':
            app = db.execute('SELECT id,progress FROM applications WHERE user_id=?', (appeal['applicant_id'],)).fetchone()
            if app:
                db.execute('INSERT INTO reviews(application_id,applicant_id,reviewer_id,action,message,note,items,round,source,appeal_id,created_at) '
                           'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (app['id'], appeal['applicant_id'], user['id'], 'approve', message, note, '[]',
                            _round(json.loads(app['progress'] or '{}')), 'appeal', appeal_id, now))
            db.execute('UPDATE cases SET status=? WHERE id=?', ('KYC_APPROVED', appeal['case_id']))
            portal.refresh_status(db, appeal['applicant_id'])
        _record(db, appeal['applicant_id'], APPEAL_KEY[outcome])
    return {'id': appeal_id, 'status': status, 'outcome': outcome, 'resolved_at': now}


# ---------------------------------------------------------------- route table
ROUTES = [
    ('GET', re.compile(r'^/api/admin/reviewer-requests$'), lambda u, b, p, m: list_requests(u, p)),
    ('POST', re.compile(r'^/api/admin/reviewer-requests/decide$'), lambda u, b, p, m: decide_request(u, b)),
    ('GET', re.compile(r'^/api/reviewer/applications$'), lambda u, b, p, m: queue(u, p)),
    ('GET', re.compile(r'^/api/reviewer/applications/(?P<ref>[^/]{1,40})$'), lambda u, b, p, m: detail(u, m['ref'])),
    ('POST', re.compile(r'^/api/reviewer/applications/(?P<ref>[^/]{1,40})/decision$'), lambda u, b, p, m: decide(u, m['ref'], b)),
    ('GET', re.compile(r'^/api/reviewer/appeals$'), lambda u, b, p, m: list_appeals(u, p)),
    ('POST', re.compile(r'^/api/reviewer/appeals/(?P<id>[^/]{1,40})/resolve$'), lambda u, b, p, m: resolve_appeal(u, m['id'], b)),
]


def match(method, path):
    for m, rx, fn in ROUTES:
        if m == method:
            found = rx.match(path)
            if found:
                return fn, found.groupdict()
    return None


def template(path):
    """Route template for telemetry (no references or ids)."""
    for _, rx, _ in ROUTES:
        if rx.match(path):
            return re.sub(r'\(\?P<(\w+)>[^)]*\)', lambda g: '{reference}' if g.group(1) == 'ref' else '{id}', rx.pattern.strip('^$'))
    return None
