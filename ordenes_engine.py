"""
ordenes_engine.py — Blueprint del módulo Órdenes Médicas de Arthemis Health.

Gestiona el ciclo de vida completo de órdenes médicas (laboratorio, imágenes, etc.):
  - Creación de órdenes por médico tratante
  - Cola de trabajo por servicio (laboratorio, imágenes, rx)
  - Aceptación y procesamiento de órdenes
  - Registro y validación de resultados
  - Cancelación con motivo
  - Estadísticas de producción diaria

Endpoints:
  POST   /api/ordenes/crear                    — crear orden médica
  GET    /api/ordenes/por-hc/<hc_id>           — listar órdenes de una HC
  GET    /api/ordenes/cola/<servicio>           — cola de órdenes pendientes por servicio
  PUT    /api/ordenes/<orden_id>/aceptar        — aceptar orden
  PUT    /api/ordenes/<orden_id>/en-proceso     — marcar orden en proceso
  POST   /api/ordenes/<orden_id>/resultado      — registrar resultado(s)
  PUT    /api/ordenes/<orden_id>/validar        — validar resultados
  GET    /api/ordenes/<orden_id>/resultados     — obtener resultados de una orden
  PUT    /api/ordenes/<orden_id>/cancelar       — cancelar orden
  GET    /api/ordenes/estadisticas              — estadísticas del día
"""

from flask import Blueprint, request, jsonify, session

ordenes_bp = Blueprint('ordenes', __name__)


def _get_deps():
    import core
    return core


def _D(col, db):
    """DATE extraction compatible with both PG and SQLite (Colombia TZ)."""
    return f"CAST({col} AS DATE)" if db == 'pg' else f"DATE({col},'-5 hours')"


def _is_authenticated():
    return bool(session.get('user_id'))


def _has_permiso_ordenes():
    """Check if user has permission for ordenes module."""
    if not session.get('user_id'):
        return False
    core = _get_deps()
    permisos = core.get_user_permisos()
    return bool({'superadmin', 'ordenes', 'laboratorio', 'historia_clinica'} & set(permisos))


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


# ── Prioridad ordering helper ─────────────────────────────────────────────

PRIORIDAD_ORDEN = {'stat': 0, 'urgente': 1, 'rutina': 2}


# ── POST /api/ordenes/crear ───────────────────────────────────────────────

@ordenes_bp.route('/api/ordenes/crear', methods=['POST'])
def ordenes_crear():
    """Create a new medical order (lab, imaging, etc.)."""
    if not _has_permiso_ordenes():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    required = ['hc_id', 'admision_id', 'paciente_id', 'tipo_orden', 'nombre_estudio']
    if not all(d.get(k) for k in required):
        return jsonify({'error': 'Campos requeridos: hc_id, admision_id, paciente_id, tipo_orden, nombre_estudio'}), 400

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
        servicio_destino = d.get('servicio_destino', d['tipo_orden'])

        cur.execute(core.adapt(
            "INSERT INTO ordenes_medicas(hc_id,admision_id,paciente_id,tipo_orden,"
            "cod_cups,nombre_estudio,cantidad,prioridad,indicacion_clinica,"
            "diagnostico_asociado,instrucciones,estado,servicio_destino,"
            "medico_ordena_id,medico_ordena)"
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", db),
            (d['hc_id'], d['admision_id'], d['paciente_id'], d['tipo_orden'],
             d.get('cod_cups', ''), d['nombre_estudio'], d.get('cantidad', 1),
             prioridad, d.get('indicacion_clinica', ''),
             d.get('diagnostico_asociado', ''), d.get('instrucciones', ''),
             'solicitada', servicio_destino,
             medico_id, medico_nombre))
        conn.commit()

        # Get the new order id
        orden = core.row(cur, core.adapt(
            "SELECT id FROM ordenes_medicas WHERE hc_id=? AND admision_id=? "
            "AND nombre_estudio=? AND medico_ordena_id=? "
            "ORDER BY id DESC LIMIT 1", db),
            (d['hc_id'], d['admision_id'], d['nombre_estudio'], medico_id))
        orden_id = orden['id'] if orden else None

        # Timeline
        _add_timeline(cur, db, d['admision_id'], 'orden_creada',
                      f"Orden {d['tipo_orden']}: {d['nombre_estudio']} (prioridad: {prioridad})")
        conn.commit()

        # Audit
        core.audit(cur, db, 'ordenes_medicas', orden_id, 'crear',
                   f"Orden {d['tipo_orden']} creada por {medico_nombre}")
        conn.commit()

        # SSE broadcast
        nombre_paciente = f"{paciente.get('nombres', '')} {paciente.get('apellidos', '')}".strip()
        core.sse_broadcast({
            'tipo': 'orden_nueva',
            'orden_id': orden_id,
            'tipo_orden': d['tipo_orden'],
            'nombre_estudio': d['nombre_estudio'],
            'paciente': nombre_paciente,
            'prioridad': prioridad,
            'servicio_destino': servicio_destino,
        })

        return jsonify({'success': True, 'orden_id': orden_id}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/ordenes/por-hc/<hc_id> ───────────────────────────────────────

@ordenes_bp.route('/api/ordenes/por-hc/<int:hc_id>')
def ordenes_por_hc(hc_id):
    """List all medical orders for a clinical history, including result count per order."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        ordenes = core.rows(cur, core.adapt(
            "SELECT o.*, "
            "(SELECT COUNT(*) FROM orden_resultados r WHERE r.orden_id=o.id) as resultados_count "
            "FROM ordenes_medicas o WHERE o.hc_id=? "
            "ORDER BY o.creado_en DESC", db),
            (hc_id,))
        return jsonify({'ordenes': ordenes, 'total': len(ordenes)})
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/ordenes/cola/<servicio> ──────────────────────────────────────

@ordenes_bp.route('/api/ordenes/cola/<servicio>')
def ordenes_cola(servicio):
    """Get the pending orders queue for a service.

    servicio: laboratorio | imagenes | rx | all
    Returns orders with estado in (solicitada, aceptada, en_proceso),
    sorted by prioridad (stat first) then creado_en.
    """
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        q = ("SELECT o.*, p.nombres, p.apellidos, p.num_doc as p_num_doc, "
             "a.triage_nivel "
             "FROM ordenes_medicas o "
             "LEFT JOIN pacientes p ON o.paciente_id=p.id "
             "LEFT JOIN admisiones a ON o.admision_id=a.id "
             "WHERE o.estado IN ('solicitada','aceptada','en_proceso')")
        params = []

        if servicio != 'all':
            q += " AND o.servicio_destino=?"
            params.append(servicio)

        q += (" ORDER BY "
              "CASE o.prioridad "
              "  WHEN 'stat' THEN 0 "
              "  WHEN 'urgente' THEN 1 "
              "  WHEN 'rutina' THEN 2 "
              "  ELSE 3 END, "
              "o.creado_en ASC")

        ordenes = core.rows(cur, core.adapt(q, db), params)
        return jsonify({'ordenes': ordenes, 'total': len(ordenes), 'servicio': servicio})
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/ordenes/<orden_id>/aceptar ───────────────────────────────────

@ordenes_bp.route('/api/ordenes/<int:orden_id>/aceptar', methods=['PUT'])
def ordenes_aceptar(orden_id):
    """Accept a medical order for processing."""
    if not _has_permiso_ordenes():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        orden = core.row(cur, core.adapt(
            "SELECT * FROM ordenes_medicas WHERE id=?", db), (orden_id,))
        if not orden:
            return jsonify({'error': 'Orden no encontrada'}), 404

        cur.execute(core.adapt(
            f"UPDATE ordenes_medicas SET estado='aceptada', actualizado_en={core.NOW(db)} "
            f"WHERE id=?", db),
            (orden_id,))
        conn.commit()

        # Timeline
        _add_timeline(cur, db, orden['admision_id'], 'orden_aceptada',
                      f"Orden #{orden_id} ({orden.get('nombre_estudio', '')}) aceptada")
        conn.commit()

        # Audit
        core.audit(cur, db, 'ordenes_medicas', orden_id, 'aceptar',
                   f"Orden aceptada por {session.get('usuario', '?')}")
        conn.commit()

        # SSE broadcast
        core.sse_broadcast({
            'tipo': 'orden_aceptada',
            'orden_id': orden_id,
            'tipo_orden': orden.get('tipo_orden', ''),
            'nombre_estudio': orden.get('nombre_estudio', ''),
        })

        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/ordenes/<orden_id>/en-proceso ────────────────────────────────

@ordenes_bp.route('/api/ordenes/<int:orden_id>/en-proceso', methods=['PUT'])
def ordenes_en_proceso(orden_id):
    """Mark an order as in process."""
    if not _has_permiso_ordenes():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        orden = core.row(cur, core.adapt(
            "SELECT * FROM ordenes_medicas WHERE id=?", db), (orden_id,))
        if not orden:
            return jsonify({'error': 'Orden no encontrada'}), 404

        cur.execute(core.adapt(
            f"UPDATE ordenes_medicas SET estado='en_proceso', actualizado_en={core.NOW(db)} "
            f"WHERE id=?", db),
            (orden_id,))
        conn.commit()

        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/ordenes/<orden_id>/resultado ────────────────────────────────

@ordenes_bp.route('/api/ordenes/<int:orden_id>/resultado', methods=['POST'])
def ordenes_resultado(orden_id):
    """Register result(s) for a medical order.

    Input: { resultados: [ {parametro, valor, unidad, rango_referencia, fuera_rango, observaciones, tipo_resultado}, ... ] }
    """
    if not _has_permiso_ordenes():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    resultados = d.get('resultados', [])
    if not resultados:
        return jsonify({'error': 'Se requiere al menos un resultado'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        orden = core.row(cur, core.adapt(
            "SELECT * FROM ordenes_medicas WHERE id=?", db), (orden_id,))
        if not orden:
            return jsonify({'error': 'Orden no encontrada'}), 404

        procesado_por = session.get('usuario', 'sistema')

        for r in resultados:
            cur.execute(core.adapt(
                "INSERT INTO orden_resultados(orden_id,tipo_resultado,parametro,valor,"
                "unidad,rango_referencia,fuera_rango,observaciones,archivo_url,"
                "archivo_tipo,procesado_por)"
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)", db),
                (orden_id, r.get('tipo_resultado', 'numerico'),
                 r.get('parametro', ''), r.get('valor', ''),
                 r.get('unidad', ''), r.get('rango_referencia', ''),
                 1 if r.get('fuera_rango') else 0,
                 r.get('observaciones', ''),
                 r.get('archivo_url', ''), r.get('archivo_tipo', ''),
                 procesado_por))

        # Update order estado
        cur.execute(core.adapt(
            f"UPDATE ordenes_medicas SET estado='con_resultado', actualizado_en={core.NOW(db)} "
            f"WHERE id=?", db),
            (orden_id,))
        conn.commit()

        # Timeline
        _add_timeline(cur, db, orden['admision_id'], 'resultado_registrado',
                      f"Resultado(s) registrado(s) para orden #{orden_id} "
                      f"({orden.get('nombre_estudio', '')}): {len(resultados)} parámetro(s)")
        conn.commit()

        # Audit
        core.audit(cur, db, 'ordenes_medicas', orden_id, 'registrar_resultado',
                   f"{len(resultados)} resultado(s) registrado(s) por {procesado_por}")
        conn.commit()

        # SSE broadcast to notify doctor
        core.sse_broadcast({
            'tipo': 'resultado_disponible',
            'orden_id': orden_id,
            'tipo_orden': orden.get('tipo_orden', ''),
            'nombre_estudio': orden.get('nombre_estudio', ''),
            'paciente_id': orden.get('paciente_id'),
            'medico_ordena_id': orden.get('medico_ordena_id'),
            'resultados_count': len(resultados),
        })

        return jsonify({'success': True, 'resultados_registrados': len(resultados)})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/ordenes/<orden_id>/validar ───────────────────────────────────

@ordenes_bp.route('/api/ordenes/<int:orden_id>/validar', methods=['PUT'])
def ordenes_validar(orden_id):
    """Validate all results for an order (sets validado_por on all resultados)."""
    if not _has_permiso_ordenes():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        orden = core.row(cur, core.adapt(
            "SELECT * FROM ordenes_medicas WHERE id=?", db), (orden_id,))
        if not orden:
            return jsonify({'error': 'Orden no encontrada'}), 404

        validado_por = session.get('usuario', 'sistema')
        cur.execute(core.adapt(
            "UPDATE orden_resultados SET validado_por=? WHERE orden_id=?", db),
            (validado_por, orden_id))
        conn.commit()

        return jsonify({'success': True, 'validado_por': validado_por})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/ordenes/<orden_id>/resultados ────────────────────────────────

@ordenes_bp.route('/api/ordenes/<int:orden_id>/resultados')
def ordenes_resultados(orden_id):
    """Get all results for a medical order."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        orden = core.row(cur, core.adapt(
            "SELECT * FROM ordenes_medicas WHERE id=?", db), (orden_id,))
        if not orden:
            return jsonify({'error': 'Orden no encontrada'}), 404

        resultados = core.rows(cur, core.adapt(
            "SELECT * FROM orden_resultados WHERE orden_id=? ORDER BY id", db),
            (orden_id,))
        return jsonify({
            'orden': orden,
            'resultados': resultados,
            'total': len(resultados),
        })
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/ordenes/<orden_id>/cancelar ──────────────────────────────────

@ordenes_bp.route('/api/ordenes/<int:orden_id>/cancelar', methods=['PUT'])
def ordenes_cancelar(orden_id):
    """Cancel a medical order with a reason."""
    if not _has_permiso_ordenes():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    motivo = d.get('motivo', '').strip()
    if not motivo:
        return jsonify({'error': 'motivo requerido'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        orden = core.row(cur, core.adapt(
            "SELECT * FROM ordenes_medicas WHERE id=?", db), (orden_id,))
        if not orden:
            return jsonify({'error': 'Orden no encontrada'}), 404

        cur.execute(core.adapt(
            f"UPDATE ordenes_medicas SET estado='cancelada', "
            f"instrucciones=COALESCE(instrucciones,'') || ' [CANCELADA: ' || ? || ']', "
            f"actualizado_en={core.NOW(db)} WHERE id=?", db),
            (motivo, orden_id))
        conn.commit()

        # Timeline
        _add_timeline(cur, db, orden['admision_id'], 'orden_cancelada',
                      f"Orden #{orden_id} ({orden.get('nombre_estudio', '')}) cancelada: {motivo}")
        conn.commit()

        # Audit
        core.audit(cur, db, 'ordenes_medicas', orden_id, 'cancelar',
                   f"Cancelada por {session.get('usuario', '?')}: {motivo}")
        conn.commit()

        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/ordenes/estadisticas ─────────────────────────────────────────

@ordenes_bp.route('/api/ordenes/estadisticas')
def ordenes_estadisticas():
    """Lab/imaging statistics for today: counts by estado, by tipo_orden, avg processing time."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    T = core.TODAY(db)

    try:
        # Total today
        total_row = core.row(cur, core.adapt(
            f"SELECT COUNT(*) as total FROM ordenes_medicas WHERE {_D('creado_en',db)}={T}", db))
        total = total_row['total'] if total_row else 0

        # By estado
        estados = core.rows(cur, core.adapt(
            f"SELECT estado, COUNT(*) as cantidad FROM ordenes_medicas "
            f"WHERE {_D('creado_en',db)}={T} GROUP BY estado", db))
        por_estado = {e['estado']: e['cantidad'] for e in estados}

        # By tipo_orden
        tipos = core.rows(cur, core.adapt(
            f"SELECT tipo_orden, COUNT(*) as cantidad FROM ordenes_medicas "
            f"WHERE {_D('creado_en',db)}={T} GROUP BY tipo_orden", db))
        por_tipo = {t['tipo_orden']: t['cantidad'] for t in tipos}

        # Average processing time (from solicitada to con_resultado)
        if db == 'sqlite':
            tiempo = core.row(cur, core.adapt(
                f"SELECT AVG((julianday(actualizado_en) - julianday(creado_en)) * 1440) "
                f"as avg_minutos FROM ordenes_medicas "
                f"WHERE {_D('creado_en',db)}={T} AND estado='con_resultado'", db))
        else:
            tiempo = core.row(cur, core.adapt(
                f"SELECT AVG(EXTRACT(EPOCH FROM (actualizado_en::timestamp - creado_en)) / 60) "
                f"as avg_minutos FROM ordenes_medicas "
                f"WHERE {_D('creado_en',db)}={T} AND estado='con_resultado'", db))

        avg_minutos = round(tiempo.get('avg_minutos') or 0, 1) if tiempo else 0

        return jsonify({
            'total_hoy': total,
            'por_estado': por_estado,
            'por_tipo_orden': por_tipo,
            'tiempo_promedio_minutos': avg_minutos,
        })
    finally:
        cur.close()
        core._return_db(conn, db)
