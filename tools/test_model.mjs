// Tests for the pure MODEL region of policynest_mvp/PolicyNest.html.  Run: node --test tools/test_model.mjs
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';

const here = path.dirname(fileURLToPath(import.meta.url));
const html = fs.readFileSync(path.join(here, '..', 'policynest_mvp', 'PolicyNest.html'), 'utf8');
const a = html.indexOf('/* MODEL:START'), b = html.indexOf('/* MODEL:END */');
assert.ok(a > 0 && b > a, 'MODEL region present');
assert.ok(a > html.indexOf('/* ENGINE:END */'), 'MODEL sits after the engine block');
const src = html.slice(a, b + '/* MODEL:END */'.length);
const names = ['STAGES','APP_STATUS','statusOf','progressModel','resumePosition','nextAction','USER_ROLES','PERMISSIONS','can','applicantTimeline','I18N','GLOSSARY','t','ICON_PATHS'];
// Sandbox has no DOM, window or document: the region must work without them.
const ctx = vm.createContext({});
const M = vm.runInContext('(function(){' + src + '\nreturn {' + names.join(',') + '};})()', ctx);
const J = (o) => JSON.parse(JSON.stringify(o)); // strip vm-realm prototypes for deepEqual

const DOCS = ['PAN','AADHAAR_FRONT','AADHAAR_BACK','CLASS10','BANK','SELFIE'];
const okDocs = (keys) => Object.fromEntries(keys.map(k => [k, { status: 'ok' }]));
function S(o = {}) {
  return Object.assign({ step: 0, begun: false, dpage: 0, dk: 0, docs: {}, status: 'DRAFT', reached: { DRAFT: 1 }, decision: null,
    checks: [], fu: null, mods: [false,false,false,false,false], exam: { ans: {}, score: null }, rechecking: false, changed: {}, f: {} }, o);
}
const SCEN = {
  not_started: S(),
  draft: S({ begun: true, dpage: 2, f: { name: 'A' } }),
  in_progress: S({ begun: true, step: 1, dk: 2, docs: okDocs(['PAN','AADHAAR_FRONT']) }),
  submitted: S({ step: 2, begun: true, status: 'SUBMITTED', reached: { DRAFT: 1, SUBMITTED: 1 }, docs: okDocs(DOCS) }),
  verifying: S({ step: 2, begun: true, status: 'IN_REVIEW', reached: { DRAFT: 1, SUBMITTED: 1, IN_REVIEW: 1 }, docs: okDocs(DOCS) }),
  action_required: S({ step: 1, begun: true, status: 'NEEDS_MORE_INFO', decision: 'NEEDS_MORE_INFO', reached: { SUBMITTED: 1 }, docs: okDocs(DOCS),
    fu: { items: [{ key: 'BANK' }, { key: 'AADHAAR_BACK' }] }, checks: [{ res: 'PASS' }, { res: 'NEEDS_INFO' }, { res: 'INFO' }] }),
  human_review: S({ step: 2, begun: true, status: 'HUMAN_REVIEW', decision: 'HUMAN_REVIEW', reached: { SUBMITTED: 1 }, docs: okDocs(DOCS) }),
  training_pending: S({ step: 3, begun: true, status: 'KYC_APPROVED', reached: { SUBMITTED: 1, KYC_APPROVED: 1 }, docs: okDocs(DOCS) }),
  training_active: S({ step: 3, begun: true, status: 'TRAINING_IN_PROGRESS', reached: { SUBMITTED: 1, KYC_APPROVED: 1 }, docs: okDocs(DOCS), mods: [true,true,false,false,false] }),
  exam_pending: S({ step: 4, begun: true, status: 'TRAINING_COMPLETED', reached: { SUBMITTED: 1, KYC_APPROVED: 1, TRAINING_COMPLETED: 1 }, docs: okDocs(DOCS), mods: [true,true,true,true,true] }),
  exam_failed: S({ step: 4, begun: true, status: 'TRAINING_COMPLETED', reached: { SUBMITTED: 1, KYC_APPROVED: 1, TRAINING_COMPLETED: 1 }, docs: okDocs(DOCS), mods: [true,true,true,true,true], exam: { ans: {}, score: 20, retakeAt: 2e12 } }),
  certificate_pending: S({ step: 5, begun: true, status: 'EXAM_PASSED', reached: { SUBMITTED: 1, KYC_APPROVED: 1, EXAM_PASSED: 1 }, docs: okDocs(DOCS), mods: [true,true,true,true,true], exam: { ans: {}, score: 80 } }),
  certified: S({ step: 5, begun: true, status: 'CERTIFIED', reached: { SUBMITTED: 1, KYC_APPROVED: 1, EXAM_PASSED: 1, CERTIFIED: 1 }, docs: okDocs(DOCS), mods: [true,true,true,true,true], exam: { ans: {}, score: 80 } }),
  rejected: S({ step: 2, begun: true, status: 'REJECTED', decision: 'REJECT', reached: { SUBMITTED: 1 }, docs: okDocs(DOCS) }),
};
const NOW = 1.7e12;

test('statusOf maps every scenario to its status', () => {
  const want = { not_started:'not_started', draft:'draft', in_progress:'in_progress', submitted:'submitted', verifying:'verifying', action_required:'action_required',
    human_review:'human_review', training_pending:'training_pending', training_active:'training_pending', exam_pending:'exam_pending', exam_failed:'exam_pending',
    certificate_pending:'certificate_pending', certified:'certified', rejected:'rejected' };
  for (const [k, v] of Object.entries(want)) assert.equal(M.statusOf(SCEN[k]), v, k);
  assert.equal(M.statusOf(S({ status: 'NEEDS_MORE_INFO', rechecking: true })), 'verifying');
  assert.equal(M.statusOf(S({ status: 'EXAM_IN_PROGRESS' })), 'exam_pending');
  assert.equal(M.statusOf(null), 'not_started');
});

test('every APP_STATUS entry is complete and has text in both languages', () => {
  const keys = Object.keys(M.APP_STATUS);
  assert.equal(keys.length, 12);
  assert.ok(!keys.includes('withdrawn'));
  for (const k of keys) {
    const s = M.APP_STATUS[k];
    assert.equal(typeof s.needsAction, 'boolean');
    assert.ok(['ok','info','warn','bad','neutral'].includes(s.tone), k);
    assert.ok(M.ICON_PATHS[s.icon], k + ' icon');
    for (const lang of ['en','hi']) {
      for (const key of [s.labelKey, s.descKey, s.nextEventKey, ...(s.timelineKey === 'none' ? [] : [s.timelineKey])]) {
        assert.ok(Object.prototype.hasOwnProperty.call(M.I18N[lang], key), `${lang} missing ${key}`);
        assert.ok(M.t(key, {}, lang).trim().length > 0);
      }
    }
    assert.ok(!/[A-Z]{3,}_[A-Z_]+/.test(M.t(s.labelKey, {}, 'en') + M.t(s.descKey, {}, 'en')), 'no internal codes in ' + k);
  }
});

test('en and hi have identical key sets and the same placeholders', () => {
  const en = Object.keys(M.I18N.en).sort(), hi = Object.keys(M.I18N.hi).sort();
  assert.deepEqual(hi, en);
  const ph = (s) => (s.match(/\{[A-Za-z0-9_]+\}/g) || []).sort().join();
  for (const k of en) assert.equal(ph(M.I18N.hi[k]), ph(M.I18N.en[k]), 'placeholders differ for ' + k);
  for (const k of en) assert.ok(M.I18N.hi[k].length > 0);
});

test('Hindi uses Devanagari and keeps glossary terms as-is', () => {
  const hiText = Object.values(M.I18N.hi).join(' ');
  assert.ok(/[ऀ-ॿ]/.test(hiText));
  assert.deepEqual(J(M.GLOSSARY).slice(0, 5), ['PAN','Aadhaar','POSP','IRDAI','KYC']);
  for (const term of ['PAN','POSP','IRDAI']) assert.ok(hiText.includes(term), term);
  assert.ok(M.t('doc.PAN', {}, 'hi').includes('PAN'));
  assert.ok(!/पैन|आधार कार्ड|इरडा/.test(hiText), 'glossary terms not transliterated');
});

test('STAGES are the six ordered stages with i18n keys', () => {
  assert.deepEqual(J(M.STAGES).map(s => s.k), ['details','documents','verification','training','exam','certificate']);
  for (const s of M.STAGES) for (const l of ['en','hi']) assert.ok(M.I18N[l][s.nameKey]);
});

function allTexts(obj) { // every key reachable from a nextAction/resumePosition result, rendered in both languages
  const out = [];
  for (const lang of ['en','hi']) {
    out.push(M.t(obj.labelKey, obj.params, lang));
    if (obj.descKey) out.push(M.t(obj.descKey, obj.descParams, lang));
  }
  return out;
}

test('nextAction returns exactly one well-formed action per scenario, with the right route', () => {
  const want = {
    not_started: ['start','#apply',true], draft: ['continue','#apply',true], in_progress: ['continue','#documents',true],
    submitted: ['wait','#home',false], verifying: ['wait','#home',false], action_required: ['action-required','#documents',true],
    human_review: ['wait','#home',false], training_pending: ['training','#training',true], training_active: ['training','#training',true],
    exam_pending: ['exam','#exam',true], exam_failed: ['exam','#exam',false], certificate_pending: ['certificate','#certificate',true],
    certified: ['certificate','#certificate',true], rejected: ['action-required','#home',true],
  };
  for (const [k, [pri, route, cta]] of Object.entries(want)) {
    const n = M.nextAction(SCEN[k], { now: NOW });
    assert.ok(!Array.isArray(n), k);
    assert.equal(n.priority, pri, k + ' priority');
    assert.equal(n.route, route, k + ' route');
    assert.equal(n.cta, cta, k + ' cta');
    for (const e of ['en','hi']) assert.ok(M.I18N[e][n.labelKey] && M.I18N[e][n.descKey], `${k} keys in ${e}`);
    for (const txt of allTexts(n)) { assert.ok(txt.trim().length > 0); assert.ok(!/\{[A-Za-z]+\}/.test(txt), k + ' unresolved placeholder: ' + txt); }
  }
});

test('nextAction details: fix list names the documents, training shows hours, retake shows time, wait states have no CTA', () => {
  const fix = M.nextAction(SCEN.action_required);
  assert.equal(fix.labelKey, 'next.fix.many');
  const d = M.t(fix.descKey, fix.descParams, 'en');
  assert.ok(d.includes('Cancelled cheque') && d.includes('Aadhaar, back'), d);
  assert.ok(M.t(fix.descKey, fix.descParams, 'hi').includes('Aadhaar, पीछे का भाग'));
  assert.equal(M.nextAction(S({ status: 'NEEDS_MORE_INFO', decision: 'NEEDS_MORE_INFO', fu: { items: [{ key: 'PAN' }] }, begun: true })).labelKey, 'next.fix.one');
  const tr = M.nextAction(SCEN.training_active);
  assert.equal(M.t(tr.labelKey, tr.params, 'en'), 'Continue training (6 of 15 hours)');
  assert.equal(M.nextAction(SCEN.training_pending).labelKey, 'next.training.start');
  assert.equal(M.nextAction(SCEN.exam_pending).labelKey, 'next.exam.take');
  assert.equal(M.nextAction(SCEN.exam_failed, { now: 2.1e12 }).labelKey, 'next.exam.retake');
  assert.equal(M.nextAction(SCEN.exam_failed, { now: NOW }).labelKey, 'next.exam.wait');
  assert.equal(M.nextAction(SCEN.certified).labelKey, 'next.certificate.view');
  assert.match(M.t(M.nextAction(SCEN.human_review).labelKey, {}, 'en'), /reviewer/);
  assert.equal(M.nextAction(SCEN.rejected).labelKey, 'next.rejected');
});

test('nextAction continues at the resume position', () => {
  for (const k of ['draft','in_progress']) {
    const n = M.nextAction(SCEN[k]), r = M.resumePosition(SCEN[k]);
    assert.equal(n.route, r.route); assert.equal(n.stage, r.stage);
  }
});

test('resumePosition routes each stage', () => {
  const d = M.resumePosition(SCEN.draft);
  assert.equal(d.stage, 'details'); assert.equal(d.page, 3); assert.equal(d.of, 4); assert.equal(d.section, 'sec.details.2');
  assert.equal(M.t(d.labelKey, d.params, 'en'), 'Your details, page 3 of 4: How we reach you');
  assert.equal(M.resumePosition(S({ begun: true, dpage: 3 })).section, 'sec.details.3');
  assert.equal(M.resumePosition(SCEN.not_started).page, 1);
  const docs = M.resumePosition(SCEN.in_progress);
  assert.equal(docs.stage, 'documents'); assert.equal(docs.route, '#documents');
  assert.equal(docs.doc, 'AADHAAR_BACK'); // current slot (index 2) is empty
  const skip = M.resumePosition(S({ begun: true, step: 1, dk: 0, docs: okDocs(['PAN']) }));
  assert.equal(skip.doc, 'AADHAAR_FRONT'); // current slot done: next missing
  assert.equal(M.resumePosition(SCEN.action_required).doc, 'BANK');
  assert.equal(M.resumePosition(SCEN.verifying).stage, 'verification');
  const tr = M.resumePosition(SCEN.training_active);
  assert.equal(tr.stage, 'training'); assert.equal(tr.page, 3); assert.equal(tr.route, '#training');
  assert.equal(M.t(tr.labelKey, tr.params, 'en'), 'Training: module 3 of 5 (6 of 15 hours)');
  assert.equal(M.resumePosition(SCEN.exam_pending).stage, 'exam');
  assert.equal(M.resumePosition(SCEN.certified).stage, 'certificate');
  for (const k of Object.keys(SCEN)) {
    const r = M.resumePosition(SCEN[k]);
    assert.ok(r.route.startsWith('#'), k);
    for (const lang of ['en','hi']) assert.ok(!/\{[A-Za-z]+\}/.test(M.t(r.labelKey, r.params, lang)), k);
  }
});

test('progressModel keeps journey, form, documents, training, exam and certificate separate', () => {
  const p = M.progressModel(SCEN.in_progress);
  assert.equal(p.journey.step, 1); assert.equal(p.journey.of, 6); assert.equal(p.journey.stagesDone, 1);
  assert.equal(p.form.pagesDone, 4); assert.equal(p.form.of, 4);
  assert.equal(p.documents.uploaded, 2); assert.equal(p.documents.verified, 2); assert.deepEqual(J(p.documents.missing), ['AADHAAR_BACK','CLASS10','BANK','SELFIE']);
  assert.equal(p.compliance, null);
  assert.ok(!('percent' in p) && !('pct' in p) && !('overall' in p));
  const d = M.progressModel(SCEN.draft);
  assert.equal(d.form.pagesDone, 2); assert.equal(d.documents.uploaded, 0); assert.equal(d.journey.stagesDone, 0);
  const a = M.progressModel(SCEN.action_required);
  assert.equal(a.documents.uploaded, 6); assert.equal(a.documents.actionRequired, 2); assert.equal(a.documents.verified, 4);
  assert.deepEqual(J(a.compliance), { passed: 1, pending: 0, total: 2 });
  const proc = M.progressModel(S({ begun: true, step: 1, docs: { PAN: { status: 'reading' }, SELFIE: { status: 'rejected' } } }));
  assert.equal(proc.documents.processing, 1); assert.equal(proc.documents.uploaded, 0);
  assert.ok(!proc.documents.missing.includes('PAN') && proc.documents.missing.includes('SELFIE'));
  const t3 = M.progressModel(SCEN.training_active);
  assert.deepEqual(J(t3.training), { hours: 6, of: 15, done: 2, modules: 5 });
  assert.equal(t3.exam.state, 'locked'); assert.equal(t3.certificate.state, 'locked');
  assert.equal(M.progressModel(SCEN.exam_pending).exam.state, 'available');
  assert.equal(M.progressModel(SCEN.exam_failed).exam.state, 'failed');
  assert.equal(M.progressModel(SCEN.exam_failed).exam.retakeAt, 2e12);
  assert.equal(M.progressModel(SCEN.certificate_pending).certificate.state, 'pending');
  const c = M.progressModel(SCEN.certified);
  assert.equal(c.certificate.state, 'issued'); assert.equal(c.exam.state, 'passed'); assert.equal(c.journey.stagesDone, 6); assert.equal(c.exam.score, 80);
});

test('progressModel has no contradictions (late journey stage implies documents and earlier stages are in place)', () => {
  for (const [k, s] of Object.entries(SCEN)) {
    const p = M.progressModel(s);
    if (p.journey.step >= 3) { assert.equal(p.documents.verified, 6, k + ' docs verified'); assert.ok(p.journey.stagesDone >= 3, k + ' stagesDone'); }
    if (p.journey.step >= 2) assert.equal(p.form.pagesDone, 4, k + ' form');
    assert.equal(p.documents.uploaded + p.documents.processing + p.documents.missing.length, 6, k);
    assert.ok(p.documents.verified <= p.documents.uploaded, k);
    if (p.certificate.state === 'issued') assert.equal(p.journey.stagesDone, 6);
    if (p.training.hours >= 15) assert.equal(p.training.done, 5);
  }
});

test('can(): applicants never get staff views; staff roles are scoped', () => {
  const staffViews = ['view.review','view.evaluation','view.audit','review.decide','admin.assign','reports.read'];
  const appViews = ['view.dashboard','view.application','view.documents','view.training','view.exam','view.certificate','view.notifications','view.history','view.account'];
  for (const a of staffViews) assert.equal(M.can('applicant', a), false, a);
  for (const a of appViews) assert.equal(M.can('applicant', a), true, a);
  assert.equal(M.can('reviewer', 'view.review'), true); assert.equal(M.can('reviewer', 'review.decide'), true);
  assert.equal(M.can('reviewer', 'view.evaluation'), false); assert.equal(M.can('reviewer', 'admin.assign'), false);
  assert.equal(M.can('ops', 'view.audit'), true); assert.equal(M.can('ops', 'view.evaluation'), true); assert.equal(M.can('ops', 'reports.read'), true);
  assert.equal(M.can('ops', 'review.decide'), false); assert.equal(M.can('ops', 'admin.assign'), false);
  assert.equal(M.can('admin', 'admin.assign'), true); assert.equal(M.can('admin', 'view.audit'), true);
  for (const r of ['reviewer','ops','admin']) assert.equal(M.can(r, 'view.account'), true);
  assert.equal(M.can('hacker', 'view.dashboard'), false);
  assert.equal(M.can('constructor', 'view.dashboard'), false);
  assert.equal(M.can('applicant', 'view.unknown'), false);
  assert.equal(M.can(undefined, 'view.dashboard'), false);
  assert.deepEqual(J(M.USER_ROLES), ['applicant','reviewer_pending','reviewer','ops','admin']);
  // A reviewer request waiting for an administrator has no reviewer power and no applicant views.
  for (const a of staffViews.concat(appViews)) assert.equal(M.can('reviewer_pending', a), false, 'pending ' + a);
  assert.equal(M.can('reviewer_pending', 'view.wait'), true);
  // Only an administrator decides reviewer requests.
  assert.equal(M.can('admin', 'admin.requests'), true);
  for (const r of ['applicant','reviewer_pending','reviewer','ops']) assert.equal(M.can(r, 'admin.requests'), false, r);
});

test('applicantTimeline filters to applicant-safe events and sorts chronologically', () => {
  const ev = [
    { t: 30, event: 'DOCS_UPLOADED', actor: 'APPLICANT', audience: 'applicant', note: 'PAN ABCDE1234F uploaded', round: 1 },
    { t: 10, event: 'ONBOARDING_OPENED', actor: 'APPLICANT', audience: 'applicant' },
    { t: 40, event: 'FRAUD_DESK_REFERRAL', actor: 'SYSTEM', audience: 'internal', note: 'fraud' },
    { t: 41, event: 'REVIEWER_DECISION', actor: 'REVIEWER', audience: 'internal', note: 'internal note' },
    { t: 42, event: 'ROUTED_TO_REVIEWER_DETAIL', actor: 'SYSTEM', audience: 'internal' },
    { t: 43, event: 'FOCUS_EXIT', actor: 'APPLICANT', audience: 'applicant' },
    { t: 44, event: 'COPILOT_ANSWER', actor: 'SYSTEM', audience: 'applicant' },
    { t: 45, event: 'DECISION', actor: 'SYSTEM', audience: 'applicant', engine: 'x', hash: 'abc', rules: ['R1'] },
    { t: 46, event: 'SECURITY_LOGIN_FAILED', actor: 'SYSTEM', audience: 'applicant' },
    { t: 47, event: 'EXAM_SUBMIT', actor: 'SYSTEM', audience: 'applicant', note: 'Score 80%' },
    { t: 20, event: 'FOLLOWUP_SENT', actor: 'SYSTEM', audience: 'applicant', doc: 'BANK' },
    { t: 25, event: 'ROUTED_TO_REVIEWER', actor: 'SYSTEM', audience: 'applicant', note: 'Decision HUMAN_REVIEW · NAME_MISMATCH' },
    { t: 26, event: 'document_accepted', doc: 'PAN', actor: 'policynest', audience: 'applicant', secret: 'x' },
    { t: 27, key: 'needs_correction', doc: 'NOT_A_SLOT', internal: true },
    { t: 28, event: 'DOCS_UPLOADED', security: true },
  ];
  const tl = J(M.applicantTimeline(ev));
  assert.deepEqual(tl.map(x => x.t), [10, 20, 25, 26, 30, 47]);
  assert.deepEqual(tl.map(x => x.key), ['tl.application_created','tl.more_info_requested','tl.human_review_started','tl.document_accepted','tl.documents_received','tl.exam_result']);
  assert.equal(tl[0].actor, 'you'); assert.equal(tl[1].actor, 'policynest'); assert.equal(tl[1].doc, 'BANK'); assert.deepEqual(tl[1].params, { docKey: 'doc.BANK' });
  for (const it of tl) {
    assert.deepEqual(Object.keys(it).sort(), ['actor','doc','key','params','stage','t']);
    assert.ok(M.I18N.en[it.key] && M.I18N.hi[it.key]);
    assert.ok(['you','policynest','reviewer'].includes(it.actor));
  }
  const blob = JSON.stringify(tl);
  for (const bad of ['ABCDE1234F','fraud','internal note','Score','NAME_MISMATCH','hash','engine','R1']) assert.ok(!blob.includes(bad), bad);
  assert.deepEqual(J(M.applicantTimeline(null)), []);
  assert.deepEqual(J(M.applicantTimeline([null, 5, 'x', {}])), []);
});

test('t(): named placeholders, fallback to English, then to the key', () => {
  assert.equal(M.t('save.last', { time: '15 Nov, 14:05' }, 'en'), 'Last saved 15 Nov, 14:05');
  assert.equal(M.t('save.last', { time: '15 Nov, 14:05' }, 'hi'), 'आख़िरी बार सहेजा गया: 15 Nov, 14:05');
  assert.equal(M.t('next.fix.many', { n: 3 }, 'en'), 'Fix 3 documents');
  assert.equal(M.t('save.saving', {}, 'en'), 'Saving…');
  assert.equal(M.t('save.saving', null, 'hi'), 'सहेजा जा रहा है…');
  assert.equal(M.t('save.saved'), 'Saved');
  assert.equal(M.t('save.saved', {}, 'fr'), 'Saved');            // unknown language: English
  assert.equal(M.t('no.such.key', {}, 'hi'), 'no.such.key');     // unknown key: the key
  assert.equal(M.t('save.last', {}, 'en'), 'Last saved ');       // missing param: empty, never "undefined"
  assert.equal(M.t('resume.documents', { docKey: 'doc.PAN' }, 'hi'), 'दस्तावेज़: PAN कार्ड');
  assert.equal(M.t('next.fix.desc', { docsKeys: ['doc.PAN','doc.SELFIE'] }, 'en'), 'Documents to fix: PAN card, Selfie. Send them together and we re-check only what changed.');
  // a key missing from hi falls back to English at runtime
  M.I18N.en['__only_en'] = 'English only {x}';
  assert.equal(M.t('__only_en', { x: 1 }, 'hi'), 'English only 1');
  delete M.I18N.en['__only_en'];
  for (const k of ['save.saving','save.saved','save.failed','save.offline','save.last']) { assert.ok(M.I18N.en[k]); assert.ok(M.I18N.hi[k]); }
  const nt = Object.keys(M.I18N.en).filter(k => k.startsWith('nt.'));
  for (const k of ['application_created','progress_saved','documents_received','processing_completed','document_accepted','needs_correction','verification_started','human_review_started','more_info_requested','training_unlocked','training_milestone','exam_unlocked','exam_result','certificate_issued','status_changed'])
    assert.ok(nt.includes('nt.' + k), k);
});

test('no sensitive identifier can come out of t(), nextAction, resumePosition, progressModel or the timeline', () => {
  const PAN = 'ABCDE1234F', AAD = '123456789012', ACCT = '123456789012345', PAN_LC = 'abcde1234f';
  const bad = (s0) => { const s = s0.replace(/"(retakeAt|t)":\d+/g, '"$1":0'); return /\b[A-Za-z]{5}\d{4}[A-Za-z]\b/.test(s) || /\d{12}/.test(s) || /\d{9,18}/.test(s.replace(/\d{9,18}\.\d+/g, '')); };
  const secret = S({ begun: true, step: 1, status: 'NEEDS_MORE_INFO', decision: 'NEEDS_MORE_INFO', docs: okDocs(DOCS),
    f: { name: 'Asha Verma', pan: PAN, aadhaar4: '9012', acct: ACCT, ifsc: 'SBIN0001234' }, appId: 'PN-2026-12345', posp: 'PN-POSP-12345',
    fu: { items: [{ key: 'BANK', note: ACCT }, { key: 'PAN', note: PAN }] },
    checks: [{ res: 'NEEDS_INFO', label: 'PAN ' + PAN + ' vs Aadhaar ' + AAD }],
    resume: { action: "Typed " + PAN, t: 5 } });
  const outs = [];
  for (const s of [secret, ...Object.values(SCEN)]) {
    const n = M.nextAction(s, { now: NOW }), r = M.resumePosition(s), p = M.progressModel(s);
    outs.push(JSON.stringify(n), JSON.stringify(r), JSON.stringify(p));
    for (const lang of ['en','hi']) { outs.push(...allTexts(n)); outs.push(M.t(r.labelKey, r.params, lang)); }
  }
  const ev = [{ t: 1, event: 'DOCS_UPLOADED', actor: 'APPLICANT', audience: 'applicant', note: `PAN ${PAN} aadhaar ${AAD} account ${ACCT}`, doc: 'PAN', pan: PAN, acct: ACCT }];
  outs.push(JSON.stringify(M.applicantTimeline(ev)));
  for (const lang of ['en','hi']) for (const it of M.applicantTimeline(ev)) outs.push(M.t(it.key, it.params, lang));
  // hostile params are scrubbed by t() itself
  outs.push(M.t('save.last', { timeKey: AAD }, 'en'), M.t('acct.signed_in_as', { nameKey: ACCT }, 'hi'), M.t('next.fix.desc', { docsKeys: [PAN, ACCT] }, 'en'));
  assert.ok(M.t('save.last', { timeKey: '123456789012345' }, 'en').includes('[hidden]'));
  for (const v of [PAN, PAN_LC, AAD, '1234 5678 9012', ACCT, 'a ' + ACCT + ' b', 123456789012, 12345678901234])
    outs.push(M.t('acct.signed_in_as', { name: v }, 'en'), M.t('save.last', { time: v }, 'hi'));
  assert.ok(M.t('acct.signed_in_as', { name: PAN }, 'en').includes('[hidden]'));
  for (const o of outs) assert.ok(!bad(o) , 'sensitive pattern in: ' + o.slice(0, 120));
  // ordinary values survive
  assert.equal(M.t('acct.signed_in_as', { name: 'Asha Verma' }, 'en'), 'Signed in as Asha Verma');
  assert.equal(M.t('next.fix.many', { n: 12 }, 'en'), 'Fix 12 documents');
});

test('the model region is pure: no DOM or browser globals referenced', () => {
  const stripped = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/"(?:[^"\\]|\\.)*"/g, '""');
  for (const g of ['document.', 'window.', 'localStorage', 'navigator.', 'fetch(', 'getElementById']) assert.ok(!stripped.includes(g), g);
});

test('follow-up with every fix already re-uploaded: one action, re-check', () => {
  const s = S({ begun: true, step: 1, status: 'NEEDS_MORE_INFO', decision: 'NEEDS_MORE_INFO', docs: okDocs(DOCS), fu: { items: [{ key: 'PAN' }] }, changed: { PAN: 1 } });
  const n = M.nextAction(s, { now: NOW });
  assert.equal(n.labelKey, 'next.recheck'); assert.equal(n.descKey, 'next.recheck.desc');
  for (const lang of ['en','hi']) assert.ok(!/\{|: \./.test(M.t(n.descKey, n.descParams, lang)));
  const r = M.resumePosition(s);
  assert.equal(r.doc, null); assert.equal(r.labelKey, 'resume.documents.recheck'); assert.equal(r.milestone, 'recheck');
  assert.equal(M.progressModel(s).documents.actionRequired, 0);
});

test('documents.verified excludes docs flagged by instant feedback; unassessed docs are not flagged', () => {
  const s = S({ begun: true, step: 1, docs: okDocs(['PAN','BANK']), fb: { PAN: { stop: [], fix: [{ what: 'x' }] }, BANK: { stop: [], fix: [] } } });
  const d = M.progressModel(s).documents;
  assert.equal(d.uploaded, 2); assert.equal(d.verified, 1); assert.equal(d.actionRequired, 1); assert.deepEqual(J(d.actionKeys), ['PAN']);
  const s2 = S({ begun: true, step: 1, docs: okDocs(['PAN']) });
  assert.equal(M.progressModel(s2).documents.verified, 1);
});

test('doc status photo counts as missing, not processing', () => {
  const s = S({ begun: true, step: 1, docs: { PAN: { status: 'photo' }, AADHAAR_FRONT: { status: 'reading' } } });
  const d = M.progressModel(s).documents;
  assert.ok(J(d.missing).includes('PAN')); assert.equal(d.processing, 1); assert.equal(d.uploaded, 0);
});

test('t() keeps ISO timestamps and scrubs grouped numbers', () => {
  assert.equal(M.t('save.last', { time: '2026-11-15 14:05' }, 'en'), 'Last saved 2026-11-15 14:05');
  assert.ok(M.t('save.last', { time: '1234 5678 9012' }, 'en').includes('[hidden]'));
});

test('resumePosition derives lastAction from S.events when S.resume is absent', () => {
  const s = S({ begun: true, step: 1, docs: okDocs(['PAN']), events: [{ t: 5, event: 'ONBOARDING_OPENED', actor: 'APPLICANT' }, { t: 9, event: 'REGISTRATION_STARTED', actor: 'APPLICANT' }] });
  const r = M.resumePosition(s);
  assert.equal(r.lastAction, 'details_started'); assert.equal(r.t, 9);
});
