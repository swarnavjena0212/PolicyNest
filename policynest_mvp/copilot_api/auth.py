"""Minimal server-side identity, JWT and ownership store for PolicyNest.

Passwords are scrypt hashes; JWTs are HMAC-SHA256 and contain only subject, role and expiry.
Reviewer access is assigned by an operator directly in the database, never by registration input.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager, closing


HERE = Path(__file__).resolve().parent
def resolve_db_path(env=None):
    """DATA_DIR (Embarko's durable folder) wins, then POLICYNEST_DB_PATH, then the local default."""
    env = os.environ if env is None else env
    if env.get("DATA_DIR"):
        return Path(os.path.join(env["DATA_DIR"], "policynest.sqlite3"))
    return Path(env.get("POLICYNEST_DB_PATH") or str(HERE / "policynest.sqlite3"))


DB_PATH = resolve_db_path()
JWT_TTL = int(os.environ.get("JWT_TTL_SECONDS", "3600"))


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _secret():
    value = os.environ.get("JWT_SECRET", "")
    if len(value) < 32 or value.startswith("your-"):
        raise ValueError("auth_not_configured")
    return value.encode("utf-8")


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
          password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'applicant', created_at INTEGER NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS application_snapshots (
          user_id INTEGER PRIMARY KEY REFERENCES users(id), snapshot TEXT NOT NULL, updated_at INTEGER NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS refresh_tokens (
          digest TEXT PRIMARY KEY, user_id INTEGER NOT NULL, family TEXT NOT NULL,
          expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0)""")
        db.execute("""CREATE TABLE IF NOT EXISTS reset_tokens (
          digest TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0)""")
        if 'token_version' not in {r[1] for r in db.execute('PRAGMA table_info(users)')}:
            db.execute('ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0')
        cols = {r[1] for r in db.execute('PRAGMA table_info(users)')}
        for name, ddl in (('language', "TEXT NOT NULL DEFAULT 'en'"), ('mobile', 'TEXT'), ('analytics_consent', 'INTEGER NOT NULL DEFAULT 0')):
            if name not in cols:
                db.execute('ALTER TABLE users ADD COLUMN ' + name + ' ' + ddl)
        db.commit()


@contextmanager
def _db():
    init_db()
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def _hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return _b64(salt) + "$" + _b64(key)


def _verify(password, stored):
    try:
        salt, expected = stored.split("$", 1)
        return hmac.compare_digest(_hash(password, _unb64(salt)), stored)
    except (ValueError, TypeError):
        return False


def _public(row):
    return {"id": int(row["id"]), "email": row["email"], "display_name": row["display_name"], "role": row["role"]}


REGISTRABLE_ROLES = ("applicant", "reviewer_pending", "admin")  # reviewer and ops are never granted at registration


def register(email, password, display_name, role="applicant", after_insert=None):
    """Create an account. role is chosen by the server (reviewer.register_account), never taken from input;
    reviewer power needs an admin's approval. after_insert(db, user_id) runs in the same transaction."""
    if role not in REGISTRABLE_ROLES:
        raise ValueError("invalid_role")
    _secret()  # fail before inserting an account when authentication is not configured
    email = str(email or "").strip().lower()
    display_name = str(display_name or "").strip()
    if len(email) > 254 or "@" not in email or not isinstance(password, str) or not 12 <= len(password) <= 256 or len(display_name) < 2 or len(display_name) > 80:
        raise ValueError("invalid_registration")
    try:
        with _db() as db:
            cur = db.execute("INSERT INTO users(email, display_name, password_hash, role, created_at) VALUES(?,?,?,?,?)",
                             (email, display_name, _hash(password), role, int(time.time())))
            if after_insert:
                after_insert(db, cur.lastrowid)
            row = db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    except sqlite3.IntegrityError:
        raise ValueError("email_taken")
    return _public(row)


def login(email, password):
    with _db() as db:
        row = db.execute("SELECT * FROM users WHERE email=?", (str(email or "").strip().lower(),)).fetchone()
    if not row or not _verify(str(password or ""), row["password_hash"]):
        raise ValueError("invalid_credentials")
    return _public(row)


def issue(user, refresh=None):
    now = int(time.time())
    with _db() as db:
        version = db.execute('SELECT token_version FROM users WHERE id=?', (user['id'],)).fetchone()[0]
        session = db.execute('SELECT family FROM refresh_tokens WHERE digest=? AND revoked=0 AND used=0 AND expires>?',(_digest(refresh),int(time.time()))).fetchone() if refresh else None
        if refresh and not session:raise ValueError('invalid_session')
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps({"sub": str(user["id"]), "role": user["role"], "sid":session['family'] if session else None,"ver": version, "iss": "policynest", "aud": "policynest-api", "iat": now, "exp": now + JWT_TTL}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_secret(), (head + "." + body).encode(), hashlib.sha256).digest())
    return head + "." + body + "." + sig


def authenticate(header):
    if not isinstance(header, str) or not header.startswith("Bearer "):
        raise ValueError("unauthenticated")
    try:
        head, body, sig = header[7:].split(".")
        if json.loads(_unb64(head)) != {"alg": "HS256", "typ": "JWT"}: raise ValueError
        expected = _b64(hmac.new(_secret(), (head + "." + body).encode(), hashlib.sha256).digest())
        data = json.loads(_unb64(body))
        if not hmac.compare_digest(sig, expected) or data.get("exp", 0) <= time.time() or data.get('iss') != 'policynest' or data.get('aud') != 'policynest-api': raise ValueError
        with _db() as db:
            row = db.execute("SELECT * FROM users WHERE id=?", (int(data["sub"]),)).fetchone()
            if data.get('sid') and not db.execute('SELECT 1 FROM refresh_tokens WHERE family=? AND revoked=0 AND expires>?',(data['sid'],int(time.time()))).fetchone():raise ValueError
        if not row or row["role"] != data.get("role") or row['token_version'] != data.get('ver'): raise ValueError
        return _public(row)
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, json.JSONDecodeError):
        raise ValueError("unauthenticated")


def save_snapshot(user, snapshot):
    if user["role"] != "applicant": raise ValueError("forbidden")
    from application_state import minimise
    snapshot = minimise(snapshot)
    raw = json.dumps(snapshot, separators=(",", ":"))
    if len(raw) > 24_000: raise ValueError("snapshot_too_large")
    with _db() as db:
        db.execute("INSERT INTO application_snapshots(user_id,snapshot,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET snapshot=excluded.snapshot,updated_at=excluded.updated_at", (user["id"], raw, int(time.time())))


def load_snapshot(user):
    if user["role"] != "applicant": raise ValueError("forbidden")
    with _db() as db: row = db.execute("SELECT snapshot,updated_at FROM application_snapshots WHERE user_id=?", (user["id"],)).fetchone()
    return None if not row else {"snapshot": json.loads(row["snapshot"]), "updated_at": row["updated_at"]}


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_refresh(user, family=None):
    token = secrets.token_urlsafe(48)
    with _db() as db:
        db.execute("INSERT INTO refresh_tokens(digest,user_id,family,expires) VALUES(?,?,?,?)",
                   (_digest(token), user["id"], family or secrets.token_hex(16), int(time.time()) + 604800))
    return token


def rotate_refresh(token):
    """Consume once under a write lock; reuse revokes the entire token family."""
    invalid = False
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM refresh_tokens WHERE digest=?", (_digest(token),)).fetchone()
        if not row or row["revoked"] or row["used"] or row["expires"] <= time.time():
            if row:
                db.execute("UPDATE refresh_tokens SET revoked=1 WHERE family=?", (row["family"],))
                db.execute('UPDATE users SET token_version=token_version+1 WHERE id=?', (row['user_id'],))
            invalid = True
        else:
            db.execute("UPDATE refresh_tokens SET used=1 WHERE digest=?", (_digest(token),))
            user = _public(db.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone())
            replacement = secrets.token_urlsafe(48)
            db.execute("INSERT INTO refresh_tokens(digest,user_id,family,expires) VALUES(?,?,?,?)",
                       (_digest(replacement), user["id"], row["family"], int(time.time()) + 604800))
    if invalid: raise ValueError("invalid_session")
    return user, replacement


def revoke_refresh(token):
    with _db() as db:
        row = db.execute("SELECT family,user_id FROM refresh_tokens WHERE digest=?", (_digest(token),)).fetchone()
        if row:
            db.execute("UPDATE refresh_tokens SET revoked=1 WHERE family=?", (row["family"],))
            db.execute('UPDATE users SET token_version=token_version+1 WHERE id=?', (row['user_id'],))


def create_reset(email):
    """For trusted delivery adapter only. Never include this token in an HTTP recovery response."""
    with _db() as db:
        row = db.execute("SELECT id FROM users WHERE email=?", (str(email).strip().lower(),)).fetchone()
        if not row: return None
        token = secrets.token_urlsafe(48)
        db.execute("UPDATE reset_tokens SET used=1 WHERE user_id=?", (row["id"],))
        db.execute("INSERT INTO reset_tokens VALUES(?,?,?,0)", (_digest(token), row["id"], int(time.time()) + 900))
        return token


def reset_password(token, password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256: raise ValueError("invalid_password")
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM reset_tokens WHERE digest=?", (_digest(token),)).fetchone()
        if not row or row["used"] or row["expires"] <= time.time(): raise ValueError("invalid_reset")
        db.execute("UPDATE users SET password_hash=?,token_version=token_version+1 WHERE id=?", (_hash(password), row["user_id"]))
        db.execute("UPDATE reset_tokens SET used=1 WHERE user_id=?", (row["user_id"],))
        db.execute("UPDATE refresh_tokens SET revoked=1 WHERE user_id=?", (row["user_id"],))
