"""
interconsultas_engine.py — Blueprint del módulo Interconsultas de Arthemis Health.

Gestiona el ciclo de vida de interconsultas médicas (internas y externas):
  - Solicitud de interconsulta por médico tratante
  - Cola de interconsultas pendientes por especialidad
  - Aceptación por especialista
  - Respuesta con recomendaciones y diagnóstico
  - Cancelación con motivo
  - Consulta de detalle con información del paciente

Endpoints:
  POST   /api/interconsultas/solicitar            — solicitar interconsulta
  GET    /api/interconsultas/pendientes            — listar pendientes (filtro por especialidad)
  GET    /api/interconsultas/por-hc/<hc_id>        — listar por historia clínica
  PUT    /api/interconsultas/<ic_id>/aceptar       — especialista acepta
  PUT    /api/interconsultas/<ic_id>/responder      — especialista responde
  PUT    /api/interconsultas/<ic_id>/cancelar       — cancelar interconsulta
  GET    /api/interconsultas/<ic_id>                — detalle con info de paciente
"""

from flask import Blueprint, request, jsonify, session

interconsultas_bp = Blueprint('interconsultas', __name__)


def _get_deps():
    import core
    return core


def _is_authenticated():
    return bool(session.get('user_id'))


def _has_permiso_interconsultas():
    """Check if user has permission for interconsultas module."""
    if not session.get('user_id'):
        return False
    core = _get_deps()
    permisos = core.get_user_permisos()
    return bool({'superadmin', 'interconsultas', 'historia_clinica'} & set(permisos))


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


# ── POST /api/interconsultas/solicitar ────────────────────────────────────

@interconsultas_bp.route('/api/interconsultas/solicitar', methods=['POST'])
def interconsultas_solicitar():
    """Create a new interconsulta request."""
    if not _has_permiso_interconsultas():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    required = ['hc_id', 'admision_id', 'paciente_id', 'especialidad_solicitada', 'motivo']
    if not all(d.get(k) for k in required):
        return jsonify({'error': 'Campos requeridos: hc_id, admision_id, paciente_id, especialidad_solicitada, motivo'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        # Verify admission exists
        adm = core.row(cur, core.adapt(
            "SELECT * FROM admisiones WHERE id=?", db), (d['admision_id'],))
        if not adm:
            return jsonify({'error': 'Admisión no encontrada'}), 404

        # Get patient info for SSE broadcast
        paciente = core.row(cur, core.adapt(
            "SELECT * FROM pacientes WHERE id=?", db), (d['paciente_id'],))
        if not paciente:
            return jsonify({'error': 'Paciente no encontrado'}), 404

        medico_id = session.get('user_id')
        medico_nombre = session.get('usuario', 'sistema')
        prioridad = d.get('prioridad', 'rutina')
        tipo = d.get('tipo', 'interna')

        cur.execute(core.adapt(
            "INSERT INTO interconsultas(hc_id,admision_id,paciente_id,tipo,"
            "especialidad_solicitada,cod_cups,motivo,diagnostico_presuntivo,"
            "cod_cie10,prioridad,estado,medico_solicitante_id,medico_solicitante,"
            "fecha_solicitud)"
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,{now})".format(now=core.NOW(db)), db),
            (d['hc_id'], d['admision_id'], d['paciente_id'], tipo,
             d['especialidad_solicitada'], d.get('cod_cups', ''),
             d['motivo'], d.get('diagnostico_presuntivo', ''),
             d.get('cod_cie10', ''), prioridad, 'solicitada',
             medico_id, medico_nombre))
        conn.commit()

        # Get the new interconsulta id
        ic = core.row(cur, core.adapt(
            "SELECT id FROM interconsultas WHERE hc_id=? AND admision_id=? "
            "AND especialidad_solicitada=? AND medico_solicitante_id=? "
            "ORDER BY id DESC LIMIT 1", db),
            (d['hc_id'], d['admision_id'], d['especialidad_solicitada'], medico_id))
        ic_id = ic['id'] if ic else None

        # Timeline
        _add_timeline(cur, db, d['admision_id'], 'interconsulta_solicitada',
                      f"Interconsulta {tipo} a {d['especialidad_solicitada']} "
                      f"(prioridad: {prioridad})")
        conn.commit()

        # Audit
        core.audit(cur, db, 'interconsultas', ic_id, 'solicitar',
                   f"Interconsulta a {d['especialidad_solicitada']} solicitada por {medico_nombre}")
        conn.commit()

        # SSE broadcast
        nombre_paciente = f"{paciente.get('nombres', '')} {paciente.get('apellidos', '')}".strip()
        core.sse_broadcast({
            'tipo': 'interconsulta_nueva',
            'ic_id': ic_id,
            'especialidad_solicitada': d['especialidad_solicitada'],
            'paciente': nombre_paciente,
            'prioridad': prioridad,
            'tipo': tipo,
            'medico_solicitante': medico_nombre,
        })

        return jsonify({'success': True, 'ic_id': ic_id}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/interconsultas/pendientes ────────────────────────────────────

@interconsultas_bp.route('/api/interconsultas/pendientes')
def interconsultas_pendientes():
    """List pending interconsultas, optionally filtered by especialidad.

    Sorted by prioridad (stat first) then fecha_solicitud.
    """
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    especialidad = request.args.get('especialidad', '').strip()

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        q = ("SELECT ic.*, p.nombres, p.apellidos, p.num_doc as p_num_doc, "
             "a.triage_nivel "
             "FROM interconsultas ic "
             "LEFT JOIN pacientes p ON ic.paciente_id=p.id "
             "LEFT JOIN admisiones a ON ic.admision_id=a.id "
             "WHERE ic.estado IN ('solicitada','aceptada')")
        params = []

        if especialidad:
            q += " AND ic.especialidad_solicitada=?"
            params.append(especialidad)

        q += (" ORDER BY "
              "CASE ic.prioridad "
              "  WHEN 'stat' THEN 0 "
              "  WHEN 'urgente' THEN 1 "
              "  WHEN 'rutina' THEN 2 "
              "  ELSE 3 END, "
              "ic.fecha_solicitud ASC")

        interconsultas = core.rows(cur, core.adapt(q, db), params)
        return jsonify({'interconsultas': interconsultas, 'total': len(interconsultas)})
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/interconsultas/por-hc/<hc_id> ────────────────────────────────

@interconsultas_bp.route('/api/interconsultas/por-hc/<int:hc_id>')
def interconsultas_por_hc(hc_id):
    """List all interconsultas for a clinical history."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        interconsultas = core.rows(cur, core.adapt(
            "SELECT * FROM interconsultas WHERE hc_id=? ORDER BY creado_en DESC", db),
            (hc_id,))
        return jsonify({'interconsultas': interconsultas, 'total': len(interconsultas)})
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/interconsultas/<ic_id>/aceptar ───────────────────────────────

@interconsultas_bp.route('/api/interconsultas/<int:ic_id>/aceptar', methods=['PUT'])
def interconsultas_aceptar(ic_id):
    """Specialist accepts an interconsulta."""
    if not _has_permiso_interconsultas():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        ic = core.row(cur, core.adapt(
            "SELECT * FROM interconsultas WHERE id=?", db), (ic_id,))
        if not ic:
            return jsonify({'error': 'Interconsulta no encontrada'}), 404

        medico_id = session.get('user_id')
        medico_nombre = session.get('usuario', 'sistema')

        cur.execute(core.adapt(
            f"UPDATE interconsultas SET estado='aceptada', "
            f"medico_interconsultante_id=?, medico_interconsultante=?, "
            f"fecha_aceptacion={core.NOW(db)} WHERE id=?", db),
            (medico_id, medico_nombre, ic_id))
        conn.commit()

        # Timeline
        _add_timeline(cur, db, ic['admision_id'], 'interconsulta_aceptada',
                      f"Interconsulta #{ic_id} a {ic.get('especialidad_solicitada', '')} "
                      f"aceptada por {medico_nombre}")
        conn.commit()

        # Audit
        core.audit(cur, db, 'interconsultas', ic_id, 'aceptar',
                   f"Aceptada por {medico_nombre}")
        conn.commit()

        # SSE broadcast
        core.sse_broadcast({
            'tipo': 'interconsulta_aceptada',
            'ic_id': ic_id,
            'especialidad_solicitada': ic.get('especialidad_solicitada', ''),
            'medico_interconsultante': medico_nombre,
            'medico_solicitante_id': ic.get('medico_solicitante_id'),
        })

        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/interconsultas/<ic_id>/responder ─────────────────────────────

@interconsultas_bp.route('/api/interconsultas/<int:ic_id>/responder', methods=['PUT'])
def interconsultas_responder(ic_id):
    """Specialist responds to an interconsulta with findings and recommendations."""
    if not _has_permiso_interconsultas():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    respuesta = d.get('respuesta', '').strip()
    if not respuesta:
        return jsonify({'error': 'respuesta requerida'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        ic = core.row(cur, core.adapt(
            "SELECT * FROM interconsultas WHERE id=?", db), (ic_id,))
        if not ic:
            return jsonify({'error': 'Interconsulta no encontrada'}), 404

        medico_nombre = session.get('usuario', 'sistema')

        cur.execute(core.adapt(
            f"UPDATE interconsultas SET estado='respondida', "
            f"respuesta=?, recomendaciones=?, cod_cie10_respuesta=?, "
            f"fecha_respuesta={core.NOW(db)} WHERE id=?", db),
            (respuesta, d.get('recomendaciones', ''),
             d.get('cod_cie10_respuesta', ''), ic_id))
        conn.commit()

        # Timeline
        _add_timeline(cur, db, ic['admision_id'], 'interconsulta_respondida',
                      f"Interconsulta #{ic_id} a {ic.get('especialidad_solicitada', '')} "
                      f"respondida por {medico_nombre}")
        conn.commit()

        # Audit
        core.audit(cur, db, 'interconsultas', ic_id, 'responder',
                   f"Respondida por {medico_nombre}")
        conn.commit()

        # SSE broadcast to notify requesting doctor
        core.sse_broadcast({
            'tipo': 'interconsulta_respondida',
            'ic_id': ic_id,
            'especialidad_solicitada': ic.get('especialidad_solicitada', ''),
            'medico_interconsultante': medico_nombre,
            'medico_solicitante_id': ic.get('medico_solicitante_id'),
            'paciente_id': ic.get('paciente_id'),
        })

        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/interconsultas/<ic_id>/cancelar ──────────────────────────────

@interconsultas_bp.route('/api/interconsultas/<int:ic_id>/cancelar', methods=['PUT'])
def interconsultas_cancelar(ic_id):
    """Cancel an interconsulta with a reason."""
    if not _has_permiso_interconsultas():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    motivo = d.get('motivo', '').strip()
    if not motivo:
        return jsonify({'error': 'motivo requerido'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        ic = core.row(cur, core.adapt(
            "SELECT * FROM interconsultas WHERE id=?", db), (ic_id,))
        if not ic:
            return jsonify({'error': 'Interconsulta no encontrada'}), 404

        cur.execute(core.adapt(
            f"UPDATE interconsultas SET estado='cancelada' WHERE id=?", db),
            (ic_id,))
        conn.commit()

        # Timeline
        _add_timeline(cur, db, ic['admision_id'], 'interconsulta_cancelada',
                      f"Interconsulta #{ic_id} a {ic.get('especialidad_solicitada', '')} "
                      f"cancelada: {motivo}")
        conn.commit()

        # Audit
        core.audit(cur, db, 'interconsultas', ic_id, 'cancelar',
                   f"Cancelada por {session.get('usuario', '?')}: {motivo}")
        conn.commit()

        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/interconsultas/<ic_id> ───────────────────────────────────────

@interconsultas_bp.route('/api/interconsultas/<int:ic_id>')
def interconsultas_detalle(ic_id):
    """Get interconsulta detail with patient information."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        ic = core.row(cur, core.adapt(
            "SELECT ic.*, p.nombres, p.apellidos, p.tipo_doc as p_tipo_doc, "
            "p.num_doc as p_num_doc, p.fecha_nacimiento, p.genero, "
            "p.celular as p_celular, p.eps as p_eps, "
            "a.triage_nivel, a.turno "
            "FROM interconsultas ic "
            "LEFT JOIN pacientes p ON ic.paciente_id=p.id "
            "LEFT JOIN admisiones a ON ic.admision_id=a.id "
            "WHERE ic.id=?", db),
            (ic_id,))
        if not ic:
            return jsonify({'error': 'Interconsulta no encontrada'}), 404

        return jsonify({'interconsulta': ic})
    finally:
        cur.close()
        core._return_db(conn, db)
