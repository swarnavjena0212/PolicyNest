"""End-to-end test for reviewer sign-up, administrator approval, the review console, reviewer decisions, appeals
and the applicant's 30-minute timer (lane RV-FE).

Usage:  python tools/test_reviewer_e2e.py

Starts, on its own, the backend (port 9530, temporary database, random JWT secret, a random ADMIN_INVITE_CODE,
COPILOT_ORIGINS set to the local site origin) and a static server for the demo build (port 9531). Both are stopped
before exit. Needs policynest_mvp/site-demo/index.html (run `python policynest_mvp/build_site.py --mode demo` first;
the applicants use the sample details and the built-in practice documents) and Python Playwright.
All data is made up for the test. Exit code 0 only when every check passes.
"""
import http.server
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PORT, SITE_PORT = 9530, 9531
API = "http://127.0.0.1:%d" % API_PORT
SITE = "http://127.0.0.1:%d" % SITE_PORT
PASSWORD = "Rv-Test-Pw-" + secrets.token_hex(6)  # generated per run, never stored
INVITE = "invite-" + secrets.token_hex(12)       # generated per run, never stored
RESULTS = []
SHOTS = os.environ.get("RV_SHOTS")  # optional folder for screenshots
FULL_PAN = "BNZPS4821K"  # the sample applicant's typed PAN: it must never reach a reviewer screen



def close_camera_check(page):
    """Start opens the 'Check your camera' dialog once (lane VC). Close it with Continue so the test goes on; uploads never need it."""
    page.wait_for_selector("#camdlg[open]", timeout=15000)
    page.click('#camdlg [data-act="cam-cont"]')
    page.wait_for_function("()=>!document.getElementById('camdlg').open", timeout=15000)

def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + str(detail)[:900]) if (detail and not ok) else ""), flush=True)


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
    env = dict(os.environ, POLICYNEST_DB_PATH=os.path.join(tmp, "rv.sqlite3"), JWT_SECRET=os.urandom(32).hex(),
               COPILOT_ORIGINS=SITE, POLICYNEST_SKIP_DOTENV="1", ANTHROPIC_API_KEY="", GEMINI_API_KEY="",
               ADMIN_INVITE_CODE=INVITE, PYTHONDONTWRITEBYTECODE="1")
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

    class Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = Srv(("127.0.0.1", SITE_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- helpers
# Date.now can be moved forward by window.__off (kept in sessionStorage so a reload keeps the same clock).
CLOCK = """(function(){ var real=Date.now.bind(Date); window.__off=Number(sessionStorage.getItem('__off')||0);
  window.__setOff=function(ms){ window.__off=ms; sessionStorage.setItem('__off',String(ms)); }; Date.now=function(){ return real()+window.__off; }; })();"""


class Dev:
    def __init__(self, browser, label, clock=False):
        self.label = label
        self.ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        if clock:
            self.ctx.add_init_script(CLOCK)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(20000)
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))

    def goto(self, hash_=""):
        self.page.goto(SITE + "/?demo=1&v=" + secrets.token_hex(3) + hash_)
        self.page.wait_for_selector("#main", state="attached")

    def api(self, method, path, body=None):
        return self.page.evaluate(
            """async ([m,p,b])=>{const r=await fetch(%r+p,{method:m,credentials:'include',headers:{'Content-Type':'application/json'},body:b===null?undefined:JSON.stringify(b)});
               let j=null;try{j=await r.json()}catch(e){} return {status:r.status,json:j}}""" % API, [method, path, body])

    def hash(self, h):
        self.page.evaluate("(h)=>{location.hash=h}", h)

    def text(self, sel):
        return self.page.inner_text(sel)

    def shot(self, name):
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            self.page.screenshot(path=os.path.join(SHOTS, name + ".png"), full_page=True)

    def close(self):
        self.ctx.close()


def until(fn, timeout=20.0, step=0.4):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:  # noqa: BLE001
            last = None
        time.sleep(step)
    return last


def register(dev, name, email, kind="applicant", invite=None, sync=True):
    dev.goto("#welcome")
    dev.page.click('[data-act="auth-open"][data-mode="register"]')
    if kind == "reviewer":
        dev.page.click('[data-auth-type="reviewer"]')
    dev.page.fill("#account-name", name)
    dev.page.fill("#account-email", email)
    dev.page.fill("#account-password", PASSWORD)
    if invite:
        dev.page.click("details.invite summary")
        dev.page.fill("#account-invite", invite)
    if kind == "applicant" and sync:
        dev.page.check("#account-sync-opt")
    dev.page.click("#account-submit")
    dev.page.wait_for_selector("#account-message:not([hidden])", timeout=15000)
    dev.page.wait_for_function("()=>!/wait/i.test(document.getElementById('account-message').textContent)", timeout=15000)
    return dev.text("#account-message")


def sign_in(dev, email, as_="applicant"):
    """Signs in from the panel that is already open (after sign-up) or opens it."""
    if not dev.page.is_visible("#account-form"):
        dev.goto("#welcome")
        dev.page.click('[data-act="auth-open"][data-mode="login"]')
        dev.page.fill("#account-email", email)
    dev.page.click('[data-auth-as="%s"]' % as_)
    dev.page.fill("#account-password", PASSWORD)
    dev.page.click("#account-submit")


def apply_until_review(dev, label):
    """Details from the sample, the six practice documents with a borderline selfie (face score 0.68: a person decides), submit."""
    dev.hash("#apply")
    dev.page.click('[data-act="begin"]')
    close_camera_check(dev.page)
    dev.page.evaluate("document.querySelectorAll('details.demo').forEach(d=>d.open=true)")
    dev.page.click('[data-act="sample"]')
    for _ in range(4):
        dev.page.click('[data-act="details-next"]')
    dev.page.select_option('select[data-specv="SELFIE"]', "borderline")
    dev.page.click('[data-act="spec-all"]')
    dev.page.wait_for_function("()=>{const b=document.querySelector('[data-act=submit]');return b&&!b.disabled}", timeout=60000)
    dev.page.click('[data-act="submit"]')
    dev.page.wait_for_function("()=>/With a reviewer/.test(document.querySelector('#view-apply h1')?.textContent||'')", timeout=60000)
    app = until(lambda: (lambda a: a if a["progress"].get("decision") == "HUMAN_REVIEW" else None)(dev.api("GET", "/api/applications/me")["json"]["application"]))
    check(label + ": submitted application is saved with the engine decision HUMAN_REVIEW", bool(app), app and app.get("progress"))
    return app["reference"] if app else None


def applicant_sees(dev, pattern, timeout=40.0):
    """The applicant keeps the page open; moving to the dashboard refreshes notifications, which pulls in a reviewer's decision."""
    rx = re.compile(pattern)

    def probe():
        dev.hash("#dashboard")
        time.sleep(1.2)
        dev.hash("#apply")
        time.sleep(0.6)
        return rx.search(dev.text("#view-apply"))
    return until(probe, timeout=timeout, step=0.2)


def rv_queue(dev, tab="needs_review"):
    dev.hash("#review")
    dev.page.wait_for_selector('[data-act="rvc-tab"][data-tab="%s"]' % tab)
    dev.page.click('[data-act="rvc-tab"][data-tab="%s"]' % tab)
    dev.page.wait_for_function("()=>!/Loading/.test(document.getElementById('rv-list').textContent)")


def rv_open(dev, ref, tab="needs_review"):
    def present():
        rv_queue(dev, tab)
        return dev.page.query_selector('#rv-list a[href="#review/%s"]' % ref)
    link = until(present, timeout=25)
    check("reviewer: %s is listed in the %s tab" % (ref, tab), bool(link), dev.text("#rv-list")[:400])
    if not link:
        return False
    row = dev.text('#rv-list a[href="#review/%s"]' % ref)
    dev.page.click('#rv-list a[href="#review/%s"]' % ref)
    dev.page.wait_for_function("(r)=>(document.getElementById('rv-dh')||{}).textContent&&document.getElementById('rv-dh').textContent.indexOf(r)>=0&&!/Loading/.test(document.getElementById('rv-det').textContent)", arg=ref)
    return row


def confirm_dialog(dev, message=None):
    dev.page.wait_for_selector("#rv-dlg[open]")
    if message is not None:
        dev.page.fill("#rvd-msg", message)
    dev.page.click("#rvd-ok")
    dev.page.wait_for_selector("#rv-dlg:not([open])", state="attached", timeout=15000)


# ---------------------------------------------------------------- the run
def run(browser):
    ts = secrets.token_hex(3)
    rev_email, adm_email = "reviewer-%s@example.test" % ts, "admin-%s@example.test" % ts
    a_email, b_email = "asha-%s@example.test" % ts, "bilal-%s@example.test" % ts

    # ---- reviewer signs up: a request, then a waiting page with no reviewer power
    R = Dev(browser, "R")
    msg = register(R, "Riya Reviewer", rev_email, kind="reviewer")
    check("reviewer sign-up ends on sign-in with Reviewer chosen and the request message",
          "administrator must approve" in msg.lower() and R.page.get_attribute('[data-auth-as="reviewer"]', "aria-pressed") == "true", msg)
    sign_in(R, rev_email, "reviewer")
    R.page.wait_for_selector("#wait-h", timeout=15000)
    R.shot("rv_waiting")
    check("pending reviewer sees the calm 'Waiting for approval' page", "Waiting for approval" in R.text("#wait-h"), R.text("#lp-main")[:300])
    check("waiting page has no applicant shell and no Copilot",
          not R.page.is_visible(".shell") and not R.page.is_visible("#cp-fab") and not R.page.is_visible("#bnav"))
    check("waiting page offers sign-out", R.page.is_visible('[data-act="wait-out"]'))
    r403 = R.api("GET", "/api/reviewer/applications")
    check("a pending reviewer gets 403 from the queue API", r403["status"] == 403, r403)
    R.hash("#review")
    time.sleep(0.5)
    check("a pending reviewer cannot open the review console by URL", R.page.is_visible("#wait-h") and not R.page.is_visible("#view-ops"))

    # ---- administrator via the invite code; a wrong code is refused
    M = Dev(browser, "M")
    M.goto("#welcome")
    bad = M.api("POST", "/api/auth/register", {"email": "nobody-%s@example.test" % ts, "password": PASSWORD, "display_name": "No One", "account_type": "admin", "invite_code": "wrong-" + INVITE})
    check("a wrong admin invite code is refused (403 invite_invalid)", bad["status"] == 403 and bad["json"].get("error") == "invite_invalid", bad)
    msg = register(M, "Mira Admin", adm_email, kind="reviewer", invite=INVITE)
    check("admin sign-up with the invite code creates an administrator", "administrator account is created" in msg.lower(), msg)
    sign_in(M, adm_email, "reviewer")
    M.page.wait_for_selector("#view-ops .rvc", timeout=15000)
    check("administrator lands on the review console", "Review console" in M.text("#view-ops h1"))
    check("administrator has the Reviewer requests navigation item", M.page.query_selector('#menu a[href="#requests"]') is not None)
    M.hash("#requests")
    M.page.wait_for_function("(e)=>document.getElementById('view-req').innerText.indexOf(e)>=0", arg=rev_email)
    M.shot("rv_requests")
    req_txt = M.text("#view-req")
    check("pending request shows name, email and requested time", "Riya Reviewer" in req_txt and rev_email in req_txt and "Requested" in req_txt, req_txt[:400])
    M.page.click('#view-req [data-act="req-act"][data-dec="approve"]')
    M.page.wait_for_selector("#rv-dlg[open]")
    check("approving a reviewer asks for confirmation in a dialog", "Approve Riya Reviewer" in M.text("#rvd-h"))
    M.page.click("#rvd-ok")
    M.page.wait_for_function("()=>/No reviewer requests are waiting/.test(document.getElementById('view-req').innerText)", timeout=15000)
    check("approved request leaves the Pending tab", True)
    M.page.click('[data-act="req-tab"][data-tab="all"]')
    M.page.wait_for_function("()=>/Approved/.test(document.getElementById('view-req').innerText)")
    check("the All tab shows the request as approved", "Approved" in M.text("#view-req"))

    # ---- the reviewer checks again and gets the console
    R.page.click('[data-act="wait-check"]')
    R.page.wait_for_selector("#view-ops .rvc", timeout=20000)
    check("after approval, 'Check again' opens the review console (token refreshed)", "Review console" in R.text("#view-ops h1"))
    check("reviewer console shows the data notice", "You see the results of the checks, not the images" in R.text("#view-ops"))
    R.page.set_viewport_size({"width": 390, "height": 844})
    time.sleep(0.4)
    check("review queue has no horizontal scroll at 390 px", R.page.evaluate("document.documentElement.scrollWidth<=window.innerWidth+1"),
          R.page.evaluate("[document.documentElement.scrollWidth,window.innerWidth]"))
    R.page.set_viewport_size({"width": 1280, "height": 900})

    # ---- applicant A: submits, routed to a person
    A = Dev(browser, "A")
    register(A, "Asha Rao", a_email)
    sign_in(A, a_email, "applicant")
    A.page.wait_for_selector("#tb-refv", timeout=15000)
    ref_a = apply_until_review(A, "applicant A")

    # ---- reviewer: Needs review, detail, ask for more info (keyboard M)
    rv_queue(R)
    R.shot("rv_queue")
    row = rv_open(R, ref_a)
    R.shot("rv_detail")
    check("queue row shows the engine decision, waiting time and an issue label",
          bool(row) and "Engine: a person decides" in row and "waiting" in row and "borderline" in row.lower(), row)
    det = R.text("#view-ops")
    check("case detail shows the six documents, allow-listed details and the photo notice",
          "Selfie" in det and "Aadhaar, front" in det and "821K" in det and "You see the results of the checks, not the images" in det, det[:600])
    shown = R.page.evaluate("document.getElementById('view-ops').innerHTML+document.body.innerText")
    check("no full PAN or full account number on the reviewer screen", FULL_PAN not in shown and "50100284417" not in shown, "full identifier present")
    R.page.set_viewport_size({"width": 390, "height": 844})
    time.sleep(0.4)
    check("case detail has no horizontal scroll at 390 px", R.page.evaluate("document.documentElement.scrollWidth<=window.innerWidth+1"),
          R.page.evaluate("[document.documentElement.scrollWidth,window.innerWidth]"))
    R.page.set_viewport_size({"width": 1280, "height": 900})
    R.page.evaluate("document.activeElement&&document.activeElement.blur()")
    R.page.keyboard.press("m")
    R.page.wait_for_selector("#rv-dlg[open]")
    R.shot("rv_more_info_dialog")
    pairs = R.page.eval_on_selector_all("#rvd-pairs input[type=checkbox]", "els=>els.map(e=>[e.checked,e.parentElement.innerText])")
    check("shortcut M opens 'Ask for more info' with the selfie issue pre-ticked",
          any(p[0] and "Selfie" in p[1] for p in pairs), pairs)
    prefill = R.page.input_value("#rvd-msg")
    check("the message to the applicant is prefilled from the ticked items", "Selfie" in prefill, prefill)
    R.page.fill("#rvd-msg", "Please take a fresh selfie in daylight, facing the camera.")
    R.page.click("#rvd-ok")
    R.page.wait_for_selector("#rv-dlg:not([open])", state="attached")
    R.page.wait_for_function("()=>location.hash==='#review'")
    check("after the decision the reviewer is back on the queue with a toast", "More information requested" in R.text("#toasts"), R.text("#toasts"))
    rv_queue(R)
    check("the case leaves Needs review after asking for more info", R.page.query_selector('#rv-list a[href="#review/%s"]' % ref_a) is None)

    # ---- applicant A sees the request, fixes the selfie, the case returns
    seen = applicant_sees(A, r"A reviewer asked for a few things")
    A.shot("applicant_more_info")
    txt = A.text("#view-apply") if seen else ""
    check("applicant sees the reviewer's request with the message and the selfie item",
          bool(seen) and "fresh selfie in daylight" in txt and "Selfie" in txt, txt[:600])
    A.page.evaluate("document.querySelectorAll('details.demo').forEach(d=>d.open=true)")
    A.page.select_option('select[data-specv="SELFIE"]', "clean")
    A.page.click('[data-spec="SELFIE"]')
    A.page.wait_for_function("()=>{const b=document.querySelector('[data-act=resubmit]');return b&&!b.disabled}", timeout=30000)
    A.page.click('[data-act="resubmit"]')
    A.page.wait_for_function("()=>/With a reviewer/.test(document.querySelector('#view-apply h1')?.textContent||'')", timeout=30000)
    check("after the fixes, a clean re-check goes back to the reviewer (not auto-approved)", True)
    prog = until(lambda: (lambda p: p if (p.get("round") or 0) >= 2 else None)(A.api("GET", "/api/applications/me")["json"]["application"]["progress"]))
    check("the resubmission is saved as a new round", bool(prog) and prog.get("round") == 2 and prog.get("decision") == "HUMAN_REVIEW", prog)
    row = rv_open(R, ref_a)
    check("the resubmitted case is back in Needs review (round 2)", bool(row) and "round 2" in row, row)
    R.page.evaluate("document.activeElement&&document.activeElement.blur()")
    R.page.keyboard.press("a")
    R.page.wait_for_selector("#rv-dlg[open]")
    check("shortcut A opens the approve confirmation", "Approve" in R.text("#rvd-h"))
    R.page.click("#rvd-ok")
    R.page.wait_for_function("()=>location.hash==='#review'")
    rv_queue(R, "decided")
    check("the approved case is in the Decided tab", R.page.query_selector('#rv-list a[href="#review/%s"]' % ref_a) is not None, R.text("#rv-list")[:300])

    seen = applicant_sees(A, r"Verification complete")
    check("applicant A is approved after the reviewer's decision", bool(seen), A.text("#view-apply")[:300])
    if seen:
        A.page.click('[data-act="start-training"]')
        A.page.wait_for_selector('[data-mod="0"]')
        check("applicant A's training unlocks", A.page.is_visible('[data-mod="0"]'))

    # ---- applicant B: rejected, appeals, the reviewer overturns
    B = Dev(browser, "B")
    register(B, "Bilal Khan", b_email)
    sign_in(B, b_email, "applicant")
    B.page.wait_for_selector("#tb-refv", timeout=15000)
    ref_b = apply_until_review(B, "applicant B")
    rv_open(R, ref_b)
    R.page.evaluate("document.activeElement&&document.activeElement.blur()")
    R.page.keyboard.press("r")
    R.page.wait_for_selector("#rv-dlg[open]")
    R.page.click("#rvd-ok")  # empty message: refused on the page
    R.page.wait_for_selector("#rvd-err:not([hidden])")
    check("reject needs a message to the applicant", "Write a message" in R.text("#rvd-err"))
    R.page.fill("#rvd-msg", "We could not match your selfie with the photo on your PAN card.")
    R.page.fill("#rvd-note", "Compared statuses only; borderline face score.")
    R.page.click("#rvd-ok")
    R.page.wait_for_function("()=>location.hash==='#review'")
    seen = applicant_sees(B, r"Application not accepted")
    txt = B.text("#view-apply") if seen else ""
    check("applicant B sees the rejection with the reviewer's message", bool(seen) and "could not match your selfie" in txt, txt[:500])
    check("the internal note never reaches the applicant", "borderline face score" not in B.page.content())
    B.page.wait_for_selector("#ap-why")
    B.page.fill("#ap-why", "This selfie is me. I can send a clearer one taken in daylight.")
    B.page.click("#ap-send")
    B.page.wait_for_function("()=>/Appeal sent. A person will look at it./.test(document.getElementById('view-apply').innerText)")
    B.shot("applicant_appeal_sent")
    check("applicant B's appeal is sent and acknowledged", True)
    aps = B.api("GET", "/api/appeals/me")["json"]["appeals"]
    check("the appeal is open on the server", any(a["status"] == "OPEN" for a in aps), aps)

    rv_queue(R, "appeals")
    R.page.wait_for_function("(r)=>document.getElementById('rv-list').innerText.indexOf(r)>=0", arg=ref_b)
    check("the appeal shows in the Appeals tab with the applicant's reason", "This selfie is me" in R.text("#rv-list"), R.text("#rv-list")[:300])
    R.page.click('#rv-list a[href="#review/%s"]' % ref_b)
    R.page.wait_for_selector('[data-act="rvc-appeal"][data-outcome="overturn"]')
    check("an open appeal replaces the decision buttons with appeal answers", R.page.query_selector('[data-rvact="approve"]') is None)
    R.page.click('[data-act="rvc-appeal"][data-outcome="overturn"]')
    confirm_dialog(R)
    seen = applicant_sees(B, r"Verification complete")
    check("after the overturn, applicant B is approved", bool(seen), B.text("#view-apply")[:300])
    aps = B.api("GET", "/api/appeals/me")["json"]["appeals"]
    check("the appeal is recorded as overturned", any(a["status"] == "OVERTURNED" for a in aps), aps)

    # ---- an applicant cannot read reviewer data
    r1 = A.api("GET", "/api/reviewer/applications")
    r2 = A.api("GET", "/api/reviewer/applications/" + (ref_b or "PN-2026-00000"))
    r3 = A.api("POST", "/api/reviewer/applications/%s/decision" % (ref_b or "PN-2026-00000"), {"action": "approve", "message": ""})
    check("an applicant gets 403 from the queue, the case detail and decisions", r1["status"] == 403 and r2["status"] == 403 and r3["status"] == 403, [r1["status"], r2["status"], r3["status"]])
    A.hash("#review")
    A.page.wait_for_function("()=>/do not have access/i.test(document.getElementById('view-ops').innerText)")
    check("an applicant opening #review sees 'You do not have access'", True)
    for d in (R, M, A, B):
        check("no page errors on %s" % d.label, not d.errors, d.errors[:3])
    for d in (R, M, A, B):
        d.close()


def run_timer(browser):
    T = Dev(browser, "T", clock=True)
    T.goto("#apply")
    T.page.check("#f-save")
    T.page.click('[data-act="begin"]')
    close_camera_check(T.page)
    T.page.wait_for_selector("#fb-timer:not([hidden])")
    t0 = T.text("#fb-timer")
    check("timer starts at 30:00 and is a role=timer", t0 in ("30:00 left", "29:59 left") and T.page.get_attribute("#fb-timer", "role") == "timer", t0)
    check("no full screen", T.page.evaluate("!document.fullscreenElement"))
    T.page.evaluate("document.querySelectorAll('details.demo').forEach(d=>d.open=true)")
    T.page.click('[data-act="sample"]')
    T.page.click('[data-act="details-next"]')
    T.page.click('[data-act="details-next"]')
    T.page.click('[data-act="fb-timer"]')
    check("Hide timer hides it", T.page.is_hidden("#fb-timer") and T.text("#fb-tt") == "Show timer")
    T.page.click('[data-act="fb-timer"]')
    check("Show timer brings it back", T.page.is_visible("#fb-timer"))
    T.page.evaluate("window.__setOff(25*60000+5000)")
    T.page.wait_for_function("()=>/5 minutes left/.test(document.getElementById('sr-status').textContent)", timeout=8000)
    check("one gentle 5-minute announcement through the live region", True)
    T.page.evaluate("window.__setOff(30*60000+2000)")
    T.page.wait_for_selector("#modal[open]", timeout=8000)
    T.shot("timer_times_up")
    check("at zero a 'Time's up' dialog opens", "Time's up" in T.text("#m-t") and "10 more minutes" in T.text("#m-a"), T.text("#modal"))
    T.page.click('[data-act="m-more"]')
    T.page.wait_for_function("()=>/^(10:00|09:5\\d) left$/.test(document.getElementById('fb-timer').textContent)", timeout=5000)
    check("Take 10 more minutes extends the timer by 10 minutes", True)
    T.page.evaluate("window.__setOff(40*60000+4000)")
    T.page.wait_for_selector("#modal[open]", timeout=8000)
    T.page.keyboard.press("Escape")
    T.page.wait_for_function("()=>!document.getElementById('modal').open&&/^(10:00|09:5\\d) left$/.test(document.getElementById('fb-timer').textContent)", timeout=5000)
    check("Escape behaves like taking 10 more minutes", True)
    T.page.reload()
    T.page.wait_for_selector("#fb-timer:not([hidden])", timeout=10000)
    left = T.text("#fb-timer")
    check("after a reload the same sitting and timer continue (not reset to 30:00)", re.match(r"^(10:00|09:\d\d) left$", left) is not None, left)
    check("details survive the reload", T.page.evaluate("JSON.parse(localStorage.getItem('pn-onboarding-v1')).S.f.name") == "Rahul Kumar Sharma")
    T.page.evaluate("window.__setOff(51*60000)")
    T.page.wait_for_selector("#modal[open]", timeout=8000)
    T.page.click('[data-act="m-later"]')
    T.page.wait_for_function("()=>location.hash==='#dashboard'", timeout=8000)
    check("Save and exit ends the sitting and returns to the dashboard", T.page.is_hidden("#focusbar"))
    saved = T.page.evaluate("JSON.parse(localStorage.getItem('pn-onboarding-v1')).S")
    check("no data lost: details and page kept, nothing reset", saved["f"]["name"] == "Rahul Kumar Sharma" and saved["dpage"] == 2 and saved["begun"] is True and saved["sess"] is None, {k: saved.get(k) for k in ("dpage", "begun", "sess")})
    T.hash("#apply")
    T.page.wait_for_selector('[data-act="resume"]')
    check("the resume screen shows where the applicant stopped", "page 3 of 4" in T.text("#view-apply"), T.text("#view-apply")[:300])
    T.page.click('[data-act="resume"]')
    T.page.wait_for_selector("#fb-timer:not([hidden])")
    check("a new sitting after Save and exit starts a fresh 30 minutes", T.text("#fb-timer") in ("30:00 left", "29:59 left"), T.text("#fb-timer"))
    T.page.click('[data-act="details-next"]')
    check("the applicant continues from page 3 with the details intact", T.page.input_value("#f-acct") != "" or "page 4 of 4" in T.text("#view-apply").lower(), T.text("#view-apply")[:200])
    check("no page errors in the timer run", not T.errors, T.errors[:3])
    T.close()


def _start_unsaved(dev):
    """Begin without ticking save-on-this-device, fill the sample details and go to page 2."""
    dev.hash("#apply")
    dev.page.wait_for_selector('[data-act="begin"]')
    if dev.page.is_checked("#f-save"):
        dev.page.uncheck("#f-save")
    dev.page.click('[data-act="begin"]')
    close_camera_check(dev.page)
    dev.page.wait_for_selector("#fb-timer:not([hidden])")
    dev.page.evaluate("document.querySelectorAll('details.demo').forEach(d=>d.open=true)")
    dev.page.click('[data-act="sample"]')
    dev.page.click('[data-act="details-next"]')


def _times_up(dev, minutes=30):
    dev.page.evaluate("window.__setOff(%d*60000+2000)" % minutes)
    dev.page.wait_for_selector("#modal[open]", timeout=8000)


def run_timer_unsaved(browser):
    """Fix M3: with account saving off and no device consent, the time's-up dialog never claims progress is kept;
    it offers a save first, and each way out tells the truth."""
    ts = secrets.token_hex(3)
    # signed-in applicant, account saving left off (the default)
    U = Dev(browser, "U", clock=True)
    email = "usha-%s@example.test" % ts
    register(U, "Usha Unsaved", email, sync=False)
    sign_in(U, email, "applicant")
    U.page.wait_for_selector("#tb-refv", timeout=15000)
    _start_unsaved(U)
    _times_up(U)
    U.shot("timer_times_up_unsaved")
    body, acts = U.text("#m-b"), U.text("#m-a")
    check("unsaved: the time's-up dialog says nothing is saved (not 'everything is kept')",
          "nothing is saved" in body and "is kept" not in body, body)
    check("unsaved: the dialog offers Save to my account, Save on this device and Exit without saving",
          all(U.page.query_selector('#m-a [data-act="%s"]' % a) for a in ("m-more", "m-save-acct", "m-save-dev", "m-later"))
          and "Exit without saving" in acts, acts)
    # Record every screen-reader announcement from here on: a later "Saved" status can overwrite #sr-status, so reading it once
    # afterwards is racy. The check below still needs the exact time's-up confirmation to have been announced.
    U.page.evaluate("""()=>{ window.__srlog=[]; const s=document.getElementById('sr-status');
      new MutationObserver(()=>{ const t=s.textContent; if(t) window.__srlog.push(t); }).observe(s,{childList:true,characterData:true,subtree:true}); }""")
    U.page.click('[data-act="m-save-acct"]')
    U.page.wait_for_function("()=>location.hash==='#dashboard'", timeout=15000)
    srv = until(lambda: (lambda a: a if (a.get("details") or {}).get("name") else None)(U.api("GET", "/api/applications/me")["json"]["application"]))
    check("Save to my account and exit stores the typed details on the server", bool(srv) and srv["details"]["name"] == "Rahul Kumar Sharma",
          srv and srv.get("details"))
    try:
        U.page.wait_for_function("()=>(window.__srlog||[]).some(t=>/^Saved to your account\. You can come back/.test(t))", timeout=8000)
        said = True
    except Exception:  # noqa: BLE001
        said = False
    check("the confirmation says it is saved to the account", said, U.page.evaluate("window.__srlog||[]"))
    U.page.reload()
    U.page.wait_for_selector("#tb-refv", timeout=15000)
    U.hash("#apply")
    U.page.wait_for_selector('[data-act="resume"]', timeout=15000)
    check("after a reload the application resumes from the account (the intro does not come back)",
          U.page.query_selector('[data-act="begin"]') is None, U.text("#view-apply")[:200])
    check("no page errors in the account-save run", not U.errors, U.errors[:3])
    U.close()

    # signed out, no consent: the device choice, and the plain exit
    V = Dev(browser, "V", clock=True)
    V.goto("#apply")
    _start_unsaved(V)
    V.page.click('[data-act="fb-exit"]')
    V.page.wait_for_selector("#modal[open]")
    check("Save and exit without any saving explains the limit and offers the device save",
          "only until you close this tab" in V.text("#m-b") and V.page.query_selector('#m-a [data-act="m-save-dev"]') is not None
          and V.page.query_selector('#m-a [data-act="m-save-acct"]') is None, V.text("#modal"))
    V.page.click('[data-act="m-close"]')
    _times_up(V)
    check("signed out: the time's-up dialog says nothing is saved", "nothing is saved" in V.text("#m-b"), V.text("#m-b"))
    V.page.click('[data-act="m-save-dev"]')
    V.page.wait_for_function("()=>location.hash==='#dashboard'", timeout=8000)
    saved = V.page.evaluate("(()=>{try{return JSON.parse(localStorage.getItem('pn-onboarding-v1')).S}catch(e){return null}})()")
    check("Save on this device and exit keeps the details on the device", bool(saved) and saved["f"]["name"] == "Rahul Kumar Sharma" and saved["saveOK"] is True)
    V.close()

    W = Dev(browser, "W", clock=True)
    W.goto("#apply")
    _start_unsaved(W)
    _times_up(W)
    W.page.click('[data-act="m-later"]')
    W.page.wait_for_function("()=>location.hash==='#dashboard'", timeout=8000)
    W.page.wait_for_function("()=>/Nothing was saved/.test(document.getElementById('sr-status').textContent)", timeout=5000)
    status = W.text("#sr-status")
    check("Exit without saving says nothing was saved (never 'Your progress is kept')", "Your progress is kept" not in status, status)
    check("no page errors in the unsaved runs", not V.errors and not W.errors, (V.errors + W.errors)[:3])
    W.close()


def main():
    if not os.path.exists(os.path.join(ROOT, "policynest_mvp", "site-demo", "index.html")):
        raise SystemExit("build the demo site first: python policynest_mvp/build_site.py --mode demo")
    tmp = tempfile.mkdtemp(prefix="pn-rv-")
    api = start_backend(tmp)
    web = start_static(tmp)
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception:
                browser = pw.chromium.launch(channel="chrome")
            try:
                run(browser)
                run_timer(browser)
                run_timer_unsaved(browser)
            finally:
                browser.close()
    finally:
        web.shutdown()
        web.server_close()
        api.terminate()
        try:
            api.wait(10)
        except Exception:
            api.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for r in RESULTS if r[1])
    print("SUMMARY %d/%d passed" % (passed, len(RESULTS)))
    print("ALL PASS" if passed == len(RESULTS) else "SOME CHECKS FAILED")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
