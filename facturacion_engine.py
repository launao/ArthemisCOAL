"""
facturacion_engine.py — Blueprint del módulo Facturación de Arthemis Health.

Gestiona la pre-facturación de servicios de urgencias:
  - Generación automática de pre-factura a partir de la admisión
  - Ítems de servicio (consultas, órdenes, prescripciones, interconsultas)
  - Aprobación de pre-facturas
  - Resumen diario de facturación
  - Generación de RIPS JSON (Resolución 2275/2023)

Endpoints:
  POST   /api/facturacion/generar/<admision_id>    — generar/regenerar pre-factura
  GET    /api/facturacion/pre-factura/<admision_id> — obtener pre-factura con ítems
  POST   /api/facturacion/item                     — agregar ítem manual
  PUT    /api/facturacion/item/<item_id>           — editar ítem
  DELETE /api/facturacion/item/<item_id>           — eliminar ítem
  PUT    /api/facturacion/aprobar/<pf_id>          — aprobar pre-factura
  GET    /api/facturacion/pendientes               — listar pre-facturas pendientes
  GET    /api/facturacion/resumen-diario           — resumen diario de facturación
  POST   /api/facturacion/rips/<admision_id>       — generar JSON RIPS 2275/2023
"""

import json
from datetime import datetime
from flask import Blueprint, request, jsonify, session

facturacion_bp = Blueprint('facturacion', __name__)


def _get_deps():
    import core
    return core


def _is_authenticated():
    return bool(session.get('user_id'))


def _has_permiso_facturacion():
    if not session.get('user_id'):
        return False
    core = _get_deps()
    permisos = core.get_user_permisos()
    return 'superadmin' in permisos or 'facturacion' in permisos


# ── Tarifas por defecto ──────────────────────────────────────────────────────

TARIFAS_DEFAULT = {
    'consulta_urgencias': 150000,
    'laboratorio_min': 25000,
    'laboratorio_max': 80000,
    'laboratorio_default': 40000,
    'imagen_min': 60000,
    'imagen_max': 150000,
    'imagen_default': 90000,
    'interconsulta': 120000,
}


def _tarifa_orden(tipo_orden):
    """Return default tariff for an order type."""
    tipo = (tipo_orden or '').lower()
    if tipo in ('laboratorio', 'lab'):
        return TARIFAS_DEFAULT['laboratorio_default']
    if tipo in ('imagen', 'imagenologia', 'radiologia'):
        return TARIFAS_DEFAULT['imagen_default']
    return TARIFAS_DEFAULT.get(tipo, 50000)


# ── Helper: recalcular totales ────────────────────────────────────────────────

def _recalcular_totales(cur, db, pre_factura_id):
    """Recalculate pre_factura totals from its items.

    subtotal = sum of all items valor_total
    total = subtotal
    total_paciente = copago (from the admission)
    total_pagador = total - copago
    """
    core = _get_deps()

    # Sum items
    subtotal_row = core.row(cur, core.adapt(
        "SELECT COALESCE(SUM(valor_total),0) as subtotal FROM pre_factura_items "
        "WHERE pre_factura_id=?", db), (pre_factura_id,))
    subtotal = subtotal_row['subtotal'] if subtotal_row else 0

    # Get copago from the admission via pre_factura
    pf = core.row(cur, core.adapt(
        "SELECT admision_id, copago FROM pre_factura WHERE id=?", db), (pre_factura_id,))
    copago = pf.get('copago', 0) if pf else 0

    total = subtotal
    total_paciente = copago
    total_pagador = total - copago if total > copago else 0

    cur.execute(core.adapt(
        "UPDATE pre_factura SET subtotal=?, total=?, total_paciente=?, total_pagador=? "
        "WHERE id=?", db),
        (subtotal, total, total_paciente, total_pagador, pre_factura_id))


# ── POST /api/facturacion/generar/<admision_id> ───────────────────────────────

@facturacion_bp.route('/api/facturacion/generar/<int:admision_id>', methods=['POST'])
def facturacion_generar(admision_id):
    """Generate or regenerate a pre-factura from all services in an admission."""
    if not _has_permiso_facturacion():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        # Verify admission exists
        adm = core.row(cur, core.adapt(
            "SELECT * FROM admisiones WHERE id=?", db), (admision_id,))
        if not adm:
            return jsonify({'error': 'Admisión no encontrada'}), 404

        paciente_id = adm.get('paciente_id')
        if not paciente_id:
            return jsonify({'error': 'Admisión sin paciente vinculado'}), 400

        # Get payer info
        pagador = core.row(cur, core.adapt(
            "SELECT * FROM pagador_validacion WHERE admision_id=? ORDER BY id DESC LIMIT 1", db),
            (admision_id,))
        tipo_pagador = pagador.get('tipo_pagador', 'particular') if pagador else 'particular'
        entidad_pagadora = pagador.get('entidad_nombre', '') if pagador else ''
        entidad_codigo = pagador.get('entidad_codigo', '') if pagador else ''
        numero_autorizacion = pagador.get('numero_autorizacion', '') if pagador else ''

        copago = adm.get('copago_calculado', 0) or 0

        # Delete existing pre_factura and items if regenerating
        existing = core.row(cur, core.adapt(
            "SELECT id FROM pre_factura WHERE admision_id=?", db), (admision_id,))
        if existing:
            cur.execute(core.adapt(
                "DELETE FROM pre_factura_items WHERE pre_factura_id=?", db),
                (existing['id'],))
            cur.execute(core.adapt(
                "DELETE FROM pre_factura WHERE id=?", db),
                (existing['id'],))
            conn.commit()

        # Create pre_factura
        cur.execute(core.adapt(
            "INSERT INTO pre_factura(admision_id,paciente_id,estado,tipo_pagador,"
            "entidad_pagadora,entidad_codigo,numero_autorizacion,copago,"
            "subtotal,total,total_paciente,total_pagador)"
            "VALUES(?,?,'borrador',?,?,?,?,?,0,0,0,0)", db),
            (admision_id, paciente_id, tipo_pagador, entidad_pagadora,
             entidad_codigo, numero_autorizacion, copago))
        conn.commit()

        pf = core.row(cur, core.adapt(
            "SELECT id FROM pre_factura WHERE admision_id=?", db), (admision_id,))
        pf_id = pf['id']

        items_added = 0

        # a) Consulta de urgencias
        cod_cups_consulta = adm.get('cod_cups') or '890701'
        cur.execute(core.adapt(
            "INSERT INTO pre_factura_items(pre_factura_id,tipo_servicio,cod_cups,"
            "descripcion,cantidad,valor_unitario,valor_total,tarifa_referencia,origen)"
            "VALUES(?,'consulta',?,'Consulta de urgencias',1,?,?,'ISS+30%','auto')", db),
            (pf_id, cod_cups_consulta,
             TARIFAS_DEFAULT['consulta_urgencias'],
             TARIFAS_DEFAULT['consulta_urgencias']))
        items_added += 1

        # b) Órdenes médicas (con_resultado o en_proceso)
        ordenes = core.rows(cur, core.adapt(
            "SELECT * FROM ordenes_medicas WHERE admision_id=? "
            "AND estado IN ('con_resultado','en_proceso')", db), (admision_id,))
        for orden in ordenes:
            tarifa = _tarifa_orden(orden.get('tipo_orden'))
            cantidad = orden.get('cantidad', 1) or 1
            cur.execute(core.adapt(
                "INSERT INTO pre_factura_items(pre_factura_id,tipo_servicio,cod_cups,"
                "descripcion,cantidad,valor_unitario,valor_total,tarifa_referencia,"
                "orden_id,origen)"
                "VALUES(?,?,?,?,?,?,?,?,?,'auto')", db),
                (pf_id, orden.get('tipo_orden', 'procedimiento'),
                 orden.get('cod_cups', ''),
                 orden.get('nombre_estudio', 'Orden médica'),
                 cantidad, tarifa, tarifa * cantidad,
                 'ISS+30%', orden['id']))
            items_added += 1

        # c) Prescripciones
        prescripciones = core.rows(cur, core.adapt(
            "SELECT * FROM prescripciones WHERE admision_id=?", db), (admision_id,))
        for presc in prescripciones:
            cantidad = presc.get('cantidad_total', 1) or 1
            # Use a base value for medications; in production this comes from the formulary
            valor_unit = 15000
            cur.execute(core.adapt(
                "INSERT INTO pre_factura_items(pre_factura_id,tipo_servicio,cod_cups,"
                "descripcion,cantidad,valor_unitario,valor_total,tarifa_referencia,"
                "prescripcion_id,origen)"
                "VALUES(?,'medicamento',?,?,?,?,?,?,?,'auto')", db),
                (pf_id, presc.get('cod_cum', ''),
                 f"{presc.get('medicamento','')} {presc.get('concentracion','')}".strip(),
                 cantidad, valor_unit, valor_unit * cantidad,
                 'regulado', presc['id']))
            items_added += 1

        # d) Interconsultas respondidas
        interconsultas = core.rows(cur, core.adapt(
            "SELECT * FROM interconsultas WHERE admision_id=? "
            "AND estado='respondida'", db), (admision_id,))
        for ic in interconsultas:
            cur.execute(core.adapt(
                "INSERT INTO pre_factura_items(pre_factura_id,tipo_servicio,cod_cups,"
                "descripcion,cantidad,valor_unitario,valor_total,tarifa_referencia,origen)"
                "VALUES(?,'interconsulta',?,"
                "?,1,?,?,'ISS+30%','auto')", db),
                (pf_id, ic.get('cod_cups', ''),
                 f"Interconsulta {ic.get('especialidad_solicitada','')}".strip(),
                 TARIFAS_DEFAULT['interconsulta'],
                 TARIFAS_DEFAULT['interconsulta']))
            items_added += 1

        conn.commit()

        # Recalculate totals
        _recalcular_totales(cur, db, pf_id)
        conn.commit()

        # Fetch complete pre_factura with items
        pre_factura = core.row(cur, core.adapt(
            "SELECT * FROM pre_factura WHERE id=?", db), (pf_id,))
        items = core.rows(cur, core.adapt(
            "SELECT * FROM pre_factura_items WHERE pre_factura_id=? ORDER BY id", db),
            (pf_id,))

        core.audit(cur, db, 'pre_factura', pf_id, 'generar',
                   f"Pre-factura generada con {items_added} ítems por {session.get('usuario', '?')}")
        conn.commit()

        core.sse_broadcast({
            'tipo': 'factura_generada',
            'admision_id': admision_id,
            'pf_id': pf_id,
        })

        return jsonify({
            'success': True,
            'pre_factura': pre_factura,
            'items': items,
            'items_count': items_added,
        })
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/facturacion/pre-factura/<admision_id> ────────────────────────────

@facturacion_bp.route('/api/facturacion/pre-factura/<int:admision_id>')
def facturacion_pre_factura(admision_id):
    """Get a pre-factura with all its items for a given admission."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        pf = core.row(cur, core.adapt(
            "SELECT * FROM pre_factura WHERE admision_id=?", db), (admision_id,))
        if not pf:
            return jsonify({'error': 'Pre-factura no encontrada para esta admisión'}), 404

        items = core.rows(cur, core.adapt(
            "SELECT * FROM pre_factura_items WHERE pre_factura_id=? ORDER BY id", db),
            (pf['id'],))

        return jsonify({'pre_factura': pf, 'items': items})
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/facturacion/item ────────────────────────────────────────────────

@facturacion_bp.route('/api/facturacion/item', methods=['POST'])
def facturacion_add_item():
    """Add a manual item to a pre-factura."""
    if not _has_permiso_facturacion():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    pf_id = d.get('pre_factura_id')
    tipo_servicio = d.get('tipo_servicio', '').strip()
    descripcion = d.get('descripcion', '').strip()

    if not pf_id or not tipo_servicio or not descripcion:
        return jsonify({'error': 'pre_factura_id, tipo_servicio y descripcion requeridos'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        pf = core.row(cur, core.adapt(
            "SELECT id, estado FROM pre_factura WHERE id=?", db), (pf_id,))
        if not pf:
            return jsonify({'error': 'Pre-factura no encontrada'}), 404
        if pf.get('estado') == 'aprobada':
            return jsonify({'error': 'No se pueden agregar ítems a una pre-factura aprobada'}), 400

        cantidad = int(d.get('cantidad', 1))
        valor_unitario = float(d.get('valor_unitario', 0))
        valor_total = cantidad * valor_unitario

        cur.execute(core.adapt(
            "INSERT INTO pre_factura_items(pre_factura_id,tipo_servicio,cod_cups,"
            "descripcion,cantidad,valor_unitario,valor_total,tarifa_referencia,origen)"
            "VALUES(?,?,?,?,?,?,?,?,'manual')", db),
            (pf_id, tipo_servicio, d.get('cod_cups', ''), descripcion,
             cantidad, valor_unitario, valor_total,
             d.get('tarifa_referencia', '')))
        conn.commit()

        _recalcular_totales(cur, db, pf_id)
        conn.commit()

        core.audit(cur, db, 'pre_factura', pf_id, 'agregar_item',
                   f"Ítem manual '{descripcion}' agregado por {session.get('usuario', '?')}")
        conn.commit()

        pre_factura = core.row(cur, core.adapt(
            "SELECT * FROM pre_factura WHERE id=?", db), (pf_id,))
        items = core.rows(cur, core.adapt(
            "SELECT * FROM pre_factura_items WHERE pre_factura_id=? ORDER BY id", db),
            (pf_id,))

        return jsonify({'success': True, 'pre_factura': pre_factura, 'items': items}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/facturacion/item/<item_id> ───────────────────────────────────────

@facturacion_bp.route('/api/facturacion/item/<int:item_id>', methods=['PUT'])
def facturacion_edit_item(item_id):
    """Edit an existing pre-factura item (cantidad, valor_unitario, descripcion)."""
    if not _has_permiso_facturacion():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        item = core.row(cur, core.adapt(
            "SELECT * FROM pre_factura_items WHERE id=?", db), (item_id,))
        if not item:
            return jsonify({'error': 'Ítem no encontrado'}), 404

        pf_id = item['pre_factura_id']
        pf = core.row(cur, core.adapt(
            "SELECT estado FROM pre_factura WHERE id=?", db), (pf_id,))
        if pf and pf.get('estado') == 'aprobada':
            return jsonify({'error': 'No se puede editar un ítem de pre-factura aprobada'}), 400

        cantidad = int(d.get('cantidad', item.get('cantidad', 1)))
        valor_unitario = float(d.get('valor_unitario', item.get('valor_unitario', 0)))
        descripcion = d.get('descripcion', item.get('descripcion', ''))
        valor_total = cantidad * valor_unitario

        cur.execute(core.adapt(
            "UPDATE pre_factura_items SET cantidad=?, valor_unitario=?, valor_total=?, "
            "descripcion=? WHERE id=?", db),
            (cantidad, valor_unitario, valor_total, descripcion, item_id))
        conn.commit()

        _recalcular_totales(cur, db, pf_id)
        conn.commit()

        core.audit(cur, db, 'pre_factura', pf_id, 'editar_item',
                   f"Ítem #{item_id} editado por {session.get('usuario', '?')}")
        conn.commit()

        pre_factura = core.row(cur, core.adapt(
            "SELECT * FROM pre_factura WHERE id=?", db), (pf_id,))
        items = core.rows(cur, core.adapt(
            "SELECT * FROM pre_factura_items WHERE pre_factura_id=? ORDER BY id", db),
            (pf_id,))

        return jsonify({'success': True, 'pre_factura': pre_factura, 'items': items})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── DELETE /api/facturacion/item/<item_id> ────────────────────────────────────

@facturacion_bp.route('/api/facturacion/item/<int:item_id>', methods=['DELETE'])
def facturacion_delete_item(item_id):
    """Delete an item from a pre-factura and recalculate totals."""
    if not _has_permiso_facturacion():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        item = core.row(cur, core.adapt(
            "SELECT * FROM pre_factura_items WHERE id=?", db), (item_id,))
        if not item:
            return jsonify({'error': 'Ítem no encontrado'}), 404

        pf_id = item['pre_factura_id']
        pf = core.row(cur, core.adapt(
            "SELECT estado FROM pre_factura WHERE id=?", db), (pf_id,))
        if pf and pf.get('estado') == 'aprobada':
            return jsonify({'error': 'No se puede eliminar un ítem de pre-factura aprobada'}), 400

        cur.execute(core.adapt(
            "DELETE FROM pre_factura_items WHERE id=?", db), (item_id,))
        conn.commit()

        _recalcular_totales(cur, db, pf_id)
        conn.commit()

        core.audit(cur, db, 'pre_factura', pf_id, 'eliminar_item',
                   f"Ítem #{item_id} eliminado por {session.get('usuario', '?')}")
        conn.commit()

        pre_factura = core.row(cur, core.adapt(
            "SELECT * FROM pre_factura WHERE id=?", db), (pf_id,))
        items = core.rows(cur, core.adapt(
            "SELECT * FROM pre_factura_items WHERE pre_factura_id=? ORDER BY id", db),
            (pf_id,))

        return jsonify({'success': True, 'pre_factura': pre_factura, 'items': items})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/facturacion/aprobar/<pf_id> ──────────────────────────────────────

@facturacion_bp.route('/api/facturacion/aprobar/<int:pf_id>', methods=['PUT'])
def facturacion_aprobar(pf_id):
    """Approve a pre-factura. Sets estado='aprobada', aprobado_en, revisado_por."""
    if not _has_permiso_facturacion():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        pf = core.row(cur, core.adapt(
            "SELECT * FROM pre_factura WHERE id=?", db), (pf_id,))
        if not pf:
            return jsonify({'error': 'Pre-factura no encontrada'}), 404
        if pf.get('estado') == 'aprobada':
            return jsonify({'error': 'Pre-factura ya está aprobada'}), 400

        # Check it has items
        item_count = core.row(cur, core.adapt(
            "SELECT COUNT(*) as total FROM pre_factura_items WHERE pre_factura_id=?", db),
            (pf_id,))
        if not item_count or item_count['total'] == 0:
            return jsonify({'error': 'No se puede aprobar una pre-factura sin ítems'}), 400

        cur.execute(core.adapt(
            f"UPDATE pre_factura SET estado='aprobada', revisado_por=?, "
            f"aprobado_en={core.NOW(db)} WHERE id=?", db),
            (session.get('usuario', 'sistema'), pf_id))
        conn.commit()

        core.audit(cur, db, 'pre_factura', pf_id, 'aprobar',
                   f"Pre-factura aprobada por {session.get('usuario', '?')}")
        conn.commit()

        core.sse_broadcast({
            'tipo': 'factura_aprobada',
            'pf_id': pf_id,
            'admision_id': pf.get('admision_id'),
        })

        pre_factura = core.row(cur, core.adapt(
            "SELECT * FROM pre_factura WHERE id=?", db), (pf_id,))
        return jsonify({'success': True, 'pre_factura': pre_factura})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/facturacion/pendientes ───────────────────────────────────────────

@facturacion_bp.route('/api/facturacion/pendientes')
def facturacion_pendientes():
    """List pending pre-facturas (estado borrador or revisada) with patient info."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        pendientes = core.rows(cur, core.adapt(
            "SELECT pf.*, p.nombres, p.apellidos, p.num_doc as p_num_doc, "
            "p.tipo_doc as p_tipo_doc "
            "FROM pre_factura pf "
            "LEFT JOIN pacientes p ON pf.paciente_id=p.id "
            "WHERE pf.estado IN ('borrador','revisada') "
            "ORDER BY pf.generado_en DESC", db))
        return jsonify({'pendientes': pendientes, 'total': len(pendientes)})
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/facturacion/resumen-diario ───────────────────────────────────────

@facturacion_bp.route('/api/facturacion/resumen-diario')
def facturacion_resumen_diario():
    """Daily billing summary: total facturas, total amount, by pagador, by estado."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    T = core.TODAY(db)

    try:
        # Total facturas today
        total_row = core.row(cur, core.adapt(
            f"SELECT COUNT(*) as total, COALESCE(SUM(total),0) as monto_total "
            f"FROM pre_factura WHERE DATE(generado_en)={T}", db))
        total_facturas = total_row['total'] if total_row else 0
        monto_total = total_row['monto_total'] if total_row else 0

        # By pagador type
        por_pagador = core.rows(cur, core.adapt(
            f"SELECT tipo_pagador, COUNT(*) as cantidad, "
            f"COALESCE(SUM(total),0) as monto "
            f"FROM pre_factura WHERE DATE(generado_en)={T} "
            f"GROUP BY tipo_pagador", db))

        # By estado
        por_estado = core.rows(cur, core.adapt(
            f"SELECT estado, COUNT(*) as cantidad, "
            f"COALESCE(SUM(total),0) as monto "
            f"FROM pre_factura WHERE DATE(generado_en)={T} "
            f"GROUP BY estado", db))

        # Copago vs pagador breakdown
        totales_row = core.row(cur, core.adapt(
            f"SELECT COALESCE(SUM(total_paciente),0) as total_copago, "
            f"COALESCE(SUM(total_pagador),0) as total_aseguradora "
            f"FROM pre_factura WHERE DATE(generado_en)={T}", db))

        return jsonify({
            'fecha': datetime.now().strftime('%Y-%m-%d'),
            'total_facturas': total_facturas,
            'monto_total': monto_total,
            'total_copago': totales_row['total_copago'] if totales_row else 0,
            'total_aseguradora': totales_row['total_aseguradora'] if totales_row else 0,
            'por_pagador': por_pagador,
            'por_estado': por_estado,
            'ts': datetime.now().isoformat(),
        })
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/facturacion/rips/<admision_id> ──────────────────────────────────

@facturacion_bp.route('/api/facturacion/rips/<int:admision_id>', methods=['POST'])
def facturacion_rips(admision_id):
    """Generate the complete RIPS JSON per Resolución 2275/2023.

    Structure:
    {
      numDocumentoIdObligado, numFactura,
      usuarios: [{
        tipoDocumentoIdentificacion, numDocumentoIdentificacion,
        tipoUsuario, fechaNacimiento, codSexo,
        codPaisResidencia, codMunicipioResidencia, consecutivo,
        servicios: {consultas, procedimientos, urgencias, medicamentos, otrosServicios}
      }]
    }
    """
    if not _has_permiso_facturacion():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        # Admission
        adm = core.row(cur, core.adapt(
            "SELECT * FROM admisiones WHERE id=?", db), (admision_id,))
        if not adm:
            return jsonify({'error': 'Admisión no encontrada'}), 404

        paciente_id = adm.get('paciente_id')
        if not paciente_id:
            return jsonify({'error': 'Admisión sin paciente vinculado'}), 400

        # Patient
        paciente = core.row(cur, core.adapt(
            "SELECT * FROM pacientes WHERE id=?", db), (paciente_id,))
        if not paciente:
            return jsonify({'error': 'Paciente no encontrado'}), 404

        # Pre-factura
        pf = core.row(cur, core.adapt(
            "SELECT * FROM pre_factura WHERE admision_id=?", db), (admision_id,))

        # Tenant config (IPS info)
        tenant = core.row(cur, core.adapt(
            "SELECT * FROM tenant_config WHERE tenant_id='default'", db)) or {}

        # HC
        hc = core.row(cur, core.adapt(
            "SELECT * FROM historia_clinica WHERE admision_id=?", db), (admision_id,))

        # Pagador
        pagador = core.row(cur, core.adapt(
            "SELECT * FROM pagador_validacion WHERE admision_id=? ORDER BY id DESC LIMIT 1", db),
            (admision_id,))

        # Map gender
        genero = (paciente.get('genero') or '').upper()
        cod_sexo = 'M' if genero in ('M', 'MASCULINO') else 'F' if genero in ('F', 'FEMENINO') else 'M'

        # Map document type
        tipo_doc_map = {
            'CC': 'CC', 'TI': 'TI', 'RC': 'RC', 'CE': 'CE',
            'PA': 'PA', 'NIT': 'NI', 'MS': 'MS', 'AS': 'AS',
        }
        tipo_doc = tipo_doc_map.get(paciente.get('tipo_doc', 'CC'), 'CC')

        # Tipo usuario RIPS
        tipo_usuario_map = {
            'Contributivo': '1', 'Subsidiado': '2', 'particular': '8',
            'eps_contributivo': '1', 'eps_subsidiado': '2',
            'arl': '6', 'soat': '5', 'poliza': '8',
        }
        tipo_pagador_val = pagador.get('tipo_pagador', 'particular') if pagador else 'particular'
        tipo_usuario = tipo_usuario_map.get(tipo_pagador_val, '8')

        # Build services
        consultas = []
        procedimientos = []
        urgencias = []
        medicamentos = []
        otros_servicios = []

        # Pre-factura items
        items = []
        if pf:
            items = core.rows(cur, core.adapt(
                "SELECT * FROM pre_factura_items WHERE pre_factura_id=? ORDER BY id", db),
                (pf['id'],))

        fecha_ingreso = str(adm.get('creado_en', ''))[:10]
        fecha_egreso = str(adm.get('fecha_salida') or adm.get('creado_en', ''))[:10]
        dx_ingreso = ''
        dx_egreso = ''
        if hc:
            dx_ingreso = hc.get('cod_cie10_ingreso', '') or ''
            dx_egreso = hc.get('cod_cie10_egreso', '') or dx_ingreso

        # Urgencia record (always one per admission)
        urgencias.append({
            'codPrestador': tenant.get('nit', ''),
            'fechaInicioAtencion': fecha_ingreso,
            'codServicio': '01',
            'causaMotivoAtencion': adm.get('causa_atencion', '01') or '01',
            'codDiagnosticoPrincipal': dx_ingreso,
            'codDiagnosticoRelacionadoE1': dx_egreso,
            'condicionDestinoUsuarioEgreso': adm.get('condicion_salida', '1') or '1',
            'codDiagnosticoCausaMuerte': '',
            'fechaEgreso': fecha_egreso,
        })

        for it in items:
            tipo = (it.get('tipo_servicio') or '').lower()
            base_item = {
                'codPrestador': tenant.get('nit', ''),
                'fechaPrestacion': str(it.get('creado_en', fecha_ingreso))[:10],
                'numAutorizacion': it.get('numero_autorizacion') or '',
                'codCUPS': it.get('cod_cups', ''),
                'descripcion': it.get('descripcion', ''),
                'cantidad': it.get('cantidad', 1),
                'valorUnitario': it.get('valor_unitario', 0),
                'valorTotal': it.get('valor_total', 0),
            }

            if tipo == 'consulta':
                consultas.append({
                    **base_item,
                    'modalidadGrupoServicioTecSal': '01',
                    'grupoServicios': '01',
                    'codServicio': it.get('cod_cups', '890701'),
                    'finalidadTecnologiaSalud': '12',
                    'causaMotivoAtencion': '38',
                    'codDiagnosticoPrincipal': dx_ingreso,
                    'codDiagnosticoRelacionado1': dx_egreso,
                    'tipoDiagnosticoPrincipal': '1',
                    'tipoDocumentoIdentificacion': tipo_doc,
                    'numDocumentoIdentificacion': paciente.get('num_doc', ''),
                    'vrServicio': it.get('valor_total', 0),
                    'conceptoRecaudo': '05',
                    'valorPagoModerador': pf.get('copago', 0) if pf else 0,
                })
            elif tipo in ('laboratorio', 'lab', 'imagen', 'imagenologia', 'radiologia', 'procedimiento'):
                procedimientos.append({
                    **base_item,
                    'modalidadGrupoServicioTecSal': '01',
                    'grupoServicios': '03',
                    'codServicio': it.get('cod_cups', ''),
                    'finalidadTecnologiaSalud': '12',
                    'codDiagnosticoPrincipal': dx_ingreso,
                    'codDiagnosticoRelacionado1': dx_egreso,
                    'vrServicio': it.get('valor_total', 0),
                })
            elif tipo == 'medicamento':
                medicamentos.append({
                    **base_item,
                    'codTecnologiaSalud': it.get('cod_cups', ''),
                    'nomTecnologiaSalud': it.get('descripcion', ''),
                    'concentracionMedicamento': '',
                    'unidadMedida': 'unidad',
                    'formaFarmaceutica': '',
                    'unidadMinDispensa': 1,
                    'cantidadMedicamento': it.get('cantidad', 1),
                    'diasTratamiento': 1,
                    'tipoMedicamento': 'POS',
                    'vrUnitMedicamento': it.get('valor_unitario', 0),
                    'vrServicio': it.get('valor_total', 0),
                })
            elif tipo == 'interconsulta':
                consultas.append({
                    **base_item,
                    'modalidadGrupoServicioTecSal': '01',
                    'grupoServicios': '01',
                    'codServicio': it.get('cod_cups', ''),
                    'finalidadTecnologiaSalud': '12',
                    'causaMotivoAtencion': '38',
                    'codDiagnosticoPrincipal': dx_ingreso,
                    'codDiagnosticoRelacionado1': dx_egreso,
                    'tipoDiagnosticoPrincipal': '1',
                    'tipoDocumentoIdentificacion': tipo_doc,
                    'numDocumentoIdentificacion': paciente.get('num_doc', ''),
                    'vrServicio': it.get('valor_total', 0),
                    'conceptoRecaudo': '05',
                    'valorPagoModerador': 0,
                })
            else:
                otros_servicios.append({
                    **base_item,
                    'tipoOtrosServicios': '99',
                    'vrServicio': it.get('valor_total', 0),
                })

        # Build RIPS JSON
        num_factura = f"PF-{pf['id']:06d}" if pf else f"PF-{admision_id:06d}"

        rips = {
            'numDocumentoIdObligado': tenant.get('nit', ''),
            'numFactura': num_factura,
            'tipoNota': None,
            'numNota': None,
            'usuarios': [{
                'tipoDocumentoIdentificacion': tipo_doc,
                'numDocumentoIdentificacion': paciente.get('num_doc', ''),
                'tipoUsuario': tipo_usuario,
                'fechaNacimiento': str(paciente.get('fecha_nacimiento', ''))[:10],
                'codSexo': cod_sexo,
                'codPaisResidencia': '170',
                'codMunicipioResidencia': '11001',
                'consecutivo': 1,
                'servicios': {
                    'consultas': consultas,
                    'procedimientos': procedimientos,
                    'urgencias': urgencias,
                    'medicamentos': medicamentos,
                    'otrosServicios': otros_servicios,
                },
            }],
        }

        core.audit(cur, db, 'pre_factura', pf['id'] if pf else 0, 'generar_rips',
                   f"RIPS generado para admisión {admision_id} por {session.get('usuario', '?')}")
        conn.commit()

        return jsonify({'success': True, 'rips': rips})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)
