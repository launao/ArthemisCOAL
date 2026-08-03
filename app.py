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
# Fixed secret key — random fallback causes session loss on restart
app.secret_key = os.getenv('SECRET_KEY', 'arthemis-dev-key-change-in-production-2026')
app.permanent_session_lifetime = timedelta(hours=12)
_is_https = os.getenv('RAILWAY_PUBLIC_DOMAIN') or os.getenv('FLASK_ENV') == 'production'
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=bool(_is_https),
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
_railway_domain = os.getenv('RAILWAY_PUBLIC_DOMAIN', '')
if _railway_domain:
    _default_origins += f',https://{_railway_domain}'
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
    # Behind reverse proxy, request.scheme may be 'http' while Origin is 'https'
    scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
    request_origin = f"{scheme}://{request.host}"
    if origin == request_origin:
        return None
    # Also check without scheme mismatch (http vs https same host)
    origin_host = origin.split('://', 1)[-1] if '://' in origin else origin
    if origin_host == request.host:
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
CREATE TABLE IF NOT EXISTS medicos(id {S},nombres TEXT,especialidad TEXT,modulo TEXT,activo INTEGER DEFAULT 1,color TEXT DEFAULT '#2E5D7A');
CREATE TABLE IF NOT EXISTS admisiones(id {S},id_adm TEXT UNIQUE NOT NULL,paciente_id INTEGER,fecha_entrada {T} {D},fecha_llamado {T},fecha_admision_inicio {T},fecha_admision_fin {T},fecha_salida {T},estado TEXT DEFAULT 'kiosco',tipo_atencion TEXT,turno TEXT,turno_tipo TEXT DEFAULT 'general',modulo TEXT,tiempo_espera_min INTEGER DEFAULT 0,notif_ticket INTEGER DEFAULT 0,notif_wa INTEGER DEFAULT 0,notif_sms INTEGER DEFAULT 0,celular_notif TEXT,medico_id INTEGER,sede TEXT DEFAULT 'Principal',servicio_nombre TEXT,cod_cups TEXT,copago REAL DEFAULT 0,copago_cobrado INTEGER DEFAULT 0,numero_autorizacion TEXT,eps_validada INTEGER DEFAULT 0,eps_estado TEXT,eps_copago_real REAL,habeas_data INTEGER DEFAULT 0,habeas_data_ts {T},color_alerta TEXT DEFAULT 'yellow',origen TEXT DEFAULT 'kiosco',doc_num_temp TEXT,doc_type_temp TEXT,nombre_temp TEXT,triage_nivel TEXT,triage_notas TEXT,triage_ts TEXT,triage_enfermera TEXT,destino TEXT,llamado_count INTEGER DEFAULT 0,creado_en {T} {D});
CREATE TABLE IF NOT EXISTS citas(id {S},paciente_id INTEGER NOT NULL,medico_id INTEGER,fecha TEXT NOT NULL,hora_inicio TEXT NOT NULL,hora_fin TEXT,servicio_nombre TEXT,cod_cups TEXT,tipo_cita TEXT DEFAULT 'consulta',estado TEXT DEFAULT 'programada',notas TEXT,celular_notif TEXT,recordatorio_enviado INTEGER DEFAULT 0,creado_en {T} {D},actualizado_en {T} {D});
CREATE TABLE IF NOT EXISTS consentimientos_catalogo(id {S},codigo TEXT UNIQUE,titulo TEXT,texto TEXT,requerido INTEGER DEFAULT 1,orden INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS firmas_consentimiento(id {S},admision_id INTEGER,consentimiento_id INTEGER,firma_svg TEXT,firma_hash TEXT,firmado_en {T},canal TEXT DEFAULT 'tablet',token TEXT UNIQUE,token_expira {T},estado TEXT DEFAULT 'pendiente');
CREATE TABLE IF NOT EXISTS audit_trail(id {S},entidad TEXT,entidad_id TEXT,accion TEXT,detalle TEXT,usuario TEXT DEFAULT 'sistema',ts TEXT,ip TEXT);
CREATE TABLE IF NOT EXISTS notificaciones(id {S},tipo TEXT,destinatario TEXT,mensaje TEXT,admision_id INTEGER,leida INTEGER DEFAULT 0,creado_en {T} {D});
CREATE TABLE IF NOT EXISTS modulos_config(id {S},tenant_id TEXT DEFAULT 'default',modulo TEXT NOT NULL,activo INTEGER DEFAULT 1,configuracion TEXT DEFAULT '{{}}');
CREATE TABLE IF NOT EXISTS tenant_config(id {S},tenant_id TEXT UNIQUE DEFAULT 'default',nombre_clinica TEXT DEFAULT 'Arthemis Health',nit TEXT DEFAULT '',email_habeas_data TEXT DEFAULT 'privacidad@arthemishealth.co',telefono TEXT DEFAULT '',direccion TEXT DEFAULT '',ciudad TEXT DEFAULT 'Bogotá',logo_url TEXT,color_primario TEXT DEFAULT '#2E5D7A',color_secundario TEXT DEFAULT '#3A7A9B');
CREATE TABLE IF NOT EXISTS roles(id {S},nombre TEXT UNIQUE NOT NULL,descripcion TEXT,permisos TEXT DEFAULT '[]',es_sistema INTEGER DEFAULT 0,creado_en {T} {D});
CREATE TABLE IF NOT EXISTS usuarios(id {S},usuario TEXT UNIQUE NOT NULL,nombre TEXT,email TEXT,pass_hash TEXT,rol_id INTEGER,rol_nombre TEXT,activo INTEGER DEFAULT 1,ultimo_acceso {T},creado_en {T} {D});
CREATE TABLE IF NOT EXISTS copago_param(id {S},anio INTEGER,concepto TEXT,rango TEXT,pct REAL DEFAULT 0,valor REAL DEFAULT 0,tope_evento REAL DEFAULT 0,tope_anio REAL DEFAULT 0,fuente TEXT,activo INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS kiosco_anuncios(id {S},titulo TEXT NOT NULL,descripcion TEXT,media_type TEXT DEFAULT 'none',media_url TEXT,activo INTEGER DEFAULT 1,orden INTEGER DEFAULT 0,creado_en {T} {D});
CREATE TABLE IF NOT EXISTS kiosco_servicios(id {S},codigo TEXT UNIQUE,nombre TEXT NOT NULL,icono TEXT DEFAULT '●',activo INTEGER DEFAULT 1,orden INTEGER DEFAULT 0,modo TEXT DEFAULT 'general');
CREATE TABLE IF NOT EXISTS puestos_atencion(id {S},codigo TEXT UNIQUE NOT NULL,nombre TEXT NOT NULL,tipo TEXT NOT NULL,activo INTEGER DEFAULT 1,modo TEXT DEFAULT 'urgencias',creado_en {T} {D});
CREATE TABLE IF NOT EXISTS admision_timeline(id {S},admision_id INTEGER NOT NULL,evento TEXT NOT NULL,detalle TEXT,usuario TEXT,puesto TEXT,ts {T} {D});
CREATE TABLE IF NOT EXISTS historia_clinica_urgencias(id {S},admision_id INTEGER UNIQUE NOT NULL,paciente_id INTEGER NOT NULL,motivo_consulta TEXT,enfermedad_actual TEXT,antecedentes TEXT,examen_fisico TEXT,signos_vitales TEXT,diagnostico_ingreso TEXT,cod_cie10_ingreso TEXT,diagnostico_egreso TEXT,cod_cie10_egreso TEXT,diagnosticos_relacionados TEXT,conducta TEXT,tratamiento TEXT,observaciones TEXT,condicion_salida TEXT,destino_salida TEXT,medico_id INTEGER,medico_nombre TEXT,creado_por TEXT,creado_en {T} {D},actualizado_en {T} {D});
CREATE TABLE IF NOT EXISTS pagador_validacion(id {S},admision_id INTEGER NOT NULL,tipo_pagador TEXT NOT NULL,entidad_nombre TEXT,entidad_codigo TEXT,regimen TEXT,estado_afiliacion TEXT,fecha_afiliacion TEXT,nivel_sisben TEXT,grupo_ingreso TEXT,numero_poliza TEXT,numero_autorizacion TEXT,placa_vehiculo TEXT,fecha_accidente TEXT,empresa_nombre TEXT,nit_empresa TEXT,copago_aplica INTEGER DEFAULT 0,copago_valor REAL DEFAULT 0,copago_excento INTEGER DEFAULT 0,copago_motivo_exencion TEXT,validado INTEGER DEFAULT 0,validado_por TEXT,validado_en {T},datos_json TEXT DEFAULT '{{}}',creado_en {T} {D});
CREATE TABLE IF NOT EXISTS triage_clinico(id {S},admision_id INTEGER UNIQUE NOT NULL,motivo_consulta TEXT,ta_sistolica INTEGER,ta_diastolica INTEGER,fc INTEGER,fr INTEGER,temperatura REAL,spo2 INTEGER,glucometria INTEGER,glasgow_ocular INTEGER DEFAULT 4,glasgow_verbal INTEGER DEFAULT 5,glasgow_motor INTEGER DEFAULT 6,glasgow_total INTEGER DEFAULT 15,eva_dolor INTEGER DEFAULT 0,dolor_localizacion TEXT,disc_via_aerea INTEGER DEFAULT 0,disc_sangrado INTEGER DEFAULT 0,disc_dolor_toracico INTEGER DEFAULT 0,disc_alt_neurologica INTEGER DEFAULT 0,disc_gestante INTEGER DEFAULT 0,disc_menor_edad INTEGER DEFAULT 0,disc_trauma_mayor INTEGER DEFAULT 0,disc_convulsiones INTEGER DEFAULT 0,disc_fiebre_alta INTEGER DEFAULT 0,disc_otros TEXT,alergias TEXT,nivel_asignado TEXT NOT NULL,nivel_sugerido TEXT,color_triage TEXT,notas_enfermeria TEXT,enfermera_id INTEGER,enfermera_nombre TEXT,hora_inicio_triage {T},hora_fin_triage {T},creado_en {T} {D});
CREATE TABLE IF NOT EXISTS triage_form_config(id {S},campo TEXT UNIQUE NOT NULL,etiqueta TEXT NOT NULL,grupo TEXT NOT NULL,tipo TEXT DEFAULT 'text',requerido INTEGER DEFAULT 0,visible INTEGER DEFAULT 1,orden INTEGER DEFAULT 0,opciones TEXT DEFAULT '[]',rango_min REAL,rango_max REAL,unidad TEXT,ayuda TEXT,modificado_por TEXT,modificado_en {T} {D});
CREATE TABLE IF NOT EXISTS hc_campos_config(id {S},seccion TEXT NOT NULL,campo TEXT UNIQUE NOT NULL,etiqueta TEXT NOT NULL,tipo TEXT DEFAULT 'textarea',requerido INTEGER DEFAULT 0,visible INTEGER DEFAULT 1,orden INTEGER DEFAULT 0,opciones TEXT DEFAULT '[]',ayuda TEXT,especialidad TEXT DEFAULT 'general',modificado_por TEXT,modificado_en {T} {D});
CREATE TABLE IF NOT EXISTS historia_clinica(id {S},admision_id INTEGER UNIQUE NOT NULL,paciente_id INTEGER NOT NULL,tipo_hc TEXT DEFAULT 'urgencias',estado TEXT DEFAULT 'abierta',motivo_consulta TEXT,causa_atencion TEXT DEFAULT 'urgencia',cod_cie10_ingreso TEXT,cod_cie10_egreso TEXT,diagnosticos_relacionados TEXT DEFAULT '[]',condicion_egreso TEXT,destino_egreso TEXT,medico_id INTEGER,medico_nombre TEXT,firma_medico TEXT,creado_por TEXT,creado_en {T} {D},cerrado_en {T},actualizado_en {T} {D});
CREATE TABLE IF NOT EXISTS hc_evoluciones(id {S},hc_id INTEGER NOT NULL,tipo TEXT DEFAULT 'evolucion',enfermedad_actual TEXT,antecedentes_json TEXT DEFAULT '{{}}',revision_sistemas TEXT DEFAULT '{{}}',examen_fisico TEXT,signos_vitales_json TEXT DEFAULT '{{}}',analisis TEXT,plan_terapeutico TEXT,cod_cie10 TEXT,campos_custom TEXT DEFAULT '{{}}',medico_id INTEGER,medico_nombre TEXT,creado_en {T} {D});
CREATE TABLE IF NOT EXISTS hc_antecedentes(id {S},paciente_id INTEGER NOT NULL,tipo TEXT NOT NULL,descripcion TEXT,fecha TEXT,activo INTEGER DEFAULT 1,registrado_por TEXT,creado_en {T} {D});
CREATE TABLE IF NOT EXISTS interconsultas(id {S},hc_id INTEGER NOT NULL,admision_id INTEGER NOT NULL,paciente_id INTEGER NOT NULL,tipo TEXT DEFAULT 'interna',especialidad_solicitada TEXT,cod_cups TEXT,motivo TEXT,diagnostico_presuntivo TEXT,cod_cie10 TEXT,prioridad TEXT DEFAULT 'urgente',estado TEXT DEFAULT 'solicitada',medico_solicitante_id INTEGER,medico_solicitante TEXT,medico_interconsultante_id INTEGER,medico_interconsultante TEXT,respuesta TEXT,recomendaciones TEXT,cod_cie10_respuesta TEXT,fecha_solicitud {T} {D},fecha_aceptacion {T},fecha_respuesta {T},creado_en {T} {D});
CREATE TABLE IF NOT EXISTS ordenes_medicas(id {S},hc_id INTEGER NOT NULL,admision_id INTEGER NOT NULL,paciente_id INTEGER NOT NULL,tipo_orden TEXT NOT NULL,cod_cups TEXT,nombre_estudio TEXT NOT NULL,cantidad INTEGER DEFAULT 1,prioridad TEXT DEFAULT 'rutina',indicacion_clinica TEXT,diagnostico_asociado TEXT,instrucciones TEXT,estado TEXT DEFAULT 'solicitada',servicio_destino TEXT,medico_ordena_id INTEGER,medico_ordena TEXT,numero_autorizacion TEXT,creado_en {T} {D},actualizado_en {T} {D});
CREATE TABLE IF NOT EXISTS orden_resultados(id {S},orden_id INTEGER NOT NULL,tipo_resultado TEXT DEFAULT 'texto',parametro TEXT,valor TEXT,unidad TEXT,rango_referencia TEXT,fuera_rango INTEGER DEFAULT 0,observaciones TEXT,archivo_url TEXT,archivo_tipo TEXT,procesado_por TEXT,validado_por TEXT,creado_en {T} {D});
CREATE TABLE IF NOT EXISTS prescripciones(id {S},hc_id INTEGER NOT NULL,admision_id INTEGER NOT NULL,paciente_id INTEGER NOT NULL,medicamento TEXT NOT NULL,cod_cum TEXT,concentracion TEXT,forma_farmaceutica TEXT,via_administracion TEXT DEFAULT 'oral',dosis TEXT,frecuencia TEXT,duracion TEXT,cantidad_total INTEGER DEFAULT 1,instrucciones TEXT,diagnostico_asociado TEXT,requiere_mipres INTEGER DEFAULT 0,id_mipres TEXT,estado TEXT DEFAULT 'prescrita',medico_id INTEGER,medico_nombre TEXT,creado_en {T} {D});
CREATE TABLE IF NOT EXISTS pre_factura(id {S},admision_id INTEGER UNIQUE,paciente_id INTEGER NOT NULL,estado TEXT DEFAULT 'borrador',tipo_pagador TEXT,entidad_pagadora TEXT,entidad_codigo TEXT,numero_contrato TEXT,numero_autorizacion TEXT,subtotal REAL DEFAULT 0,copago REAL DEFAULT 0,cuota_moderadora REAL DEFAULT 0,descuento REAL DEFAULT 0,total REAL DEFAULT 0,total_paciente REAL DEFAULT 0,total_pagador REAL DEFAULT 0,observaciones TEXT,generado_en {T} {D},revisado_por TEXT,aprobado_en {T});
CREATE TABLE IF NOT EXISTS pre_factura_items(id {S},pre_factura_id INTEGER NOT NULL,tipo_servicio TEXT NOT NULL,cod_cups TEXT,descripcion TEXT NOT NULL,cantidad INTEGER DEFAULT 1,valor_unitario REAL DEFAULT 0,valor_total REAL DEFAULT 0,tarifa_referencia TEXT,porcentaje_negociado REAL DEFAULT 100,numero_autorizacion TEXT,orden_id INTEGER,prescripcion_id INTEGER,origen TEXT DEFAULT 'auto',creado_en {T} {D})
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
        "ALTER TABLE admisiones ADD COLUMN puesto_id INTEGER",
        # Admisiones urgencias extensions
        "ALTER TABLE admisiones ADD COLUMN tipo_pagador TEXT DEFAULT 'eps_contributivo'",
        "ALTER TABLE admisiones ADD COLUMN pagador_entidad TEXT",
        "ALTER TABLE admisiones ADD COLUMN pagador_regimen TEXT",
        "ALTER TABLE admisiones ADD COLUMN pagador_estado TEXT",
        "ALTER TABLE admisiones ADD COLUMN pagador_validado INTEGER DEFAULT 0",
        "ALTER TABLE admisiones ADD COLUMN copago_calculado REAL DEFAULT 0",
        "ALTER TABLE admisiones ADD COLUMN copago_excento INTEGER DEFAULT 0",
        "ALTER TABLE admisiones ADD COLUMN copago_motivo_exencion TEXT",
        "ALTER TABLE admisiones ADD COLUMN hc_abierta INTEGER DEFAULT 0",
        "ALTER TABLE admisiones ADD COLUMN hc_id INTEGER",
        "ALTER TABLE admisiones ADD COLUMN admision_completa INTEGER DEFAULT 0",
        "ALTER TABLE admisiones ADD COLUMN admisionista TEXT",
        "ALTER TABLE admisiones ADD COLUMN causa_atencion TEXT DEFAULT 'urgencia'",
        "ALTER TABLE admisiones ADD COLUMN cod_diagnostico_ingreso TEXT",
        "ALTER TABLE admisiones ADD COLUMN rips_json TEXT DEFAULT '{}'",
        # RIPS fields for admisiones
        "ALTER TABLE admisiones ADD COLUMN rips_via_ingreso TEXT DEFAULT '2'",
        "ALTER TABLE admisiones ADD COLUMN rips_causa_externa TEXT DEFAULT '13'",
        "ALTER TABLE admisiones ADD COLUMN rips_modalidad TEXT DEFAULT '3'",
        "ALTER TABLE admisiones ADD COLUMN rips_grupo_servicios TEXT DEFAULT '01'",
        "ALTER TABLE admisiones ADD COLUMN rips_finalidad TEXT DEFAULT '4'",
        # Doctor/consultorio fields
        "ALTER TABLE admisiones ADD COLUMN medico_nombre_atencion TEXT",
        "ALTER TABLE admisiones ADD COLUMN condicion_egreso TEXT",
        "ALTER TABLE admisiones ADD COLUMN destino_egreso TEXT",
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
            ('Dra. Andrea Martínez', 'Medicina General', 'Consultorio 3'),
            ('Dr. Carlos Méndez', 'Ortopedia', 'Consultorio 1'),
            ('Dra. Claudia Herrera', 'Medicina General', 'Consultorio 4'),
            ('Dr. Sergio Montoya', 'Ortopedia', 'Consultorio 2'),
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
        for m in [('kiosco', 1), ('admisiones', 1), ('historia_clinica', 1), ('interconsultas', 1), ('ordenes', 1), ('laboratorio', 1), ('facturacion', 1), ('impresion', 1)]:
            cur.execute(core.adapt("INSERT INTO modulos_config(tenant_id,modulo,activo)VALUES('default',?,?)", db), m)

    cur.execute("SELECT COUNT(*) FROM tenant_config")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO tenant_config(tenant_id,nombre_clinica)VALUES('default','COAL - Clínica de Ortopedia y Accidentes Laborales')")

    # Kiosco servicios seed
    cur.execute("SELECT COUNT(*) FROM kiosco_servicios")
    if cur.fetchone()[0] == 0:
        for s in [
            ('ort', 'Ortopedia', '🦴', 1, 0, 'consulta'),
            ('med', 'Medicina General', '🩺', 1, 1, 'consulta'),
            ('fis', 'Fisioterapia', '💪', 1, 2, 'consulta'),
            ('cir', 'Cirugía', '🏥', 1, 3, 'cirugia'),
            ('lab', 'Laboratorio', '🧪', 1, 4, 'diagnostico'),
            ('img', 'Imágenes diagnósticas', '📷', 1, 5, 'diagnostico'),
            ('urg', 'Urgencias', '🚨', 1, 6, 'urgencias'),
        ]:
            cur.execute(core.adapt("INSERT INTO kiosco_servicios(codigo,nombre,icono,activo,orden,modo)VALUES(?,?,?,?,?,?)", db), s)

    # Puestos de atención seed
    cur.execute("SELECT COUNT(*) FROM puestos_atencion")
    if cur.fetchone()[0] == 0:
        for pt in [
            ('T1', 'Triage 1', 'triage', 1, 'urgencias'),
            ('T2', 'Triage 2', 'triage', 1, 'urgencias'),
            ('A1', 'Admisiones 1', 'admisiones', 1, 'urgencias'),
            ('A2', 'Admisiones 2', 'admisiones', 1, 'urgencias'),
            ('C1', 'Consultorio 1', 'consultorio', 1, 'urgencias'),
            ('C2', 'Consultorio 2', 'consultorio', 1, 'urgencias'),
            ('C3', 'Consultorio 3', 'consultorio', 1, 'urgencias'),
            ('L1', 'Laboratorio 1', 'laboratorio', 1, 'urgencias'),
            ('I1', 'Imágenes 1', 'imagenes', 1, 'urgencias'),
            ('E1', 'Especialista 1', 'especialidad', 1, 'urgencias'),
        ]:
            cur.execute(core.adapt(
                "INSERT INTO puestos_atencion(codigo,nombre,tipo,activo,modo)VALUES(?,?,?,?,?)", db), pt)

    # Kiosco anuncios seed
    cur.execute("SELECT COUNT(*) FROM kiosco_anuncios")
    if cur.fetchone()[0] == 0:
        for a in [
            ('Rehabilitación integral', 'Programas de fisioterapia con tecnología de última generación', 'none', '', 1, 0),
            ('Atención de urgencias', 'Servicio especializado en accidentes laborales y trauma', 'none', '', 1, 1),
        ]:
            cur.execute(core.adapt("INSERT INTO kiosco_anuncios(titulo,descripcion,media_type,media_url,activo,orden)VALUES(?,?,?,?,?,?)", db), a)

    # Triage form config seed (superadmin-configurable)
    cur.execute("SELECT COUNT(*) FROM triage_form_config")
    if cur.fetchone()[0] == 0:
        triage_fields = [
            # ── Signos Vitales ──
            ('ta_sistolica','T/A Sistólica','signos_vitales','number',1,1,1,'[]',60,300,'mmHg','Presión arterial sistólica'),
            ('ta_diastolica','T/A Diastólica','signos_vitales','number',1,1,2,'[]',30,200,'mmHg','Presión arterial diastólica'),
            ('fc','Frecuencia Cardíaca','signos_vitales','number',1,1,3,'[]',20,250,'lpm','Latidos por minuto'),
            ('fr','Frecuencia Respiratoria','signos_vitales','number',1,1,4,'[]',4,60,'rpm','Respiraciones por minuto'),
            ('temperatura','Temperatura','signos_vitales','number',1,1,5,'[]',30.0,45.0,'°C','Temperatura corporal'),
            ('spo2','SpO2','signos_vitales','number',1,1,6,'[]',0,100,'%','Saturación de oxígeno'),
            ('glucometria','Glucometría','signos_vitales','number',0,1,7,'[]',0,600,'mg/dL','Glucosa capilar'),
            # ── Escalas ──
            ('glasgow_ocular','Glasgow Ocular','escalas','select',1,1,10,'[{"v":4,"l":"4 - Espontánea"},{"v":3,"l":"3 - Al estímulo verbal"},{"v":2,"l":"2 - Al dolor"},{"v":1,"l":"1 - Ninguna"}]',1,4,'','Respuesta ocular'),
            ('glasgow_verbal','Glasgow Verbal','escalas','select',1,1,11,'[{"v":5,"l":"5 - Orientada"},{"v":4,"l":"4 - Confusa"},{"v":3,"l":"3 - Inapropiada"},{"v":2,"l":"2 - Incomprensible"},{"v":1,"l":"1 - Ninguna"}]',1,5,'','Respuesta verbal'),
            ('glasgow_motor','Glasgow Motor','escalas','select',1,1,12,'[{"v":6,"l":"6 - Obedece órdenes"},{"v":5,"l":"5 - Localiza dolor"},{"v":4,"l":"4 - Retira"},{"v":3,"l":"3 - Flexión anormal"},{"v":2,"l":"2 - Extensión"},{"v":1,"l":"1 - Ninguna"}]',1,6,'','Respuesta motora'),
            ('eva_dolor','EVA Dolor (0-10)','escalas','range',1,1,13,'[]',0,10,'','Escala Visual Análoga del dolor'),
            ('dolor_localizacion','Localización del dolor','escalas','text',0,1,14,'[]',None,None,'','Dónde refiere el dolor'),
            # ── Discriminadores (checkboxes) ──
            ('disc_via_aerea','Compromiso vía aérea','discriminadores','checkbox',0,1,20,'[]',None,None,'','Obstrucción o dificultad respiratoria severa'),
            ('disc_sangrado','Sangrado activo no controlado','discriminadores','checkbox',0,1,21,'[]',None,None,'','Hemorragia activa'),
            ('disc_dolor_toracico','Dolor torácico','discriminadores','checkbox',0,1,22,'[]',None,None,'','Dolor opresivo/irradiado'),
            ('disc_alt_neurologica','Alteración neurológica','discriminadores','checkbox',0,1,23,'[]',None,None,'','Alteración conciencia, focalización'),
            ('disc_gestante','Gestante','discriminadores','checkbox',0,1,24,'[]',None,None,'','Embarazo activo'),
            ('disc_menor_edad','Menor de edad','discriminadores','checkbox',0,1,25,'[]',None,None,'','Paciente pediátrico'),
            ('disc_trauma_mayor','Trauma mayor','discriminadores','checkbox',0,1,26,'[]',None,None,'','Politraumatismo, caída de altura'),
            ('disc_convulsiones','Convulsiones','discriminadores','checkbox',0,1,27,'[]',None,None,'','Crisis convulsiva activa o reciente'),
            ('disc_fiebre_alta','Fiebre alta (≥39°C)','discriminadores','checkbox',0,1,28,'[]',None,None,'','Fiebre alta persistente'),
            # ── Campos clínicos ──
            ('motivo_consulta','Motivo de consulta','clinico','textarea',1,1,30,'[]',None,None,'','Razón principal de la consulta'),
            ('alergias','Alergias conocidas','clinico','text',0,1,31,'[]',None,None,'','Medicamentos, alimentos, otros'),
            ('disc_otros','Otros discriminadores','clinico','textarea',0,1,32,'[]',None,None,'','Observaciones adicionales relevantes'),
            ('notas_enfermeria','Notas de enfermería','clinico','textarea',0,1,33,'[]',None,None,'','Observaciones del profesional de triage'),
        ]
        for f in triage_fields:
            cur.execute(core.adapt(
                "INSERT INTO triage_form_config(campo,etiqueta,grupo,tipo,requerido,visible,orden,opciones,rango_min,rango_max,unidad,ayuda)"
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", db), f)
        conn.commit()

    # HC campos config seed (superadmin-configurable doctor HC fields)
    cur.execute("SELECT COUNT(*) FROM hc_campos_config")
    if cur.fetchone()[0] == 0:
        hc_fields = [
            # ── Anamnesis ──
            ('anamnesis','enfermedad_actual','Enfermedad actual','textarea',1,1,1,'[]','Descripción detallada de la enfermedad actual','general'),
            ('anamnesis','antecedentes_personales','Antecedentes personales','textarea',0,1,2,'[]','Antecedentes patológicos personales','general'),
            ('anamnesis','antecedentes_familiares','Antecedentes familiares','textarea',0,1,3,'[]','Antecedentes patológicos familiares','general'),
            ('anamnesis','antecedentes_quirurgicos','Antecedentes quirúrgicos','textarea',0,1,4,'[]','Cirugías previas','general'),
            ('anamnesis','antecedentes_farmacologicos','Antecedentes farmacológicos','textarea',0,1,5,'[]','Medicamentos actuales o previos','general'),
            ('anamnesis','alergias','Alergias','text',0,1,6,'[]','Alergias conocidas','general'),
            ('anamnesis','habitos','Hábitos','textarea',0,1,7,'[]','Tabaquismo, alcohol, sustancias, ejercicio','general'),
            ('anamnesis','revision_sistemas','Revisión por sistemas','textarea',0,1,8,'[]','Revisión sistemática por aparatos','general'),
            # ── Examen Físico ──
            ('examen_fisico','examen_general','Examen general','textarea',1,1,10,'[]','Estado general del paciente','general'),
            ('examen_fisico','cabeza_cuello','Cabeza y cuello','textarea',0,1,11,'[]','Hallazgos en cabeza y cuello','general'),
            ('examen_fisico','torax','Tórax','textarea',0,1,12,'[]','Inspección, palpación, percusión, auscultación','general'),
            ('examen_fisico','abdomen','Abdomen','textarea',0,1,13,'[]','Hallazgos abdominales','general'),
            ('examen_fisico','extremidades','Extremidades','textarea',0,1,14,'[]','Evaluación de extremidades','general'),
            ('examen_fisico','neurologico','Neurológico','textarea',0,1,15,'[]','Examen neurológico','general'),
            ('examen_fisico','piel','Piel y anexos','textarea',0,1,16,'[]','Hallazgos en piel','general'),
            # ── Análisis ──
            ('analisis','impresion_diagnostica','Impresión diagnóstica','textarea',1,1,20,'[]','Diagnóstico principal y CIE-10','general'),
            ('analisis','diagnostico_diferencial','Diagnóstico diferencial','textarea',0,1,21,'[]','Diagnósticos diferenciales a considerar','general'),
            # ── Plan ──
            ('plan','plan_terapeutico','Plan terapéutico','textarea',1,1,30,'[]','Plan de manejo y tratamiento','general'),
            ('plan','recomendaciones','Recomendaciones','textarea',0,1,31,'[]','Indicaciones para el paciente','general'),
            ('plan','signos_alarma','Signos de alarma','textarea',0,1,32,'[]','Signos de alarma para consultar de nuevo','general'),
        ]
        for f in hc_fields:
            cur.execute(core.adapt(
                "INSERT INTO hc_campos_config(seccion,campo,etiqueta,tipo,requerido,visible,orden,opciones,ayuda,especialidad)"
                "VALUES(?,?,?,?,?,?,?,?,?,?)", db), f)
        conn.commit()

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
    from flask import redirect
    return redirect('/kiosco/urgencias')

@app.route('/kiosco/tv')
def kiosco_tv_page():
    from flask import redirect
    return redirect('/kiosco/tv/urgencias')

@app.route('/kiosco/admin')
def kiosco_admin_page():
    return send_from_directory('static', 'kiosco-admin.html')

@app.route('/kiosco/<modo>')
def kiosco_modo_page(modo):
    return send_from_directory('static', 'kiosco.html')

@app.route('/kiosco/tv/<modo>')
def kiosco_tv_modo_page(modo):
    return send_from_directory('static', 'kiosco-tv.html')

@app.route('/atencion/triage')
def atencion_triage_page():
    return send_from_directory('static', 'atencion-triage.html')

@app.route('/atencion/admisiones')
def atencion_admisiones_page():
    return send_from_directory('static', 'atencion-admisiones.html')

@app.route('/atencion/consulta')
def atencion_consulta_page():
    return send_from_directory('static', 'atencion-consulta.html')

@app.route('/admisiones/dashboard')
def admisiones_dashboard_page():
    return send_from_directory('static', 'admisiones-dashboard.html')

@app.route('/laboratorio')
def laboratorio_page():
    return send_from_directory('static', 'laboratorio.html')

@app.route('/interconsultas')
def interconsultas_page():
    return send_from_directory('static', 'interconsultas.html')

@app.route('/facturacion')
def facturacion_page():
    return send_from_directory('static', 'facturacion.html')

@app.route('/admin/dashboard')
def admin_dashboard_page():
    return send_from_directory('static', 'admin-dashboard.html')

@app.route('/admin/usuarios')
def admin_usuarios_page():
    return send_from_directory('static', 'admin-usuarios.html')

@app.route('/historias-clinicas')
def historias_clinicas_page():
    return send_from_directory('static', 'historias-clinicas.html')

@app.route('/nueva-hc')
def nueva_hc_page():
    return send_from_directory('static', 'nueva-hc.html')

@app.route('/dashboard')
def dashboard_page():
    return send_from_directory('static', 'dashboard.html')

@app.route('/api/seed-demo', methods=['POST'])
def seed_demo():
    """One-time seed of 15 demo clinical histories. Requires superadmin."""
    if not session.get('user_id'):
        return jsonify({'error': 'No autenticado'}), 401
    permisos = core.get_user_permisos()
    if 'superadmin' not in permisos:
        return jsonify({'error': 'Requiere superadmin'}), 403

    conn, db = core.get_db()
    cur = conn.cursor()
    A = lambda q: core.adapt(q, db)

    # Check if already seeded
    count = core.row(cur, A("SELECT COUNT(*) as c FROM historia_clinica"))
    if count['c'] >= 15:
        cur.close(); core._return_db(conn, db)
        return jsonify({'error': 'Ya hay datos demo cargados', 'total_hc': count['c']}), 409

    PACIENTES = [
        ("CC","80123456","Pedro Antonio","Ramírez Castillo","1985-03-14","M","3105551234","Compensar"),
        ("CC","52987654","Luz Marina","Gómez Ospina","1972-11-28","F","3209876543","Sanitas"),
        ("CC","1098765432","Sebastián","Herrera Montoya","1998-07-02","M","3001234567","Sura"),
        ("TI","1234509876","Valentina","Ríos Castaño","2010-05-19","F","3187654321","Nueva EPS"),
        ("CC","79654321","Jorge Eliecer","Muñoz Patiño","1960-01-30","M","3112345678","Salud Total"),
        ("CE","E456789","María Alejandra","dos Santos Lima","1990-09-15","F","3156789012","Coomeva"),
        ("CC","1023987654","Camila Andrea","Vargas Restrepo","1995-12-08","F","3204567890","Famisanar"),
        ("CC","7812345","Roberto Carlos","Díaz Arango","1955-06-22","M","3178901234","Cafesalud"),
        ("CC","1087654321","Daniela","Moreno Suárez","2000-02-14","F","3143210987","EPS Sura"),
        ("CC","98765432","Héctor Fabio","Londoño Mejía","1968-08-05","M","3167890123","Coomeva"),
        ("RC","1122334455","Santiago","Pérez Ochoa","2023-04-10","M","3198765432","Nueva EPS"),
        ("CC","51234567","Gloria Patricia","Martínez Henao","1978-10-31","F","3134567890","Compensar"),
    ]

    pac_ids = []
    for td,nd,nom,ape,fn,gen,cel,eps in PACIENTES:
        existing = core.row(cur, A("SELECT id FROM pacientes WHERE num_doc=?"), (nd,))
        if existing:
            pac_ids.append(existing['id'])
        else:
            cur.execute(A("INSERT INTO pacientes(tipo_doc,num_doc,nombres,apellidos,fecha_nacimiento,genero,celular,eps)VALUES(?,?,?,?,?,?,?,?)"),
                        (td,nd,nom,ape,fn,gen,cel,eps))
            conn.commit()
            p = core.row(cur, A("SELECT id FROM pacientes WHERE num_doc=?"), (nd,))
            pac_ids.append(p['id'])

    HISTORIAS = [
        (0,"urgencias","Dolor torácico opresivo irradiado a brazo izquierdo, diaforesis y náuseas","urgencia","I20.0","I20.9","mejorado","domicilio","cerrada","U-D01","II","2026-07-15 08:30:00","Dra. Carolina Mejía",
         [("Masculino 41a, dolor torácico típico. TA 150/90, FC 98, SatO2 96%. ECG: supradesnivel ST V1-V4. Troponinas elevadas.","SCA con elevación ST anterior. Protocolo ACS.","I20.0",'{"ta":"150/90","fc":98,"sat":96}')],
         [("laboratorio","903841","Troponina I ultrasensible",1,"stat","Dolor torácico","I20.0"),("imagenologia","879101","Rx tórax PA y lateral",1,"urgente","Cardiomegalia","I20.0")],
         [("Ácido Acetilsalicílico","19900123","300 mg","Tableta","Oral","300 mg","Dosis única","1 día","1","Masticar inmediatamente","I20.0"),("Enoxaparina","20050789","60 mg","Sol inyectable","SC","60 mg","c/12h","5 días","10","Anticoagulación","I20.0")],
         [("cardiologia","Cardiología","SCA STEMI anterior. Cateterismo urgente","I20.0","stat")]),
        (1,"urgencias","Caída con trauma en cadera derecha. Imposibilidad para la marcha","urgencia","S72.0","S72.00","mejorado","hospitalizacion","cerrada","U-D02","III","2026-07-16 14:20:00","Dr. Andrés Felipe Ruiz",
         [("Femenina 53a, trauma cadera. Rotación externa MID. EVA 8/10.","Fractura cuello femoral. Rx + ortopedia.","S72.0",'{"ta":"130/80","fc":88,"sat":98}')],
         [("imagenologia","871021","Rx pelvis AP",1,"urgente","Fractura cadera","S72.0"),("laboratorio","903859","Hemograma",1,"urgente","Pre-qx","S72.0")],
         [("Tramadol","19950111","50 mg/mL","Sol inyectable","IV","50 mg","c/8h","3 días","9","Analgesia","S72.0")],
         [("ortopedia","Ortopedia y Traumatología","Fractura cuello femoral Garden III. Osteosíntesis vs artroplastia","S72.0","urgente")]),
        (2,"urgencias","Crisis asmática severa, disnea progresiva 6h, sin mejoría con inhalador","enfermedad_general","J45.1","J45.1","mejorado","domicilio","cerrada","U-D03","II","2026-07-18 22:15:00","Dra. Sandra Milena Ortiz",
         [("Masculino 28a asmático. Sibilancias difusas, músculos accesorios. FR 28, SatO2 89%.","Exacerbación severa asma. Nebulizaciones + corticoide.","J45.1",'{"ta":"120/70","fc":110,"fr":28,"sat":89}'),
          ("Post-nebulización x3: FR 22, SatO2 94%. Sibilancias leves. PEF 65%.","Respuesta parcial. Continuar oxígeno.","J45.1",'{"ta":"118/72","fc":92,"sat":94}')],
         [("laboratorio","903854","Gases arteriales",1,"stat","Crisis asmática","J45.1")],
         [("Salbutamol","20000333","5 mg/mL","Sol nebulización","Inhalatoria","0.5mL+3mL SSN","c/20min x3","1 día","3","Nebulización seriada","J45.1"),("Prednisolona","19970444","50 mg","Tableta","Oral","50 mg","Única","1 día","1","Corticoide","J45.1")],
         []),
        (3,"urgencias","Fiebre alta 39.5°C de 3 días, cefalea intensa y mialgias generalizadas","urgencia","A90","A90","mejorado","domicilio","cerrada","U-D04","III","2026-07-20 10:45:00","Dr. Mauricio Velásquez",
         [("Adolescente 16a con síndrome febril. Petequias, torniquete +. Plaquetas 95,000.","Dengue con signos alarma. Hidratación IV, monitoreo c/6h.","A90",'{"ta":"100/60","fc":104,"temp":39.2}')],
         [("laboratorio","903859","Hemograma",1,"stat","Plaquetas","A90"),("laboratorio","903856","NS1 + IgM/IgG Dengue",1,"stat","Confirmación","A90")],
         [("Acetaminofén","19850666","500 mg","Tableta","Oral","500 mg","c/6h","5 días","20","NO AINEs","A90")],
         []),
        (4,"urgencias","Dolor abdominal FID 12h, progresivo, náuseas y vómito","urgencia","K35.9","K35.80","mejorado","hospitalizacion","cerrada","U-D05","III","2026-07-22 06:00:00","Dra. Carolina Mejía",
         [("Masculino 65a, dolor FID. McBurney +, Blumberg +. Temp 38.1. Leuco 15,800.","Apendicitis aguda. TAC + cirugía general. NPO.","K35.9",'{"ta":"140/85","fc":94,"temp":38.1}')],
         [("imagenologia","879301","TAC abdomen con contraste",1,"urgente","Apendicitis","K35.9"),("laboratorio","903859","Hemograma",1,"stat","Leucocitosis","K35.9"),("laboratorio","903866","PCR cuantitativa",1,"urgente","Inflamación","K35.9")],
         [("Dipirona","19880222","1g/2mL","Sol inyectable","IV","2g","c/6h","1 día","4","Analgesia","K35.9"),("Metoclopramida","19900777","10 mg","Sol inyectable","IV","10 mg","c/8h","1 día","3","Antiemético","K35.9")],
         [("cirugia_general","Cirugía General","Apendicitis aguda. Apendicectomía.","K35.9","urgente")]),
        (5,"consulta","Control prenatal semana 28. Primer embarazo sin complicaciones","control","Z34.0","Z34.0","estable","domicilio","cerrada","C-D01",None,"2026-07-23 09:00:00","Dra. Patricia Gómez",
         [("Gestante 28 sem. AU 27cm, FCF 144. TA 110/70. Sin complicaciones.","Embarazo 28 sem normal. Labs III trimestre. Control 2 sem.","Z34.0",'{"ta":"110/70","fc":78}')],
         [("laboratorio","903859","Hemograma",1,"normal","Prenatal III trim","Z34.0"),("imagenologia","881302","Eco obstétrica",1,"normal","Biometría sem 28","Z34.0")],
         [("Sulfato Ferroso","19920888","200 mg","Tableta","Oral","200 mg","c/24h","30 días","30","Hierro","Z34.0"),("Ácido Fólico","19930999","1 mg","Tableta","Oral","1 mg","c/24h","30 días","30","Suplemento","Z34.0")],
         []),
        (6,"urgencias","Laceración profunda antebrazo derecho por vidrio. Sangrado activo","urgencia","S51.0","S51.0","mejorado","domicilio","cerrada","U-D06","IV","2026-07-24 16:30:00","Dr. Andrés Felipe Ruiz",
         [("Femenina 31a, laceración 8cm antebrazo. Sin compromiso tendinoso/vascular.","Sutura Nylon 4-0 (12 puntos). Profilaxis antitetánica.","S51.0",'{"ta":"115/70","fc":82,"sat":99}')],
         [],
         [("Cefalexina","19960111","500 mg","Cápsula","Oral","500 mg","c/6h","7 días","28","ATB profiláctico","S51.0"),("Ibuprofeno","19870222","400 mg","Tableta","Oral","400 mg","c/8h","5 días","15","AINE","S51.0")],
         []),
        (7,"urgencias","Disnea súbita con dolor pleurítico. Post-cirugía rodilla 10 días","urgencia","I26.9","I26.0","mejorado","hospitalizacion","cerrada","U-D07","I","2026-07-25 03:20:00","Dra. Sandra Milena Ortiz",
         [("Masculino 67a post-artroplastia rodilla. Disnea severa. TA 90/60, SatO2 85%. Dímero D >5000.","TEP masivo. Angiotac urgente. Soporte hemodinámico.","I26.9",'{"ta":"90/60","fc":120,"fr":30,"sat":85}'),
          ("Angiotac: TEP bilateral confirmado. HNF en infusión. Traslado UCI.","TEP masivo confirmado. Anticoagulación plena. UCI.","I26.0",'{"ta":"95/62","fc":112,"sat":90}')],
         [("imagenologia","879401","Angiotac tórax",1,"stat","TEP","I26.9"),("laboratorio","903870","Dímero D",1,"stat","TEP","I26.9"),("laboratorio","903854","Gases arteriales",1,"stat","Hipoxemia","I26.9")],
         [("Heparina sódica","19840333","25000 UI","Sol inyectable","IV","80 UI/kg bolo + 18 UI/kg/h","Infusión","5 días","5","Anticoagulación","I26.0")],
         [("neumologia","Neumología","TEP masivo bilateral. Trombolisis sistémica.","I26.0","stat"),("medicina_intensiva","UCI","TEP con compromiso hemodinámico. Monitoreo invasivo.","I26.0","stat")]),
        (8,"consulta","Cefalea crónica tipo migraña, 3-4 episodios/semana, 6 meses","consulta","G43.9","G43.0","estable","domicilio","cerrada","C-D02",None,"2026-07-26 11:00:00","Dr. Mauricio Velásquez",
         [("Femenina 26a, migraña crónica. Pulsátil hemicraneal, foto/fonofobia. Neuro normal. MIDAS III.","Migraña sin aura crónica. Profilaxis propranolol. Control 4 sem.","G43.0",'{"ta":"105/65","fc":72}')],
         [],
         [("Propranolol","19860444","40 mg","Tableta","Oral","40 mg","c/12h","30 días","60","Profilaxis","G43.0"),("Sumatriptán","20020555","50 mg","Tableta","Oral","50 mg","SOS crisis","30 días","4","Rescate","G43.0")],
         []),
        (9,"urgencias","Alteración conciencia, glucometría 42 mg/dL. Diabético tipo 2","urgencia","E16.2","E11.6","mejorado","domicilio","cerrada","U-D08","I","2026-07-27 19:45:00","Dra. Carolina Mejía",
         [("Masculino 58a DM2. Glasgow 10. Glucometría 42. Diaforesis.","Hipoglucemia severa por glibenclamida. Dextrosa 50%. Monitoreo.","E16.2",'{"ta":"160/95","fc":108,"glasgow":10}'),
          ("Post-rescate: Glucometría 185. Glasgow 15. Alerta.","Recuperación. Suspender glibenclamida, ajustar insulina.","E11.6",'{"ta":"135/82","fc":82,"glasgow":15}')],
         [("laboratorio","903869","Glucosa sérica",1,"stat","Hipoglucemia","E16.2"),("laboratorio","903862","HbA1c",1,"urgente","Control metabólico","E11.6")],
         [("Metformina","19980666","850 mg","Tableta","Oral","850 mg","c/12h","30 días","60","Continuar","E11.6"),("Insulina Glargina","20100777","100 UI/mL","Sol inyectable","SC","14 UI","c/24h noche","30 días","1","Ajuste dosis","E11.6")],
         [("endocrinologia","Endocrinología","Hipoglucemia severa DM2. Ajuste integral esquema.","E11.6","preferente")]),
        (10,"urgencias","Lactante 3 años, dificultad respiratoria, rinorrea, tos y fiebre 38.5°C","urgencia","J21.0","J21.0","mejorado","domicilio","cerrada","U-D09","III","2026-07-28 07:30:00","Dr. Mauricio Velásquez",
         [("Lactante 3a. Tirajes subcostales. Sibilancias. SatO2 93%.","Bronquiolitis aguda probable VSR. O2 + nebulización SSN hipertónica.","J21.0",'{"fc":130,"fr":42,"sat":93,"temp":38.2}')],
         [("laboratorio","903856","Panel viral respiratorio",1,"urgente","Identificar agente","J21.0")],
         [("Acetaminofén","19850666","150 mg/5mL","Jarabe","Oral","7.5 mL","c/6h","3 días","60 mL","15mg/kg","J21.0")],
         []),
        (11,"consulta","Dolor lumbar crónico 8 meses con irradiación a MID","enfermedad_general","M54.5","M51.1","estable","domicilio","cerrada","C-D03",None,"2026-07-29 15:00:00","Dr. Andrés Felipe Ruiz",
         [("Femenina 48a, lumbalgia + ciatalgia der. Lasègue + 30°. Fuerza 4/5 dorsiflexión.","Radiculopatía L5-S1. RMN + pregabalina + fisioterapia.","M51.1",'{"ta":"120/78","fc":74}')],
         [("imagenologia","883101","RMN columna lumbosacra",1,"preferente","Radiculopatía","M51.1")],
         [("Pregabalina","20060888","75 mg","Cápsula","Oral","75 mg","c/12h","30 días","60","Dolor neuropático","M51.1"),("Naproxeno","19870999","250 mg","Tableta","Oral","250 mg","c/8h","10 días","30","AINE","M54.5")],
         [("rehabilitacion","Medicina Física y Rehabilitación","Radiculopatía L5-S1. Fisioterapia 10 sesiones + TENS","M51.1","normal")]),
        (6,"urgencias","Accidente tránsito motociclista. TEC moderado con pérdida de consciencia","accidente_transito","S06.0","S06.0","mejorado","hospitalizacion","abierta","U-D10","I","2026-08-01 23:10:00","Dra. Sandra Milena Ortiz",
         [("Femenina 31a accidente moto. Glasgow 12. Herida parietal izq. Anisocoria leve.","TEC moderado. TAC cráneo urgente. Neuroprotección.","S06.0",'{"ta":"100/65","fc":105,"sat":95,"glasgow":12}')],
         [("imagenologia","879501","TAC cráneo simple",1,"stat","TEC moderado","S06.0"),("imagenologia","879502","Rx columna cervical",1,"stat","Fractura cervical","S06.0"),("laboratorio","903859","Hemograma",1,"stat","Trauma","S06.0")],
         [("Fenitoína","19840111","250 mg/5mL","Sol inyectable","IV","15 mg/kg","Única","1 día","1","Profilaxis convulsiones","S06.0")],
         [("neurocirugia","Neurocirugía","TEC moderado Glasgow 12 con anisocoria. Decisión quirúrgica.","S06.0","stat")]),
        (1,"consulta","Control post-op fractura cadera — 6 semanas post osteosíntesis","control","Z09.8","Z09.8","estable","domicilio","cerrada","C-D04",None,"2026-08-02 10:30:00","Dr. Andrés Felipe Ruiz",
         [("Femenina 53a, 6 sem post-osteosíntesis cadera. Herida sana. Marcha con andador.","Evolución satisfactoria. Continuar fisio. Rx control 6 sem.","Z09.8",'{"ta":"125/75","fc":72}')],
         [("imagenologia","871022","Rx cadera control",1,"normal","Post-quirúrgico","Z09.8")],
         [],
         []),
        (9,"urgencias","Dolor precordial atípico con irradiación epigástrica. Antecedente IAM","urgencia","I20.9","K21.0","mejorado","domicilio","cerrada","U-D11","II","2026-08-03 05:15:00","Dra. Carolina Mejía",
         [("Masculino 58a, antecedente IAM. Dolor retroesternal urente. ECG normal. Troponinas neg x2.","Dolor no cardíaco — ERGE. Troponinas neg. IBP. Gastro ambulatorio.","K21.0",'{"ta":"145/88","fc":84,"sat":98}')],
         [("laboratorio","903841","Troponina I seriada x2",2,"stat","Descartar SCA","I20.9"),("laboratorio","903859","Hemograma",1,"urgente","General","I20.9")],
         [("Omeprazol","19900333","20 mg","Cápsula","Oral","20 mg","c/12h","14 días","28","IBP ERGE","K21.0")],
         []),
    ]

    created = []
    try:
        for h in HISTORIAS:
            (pi,tipo_hc,motivo,causa,cie10i,cie10e,cond,dest,est,turno,tri,fecha,med,evols,ords,rxs,ics) = h
            pid = pac_ids[pi]
            nombre_p = f"{PACIENTES[pi][2]} {PACIENTES[pi][3]}"
            id_adm = f"DEMO-{turno}-{pid}"
            cur.execute(A("INSERT INTO admisiones(id_adm,turno,turno_tipo,estado,nombre_temp,doc_num_temp,servicio_nombre,paciente_id,triage_nivel,creado_en,hc_abierta)VALUES(?,?,?,?,?,?,?,?,?,?,1)"),
                        (id_adm,turno,'general','atendido' if est=='cerrada' else 'en_atencion',nombre_p,PACIENTES[pi][1],'Urgencias' if tipo_hc=='urgencias' else 'Consulta externa',pid,tri,fecha))
            conn.commit()
            adm = core.row(cur, A("SELECT id FROM admisiones WHERE id_adm=?"), (id_adm,))
            aid = adm['id']
            cerr = fecha.replace(fecha[11:],"18:00:00") if est=='cerrada' else None
            cur.execute(A("INSERT INTO historia_clinica(admision_id,paciente_id,tipo_hc,estado,motivo_consulta,causa_atencion,cod_cie10_ingreso,cod_cie10_egreso,condicion_egreso,destino_egreso,medico_nombre,firma_medico,creado_por,creado_en,cerrado_en)VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"),
                        (aid,pid,tipo_hc,est,motivo,causa,cie10i,cie10e,cond,dest,med,med,med,fecha,cerr))
            conn.commit()
            hc = core.row(cur, A("SELECT id FROM historia_clinica WHERE admision_id=?"), (aid,))
            hid = hc['id']
            cur.execute(A("UPDATE admisiones SET hc_id=? WHERE id=?"), (hid,aid))
            conn.commit()
            for ev in evols:
                ea,an,c10,sv = ev
                cur.execute(A("INSERT INTO hc_evoluciones(hc_id,tipo,enfermedad_actual,analisis,plan_terapeutico,cod_cie10,signos_vitales_json,medico_nombre,creado_en)VALUES(?,?,?,?,?,?,?,?,?)"),
                            (hid,'evolucion',ea,an,an,c10,sv,med,fecha))
                conn.commit()
            for o in ords:
                to,cups,ne,ca,pr,ind,dx = o
                cur.execute(A("INSERT INTO ordenes_medicas(hc_id,admision_id,paciente_id,tipo_orden,cod_cups,nombre_estudio,cantidad,prioridad,indicacion_clinica,diagnostico_asociado,estado,medico_ordena,creado_en)VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"),
                            (hid,aid,pid,to,cups,ne,ca,pr,ind,dx,'completada' if est=='cerrada' else 'pendiente',med,fecha))
                conn.commit()
            for rx in rxs:
                me,cu,co,fo,vi,do2,fr,du,ca2,ins,dx = rx
                cur.execute(A("INSERT INTO prescripciones(hc_id,admision_id,paciente_id,medicamento,cod_cum,concentracion,forma_farmaceutica,via_administracion,dosis,frecuencia,duracion,cantidad_total,instrucciones,diagnostico_asociado,estado,medico_nombre,creado_en)VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"),
                            (hid,aid,pid,me,cu,co,fo,vi,do2,fr,du,ca2,ins,dx,'dispensada' if est=='cerrada' else 'pendiente',med,fecha))
                conn.commit()
            for ic in ics:
                es,en2,mo,dx,pr = ic
                cur.execute(A("INSERT INTO interconsultas(hc_id,admision_id,paciente_id,tipo,especialidad_solicitada,motivo,diagnostico_presuntivo,cod_cie10,prioridad,estado,medico_solicitante,creado_en)VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"),
                            (hid,aid,pid,'interconsulta',en2,mo,mo,dx,pr,'respondida' if est=='cerrada' else 'solicitada',med,fecha))
                conn.commit()
            created.append({'hc_id': hid, 'paciente': nombre_p, 'dx': cie10i})

        final = core.row(cur, A("SELECT COUNT(*) as c FROM historia_clinica"))
        return jsonify({'success': True, 'created': len(created), 'total_hc': final['c'], 'historias': created})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


@app.route('/health')
def health():
    info = {
        'status': 'ok',
        'db': 'pg' if core.USE_PG else 'sqlite',
        'ts': datetime.now().isoformat(),
    }
    # Quick DB test
    try:
        conn, db = core.get_db()
        cur = conn.cursor()
        T = core.TODAY(db)
        D = f"CAST(creado_en AS DATE)" if db == 'pg' else f"DATE(creado_en,'-5 hours')"
        cur.execute(f"SELECT COUNT(*) FROM admisiones WHERE {D}={T}")
        info['admisiones_hoy'] = cur.fetchone()[0]
        # Show all admissions with their states grouped by estado
        rows = core.rows(cur, "SELECT id, id_adm, turno, estado, triage_nivel, nombre_temp, creado_en FROM admisiones ORDER BY id DESC LIMIT 30")
        info['ultimas_admisiones'] = rows
        # Count by estado
        estados = core.rows(cur, "SELECT estado, COUNT(*) as cnt FROM admisiones GROUP BY estado")
        info['admisiones_por_estado'] = {r['estado']: r['cnt'] for r in estados}
        # Today's by estado
        estados_hoy = core.rows(cur, core.adapt(f"SELECT estado, COUNT(*) as cnt FROM admisiones WHERE {D}={T} GROUP BY estado", db))
        info['hoy_por_estado'] = {r['estado']: r['cnt'] for r in estados_hoy}
        # Show puestos
        puestos = core.rows(cur, "SELECT id, codigo, nombre, tipo, activo FROM puestos_atencion")
        info['puestos'] = puestos
        # Show current date/time from DB perspective
        if db == 'pg':
            cur.execute("SELECT NOW() as now_ts, CURRENT_DATE as today, current_setting('TIMEZONE') as tz")
        else:
            cur.execute("SELECT datetime('now','-5 hours') as now_ts, DATE('now','-5 hours') as today, 'America/Bogota (UTC-5)' as tz")
        ts_row = cur.fetchone()
        info['db_now'] = ts_row[0] if ts_row else None
        info['db_today'] = str(ts_row[1]) if ts_row else None
        info['db_timezone'] = ts_row[2] if ts_row else None
        cur.close()
        core._return_db(conn, db)
    except Exception as e:
        info['db_error'] = str(e)
        import traceback
        info['db_trace'] = traceback.format_exc()
    return jsonify(info)

# ── BLUEPRINT REGISTRATION ───────────────────────────────────────────────────

try:
    from kiosco_engine import kiosco_bp
    app.register_blueprint(kiosco_bp)
    print("🏥 Módulo Kiosco registrado en /api/kiosco")
except Exception as e:
    print(f"⚠ kiosco_engine no disponible: {e}")

try:
    from atencion_engine import atencion_bp
    app.register_blueprint(atencion_bp)
    print("🩺 Módulo Atención registrado en /api/atencion")
except Exception as e:
    print(f"⚠ atencion_engine no disponible: {e}")

try:
    from admisiones_engine import admisiones_bp
    app.register_blueprint(admisiones_bp)
    print("📋 Módulo Admisiones registrado en /api/admisiones")
except Exception as e:
    print(f"⚠ admisiones_engine no disponible: {e}")

try:
    from hc_engine import hc_bp
    app.register_blueprint(hc_bp)
    print("📋 Módulo Historia Clínica registrado en /api/hc")
except Exception as e:
    print(f"⚠ hc_engine no disponible: {e}")

try:
    from ordenes_engine import ordenes_bp
    app.register_blueprint(ordenes_bp)
    print("🔬 Módulo Órdenes registrado en /api/ordenes")
except Exception as e:
    print(f"⚠ ordenes_engine no disponible: {e}")

try:
    from interconsultas_engine import interconsultas_bp
    app.register_blueprint(interconsultas_bp)
    print("🏥 Módulo Interconsultas registrado en /api/interconsultas")
except Exception as e:
    print(f"⚠ interconsultas_engine no disponible: {e}")

try:
    from facturacion_engine import facturacion_bp
    app.register_blueprint(facturacion_bp)
    print("💰 Módulo Facturación registrado en /api/facturacion")
except Exception as e:
    print(f"⚠ facturacion_engine no disponible: {e}")

try:
    from impresion_engine import impresion_bp
    app.register_blueprint(impresion_bp)
    print("🖨 Módulo Impresión registrado en /api/impresion")
except Exception as e:
    print(f"⚠ impresion_engine no disponible: {e}")

try:
    from roles_engine import roles_bp
    app.register_blueprint(roles_bp)
    print("🔐 Módulo RBAC registrado en /api/admin")
except Exception as e:
    print(f"⚠ roles_engine no disponible: {e}")

# ── INIT ──────────────────────────────────────────────────────────────────────

init_db()
core.seed_auth()

# ── SEED 15 ROLES RBAC ──────────────────────────────────────────────────────

def seed_roles_rbac():
    """Seed the full 15-role RBAC structure (idempotent)."""
    conn, db = core.get_db()
    cur = conn.cursor()

    roles_15 = [
        ('Superadmin', 'Acceso total al sistema — monitoreo, endpoints, alertas, cuentas',
         ['superadmin'], 1),
        ('Director/Gerente', 'Visión ejecutiva completa — KPIs, widgets, gestión usuarios/roles',
         ['director', 'admin_usuarios', 'admin_roles', 'admin_campos', 'admin_sistema',
          'kiosco', 'admisiones', 'historia_clinica', 'historia_clinica_read', 'enfermeria',
          'ordenes', 'interconsultas', 'prescripciones', 'laboratorio', 'imagenes',
          'farmacia', 'facturacion', 'facturacion_aprobar', 'cobros', 'reportes',
          'agendamiento', 'inventario', 'auditor'], 1),
        ('Admin Operativo', 'Gestión de roles, campos y módulos — sin acceso financiero',
         ['admin_usuarios', 'admin_roles', 'admin_campos', 'admin_sistema',
          'kiosco', 'admisiones', 'historia_clinica_read', 'enfermeria',
          'ordenes', 'interconsultas', 'reportes', 'agendamiento'], 1),
        ('Coordinador Médico', 'Campos HC, horarios médicos, dashboard medicina',
         ['coord_medico', 'admin_campos', 'historia_clinica', 'historia_clinica_read',
          'ordenes', 'interconsultas', 'prescripciones', 'agendamiento', 'reportes'], 1),
        ('Coordinador Enfermería', 'Campos triage, horarios enfermería, dashboard enfermería',
         ['coord_enfermeria', 'admin_campos', 'enfermeria', 'historia_clinica_read',
          'admisiones', 'agendamiento', 'reportes'], 1),
        ('Coordinador Admisiones', 'Supervisión admisiones, campos, validación, usuarios',
         ['coord_admisiones', 'admin_campos', 'admin_usuarios', 'admisiones', 'kiosco',
          'historia_clinica_read', 'reportes'], 1),
        ('Coordinador Financiero', 'Facturación, RIPS, copagos, aprobaciones, dashboards financieros',
         ['coord_financiero', 'facturacion', 'facturacion_aprobar', 'cobros',
          'reportes', 'historia_clinica_read', 'admisiones'], 1),
        ('Médico', 'Historia clínica, evoluciones, órdenes, interconsultas, prescripciones',
         ['historia_clinica', 'ordenes', 'interconsultas', 'prescripciones',
          'historia_clinica_read', 'agendamiento'], 0),
        ('Enfermero/a', 'Triage, signos vitales, medicamentos, escalas, enfermería cirugía',
         ['enfermeria', 'historia_clinica_read', 'admisiones'], 0),
        ('Admisionista', 'Admisiones, validación derechos, kiosco',
         ['admisiones', 'kiosco', 'historia_clinica_read'], 0),
        ('Auxiliar Facturación', 'Genera pre-facturas — NO aprueba',
         ['facturacion', 'historia_clinica_read', 'admisiones'], 0),
        ('Farmacia', 'Despacho medicamentos, validación prescripciones',
         ['farmacia', 'prescripciones', 'historia_clinica_read'], 0),
        ('Laboratorio/Imágenes', 'Cola de órdenes, captura resultados, validación',
         ['laboratorio', 'imagenes', 'ordenes', 'historia_clinica_read'], 0),
        ('Auditor/Calidad', 'Solo lectura total — reportes de calidad e indicadores',
         ['auditor', 'historia_clinica_read', 'reportes', 'admisiones',
          'facturacion', 'ordenes', 'interconsultas'], 0),
        ('Cajero', 'Cobro copagos, registro pagos',
         ['cobros', 'admisiones'], 0),
    ]

    for nombre, desc, perms, es_sistema in roles_15:
        try:
            ex = core.row(cur, core.adapt("SELECT id FROM roles WHERE nombre=?", db), (nombre,))
            if not ex:
                cur.execute(core.adapt(
                    "INSERT INTO roles(nombre,descripcion,permisos,es_sistema)VALUES(?,?,?,?)", db),
                    (nombre, desc, json.dumps(perms), es_sistema))
                conn.commit()
            else:
                # Update permisos if role already exists (keep in sync)
                cur.execute(core.adapt(
                    "UPDATE roles SET descripcion=?, permisos=?, es_sistema=? WHERE nombre=?", db),
                    (desc, json.dumps(perms), es_sistema, nombre))
                conn.commit()
        except Exception:
            conn.rollback()

    cur.close()
    core._return_db(conn, db)
    print("🔐 15 roles RBAC configurados")

seed_roles_rbac()

if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.getenv('PORT', 5050)),
        debug=os.getenv('FLASK_ENV') != 'production',
        use_reloader=os.getenv('NO_RELOAD') != '1',
        threaded=True,
    )
