"""
kiosco_engine.py — Blueprint del módulo Kiosco de Arthemis Health.

Endpoints:
  POST /api/kiosco/anuncio          — registro público desde kiosco (genera turno + admisión)
  GET  /api/kiosco/turnos-hoy       — conteo de turnos del día
  GET  /api/kiosco/cola             — cola completa de hoy (para TV display y admin)
  POST /api/kiosco/llamar-turno     — llamar un turno (cambia estado, SSE broadcast)
  POST /api/kiosco/atender-turno    — marcar turno como atendido
  GET  /api/kiosco/config           — configuración extendida del kiosco (público)
  PUT  /api/kiosco/config           — guardar configuración (admin)
  GET  /api/kiosco/anuncios         — anuncios activos (público, para carrusel)
  PUT  /api/kiosco/anuncios         — actualizar anuncios (admin)
  GET  /api/kiosco/servicios        — servicios disponibles
  PUT  /api/kiosco/servicios        — actualizar servicios (admin)
  GET  /api/audit                   — log de auditoría (admin)
"""

import json
from datetime import datetime
from flask import Blueprint, request, jsonify

kiosco_bp = Blueprint('kiosco', __name__)

def _get_deps():
    """Lazy import to avoid circular dependencies."""
    import core
    return core

# ── POST /api/kiosco/anuncio ─────────────────────────────────────────────────

@kiosco_bp.route('/api/kiosco/anuncio', methods=['POST'])
def kiosco_anuncio():
    """Registro público desde kiosco. Genera turno + admisión."""
    core = _get_deps()
    d = request.json
    if not d:
        return jsonify({'error': 'Datos requeridos'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()

    try:
        T = core.TODAY(db)

        # Determine alert color
        color = 'yellow'
        if d.get('turno_tipo') == 'preferencial':
            color = 'red'
        elif d.get('tipo_atencion') == 'cita_programada':
            color = 'green'

        # Generate admission ID
        cur.execute(f"SELECT COUNT(*) FROM admisiones WHERE DATE(creado_en)={T}")
        n = cur.fetchone()[0]
        id_adm = f"ADM{str(n + 1).zfill(3)}-{datetime.now().strftime('%Y%m%d')}"

        # Generate turno: PP (preferencial), PC (cita programada), PS (sin cita)
        pref = 'PP' if d.get('turno_tipo') == 'preferencial' else (
            'PC' if d.get('tipo_atencion') == 'cita_programada' else 'PS')
        cur.execute(
            core.adapt(f"SELECT COUNT(*) FROM admisiones WHERE turno LIKE ? AND DATE(creado_en)={T}", db),
            (f'{pref}%',))
        nt = cur.fetchone()[0]
        turno = f"{pref}{nt + 1}"

        # Set habeas data fields
        habeas = 1 if d.get('habeas_data') else 0
        habeas_ts = datetime.now().isoformat() if habeas else None

        # Insert admission
        cur.execute(core.adapt(
            "INSERT INTO admisiones(id_adm,paciente_id,estado,tipo_atencion,turno,turno_tipo,"
            "tiempo_espera_min,color_alerta,origen,doc_num_temp,doc_type_temp,nombre_temp,"
            "celular_notif,habeas_data,habeas_data_ts,servicio_nombre)VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", db),
            (id_adm, d.get('paciente_id'), 'kiosco',
             d.get('tipo_atencion', 'sin_cita'), turno,
             d.get('turno_tipo', 'general'),
             5 if color == 'red' else 15, color, 'kiosco',
             d.get('doc_num'), d.get('doc_type', 'CC'),
             d.get('nombre'), d.get('celular'),
             habeas, habeas_ts, d.get('servicio_nombre')))
        conn.commit()

        # Get inserted ID
        a = core.row(cur, core.adapt("SELECT id FROM admisiones WHERE id_adm=?", db), (id_adm,))

        # Audit
        core.audit(cur, db, 'admisiones', a['id'], 'kiosco_anuncio',
                    f"Turno {turno} asignado desde kiosco")

        # Notification for reception
        cur.execute(core.adapt(
            "INSERT INTO notificaciones(tipo,destinatario,mensaje,admision_id)VALUES(?,?,?,?)", db),
            ('nuevo_turno', 'recepcion',
             f"Nuevo turno {turno} — {d.get('doc_num', '')}", a['id']))
        conn.commit()

        # SSE broadcast
        core.sse_broadcast({
            'tipo': 'nuevo_turno',
            'turno': turno,
            'turno_tipo': d.get('turno_tipo', 'general'),
            'color_alerta': color,
            'id': a['id'],
            'id_adm': id_adm,
            'nombre': d.get('nombre') or d.get('doc_num', ''),
            'servicio': d.get('servicio_nombre', ''),
        })

        return jsonify({
            'success': True,
            'turno': turno,
            'id_adm': id_adm,
            'id': a['id'],
            'color_alerta': color,
            'turno_tipo': d.get('turno_tipo', 'general'),
        })

    except Exception as e:
        conn.rollback()
        return jsonify({'error': f'Error interno: {str(e)}'}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/kiosco/turnos-hoy ───────────────────────────────────────────────

@kiosco_bp.route('/api/kiosco/turnos-hoy')
def kiosco_turnos_hoy():
    """Returns today's turn counts per type (for display/stats)."""
    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    T = core.TODAY(db)

    total = core.rows(cur, f"SELECT turno_tipo, COUNT(*) as total FROM admisiones WHERE DATE(creado_en)={T} GROUP BY turno_tipo")
    en_espera = core.rows(cur, core.adapt(
        f"SELECT turno_tipo, COUNT(*) as total FROM admisiones WHERE DATE(creado_en)={T} AND estado=? GROUP BY turno_tipo", db),
        ('kiosco',))

    cur.close()
    core._return_db(conn, db)

    return jsonify({
        'total': {r['turno_tipo']: r['total'] for r in total},
        'en_espera': {r['turno_tipo']: r['total'] for r in en_espera},
    })


# ── GET /api/kiosco/cola ─────────────────────────────────────────────────────

@kiosco_bp.route('/api/kiosco/cola')
def kiosco_cola():
    """Cola completa de hoy — para TV display y panel admin."""
    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    T = core.TODAY(db)

    admisiones = core.rows(cur, core.adapt(
        f"SELECT a.id, a.id_adm, a.turno, a.turno_tipo, a.estado, a.nombre_temp, "
        f"a.doc_num_temp, a.servicio_nombre, a.color_alerta, a.creado_en, "
        f"a.fecha_llamado, p.nombres, p.apellidos "
        f"FROM admisiones a LEFT JOIN pacientes p ON a.paciente_id=p.id "
        f"WHERE DATE(a.creado_en)={T} "
        f"ORDER BY CASE a.estado WHEN 'llamando' THEN 0 WHEN 'kiosco' THEN 1 "
        f"WHEN 'atendido' THEN 2 ELSE 3 END, "
        f"CASE WHEN a.turno_tipo='preferencial' THEN 0 ELSE 1 END, a.id", db))

    result = []
    for a in admisiones:
        nombre = a.get('nombre_temp') or ''
        if a.get('nombres'):
            nombre = f"{a['nombres']} {a.get('apellidos', '')}".strip()
        result.append({
            'id': a['id'],
            'id_adm': a['id_adm'],
            'turno': a['turno'],
            'turno_tipo': a.get('turno_tipo', 'general'),
            'estado': a['estado'],
            'nombre': nombre,
            'servicio': a.get('servicio_nombre', ''),
            'color_alerta': a.get('color_alerta', 'yellow'),
            'creado_en': a.get('creado_en', ''),
            'fecha_llamado': a.get('fecha_llamado'),
            'prioritario': a.get('turno_tipo') == 'preferencial',
        })

    cur.close()
    core._return_db(conn, db)
    return jsonify({'cola': result})


# ── POST /api/kiosco/llamar-turno ────────────────────────────────────────────

@kiosco_bp.route('/api/kiosco/llamar-turno', methods=['POST'])
def kiosco_llamar_turno():
    """Llamar un turno — cambia estado a 'llamando', SSE broadcast para TV."""
    core = _get_deps()
    d = request.json or {}
    turno_id = d.get('id')
    if not turno_id:
        return jsonify({'error': 'ID de admisión requerido'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()

    try:
        # Move any currently "llamando" to "atendido"
        T = core.TODAY(db)
        cur.execute(core.adapt(
            f"UPDATE admisiones SET estado='atendido' WHERE estado='llamando' AND DATE(creado_en)={T}", db))

        # Set this one to "llamando"
        cur.execute(core.adapt(
            f"UPDATE admisiones SET estado='llamando', fecha_llamado={core.NOW(db)} WHERE id=?", db),
            (turno_id,))
        conn.commit()

        # Get updated info
        a = core.row(cur, core.adapt(
            "SELECT a.*, p.nombres, p.apellidos FROM admisiones a "
            "LEFT JOIN pacientes p ON a.paciente_id=p.id WHERE a.id=?", db), (turno_id,))

        if a:
            nombre = a.get('nombre_temp') or ''
            if a.get('nombres'):
                nombre = f"{a['nombres']} {a.get('apellidos', '')}".strip()

            core.audit(cur, db, 'admisiones', turno_id, 'llamar_turno',
                        f"Turno {a['turno']} llamado")
            conn.commit()

            core.sse_broadcast({
                'tipo': 'llamar_turno',
                'turno': a['turno'],
                'nombre': nombre,
                'servicio': a.get('servicio_nombre', ''),
                'turno_tipo': a.get('turno_tipo', 'general'),
                'id': a['id'],
            })

            return jsonify({'success': True, 'turno': a['turno'], 'nombre': nombre})
        return jsonify({'error': 'Admisión no encontrada'}), 404

    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/kiosco/atender-turno ───────────────────────────────────────────

@kiosco_bp.route('/api/kiosco/atender-turno', methods=['POST'])
def kiosco_atender_turno():
    """Marcar turno como atendido."""
    core = _get_deps()
    d = request.json or {}
    turno_id = d.get('id')
    if not turno_id:
        return jsonify({'error': 'ID requerido'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        cur.execute(core.adapt("UPDATE admisiones SET estado='atendido' WHERE id=?", db), (turno_id,))
        conn.commit()
        core.audit(cur, db, 'admisiones', turno_id, 'atender_turno', 'Turno atendido')
        conn.commit()

        core.sse_broadcast({'tipo': 'turno_atendido', 'id': turno_id})
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET/PUT /api/kiosco/config ───────────────────────────────────────────────

@kiosco_bp.route('/api/kiosco/config', methods=['GET'])
def kiosco_config_get():
    """Configuración extendida del kiosco (branding, flow, consent, whatsapp, etc)."""
    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()

    # Base tenant config
    tenant = core.row(cur, "SELECT * FROM tenant_config WHERE tenant_id='default'") or {}

    # Kiosco extended config from modulos_config
    mod = core.row(cur, core.adapt(
        "SELECT configuracion FROM modulos_config WHERE tenant_id='default' AND modulo='kiosco'", db))

    cur.close()
    core._return_db(conn, db)

    kiosco_cfg = {}
    if mod and mod.get('configuracion'):
        try:
            kiosco_cfg = json.loads(mod['configuracion'])
        except Exception:
            pass

    # Merge tenant branding into kiosco config
    result = {
        'branding': {
            'clinicName': tenant.get('nombre_clinica', 'Arthemis Health'),
            'logo': tenant.get('logo_url'),
            'primaryColor': tenant.get('color_primario', '#5147C4'),
            'accentColor': tenant.get('color_secundario', '#7269D8'),
            'bgColor': kiosco_cfg.get('bgColor', '#FAF8F4'),
            'darkBg': kiosco_cfg.get('darkBg', '#1C1916'),
            'welcomeMsg': kiosco_cfg.get('welcomeMsg', 'Bienvenido a su cita'),
            'footerMsg': kiosco_cfg.get('footerMsg', 'Su salud, nuestra prioridad'),
        },
        'consent': {
            'title': kiosco_cfg.get('consent_title', 'Autorización para el tratamiento de datos personales'),
            'body': kiosco_cfg.get('consent_body',
                'En cumplimiento de la Ley Estatutaria 1581 de 2012, autorizo a {{clinicName}} '
                '(NIT {{nit}}) para recolectar, almacenar, usar y tratar mis datos personales '
                'y datos sensibles de salud para la prestación de servicios de salud, '
                'gestión administrativa, y cumplimiento de obligaciones legales.'),
            'checkboxLabel': kiosco_cfg.get('consent_checkbox', 'Acepto el tratamiento de mis datos personales y de salud'),
            'nit': tenant.get('nit', ''),
            'email': tenant.get('email_habeas_data', ''),
        },
        'whatsapp': kiosco_cfg.get('whatsapp', {'enabled': False, 'countryCode': '+57', 'businessNumber': ''}),
        'lang': kiosco_cfg.get('lang', 'es'),
        'adminPin': kiosco_cfg.get('adminPin', '1234'),
        'adRotationSeconds': kiosco_cfg.get('adRotationSeconds', 6),
        'idleTimeoutMs': kiosco_cfg.get('idleTimeoutMs', 50000),
        'countdownSeconds': kiosco_cfg.get('countdownSeconds', 10),
        'flowSteps': kiosco_cfg.get('flowSteps', []),
    }

    return jsonify(result)


@kiosco_bp.route('/api/kiosco/config', methods=['PUT'])
def kiosco_config_put():
    """Guardar configuración extendida del kiosco (admin only)."""
    core = _get_deps()
    if not _is_admin():
        return jsonify({'error': 'No autorizado'}), 401

    d = request.json or {}
    conn, db = core.get_db()
    cur = conn.cursor()

    try:
        # Update tenant_config branding fields
        branding = d.get('branding', {})
        if branding:
            fields = []
            vals = []
            if 'clinicName' in branding:
                fields.append('nombre_clinica=?')
                vals.append(branding['clinicName'])
            if 'primaryColor' in branding:
                fields.append('color_primario=?')
                vals.append(branding['primaryColor'])
            if 'accentColor' in branding:
                fields.append('color_secundario=?')
                vals.append(branding['accentColor'])
            if 'logo' in branding:
                fields.append('logo_url=?')
                vals.append(branding['logo'])
            if fields:
                cur.execute(core.adapt(
                    f"UPDATE tenant_config SET {','.join(fields)} WHERE tenant_id='default'", db),
                    vals)

        # Update consent NIT and email in tenant_config
        consent = d.get('consent', {})
        if consent:
            if 'nit' in consent:
                cur.execute(core.adapt("UPDATE tenant_config SET nit=? WHERE tenant_id='default'", db),
                            (consent['nit'],))
            if 'email' in consent:
                cur.execute(core.adapt("UPDATE tenant_config SET email_habeas_data=? WHERE tenant_id='default'", db),
                            (consent['email'],))

        # Store extended kiosco config in modulos_config.configuracion
        kiosco_cfg = {
            'bgColor': branding.get('bgColor', '#FAF8F4'),
            'darkBg': branding.get('darkBg', '#1C1916'),
            'welcomeMsg': branding.get('welcomeMsg', 'Bienvenido a su cita'),
            'footerMsg': branding.get('footerMsg', 'Su salud, nuestra prioridad'),
            'consent_title': consent.get('title', ''),
            'consent_body': consent.get('body', ''),
            'consent_checkbox': consent.get('checkboxLabel', ''),
            'whatsapp': d.get('whatsapp', {}),
            'lang': d.get('lang', 'es'),
            'adminPin': d.get('adminPin', '1234'),
            'adRotationSeconds': d.get('adRotationSeconds', 6),
            'idleTimeoutMs': d.get('idleTimeoutMs', 50000),
            'countdownSeconds': d.get('countdownSeconds', 10),
            'flowSteps': d.get('flowSteps', []),
        }

        cur.execute(core.adapt(
            "UPDATE modulos_config SET configuracion=? WHERE tenant_id='default' AND modulo='kiosco'", db),
            (json.dumps(kiosco_cfg, ensure_ascii=False),))
        conn.commit()

        core.audit(cur, db, 'modulos_config', 'kiosco', 'config_update', 'Configuración del kiosco actualizada')
        conn.commit()

        return jsonify({'success': True})

    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET/PUT /api/kiosco/anuncios ─────────────────────────────────────────────

@kiosco_bp.route('/api/kiosco/anuncios')
def kiosco_anuncios_get():
    """Anuncios activos para el carrusel del kiosco."""
    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()

    anuncios = core.rows(cur, "SELECT * FROM kiosco_anuncios ORDER BY orden, id")
    cur.close()
    core._return_db(conn, db)
    return jsonify({'anuncios': anuncios})


@kiosco_bp.route('/api/kiosco/anuncios', methods=['PUT'])
def kiosco_anuncios_put():
    """Actualizar lista de anuncios (admin)."""
    core = _get_deps()
    if not _is_admin():
        return jsonify({'error': 'No autorizado'}), 401

    d = request.json or {}
    anuncios = d.get('anuncios', [])

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM kiosco_anuncios")
        for i, a in enumerate(anuncios):
            cur.execute(core.adapt(
                "INSERT INTO kiosco_anuncios(titulo,descripcion,media_type,media_url,activo,orden)"
                "VALUES(?,?,?,?,?,?)", db),
                (a.get('titulo', ''), a.get('descripcion', ''),
                 a.get('media_type', 'none'), a.get('media_url', ''),
                 1 if a.get('activo', True) else 0, i))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET/PUT /api/kiosco/servicios ────────────────────────────────────────────

@kiosco_bp.route('/api/kiosco/servicios')
def kiosco_servicios_get():
    """Servicios disponibles en el kiosco."""
    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()

    servicios = core.rows(cur, "SELECT * FROM kiosco_servicios ORDER BY orden, id")
    cur.close()
    core._return_db(conn, db)
    return jsonify({'servicios': servicios})


@kiosco_bp.route('/api/kiosco/servicios', methods=['PUT'])
def kiosco_servicios_put():
    """Actualizar servicios (admin)."""
    core = _get_deps()
    if not _is_admin():
        return jsonify({'error': 'No autorizado'}), 401

    d = request.json or {}
    servicios = d.get('servicios', [])

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM kiosco_servicios")
        for i, s in enumerate(servicios):
            cur.execute(core.adapt(
                "INSERT INTO kiosco_servicios(codigo,nombre,icono,activo,orden)VALUES(?,?,?,?,?)", db),
                (s.get('codigo', f'srv_{i}'), s.get('nombre', ''),
                 s.get('icono', '●'), 1 if s.get('activo', True) else 0, i))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/audit ───────────────────────────────────────────────────────────

@kiosco_bp.route('/api/audit')
def audit_log():
    """Audit trail — admin only."""
    core = _get_deps()
    if not _is_admin():
        return jsonify({'error': 'No autorizado'}), 401

    conn, db = core.get_db()
    cur = conn.cursor()

    limit = request.args.get('limit', 100, type=int)
    entidad = request.args.get('entidad', '')

    if entidad:
        logs = core.rows(cur, core.adapt(
            "SELECT * FROM audit_trail WHERE entidad=? ORDER BY id DESC LIMIT ?", db),
            (entidad, limit))
    else:
        logs = core.rows(cur, f"SELECT * FROM audit_trail ORDER BY id DESC LIMIT {limit}")

    cur.close()
    core._return_db(conn, db)
    return jsonify({'logs': logs})


# ── HELPERS ──────────────────────────────────────────────────────────────────

def _is_admin():
    """Check if current session has admin/kiosco permissions."""
    from flask import session
    if not session.get('user_id'):
        return False
    core = _get_deps()
    permisos = core.get_user_permisos()
    return 'superadmin' in permisos or 'kiosco' in permisos
