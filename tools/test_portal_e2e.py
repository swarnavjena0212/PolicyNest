"""End-to-end continuity test for the applicant portal (lane P5): saving, second device, 409, notifications,
timeline, profile and sensitive-data checks.

Usage:  python tools/test_portal_e2e.py

Starts, on its own, the backend (port 9480, temporary database, random JWT secret, COPILOT_ORIGINS set to the
local site origin) and a static server for the built site (port 9481). Both are stopped before exit. Needs
policynest_mvp/site-demo/index.html (run `python policynest_mvp/build_site.py --mode demo` first; the test uses the sample applicant and specimens) and Python Playwright.
All data is synthetic. Exit code 0 only when every check passes.
"""
import http.server
import json
import os
import re
import secrets
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PORT, SITE_PORT = 9480, 9481
API = "http://127.0.0.1:%d" % API_PORT
SITE = "http://127.0.0.1:%d" % SITE_PORT
PASSWORD = "Synthetic-Test-Pw-" + secrets.token_hex(4)  # generated per run, never stored
RESULTS = []



def close_camera_check(page):
    """Start opens the 'Check your camera' dialog once (lane VC). Close it with Continue so the test goes on; uploads never need it."""
    page.wait_for_selector("#camdlg[open]", timeout=15000)
    page.click('#camdlg [data-act="cam-cont"]')
    page.wait_for_function("()=>!document.getElementById('camdlg').open", timeout=15000)

def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + str(detail)[:1200]) if (detail and not ok) else ""), flush=True)


# ---------------------------------------------------------------- servers
LAUNCH = r"""
import os, sys
sys.path.insert(0, os.path.join(%r, 'policynest_mvp', 'copilot_api'))
import main
main.RATE = (10**6, 600); main.GLOBAL_PER_MIN = 10**6
main.auth.init_db(); main.workflows.init(); main.telemetry.init()
from http.server import ThreadingHTTPServer
ThreadingHTTPServer(('127.0.0.1', %d), main.H).serve_forever()
""" % (ROOT, API_PORT)


def start_backend(tmp):
    env = dict(os.environ, POLICYNEST_DB_PATH=os.path.join(tmp, "e2e.sqlite3"), JWT_SECRET=os.urandom(32).hex(),
               COPILOT_ORIGINS=SITE, POLICYNEST_SKIP_DOTENV="1", ANTHROPIC_API_KEY="", PYTHONDONTWRITEBYTECODE="1")
    env.pop("APP_NAME", None)
    p = subprocess.Popen([sys.executable, "-c", LAUNCH], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(API + "/api/health", timeout=1).read()
            return p
        except Exception:
            time.sleep(0.5)
    p.kill()
    raise SystemExit("backend did not start")


def start_static(tmp):
    src = open(os.path.join(ROOT, "policynest_mvp", "site-demo", "index.html"), encoding="utf-8").read()
    assert "'http://localhost:8787'" in src, "COPILOT_URL pattern changed"
    src = src.replace("'http://localhost:8787'", "'" + API + "'")
    web = os.path.join(tmp, "web")
    os.makedirs(web)
    open(os.path.join(web, "index.html"), "w", encoding="utf-8").write(src)
    for f in ("kb.json", "ocr-worker.js", "pdf-worker.js"):
        fp = os.path.join(ROOT, "policynest_mvp", "site-demo", f)
        if os.path.exists(fp):
            shutil.copy(fp, web)

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=web, **k)

        def log_message(self, *a):
            pass

    class T(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = T(("127.0.0.1", SITE_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- helpers
def launch(pw):
    try:
        return pw.chromium.launch()
    except Exception:
        return pw.chromium.launch(channel="chrome")


class Dev:
    """One browser context = one device. Records page errors, request bodies and local storage."""

    def __init__(self, browser, label):
        self.label = label
        self.ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        self.page = self.ctx.new_page()
        self.errors, self.bodies = [], []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("request", lambda r: self.bodies.append((r.method, r.url, r.post_data or "")) if r.url.startswith(API) else None)
        self.page.add_init_script("""
          window.__sv=[]; document.addEventListener('DOMContentLoaded',function(){
            var el=document.getElementById('savestate'); if(!el) return;
            new MutationObserver(function(){var t=el.textContent.trim(); if(t&&window.__sv[window.__sv.length-1]!==t) window.__sv.push(t)}).observe(el,{childList:true,subtree:true,characterData:true});});""")

    def goto(self, hash_=""):
        self.page.goto(SITE + "/?demo=1&v=" + secrets.token_hex(3) + hash_)
        self.page.wait_for_selector("#main", state="attached")

    def api(self, method, path, body=None):
        return self.page.evaluate(
            """async ([m,p,b])=>{const r=await fetch(%r+p,{method:m,credentials:'include',headers:{'Content-Type':'application/json'},body:b===null?undefined:JSON.stringify(b)});
               let j=null;try{j=await r.json()}catch(e){} return {status:r.status,json:j}}""" % API, [method, path, body])

    def sv(self):
        return self.page.evaluate("window.__sv||[]")

    def savestate(self):
        return self.page.inner_text("#savestate")

    def wait_saved(self, timeout=15000):
        self.page.wait_for_function("()=>/Saved/.test(document.getElementById('savestate').textContent)&&!/Saving/.test(document.getElementById('savestate').textContent)", timeout=timeout)

    def close(self):
        self.ctx.close()


def until(fn, timeout=15.0, step=0.3):
    """Poll fn() until truthy (server-side effects are debounced, so a fixed sleep is not enough)."""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(step)
    return last


def register_ui(dev, name, email, sync=True):
    dev.goto("#welcome")
    dev.page.click('[data-act="auth-open"][data-mode="register"]')
    dev.page.fill("#account-name", name)
    dev.page.fill("#account-email", email)
    dev.page.fill("#account-password", PASSWORD)
    if sync:
        dev.page.check("#account-sync-opt")
    dev.page.click("#account-submit")
    # Sign-up does not sign in: it ends on the sign-in panel with the email kept and a confirmation.
    dev.page.wait_for_selector('[data-auth-as="applicant"][aria-pressed="true"]', timeout=15000)
    msg = dev.page.inner_text("#account-message")
    check("sign-up ends on the sign-in panel with a confirmation, not signed in",
          "created" in msg.lower() and dev.page.input_value("#account-email") == email
          and not dev.page.is_visible("#tb-refv"), msg)
    # Choosing Reviewer with an applicant account is refused with a clear message.
    dev.page.click('[data-auth-as="reviewer"]')
    # Reviewers can now request an account themselves (an administrator approves it), so Create account stays.
    check("reviewer choice keeps Create account (reviewers request an account)",
          dev.page.locator('#account-form [data-auth-mode="register"]').count() == 1)
    dev.page.fill("#account-password", PASSWORD)
    dev.page.click("#account-submit")
    dev.page.wait_for_selector("#account-errors:not([hidden])", timeout=15000)
    check("an applicant account cannot sign in as reviewer", "not a reviewer" in dev.page.inner_text("#account-errors").lower())
    dev.page.click('[data-auth-as="applicant"]')
    dev.page.fill("#account-password", PASSWORD)
    dev.page.click("#account-submit")
    dev.page.wait_for_selector("#tb-refv", timeout=15000)


def login_ui(dev, email):
    dev.goto("#welcome")
    dev.page.click('[data-act="auth-open"][data-mode="login"]')
    dev.page.fill("#account-email", email)
    dev.page.fill("#account-password", PASSWORD)
    dev.page.click("#account-submit")
    dev.page.wait_for_selector("#acct-btn .amn", timeout=15000)


def api_user(dev, name, email):
    """Registers through the API from the page (cookies land in this context). Used for seeded second-device cases."""
    dev.goto("#welcome")
    r = dev.api("POST", "/api/auth/register", {"email": email, "password": PASSWORD, "display_name": name})
    assert r["status"] in (200, 201), r


DET = {"name": "Rahul Kumar Sharma", "dob": "1994-08-14", "email": "rahul.sharma@example.com", "state": "Haryana", "city": "Gurugram",
       "education": "Graduate", "ifsc": "NEST0004521", "aadhaar4": "4417", "pan_last4": "821K", "acct_last4": "4417", "language": "en"}
VERIFIED = {k: {"status": "verified", "version": 1, "issues": []} for k in ("PAN", "AADHAAR_FRONT", "AADHAAR_BACK", "CLASS10", "BANK", "SELFIE")}


def seed(dev, progress, details=DET):
    cur = dev.api("POST", "/api/applications/me", {})
    v = cur["json"]["application"]["version"]
    r = dev.api("PUT", "/api/applications/me", {"version": v, "details": details, "progress": progress})
    assert r["status"] == 200, r


def run(browser):
    ts = secrets.token_hex(3)
    email = "applicant-%s@example.test" % ts

    # ---------------- device A: register, details, documents, verification
    A = Dev(browser, "A")
    register_ui(A, "Rahul Sharma", email)
    for _ in range(40):
        srv = A.api("GET", "/api/applications/me")
        if srv["status"] == 200:
            break
        time.sleep(0.25)
    A.page.wait_for_function("(r)=>document.getElementById('tb-refv').textContent===r", arg=srv["json"]["application"]["reference"], timeout=15000)
    ref_ui = A.page.inner_text("#tb-refv")
    check("reference visible and equals the server reference (not the local one)", srv["status"] == 200 and srv["json"]["application"]["reference"] == ref_ui and re.fullmatch(r"PN-\d{4}-\d{5}", ref_ui), [ref_ui, srv])
    check("reference is not labelled 'not yet registered' once signed in", "not yet" not in A.page.inner_text("#tb-ref").lower(), A.page.inner_text("#tb-ref"))
    apps = A.api("POST", "/api/applications/me", {})
    check("create is idempotent (same reference on repeat)", apps["json"]["application"]["reference"] == ref_ui)
    check("no duplicate application created by the page", A.api("GET", "/api/applications/me")["json"]["application"]["reference"] == ref_ui)

    A.page.evaluate("location.hash='#apply'")
    A.page.click('[data-act="begin"]')
    close_camera_check(A.page)
    A.page.evaluate("document.querySelectorAll('details.demo').forEach(d=>d.open=true)")
    A.page.click('[data-act="sample"]')
    for _ in range(4):
        A.page.click('[data-act="details-next"]')
    A.wait_saved()
    sv = A.sv()
    check("save states shown: Saving then Saved", any("Saving" in t for t in sv) and any("Saved" in t for t in sv), sv)
    app = A.api("GET", "/api/applications/me")["json"]["application"]
    d = app["details"]
    check("details saved to the account (allow-listed, last four only)", d.get("name") == "Rahul Kumar Sharma" and d.get("pan_last4") == "821K" and d.get("acct_last4") == "4417" and d.get("aadhaar4") == "4417", d)
    check("details page reached is saved with a resume position", app["progress"].get("stage") == "documents" and app["progress"].get("resume", {}).get("stage") == "documents", app["progress"])

    A.page.click('[data-act="spec-all"]')
    A.page.wait_for_function("()=>document.querySelectorAll('#view-apply .seg6 i.on, #view-apply [data-dk]').length>0", timeout=15000)
    A.page.wait_for_function("()=>/Submit for verification/.test(document.getElementById('view-apply').innerText)", timeout=20000)
    docs = until(lambda: (lambda d: d if len(d) == 6 else None)(A.api("GET", "/api/applications/me")["json"]["application"]["progress"].get("documents", {}))) or {}
    check("six document statuses saved (statuses only)", len(docs) == 6 and all(v["status"] == "verified" and set(v) == {"status", "version", "issues"} for v in docs.values()), docs)
    A.page.click('[data-act="submit"]')
    A.page.wait_for_function("()=>/Verification complete/.test(document.getElementById('view-apply').innerText)", timeout=40000)
    A.wait_saved()
    A.page.click('[data-act="start-training"]')
    A.page.wait_for_selector('[data-mod="0"]')
    A.page.click('[data-mod="0"]')
    A.page.click('[data-mod="1"]')
    A.page.wait_for_function("()=>/Saved/.test(document.getElementById('savestate').textContent)", timeout=15000)
    time.sleep(2.2)
    A.wait_saved()
    prog = A.api("GET", "/api/applications/me")["json"]["application"]["progress"]
    check("decision, training modules and stage saved", prog.get("decision") == "APPROVE" and prog.get("training", {}).get("modules") == ["m1", "m2"] and prog.get("stage") == "training", prog)

    # ---------------- notifications and timeline (server-derived from events)
    time.sleep(1.5)
    n = A.api("GET", "/api/notifications")["json"]
    check("notifications created by events", n["unread"] > 0 and len(n["items"]) > 0, n)
    A.page.evaluate("location.hash='#dashboard'")
    A.page.evaluate("location.hash='#notifications'")
    A.page.wait_for_selector("#view-notif .nlist li", timeout=10000)
    badge = A.page.inner_text(".bell .bdg") if A.page.query_selector(".bell .bdg") else ""
    check("unread count badge in the shell", badge.strip() != "", badge)
    check("bell has an accessible name with the unread count", "unread" in (A.page.get_attribute(".bell", "aria-label") or ""), A.page.get_attribute(".bell", "aria-label"))
    check("notification rows have text, time and an action", A.page.query_selector("#view-notif .nlist li .nacts button") is not None)
    first = A.page.query_selector('#view-notif [data-act="notif-read"]')
    before = A.page.evaluate("document.querySelectorAll('#view-notif .nrow.unread').length")
    first.click()
    A.page.wait_for_function("(b)=>document.querySelectorAll('#view-notif .nrow.unread').length===b-1", arg=before, timeout=8000)
    check("mark one as read updates the row", True)
    A.page.click('[data-act="notif-readall"]')
    A.page.wait_for_function("()=>document.querySelectorAll('#view-notif .nrow.unread').length===0", timeout=8000)
    check("mark all as read: server unread is zero", A.api("GET", "/api/notifications")["json"]["unread"] == 0)
    check("badge disappears at zero", A.page.query_selector(".bell .bdg") is None)

    A.page.evaluate("location.hash='#history'")
    A.page.wait_for_function("()=>/Document accepted/.test(document.getElementById('view-hist').innerText)", timeout=10000)
    txt = A.page.inner_text("#view-hist")
    keys = [i["key"] for i in A.api("GET", "/api/applications/me/timeline")["json"]["items"]]
    check("timeline lists your actions and PolicyNest's", "You started your application" in txt and "Document accepted" in txt and "Training milestone" in txt and "PolicyNest" in txt, [txt[-700:], ("started", "You started your application" in txt, "accepted", "Document accepted" in txt, "milestone", "Training milestone" in txt, "ps", "PolicyNest" in txt), keys[-4:]])
    check("timeline highlights the current status and the latest entry", "Current status" in txt and A.page.query_selector("#view-hist li.cur") is not None)
    tl = A.api("GET", "/api/applications/me/timeline")["json"]["items"]
    check("timeline events carry only keys, stage and document slot", all(set(i) <= {"id", "t", "actor", "key", "stage", "doc"} for i in tl) and len(tl) >= 8, tl[:3])

    # ---------------- profile
    A.page.evaluate("location.hash='#account'")
    A.page.click('[data-act="prof-edit"]')
    A.page.fill("#pf-name", "R")
    A.page.fill("#pf-mobile", "12345")
    A.page.click('#prof-form button[type="submit"]')
    A.page.wait_for_selector("#pf-errors:not([hidden])")
    errtxt = A.page.inner_text("#pf-errors")
    check("profile validation shows an error summary with both problems", "name" in errtxt.lower() and "mobile" in errtxt.lower(), errtxt)
    check("error summary takes focus", A.page.evaluate("document.activeElement&&document.activeElement.id")=="pf-errors")
    A.page.fill("#pf-name", "Rahul K Sharma")
    A.page.fill("#pf-mobile", "9876543210")
    A.page.fill("#pf-city", "Noida")
    A.page.select_option("#pf-lang", "en")
    A.page.click('#prof-form button[type="submit"]')
    A.page.wait_for_selector("#view-acct .okmsg", timeout=8000)
    me = A.api("GET", "/api/me")["json"]["user"]
    check("profile saved through PATCH /api/me (name, masked mobile)", me["display_name"] == "Rahul K Sharma" and (me["mobile_masked"] or "").endswith("3210"), me)
    acct_text = A.page.inner_text("#view-acct")
    check("profile never shows PAN, Aadhaar or bank numbers", not re.search(r"[A-Z]{5}\d{4}[A-Z]|\d{9,}", acct_text), acct_text[:300])
    check("optional city is saved with the application", until(lambda: A.api("GET", "/api/applications/me")["json"]["application"]["details"].get("city") == "Noida"))

    # ---------------- save failure, retry, offline
    holder = {"mode": "fail"}

    def handler(route):
        req = route.request
        if req.method == "PUT":
            if holder["mode"] == "fail":
                return route.fulfill(status=500, headers={"access-control-allow-origin": SITE, "access-control-allow-credentials": "true", "content-type": "application/json"}, body='{"error":"boom"}')
            if holder["mode"] == "net":
                return route.abort()
        return route.continue_()

    A.page.route("**/api/applications/me", handler)
    A.page.evaluate("location.hash='#account'")
    A.page.click('[data-act="prof-edit"]')
    A.page.fill("#pf-city", "Jaipur")
    A.page.click('#prof-form button[type="submit"]')
    A.page.wait_for_selector('#savestate [data-act="save-retry"]', timeout=12000)
    check("Save failed shows an accessible Retry button", "Save failed" in A.savestate() and A.page.get_attribute('#savestate [data-act="save-retry"]', "aria-label"), A.savestate())
    holder["mode"] = "ok"
    A.page.click('#savestate [data-act="save-retry"]')
    A.wait_saved()
    check("Retry saves the pending change", until(lambda: A.api("GET", "/api/applications/me")["json"]["application"]["details"].get("city") == "Jaipur"))
    holder["mode"] = "net"
    A.page.evaluate("location.hash='#account'")
    A.page.click('[data-act="prof-edit"]')
    A.page.fill("#pf-city", "Pune")
    A.page.click('#prof-form button[type="submit"]')
    A.page.wait_for_function("()=>/Offline changes pending/.test(document.getElementById('savestate').textContent)", timeout=12000)
    check("network loss shows Offline changes pending", True)
    holder["mode"] = "ok"
    A.page.evaluate("window.dispatchEvent(new Event('online'))")
    A.wait_saved()
    check("saved again after reconnect (flush on online)", until(lambda: A.api("GET", "/api/applications/me")["json"]["application"]["details"].get("city") == "Pune"))
    A.page.unroute("**/api/applications/me")

    # ---------------- 409: another device saved first
    cur = A.api("GET", "/api/applications/me")["json"]["application"]
    det2 = dict(cur["details"], email="other.device@example.com")
    other = A.api("PUT", "/api/applications/me", {"version": cur["version"], "details": det2, "progress": cur["progress"]})
    check("(setup) a second writer advanced the version", other["status"] == 200)
    A.page.evaluate("location.hash='#account'")
    A.page.click('[data-act="prof-edit"]')
    A.page.fill("#pf-city", "Surat")
    A.page.click('#prof-form button[type="submit"]')
    A.page.wait_for_selector("#syncnote:not([hidden])", timeout=15000)
    note = A.page.inner_text("#syncnote")
    check("409: applicant is told newer progress was loaded", "newer progress from another device" in note, note)
    until(lambda: A.api("GET", "/api/applications/me")["json"]["application"]["details"].get("city") == "Surat")
    final = A.api("GET", "/api/applications/me")["json"]["application"]["details"]
    check("409: newer server data kept AND the local edit re-applied (nothing silently lost)", final.get("email") == "other.device@example.com" and final.get("city") == "Surat", final)

    # ---------------- device B: new browser context, same account
    A.page.evaluate("location.hash='#dashboard'")
    A.page.wait_for_selector(".dnext .dest")
    dest_a = A.page.inner_text(".dnext .dest")
    B = Dev(browser, "B")
    login_ui(B, email)
    B.page.wait_for_selector('#modal[open] [data-act="m-sync-yes"]', timeout=10000)
    check("second device offers to load the account's saved progress", True)
    B.page.click('[data-act="m-sync-yes"]')
    B.page.wait_for_selector(".dnext .dest", timeout=10000)
    B.page.wait_for_function("()=>/PN-\\d{4}-\\d{5}/.test(document.getElementById('tb-refv').textContent)")
    check("second device shows the same application reference", B.page.inner_text("#tb-refv") == ref_ui, B.page.inner_text("#tb-refv"))
    dest_b = B.page.inner_text(".dnext .dest")
    check("second device resumes at the same position", dest_a == dest_b, [dest_a, dest_b])
    check("second device carries the notification, status and name", "Rahul K Sharma" in B.page.inner_text("#tb-who"), B.page.inner_text("#tb-who"))
    B.page.evaluate("location.hash='#apply'")
    B.page.wait_for_selector("#view-apply h1, #view-apply h2")
    check("second device application page renders without script errors", not B.errors, B.errors)
    st = B.api("GET", "/api/applications/me")["json"]["application"]
    check("second device did not change server data on load", st["details"].get("city") == "Surat" and st["progress"].get("decision") == "APPROVE", st["progress"].get("decision"))
    ls_b = B.page.evaluate("Object.keys(localStorage).map(k=>[k,localStorage.getItem(k)])")
    check("no application data in the second device's storage unless saved there", not any(k.startswith("pn-onboarding") for k, _ in ls_b), [k for k, _ in ls_b])
    check("second device does not hold the full PAN or account (asked again)", B.page.evaluate("document.body.innerText").count("BNZPS4821K") == 0)

    # ---------------- re-entry of PAN/bank account after a new device, plus every decision state renders
    C = Dev(browser, "C")
    api_user(C, "Priya Nair", "priya-%s@example.test" % ts)
    seed(C, {"stage": "documents", "dpage": 3, "doc_index": 3, "resume": {"stage": "documents", "page": 0, "doc": "CLASS10", "milestone": "next_doc"},
             "documents": {k: VERIFIED[k] for k in ("PAN", "AADHAAR_FRONT", "AADHAAR_BACK")}, "decision": None, "round": 1})
    C.goto("#dashboard")
    C.page.wait_for_selector('#modal[open] [data-act="m-sync-yes"]', timeout=10000)
    C.page.click('[data-act="m-sync-yes"]')
    C.page.evaluate("location.hash='#apply'")
    C.page.wait_for_function("()=>document.getElementById('view-apply').innerText.length>50")
    t = C.page.inner_text("#view-apply")
    check("new device asks for PAN, bank account and mobile again, with a clear message", "never store your full PAN or bank account" in t and "your PAN" in t and "bank account number" in t, t[:400])
    check("documents verified elsewhere are noted (photos stay on the original device)", "another device" in t and "photos" in t.lower(), t[:500])
    check("resume screen shows the exact position (details page that needs the numbers)", "Your details, page 2 of 4" in t or "page 2 of 4" in t, t[:400])
    C.page.click('[data-act="resume"]')
    C.page.wait_for_selector('[data-f="pan"]', timeout=8000)
    check("re-entry routes to the details page that needs the numbers", C.page.query_selector('[data-f="pan"]') is not None and "never store your full PAN" in C.page.inner_text("#view-apply"))
    check("resumed application page has no script errors", not C.errors, C.errors)
    C.close()

    for dec, heading in (("NEEDS_MORE_INFO", "A few fixes needed"), ("REJECT", "Application not accepted"), ("HUMAN_REVIEW", "With a reviewer"), ("APPROVE", "Verification complete")):
        D = Dev(browser, dec)
        api_user(D, "Test " + dec.title().replace("_", ""), "dec-%s-%s@example.test" % (dec.lower().replace("_", ""), ts))
        docs = dict(VERIFIED)
        if dec == "NEEDS_MORE_INFO":
            docs["BANK"] = {"status": "needs_fix", "version": 1, "issues": ["REUPLOAD_IMAGE"]}
        seed(D, {"stage": "verification", "dpage": 3, "doc_index": 5, "resume": {"stage": "verification", "page": 0}, "documents": docs, "decision": dec, "round": 1})
        D.goto("#apply")
        D.page.wait_for_selector('#modal[open] [data-act="m-sync-yes"]', timeout=10000)
        D.page.click('[data-act="m-sync-yes"]')
        D.page.wait_for_function("(h)=>document.getElementById('view-apply').innerText.indexOf(h)>=0", arg=heading, timeout=10000)
        check("loads on another device and shows the %s outcome without errors" % dec, not D.errors, D.errors)
        D.close()

    # ---------------- same-device reload: nothing lost, no false "newer progress" banner
    before = A.api("GET", "/api/applications/me")["json"]["application"]["details"]
    A.goto("#dashboard")
    A.page.wait_for_selector(".dnext .dest", timeout=15000)
    time.sleep(1.5)
    check("same-device reload: no 'newer progress from another device' banner", "newer progress" not in A.page.evaluate("(document.getElementById('syncnote')||{}).textContent||''"), A.page.evaluate("(document.getElementById('syncnote')||{}).textContent||''"))
    after = A.api("GET", "/api/applications/me")["json"]["application"]["details"]
    check("same-device reload: server details unchanged", {k: after.get(k) for k in before} == before, [before, after])
    A.page.evaluate("location.hash='#account'")
    A.page.click('[data-act="prof-edit"]')
    A.page.fill("#pf-city", "Kota")
    A.page.click('#prof-form button[type="submit"]')
    A.wait_saved()
    until(lambda: A.api("GET", "/api/applications/me")["json"]["application"]["details"].get("city") == "Kota")

    # ---------------- sensitive data scan: bodies, storage, notifications and timeline
    leak = re.compile(r"BNZPS4821K|\b\d{12}\b|\b\d{11}\b|50100284417|\b4417\s?4417\s?4417\b")
    bad = []
    for dev in (A, B):
        for method, url, body in dev.bodies:
            if method != "GET" and leak.search(body):
                bad.append((dev.label, method, url))
    check("no request body carried a full PAN, 12-digit number or full account number", not bad, bad)
    check("no request body carried images or OCR text", not any(re.search(r"data:image|ocr|\"text\"", b) for dev in (A, B) for m, u, b in dev.bodies if m != "GET" and "/api/applications/me" in u), "")
    ls_a = A.page.evaluate("Object.keys(localStorage).map(k=>[k,localStorage.getItem(k)])")
    check("no full PAN, 12-digit number or account number in any localStorage value", not any(leak.search(v or "") for k, v in ls_a if not k.startswith("pn-onboarding")), [k for k, _ in ls_a])
    server_all = json.dumps([A.api("GET", p)["json"] for p in ("/api/applications/me", "/api/notifications", "/api/applications/me/timeline", "/api/me")])
    check("server responses hold no full identifier", not leak.search(server_all), "")
    check("no script errors on device A", not A.errors, A.errors)

    # ---------------- phone width, light and dark: no sideways scroll, 44 px targets on the new controls
    A.page.set_viewport_size({"width": 390, "height": 844})
    for scheme in ("light", "dark"):
        A.page.emulate_media(color_scheme=scheme)
        for h in ("#dashboard", "#notifications", "#history", "#account"):
            A.page.evaluate("location.hash=%r" % h)
            time.sleep(0.6)
            wide = A.page.evaluate("document.documentElement.scrollWidth>document.documentElement.clientWidth+1")
            small = A.page.evaluate("""[...document.querySelectorAll('.iconbtn,.nacts .btn,#savestate .btn')].filter(e=>e.offsetParent&&e.getBoundingClientRect().height>0&&e.getBoundingClientRect().height<43.5).map(e=>e.className+':'+Math.round(e.getBoundingClientRect().height))""")
            check("390px %s %s: no horizontal scroll and targets at least 44 px" % (scheme, h), not wide and not small, [wide, small])
    A.page.emulate_media(color_scheme="light")
    A.page.set_viewport_size({"width": 1280, "height": 900})

    # ---------------- sign-out leaves no personal data behind
    A.page.click("#acct-btn")
    A.page.click('[data-act="menu-auth"]')
    until(lambda: A.api("GET", "/api/me")["status"] in (401, 403) or None)
    time.sleep(1)
    ls_out = A.page.evaluate("Object.keys(localStorage).map(k=>[k,localStorage.getItem(k)])")
    bad_out = [k for k, v in ls_out if k.startswith("pn-pending-") or "Rahul" in (v or "") or "Kota" in (v or "") or "rahul.sharma@example.com" in (v or "") or "9876543210" in (v or "")]
    check("after sign-out no pn-pending key and no personal value in localStorage", not bad_out, bad_out)
    B.close()
    A.close()


def main():
    tmp = tempfile.mkdtemp(prefix="pn-e2e-")
    backend = static = None
    try:
        backend = start_backend(tmp)
        static = start_static(tmp)
        with sync_playwright() as pw:
            browser = launch(pw)
            try:
                run(browser)
            finally:
                browser.close()
    finally:
        if static:
            static.shutdown()
            static.server_close()
        if backend:
            backend.kill()
            backend.wait()
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [r for r in RESULTS if not r[1]]
    print("%d checks, %d failed" % (len(RESULTS), len(failed)))
    print("ALL PASS" if not failed else "SOME CHECKS FAILED")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
