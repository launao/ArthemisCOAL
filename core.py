"""
core.py — Núcleo compartido de Arthemis Health (ArthemisCOAL).

Contiene:
  - Conexión a BD (PostgreSQL / SQLite)
  - Helpers SQL: rows(), row(), adapt(), Q(), NOW(), TODAY(), jstr()
  - Context manager db_ctx()
  - Auditoría: audit()
  - SSE broadcast
  - Auth: hash_pass, verify_pass, validate_password, login_required, requiere_permiso
  - seed_auth() con roles base y usuario admin
"""

import os, json, hashlib, hmac, secrets, re, time, threading, queue
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps

from flask import jsonify, request, session
from werkzeug.security import generate_password_hash, check_password_hash

# ── DATABASE ──────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv('DATABASE_URL', '')
USE_PG = bool(DATABASE_URL and DATABASE_URL.startswith('postgresql'))
TZ_COL = os.getenv('TZ_CLINICA', 'America/Bogota')

_pg_pool = None

def _init_pg_pool():
    global _pg_pool
    if _pg_pool is None and USE_PG:
        import psycopg2.pool
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=int(os.getenv('DB_POOL_MAX', 10)),
            dsn=DATABASE_URL,
            options=f'-c timezone={TZ_COL}',
        )

def get_db():
    if USE_PG:
        _init_pg_pool()
        conn = _pg_pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute("SET TIME ZONE %s", (TZ_COL,))
            conn.commit()
            cur.close()
        except Exception:
            conn.rollback()
        return conn, 'pg'
    import sqlite3
    os.makedirs('data', exist_ok=True)
    conn = sqlite3.connect('data/arthemis.db')
    conn.row_factory = sqlite3.Row
    return conn, 'sqlite'

def _return_db(conn, db):
    """Return a connection to the pool (PG) or close it (SQLite)."""
    if db == 'pg' and _pg_pool is not None:
        try:
            _pg_pool.putconn(conn)
        except Exception:
            try: conn.close()
            except Exception: pass
    else:
        try: conn.close()
        except Exception: pass

@contextmanager
def db_ctx():
    """Context manager that guarantees the connection is returned/closed."""
    conn, db = get_db()
    try:
        yield conn, db
    finally:
        _return_db(conn, db)

# ── SQL HELPERS ───────────────────────────────────────────────────────────────

def rows(cur, q, p=()):
    cur.execute(q, p)
    rs = cur.fetchall()
    if not rs:
        return []
    if hasattr(rs[0], 'keys'):
        return [dict(r) for r in rs]
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rs]

def row(cur, q, p=()):
    r = rows(cur, q, p)
    return r[0] if r else None

def Q(db):
    return '%s' if db == 'pg' else '?'

def NOW(db):
    return 'NOW()' if db == 'pg' else "datetime('now')"

def TODAY(db):
    return 'CURRENT_DATE' if db == 'pg' else "DATE('now','-5 hours')"

def adapt(q, db):
    if db == 'pg':
        return q.replace('?', '%s').replace(' LIKE ', ' ILIKE ').replace("datetime('now')", 'NOW()').replace("DATE('now')", 'CURRENT_DATE')
    return q

def jstr(v):
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else (v or '[]')

def audit(cur, db, entidad, entidad_id, accion, detalle='', usuario='sistema'):
    ip = ''
    ua = ''
    try:
        ip = request.remote_addr or ''
        ua = (request.user_agent.string or '')[:200]
    except RuntimeError:
        pass  # Outside request context
    cur.execute(
        adapt("INSERT INTO audit_trail(entidad,entidad_id,accion,detalle,usuario,ts,ip)VALUES(?,?,?,?,?,?,?)", db),
        (entidad, str(entidad_id), accion,
         f"{detalle} [UA:{ua}]".strip() if ua else detalle,
         usuario, datetime.now().isoformat(), ip))

# ── SSE ───────────────────────────────────────────────────────────────────────

_sse_clients = []
_sse_lock = threading.Lock()

def sse_broadcast(data):
    msg = "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)

# ── AUTH ──────────────────────────────────────────────────────────────────────

MODULOS_SISTEMA = [
    'kiosco', 'admisiones', 'historia_clinica', 'facturacion',
    'inventario', 'agendamiento', 'reportes', 'superadmin',
]

def _legacy_hash(pw):
    salt = os.getenv('PASS_SALT', 'arthemis_salt_2026')
    return hashlib.sha256(f"{salt}{pw}".encode()).hexdigest()

def validate_password(pw):
    """Validates password policy: min 8 chars, 1 uppercase, 1 number."""
    if not pw or len(pw) < 8:
        return False, 'La contraseña debe tener al menos 8 caracteres'
    if not re.search(r'[A-Z]', pw):
        return False, 'La contraseña debe contener al menos una letra mayúscula'
    if not re.search(r'[0-9]', pw):
        return False, 'La contraseña debe contener al menos un número'
    return True, ''

def hash_pass(pw):
    return generate_password_hash(pw)

def verify_pass(pw, stored):
    """Returns (ok, needs_upgrade). Compatible with legacy SHA-256 hashes."""
    if not stored:
        return False, False
    if stored.startswith('pbkdf2:') or stored.startswith('scrypt:'):
        return check_password_hash(stored, pw), False
    ok = hmac.compare_digest(stored, _legacy_hash(pw))
    return ok, ok

def get_user_permisos():
    if not session.get('user_id'):
        return []
    conn, db = get_db()
    cur = conn.cursor()
    u = row(cur, adapt("SELECT rol_id FROM usuarios WHERE id=?", db), (session['user_id'],))
    if not u:
        cur.close()
        _return_db(conn, db)
        return []
    r = row(cur, adapt("SELECT permisos FROM roles WHERE id=?", db), (u['rol_id'],))
    cur.close()
    _return_db(conn, db)
    try:
        return json.loads(r['permisos']) if r else []
    except Exception:
        return []

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({'error': 'No autenticado', 'login_required': True}), 401
        return f(*args, **kwargs)
    return wrapped

def requiere_permiso(modulo):
    """Decorator: checks if the logged-in user's role has the required module permission.
    Must be applied AFTER @login_required."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            permisos = session.get('_cached_permisos')
            if permisos is None:
                permisos = get_user_permisos()
                session['_cached_permisos'] = permisos
            if 'superadmin' in permisos:
                return f(*args, **kwargs)
            if modulo not in permisos:
                return jsonify({'error': f'No tiene permiso para el módulo: {modulo}'}), 403
            return f(*args, **kwargs)
        return wrapped
    return decorator

# ── RATE LIMITER (login) ─────────────────────────────────────────────────────

_login_attempts = {}
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_BLOCK_SECONDS = 300

_login_lock = threading.Lock()

def _check_rate_limit(ip):
    now = time.time()
    with _login_lock:
        attempts = _login_attempts.get(ip, [])
        attempts = [t for t in attempts if now - t < _LOGIN_BLOCK_SECONDS]
        _login_attempts[ip] = attempts
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            oldest = attempts[0]
            remaining = int(_LOGIN_BLOCK_SECONDS - (now - oldest))
            return False, max(remaining, 1)
        return True, 0

def _record_failed_login(ip):
    now = time.time()
    with _login_lock:
        if ip not in _login_attempts:
            _login_attempts[ip] = []
        _login_attempts[ip].append(now)

def _clear_login_attempts(ip):
    with _login_lock:
        _login_attempts.pop(ip, None)

# ── SEED AUTH ─────────────────────────────────────────────────────────────────

def seed_auth():
    conn, db = get_db()
    cur = conn.cursor()

    # Roles base
    cur.execute("SELECT COUNT(*) FROM roles")
    if cur.fetchone()[0] == 0:
        roles_base = [
            ('Superadmin', 'Acceso total al sistema', json.dumps(MODULOS_SISTEMA), 1),
            ('Recepción', 'Kiosco, admisiones y facturación', json.dumps(['kiosco', 'admisiones', 'facturacion']), 0),
            ('Médico', 'Historia clínica y agendamiento', json.dumps(['historia_clinica', 'agendamiento']), 0),
            ('Facturación', 'Facturación y reportes', json.dumps(['facturacion', 'reportes']), 0),
        ]
        for r in roles_base:
            cur.execute(adapt("INSERT INTO roles(nombre,descripcion,permisos,es_sistema)VALUES(?,?,?,?)", db), r)

    # Ensure roles (idempotent)
    roles_ensure = [
        ('Recepción', 'Kiosco, admisiones y facturación', ['kiosco', 'admisiones', 'facturacion']),
        ('Admisión', 'Admisiones y validación de derechos', ['admisiones', 'kiosco']),
        ('Médico', 'Historia clínica y agendamiento', ['historia_clinica', 'agendamiento']),
        ('Enfermería', 'Historia clínica - enfermería', ['historia_clinica', 'enfermeria']),
        ('Facturación', 'Facturación y reportes', ['facturacion', 'reportes']),
    ]
    for nombre, desc, perms in roles_ensure:
        try:
            ex = row(cur, adapt("SELECT id FROM roles WHERE nombre=?", db), (nombre,))
            if not ex:
                cur.execute(adapt("INSERT INTO roles(nombre,descripcion,permisos,es_sistema)VALUES(?,?,?,0)", db),
                            (nombre, desc, json.dumps(perms)))
                conn.commit()
        except Exception:
            conn.rollback()

    # Admin user
    cur.execute("SELECT COUNT(*) FROM usuarios")
    if cur.fetchone()[0] == 0:
        admin_pw = os.getenv('ADMIN_PASSWORD') or secrets.token_urlsafe(12)
        rol = row(cur, adapt("SELECT id FROM roles WHERE nombre=?", db), ('Superadmin',))
        cur.execute(
            adapt("INSERT INTO usuarios(usuario,nombre,email,pass_hash,rol_id,rol_nombre,activo)VALUES(?,?,?,?,?,?,1)", db),
            ('admin', 'Administrador', 'admin@arthemishealth.co',
             hash_pass(admin_pw), rol['id'] if rol else None, 'Superadmin'))

    conn.commit()
    cur.close()
    _return_db(conn, db)
