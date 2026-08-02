"""
hc_engine.py — Blueprint del modulo Historia Clinica de Arthemis Health.

Gestiona el ciclo de vida completo de la Historia Clinica:
  - Apertura de HC asociada a una admision
  - Consulta de HC completa (evoluciones, ordenes, interconsultas, prescripciones)
  - Busqueda de HC por admision
  - Registro de evoluciones clinicas (inmutables)
  - Diagnostico de egreso y cierre de HC
  - Antecedentes del paciente
  - Configuracion dinamica de campos HC
  - Resumen clinico estructurado (JSON para RDA/FHIR)

Endpoints:
  POST   /api/hc/abrir                        — abrir HC para una admision
  GET    /api/hc/<hc_id>                       — obtener HC completa
  GET    /api/hc/por-admision/<admision_id>    — obtener HC por admision
  POST   /api/hc/<hc_id>/evolucion             — agregar nota de evolucion (inmutable)
  GET    /api/hc/<hc_id>/evoluciones           — listar evoluciones
  PUT    /api/hc/<hc_id>/diagnostico-egreso    — registrar diagnostico de egreso
  PUT    /api/hc/<hc_id>/cerrar                — cerrar HC
  GET    /api/hc/antecedentes/<paciente_id>    — obtener antecedentes del paciente
  POST   /api/hc/antecedentes                  — agregar antecedente
  PUT    /api/hc/antecedentes/<ant_id>         — actualizar antecedente
  GET    /api/hc/campos-config                 — obtener configuracion de campos
  PUT    /api/hc/campos-config/<campo>         — actualizar configuracion de campo
  GET    /api/hc/<hc_id>/resumen               — resumen clinico estructurado
"""

import json
from flask import Blueprint, request, jsonify, session

hc_bp = Blueprint('hc', __name__)


def _get_deps():
    import core
    return core


def _is_authenticated():
    return bool(session.get('user_id'))


def _has_permiso_hc():
    """Check if current user has historia_clinica or superadmin permission."""
    if not session.get('user_id'):
        return False
    core = _get_deps()
    permisos = core.get_user_permisos()
    return 'superadmin' in permisos or 'historia_clinica' in permisos


def _has_permiso_superadmin():
    """Check if current user has superadmin permission."""
    if not session.get('user_id'):
        return False
    core = _get_deps()
    permisos = core.get_user_permisos()
    return 'superadmin' in permisos


def _add_timeline(cur, db, admision_id, evento, detalle='', usuario=None, puesto=None):
    """Add a timeline entry for an admission."""
    core = _get_deps()
    if usuario is None:
        usuario = session.get('usuario', 'sistema')
    if puesto is None:
        puesto = session.get('puesto_nombre', '')
    cur.execute(core.adapt(
        "INSERT INTO admision_timeline(admision_id,evento,detalle,usuario,puesto)"
        "VALUES(?,?,?,?,?)", db),
        (admision_id, evento, detalle, usuario, puesto))


# ── POST /api/hc/abrir ────────────────────────────────────────────────────────

@hc_bp.route('/api/hc/abrir', methods=['POST'])
def hc_abrir():
    """Open a new Historia Clinica for an admission.

    Input: admision_id, motivo_consulta, causa_atencion, cod_cie10_ingreso
    Validates admission exists, patient linked, and no duplicate HC.
    Creates historia_clinica record, updates admisiones, adds timeline.
    """
    if not _has_permiso_hc():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    admision_id = d.get('admision_id')

    if not admision_id:
        return jsonify({'error': 'admision_id requerido'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        # Check admission exists
        adm = core.row(cur, core.adapt(
            "SELECT * FROM admisiones WHERE id=?", db), (admision_id,))
        if not adm:
            return jsonify({'error': 'Admision no encontrada'}), 404

        if not adm.get('paciente_id'):
            return jsonify({'error': 'Debe vincular un paciente antes de abrir HC'}), 400

        # Check no existing HC for this admission
        existing = core.row(cur, core.adapt(
            "SELECT id FROM historia_clinica WHERE admision_id=?", db), (admision_id,))
        if existing:
            return jsonify({'error': 'Ya existe HC para esta admision', 'hc_id': existing['id']}), 409

        cur.execute(core.adapt(
            "INSERT INTO historia_clinica(admision_id,paciente_id,tipo_hc,estado,"
            "motivo_consulta,causa_atencion,cod_cie10_ingreso,creado_por)"
            "VALUES(?,?,?,?,?,?,?,?)", db),
            (admision_id, adm['paciente_id'],
             d.get('tipo_hc', 'urgencias'), 'abierta',
             d.get('motivo_consulta', ''),
             d.get('causa_atencion', 'urgencia'),
             d.get('cod_cie10_ingreso', ''),
             session.get('usuario', 'sistema')))
        conn.commit()

        # Get the HC id
        hc = core.row(cur, core.adapt(
            "SELECT id FROM historia_clinica WHERE admision_id=?", db), (admision_id,))

        # Update admission
        cur.execute(core.adapt(
            "UPDATE admisiones SET hc_abierta=1, hc_id=? WHERE id=?", db),
            (hc['id'], admision_id))
        conn.commit()

        _add_timeline(cur, db, admision_id, 'hc_abierta',
                      f"Historia clinica abierta (HC#{hc['id']})")
        conn.commit()

        core.audit(cur, db, 'historia_clinica', hc['id'], 'abrir_hc',
                   f"HC #{hc['id']} abierta por {session.get('usuario', '?')}")
        conn.commit()

        core.sse_broadcast({
            'tipo': 'hc_abierta',
            'hc_id': hc['id'],
            'admision_id': admision_id,
        })

        return jsonify({'success': True, 'hc_id': hc['id']})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/hc/<hc_id> ───────────────────────────────────────────────────────

@hc_bp.route('/api/hc/<int:hc_id>')
def hc_detalle(hc_id):
    """Get complete HC with evoluciones, ordenes, interconsultas, prescripciones.

    Joins with admisiones and pacientes. Fetches related records from
    ordenes_medicas, interconsultas, and prescripciones (try/except for
    tables that may not exist yet).
    """
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        # HC with admission and patient data
        hc = core.row(cur, core.adapt(
            "SELECT h.*, a.turno, a.estado as admision_estado, a.triage_nivel, "
            "a.tipo_pagador, a.pagador_entidad, a.creado_en as admision_creado_en, "
            "p.nombres, p.apellidos, p.tipo_doc as p_tipo_doc, p.num_doc as p_num_doc, "
            "p.fecha_nacimiento, p.genero, p.celular as p_celular, "
            "p.eps as p_eps, p.tipo_afiliado as p_tipo_afiliado "
            "FROM historia_clinica h "
            "LEFT JOIN admisiones a ON h.admision_id=a.id "
            "LEFT JOIN pacientes p ON h.paciente_id=p.id "
            "WHERE h.id=?", db),
            (hc_id,))
        if not hc:
            return jsonify({'error': 'Historia clinica no encontrada'}), 404

        # Evoluciones
        evoluciones = core.rows(cur, core.adapt(
            "SELECT * FROM hc_evoluciones WHERE hc_id=? ORDER BY creado_en", db),
            (hc_id,))

        # Ordenes medicas (table may not exist)
        ordenes = []
        try:
            ordenes = core.rows(cur, core.adapt(
                "SELECT * FROM ordenes_medicas WHERE hc_id=? ORDER BY creado_en", db),
                (hc_id,))
        except Exception:
            pass

        # Interconsultas (table may not exist)
        interconsultas = []
        try:
            interconsultas = core.rows(cur, core.adapt(
                "SELECT * FROM interconsultas WHERE hc_id=? ORDER BY creado_en", db),
                (hc_id,))
        except Exception:
            pass

        # Prescripciones (table may not exist)
        prescripciones = []
        try:
            prescripciones = core.rows(cur, core.adapt(
                "SELECT * FROM prescripciones WHERE hc_id=? ORDER BY creado_en", db),
                (hc_id,))
        except Exception:
            pass

        return jsonify({
            'hc': hc,
            'evoluciones': evoluciones,
            'ordenes': ordenes,
            'interconsultas': interconsultas,
            'prescripciones': prescripciones,
        })
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/hc/por-admision/<admision_id> ─────────────────────────────────────

@hc_bp.route('/api/hc/por-admision/<int:admision_id>')
def hc_por_admision(admision_id):
    """Get HC by admission ID. Returns the HC record or 404."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        hc = core.row(cur, core.adapt(
            "SELECT * FROM historia_clinica WHERE admision_id=?", db),
            (admision_id,))
        if not hc:
            return jsonify({'error': 'No existe HC para esta admision'}), 404
        return jsonify({'hc': hc})
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/hc/<hc_id>/evolucion ─────────────────────────────────────────────

@hc_bp.route('/api/hc/<int:hc_id>/evolucion', methods=['POST'])
def hc_agregar_evolucion(hc_id):
    """Add an evolution note to the HC. IMMUTABLE - INSERT only, never updated.

    Input: tipo, enfermedad_actual, antecedentes_json, revision_sistemas,
           examen_fisico, signos_vitales_json, analisis, plan_terapeutico,
           cod_cie10, campos_custom
    campos_custom is a JSON object with dynamic fields from hc_campos_config.
    Sets medico_id and medico_nombre from session.
    """
    if not _has_permiso_hc():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        # Verify HC exists and is open
        hc = core.row(cur, core.adapt(
            "SELECT * FROM historia_clinica WHERE id=?", db), (hc_id,))
        if not hc:
            return jsonify({'error': 'Historia clinica no encontrada'}), 404
        if hc.get('estado') == 'cerrada':
            return jsonify({'error': 'HC cerrada, no se pueden agregar evoluciones'}), 400

        # Serialize JSON fields
        antecedentes_json = d.get('antecedentes_json')
        if isinstance(antecedentes_json, (dict, list)):
            antecedentes_json = json.dumps(antecedentes_json, ensure_ascii=False)

        signos_vitales_json = d.get('signos_vitales_json')
        if isinstance(signos_vitales_json, (dict, list)):
            signos_vitales_json = json.dumps(signos_vitales_json, ensure_ascii=False)

        campos_custom = d.get('campos_custom')
        if isinstance(campos_custom, (dict, list)):
            campos_custom = json.dumps(campos_custom, ensure_ascii=False)

        medico_id = session.get('user_id')
        medico_nombre = session.get('usuario', 'sistema')

        cur.execute(core.adapt(
            "INSERT INTO hc_evoluciones(hc_id,tipo,enfermedad_actual,antecedentes_json,"
            "revision_sistemas,examen_fisico,signos_vitales_json,analisis,"
            "plan_terapeutico,cod_cie10,campos_custom,medico_id,medico_nombre)"
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", db),
            (hc_id,
             d.get('tipo', 'evolucion'),
             d.get('enfermedad_actual', ''),
             antecedentes_json or '',
             d.get('revision_sistemas', ''),
             d.get('examen_fisico', ''),
             signos_vitales_json or '',
             d.get('analisis', ''),
             d.get('plan_terapeutico', ''),
             d.get('cod_cie10', ''),
             campos_custom or '',
             medico_id,
             medico_nombre))
        conn.commit()

        # Get the evolucion id
        evolucion = core.row(cur, core.adapt(
            "SELECT id FROM hc_evoluciones WHERE hc_id=? ORDER BY id DESC LIMIT 1", db),
            (hc_id,))

        # Update HC actualizado_en
        cur.execute(core.adapt(
            f"UPDATE historia_clinica SET actualizado_en={core.NOW(db)} WHERE id=?", db),
            (hc_id,))
        conn.commit()

        # Timeline
        admision_id = hc.get('admision_id')
        if admision_id:
            _add_timeline(cur, db, admision_id, 'evolucion_agregada',
                          f"Evolucion #{evolucion['id']} tipo={d.get('tipo', 'evolucion')} "
                          f"por {medico_nombre}")
            conn.commit()

        core.audit(cur, db, 'hc_evoluciones', evolucion['id'], 'crear_evolucion',
                   f"Evolucion en HC#{hc_id} por {medico_nombre}")
        conn.commit()

        return jsonify({'success': True, 'evolucion_id': evolucion['id']}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/hc/<hc_id>/evoluciones ────────────────────────────────────────────

@hc_bp.route('/api/hc/<int:hc_id>/evoluciones')
def hc_listar_evoluciones(hc_id):
    """List all evoluciones for an HC, ordered chronologically."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        evoluciones = core.rows(cur, core.adapt(
            "SELECT * FROM hc_evoluciones WHERE hc_id=? ORDER BY creado_en", db),
            (hc_id,))
        return jsonify({'evoluciones': evoluciones, 'total': len(evoluciones)})
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/hc/<hc_id>/diagnostico-egreso ─────────────────────────────────────

@hc_bp.route('/api/hc/<int:hc_id>/diagnostico-egreso', methods=['PUT'])
def hc_diagnostico_egreso(hc_id):
    """Set egreso diagnosis on HC.

    Input: cod_cie10_egreso, diagnosticos_relacionados, condicion_egreso,
           destino_egreso
    """
    if not _has_permiso_hc():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        hc = core.row(cur, core.adapt(
            "SELECT * FROM historia_clinica WHERE id=?", db), (hc_id,))
        if not hc:
            return jsonify({'error': 'Historia clinica no encontrada'}), 404
        if hc.get('estado') == 'cerrada':
            return jsonify({'error': 'HC ya esta cerrada'}), 400

        diagnosticos_rel = d.get('diagnosticos_relacionados')
        if isinstance(diagnosticos_rel, (list, dict)):
            diagnosticos_rel = json.dumps(diagnosticos_rel, ensure_ascii=False)

        cur.execute(core.adapt(
            f"UPDATE historia_clinica SET cod_cie10_egreso=?, diagnosticos_relacionados=?, "
            f"condicion_egreso=?, destino_egreso=?, actualizado_en={core.NOW(db)} "
            f"WHERE id=?", db),
            (d.get('cod_cie10_egreso', ''),
             diagnosticos_rel or '',
             d.get('condicion_egreso', ''),
             d.get('destino_egreso', ''),
             hc_id))
        conn.commit()

        # Timeline
        admision_id = hc.get('admision_id')
        if admision_id:
            _add_timeline(cur, db, admision_id, 'diagnostico_egreso',
                          f"Dx egreso: {d.get('cod_cie10_egreso', '')} "
                          f"Condicion: {d.get('condicion_egreso', '')}")
            conn.commit()

        core.audit(cur, db, 'historia_clinica', hc_id, 'diagnostico_egreso',
                   f"Dx egreso registrado por {session.get('usuario', '?')}")
        conn.commit()

        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/hc/<hc_id>/cerrar ─────────────────────────────────────────────────

@hc_bp.route('/api/hc/<int:hc_id>/cerrar', methods=['PUT'])
def hc_cerrar(hc_id):
    """Close an HC. Sets estado='cerrada', cerrado_en, firma_medico.

    Also updates admisiones: fecha_salida, estado='atendido', condicion_egreso,
    destino_egreso. Broadcasts SSE turno_atendido. Triggers pre_factura
    generation if possible (non-blocking).
    """
    if not _has_permiso_hc():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        hc = core.row(cur, core.adapt(
            "SELECT * FROM historia_clinica WHERE id=?", db), (hc_id,))
        if not hc:
            return jsonify({'error': 'Historia clinica no encontrada'}), 404
        if hc.get('estado') == 'cerrada':
            return jsonify({'error': 'HC ya esta cerrada'}), 400

        firma_medico = d.get('firma_medico', session.get('usuario', 'sistema'))

        # Close HC
        cur.execute(core.adapt(
            f"UPDATE historia_clinica SET estado='cerrada', cerrado_en={core.NOW(db)}, "
            f"firma_medico=?, medico_id=?, medico_nombre=?, actualizado_en={core.NOW(db)} "
            f"WHERE id=?", db),
            (firma_medico,
             session.get('user_id'),
             session.get('usuario', 'sistema'),
             hc_id))
        conn.commit()

        # Update admission
        admision_id = hc.get('admision_id')
        if admision_id:
            condicion_egreso = hc.get('condicion_egreso') or d.get('condicion_egreso', '')
            destino_egreso = hc.get('destino_egreso') or d.get('destino_egreso', '')

            cur.execute(core.adapt(
                f"UPDATE admisiones SET fecha_salida={core.NOW(db)}, estado='atendido', "
                f"condicion_egreso=?, destino_egreso=? WHERE id=?", db),
                (condicion_egreso, destino_egreso, admision_id))
            conn.commit()

            _add_timeline(cur, db, admision_id, 'hc_cerrada',
                          f"HC#{hc_id} cerrada por {firma_medico}")
            conn.commit()

            # SSE broadcast
            adm = core.row(cur, core.adapt(
                "SELECT turno FROM admisiones WHERE id=?", db), (admision_id,))
            core.sse_broadcast({
                'tipo': 'turno_atendido',
                'id': admision_id,
                'turno': adm.get('turno', '') if adm else '',
                'hc_id': hc_id,
            })

        core.audit(cur, db, 'historia_clinica', hc_id, 'cerrar_hc',
                   f"HC cerrada por {firma_medico}")
        conn.commit()

        # Trigger pre_factura generation (non-blocking)
        try:
            cur.execute(core.adapt(
                "INSERT INTO pre_factura(admision_id,paciente_id,estado)"
                "VALUES(?,?,?)", db),
                (admision_id, hc.get('paciente_id'), 'borrador'))
            conn.commit()
        except Exception:
            pass  # Table may not exist or pre_factura logic handled elsewhere

        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/hc/antecedentes/<paciente_id> ─────────────────────────────────────

@hc_bp.route('/api/hc/antecedentes/<int:paciente_id>')
def hc_antecedentes(paciente_id):
    """Get all antecedentes for a patient."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        antecedentes = core.rows(cur, core.adapt(
            "SELECT * FROM hc_antecedentes WHERE paciente_id=? ORDER BY creado_en DESC", db),
            (paciente_id,))
        return jsonify({'antecedentes': antecedentes, 'total': len(antecedentes)})
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/hc/antecedentes ──────────────────────────────────────────────────

@hc_bp.route('/api/hc/antecedentes', methods=['POST'])
def hc_agregar_antecedente():
    """Add a new antecedente for a patient.

    Input: paciente_id, tipo, descripcion, fecha
    """
    if not _has_permiso_hc():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    paciente_id = d.get('paciente_id')
    tipo = d.get('tipo', '').strip()
    descripcion = d.get('descripcion', '').strip()

    if not paciente_id or not tipo:
        return jsonify({'error': 'paciente_id y tipo requeridos'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        # Verify patient exists
        paciente = core.row(cur, core.adapt(
            "SELECT id FROM pacientes WHERE id=?", db), (paciente_id,))
        if not paciente:
            return jsonify({'error': 'Paciente no encontrado'}), 404

        cur.execute(core.adapt(
            "INSERT INTO hc_antecedentes(paciente_id,tipo,descripcion,fecha,activo,registrado_por)"
            "VALUES(?,?,?,?,1,?)", db),
            (paciente_id, tipo, descripcion,
             d.get('fecha', ''),
             session.get('usuario', 'sistema')))
        conn.commit()

        antecedente = core.row(cur, core.adapt(
            "SELECT * FROM hc_antecedentes WHERE paciente_id=? ORDER BY id DESC LIMIT 1", db),
            (paciente_id,))

        core.audit(cur, db, 'hc_antecedentes', antecedente['id'], 'crear_antecedente',
                   f"Antecedente tipo={tipo} para paciente {paciente_id} "
                   f"por {session.get('usuario', '?')}")
        conn.commit()

        return jsonify({'success': True, 'antecedente': antecedente}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/hc/antecedentes/<ant_id> ──────────────────────────────────────────

@hc_bp.route('/api/hc/antecedentes/<int:ant_id>', methods=['PUT'])
def hc_actualizar_antecedente(ant_id):
    """Update an antecedente. Use activo=0 to deactivate.

    Input: tipo, descripcion, fecha, activo
    """
    if not _has_permiso_hc():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        existing = core.row(cur, core.adapt(
            "SELECT * FROM hc_antecedentes WHERE id=?", db), (ant_id,))
        if not existing:
            return jsonify({'error': 'Antecedente no encontrado'}), 404

        allowed_fields = ['tipo', 'descripcion', 'fecha', 'activo']
        fields, vals = [], []
        for k in allowed_fields:
            if k in d:
                fields.append(f"{k}=?")
                vals.append(d[k])
        if not fields:
            return jsonify({'error': 'Nada que actualizar'}), 400

        vals.append(ant_id)
        cur.execute(core.adapt(
            f"UPDATE hc_antecedentes SET {','.join(fields)} WHERE id=?", db), vals)
        conn.commit()

        core.audit(cur, db, 'hc_antecedentes', ant_id, 'actualizar_antecedente',
                   f"Actualizado por {session.get('usuario', '?')}")
        conn.commit()

        updated = core.row(cur, core.adapt(
            "SELECT * FROM hc_antecedentes WHERE id=?", db), (ant_id,))
        return jsonify({'success': True, 'antecedente': updated})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/hc/campos-config ──────────────────────────────────────────────────

@hc_bp.route('/api/hc/campos-config')
def hc_campos_config():
    """Get all HC field configurations for the admin UI.

    Optional query param: ?especialidad=general
    Returns fields grouped by seccion.
    """
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    especialidad = request.args.get('especialidad', '').strip()

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        if especialidad:
            campos = core.rows(cur, core.adapt(
                "SELECT * FROM hc_campos_config WHERE especialidad=? OR especialidad='general' "
                "ORDER BY seccion, orden", db),
                (especialidad,))
        else:
            campos = core.rows(cur, core.adapt(
                "SELECT * FROM hc_campos_config ORDER BY seccion, orden", db))

        # Group by seccion
        por_seccion = {}
        for c in campos:
            sec = c.get('seccion', 'general')
            if sec not in por_seccion:
                por_seccion[sec] = []
            por_seccion[sec].append(c)

        return jsonify({'campos': campos, 'por_seccion': por_seccion, 'total': len(campos)})
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/hc/campos-config/<campo> ──────────────────────────────────────────

@hc_bp.route('/api/hc/campos-config/<campo>', methods=['PUT'])
def hc_actualizar_campo_config(campo):
    """Update a field configuration. Admin only (superadmin permission).

    Input: etiqueta, tipo, requerido, visible, orden, opciones, ayuda, especialidad
    """
    if not _has_permiso_superadmin():
        return jsonify({'error': 'No autorizado - requiere superadmin'}), 403

    core = _get_deps()
    d = request.json or {}

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        existing = core.row(cur, core.adapt(
            "SELECT * FROM hc_campos_config WHERE campo=?", db), (campo,))
        if not existing:
            return jsonify({'error': 'Campo no encontrado'}), 404

        allowed_fields = ['etiqueta', 'tipo', 'requerido', 'visible', 'orden',
                          'opciones', 'ayuda', 'especialidad']
        fields, vals = [], []
        for k in allowed_fields:
            if k in d:
                fields.append(f"{k}=?")
                val = d[k]
                if k == 'opciones' and isinstance(val, (list, dict)):
                    val = json.dumps(val, ensure_ascii=False)
                vals.append(val)
        if not fields:
            return jsonify({'error': 'Nada que actualizar'}), 400

        fields.append(f"modificado_por=?")
        vals.append(session.get('usuario', 'sistema'))
        fields.append(f"modificado_en={core.NOW(db)}")
        vals.append(campo)

        cur.execute(core.adapt(
            f"UPDATE hc_campos_config SET {','.join(fields)} WHERE campo=?", db), vals)
        conn.commit()

        core.audit(cur, db, 'hc_campos_config', existing['id'], 'actualizar_campo',
                   f"Campo '{campo}' actualizado por {session.get('usuario', '?')}")
        conn.commit()

        updated = core.row(cur, core.adapt(
            "SELECT * FROM hc_campos_config WHERE campo=?", db), (campo,))
        return jsonify({'success': True, 'campo': updated})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/hc/<hc_id>/resumen ────────────────────────────────────────────────

@hc_bp.route('/api/hc/<int:hc_id>/resumen')
def hc_resumen(hc_id):
    """Generate a structured clinical summary (JSON for future RDA/FHIR).

    Gathers: patient data, triage data, evoluciones, diagnoses,
    prescriptions, orders. Returns structured JSON summary.
    """
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        # HC + admission + patient
        hc = core.row(cur, core.adapt(
            "SELECT h.*, a.turno, a.triage_nivel, a.triage_ts, "
            "a.tipo_pagador, a.pagador_entidad, a.pagador_regimen, "
            "a.creado_en as admision_creado_en, a.fecha_salida, "
            "p.nombres, p.apellidos, p.tipo_doc as p_tipo_doc, p.num_doc as p_num_doc, "
            "p.fecha_nacimiento, p.genero, p.celular as p_celular, "
            "p.eps as p_eps, p.tipo_afiliado as p_tipo_afiliado, "
            "p.direccion as p_direccion, p.ciudad as p_ciudad "
            "FROM historia_clinica h "
            "LEFT JOIN admisiones a ON h.admision_id=a.id "
            "LEFT JOIN pacientes p ON h.paciente_id=p.id "
            "WHERE h.id=?", db),
            (hc_id,))
        if not hc:
            return jsonify({'error': 'Historia clinica no encontrada'}), 404

        # Evoluciones
        evoluciones = core.rows(cur, core.adapt(
            "SELECT * FROM hc_evoluciones WHERE hc_id=? ORDER BY creado_en", db),
            (hc_id,))

        # Antecedentes
        antecedentes = core.rows(cur, core.adapt(
            "SELECT * FROM hc_antecedentes WHERE paciente_id=? AND activo=1 "
            "ORDER BY creado_en DESC", db),
            (hc.get('paciente_id'),))

        # Prescripciones (try/except)
        prescripciones = []
        try:
            prescripciones = core.rows(cur, core.adapt(
                "SELECT * FROM prescripciones WHERE hc_id=? ORDER BY creado_en", db),
                (hc_id,))
        except Exception:
            pass

        # Ordenes medicas (try/except)
        ordenes = []
        try:
            ordenes = core.rows(cur, core.adapt(
                "SELECT * FROM ordenes_medicas WHERE hc_id=? ORDER BY creado_en", db),
                (hc_id,))
        except Exception:
            pass

        # Triage data (try/except)
        triage = None
        try:
            triage = core.row(cur, core.adapt(
                "SELECT * FROM triage_clinico WHERE admision_id=?", db),
                (hc.get('admision_id'),))
        except Exception:
            pass

        # Build structured summary
        resumen = {
            'hc_id': hc_id,
            'estado': hc.get('estado'),
            'tipo_hc': hc.get('tipo_hc'),
            'paciente': {
                'id': hc.get('paciente_id'),
                'nombres': hc.get('nombres'),
                'apellidos': hc.get('apellidos'),
                'tipo_doc': hc.get('p_tipo_doc'),
                'num_doc': hc.get('p_num_doc'),
                'fecha_nacimiento': hc.get('fecha_nacimiento'),
                'genero': hc.get('genero'),
                'celular': hc.get('p_celular'),
                'eps': hc.get('p_eps'),
                'tipo_afiliado': hc.get('p_tipo_afiliado'),
                'direccion': hc.get('p_direccion'),
                'ciudad': hc.get('p_ciudad'),
            },
            'admision': {
                'id': hc.get('admision_id'),
                'turno': hc.get('turno'),
                'triage_nivel': hc.get('triage_nivel'),
                'triage_ts': hc.get('triage_ts'),
                'tipo_pagador': hc.get('tipo_pagador'),
                'pagador_entidad': hc.get('pagador_entidad'),
                'pagador_regimen': hc.get('pagador_regimen'),
                'creado_en': hc.get('admision_creado_en'),
                'fecha_salida': hc.get('fecha_salida'),
            },
            'motivo_consulta': hc.get('motivo_consulta'),
            'causa_atencion': hc.get('causa_atencion'),
            'diagnosticos': {
                'ingreso': hc.get('cod_cie10_ingreso'),
                'egreso': hc.get('cod_cie10_egreso'),
                'relacionados': hc.get('diagnosticos_relacionados'),
            },
            'condicion_egreso': hc.get('condicion_egreso'),
            'destino_egreso': hc.get('destino_egreso'),
            'medico': {
                'id': hc.get('medico_id'),
                'nombre': hc.get('medico_nombre'),
                'firma': hc.get('firma_medico'),
            },
            'triage': triage,
            'antecedentes': antecedentes,
            'evoluciones': evoluciones,
            'prescripciones': prescripciones,
            'ordenes': ordenes,
            'fechas': {
                'creado_en': hc.get('creado_en'),
                'cerrado_en': hc.get('cerrado_en'),
                'actualizado_en': hc.get('actualizado_en'),
            },
        }

        return jsonify({'resumen': resumen})
    finally:
        cur.close()
        core._return_db(conn, db)
