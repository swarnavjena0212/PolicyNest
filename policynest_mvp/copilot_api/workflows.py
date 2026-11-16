"""Persistent assignments and appeals. Policies are enforced on fetched database resources."""
import json
import secrets
import time
import re
import auth
import policy
import portal


REVIEW_EVENT={'NEEDS_MORE_INFO':'review_more_info','KYC_APPROVED':'review_approved','REJECTED':'review_rejected','HUMAN_REVIEW':'review_in_progress'}


def init():
    portal.init()
    with auth._db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, applicant_id INTEGER NOT NULL UNIQUE, reviewer_id INTEGER, status TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS appeals (id TEXT PRIMARY KEY, case_id TEXT NOT NULL, explanation TEXT NOT NULL, status TEXT NOT NULL, response TEXT, created INTEGER NOT NULL, resolved INTEGER)')
        db.execute('CREATE TABLE IF NOT EXISTS audit_events (id INTEGER PRIMARY KEY, case_id TEXT NOT NULL, actor_id INTEGER NOT NULL, action TEXT NOT NULL, occurred INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS review_notes (id INTEGER PRIMARY KEY,case_id TEXT NOT NULL,reviewer_id INTEGER NOT NULL,note TEXT NOT NULL,created INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS assignment_history (id INTEGER PRIMARY KEY,case_id TEXT NOT NULL,previous_reviewer INTEGER,new_reviewer INTEGER NOT NULL,actor_id INTEGER NOT NULL,occurred INTEGER NOT NULL)')
        if 'evidence' not in {r[1] for r in db.execute('PRAGMA table_info(appeals)')}:
            db.execute("ALTER TABLE appeals ADD COLUMN evidence TEXT NOT NULL DEFAULT '{}'")
        db.execute("CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT,'immutable'); END")
        db.execute("CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT,'immutable'); END")


def _audit(db, case, actor, action):
    db.execute('INSERT INTO audit_events(case_id,actor_id,action,occurred) VALUES(?,?,?,?)', (case, actor['id'], action, int(time.time())))


def own_case(actor):
    if actor['role'] != 'applicant': raise ValueError('forbidden')
    init()
    with auth._db() as db:
        db.execute('INSERT OR IGNORE INTO cases VALUES(?,?,NULL,?)', ('PN-' + secrets.token_hex(8), actor['id'], 'DRAFT'))
        return dict(db.execute('SELECT * FROM cases WHERE applicant_id=?', (actor['id'],)).fetchone())


def assign(actor, case_id, reviewer_id):
    if actor['role'] != 'admin': raise ValueError('forbidden')
    init()
    with auth._db() as db:
        reviewer = db.execute("SELECT id FROM users WHERE id=? AND role='reviewer'", (reviewer_id,)).fetchone()
        case = db.execute('SELECT id,reviewer_id FROM cases WHERE id=?', (case_id,)).fetchone()
        if not reviewer or not case: raise ValueError('not_found')
        db.execute('UPDATE cases SET reviewer_id=? WHERE id=?', (reviewer_id, case_id))
        db.execute('INSERT INTO assignment_history(case_id,previous_reviewer,new_reviewer,actor_id,occurred) VALUES(?,?,?,?,?)',(case_id,case['reviewer_id'],reviewer_id,actor['id'],int(time.time())))
        _audit(db, case_id, actor, 'REVIEWER_ASSIGNED')


def queue(actor):
    if actor['role'] != 'reviewer': raise ValueError('forbidden')
    init()
    with auth._db() as db:
        return [dict(r) for r in db.execute('SELECT id,status FROM cases WHERE reviewer_id=?', (actor['id'],))]


def appeals(actor, case_id, explanation=None):
    init()
    with auth._db() as db:
        row = db.execute('SELECT * FROM cases WHERE id=?', (case_id,)).fetchone()
        if not row: raise ValueError('forbidden')
        resource = dict(row)
        action = 'appeal.create' if explanation is not None else ('application.read' if actor['role']=='applicant' else 'appeal.read')
        if not policy.allows(actor, action, resource): raise ValueError('forbidden')
        if explanation is not None:
            if not isinstance(explanation, str) or not 10 <= len(explanation) <= 1500: raise ValueError('invalid_explanation')
            if re.search(r'\b\d{4}\s?\d{4}\s?\d{4}\b|\b[A-Za-z]{5}\d{4}[A-Za-z]\b|\b\d{9,18}\b', explanation): raise ValueError('do_not_include_identity_numbers')
            if db.execute("SELECT 1 FROM appeals WHERE case_id=? AND status='OPEN'", (case_id,)).fetchone(): raise ValueError('appeal_open')
            reference = 'AP-' + secrets.token_hex(8)
            stored=db.execute('SELECT snapshot FROM application_snapshots WHERE user_id=?',(actor['id'],)).fetchone()
            evidence=stored['snapshot'] if stored else _portal_evidence(db, actor['id'])
            db.execute('INSERT INTO appeals(id,case_id,explanation,status,created,evidence) VALUES(?,?,?,?,?,?)', (reference, case_id, explanation, 'OPEN', int(time.time()),evidence))
            _audit(db, case_id, actor, 'APPEAL_SUBMITTED')
            return {'reference': reference, 'status': 'OPEN'}
        return [dict(r) for r in db.execute('SELECT id,status,explanation,response,created,resolved,evidence FROM appeals WHERE case_id=?', (case_id,))]


def _portal_evidence(db, user_id):
    """Allow-listed evidence from the portal application: decision, round and per-slot statuses and issue codes only."""
    row = db.execute('SELECT progress FROM applications WHERE user_id=?', (user_id,)).fetchone()
    if not row: return '{}'
    p = json.loads(row['progress'] or '{}')
    docs = {k: {'status': v.get('status'), 'issues': v.get('issues', [])} for k, v in (p.get('documents') or {}).items() if isinstance(v, dict)}
    return json.dumps({'decision': p.get('decision'), 'round': p.get('round'), 'documents': docs}, separators=(',', ':'))


def resolve(actor, reference, status, response):
    init()
    if status not in ('RESOLVED','MORE_INFO','ESCALATED') or not isinstance(response,str) or not 10 <= len(response) <= 1500: raise ValueError('invalid_resolution')
    if re.search(r'\b(fraud\w*|fake|forged|tamper\w*|suspicious)\b|\b\d{4}\s?\d{4}\s?\d{4}\b|\b[A-Za-z]{5}\d{4}[A-Za-z]\b|\b\d{9,18}\b',response,re.I):raise ValueError('unsafe_public_response')
    with auth._db() as db:
        row = db.execute('SELECT c.* FROM cases c JOIN appeals a ON a.case_id=c.id WHERE a.id=?', (reference,)).fetchone()
        if not row or not policy.allows(actor, 'appeal.resolve', dict(row)): raise ValueError('forbidden')
        current=db.execute('SELECT status FROM appeals WHERE id=?',(reference,)).fetchone()
        if current['status']!='OPEN':raise ValueError('already_resolved')  # any outcome is final: no second resolution
        db.execute('UPDATE appeals SET status=?,response=?,resolved=? WHERE id=?', (status,response,int(time.time()),reference))
        _audit(db,row['id'],actor,'APPEAL_'+status)
        portal.record_reviewer_event(db,row['applicant_id'],'appeal_update')  # key only: no response text
        return {'reference':reference,'status':status}


def case_detail(actor,case_id):
    init()
    with auth._db() as db:
        row=db.execute('SELECT * FROM cases WHERE id=?',(case_id,)).fetchone()
        if not row or not policy.allows(actor,'application.read',dict(row)):raise ValueError('forbidden')
        snapshot=db.execute('SELECT snapshot,updated_at FROM application_snapshots WHERE user_id=?',(row['applicant_id'],)).fetchone()
        result=dict(row)
        result['progress']=json.loads(snapshot['snapshot']) if snapshot else None
        result['appeals']=appeals(actor,case_id)
        if actor['role']=='reviewer':
            result['notes']=[dict(r) for r in db.execute('SELECT note,created FROM review_notes WHERE case_id=?',(case_id,))]
            result['audit']=[dict(r) for r in db.execute('SELECT action,occurred FROM audit_events WHERE case_id=? ORDER BY id',(case_id,))]
        return result


def review(actor,case_id,status,note,rule):
    if status not in ('NEEDS_MORE_INFO','KYC_APPROVED','REJECTED','HUMAN_REVIEW') or not isinstance(note,str) or not 10<=len(note)<=1500 or not isinstance(rule,str) or not re.fullmatch(r'§[0-9.]+',rule):raise ValueError('invalid_review')
    init()
    with auth._db() as db:
        row=db.execute('SELECT * FROM cases WHERE id=?',(case_id,)).fetchone()
        if not row or not policy.allows(actor,'review.decide',dict(row)):raise ValueError('forbidden')
        db.execute('UPDATE cases SET status=? WHERE id=?',(status,case_id))
        db.execute('INSERT INTO review_notes(case_id,reviewer_id,note,created) VALUES(?,?,?,?)',(case_id,actor['id'],rule+' '+note,int(time.time())))
        _audit(db,case_id,actor,'REVIEW_'+status)
        portal.record_reviewer_event(db,row['applicant_id'],REVIEW_EVENT[status])  # key only: no note, rule or evidence
    return {'status':status,'rule':rule}
