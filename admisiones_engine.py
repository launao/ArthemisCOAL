"""
admisiones_engine.py — Blueprint del módulo Admisiones Urgencias de Arthemis Health.

Gestiona el proceso completo de admisión de urgencias:
  - Búsqueda y creación de pacientes
  - Validación de derechos (EPS, ARL, SOAT, póliza, particular)
  - Cálculo automático de copago (Circular 048/2025)
  - Apertura de historia clínica de urgencias
  - Timeline minuto-a-minuto
  - Dashboard de urgencias con KPIs en tiempo real
  - Campos RIPS (Resolución 2275/2023)

Endpoints:
  GET    /api/admisiones/buscar-paciente     — buscar paciente por documento
  POST   /api/admisiones/crear-paciente      — crear nuevo paciente
  PUT    /api/admisiones/vincular-paciente    — vincular paciente existente a admisión
  POST   /api/admisiones/validar-pagador     — registrar/validar tipo de pagador
  GET    /api/admisiones/calcular-copago     — calcular copago según régimen y triage
  POST   /api/admisiones/abrir-hc           — abrir historia clínica urgencias
  PUT    /api/admisiones/hc/<id>            — actualizar historia clínica
  POST   /api/admisiones/completar          — marcar admisión como completa → pasa a 'admision'
  GET    /api/admisiones/timeline/<adm_id>  — timeline de una admisión
  POST   /api/admisiones/timeline           — agregar evento a timeline
  GET    /api/admisiones/detalle/<adm_id>   — detalle completo de una admisión
  GET    /api/admisiones/dashboard          — estadísticas de urgencias del día
  GET    /api/admisiones/historial          — búsqueda de admisiones históricas
"""

import json
from datetime import datetime
from flask import Blueprint, request, jsonify, session

admisiones_bp = Blueprint('admisiones', __name__)


def _get_deps():
    import core
    return core


def _D(col, db):
    """DATE extraction compatible with both PG and SQLite (Colombia TZ)."""
    return f"CAST({col} AS DATE)" if db == 'pg' else f"DATE({col},'-5 hours')"


def _is_authenticated():
    return bool(session.get('user_id'))


def _has_permiso_admisiones():
    if not session.get('user_id'):
        return False
    core = _get_deps()
    permisos = core.get_user_permisos()
    return 'superadmin' in permisos or 'admisiones' in permisos


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


# ── Copago calculation (Circular 048 de 2025) ──────────────────────────────

TRIAGE_EXCENTO = ('I', 'II', 'III')  # Triage I-III siempre exento de copago

POBLACIONES_EXENTAS = [
    'menor_edad', 'gestante', 'alto_costo', 'victima_conflicto',
    'adulto_mayor_sisben_1', 'discapacidad', 'indigena', 'desplazado',
]

CAUSA_EXENCION_LABELS = {
    'triage_i_iii': 'Triage I-III: exento por urgencia vital',
    'menor_edad': 'Menor de edad',
    'gestante': 'Mujer gestante o en periodo de lactancia',
    'alto_costo': 'Enfermedad de alto costo',
    'victima_conflicto': 'Víctima del conflicto armado',
    'adulto_mayor_sisben_1': 'Adulto mayor Sisbén nivel 1',
    'discapacidad': 'Persona con discapacidad',
    'indigena': 'Población indígena',
    'desplazado': 'Población desplazada',
    'subsidiado': 'Régimen subsidiado - sin copago en urgencias',
    'particular': 'Particular - pago directo',
    'arl': 'ARL - sin copago (accidente de trabajo)',
    'soat': 'SOAT - sin copago (accidente de tránsito)',
    'poliza': 'Póliza de salud - según contrato',
}


def _calcular_copago(cur, db, triage_nivel, tipo_pagador, regimen=None,
                     grupo_ingreso=None, poblacion_especial=None):
    """Calculate copago based on Colombian regulations.

    Returns dict: {aplica, valor, excento, motivo_exencion}
    """
    core = _get_deps()

    # Triage I-III: always exempt
    if triage_nivel in TRIAGE_EXCENTO:
        return {
            'aplica': False, 'valor': 0, 'excento': True,
            'motivo_exencion': CAUSA_EXENCION_LABELS['triage_i_iii'],
        }

    # ARL, SOAT: no copago
    if tipo_pagador in ('arl', 'soat'):
        return {
            'aplica': False, 'valor': 0, 'excento': True,
            'motivo_exencion': CAUSA_EXENCION_LABELS.get(tipo_pagador, 'Sin copago'),
        }

    # Particular: direct payment, no copago scheme
    if tipo_pagador == 'particular':
        return {
            'aplica': False, 'valor': 0, 'excento': True,
            'motivo_exencion': CAUSA_EXENCION_LABELS['particular'],
        }

    # Póliza: depends on contract
    if tipo_pagador == 'poliza':
        return {
            'aplica': False, 'valor': 0, 'excento': True,
            'motivo_exencion': CAUSA_EXENCION_LABELS['poliza'],
        }

    # Special populations
    if poblacion_especial and poblacion_especial in POBLACIONES_EXENTAS:
        return {
            'aplica': False, 'valor': 0, 'excento': True,
            'motivo_exencion': CAUSA_EXENCION_LABELS.get(poblacion_especial, 'Población exenta'),
        }

    # Subsidiado: no copago in urgencias for triage IV-V (varies by regulation)
    if tipo_pagador == 'eps_subsidiado' or regimen == 'Subsidiado':
        # Look up copago_param for subsidiado
        param = core.row(cur, core.adapt(
            "SELECT * FROM copago_param WHERE anio=2026 AND concepto='copago_subsidiado' AND activo=1", db))
        if param:
            return {
                'aplica': True,
                'valor': param.get('tope_evento', 0),
                'excento': False,
                'motivo_exencion': '',
                'pct': param.get('pct', 0.10),
                'tope_evento': param.get('tope_evento', 0),
                'tope_anio': param.get('tope_anio', 0),
            }
        return {
            'aplica': False, 'valor': 0, 'excento': True,
            'motivo_exencion': CAUSA_EXENCION_LABELS['subsidiado'],
        }

    # EPS Contributivo: copago based on income group
    if tipo_pagador == 'eps_contributivo' or regimen == 'Contributivo':
        rango = grupo_ingreso or 'menor_2'  # default to lowest
        param = core.row(cur, core.adapt(
            "SELECT * FROM copago_param WHERE anio=2026 AND concepto='copago_contributivo' AND rango=? AND activo=1", db),
            (rango,))
        if param:
            return {
                'aplica': True,
                'valor': param.get('tope_evento', 0),
                'excento': False,
                'motivo_exencion': '',
                'pct': param.get('pct', 0),
                'tope_evento': param.get('tope_evento', 0),
                'tope_anio': param.get('tope_anio', 0),
                'grupo_ingreso': rango,
            }

    return {'aplica': False, 'valor': 0, 'excento': True, 'motivo_exencion': 'No determinado'}


# ── GET /api/admisiones/buscar-paciente ─────────────────────────────────────

@admisiones_bp.route('/api/admisiones/buscar-paciente')
def admisiones_buscar_paciente():
    """Search for a patient by document number."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    num_doc = request.args.get('num_doc', '').strip()
    tipo_doc = request.args.get('tipo_doc', 'CC').strip()

    if not num_doc:
        return jsonify({'error': 'num_doc requerido'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        paciente = core.row(cur, core.adapt(
            "SELECT * FROM pacientes WHERE num_doc=?", db), (num_doc,))
        if paciente:
            return jsonify({'encontrado': True, 'paciente': paciente})
        return jsonify({'encontrado': False, 'paciente': None})
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/admisiones/crear-paciente ─────────────────────────────────────

@admisiones_bp.route('/api/admisiones/crear-paciente', methods=['POST'])
def admisiones_crear_paciente():
    """Create a new patient record during admissions."""
    if not _has_permiso_admisiones():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    required = ['num_doc', 'nombres', 'apellidos']
    if not all(d.get(k) for k in required):
        return jsonify({'error': 'Campos requeridos: num_doc, nombres, apellidos'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        existing = core.row(cur, core.adapt(
            "SELECT id FROM pacientes WHERE num_doc=?", db), (d['num_doc'],))
        if existing:
            return jsonify({'error': 'Ya existe paciente con ese documento', 'id': existing['id']}), 409

        cur.execute(core.adapt(
            "INSERT INTO pacientes(tipo_doc,num_doc,nombres,apellidos,fecha_nacimiento,"
            "genero,celular,telefono,email,direccion,ciudad,eps,tipo_afiliado)"
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", db),
            (d.get('tipo_doc', 'CC'), d['num_doc'], d['nombres'], d['apellidos'],
             d.get('fecha_nacimiento'), d.get('genero'), d.get('celular'),
             d.get('telefono'), d.get('email'), d.get('direccion'),
             d.get('ciudad', 'Bogotá'), d.get('eps'), d.get('tipo_afiliado', 'Contributivo')))
        conn.commit()

        paciente = core.row(cur, core.adapt(
            "SELECT * FROM pacientes WHERE num_doc=?", db), (d['num_doc'],))
        core.audit(cur, db, 'pacientes', paciente['id'], 'crear_admisiones',
                   f"Paciente creado en admisiones por {session.get('usuario', '?')}")
        conn.commit()

        return jsonify({'success': True, 'paciente': paciente}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/admisiones/vincular-paciente ───────────────────────────────────

@admisiones_bp.route('/api/admisiones/vincular-paciente', methods=['PUT'])
def admisiones_vincular_paciente():
    """Link an existing patient to an admission record (updates paciente_id)."""
    if not _has_permiso_admisiones():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    admision_id = d.get('admision_id')
    paciente_id = d.get('paciente_id')

    if not admision_id or not paciente_id:
        return jsonify({'error': 'admision_id y paciente_id requeridos'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        # Get patient data
        paciente = core.row(cur, core.adapt(
            "SELECT * FROM pacientes WHERE id=?", db), (paciente_id,))
        if not paciente:
            return jsonify({'error': 'Paciente no encontrado'}), 404

        # Update admisión
        cur.execute(core.adapt(
            "UPDATE admisiones SET paciente_id=?, nombre_temp=?, doc_num_temp=?, doc_type_temp=? WHERE id=?", db),
            (paciente_id,
             f"{paciente['nombres']} {paciente['apellidos']}",
             paciente['num_doc'],
             paciente.get('tipo_doc', 'CC'),
             admision_id))
        conn.commit()

        _add_timeline(cur, db, admision_id, 'paciente_vinculado',
                      f"Paciente {paciente['nombres']} {paciente['apellidos']} (ID:{paciente_id}) vinculado")
        conn.commit()

        core.audit(cur, db, 'admisiones', admision_id, 'vincular_paciente',
                   f"Paciente {paciente_id} vinculado por {session.get('usuario', '?')}")
        conn.commit()

        return jsonify({'success': True, 'paciente': paciente})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/admisiones/validar-pagador ────────────────────────────────────

@admisiones_bp.route('/api/admisiones/validar-pagador', methods=['POST'])
def admisiones_validar_pagador():
    """Register/validate payer information for an admission.

    tipo_pagador: eps_contributivo | eps_subsidiado | arl | soat | poliza | particular
    """
    if not _has_permiso_admisiones():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    admision_id = d.get('admision_id')
    tipo_pagador = d.get('tipo_pagador', '').strip()

    valid_tipos = ('eps_contributivo', 'eps_subsidiado', 'arl', 'soat', 'poliza', 'particular')
    if not admision_id or tipo_pagador not in valid_tipos:
        return jsonify({'error': 'admision_id y tipo_pagador válido requeridos'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        # Check admission exists
        adm = core.row(cur, core.adapt("SELECT * FROM admisiones WHERE id=?", db), (admision_id,))
        if not adm:
            return jsonify({'error': 'Admisión no encontrada'}), 404

        # Save pagador validation record
        cur.execute(core.adapt(
            "INSERT INTO pagador_validacion(admision_id,tipo_pagador,entidad_nombre,entidad_codigo,"
            "regimen,estado_afiliacion,fecha_afiliacion,nivel_sisben,grupo_ingreso,"
            "numero_poliza,numero_autorizacion,placa_vehiculo,fecha_accidente,"
            "empresa_nombre,nit_empresa,validado,validado_por,datos_json)"
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)", db),
            (admision_id, tipo_pagador,
             d.get('entidad_nombre', ''), d.get('entidad_codigo', ''),
             d.get('regimen', ''), d.get('estado_afiliacion', 'Activo'),
             d.get('fecha_afiliacion', ''), d.get('nivel_sisben', ''),
             d.get('grupo_ingreso', 'menor_2'),
             d.get('numero_poliza', ''), d.get('numero_autorizacion', ''),
             d.get('placa_vehiculo', ''), d.get('fecha_accidente', ''),
             d.get('empresa_nombre', ''), d.get('nit_empresa', ''),
             session.get('usuario', 'sistema'),
             json.dumps(d.get('datos_extra', {}), ensure_ascii=False)))
        conn.commit()

        # Calculate copago
        copago = _calcular_copago(
            cur, db,
            triage_nivel=adm.get('triage_nivel', 'V'),
            tipo_pagador=tipo_pagador,
            regimen=d.get('regimen'),
            grupo_ingreso=d.get('grupo_ingreso'),
            poblacion_especial=d.get('poblacion_especial'),
        )

        # Update admisión with pagador and copago info
        cur.execute(core.adapt(
            "UPDATE admisiones SET tipo_pagador=?, pagador_entidad=?, pagador_regimen=?, "
            "pagador_validado=1, pagador_estado=?, "
            "copago_calculado=?, copago_excento=?, copago_motivo_exencion=?, "
            "numero_autorizacion=? WHERE id=?", db),
            (tipo_pagador, d.get('entidad_nombre', ''), d.get('regimen', ''),
             d.get('estado_afiliacion', 'Activo'),
             copago.get('valor', 0), 1 if copago.get('excento') else 0,
             copago.get('motivo_exencion', ''),
             d.get('numero_autorizacion', ''),
             admision_id))
        conn.commit()

        _add_timeline(cur, db, admision_id, 'pagador_validado',
                      f"Pagador: {tipo_pagador} - {d.get('entidad_nombre', 'N/A')}")
        conn.commit()

        core.audit(cur, db, 'admisiones', admision_id, 'validar_pagador',
                   f"{tipo_pagador}: {d.get('entidad_nombre', '')} por {session.get('usuario', '?')}")
        conn.commit()

        return jsonify({'success': True, 'copago': copago, 'tipo_pagador': tipo_pagador})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/admisiones/calcular-copago ─────────────────────────────────────

@admisiones_bp.route('/api/admisiones/calcular-copago')
def admisiones_calcular_copago():
    """Calculate copago for a given admission without saving."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    admision_id = request.args.get('admision_id')

    if not admision_id:
        return jsonify({'error': 'admision_id requerido'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        adm = core.row(cur, core.adapt("SELECT * FROM admisiones WHERE id=?", db), (admision_id,))
        if not adm:
            return jsonify({'error': 'Admisión no encontrada'}), 404

        copago = _calcular_copago(
            cur, db,
            triage_nivel=adm.get('triage_nivel', 'V'),
            tipo_pagador=adm.get('tipo_pagador', 'eps_contributivo'),
            regimen=adm.get('pagador_regimen'),
            grupo_ingreso=request.args.get('grupo_ingreso', 'menor_2'),
            poblacion_especial=request.args.get('poblacion_especial'),
        )
        return jsonify({'copago': copago})
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/admisiones/abrir-hc ───────────────────────────────────────────

@admisiones_bp.route('/api/admisiones/abrir-hc', methods=['POST'])
def admisiones_abrir_hc():
    """Open a new urgencias clinical history for an admission."""
    if not _has_permiso_admisiones():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    admision_id = d.get('admision_id')

    if not admision_id:
        return jsonify({'error': 'admision_id requerido'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        adm = core.row(cur, core.adapt("SELECT * FROM admisiones WHERE id=?", db), (admision_id,))
        if not adm:
            return jsonify({'error': 'Admisión no encontrada'}), 404

        if not adm.get('paciente_id'):
            return jsonify({'error': 'Debe vincular un paciente antes de abrir HC'}), 400

        # Check if HC already exists
        existing = core.row(cur, core.adapt(
            "SELECT id FROM historia_clinica_urgencias WHERE admision_id=?", db), (admision_id,))
        if existing:
            return jsonify({'error': 'Ya existe HC para esta admisión', 'hc_id': existing['id']}), 409

        cur.execute(core.adapt(
            "INSERT INTO historia_clinica_urgencias(admision_id,paciente_id,motivo_consulta,"
            "cod_cie10_ingreso,creado_por)"
            "VALUES(?,?,?,?,?)", db),
            (admision_id, adm['paciente_id'],
             d.get('motivo_consulta', ''),
             d.get('cod_cie10_ingreso', ''),
             session.get('usuario', 'sistema')))
        conn.commit()

        # Get the HC id
        hc = core.row(cur, core.adapt(
            "SELECT id FROM historia_clinica_urgencias WHERE admision_id=?", db), (admision_id,))

        # Update admisión
        cur.execute(core.adapt(
            "UPDATE admisiones SET hc_abierta=1, hc_id=?, causa_atencion=? WHERE id=?", db),
            (hc['id'], d.get('causa_atencion', 'urgencia'), admision_id))
        conn.commit()

        _add_timeline(cur, db, admision_id, 'hc_abierta',
                      f"Historia clínica urgencias abierta (HC#{hc['id']})")
        conn.commit()

        core.audit(cur, db, 'admisiones', admision_id, 'abrir_hc',
                   f"HC urgencias #{hc['id']} abierta por {session.get('usuario', '?')}")
        conn.commit()

        return jsonify({'success': True, 'hc_id': hc['id']})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── PUT /api/admisiones/hc/<id> ─────────────────────────────────────────────

@admisiones_bp.route('/api/admisiones/hc/<int:hc_id>', methods=['PUT'])
def admisiones_update_hc(hc_id):
    """Update urgencias clinical history fields."""
    if not _has_permiso_admisiones():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}

    allowed_fields = [
        'motivo_consulta', 'enfermedad_actual', 'antecedentes', 'examen_fisico',
        'signos_vitales', 'diagnostico_ingreso', 'cod_cie10_ingreso',
        'diagnostico_egreso', 'cod_cie10_egreso', 'diagnosticos_relacionados',
        'conducta', 'tratamiento', 'observaciones', 'condicion_salida',
        'destino_salida', 'medico_id', 'medico_nombre',
    ]

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        fields, vals = [], []
        for k in allowed_fields:
            if k in d:
                fields.append(f"{k}=?")
                vals.append(d[k])
        if not fields:
            return jsonify({'error': 'Nada que actualizar'}), 400

        # Add actualizado_en
        fields.append(f"actualizado_en={core.NOW(db)}")
        vals.append(hc_id)

        cur.execute(core.adapt(
            f"UPDATE historia_clinica_urgencias SET {','.join(fields)} WHERE id=?", db), vals)
        conn.commit()

        # Get admision_id for timeline
        hc = core.row(cur, core.adapt(
            "SELECT admision_id FROM historia_clinica_urgencias WHERE id=?", db), (hc_id,))
        if hc:
            changed = ', '.join(k for k in allowed_fields if k in d)
            _add_timeline(cur, db, hc['admision_id'], 'hc_actualizada',
                          f"Campos actualizados: {changed}")
            conn.commit()

        core.audit(cur, db, 'historia_clinica_urgencias', hc_id, 'actualizar',
                   f"Actualizado por {session.get('usuario', '?')}")
        conn.commit()

        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/admisiones/completar ──────────────────────────────────────────

@admisiones_bp.route('/api/admisiones/completar', methods=['POST'])
def admisiones_completar():
    """Mark admission as complete. Moves patient to estado='admision'.

    Requirements: paciente vinculado + pagador validado.
    HC is optional but recommended.
    """
    if not _has_permiso_admisiones():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    admision_id = d.get('admision_id')

    if not admision_id:
        return jsonify({'error': 'admision_id requerido'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        adm = core.row(cur, core.adapt("SELECT * FROM admisiones WHERE id=?", db), (admision_id,))
        if not adm:
            return jsonify({'error': 'Admisión no encontrada'}), 404

        # Validations
        errors = []
        if not adm.get('paciente_id'):
            errors.append('Debe vincular un paciente')
        if not adm.get('pagador_validado'):
            errors.append('Debe validar el tipo de pagador')
        if errors:
            return jsonify({'error': 'Admisión incompleta', 'detalles': errors}), 400

        # Update admission
        cur.execute(core.adapt(
            f"UPDATE admisiones SET estado='admision', admision_completa=1, "
            f"admisionista=?, fecha_admision_fin={core.NOW(db)}, puesto_id=NULL WHERE id=?", db),
            (session.get('usuario', 'sistema'), admision_id))
        conn.commit()

        _add_timeline(cur, db, admision_id, 'admision_completa',
                      f"Admisión completada por {session.get('usuario', '?')} → pasa a espera médica")
        conn.commit()

        core.audit(cur, db, 'admisiones', admision_id, 'completar_admision',
                   f"Admisión completada por {session.get('usuario', '?')}")
        conn.commit()

        core.sse_broadcast({
            'tipo': 'turno_admision',
            'id': admision_id,
            'turno': adm.get('turno', ''),
        })

        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/admisiones/timeline/<adm_id> ───────────────────────────────────

@admisiones_bp.route('/api/admisiones/timeline/<int:adm_id>')
def admisiones_timeline(adm_id):
    """Get the minute-by-minute timeline for an admission."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        eventos = core.rows(cur, core.adapt(
            "SELECT * FROM admision_timeline WHERE admision_id=? ORDER BY ts", db),
            (adm_id,))
        return jsonify({'timeline': eventos, 'total': len(eventos)})
    finally:
        cur.close()
        core._return_db(conn, db)


# ── POST /api/admisiones/timeline ───────────────────────────────────────────

@admisiones_bp.route('/api/admisiones/timeline', methods=['POST'])
def admisiones_add_timeline():
    """Add a manual timeline entry."""
    if not _has_permiso_admisiones():
        return jsonify({'error': 'No autorizado'}), 403

    core = _get_deps()
    d = request.json or {}
    admision_id = d.get('admision_id')
    evento = d.get('evento', '').strip()
    detalle = d.get('detalle', '').strip()

    if not admision_id or not evento:
        return jsonify({'error': 'admision_id y evento requeridos'}), 400

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        _add_timeline(cur, db, admision_id, evento, detalle)
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/admisiones/detalle/<adm_id> ────────────────────────────────────

@admisiones_bp.route('/api/admisiones/detalle/<int:adm_id>')
def admisiones_detalle(adm_id):
    """Get full admission detail including patient, payer, HC, timeline."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        # Admission
        adm = core.row(cur, core.adapt(
            "SELECT a.*, p.nombres, p.apellidos, p.tipo_doc as p_tipo_doc, p.num_doc as p_num_doc, "
            "p.fecha_nacimiento, p.genero, p.celular as p_celular, p.telefono as p_telefono, "
            "p.email as p_email, p.direccion as p_direccion, p.ciudad as p_ciudad, "
            "p.eps as p_eps, p.tipo_afiliado as p_tipo_afiliado "
            "FROM admisiones a LEFT JOIN pacientes p ON a.paciente_id=p.id WHERE a.id=?", db),
            (adm_id,))
        if not adm:
            return jsonify({'error': 'Admisión no encontrada'}), 404

        # Payer validation
        pagador = core.row(cur, core.adapt(
            "SELECT * FROM pagador_validacion WHERE admision_id=? ORDER BY id DESC LIMIT 1", db),
            (adm_id,))

        # HC
        hc = core.row(cur, core.adapt(
            "SELECT * FROM historia_clinica_urgencias WHERE admision_id=?", db),
            (adm_id,))

        # Timeline
        timeline = core.rows(cur, core.adapt(
            "SELECT * FROM admision_timeline WHERE admision_id=? ORDER BY ts", db),
            (adm_id,))

        return jsonify({
            'admision': adm,
            'pagador': pagador,
            'hc': hc,
            'timeline': timeline,
        })
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/admisiones/dashboard ───────────────────────────────────────────

@admisiones_bp.route('/api/admisiones/dashboard')
def admisiones_dashboard():
    """Real-time urgencias dashboard with KPIs."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    conn, db = core.get_db()
    cur = conn.cursor()
    T = core.TODAY(db)

    try:
        # Total today
        total_row = core.row(cur, core.adapt(
            f"SELECT COUNT(*) as total FROM admisiones WHERE {_D('creado_en',db)}={T}", db))
        total = total_row['total'] if total_row else 0

        # By estado
        estados = core.rows(cur, core.adapt(
            f"SELECT estado, COUNT(*) as cantidad FROM admisiones "
            f"WHERE {_D('creado_en',db)}={T} GROUP BY estado", db))
        por_estado = {e['estado']: e['cantidad'] for e in estados}

        # By triage level
        triajes = core.rows(cur, core.adapt(
            f"SELECT triage_nivel, COUNT(*) as cantidad FROM admisiones "
            f"WHERE {_D('creado_en',db)}={T} AND triage_nivel IS NOT NULL GROUP BY triage_nivel", db))
        por_triage = {t['triage_nivel']: t['cantidad'] for t in triajes}

        # Patients in each stage
        en_kiosco = por_estado.get('kiosco', 0)
        en_triage = por_estado.get('triaje', 0) + por_estado.get('llamando', 0)
        en_admision_espera = por_estado.get('triaje', 0)
        en_admision_proceso = por_estado.get('llamando', 0)
        en_consulta = por_estado.get('admision', 0)
        atendidos = por_estado.get('atendido', 0)

        # Average wait times (in minutes)
        tiempos = core.row(cur, core.adapt(
            f"SELECT "
            f"AVG(CASE WHEN triage_ts IS NOT NULL THEN "
            f"  (julianday(triage_ts) - julianday(creado_en)) * 1440 END) as avg_kiosco_triage, "
            f"AVG(CASE WHEN fecha_admision_inicio IS NOT NULL AND triage_ts IS NOT NULL THEN "
            f"  (julianday(fecha_admision_inicio) - julianday(triage_ts)) * 1440 END) as avg_triage_admision, "
            f"AVG(CASE WHEN fecha_salida IS NOT NULL AND fecha_admision_fin IS NOT NULL THEN "
            f"  (julianday(fecha_salida) - julianday(fecha_admision_fin)) * 1440 END) as avg_admision_consulta "
            f"FROM admisiones WHERE {_D('creado_en',db)}={T}", db)) if db == 'sqlite' else None

        if db == 'pg':
            tiempos = core.row(cur, core.adapt(
                f"SELECT "
                f"AVG(CASE WHEN triage_ts IS NOT NULL THEN "
                f"  EXTRACT(EPOCH FROM (triage_ts::timestamp - creado_en)) / 60 END) as avg_kiosco_triage, "
                f"AVG(CASE WHEN fecha_admision_inicio IS NOT NULL AND triage_ts IS NOT NULL THEN "
                f"  EXTRACT(EPOCH FROM (fecha_admision_inicio - triage_ts::timestamp)) / 60 END) as avg_triage_admision, "
                f"AVG(CASE WHEN fecha_salida IS NOT NULL AND fecha_admision_fin IS NOT NULL THEN "
                f"  EXTRACT(EPOCH FROM (fecha_salida - fecha_admision_fin)) / 60 END) as avg_admision_consulta "
                f"FROM admisiones WHERE {_D('creado_en',db)}={T}", db))

        # Long-wait alerts (>30 min in any stage)
        alertas = core.rows(cur, core.adapt(
            f"SELECT a.id, a.turno, a.nombre_temp, a.estado, a.triage_nivel, a.creado_en, "
            f"p.nombres, p.apellidos "
            f"FROM admisiones a LEFT JOIN pacientes p ON a.paciente_id=p.id "
            f"WHERE {_D('a.creado_en',db)}={T} AND a.estado NOT IN ('atendido','cancelado')", db))

        alertas_list = []
        now = datetime.now()
        for a in alertas:
            try:
                creado = datetime.fromisoformat(str(a.get('creado_en', '')).replace('T', ' ').split('+')[0])
                mins = (now - creado).total_seconds() / 60
                if mins > 30:
                    nombre = a.get('nombre_temp') or ''
                    if a.get('nombres'):
                        nombre = f"{a['nombres']} {a.get('apellidos', '')}".strip()
                    alertas_list.append({
                        'id': a['id'],
                        'turno': a['turno'],
                        'nombre': nombre,
                        'estado': a['estado'],
                        'triage_nivel': a.get('triage_nivel'),
                        'minutos_espera': round(mins),
                    })
            except (ValueError, TypeError):
                pass

        alertas_list.sort(key=lambda x: x.get('minutos_espera', 0), reverse=True)

        return jsonify({
            'total_hoy': total,
            'por_estado': por_estado,
            'por_triage': por_triage,
            'resumen': {
                'en_kiosco': en_kiosco,
                'en_triage': en_triage,
                'en_admision': en_admision_espera + en_admision_proceso,
                'en_consulta': en_consulta,
                'atendidos': atendidos,
            },
            'tiempos_promedio': {
                'kiosco_a_triage': round(tiempos.get('avg_kiosco_triage') or 0, 1) if tiempos else 0,
                'triage_a_admision': round(tiempos.get('avg_triage_admision') or 0, 1) if tiempos else 0,
                'admision_a_consulta': round(tiempos.get('avg_admision_consulta') or 0, 1) if tiempos else 0,
            },
            'alertas': alertas_list[:20],
            'ts': datetime.now().isoformat(),
        })
    finally:
        cur.close()
        core._return_db(conn, db)


# ── GET /api/admisiones/historial ───────────────────────────────────────────

@admisiones_bp.route('/api/admisiones/historial')
def admisiones_historial():
    """Search historical admissions by patient document or date range."""
    if not _is_authenticated():
        return jsonify({'error': 'No autenticado'}), 401

    core = _get_deps()
    num_doc = request.args.get('num_doc', '').strip()
    fecha_desde = request.args.get('fecha_desde', '').strip()
    fecha_hasta = request.args.get('fecha_hasta', '').strip()
    limit = min(int(request.args.get('limit', 50)), 200)

    conn, db = core.get_db()
    cur = conn.cursor()
    try:
        q = ("SELECT a.*, p.nombres, p.apellidos, p.num_doc as p_num_doc "
             "FROM admisiones a LEFT JOIN pacientes p ON a.paciente_id=p.id WHERE 1=1")
        params = []

        if num_doc:
            q += " AND (a.doc_num_temp=? OR p.num_doc=?)"
            params.extend([num_doc, num_doc])
        if fecha_desde:
            q += f" AND {_D('a.creado_en',db)} >= ?"
            params.append(fecha_desde)
        if fecha_hasta:
            q += f" AND {_D('a.creado_en',db)} <= ?"
            params.append(fecha_hasta)

        q += " ORDER BY a.creado_en DESC LIMIT ?"
        params.append(limit)

        admisiones = core.rows(cur, core.adapt(q, db), params)
        return jsonify({'admisiones': admisiones, 'total': len(admisiones)})
    finally:
        cur.close()
        core._return_db(conn, db)
