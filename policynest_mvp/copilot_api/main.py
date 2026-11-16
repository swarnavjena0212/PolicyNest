"""PolicyNest Onboarding Copilot proxy.

A tiny HTTP server that holds the Anthropic API key (env var ANTHROPIC_API_KEY) so the
browser never sees it. The static site POSTs {question, context, history} to /api/copilot.
This server looks up the rulebook passages itself (BM25 over the applicant-safe kb.json that
build_site.py writes next to this file), checks the context against a fixed schema, refuses
ID numbers, calls Claude and filters the answer (ADR 0002). Passages sent by a browser are ignored.

Deployed as its own Embarko server app (static sites cannot hold env vars).
Run locally:  ANTHROPIC_API_KEY=... python main.py   (reads PORT, default 8787)
"""
import json
import math
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from http.cookies import SimpleCookie

# Local runs: read ../../.env if present (Embarko sets real env vars and never gets .env).
_here = Path(__file__).resolve()
for envfile in [p / ".env" for p in (_here.parent, *_here.parents[1:3])]:
    if not os.environ.get('POLICYNEST_SKIP_DOTENV') and envfile.is_file():
        for line in envfile.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

import logging
import auth
import policy
import portal
import workflows
import reviewer
import application_state
import rag
import llm
import assistant
import guard
import telemetry
import recovery
import secrets
import threading
from urllib.parse import urlsplit, parse_qs

log = logging.getLogger('policynest.api')
MODEL = llm.model()  # provider model name (never a key); /api/health reports it
EFFORT = os.environ.get("COPILOT_EFFORT", "low")
# Production (Embarko sets APP_NAME) allows only the live site; local runs also allow the dev server.
# The "null" origin (a page opened from file://) is never allowed (G-24).
_default_origins = "https://policynest.embarko.app" + ("" if os.environ.get("APP_NAME") else ",http://localhost:8000,http://127.0.0.1:8000")
ALLOWED = {o.strip() for o in os.environ.get("COPILOT_ORIGINS", _default_origins).split(",") if o.strip() and o.strip() != "null"}
MAX_BODY = 24_000
RATE = (int(os.environ.get("COPILOT_RATE", "20")), 600)  # questions per 10 minutes per client
GLOBAL_PER_MIN = int(os.environ.get("COPILOT_GLOBAL_PER_MIN", "30"))  # all clients together (fallback if the client address is unreliable)
DAILY_CAP = int(os.environ.get("COPILOT_DAILY_CAP", "50"))  # AI answers per UTC day (G-25, user decision 14 Nov)
_hits = {}
_global = []
_day = {"date": "", "n": 0}

SYSTEM = assistant.SYSTEM  # the applicant-assistant prompt lives in assistant.py


def _cors(h, origin):
    if origin in ALLOWED:
        h.send_header("Access-Control-Allow-Origin", origin)
        h.send_header("Vary", "Origin")
        h.send_header("Access-Control-Allow-Credentials", "true")
        h.send_header("Access-Control-Allow-Methods", "POST, GET, PUT, PATCH, OPTIONS")
        h.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")


def _client(h):
    """The client address the edge proxy saw. A client can put anything at the left of X-Forwarded-For,
    so only entries appended by Embarko's own proxies are trusted (G-24). Measured 14 Nov: Embarko's two proxy
    hops append two entries ([client, edge]; a spoofed header becomes [spoof, client, edge]), so the client
    is the second entry from the right. XFF_TRUSTED_HOPS overrides this if Embarko changes."""
    hops = int(os.environ.get("XFF_TRUSTED_HOPS", "2"))
    xff = [p.strip() for p in (h.headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
    return xff[-hops] if len(xff) >= hops else h.client_address[0]


_rate_lock=threading.RLock()
_budget_lock=threading.RLock()

def _limited(ip):
    with _rate_lock:return _limited_unlocked(ip)

def _limited_unlocked(ip):
    now = time.time()
    if len(_hits) > 5000:  # prune entries with no recent questions
        for k in [k for k, v in _hits.items() if not v or now - v[-1] >= RATE[1]]:
            _hits.pop(k, None)
    _global[:] = [t for t in _global if now - t < 60]
    q = [t for t in _hits.get(ip, []) if now - t < RATE[1]]
    if len(q) >= RATE[0] or len(_global) >= GLOBAL_PER_MIN:
        _hits[ip] = q
        return True
    q.append(now)
    _hits[ip] = q
    _global.append(now)
    return False


def _daily_left():
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if _day["date"] != today:
        _day.update(date=today, n=0)
    try:
        with auth._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS ai_daily_usage (day TEXT PRIMARY KEY,requests INTEGER NOT NULL)')
            row=db.execute('SELECT requests FROM ai_daily_usage WHERE day=?',(today,)).fetchone()
            used=row['requests'] if row else 0
    except Exception as e:
        # A database error must not silently turn the budget to zero: log it and count in memory instead.
        log.warning('daily usage table unavailable (%s); using the in-memory counter',type(e).__name__)
        used=0
    return max(0, DAILY_CAP - max(_day['n'],used))


def _reserve_answer():
    """Persist the spend ceiling across restarts; failed upstream attempts conservatively consume a slot."""
    with _budget_lock:
        if _daily_left()<=0:return False
        today=time.strftime('%Y-%m-%d',time.gmtime())
        try:
            with auth._db() as db:
                db.execute('CREATE TABLE IF NOT EXISTS ai_daily_usage (day TEXT PRIMARY KEY,requests INTEGER NOT NULL)')
                db.execute('BEGIN IMMEDIATE')
                db.execute('INSERT OR IGNORE INTO ai_daily_usage VALUES(?,0)',(today,))
                changed=db.execute('UPDATE ai_daily_usage SET requests=requests+1 WHERE day=? AND requests<?',(today,DAILY_CAP)).rowcount
        except Exception as e:
            log.warning('could not persist the daily usage (%s); counting in memory',type(e).__name__)
            changed=1 if _day['n']<DAILY_CAP else 0
        if changed:_day['n']+=1
        return bool(changed)


def _key_ok():
    return llm.configured()


# ---------- Retrieval: the same BM25 the browser uses, over applicant-safe passages only ----------
_STOP = set("the and for are you your is of to in on a an it be or my me i we do does what how why can this that with not".split())


def tokens(s):
    s = re.sub(r"[^a-z0-9ऀ-ॿ ]+", " ", str(s).lower())
    return [w for w in s.split() if len(w) > 1 and w not in _STOP]


def load_kb(path=_here.parent / "kb.json"):
    try:
        kb = [c for c in json.loads(Path(path).read_text(encoding="utf-8")) if c.get("audience") == "applicant"]
    except (OSError, ValueError):
        kb = []
    df = {}
    for c in kb:
        c["_tk"] = tokens(c["head"] + " " + c["head"] + " " + c["text"])
        for t in set(c["_tk"]):
            df[t] = df.get(t, 0) + 1
    n = len(kb)
    idf = {t: math.log(1 + (n - d + .5) / (d + .5)) for t, d in df.items()}
    avg = sum(len(c["_tk"]) for c in kb) / max(1, n)
    return {"chunks": kb, "idf": idf, "avg": avg}


KB = load_kb()


def retrieve(q, n=5, kb=None):
    kb = kb or KB
    qt, k1, b, out = tokens(q), 1.4, .75, []
    for c in kb["chunks"]:
        tf = {}
        for t in c["_tk"]:
            tf[t] = tf.get(t, 0) + 1
        s = sum(kb["idf"].get(t, 0) * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(c["_tk"]) / kb["avg"]))
                for t in qt if t in tf)
        if s > 0:
            out.append((s, c))
    return [c for s, c in sorted(out, key=lambda x: -x[0])[:n]]


# ---------- Input checks (shared guard module) ----------
from guard import ID_NUMBER, id_in, BANNED_OUT, SAFE, SAFE_HI, REFUSED, REFUSED_HI, INJECTION  # noqa: E402
CODE = re.compile(r"^[A-Za-z0-9_:\- ]{1,40}$")


def _s(v, n):
    return str(v)[:n] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else None


def _i(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def clean_context(ctx):
    """Keep only the fields the page sends (copilotContext in PolicyNest.html), with types and lengths
    checked. Anything else is dropped, so a caller cannot smuggle free text into the prompt."""
    ctx = ctx if isinstance(ctx, dict) else {}
    out = {"first_name": re.sub(r"[^A-Za-zऀ-ॿ .'-]", "", _s(ctx.get("first_name"), 40) or "") or None}
    for k, n in (("stage", 40), ("details_page", 60), ("application_status", 40), ("decision", 40),
                 ("suggested_next_step", 300)):
        out[k] = _s(ctx.get(k), n)
    for k in ("minutes_left_in_sitting", "sittings", "round", "exam_score"):
        out[k] = _i(ctx.get(k))
    th = ctx.get("training_hours")
    out["training_hours"] = th if isinstance(th, (int, float)) and not isinstance(th, bool) else None
    docs = []
    for d in (ctx.get("documents") or [])[:6] if isinstance(ctx.get("documents"), list) else []:
        if isinstance(d, dict):
            docs.append({"document": _s(d.get("document"), 40), "status": _s(d.get("status"), 20),
                         "refusal_reason": _s(d.get("refusal_reason"), 200),
                         "open_issues": [x for x in (d.get("open_issues") or [])[:8]
                                         if isinstance(x, str) and CODE.match(x)]})
    out["documents"] = docs
    fu = ctx.get("follow_up_items") if isinstance(ctx.get("follow_up_items"), list) else []
    out["follow_up_items"] = [{"document": _s(i.get("document"), 40), "issue": _s(i.get("issue"), 40)}
                              for i in fu[:10] if isinstance(i, dict)]
    fe = ctx.get("field_errors") if isinstance(ctx.get("field_errors"), list) else []
    out["field_errors"] = [x for x in fe[:12] if isinstance(x, str) and CODE.match(x)]
    return out


def clean_history(hist):
    msgs = []
    for turn in (hist if isinstance(hist, list) else [])[-6:]:
        if isinstance(turn, dict) and turn.get("role") in ("user", "assistant") and isinstance(turn.get("text"), str):
            msgs.append({"role": turn["role"], "content": turn["text"][:800]})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if msgs and msgs[-1]["role"] == "user":
        msgs.pop()
    return msgs


def has_id_number(body):
    texts = [str(body.get("question", ""))] + [t.get("text", "") for t in (body.get("history") or [])
                                              if isinstance(t, dict) and t.get("role") == "user"]
    ctx = body.get("context") if isinstance(body.get("context"), dict) else {}
    texts += [str(ctx.get("suggested_next_step", ""))] + [str(d.get("refusal_reason", "")) for d in
                                                         (ctx.get("documents") or []) if isinstance(d, dict)]
    return any(id_in(t) for t in texts)


def build_request(body):
    """Pure: the model request and the citations, from the request body. Passages from the body are ignored."""
    ctx = clean_context(body.get("context"))
    ctx["source"] = "this device (not signed in)"
    q = str(body.get("question", ""))[:1200]
    lang = assistant.lang_for(q, "hi" if body.get("lang") == "hi" else "en")
    hits = retrieve(assistant.retrieval_query(q, ctx))
    return assistant.build_kw(q, clean_history(body.get("history")), ctx, hits, lang), [c["cite"] for c in hits]


def call_model(kw):
    """Returns (stop_reason, text) from the configured provider (Gemini by default; see llm.py)."""
    return llm.generate(kw)


def ask(body):
    kw, cites = build_request(body)
    stop, text = call_model(kw)
    if stop == "refusal":
        return REFUSED, cites
    if not text or BANNED_OUT.search(text) or id_in(text):
        return SAFE, cites
    return text, cites


class H(BaseHTTPRequestHandler):
    def handle_one_request(self):
        self._started=time.perf_counter();self._request_id=secrets.token_hex(12)
        try:
            return super().handle_one_request()
        except (ConnectionResetError,BrokenPipeError):
            return
        except Exception:
            try:self._json(500,{'error':'internal_error','request_id':self._request_id})
            except (ConnectionResetError,BrokenPipeError):pass
    def _cookie(self, name):
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            return cookies[name].value if name in cookies else ''
        except Exception:
            return ''

    def _identity(self):
        return auth.authenticate(self.headers.get('Authorization') or 'Bearer ' + self._cookie('pn_access'))

    def _session(self, code, user, refresh):
        self._session_cookies = (auth.issue(user,refresh), refresh)
        return self._json(code, {'user': user})

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        _cors(self, self.headers.get("Origin", ""))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Request-ID',getattr(self,'_request_id',''))
        self.send_header('X-Content-Type-Options','nosniff')
        if hasattr(self, '_session_cookies'):
            secure = '; Secure' if os.environ.get('APP_NAME') else ''
            for name, value, age in [('pn_access', self._session_cookies[0], auth.JWT_TTL), ('pn_refresh', self._session_cookies[1], 604800)]:
                self.send_header('Set-Cookie', f'{name}={value}; HttpOnly; SameSite=Lax; Path=/api; Max-Age={age if value else 0}{secure}')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            route=urlsplit(self.path).path
            known=reviewer.template(route) or (route if route in ('/api/auth/login','/api/auth/register','/api/auth/refresh','/api/auth/logout','/api/assistant','/api/appeals','/api/applications/me','/api/copilot','/api/me','/api/notifications','/api/notifications/read','/api/applications/me/events','/api/applications/me/timeline','/api/events','/api/telemetry') else '/api/other')
            elapsed=(time.perf_counter()-self._started)*1000
            telemetry.technical(known,code,elapsed,self._request_id)
            if os.environ.get('STRUCTURED_LOGS')=='1':print(json.dumps({'request_id':self._request_id,'route':known,'status':code,'latency_ms':round(elapsed,2)}),flush=True)
        except Exception:pass
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        _cors(self, self.headers.get("Origin", ""))
        self.end_headers()

    def do_GET(self):
        parsed=urlsplit(self.path);params=parse_qs(parsed.query);self.path=parsed.path
        legacy=self.path=='/api/applications/me' and params.get('view',[''])[0]=='snapshot'
        if self._reviewer('GET',None,params):return
        if not legacy and self._portal('GET',None,params):return
        if self.path=='/api/reviewer/case':
            try:return self._json(200,workflows.case_detail(self._identity(),params.get('id',[''])[0]))
            except ValueError:return self._json(403,{'error':'forbidden'})
        if self.path=='/api/reports':
            try:
                workflows.init()
                return self._json(200,telemetry.report(self._identity(),int(params.get('days',['30'])[0]),params.get('source',['demo'])[0],params.get('stage',[None])[0]))
            except ValueError:return self._json(403,{'error':'forbidden_or_invalid_filter'})
        if self.path=='/api/ready':
            try:
                auth._secret();workflows.init()
                return self._json(200,{'ready':True,'database':True,'corpus':rag.INDEX.exists()})
            except Exception:return self._json(503,{'ready':False})
        if self.path in ('/api/cases/me', '/api/reviewer/queue', '/api/appeals/me'):
            try:
                user = self._identity()
                if self.path == '/api/reviewer/queue': return self._json(200, {'cases': workflows.queue(user)})
                case = workflows.own_case(user)
                return self._json(200, case if self.path == '/api/cases/me' else {'appeals': workflows.appeals(user, case['id'])})
            except ValueError: return self._json(403, {'error': 'forbidden'})
        if self.path == "/api/auth/me":
            try: return self._json(200, {"user": self._identity()})
            except ValueError as e: return self._json(401, {"error": str(e)})
        if self.path == "/api/applications/me":
            try:
                user = self._identity()
                case=workflows.own_case(user)
                if not policy.allows(user, "application.read", case): raise ValueError("forbidden")
                return self._json(200, auth.load_snapshot(user) or {"snapshot": None})
            except ValueError as e: return self._json(403 if str(e)=="forbidden" else 401, {"error": str(e)})
        if self.path.startswith("/api/health") or self.path == "/":
            xff = [p for p in (self.headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
            # xff_hops lets us check how the edge builds X-Forwarded-For (no addresses are returned)
            return self._json(200, {"ok": True, "llm": _key_ok(), "provider": llm.provider(), "model": llm.model() if _key_ok() else None, "daily_left": _daily_left(),
                                    "daily_cap": DAILY_CAP, "xff_hops": len(xff)})
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        origin = self.headers.get("Origin", "")
        if origin not in ALLOWED:
            return self._json(403, {"error": "origin_not_allowed"})
        try: n = int(self.headers.get("Content-Length") or 0)
        except ValueError: return self._json(400, {'error': 'bad_length'})
        if n <= 0 or n > MAX_BODY:
            return self._json(413, {"error": "body_too_large"})
        try:
            body = json.loads(self.rfile.read(n))
        except ValueError:
            return self._json(400, {"error": "bad_json"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "schema"})
        if self._reviewer('POST',body,{}):return
        if not (self.path=='/api/applications/me' and 'snapshot' in body) and self._portal('POST',body,{}):return
        if self.path=='/api/reviewer/decide':
            try:return self._json(200,workflows.review(self._identity(),body.get('case_id'),body.get('status'),body.get('note'),body.get('rule')))
            except ValueError:return self._json(403,{'error':'forbidden_or_invalid_review'})
        if self.path=='/api/events':
            try:
                user=self._identity()
                if not portal.has_analytics_consent(user):return self._json(403,{'error':'consent_required'})
                return self._json(201,{'event_id':telemetry.business(user,body)})
            except ValueError as e:
                capped=str(e)=='daily_cap'
                return self._json(429 if capped else 400,{'error':'daily_cap' if capped else 'invalid_event_or_identity'})
        if self.path=='/api/telemetry':
            if _limited('telemetry:'+_client(self)):return self._json(429,{'error':'rate_limited'})
            try:telemetry.frontend(body.get('metrics'));return self._json(200,{'ok':True})
            except ValueError as e:
                capped=str(e)=='daily_cap'
                return self._json(429 if capped else 400,{'error':'daily_cap' if capped else 'invalid_metrics'})
        if self.path=='/api/assistant':
            return self._assistant(body)
        if self.path in ('/api/appeals', '/api/reviewer/appeals/resolve', '/api/admin/assign'):
            try:
                user = self._identity()
                if self.path == '/api/appeals':
                    case = workflows.own_case(user)
                    try:
                        return self._json(201, workflows.appeals(user, case['id'], body.get('explanation')))
                    except ValueError as e:
                        code = {'do_not_include_identity_numbers': (400, 'id_number'), 'invalid_explanation': (400, 'invalid_explanation'),
                                'appeal_open': (409, 'appeal_open')}.get(str(e))
                        if code: return self._json(code[0], {'error': code[1]})
                        raise
                if self.path == '/api/admin/assign':
                    workflows.assign(user, body.get('case_id'), body.get('reviewer_id'))
                    return self._json(200, {'ok': True})
                return self._json(200, workflows.resolve(user, body.get('reference'), body.get('status'), body.get('response')))
            except ValueError as e:
                if str(e) == 'already_resolved': return self._json(409, {'error': 'already_resolved'})
                return self._json(403, {'error': 'forbidden_or_invalid_request'})
        if self.path.startswith("/api/auth/") and _limited(_client(self)):
            return self._json(429, {"error": "rate_limited"})
        if self.path == "/api/auth/refresh":
            try:
                user, refresh = auth.rotate_refresh(self._cookie('pn_refresh'))
                return self._session(200, user, refresh)
            except ValueError:
                return self._json(401, {"error": "invalid_session"})
        if self.path == "/api/auth/logout":
            auth.revoke_refresh(self._cookie('pn_refresh'))
            self._session_cookies = ('', '')
            return self._json(200, {"ok": True})
        if self.path == "/api/auth/recover":
            def deliver():
                try:recovery.send(str(body.get('email','')))
                except Exception:pass
            threading.Thread(target=deliver,daemon=True).start()
            return self._json(202, {"message": "If an account exists and recovery delivery is configured, a reset link will be sent. Otherwise contact your administrator for recovery."})
        if self.path == "/api/auth/reset":
            try:
                auth.reset_password(str(body.get("reset_token", "")), body.get("password"))
                return self._json(200, {"ok": True})
            except ValueError:
                return self._json(400, {"error": "invalid_reset"})
        if self.path == "/api/auth/register":
            try:
                user = reviewer.register_account(body, _client(self))
                return self._session(201, user, auth.new_refresh(user))
            except portal.PortalError as e: return self._json(e.status, e.payload)
            except ValueError as e: return self._json(400 if str(e) != "email_taken" else 409, {"error": str(e)})
        if self.path == "/api/auth/login":
            try:
                user = auth.login(body.get("email"), body.get("password"))
                return self._session(200, user, auth.new_refresh(user))
            except ValueError as e: return self._json(401 if str(e)=="invalid_credentials" else 503, {"error": str(e)})
        if self.path == "/api/applications/me":
            try:
                user = self._identity()
                case=workflows.own_case(user);snapshot=application_state.minimise(body.get('snapshot'))
                allowed=policy.allows(user,'application.save',case)
                if not allowed and policy.allows(user,'progress.save',case):
                    stored=auth.load_snapshot(user)
                    allowed=bool(stored and stored['snapshot']['documents']==snapshot['documents'] and snapshot['stage'] in ('Training','Exam','Certificate'))
                if not allowed:raise ValueError('forbidden')
                auth.save_snapshot(user, snapshot)
                return self._json(200, {'ok':True})
            except ValueError as e: return self._json(403 if str(e)=="forbidden" else 401, {"error": str(e)})
        if not self.path.startswith("/api/copilot"):
            return self._json(404, {"error": "not_found"})
        if _limited(_client(self)):
            return self._json(429, {"error": "rate_limited"})
        if not _key_ok():
            return self._json(503, {"error": "no_key"})
        if _daily_left() <= 0:
            return self._json(429, {"error": "daily_cap"})
        if not isinstance(body, dict) or not str(body.get("question", "")).strip():
            return self._json(400, {"error": "schema"})
        if has_id_number(body):  # rulebook §4.3: never send ID numbers to a model
            return self._json(400, {"error": "id_number"})
        try:
            if not _reserve_answer():return self._json(429,{'error':'daily_cap'})
            answer, cites = ask(body)
            return self._json(200, {"answer": answer, "sources": cites, "model": llm.model(), "provider": llm.provider(), "daily_left": _daily_left()})
        except Exception as e:  # never leak the key or stack to the browser
            return self._json(502, {"error": "upstream", "type": type(e).__name__})

    def _assistant(self, body):
        """Applicant assistant: grounded in the rulebook and the applicant's own state (assistant.py)."""
        if _limited(_client(self)):return self._json(429,{'error':'rate_limited'})
        if not isinstance(body.get('question'),str) or not 1<=len(body['question'].strip())<=1200:return self._json(400,{'error':'schema'})
        if has_id_number(body):return self._json(400,{'error':'id_number'})
        ui_lang='hi' if body.get('lang')=='hi' else 'en'
        client_ctx=clean_context(body.get('context'))
        ctx=None;first=None
        try:
            user=self._identity()
            if user['role']!='applicant':return self._json(403,{'error':'applicant_only'})
            case=workflows.own_case(user)
            if not policy.allows(user,'assistant.ask',case):return self._json(403,{'error':'forbidden'})
            first=re.sub(r"[^A-Za-zऀ-ॿ .'-]",'',str(user.get('display_name') or '').split(' ')[0])[:40] or None
            try: ctx=assistant.ctx_from_progress(portal.get_application(user),first)
            except portal.PortalError: ctx=None
            if not ctx or not ctx.get('documents') and ctx.get('stage')=='Before you start':
                stored=auth.load_snapshot(user)
                if stored: ctx=assistant.ctx_from_snapshot(application_state.context(stored['snapshot']),first)
            if ctx:
                ctx['review_status']=case['status']
                ctx['appeals']=[{'id':a['id'],'status':a['status']} for a in workflows.appeals(user,case['id'])]
        except ValueError:
            if self._cookie('pn_access') or self.headers.get('Authorization'):return self._json(401,{'error':'session_expired'})
            ctx=None
        if ctx is None and (client_ctx.get('stage') or client_ctx.get('documents')):
            ctx=dict(client_ctx,source='this device (not signed in)')
        question=body['question'].strip()
        lang=assistant.lang_for(question,ui_lang)
        if INJECTION.search(question):
            return self._json(200,{'answer':assistant.refusal(lang),'sources':[],'mode':'safe_refusal','offer_appeal':False,'lang':lang})
        hits,diagnostics=rag.retrieve(assistant.retrieval_query(question,ctx),limit=6)
        telemetry.technical('/internal/retrieval',200,diagnostics['latency_ms'],self._request_id+'-rag')
        # Prefer the approved challenge corpus when it supplies directly relevant onboarding evidence.
        primary=[c for c in hits if c['source_type']=='approved_challenge_source' and c['score']>=hits[0]['score']*.88]
        evidence=(primary if primary else hits)[:5]
        retrieved=[c['cite'] for c in evidence]
        answer,sources=assistant.deterministic(question,ctx,evidence,lang)
        mode='deterministic'
        if not evidence and not ctx:
            answer='I could not find reliable supporting evidence. You can ask a human reviewer through the appeal control.'
        if llm.configured():
            if _reserve_answer():
                started=time.perf_counter()
                try:
                    hist=clean_history(body.get('history'))
                    stop,generated=call_model(assistant.build_kw(question,hist,ctx,evidence,lang))
                    telemetry.technical('/internal/llm',200,(time.perf_counter()-started)*1000,self._request_id+'-llm')
                    if stop!='refusal':
                        ans,cited,ok=assistant.finalize(generated,retrieved,lang)
                        if ok: answer,sources,mode=ans,cited,'ai_grounded'
                        else: mode='deterministic_filtered'
                    else: answer,sources,mode=assistant.refusal(lang),[],'safe_refusal'
                except Exception:
                    telemetry.technical('/internal/llm',502,(time.perf_counter()-started)*1000,self._request_id+'-llm')
                    mode='deterministic_upstream_unavailable'
            else: mode='deterministic_daily_cap'
        if guard.violates(answer) or id_in(answer):answer=SAFE_HI if lang=='hi' else SAFE
        note=None
        if mode.startswith('deterministic'):
            note=('आज AI उत्तर उपलब्ध नहीं हैं; यह उत्तर नियमावली से बना है।' if lang=='hi' else 'AI answers are not available right now; this answer comes from the rulebook and your saved progress.') if llm.configured() else None
        return self._json(200,{'answer':answer,'sources':sources,'mode':mode,'retrieval':diagnostics,'offer_appeal':True,'lang':lang,
                               'context_source':'authenticated_saved_progress' if ctx and ctx.get('source','').startswith('server') else ('device_progress' if ctx else 'general_policy_only'),
                               'fallback_note':note,'daily_left':_daily_left()})

    def _portal(self, method, body, params):
        """Applicant-portal routes (portal.ROUTES). Returns True when the request was handled."""
        route = portal.ROUTES.get((method, self.path))
        if not route:
            return False
        try:
            user = self._identity()
        except ValueError:
            self._json(401, {'error': 'unauthenticated'})
            return True
        if method == 'POST' and self.path == '/api/applications/me/events' and _limited('portal:' + _client(self)):
            self._json(429, {'error': 'rate_limited'})
            return True
        try:
            code, out = route(user, body if isinstance(body, dict) else {}, params)
            self._json(code, out)
        except portal.PortalError as e:
            self._json(e.status, e.payload)
        return True

    def _reviewer(self, method, body, params):
        """Reviewer workspace and admin routes (reviewer.ROUTES). Returns True when the request was handled."""
        found = reviewer.match(method, self.path)
        if not found:
            return False
        fn, groups = found
        try:
            user = self._identity()
        except ValueError:
            self._json(401, {'error': 'unauthenticated'})
            return True
        try:
            self._json(200, fn(user, body if isinstance(body, dict) else {}, params, groups))
        except portal.PortalError as e:
            self._json(e.status, e.payload)
        return True

    def _body_request(self, method):
        self.path = urlsplit(self.path).path
        if self.headers.get("Origin", "") not in ALLOWED:
            return self._json(403, {"error": "origin_not_allowed"})
        try: n = int(self.headers.get("Content-Length") or 0)
        except ValueError: return self._json(400, {'error': 'bad_length'})
        if n <= 0 or n > MAX_BODY:
            return self._json(413, {"error": "body_too_large"})
        try: body = json.loads(self.rfile.read(n))
        except ValueError: return self._json(400, {"error": "bad_json"})
        if not isinstance(body, dict): return self._json(400, {"error": "schema"})
        if not self._portal(method, body, {}):
            return self._json(404, {"error": "not_found"})

    def do_PUT(self):
        self._body_request('PUT')

    def do_PATCH(self):
        self._body_request('PATCH')

    def log_message(self, fmt, *args):  # no request bodies in logs
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8787"))
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
