"""
app.py — Orquestador principal de Arthemis Health (ArthemisCOAL).

Responsabilidades:
  - Crear la app Flask y configurar seguridad (headers, CORS, CSRF)
  - Registrar blueprints de cada módulo
  - init_db() con esquema base
  - Endpoints globales: /health, /api/config, /api/auth/*, SSE /api/sse
  - Rutas estáticas
"""

import os, json, secrets, queue
from datetime import timedelta, datetime
from flask import Flask, jsonify, request, send_from_directory, session, Response, stream_with_context
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

import core

app = Flask(__name__, static_folder='static')
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(hours=12)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.getenv('FLASK_ENV') == 'production',
    SESSION_COOKIE_SAMESITE='Lax',
)

# ── SECURITY HEADERS ─────────────────────────────────────────────────────────

@app.after_request
def _security_headers(resp):
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    resp.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    if os.getenv('FLASK_ENV') == 'production':
        resp.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return resp

# ── CORS ──────────────────────────────────────────────────────────────────────

_default_origins = 'http://localhost:5050,http://127.0.0.1:5050'
ALLOWED_ORIGINS = [o.strip() for o in os.getenv('CORS_ORIGINS', _default_origins).split(',') if o.strip()]
CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

# ── CSRF ──────────────────────────────────────────────────────────────────────

_CSRF_EXEMPT_PREFIXES = ('/api/pagos/wompi/webhook', '/api/kiosco/', '/api/firmas/')

@app.before_request
def _csrf_check():
    if request.method not in ('POST', 'PUT', 'DELETE'):
        return None
    for prefix in _CSRF_EXEMPT_PREFIXES:
        if request.path.startswith(prefix):
            return None
    origin = request.headers.get('Origin', '')
    if not origin:
        return None
    # Allow same-origin requests (covers Railway, Render, etc.)
    request_origin = f"{request.scheme}://{request.host}"
    if origin == request_origin:
        return None
    if origin not in ALLOWED_ORIGINS:
        return jsonify({'error': 'Origen no permitido'}), 403
    return None

# ── DATABASE INIT ─────────────────────────────────────────────────────────────

def init_db():
    conn, db = core.get_db()
    cur = conn.cursor()
    S = 'SERIAL PRIMARY KEY' if db == 'pg' else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    T = 'TIMESTAMP' if db == 'pg' else 'TEXT'
    D = 'DEFAULT NOW()' if db == 'pg' else "DEFAULT (datetime('now'))"

    tables = f"""
CREATE TABLE IF NOT EXISTS pacientes(id {S},tipo_doc TEXT DEFAULT 'CC',num_doc TEXT UNIQUE NOT NULL,nombres TEXT NOT NULL,apellidos TEXT NOT NULL,fecha_nacimiento TEXT,genero TEXT,telefono TEXT,celular TEXT,email TEXT,direccion TEXT,ciudad TEXT DEFAULT 'Bogotá',eps TEXT,tipo_afiliado TEXT DEFAULT 'Contributivo',estado TEXT DEFAULT 'activo',creado_en {T} {D},actualizado_en {T} {D});
CREATE TABLE IF NOT EXISTS medicos(id {S},nombres TEXT,especialidad TEXT,modulo TEXT,activo INTEGER DEFAULT 1,color TEXT DEFAULT '#5147C4');
CREATE TABLE IF NOT EXISTS admisiones(id {S},id_adm TEXT UNIQUE NOT NULL,paciente_id INTEGER,fecha_entrada {T} {D},fecha_llamado {T},fecha_admision_inicio {T},fecha_admision_fin {T},fecha_salida {T},estado TEXT DEFAULT 'kiosco',tipo_atencion TEXT,turno TEXT,turno_tipo TEXT DEFAULT 'general',modulo TEXT,tiempo_espera_min INTEGER DEFAULT 0,notif_ticket INTEGER DEFAULT 0,notif_wa INTEGER DEFAULT 0,notif_sms INTEGER DEFAULT 0,celular_notif TEXT,medico_id INTEGER,sede TEXT DEFAULT 'Principal',servicio_nombre TEXT,cod_cups TEXT,copago REAL DEFAULT 0,copago_cobrado INTEGER DEFAULT 0,numero_autorizacion TEXT,eps_validada INTEGER DEFAULT 0,eps_estado TEXT,eps_copago_real REAL,habeas_data INTEGER DEFAULT 0,habeas_data_ts {T},color_alerta TEXT DEFAULT 'yellow',origen TEXT DEFAULT 'kiosco',doc_num_temp TEXT,doc_type_temp TEXT,nombre_temp TEXT,triage_nivel TEXT,triage_notas TEXT,triage_ts TEXT,triage_enfermera TEXT,destino TEXT,llamado_count INTEGER DEFAULT 0,creado_en {T} {D});
CREATE TABLE IF NOT EXISTS citas(id {S},paciente_id INTEGER NOT NULL,medico_id INTEGER,fecha TEXT NOT NULL,hora_inicio TEXT NOT NULL,hora_fin TEXT,servicio_nombre TEXT,cod_cups TEXT,tipo_cita TEXT DEFAULT 'consulta',estado TEXT DEFAULT 'programada',notas TEXT,celular_notif TEXT,recordatorio_enviado INTEGER DEFAULT 0,creado_en {T} {D},actualizado_en {T} {D});
CREATE TABLE IF NOT EXISTS consentimientos_catalogo(id {S},codigo TEXT UNIQUE,titulo TEXT,texto TEXT,requerido INTEGER DEFAULT 1,orden INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS firmas_consentimiento(id {S},admision_id INTEGER,consentimiento_id INTEGER,firma_svg TEXT,firma_hash TEXT,firmado_en {T},canal TEXT DEFAULT 'tablet',token TEXT UNIQUE,token_expira {T},estado TEXT DEFAULT 'pendiente');
CREATE TABLE IF NOT EXISTS audit_trail(id {S},entidad TEXT,entidad_id TEXT,accion TEXT,detalle TEXT,usuario TEXT DEFAULT 'sistema',ts TEXT,ip TEXT);
CREATE TABLE IF NOT EXISTS notificaciones(id {S},tipo TEXT,destinatario TEXT,mensaje TEXT,admision_id INTEGER,leida INTEGER DEFAULT 0,creado_en {T} {D});
CREATE TABLE IF NOT EXISTS modulos_config(id {S},tenant_id TEXT DEFAULT 'default',modulo TEXT NOT NULL,activo INTEGER DEFAULT 1,configuracion TEXT DEFAULT '{{}}');
CREATE TABLE IF NOT EXISTS tenant_config(id {S},tenant_id TEXT UNIQUE DEFAULT 'default',nombre_clinica TEXT DEFAULT 'Arthemis Health',nit TEXT DEFAULT '',email_habeas_data TEXT DEFAULT 'privacidad@arthemishealth.co',telefono TEXT DEFAULT '',direccion TEXT DEFAULT '',ciudad TEXT DEFAULT 'Bogotá',logo_url TEXT,color_primario TEXT DEFAULT '#5147C4',color_secundario TEXT DEFAULT '#7269D8');
CREATE TABLE IF NOT EXISTS roles(id {S},nombre TEXT UNIQUE NOT NULL,descripcion TEXT,permisos TEXT DEFAULT '[]',es_sistema INTEGER DEFAULT 0,creado_en {T} {D});
CREATE TABLE IF NOT EXISTS usuarios(id {S},usuario TEXT UNIQUE NOT NULL,nombre TEXT,email TEXT,pass_hash TEXT,rol_id INTEGER,rol_nombre TEXT,activo INTEGER DEFAULT 1,ultimo_acceso {T},creado_en {T} {D});
CREATE TABLE IF NOT EXISTS copago_param(id {S},anio INTEGER,concepto TEXT,rango TEXT,pct REAL DEFAULT 0,valor REAL DEFAULT 0,tope_evento REAL DEFAULT 0,tope_anio REAL DEFAULT 0,fuente TEXT,activo INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS kiosco_anuncios(id {S},titulo TEXT NOT NULL,descripcion TEXT,media_type TEXT DEFAULT 'none',media_url TEXT,activo INTEGER DEFAULT 1,orden INTEGER DEFAULT 0,creado_en {T} {D});
CREATE TABLE IF NOT EXISTS kiosco_servicios(id {S},codigo TEXT UNIQUE,nombre TEXT NOT NULL,icono TEXT DEFAULT '●',activo INTEGER DEFAULT 1,orden INTEGER DEFAULT 0,modo TEXT DEFAULT 'general')
"""
    for s in tables.strip().split(';'):
        s = s.strip()
        if s:
            try:
                cur.execute(s)
                conn.commit()
            except Exception:
                conn.rollback()

    # Idempotent migrations
    for mig in [
        "ALTER TABLE admisiones ADD COLUMN nombre_temp TEXT",
        "ALTER TABLE admisiones ADD COLUMN fecha_llamado TEXT",
        "ALTER TABLE kiosco_servicios ADD COLUMN modo TEXT DEFAULT 'general'",
        "ALTER TABLE admisiones ADD COLUMN triage_nivel TEXT",
        "ALTER TABLE admisiones ADD COLUMN triage_notas TEXT",
        "ALTER TABLE admisiones ADD COLUMN triage_ts TEXT",
        "ALTER TABLE admisiones ADD COLUMN triage_enfermera TEXT",
        "ALTER TABLE admisiones ADD COLUMN destino TEXT",
        "ALTER TABLE admisiones ADD COLUMN llamado_count INTEGER DEFAULT 0",
    ]:
        try:
            cur.execute(mig)
            conn.commit()
        except Exception:
            conn.rollback()

    # Seeds
    cur.execute("SELECT COUNT(*) FROM medicos")
    if cur.fetchone()[0] == 0:
        for m in [
            ('Dra. Andrea Martínez', 'Optometría', 'Módulo 1'),
            ('Dr. Felipe Rincón', 'Oftalmología', 'Módulo 2'),
            ('Dra. Claudia Herrera', 'Optometría', 'Módulo 3'),
            ('Dr. Sergio Montoya', 'Baja Visión', 'Módulo 4'),
        ]:
            cur.execute(core.adapt("INSERT INTO medicos(nombres,especialidad,modulo)VALUES(?,?,?)", db), m)

    cur.execute("SELECT COUNT(*) FROM consentimientos_catalogo")
    if cur.fetchone()[0] == 0:
        for c in [
            ('habeas_data', 'Autorización Habeas Data',
             'Autorizo a {CLINICA} para tratar mis datos personales conforme a la Ley 1581 de 2012.', 1, 1),
            ('tratamiento_medico', 'Consentimiento Informado de Atención',
             'Autorizo al equipo médico de {CLINICA} para realizar el examen y tratamiento necesario.', 1, 2),
        ]:
            cur.execute(core.adapt("INSERT INTO consentimientos_catalogo(codigo,titulo,texto,requerido,orden)VALUES(?,?,?,?,?)", db), c)

    cur.execute("SELECT COUNT(*) FROM pacientes")
    if cur.fetchone()[0] == 0:
        for p in [
            ('CC', '1023456789', 'Juan Carlos', 'Salcedo Gómez', '1982-03-15', 'M', '3101234567', 'Sanitas', 'Contributivo'),
            ('CC', '52789012', 'María Fernanda', 'López Ruiz', '1990-07-22', 'F', '3209876543', 'Sura', 'Contributivo'),
            ('CC', '79456123', 'Carlos Andrés', 'Torres Medina', '1975-11-08', 'M', '3004567891', 'Nueva EPS', 'Contributivo'),
        ]:
            cur.execute(core.adapt("INSERT INTO pacientes(tipo_doc,num_doc,nombres,apellidos,fecha_nacimiento,genero,celular,eps,tipo_afiliado)VALUES(?,?,?,?,?,?,?,?,?)", db), p)

    cur.execute("SELECT COUNT(*) FROM modulos_config")
    if cur.fetchone()[0] == 0:
        for m in [('kiosco', 1), ('admisiones', 1), ('historia_clinica', 0), ('facturacion', 0)]:
            cur.execute(core.adapt("INSERT INTO modulos_config(tenant_id,modulo,activo)VALUES('default',?,?)", db), m)

    cur.execute("SELECT COUNT(*) FROM tenant_config")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO tenant_config(tenant_id,nombre_clinica)VALUES('default','Centro Ocular Dr. Rincón')")

    # Kiosco servicios seed
    cur.execute("SELECT COUNT(*) FROM kiosco_servicios")
    if cur.fetchone()[0] == 0:
        for s in [
            ('oft', 'Oftalmología', '👁️', 1, 0, 'consulta'),
            ('opt', 'Optometría', '👓', 1, 1, 'consulta'),
            ('ort', 'Ortóptica', '🔬', 1, 2, 'consulta'),
            ('cir', 'Cirugía', '🏥', 1, 3, 'cirugia'),
            ('lab', 'Laboratorio', '🧪', 1, 4, 'diagnostico'),
            ('img', 'Imágenes diagnósticas', '📷', 1, 5, 'diagnostico'),
            ('urg', 'Urgencias', '🚨', 1, 6, 'urgencias'),
        ]:
            cur.execute(core.adapt("INSERT INTO kiosco_servicios(codigo,nombre,icono,activo,orden,modo)VALUES(?,?,?,?,?,?)", db), s)

    # Kiosco anuncios seed
    cur.execute("SELECT COUNT(*) FROM kiosco_anuncios")
    if cur.fetchone()[0] == 0:
        for a in [
            ('Cirugía láser', 'Corrección visual con tecnología de última generación', 'none', '', 1, 0),
            ('Lentes de contacto', 'Adaptación personalizada con los mejores materiales', 'none', '', 1, 1),
        ]:
            cur.execute(core.adapt("INSERT INTO kiosco_anuncios(titulo,descripcion,media_type,media_url,activo,orden)VALUES(?,?,?,?,?,?)", db), a)

    # Copago params (Circular 048 de 2025)
    try:
        cur.execute("SELECT COUNT(*) FROM copago_param")
        if cur.fetchone()[0] == 0:
            F = 'Circular 048 de 2025'
            for s in [
                (2026, 'cuota_moderadora', 'menor_2', 0, 5000, 0, 0, F),
                (2026, 'cuota_moderadora', 'entre_2_5', 0, 20100, 0, 0, F),
                (2026, 'cuota_moderadora', 'mayor_5', 0, 52800, 0, 0, F),
                (2026, 'copago_contributivo', 'menor_2', 0.115, 0, 373715, 748882, F),
                (2026, 'copago_contributivo', 'entre_2_5', 0.173, 0, 1497644, 2995409, F),
                (2026, 'copago_contributivo', 'mayor_5', 0.230, 0, 2995409, 5990696, F),
                (2026, 'copago_subsidiado', 'general', 0.10, 0, 651155, 1302309, F),
            ]:
                cur.execute(core.adapt("INSERT INTO copago_param(anio,concepto,rango,pct,valor,tope_evento,tope_anio,fuente)VALUES(?,?,?,?,?,?,?,?)", db), s)
            conn.commit()
    except Exception:
        conn.rollback()

    conn.commit()
    cur.close()
    core._return_db(conn, db)
    print(f"✅ DB ({'PG' if core.USE_PG else 'SQLite'})")

# ── AUTH ENDPOINTS ────────────────────────────────────────────────────────────

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    d = request.json or {}
    usuario = d.get('usuario', '').strip()
    pw = d.get('password', '')
    if not usuario or not pw:
        return jsonify({'error': 'Usuario y contraseña requeridos'}), 400

    client_ip = request.remote_addr or '0.0.0.0'
    allowed, wait_seconds = core._check_rate_limit(client_ip)
    if not allowed:
        return jsonify({'error': f'Demasiados intentos fallidos. Intente de nuevo en {wait_seconds} segundos.'}), 429

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        u = core.row(cur, core.adapt("SELECT * FROM usuarios WHERE usuario=? AND activo=1", db), (usuario,))
        ok, upgrade = core.verify_pass(pw, u['pass_hash']) if u else (False, False)
        if not u or not ok:
            core._record_failed_login(client_ip)
            cur.close()
            core._return_db(conn, db)
            return jsonify({'error': 'Usuario o contraseña incorrectos'}), 401

        core._clear_login_attempts(client_ip)
        if upgrade:
            try:
                cur.execute(core.adapt("UPDATE usuarios SET pass_hash=? WHERE id=?", db),
                            (core.hash_pass(pw), u['id']))
            except Exception:
                pass

        cur.execute(core.adapt(f"UPDATE usuarios SET ultimo_acceso={core.NOW(db)} WHERE id=?", db), (u['id'],))
        conn.commit()

        session.clear()
        session.permanent = True
        session['user_id'] = u['id']
        session['usuario'] = u['usuario']
        session['rol'] = u['rol_nombre']
        permisos = core.get_user_permisos()
        session['_cached_permisos'] = permisos

        cur.close()
        core._return_db(conn, db)
        return jsonify({
            'success': True, 'usuario': u['usuario'],
            'nombre': u['nombre'], 'rol': u['rol_nombre'], 'permisos': permisos,
        })
    except Exception:
        cur.close()
        core._return_db(conn, db)
        raise

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/auth/me')
def auth_me():
    if not session.get('user_id'):
        return jsonify({'autenticado': False}), 200
    return jsonify({
        'autenticado': True, 'usuario': session.get('usuario'),
        'rol': session.get('rol'), 'permisos': core.get_user_permisos(),
    })

# ── SSE ───────────────────────────────────────────────────────────────────────

@app.route('/api/sse')
def sse_stream():
    q = queue.Queue()
    with core._sse_lock:
        core._sse_clients.append(q)

    def gen():
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except Exception:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            with core._sse_lock:
                try:
                    core._sse_clients.remove(q)
                except ValueError:
                    pass

    return Response(stream_with_context(gen()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

# ── CONFIG ────────────────────────────────────────────────────────────────────

@app.route('/api/config')
def get_config():
    conn, db = core.get_db()
    cur = conn.cursor()
    cfg = core.row(cur, "SELECT * FROM tenant_config WHERE tenant_id='default'") or {}
    mods = core.rows(cur, "SELECT modulo,activo FROM modulos_config WHERE tenant_id='default'")
    cur.close()
    core._return_db(conn, db)
    cfg['modulos'] = {m['modulo']: bool(m['activo']) for m in mods}
    return jsonify(cfg)

# ── PACIENTES (lookup público para kiosco + CRUD autenticado) ─────────────

@app.route('/api/pacientes/<num_doc>')
def get_paciente_by_doc(num_doc):
    conn, db = core.get_db()
    cur = conn.cursor()
    p = core.row(cur, core.adapt("SELECT * FROM pacientes WHERE num_doc=?", db), (num_doc,))
    if not p:
        cur.close()
        core._return_db(conn, db)
        return jsonify({'error': 'No encontrado'}), 404

    # Check for today's appointments
    hoy = datetime.now().strftime('%Y-%m-%d')
    citas = core.rows(cur, core.adapt(
        "SELECT c.*, m.nombres as medico_nombre FROM citas c "
        "LEFT JOIN medicos m ON c.medico_id=m.id "
        "WHERE c.paciente_id=? AND c.fecha=? AND c.estado='programada'", db),
        (p['id'], hoy))
    p['citas_futuras'] = citas

    cur.close()
    core._return_db(conn, db)
    return jsonify(p)

@app.route('/api/pacientes', methods=['POST'])
@core.login_required
def create_paciente():
    d = request.json or {}
    required = ['num_doc', 'nombres', 'apellidos']
    if not all(d.get(k) for k in required):
        return jsonify({'error': 'Campos requeridos: num_doc, nombres, apellidos'}), 400
    conn, db = core.get_db()
    cur = conn.cursor()
    existing = core.row(cur, core.adapt("SELECT id FROM pacientes WHERE num_doc=?", db), (d['num_doc'],))
    if existing:
        cur.close()
        core._return_db(conn, db)
        return jsonify({'error': 'Ya existe un paciente con ese documento', 'id': existing['id']}), 409
    cur.execute(core.adapt(
        "INSERT INTO pacientes(tipo_doc,num_doc,nombres,apellidos,fecha_nacimiento,genero,celular,eps,tipo_afiliado)"
        "VALUES(?,?,?,?,?,?,?,?,?)", db),
        (d.get('tipo_doc', 'CC'), d['num_doc'], d['nombres'], d['apellidos'],
         d.get('fecha_nacimiento'), d.get('genero'), d.get('celular'),
         d.get('eps'), d.get('tipo_afiliado', 'Contributivo')))
    conn.commit()
    nuevo = core.row(cur, core.adapt("SELECT * FROM pacientes WHERE num_doc=?", db), (d['num_doc'],))
    core.audit(cur, db, 'pacientes', nuevo['id'], 'crear', f"Paciente {d['nombres']} {d['apellidos']}")
    conn.commit()
    cur.close()
    core._return_db(conn, db)
    return jsonify(nuevo), 201

# ── STATIC ROUTES ─────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/kiosco')
def kiosco_page():
    return send_from_directory('static', 'kiosco.html')

@app.route('/kiosco/tv')
def kiosco_tv_page():
    return send_from_directory('static', 'kiosco-tv.html')

@app.route('/kiosco/admin')
def kiosco_admin_page():
    return send_from_directory('static', 'kiosco-admin.html')

@app.route('/kiosco/<modo>')
def kiosco_modo_page(modo):
    return send_from_directory('static', 'kiosco.html')

@app.route('/kiosco/tv/<modo>')
def kiosco_tv_modo_page(modo):
    return send_from_directory('static', 'kiosco-tv.html')

@app.route('/health')
def health():
    return jsonify({
        'status': 'ok',
        'db': 'pg' if core.USE_PG else 'sqlite',
        'ts': datetime.now().isoformat(),
    })

# ── BLUEPRINT REGISTRATION ───────────────────────────────────────────────────

try:
    from kiosco_engine import kiosco_bp
    app.register_blueprint(kiosco_bp)
    print("🏥 Módulo Kiosco registrado en /api/kiosco")
except Exception as e:
    print(f"⚠ kiosco_engine no disponible: {e}")

# ── INIT ──────────────────────────────────────────────────────────────────────

init_db()
core.seed_auth()

if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.getenv('PORT', 5050)),
        debug=os.getenv('FLASK_ENV') != 'production',
        threaded=True,
    )
