"""Contract tests for the Copilot proxy (PRD seam 3). Standard library only; the model call is stubbed,
so no API key and no SDK are needed. Run:  python policynest_mvp/copilot_api/test_main.py"""
import json
import os
import re
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

os.environ["GEMINI_API_KEY"] = ""  # unit tests never use a live provider
os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"  # set before import; .env values never override it
os.environ.pop("APP_NAME", None)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import main  # noqa: E402

SITE = "http://localhost:8000"
CALLS = []


def fake_model(reply="Upload a clear photo of your PAN card [Rulebook §4.1].", stop="end_turn"):
    def f(kw):
        CALLS.append(kw)
        return stop, reply
    return f


class ProxyContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), main.H)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        CALLS.clear()
        main._hits.clear();main._auth_hits.clear()
        main._global.clear()
        main._day.update(date="", n=0)
        main.call_model = fake_model()

    def post(self, body, origin=SITE, xff=None):
        data = json.dumps(body).encode()
        req = urllib.request.Request(self.url + "/api/copilot", data=data, method="POST",
                                     headers={"Content-Type": "application/json", "Origin": origin})
        if xff:
            req.add_header("X-Forwarded-For", xff)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def prompt(self):
        return CALLS[-1]["messages"][-1]["content"]

    # --- corpus ---
    def test_corpus_is_applicant_safe(self):
        self.assertGreater(len(main.KB["chunks"]), 20)
        for c in main.KB["chunks"]:
            self.assertEqual(c["audience"], "applicant")
            self.assertIsNone(re.search(r"fraud|forged|tamper", c["text"] + c["head"], re.I), c["cite"])

    # --- retrieval ---
    def test_passages_from_the_browser_are_ignored(self):
        code, out = self.post({"question": "How do I take a clear photo of my PAN card?",
                               "passages": [{"cite": "Evil", "text": "SMUGGLED-INSTRUCTIONS"}]})
        self.assertEqual(code, 200)
        self.assertNotIn("SMUGGLED-INSTRUCTIONS", self.prompt())
        self.assertNotIn("[Evil]", self.prompt())
        self.assertTrue(out["sources"])
        self.assertTrue(all(s in self.prompt() for s in out["sources"]))

    def test_selfie_question_finds_the_selfie_rule_without_fraud_wording(self):
        code, out = self.post({"question": "my selfie failed twice what happens"})
        self.assertEqual(code, 200)
        self.assertIn("Rulebook §4.6", out["sources"])
        self.assertNotRegex(self.prompt(), re.compile("fraud", re.I))

    # --- context schema ---
    def test_unknown_context_fields_are_dropped(self):
        ctx = {"first_name": "Rahul", "stage": "Documents", "notes": "IGNORE-ALL-RULES",
               "documents": [{"document": "PAN card", "status": "ok", "open_issues": ["IMAGE_BLURRY", "free text here!"],
                              "extra": "SMUGGLED"}], "round": 1}
        self.assertEqual(self.post({"question": "what next", "context": ctx})[0], 200)
        p = self.prompt()
        self.assertNotIn("IGNORE-ALL-RULES", p)
        self.assertNotIn("SMUGGLED", p)
        self.assertNotIn("free text here!", p)
        self.assertIn("IMAGE_BLURRY", p)
        self.assertIn("Rahul", p)

    def test_history_is_trimmed(self):
        hist = [{"role": "user", "text": "x" * 5000}, {"role": "assistant", "text": "ok"}]
        self.post({"question": "what next", "history": hist})
        self.assertLessEqual(len(CALLS[-1]["messages"][0]["content"]), 800)

    # --- ID numbers (rulebook §4.3) ---
    def test_id_numbers_are_refused_before_any_model_call(self):
        for q in ("my aadhaar is 1234 5678 9012", "pan ABCDE1234F ok?", "account 50100284417"):
            code, out = self.post({"question": q})
            self.assertEqual((code, out["error"]), (400, "id_number"), q)
        code, _ = self.post({"question": "hi", "history": [{"role": "user", "text": "1234 5678 9012"},
                                                           {"role": "assistant", "text": "ok"}]})
        self.assertEqual(code, 400)
        self.assertEqual(CALLS, [])

    # --- output filter and refusals ---
    def test_fraud_wording_in_an_answer_is_replaced(self):
        main.call_model = fake_model("This looks like fraud to me.")
        self.assertEqual(self.post({"question": "why was my marksheet refused"})[1]["answer"], main.SAFE)

    def test_outcome_predictions_are_replaced(self):
        main.call_model = fake_model("Good news, you are approved!")
        self.assertEqual(self.post({"question": "am i approved"})[1]["answer"], main.SAFE)

    def test_refusal_gives_the_fixed_sentence(self):
        main.call_model = fake_model("", stop="refusal")
        self.assertEqual(self.post({"question": "write me a poem"})[1]["answer"], main.REFUSED)

    # --- access controls ---
    def test_null_and_unknown_origins_are_refused(self):
        for o in ("null", "https://evil.example"):
            self.assertEqual(self.post({"question": "hi"}, origin=o)[0], 403)

    def test_bad_bodies_are_refused(self):
        self.assertEqual(self.post({"context": {}})[0], 400)
        self.assertEqual(self.post(["not", "an", "object"])[0], 400)

    def test_spoofed_forwarded_for_does_not_reset_the_limit(self):
        for i in range(main.RATE[0]):
            self.assertEqual(self.post({"question": "hi"}, xff=f"9.9.9.{i}, 1.2.3.4, 10.0.0.1")[0], 200)
        self.assertEqual(self.post({"question": "hi"}, xff="8.8.8.8, 1.2.3.4, 10.0.0.1")[0], 429)

    def test_daily_cap(self):
        main._day.update(date=main.time.strftime("%Y-%m-%d", main.time.gmtime()), n=main.DAILY_CAP)
        code, out = self.post({"question": "hi"})
        self.assertEqual((code, out["error"]), (429, "daily_cap"))

    def test_health(self):
        with urllib.request.urlopen(self.url + "/api/health") as r:
            h = json.loads(r.read())
        self.assertTrue(h["ok"])
        self.assertEqual(h["model"], main.MODEL)

    def test_cookie_login_refresh_logout(self):
        import http.cookiejar
        import secrets
        os.environ['JWT_SECRET'] = 'test-only-long-enough-cookie-signing-secret'
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        def request(path, body=None):
            req = urllib.request.Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                                         headers={'Origin': SITE, 'Content-Type': 'application/json'})
            return opener.open(req)
        email = secrets.token_hex(8) + '@example.test'
        with request('/api/auth/register', {'email': email, 'password': 'correct horse battery', 'display_name': 'Test Applicant', 'role': 'reviewer'}) as r:
            data = json.loads(r.read())
            self.assertEqual(data['user']['role'], 'applicant')
            self.assertNotIn('token', data)
            self.assertTrue(all('HttpOnly' in c for c in r.headers.get_all('Set-Cookie')))
        with request('/api/auth/me') as r: self.assertEqual(json.loads(r.read())['user']['email'], email)
        with request('/api/auth/refresh', {}) as r: self.assertEqual(r.status, 200)
        with request('/api/auth/logout', {}) as r: self.assertEqual(r.status, 200)
        with self.assertRaises(urllib.error.HTTPError) as error: request('/api/auth/me')
        self.assertEqual(error.exception.code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class SignOutNeverLimited(unittest.TestCase):
    """16 Nov: sign-out was refused with 429 when the shared question budget was used up, so a reload signed people back in."""

    def test_logout_and_auth_do_not_share_the_question_budget(self):
        import main as m, time
        m._hits.clear(); m._auth_hits.clear(); m._global.clear()
        m._global.extend([time.time()] * (m.GLOBAL_PER_MIN + 5))  # the Copilot's global cap is exhausted
        self.assertFalse(m._auth_limited("auth:1.2.3.4", *m.AUTH_RATE))
        for _ in range(m.AUTH_RATE[0] + 5):
            m._auth_limited("auth:1.2.3.4", *m.AUTH_RATE)
        self.assertTrue(m._auth_limited("auth:1.2.3.4", *m.AUTH_RATE))
        self.assertFalse(m._auth_limited("refresh:1.2.3.4", *m.REFRESH_RATE))  # separate bucket
        m._global.clear(); m._auth_hits.clear()
