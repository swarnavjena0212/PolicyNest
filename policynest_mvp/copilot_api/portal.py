"""Applicant portal service: profile, versioned application, timeline and notifications.

Nothing here stores photos, OCR text, a full PAN, a full Aadhaar or a full bank account number.
Timeline rows and notifications hold only allow-listed event keys plus a stage and a document slot,
so they cannot carry identifiers or document contents. Authorisation goes through policy.allows.
"""
import json
import re
import secrets
import sqlite3
import time

import auth
import guard
import policy


class PortalError(Exception):
    def __init__(self, status, error, **extra):
        super().__init__(error)
        self.status = status
        self.payload = dict(extra, error=error)


# ---------------------------------------------------------------- schema
def init():
    with auth._db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS applications (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
          reference TEXT NOT NULL UNIQUE, version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'DRAFT',
          details TEXT NOT NULL DEFAULT '{}', progress TEXT NOT NULL DEFAULT '{}',
          created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS timeline (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, t INTEGER NOT NULL, actor TEXT NOT NULL, key TEXT NOT NULL,
          stage TEXT NOT NULL DEFAULT '', doc TEXT NOT NULL DEFAULT '',
          UNIQUE(user_id, key, stage, doc, t))""")
        db.execute("""CREATE TABLE IF NOT EXISTS notifications (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, type TEXT NOT NULL, key TEXT NOT NULL,
          stage TEXT NOT NULL DEFAULT '', doc TEXT NOT NULL DEFAULT '', action TEXT NOT NULL DEFAULT '',
          read INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, reference TEXT NOT NULL DEFAULT '')""")
        cols = {r[1] for r in db.execute('PRAGMA table_info(applications)')}
        # submitted_at / last_submitted_at: when it first / last reached a reviewable state (server time).
        # human_round: the highest round the server has seen routed to a person (decision HUMAN_REVIEW).
        for name in ('submitted_at', 'last_submitted_at', 'human_round'):
            if name not in cols:
                db.execute('ALTER TABLE applications ADD COLUMN ' + name + ' INTEGER')
        db.execute('CREATE INDEX IF NOT EXISTS timeline_user ON timeline(user_id, t)')
        db.execute('CREATE INDEX IF NOT EXISTS notif_user ON notifications(user_id, read, created_at)')


# ---------------------------------------------------------------- helpers
def _applicant(user):
    """Only applicants own a portal application; ownership is the user id, checked through the ABAC policy."""
    resource = {'applicant_id': user['id'], 'status': 'DRAFT', 'sensitivity': 'applicant'}
    if user.get('role') != 'applicant' or not policy.allows(user, 'portal.read', resource) \
            or not policy.allows(user, 'portal.write', resource):
        raise PortalError(403, 'forbidden')
    return user


def _mask_mobile(m):
    digits = re.sub(r'\D', '', str(m or ''))
    return ('•' * 6 + digits[-4:]) if len(digits) >= 4 else None


def _clean_mobile(value):
    digits = re.sub(r'[\s\-]', '', str(value or ''))
    digits = re.sub(r'^(\+91|91|0)(?=\d{10}$)', '', digits)
    if not re.fullmatch(r'[6-9]\d{9}', digits):
        raise PortalError(400, 'invalid_mobile')
    return digits


def public_profile(row):
    return {'id': int(row['id']), 'email': row['email'], 'display_name': row['display_name'], 'role': row['role'],
            'email_verified': False, 'mobile_verified': False,  # no verification provider exists yet
            'mobile_masked': _mask_mobile(row['mobile']), 'language': row['language'],
            'analytics_consent': bool(row['analytics_consent'])}


def get_profile(user):
    with auth._db() as db:
        row = db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
        try:
            req = db.execute('SELECT status,created_at,decided_at FROM reviewer_requests WHERE user_id=?', (user['id'],)).fetchone()
        except sqlite3.OperationalError:  # no reviewer request has ever been made on this database
            req = None
    if not row:
        raise PortalError(401, 'unauthenticated')
    out = public_profile(row)
    if req or row['role'] == 'reviewer_pending':
        # status only: the admin's note and identity are never shown to the requester
        out['reviewer_request'] = {'status': req['status'], 'created_at': req['created_at'], 'decided_at': req['decided_at']} if req else {'status': 'pending', 'created_at': None, 'decided_at': None}
    return {'user': out}


def patch_profile(user, body):
    sets, args = [], []
    if 'display_name' in body:
        name = str(body['display_name'] or '').strip() if isinstance(body['display_name'], str) else ''
        if not 2 <= len(name) <= 80:
            raise PortalError(400, 'invalid_display_name')
        sets.append('display_name=?'); args.append(name)
    if 'mobile' in body:
        if body['mobile'] in (None, ''):
            sets.append('mobile=NULL')
        else:
            sets.append('mobile=?'); args.append(_clean_mobile(body['mobile']))
    if 'language' in body:
        if body['language'] not in ('en', 'hi'):
            raise PortalError(400, 'invalid_language')
        sets.append('language=?'); args.append(body['language'])
    if 'analytics_consent' in body:
        if not isinstance(body['analytics_consent'], bool):
            raise PortalError(400, 'invalid_consent')
        sets.append('analytics_consent=?'); args.append(1 if body['analytics_consent'] else 0)
    if sets:
        with auth._db() as db:
            db.execute('UPDATE users SET ' + ','.join(sets) + ' WHERE id=?', args + [user['id']])
    return get_profile(user)


def has_analytics_consent(user):
    with auth._db() as db:
        row = db.execute('SELECT analytics_consent FROM users WHERE id=?', (user['id'],)).fetchone()
    return bool(row and row['analytics_consent'])


# ---------------------------------------------------------------- application
PAN_FULL = re.compile(r'(?<![A-Za-z0-9])[A-Za-z]{5}\d{4}[A-Za-z](?![A-Za-z0-9])')
DETAIL_TEXT = {'name': 80, 'dob': 10, 'email': 254, 'state': 40, 'city': 60, 'education': 40, 'ifsc': 11}
SLOTS = ('PAN', 'AADHAAR_FRONT', 'AADHAAR_BACK', 'CLASS10', 'BANK', 'SELFIE')
CODE = re.compile(r'^[A-Za-z][A-Za-z0-9_\-]{0,39}$')
LABEL = re.compile(r'^[A-Za-z0-9 _\-]{1,40}$')
MAX_PROGRESS = 12_000


def clean_details(value, stored=None):
    if not isinstance(value, dict):
        raise PortalError(400, 'invalid_details')
    out = {}
    for k, v in value.items():
        if k in DETAIL_TEXT:
            if v is None or v == '':
                continue
            if not isinstance(v, str) or len(v) > DETAIL_TEXT[k]:
                raise PortalError(400, 'invalid_details', field=k)
            out[k] = v.strip()
        elif k == 'mobile':
            if v is None or v == '':
                continue
            if isinstance(v, str) and '•' in v:  # the masked copy we returned: keep what is stored
                if stored and stored.get('mobile'):
                    out['mobile'] = stored['mobile']
                continue
            out['mobile'] = _clean_mobile(v)
        elif k in ('aadhaar4', 'acct_last4'):
            if v is None or v == '':
                continue
            if not isinstance(v, (str, int)) or isinstance(v, bool) or not re.fullmatch(r'\d{1,4}', str(v)):
                raise PortalError(400, 'identifier_too_long', field=k)
            out[k] = str(v)
        elif k == 'pan_last4':
            if v is None or v == '':
                continue
            if not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9]{1,4}', v):
                raise PortalError(400, 'identifier_too_long', field=k)
            out[k] = v.upper()
        elif k == 'language':
            if v not in ('en', 'hi'):
                raise PortalError(400, 'invalid_language')
            out[k] = v
        # unknown keys are dropped
    for k, v in out.items():  # defence in depth: no full PAN, 12-digit Aadhaar or long account number anywhere
        if k in ('mobile', 'email'):  # an email such as 9876543210@gmail.com is legitimate
            continue
        if PAN_FULL.search(v) or guard.NUMBER_TEXT.search(v):  # also numbers written with hyphens or dots
            raise PortalError(400, 'identifier_not_accepted', field=k)
    return out


def _int(v, lo, hi, field):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
        raise PortalError(400, 'invalid_progress', field=field)
    return int(v)


def _code(v, field, n=40):
    if not isinstance(v, str) or not CODE.fullmatch(v) or len(v) > n:
        raise PortalError(400, 'invalid_progress', field=field)
    return v


def _label(v, field):
    if not isinstance(v, str) or not LABEL.fullmatch(v):
        raise PortalError(400, 'invalid_progress', field=field)
    return v


def clean_progress(value):
    if not isinstance(value, dict):
        raise PortalError(400, 'invalid_progress')
    out = {}
    if 'stage' in value and value['stage'] is not None:
        out['stage'] = _label(value['stage'], 'stage')
    if 'dpage' in value and value['dpage'] is not None:
        out['dpage'] = _int(value['dpage'], 0, 10, 'dpage')
    if 'doc_index' in value and value['doc_index'] is not None:
        out['doc_index'] = _int(value['doc_index'], 0, 10, 'doc_index')
    r = value.get('resume')
    if isinstance(r, dict):
        res = {}
        for k in ('stage', 'milestone', 'action'):
            if r.get(k) is not None:
                res[k] = _label(r[k], 'resume.' + k)
        for k in ('page', 'doc'):
            if r.get(k) is not None:
                res[k] = _int(r[k], 0, 20, 'resume.' + k) if k == 'page' else _code(r[k], 'resume.doc', 24)
        if r.get('t') is not None:
            res['t'] = _int(r['t'], 0, 4_102_444_800_000, 'resume.t')
        out['resume'] = res
    d = value.get('documents')
    if isinstance(d, dict):
        docs = {}
        for slot, v in list(d.items())[:8]:
            if not isinstance(v, dict) or not isinstance(slot, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,23}', slot):
                raise PortalError(400, 'invalid_progress', field='documents')
            item = {'status': _label(v.get('status', 'not uploaded'), 'documents.status')[:20],
                    'version': _int(v.get('version', 1), 1, 100, 'documents.version')}
            issues = v.get('issues', [])
            if not isinstance(issues, list) or len(issues) > 8:
                raise PortalError(400, 'invalid_progress', field='documents.issues')
            item['issues'] = [_code(i, 'documents.issues') for i in issues]
            docs[slot] = item
        out['documents'] = docs
    if 'decision' in value:
        if value['decision'] not in (None, 'APPROVE', 'REJECT', 'NEEDS_MORE_INFO', 'HUMAN_REVIEW'):
            raise PortalError(400, 'invalid_progress', field='decision')
        out['decision'] = value['decision']
    if value.get('round') is not None:
        out['round'] = _int(value['round'], 0, 20, 'round')
    t = value.get('training')
    if isinstance(t, dict):
        mods = t.get('modules', [])
        if not isinstance(mods, list) or len(mods) > 20:
            raise PortalError(400, 'invalid_progress', field='training')
        out['training'] = {'modules': [_code(m, 'training.modules') for m in mods],
                           'hours': _int(t.get('hours', 0), 0, 15, 'training.hours')}
    e = value.get('exam')
    if isinstance(e, dict):
        ex = {}
        if e.get('score') is not None:
            ex['score'] = _int(e['score'], 0, 100, 'exam.score')
        if e.get('passed') is not None:
            if not isinstance(e['passed'], bool):
                raise PortalError(400, 'invalid_progress', field='exam.passed')
            ex['passed'] = e['passed']
        if e.get('attempts') is not None:
            ex['attempts'] = _int(e['attempts'], 0, 50, 'exam.attempts')
        if e.get('retake_at') is not None:
            ex['retake_at'] = _int(e['retake_at'], 0, 4_102_444_800_000, 'exam.retake_at')
        out['exam'] = ex
    c = value.get('certificate')
    if isinstance(c, dict):
        cert = {}
        if c.get('code') is not None:
            cert['code'] = _code(c['code'], 'certificate.code')
        if c.get('seal') is not None:
            cert['seal'] = _code(c['seal'], 'certificate.seal')
        if c.get('issued_at') is not None:
            cert['issued_at'] = _int(c['issued_at'], 0, 4_102_444_800_000, 'certificate.issued_at')
        out['certificate'] = cert
    if len(json.dumps(out, separators=(',', ':'))) > MAX_PROGRESS:
        raise PortalError(400, 'progress_too_large')
    return out


SUBMITTED_STAGES = ('verification', 'training', 'exam', 'certificate')


def is_submitted(progress):
    """Submitted: the engine reported a decision, or the journey reached verification or later."""
    return progress.get('decision') is not None or str(progress.get('stage') or '').strip().lower() in SUBMITTED_STAGES


POST_VERIFICATION_STAGES = ('training', 'exam', 'certificate')


def _progress_round(progress):
    r = progress.get('round')
    return int(r) if isinstance(r, int) and not isinstance(r, bool) else 0


def past_verification(progress):
    """True when the progress claims anything after verification: a later stage, training, an exam or a certificate."""
    if str(progress.get('stage') or '').strip().lower() in POST_VERIFICATION_STAGES:
        return True
    if (progress.get('training') or {}).get('modules'):
        return True
    ex = progress.get('exam') or {}
    if ex.get('score') is not None or ex.get('passed') is not None:
        return True
    return bool((progress.get('certificate') or {}).get('code'))


def latest_review(db, applicant_id):
    """The latest reviewer decision row for this applicant, or None (also before the reviews table exists)."""
    try:
        return db.execute('SELECT action,round FROM reviews WHERE applicant_id=? ORDER BY id DESC LIMIT 1',
                          (applicant_id,)).fetchone()
    except sqlite3.OperationalError:
        return None


def review_hold(progress, review, human_round):
    """Why the applicant may not go past verification, or None. Reviewer decisions are authoritative:
    - 'rejected': the latest reviewer decision is reject (only an appeal overturn, which records an approve, lifts it);
    - 'awaiting_review': a round was routed to a person (HUMAN_REVIEW) and no reviewer has approved that round or later,
      unless a reviewer asked for more information and the applicant has since sent a new round."""
    if review is not None and review['action'] == 'reject':
        return 'rejected'
    if human_round is None:
        return None
    if review is not None and review['round'] >= human_round:
        if review['action'] == 'approve':
            return None
        if review['action'] == 'more_info' and _progress_round(progress) > review['round']:
            return None
    return 'awaiting_review'


def derive_status(progress, review=None, human_round=None):
    hold = review_hold(progress, review, human_round)
    current = review is not None and review['round'] >= _progress_round(progress)
    if hold == 'rejected':
        return 'REJECTED'
    if current and review['action'] == 'more_info':
        return 'NEEDS_MORE_INFO'  # a reviewer asked for more and the applicant has not sent a new round yet
    if hold == 'awaiting_review':
        return 'IN_REVIEW'
    if progress.get('certificate', {}).get('code'):
        return 'CERTIFIED'
    if current and review['action'] == 'approve' and progress.get('decision') in ('APPROVE', 'HUMAN_REVIEW'):
        return 'VERIFIED'
    d = progress.get('decision')
    if d == 'REJECT':
        return 'REJECTED'
    if d == 'HUMAN_REVIEW':
        return 'IN_REVIEW'
    if d == 'NEEDS_MORE_INFO':
        return 'NEEDS_MORE_INFO'
    if d == 'APPROVE':
        return 'VERIFIED'
    return 'IN_PROGRESS' if progress.get('stage') not in (None, 'Before you start') else 'DRAFT'


def refresh_status(db, user_id):
    """Re-derive the stored status after a reviewer acts, so the queue and the applicant see the decision at once."""
    row = db.execute('SELECT progress,human_round FROM applications WHERE user_id=?', (user_id,)).fetchone()
    if row:
        status = derive_status(json.loads(row['progress'] or '{}'), latest_review(db, user_id), row['human_round'])
        db.execute('UPDATE applications SET status=? WHERE user_id=?', (status, user_id))


def _public_app(row):
    details = json.loads(row['details'])
    if details.get('mobile'):
        details['mobile'] = _mask_mobile(details['mobile'])
    return {'reference': row['reference'], 'version': row['version'], 'status': row['status'], 'details': details,
            'progress': json.loads(row['progress']), 'updated_at': row['updated_at']}


def _new_reference():
    return 'PN-%d-%05d' % (time.gmtime().tm_year, secrets.randbelow(100000))


def _with_review(db, row):
    """The applicant's view of the latest reviewer decision (never the note or who decided)."""
    import reviewer  # late import: reviewer builds on this module
    out = _public_app(row)
    out['review'] = reviewer.applicant_review(db, row['user_id'])
    return out


def create_application(user):
    _applicant(user)
    init()
    now = int(time.time())
    import reviewer
    reviewer.init()
    with auth._db() as db:
        for _ in range(30):
            row = db.execute('SELECT * FROM applications WHERE user_id=?', (user['id'],)).fetchone()
            if row:
                return _with_review(db, row)
            try:
                db.execute('INSERT INTO applications(user_id,reference,created_at,updated_at) VALUES(?,?,?,?)',
                           (user['id'], _new_reference(), now, now))
            except sqlite3.IntegrityError:
                continue  # reference collision (or a concurrent create): look again
        raise PortalError(503, 'reference_unavailable')


def get_application(user):
    _applicant(user)
    init()
    import reviewer
    reviewer.init()
    with auth._db() as db:
        row = db.execute('SELECT * FROM applications WHERE user_id=?', (user['id'],)).fetchone()
        if not row:
            raise PortalError(404, 'no_application')
        return _with_review(db, row)


def put_application(user, body):
    _applicant(user)
    init()
    version = body.get('version')
    if isinstance(version, bool) or not isinstance(version, int):
        raise PortalError(400, 'version_required')
    with auth._db() as db:
        row = db.execute('SELECT * FROM applications WHERE user_id=?', (user['id'],)).fetchone()
        if not row:
            raise PortalError(404, 'no_application')
        stored_details = json.loads(row['details'])
        details = clean_details(body['details'], stored_details) if 'details' in body else stored_details
        old_progress = json.loads(row['progress'])
        progress = clean_progress(body['progress']) if 'progress' in body else old_progress
        human_round = row['human_round']
        if progress.get('decision') == 'HUMAN_REVIEW':
            human_round = max(-1 if human_round is None else human_round, _progress_round(progress))
        review = latest_review(db, user['id'])
        hold = review_hold(progress, review, human_round)
        if hold and past_verification(progress):
            # the server, not the applicant's browser, decides whether training, the exam or a certificate may start
            raise PortalError(409, 'review_hold', reason=hold)
        now = int(time.time())
        submitted_at, last_submitted = row['submitted_at'], row['last_submitted_at']
        if is_submitted(progress):
            submitted_at = submitted_at or now
            # a new round or a new engine decision is a (re)submission; other saves leave the time alone
            if not last_submitted or (progress.get('round'), progress.get('decision')) != (old_progress.get('round'), old_progress.get('decision')):
                last_submitted = now
        changed = db.execute(
            'UPDATE applications SET version=version+1,details=?,progress=?,status=?,updated_at=?,submitted_at=?,last_submitted_at=?,human_round=? '
            'WHERE user_id=? AND version=?',
            (json.dumps(details, separators=(',', ':')), json.dumps(progress, separators=(',', ':')),
             derive_status(progress, review, human_round), now, submitted_at, last_submitted, human_round, user['id'], version)).rowcount
        if not changed:
            current = db.execute('SELECT * FROM applications WHERE user_id=?', (user['id'],)).fetchone()
            raise PortalError(409, 'stale', application=_with_review(db, current))
    return {'version': version + 1, 'updated_at': now}


# ---------------------------------------------------------------- events, timeline, notifications
# key -> (actor, notification type or None, action route). Applicants may send only the "you"/"policynest"
# keys below; reviewer keys are written by the server when a person decides.
EVENTS = {
    'application_started': ('you', None, ''),
    'resumed': ('you', None, ''),
    'saved_exit': ('you', None, ''),
    'details_saved': ('you', 'success', '#documents'),
    'doc_uploaded': ('you', None, ''),
    'doc_verified': ('policynest', 'success', '#documents'),
    'doc_needs_fix': ('policynest', 'action', '#documents'),
    'doc_refused': ('policynest', 'action', '#documents'),
    'docs_complete': ('policynest', 'success', '#apply'),
    'verification_passed': ('policynest', 'success', '#apply'),
    'verification_followup': ('policynest', 'action', '#documents'),
    'verification_review': ('policynest', 'info', '#history'),
    'fixes_received': ('you', 'info', '#history'),
    'training_module_done': ('you', 'success', '#training'),
    'training_complete': ('you', 'success', '#exam'),
    'exam_passed': ('policynest', 'success', '#certificate'),
    'exam_failed': ('policynest', 'action', '#exam'),
    'certificate_issued': ('policynest', 'success', '#certificate'),
    'dispute_submitted': ('you', 'info', '#history'),
}
REVIEWER_EVENTS = {
    'review_approved': ('reviewer', 'success', '#home'),
    'review_more_info': ('reviewer', 'action', '#documents'),
    'review_rejected': ('reviewer', 'info', '#history'),
    'review_in_progress': ('reviewer', 'info', '#history'),
    'appeal_update': ('reviewer', 'info', '#history'),
    # reviewer workspace (reviewer.py): keys only; the applicant reads the message through GET /api/applications/me
    'review.approved': ('reviewer', 'success', '#home'),
    'review.rejected': ('reviewer', 'info', '#history'),
    'review.more_info': ('reviewer', 'action', '#documents'),
    'appeal.upheld': ('reviewer', 'info', '#history'),
    'appeal.overturned': ('reviewer', 'success', '#home'),
    'appeal.more_info': ('reviewer', 'action', '#history'),
}
TIMELINE_KEEP = 500
NOTIFICATIONS_KEEP = 200
APPLICANT_ACTORS = ('you', 'policynest', 'reviewer')


def record(db, user_id, key, stage='', doc='', t=None, defs=None):
    """Write one timeline row (and its notification, when the event has one). Returns True if new."""
    actor, ntype, action = (defs or {**EVENTS, **REVIEWER_EVENTS})[key]
    now = int(time.time())
    # The client time is the idempotency key, so it is stored as sent (a retried offline batch of any age
    # stays idempotent); it is clamped only when shown (see timeline()). No usable time: server time.
    t = int(t) if isinstance(t, (int, float)) and not isinstance(t, bool) and t == t and t > 0 else int(time.time() * 1000)
    shown = t
    try:
        cur = db.execute('INSERT INTO timeline(user_id,t,actor,key,stage,doc) VALUES(?,?,?,?,?,?)',
                         (user_id, shown, actor, key, stage or '', doc or ''))
    except sqlite3.IntegrityError:
        return False  # the same event again (a retried offline queue): already recorded
    if ntype:
        ref = db.execute('SELECT reference FROM applications WHERE user_id=?', (user_id,)).fetchone()
        db.execute('INSERT INTO notifications(user_id,type,key,stage,doc,action,read,created_at,reference) VALUES(?,?,?,?,?,?,0,?,?)',
                   (user_id, ntype, key, stage or '', doc or '', action, now, ref['reference'] if ref else ''))
        db.execute('DELETE FROM notifications WHERE user_id=? AND id NOT IN (SELECT id FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT ?)',
                   (user_id, user_id, NOTIFICATIONS_KEEP))
    db.execute('DELETE FROM timeline WHERE user_id=? AND id NOT IN (SELECT id FROM timeline WHERE user_id=? ORDER BY id DESC LIMIT ?)',
               (user_id, user_id, TIMELINE_KEEP))
    return True


def record_reviewer_event(db, applicant_id, key):
    """Called by workflows when a person decides. Carries no rationale, note or evidence."""
    init()
    if key in REVIEWER_EVENTS:
        record(db, applicant_id, key)


def post_events(user, body):
    _applicant(user)
    init()
    events = body.get('events')
    if not isinstance(events, list) or not 1 <= len(events) <= 20:
        raise PortalError(400, 'invalid_events')
    accepted = 0
    with auth._db() as db:
        if not db.execute('SELECT 1 FROM applications WHERE user_id=?', (user['id'],)).fetchone():
            raise PortalError(404, 'no_application')
        for e in events:
            if not isinstance(e, dict) or e.get('key') not in EVENTS:
                continue
            stage = e.get('stage') or ''
            doc = e.get('doc') or ''
            if (stage and not (isinstance(stage, str) and LABEL.fullmatch(stage))) or \
                    (doc and not (isinstance(doc, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,23}', doc))):
                continue
            if record(db, user['id'], e['key'], stage, doc, e.get('t'), EVENTS):
                accepted += 1
    return {'accepted': accepted}


def timeline(user, params):
    _applicant(user)
    init()
    sql, args = 'SELECT id,t,actor,key,stage,doc FROM timeline WHERE user_id=?', [user['id']]
    actor = (params.get('actor') or [''])[0]
    if actor in APPLICANT_ACTORS:
        sql += ' AND actor=?'; args.append(actor)
    stage = (params.get('stage') or [''])[0]
    if stage and LABEL.fullmatch(stage):
        sql += ' AND stage=?'; args.append(stage)
    sql += ' ORDER BY t DESC,id DESC LIMIT ?'
    args.append(_limit(params, 100, 200))
    allowed = set(EVENTS) | set(REVIEWER_EVENTS)
    with auth._db() as db:
        rows = [dict(r) for r in db.execute(sql, args)]
    items = []
    for r in rows:
        if r['key'] not in allowed or r['actor'] not in APPLICANT_ACTORS:
            continue  # nothing outside the applicant-safe list is ever shown
        item = {'id': r['id'], 't': min(max(r['t'], (int(time.time()) - 86400 * 30) * 1000), (int(time.time()) + 3600) * 1000), 'actor': r['actor'], 'key': r['key']}
        if r['stage']:
            item['stage'] = r['stage']
        if r['doc']:
            item['doc'] = r['doc']
        items.append(item)
    return {'items': items}


def _limit(params, default, maximum):
    try:
        n = int((params.get('limit') or [default])[0])
    except (TypeError, ValueError):
        n = default
    return max(1, min(maximum, n))


def list_notifications(user, params):
    _applicant(user)
    init()
    with auth._db() as db:
        rows = db.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT ?',
                          (user['id'], _limit(params, 50, 100))).fetchall()
        unread = db.execute('SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0', (user['id'],)).fetchone()[0]
    items = []
    for r in rows:
        item = {'id': r['id'], 'type': r['type'], 'key': r['key'], 'read': bool(r['read']),
                'created_at': r['created_at'], 'reference': r['reference']}
        for k in ('stage', 'doc', 'action'):
            if r[k]:
                item[k] = r[k]
        items.append(item)
    return {'items': items, 'unread': unread}


def mark_read(user, body):
    _applicant(user)
    init()
    with auth._db() as db:
        if body.get('all') is True:
            db.execute('UPDATE notifications SET read=1 WHERE user_id=?', (user['id'],))
        else:
            ids = body.get('ids')
            if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
                raise PortalError(400, 'invalid_ids')
            db.execute('UPDATE notifications SET read=1 WHERE user_id=? AND id IN (%s)' % ','.join('?' * len(ids)),
                       [user['id']] + ids)
        unread = db.execute('SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0', (user['id'],)).fetchone()[0]
    return {'unread': unread}


# ---------------------------------------------------------------- route table
ROUTES = {
    ('GET', '/api/me'): lambda u, b, p: (200, get_profile(u)),
    ('PATCH', '/api/me'): lambda u, b, p: (200, patch_profile(u, b)),
    ('POST', '/api/applications/me'): lambda u, b, p: (200, {'application': create_application(u)}),
    ('GET', '/api/applications/me'): lambda u, b, p: (200, {'application': get_application(u)}),
    ('PUT', '/api/applications/me'): lambda u, b, p: (200, put_application(u, b)),
    ('POST', '/api/applications/me/events'): lambda u, b, p: (200, post_events(u, b)),
    ('GET', '/api/applications/me/timeline'): lambda u, b, p: (200, timeline(u, p)),
    ('GET', '/api/notifications'): lambda u, b, p: (200, list_notifications(u, p)),
    ('POST', '/api/notifications/read'): lambda u, b, p: (200, mark_read(u, b)),
}
