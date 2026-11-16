"""Reviewer workspace tests: reviewer sign-up with admin approval, the admin invite, the review queue, decisions,
the applicant's view of a review, notifications and timeline, appeals and resubmission. Standard library only.
Run: python -m unittest test_reviewer -v"""
import json
import os
import sqlite3
import unittest

import auth
import reviewer
import test_portal

PortalBase = test_portal.PortalBase
PW = test_portal.PW
INVITE = 'invite-code-for-tests-0123456789'
REVIEWER_GETS = ['/api/reviewer/applications', '/api/reviewer/applications?tab=all', '/api/reviewer/applications/PN-2026-00001',
                 '/api/reviewer/appeals', '/api/admin/reviewer-requests']
REVIEWER_POSTS = [('/api/reviewer/applications/PN-2026-00001/decision', {'action': 'approve'}),
                  ('/api/reviewer/appeals/AP-0123456789abcdef/resolve', {'outcome': 'uphold', 'message': 'We checked it.'}),
                  ('/api/admin/reviewer-requests/decide', {'id': 'RR-0123456789abcdef', 'decision': 'approve'})]


class Base(PortalBase):
    def setUp(self):
        super().setUp()
        reviewer.reset_limits()
        os.environ['ADMIN_INVITE_CODE'] = INVITE

    def tearDown(self):
        os.environ.pop('ADMIN_INVITE_CODE', None)

    def register(self, account_type=None, invite=None, name='Staff Person'):
        c = self.client(register=False)
        body = {'email': c.email, 'password': PW, 'display_name': name}
        if account_type:
            body['account_type'] = account_type
        if invite is not None:
            body['invite_code'] = invite
        c.code, c.out = c.call('POST', '/api/auth/register', body)
        if c.code == 201:
            c.id = c.out['user']['id']
        return c

    def admin(self):
        a = self.register('admin', INVITE, 'Admin Person')
        self.assertEqual(a.code, 201, a.out)
        return a

    def request_id(self, admin, user_id):
        items = admin.call('GET', '/api/admin/reviewer-requests')[1]['items']
        return [i['id'] for i in items if i['user_id'] == user_id][0]

    def reviewer(self, admin=None, name='Riya Reviewer'):
        admin = admin or self.admin()
        r = self.register('reviewer', name=name)
        self.assertEqual(r.code, 201)
        code, _ = admin.call('POST', '/api/admin/reviewer-requests/decide', {'id': self.request_id(admin, r.id), 'decision': 'approve'})
        self.assertEqual(code, 200)
        self.assertEqual(r.call('POST', '/api/auth/refresh', {})[0], 200)
        return r

    def applicant(self, name='Asha Rao', decision='HUMAN_REVIEW', round_=1, stage='verification', issues=('NAME_SURNAME_CHANGE',)):
        c = self.client()
        c.call('PATCH', '/api/me', {'display_name': name})
        code, out = c.call('POST', '/api/applications/me', {})
        self.assertEqual(code, 200)
        c.ref, c.version = out['application']['reference'], out['application']['version']
        if decision is not None or stage != 'details':
            self.submit(c, decision, round_, stage, issues)
        return c

    def submit(self, c, decision, round_, stage='verification', issues=('NAME_SURNAME_CHANGE',)):
        progress = {'stage': stage, 'round': round_, 'decision': decision,
                    'documents': {'PAN': {'status': 'ok', 'version': round_ or 1, 'issues': list(issues)},
                                  'SELFIE': {'status': 'ok', 'version': 1, 'issues': []}}}
        code, out = c.call('PUT', '/api/applications/me', {'version': c.version, 'progress': progress,
                                                           'details': {'name': 'ASHA RAO', 'state': 'Karnataka', 'pan_last4': '234F',
                                                                       'aadhaar4': '1234', 'ifsc': 'SBIN0001234', 'email': 'x@example.test'}})
        self.assertEqual(code, 200, out)
        c.version = out['version']

    def queue(self, staff, tab='needs_review', q=''):
        code, out = staff.call('GET', '/api/reviewer/applications?tab=%s%s' % (tab, '&q=' + q if q else ''))
        self.assertEqual(code, 200, out)
        return out


class Roles(Base):
    def test_registration_defaults_to_applicant_and_never_grants_staff_roles(self):
        self.assertEqual(self.register().out['user']['role'], 'applicant')
        self.assertEqual(self.register('applicant').out['user']['role'], 'applicant')
        r = self.register('reviewer')
        self.assertEqual((r.code, r.out['user']['role']), (201, 'reviewer_pending'))
        for bad in ('ops', 'reviewer_pending', 'superuser', 7):
            self.assertEqual(self.register(bad).code, 400, bad)
        c = self.register(None)
        self.assertEqual(c.call('PATCH', '/api/me', {'role': 'admin'})[1]['user']['role'], 'applicant')

    def test_reviewer_pending_has_no_queue_access_only_its_profile(self):
        r = self.register('reviewer')
        code, out = r.call('GET', '/api/me')
        self.assertEqual((code, out['user']['role'], out['user']['reviewer_request']['status']), (200, 'reviewer_pending', 'pending'))
        self.assertEqual(r.call('PATCH', '/api/me', {'language': 'hi'})[0], 200)
        for path in REVIEWER_GETS:
            self.assertEqual(r.call('GET', path)[0], 403, path)
        for path, body in REVIEWER_POSTS:
            self.assertEqual(r.call('POST', path, body)[0], 403, path)
        for method, path in (('GET', '/api/applications/me'), ('POST', '/api/applications/me'), ('GET', '/api/notifications'),
                             ('GET', '/api/reviewer/queue'), ('GET', '/api/cases/me')):
            self.assertEqual(r.call(method, path, {} if method == 'POST' else None)[0], 403, path)
        self.assertEqual(r.call('POST', '/api/assistant', {'question': 'What next?'})[0], 403)
        self.assertEqual(r.call('POST', '/api/appeals', {'explanation': 'Please look at this again.'})[0], 403)
        self.assertEqual(r.call('POST', '/api/auth/logout', {})[0], 200)

    def test_applicant_gets_403_on_every_reviewer_and_admin_route(self):
        a = self.client()
        for path in REVIEWER_GETS:
            self.assertEqual(a.call('GET', path)[0], 403, path)
        for path, body in REVIEWER_POSTS:
            self.assertEqual(a.call('POST', path, body)[0], 403, path)

    def test_anonymous_gets_401_and_writes_need_the_origin(self):
        for path in REVIEWER_GETS:
            self.assertEqual(self.anon('GET', path)[0], 401, path)
        admin = self.admin()
        self.assertEqual(admin.call('POST', '/api/admin/reviewer-requests/decide', {'id': 'RR-0123456789abcdef', 'decision': 'approve'},
                                    origin='https://evil.example')[0], 403)

    def test_reviewer_cannot_use_admin_routes_and_ops_has_no_queue(self):
        r = self.reviewer()
        self.assertEqual(r.call('GET', '/api/admin/reviewer-requests')[0], 403)
        o = self.client()
        with auth._db() as db:
            db.execute("UPDATE users SET role='ops' WHERE id=?", (o.id,))
        o.call('POST', '/api/auth/login', {'email': o.email, 'password': PW})
        self.assertEqual(o.call('GET', '/api/reviewer/applications')[0], 403)


class AdminApproval(Base):
    def test_approve_is_needed_before_access_and_applies_on_refresh(self):
        admin = self.admin()
        r = self.register('reviewer', name='Ravi Request')
        items = admin.call('GET', '/api/admin/reviewer-requests?status=pending')[1]['items']
        self.assertEqual([(i['user_id'], i['display_name'], i['status']) for i in items], [(r.id, 'Ravi Request', 'pending')])
        self.assertEqual(set(items[0]), {'id', 'user_id', 'display_name', 'email', 'status', 'created_at', 'decided_at'})
        self.assertEqual(r.call('GET', '/api/reviewer/applications')[0], 403)
        code, out = admin.call('POST', '/api/admin/reviewer-requests/decide', {'id': items[0]['id'], 'decision': 'approve', 'note': 'Team lead confirmed.'})
        self.assertEqual((code, out['status']), (200, 'approved'))
        self.assertEqual(r.call('GET', '/api/reviewer/applications')[0], 401)  # the old token carries the old role
        self.assertEqual(r.call('POST', '/api/auth/refresh', {})[1]['user']['role'], 'reviewer')
        self.assertEqual(r.call('GET', '/api/reviewer/applications')[0], 200)
        me = r.call('GET', '/api/me')[1]['user']
        self.assertEqual((me['role'], me['reviewer_request']['status']), ('reviewer', 'approved'))
        self.assertNotIn('Team lead', json.dumps(me))
        self.assertEqual(admin.call('GET', '/api/admin/reviewer-requests')[1]['items'], [])
        self.assertEqual(len(admin.call('GET', '/api/admin/reviewer-requests?status=all')[1]['items']), 1)

    def test_deny_and_double_decide(self):
        admin = self.admin()
        r = self.register('reviewer')
        rid = self.request_id(admin, r.id)
        self.assertEqual(admin.call('POST', '/api/admin/reviewer-requests/decide', {'id': rid, 'decision': 'deny'})[1]['status'], 'denied')
        code, out = admin.call('POST', '/api/admin/reviewer-requests/decide', {'id': rid, 'decision': 'approve'})
        self.assertEqual((code, out['error']), (409, 'already_decided'))
        r.call('POST', '/api/auth/refresh', {})
        me = r.call('GET', '/api/me')[1]['user']
        self.assertEqual((me['role'], me['reviewer_request']['status']), ('reviewer_pending', 'denied'))
        self.assertEqual(r.call('GET', '/api/reviewer/applications')[0], 403)
        r2 = self.register('reviewer')
        rid2 = self.request_id(admin, r2.id)
        admin.call('POST', '/api/admin/reviewer-requests/decide', {'id': rid2, 'decision': 'approve'})
        self.assertEqual(admin.call('POST', '/api/admin/reviewer-requests/decide', {'id': rid2, 'decision': 'deny'})[0], 409)

    def test_decide_validation(self):
        admin = self.admin()
        self.assertEqual(admin.call('POST', '/api/admin/reviewer-requests/decide', {'id': 'RR-0123456789abcdef', 'decision': 'approve'})[0], 404)
        r = self.register('reviewer')
        rid = self.request_id(admin, r.id)
        self.assertEqual(admin.call('POST', '/api/admin/reviewer-requests/decide', {'id': rid, 'decision': 'maybe'})[0], 400)
        self.assertEqual(admin.call('GET', '/api/admin/reviewer-requests?status=weird')[0], 400)
        self.assertEqual(admin.call('POST', '/api/admin/reviewer-requests/decide', {'id': rid, 'decision': 'approve', 'note': 'PAN ABCDE1234F'})[1]['error'], 'id_number')

    def test_decisions_are_audited(self):
        admin = self.admin()
        r = self.register('reviewer')
        admin.call('POST', '/api/admin/reviewer-requests/decide', {'id': self.request_id(admin, r.id), 'decision': 'approve'})
        with auth._db() as db:
            actions = [x[0] for x in db.execute("SELECT action FROM audit_events WHERE case_id=? ORDER BY id", ('USER-%d' % r.id,))]
        self.assertEqual(actions, ['REVIEWER_REQUESTED', 'REVIEWER_APPROVED'])


class AdminInvite(Base):
    def test_right_code_creates_an_admin(self):
        a = self.admin()
        self.assertEqual(a.out['user']['role'], 'admin')
        self.assertEqual(a.call('GET', '/api/admin/reviewer-requests')[0], 200)

    def test_wrong_missing_or_unset_code_is_refused_the_same_way(self):
        wrong = self.register('admin', 'not-the-right-code-at-all')
        missing = self.register('admin')
        os.environ.pop('ADMIN_INVITE_CODE')
        unset = self.register('admin', INVITE)
        os.environ['ADMIN_INVITE_CODE'] = 'short'
        short = self.register('admin', 'short')
        for c in (wrong, missing, unset, short):
            self.assertEqual((c.code, c.out), (403, {'error': 'invite_invalid', 'message': reviewer.INVITE_MESSAGE}))
        with auth._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_events WHERE action='ADMIN_INVITE_REFUSED'").fetchone()[0], 4)

    def test_invite_attempts_are_rate_limited(self):
        codes = [self.register('admin', 'wrong-code-%02d-xxxxxxxx' % i).code for i in range(6)]
        self.assertEqual(codes, [403] * 5 + [429])
        self.assertEqual(self.register('admin', INVITE).code, 429)  # even the right code waits


class Queue(Base):
    def test_filters_counts_and_search(self):
        staff = self.reviewer()
        hr = self.applicant('Asha Rao')
        auto = self.applicant('Bala Kumar', decision='APPROVE', issues=())
        self.applicant('Chitra Draft', decision=None, stage='details')  # not submitted: never listed
        out = self.queue(staff)
        self.assertEqual([i['reference'] for i in out['items']], [hr.ref])
        self.assertEqual(out['counts'], {'needs_review': 1, 'appeals': 0, 'all': 2, 'decided': 0})
        item = out['items'][0]
        self.assertEqual(set(item), {'reference', 'display_name', 'status', 'engine_decision', 'submitted_at', 'updated_at', 'waiting_seconds',
                                     'open_issues', 'documents', 'review', 'open_appeal', 'needs_review', 'round'})
        self.assertEqual((item['display_name'], item['engine_decision'], item['open_issues'], item['documents'], item['review']),
                         ('Asha Rao', 'HUMAN_REVIEW', ['NAME_SURNAME_CHANGE'], {'PAN': 'ok', 'SELFIE': 'ok'}, None))
        self.assertIsInstance(item['submitted_at'], int)
        self.assertGreaterEqual(item['waiting_seconds'], 0)
        self.assertEqual({i['reference'] for i in self.queue(staff, 'all')['items']}, {hr.ref, auto.ref})
        self.assertEqual(self.queue(staff, 'decided')['items'], [])
        self.assertEqual([i['reference'] for i in self.queue(staff, 'all', 'bala')['items']], [auto.ref])
        self.assertEqual([i['reference'] for i in self.queue(staff, 'all', hr.ref.lower())['items']], [hr.ref])
        self.assertEqual(staff.call('GET', '/api/reviewer/applications?tab=bogus')[0], 400)
        staff.call('POST', '/api/reviewer/applications/%s/decision' % hr.ref, {'action': 'approve'})
        out = self.queue(staff, 'decided')
        self.assertEqual([i['reference'] for i in out['items']], [hr.ref])
        self.assertEqual(out['items'][0]['review']['action'], 'approve')
        self.assertEqual(out['counts'], {'needs_review': 0, 'appeals': 0, 'all': 2, 'decided': 1})

    def test_admin_reads_the_queue_and_detail_is_allow_listed(self):
        admin = self.admin()
        a = self.applicant()
        self.assertEqual(len(self.queue(admin)['items']), 1)
        code, d = admin.call('GET', '/api/reviewer/applications/' + a.ref)
        self.assertEqual(code, 200)
        self.assertEqual(d['details'], {'name': 'ASHA RAO', 'state': 'Karnataka', 'pan_last4': '234F', 'aadhaar4': '1234'})
        self.assertNotIn('SBIN0001234', json.dumps(d))
        self.assertNotIn('x@example.test', json.dumps(d))
        self.assertEqual(d['progress']['documents']['PAN'], {'status': 'ok', 'issues': ['NAME_SURNAME_CHANGE'], 'version': 1})
        self.assertEqual((d['progress']['decision'], d['progress']['round']), ('HUMAN_REVIEW', 1))
        self.assertTrue(d['state']['needs_review'])
        self.assertIn('never stored', d['data_notice'])
        for k in ('timeline', 'internal_events', 'appeals', 'reviews'):
            self.assertIsInstance(d[k], list)
        self.assertEqual(admin.call('GET', '/api/reviewer/applications/PN-2026-99999')[0], 404)
        self.assertEqual(admin.call('GET', '/api/reviewer/applications/not-a-reference')[0], 404)


class Decisions(Base):
    def decide(self, staff, ref, body):
        return staff.call('POST', '/api/reviewer/applications/%s/decision' % ref, body)

    def test_validation(self):
        staff = self.reviewer()
        a = self.applicant()
        cases = [({'action': 'maybe'}, 'invalid_action'),
                 ({'action': 'reject'}, 'message_required'),
                 ({'action': 'more_info', 'message': '   '}, 'message_required'),
                 ({'action': 'reject', 'message': 'x' * 601}, 'message_too_long'),
                 ({'action': 'reject', 'message': 7}, 'invalid_message'),
                 ({'action': 'reject', 'message': 'Your PAN ABCDE1234F does not match.'}, 'id_number'),
                 ({'action': 'reject', 'message': 'Aadhaar 1234 5678 9012 is wrong.'}, 'id_number'),
                 ({'action': 'reject', 'message': 'This looks like a fake document.'}, 'unsafe_message'),
                 ({'action': 'reject', 'message': 'Suspicious upload, sorry.'}, 'unsafe_message'),
                 ({'action': 'approve', 'note': 'account 123456789012'}, 'id_number'),
                 ({'action': 'approve', 'note': 'x' * 1501}, 'invalid_note'),
                 ({'action': 'more_info', 'message': 'Please upload again.', 'items': [{'doc': 'PASSPORT', 'issue': 'IMAGE_BLURRY'}]}, 'invalid_items'),
                 ({'action': 'more_info', 'message': 'Please upload again.', 'items': [{'doc': 'PAN', 'issue': 'blurry photo'}]}, 'invalid_items'),
                 ({'action': 'more_info', 'message': 'Please upload again.', 'items': 'PAN'}, 'invalid_items'),
                 ({'action': 'more_info', 'message': 'Please upload again.', 'items': [{'doc': 'PAN', 'issue': 'X'}] * 13}, 'invalid_items')]
        for body, error in cases:
            code, out = self.decide(staff, a.ref, body)
            self.assertEqual((code, out['error']), (400, error), body)
        self.assertEqual(self.decide(staff, 'PN-2026-99999', {'action': 'approve'})[0], 404)
        with auth._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 0)

    def test_not_submitted_is_409(self):
        staff = self.reviewer()
        d = self.applicant(decision=None, stage='details')
        self.assertEqual(self.decide(staff, d.ref, {'action': 'approve'})[1]['error'], 'not_submitted')

    def test_approve_reject_and_more_info_reach_the_applicant_without_the_note(self):
        staff = self.reviewer(name='Secret Reviewer Name')
        a = self.applicant()
        code, out = self.decide(staff, a.ref, {'action': 'more_info', 'message': 'Please upload a clearer selfie.',
                                               'note': 'INTERNAL-NOTE face score borderline',
                                               'items': [{'doc': 'SELFIE', 'issue': 'FACE_MATCH_LOW'}, {'doc': 'SELFIE', 'issue': 'FACE_MATCH_LOW'}]})
        self.assertEqual(code, 200, out)
        self.assertEqual((out['review']['action'], out['review']['items'], out['review']['round']), ('more_info', [{'doc': 'SELFIE', 'issue': 'FACE_MATCH_LOW'}], 1))
        app = a.call('GET', '/api/applications/me')[1]['application']
        self.assertEqual(set(app['review']), {'action', 'message', 'items', 'at', 'round', 'by'})
        self.assertEqual((app['review']['action'], app['review']['message'], app['review']['by']),
                         ('more_info', 'Please upload a clearer selfie.', 'a reviewer'))
        blob = json.dumps(a.call('GET', '/api/applications/me')[1]) + json.dumps(a.call('GET', '/api/notifications')[1]) \
            + json.dumps(a.call('GET', '/api/applications/me/timeline')[1])
        self.assertNotIn('INTERNAL-NOTE', blob)
        self.assertNotIn('Secret Reviewer', blob)
        # no reviewer identity in the applicant's copy (checked by key and by name, not by the id's digits,
        # which can appear by chance in a timestamp or a round)
        self.assertFalse({'reviewer', 'reviewer_id', 'note'} & set(app['review']))
        self.assertNotIn(staff.email, json.dumps(app['review']))
        n = a.call('GET', '/api/notifications')[1]
        self.assertEqual([(i['key'], i['type'], i['action'], i['reference']) for i in n['items']], [('review.more_info', 'action', '#documents', a.ref)])
        t = a.call('GET', '/api/applications/me/timeline?actor=reviewer')[1]['items']
        self.assertEqual([(i['key'], i['actor']) for i in t], [('review.more_info', 'reviewer')])
        # the reviewer sees the note and who decided
        d = staff.call('GET', '/api/reviewer/applications/' + a.ref)[1]
        self.assertEqual((d['reviews'][0]['note'], d['reviews'][0]['reviewer']), ('INTERNAL-NOTE face score borderline', 'Secret Reviewer Name'))
        self.assertIn('REVIEW_MORE_INFO', [e['action'] for e in d['internal_events']])
        # more_info then a final decision on the same round is allowed; a second final decision is not
        self.assertEqual(self.decide(staff, a.ref, {'action': 'reject', 'message': 'The documents do not meet the rules.'})[0], 200)
        code, out = self.decide(staff, a.ref, {'action': 'approve'})
        self.assertEqual((code, out['error']), (409, 'already_decided'))
        self.assertEqual(a.call('GET', '/api/applications/me')[1]['application']['review']['action'], 'reject')
        keys = [i['key'] for i in a.call('GET', '/api/notifications')[1]['items']]
        self.assertEqual(keys, ['review.rejected', 'review.more_info'])

    def test_approve_without_message_and_case_status(self):
        staff = self.reviewer()
        a = self.applicant()
        code, out = self.decide(staff, a.ref, {'action': 'approve'})
        self.assertEqual((code, out['review']['message']), (200, ''))
        self.assertEqual(a.call('GET', '/api/cases/me')[1]['status'], 'KYC_APPROVED')
        self.assertEqual(a.call('GET', '/api/notifications')[1]['items'][0]['key'], 'review.approved')

    def test_reviews_are_immutable(self):
        staff = self.reviewer()
        a = self.applicant()
        self.decide(staff, a.ref, {'action': 'approve'})
        with self.assertRaises(sqlite3.IntegrityError):
            with auth._db() as db:
                db.execute("UPDATE reviews SET action='reject'")
        with self.assertRaises(sqlite3.IntegrityError):
            with auth._db() as db:
                db.execute('DELETE FROM reviews')

    def test_no_one_acts_on_their_own_application_or_a_case_assigned_to_someone_else(self):
        import workflows
        admin = self.admin()
        r1 = self.reviewer(admin, 'First Reviewer')
        r2 = self.reviewer(admin, 'Second Reviewer')
        a = self.applicant()
        with auth._db() as db:  # a staff account that also owns an application (e.g. a role changed later)
            db.execute("UPDATE users SET role='reviewer' WHERE id=?", (a.id,))
        a.call('POST', '/api/auth/login', {'email': a.email, 'password': PW})
        self.assertEqual(self.decide(a, a.ref, {'action': 'approve'})[1]['error'], 'own_application')
        with auth._db() as db:
            db.execute("UPDATE users SET role='applicant' WHERE id=?", (a.id,))
        case = workflows.own_case({'id': a.id, 'role': 'applicant'})
        workflows.assign({'id': admin.id, 'role': 'admin'}, case['id'], r1.id)
        self.assertEqual(self.decide(r2, a.ref, {'action': 'approve'})[1]['error'], 'forbidden')
        self.assertEqual(self.decide(r1, a.ref, {'action': 'approve'})[0], 200)


class Resubmission(Base):
    def test_resubmission_after_more_info_returns_to_needs_review(self):
        staff = self.reviewer()
        a = self.applicant()
        staff.call('POST', '/api/reviewer/applications/%s/decision' % a.ref, {'action': 'more_info', 'message': 'Please upload the PAN again.',
                                                                              'items': [{'doc': 'PAN', 'issue': 'IMAGE_BLURRY'}]})
        self.assertEqual(self.queue(staff)['items'], [])  # waiting on the applicant
        self.submit(a, 'HUMAN_REVIEW', 1)  # a save on the same round is not a resubmission
        self.assertEqual(self.queue(staff)['items'], [])
        self.submit(a, 'APPROVE', 2, issues=())  # a new round with a decision
        out = self.queue(staff)
        self.assertEqual([(i['reference'], i['round'], i['review']['action']) for i in out['items']], [(a.ref, 2, 'more_info')])
        self.assertEqual(out['counts']['needs_review'], 1)
        code, out = staff.call('POST', '/api/reviewer/applications/%s/decision' % a.ref, {'action': 'approve'})
        self.assertEqual(out['review']['round'], 2)
        self.assertEqual(self.queue(staff)['items'], [])

    def test_a_later_round_routed_to_a_person_needs_review_again(self):
        staff = self.reviewer()
        a = self.applicant()
        staff.call('POST', '/api/reviewer/applications/%s/decision' % a.ref, {'action': 'approve'})
        self.submit(a, 'HUMAN_REVIEW', 2)
        self.assertEqual([i['reference'] for i in self.queue(staff)['items']], [a.ref])
        self.assertEqual(staff.call('POST', '/api/reviewer/applications/%s/decision' % a.ref, {'action': 'approve'})[0], 200)


class Appeals(Base):
    def appeal(self, a, text='Please look again: my name changed after marriage.'):
        return a.call('POST', '/api/appeals', {'explanation': text})

    def test_applicant_appeals_a_reject_and_a_reviewer_overturns_it(self):
        staff = self.reviewer()
        a = self.applicant(decision='REJECT')
        self.assertEqual(self.queue(staff)['items'], [])  # an engine reject alone is not queued
        code, out = self.appeal(a)
        self.assertEqual((code, out['status']), (201, 'OPEN'))
        ap = out['reference']
        self.assertEqual(self.appeal(a)[1]['error'], 'appeal_open')
        q = self.queue(staff)
        self.assertEqual([(i['reference'], i['open_appeal']) for i in q['items']], [(a.ref, True)])
        self.assertEqual(q['counts']['appeals'], 1)
        items = staff.call('GET', '/api/reviewer/appeals')[1]['items']
        self.assertEqual([(i['id'], i['reference'], i['status'], i['display_name']) for i in items], [(ap, a.ref, 'OPEN', 'Asha Rao')])
        # a decision while an appeal is open goes through the appeal
        self.assertEqual(staff.call('POST', '/api/reviewer/applications/%s/decision' % a.ref, {'action': 'approve'})[1]['error'], 'appeal_open')
        code, out = staff.call('POST', '/api/reviewer/appeals/%s/resolve' % ap, {'outcome': 'overturn', 'message': 'We checked the evidence and accept it.', 'note': 'INTERNAL-APPEAL'})
        self.assertEqual((code, out['status']), (200, 'OVERTURNED'))
        app = a.call('GET', '/api/applications/me')[1]['application']
        self.assertEqual((app['review']['action'], app['review']['message']), ('approve', 'We checked the evidence and accept it.'))
        self.assertEqual(a.call('GET', '/api/appeals/me')[1]['appeals'][0]['status'], 'OVERTURNED')
        self.assertEqual(a.call('GET', '/api/notifications')[1]['items'][0]['key'], 'appeal.overturned')
        self.assertNotIn('INTERNAL-APPEAL', json.dumps(a.call('GET', '/api/applications/me')[1]))
        self.assertEqual(self.queue(staff)['items'], [])
        self.assertEqual([i['reference'] for i in self.queue(staff, 'decided')['items']], [a.ref])
        self.assertEqual(staff.call('GET', '/api/reviewer/appeals')[1]['items'], [])
        self.assertEqual(len(staff.call('GET', '/api/reviewer/appeals?status=all')[1]['items']), 1)

    def test_double_resolve_is_409_for_every_outcome(self):
        staff = self.reviewer()
        for outcome, status, key in (('uphold', 'UPHELD', 'appeal.upheld'), ('more_info', 'MORE_INFO', 'appeal.more_info'), ('overturn', 'OVERTURNED', 'appeal.overturned')):
            a = self.applicant(decision='REJECT')
            ap = self.appeal(a)[1]['reference']
            code, out = staff.call('POST', '/api/reviewer/appeals/%s/resolve' % ap, {'outcome': outcome, 'message': 'We have reviewed your appeal.'})
            self.assertEqual((code, out['status']), (200, status))
            self.assertEqual(a.call('GET', '/api/notifications')[1]['items'][0]['key'], key)
            self.assertEqual(a.call('GET', '/api/applications/me/timeline?actor=reviewer')[1]['items'][0]['key'], key)
            for again in ('uphold', 'overturn', 'more_info'):
                code, out = staff.call('POST', '/api/reviewer/appeals/%s/resolve' % ap, {'outcome': again, 'message': 'A second answer.'})
                self.assertEqual((code, out['error']), (409, 'already_resolved'), (outcome, again))
            # the legacy route refuses a second resolution too
            import workflows
            with self.assertRaises(ValueError):
                workflows.resolve({'id': staff.id, 'role': 'reviewer'}, ap, 'RESOLVED', 'A second legacy answer.')
        # after an upheld appeal the applicant may appeal again
        self.assertEqual(self.appeal(a)[0], 201)

    def test_appeal_validation(self):
        staff = self.reviewer()
        a = self.applicant(decision='REJECT')
        self.assertEqual(self.appeal(a, 'My PAN is ABCDE1234F, please check.')[1]['error'], 'id_number')
        self.assertEqual(self.appeal(a, 'short')[1]['error'], 'invalid_explanation')
        ap = self.appeal(a)[1]['reference']
        for body, error in (({'outcome': 'maybe', 'message': 'We looked.'}, 'invalid_outcome'),
                            ({'outcome': 'uphold'}, 'message_required'),
                            ({'outcome': 'uphold', 'message': 'This is fraud.'}, 'unsafe_message'),
                            ({'outcome': 'uphold', 'message': 'Account 123456789012 is not yours.'}, 'id_number')):
            code, out = staff.call('POST', '/api/reviewer/appeals/%s/resolve' % ap, body)
            self.assertEqual((code, out['error']), (400, error), body)
        self.assertEqual(staff.call('POST', '/api/reviewer/appeals/AP-0123456789abcdef/resolve', {'outcome': 'uphold', 'message': 'We looked.'})[0], 404)
        self.assertEqual(staff.call('GET', '/api/reviewer/appeals?status=bogus')[0], 400)

    def test_appeal_of_a_reviewer_reject_then_uphold(self):
        staff = self.reviewer()
        a = self.applicant()
        staff.call('POST', '/api/reviewer/applications/%s/decision' % a.ref, {'action': 'reject', 'message': 'The marksheet board is not recognised.'})
        ap = self.appeal(a)[1]['reference']
        self.assertTrue(self.queue(staff)['items'][0]['open_appeal'])
        staff.call('POST', '/api/reviewer/appeals/%s/resolve' % ap, {'outcome': 'uphold', 'message': 'The decision stands after a second look.'})
        self.assertEqual(a.call('GET', '/api/applications/me')[1]['application']['review']['action'], 'reject')
        self.assertEqual(self.queue(staff)['items'], [])
        self.assertEqual(self.queue(staff, 'decided')['items'][0]['review']['action'], 'reject')


if __name__ == '__main__':
    unittest.main()
