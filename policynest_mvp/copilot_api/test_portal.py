"""Applicant-portal API tests: profile, application, events, timeline, notifications, and the PR #1 review fixes.
Standard library only; the model is stubbed. Run: python -m unittest test_portal -v"""
import http.cookiejar
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import auth
import portal
import rag
import telemetry
import workflows
import test_auth
import test_main

main = test_main.main
SITE = test_main.SITE
PW = 'correct horse battery'


class PortalBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), main.H)
        cls.url = 'http://127.0.0.1:%d' % cls.server.server_port
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        test_auth.AuthStoreTest.setUp(self)  # fresh database
        main._hits.clear();main._auth_hits.clear()
        main._global.clear()
        main._day.update(date='', n=0)

    def client(self, email=None, register=True):
        """An opener with its own cookie jar; registers an applicant unless told otherwise."""
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        c = type('C', (), {})()
        c.opener = op
        c.email = email or ('u%s@example.test' % os.urandom(4).hex())
        c.call = lambda method, path, body=None, origin=SITE: self.call(op, method, path, body, origin)
        if register:
            code, out = c.call('POST', '/api/auth/register', {'email': c.email, 'password': PW, 'display_name': 'Test Applicant'})
            self.assertEqual(code, 201)
            c.id = out['user']['id']
        return c

    def call(self, opener, method, path, body=None, origin=SITE):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        req = urllib.request.Request(self.url + path, method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with opener.open(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def anon(self, method, path, body=None):
        return self.call(urllib.request.build_opener(), method, path, body)


class AuthRequired(PortalBase):
    def test_every_portal_route_needs_a_session(self):
        for method, path in portal.ROUTES:
            body = {} if method != 'GET' else None
            code, out = self.anon(method, path, body)
            self.assertEqual(code, 401, (method, path))
            self.assertEqual(out['error'], 'unauthenticated')

    def test_staff_cannot_use_applicant_routes_but_can_read_their_profile(self):
        c = self.client()
        with auth._db() as db:
            db.execute("UPDATE users SET role='reviewer' WHERE id=?", (c.id,))
        code, _ = c.call('POST', '/api/auth/login', {'email': c.email, 'password': PW})
        self.assertEqual(code, 200)
        self.assertEqual(c.call('POST', '/api/applications/me', {})[0], 403)
        self.assertEqual(c.call('GET', '/api/notifications')[0], 403)
        code, out = c.call('GET', '/api/me')
        self.assertEqual((code, out['user']['role']), (200, 'reviewer'))

    def test_wrong_origin_is_refused_on_writes(self):
        c = self.client()
        self.assertEqual(c.call('PUT', '/api/applications/me', {'version': 1}, origin='https://evil.example')[0], 403)
        self.assertEqual(c.call('PATCH', '/api/me', {'language': 'hi'}, origin=None)[0], 403)


class Profile(PortalBase):
    def test_get_patch_and_masking(self):
        c = self.client()
        code, out = c.call('GET', '/api/me')
        u = out['user']
        self.assertEqual(code, 200)
        self.assertEqual((u['language'], u['email_verified'], u['mobile_verified'], u['mobile_masked']), ('en', False, False, None))
        code, out = c.call('PATCH', '/api/me', {'mobile': '+91 98765 43210', 'language': 'hi', 'display_name': 'Asha Rao'})
        self.assertEqual(code, 200)
        u = out['user']
        self.assertEqual((u['language'], u['display_name']), ('hi', 'Asha Rao'))
        self.assertEqual(u['mobile_masked'], '•' * 6 + '3210')
        self.assertNotIn('9876543210', json.dumps(out))
        with auth._db() as db:  # stored, but never returned
            self.assertEqual(db.execute('SELECT mobile FROM users WHERE id=?', (c.id,)).fetchone()[0], '9876543210')

    def test_validation(self):
        c = self.client()
        for body in ({'language': 'fr'}, {'mobile': '12345'}, {'display_name': 'x'}, {'analytics_consent': 'yes'}):
            self.assertEqual(c.call('PATCH', '/api/me', body)[0], 400, body)
        self.assertEqual(c.call('PATCH', '/api/me', {'mobile': None})[1]['user']['mobile_masked'], None)

    def test_role_cannot_be_changed_through_patch(self):
        c = self.client()
        out = c.call('PATCH', '/api/me', {'role': 'admin', 'email': 'x@y.z'})[1]
        self.assertEqual(out['user']['role'], 'applicant')
        self.assertEqual(out['user']['email'], c.email)


class Applications(PortalBase):
    def test_get_is_404_until_created_then_create_is_idempotent(self):
        c = self.client()
        self.assertEqual(c.call('GET', '/api/applications/me')[0], 404)
        code, first = c.call('POST', '/api/applications/me', {})
        self.assertEqual(code, 200)
        app = first['application']
        self.assertRegex(app['reference'], r'^PN-\d{4}-\d{5}$')
        self.assertEqual((app['version'], app['status'], app['details'], app['progress']), (1, 'DRAFT', {}, {}))
        again = c.call('POST', '/api/applications/me', {})[1]['application']
        self.assertEqual(again['reference'], app['reference'])
        self.assertEqual(c.call('GET', '/api/applications/me')[1]['application']['reference'], app['reference'])
        with auth._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM applications').fetchone()[0], 1)

    def test_reference_is_stable_across_saves_and_not_derived_from_identifiers(self):
        c = self.client()
        ref = c.call('POST', '/api/applications/me', {})[1]['application']['reference']
        c.call('PUT', '/api/applications/me', {'version': 1, 'details': {'pan_last4': '234F', 'aadhaar4': '9012', 'mobile': '9876543210'}})
        self.assertEqual(c.call('GET', '/api/applications/me')[1]['application']['reference'], ref)
        for fragment in ('234F', '9012', '3210'):
            self.assertNotIn(fragment, ref)

    def test_reference_collision_is_retried(self):
        a, b = self.client(), self.client()
        first = a.call('POST', '/api/applications/me', {})[1]['application']['reference']
        seq = iter([first, first, first, 'PN-2026-00001'])
        original = portal._new_reference
        portal._new_reference = lambda: next(seq)
        try:
            second = b.call('POST', '/api/applications/me', {})[1]['application']['reference']
        finally:
            portal._new_reference = original
        self.assertEqual(second, 'PN-2026-00001')

    def test_ownership(self):
        a, b = self.client(), self.client()
        a.call('POST', '/api/applications/me', {})
        a.call('PUT', '/api/applications/me', {'version': 1, 'details': {'name': 'Asha Rao', 'city': 'Pune'}})
        self.assertEqual(b.call('GET', '/api/applications/me')[0], 404)  # B has none; A's is never reachable by B
        b.call('POST', '/api/applications/me', {})
        got = b.call('GET', '/api/applications/me')[1]['application']
        self.assertEqual(got['details'], {})
        self.assertNotEqual(got['reference'], a.call('GET', '/api/applications/me')[1]['application']['reference'])

    def test_put_versioning_and_409(self):
        c = self.client()
        c.call('POST', '/api/applications/me', {})
        code, out = c.call('PUT', '/api/applications/me', {'version': 1, 'details': {'name': 'Asha Rao'}, 'progress': {'stage': 'Documents'}})
        self.assertEqual(code, 200)
        self.assertEqual(out['version'], 2)
        self.assertIn('updated_at', out)
        code, out = c.call('PUT', '/api/applications/me', {'version': 1, 'details': {'name': 'Stale Writer'}})
        self.assertEqual((code, out['error']), (409, 'stale'))
        self.assertEqual(out['application']['version'], 2)
        self.assertEqual(out['application']['details']['name'], 'Asha Rao')
        self.assertEqual(c.call('PUT', '/api/applications/me', {'details': {}})[0], 400)  # version is required
        self.assertEqual(c.call('PUT', '/api/applications/me', {'version': '2'})[0], 400)
        self.assertEqual(c.call('GET', '/api/applications/me')[1]['application']['details']['name'], 'Asha Rao')

    def test_put_without_application_is_404(self):
        self.assertEqual(self.client().call('PUT', '/api/applications/me', {'version': 1})[0], 404)

    def test_details_allow_list_and_unknown_keys_stripped(self):
        c = self.client()
        c.call('POST', '/api/applications/me', {})
        body = {'version': 1, 'details': {'name': 'Asha Rao', 'dob': '1995-04-12', 'state': 'Maharashtra', 'city': 'Pune',
                                          'education': 'Class 12', 'email': 'asha@example.test', 'ifsc': 'HDFC0001234',
                                          'aadhaar4': '9012', 'pan_last4': '234f', 'acct_last4': '4417', 'language': 'hi',
                                          'password': 'SECRET', 'ocr_text': 'SECRET', 'role': 'admin'}}
        self.assertEqual(c.call('PUT', '/api/applications/me', body)[0], 200)
        d = c.call('GET', '/api/applications/me')[1]['application']['details']
        self.assertEqual(sorted(d), ['aadhaar4', 'acct_last4', 'city', 'dob', 'education', 'email', 'ifsc', 'language', 'name', 'pan_last4', 'state'])
        self.assertEqual(d['pan_last4'], '234F')
        self.assertNotIn('SECRET', json.dumps(d))

    def test_identifiers_are_refused(self):
        c = self.client()
        c.call('POST', '/api/applications/me', {})
        cases = [{'pan_last4': 'ABCDE1234F'}, {'name': 'ABCDE1234F'}, {'city': 'abcde1234f'},
                 {'acct_last4': '50100284417'}, {'acct_last4': '12345'}, {'aadhaar4': '123456789012'},
                 {'aadhaar4': '1234 5678 9012'}, {'ifsc': '50100284417'}, {'state': 'ok 123456789012345'}]
        for d in cases:
            code, out = c.call('PUT', '/api/applications/me', {'version': 1, 'details': d})
            self.assertEqual(code, 400, d)
        self.assertEqual(c.call('GET', '/api/applications/me')[1]['application']['version'], 1)  # nothing was stored

    def test_mobile_is_masked_in_details_and_masked_value_keeps_the_stored_one(self):
        c = self.client()
        c.call('POST', '/api/applications/me', {})
        c.call('PUT', '/api/applications/me', {'version': 1, 'details': {'name': 'Asha Rao', 'mobile': '9876543210'}})
        app = c.call('GET', '/api/applications/me')[1]['application']
        self.assertEqual(app['details']['mobile'], '•' * 6 + '3210')
        c.call('PUT', '/api/applications/me', {'version': app['version'], 'details': app['details']})
        with auth._db() as db:
            self.assertIn('9876543210', db.execute('SELECT details FROM applications').fetchone()[0])
        self.assertEqual(c.call('PUT', '/api/applications/me', {'version': 3, 'details': {'mobile': '123'}})[0], 400)

    def test_progress_allow_list_bounds_and_status(self):
        c = self.client()
        c.call('POST', '/api/applications/me', {})
        progress = {'stage': 'Documents', 'dpage': 2, 'doc_index': 3, 'decision': 'NEEDS_MORE_INFO', 'round': 1,
                    'resume': {'stage': 'Documents', 'page': 2, 'doc': 'PAN', 'milestone': 'details-saved', 'action': 'upload', 't': 1763200000000},
                    'documents': {'PAN': {'status': 'ok', 'version': 2, 'issues': ['IMAGE_BLURRY'], 'text': 'OCR-SECRET'}},
                    'training': {'modules': ['basics', 'claims'], 'hours': 4}, 'exam': {'score': 40, 'passed': False, 'attempts': 1},
                    'raw_ocr': 'OCR-SECRET', 'photo': 'data:image/png;base64,AAAA'}
        self.assertEqual(c.call('PUT', '/api/applications/me', {'version': 1, 'progress': progress})[0], 200)
        app = c.call('GET', '/api/applications/me')[1]['application']
        self.assertEqual(app['status'], 'NEEDS_MORE_INFO')
        self.assertNotIn('OCR-SECRET', json.dumps(app))
        self.assertNotIn('photo', app['progress'])
        self.assertEqual(app['progress']['documents']['PAN'], {'status': 'ok', 'version': 2, 'issues': ['IMAGE_BLURRY']})
        v = app['version']
        for bad in ({'decision': 'APPROVED!'}, {'dpage': 99}, {'exam': {'score': 500}}, {'stage': 'x' * 80},
                    {'documents': {'PAN': {'issues': ['free text with spaces']}}},
                    {'training': {'modules': ['m%d' % i for i in range(30)]}}):
            self.assertEqual(c.call('PUT', '/api/applications/me', {'version': v, 'progress': bad})[0], 400, bad)
        cert = {'stage': 'Certificate', 'decision': 'APPROVE', 'certificate': {'code': 'PNCERT-1234', 'issued_at': 1763200000000, 'seal': 'seal-a'}}
        c.call('PUT', '/api/applications/me', {'version': v, 'progress': cert})
        self.assertEqual(c.call('GET', '/api/applications/me')[1]['application']['status'], 'CERTIFIED')

    def test_legacy_snapshot_route_still_works(self):
        c = self.client()
        code, out = c.call('POST', '/api/applications/me', {'snapshot': {'stage': 'Documents', 'documents': []}})
        self.assertEqual((code, out), (200, {'ok': True}))


    def test_digit_heavy_email_is_accepted_and_legacy_snapshot_get_works(self):
        c = self.client()
        c.call('POST', '/api/applications/me', {})
        code, out = c.call('PUT', '/api/applications/me', {'version': 1, 'details': {'email': '9876543210@gmail.com', 'name': 'Asha Rao'}})
        self.assertEqual(code, 200, out)
        self.assertEqual(c.call('GET', '/api/applications/me')[1]['application']['details']['email'], '9876543210@gmail.com')
        c.call('POST', '/api/applications/me', {'snapshot': {'stage': 'Documents', 'documents': []}})
        code, out = c.call('GET', '/api/applications/me?view=snapshot')
        self.assertEqual(code, 200)
        self.assertNotIn('application', out)


class EventsAndNotifications(PortalBase):
    def started(self):
        c = self.client()
        self.ref = c.call('POST', '/api/applications/me', {})[1]['application']['reference']
        return c

    def test_events_need_an_application(self):
        c = self.client()
        self.assertEqual(c.call('POST', '/api/applications/me/events', {'events': [{'key': 'details_saved', 't': 1}]})[0], 404)

    def test_events_write_timeline_and_notifications(self):
        c = self.started()
        now = int(__import__('time').time() * 1000)
        evs = [{'key': 'details_saved', 'stage': 'Your details', 't': now},
               {'key': 'doc_verified', 'doc': 'PAN', 'stage': 'Documents', 't': now + 1},
               {'key': 'doc_uploaded', 'doc': 'PAN', 't': now + 2},   # timeline only
               {'key': 'review_approved', 't': now + 3},               # reviewer keys cannot be sent by a client
               {'key': 'made_up', 't': now + 4},
               {'key': 'doc_verified', 'doc': 'bad slot!', 't': now + 5}]
        code, out = c.call('POST', '/api/applications/me/events', {'events': evs})
        self.assertEqual((code, out['accepted']), (200, 3))
        n = c.call('GET', '/api/notifications')[1]
        self.assertEqual(n['unread'], 2)
        self.assertEqual({i['key'] for i in n['items']}, {'details_saved', 'doc_verified'})
        item = [i for i in n['items'] if i['key'] == 'doc_verified'][0]
        self.assertEqual((item['type'], item['stage'], item['doc'], item['action'], item['read'], item['reference']),
                         ('success', 'Documents', 'PAN', '#documents', False, self.ref))
        self.assertEqual(len(c.call('GET', '/api/applications/me/timeline')[1]['items']), 3)

    def test_retried_events_are_idempotent(self):
        c = self.started()
        ev = {'events': [{'key': 'doc_needs_fix', 'doc': 'BANK', 't': int(__import__('time').time() * 1000)}]}
        self.assertEqual(c.call('POST', '/api/applications/me/events', ev)[1]['accepted'], 1)
        self.assertEqual(c.call('POST', '/api/applications/me/events', ev)[1]['accepted'], 0)
        self.assertEqual(c.call('GET', '/api/notifications')[1]['unread'], 1)

    def test_old_retried_events_stay_idempotent_and_notifications_are_pruned(self):
        c = self.started()
        ev = {'events': [{'key': 'details_saved', 't': 1000}]}
        self.assertEqual(c.call('POST', '/api/applications/me/events', ev)[1]['accepted'], 1)
        self.assertEqual(c.call('POST', '/api/applications/me/events', ev)[1]['accepted'], 0)
        self.assertGreater(c.call('GET', '/api/applications/me/timeline')[1]['items'][0]['t'], 1000)
        self.assertEqual(portal.NOTIFICATIONS_KEEP, 200)

    def test_bad_event_bodies(self):
        c = self.started()
        for body in ({}, {'events': []}, {'events': 'x'}, {'events': [{'key': 'details_saved'}] * 21}):
            self.assertEqual(c.call('POST', '/api/applications/me/events', body)[0], 400, body)

    def test_mark_read_ids_all_and_isolation(self):
        a, b = self.started(), self.started()
        for c in (a, b):
            c.call('POST', '/api/applications/me/events', {'events': [{'key': 'doc_verified', 'doc': 'PAN', 't': 1},
                                                                       {'key': 'doc_verified', 'doc': 'BANK', 't': 2},
                                                                       {'key': 'exam_passed', 't': 3}]})
        items = a.call('GET', '/api/notifications')[1]['items']
        self.assertEqual(len(items), 3)
        self.assertEqual(a.call('POST', '/api/notifications/read', {'ids': [items[0]['id']]})[1]['unread'], 2)
        # B cannot mark A's notifications read
        self.assertEqual(b.call('POST', '/api/notifications/read', {'ids': [i['id'] for i in items]})[1]['unread'], 3)
        self.assertEqual(a.call('GET', '/api/notifications')[1]['unread'], 2)
        self.assertEqual(a.call('POST', '/api/notifications/read', {'all': True})[1]['unread'], 0)
        self.assertEqual(a.call('POST', '/api/notifications/read', {})[0], 400)
        self.assertEqual(a.call('POST', '/api/notifications/read', {'ids': ['1']})[0], 400)
        self.assertEqual(len(a.call('GET', '/api/notifications?limit=1')[1]['items']), 1)

    def test_notifications_and_timeline_hold_no_identifiers(self):
        c = self.started()
        c.call('PUT', '/api/applications/me', {'version': 1, 'details': {'name': 'Asha Rao', 'pan_last4': '234F', 'aadhaar4': '9012'}})
        c.call('POST', '/api/applications/me/events', {'events': [{'key': 'doc_verified', 'doc': 'PAN', 't': 1, 'name': 'Asha Rao', 'pan': 'ABCDE1234F', 'text': 'OCR-SECRET'}]})
        blob = json.dumps(c.call('GET', '/api/notifications')[1]) + json.dumps(c.call('GET', '/api/applications/me/timeline')[1])
        for secret in ('Asha', '234F', '9012', 'ABCDE1234F', 'OCR-SECRET'):
            self.assertNotIn(secret, blob)

    def test_timeline_filtering(self):
        c = self.started()
        c.call('POST', '/api/applications/me/events', {'events': [
            {'key': 'details_saved', 'stage': 'Your details', 't': 1}, {'key': 'doc_verified', 'doc': 'PAN', 'stage': 'Documents', 't': 2},
            {'key': 'doc_uploaded', 'doc': 'PAN', 'stage': 'Documents', 't': 3}]})
        with auth._db() as db:  # an internal row that must never reach the applicant
            db.execute("INSERT INTO timeline(user_id,t,actor,key,stage,doc) VALUES(?,?,?,?,?,?)", (c.id, 9, 'reviewer', 'fraud_desk_referral', '', ''))
            db.execute("INSERT INTO timeline(user_id,t,actor,key,stage,doc) VALUES(?,?,?,?,?,?)", (c.id, 10, 'staff', 'details_saved', '', ''))
        items = c.call('GET', '/api/applications/me/timeline')[1]['items']
        self.assertEqual([i['key'] for i in items], ['doc_uploaded', 'doc_verified', 'details_saved'])  # newest first
        self.assertEqual({i['actor'] for i in items}, {'you', 'policynest'})
        only = c.call('GET', '/api/applications/me/timeline?actor=policynest')[1]['items']
        self.assertEqual([i['key'] for i in only], ['doc_verified'])
        staged = c.call('GET', '/api/applications/me/timeline?stage=Documents&limit=1')[1]['items']
        self.assertEqual(len(staged), 1)

    def test_reviewer_decisions_become_applicant_safe_rows(self):
        c = self.started()
        reviewer = auth.register('rev@example.test', PW, 'Reviewer')
        admin = auth.register('adm@example.test', PW, 'Admin')
        with auth._db() as db:
            db.execute("UPDATE users SET role='reviewer' WHERE id=?", (reviewer['id'],))
            db.execute("UPDATE users SET role='admin' WHERE id=?", (admin['id'],))
        reviewer['role'], admin['role'] = 'reviewer', 'admin'
        case_id = workflows.own_case({'id': c.id, 'role': 'applicant'})['id']
        workflows.assign(admin, case_id, reviewer['id'])
        workflows.review(reviewer, case_id, 'NEEDS_MORE_INFO', 'INTERNAL-RATIONALE name differs from PAN', '§4.4')
        n = c.call('GET', '/api/notifications')[1]
        self.assertEqual([(i['key'], i['type'], i['action']) for i in n['items']], [('review_more_info', 'action', '#documents')])
        t = c.call('GET', '/api/applications/me/timeline')[1]['items']
        self.assertEqual([(i['key'], i['actor']) for i in t], [('review_more_info', 'reviewer')])
        blob = json.dumps(n) + json.dumps(t)
        self.assertNotIn('INTERNAL-RATIONALE', blob)
        self.assertNotIn('4.4', blob)
        workflows.review(reviewer, case_id, 'KYC_APPROVED', 'Evidence checked and accepted today', '§4.4')
        self.assertEqual(c.call('GET', '/api/notifications')[1]['unread'], 2)


class DatabasePath(unittest.TestCase):
    def test_data_dir_wins_then_explicit_path_then_default(self):
        d = os.path.join(tempfile.gettempdir(), 'durable')
        self.assertEqual(auth.resolve_db_path({'DATA_DIR': d, 'POLICYNEST_DB_PATH': '/x/y.db'}), Path(os.path.join(d, 'policynest.sqlite3')))
        self.assertEqual(auth.resolve_db_path({'POLICYNEST_DB_PATH': '/x/y.db'}), Path('/x/y.db'))
        self.assertEqual(auth.resolve_db_path({}), auth.HERE / 'policynest.sqlite3')

    def test_migrations_are_additive_and_idempotent(self):
        test_auth.AuthStoreTest.setUp(self)
        auth.register('m@example.test', PW, 'Migrator')
        for _ in range(3):
            portal.init()
            auth.init_db()
        with auth._db() as db:
            cols = {r[1] for r in db.execute('PRAGMA table_info(users)')}
        self.assertTrue({'language', 'mobile', 'analytics_consent', 'token_version'} <= cols)


class AssistantFilterFix(PortalBase):
    def test_format_example_is_allowed_but_real_looking_numbers_are_not(self):
        for ok in ('AAAAA9999A', 'The format is AAAAA9999A.', 'XXXXX0000X'):
            self.assertFalse(main.id_in(ok), ok)
        for bad in ('ABCDE1234F', 'my pan abcde1234f', '1234 5678 9012', '50100284417', 'AAAAA9999A and ABCDE1234F'):
            self.assertTrue(main.id_in(bad), bad)

    def test_copilot_accepts_the_example_in_question_and_answer(self):
        calls = []
        main.call_model = lambda kw: (calls.append(kw) or ('end_turn', 'A PAN has five letters, four digits, one letter (AAAAA9999A) [Rulebook §4.1].'))
        code, out = self.call(urllib.request.build_opener(), 'POST', '/api/copilot', {'question': 'Is the format AAAAA9999A right?'})
        self.assertEqual(code, 200)
        self.assertIn('AAAAA9999A', out['answer'])
        self.assertEqual(self.call(urllib.request.build_opener(), 'POST', '/api/copilot', {'question': 'my pan is ABCDE1234F'})[0], 400)
        main.call_model = lambda kw: ('end_turn', 'Your PAN ABCDE1234F is fine [Rulebook §4.1].')
        self.assertEqual(self.call(urllib.request.build_opener(), 'POST', '/api/copilot', {'question': 'what next'})[1]['answer'], main.SAFE)

    def test_assistant_route_does_not_refuse_the_example(self):
        c = self.client()
        code, out = c.call('POST', '/api/assistant', {'question': 'What does AAAAA9999A mean for a PAN?'})
        self.assertEqual(code, 200)
        self.assertNotIn(main.SAFE, out['answer'])


class StartupRobustness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (rag.HERE, rag.INDEX)

    def tearDown(self):
        rag.HERE, rag.INDEX = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def point_rag_at(self, folder):
        rag.HERE, rag.INDEX = folder, folder / 'rag_index.json'

    def test_missing_index_and_manual_degrades_to_the_rulebook(self):
        shutil.copy(Path(rag.__file__).parent / 'kb.json', self.tmp / 'kb.json')
        self.point_rag_at(self.tmp)
        with self.assertLogs('policynest.rag', level='WARNING'):
            hits, diag = rag.retrieve('What is the PAN card format?', semantic=False)
        self.assertTrue(hits)
        self.assertEqual(diag['mode'], 'keyword_fallback')
        self.assertTrue(all(h['source_type'] == 'approved_challenge_source' for h in hits))
        self.assertFalse((self.tmp / 'rag_index.json').exists())  # a degraded index is never persisted

    def test_nothing_at_all_returns_no_hits_instead_of_crashing(self):
        self.point_rag_at(self.tmp)
        with self.assertLogs('policynest.rag', level='WARNING'):
            hits, diag = rag.retrieve('anything', semantic=False)
        self.assertEqual((hits, diag['mode']), ([], 'unavailable'))

    def test_corrupt_index_degrades(self):
        shutil.copy(Path(rag.__file__).parent / 'kb.json', self.tmp / 'kb.json')
        (self.tmp / 'rag_index.json').write_text('{not json', encoding='utf-8')
        self.point_rag_at(self.tmp)
        with self.assertLogs('policynest.rag', level='WARNING'):
            hits, _ = rag.retrieve('PAN card format', semantic=False)
        self.assertTrue(hits)

    def test_main_loads_with_missing_kb(self):
        kb = main.load_kb(self.tmp / 'nope.json')
        self.assertEqual(kb['chunks'], [])

    def test_daily_left_survives_a_database_error(self):
        main._day.update(date='', n=0)
        original = auth._db

        def broken():
            raise RuntimeError('disk gone')
        auth._db = broken
        try:
            with self.assertLogs('policynest.api', level='WARNING'):
                left = main._daily_left()
            self.assertEqual(left, main.DAILY_CAP)  # not silently zero
            self.assertTrue(main._reserve_answer())
            self.assertEqual(main._daily_left(), main.DAILY_CAP - 1)
        finally:
            auth._db = original
            main._day.update(date='', n=0)


class TelemetryHardening(PortalBase):
    def test_telemetry_needs_an_allowed_origin_and_is_capped(self):
        op = urllib.request.build_opener()
        metric = {'metrics': [{'name': 'lcp', 'value': 1200}]}
        self.assertEqual(self.call(op, 'POST', '/api/telemetry', metric, origin=None)[0], 403)
        self.assertEqual(self.call(op, 'POST', '/api/telemetry', metric, origin='https://evil.example')[0], 403)
        saved = telemetry.FRONTEND_DAILY_CAP
        telemetry.FRONTEND_DAILY_CAP = 2
        try:
            self.assertEqual(self.call(op, 'POST', '/api/telemetry', {'metrics': [{'name': 'lcp', 'value': 1}] * 5})[0], 200)
            code, out = self.call(op, 'POST', '/api/telemetry', metric)
            self.assertEqual((code, out['error']), (429, 'daily_cap'))
            with auth._db() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM frontend_metrics').fetchone()[0], 2)
        finally:
            telemetry.FRONTEND_DAILY_CAP = saved

    def test_technical_rows_stop_growing_at_the_cap(self):
        saved = telemetry.TECHNICAL_DAILY_CAP
        telemetry.TECHNICAL_DAILY_CAP = 3
        try:
            for i in range(10):
                telemetry.technical('/api/other', 404, 1.0, 'req-%d' % i)
            with auth._db() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM technical').fetchone()[0], 3)
        finally:
            telemetry.TECHNICAL_DAILY_CAP = saved

    def test_events_require_the_stored_consent_flag(self):
        c = self.client()
        ev = {'event_name': 'onboarding_started', 'stage': 'Your details'}
        code, out = c.call('POST', '/api/events', ev)
        self.assertEqual((code, out['error']), (403, 'consent_required'))
        self.assertEqual(c.call('PATCH', '/api/me', {'analytics_consent': True})[1]['user']['analytics_consent'], True)
        self.assertEqual(c.call('POST', '/api/events', ev)[0], 201)
        c.call('PATCH', '/api/me', {'analytics_consent': False})
        self.assertEqual(c.call('POST', '/api/events', ev)[0], 403)
        self.assertEqual(self.anon('POST', '/api/events', ev)[0], 400)


class PackageScript(unittest.TestCase):
    def test_package_excludes_secrets_state_and_tests(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
        import package_proxy
        names = [arc for _, arc in package_proxy.collect()]
        self.assertIn('main.py', names)
        self.assertIn('portal.py', names)
        for n in names:
            self.assertIsNone(re.search(r'(^|/)(\.env|__pycache__|\.local)|\.sqlite3|(^|/)test_', n), n)


if __name__ == '__main__':
    unittest.main(verbosity=2)
