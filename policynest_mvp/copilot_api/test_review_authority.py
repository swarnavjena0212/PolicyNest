"""Fixes after the reviewer verification (16 Nov 2026):
M1  the server enforces a reviewer's reject and an unapproved HUMAN_REVIEW: no training, exam or certificate progress;
M2  ID numbers written with hyphens or dots are refused everywhere a person can type text the server stores.
Standard library only. Run: python -m unittest test_review_authority -v"""
import json
import unittest

import auth
import guard
import test_reviewer

Base = test_reviewer.Base

CERTIFIED = {'stage': 'certificate', 'round': 1, 'decision': 'APPROVE',
             'documents': {'PAN': {'status': 'ok', 'version': 1, 'issues': []}},
             'training': {'modules': ['m1', 'm2'], 'hours': 15}, 'exam': {'score': 80, 'passed': True},
             'certificate': {'code': 'POSP-TEST-0001'}}


def progress(**over):
    p = json.loads(json.dumps(CERTIFIED))
    p.update(over)
    return p


class Authority(Base):
    def put(self, c, prog):
        code, out = c.call('PUT', '/api/applications/me', {'version': c.version, 'progress': prog})
        if code == 200:
            c.version = out['version']
        return code, out

    def status(self, c):
        return c.call('GET', '/api/applications/me')[1]['application']['status']

    def decide(self, staff, c, body):
        return staff.call('POST', '/api/reviewer/applications/%s/decision' % c.ref, body)

    def test_reject_cannot_be_undone_by_the_applicants_browser(self):
        staff = self.reviewer()
        a = self.applicant()
        self.assertEqual(self.decide(staff, a, {'action': 'reject', 'message': 'The documents do not meet the rules.'})[0], 200)
        self.assertEqual(self.status(a), 'REJECTED')  # the stored status follows the decision at once
        for prog in (CERTIFIED, progress(stage='training', certificate={}, exam={}), progress(stage='exam', certificate={})):
            code, out = self.put(a, prog)
            self.assertEqual((code, out.get('error'), out.get('reason')), (409, 'review_hold', 'rejected'), prog['stage'])
        # a save that stays at verification is still accepted (details keep saving), but the status stays REJECTED
        code, _ = self.put(a, {'stage': 'verification', 'round': 1, 'decision': 'APPROVE'})
        self.assertEqual(code, 200)
        self.assertEqual(self.status(a), 'REJECTED')
        row = [i for i in self.queue(staff, 'all')['items'] if i['reference'] == a.ref][0]
        self.assertEqual(row['status'], 'REJECTED')

    def test_unapproved_human_review_cannot_start_training(self):
        staff = self.reviewer()
        a = self.applicant()  # HUMAN_REVIEW, round 1
        self.assertEqual(self.status(a), 'IN_REVIEW')
        code, out = self.put(a, CERTIFIED)  # the browser claims the engine approved and certified the same round
        self.assertEqual((code, out.get('error'), out.get('reason')), (409, 'review_hold', 'awaiting_review'))
        code, out = self.put(a, progress(round=2))  # bumping the round without a reviewer's request does not help
        self.assertEqual((code, out.get('reason')), (409, 'awaiting_review'))
        # a verification-stage save claiming APPROVE is stored but does not change the status
        self.assertEqual(self.put(a, {'stage': 'verification', 'round': 1, 'decision': 'APPROVE'})[0], 200)
        self.assertEqual(self.status(a), 'IN_REVIEW')
        # once a reviewer approves, the applicant moves on
        self.assertEqual(self.decide(staff, a, {'action': 'approve'})[0], 200)
        self.assertEqual(self.status(a), 'VERIFIED')
        self.assertEqual(self.put(a, CERTIFIED)[0], 200)
        self.assertEqual(self.status(a), 'CERTIFIED')

    def test_more_info_then_a_new_round_is_not_held(self):
        staff = self.reviewer()
        a = self.applicant()
        self.decide(staff, a, {'action': 'more_info', 'message': 'Please upload the PAN again.', 'items': [{'doc': 'PAN', 'issue': 'IMAGE_BLURRY'}]})
        self.assertEqual(self.status(a), 'NEEDS_MORE_INFO')
        self.assertEqual(self.put(a, progress(round=1))[1].get('reason'), 'awaiting_review')  # same round: still held
        self.assertEqual(self.put(a, progress(round=2))[0], 200)  # the fixes were sent: the engine's new round stands

    def test_a_later_round_routed_to_a_person_is_held_again(self):
        staff = self.reviewer()
        a = self.applicant()
        self.decide(staff, a, {'action': 'approve'})
        self.submit(a, 'HUMAN_REVIEW', 2)
        self.assertEqual(self.put(a, progress(round=2))[1].get('reason'), 'awaiting_review')
        self.decide(staff, a, {'action': 'approve'})
        self.assertEqual(self.put(a, progress(round=2))[0], 200)

    def test_an_overturned_appeal_lifts_the_reject(self):
        staff = self.reviewer()
        a = self.applicant()
        self.decide(staff, a, {'action': 'reject', 'message': 'The documents do not meet the rules.'})
        ap = a.call('POST', '/api/appeals', {'explanation': 'Please look again: my name changed after marriage.'})[1]['reference']
        staff.call('POST', '/api/reviewer/appeals/%s/resolve' % ap, {'outcome': 'uphold', 'message': 'We checked and the decision stands.'})
        self.assertEqual(self.put(a, CERTIFIED)[1].get('reason'), 'rejected')  # upheld: still rejected
        ap = a.call('POST', '/api/appeals', {'explanation': 'Please look again: I have sent the marriage certificate.'})[1]['reference']
        staff.call('POST', '/api/reviewer/appeals/%s/resolve' % ap, {'outcome': 'overturn', 'message': 'We checked the evidence and accept it.'})
        self.assertEqual(self.status(a), 'VERIFIED')
        self.assertEqual(self.put(a, CERTIFIED)[0], 200)

    def test_an_engine_approved_application_is_not_held(self):
        a = self.applicant(decision='APPROVE', issues=())
        self.assertEqual(self.put(a, CERTIFIED)[0], 200)
        self.assertEqual(self.status(a), 'CERTIFIED')

    def test_no_decision_once_the_applicant_is_past_verification(self):
        staff = self.reviewer()
        a = self.applicant(decision='APPROVE', issues=())
        self.put(a, CERTIFIED)
        code, out = self.decide(staff, a, {'action': 'reject', 'message': 'The documents do not meet the rules.'})
        self.assertEqual((code, out['error']), (409, 'past_verification'))
        with auth._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 0)


SEPARATED = ['Aadhaar 1234-5678-9012 is wrong.', 'Aadhaar 1234.5678.9012 is wrong.', 'Aadhaar 1234 5678 9012 is wrong.',
             'Account 1234-5678-9012-34 is not yours.', 'Account 123.456.789.012 is not yours.', 'Account 123456789012 is not yours.']
ALLOWED = ['Please upload again by 16-11-2026.', 'Round 2 of 3.', 'Score 72 out of 100.', 'On 16.11.2026 at 10.30.', 'PAN format AAAAA9999A.']


class IdNumbers(Base):
    def test_guard_pattern(self):
        for text in SEPARATED:
            self.assertTrue(guard.id_in(text), text)
        for text in ALLOWED:
            self.assertFalse(guard.id_in(text), text)

    def test_reviewer_message_and_note(self):
        staff = self.reviewer()
        a = self.applicant()
        for text in SEPARATED:
            for body in ({'action': 'reject', 'message': text}, {'action': 'more_info', 'message': text, 'items': [{'doc': 'PAN', 'issue': 'IMAGE_BLURRY'}]},
                         {'action': 'approve', 'note': text}):
                code, out = staff.call('POST', '/api/reviewer/applications/%s/decision' % a.ref, body)
                self.assertEqual((code, out['error']), (400, 'id_number'), body)
        with auth._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 0)

    def test_appeal_explanation_and_answer(self):
        staff = self.reviewer()
        a = self.applicant(decision='REJECT')
        for text in SEPARATED:
            code, out = a.call('POST', '/api/appeals', {'explanation': 'Please check: ' + text})
            self.assertEqual((code, out['error']), (400, 'id_number'), text)
        ap = a.call('POST', '/api/appeals', {'explanation': 'Please look again: my name changed after marriage.'})[1]['reference']
        for text in SEPARATED:
            code, out = staff.call('POST', '/api/reviewer/appeals/%s/resolve' % ap, {'outcome': 'uphold', 'message': text})
            self.assertEqual((code, out['error']), (400, 'id_number'), text)

    def test_admin_note(self):
        admin = self.admin()
        r = self.register('reviewer')
        rid = self.request_id(admin, r.id)
        for text in SEPARATED:
            code, out = admin.call('POST', '/api/admin/reviewer-requests/decide', {'id': rid, 'decision': 'approve', 'note': text})
            self.assertEqual((code, out['error']), (400, 'id_number'), text)

    def test_details(self):
        a = self.applicant(decision=None, stage='details')
        for text in ('1234-5678-9012', '1234.5678.9012', '123-456-789-012'):
            code, out = a.call('PUT', '/api/applications/me', {'version': a.version, 'details': {'city': text}})
            self.assertEqual((code, out['error']), (400, 'identifier_not_accepted'), text)
        code, out = a.call('PUT', '/api/applications/me', {'version': a.version, 'details': {'city': 'Pune', 'dob': '1990-01-01'}})
        self.assertEqual(code, 200, out)


if __name__ == '__main__':
    unittest.main()
