"""
atencion_engine.py — Blueprint del módulo Atención de Arthemis Health.

Gestiona el flujo operativo del personal: enfermeras (triage), admisiones y doctores.
Cada operador inicia sesión, selecciona un puesto de atención, y atiende pacientes
según la cola de prioridad de su etapa.

Flujo urgencias:
  kiosco → (triage llama) → llamando → (asigna nivel) → triaje
         → (admisiones llama) → llamando → (admite) → admision
         → (doctor llama) → llamando → (atiende) → atendido

Endpoints:
  GET    /api/atencion/puestos            — listar puestos (filtro por tipo/modo)
  POST   /api/atencion/puestos            — crear puesto (admin)
  PUT    /api/atencion/puestos/<id>       — actualizar puesto (admin)
  DELETE /api/atencion/puestos/<id>       — desactivar puesto (admin)
  POST   /api/atencion/seleccionar-puesto — vincular puesto a sesión
  GET    /api/atencion/mi-puesto          — puesto actual de la sesión
  GET    /api/atencion/cola               — cola filtrada por etapa del puesto
  POST   /api/atencion/siguiente          — llamar al siguiente paciente
  POST   /api/atencion/devolver           — devolver paciente a cola (no se presentó)
  POST   /api/atencion/accion             — acción de etapa (triage/admitir/atender)
"""

import json
from datetime import datetime
from flask import Blueprint, request, jsonify, session

atencion_bp = Blueprint('atencion', __name__)


def _get_deps():
    import core
    return core


def _is_authenticated():
    return bool(session.get('user_id'))


def _is_admin():
    if not session.get('user_id'):
        return False
    core = _get_deps()
    permisos = core.get_user_permisos()
    return 'superadmin' in permisos or 'admisiones' in permisos


# ── Triage priority order (I=highest, V=lowest) ─────────────────────────────

TRIAGE_ORDER = {'I': 0, 'II': 1, 'III': 2, 'IV': 3, 'V': 4}

# Map puesto tipo → which estado to pull from, and which estado to set after action
STAGE_CONFIG = {
    'triage': {
        'queue_estado': 'kiosco',       # patients waiting for triage
        'action_name': 'asignar_triage',
        'next_estado': 'triaje',        # after triage is assigned
    },
    'admisiones': {
        'queue_estado': 'triaje',       # patients with triage assigned
        'action_name': 'admitir',
        'next_estado': 'admision',
    },
    'consultorio': {
        'queue_estado': 'admision',     # patients admitted, waiting for doctor
        'action_name': 'atender',
        'next_estado': 'atendido',
    },
}


# ── GET /api/atencion/puestos ───────────────────────────────────────────────

@atencion_bp.route('/api/atencion/puestos')
def atencion_puestos_list():
    """List puestos, optionally filtered by tipo and/or modo."""
    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()

    tipo = request.args.get('tipo', '').strip()
    modo = request.args.get('modo', '').strip()
    only_active = request.args.get('activo', '').strip()

    q = "SELECT * FROM puestos_atencion WHERE 1=1"
    params = []
    if tipo:
        q += " AND tipo=?"
        params.append(tipo)
    if modo:
        q += " AND modo=?"
        params.append(modo)
    if only_active == '1':
        q += " AND activo=1"
    q += " ORDER BY tipo, codigo"

    puestos = core.rows(cur, core.adapt(q, db), params)
    cur.close()
    core._return_db(conn, db)
    return jsonify({'puestos': puestos})


# ── POST /api/atencion/puestos ──────────────────────────────────────────────

@atencion_bp.route('/api/atencion/puestos', methods=['POST'])
def atencion_puestos_create():
    """Create a new puesto (admin only)."""
    core = _get_deps()
    if not _is_admin():
        return jsonify({'error': 'No autorizado'}), 401

    d = request.json or {}
    codigo = d.get('codigo', '').strip()
    nombre = d.get('nombre', '').strip()
    tipo = d.get('tipo', '').strip()

    if not codigo or not nombre or tipo not in ('triage', 'admisiones', 'consultorio'):
        return jsonify({'error': 'codigo, nombre y tipo (triage|admisiones|consultorio) requeridos'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        cur.execute(core.adapt(
            "INSERT INTO puestos_atencion(codigo,nombre,tipo,activo,modo)VALUES(?,?,?,1,?)", db),
            (codigo, nombre, tipo, d.get('modo', 'urgencias')))
        conn.commit()
        puesto = core.row(cur, core.adapt("SELECT * FROM puestos_atencion WHERE codigo=?", db), (codigo,))
        core.audit(cur, db, 'puestos_atencion', puesto['id'], 'crear', f"Puesto {nombre} creado")
        conn.commit()
        return jsonify({'success': True, 'puesto': puesto}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': f'Error: {e}'}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/atencion/puestos/<id> ──────────────────────────────────────────

@atencion_bp.route('/api/atencion/puestos/<int:puesto_id>', methods=['PUT'])
def atencion_puestos_update(puesto_id):
    """Update a puesto (admin only)."""
    core = _get_deps()
    if not _is_admin():
        return jsonify({'error': 'No autorizado'}), 401

    d = request.json or {}
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        fields, vals = [], []
        for k in ('nombre', 'tipo', 'activo', 'modo', 'codigo'):
            if k in d:
                fields.append(f"{k}=?")
                vals.append(d[k])
        if not fields:
            return jsonify({'error': 'Nada que actualizar'}), 400
        vals.append(puesto_id)
        cur.execute(core.adapt(f"UPDATE puestos_atencion SET {','.join(fields)} WHERE id=?", db), vals)
        conn.commit()
        core.audit(cur, db, 'puestos_atencion', puesto_id, 'actualizar', json.dumps(d, ensure_ascii=False))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── DELETE /api/atencion/puestos/<id> ───────────────────────────────────────

@atencion_bp.route('/api/atencion/puestos/<int:puesto_id>', methods=['DELETE'])
def atencion_puestos_delete(puesto_id):
    """Deactivate a puesto (admin only). Soft delete."""
    core = _get_deps()
    if not _is_admin():
        return jsonify({'error': 'No autorizado'}), 401

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        cur.execute(core.adapt("UPDATE puestos_atencion SET activo=0 WHERE id=?", db), (puesto_id,))
        conn.commit()
        core.audit(cur, db, 'puestos_atencion', puesto_id, 'desactivar', 'Puesto desactivado')
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/atencion/seleccionar-puesto ───────────────────────────────────

@atencion_bp.route('/api/atencion/seleccionar-puesto', methods=['POST'])
def atencion_seleccionar_puesto():
    """Bind a puesto to the current session. User must be authenticated."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    d = request.json or {}
    puesto_id = d.get('puesto_id')
    if not puesto_id:
        return jsonify({'error': 'puesto_id requerido'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        puesto = core.row(cur, core.adapt(
            "SELECT * FROM puestos_atencion WHERE id=? AND activo=1", db), (puesto_id,))
        if not puesto:
            return jsonify({'error': 'Puesto no encontrado o inactivo'}), 404

        session['puesto_id'] = puesto['id']
        session['puesto_codigo'] = puesto['codigo']
        session['puesto_nombre'] = puesto['nombre']
        session['puesto_tipo'] = puesto['tipo']
        session['puesto_modo'] = puesto.get('modo', 'urgencias')

        core.audit(cur, db, 'puestos_atencion', puesto_id, 'seleccionar',
                   f"{session.get('usuario', '?')} seleccionó {puesto['nombre']}")
        conn.commit()

        return jsonify({
            'success': True,
            'puesto': puesto,
        })
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/atencion/mi-puesto ─────────────────────────────────────────────

@atencion_bp.route('/api/atencion/mi-puesto')
def atencion_mi_puesto():
    """Return the puesto bound to the current session."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    if not session.get('puesto_id'):
        return jsonify({'puesto': None})

    return jsonify({
        'puesto': {
            'id': session['puesto_id'],
            'codigo': session.get('puesto_codigo'),
            'nombre': session.get('puesto_nombre'),
            'tipo': session.get('puesto_tipo'),
            'modo': session.get('puesto_modo'),
        }
    })


# ── GET /api/atencion/cola ──────────────────────────────────────────────────

@atencion_bp.route('/api/atencion/cola')
def atencion_cola():
    """Queue filtered by the stage corresponding to the puesto type.
    Also returns the currently-being-attended patient for this puesto (if any)."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    puesto_tipo = session.get('puesto_tipo') or request.args.get('tipo', '')
    puesto_id = session.get('puesto_id')

    if not puesto_tipo:
        return jsonify({'error': 'No hay puesto seleccionado'}), 400

    stage = STAGE_CONFIG.get(puesto_tipo)
    if not stage:
        return jsonify({'error': f'Tipo de puesto inválido: {puesto_tipo}'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    T = core.TODAY(db)

    # Get queue: patients in the estado this puesto pulls from
    queue_estado = stage['queue_estado']
    admisiones = core.rows(cur, core.adapt(
        f"SELECT a.id, a.id_adm, a.turno, a.turno_tipo, a.estado, a.nombre_temp, "
        f"a.doc_num_temp, a.servicio_nombre, a.color_alerta, a.creado_en, "
        f"a.triage_nivel, a.triage_ts, a.triage_enfermera, a.triage_notas, "
        f"a.destino, a.llamado_count, a.puesto_id, "
        f"p.nombres, p.apellidos, p.celular, p.eps, p.tipo_doc, p.num_doc "
        f"FROM admisiones a LEFT JOIN pacientes p ON a.paciente_id=p.id "
        f"WHERE DATE(a.creado_en)={T} AND a.estado=? "
        f"ORDER BY a.id", db), (queue_estado,))

    # Build result with sorting
    cola = []
    for a in admisiones:
        nombre = a.get('nombre_temp') or ''
        if a.get('nombres'):
            nombre = f"{a['nombres']} {a.get('apellidos', '')}".strip()
        cola.append({
            'id': a['id'],
            'id_adm': a['id_adm'],
            'turno': a['turno'],
            'turno_tipo': a.get('turno_tipo', 'general'),
            'estado': a['estado'],
            'nombre': nombre,
            'doc_num': a.get('num_doc') or a.get('doc_num_temp', ''),
            'doc_tipo': a.get('tipo_doc') or 'CC',
            'servicio': a.get('servicio_nombre', ''),
            'color_alerta': a.get('color_alerta', 'yellow'),
            'creado_en': a.get('creado_en', ''),
            'triage_nivel': a.get('triage_nivel'),
            'triage_ts': a.get('triage_ts'),
            'triage_enfermera': a.get('triage_enfermera'),
            'triage_notas': a.get('triage_notas', ''),
            'destino': a.get('destino', ''),
            'llamado_count': a.get('llamado_count', 0),
            'prioritario': a.get('turno_tipo') == 'preferencial',
            'celular': a.get('celular', ''),
            'eps': a.get('eps', ''),
        })

    # Sort: preferenciales first, then by triage priority (if available), then by arrival
    def sort_key(r):
        pref = 0 if r['prioritario'] else 1
        triage = TRIAGE_ORDER.get(r.get('triage_nivel') or 'V', 5)
        return (pref, triage, r.get('creado_en', ''))
    cola.sort(key=sort_key)

    # Get the patient currently being called/attended by THIS puesto
    actual = None
    if puesto_id:
        a = core.row(cur, core.adapt(
            f"SELECT a.id, a.id_adm, a.turno, a.turno_tipo, a.estado, a.nombre_temp, "
            f"a.doc_num_temp, a.servicio_nombre, a.color_alerta, a.creado_en, "
            f"a.triage_nivel, a.triage_ts, a.triage_enfermera, a.triage_notas, "
            f"a.destino, a.llamado_count, "
            f"p.nombres, p.apellidos, p.celular, p.eps, p.tipo_doc, p.num_doc "
            f"FROM admisiones a LEFT JOIN pacientes p ON a.paciente_id=p.id "
            f"WHERE DATE(a.creado_en)={T} AND a.estado='llamando' AND a.puesto_id=?", db),
            (puesto_id,))
        if a:
            nombre = a.get('nombre_temp') or ''
            if a.get('nombres'):
                nombre = f"{a['nombres']} {a.get('apellidos', '')}".strip()
            actual = {
                'id': a['id'],
                'id_adm': a['id_adm'],
                'turno': a['turno'],
                'turno_tipo': a.get('turno_tipo', 'general'),
                'nombre': nombre,
                'doc_num': a.get('num_doc') or a.get('doc_num_temp', ''),
                'doc_tipo': a.get('tipo_doc') or 'CC',
                'servicio': a.get('servicio_nombre', ''),
                'triage_nivel': a.get('triage_nivel'),
                'triage_notas': a.get('triage_notas', ''),
                'destino': a.get('destino', ''),
                'llamado_count': a.get('llamado_count', 0),
                'celular': a.get('celular', ''),
                'eps': a.get('eps', ''),
            }

    cur.close()
    core._return_db(conn, db)
    return jsonify({
        'cola': cola,
        'actual': actual,
        'total': len(cola),
        'puesto_tipo': puesto_tipo,
        'queue_estado': stage['queue_estado'],
    })


# ── POST /api/atencion/siguiente ────────────────────────────────────────────

@atencion_bp.route('/api/atencion/siguiente', methods=['POST'])
def atencion_siguiente():
    """Call the next patient in the queue for this puesto's stage.
    Auto-selects by priority: preferencial > triage level > arrival time.
    Sets estado='llamando', destino=puesto name, puesto_id=this puesto."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    puesto_tipo = session.get('puesto_tipo')
    puesto_id = session.get('puesto_id')
    puesto_nombre = session.get('puesto_nombre', '')

    if not puesto_tipo or not puesto_id:
        return jsonify({'error': 'No hay puesto seleccionado'}), 400

    stage = STAGE_CONFIG.get(puesto_tipo)
    if not stage:
        return jsonify({'error': f'Tipo de puesto inválido: {puesto_tipo}'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    T = core.TODAY(db)

    try:
        # If this puesto already has a patient in 'llamando', return them to queue
        cur.execute(core.adapt(
            f"UPDATE admisiones SET estado=?, puesto_id=NULL, destino=NULL "
            f"WHERE estado='llamando' AND puesto_id=? AND DATE(creado_en)={T}", db),
            (stage['queue_estado'], puesto_id))
        conn.commit()

        # Find next patient in queue, sorted by priority
        queue_estado = stage['queue_estado']

        # Build ORDER BY: preferencial first, then triage priority, then arrival
        order = (
            "CASE WHEN a.turno_tipo='preferencial' THEN 0 ELSE 1 END, "
            "CASE a.triage_nivel "
            "WHEN 'I' THEN 0 WHEN 'II' THEN 1 WHEN 'III' THEN 2 "
            "WHEN 'IV' THEN 3 WHEN 'V' THEN 4 ELSE 5 END, "
            "a.creado_en"
        )

        siguiente = core.row(cur, core.adapt(
            f"SELECT a.id, a.turno, a.nombre_temp, a.triage_nivel, a.turno_tipo, "
            f"a.servicio_nombre, a.color_alerta, a.doc_num_temp, "
            f"p.nombres, p.apellidos "
            f"FROM admisiones a LEFT JOIN pacientes p ON a.paciente_id=p.id "
            f"WHERE DATE(a.creado_en)={T} AND a.estado=? "
            f"ORDER BY {order} LIMIT 1", db), (queue_estado,))

        if not siguiente:
            cur.close()
            core._return_db(conn, db)
            return jsonify({'success': False, 'message': 'No hay pacientes en espera'})

        turno_id = siguiente['id']

        # Update: set to llamando, assign puesto
        cur.execute(core.adapt(
            f"UPDATE admisiones SET estado='llamando', fecha_llamado={core.NOW(db)}, "
            f"destino=?, puesto_id=?, llamado_count=1 WHERE id=?", db),
            (puesto_nombre, puesto_id, turno_id))
        conn.commit()

        # Get full updated info
        a = core.row(cur, core.adapt(
            "SELECT a.*, p.nombres, p.apellidos, p.celular, p.eps, p.tipo_doc, p.num_doc "
            "FROM admisiones a LEFT JOIN pacientes p ON a.paciente_id=p.id WHERE a.id=?", db),
            (turno_id,))

        nombre = a.get('nombre_temp') or ''
        if a.get('nombres'):
            nombre = f"{a['nombres']} {a.get('apellidos', '')}".strip()

        core.audit(cur, db, 'admisiones', turno_id, 'llamar_siguiente',
                   f"Turno {a['turno']} llamado a {puesto_nombre} por {session.get('usuario', '?')}")
        conn.commit()

        # SSE broadcast so TV and other views update
        core.sse_broadcast({
            'tipo': 'llamar_turno',
            'turno': a['turno'],
            'nombre': nombre,
            'servicio': a.get('servicio_nombre', ''),
            'turno_tipo': a.get('turno_tipo', 'general'),
            'destino': puesto_nombre,
            'llamado_count': 1,
            'triage_nivel': a.get('triage_nivel'),
            'id': a['id'],
            'puesto': puesto_nombre,
        })

        return jsonify({
            'success': True,
            'paciente': {
                'id': a['id'],
                'id_adm': a.get('id_adm'),
                'turno': a['turno'],
                'nombre': nombre,
                'doc_num': a.get('num_doc') or a.get('doc_num_temp', ''),
                'doc_tipo': a.get('tipo_doc') or 'CC',
                'servicio': a.get('servicio_nombre', ''),
                'triage_nivel': a.get('triage_nivel'),
                'triage_notas': a.get('triage_notas', ''),
                'turno_tipo': a.get('turno_tipo', 'general'),
                'color_alerta': a.get('color_alerta', 'yellow'),
                'celular': a.get('celular', ''),
                'eps': a.get('eps', ''),
                'destino': puesto_nombre,
                'llamado_count': 1,
            },
        })

    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/atencion/re-llamar ────────────────────────────────────────────

@atencion_bp.route('/api/atencion/re-llamar', methods=['POST'])
def atencion_rellamar():
    """Re-call the current patient (increment llamado_count, re-broadcast SSE)."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    d = request.json or {}
    turno_id = d.get('id')
    puesto_id = session.get('puesto_id')
    puesto_nombre = session.get('puesto_nombre', '')

    if not turno_id:
        return jsonify({'error': 'ID requerido'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        current = core.row(cur, core.adapt(
            "SELECT llamado_count FROM admisiones WHERE id=? AND estado='llamando'", db),
            (turno_id,))
        if not current:
            return jsonify({'error': 'Turno no está en estado llamando'}), 400

        new_count = (current.get('llamado_count') or 0) + 1
        cur.execute(core.adapt(
            "UPDATE admisiones SET llamado_count=? WHERE id=?", db),
            (new_count, turno_id))
        conn.commit()

        a = core.row(cur, core.adapt(
            "SELECT a.*, p.nombres, p.apellidos FROM admisiones a "
            "LEFT JOIN pacientes p ON a.paciente_id=p.id WHERE a.id=?", db),
            (turno_id,))

        nombre = a.get('nombre_temp') or ''
        if a.get('nombres'):
            nombre = f"{a['nombres']} {a.get('apellidos', '')}".strip()

        core.sse_broadcast({
            'tipo': 'llamar_turno',
            'turno': a['turno'],
            'nombre': nombre,
            'servicio': a.get('servicio_nombre', ''),
            'turno_tipo': a.get('turno_tipo', 'general'),
            'destino': puesto_nombre,
            'llamado_count': new_count,
            'triage_nivel': a.get('triage_nivel'),
            'id': a['id'],
            'puesto': puesto_nombre,
        })

        return jsonify({'success': True, 'llamado_count': new_count})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/atencion/devolver ─────────────────────────────────────────────

@atencion_bp.route('/api/atencion/devolver', methods=['POST'])
def atencion_devolver():
    """Return a patient to the queue (no se presentó). Resets to previous state."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    d = request.json or {}
    turno_id = d.get('id')
    puesto_tipo = session.get('puesto_tipo')

    if not turno_id:
        return jsonify({'error': 'ID requerido'}), 400

    stage = STAGE_CONFIG.get(puesto_tipo, {})
    prev_estado = stage.get('queue_estado', 'kiosco')

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        cur.execute(core.adapt(
            "UPDATE admisiones SET estado=?, puesto_id=NULL, destino=NULL WHERE id=?", db),
            (prev_estado, turno_id))
        conn.commit()
        core.audit(cur, db, 'admisiones', turno_id, 'devolver',
                   f"Devuelto a cola ({prev_estado}) — no se presentó")
        conn.commit()

        core.sse_broadcast({'tipo': 'turno_devuelto', 'id': turno_id, 'estado': prev_estado})
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/atencion/accion ───────────────────────────────────────────────

@atencion_bp.route('/api/atencion/accion', methods=['POST'])
def atencion_accion():
    """Perform the stage-specific action on the current patient.

    For triage:     expects {id, nivel (I-V), notas?} → sets triage, moves to 'triaje'
    For admisiones: expects {id} → moves to 'admision'
    For consultorio: expects {id} → moves to 'atendido'
    """
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    d = request.json or {}
    turno_id = d.get('id')
    puesto_tipo = session.get('puesto_tipo')
    puesto_nombre = session.get('puesto_nombre', '')

    if not turno_id:
        return jsonify({'error': 'ID requerido'}), 400
    if not puesto_tipo:
        return jsonify({'error': 'No hay puesto seleccionado'}), 400

    stage = STAGE_CONFIG.get(puesto_tipo)
    if not stage:
        return jsonify({'error': f'Tipo de puesto inválido: {puesto_tipo}'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()

    try:
        if puesto_tipo == 'triage':
            # Assign triage level
            nivel = d.get('nivel', '').strip().upper()
            if nivel not in ('I', 'II', 'III', 'IV', 'V'):
                return jsonify({'error': 'Nivel de triage inválido (I-V)'}), 400

            notas = d.get('notas', '')
            enfermera = session.get('usuario', 'Enfermería')

            cur.execute(core.adapt(
                f"UPDATE admisiones SET estado='triaje', triage_nivel=?, triage_notas=?, "
                f"triage_ts={core.NOW(db)}, triage_enfermera=?, puesto_id=NULL WHERE id=?", db),
                (nivel, notas, enfermera, turno_id))
            conn.commit()

            core.audit(cur, db, 'admisiones', turno_id, 'asignar_triage',
                       f"Triage {nivel} por {enfermera} en {puesto_nombre}")
            conn.commit()

            # Get updated info for SSE
            a = core.row(cur, core.adapt(
                "SELECT a.turno, a.nombre_temp, p.nombres, p.apellidos "
                "FROM admisiones a LEFT JOIN pacientes p ON a.paciente_id=p.id WHERE a.id=?", db),
                (turno_id,))
            nombre = ''
            turno = ''
            if a:
                nombre = a.get('nombre_temp') or ''
                if a.get('nombres'):
                    nombre = f"{a['nombres']} {a.get('apellidos', '')}".strip()
                turno = a['turno']

            core.sse_broadcast({
                'tipo': 'triage_asignado',
                'turno': turno,
                'nombre': nombre,
                'triage_nivel': nivel,
                'id': turno_id,
            })

            return jsonify({'success': True, 'triage_nivel': nivel, 'turno': turno})

        elif puesto_tipo == 'admisiones':
            # Move to admision
            cur.execute(core.adapt(
                f"UPDATE admisiones SET estado='admision', "
                f"fecha_admision_inicio={core.NOW(db)}, puesto_id=NULL WHERE id=?", db),
                (turno_id,))
            conn.commit()
            core.audit(cur, db, 'admisiones', turno_id, 'admitir',
                       f"Admitido por {session.get('usuario', '?')} en {puesto_nombre}")
            conn.commit()

            core.sse_broadcast({'tipo': 'turno_admision', 'id': turno_id})
            return jsonify({'success': True})

        elif puesto_tipo == 'consultorio':
            # Mark as attended
            cur.execute(core.adapt(
                f"UPDATE admisiones SET estado='atendido', "
                f"fecha_salida={core.NOW(db)}, puesto_id=NULL WHERE id=?", db),
                (turno_id,))
            conn.commit()
            core.audit(cur, db, 'admisiones', turno_id, 'atender',
                       f"Atendido por {session.get('usuario', '?')} en {puesto_nombre}")
            conn.commit()

            core.sse_broadcast({'tipo': 'turno_atendido', 'id': turno_id})
            return jsonify({'success': True})

    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)
