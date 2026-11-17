"""End-to-end test for the voice guide and the camera check (lane VC, planning/VOICE_AND_CAMERA_PLAN.md).

Usage:  python tools/test_voice_camera.py

Serves the demo build (policynest_mvp/site-demo, build it first with `python policynest_mvp/build_site.py --mode demo`)
on 127.0.0.1:9610 and drives it with Python Playwright (Chromium). Nothing else is started; the server is stopped before exit.
- speechSynthesis and SpeechRecognition are replaced by stubs that record what would be spoken (no audio).
- Chromium's fake camera (--use-fake-device-for-media-stream, --use-fake-ui-for-media-stream) gives the "Camera works" path;
  getUserMedia stubs give the blocked, no-camera, busy, unsupported and black-picture paths.
- The Copilot proxy (localhost:8787) is answered by a Playwright route with an answer that holds ID-like numbers, so the
  test proves the guide scrubs them before speaking.
All data is made up. Exit code 0 only when every check passes.
"""
import base64
import http.server
import json
import os
import re
import secrets
import socketserver
import sys
import tempfile
import threading

from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_DIR = os.path.join(ROOT, "policynest_mvp", "site-demo")
PORT = 9610
SITE = "http://127.0.0.1:%d" % PORT
RESULTS = []
ALL_SPOKEN = []
SAMPLE_PAN = "BNZPS4821K"  # the sample applicant's typed PAN: it must never be spoken

# No 9-or-more digit run (separators allowed between digits), no 12-digit Aadhaar-like string, no PAN.
ID_LIKE = re.compile(r"\d(?:[\s.\-]?\d){8,}|\b\d{4}[\s.\-]?\d{4}[\s.\-]?\d{4}\b|\b[A-Za-z]{5}\d{4}[A-Za-z]\b")


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + str(detail)[:700]) if (detail and not ok) else ""), flush=True)


STUBS = r"""
(() => {
  window.__spoken = []; window.__cancels = 0;
  const ss = { speaking: false, pending: false, paused: false, onvoiceschanged: null,
    speak(u) { window.__spoken.push({ text: String(u.text), lang: String(u.lang || '') }); this.speaking = true; },
    cancel() { window.__cancels++; this.speaking = false; },
    pause() {}, resume() {}, getVoices() { return []; }, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; } };
  Object.defineProperty(window, 'speechSynthesis', { configurable: true, get() { return ss; } });
  window.SpeechSynthesisUtterance = function (t) { this.text = t; this.lang = ''; this.voice = null; this.rate = 1; this.onend = null; this.onerror = null; };
  window.__abort = 0; window.__recog = [];
  function R() { this.lang = ''; window.__recog.push(this); }
  R.prototype.start = function () { const me = this; setTimeout(() => { if (me._aborted) return;
    if (me.onresult) me.onresult({ results: [[{ transcript: window.__transcript || 'What documents do I need' }]] }); if (me.onend) me.onend(); }, 80); };
  R.prototype.stop = function () { if (this.onend) this.onend(); };
  R.prototype.abort = function () { this._aborted = true; window.__abort++; if (this.onend) this.onend(); };
  window.SpeechRecognition = R; window.webkitSpeechRecognition = R;
  window.__streams = [];
  const md = navigator.mediaDevices;
  const mode = sessionStorage.getItem('__gum') || window.__gumMode || '';
  if (md && md.getUserMedia) {
    const real = md.getUserMedia.bind(md);
    md.getUserMedia = function (c) {
      const m = window.__gumMode || mode;
      if (m === 'blocked') return Promise.reject(new DOMException('denied', 'NotAllowedError'));
      if (m === 'none') return Promise.reject(new DOMException('none', 'NotFoundError'));
      if (m === 'busy') return Promise.reject(new DOMException('busy', 'NotReadableError'));
      if (m === 'dark') { const cv = document.createElement('canvas'); cv.width = 64; cv.height = 48; const x = cv.getContext('2d');
        const paint = () => { x.fillStyle = '#000'; x.fillRect(0, 0, 64, 48); }; paint(); setInterval(paint, 100);
        const s = cv.captureStream(10); window.__streams.push(s); return Promise.resolve(s); }
      return real(c).then(s => { window.__streams.push(s); return s; });
    };
  }
})();
"""
UNSUPPORTED = "(() => { try { Object.defineProperty(navigator, 'mediaDevices', { configurable: true, get() { return undefined; } }); } catch (e) {} })();"

ANSWER = ("Here is your answer. Your account 123456789012 and Aadhaar 2345 6789 0123 and 2345-6789-0124 "
          "and PAN ABCDE1234F stay private.")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")


def start_static():
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=SITE_DIR, **k)

        def log_message(self, *a):
            pass

    class Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = Srv(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def route_copilot(ctx):
    def handle(route):
        url = route.request.url
        if url.endswith("/api/assistant"):
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"answer": ANSWER, "mode": "ai_grounded", "sources": []}))
        else:
            route.fulfill(status=503, content_type="application/json", body="{}")
    ctx.route(re.compile(r"^http://localhost:8787/.*"), handle)


class Dev:
    def __init__(self, browser, label, gum="", permissions=None, unsupported=False, lang=None):
        self.label = label
        kw = {"viewport": {"width": 1280, "height": 900}}
        if permissions is not None:
            kw["permissions"] = permissions
        self.ctx = browser.new_context(**kw)
        if gum:
            self.ctx.add_init_script("sessionStorage.setItem('__gum', %s);" % json.dumps(gum))
        if lang:
            self.ctx.add_init_script("try{ if(!sessionStorage.getItem('__langset')){ localStorage.setItem('pn-lang', %s); sessionStorage.setItem('__langset','1'); } }catch(e){}" % json.dumps(lang))
        if unsupported:
            self.ctx.add_init_script(UNSUPPORTED)
        self.ctx.add_init_script(STUBS)
        route_copilot(self.ctx)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(15000)
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))

    def goto(self, hash_="#apply"):
        self.page.goto(SITE + "/?demo=1&v=" + secrets.token_hex(3) + hash_)
        self.page.wait_for_selector("#main", state="attached")

    def spoken(self):
        s = self.page.evaluate("window.__spoken.slice()")
        return s

    def collect(self):
        try:
            ALL_SPOKEN.extend(self.spoken())
        except Exception:  # noqa: BLE001
            pass

    def wait_spoken(self, pattern, timeout=8000):
        try:
            self.page.wait_for_function("(re)=>window.__spoken.some(s=>new RegExp(re).test(s.text))", arg=pattern, timeout=timeout)
            return True
        except Exception:  # noqa: BLE001
            return False

    def last(self):
        s = self.spoken()
        return s[-1] if s else {"text": "", "lang": ""}

    def cam_state(self, st, timeout=10000):
        try:
            self.page.wait_for_selector('#camdlg[open][data-state="%s"]' % st, timeout=timeout)
            return True
        except Exception:  # noqa: BLE001
            return False

    def close(self):
        self.collect()
        self.ctx.close()


def begin(dev, save=False):
    dev.goto("#apply")
    dev.page.wait_for_selector('[data-act="begin"]')
    if save:
        dev.page.check("#f-save")
    dev.page.click('[data-act="begin"]')


def to_documents(dev):
    dev.page.evaluate("document.querySelectorAll('details.demo').forEach(d=>d.open=true)")
    dev.page.click('[data-act="sample"]')
    for _ in range(4):
        dev.page.click('[data-act="details-next"]')


def in_dialog(dev):
    return dev.page.evaluate("(()=>{const d=document.getElementById('camdlg');return !!d&&d.open&&d.contains(document.activeElement)})()")


def run_main(browser):
    """English: off by default, the toggle persists, lines on details pages and document slots, upload results, Stop, Escape,
    the file picker, the mic asking the Copilot (answer spoken with numbers hidden), and the camera check with the fake camera."""
    A = Dev(browser, "A", permissions=["camera"])
    A.goto("#apply")
    A.page.wait_for_selector("#vg-offer")
    check("guide is off by default: nothing stored, offer not pressed, voice bar hidden",
          A.page.evaluate("localStorage.getItem('pn-voice-guide')") is None
          and A.page.get_attribute("#vg-offer", "aria-pressed") == "false"
          and A.page.get_attribute("#fb-vg", "aria-pressed") == "false" and A.page.is_hidden("#vgbar"))
    check("the Before you start checklist offers the voice guide once", A.page.locator("#vg-offer").count() == 1
          and "Turn on the voice guide?" in A.page.inner_text("#view-apply"))
    A.page.click("#vg-offer")
    check("turning the guide on stores it on this device", A.page.evaluate("localStorage.getItem('pn-voice-guide')") == "1"
          and A.page.get_attribute("#vg-offer", "aria-pressed") == "true")
    check("turning it on speaks a short hello in en-IN", A.wait_spoken("Voice guide is on") and A.last()["lang"] == "en-IN", A.spoken())
    check("focus stays on the toggle after it re-renders", A.page.evaluate("document.activeElement&&document.activeElement.id") == "vg-offer")
    A.page.check("#f-save")
    A.page.click('[data-act="begin"]')
    A.page.wait_for_selector("#camdlg[open]")
    check("Start opens the 'Check your camera' dialog once", A.page.inner_text("#cam-h") == "Check your camera")
    check("fake camera: the check says Camera works", A.cam_state("ok") and "Camera works" in A.page.inner_text("#cam-st"), A.page.inner_text("#camdlg"))
    check("the voice guide reads the camera result", A.wait_spoken("^Camera works"), A.spoken()[-3:])
    live = A.page.evaluate("window.__streams.flatMap(s=>s.getTracks()).map(t=>t.readyState)")
    check("the preview track is live while the dialog is open", live and all(x == "live" for x in live), live)
    check("the preview is shown only inside the dialog", A.page.evaluate("document.querySelectorAll('video').length===1&&!!document.querySelector('#camdlg video').srcObject"))
    # keyboard: focus starts inside, Tab and Shift+Tab stay inside
    ok_tab = in_dialog(A)
    for _ in range(5):
        A.page.keyboard.press("Tab")
        ok_tab = ok_tab and in_dialog(A)
    for _ in range(5):
        A.page.keyboard.press("Shift+Tab")
        ok_tab = ok_tab and in_dialog(A)
    check("the camera dialog traps focus (Tab and Shift+Tab stay inside)", ok_tab)
    # Retry: the old stream is stopped before a new one starts
    A.page.click('[data-act="cam-retry"]')
    A.cam_state("ok")
    states = A.page.evaluate("window.__streams.map(s=>s.getTracks().every(t=>t.readyState==='ended'))")
    check("Retry stops the previous stream before opening a new one", len(states) >= 2 and all(states[:-1]) and not states[-1], states)
    A.page.keyboard.press("Escape")
    A.page.wait_for_function("()=>!document.getElementById('camdlg').open")
    check("Escape closes the camera dialog", True)
    ended = A.page.evaluate("window.__streams.flatMap(s=>s.getTracks()).every(t=>t.readyState==='ended')")
    check("every camera track is ended after close", ended, A.page.evaluate("window.__streams.flatMap(s=>s.getTracks()).map(t=>t.readyState)"))
    check("the video element holds no stream after close", A.page.evaluate("document.getElementById('cam-v').srcObject===null"))
    check("after the camera check the guide reads page 1", A.wait_spoken("^Page 1 of 4: who you are"), A.spoken()[-3:])
    check("the caption shows the same line in a polite live region",
          "Page 1 of 4" in A.page.inner_text("#vg-cap") and A.page.get_attribute("#vg-cap", "aria-live") == "polite"
          and A.page.inner_text("#vg-cap").startswith("Guide:"), A.page.inner_text("#vg-cap"))
    check("the focus bar toggle shows the guide is on (aria-pressed)", A.page.get_attribute("#fb-vg", "aria-pressed") == "true")
    check("the mic shows where SpeechRecognition exists", A.page.is_visible("#vg-mic"))
    # Stop
    c0 = A.page.evaluate("window.__cancels")
    A.page.click('#vgbar [data-act="vg-stop"]')
    check("Stop stops speech", A.page.evaluate("window.__cancels") > c0 and not A.page.evaluate("speechSynthesis.speaking"))
    # details pages
    A.page.evaluate("document.querySelectorAll('details.demo').forEach(d=>d.open=true)")
    A.page.click('[data-act="sample"]')
    pages_ok = True
    for n, word in ((2, "your ID numbers"), (3, "how we reach you"), (4, "bank account and consent")):
        A.page.click('[data-act="details-next"]')
        pages_ok = pages_ok and A.wait_spoken("^Page %d of 4: %s" % (n, word))
    check("a line is spoken on every details page (en)", pages_ok, [s["text"][:40] for s in A.spoken()])
    # Escape stops the current line
    c1 = A.page.evaluate("window.__cancels")
    A.page.keyboard.press("Escape")
    check("Escape stops speech", A.page.evaluate("window.__cancels") > c1 and not A.page.evaluate("speechSynthesis.speaking"))
    A.page.click('[data-act="details-next"]')
    check("the first document slot is read (en)", A.wait_spoken("^Document 1 of 6: your PAN card"), A.spoken()[-2:])
    check("the Documents step has a Check camera link", A.page.locator('#view-apply .note [data-act="cam-check"]').count() == 1)
    A.page.click('[data-dk="1"]')
    check("switching slot reads that slot", A.wait_spoken("^Document 2 of 6: the front of your Aadhaar"), A.spoken()[-2:])
    A.page.click('[data-dk="5"]')
    check("the selfie slot is read and its card has a Check camera link",
          A.wait_spoken("^Document 6 of 6: your selfie") and A.page.locator('#view-apply .vcard [data-act="cam-check"]').count() == 1)
    # opening the file picker stops speech
    c2 = A.page.evaluate("window.__cancels")
    with A.page.expect_file_chooser() as fc:
        A.page.click('label[for="up-SELFIE"]')
    fc.value  # noqa: B018 (picker opened)
    check("opening the file picker stops speech", A.page.evaluate("window.__cancels") > c2)
    A.page.keyboard.press("Escape")
    # upload results
    A.page.click('[data-act="spec-all"]')
    check("upload results are read (verified documents)", A.wait_spoken("is verified", 30000), A.spoken()[-2:])
    check("when all six are in, the guide says so", A.wait_spoken("All six documents are verified", 30000), A.spoken()[-2:])
    # mic: push to talk asks the Copilot; the answer is spoken with numbers hidden and shown in the drawer
    A.page.evaluate("window.__transcript='What should I keep ready'")
    A.page.click("#vg-mic")
    ok_ans = A.wait_spoken("^Here is your answer", 10000)
    ans = [s["text"] for s in A.spoken() if s["text"].startswith("Here is your answer")]
    check("the mic asks the Copilot and the answer is spoken", ok_ans, A.spoken()[-2:])
    check("the spoken answer has every ID-like number hidden", ans and not ID_LIKE.search(ans[-1]) and "number hidden" in ans[-1], ans)
    check("the answer is also shown in the Copilot drawer", "Here is your answer" in A.page.inner_text("#cp-log"))
    A.page.evaluate("window.__transcript='my aadhaar is 2345 6789 0123'")
    n_before = len(A.spoken())
    A.page.click('[data-act="cp-close"]')
    A.page.click("#vg-mic")
    A.page.wait_for_function("(n)=>window.__spoken.length>n", arg=n_before, timeout=8000)
    check("a spoken question with an ID number is refused, and the refusal holds no number",
          not ID_LIKE.search(A.last()["text"]), A.last())
    # Stop also aborts listening
    A.page.evaluate("window.__transcript=''; window.SpeechRecognition.prototype.start=function(){}")
    A.page.click('[data-act="cp-close"]') if A.page.is_visible('[data-act="cp-close"]') else None
    A.page.click("#vg-mic")
    a0 = A.page.evaluate("window.__abort")
    A.page.click('#vgbar [data-act="vg-stop"]')
    check("Stop also ends listening", A.page.evaluate("window.__abort") > a0)
    # toggle persists: off via the focus bar or voice bar, stays off after a reload
    A.page.click("#vg-off")
    c3 = A.page.evaluate("window.__cancels")
    check("switching the guide off stores it and hides the voice bar", A.page.evaluate("localStorage.getItem('pn-voice-guide')") == "0" and A.page.is_hidden("#vgbar"))
    n_off = len(A.spoken())
    A.page.click('[data-dk="2"]')
    A.page.wait_for_timeout(500)
    check("with the guide off nothing more is spoken", len(A.spoken()) == n_off, A.spoken()[n_off:])
    check("no page errors (A)", not A.errors, A.errors[:3])
    A.close()


def run_gesture_and_hindi(browser):
    """No speech before a user gesture after a reload; Hindi lines in hi-IN; the toggle persists across a reload."""
    H = Dev(browser, "H", gum="none", lang="hi")
    H.goto("#apply")
    H.page.wait_for_selector("#vg-offer")
    H.page.click("#vg-offer")
    check("hi: the hello is spoken in hi-IN", H.wait_spoken("वॉइस गाइड चालू है") and H.last()["lang"] == "hi-IN", H.spoken()[-1:])
    H.page.check("#f-save")
    H.page.click('[data-act="begin"]')
    check("hi: no camera found is shown and read in Hindi", H.cam_state("none") and H.wait_spoken("कोई कैमरा नहीं मिला"), H.spoken()[-1:])
    H.page.click('[data-act="cam-cont"]')
    check("hi: page 1 is read in Hindi", H.wait_spoken("^पेज 1, कुल 4"), H.spoken()[-2:])
    H.page.evaluate("document.querySelectorAll('details.demo').forEach(d=>d.open=true)")
    H.page.click('[data-act="sample"]')
    H.page.click('[data-act="details-next"]')
    check("hi: page 2 is read in Hindi", H.wait_spoken("^पेज 2, कुल 4"))
    check("hi: every guide line is tagged hi-IN", all(s["lang"] == "hi-IN" for s in H.spoken()), H.spoken())
    for _ in range(3):
        H.page.click('[data-act="details-next"]')
    check("hi: the first document slot is read in Hindi", H.wait_spoken("^दस्तावेज़ 1, कुल 6: आपका PAN कार्ड"), H.spoken()[-2:])
    H.page.click('[data-dk="3"]')
    check("hi: the marksheet slot is read in Hindi", H.wait_spoken("^दस्तावेज़ 4, कुल 6"))
    # reload in the middle of the sitting: the guide is still on, but nothing is spoken until the applicant presses something
    H.collect()
    H.page.reload()
    H.page.wait_for_selector("#vgbar:not([hidden])", timeout=10000)
    H.page.wait_for_timeout(1200)
    check("after a reload the guide is still on (remembered on this device)", H.page.get_attribute("#fb-vg", "aria-pressed") == "true")
    check("nothing is spoken before a user gesture", H.spoken() == [], H.spoken())
    cap = H.page.inner_text("#vg-cap")
    check("the caption still shows the line for the screen", cap.startswith("गाइड:") and re.search(r"(पेज|दस्तावेज़) \d, कुल", cap), cap)
    if H.page.locator('[data-act="details-next"]').count():
        H.page.click('[data-act="details-next"]')
    else:
        H.page.click('[data-dk="4"]')
    check("after a gesture the guide speaks again", H.wait_spoken(r"^दस्तावेज़ \d, कुल 6"), H.spoken())
    check("no page errors (H)", not H.errors, H.errors[:3])
    H.close()


def run_blocked(browser):
    """Blocked camera: the right help, Upload photos instead keeps uploads working, and the result is logged."""
    B = Dev(browser, "B", gum="blocked")
    begin(B, save=True)
    check("blocked: the check says Camera blocked with how to allow it",
          B.cam_state("blocked") and "lock icon" in B.page.inner_text("#cam-help"), B.page.inner_text("#camdlg"))
    check("blocked: Retry, Continue and Upload photos instead are offered",
          all(B.page.locator('#camdlg [data-act="%s"]' % a).is_visible() for a in ("cam-retry", "cam-cont", "cam-upload")))
    check("blocked: the preview is hidden", B.page.is_hidden("#cam-v"))
    B.page.click('[data-act="cam-cont"]')
    check("a failed check never blocks onboarding (the details form is usable)", B.page.is_visible('[data-act="details-next"]'))
    to_documents(B)
    B.page.click('#view-apply .note [data-act="cam-check"]')
    B.cam_state("blocked")
    B.page.click('[data-act="cam-upload"]')
    B.page.wait_for_function("()=>!document.getElementById('camdlg').open")
    check("Upload photos instead closes the dialog and moves focus to the file input",
          B.page.evaluate("document.activeElement&&document.activeElement.id") == "up-PAN", B.page.evaluate("document.activeElement&&document.activeElement.id"))
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "test-pan-photo.png")
        open(fp, "wb").write(PNG)
        B.page.set_input_files("#up-PAN", fp)
        try:
            B.page.wait_for_function("()=>/test-pan-photo\\.png/.test(document.querySelector('#view-apply .vcard')?.innerText||'')", timeout=15000)
            up = True
        except Exception:  # noqa: BLE001
            up = False
    check("with the camera blocked a file can still be uploaded", up, B.page.inner_text("#view-apply .vcard")[:300] if B.page.locator("#view-apply .vcard").count() else "")
    B.page.click('[data-dk="1"]')
    B.page.wait_for_timeout(300)
    evs = B.page.evaluate("(()=>{try{return JSON.parse(localStorage.getItem('pn-onboarding-v1')).S.events.filter(e=>e.event==='CAMERA_CHECK').map(e=>e.note)}catch(e){return null}})()")
    check("each check is logged as CAMERA_CHECK with its result only", evs and len(evs) == 2 and all(n.startswith("Result: blocked") for n in evs), evs)
    check("no page errors (B)", not B.errors, B.errors[:3])
    B.close()


def run_states(browser):
    """The busy, black-picture and unsupported states."""
    for gum, st, word in (("busy", "busy", "in use by another app"), ("dark", "dark", "picture is black")):
        D = Dev(browser, gum, gum=gum)
        begin(D)
        check("%s: the check shows '%s'" % (gum, word), D.cam_state(st, 15000) and word in D.page.inner_text("#cam-st"), D.page.inner_text("#camdlg"))
        D.page.click('[data-act="cam-cont"]')
        if gum == "dark":
            try:
                D.page.wait_for_function("()=>window.__streams.length>0&&window.__streams.flatMap(s=>s.getTracks()).every(t=>t.readyState==='ended')", timeout=5000)
                st_ok = True
            except Exception:  # noqa: BLE001
                st_ok = False
            check("dark: the stream is stopped on close", st_ok)
        check("no page errors (%s)" % gum, not D.errors, D.errors[:3])
        D.close()
    U = Dev(browser, "U", unsupported=True)
    begin(U)
    check("unsupported: the check says it is not available and offers uploads", U.cam_state("unsupported") and "upload photos" in U.page.inner_text("#cam-help"), U.page.inner_text("#camdlg"))
    U.page.click('[data-act="cam-upload"]')
    check("unsupported: Upload photos instead closes the dialog", U.page.evaluate("!document.getElementById('camdlg').open"))
    check("no page errors (U)", not U.errors, U.errors[:3])
    U.close()


def run_trust(browser):
    W = Dev(browser, "W")
    W.goto("#welcome")
    W.page.wait_for_timeout(500)
    txt = W.page.inner_text("body")
    check("How we protect your information says the camera check stays on the device", "Nothing from the camera is recorded, saved or sent" in txt)
    check("How we protect your information says speech recognition may be processed by the browser maker", "your browser's maker may process the audio" in txt)
    W.close()


def parity():
    html = open(os.path.join(ROOT, "policynest_mvp", "PolicyNest.html"), encoding="utf-8").read()
    a = html.index("var I18N_VC={en:{")
    b = html.index("}};", a) + 2
    src = html[a + len("var I18N_VC="):b]
    src = src.replace("{en:{", '{"en":{', 1).replace("},hi:{", '},"hi":{', 1)
    d = json.loads(src)
    en, hi = d["en"], d["hi"]
    ph = lambda s: sorted(re.findall(r"\{([A-Za-z0-9_]+)\}", s))  # noqa: E731
    miss = sorted(set(en) ^ set(hi))
    bad = [k for k in en if k in hi and ph(en[k]) != ph(hi[k])]
    check("en/hi parity for the new strings (%d keys)" % len(en), not miss and not bad, (miss, bad))
    check("no new string spells out an ID number", not any(ID_LIKE.search(v) for v in list(en.values()) + list(hi.values())))


def main():
    if not os.path.isfile(os.path.join(SITE_DIR, "index.html")):
        raise SystemExit("build the demo site first: python policynest_mvp/build_site.py --mode demo")
    parity()
    srv = start_static()
    try:
        with sync_playwright() as p:
            args = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]
            try:
                browser = p.chromium.launch(args=args)
            except Exception:  # noqa: BLE001 (no bundled Chromium: use the installed Chrome, as the other suites do)
                browser = p.chromium.launch(channel="chrome", args=args)
            try:
                for fn in (run_main, run_gesture_and_hindi, run_blocked, run_states, run_trust):
                    try:
                        fn(browser)
                    except Exception as e:  # noqa: BLE001
                        check(fn.__name__ + " ran to the end", False, repr(e))
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()
    leaked = [s["text"] for s in ALL_SPOKEN if ID_LIKE.search(s["text"]) or SAMPLE_PAN in s["text"]]
    check("across every run no spoken line held a 9+ digit run, an Aadhaar-like number or a PAN (%d lines)" % len(ALL_SPOKEN),
          ALL_SPOKEN and not leaked, leaked[:3])
    passed = sum(1 for r in RESULTS if r[1])
    print("\n%d/%d checks passed" % (passed, len(RESULTS)))
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
